"""Request backend for "Currently popular" (Jellyfin README "Currently popular"): any signed-in Jellyfin user may request a listed title.
POST /hss-popular/request {"id": <opaque list entry id>, "mode": "night" | "now", "check": bool}, GET /hss-popular/state, GET /hss-popular/details?id=<opaque details id>, GET /hss-popular/health.
Details: only ids of the sidecars details.json (popular.py) and coming.json (upcoming-popularity.py), never a TMDB id, so it is no open TMDB proxy; those ids are never requestable.
The caller is checked with Jellyfin (/Users/Me with their own token, disabled = refused); only entries of the current allow-list (scripts/popular.py) with a found release can be requested, nothing free-form.
The request goes to Seerr with its API key as the caller's linked Seerr user, else as the service user "jellyfin-popular". Every decision of a signed-in caller is written to the audit log (rotated);
unauthenticated and wrong-origin hits are only counted. Limits are counted in a small SQLite file next to the log.
TV requests: a second listener on :8098 (internal network only, not published) takes Jellyfin Webhooks plugin events at POST /hook/<secret>. A Stop of a trending placeholder (trending-index.json of scripts/trending.py) played to its end (runtime - 2 s .. runtime + 30 s, from the Stop position or the tracked Play/Progress position plus unpaused time) requests the allow-list entry as the event's user in the mode of the played version, with the same limits, audit log and Seerr code; the placeholder's Played/position is reset for that user after every Stop. The flag file /state/hook-dry-run turns TV requests into checks."""
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
import urllib.parse
import urllib.request
import zoneinfo

SERVICE_EMAIL = "jellyfin-popular"
SERVICE_NAME = "Jellyfin popular"
SERVICE_PERMISSIONS = 32 | 128  # REQUEST + AUTO_APPROVE
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
DETAILS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
AUTH_RE = re.compile(r'Token="([^"]*)"')
ALLOWED_KEYS = {"id", "mode", "check"}
MAX_BODY = 2048
MAX_HOOK_BODY = 1 << 20
TICKS = 10_000_000
TRACK_FOR = datetime.timedelta(minutes=10)
HOOK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATE_FRESH = datetime.timedelta(minutes=10)
AUDIT_MAX_BYTES = 1 << 20
AUDIT_KEEP = 3
KEEP_DAYS = 30
FLUSH_EVERY = datetime.timedelta(minutes=1)
AUTH_TTL = datetime.timedelta(seconds=30)
UPSTREAM_TIMEOUT = 8
SERVICE_TTL = datetime.timedelta(minutes=5)


class NoServiceUser(Exception):
    """The Seerr service user does not exist and may not be created here."""


class Unauthorized(Exception):
    pass


