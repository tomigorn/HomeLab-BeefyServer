#!/usr/bin/env python3
"""Download now (README "Download now"): the button on Seerr's title page. Seerr admins / manage-requests on any title, everyone else only on a title with their own (not declined) request; checked against Seerr itself on every call.
A press tags the title download-now in Radarr/Sonarr, moves its downloads out of the night categories (qBittorrent radarr-night/sonarr-night, SABnzbd movies-night/tv-night) into the normal ones and resumes the paused ones; nothing grabbed yet = a search, which grabs through the tagged normal clients.
GET /_download-now/api?type=movie|tv&tmdb=<id>: what a press would do (no change), with the title's search (queued/started, searches ahead, last search time). POST /_download-now/api {"type","tmdbId"}: do it. The API keys stay here, the browser only sees the state."""
import http.client
import http.server
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TAG = "download-now"
NIGHT_QBIT = {"radarr-night": "radarr", "sonarr-night": "sonarr"}
NIGHT_SAB = {"movies-night": "movies", "tv-night": "tv"}
PAUSED_QBIT = {"pausedDL", "stoppedDL"}
MAY = 2 | 16  # Seerr ADMIN | MANAGE_REQUESTS
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


def assess(kind, tmdb, media, deps):
    app = APP[kind]
    arr = deps.arr(app)
    title = find_title(kind, arr, media, tmdb)
    if not title:
        return None, {"state": "not-found", "app": app, "released": []}
    tag_id = next((t["id"] for t in arr("/tag") if t.get("label") == TAG), None)
    if tag_id is None:
        raise ApiError(f"{app}: no {TAG} tag")
    queue = arr("/queue?page=1&pageSize=1000")
    torrents = deps.qbit("/torrents/info")
    slots = deps.sab({"mode": "queue", "limit": "1000"})["queue"]["slots"]
    p = plan(kind, title, tag_id, queue.get("records") or [], torrents, slots)
    p.update(app=app, title_id=title["id"], tag_id=tag_id, last_search=read_search(kind, title, arr))
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
            "moved": len(p.get("qbit", [])) + len(p.get("sab", [])), "now": bool(p.get("now")), "search": p.get("last_search")}


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


def handle(method, path, headers, body, deps):
    url = urllib.parse.urlsplit(path)
    if url.path == "/_download-now/health" and method == "GET":
        return 200, {"ok": True}
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
        def call(path, method="GET", body=None):
            data = json.dumps(body).encode() if body is not None else None
            return _open(urllib.request.Request(f"{bases[app]}/api/v3{path}", data=data, method=method,
                                                headers={"X-Api-Key": keys[app], "Content-Type": "application/json"}))
        return call
    return make


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


def secret(name):
    return pathlib.Path(os.environ.get("DN_SECRETS", "/run/secrets/download-now"), name).read_text().strip()


def deps_from_env(env):
    return Deps(
        seerr=seerr_client(env.get("DN_SEERR", "http://seerr:5055")),
        arr=arr_client({"radarr": env.get("DN_RADARR", "http://radarr:7878"), "sonarr": env.get("DN_SONARR", "http://sonarr:8989")},
                       {"radarr": secret("radarr.apikey"), "sonarr": secret("sonarr.apikey")}),
        qbit=qbit_client(env.get("DN_QBIT", "http://gluetun:8080") + "/api/v2"),
        sab=sab_client(env.get("DN_SAB", "http://gluetun:8081"), secret("sabnzbd.apikey")),
        origins={o.strip() for o in env.get("DN_ORIGINS", "https://requests.holy-grail.ch").split(",") if o.strip()},
    )


class Handler(http.server.BaseHTTPRequestHandler):
    deps = None
    server_version = "download-now"
    timeout = 10
    sys_version = ""

    def _serve(self, method):
        try:
            n = max(0, min(int(self.headers.get("Content-Length") or 0), 4096))
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
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("DN_PORT", "8090"))), Handler)
    print(f"download-now listening on {srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
