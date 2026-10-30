#!/usr/bin/env python3
"""Trending sync: fills the hidden libraries "Trending items" (films: movie.nfo, poster.jpg, fanart.jpg, clips as hard links) and "Trending shows" (series: tvshow.nfo, art, clips as Season 00 episodes) with one placeholder folder per listed title not in the library, keeps the 37 collections (BoxSets tagged hss-trending) in step,
writes the request index for the TV hook (/srv/appdata/jellyfin-popular/trending-index.json) and the JavaScript Injector entry "Trending hidden" (window.HssTrendingHide).
Inputs: lists.json, coming-list.json, details.json, coming.json, Seerr media (read only), request-state.json. Never prints API keys.
Exit 0 ok, 1 inputs stale (> 9 days) or Jellyfin error (published state kept), 2 usage. Usage: trending.py [--dry-run]"""
import argparse
import base64
import contextlib
import dataclasses
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import trending_art as art  # noqa: E402
import trending_model as tm  # noqa: E402

STATE_DIR = pathlib.Path.home() / ".local" / "state"
JF_PATH = "/config/trending/library"
JF_SHOWS = "/config/trending/shows"
JF_ROOT = {"movie": JF_PATH, "tv": JF_SHOWS}
KINDS = ("movie", "tv")
NAME = "Trending hidden"
PLUGIN = "JavaScript Injector"
TMDB = "https://image.tmdb.org/t/p/"
MAX_AGE = datetime.timedelta(days=9)
INDEX_MAX_AGE = datetime.timedelta(days=1)
LOCK_WAIT = 600
POLL_EVERY = 5
POLL_TIMEOUT = 600
TICKS = 10 * 10_000_000
IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp|svg)$")
MODE = {"now": "now", "night": "night"}
ITEM_REFRESH = "MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=true&ReplaceAllImages=true"
LIB_REFRESH = "Recursive=true&MetadataRefreshMode=Default&ImageRefreshMode=Default&ReplaceAllMetadata=false&ReplaceAllImages=false"
KEEP = object()
FAILURES = (OSError, urllib.error.URLError, ValueError, KeyError, TypeError, TimeoutError)


class Stale(Exception):
    pass


class JellyfinError(Exception):
    pass


class Refused(Exception):
    pass


@dataclasses.dataclass
class Paths:
    feeds: pathlib.Path = pathlib.Path("/srv/appdata/jellyfin-popular")
    root: pathlib.Path = pathlib.Path("/srv/appdata/jellyfin/trending")
    state: pathlib.Path = STATE_DIR / "jellyfin-trending.json"
    lock: pathlib.Path = STATE_DIR / "jellyfin-trending.lock"
    request_state: pathlib.Path = pathlib.Path("/srv/appdata/indexer-bar/request-state.json")
    signal: pathlib.Path = pathlib.Path("/srv/appdata/jellyfin-popular-signal/requested")

    @property
    def library(self):
        return self.root / "library"

    @property
    def shows(self):
        return self.root / "shows"

    def dir(self, kind):
        return self.shows if kind == "tv" else self.library

    @property
    def clips(self):
        return self.root / "clips"

    @property
    def cache(self):
        return self.root / "cache"

    @property
    def index(self):
        return self.feeds / "trending-index.json"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Io:
    """Everything that leaves the script; tests use a fake."""

    def jf(self, method, path, body=None):
        import jellyfin_api
        return jellyfin_api.api(method, path, body)

    def jf_raw(self, method, path, data, content_type):
        import jellyfin_api
        return jellyfin_api.api_raw(method, path, data, content_type)

    def seerr_media(self):
        import popular
        return popular.Io().seerr("/api/v1/media?take=1000&filter=all").get("results") or []

    def fetch(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "holy-grail trending"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    def injector_lock(self):
        import jellyfin_api
        return jellyfin_api.injector_lock()

    def sleep(self, s):
        time.sleep(s)


@contextlib.contextmanager
def run_lock(path, wait=LOCK_WAIT):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"another trending run holds {path}")
                time.sleep(1)
        yield
    finally:
        os.close(fd)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_atomic(path, data, mode=0o644):
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def aware(stamp):
    t = datetime.datetime.fromisoformat(stamp)
    return t if t.tzinfo else t.astimezone()


