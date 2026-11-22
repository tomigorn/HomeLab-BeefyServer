#!/usr/bin/env python3
"""Download now (README "Download now"): the button on Seerr's title page. Seerr admins / manage-requests on any title, everyone else only on a title with their own (not declined) request; checked against Seerr itself on every call.
A press tags the title download-now in Radarr/Sonarr, moves its downloads out of the night categories (qBittorrent radarr-night/sonarr-night, SABnzbd movies-night/tv-night) into the normal ones and resumes the paused ones; nothing grabbed yet = a search, which grabs through the tagged normal clients.
GET /_download-now/api?type=movie|tv&tmdb=<id>: what a press would do (no change), with the title's search (queued/started, searches ahead, last search time). POST /_download-now/api {"type","tmdbId"}: do it. The API keys stay here, the browser only sees the state.
GET /_download-now/night: the low tariff and the titles nobody shares (night_state), for night-label.js; every signed-in user.
GET /_download-now/origins: where each request came from (README "Request origin", origins.json of Jellyfin's popular backend); viewers of all requests get all, everyone else only their own.
GET /_download-now/by-size: the Requests page's GB sort, one sorted page of the caller's requests in Seerr's own format (by_size.py)."""
import http.client
import http.server
import json
import os
import pathlib
import re
import sys
import threading
import time
import datetime
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TAG = "download-now"
NIGHT_QBIT = {"radarr-night": "radarr", "sonarr-night": "sonarr"}
NIGHT_SAB = {"movies-night": "movies", "tv-night": "tv"}
PAUSED_QBIT = {"pausedDL", "stoppedDL"}
STALLED_QBIT = {"stalledDL", "metaDL", "forcedMetaDL"}
OVERRIDE = "override"
# same as Movie-Downloads' night-downloads.py: a paused override torrent without seeds seen for this long counts as seedless
SEEDLESS_H = 24
NIGHT_FILE_AGE = 10 * 60
NIGHT_TTL = 30
NIGHT_CACHE = {}
NIGHT_LOCK = threading.Lock()
CLOCK = time.time
MAY = 2 | 16  # Seerr ADMIN | MANAGE_REQUESTS
VIEW_ALL = 2 | 16 | 16384  # ADMIN | MANAGE_REQUESTS | REQUEST_VIEW
DECLINED = 3
APP = {"movie": "radarr", "tv": "sonarr"}
AUTH_HEADERS = ("X-Api-Key", "X-Api-User")
TMDB = re.compile(r"^[1-9][0-9]{0,9}$")
LOCK = threading.Lock()
SEARCH_COOLDOWN = 120
SEARCHED = {}
SEARCHES = {"MoviesSearch", "SeriesSearch", "SeasonSearch", "EpisodeSearch", "MissingMoviesSearch", "MissingEpisodeSearch", "CutoffUnmetMoviesSearch", "CutoffUnmetEpisodeSearch"}
RUNNING = ("queued", "started")


class ApiError(Exception):
    pass


@dataclass
class Deps:
    seerr: object
    arr: object
    qbit: object
    sab: object
    origins: set = field(default_factory=set)
    seerr_admin: object = None
    origins_file: object = None
    manual_file: object = None
    request_state_file: object = None
    download_roots: tuple = ()
    clamav_results: object = None
    scout_dir: object = None
    scout_requests: object = None
    collections_file: object = None
    night_waiting: object = None
    night_downloads: object = None
    retention_file: object = None
    retention_keep: object = None
    report_secret: object = None
    report_origin: object = None
    report_dry_run: bool = False
    bazarr: object = None
    file_check: object = None
    arr_image: object = None
    watched: object = None


def redact(text):
    return re.sub(r"\?\S*?(?=:|\s|$)", "", str(text))


def allowed(me):
    if not isinstance(me, dict):
        return False
    p = me.get("permissions")
    return isinstance(p, int) and not isinstance(p, bool) and bool(p & MAY)


