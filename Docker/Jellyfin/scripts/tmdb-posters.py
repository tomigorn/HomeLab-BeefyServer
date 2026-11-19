#!/usr/bin/env python3
"""TMDB's primary poster (Seerr's posterPath, language en) as the Primary image of every film, series and season (when TMDB has a season poster) in the Movies and Shows libraries, instead of Jellyfin's pick (best average rating).
A TMDB primary whose title text is in another language than en (TMDB lists it, Jellyfin shows the language) is left out and logged. Applied through Jellyfin (RemoteImages/Download, original size); a title with trending list marks gets the poster as its kept original (trending/originals/<id>.jpg), trending.py re-marks it. Backdrops, logos, thumbs and our own collection art stay as they are.
TMDB answers through Seerr (no own key), cached 7 days; an item already on the poster is recorded by its image tag and not looked at again. The first replaced poster of each item is kept in ~/.local/state/jellyfin-tmdb-posters/backup/ (<id>.none: it had none, a rollback deletes it).
Usage: tmdb-posters.py [--dry-run | --check [REF] | --rollback | --enable | --help]
  (none)      apply (nightly in jellyfin-popular.service, before trending.py)
  --dry-run   count what would change, 10 examples (title: current TMDB file → TMDB primary), changes nothing
  --check     read only: REF's Primary (default movie/64690, Drive) is TMDB's primary poster
  --rollback  put every backed-up poster back and stop later runs until --enable
  --enable    allow runs again after a rollback
Exit 0 ok, 1 check failed or Jellyfin/Seerr error, 2 usage."""
import dataclasses
import datetime
import hashlib
import io as _io
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

STATE_DIR = pathlib.Path.home() / ".local" / "state"
TMDB = "https://image.tmdb.org/t/p/"
LIBRARIES = ("Movies", "Shows")
TYPES = "Movie,Series,Season"
IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)$")
TTL = datetime.timedelta(days=7)
THUMB = "w154"
SAME = 2.0  # mean grey difference (0-255) of two 24x36 thumbnails below which two posters are one: re-encodes measured <= 1.5, title-text variants >= 2.9
EXAMPLES = 10
TEXT_LANGUAGES = (None, "en")  # a TMDB primary with title text in another language (TMDB's fallback when there is no en poster) keeps Jellyfin's pick
CHECK_REF = "movie/64690"
SEERR_PAUSE = 0.2
FAILURES = (OSError, urllib.error.URLError, ValueError, KeyError, TypeError, TimeoutError)
USAGE = __doc__.split("Usage: ", 1)[1].split("Exit ")[0].rstrip()


@dataclasses.dataclass
class Paths:
    state: pathlib.Path = STATE_DIR / "jellyfin-tmdb-posters.json"
    store: pathlib.Path = STATE_DIR / "jellyfin-tmdb-posters"
    originals: pathlib.Path = pathlib.Path("/srv/appdata/jellyfin/trending/originals")
    trending_state: pathlib.Path = STATE_DIR / "jellyfin-trending.json"
    lock: pathlib.Path = STATE_DIR / "jellyfin-trending.lock"


