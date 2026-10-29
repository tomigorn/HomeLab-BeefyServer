"""Request backend for "Currently popular" (Jellyfin README "Currently popular"): any signed-in Jellyfin user may request a listed title.
POST /hss-popular/request {"id": <opaque list entry id>, "mode": "night" | "now", "check": bool}, GET /hss-popular/state, GET /hss-popular/health.
The caller is checked with Jellyfin (/Users/Me with their own token, disabled = refused); only entries of the current allow-list (scripts/popular.py) with a found release can be requested, nothing free-form.
The request goes to Seerr with its API key as the caller's linked Seerr user, else as the service user "jellyfin-popular". Every decision is written to the audit log."""
import datetime
import hashlib
import http.server
import json
import os
import pathlib
import re
import secrets
import threading
import urllib.error
import urllib.request

SERVICE_EMAIL = "jellyfin-popular"
SERVICE_NAME = "Jellyfin popular"
SERVICE_PERMISSIONS = 32 | 128  # REQUEST + AUTO_APPROVE
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
AUTH_RE = re.compile(r'Token="([^"]*)"')
ALLOWED_KEYS = {"id", "mode", "check"}
MAX_BODY = 2048
STATE_FRESH = datetime.timedelta(minutes=10)


class Unauthorized(Exception):
    pass


class Config:
    def __init__(self, allow_file, audit_file, request_state, origins, limit=10, days=7, daily_max=30, rate_per_min=10, list_max_age_days=9):
        self.allow_file, self.audit_file, self.request_state = pathlib.Path(allow_file), pathlib.Path(audit_file), pathlib.Path(request_state)
        self.origins, self.limit, self.days, self.daily_max, self.rate_per_min, self.list_max_age = origins, limit, days, daily_max, rate_per_min, datetime.timedelta(days=list_max_age_days)


def seerr_status(kind, season, media):
    """None, available, requested, declined, removed (requested once, gone again)."""
    if not media:
        return None
    status = media.get("status")
    reqs = media.get("requests") or []
    if kind == "tv":
        status = {s.get("seasonNumber"): s.get("status") for s in media.get("seasons") or []}.get(season)
        reqs = [r for r in reqs if any(s.get("seasonNumber") == season for s in r.get("seasons") or [])]
    if status == 5:
        return "available"
    if status in (2, 3, 4) or any(r.get("status") in (1, 2) for r in reqs):
        return "requested"
    if any(r.get("status") == 3 for r in reqs):
        return "declined"
    if status in (6, 7) or any(r.get("status") == 5 for r in reqs):
        return "removed"
    return None


def badge(kind, tmdb, rs, now):
    """night / searching / unreleased / downloading for a requested title (request-state.json of the night run, ignored when older than 10 min)."""
    try:
        upd = datetime.datetime.fromisoformat(rs.get("updated"))
        upd = upd if upd.tzinfo else upd.astimezone()
        rs = rs if now - upd <= STATE_FRESH else {}
    except (TypeError, ValueError):
        rs = {}
    key = f"{kind}/{tmdb}"
    if key in (rs.get("unreleased") or {}):
        return "unreleased"
    if ((rs.get("queue") or {}).get(key) or {}).get("state") in ("downloading", "importing"):
        return "downloading"
    if key in (rs.get("searching") or {}) or key in (rs.get("noRelease") or {}):
        return "searching"
    if key in (rs.get("canWait") or []):
        return "night"
    return "searching"


