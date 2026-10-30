"""Pure model of the trending libraries: the published lists (lists.json, coming-list.json) and their details become placeholder folders and 37 collections.
plan() decides each title's placeholder (folder, state, clips, badge, sort title) or its real library item; nfo() writes its movie.nfo or tvshow.nfo, episode_nfos() a series' Season 00 episodes. No I/O: trending.py applies the plan."""
import dataclasses
import datetime
import copy
import re
import unicodedata
import xml.etree.ElementTree as ET

LIBRARY = "Trending items"
SHOWS_LIBRARY = "Trending shows"
LIBRARIES = {"movie": LIBRARY, "tv": SHOWS_LIBRARY}
SEASON = "Season 00"
TAG = "hss-trending"
COLLECTIONS_NAME = "Collections & Trending"
PREFIX = {
    "found": "⬇ Not downloaded — choose a version (⋯ / Version) and press Play",
    "night": "✓ Requested — downloading tonight",
    "now": "✓ Requested — downloading now",
    "none": "Not able to download yet — checked daily",
}
TV_FOUND = "⬇ Not downloaded — open Specials, choose ⬇ now or 🌙 tonight and press Play"
EPISODES = {
    "now": ("S00E01 - ⬇ Download series now", "⬇ Download series now", "Plays 10 s: at the end the series is requested and downloads now. Back cancels."),
    "night": ("S00E02 - 🌙 Download series tonight · lower power cost", "🌙 Download series tonight · lower power cost",
              "Plays 10 s: at the end the series is requested and downloads tonight, when power costs less. Back cancels."),
}
CLIPS = ("now", "night", "unavailable", "requested", "coming")
HOME = (("home-movies", "movies", "Top 10 Switzerland · Movies"), ("home-shows", "shows", "Top 10 Switzerland · Shows"))
COMING = ("coming", "Coming soon")
UNMATCHED = "u"
KIND_NAME = {"movie": "Movies", "tv": "Shows"}
ART_MEMBERS = 4
# The longest clip name adds 54 bytes to the folder name; ext4 allows 255.
FOLDER_MAX_BYTES = 150
UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
THUMB = "https://image.tmdb.org/t/p/w185"
TRAILER = "plugin://plugin.video.youtube/?action=play_video&videoid="
# Old on purpose: Jellyfin takes DateCreated from <dateadded>, so placeholders never top "Recently added".
DATE_ADDED = "2000-01-01 00:00:00"


@dataclasses.dataclass
class Ph:
    key: str
    kind: str
    tmdb: int | None
    season: int | None
    folder: str
    title: str
    year: int | None
    state: str
    mode: str | None
    x: str | None
    sort_title: str
    info: dict | None
    files: dict
    badge: str | None
    coming_date: str | None


@dataclasses.dataclass
class Plan:
    placeholders: dict
    collections: list
    art: dict = dataclasses.field(default_factory=dict)


def collection_specs(lists):
    """[{key, name, sort "NN <name>", logo}]: the two Top 10 home lists, Coming soon, then every row of lists.json in its order."""
    out = [{"key": key, "name": name, "logo": None} for key, _, name in HOME]
    out.append({"key": COMING[0], "name": COMING[1], "logo": None})
    for r in lists.get("rows") or []:
        out.append({"key": r["id"], "name": f"{r['name']['en']} · {KIND_NAME[r['kind']]}", "logo": r.get("logo")})
    for i, s in enumerate(out, 1):
        s["sort"] = f"{i:02d} {s['name']}"
    return out


def slug(title):
    plain = "".join(c for c in unicodedata.normalize("NFKD", str(title or "").casefold()) if not unicodedata.combining(c))
    return "-".join(re.findall(r"[a-z0-9]+", plain))


def entry_key(e):
    """"kind/tmdb" of a matched list entry, "u/<slug of its title>" of one TMDB did not match, None when neither."""
    if e.get("id") and not e.get("u"):
        return f"{e['k']}/{e['id']}"
    if e.get("u") and not e.get("id"):
        s = slug((e.get("t") or {}).get("en") or (e.get("t") or {}).get("de"))
        return f"{UNMATCHED}/{s}" if s else None
    return None


def folder_name(title, year, taken, kind="movie"):
    """"<Title> (<year>)" without characters unsafe on any filesystem, unique against taken (compared case-insensitively; the result is added to taken).
    A series colliding with a film gets " (Series)", any further collision " (2)", " (3)", …"""
    clean = " ".join(UNSAFE.sub(" ", str(title or "")).split()).strip(" .")
    suffix = f" ({year})" if year else ""
    budget = FOLDER_MAX_BYTES - len(suffix.encode()) - len(" (Series) (99)")
    if len(clean.encode()) > budget:
        clean = clean.encode()[:budget].decode("utf-8", "ignore").rstrip(" .")
    base = (clean or "Untitled") + suffix
    candidates = [base] + ([base + " (Series)"] if kind == "tv" else [])
    name = next((c for c in candidates if c.casefold() not in taken), None)
    n = 2
    while name is None:
        c = f"{candidates[-1]} ({n})"
        name = c if c.casefold() not in taken else None
        n += 1
    taken.add(name.casefold())
    return name