class Config:
    def __init__(self, allow_file, audit_file, request_state, origins, limit=10, days=7, daily_max=30, rate_per_min=10, list_max_age_days=9, tz="Europe/Zurich", state_db=None, audit_max_bytes=AUDIT_MAX_BYTES,
                 details_files=None, details_per_min=60, index_file=None, hook_secret=None, signal_file=None, dry_flag=None):
        self.allow_file, self.audit_file, self.request_state = pathlib.Path(allow_file), pathlib.Path(audit_file), pathlib.Path(request_state)
        self.details_files = [pathlib.Path(f) for f in details_files] if details_files else [self.allow_file.with_name("details.json"), self.allow_file.with_name("coming.json")]
        self.details_per_min = details_per_min
        self.state_db = pathlib.Path(state_db) if state_db else self.audit_file.with_name("state.db")
        self.origins, self.limit, self.days, self.daily_max, self.rate_per_min, self.list_max_age = origins, limit, days, daily_max, rate_per_min, datetime.timedelta(days=list_max_age_days)
        self.tz, self.audit_max_bytes = zoneinfo.ZoneInfo(tz), audit_max_bytes
        self.index_file = pathlib.Path(index_file) if index_file else self.allow_file.with_name("trending-index.json")
        self.hook_secret = hook_secret or None
        self.signal_file = pathlib.Path(signal_file) if signal_file else None
        self.dry_flag = pathlib.Path(dry_flag) if dry_flag else self.audit_file.with_name("hook-dry-run")


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
    NoServiceUser = NoServiceUser

    def __init__(self, cfg, seerr, jellyfin, clock=None, jellyfin_admin=None):
        self.cfg, self.seerr, self.jellyfin, self.jellyfin_admin = cfg, seerr, jellyfin, jellyfin_admin
        self.tracked = {}
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

    def allow_raw(self):
        """Every current allow-list entry, also those without a found release."""
        try:
            data = json.loads(self.cfg.allow_file.read_text())
            upd = datetime.datetime.fromisoformat(data["updated"])
            upd = upd if upd.tzinfo else upd.astimezone()
        except (OSError, ValueError, KeyError):
            return {}
        if self.clock() - upd > self.cfg.list_max_age:
            return {}
        return data.get("entries") or {}

    def allow(self):
        return {k: v for k, v in self.allow_raw().items() if v.get("res")}

    def allow_index(self):
        """(kind, tmdb) -> requestable entry; rows listing the same title collapse to the first one."""
        out = {}
        for e in self.allow().values():
            out.setdefault((e.get("kind"), e.get("tmdb")), e)
        return out

    def no_release(self, kind, tmdb):
        """Listed (details sidecars or the allow-list) but without a found release, so not requestable yet."""
        if (kind, tmdb) in self.allow_index():
            return False
        if any(e.get("kind") == kind and e.get("tmdb") == tmdb for e in self.allow_raw().values()):
            return True
        return any(isinstance(e, dict) and e.get("k") == kind and e.get("id") == tmdb for f in self.cfg.details_files for e in self.sidecar(f).values())

    def sidecar(self, path):
        """Entries of one details sidecar (parsed once per change of the file), {} when missing, broken or older than the allow-list may be."""
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {}
        with self.lock:
            hit = self.cache.get(("sidecar", str(path)))
        if hit and hit[0] == sig:
            data = hit[1]
        else:
            try:
                data = json.loads(path.read_text())
                data["at"] = datetime.datetime.fromisoformat(data["updated"])
                data["at"] = data["at"] if data["at"].tzinfo else data["at"].astimezone()
                if not isinstance(data.get("entries"), dict):
                    raise ValueError
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                data = {"at": None, "entries": {}}
            with self.lock:
                self.cache[("sidecar", str(path))] = (sig, data)
        if data["at"] is None or self.clock() - data["at"] > self.cfg.list_max_age:
            return {}
        return data["entries"]

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
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'user_requests'").fetchone():
                con.execute("CREATE TABLE IF NOT EXISTS user_requests (user_id TEXT NOT NULL, seerr_id INTEGER NOT NULL, at TEXT, PRIMARY KEY (user_id, seerr_id))")
                self.user_requests_import(con)

    def audit_lines(self):
        """Lines of the audit log and its rotations, oldest first."""
        f = self.cfg.audit_file
        for path in [f.with_name(f"{f.name}.{i}") for i in range(AUDIT_KEEP, 0, -1)] + [f]:
            try:
                yield from path.read_text().splitlines()
            except OSError:
                continue

    def user_requests_import(self, con):
        """Seerr request ids of the audit log written before the user_requests table existed."""
        for line in self.audit_lines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or r.get("decision") != "requested":
                continue
            uid, rid = r.get("userId"), r.get("requestId")
            if not isinstance(uid, str) or not uid or not isinstance(rid, int) or isinstance(rid, bool):
                continue
            try:
                at = self.iso(datetime.datetime.fromisoformat(r["at"]))
            except (KeyError, TypeError, ValueError):
                at = None
            con.execute("INSERT OR IGNORE INTO user_requests (user_id, seerr_id, at) VALUES (?, ?, ?)", (uid, rid, at))

    def remember(self, user_id, seerr_id):
        with self.lock, self.db() as con:
            con.execute("INSERT OR IGNORE INTO user_requests (user_id, seerr_id, at) VALUES (?, ?, ?)", (user_id, seerr_id, self.iso(self.clock())))

    def requested_ids(self, user_id):
        """Seerr request ids the user made through this backend, newest first; never pruned."""
        with self.db() as con:
            return [r for (r,) in con.execute("SELECT seerr_id FROM user_requests WHERE user_id = ? ORDER BY at DESC, seerr_id DESC", (user_id,))]

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
    def seerr_user(self, jf_id, create=True):
        """(Seerr user id, why). Owner's choice: any Jellyfin user may request from this page, also without a Seerr account or request right; the audit log says which applied."""
        users = self.seerr("GET", "/api/v1/user?take=1000")["results"]
        linked = next((u for u in users if str(u.get("jellyfinUserId") or "").replace("-", "").lower() == jf_id), None)
        if linked and linked.get("permissions", 0) & (2 | 32):
            return linked["id"], "linked Seerr user"
        why = "service user: linked Seerr user may not request" if linked else "service user: no Seerr account"
        return self.service_user(create)["id"], why

    def service_user(self, create=True):
        """The Seerr service user "jellyfin-popular", cached 5 min; create=False never writes to Seerr and raises NoServiceUser when it is missing."""
        now = self.clock()
        with self.lock:
            hit = self.cache.get("service_user")
        if hit and now - hit[0] < SERVICE_TTL:
            return hit[1]
        svc = self.find_service_user()
        if svc is None:
            if not create:
                raise NoServiceUser()
            with self.svc_lock:
                svc = self.find_service_user()
                if svc is None:
                    svc = self.seerr("POST", "/api/v1/user", {"email": SERVICE_EMAIL, "username": SERVICE_NAME, "password": secrets.token_urlsafe(32)})
        if svc.get("permissions") != SERVICE_PERMISSIONS:
            if not create:
                return svc
            self.seerr("POST", f"/api/v1/user/{svc['id']}/settings/permissions", {"permissions": SERVICE_PERMISSIONS})
            svc = {**svc, "permissions": SERVICE_PERMISSIONS}
        with self.lock:
            self.cache["service_user"] = (now, svc)
        return svc

    def find_service_user(self):
        users = self.seerr("GET", "/api/v1/user?take=1000")["results"]
        return next((u for u in users if (u.get("email") or "").lower() == SERVICE_EMAIL), None)

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
        path, _, query = path.partition("?")
        if path == "/hss-popular/health" and method == "GET":
            return 200, {"ok": True}
        if path == "/hss-popular/state" and method == "GET":
            return self.state(headers)
        if path == "/hss-popular/details" and method == "GET":
            return self.details(headers, query)
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

    def rate_ok(self, user_id, bucket="request", per_min=None):
        now = self.clock()
        minute = datetime.timedelta(minutes=1)
        key = (bucket, user_id)
        with self.lock:
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= minute]:
                del self.hits[k]
            recent = [t for t in self.hits.get(key, []) if now - t < minute]
            if len(recent) >= (per_min or self.cfg.rate_per_min):
                return False
            self.hits[key] = recent + [now]
            return True

    def details(self, headers, query):
        try:
            user = self.user(headers)
        except Unauthorized:
            self.reject("unauthenticated")
            return 401, {"error": "sign in to Jellyfin"}
        if user["disabled"]:
            return 403, {"error": "account disabled"}
        if not self.rate_ok(user["id"], "details", self.cfg.details_per_min):
            return 429, {"error": "slow down"}
        try:
            q = urllib.parse.parse_qs(query, keep_blank_values=True, strict_parsing=bool(query), max_num_fields=2)
        except ValueError:
            return 400, {"error": "only ?id="}
        ids = q.get("id") or []
        if set(q) != {"id"} or len(ids) != 1 or not DETAILS_ID_RE.match(ids[0]):
            return 400, {"error": "only ?id="}
        for f in self.cfg.details_files:
            entry = self.sidecar(f).get(ids[0])
            if isinstance(entry, dict):
                return 200, {"details": {k: v for k, v in entry.items() if k != "ref"}}
        return 404, {"error": "unknown"}

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
            code, out = self.send(user, entry, mode, req.get("check"), log)
        out.pop("request", None)
        return code, out

    def send(self, user, entry, mode, check, log, create_service=True):
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
            try:
                sid, via = self.seerr_user(user["id"], create=create_service)
            except NoServiceUser:
                self.audit(**log, decision="refused: no service user")
                return 503, {"error": "service user missing"}
            try:
                created = self.seerr("POST", "/api/v1/request", payload, user=sid)
            except urllib.error.HTTPError as e:
                e.close()
                self.audit(**log, decision=f"seerr refused: HTTP {e.code}", **self.who(log, sid, via))
                return 502, {"error": "Seerr refused the request"}
            except (OSError, ValueError) as e:
                self.audit(**log, decision=f"seerr error: {type(e).__name__}", **self.who(log, sid, via))
                return 502, {"error": "Seerr did not answer"}
            ok = True
        finally:
            self.settle(rid, ok)
        request_id = created.get("id") if isinstance(created, dict) else None
        self.audit(**log, decision="requested", requestId=request_id, **self.who(log, sid, via))
        if isinstance(request_id, int) and not isinstance(request_id, bool):
            self.remember(user["id"], request_id)
        return 200, {"ok": True, "state": "requested", "left": left - 1, "request": created}

    @staticmethod
    def who(log, sid, via):
        return {"seerrUser": sid, "seerrVia" if "via" in log else "via": via}

    # --- TV playback hook -------------------------------------------------
    def trending_index(self):
        path = self.cfg.index_file
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {}
        with self.lock:
            hit = self.cache.get("index")
        if hit and hit[0] == sig:
            return hit[1]
        try:
            data = json.loads(path.read_text())
            items = data["items"] if isinstance(data.get("items"), dict) else {}
            data = {"runtime": int(data.get("runtimeTicks") or 0), "items": items}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            data = {"runtime": 0, "items": {}}
        with self.lock:
            self.cache["index"] = (sig, data)
        return data

    @staticmethod
    def hook_id(value):
        v = str(value or "").replace("-", "").lower()
        return v if HOOK_ID_RE.match(v) else None

    @staticmethod
    def ticks(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

    def estimate(self, rec, now):
        if rec is None:
            return None
        return rec["pos"] if rec["paused"] else rec["pos"] + int((now - rec["at"]).total_seconds() * TICKS)

    def hook(self, path, headers, body):
        m = re.fullmatch(r"/hook/([^/?#]*)", path.partition("?")[0])
        if not m:
            return 404, {"error": "not found"}
        if not self.cfg.hook_secret or not secrets.compare_digest(m.group(1).encode(), self.cfg.hook_secret.encode()):
            self.reject("bad hook secret")
            return 403, {"error": "forbidden"}
        if len(body) > MAX_HOOK_BODY:
            return 413, {"error": "too large"}
        try:
            p = json.loads(body)
        except ValueError:
            return 400, {"error": "bad JSON"}
        if not isinstance(p, dict):
            return 400, {"error": "bad JSON"}
        item = p.get("Item") if isinstance(p.get("Item"), dict) else {}
        iid = self.hook_id(item.get("Id"))
        idx = self.trending_index()
        rec = idx["items"].get(iid) if iid else None
        if not isinstance(rec, dict):
            return 204, {}
        sess = p.get("Session") if isinstance(p.get("Session"), dict) else {}
        user = p.get("User") if isinstance(p.get("User"), dict) else {}
        ps = sess.get("PlayState") if isinstance(sess.get("PlayState"), dict) else {}
        uid = self.hook_id(user.get("Id"))
        event = p.get("Event")
        if not uid or event not in ("Play", "Progress", "Pause", "Resume", "Stop"):
            return 204, {}
        now = self.clock()
        key = (str(sess.get("Id") or ""), iid)
        ms, pos = self.hook_id(ps.get("MediaSourceId")), self.ticks(ps.get("PositionTicks"))
        with self.lock:
            for k in [k for k, v in self.tracked.items() if now - v["at"] > TRACK_FOR]:
                del self.tracked[k]
            old = self.tracked.get(key)
            if event != "Stop":
                paused = event == "Pause" or (event != "Resume" and bool(ps.get("IsPaused")))
                if event == "Play":
                    est = pos or 0
                else:
                    est = pos if pos is not None else (self.estimate(old, now) or 0)
                self.tracked[key] = {"ms": ms or (old or {}).get("ms"), "pos": est, "at": now, "paused": paused}
                return 204, {}
            self.tracked.pop(key, None)
        try:
            self.tv_stop(rec, idx, item, user, uid, sess, ms or (old or {}).get("ms"), pos if pos is not None else self.estimate(old, now))
        finally:
            self.reset(iid, uid)
        return 204, {}

    def tv_stop(self, rec, idx, item, user, uid, sess, ms, est):
        x = rec.get("x")
        if not x:
            return
        mode = (rec.get("modes") or {}).get(ms) if ms else None
        name = str(user.get("Name") or "")
        log = {"user": name, "userId": uid, "entry": str(x)[:64], "mode": mode, "via": "tv", "client": str(sess.get("Client") or "")[:40]}
        policy = user.get("Policy") if isinstance(user.get("Policy"), dict) else {}
        if bool(policy.get("IsDisabled")):
            self.audit(**log, decision="disabled user")
            return
        if mode not in ("now", "night"):
            self.audit(**log, decision="tv: unknown version")
            return
        runtime = self.ticks(item.get("RunTimeTicks")) or idx["runtime"]
        if est is None or not runtime or est < runtime - 2 * TICKS:
            self.audit(**log, decision="tv: stopped early")
            return
        if est > runtime + 30 * TICKS:
            self.audit(**log, decision="tv: stale stop")
            return
        entry = self.allow().get(x)
        if not entry:
            self.audit(**log, decision="unknown entry")
            return
        log.update(title=entry["title"], ref=entry["ref"])
        dry = self.cfg.dry_flag.exists()
        with self.user_lock(uid):
            code, _ = self.send({"id": uid, "name": name}, entry, mode, dry, log)
        if dry and code == 200:
            self.audit(**log, decision="dry-run: requestable")
        elif code == 200 and self.cfg.signal_file:
            try:
                self.cfg.signal_file.touch()
            except OSError as e:
                print(f"signal failed {type(e).__name__}", flush=True)

    def reset(self, iid, uid):
        if not self.jellyfin_admin:
            return
        try:
            self.jellyfin_admin("POST", f"/UserItems/{iid}/UserData?userId={uid}", {"Played": False, "PlaybackPositionTicks": 0})
        except Exception as e:  # noqa: BLE001
            print(f"reset failed {type(e).__name__}", flush=True)


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

    jf_key = secret_file(env.get("POP_JELLYFIN_KEY_FILE", "/run/secrets/popular/jellyfin.apikey"))

    def jellyfin_admin(method, path, body=None):
        return http_json(jf + path, {"Authorization": f'MediaBrowser Client="hss-popular", Device="hss-popular", DeviceId="hss-popular", Version="1", Token="{jf_key}"'}, method, body)

    cfg = Config(allow_file=env.get("POP_ALLOW", "/data/allow.json"), audit_file=env.get("POP_AUDIT", "/state/audit.jsonl"),
                 request_state=env.get("POP_REQUEST_STATE", "/indexer-bar/request-state.json"), origins=[o for o in env.get("POP_ORIGINS", "").split(",") if o],
                 limit=int(env.get("POP_LIMIT", "10")), days=int(env.get("POP_DAYS", "7")), daily_max=int(env.get("POP_DAILY_MAX", "30")), tz=env.get("TZ") or "Europe/Zurich",
                 hook_secret=secret_file(env.get("POP_HOOK_SECRET_FILE", "/run/secrets/popular/hook.secret")), signal_file=env.get("POP_SIGNAL", "/signal/requested"))
    return Core(cfg, seerr, jellyfin, jellyfin_admin=jellyfin_admin if jf_key else None)


def secret_file(path):
    try:
        return pathlib.Path(path).read_text().strip() or None
    except OSError:
        return None


def body_length(value, cap=MAX_BODY):
    """Content-Length as an int (0 when absent), cap + 1 for anything larger, None when invalid."""
    if value is None or value == "":
        return 0
    if not re.fullmatch(r"[0-9]{1,12}", value.strip()):
        return None
    return min(int(value), cap + 1)


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
                code, data = self.core.handle(method, self.path, self.headers, body)
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


class HookHandler(Handler):
    """POST /hook/<secret> only. The path holds the secret, so it is never logged; 204 answers are not logged at all."""
    server_version = "hss-popular-hook"

    def _go(self, method):
        n = body_length(self.headers.get("Content-Length"), MAX_HOOK_BODY)
        if n is None:
            code, data = 400, {"error": "bad length"}
        elif n > MAX_HOOK_BODY:
            self.close_connection = True
            code, data = 413, {"error": "too large"}
        else:
            try:
                body = self.rfile.read(n) if n else b""
                code, data = self.core.hook(self.path, self.headers, body)
            except TimeoutError:
                self.close_connection = True
                return
            except Exception as e:  # noqa: BLE001
                print(f"hook error {type(e).__name__}", flush=True)
                code, data = 500, {"error": "internal"}
        raw = b"" if code == 204 else json.dumps(data).encode()
        self.send_response(code)
        if raw:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.send_error(405)

    def log_request(self, code="-", size="-"):
        if str(getattr(code, "value", code)) != "204":
            print(f"hook {getattr(code, 'value', code)}", flush=True)

    def log_error(self, fmt, *args):
        print("hook error", flush=True)

    def log_message(self, fmt, *args):
        print("hook", flush=True)


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


def make_hook_server(addr=("0.0.0.0", 8098), workers=4, backlog=8):
    return Server(addr, HookHandler, workers, backlog)


def main():
    Handler.core = HookHandler.core = make_core()
    threading.Thread(target=make_hook_server(("0.0.0.0", int(os.environ.get("POP_HOOK_PORT", "8098")))).serve_forever, daemon=True).start()
    make_server().serve_forever()


if __name__ == "__main__":
    main()
