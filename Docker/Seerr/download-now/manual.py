"""Manual interaction (README "Manual interaction"): the page /_manual/ lists every download problem that needs a human, for every Seerr user, and lets them fix it.
Problems come from request-state.json (removed, Seerr hand-over failed, nothing found for days) and the Radarr/Sonarr queues (import blocked, wrong film, failed).
Every problem is tracked in one JSON file (first/last seen, actions, how it was resolved), kept 180 days after it is done; GET .../history exports it (admins).
The browser never sees keys, paths or download links: picks are indexes into what the backend itself read from Radarr/Sonarr minutes before."""
import datetime
import difflib
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import threading
import time
import urllib.parse

NOW = time.time
CACHE = {}
OFFERS = {}
THROTTLE = {}
MEM = {}
STORE_LOCK = threading.Lock()
SEARCH_LOCK = threading.Lock()
THROTTLE_LOCK = threading.Lock()
OFFERS_LOCK = threading.Lock()
SLOTS = threading.BoundedSemaphore(4)
DAY = 86400
KEEP_DAYS = 180
NO_RELEASE_DAYS = 3
OLD_YEARS = 12
STALE = 600
SCAN_TTL = 60
OFFER_TTL = 600
RELEASE_GAP = 120
LOOKUP_GAP = 2
FILES_GAP = 10
SEERR_TAKE = 1000
ACTION_GAP = 20
GRACE = 1800
RELEASE_TIMEOUT = 120
DECLINED, FAILED_REQUEST = 3, 4
PROBLEM_STATES = {"importBlocked", "importFailed", "failedPending", "failed"}
FAILED_STATES = {"failedPending", "failed"}
PID = re.compile(r"^[0-9a-f]{12}$")
ACTIONS = {"unmatched": {"link"}, "import": {"import"}, "wrong-film": {"block"}, "failed": {"block"}, "no-release": {"profile", "grab"}, "removed": {"readd", "delete"}}
RESOLVING = {"link", "import", "block", "grab", "readd", "delete"}
OLDER = "older versions allowed"
DEFAULT_OLDER = "4K preferred · older versions allowed"
SWISS = "Swiss German"
ID_MATCHED = re.compile(r"matched to (?:series|movie) by ID")
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
YEAR_SLACK = 2
EPISODE = (re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})(?:[ ._-]?[Ee-](\d{1,3}))?"), re.compile(r"(?<![\dx])(\d{1,2})x(\d{2,3})(?!\d)"))
NAMES = {"radarr": "Radarr", "sonarr": "Sonarr"}
# rejections a manual pick fixes (the arr could not tell which title or episode it is); any other one needs an admin
UNMATCHED = re.compile(r"unable to (parse|determine|identify|find)|not found|unknown (series|movie|episode)|matched to (series|movie) by id|no (series|movie|episode)s? (found|matched)", re.I)


class StoreUnreadable(Exception):
    pass


class Refused(Exception):
    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


