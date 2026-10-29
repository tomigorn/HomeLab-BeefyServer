"""Request backend for "Currently popular" (Jellyfin README "Currently popular"): any signed-in Jellyfin user may request a listed title.
POST /hss-popular/request {"id": <opaque list entry id>, "mode": "night" | "now", "check": bool}, GET /hss-popular/state, GET /hss-popular/health.
The caller is checked with Jellyfin (/Users/Me with their own token, disabled = refused); only entries of the current allow-list (scripts/popular.py) with a found release can be requested, nothing free-form.
The request goes to Seerr with its API key as the caller's linked Seerr user, else as the service user "jellyfin-popular". Every decision of a signed-in caller is written to the audit log (rotated);
unauthenticated and wrong-origin hits are only counted. Limits are counted in a small SQLite file next to the log."""
import collections
import concurrent.futures
import contextlib
import datetime
import hashlib
import http.server
import json
import os
import pathlib
import re
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
import zoneinfo

SERVICE_EMAIL = "jellyfin-popular"
SERVICE_NAME = "Jellyfin popular"
SERVICE_PERMISSIONS = 32 | 128  # REQUEST + AUTO_APPROVE
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
AUTH_RE = re.compile(r'Token="([^"]*)"')
ALLOWED_KEYS = {"id", "mode", "check"}
MAX_BODY = 2048
STATE_FRESH = datetime.timedelta(minutes=10)
AUDIT_MAX_BYTES = 1 << 20
AUDIT_KEEP = 3
KEEP_DAYS = 30
FLUSH_EVERY = datetime.timedelta(minutes=1)
AUTH_TTL = datetime.timedelta(seconds=30)
UPSTREAM_TIMEOUT = 8


class Unauthorized(Exception):
    pass


class Config:
    def __init__(self, allow_file, audit_file, request_state, origins, limit=10, days=7, daily_max=30, rate_per_min=10, list_max_age_days=9, tz="Europe/Zurich", state_db=None, audit_max_bytes=AUDIT_MAX_BYTES):
        self.allow_file, self.audit_file, self.request_state = pathlib.Path(allow_file), pathlib.Path(audit_file), pathlib.Path(request_state)
        self.state_db = pathlib.Path(state_db) if state_db else self.audit_file.with_name("state.db")
        self.origins, self.limit, self.days, self.daily_max, self.rate_per_min, self.list_max_age = origins, limit, days, daily_max, rate_per_min, datetime.timedelta(days=list_max_age_days)
        self.tz, self.audit_max_bytes = zoneinfo.ZoneInfo(tz), audit_max_bytes


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
    return "searching" if rs else None