def load_feeds(paths, now):
    """(lists, coming, details): Stale when lists.json or coming-list.json is missing or older than MAX_AGE."""
    out = {}
    for name in ("lists.json", "coming-list.json"):
        data = read_json(paths.feeds / name)
        try:
            age = now - aware(data["updated"])
        except (KeyError, TypeError, ValueError):
            raise Stale(f"{name} missing or without date")
        if age > MAX_AGE:
            raise Stale(f"{name} is {age.days} days old")
        out[name] = data
    details = {**(read_json(paths.feeds / "details.json").get("entries") or {}), **(read_json(paths.feeds / "coming.json").get("entries") or {})}
    return out["lists.json"], out["coming-list.json"], details


def own_path(path):
    return any((path or "").startswith(root + "/") for root in JF_ROOT.values())


def owned(io, lib_id):
    items = io.jf("GET", "/Items?Recursive=true&IncludeItemTypes=Movie,Series&CollapseBoxSetItems=false&Fields=ProviderIds,Path,ParentId&EnableImages=false&EnableUserData=false")["Items"]
    return {("movie/" if i["Type"] == "Movie" else "tv/") + i["ProviderIds"]["Tmdb"]: i["Id"]
            for i in items if (i.get("ProviderIds") or {}).get("Tmdb") and i.get("ParentId") != lib_id and not own_path(i.get("Path"))}


def listed(lists):
    for e in (lists.get("home") or {}).get("movies") or []:
        yield e
    for e in (lists.get("home") or {}).get("shows") or []:
        yield e
    for r in lists.get("rows") or []:
        yield from r.get("items") or []


def states(media, rs, lists, now):
    """{"kind/tmdb": (requested | downloading | declined | available, now | night)} from Seerr media and the night run's request-state.json."""
    pa = _load("popular_app", HERE.parent / "popular" / "app.py")
    by_key = {f"{m.get('mediaType')}/{m.get('tmdbId')}": m for m in media}
    wait = set(rs.get("canWait") or [])
    out = {}
    for e in listed(lists):
        if not e.get("id") or not e.get("k"):
            continue
        key = f"{e['k']}/{e['id']}"
        st = pa.seerr_status(e["k"], e.get("s"), by_key.get(key))
        if st == "requested" and pa.badge(e["k"], e["id"], rs, now) == "downloading":
            st = "downloading"
        if st in ("requested", "downloading", "declined", "available"):
            out[key] = (st, "night" if key in wait else "now")
    return out


class Images:
    """TMDB images cached in trending/cache by size and path; None when there is none, FAILED when the download failed."""
    FAILED = object()

    def __init__(self, io, cache, dry_run, log):
        self.io, self.cache, self.dry_run, self.log, self.mem = io, cache, dry_run, log, {}

    def get(self, size, path):
        if not path:
            return None
        if not IMAGE_PATH.match(path):
            self.log(f"skipped odd image path {path!r}")
            return None
        f = self.cache / f"{size}_{path[1:]}"
        if f in self.mem:
            return self.mem[f]
        try:
            data = f.read_bytes()
        except OSError:
            try:
                data = self.io.fetch(TMDB + size + path)
            except FAILURES as e:
                self.log(f"image {size}{path} failed: {type(e).__name__}")
                return self.FAILED
            if not self.dry_run:
                write_atomic(f, data)
        self.mem[f] = data
        return data


def poster_path(ph):
    p = (ph.info or {}).get("p") or {}
    return (p.get("en") or p.get("de")) if isinstance(p, dict) else p


def desired(ph, images):
    """{path in the folder: bytes | None (absent) | KEEP} for the folder's own non-video files."""
    out = {tm.nfo_name(ph): tm.nfo(ph).encode()}
    out.update({name: xml.encode() for name, xml in tm.episode_nfos(ph).items()})
    base = images.get("w780", poster_path(ph))
    out["poster.jpg"] = KEEP if base is Images.FAILED else art.poster(base, ph.badge, ph.title, ph.rank)
    back = images.get("w1280", (ph.info or {}).get("b"))
    out["fanart.jpg"] = KEEP if back is Images.FAILED else art.fanart(back)
    return out