def owns(me, media):
    uid = me.get("id") if isinstance(me, dict) else None
    if not isinstance(uid, int) or isinstance(uid, bool):
        return False
    info = media.get("mediaInfo") if isinstance(media, dict) else None
    reqs = info.get("requests") if isinstance(info, dict) else None
    if not isinstance(reqs, list):
        return False
    return any(isinstance(r, dict) and isinstance(r.get("requestedBy"), dict) and r["requestedBy"].get("id") == uid
               and r.get("status") != DECLINED for r in reqs)


def missing(kind, title):
    if kind == "movie":
        return not title.get("hasFile")
    s = title.get("statistics") or {}
    return (s.get("episodeFileCount") or 0) < (s.get("episodeCount") or 0)


def plan(kind, title, tag_id, queue, torrents, slots):
    key = "movieId" if kind == "movie" else "seriesId"
    ids = {r["downloadId"].lower() for r in queue if r.get(key) == title["id"] and r.get("downloadId")}
    qbit = [(t["hash"], NIGHT_QBIT[t["category"]], t.get("state") in PAUSED_QBIT) for t in torrents
            if t["hash"].lower() in ids and t.get("category") in NIGHT_QBIT and (t.get("progress") or 0) < 1]
    sab = [(s["nzo_id"], NIGHT_SAB[s["cat"]], s.get("status") == "Paused") for s in slots
           if s["nzo_id"].lower() in ids and s.get("cat") in NIGHT_SAB]
    has_tag = tag_id in (title.get("tags") or [])
    if qbit or sab:
        state = "started"
    elif ids:
        state = "downloading"
    elif missing(kind, title):
        state = "searching"
    else:
        state = "available"
    return {"state": state, "now": has_tag, "add_tag": state in ("started", "searching") and not has_tag, "qbit": qbit, "sab": sab,
            "search": state == "searching", "released": [h.lower() for h, _, _ in qbit] + [n.lower() for n, _, _ in sab]}


def search_info(kind, tid, commands, last):
    """The title's Radarr/Sonarr search: queued (with the searches ahead of it, Radarr runs them one at a time) or started; else the last search time."""
    cmds = [c for c in commands if isinstance(c, dict)] if isinstance(commands, list) else []
    def mine(c):
        b = c.get("body") or {}
        return tid in (b.get("movieIds") or []) if kind == "movie" else b.get("seriesId") == tid
    run = [c for c in cmds if c.get("name") in SEARCHES and c.get("status") in RUNNING]
    own = next((c for c in run if mine(c) and c.get("status") == "started"), None) or next((c for c in run if mine(c)), None)
    if not own:
        return {"status": None, "started": None, "ahead": 0, "last": last}
    ahead = 0 if own["status"] == "started" else sum(1 for c in run if c is not own and str(c.get("queued") or "") < str(own.get("queued") or ""))
    return {"status": own["status"], "started": own.get("started") if own["status"] == "started" else None, "ahead": ahead, "last": last}


def read_search(kind, title, arr):
    try:
        if kind == "movie":
            last = title.get("lastSearchTime")
        else:
            eps = arr(f"/episode?seriesId={title['id']}")
            last = max(filter(None, (e.get("lastSearchTime") for e in eps if isinstance(e, dict) and not e.get("hasFile"))), default=None) if isinstance(eps, list) else None
        return search_info(kind, title["id"], arr("/command"), last)
    except ApiError:
        return None


def find_title(kind, arr, media, tmdb):
    media = media if isinstance(media, dict) else {}
    info = media.get("mediaInfo") if isinstance(media.get("mediaInfo"), dict) else {}
    sid = info.get("externalServiceId")
    tvdb = info.get("tvdbId") or (media.get("externalIds") or {}).get("tvdbId")
    path = "/movie" if kind == "movie" else "/series"

    def same(t):
        return isinstance(t, dict) and (t.get("tmdbId") == tmdb or (kind == "tv" and tvdb and t.get("tvdbId") == tvdb))
    if isinstance(sid, int) and sid > 0:
        try:
            t = arr(f"{path}/{sid}")
            if same(t):
                return t
        except ApiError:
            pass
    if kind == "movie":
        found = arr(f"/movie?tmdbId={int(tmdb)}")
    else:
        found = arr(f"/series?tvdbId={int(tvdb)}") if tvdb else []
    found = [t for t in found if same(t)] if isinstance(found, list) else []
    return found[0] if found else None


