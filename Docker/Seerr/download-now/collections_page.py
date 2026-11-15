"""Collections (README "Collections"): the page /collections, where the household builds its own Jellyfin collections from a search like "producer:Adam Sandler genre:comedy".
A query is resolved here with Seerr's TMDB access (Seerr's API key, never the browser's), cached; a name that fits several people / studios / franchises comes back as a pick list.
Saved lists go to one JSON file that Jellyfin's popular.py reads (collections_file, /collections/lists.json): each list with its resolved terms and titles; refreshed every night in the maintenance window.
Everyone with a Seerr session sees every list; requesters create (5 each, 20 in all), only the creator or an admin edits or deletes."""
import datetime
import json
import os
import pathlib
import re
import shutil
import threading
import time
import unicodedata
import urllib.parse

NOW = time.time
LOCAL = time.localtime
CACHE = {}
THROTTLE = {}
FLOOD = {}
MEM = {}
CACHE_LOCK = threading.Lock()
THROTTLE_LOCK = threading.Lock()
STORE_LOCK = threading.Lock()
BUSY_LOCK = threading.Lock()
# previews and saves in flight overall, one per user; more = 429 at once, no thread waits
IN_FLIGHT = 3
SLOTS = threading.BoundedSemaphore(IN_FLIGHT)
BUSY = set()
CTX = threading.local()
# TMDB calls (cache misses) one query may make
CALL_BUDGET = 40
CACHE_TTL = 6 * 3600
# cache of the reduced answers, bounded by their JSON length (the container has 160 MiB)
CACHE_BYTES = 30 * 1024 * 1024
ROW_KEYS = ("id", "mediaType", "title", "name", "releaseDate", "firstAirDate", "popularity", "voteAverage", "genreIds", "posterPath", "adult", "job", "department", "character")
PREVIEW_MAX = 10
PREVIEW_WINDOW = 60
SAVE_GAP = 5
LIMIT_TOTAL = 20
LIMIT_USER = 5
MAX_DEFAULT, MAX_CAP = 50, 100
QUERY_MAX, TERMS_MAX, NAME_MAX, BODY_MAX = 300, 6, 60, 4096
PAGES = 5
OPTIONS = 6
NIGHT = (4 * 60, 4 * 60 + 40)
ADMIN, REQUEST, REQUEST_MOVIE, REQUEST_TV = 2, 32, 262144, 524288
OWNED = (4, 5)
SORTS = ("release", "popularity", "rating")
PERSON = ("actor", "director", "producer", "writer")
ANCHORS = PERSON + ("studio", "franchise")
FILTERS = ("genre", "year", "type", "keyword")
FIELDS = ANCHORS + FILTERS
PRODUCER_JOBS = {"Producer", "Executive Producer"}
# talk, news and reality shows: an actor's appearances as themselves
NOT_ROLES = {10763, 10764, 10767}
SELF = re.compile(r"\b(self|himself|herself|themselves)\b", re.I)
TERM = re.compile(r"(?i)(?<!\S)(" + "|".join(FIELDS) + r")\s*:")
NAME_OK = re.compile(r"^[\w ,.!?'&()+:-]+$")
ID = re.compile(r"^user-[a-z0-9-]{1,40}$")
RESERVED = ("coming soon", "hidden gems", "top 10")
# (names EN / DE, TMDB film genre, TMDB series genre or None)
GENRES = [
    (("action", "action & adventure"), "28", "10759"), (("adventure", "abenteuer"), "12", "10759"), (("animation", "animated", "zeichentrick"), "16", "16"),
    (("comedy", "komödie", "komodie"), "35", "35"), (("crime", "krimi"), "80", "80"), (("documentary", "documentaries", "dokumentation", "dokumentarfilm"), "99", "99"),
    (("drama",), "18", "18"), (("family", "familie"), "10751", "10751"), (("fantasy",), "14", "10765"), (("history", "historie"), "36", None),
    (("horror",), "27", None), (("music", "musik"), "10402", None), (("mystery",), "9648", "9648"), (("romance", "liebesfilm", "romantik"), "10749", None),
    (("science fiction", "sci-fi", "scifi"), "878", "10765"), (("thriller",), "53", None), (("war", "krieg"), "10752", "10768"), (("western",), "37", "37"),
    (("kids", "kinder"), None, "10762"),
]
TYPES = {"movie": "movie", "movies": "movie", "film": "movie", "films": "movie", "tv": "tv", "series": "tv", "serie": "tv", "serien": "tv", "show": "tv", "shows": "tv"}