def sync_folder(folder, ph, files, clips, dry_run):
    """Applies one placeholder folder; returns (new, structure changed, item content changed)."""
    new = not folder.is_dir() or folder.is_symlink()
    if new and not dry_run:
        folder.mkdir()
    want = ph.files
    subdirs = sorted({str(pathlib.PurePosixPath(n).parent) for n in [*files, *want]} - {"."})
    for sub in subdirs:
        d = folder / sub
        if d.is_symlink():
            raise Refused(f"symlink in a placeholder folder: {d}")
        if not dry_run:
            d.mkdir(exist_ok=True)
    content = False
    for name, data in files.items():
        f = folder / name
        if data is KEEP:
            continue
        old = None if new or f.is_symlink() else (f.read_bytes() if f.is_file() else None)
        if data is None:
            if old is not None or f.is_symlink():
                content = True
                if not dry_run:
                    f.unlink()
            continue
        if old != data:
            content = True
            if not dry_run:
                write_atomic(f, data)
    for sub in subdirs:
        d = folder / sub
        for p in sorted(d.iterdir()) if d.is_dir() else []:
            name = f"{sub}/{p.name}"
            if p.name.endswith(".nfo") and name not in files and (p.is_file() or p.is_symlink()):
                content = True
                if not dry_run:
                    p.unlink()
    have = set() if new else {p.relative_to(folder).as_posix() for p in [*folder.glob("*.mp4"), *(x for s in subdirs if (folder / s).is_dir() for x in (folder / s).glob("*.mp4"))]}
    structure = have != set(want)
    for name in have - set(want):
        if not dry_run:
            (folder / name).unlink()
    for name, clip in want.items():
        f = folder / name
        src = clips[clip]
        if name in have:
            st = os.lstat(f)
            if not f.is_symlink() and st.st_ino == src.st_ino and st.st_dev == src.st_dev:
                continue
            content = True
            if not dry_run:
                f.unlink()
        if not dry_run:
            os.link(src.path, f)
    return new, structure, content


@dataclasses.dataclass
class Clip:
    path: pathlib.Path
    st_ino: int
    st_dev: int


def clip_stats(paths):
    out = {}
    for c in tm.CLIPS:
        p = paths.clips / f"{c}.mp4"
        st = os.stat(p)
        out[c] = Clip(p, st.st_ino, st.st_dev)
    return out


def remove_stale(library, keep, dry_run, log):
    """Deletes folders directly in the library root that are not in the plan; symlinks and plain files are left alone."""
    removed = []
    root = os.path.realpath(library)
    for entry in os.scandir(library):
        if entry.name in keep or entry.name.startswith("."):
            continue
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            log(f"left alone: {entry.name}")
            continue
        if os.path.dirname(os.path.realpath(entry.path)) != root:
            continue
        removed.append(entry.name)
        if not dry_run:
            shutil.rmtree(entry.path)
    return removed