def read_fresh(path, now):
    """A night-downloads file written in the last NIGHT_FILE_AGE, else None."""
    try:
        data = json.loads(pathlib.Path(path).read_text())
        age = now - datetime.datetime.fromisoformat(data["updated"]).timestamp()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return data if isinstance(data, dict) and -60 <= age <= NIGHT_FILE_AGE else None


def low_tariff(deps, now):
    data = read_fresh(deps.night_waiting, now) if deps.night_waiting else None
    if data and isinstance(data.get("low_tariff"), bool):
        return data["low_tariff"]
    t = datetime.datetime.fromtimestamp(now)
    return t.weekday() == 6 or t.hour >= 22 or t.hour < 6


def seed_history(deps, now):
    data = read_fresh(deps.night_downloads, now) if deps.night_downloads else None
    seen = data.get("seeded") if data else None
    return {str(k).lower(): v for k, v in seen.items() if isinstance(v, (int, float))} if isinstance(seen, dict) else None


def no_seeders(t, override, seen, now):
    """Nobody shares this torrent: running without seeds or speed; paused (no live numbers) only an override torrent night-downloads saw no seeds for in SEEDLESS_H."""
    if (t.get("progress") or 0) >= 1 or (t.get("num_seeds") or 0) > 0 or (t.get("num_complete") or 0) > 0 or (t.get("dlspeed") or 0) > 0:
        return False
    if t.get("state") in STALLED_QBIT:
        return True
    if t.get("state") in PAUSED_QBIT and override and seen is not None:
        return now - seen.get(str(t.get("hash")).lower(), float("-inf")) > SEEDLESS_H * 3600
    return False


def seedless(ids, torrents, override, seen, now):
    by = {str(t.get("hash")).lower(): t for t in torrents if isinstance(t, dict)}
    return bool(ids) and all(i in by and no_seeders(by[i], override, seen, now) for i in ids)


def night_state(deps):
    """{lowTariff, seedless: {movie|tv/<tmdb>: {override, ids}}} from Radarr/Sonarr's queues and qBittorrent, cached NIGHT_TTL."""
    now = CLOCK()
    with NIGHT_LOCK:
        hit = NIGHT_CACHE.get("night")
        if hit and now - hit[0] < NIGHT_TTL:
            return hit[1]
        titles = {}
        for app, kind, inc in (("radarr", "movie", "includeMovie=true"), ("sonarr", "series", "includeSeries=true")):
            arr = deps.arr(app)
            tag = next((t["id"] for t in arr("/tag") if t.get("label") == OVERRIDE), None)
            for r in arr(f"/queue?page=1&pageSize=1000&{inc}").get("records") or []:
                item = r.get("movie") or r.get("series") if isinstance(r, dict) else None
                if not isinstance(item, dict) or not item.get("tmdbId") or not r.get("downloadId"):
                    continue
                key = f"{'movie' if r.get('movie') else 'tv'}/{item['tmdbId']}"
                t = titles.setdefault(key, {"override": tag is not None and tag in (item.get("tags") or []), "ids": []})
                if r["downloadId"].lower() not in t["ids"]:
                    t["ids"].append(r["downloadId"].lower())
        torrents, seen = deps.qbit("/torrents/info"), seed_history(deps, now)
        out = {"lowTariff": low_tariff(deps, now),
               "seedless": {k: v for k, v in titles.items() if seedless(v["ids"], torrents, v["override"], seen, now)}}
        NIGHT_CACHE["night"] = (now, out)
        return out