def ts(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def epoch(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def brief(text):
    text = re.sub(r"https?://\S+|(?:/[^\s/]+)+/?", "…", str(text or "")).strip()
    return text if len(text) <= 160 else text[:159] + "…"


def year_of(s):
    m = YEAR.match(str(s or ""))
    return int(m.group()) if m else None


def read_state(path, now):
    try:
        p = pathlib.Path(path)
        if now - p.stat().st_mtime > STALE:
            return None
        data = json.loads(p.read_text())
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def older_profile(name, profiles):
    """The `· older versions allowed` twin of a profile; None when it is one already, Swiss German, or there is none."""
    if not name or OLDER in name or SWISS in name:
        return None
    names = [p.get("name") for p in profiles if isinstance(p, dict)]
    if name.startswith("Anime"):
        return next((n for n in names if n and n.startswith("Anime") and OLDER in n), None)
    want = DEFAULT_OLDER + (" - English original" if name.endswith(" - English original") else "")
    return want if want in names else None


def hard_rejection(reason):
    """A rejection only an admin may override: anything but "could not tell which title/episode", and always a sample."""
    reason = str(reason)
    return "sample" in reason.lower() or not UNMATCHED.search(reason)


def person(u):
    """A user's name for the page: Seerr's username / Jellyfin / Plex name, never the email Seerr falls back to."""
    u = u if isinstance(u, dict) else {}
    for k in ("username", "jellyfinUsername", "plexUsername"):
        v = u.get(k)
        if isinstance(v, str) and v.strip() and "@" not in v:
            return v.strip()[:60]
    return f"user {u.get('id')}"


def queue_kind(recs, year, kind):
    msgs = [str(m) for r in recs for sm in r.get("statusMessages") or [] for m in sm.get("messages") or []]
    if any(r.get("status") == "failed" or r.get("trackedDownloadState") in FAILED_STATES for r in recs):
        return "failed", {}
    if any(ID_MATCHED.search(m) for m in msgs):
        years = [int(y.group()) for y in YEAR.finditer(str(recs[0].get("title") or ""))]
        if kind == "movie" and year and years and not any(abs(year - y) <= YEAR_SLACK for y in years):
            return "wrong-film", {"releaseYear": years[-1], "year": year}
    return "import", {}


def pid_of(kind, key, extra=""):
    return hashlib.sha1(f"{kind}|{key}|{extra}".encode()).hexdigest()[:12]


def scan(dn, deps, known):
    """Every open problem now: (problems by id, complete). Incomplete (a source unreadable) = nothing may be called gone."""
    now, complete, found, arrs, profiles = NOW(), True, {}, {}, {}
    rs = read_state(deps.request_state_file, now) if deps.request_state_file else None
    if rs is None:
        complete, rs = False, {}
    try:
        reqs = (deps.seerr_admin("/request?take=1000&skip=0&filter=all&sort=added") or {}).get("results") if deps.seerr_admin else None
    except dn.ApiError:
        reqs = None
    if not isinstance(reqs, list):
        complete, reqs = False, []
    elif len(reqs) >= SEERR_TAKE:
        complete = False
    by_key, seerr_key = {}, {}
    for r in reqs:
        m = r.get("media") if isinstance(r, dict) else None
        if isinstance(m, dict) and m.get("tmdbId") and r.get("type") in dn.APP and r.get("status") != DECLINED and isinstance(r.get("requestedBy"), dict):
            key = f"{r['type']}/{m['tmdbId']}"
            by_key.setdefault(key, []).append(r)
            # Radarr/Sonarr may know the title under another TMDB id than Seerr: their own id (and the TVDB id) find the request too
            app = dn.APP[r["type"]]
            for ext in (m.get("externalServiceId"), m.get("externalServiceId4k")):
                if is_int(ext):
                    seerr_key.setdefault((app, "id", ext), key)
            if r["type"] == "tv" and is_int(m.get("tvdbId")):
                seerr_key.setdefault((app, "tvdb", m["tvdbId"]), key)

    def arr(app):
        if app not in arrs:
            arrs[app] = deps.arr(app)
        return arrs[app]

    def profile_list(app):
        if app not in profiles:
            got = arr(app)("/qualityprofile")
            profiles[app] = [p for p in got if isinstance(p, dict)] if isinstance(got, list) else []
        return profiles[app]

    def title_of(key):
        if known.get(key) and known[key][0]:
            return known[key]
        kind, _, tmdb = key.partition("/")
        try:
            t = deps.seerr_admin(f"/{kind}/{tmdb}") if deps.seerr_admin else None
        except dn.ApiError:
            t = None
        if not isinstance(t, dict):
            return key, None
        return (t.get("title") or t.get("name") or key), year_of(t.get("releaseDate") or t.get("firstAirDate"))

    def add(ptype, key, title, year, details, extra="", **internal):
        rq = by_key.get(key) or []
        names = list(dict.fromkeys(person(r["requestedBy"]) for r in rq))
        pid = pid_of(ptype, key, extra)
        internal.setdefault("requests", rq)
        found[pid] = {"id": pid, "type": ptype, "key": key, "title": title, "year": year, "requesters": names, "details": details, "_": internal}

    def base(key):
        kind, _, tmdb = key.partition("/")
        rq = by_key.get(key) or []
        media = rq[0]["media"] if rq else {}
        return {"kind": kind, "app": dn.APP.get(kind), "tmdb": int(tmdb) if tmdb.isdigit() else None, "media": media,
                "seasons": sorted({s.get("seasonNumber") for r in rq for s in r.get("seasons") or [] if isinstance(s, dict) and isinstance(s.get("seasonNumber"), int)})}

    for key in rs.get("removed") or []:
        if isinstance(key, str) and key.partition("/")[0] in dn.APP:
            add("removed", key, *title_of(key), {}, **base(key))
    for key, q in (rs.get("queue") or {}).items():
        if isinstance(q, dict) and q.get("code") == "seerr" and key.partition("/")[0] in dn.APP:
            add("unmatched", key, *title_of(key), {}, **base(key))
    unreleased = rs.get("unreleased") or {}
    for key, last in (rs.get("noRelease") or {}).items():
        b = base(key) if key.partition("/")[0] in dn.APP else None
        rq = by_key.get(key) or []
        created = [epoch(r.get("createdAt")) for r in rq]
        if b is None or key in unreleased or not rq or None in created or now - min(created) < NO_RELEASE_DAYS * DAY:
            continue
        title, year = title_of(key)
        details = {"lastSearch": last, "profile": None, "older": None, "old": False, "seasons": b["seasons"]}
        try:
            t = dn.find_title(b["kind"], arr(b["app"]), {"mediaInfo": b["media"]}, b["tmdb"])
            if t:
                title, year = t.get("title") or title, t.get("year") or year
                name = next((p.get("name") for p in profile_list(b["app"]) if p.get("id") == t.get("qualityProfileId")), None)
                details.update(profile=name, older=older_profile(name, profile_list(b["app"])))
                b["arrId"] = t.get("id")
        except dn.ApiError:
            pass
        details["old"] = bool(year) and int(year) <= time.gmtime(now).tm_year - OLD_YEARS
        add("no-release", key, title, year, details, **b)
    for app, kind, extra in (("radarr", "movie", "&includeMovie=true"), ("sonarr", "tv", "&includeSeries=true&includeEpisode=true")):
        try:
            q = arr(app)(f"/queue?page=1&pageSize=1000{extra}")
        except dn.ApiError:
            complete = False
            continue
        if not isinstance(q, dict) or not isinstance(q.get("records"), list):
            complete = False
            continue
        groups = {}
        for r in q["records"]:
            if not isinstance(r, dict) or not (r.get("trackedDownloadState") in PROBLEM_STATES or r.get("trackedDownloadStatus") == "error" or r.get("status") == "failed"):
                continue
            groups.setdefault(r.get("downloadId") or f"q{r.get('id')}", []).append(r)
        for dl, recs in groups.items():
            t = recs[0].get("movie" if kind == "movie" else "series") or {}
            tid = recs[0].get("movieId" if kind == "movie" else "seriesId")
            key = f"{kind}/{t['tmdbId']}" if t.get("tmdbId") else f"{app}:{tid}"
            if key not in by_key:
                key = seerr_key.get((app, "id", tid)) or seerr_key.get((app, "tvdb", t.get("tvdbId"))) or key
            ptype, more = queue_kind(recs, t.get("year"), kind)
            msg = next((m for r in recs for sm in r.get("statusMessages") or [] for m in sm.get("messages") or [] if m), None) or recs[0].get("errorMessage")
            details = {"release": str(recs[0].get("title") or "")[:200], "message": brief(msg), **more}
            eps = sorted({r.get("episodeId") for r in recs if isinstance(r.get("episodeId"), int)})
            if kind == "tv":
                details["episodes"] = len(recs)
            add(ptype, key, t.get("title") or key, t.get("year"), details, dl, kind=kind, app=app, arrId=tid, downloadId=recs[0].get("downloadId"),
                queueIds=[r.get("id") for r in recs if isinstance(r.get("id"), int)], episodeIds=eps, tmdb=t.get("tmdbId"), media={}, seasons=[])
    return found, complete


def load(deps):
    """The stored problem list; a missing file is an empty list, an unreadable one raises (it must never be overwritten)."""
    if not deps.manual_file:
        return MEM.setdefault("problems", {})
    path = pathlib.Path(deps.manual_file)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        p = data["problems"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StoreUnreadable(f"{path.name}: {type(e).__name__}") from None
    if not isinstance(p, dict):
        raise StoreUnreadable(f"{path.name}: no problem list")
    return p


def save(deps, records):
    if not deps.manual_file:
        MEM["problems"] = records
        return
    path = pathlib.Path(deps.manual_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"problems": records}, ensure_ascii=False, separators=(",", ":")))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def merge(records, found, complete, now):
    for pid, p in found.items():
        pub = {k: v for k, v in p.items() if k != "_"}
        r = records.get(pid)
        if r is None:
            records[pid] = {**pub, "firstSeen": ts(now), "lastSeen": ts(now), "actions": [], "resolved": None}
            continue
        r.update(pub, lastSeen=ts(now))
        res = r.get("resolved")
        if res and (res.get("how") == "gone" or now - (epoch(res.get("at")) or 0) >= GRACE):
            r["resolved"] = None
            r.setdefault("actions", []).append({"at": ts(now), "user": None, "userId": None, "action": "reopened", "result": None})
    for pid, r in list(records.items()):
        if complete and r.get("resolved") is None and pid not in found:
            r["resolved"] = {"at": ts(now), "how": "gone"}
        res = r.get("resolved")
        if res and now - (epoch(res.get("at")) or now) > KEEP_DAYS * DAY:
            del records[pid]


def refresh(dn, deps, force=False):
    """The current problems; force = a scan that started no earlier than this call. One scan at a time, callers waiting for it share its result."""
    start = NOW()

    def cached():
        c = CACHE.get("scan")
        return c[1] if c and (c[0] >= start or not force and NOW() - c[0] < SCAN_TTL) else None
    hit = cached()
    if hit is not None:
        return hit
    with STORE_LOCK:
        hit = cached()
        if hit is not None:
            return hit
        now = NOW()
        try:
            records, readable = load(deps), True
        except StoreUnreadable as e:
            print(f"manual: problem list unreadable, not saving until fixed: {e}", flush=True)
            records, readable = {}, False
        found, complete = scan(dn, deps, {r.get("key"): (r.get("title"), r.get("year")) for r in records.values() if isinstance(r, dict)})
        if readable:
            merge(records, found, complete, now)
            save(deps, records)
        CACHE["scan"] = (now, found)
    return found


def record(deps, pid, me, action, result, resolve):
    now = NOW()
    with STORE_LOCK:
        try:
            records = load(deps)
        except StoreUnreadable as e:
            print(f"manual: problem list unreadable, action not recorded: {e}", flush=True)
            return None
        r = records.get(pid)
        if r is None:
            return None
        r.setdefault("actions", []).append({"at": ts(now), "user": person(me), "userId": me.get("id"), "action": action, "result": result})
        if resolve:
            r["resolved"] = {"at": ts(now), "how": action}
        save(deps, records)
    return r


def throttle(key, gap, mark=True):
    with THROTTLE_LOCK:
        now = NOW()
        if now - THROTTLE.get(key, -gap - 1) < gap:
            return False
        if mark:
            THROTTLE[key] = now
        return True


def keep(pid, kind, me, data):
    """A list shown to one user, picked from by index: stored per user with a token the POST must send back."""
    token, now = secrets.token_hex(8), NOW()
    with OFFERS_LOCK:
        for k in [k for k, o in OFFERS.items() if now - o[0] > OFFER_TTL]:
            del OFFERS[k]
        OFFERS[(pid, kind, me.get("id"))] = (now, token, data)
    return token


def mine(pid, kind, me):
    """The caller's own still valid list (re-opening it is no new arr call): (token, data) or None."""
    with OFFERS_LOCK:
        o = OFFERS.get((pid, kind, me.get("id")))
    return (o[1], o[2]) if o and NOW() - o[0] <= OFFER_TTL else None


def offer(pid, kind, me, req):
    with OFFERS_LOCK:
        o = OFFERS.get((pid, kind, me.get("id")))
    if not o or NOW() - o[0] > OFFER_TTL or not isinstance(req.get("token"), str) or not secrets.compare_digest(o[1], req["token"]):
        return None
    return o[2]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def lookup(dn, deps, me, p, q):
    if p["type"] != "unmatched":
        raise Refused(400, "bad request")
    term = (q.get("term") or [p["title"] or ""])[0].strip()
    if not term or len(term) > 100:
        raise Refused(400, "bad request")
    if not throttle(("lookup", me.get("id")), LOOKUP_GAP):
        raise Refused(429, "slow down")
    i = p["_"]
    got = deps.arr(i["app"])(f"/{'movie' if i['kind'] == 'movie' else 'series'}/lookup?term={urllib.parse.quote_plus(term)}")
    items = [x for x in got if isinstance(x, dict)][:10] if isinstance(got, list) else []

    def score(x):
        s = difflib.SequenceMatcher(None, norm(x.get("title")), norm(p["title"])).ratio()
        y, py = x.get("year"), p.get("year")
        return s + (0.3 if y and py and y == py else 0.1 if y and py and abs(y - py) <= 1 else 0)
    scores = [score(x) for x in items]
    best = max(range(len(items)), key=lambda n: scores[n]) if items and max(scores) >= 0.6 else None
    token = keep(p["id"], "lookup", me, items)
    field = "tmdbId" if i["kind"] == "movie" else "tvdbId"
    return {"token": token, "results": [{"n": n, "title": str(x.get("title") or ""), "year": x.get("year"), "overview": str(x.get("overview") or "")[:200], field: x.get(field),
                         "inLibrary": bool(x.get("id")), "probable": n == best} for n, x in enumerate(items)]}


def episode_guess(name, by_number):
    for rx in EPISODE:
        m = rx.search(name)
        if m:
            s, first = int(m.group(1)), int(m.group(2))
            last = int(m.group(3)) if rx is EPISODE[0] and m.group(3) else first
            ids = [by_number.get((s, e)) for e in range(first, max(first, min(last, first + 30)) + 1)]
            return [x for x in ids if x]
    return []


def files(dn, deps, me, p, q):
    if p["type"] != "import":
        raise Refused(400, "bad request")
    had = mine(p["id"], "files", me)
    if had and not q.get("refresh"):
        return dict(had[1]["public"], token=had[0])
    if not throttle(("files", me.get("id")), LOOKUP_GAP, mark=False) or not throttle(("files", p["id"]), FILES_GAP, mark=False):
        raise Refused(429, "slow down")
    throttle(("files", me.get("id")), LOOKUP_GAP)
    throttle(("files", p["id"]), FILES_GAP)
    i = p["_"]
    arr = deps.arr(i["app"])
    dl = str(i.get("downloadId") or "")
    if not dl:
        raise Refused(400, "no download")
    # downloadId alone: with seriesId/movieId Sonarr lists the title's library folder instead
    got = arr(f"/manualimport?downloadId={urllib.parse.quote(dl, safe='')}&filterExistingFiles=true")
    props = [x for x in got if isinstance(x, dict) and x.get("path") and str(x.get("downloadId") or "").lower() == dl.lower()] if isinstance(got, list) else []
    episodes = []
    if i["kind"] == "tv":
        eps = arr(f"/episode?seriesId={i['arrId']}")
        episodes = sorted([e for e in eps if isinstance(e, dict) and is_int(e.get("id"))] if isinstance(eps, list) else [], key=lambda e: ((e.get("seasonNumber") or 0) == 0, e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))
    by_number = {(e.get("seasonNumber"), e.get("episodeNumber")): e["id"] for e in episodes}
    out = []
    biggest = max(range(len(props)), key=lambda n: props[n].get("size") or 0) if props else None
    for n, x in enumerate(props):
        name = str(x.get("relativePath") or x.get("name") or x["path"]).replace("\\", "/").rsplit("/", 1)[-1]
        queued = i.get("episodeIds") or []
        guess = ([e.get("id") for e in x.get("episodes") or [] if isinstance(e, dict) and is_int(e.get("id"))] or episode_guess(name, by_number)
                 or (queued if len(queued) == 1 else [])) if i["kind"] == "tv" else []
        out.append({"n": n, "name": name, "size": x.get("size") or 0, "quality": ((x.get("quality") or {}).get("quality") or {}).get("name") or (x.get("quality") or {}).get("name"),
                    "languages": [str(lang.get("name")) for lang in x.get("languages") or [] if isinstance(lang, dict) and lang.get("name")],
                    "rejections": [brief(r.get("reason") if isinstance(r, dict) else r) for r in x.get("rejections") or []][:5],
                    "episodeIds": guess, "probable": bool(guess) if i["kind"] == "tv" else n == biggest})
    public = {"kind": i["kind"], "files": out, "episodes": [{"id": e["id"], "label": f"S{e.get('seasonNumber') or 0:02d}E{e.get('episodeNumber') or 0:02d}", "title": str(e.get("title") or "")[:80]} for e in episodes]}
    token = keep(p["id"], "files", me, {"props": props, "episodes": {e["id"]: bool(e.get("hasFile")) for e in episodes}, "public": public})
    return dict(public, token=token)


def releases(dn, deps, me, p, q):
    if p["type"] != "no-release" or not p["_"].get("arrId"):
        raise Refused(400, "bad request")
    i = p["_"]
    season = None
    if i["kind"] == "tv" and i["seasons"]:
        raw = (q.get("season") or [str(i["seasons"][0])])[0]
        season = int(raw) if raw.isdigit() else None
        if season not in i["seasons"]:
            raise Refused(400, "bad request")
    had = mine(p["id"], "releases", me)
    if had and had[1]["season"] == season and not q.get("refresh"):
        return {"token": had[0], "releases": had[1]["public"], "season": season, "cached": True}
    cached = CACHE.get(("releases", p["id"]))
    if not throttle(("releases", p["id"]), RELEASE_GAP, mark=False):
        if cached and cached["season"] == season:
            return {"token": keep(p["id"], "releases", me, cached), "releases": cached["public"], "season": season, "cached": True}
        raise Refused(429, "slow down")
    if not SEARCH_LOCK.acquire(blocking=False):
        raise Refused(429, "busy")
    try:
        THROTTLE[("releases", p["id"])] = NOW()
        path = f"/release?movieId={i['arrId']}" if i["kind"] == "movie" else f"/release?seriesId={i['arrId']}" + (f"&seasonNumber={season}" if season is not None else "")
        got = deps.arr(i["app"])(path, timeout=RELEASE_TIMEOUT)
    finally:
        SEARCH_LOCK.release()
    items = [x for x in got if isinstance(x, dict) and x.get("guid") and is_int(x.get("indexerId"))] if isinstance(got, list) else []
    items.sort(key=lambda x: (bool(x.get("rejected")), -(x.get("customFormatScore") or 0), -(x.get("seeders") or 0)))
    items = items[:100]
    public = [{"n": n, "title": str(x.get("title") or "")[:200], "size": x.get("size") or 0, "quality": ((x.get("quality") or {}).get("quality") or {}).get("name"),
               "languages": [str(lang.get("name")) for lang in x.get("languages") or [] if isinstance(lang, dict) and lang.get("name")],
               "seeders": x.get("seeders"), "leechers": x.get("leechers"), "score": x.get("customFormatScore"), "rejected": bool(x.get("rejected")),
               "rejections": [brief(r.get("reason") if isinstance(r, dict) else r) for r in x.get("rejections") or []][:5],
               "indexer": str(x.get("indexer") or "")[:60], "protocol": x.get("protocol"), "age": x.get("age")} for n, x in enumerate(items)]
    data = {"season": season, "public": public, "items": [{"guid": x["guid"], "indexerId": x["indexerId"], "rejected": bool(x.get("rejected")), "title": str(x.get("title") or "")[:200]} for x in items]}
    CACHE[("releases", p["id"])] = data
    return {"token": keep(p["id"], "releases", me, data), "releases": public, "season": season, "cached": False}


def search(arr, i, episode_ids=None):
    if i["kind"] == "movie":
        arr("/command", "POST", {"name": "MoviesSearch", "movieIds": [i["arrId"]]})
    elif episode_ids:
        arr("/command", "POST", {"name": "EpisodeSearch", "episodeIds": episode_ids})
    else:
        arr("/command", "POST", {"name": "SeriesSearch", "seriesId": i["arrId"]})


def existing(arr, kind, obj):
    if kind == "movie":
        got = arr(f"/movie?tmdbId={int(obj['tmdbId'])}")
    else:
        got = arr(f"/series?tvdbId={int(obj['tvdbId'])}")
    return bool(got) if isinstance(got, list) else False


def add_title(deps, i, obj):
    app, kind = i["app"], i["kind"]
    arr = deps.arr(app)
    profiles = [p["id"] for p in arr("/qualityprofile") or [] if isinstance(p, dict)]
    roots = [r["path"] for r in arr("/rootfolder") or [] if isinstance(r, dict)]
    req = (i.get("requests") or [{}])[0]
    try:
        servers = deps.seerr_admin(f"/settings/{app}") if deps.seerr_admin else []
    except Exception:
        servers = []
    server = next((s for s in servers or [] if isinstance(s, dict) and s.get("isDefault") and not s.get("is4k")), {})
    anime = kind == "tv" and obj.get("seriesType") == "anime"
    want_profile = req.get("profileId") if req.get("profileId") in profiles else server.get("activeAnimeProfileId" if anime and server.get("activeAnimeProfileId") else "activeProfileId")
    want_root = req.get("rootFolder") if req.get("rootFolder") in roots else server.get("activeAnimeDirectory" if anime and server.get("activeAnimeDirectory") else "activeDirectory")
    body = {k: v for k, v in obj.items() if k != "id"}
    body.update(qualityProfileId=want_profile if want_profile in profiles else profiles[0], rootFolderPath=want_root if want_root in roots else roots[0], monitored=True, tags=[])
    if kind == "movie":
        body.update(minimumAvailability=obj.get("minimumAvailability") or "released", addOptions={"searchForMovie": True})
        arr("/movie", "POST", body)
    else:
        wanted = set(i.get("seasons") or [])
        body["seasons"] = [{**s, "monitored": (s.get("seasonNumber") in wanted) if wanted else (s.get("seasonNumber") or 0) > 0} for s in obj.get("seasons") or [] if isinstance(s, dict)]
        body.update(seasonFolder=True, addOptions={"searchForMissingEpisodes": True})
        arr("/series", "POST", body)


def do_link(deps, me, admin, p, req):
    items = offer(p["id"], "lookup", me, req)
    n = req.get("pick")
    if items is None or not is_int(n) or not 0 <= n < len(items):
        raise Refused(400, "bad request")
    i, obj = p["_"], items[n]
    field = "tmdbId" if i["kind"] == "movie" else "tvdbId"
    if not is_int(obj.get(field)):
        raise Refused(400, "bad request")
    media = i.get("media") or {}
    same = obj.get("tmdbId") == i["tmdb"] if i["kind"] == "movie" else (is_int(media.get("tvdbId")) and obj.get("tvdbId") == media["tvdbId"]) or (is_int(obj.get("tmdbId")) and obj.get("tmdbId") == i["tmdb"])
    if not same and not admin:
        raise Refused(403, "another title needs an admin")
    if not same:
        print(f"manual: admin {me.get('id')} links {p['key']} to {field} {obj[field]}", flush=True)
    arr = deps.arr(i["app"])
    if obj.get("id") or existing(arr, i["kind"], obj):
        result = f"already in {NAMES[i['app']]}"
    else:
        add_title(deps, i, obj)
        result = f"added to {NAMES[i['app']]} ({field} {obj[field]})"
    seerr_id = i["tmdb"] if i["kind"] == "movie" else (i.get("media") or {}).get("tvdbId")
    if obj[field] == seerr_id and deps.seerr_admin:
        for r in i.get("requests") or []:
            if r.get("status") == FAILED_REQUEST:
                deps.seerr_admin(f"/request/{r['id']}/retry", "POST")
                result += ", Seerr request retried"
    return result


def do_import(deps, me, admin, p, req):
    o = offer(p["id"], "files", me, req)
    want = req.get("files")
    i = p["_"]
    if o is None or not isinstance(want, list) or not 0 < len(want) <= 200:
        raise Refused(400, "bad request")
    seen, out = set(), []
    for f in want:
        n = f.get("n") if isinstance(f, dict) else None
        if not is_int(n) or not 0 <= n < len(o["props"]) or n in seen:
            raise Refused(400, "bad request")
        seen.add(n)
        x = o["props"][n]
        hard = [str(rj.get("reason") if isinstance(rj, dict) else rj) for rj in x.get("rejections") or [] if hard_rejection(rj.get("reason") if isinstance(rj, dict) else rj)]
        entry = {"path": x["path"], "quality": x.get("quality"), "languages": x.get("languages") or [], "releaseGroup": x.get("releaseGroup"),
                 "downloadId": x.get("downloadId") or i["downloadId"], "indexerFlags": x.get("indexerFlags") or 0}
        if i["kind"] == "movie":
            entry["movieId"] = i["arrId"]
        else:
            eps = f.get("episodeIds")
            if not isinstance(eps, list) or not 0 < len(eps) <= 50 or not all(is_int(e) and e in o["episodes"] for e in eps):
                raise Refused(400, "bad request")
            if any(o["episodes"][e] for e in eps):
                hard.append("episode already has a file")
            entry.update(seriesId=i["arrId"], episodeIds=eps, releaseType=x.get("releaseType") or "singleEpisode")
        if hard and not admin:
            raise Refused(403, "rejected file needs an admin")
        if hard:
            print(f"manual: admin {me.get('id')} overrides for {p['key']}: {'; '.join(brief(h) for h in hard)}", flush=True)
        out.append(entry)
    deps.arr(i["app"])("/command", "POST", {"name": "ManualImport", "files": out, "importMode": "auto"})
    return f"import of {len(out)} file(s) started"


def do_block(deps, me, admin, p, req):
    i = p["_"]
    if not i.get("queueIds"):
        raise Refused(400, "bad request")
    arr = deps.arr(i["app"])
    arr("/queue/bulk?removeFromClient=true&blocklist=true", "DELETE", {"ids": i["queueIds"]})
    search(arr, i, i.get("episodeIds"))
    return "release removed and blocked, searching again"


def do_profile(deps, me, admin, p, req):
    i, name = p["_"], p["details"].get("older")
    arr = deps.arr(i["app"])
    target = next((x.get("id") for x in arr("/qualityprofile") or [] if isinstance(x, dict) and x.get("name") == name), None) if name else None
    if target is None or not i.get("arrId"):
        raise Refused(400, "bad request")
    if i["kind"] == "movie":
        arr("/movie/editor", "PUT", {"movieIds": [i["arrId"]], "qualityProfileId": target})
    else:
        arr("/series/editor", "PUT", {"seriesIds": [i["arrId"]], "qualityProfileId": target})
    search(arr, i)
    return f"profile {name}, searching again"


def do_grab(deps, me, admin, p, req):
    o = offer(p["id"], "releases", me, req)
    n = req.get("release")
    if o is None or not is_int(n) or not 0 <= n < len(o["items"]) or o["items"][n]["rejected"]:
        raise Refused(400, "bad request")
    x = o["items"][n]
    deps.arr(p["_"]["app"])("/release", "POST", {"guid": x["guid"], "indexerId": x["indexerId"]})
    return f"grabbed {x['title']}"


def do_readd(deps, me, admin, p, req):
    i = p["_"]
    arr = deps.arr(i["app"])
    if i["kind"] == "movie":
        obj = arr(f"/movie/lookup/tmdb?tmdbId={int(i['tmdb'])}")
    else:
        tvdb = (i.get("media") or {}).get("tvdbId")
        if not is_int(tvdb):
            raise Refused(400, "no tvdb id")
        got = arr(f"/series/lookup?term=tvdb:{tvdb}")
        obj = got[0] if isinstance(got, list) and got else None
    if not isinstance(obj, dict) or not is_int(obj.get("tmdbId" if i["kind"] == "movie" else "tvdbId")):
        raise Refused(502, "not found")
    if existing(arr, i["kind"], obj):
        return f"already in {NAMES[i['app']]}"
    add_title(deps, i, obj)
    return f"added to {NAMES[i['app']]} again, searching"


def do_delete(deps, me, admin, p, req):
    if req.get("confirm") != "delete":
        raise Refused(400, "confirm")
    ids = [r.get("id") for r in p["_"].get("requests") or [] if is_int(r.get("id"))]
    if not ids or not deps.seerr_admin:
        raise Refused(400, "bad request")
    done = []
    for rid in ids:
        try:
            deps.seerr_admin(f"/request/{rid}", "DELETE")
        except Exception:
            record(deps, p["id"], me, "delete", f"deleted Seerr request(s) {', '.join(map(str, done)) or 'none'}, request {rid} failed", False)
            raise
        done.append(rid)
    return f"{len(ids)} Seerr request(s) deleted, title data and files kept"


DO = {"link": do_link, "import": do_import, "block": do_block, "profile": do_profile, "grab": do_grab, "readd": do_readd, "delete": do_delete}


def problem(dn, deps, raw, force=False):
    if not isinstance(raw, str) or not PID.match(raw):
        raise Refused(400, "bad request")
    p = refresh(dn, deps, force).get(raw)
    if p is None:
        raise Refused(404, "no such problem")
    return p


def act(dn, deps, me, req):
    p = problem(dn, deps, req.get("id"))
    action = req.get("action")
    admin = dn.allowed(me)
    if not isinstance(action, str) or action not in ACTIONS[p["type"]]:
        raise Refused(400, "bad request")
    if action == "delete" and not admin:
        raise Refused(403, "forbidden")
    if action == "delete" and req.get("confirm") != "delete":
        raise Refused(400, "confirm")
    if not throttle(("act", p["id"]), ACTION_GAP):
        raise Refused(429, "slow down")
    p = problem(dn, deps, p["id"], force=True)
    result = DO[action](deps, me, admin, p, req)
    print(f"manual: user {me.get('id')} {action} {p['type']} {p['key']} ({p['title']}): {result}", flush=True)
    rec = record(deps, p["id"], me, action, result, action in RESOLVING)
    CACHE.pop("scan", None)
    return {"ok": True, "result": result, "problem": rec}


def view(deps, admin):
    with STORE_LOCK:
        try:
            records = list(load(deps).values())
        except StoreUnreadable:
            records = []
    open_ = sorted((r for r in records if r.get("resolved") is None), key=lambda r: r.get("firstSeen") or "")
    done = sorted((r for r in records if r.get("resolved")), key=lambda r: r["resolved"].get("at") or "", reverse=True)[:200]
    return {"open": open_, "done": done, "admin": admin, "updated": ts(CACHE.get("scan", (NOW(),))[0])}


def handle(dn, method, path, headers, body, deps):
    url = urllib.parse.urlsplit(path)
    sub = url.path[len("/_download-now/manual/"):]
    h = {k.lower(): v for k, v in headers.items()}
    req = {}
    if method == "POST" and sub == "action":
        if h.get("origin") not in deps.origins:
            return 403, {"error": "origin"}
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            return 415, {"error": "json only"}
        try:
            req = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            return 400, {"error": "bad request"}
        if not isinstance(req, dict):
            return 400, {"error": "bad request"}
    elif not (method == "GET" and sub in ("problems", "count", "history", "lookup", "releases", "files")):
        return 404, {"error": "not found"}
    auth = dn.auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except dn.ApiError:
        me = None
    if not isinstance(me, dict) or not is_int(me.get("id")):
        return 403, {"error": "forbidden"}
    if not SLOTS.acquire(blocking=False):
        return 429, {"error": "busy"}
    try:
        return work(dn, deps, me, method, sub, url, req)
    finally:
        SLOTS.release()


def work(dn, deps, me, method, sub, url, req):
    admin = dn.allowed(me)
    q = urllib.parse.parse_qs(url.query)
    try:
        if method == "POST":
            return 200, act(dn, deps, me, req)
        if sub == "problems":
            refresh(dn, deps)
            return 200, view(deps, admin)
        if sub == "count":
            refresh(dn, deps)
            return 200, {"open": len({r.get("key") for r in view(deps, admin)["open"]})}
        if sub == "history":
            if not admin:
                return 403, {"error": "forbidden"}
            refresh(dn, deps)
            with STORE_LOCK:
                return 200, {"problems": list(load(deps).values())}
        p = problem(dn, deps, (q.get("id") or [""])[0])
        if sub == "lookup":
            return 200, lookup(dn, deps, me, p, q)
        if sub == "files":
            return 200, files(dn, deps, me, p, q)
        return 200, releases(dn, deps, me, p, q)
    except Refused as e:
        return e.status, {"error": e.error}
    except Exception as e:
        print(f"manual {sub}: {type(e).__name__} {dn.redact(e)}", flush=True)
        return 502, {"error": "not reachable"}


def loop(dn, deps, every=600):
    while True:
        try:
            refresh(dn, deps, force=True)
        except Exception as e:
            print(f"manual scan: {type(e).__name__} {dn.redact(e)}", flush=True)
        time.sleep(every)