class Core:
    def __init__(self, cfg, seerr, jellyfin, clock=None):
        self.cfg, self.seerr, self.jellyfin = cfg, seerr, jellyfin
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
        # Short critical sections only; Seerr and Jellyfin calls run outside, under the caller's own lock.
        self.lock = threading.Lock()
        self.svc_lock = threading.Lock()
        self.user_locks = {}
        self.auth_cache, self.hits, self.cache = {}, {}, {}
        self.noise, self.flushed = collections.Counter(), None
        self.db_init()

    # --- inputs -------------------------------------------------------
    def token(self, headers):
        tok = headers.get("x-emby-token") or ""
        if not tok:
            m = AUTH_RE.search(headers.get("authorization") or "")
            tok = m.group(1) if m else ""
        return tok if TOKEN_RE.match(tok) else None

    def user(self, headers):
        tok = self.token(headers)
        if not tok:
            raise Unauthorized()
        key = hashlib.sha256(tok.encode()).hexdigest()
        now = self.clock()
        with self.lock:
            hit = self.auth_cache.get(key)
        if hit and now - hit[0] < AUTH_TTL:
            return hit[1]
        u = self.jellyfin(tok)
        user = {"id": str(u["Id"]).replace("-", "").lower(), "name": u.get("Name") or "", "disabled": bool((u.get("Policy") or {}).get("IsDisabled"))}
        with self.lock:
            for k in [k for k, (at, _) in self.auth_cache.items() if now - at >= AUTH_TTL]:
                del self.auth_cache[k]
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

    # --- state db: requests (limits) and counters of refused anonymous hits ----------------
    @contextlib.contextmanager
    def db(self):
        con = sqlite3.connect(self.cfg.state_db, timeout=5, isolation_level=None)
        try:
            yield con
        finally:
            con.close()

    def db_init(self):
        self.cfg.state_db.parent.mkdir(parents=True, exist_ok=True)
        new = not self.cfg.state_db.exists()
        with self.db() as con:
            con.execute("CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, ref TEXT, at TEXT NOT NULL, day TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)")
            con.execute("CREATE INDEX IF NOT EXISTS requests_at ON requests (at)")
            con.execute("CREATE TABLE IF NOT EXISTS rejects (day TEXT NOT NULL, reason TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, reason))")
            if new:
                self.db_import(con)

    def db_import(self, con):
        """Requests of the audit log written before the state db existed."""
        try:
            lines = self.cfg.audit_file.read_text().splitlines()
        except OSError:
            return
        for line in lines:
            try:
                r = json.loads(line)
                at = datetime.datetime.fromisoformat(r["at"])
            except (ValueError, KeyError, TypeError):
                continue
            if r.get("decision") == "requested" and r.get("userId"):
                con.execute("INSERT INTO requests (user_id, ref, at, day, done) VALUES (?, ?, ?, ?, 1)", (r["userId"], r.get("ref"), self.iso(at), self.day(at)))

    def iso(self, t):
        return t.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")

    def day(self, t):
        return t.astimezone(self.cfg.tz).date().isoformat()

    def counts(self, con, user_id, now):
        since = self.iso(now - datetime.timedelta(days=self.cfg.days))
        mine = con.execute("SELECT COUNT(*) FROM requests WHERE user_id = ? AND at > ?", (user_id, since)).fetchone()[0]
        today = con.execute("SELECT COUNT(*) FROM requests WHERE day = ?", (self.day(now),)).fetchone()[0]
        return max(0, self.cfg.limit - mine), today

    def reserve(self, user_id, ref):
        """(left before this request, refusal or None, row id): the check and the reservation are one step, so parallel requests cannot pass the caps together."""
        now = self.clock()
        with self.lock, self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                left, today = self.counts(con, user_id, now)
                if left <= 0:
                    return left, "user limit", None
                if today >= self.cfg.daily_max:
                    return left, "daily cap", None
                rid = con.execute("INSERT INTO requests (user_id, ref, at, day) VALUES (?, ?, ?, ?)", (user_id, ref, self.iso(now), self.day(now))).lastrowid
                con.execute("DELETE FROM requests WHERE at < ?", (self.iso(now - datetime.timedelta(days=max(KEEP_DAYS, self.cfg.days + 1))),))
                return left, None, rid
            finally:
                con.execute("COMMIT")

    def settle(self, rid, ok):
        with self.lock, self.db() as con:
            con.execute("UPDATE requests SET done = 1 WHERE id = ?" if ok else "DELETE FROM requests WHERE id = ?", (rid,))

    def overview(self, user_id):
        now = self.clock()
        with self.db() as con:
            left, today = self.counts(con, user_id, now)
            recent = {r for (r,) in con.execute("SELECT ref FROM requests WHERE at > ?", (self.iso(now - STATE_FRESH),))}
        return left, today, recent

    def reject(self, reason):
        """Unauthenticated / wrong-origin hits: a counter, flushed to the db (and one stdout line) at most once a minute, never one log line per hit."""
        now = self.clock()
        with self.lock:
            self.noise[reason] += 1
            if self.flushed and now - self.flushed < FLUSH_EVERY:
                return
            noise, self.noise, self.flushed = self.noise, collections.Counter(), now
        with self.db() as con:
            for r, n in noise.items():
                con.execute("INSERT INTO rejects (day, reason, n) VALUES (?, ?, ?) ON CONFLICT (day, reason) DO UPDATE SET n = n + excluded.n", (self.day(now), r, n))
            con.execute("DELETE FROM rejects WHERE day < ?", (self.day(now - datetime.timedelta(days=KEEP_DAYS)),))
        print("refused " + ", ".join(f"{r} {n}" for r, n in sorted(noise.items())), flush=True)

    def db_rejects(self):
        with self.db() as con:
            return {r: n for r, n in con.execute("SELECT reason, SUM(n) FROM rejects GROUP BY reason")}

    def rejected(self):
        out = collections.Counter(self.db_rejects())
        with self.lock:
            out.update(self.noise)
        return dict(out)

    # --- audit log ----------------------------------------------------
    def audit(self, **rec):
        rec["at"] = self.clock().isoformat(timespec="seconds")
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        f = self.cfg.audit_file
        with self.lock:
            f.parent.mkdir(parents=True, exist_ok=True)
            try:
                full = f.stat().st_size + len(line.encode()) > self.cfg.audit_max_bytes
            except OSError:
                full = False
            if full:
                for i in range(AUDIT_KEEP - 1, 0, -1):
                    old = f.with_name(f"{f.name}.{i}")
                    if old.exists():
                        os.replace(old, f.with_name(f"{f.name}.{i + 1}"))
                os.replace(f, f.with_name(f.name + ".1"))
            with f.open("a") as out:
                out.write(line)

    @contextlib.contextmanager
    def user_lock(self, user_id):
        with self.lock:
            lock = self.user_locks.setdefault(user_id, threading.Lock())
        with lock:
            yield

    # --- Seerr ----------------------------------------------------------
    def seerr_user(self, jf_id):
        """(Seerr user id, why). Owner's choice: any Jellyfin user may request from this page, also without a Seerr account or request right; the audit log says which applied."""
        users = self.seerr("GET", "/api/v1/user?take=1000")["results"]
        linked = next((u for u in users if str(u.get("jellyfinUserId") or "").replace("-", "").lower() == jf_id), None)
        if linked and linked.get("permissions", 0) & (2 | 32):
            return linked["id"], "linked Seerr user"
        why = "service user: linked Seerr user may not request" if linked else "service user: no Seerr account"
        svc = next((u for u in users if (u.get("email") or "").lower() == SERVICE_EMAIL), None)
        if svc is None:
            with self.svc_lock:
                users = self.seerr("GET", "/api/v1/user?take=1000")["results"]
                svc = next((u for u in users if (u.get("email") or "").lower() == SERVICE_EMAIL), None)
                if svc is None:
                    svc = self.seerr("POST", "/api/v1/user", {"email": SERVICE_EMAIL, "username": SERVICE_NAME, "password": secrets.token_urlsafe(32)})
        if svc.get("permissions") != SERVICE_PERMISSIONS:
            self.seerr("POST", f"/api/v1/user/{svc['id']}/settings/permissions", {"permissions": SERVICE_PERMISSIONS})
        return svc["id"], why

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
        headers = {str(k).lower(): v for k, v in headers.items()}
        if path == "/hss-popular/health" and method == "GET":
            return 200, {"ok": True}
        if path == "/hss-popular/state" and method == "GET":
            return self.state(headers)
        if path == "/hss-popular/request" and method == "POST":
            return self.request(headers, body)
        return 404, {"error": "not found"}

    def state(self, headers):
        try:
            user = self.user(headers)
        except Unauthorized:
            self.reject("unauthenticated")
            return 401, {"error": "sign in to Jellyfin"}
        if user["disabled"]:
            return 403, {"error": "account disabled"}
        left, today, recent = self.overview(user["id"])
        media = self.media_list()
        rs = self.request_state()
        now = self.clock()
        entries = {}
        for x, e in self.allow().items():
            st = seerr_status(e["kind"], e["season"], media.get(f"{e['kind']}/{e['tmdb']}"))
            st = "owned" if st == "available" else "requested" if st == "requested" or e["ref"] in recent else "declined" if st == "declined" else "requestable"
            entries[x] = {"state": st, "badge": badge(e["kind"], e["tmdb"], rs, now) if st == "requested" else None}
        return 200, {"limit": self.cfg.limit, "days": self.cfg.days, "left": left, "dailyFull": today >= self.cfg.daily_max, "entries": entries}

    def rate_ok(self, user_id):
        now = self.clock()
        minute = datetime.timedelta(minutes=1)
        with self.lock:
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= minute]:
                del self.hits[k]
            recent = [t for t in self.hits.get(user_id, []) if now - t < minute]
            if len(recent) >= self.cfg.rate_per_min:
                return False
            self.hits[user_id] = recent + [now]
            return True

    def request(self, headers, body):
        if (headers.get("origin") or "") not in self.cfg.origins:
            self.reject("bad origin")
            return 403, {"error": "origin"}
        if (headers.get("content-type") or "").split(";")[0].strip().lower() != "application/json":
            self.reject("not JSON")
            return 415, {"error": "JSON only"}
        if len(body) > MAX_BODY:
            self.reject("too large")
            return 413, {"error": "too large"}
        try:
            user = self.user(headers)
        except Unauthorized:
            self.reject("unauthenticated")
            return 401, {"error": "sign in to Jellyfin"}
        if user["disabled"]:
            self.audit(user=user["name"], userId=user["id"], entry=None, decision="disabled user")
            return 403, {"error": "account disabled"}
        if not self.rate_ok(user["id"]):
            return 429, {"error": "slow down"}
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
        with self.user_lock(user["id"]):
            return self.send(user, entry, mode, req.get("check"), log)

    def send(self, user, entry, mode, check, log):
        details = self.seerr("GET", f"/api/v1/{entry['kind']}/{entry['tmdb']}?language=en")
        st = seerr_status(entry["kind"], entry["season"], details.get("mediaInfo"))
        if st in ("available", "requested", "declined"):
            state = "owned" if st == "available" else st
            self.audit(**log, decision=f"refused: {state}")
            return 409, {"error": state, "state": state, "left": self.overview(user["id"])[0]}
        if check:
            return 200, {"ok": True, "state": "requestable", "left": self.overview(user["id"])[0]}
        left, refused, rid = self.reserve(user["id"], entry["ref"])
        if refused == "user limit":
            self.audit(**log, decision="refused: user limit")
            return 429, {"error": "limit", "left": 0, "limit": self.cfg.limit, "days": self.cfg.days}
        if refused:
            self.audit(**log, decision="refused: daily cap")
            return 429, {"error": "daily cap", "left": left}
        ok = False
        try:
            payload = {"mediaType": entry["kind"], "mediaId": entry["tmdb"]}
            if entry["kind"] == "tv":
                payload["seasons"] = [entry["season"]]
            if mode == "now":
                tag = self.now_tag(entry["kind"])
                if tag is None:
                    self.audit(**log, decision="refused: no download-now tag")
                    return 503, {"error": "download now unavailable"}
                payload["tags"] = [tag]
            sid, via = self.seerr_user(user["id"])
            try:
                created = self.seerr("POST", "/api/v1/request", payload, user=sid)
            except urllib.error.HTTPError as e:
                self.audit(**log, decision=f"seerr refused: HTTP {e.code}", seerrUser=sid, via=via)
                return 502, {"error": "Seerr refused the request"}
            except (OSError, ValueError) as e:
                self.audit(**log, decision=f"seerr error: {type(e).__name__}", seerrUser=sid, via=via)
                return 502, {"error": "Seerr did not answer"}
            ok = True
        finally:
            self.settle(rid, ok)
        self.audit(**log, decision="requested", seerrUser=sid, via=via, requestId=(created or {}).get("id"))
        return 200, {"ok": True, "state": "requested", "left": left - 1}