def _date_text(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _files(folder, kind, state):
    """{path relative to the placeholder folder: clip}; a series keeps its clips as Season 00 episodes."""
    if kind == "tv":
        if state == "found":
            return {f"{SEASON}/{EPISODES[m][0]}.mp4": m for m in ("now", "night")}
        return {f"{SEASON}/S00E01.mp4": _clip(state)}
    if state == "found":
        return {f"{folder} - ⬇ Download movie now.mp4": "now", f"{folder} - 🌙 Download movie tonight · lower power cost.mp4": "night"}
    return {f"{folder}.mp4": _clip(state)}


def _clip(state):
    return {"none": "unavailable", "requested": "requested", "downloading": "requested", "coming": "coming"}[state]


def _state(entry, request):
    """(state, mode) of a title; entry is its lists.json item, None for a Coming soon title only."""
    if entry is None:
        return "coming", None
    status, mode = request or (None, None)
    if status in ("requested", "downloading"):
        return status, mode
    if status == "declined":
        return "none", None
    if entry.get("f") and entry.get("x"):
        return "found", None
    return "none", None


def plan(lists, coming, details, owned, states):
    """Plan of placeholders {folder: Ph}, collections [(spec, [("ph", folder) | ("real", item id)])] in rank order and art {spec key: [{"p": poster, "b": backdrop}] of the first members}.
    details: {details token: tmdb_details projection} (details.json and coming.json entries); owned: {"kind/tmdb": Jellyfin item id}; states: {"kind/tmdb": (request status, mode)}.
    One placeholder per title (kind/tmdb, or u/<slug> for an entry TMDB did not match: a title-only film placeholder, unavailable): its sort title "LL-RR <title>" comes from the first collection it appears in; a popular entry wins over a Coming soon one."""
    specs = collection_specs(lists)
    by_ref = {v.get("ref"): v for v in details.values() if isinstance(v, dict) and v.get("ref")}
    entries = {s["key"]: [] for s in specs}
    for key, home_key, _ in HOME:
        entries[key] = [e for e in (lists.get("home") or {}).get(home_key) or [] if entry_key(e)]
    for r in lists.get("rows") or []:
        entries[r["id"]] = [e for e in r.get("items") or [] if entry_key(e)]

    titles = {}
    for i, spec in enumerate(specs, 1):
        if spec["key"] == COMING[0]:
            for rank, c in enumerate((coming or {}).get("items") or [], 1):
                inf = details.get(c.get("d")) or by_ref.get(c.get("ref"))
                if inf is None:
                    continue
                key = f"{c['kind']}/{c['tmdb']}"
                t = titles.setdefault(key, {"kind": c["kind"], "tmdb": int(c["tmdb"]), "first": (i, rank), "entry": None, "info": None, "date": None})
                t["date"] = t["date"] or c.get("date")
                t["info"] = t["info"] or inf
                t.setdefault("coming_rank", rank)
            continue
        for e in entries[spec["key"]]:
            key = entry_key(e)
            u = key.startswith(UNMATCHED + "/")
            t = titles.setdefault(key, {"kind": "movie" if u else e["k"], "tmdb": None if u else int(e["id"]), "first": (i, e["r"]), "entry": None, "info": None, "date": None})
            if t["entry"] is None or (e.get("x") and not t["entry"].get("x")):
                t["entry"] = e
            t["info"] = t["info"] or details.get(e.get("d")) or by_ref.get(key)

    real = {key: owned[key] for key, t in titles.items() if key in owned}
    skip = {key for key, t in titles.items() if key not in owned and t["entry"] is not None and t["entry"].get("o")}
    taken, folders, placeholders = set(), {}, {}
    for key in sorted(k for k in titles if k not in real and k not in skip):
        t = titles[key]
        e, inf = t["entry"], t["info"]
        title = (inf or {}).get("t", {}).get("en") or (e or {}).get("t", {}).get("en") or ""
        year = (e or {}).get("y") or (inf or {}).get("y") or (int(t["date"][:4]) if t["date"] else None)
        folder = folder_name(title, year, taken, t["kind"])
        state, mode = _state(e, states.get(key))
        li, rank = t["first"]
        placeholders[folder] = Ph(
            key=key, kind=t["kind"], tmdb=t["tmdb"], season=(e or {}).get("s"), folder=folder, title=title, year=year, state=state, mode=mode,
            x=e.get("x") if state == "found" else None, sort_title=f"{li:02d}-{rank:02d} {title}", info=inf, files=_files(folder, t["kind"], state),
            badge=None if state == "coming" else state, coming_date=t["date"])
        folders[key] = folder

    def member(key):
        if key in real:
            return ("real", real[key])
        if key in folders:
            return ("ph", folders[key])
        return None

    def art_ref(key):
        t = titles[key]
        p = (t["entry"] or {}).get("p") or (t["info"] or {}).get("p")
        if isinstance(p, dict):
            p = p.get("en") or p.get("de")
        return {"p": p, "b": (t["info"] or {}).get("b")}

    collections, art = [], {}
    for spec in specs:
        if spec["key"] == COMING[0]:
            keys = [k for k, t in sorted(titles.items(), key=lambda kv: kv[1].get("coming_rank", 0)) if "coming_rank" in t]
        else:
            keys = [entry_key(e) for e in entries[spec["key"]]]
        collections.append((spec, [m for m in map(member, keys) if m]))
        art[spec["key"]] = [art_ref(k) for k in keys if member(k)][:ART_MEMBERS]
    return Plan(placeholders, collections, art)


def _prefix(ph):
    if ph.state == "coming":
        return f"Coming soon — {_date_text(ph.coming_date)}" if ph.coming_date else "Coming soon"
    if ph.state == "requested":
        return PREFIX["night" if ph.mode == "night" else "now"]
    if ph.state == "downloading":
        return PREFIX["now"]
    if ph.state == "found" and ph.kind == "tv":
        return TV_FOUND
    return PREFIX[ph.state]


def _xml(root):
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _add(parent, tag, value):
    if value is not None and value != "":
        ET.SubElement(parent, tag).text = str(value)


def nfo(ph):
    """movie.nfo (<movie>) or tvshow.nfo (<tvshow>) of a placeholder, UTF-8 XML: title, sort title, plot behind the state prefix, year, genres, ratings, trailers, cast, an old date added; never a <uniqueid>."""
    inf = ph.info or {}
    root = ET.Element("tvshow" if ph.kind == "tv" else "movie")
    add = _add

    add(root, "title", ph.title)
    add(root, "sorttitle", ph.sort_title)
    overview = (inf.get("o") or {}).get("en")
    add(root, "plot", _prefix(ph) + (f"\n\n{overview}" if overview else ""))
    add(root, "year", ph.year)
    for g in (inf.get("g") or {}).get("en") or []:
        add(root, "genre", g)
    sc = inf.get("sc") or {}
    add(root, "rating", sc.get("tmdb"))
    add(root, "criticrating", sc.get("rtc"))
    add(root, "mpaa", inf.get("cert"))
    for v in inf.get("v") or []:
        add(root, "trailer", TRAILER + v["key"])
    for c in inf.get("cast") or []:
        a = ET.SubElement(root, "actor")
        add(a, "name", c.get("n"))
        add(a, "role", c.get("c"))
        add(a, "thumb", THUMB + c["p"] if c.get("p") else None)
    add(root, "dateadded", DATE_ADDED)
    return _xml(root)


def nfo_name(ph):
    return "tvshow.nfo" if ph.kind == "tv" else "movie.nfo"


def episode_nfos(ph):
    """{Season 00 path: episode NFO} of a series placeholder, one per clip; {} for a film."""
    if ph.kind != "tv":
        return {}
    overview = ((ph.info or {}).get("o") or {}).get("en")
    out = {}
    for n, (path, clip) in enumerate(ph.files.items(), 1):
        if clip in EPISODES:
            _, title, plot = EPISODES[clip]
        else:
            title, plot = _prefix(ph), overview
        root = ET.Element("episodedetails")
        _add(root, "title", title)
        _add(root, "showtitle", ph.title)
        _add(root, "season", 0)
        _add(root, "episode", n)
        _add(root, "plot", plot)
        _add(root, "dateadded", DATE_ADDED)
        out[path[:-len(".mp4")] + ".nfo"] = _xml(root)
    return out


def library_id(folders, name=LIBRARY):
    return next((f.get("ItemId") for f in folders if f["Name"] == name), None)


def find_library(folders, name=LIBRARY):
    return next((f for f in folders if f["Name"] == name), None)


def hidden(config, lib_id):
    """config with lib_id added to MyMediaExcludes; unchanged when lib_id is unresolved."""
    out = copy.deepcopy(config)
    if not lib_id:
        return out
    excludes = list(out.get("MyMediaExcludes") or [])
    if lib_id not in excludes:
        excludes.append(lib_id)
    out["MyMediaExcludes"] = excludes
    return out