class Refused(Exception):
    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra


class StoreUnreadable(Exception):
    pass


def fold(text):
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def ts(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text):
    """[(field, value)] of a query like `producer:Adam Sandler year:1990-1999`; Refused(400) for anything else."""
    text = " ".join(str(text or "").split())
    if not text:
        raise Refused(400, "empty")
    if len(text) > QUERY_MAX:
        raise Refused(400, "too-long")
    marks = list(TERM.finditer(text))
    if not marks or marks[0].start() != 0:
        raise Refused(400, "syntax")
    if len(marks) > TERMS_MAX:
        raise Refused(400, "too-many")
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        value = " ".join(text[m.end():end].strip().strip('"').split())
        if not value:
            raise Refused(400, "empty-term")
        out.append((m.group(1).lower(), value))
    return out


def genre_ids(name):
    want = fold(name)
    for names, movie, tv in GENRES:
        if want in names:
            return movie, tv
    return None


def year_range(value):
    m = re.fullmatch(r"(\d{4})(?:\s*-\s*(\d{4}))?", value)
    if not m:
        raise Refused(400, "year")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if not 1900 <= a <= b <= 2100:
        raise Refused(400, "year")
    return a, b


def check_terms(terms):
    """Filters well-formed, each filter at most once, at least one title source (a name, a studio, a franchise) or two filters."""
    seen = set()
    for f, v in terms:
        if f in ("year", "type", "genre") and f in seen:
            raise Refused(400, "twice")
        seen.add(f)
        if f == "year":
            year_range(v)
        elif f == "type" and fold(v) not in TYPES:
            raise Refused(400, "type")
        elif f == "genre" and not genre_ids(v):
            raise Refused(400, "genre")
    if not any(f in ANCHORS for f, _ in terms) and len([f for f, _ in terms if f in ("genre", "year", "keyword")]) < 2:
        raise Refused(400, "anchor-or-filter")


def row(r):
    if not isinstance(r, dict):
        return None
    out = {k: r[k] for k in ROW_KEYS if k in r}
    if isinstance(r.get("mediaInfo"), dict):
        out["mediaInfo"] = {"status": r["mediaInfo"].get("status")}
    if isinstance(r.get("knownFor"), list):
        out["knownFor"] = [{"title": k.get("title"), "name": k.get("name")} for k in r["knownFor"][:3] if isinstance(k, dict)]
    for k in ("profilePath", "logoPath", "originCountry"):
        if k in r:
            out[k] = r[k]
    return out


def slim(data):
    """Only the fields this page reads: results / cast / crew / parts as reduced rows, a film's collection, the page count."""
    if not isinstance(data, dict):
        return {}
    out = {k: [x for x in map(row, data[k]) if x] for k in ("results", "cast", "crew", "parts") if isinstance(data.get(k), list)}
    if "totalPages" in data:
        out["totalPages"] = data["totalPages"]
    c = data.get("collection")
    if isinstance(c, dict):
        out["collection"] = {"id": c.get("id"), "name": c.get("name"), "posterPath": c.get("posterPath")}
    return out


def tmdb(deps, path):
    """Seerr's TMDB answer for path (Seerr's API key), reduced and cached CACHE_TTL; every miss counts against the query's call budget."""
    now = NOW()
    with CACHE_LOCK:
        hit = CACHE.get(path)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    left = getattr(CTX, "calls", None)
    if left is not None:
        if left <= 0:
            raise Refused(400, "too-broad")
        CTX.calls = left - 1
    data = slim(deps.seerr_admin(path))
    size = len(json.dumps(data))
    with CACHE_LOCK:
        for k in [k for k, e in CACHE.items() if now - e[0] >= CACHE_TTL]:
            del CACHE[k]
        if size <= CACHE_BYTES:
            total = sum(e[2] for e in CACHE.values())
            for k, e in sorted(CACHE.items(), key=lambda kv: kv[1][0]):
                if total + size <= CACHE_BYTES:
                    break
                total -= e[2]
                del CACHE[k]
            CACHE[path] = (now, data, size)
    return data


def qs(value):
    return urllib.parse.quote(value, safe="")