# --- production wiring ------------------------------------------------------
def http_json(url, headers, method="GET", body=None, timeout=UPSTREAM_TIMEOUT):
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
                 limit=int(env.get("POP_LIMIT", "10")), days=int(env.get("POP_DAYS", "7")), daily_max=int(env.get("POP_DAILY_MAX", "30")), tz=env.get("TZ") or "Europe/Zurich")
    return Core(cfg, seerr, jellyfin)


def body_length(value):
    """Content-Length as an int (0 when absent), MAX_BODY + 1 for anything larger, None when invalid."""
    if value is None or value == "":
        return 0
    if not re.fullmatch(r"[0-9]{1,12}", value.strip()):
        return None
    return min(int(value), MAX_BODY + 1)


class Handler(http.server.BaseHTTPRequestHandler):
    core = None
    server_version = "hss-popular"
    sys_version = ""
    # Per socket operation: a client that sends nothing (or too slowly) is dropped after 10 s.
    timeout = 10

    def _go(self, method):
        n = body_length(self.headers.get("Content-Length"))
        if n is None:
            code, data = 400, {"error": "bad length"}
        else:
            try:
                body = self.rfile.read(n) if n else b""
                code, data = self.core.handle(method, self.path.split("?")[0], self.headers, body)
            except TimeoutError:
                self.close_connection = True
                return
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
        print("%s %s" % (getattr(self, "command", None) or "-", fmt % args), flush=True)


class Server(http.server.HTTPServer):
    """A fixed pool of workers (pids_limit 64) and a bounded queue: connections beyond it are closed at once instead of starting threads."""
    request_queue_size = 64

    def __init__(self, addr, handler, workers=16, backlog=32):
        super().__init__(addr, handler)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hss")
        self.slots = threading.BoundedSemaphore(workers + backlog)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        self.pool.submit(self.work, request, client_address)

    def work(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:  # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self.slots.release()

    def server_close(self):
        super().server_close()
        self.pool.shutdown(wait=False, cancel_futures=True)


def make_server(addr=("0.0.0.0", 8097), workers=16, backlog=32):
    return Server(addr, Handler, workers, backlog)


def main():
    Handler.core = make_core()
    make_server().serve_forever()


if __name__ == "__main__":
    main()