class Io:
    def jf(self, method, path, body=None):
        import jellyfin_api
        return jellyfin_api.api(method, path, body)

    def jf_raw(self, method, path, data, content_type):
        import jellyfin_api
        return jellyfin_api.api_raw(method, path, data, content_type)

    def seerr(self, path):
        import popular
        return popular.Io().seerr(path)

    def fetch(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "holy-grail tmdb-posters"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    def sleep(self, s):
        time.sleep(s)


def thumb(data):
    from PIL import Image
    try:
        im = Image.open(_io.BytesIO(data))
        im.load()
    except Exception:
        return None
    return im.convert("L").resize((24, 36)).tobytes()


def same(a, b):
    ta, tb = thumb(a), thumb(b)
    if ta is None or tb is None:
        return False
    return sum(abs(x - y) for x, y in zip(ta, tb)) / len(ta) < SAME


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def ok_path(p):
    return p if isinstance(p, str) and IMAGE_PATH.match(p) else None


class Run:
    def __init__(self, io, paths, now, dry_run, out):
        self.io, self.paths, self.now, self.dry_run, self.out = io, paths, now, dry_run, out
        self.state = read_json(paths.state)
        self.tmdb_cache = self.state.setdefault("tmdb", {})
        self.done = self.state.setdefault("items", {})
        self.marked = set(read_json(paths.trending_state).get("real") or {})
        self.thumbs = {}
        self.remotes = {}

    def log(self, msg):
        print(msg, file=self.out, flush=True)

    def save(self):
        if not self.dry_run:
            write_atomic(self.paths.state, json.dumps(self.state, indent=0, sort_keys=True).encode())

    def details(self, kind, tmdb):
        """{"p": poster path | None, "s": {season number: poster path}} of TMDB's en answer through Seerr, cached TTL."""
        key = f"{kind}/{tmdb}"
        c = self.tmdb_cache.get(key)
        if c and self.now - datetime.datetime.fromisoformat(c["at"]) < TTL:
            return c
        d = self.io.seerr(f"/api/v1/{kind}/{int(tmdb)}?language=en")
        self.io.sleep(SEERR_PAUSE)
        c = {"at": self.now.isoformat(timespec="seconds"), "p": ok_path(d.get("posterPath")),
             "s": {str(s.get("seasonNumber")): ok_path(s.get("posterPath")) for s in d.get("seasons") or [] if isinstance(s.get("seasonNumber"), int)}}
        self.tmdb_cache[key] = c
        return c

    def tmdb_image(self, size, path):
        if (size, path) not in self.thumbs:
            self.thumbs[(size, path)] = self.io.fetch(TMDB + size + path)
        return self.thumbs[(size, path)]

    def items(self):
        libs = [f["ItemId"] for f in self.io.jf("GET", "/Library/VirtualFolders") if f["Name"] in LIBRARIES]
        out = []
        for lib in libs:
            out += [i for i in self.io.jf("GET", f"/Items?ParentId={lib}&Recursive=true&IncludeItemTypes={TYPES}&CollapseBoxSetItems=false&Fields=ProviderIds&EnableUserData=false")["Items"]
                    if i.get("Type") in TYPES.split(",")]
        return out

    def wanted(self, item, series):
        """(ref, TMDB poster path) or (ref, None) when TMDB has no clean poster for it."""
        if item["Type"] == "Season":
            sid = (series.get(item.get("SeriesId")) or {}).get("ProviderIds", {}).get("Tmdb")
            if not sid or not isinstance(item.get("IndexNumber"), int):
                return None, None
            return f"tv/{sid}/{item['IndexNumber']}", self.details("tv", sid)["s"].get(str(item["IndexNumber"]))
        tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
        if not tmdb or not str(tmdb).isdigit():
            return None, None
        kind = "movie" if item["Type"] == "Movie" else "tv"
        return f"{kind}/{tmdb}", self.details(kind, tmdb)["p"]

    def is_marked(self, iid):
        return iid in self.marked or (self.paths.originals / f"{iid}.badged").exists()

    def current(self, iid, item):
        """(bytes of the poster users see without list marks | None, key that changes when it changes)."""
        if self.is_marked(iid):
            f = self.paths.originals / f"{iid}.jpg"
            if f.is_file():
                data = f.read_bytes()
                return data, "orig:" + hashlib.sha256(data).hexdigest()
        tag = (item.get("ImageTags") or {}).get("Primary")
        if not tag:
            return None, None
        try:
            return self.io.jf_raw("GET", f"/Items/{iid}/Images/Primary?maxWidth=154", None, "image/jpeg"), "tag:" + tag
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, None
            raise

    def known(self, iid, item, path):
        rec = self.done.get(iid) or {}
        if rec.get("p") != path:
            return False
        if self.is_marked(iid):
            f = self.paths.originals / f"{iid}.jpg"
            return f.is_file() and rec.get("key") == "orig:" + hashlib.sha256(f.read_bytes()).hexdigest()
        return rec.get("key") == "tag:" + str((item.get("ImageTags") or {}).get("Primary"))

    def remote(self, iid):
        """[(TMDB file, language)] Jellyfin lists for the item, all languages (Seerr does not say a poster's language)."""
        if iid not in self.remotes:
            imgs = self.io.jf("GET", f"/Items/{iid}/RemoteImages?Type=Primary&ProviderName=TheMovieDb&IncludeAllLanguages=true")["Images"]
            self.remotes[iid] = [(i["Url"].rsplit("/", 1)[1], i.get("Language")) for i in imgs if i.get("Url")]
        return self.remotes[iid]

    def language(self, iid, path):
        """The text language of the TMDB poster, None when textless or not listed."""
        return next((lang for name, lang in self.remote(iid) if name == path[1:]), None) or None

    def current_name(self, iid, cur):
        """The TMDB file Jellyfin's current poster came from, for the examples (Jellyfin keeps no source)."""
        if cur is None:
            return "none"
        try:
            for name, _ in self.remote(iid)[:8]:
                if ok_path("/" + name) and same(cur, self.tmdb_image(THUMB, "/" + name)):
                    return name
        except FAILURES:
            pass
        return "other"

    def apply(self, iid, item, path):
        backup = self.paths.store / "backup"
        if self.is_marked(iid) and (self.paths.originals / f"{iid}.jpg").is_file():
            orig = self.paths.originals / f"{iid}.jpg"
            if not (backup / f"{iid}.original.jpg").exists():
                write_atomic(backup / f"{iid}.original.jpg", orig.read_bytes())
            data = self.tmdb_image("original", path)
            write_atomic(orig, data)
            return "orig:" + hashlib.sha256(data).hexdigest()
        if not (backup / f"{iid}.jpg").exists() and not (backup / f"{iid}.none").exists():
            data = None
            if (item.get("ImageTags") or {}).get("Primary"):
                try:
                    data = self.io.jf_raw("GET", f"/Items/{iid}/Images/Primary", None, "image/jpeg")
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        raise
            write_atomic(backup / (f"{iid}.jpg" if data else f"{iid}.none"), data or b"")
        url = urllib.parse.quote(TMDB + "original" + path, safe="")
        self.io.jf("POST", f"/Items/{iid}/RemoteImages/Download?Type=Primary&ImageUrl={url}")
        items = self.io.jf("GET", f"/Items?Ids={iid}&Recursive=true&EnableUserData=false")["Items"]
        return "tag:" + str(((items[0] if items else {}).get("ImageTags") or {}).get("Primary"))

    def run(self):
        items = self.items()
        series = {i["Id"]: i for i in items if i["Type"] == "Series"}
        checked = changed = skipped = failed = 0
        examples = []
        for item in items:
            checked += 1
            iid = item["Id"]
            try:
                ref, path = self.wanted(item, series)
                if not path or self.known(iid, item, path):
                    skipped += 1
                    continue
                cur, key = self.current(iid, item)
                if cur is not None and same(cur, self.tmdb_image(THUMB, path)):
                    skipped += 1
                    if not self.dry_run:
                        self.done[iid] = {"p": path, "key": key}
                    continue
                name = item.get("Name") or iid
                if item["Type"] == "Season":
                    name = f"{(series.get(item.get('SeriesId')) or {}).get('Name', '?')} · {name}"
                elif item.get("ProductionYear"):
                    name = f"{name} ({item['ProductionYear']})"
                lang = self.language(iid, path)
                if lang not in TEXT_LANGUAGES:
                    skipped += 1
                    self.log(f"  {name}: {path[1:]} has {lang} text, kept")
                    continue
                changed += 1
                if self.dry_run:
                    if len(examples) < EXAMPLES:
                        examples.append(f"  {name}: {self.current_name(iid, cur)} → {path[1:]}")
                    continue
                self.done[iid] = {"p": path, "key": self.apply(iid, item, path), "at": self.now.isoformat(timespec="seconds")}
                self.log(f"  {name}: → {path[1:]}{' (kept original, trending.py re-marks)' if self.is_marked(iid) else ''}")
            except FAILURES as e:
                failed += 1
                self.log(f"  {iid} failed: {type(e).__name__}")
        self.save()
        for line in examples:
            self.log(line)
        verb = "would change" if self.dry_run else "changed"
        self.log(f"{checked} checked, {changed} {verb}, {skipped} skipped" + (f", {failed} failed" if failed else ""))
        if self.dry_run and changed:
            self.log("apply: tmdb-posters.py")
        return 1 if failed else 0

    def check(self, ref):
        kind, tmdb = ref.split("/")[:2]
        item = next((i for i in self.items() if i["Type"] == ("Movie" if kind == "movie" else "Series") and (i.get("ProviderIds") or {}).get("Tmdb") == tmdb), None)
        if item is None:
            self.log(f"not in the library {ref}")
            return 1
        path = self.details(kind, tmdb)["p"]
        cur, _ = self.current(item["Id"], item)
        if path and cur is not None and same(cur, self.tmdb_image(THUMB, path)):
            self.log(f"primary ok {ref} {path}")
            return 0
        self.log(f"primary differs {ref} (TMDB {path}): run tmdb-posters.py --dry-run")
        return 1

    def rollback(self):
        backup = self.paths.store / "backup"
        n = 0
        for f in sorted(backup.glob("*.jpg")) if backup.is_dir() else []:
            if f.name.endswith(".original.jpg"):
                write_atomic(self.paths.originals / f.name.replace(".original.jpg", ".jpg"), f.read_bytes())
            else:
                import base64
                self.io.jf_raw("POST", f"/Items/{f.stem}/Images/Primary", base64.b64encode(f.read_bytes()), "image/jpeg")
            n += 1
        for f in sorted(backup.glob("*.none")) if backup.is_dir() else []:
            self.io.jf("DELETE", f"/Items/{f.stem}/Images/Primary")
            n += 1
        self.state["off"] = True
        self.state["items"] = {}
        self.save()
        self.log(f"rolled back {n} posters; later runs do nothing until: tmdb-posters.py --enable")
        return 0


def main(argv=None, io=None, paths=None, now=None, out=None):
    argv = sys.argv[1:] if argv is None else argv
    out = out or sys.stdout
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__, file=out)
        return 0
    flags = ("--dry-run", "--check", "--rollback", "--enable")
    if len(argv) > 2 or (argv and argv[0] not in flags) or (len(argv) == 2 and argv[0] != "--check"):
        print(f"usage: {USAGE}\nsee: tmdb-posters.py --help", file=out)
        return 2
    paths = paths or Paths()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    r = Run(io or Io(), paths, now, dry_run=bool(argv) and argv[0] in ("--dry-run", "--check"), out=out)
    try:
        if argv[:1] == ["--check"]:
            return r.check(argv[1] if len(argv) == 2 else CHECK_REF)
        if argv[:1] == ["--enable"]:
            r.state.pop("off", None)
            r.save()
            print("enabled; apply now: tmdb-posters.py", file=out)
            return 0
        import jellyfin_api
        with jellyfin_api.file_lock(paths.lock, timeout=600, step=1):
            if argv[:1] == ["--rollback"]:
                return r.rollback()
            if r.state.get("off"):
                print("rolled back earlier, nothing done; apply again: tmdb-posters.py --enable", file=out)
                return 0
            return r.run()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        print(f"failed: {type(e).__name__}; retry: tmdb-posters.py --dry-run", file=out)
        return 1


if __name__ == "__main__":
    sys.exit(main())