def options_for(deps, field, value):
    """Candidates for a free name: [{id, name, photo, known}]."""
    if field in PERSON:
        res = tmdb(deps, f"/search?query={qs(value)}&page=1&language=en").get("results") or []
        return [{"id": r["id"], "name": r.get("name") or "", "photo": r.get("profilePath"),
                 "known": [k.get("title") or k.get("name") for k in (r.get("knownFor") or []) if isinstance(k, dict) and (k.get("title") or k.get("name"))][:3]}
                for r in res if isinstance(r, dict) and r.get("mediaType") == "person" and isinstance(r.get("id"), int)]
    if field == "studio":
        res = tmdb(deps, f"/search/company?query={qs(value)}&page=1&language=en").get("results") or []
        return [{"id": r["id"], "name": r.get("name") or "", "photo": r.get("logoPath"), "known": [r["originCountry"]] if r.get("originCountry") else []}
                for r in res if isinstance(r, dict) and isinstance(r.get("id"), int)]
    if field == "keyword":
        res = tmdb(deps, f"/search/keyword?query={qs(value)}&page=1&language=en").get("results") or []
        return [{"id": r["id"], "name": r.get("name") or "", "photo": None, "known": []} for r in res if isinstance(r, dict) and isinstance(r.get("id"), int)]
    res = tmdb(deps, f"/search?query={qs(value)}&page=1&language=en").get("results") or []
    out = []
    for r in [r for r in res if isinstance(r, dict) and r.get("mediaType") == "movie" and isinstance(r.get("id"), int)][:OPTIONS]:
        c = (tmdb(deps, f"/movie/{r['id']}?language=en") or {}).get("collection")
        if isinstance(c, dict) and isinstance(c.get("id"), int) and all(o["id"] != c["id"] for o in out):
            out.append({"id": c["id"], "name": c.get("name") or "", "photo": c.get("posterPath"), "known": []})
    return out


def resolve(deps, terms, picks):
    """Resolved terms [{f, id, name} | {f, from, to} | {f, value}] or the pick list [{term, field, value, options}] of names that fit several."""
    out, ask = [], []
    for i, (f, v) in enumerate(terms):
        if f == "year":
            a, b = year_range(v)
            out.append({"f": f, "from": a, "to": b})
            continue
        if f == "type":
            out.append({"f": f, "value": TYPES[fold(v)]})
            continue
        if f == "genre":
            names = next(n for n, *_ in GENRES if fold(v) in n)
            out.append({"f": f, "name": names[0].title()})
            continue
        opts = options_for(deps, f, v)
        if i in picks:
            hit = next((o for o in opts if o["id"] == picks[i]), None)
            if not hit:
                raise Refused(400, "pick")
            out.append({"f": f, "id": hit["id"], "name": hit["name"]})
            continue
        exact = [o for o in opts if fold(o["name"]) == fold(v)]
        if len(exact) == 1 or (len(opts) == 1 and f == "franchise"):
            hit = exact[0] if len(exact) == 1 else opts[0]
            out.append({"f": f, "id": hit["id"], "name": hit["name"]})
        elif not opts:
            raise Refused(404, "not-found", term=i)
        else:
            ask.append({"term": i, "field": f, "value": v, "options": (exact if len(exact) > 1 else opts)[:OPTIONS]})
    return out, ask


def canonical(resolved):
    parts = []
    for t in resolved:
        if t["f"] == "year":
            parts.append(f"year:{t['from']}" + (f"-{t['to']}" if t["to"] != t["from"] else ""))
        elif t["f"] == "type":
            parts.append(f"type:{'movie' if t['value'] == 'movie' else 'series'}")
        else:
            parts.append(f"{t['f']}:{t['name']}")
    return " ".join(parts)


def item(r):
    """A title of a TMDB answer: {k, id, t, y, p, o, pop, vote, date, g}; None for people and broken rows."""
    if not isinstance(r, dict) or not isinstance(r.get("id"), int) or r.get("adult"):
        return None
    kind = r.get("mediaType") or ("movie" if "title" in r else "tv")
    if kind not in ("movie", "tv"):
        return None
    date = (r.get("releaseDate") if kind == "movie" else r.get("firstAirDate")) or ""
    poster = r.get("posterPath")
    info = r.get("mediaInfo") if isinstance(r.get("mediaInfo"), dict) else {}
    return {"k": kind, "id": r["id"], "t": str(r.get("title") or r.get("name") or "")[:200], "y": int(date[:4]) if date[:4].isdigit() else None,
            "p": poster if isinstance(poster, str) and re.fullmatch(r"/[A-Za-z0-9_.-]+", poster) else None, "o": info.get("status") in OWNED,
            "pop": float(r.get("popularity") or 0), "vote": float(r.get("voteAverage") or 0), "date": date, "g": set(r.get("genreIds") or [])}