def night(headers, deps):
    auth = auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except ApiError:
        me = None
    uid = me.get("id") if isinstance(me, dict) else None
    if not isinstance(uid, int) or isinstance(uid, bool):
        return 403, {"error": "forbidden"}
    try:
        return 200, night_state(deps)
    except Exception as e:
        print(f"night: {type(e).__name__} {redact(e)}", file=sys.stderr, flush=True)
        return 502, {"error": "download clients not reachable"}


def assess(kind, tmdb, media, deps):
    app = APP[kind]
    arr = deps.arr(app)
    title = find_title(kind, arr, media, tmdb)
    if not title:
        return None, {"state": "not-found", "app": app, "released": []}
    tags = arr("/tag")
    tag_id = next((t["id"] for t in tags if t.get("label") == TAG), None)
    if tag_id is None:
        raise ApiError(f"{app}: no {TAG} tag")
    queue = arr("/queue?page=1&pageSize=1000")
    torrents = deps.qbit("/torrents/info")
    slots = deps.sab({"mode": "queue", "limit": "1000"})["queue"]["slots"]
    records = queue.get("records") or []
    p = plan(kind, title, tag_id, records, torrents, slots)
    now = CLOCK()
    key = "movieId" if kind == "movie" else "seriesId"
    ids = sorted({r["downloadId"].lower() for r in records if r.get(key) == title["id"] and r.get("downloadId")})
    override = next((t["id"] for t in tags if t.get("label") == OVERRIDE), None) in (title.get("tags") or [])
    p.update(app=app, title_id=title["id"], tag_id=tag_id, last_search=read_search(kind, title, arr), low_tariff=low_tariff(deps, now),
             seedless=seedless(ids, torrents, override, seed_history(deps, now), now), override=override)
    return title, p


def apply(kind, p, deps):
    arr = deps.arr(p["app"])
    tid = p["title_id"]
    if p["add_tag"]:
        ids = "movieIds" if kind == "movie" else "seriesIds"
        arr("/movie/editor" if kind == "movie" else "/series/editor", "PUT", {ids: [tid], "tags": [p["tag_id"]], "applyTags": "add"})
    for cat in sorted({c for _, c, _ in p["qbit"]}):
        deps.qbit("/torrents/setCategory", {"hashes": "|".join(h for h, c, _ in p["qbit"] if c == cat), "category": cat})
    start = [h for h, _, paused in p["qbit"] if paused]
    if start:
        deps.qbit("/torrents/start", {"hashes": "|".join(start)})
    for nzo, cat, paused in p["sab"]:
        sab_ok(deps.sab({"mode": "change_cat", "value": nzo, "value2": cat}), "change_cat")
        if paused:
            sab_ok(deps.sab({"mode": "queue", "name": "resume", "value": nzo}), "resume")
    key = (kind, tid)
    if p["search"] and time.monotonic() - SEARCHED.get(key, -SEARCH_COOLDOWN) >= SEARCH_COOLDOWN:
        arr("/command", "POST", {"name": "MoviesSearch", "movieIds": [tid]} if kind == "movie" else {"name": "SeriesSearch", "seriesId": tid})
        SEARCHED[key] = time.monotonic()


def sab_ok(r, what):
    if not (isinstance(r, dict) and r.get("status") is True):
        raise ApiError(f"sabnzbd {what} refused")


def public(p):
    return {"state": p["state"], "app": p.get("app"), "released": p.get("released", []), "tagged": bool(p.get("add_tag")),
            "moved": len(p.get("qbit", [])) + len(p.get("sab", [])), "now": bool(p.get("now")), "search": p.get("last_search"),
            "lowTariff": bool(p.get("low_tariff")), "seedless": bool(p.get("seedless")), "override": bool(p.get("override"))}


