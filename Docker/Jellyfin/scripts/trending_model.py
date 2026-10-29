"""Pure model of the trending library: the published lists (lists.json, coming-list.json) and their details become placeholder folders and 37 collections.
plan() decides each title's placeholder (folder, state, clips, badge, sort title) or its real library item; nfo() writes its movie.nfo. No I/O: trending.py applies the plan."""
import dataclasses
import datetime
import copy
import re
import xml.etree.ElementTree as ET

LIBRARY = "Trending items"
TAG = "hss-trending"
COLLECTIONS_NAME = "Collections & Trending"
PREFIX = {
    "found": "⬇ Not downloaded — choose a version (⋯ / Version) and press Play",
    "night": "✓ Requested — downloading tonight",
    "now": "✓ Requested — downloading now",
    "none": "Not able to download yet — checked daily",
}
CLIPS = ("now", "night", "unavailable", "requested", "coming")
HOME = (("home-movies", "movies", "Top 10 Switzerland · Movies"), ("home-shows", "shows", "Top 10 Switzerland · Shows"))
COMING = ("coming", "Coming soon")
KIND_NAME = {"movie": "Movies", "tv": "Shows"}
# The longest clip name adds 54 bytes to the folder name; ext4 allows 255.
FOLDER_MAX_BYTES = 150
UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
THUMB = "https://image.tmdb.org/t/p/w185"
TRAILER = "plugin://plugin.video.youtube/?action=play_video&videoid="


@dataclasses.dataclass
class Ph:
    key: str
    kind: str
    tmdb: int
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


def collection_specs(lists):
    """[{key, name, sort "NN <name>", logo}]: the two Top 10 home lists, Coming soon, then every row of lists.json in its order."""
    out = [{"key": key, "name": name, "logo": None} for key, _, name in HOME]
    out.append({"key": COMING[0], "name": COMING[1], "logo": None})
    for r in lists.get("rows") or []:
        out.append({"key": r["id"], "name": f"{r['name']['en']} · {KIND_NAME[r['kind']]}", "logo": r.get("logo")})
    for i, s in enumerate(out, 1):
        s["sort"] = f"{i:02d} {s['name']}"
    return out


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
    noun = "series" if kind == "tv" else "movie"
    if state == "found":
        return {f"{folder} - ⬇ Download {noun} now.mp4": "now", f"{folder} - 🌙 Download {noun} tonight · lower power cost.mp4": "night"}
    clip = {"none": "unavailable", "requested": "requested", "downloading": "requested", "coming": "coming"}[state]
    return {f"{folder}.mp4": clip}


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
    """Plan of placeholders {folder: Ph} and collections [(spec, [("ph", folder) | ("real", item id)])] in rank order.
    details: {details token: tmdb_details projection} (details.json and coming.json entries); owned: {"kind/tmdb": Jellyfin item id}; states: {"kind/tmdb": (request status, mode)}.
    One placeholder per title (kind/tmdb): its sort title "LL-RR <title>" comes from the first collection it appears in; a popular entry wins over a Coming soon one."""
    specs = collection_specs(lists)
    by_ref = {v.get("ref"): v for v in details.values() if isinstance(v, dict) and v.get("ref")}
    entries = {s["key"]: [] for s in specs}
    for key, home_key, _ in HOME:
        entries[key] = [e for e in (lists.get("home") or {}).get(home_key) or [] if not e.get("u") and e.get("id")]
    for r in lists.get("rows") or []:
        entries[r["id"]] = [e for e in r.get("items") or [] if not e.get("u") and e.get("id")]

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
            key = f"{e['k']}/{e['id']}"
            t = titles.setdefault(key, {"kind": e["k"], "tmdb": int(e["id"]), "first": (i, e["r"]), "entry": None, "info": None, "date": None})
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

    collections = []
    for spec in specs:
        if spec["key"] == COMING[0]:
            keys = [k for k, t in sorted(titles.items(), key=lambda kv: kv[1].get("coming_rank", 0)) if "coming_rank" in t]
        else:
            keys = [f"{e['k']}/{e['id']}" for e in entries[spec["key"]]]
        collections.append((spec, [m for m in map(member, keys) if m]))
    return Plan(placeholders, collections)


def _prefix(ph):
    if ph.state == "coming":
        return f"Coming soon — {_date_text(ph.coming_date)}" if ph.coming_date else "Coming soon"
    if ph.state == "requested":
        return PREFIX["night" if ph.mode == "night" else "now"]
    if ph.state == "downloading":
        return PREFIX["now"]
    return PREFIX[ph.state]


def nfo(ph):
    """movie.nfo of a placeholder (UTF-8 XML, <movie> root): title, sort title, plot behind the state prefix, year, genres, ratings, trailers, cast; never a <uniqueid>."""
    inf = ph.info or {}
    root = ET.Element("movie")

    def add(parent, tag, value):
        if value is not None and value != "":
            ET.SubElement(parent, tag).text = str(value)

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
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def library_id(folders):
    return next((f.get("ItemId") for f in folders if f["Name"] == LIBRARY), None)


def find_library(folders):
    return next((f for f in folders if f["Name"] == LIBRARY), None)


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