def person_titles(deps, t):
    cr = tmdb(deps, f"/person/{t['id']}/combined_credits?language=en") or {}
    if t["f"] == "actor":
        rows = [r for r in cr.get("cast") or [] if isinstance(r, dict) and not (r.get("mediaType") == "tv" and (set(r.get("genreIds") or []) & NOT_ROLES or SELF.search(str(r.get("character") or ""))))]
    elif t["f"] == "director":
        rows = [r for r in cr.get("crew") or [] if isinstance(r, dict) and r.get("job") == "Director"]
    elif t["f"] == "producer":
        rows = [r for r in cr.get("crew") or [] if isinstance(r, dict) and r.get("job") in PRODUCER_JOBS]
    else:
        rows = [r for r in cr.get("crew") or [] if isinstance(r, dict) and r.get("department") == "Writing"]
    return rows


def discover(deps, kind, params, enough):
    out = []
    for page in range(1, PAGES + 1):
        d = tmdb(deps, f"/discover/{'movies' if kind == 'movie' else 'tv'}?{params}&sortBy=popularity.desc&page={page}&language=en") or {}
        out += [dict(r, mediaType=kind) for r in d.get("results") or [] if isinstance(r, dict)]
        if page >= (d.get("totalPages") or 1) or len(out) >= enough:
            break
    return out


def source(deps, t):
    if t["f"] in PERSON:
        return person_titles(deps, t)
    if t["f"] == "studio":
        return discover(deps, "movie", f"studio={t['id']}", 10 ** 6)
    return [dict(r, mediaType="movie") for r in (tmdb(deps, f"/collection/{t['id']}?language=en") or {}).get("parts") or [] if isinstance(r, dict)]


def evaluate(deps, resolved, sort, cap):
    """(titles in the chosen order, at most cap, total before the cap) of resolved terms: the title sources ANDed, then the filters."""
    kinds = {"movie", "tv"}
    genre = years = None
    for t in resolved:
        if t["f"] == "type":
            kinds = {t["value"]}
        elif t["f"] == "genre":
            genre = genre_ids(t["name"])
        elif t["f"] == "year":
            years = (t["from"], t["to"])
    anchors = [t for t in resolved if t["f"] in ANCHORS]
    keywords = [t for t in resolved if t["f"] == "keyword"]
    pool = None
    for t in anchors:
        got = {}
        for r in source(deps, t):
            it = item(r)
            if it:
                got.setdefault((it["k"], it["id"]), it)
        pool = got if pool is None else {k: v for k, v in pool.items() if k in got}
    if pool is None:
        pool = {}
        for kind in sorted(kinds):
            g = genre[0 if kind == "movie" else 1] if genre else None
            if genre and not g:
                continue
            params = []
            if keywords:
                params.append(f"keywords={keywords[0]['id']}")
            if g:
                params.append(f"genre={qs(g)}")
            if years:
                pre = "primaryReleaseDate" if kind == "movie" else "firstAirDate"
                params += [f"{pre}Gte={years[0]}-01-01", f"{pre}Lte={years[1]}-12-31"]
            for r in discover(deps, kind, "&".join(params), MAX_CAP):
                it = item(r)
                if it:
                    pool.setdefault((it["k"], it["id"]), it)
        keywords = keywords[1:]
    for t in keywords:
        hit = set()
        for kind in sorted(kinds):
            hit |= {(kind, r.get("id")) for r in discover(deps, kind, f"keywords={t['id']}", 10 ** 6)}
        pool = {k: v for k, v in pool.items() if k in hit}

    def keep(it):
        if it["k"] not in kinds:
            return False
        if genre:
            g = genre[0 if it["k"] == "movie" else 1]
            if not g or not it["g"] & {int(x) for x in g.split("|")}:
                return False
        if years and not (it["y"] and years[0] <= it["y"] <= years[1]):
            return False
        return True
    found = [it for it in pool.values() if keep(it)]
    if sort == "popularity":
        found.sort(key=lambda i: (-i["pop"], i["t"]))
    elif sort == "rating":
        found.sort(key=lambda i: (-i["vote"], -i["pop"]))
    else:
        found.sort(key=lambda i: i["t"])
        found.sort(key=lambda i: i["date"], reverse=True)
    return [{k: it[k] for k in ("k", "id", "t", "y", "p", "o")} for it in found[:cap]], len(found)