class Core:
    def __init__(self, cfg, seerr, jellyfin, clock=None):
        self.cfg, self.seerr, self.jellyfin = cfg, seerr, jellyfin
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.lock = threading.Lock()
        self.auth_cache, self.hits, self.cache = {}, {}, {}

    # --- inputs -------------------------------------------------------
    def token(self, headers):
        tok = headers.get("X-Emby-Token") or ""
        if not tok:
            m = AUTH_RE.search(headers.get("Authorization") or "")
            tok = m.group(1) if m else ""
        return tok if TOKEN_RE.match(tok) else None

    def user(self, headers):
        tok = self.token(headers)
        if not tok:
            raise Unauthorized()
        key = hashlib.sha256(tok.encode()).hexdigest()
        now = self.clock()
        hit = self.auth_cache.get(key)
        if hit and now - hit[0] < datetime.timedelta(seconds=30):
            return hit[1]
        u = self.jellyfin(tok)
        user = {"id": str(u["Id"]).replace("-", "").lower(), "name": u.get("Name") or "", "disabled": bool((u.get("Policy") or {}).get("IsDisabled"))}
        self.auth_cache[key] = (now, user)
        return user

    def allow(self):
        try:
            data = json.loads(self.cfg.allow_file.read_text())
            upd = datetime.datetime.fromisoformat(data["updated"])
            upd = upd if upd.tzinfo else upd.astimezone()
        except (OSError, ValueError, KeyError):
            return {}
        if self.clock() - upd > self.cfg.list_max_age:
            return {}
        return {k: v for k, v in (data.get("entries") or {}).items() if v.get("res")}

    # --- audit log ----------------------------------------------------
    def audit(self, **rec):
        rec["at"] = self.clock().isoformat(timespec="seconds")
        self.cfg.audit_file.parent.mkdir(parents=True, exist_ok=True)
        with self.cfg.audit_file.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def history(self):
        try:
            lines = self.cfg.audit_file.read_text().splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                r = json.loads(line)
                r["_at"] = datetime.datetime.fromisoformat(r["at"])
                out.append(r)
            except (ValueError, KeyError):
                continue
        return [r for r in out if r.get("decision") == "requested"]

    def left(self, user, hist):
        since = self.clock() - datetime.timedelta(days=self.cfg.days)
        return max(0, self.cfg.limit - sum(1 for r in hist if r.get("userId") == user["id"] and r["_at"] > since))

    # --- Seerr ----------------------------------------------------------
    def seerr_user(self, jf_id):
        users = self.seerr("GET", "/api/v1/user?take=1000")["results"]
        linked = next((u for u in users if str(u.get("jellyfinUserId") or "").replace("-", "").lower() == jf_id), None)
        if linked and linked.get("permissions", 0) & (2 | 32):
            return linked["id"]
        svc = next((u for u in users if (u.get("email") or "").lower() == SERVICE_EMAIL), None)
        if svc is None:
            svc = self.seerr("POST", "/api/v1/user", {"email": SERVICE_EMAIL, "username": SERVICE_NAME, "password": secrets.token_urlsafe(32)})
        if svc.get("permissions") != SERVICE_PERMISSIONS:
            self.seerr("POST", f"/api/v1/user/{svc['id']}/settings/permissions", {"permissions": SERVICE_PERMISSIONS})
        return svc["id"]

    def now_tag(self, kind):
        svc = "radarr" if kind == "movie" else "sonarr"
        servers = self.seerr("GET", f"/api/v1/service/{svc}")
        default = next((s for s in servers if s.get("isDefault") and not s.get("is4k")), None)
        if not default:
            return None
        tags = self.seerr("GET", f"/api/v1/service/{svc}/{default['id']}").get("tags") or []
        return next((t["id"] for t in tags if t.get("label") == "download-now"), None)

    def media_list(self):
        now = self.clock()
        hit = self.cache.get("media")
        if hit and now - hit[0] < datetime.timedelta(seconds=60):
            return hit[1]
        out = {}
        for m in self.seerr("GET", "/api/v1/media?take=1000&filter=all").get("results") or []:
            out[f"{m.get('mediaType')}/{m.get('tmdbId')}"] = m
        self.cache["media"] = (now, out)
        return out

    def request_state(self):
        try:
            return json.loads(self.cfg.request_state.read_text())
        except (OSError, ValueError):
            return {}

    # --- endpoints ------------------------------------------------------
    def handle(self, method, path, headers, body):
        if path == "/hss-popular/health" and method == "GET":
            return 200, {"ok": True}
        if path == "/hss-popular/state" and method == "GET":
            return self.state(headers)
        if path == "/hss-popular/request" and method == "POST":
            with self.lock:
                return self.request(headers, body)
        return 404, {"error": "not found"}

    def state(self, headers):
        try:
            user = self.user(headers)
        except Unauthorized:
            return 401, {"error": "sign in to Jellyfin"}
        if user["disabled"]:
            return 403, {"error": "account disabled"}
        hist = self.history()
        media = self.media_list()
        rs = self.request_state()
        now = self.clock()
        recent = {r.get("ref") for r in hist if now - r["_at"] < STATE_FRESH}
        entries = {}
        for x, e in self.allow().items():
            st = seerr_status(e["kind"], e["season"], media.get(f"{e['kind']}/{e['tmdb']}"))
            st = "owned" if st == "available" else "requested" if st == "requested" or e["ref"] in recent else "declined" if st == "declined" else "requestable"
            entries[x] = {"state": st, "badge": badge(e["kind"], e["tmdb"], rs, now) if st == "requested" else None}
        return 200, {"limit": self.cfg.limit, "days": self.cfg.days, "left": self.left(user, hist), "entries": entries}

    def request(self, headers, body):
        if (headers.get("Origin") or "") not in self.cfg.origins:
            self.audit(user=None, userId=None, entry=None, decision="bad origin")
            return 403, {"error": "origin"}
        if (headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return 415, {"error": "JSON only"}
        if len(body) > MAX_BODY:
            return 413, {"error": "too large"}
        try:
            user = self.user(headers)
        except Unauthorized:
            self.audit(user=None, userId=None, entry=None, decision="unauthenticated")
            return 401, {"error": "sign in to Jellyfin"}
        if user["disabled"]:
            self.audit(user=user["name"], userId=user["id"], entry=None, decision="disabled user")
            return 403, {"error": "account disabled"}
        now = self.clock()
        recent = [t for t in self.hits.get(user["id"], []) if now - t < datetime.timedelta(minutes=1)]
        if len(recent) >= self.cfg.rate_per_min:
            return 429, {"error": "slow down"}
        self.hits[user["id"]] = recent + [now]
        try:
            req = json.loads(body)
        except ValueError:
            return 400, {"error": "bad JSON"}
        if not isinstance(req, dict) or set(req) - ALLOWED_KEYS or not isinstance(req.get("id"), str) or req.get("mode", "night") not in ("night", "now") or not isinstance(req.get("check", False), bool):
            self.audit(user=user["name"], userId=user["id"], entry=str(req.get("id"))[:64] if isinstance(req, dict) else None, decision="bad request")
            return 400, {"error": "only {id, mode, check}"}
        x, mode = req["id"], req.get("mode", "night")
        log = {"user": user["name"], "userId": user["id"], "entry": x[:64], "mode": mode}
        entry = self.allow().get(x)
        if not entry:
            self.audit(**log, decision="unknown entry")
            return 403, {"error": "not in the current list"}
        log.update(title=entry["title"], ref=entry["ref"])
        hist = self.history()
        left = self.left(user, hist)
        details = self.seerr("GET", f"/api/v1/{entry['kind']}/{entry['tmdb']}?language=en")
        st = seerr_status(entry["kind"], entry["season"], details.get("mediaInfo"))
        if st in ("available", "requested", "declined"):
            state = "owned" if st == "available" else st
            self.audit(**log, decision=f"refused: {state}")
            return 409, {"error": state, "state": state, "left": left}
        if req.get("check"):
            return 200, {"ok": True, "state": "requestable", "left": left}
        if left <= 0:
            self.audit(**log, decision="refused: user limit")
            return 429, {"error": "limit", "left": 0, "limit": self.cfg.limit, "days": self.cfg.days}
        today = sum(1 for r in hist if r["_at"].date() == now.date())
        if today >= self.cfg.daily_max:
            self.audit(**log, decision="refused: daily cap")
            return 429, {"error": "daily cap", "left": left}
        payload = {"mediaType": entry["kind"], "mediaId": entry["tmdb"]}
        if entry["kind"] == "tv":
            payload["seasons"] = [entry["season"]]
        if mode == "now":
            tag = self.now_tag(entry["kind"])
            if tag is None:
                self.audit(**log, decision="refused: no download-now tag")
                return 503, {"error": "download now unavailable"}
            payload["tags"] = [tag]
        sid = self.seerr_user(user["id"])
        try:
            created = self.seerr("POST", "/api/v1/request", payload, user=sid)
        except urllib.error.HTTPError as e:
            self.audit(**log, decision=f"seerr refused: HTTP {e.code}", seerrUser=sid)
            return 502, {"error": "Seerr refused the request"}
        self.audit(**log, decision="requested", seerrUser=sid, requestId=(created or {}).get("id"))
        return 200, {"ok": True, "state": "requested", "left": left - 1}


# --- production wiring ------------------------------------------------------
def http_json(url, headers, method="GET", body=None, timeout=20):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers, **({"Content-Type": "application/json"} if data else {})))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def make_core():
    env = os.environ
    jf = env.get("POP_JELLYFIN", "http://jellyfin:8096")
    seerr_url = env.get("POP_SEERR", "http://seerr:5055")
    key = pathlib.Path(env.get("POP_SEERR_KEY_FILE", "/run/secrets/popular/seerr.apikey")).read_text().strip()

    def jellyfin(token):
        try:
            return http_json(jf + "/Users/Me", {"Authorization": f'MediaBrowser Client="hss-popular", Device="hss-popular", DeviceId="hss-popular", Version="1", Token="{token}"'})
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                raise Unauthorized()
            raise

    def seerr(method, path, body=None, user=None):
        h = {"X-Api-Key": key}
        if user:
            h["X-Api-User"] = str(user)
        return http_json(seerr_url + path, h, method, body)

    cfg = Config(allow_file=env.get("POP_ALLOW", "/data/allow.json"), audit_file=env.get("POP_AUDIT", "/state/audit.jsonl"),
                 request_state=env.get("POP_REQUEST_STATE", "/indexer-bar/request-state.json"), origins=[o for o in env.get("POP_ORIGINS", "").split(",") if o],
                 limit=int(env.get("POP_LIMIT", "10")), days=int(env.get("POP_DAYS", "7")), daily_max=int(env.get("POP_DAILY_MAX", "30")))
    return Core(cfg, seerr, jellyfin)


class Handler(http.server.BaseHTTPRequestHandler):
    core = None
    server_version = "hss-popular"
    sys_version = ""

    def _go(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(n, MAX_BODY + 1)) if n else b""
        try:
            code, data = self.core.handle(method, self.path.split("?")[0], {k: v for k, v in self.headers.items()}, body)
        except Exception as e:  # noqa: BLE001
            self.log_message("error %s", type(e).__name__)
            code, data = 500, {"error": "internal"}
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")

    def log_message(self, fmt, *args):
        # The path only, never headers: tokens travel in headers.
        print("%s %s" % (self.command, fmt % args), flush=True)


def main():
    Handler.core = make_core()
    class Server(http.server.ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True
    Server(("0.0.0.0", 8097), Handler).serve_forever()


if __name__ == "__main__":
    main()