def sort_key(text):
    """Jellyfin stores SortName lower-cased, without accents, punctuation and the words the/a/an, numbers zero-padded."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    words = [w for w in re.split(r"[\W_]+", plain.replace("-", "")) if w and w not in ("the", "a", "an")]
    return re.sub(r"\d+", lambda m: str(int(m.group())), "".join(words))


def listing(io, lib_id, kind="movie"):
    types = "Series,Episode" if kind == "tv" else "Movie"
    fields = "Path,SortName" if kind == "tv" else "Path,MediaSources,SortName"
    return io.jf("GET", f"/Items?ParentId={lib_id}&Recursive=true&IncludeItemTypes={types}&CollapseBoxSetItems=false&Fields={fields}&EnableImages=false&EnableUserData=false")["Items"]


def folder_of(path):
    p = pathlib.PurePosixPath(path or "")
    return p.parent.name if str(p.parent.parent) == JF_PATH else None


@dataclasses.dataclass
class Placed:
    """A placeholder folder as Jellyfin lists it: item = the film or the series, files = {path in the folder: playable item or version id}, parents = series and season ids of a show."""
    item: dict
    files: dict
    ticks: list
    parents: set = dataclasses.field(default_factory=set)


def view(kind, items):
    """{folder: Placed} of one trending library listing."""
    out = {}
    if kind == "movie":
        for f, i in by_folder(items).items():
            sources = i.get("MediaSources") or []
            out[f] = Placed(i, {pathlib.PurePosixPath(ms.get("Path") or "").name: ms["Id"] for ms in sources}, [i["RunTimeTicks"]] if i.get("RunTimeTicks") else [])
        return out
    series = {}
    for i in items:
        p = pathlib.PurePosixPath(i.get("Path") or "")
        if i.get("Type") == "Series" and str(p.parent) == JF_SHOWS:
            out[p.name] = Placed(i, {}, [], {i["Id"]})
            series[i["Id"]] = p.name
    for i in items:
        if i.get("Type") != "Episode" or i.get("SeriesId") not in series:
            continue
        folder = series[i["SeriesId"]]
        try:
            rel = pathlib.PurePosixPath(i.get("Path") or "").relative_to(f"{JF_SHOWS}/{folder}").as_posix()
        except ValueError:
            continue
        out[folder].files[rel] = i["Id"]
        if i.get("SeasonId"):
            out[folder].parents.add(i["SeasonId"])
        if i.get("RunTimeTicks"):
            out[folder].ticks.append(i["RunTimeTicks"])
    return out


def by_folder(items):
    """{folder: its primary item}; an alternate version listed on its own never wins over the item that carries all versions."""
    out = {}
    for i in items:
        f = folder_of(i.get("Path"))
        if f and (f not in out or len(i.get("MediaSources") or []) > len(out[f].get("MediaSources") or [])):
            out[f] = i
    return out


def signature(placed):
    return {f: sorted(p.files) for f, p in placed.items()}


def wanted_signature(placeholders):
    return {f: sorted(ph.files) for f, ph in placeholders.items()}


def covers(have, want):
    return all(have.get(f) == w for f, w in want.items())


def index_items(placeholders, placed):
    """{playable id: {x, t, modes {version or episode id: now | night}, rt}}: a film's item and version ids, a series' episode ids; rt is the shortest clip runtime of that placeholder."""
    out = {}
    for folder, pl in placed.items():
        ph = placeholders.get(folder)
        if ph is None:
            continue
        modes = {fid: MODE[ph.files[name]] for name, fid in pl.files.items() if ph.files.get(name) in MODE}
        rec = {"x": ph.x, "t": ph.title, "modes": modes, "rt": min(pl.ticks, default=TICKS)}
        if ph.kind != "tv":
            out[pl.item["Id"]] = rec
        for fid in pl.files.values():
            out[fid] = rec
    return out


def hide_script(data):
    return "window.HssTrendingHide = " + json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/") + ";\n"


def q(value):
    return urllib.parse.quote(value, safe="")


RANKED = {key for key, _, _ in tm.HOME} | {tm.COMING[0]}
gems = _load("hidden_gems", HERE / "hidden-gems.py")


def display_order(spec):
    """Top 10 and Coming soon keep their members in rank order (owned titles at their rank), every other list sorts by sort title."""
    return "Default" if spec["key"] in RANKED else "SortName"


class Collections:
    def __init__(self, io, state, admin, images, dry_run, log, save):
        self.io, self.state, self.admin, self.images, self.dry_run, self.log, self.save = io, state, admin, images, dry_run, log, save
        self.changed = 0

    def own_untagged(self, sid, spec):
        """A BoxSet this script created (its id is in the state file) whose tag update failed: same name, still there."""
        try:
            item = self.io.jf("GET", f"/Items/{sid}?userId={self.admin}")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return False
            raise
        return item.get("Type", "BoxSet") == "BoxSet" and item.get("Name") == spec["name"]

    def tagged(self):
        items = self.io.jf("GET", f"/Items?Recursive=true&IncludeItemTypes=BoxSet&Tags={tm.TAG}&Fields=Tags,SortName&EnableImages=false&EnableUserData=false")["Items"]
        return {i["Id"]: i for i in items if tm.TAG in (i.get("Tags") or [])}

    def sync(self, collections, ids_of, art_refs=None):
        tagged = self.tagged()
        sets, arts = self.state.setdefault("sets", {}), self.state.setdefault("art", {})
        used = set()
        for spec, members in collections:
            key = spec["key"]
            want = [i for i in map(ids_of, members) if i]
            sid, own = sets.get(key), False
            if sid and sid not in tagged:
                own = self.own_untagged(sid, spec)
                sid = sid if own else None
            if sid is None:
                sid = next((i for i, s in tagged.items() if s.get("Name") == spec["name"] and i not in used and i not in sets.values()), None)
            created = False
            if sid is None:
                self.changed += 1
                if self.dry_run:
                    self.log(f"would create collection {spec['name']}")
                    continue
                sid = self.io.jf("POST", f"/Collections?name={q(spec['name'])}&ids={','.join(want)}&isLocked=true")["Id"]
                created = True
                arts.pop(key, None)
                sets[key] = sid
                self.save()
                self.log(f"created collection {spec['name']}")
            used.add(sid)
            sets[key] = sid
            self.metadata(sid, spec, created or own)
            if not created:
                self.members(sid, want, spec["key"] in RANKED)
            self.artwork(sid, spec, arts, (art_refs or {}).get(key) or [])
        return sorted(set(tagged) | used)

    def metadata(self, sid, spec, untagged_ok):
        item = self.io.jf("GET", f"/Items/{sid}?userId={self.admin}")
        tags = list(item.get("Tags") or [])
        order = display_order(spec)
        if tm.TAG in tags and item.get("ForcedSortName") == spec["sort"] and item.get("DisplayOrder") == order and item.get("LockData") is True and item.get("Name") == spec["name"]:
            return
        if not untagged_ok and tm.TAG not in tags:
            raise JellyfinError(f"collection {sid} lost its tag")
        self.changed += 1
        if self.dry_run:
            self.log(f"would update collection {spec['name']}")
            return
        if tm.TAG not in tags:
            tags.append(tm.TAG)
        self.io.jf("POST", f"/Items/{sid}", {**item, "Name": spec["name"], "Tags": tags, "ForcedSortName": spec["sort"], "DisplayOrder": order, "LockData": True})

    def members(self, sid, want, ordered=False):
        """Ranked lists (ordered) keep their members when want is empty, so a list never goes empty."""
        have = [i["Id"] for i in self.io.jf("GET", f"/Items?ParentId={sid}&userId={self.admin}&EnableImages=false&EnableUserData=false")["Items"]]
        if ordered:
            todo = gems.steps(have, want)
            if todo:
                self.changed += 1
                if self.dry_run:
                    self.log(f"would reorder collection {sid}: {len(todo)} steps")
                    return
            for method, ids in todo:
                self.io.jf(method, f"/Collections/{sid}/Items?ids={','.join(ids)}")
            return
        add = [i for i in want if i not in have]
        drop = [i for i in have if i not in want]
        if add or drop:
            self.changed += 1
        if self.dry_run:
            if add or drop:
                self.log(f"would change collection {sid}: +{len(add)} -{len(drop)}")
            return
        if add:
            self.io.jf("POST", f"/Collections/{sid}/Items?ids={','.join(add)}")
        if drop:
            self.io.jf("DELETE", f"/Collections/{sid}/Items?ids={','.join(drop)}")

    def artwork(self, sid, spec, arts, refs):
        """Primary poster and Backdrop of a list, uploaded when either changed; a failed download leaves the old art for the next run."""
        logo = self.images.get("w500", spec.get("logo"))
        posters = [self.images.get("w780", r.get("p")) for r in refs]
        back_path = next((r["b"] for r in refs if r.get("b")), None)
        back = self.images.get("w1280", back_path)
        if any(x is Images.FAILED for x in (logo, back, *posters)):
            return
        posters = [x for x in posters if x]
        data = art.collection_poster(spec["key"], spec["name"], logo, posters)
        backdrop = art.collection_backdrop(spec["key"], spec["name"], logo, back, posters)
        digest = hashlib.sha256(data + backdrop).hexdigest()
        if arts.get(spec["key"]) == digest:
            return
        self.changed += 1
        if self.dry_run:
            self.log(f"would upload art for {spec['name']}")
            return
        self.io.jf_raw("POST", f"/Items/{sid}/Images/Primary", base64.b64encode(data), "image/jpeg")
        self.io.jf_raw("POST", f"/Items/{sid}/Images/Backdrop/0", base64.b64encode(backdrop), "image/jpeg")
        arts[spec["key"]] = digest


def admin_id(io):
    return next(u["Id"] for u in io.jf("GET", "/Users") if (u.get("Policy") or {}).get("IsAdministrator"))


def upsert_hide(io, source, dry_run):
    tl = _load("tracker_link", HERE / "tracker-link.py")
    pid = next(p["Id"] for p in io.jf("GET", "/Plugins") if p["Name"] == PLUGIN)
    path = f"/Plugins/{pid}/Configuration"
    with io.injector_lock():
        config = io.jf("GET", path)
        if any(s.get("Name") == NAME and s.get("Script") == source for s in config.get("CustomJavaScripts") or []):
            return False
        if not dry_run:
            io.jf("POST", path, tl.upsert_script(config, NAME, source))
    return True


def index_parents(placeholders, placed):
    """Series and season ids of the show placeholders: the TV hook unmarks these only."""
    return sorted({p for folder, pl in placed.items() if folder in placeholders for p in pl.parents})


def write_index(paths, placed, placeholders, now, dry_run):
    data = {"items": index_items(placeholders, placed), "parents": index_parents(placeholders, placed)}
    old = read_json(paths.index)
    try:
        recent = now - aware(old["updated"]) < INDEX_MAX_AGE
    except (KeyError, TypeError, ValueError):
        recent = False
    if recent and {k: v for k, v in old.items() if k != "updated"} == data:
        return False, len(data["items"])
    if not dry_run:
        write_atomic(paths.index, json.dumps({"updated": now.isoformat(timespec="seconds"), **data}, ensure_ascii=False, sort_keys=True).encode())
    return True, len(data["items"])


def wait_settled(io, lib_id, kind, want, settled):
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        placed = view(kind, listing(io, lib_id, kind))
        if settled(signature(placed), want):
            return placed
        if time.monotonic() >= deadline:
            raise JellyfinError(f"{tm.LIBRARIES[kind]} refresh did not settle in {POLL_TIMEOUT} s")
        io.sleep(POLL_EVERY)


def sync(io, paths, now, dry_run, log):
    lists, coming, details = load_feeds(paths, now)
    folders = io.jf("GET", "/Library/VirtualFolders")
    lib_ids = {k: tm.library_id(folders, tm.LIBRARIES[k]) for k in KINDS}
    missing = [tm.LIBRARIES[k] for k in KINDS if not lib_ids[k]]
    if missing:
        raise JellyfinError(f"library {' and '.join(missing)} missing or unresolved (run trending-setup.py)")
    lib_id = lib_ids["movie"]
    have = owned(io, lib_id)
    rs = read_json(paths.request_state)
    st = states(io.seerr_media(), rs, lists, now)
    plan = tm.plan(lists, coming, details, have, st)
    if not plan.placeholders:
        raise Refused("the feeds give 0 placeholders")
    clips = clip_stats(paths)
    images = Images(io, paths.cache, dry_run, log)
    state = read_json(paths.state)
    kinds = {folder: ph.kind if ph.kind in KINDS else "movie" for folder, ph in plan.placeholders.items()}
    by_kind = {k: {f: ph for f, ph in plan.placeholders.items() if kinds[f] == k} for k in KINDS}

    def save():
        if not dry_run:
            paths.state.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(paths.state, json.dumps(state, indent=1, sort_keys=True).encode(), 0o600)

    added, restructured, touched = [], [], []
    files = {folder: desired(ph, images) for folder, ph in plan.placeholders.items()}
    for folder, ph in plan.placeholders.items():
        new, structure, content = sync_folder(paths.dir(kinds[folder]) / folder, ph, files[folder], clips, True)
        if new:
            added.append(folder)
        elif structure:
            restructured.append(folder)
            if content and kinds[folder] == "tv":
                touched.append(folder)
        elif content:
            touched.append(folder)
    removed = {k: remove_stale(paths.dir(k), set(by_kind[k]), True, lambda m: None) for k in KINDS}
    pending = state.get("pending") or {}
    old_libs = set(pending.get("libs") or []) | ({"movie"} if pending.get("library") else set())
    lib_pending = {k for k in KINDS if k in old_libs or any(kinds[f] == k for f in added + restructured)}
    refresh_items = sorted(set(touched) | set(pending.get("items") or []))
    if lib_pending or any(removed.values()) or refresh_items:
        state["pending"] = {"libs": sorted(lib_pending | {k for k in KINDS if removed[k]}), "items": refresh_items}
        save()
    for folder, ph in plan.placeholders.items():
        sync_folder(paths.dir(kinds[folder]) / folder, ph, files[folder], clips, dry_run)
    verb = "would " if dry_run else ""
    n_removed = sum(len(r) for r in removed.values())
    log(f"{len(plan.placeholders)} placeholders: {verb}add {len(added)}, {verb}restructure {len(restructured)}, {verb}update {len(touched)}, {verb}remove {n_removed}")

    placed = {k: view(k, listing(io, lib_ids[k], k)) for k in KINDS}
    want = {k: wanted_signature(by_kind[k]) for k in KINDS}
    refreshed = 0
    for k in KINDS:
        if k in lib_pending or not covers(signature(placed[k]), want[k]):
            if dry_run:
                log(f"would refresh {tm.LIBRARIES[k]}")
                continue
            io.jf("POST", f"/Items/{lib_ids[k]}/Refresh?{LIB_REFRESH}")
            refreshed += 1
            placed[k] = wait_settled(io, lib_ids[k], k, want[k], covers)
    ids = {f: pl.item for k in KINDS for f, pl in placed[k].items() if f in by_kind[k]}
    if dry_run:
        log(f"would refresh {len(refresh_items)} items")
    else:
        for folder in refresh_items:
            if folder in ids:
                io.jf("POST", f"/Items/{ids[folder]['Id']}/Refresh?{ITEM_REFRESH}")
                refreshed += 1
    for folder, ph in plan.placeholders.items():
        sn = (ids.get(folder) or {}).get("SortName")
        if sn is not None and folder not in refresh_items and sort_key(sn) != sort_key(ph.sort_title):
            log(f"sort title not applied: {folder}")

    cols = Collections(io, state, admin_id(io), images, dry_run, log, save)

    def member_id(m):
        kind, ref = m
        return ref if kind == "real" else (ids.get(ref) or {}).get("Id")

    set_ids = cols.sync(plan.collections, member_id, plan.art)
    all_placed = {f: pl for k in KINDS for f, pl in placed[k].items() if f in by_kind[k]}
    index_changed, n_index = write_index(paths, all_placed, plan.placeholders, now, dry_run)
    hidden_ids = set(index_items(plan.placeholders, all_placed)) | {pl.item["Id"] for pl in all_placed.values()}
    hide_changed = upsert_hide(io, hide_script({"lib": [lib_ids[k] for k in KINDS], "sets": set_ids, "items": sorted(hidden_ids)}), dry_run)

    for k in KINDS:
        gone = remove_stale(paths.dir(k), set(by_kind[k]), dry_run, log)
        if gone or signature(placed[k]) != want[k]:
            if dry_run:
                log(f"would refresh {tm.LIBRARIES[k]} after removing {len(gone)}")
                continue
            io.jf("POST", f"/Items/{lib_ids[k]}/Refresh?{LIB_REFRESH}")
            refreshed += 1
            wait_settled(io, lib_ids[k], k, want[k], lambda have, w: have == w)
    if not dry_run and state.pop("pending", None) is not None:
        save()
    if state != read_json(paths.state):
        save()

    changes = len(added) + len(restructured) + len(touched) + n_removed + refreshed + cols.changed + hide_changed
    if dry_run:
        log(f"would change {cols.changed} collection settings, index {'would change' if index_changed else 'unchanged'} ({n_index} ids), hide entry {'would change' if hide_changed else 'unchanged'}")
    elif changes:
        log(f"{refreshed} refreshes, {cols.changed} collection changes, index {n_index} ids, hide entry {'updated' if hide_changed else 'unchanged'}")
    else:
        log(f"nothing changed ({len(plan.placeholders)} placeholders, {len(plan.collections)} collections, index {n_index} ids)")


def signalled(path, since):
    try:
        return os.stat(path).st_mtime > since
    except OSError:
        return False


def main(argv=None, io=None, paths=None, now=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    io, paths = io or Io(), paths or Paths()
    clock = (lambda: now) if now else (lambda: datetime.datetime.now(datetime.timezone.utc))

    def log(msg):
        print(msg, file=out, flush=True)

    try:
        with run_lock(paths.lock):
            for extra in (False, True):
                start = time.time()
                sync(io, paths, clock(), args.dry_run, log)
                if extra or args.dry_run or not signalled(paths.signal, start):
                    break
                log("request signal during the run, syncing once more")
    except Stale as e:
        log(f"inputs stale, nothing changed: {e}")
        return 1
    except Refused as e:
        log(f"refused, nothing changed: {e}")
        return 1
    except (JellyfinError, *FAILURES) as e:
        log(f"error: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