def may_create(me):
    p = me.get("permissions")
    return isinstance(p, int) and not isinstance(p, bool) and bool(p & (ADMIN | REQUEST | REQUEST_MOVIE | REQUEST_TV))


def is_admin(me):
    p = me.get("permissions")
    return isinstance(p, int) and not isinstance(p, bool) and bool(p & ADMIN)


def throttle(key, gap):
    with THROTTLE_LOCK:
        now = NOW()
        if now - THROTTLE.get(key, -gap - 1) < gap:
            return False
        THROTTLE[key] = now
        return True


def flooding(uid):
    with THROTTLE_LOCK:
        now = NOW()
        hits = [t for t in FLOOD.get(uid, []) if now - t < PREVIEW_WINDOW] + [now]
        FLOOD[uid] = hits
        return len(hits) > PREVIEW_MAX


def load(deps):
    if not deps.collections_file:
        return MEM.setdefault("lists", [])
    path = pathlib.Path(deps.collections_file)
    if not path.exists():
        return []
    try:
        lists = json.loads(path.read_text())["lists"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StoreUnreadable(f"{path.name}: {type(e).__name__}") from None
    if not isinstance(lists, list):
        raise StoreUnreadable(f"{path.name}: no lists")
    return lists


def save(deps, lists):
    """Atomic; group-readable (0640, setgid dir of group buntu) for Jellyfin's popular.py, the previous version kept as .bak."""
    if not deps.collections_file:
        MEM["lists"] = lists
        return
    path = pathlib.Path(deps.collections_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
        os.chmod(path.with_name(path.name + ".bak"), 0o640)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"updated": ts(NOW()), "lists": lists}, ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def person(u):
    """The creator's name as it goes to Jellyfin (overview, poster): Seerr / Jellyfin / Plex name in the name charset, else "user N"."""
    for k in ("username", "jellyfinUsername", "plexUsername"):
        v = " ".join(u.get(k).split()) if isinstance(u.get(k), str) else ""
        if v and len(v) <= NAME_MAX and NAME_OK.match(v):
            return v
    return f"user {int(u.get('id') or 0)}"


def slug(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).strip("-")[:30].strip("-") or "list"
    sid, n = f"user-{base}", 2
    while sid in taken:
        sid, n = f"user-{base}-{n}", n + 1
    return sid


def options(req):
    sort = req.get("sort") or "release"
    if sort not in SORTS:
        raise Refused(400, "sort")
    cap = req.get("max", MAX_DEFAULT)
    try:
        cap = int(cap) if not isinstance(cap, bool) else None
    except (TypeError, ValueError):
        cap = None
    if cap is None or not 1 <= cap <= MAX_CAP:
        raise Refused(400, "max")
    picks = {}
    if not isinstance(req.get("picks") or {}, dict):
        raise Refused(400, "pick")
    for k, v in (req.get("picks") or {}).items():
        try:
            picks[int(k)] = int(v)
        except (TypeError, ValueError):
            raise Refused(400, "pick") from None
    return sort, cap, picks


def run_query(deps, req):
    """(status, answer) of a query: the pick list, or the titles with the canonical query."""
    if not isinstance(req.get("query"), str):
        raise Refused(400, "empty")
    sort, cap, picks = options(req)
    terms = parse(req["query"])
    check_terms(terms)
    resolved, ask = resolve(deps, terms, picks)
    if ask:
        return "pick", {"status": "pick", "picks": ask}, None
    items, total = evaluate(deps, resolved, sort, cap)
    return "ok", {"status": "ok", "query": canonical(resolved), "items": items, "count": len(items), "total": total, "owned": sum(1 for i in items if i["o"])}, (resolved, sort, cap)


def public(lst, me):
    mine = lst.get("owner") == me.get("id")
    return {"id": lst["id"], "name": lst["name"], "ownerName": lst.get("ownerName"), "mine": mine, "canEdit": mine or is_admin(me), "query": lst.get("query"),
            "sort": lst.get("sort"), "max": lst.get("max"), "count": len(lst.get("items") or []), "owned": sum(1 for i in lst.get("items") or [] if i.get("o")),
            "items": lst.get("items") or [], "updated": lst.get("updated")}


def view(deps, me):
    with STORE_LOCK:
        lists = load(deps)
    mine = sum(1 for l in lists if l.get("owner") == me.get("id"))
    return {"me": {"id": me.get("id"), "admin": is_admin(me), "create": may_create(me)}, "limits": {"total": LIMIT_TOTAL, "perUser": LIMIT_USER, "mine": mine, "all": len(lists)},
            "lists": [public(l, me) for l in sorted(lists, key=lambda l: (fold(l.get("name")), l["id"]))]}


def clean_name(name):
    if not isinstance(name, str) or any(c in name for c in "\r\n\t\x00"):
        raise Refused(400, "name")
    name = " ".join(name.split())
    if not name or len(name) > NAME_MAX or not NAME_OK.match(name):
        raise Refused(400, "name")
    return name


def do_save(deps, me, req):
    if not may_create(me):
        raise Refused(403, "forbidden")
    name = clean_name(req.get("name"))
    lid = req.get("id")
    if lid is not None and (not isinstance(lid, str) or not ID.match(lid)):
        raise Refused(404, "not-found")
    with STORE_LOCK:
        lists = load(deps)
    old = next((l for l in lists if l["id"] == lid), None) if lid else None
    if lid and not old:
        raise Refused(404, "not-found")
    if old and not (old.get("owner") == me.get("id") or is_admin(me)):
        raise Refused(403, "forbidden")
    if fold(name).startswith(RESERVED) or any(fold(l["name"]) == fold(name) and l["id"] != lid for l in lists):
        raise Refused(409, "name-taken")
    if not old:
        if len(lists) >= LIMIT_TOTAL:
            raise Refused(409, "limit-total")
        if sum(1 for l in lists if l.get("owner") == me.get("id")) >= LIMIT_USER:
            raise Refused(409, "limit-user")
    state, answer, how = run_query(deps, req)
    if state == "pick":
        raise Refused(409, "pick", body=answer)
    if not answer["items"]:
        raise Refused(400, "no-titles")
    resolved, sort, cap = how
    now = ts(NOW())
    with STORE_LOCK:
        lists = load(deps)
        cur = next((l for l in lists if l["id"] == lid), None) if lid else None
        if lid and not cur:
            raise Refused(404, "not-found")
        if cur and (cur.get("created") != old.get("created") or not (cur.get("owner") == me.get("id") or is_admin(me))):
            raise Refused(409, "changed")
        if not cur and (len(lists) >= LIMIT_TOTAL or sum(1 for l in lists if l.get("owner") == me.get("id")) >= LIMIT_USER):
            raise Refused(409, "limit-total" if len(lists) >= LIMIT_TOTAL else "limit-user")
        if any(fold(l["name"]) == fold(name) and l["id"] != lid for l in lists):
            raise Refused(409, "name-taken")
        if not cur:
            cur = {"id": slug(name, {l["id"] for l in lists}), "owner": me.get("id"), "ownerName": person(me), "created": now}
            lists.append(cur)
        cur.update(name=name, query=answer["query"], terms=resolved, sort=sort, max=cap, items=answer["items"], updated=now, refreshed=now, editedBy=me.get("id"))
        save(deps, lists)
    print(f"collections: user {me.get('id')} saved {cur['id']} ({len(answer['items'])} titles)", flush=True)
    return public(cur, me)


def do_delete(deps, me, req):
    lid = req.get("id")
    if not isinstance(lid, str) or not ID.match(lid):
        raise Refused(404, "not-found")
    with STORE_LOCK:
        lists = load(deps)
        cur = next((l for l in lists if l["id"] == lid), None)
        if not cur:
            raise Refused(404, "not-found")
        if not (cur.get("owner") == me.get("id") or is_admin(me)):
            raise Refused(403, "forbidden")
        if req.get("confirm") != "delete":
            raise Refused(400, "confirm")
        save(deps, [l for l in lists if l["id"] != lid])
    print(f"collections: user {me.get('id')} deleted {lid}", flush=True)
    return {"deleted": lid}


def handle(dn, method, path, headers, body, deps):
    url = urllib.parse.urlsplit(path)
    sub = url.path[len("/_download-now/collections/"):]
    h = {k.lower(): v for k, v in headers.items()}
    req = {}
    if method == "POST" and sub in ("save", "delete"):
        if h.get("origin") not in deps.origins:
            return 403, {"error": "origin"}
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            return 415, {"error": "json only"}
        if len(body or b"") > BODY_MAX:
            return 413, {"error": "too-big"}
        try:
            req = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            return 400, {"error": "bad request"}
        if not isinstance(req, dict):
            return 400, {"error": "bad request"}
    elif not (method == "GET" and sub in ("lists", "preview")):
        return 404, {"error": "not found"}
    auth = dn.auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except dn.ApiError:
        me = None
    if not isinstance(me, dict) or not isinstance(me.get("id"), int) or isinstance(me.get("id"), bool):
        return 403, {"error": "forbidden"}
    if sub == "lists":
        try:
            return 200, view(deps, me)
        except StoreUnreadable as e:
            print(f"collections: store unreadable ({e})", flush=True)
            return 503, {"error": "store"}
    if sub in ("preview", "save"):
        with BUSY_LOCK:
            if me["id"] in BUSY or not SLOTS.acquire(blocking=False):
                return 429, {"error": "busy"}
            BUSY.add(me["id"])
        CTX.calls = CALL_BUDGET
        try:
            return work(dn, deps, me, sub, url, req)
        finally:
            CTX.calls = None
            with BUSY_LOCK:
                BUSY.discard(me["id"])
            SLOTS.release()
    return work(dn, deps, me, sub, url, req)


def work(dn, deps, me, sub, url, req):
    try:
        if sub == "preview":
            if flooding(me["id"]):
                return 429, {"error": "slow-down"}
            q = urllib.parse.parse_qs(url.query)
            picks = {}
            for p in q.get("pick") or []:
                k, _, v = p.partition(":")
                picks[k] = v
            req = {"query": (q.get("q") or [""])[0], "sort": (q.get("sort") or ["release"])[0], "max": (q.get("max") or [MAX_DEFAULT])[0], "picks": picks}
            return 200, run_query(deps, req)[1]
        if not throttle(("save", me["id"]), SAVE_GAP):
            return 429, {"error": "slow-down"}
        if sub == "save":
            return 200, do_save(deps, me, req)
        return 200, do_delete(deps, me, req)
    except Refused as e:
        return e.status, e.extra["body"] if "body" in e.extra else dict({"error": e.error}, **e.extra)
    except StoreUnreadable as e:
        print(f"collections: store unreadable ({e}), not overwritten", flush=True)
        return 503, {"error": "store"}
    except Exception as e:
        print(f"collections {sub}: {type(e).__name__} {dn.redact(e)}", flush=True)
        return 502, {"error": "not-reachable"}


def nightly(dn, deps):
    """Inside the maintenance window, once a night: every list re-run from its stored terms; the file is written only when a list changed. Returns the number of lists run."""
    lt = LOCAL(NOW())
    if not NIGHT[0] <= lt.tm_hour * 60 + lt.tm_min < NIGHT[1]:
        return 0
    today = time.strftime("%Y-%m-%d", lt)
    if MEM.get("night") == today:
        return 0
    MEM["night"] = today
    with STORE_LOCK:
        try:
            lists = load(deps)
        except StoreUnreadable:
            return 0
    fresh, seen = {}, {l["id"]: l.get("updated") for l in lists}
    for l in lists:
        CTX.calls = CALL_BUDGET
        try:
            fresh[l["id"]] = evaluate(deps, l["terms"], l.get("sort") or "release", int(l.get("max") or MAX_DEFAULT))[0]
        except Exception as e:
            print(f"collections night {l.get('id')}: {type(e).__name__} {dn.redact(e)}", flush=True)
        finally:
            CTX.calls = None
    with STORE_LOCK:
        try:
            lists = load(deps)
        except StoreUnreadable:
            return 0
        changed = False
        for l in lists:
            items = fresh.get(l["id"])
            if items is not None and items != l.get("items") and l.get("updated") == seen.get(l["id"]):
                l.update(items=items, refreshed=ts(NOW()))
                changed = True
        if changed:
            save(deps, lists)
    print(f"collections night: {len(fresh)} of {len(lists)} lists run, {'saved' if changed else 'unchanged'}", flush=True)
    return len(lists)


def loop(dn, deps, every=300):
    while True:
        try:
            nightly(dn, deps)
        except Exception as e:
            print(f"collections night: {type(e).__name__} {dn.redact(e)}", flush=True)
        time.sleep(every)