def auth_of(headers):
    h = {k.lower(): v for k, v in headers.items()}
    out = {}
    sid = [c.strip() for c in h.get("cookie", "").split(";") if c.strip().startswith("connect.sid=")]
    if sid:
        out["Cookie"] = sid[0]
    for k in AUTH_HEADERS:
        if h.get(k.lower()):
            out[k] = h[k.lower()]
    return out


def read_origins(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return {}
    reqs = data.get("requests") if isinstance(data, dict) else None
    return {k: v for k, v in reqs.items() if isinstance(v, dict)} if isinstance(reqs, dict) else {}


def origins(headers, deps):
    auth = auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except ApiError:
        me = None
    uid = me.get("id") if isinstance(me, dict) else None
    if not isinstance(uid, int) or isinstance(uid, bool):
        return 403, {"error": "forbidden"}
    reqs = read_origins(deps.origins_file) if deps.origins_file else {}
    p = me.get("permissions")
    if not (isinstance(p, int) and p & VIEW_ALL):
        reqs = {k: v for k, v in reqs.items() if uid in (v.get("seerrUser"), v.get("person"))}
    return 200, {"requests": reqs}


def handle(method, path, headers, body, deps):
    url = urllib.parse.urlsplit(path)
    if url.path == "/_download-now/health" and method == "GET":
        return 200, {"ok": True}
    if url.path == "/_download-now/origins" and method == "GET":
        return origins(headers, deps)
    if url.path == "/_download-now/night" and method == "GET":
        return night(headers, deps)
    if url.path == "/_download-now/request":
        import my_request
        return my_request.handle(sys.modules[__name__], method, path, headers, body, deps)
    if url.path == "/_download-now/by-size":
        import by_size
        return by_size.handle(sys.modules[__name__], method, path, headers, body, deps)
    if url.path.startswith("/_download-now/collections/"):
        import collections_page
        return collections_page.handle(sys.modules[__name__], method, path, headers, body, deps)
    if url.path.startswith("/_download-now/retention/"):
        import retention_api
        return retention_api.handle(sys.modules[__name__], method, path, headers, body, deps)
    if url.path.startswith("/_download-now/manual/"):
        import manual
        return manual.handle(sys.modules[__name__], method, path, headers, body, deps)
    if url.path != "/_download-now/api" or method not in ("GET", "POST"):
        return 404, {"error": "not found"}
    h = {k.lower(): v for k, v in headers.items()}
    if method == "POST":
        if h.get("origin") not in deps.origins:
            return 403, {"state": "error", "error": "origin"}
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            return 415, {"state": "error", "error": "json only"}
        try:
            req = json.loads(body or b"{}")
            kind, tmdb = req.get("type"), str(req.get("tmdbId", ""))
        except (ValueError, AttributeError, RecursionError):
            return 400, {"state": "error", "error": "bad request"}
    else:
        q = urllib.parse.parse_qs(url.query)
        kind, tmdb = (q.get("type") or [None])[0], (q.get("tmdb") or [""])[0]
    if kind not in APP or not TMDB.match(tmdb):
        return 400, {"state": "error", "error": "bad request"}
    auth = auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except ApiError:
        me = None
    if not isinstance(me, dict):
        return 403, {"state": "error", "error": "forbidden"}
    try:
        media = deps.seerr(f"/{kind}/{tmdb}", auth)
    except ApiError:
        media = None
    role = "manager" if allowed(me) else "own request" if owns(me, media) else None
    if not role:
        return 403, {"state": "error", "error": "forbidden"}
    try:
        if method == "GET":
            title, p = assess(kind, int(tmdb), media, deps)
        else:
            with LOCK:
                title, p = assess(kind, int(tmdb), media, deps)
                if title:
                    apply(kind, p, deps)
                    p["now"] = p["now"] or p["add_tag"]
                    if p["state"] == "searching":
                        p["last_search"] = read_search(kind, title, deps.arr(p["app"]))
                    print(f"user {me.get('id')} ({role}) {kind} {tmdb} ({title.get('title')}): {p['state']}, moved {len(p['qbit']) + len(p['sab'])}, tag {'added' if p['add_tag'] else 'kept'}", flush=True)
    except Exception as e:
        print(f"{kind} {tmdb}: {type(e).__name__} {redact(e)}", file=sys.stderr, flush=True)
        return 502, {"state": "error", "error": "download clients not reachable"}
    return 200, public(p)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def _open(req, timeout=15):
    try:
        with OPENER.open(req, timeout=timeout) as r:
            data = r.read()
            return json.loads(data) if data.strip() else None
    except urllib.error.HTTPError as e:
        raise ApiError(f"{req.full_url.split('?')[0]}: {e.code}") from None
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as e:
        raise ApiError(f"{req.full_url.split('?')[0]}: {redact(e)}") from None


def seerr_client(base):
    def call(path, auth):
        return _open(urllib.request.Request(f"{base}/api/v1{path}", headers={**auth, "Accept": "application/json"}))
    return call


def arr_client(bases, keys):
    def make(app):
        def call(path, method="GET", body=None, timeout=15):
            data = json.dumps(body).encode() if body is not None else None
            return _open(urllib.request.Request(f"{bases[app]}/api/v3{path}", data=data, method=method,
                                                headers={"X-Api-Key": keys[app], "Content-Type": "application/json"}), timeout)
        return call
    return make


def arr_image_client(bases, keys, limit=2_000_000):
    """(app, path under the arr's root, e.g. /MediaCover/12/poster-250.jpg) -> the image bytes."""
    def call(app, path):
        req = urllib.request.Request(f"{bases[app]}{path}", headers={"X-Api-Key": keys[app]})
        try:
            with OPENER.open(req, timeout=15) as r:
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise ApiError(f"{path}: {e.code}") from None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
            raise ApiError(f"{path}: {redact(e)}") from None
        if not data or len(data) > limit:
            raise ApiError(f"{path}: not an image")
        return data
    return call


LEGACY = {"/torrents/start": "/torrents/resume"}


def qbit_client(base):
    def call(path, data=None):
        if data is None:
            return _open(urllib.request.Request(base + path))
        body = urllib.parse.urlencode(data).encode()
        try:
            return _open(urllib.request.Request(base + path, body))
        except ApiError:
            if path not in LEGACY:
                raise
            return _open(urllib.request.Request(base + LEGACY[path], body))
    return call


def sab_client(base, key):
    def call(params):
        return _open(urllib.request.Request(f"{base}/api?" + urllib.parse.urlencode({**params, "output": "json", "apikey": key})))
    return call


def seerr_admin_client(base, key):
    def call(path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        return _open(urllib.request.Request(f"{base}/api/v1{path}", data=data, method=method, headers={"X-Api-Key": key, "Content-Type": "application/json", "Accept": "application/json"}))
    return call


def bazarr_client(base, key):
    def call(path, method="GET"):
        return _open(urllib.request.Request(base + path, method=method, headers={"X-API-KEY": key, "Accept": "application/json"}), 60)
    return call


def file_checker(path):
    """Antivirus' download quality check (hook/quality_check.py, mounted read-only) for the problem report site; None when not mounted."""
    if not path or not pathlib.Path(path).is_file():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("quality_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check


def read_secret(path):
    try:
        return pathlib.Path(path).read_text().strip() or None
    except OSError:
        return None


def secret(name):
    return pathlib.Path(os.environ.get("DN_SECRETS", "/run/secrets/download-now"), name).read_text().strip()


def arr_bases(env):
    return {"radarr": env.get("DN_RADARR", "http://radarr:7878"), "sonarr": env.get("DN_SONARR", "http://sonarr:8989")}


def deps_from_env(env):
    return Deps(
        seerr=seerr_client(env.get("DN_SEERR", "http://seerr:5055")),
        arr=arr_client(arr_bases(env), {"radarr": secret("radarr.apikey"), "sonarr": secret("sonarr.apikey")}),
        arr_image=arr_image_client(arr_bases(env), {"radarr": secret("radarr.apikey"), "sonarr": secret("sonarr.apikey")}),
        qbit=qbit_client(env.get("DN_QBIT", "http://gluetun:8080") + "/api/v2"),
        sab=sab_client(env.get("DN_SAB", "http://gluetun:8081"), secret("sabnzbd.apikey")),
        origins={o.strip() for o in env.get("DN_ORIGINS", "https://requests.holy-grail.ch").split(",") if o.strip()},
        origins_file=env.get("DN_ORIGINS_FILE") or None,
        manual_file=env.get("DN_MANUAL_STATE") or None,
        request_state_file=env.get("DN_REQUEST_STATE") or None,
        download_roots=tuple(x.strip() for x in env.get("DN_DOWNLOAD_ROOTS", "").split(",") if x.strip()),
        clamav_results=env.get("DN_CLAMAV_RESULTS") or None,
        scout_dir=env.get("DN_SCOUT_DIR") or None,
        scout_requests=env.get("DN_SCOUT_REQUESTS") or None,
        collections_file=env.get("DN_COLLECTIONS") or None,
        night_waiting=env.get("DN_NIGHT_WAITING") or (str(pathlib.Path(env["DN_REQUEST_STATE"]).with_name("night-waiting.json")) if env.get("DN_REQUEST_STATE") else None),
        night_downloads=env.get("DN_NIGHT_DOWNLOADS") or (str(pathlib.Path(env["DN_SCOUT_DIR"], "night-downloads.json")) if env.get("DN_SCOUT_DIR") else None),
        retention_file=env.get("DN_RETENTION") or (str(pathlib.Path(env["DN_REQUEST_STATE"]).with_name("retention.json")) if env.get("DN_REQUEST_STATE") else None),
        retention_keep=env.get("DN_RETENTION_KEEP") or None,
        report_secret=read_secret(env["RP_SECRET_FILE"]) if env.get("RP_SECRET_FILE") else None,
        report_origin=env.get("RP_ORIGIN") or None,
        report_dry_run=env.get("RP_DRY_RUN") == "1",
        bazarr=bazarr_client(env.get("DN_BAZARR", "http://movie-bazarr:6767"), secret("bazarr.apikey")) if pathlib.Path(env.get("DN_SECRETS", "/run/secrets/download-now"), "bazarr.apikey").is_file() else None,
        file_check=file_checker(env.get("RP_QUALITY_CHECK")),
        seerr_admin=seerr_admin_client(env.get("DN_SEERR", "http://seerr:5055"), secret("seerr.apikey")) if pathlib.Path(env.get("DN_SECRETS", "/run/secrets/download-now"), "seerr.apikey").is_file() else None,
    )


class Handler(http.server.BaseHTTPRequestHandler):
    deps = None
    server_version = "download-now"
    timeout = 10
    sys_version = ""

    def _serve(self, method):
        try:
            n = max(0, min(int(self.headers.get("Content-Length") or 0), 16384))
        except ValueError:
            n = 0
        body = self.rfile.read(n) if n else b""
        status, out = handle(method, self.path, dict(self.headers.items()), body, self.deps)
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def log_message(self, fmt, *args):
        pass


def main():
    Handler.deps = deps_from_env(os.environ)
    if Handler.deps.manual_file:
        import manual
        threading.Thread(target=manual.loop, args=(sys.modules[__name__], Handler.deps), daemon=True).start()
    if Handler.deps.collections_file:
        import collections_page
        threading.Thread(target=collections_page.loop, args=(sys.modules[__name__], Handler.deps), daemon=True).start()
    if os.environ.get("RP_PORT") and Handler.deps.report_secret and Handler.deps.manual_file:
        import report
        report.serve(sys.modules[__name__], Handler.deps, int(os.environ["RP_PORT"]))
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("DN_PORT", "8090"))), Handler)
    print(f"download-now listening on {srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
