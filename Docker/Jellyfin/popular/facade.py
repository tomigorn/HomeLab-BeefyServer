"""Seerr-compatible facade for the Moonfin app (Jellyfin README "Moonfin requests (Seerr facade)")."""
import base64
import binascii
import datetime
import hashlib
import hmac
import http.client
import json
import pathlib
import re
import secrets
import urllib.error

FIRST_UID = 1000001
COOKIE = "connect.sid"
MAX_AGE = 30 * 86400
ROTATE_AFTER = 86400
MAX_COOKIE = 1024
B64_RE = re.compile(r"[A-Za-z0-9_-]+")
KEY_RE = re.compile(r"[0-9a-fA-F]{64}")
API = "/api/v1/"
REQUESTER = 32 | 8192
RECHECK = datetime.timedelta(seconds=60)
HANDLE_TTL = datetime.timedelta(minutes=5)
MAX_PENDING = 8
SIGN_INS_PER_HOUR = 5
SIGN_IN_WINDOW = datetime.timedelta(hours=1)
PATH_RE = re.compile(r"[a-z0-9_/-]{1,120}")
QUERY_RE = re.compile(r"[\x21-\x7e]{0,1024}")
WARN_EVERY = datetime.timedelta(minutes=1)
MAX_WARNED = 256
N = r"[0-9]{1,10}"
ROUTES = [
    ("GET", r"status", "public"),
    ("GET", r"settings/public", "public"),
    ("POST", r"auth/jellyfin", "auth"),
    ("POST", r"auth/local", "auth"),
    ("POST", r"auth/jellyfin/quickconnect/initiate", "auth"),
    ("POST", r"auth/jellyfin/quickconnect/authenticate", "auth"),
    ("POST", r"auth/logout", "auth"),
    ("GET", r"auth/me", "auth"),
    ("GET", r"discover/watchlist", "empty_page"),
    ("GET", rf"blocklist|blacklist|issue|issue/{N}", "empty_page"),
    ("GET", r"service/(radarr|sonarr)", "synth"),
    ("GET", rf"service/(radarr|sonarr)/{N}", "synth"),
    ("GET", rf"user/{N}/quota", "synth"),
    ("GET", r"request", "synth"),
    ("GET", r"request/count", "synth"),
    ("GET", rf"request/{N}", "synth"),
    ("POST", r"request", "synth"),
    ("GET", r"settings/discover", "proxy"),
    ("GET", r"discover/(?!watchlist(/|$))[a-z0-9_/-]+", "proxy"),
    ("GET", r"search", "proxy"),
    ("GET", rf"(movie|tv)/{N}(/similar|/recommendations)?", "proxy"),
    ("GET", rf"tv/tvdb/{N}", "proxy"),
    ("GET", rf"person/{N}(/combined_credits)?", "proxy"),
    ("GET", rf"collection/{N}", "proxy"),
    ("GET", r"media", "proxy"),
    ("GET", r"languages|regions|genres/(movie|tv)|backdrops", "proxy"),
    ("GET", r"watchproviders/(regions|movies|tv)", "proxy"),
    ("GET", rf"(studio|network|keyword)/{N}", "proxy"),
    ("GET", r"certifications/(movie|tv)", "proxy"),
    ("GET", r"issue/count", "proxy"),
]
COMPILED = [(m, re.compile(rx), kind) for m, rx, kind in ROUTES]
DROP = {"email", "apiKey", "plexToken", "jellyfinAuthToken", "jellyfinDeviceId", "password", "resetPasswordGuid", "plexId", "jellyfinUserId", "settings", "serviceUrl", "serviceUrl4k"}
USER_KEYS = {"requestedBy", "modifiedBy", "createdBy", "user"}
HOUSEHOLD = {"id": 0, "displayName": "Household"}
METHOD_RE = re.compile(r"[A-Z]{1,10}")


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def mac(key, body):
    return b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def make_cookie(key, sid, uid, iat, exp):
    body = b64(json.dumps({"v": 1, "sid": sid, "uid": uid, "iat": iat, "exp": exp}, separators=(",", ":")).encode())
    return f"s:{body}.{mac(key, body)}"


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def read_cookie(key, cookie_header, now):
    """Payload of the one valid signed connect.sid cookie in a Cookie header, else None."""
    if not key or not cookie_header:
        return None
    values = []
    for part in cookie_header.split(";"):
        name, eq, value = part.strip().partition("=")
        if eq and name == COOKIE:
            values.append(value.strip())
    if len(values) != 1 or len(values[0]) > MAX_COOKIE:
        return None
    value = values[0]
    if value.startswith("s:"):
        value = value[2:]
    elif value[:4].lower() == "s%3a":
        value = value[4:]
    else:
        return None
    body, dot, sig = value.partition(".")
    if not dot or not B64_RE.fullmatch(body) or not B64_RE.fullmatch(sig) or not hmac.compare_digest(sig, mac(key, body)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or not is_int(data.get("v")):
        return None
    if not isinstance(data.get("sid"), str) or not data["sid"] or not all(is_int(data.get(k)) for k in ("uid", "iat", "exp")):
        return None
    if not data["iat"] <= now < data["exp"]:
        return None
    return data


def set_cookie_header(value):
    return f"{COOKIE}={value}; HttpOnly; Path=/; SameSite=Strict; Max-Age={MAX_AGE}"


def load_key(path):
    try:
        text = pathlib.Path(path).read_text().strip()
    except (OSError, TypeError):
        return None
    return bytes.fromhex(text) if KEY_RE.fullmatch(text) else None


class Sessions:
    """Facade users (stable synthetic Seerr ids) and sign-in sessions in the shared state db."""

    def __init__(self, core):
        self.core = core

    def init(self):
        with self.core.lock, self.core.db() as con:
            con.execute("CREATE TABLE IF NOT EXISTS facade_users (jf_id TEXT PRIMARY KEY, uid INTEGER UNIQUE NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, jf_id TEXT NOT NULL, created TEXT, last_seen TEXT, revoked INTEGER NOT NULL DEFAULT 0)")

    def uid_for(self, jf_id):
        with self.core.lock, self.core.db() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT uid FROM facade_users WHERE jf_id = ?", (jf_id,)).fetchone()
                if row:
                    return row[0]
                top = con.execute("SELECT MAX(uid) FROM facade_users").fetchone()[0]
                uid = max(FIRST_UID, (top or 0) + 1)
                con.execute("INSERT INTO facade_users (jf_id, uid) VALUES (?, ?)", (jf_id, uid))
                return uid
            finally:
                con.execute("COMMIT")

    def open(self, jf_id):
        sid = secrets.token_urlsafe(24)
        now = self.core.iso(self.core.clock())
        with self.core.lock, self.core.db() as con:
            con.execute("INSERT INTO sessions (sid, jf_id, created, last_seen) VALUES (?, ?, ?, ?)", (sid, jf_id, now, now))
        return sid

    def owner(self, sid, uid):
        """Jellyfin user id of an open session whose facade user id matches the cookie, else None."""
        with self.core.db() as con:
            row = con.execute("SELECT s.jf_id FROM sessions s JOIN facade_users u ON u.jf_id = s.jf_id WHERE s.sid = ? AND s.revoked = 0 AND u.uid = ?", (sid, uid)).fetchone()
        return row[0] if row else None

    def active(self, sid, jf_id):
        with self.core.db() as con:
            row = con.execute("SELECT 1 FROM sessions WHERE sid = ? AND jf_id = ? AND revoked = 0", (sid, jf_id)).fetchone()
        return row is not None

    def touch(self, sid):
        with self.core.lock, self.core.db() as con:
            con.execute("UPDATE sessions SET last_seen = ? WHERE sid = ?", (self.core.iso(self.core.clock()), sid))

    def revoke(self, sid):
        with self.core.lock, self.core.db() as con:
            con.execute("UPDATE sessions SET revoked = 1 WHERE sid = ?", (sid,))

    def prune(self):
        """Revoked sessions, and sessions unused for longer than a cookie lives (their cookies have expired)."""
        cutoff = self.core.iso(self.core.clock() - datetime.timedelta(seconds=MAX_AGE))
        with self.core.lock, self.core.db() as con:
            con.execute("DELETE FROM sessions WHERE revoked = 1 OR COALESCE(last_seen, created) < ?", (cutoff,))


def shown(method):
    return method if isinstance(method, str) and METHOD_RE.fullmatch(method) else "-"


def scrub(obj, me_uid, mine, me_name="You"):
    """Copy of a Seerr body without secrets or user details: embedded users become {id, displayName}, the caller's own requests (id in mine) name the caller, everyone else is Household."""
    me = {"id": me_uid, "displayName": me_name}
    if isinstance(obj, list):
        return [scrub(v, me_uid, mine, me_name) for v in obj]
    if not isinstance(obj, dict):
        return obj
    own = is_int(obj.get("id")) and obj["id"] in mine
    requester = obj.get("requestedBy")
    out = {}
    for k, v in obj.items():
        if k in DROP or k.startswith("externalService"):
            continue
        if k in USER_KEYS:
            if v is None:
                out[k] = None
            elif own and (k == "requestedBy" or (k == "modifiedBy" and isinstance(v, dict) and isinstance(requester, dict) and is_int(v.get("id")) and v.get("id") == requester.get("id"))):
                out[k] = dict(me)
            else:
                out[k] = dict(HOUSEHOLD)
            continue
        out[k] = scrub(v, me_uid, mine, me_name)
    return out


class Unavailable(Exception):
    """Jellyfin cannot be asked whether the caller still exists (503, session kept)."""


class Facade:
    """Seerr API subset for Moonbase: Quick Connect sign-in, the signed session and a deny-by-default read proxy (ROUTES)."""

    def __init__(self, core, key, jf, clock=None):
        self.core, self.key, self.jf = core, key, jf
        self.clock = clock or core.clock
        self.sessions = Sessions(core)
        self.sessions.init()
        self.pending, self.signins, self.checked, self.warned = {}, {}, {}, {}
        self.log = lambda line: print(line, flush=True)

    def now(self):
        return int(self.clock().timestamp())

    def handle(self, method, path, headers, body):
        headers = {str(k).lower(): v for k, v in headers.items()}
        status, out, extra, rel, jf_id = self.dispatch(method, path, headers, body)
        self.log(f"facade {shown(method)} {rel or '-'} {status} {jf_id or '-'}")
        return status, out, extra

    def dispatch(self, method, path, headers, body):
        """(status, body, extra headers, logged path or None, Jellyfin user id or None)."""
        path, _, query = path.partition("?")
        if not path.startswith(API) or not PATH_RE.fullmatch(path[len(API):]) or "//" in path or ".." in path or path.endswith("/"):
            return 404, {"message": "Not found"}, [], None, None
        rel = path[len(API):]
        if not QUERY_RE.fullmatch(query):
            return 400, {"message": "Bad request"}, [], rel, None
        kind = next((k for m, rx, k in COMPILED if m == method and rx.fullmatch(rel)), None)
        if kind is None:
            self.warn(method, rel)
            if method == "GET":
                return 404, {"message": "Not found"}, [], rel, None
            return 403, {"message": "Not available in this app"}, [], rel, None
        path_q = rel + ("?" + query if query else "")
        try:
            if kind == "auth":
                status, out, extra, jf_id = self.auth(method, rel, headers, body)
                return status, out, extra, rel, jf_id
            if kind == "public":
                return self.proxy(path_q, None) + ([], rel, None)
            who = self.caller(headers)
            if who is None:
                return 401, {"message": "Unauthorized"}, [], rel, None
            if kind == "empty_page":
                status, out = 200, self.empty(rel)
            elif kind == "proxy":
                status, out = self.proxy(path_q, who)
            else:
                status, out = 404, {"message": "Not found"}
            return self.rotated(who, status, out, []) + (rel, who["jf_id"])
        except Unavailable:
            return 503, {"message": "Jellyfin is not reachable"}, [], rel, None

    def auth(self, method, rel, headers, body):
        """(status, body, extra headers, Jellyfin user id or None) of the sign-in routes."""
        route = (method, rel)
        if route in (("POST", "auth/jellyfin"), ("POST", "auth/local")):
            self.core.reject("facade password")
            return 401, {"message": "Use Quick Connect to sign in"}, [], None
        if route == ("POST", "auth/jellyfin/quickconnect/initiate"):
            return self.initiate() + (None,)
        if route == ("POST", "auth/jellyfin/quickconnect/authenticate"):
            return self.authenticate(body) + (None,)
        if route == ("POST", "auth/logout"):
            return self.logout(headers) + (None,)
        who = self.caller(headers)
        if who is None:
            return 403, {"status": 403, "error": "Unauthorized"}, [], None
        return self.rotated(who, 200, self.me(who), []) + (who["jf_id"],)

    def warn(self, method, rel):
        now = self.clock()
        with self.core.lock:
            for k in [k for k, at in self.warned.items() if now - at >= WARN_EVERY]:
                del self.warned[k]
            if rel in self.warned or len(self.warned) >= MAX_WARNED:
                return
            self.warned[rel] = now
        self.log(f"facade refused {shown(method)} {rel}")

    # --- reads --------------------------------------------------------------
    def proxy(self, path_q, me):
        """(status, body) of a GET to Seerr as the service user, scrubbed; me is the caller or None for the public routes."""
        try:
            svc = self.core.service_user(create=False)
            uid = svc.get("id") if isinstance(svc, dict) else None
            if not is_int(uid) or uid <= 1:
                return 503, {"message": "Requests are not set up"}
            out = self.core.seerr("GET", API + path_q, user=uid)
            if not isinstance(out, (dict, list)):
                return 502, {"message": "Seerr is not reachable"}
            if path_q.partition("?")[0] == "settings/public" and isinstance(out, dict):
                out = {**out, "movie4kEnabled": False, "series4kEnabled": False}
            if me is None:
                return 200, scrub(out, 0, set())
            return 200, scrub(out, me["uid"], self.mine(me), me["name"])
        except self.core.NoServiceUser:
            return 503, {"message": "Requests are not set up"}
        except urllib.error.HTTPError as e:
            e.close()
            if 400 <= e.code < 500:
                return 404, {"message": "Not found"}
            return 502, {"message": "Seerr is not reachable"}
        except (OSError, ValueError, RecursionError, http.client.HTTPException):
            return 502, {"message": "Seerr is not reachable"}

    @staticmethod
    def empty(rel):
        """No Plex watchlist, blocklist or issues for app users (the service user may not read issues): empty pages in Seerr's shapes."""
        if rel == "discover/watchlist":
            return {"page": 1, "totalPages": 1, "totalResults": 0, "results": []}
        return {"pageInfo": {"pages": 0, "pageSize": 25, "results": 0, "page": 1}, "results": []}

    def mine(self, me):
        """Seerr request ids the caller made (filled in with the request routes)."""
        return set()

    def rotated(self, who, status, out, extra):
        now = self.now()
        if 200 <= status < 300 and now - who["iat"] > ROTATE_AFTER:
            self.sessions.touch(who["sid"])
            extra = extra + [("Set-Cookie", set_cookie_header(make_cookie(self.key, who["sid"], who["uid"], now, now + MAX_AGE)))]
        return status, out, extra

    def me(self, caller):
        name = caller["name"]
        return {"id": caller["uid"], "displayName": name, "username": name, "email": None, "avatar": None, "permissions": REQUESTER,
                "userType": 2, "requestCount": 0, "warnings": [], "settings": {"streamingRegion": "CH", "locale": None}}

    # --- session ----------------------------------------------------------
    def caller(self, headers):
        """The signed-in caller, or None; Jellyfin is asked again at most once a minute whether the user still exists and is enabled."""
        data = read_cookie(self.key, headers.get("cookie"), self.now())
        if not data:
            return None
        jf_id = self.sessions.owner(data["sid"], data["uid"])
        if jf_id is None:
            return None
        user = self.jellyfin_user(jf_id)
        if user is None:
            self.sessions.revoke(data["sid"])
            return None
        return {"jf_id": jf_id, "name": user["name"], "uid": data["uid"], "sid": data["sid"], "iat": data["iat"]}

    def jellyfin_user(self, jf_id):
        now = self.clock()
        with self.core.lock:
            hit = self.checked.get(jf_id)
        if hit and now - hit[0] < RECHECK:
            return hit[1]
        if self.core.jellyfin_admin is None:
            raise Unavailable()
        try:
            u = self.core.jellyfin_admin("GET", f"/Users/{jf_id}")
        except urllib.error.HTTPError as e:
            e.close()
            if e.code in (400, 404):
                u = None
            else:
                raise Unavailable() from None
        except (OSError, ValueError):
            raise Unavailable() from None
        user = self.user_of(u)
        with self.core.lock:
            for k in [k for k, (at, _) in self.checked.items() if now - at >= RECHECK]:
                del self.checked[k]
            if user is None or user["id"] != jf_id:
                self.checked.pop(jf_id, None)
            else:
                self.checked[jf_id] = (now, user)
        return user if user and user["id"] == jf_id else None

    @staticmethod
    def user_of(u):
        """{id, name} of an enabled Jellyfin user object, else None."""
        if not isinstance(u, dict) or not isinstance(u.get("Id"), str) or not u["Id"]:
            return None
        policy = u.get("Policy")
        if not isinstance(policy, dict) or policy.get("IsDisabled") is not False:
            return None
        return {"id": u["Id"].replace("-", "").lower(), "name": u.get("Name") if isinstance(u.get("Name"), str) else ""}

    def logout(self, headers):
        data = read_cookie(self.key, headers.get("cookie"), self.now())
        if data:
            self.sessions.revoke(data["sid"])
        return 200, {"status": "ok"}, []

    # --- Quick Connect ----------------------------------------------------
    def initiate(self):
        if not self.core.rate_ok("global", "qc-initiate", 10):
            return 429, {"message": "Too many sign-in attempts, try again in a minute"}, []
        now = self.clock()
        with self.core.lock:
            self.expire(now)
            full = len(self.pending) >= MAX_PENDING
        if full:
            return 429, {"message": "Too many sign-in attempts, try again in a minute"}, []
        device_id = "hss-seerr-" + secrets.token_hex(8)
        try:
            r = self.jf("POST", "/QuickConnect/Initiate", device_id=device_id)
        except (OSError, ValueError):
            return 503, {"message": "Quick Connect is not available"}, []
        if not isinstance(r, dict) or not isinstance(r.get("Code"), str) or not isinstance(r.get("Secret"), str) or not r["Code"] or not r["Secret"]:
            return 503, {"message": "Quick Connect is not available"}, []
        handle = secrets.token_hex(16)
        with self.core.lock:
            self.expire(now)
            if len(self.pending) >= MAX_PENDING:
                return 429, {"message": "Too many sign-in attempts, try again in a minute"}, []
            self.pending[handle] = (r["Secret"], device_id, now)
        return 200, {"code": r["Code"], "secret": handle}, []

    def expire(self, now):
        for h in [h for h, (_, _, at) in self.pending.items() if now - at >= HANDLE_TTL]:
            del self.pending[h]

    def authenticate(self, body):
        try:
            data = json.loads(body or b"")
        except ValueError:
            return 400, {"message": "Bad request"}, []
        if not isinstance(data, dict):
            return 400, {"message": "Bad request"}, []
        handle = data.get("secret")
        now = self.clock()
        with self.core.lock:
            entry = self.pending.pop(handle, None) if isinstance(handle, str) else None
        if entry is None or now - entry[2] >= HANDLE_TTL:
            return 401, {"message": "Quick Connect expired"}, []
        secret, device_id, _ = entry
        token, u = None, None
        try:
            r = self.jf("POST", "/Users/AuthenticateWithQuickConnect", {"Secret": secret}, device_id=device_id)
            token = r.get("AccessToken") if isinstance(r, dict) else None
            if not isinstance(token, str) or not token:
                return 503, {"message": "Jellyfin is not reachable"}, []
            u = self.jf("GET", "/Users/Me", device_id=device_id, token=token)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code < 500:
                return 401, {"message": "Quick Connect expired"}, []
            return 503, {"message": "Jellyfin is not reachable"}, []
        except (OSError, ValueError):
            return 503, {"message": "Jellyfin is not reachable"}, []
        finally:
            if isinstance(token, str) and token:
                try:
                    self.jf("POST", "/Sessions/Logout", device_id=device_id, token=token)
                except (OSError, ValueError):
                    pass
        user = self.user_of(u)
        if user is None:
            return 403, {"message": "This Jellyfin account may not sign in"}, []
        if not self.sign_in_ok(user["id"], now):
            return 429, {"message": "Too many sign-ins, try again later"}, []
        uid = self.sessions.uid_for(user["id"])
        sid = self.sessions.open(user["id"])
        with self.core.lock:
            self.checked[user["id"]] = (now, user)
        iat = int(now.timestamp())
        cookie = make_cookie(self.key, sid, uid, iat, iat + MAX_AGE)
        return 200, self.me({"uid": uid, "name": user["name"]}), [("Set-Cookie", set_cookie_header(cookie))]

    def sign_in_ok(self, jf_id, now):
        with self.core.lock:
            for k in [k for k, v in self.signins.items() if not v or now - v[-1] >= SIGN_IN_WINDOW]:
                del self.signins[k]
            recent = [t for t in self.signins.get(jf_id, []) if now - t < SIGN_IN_WINDOW]
            if len(recent) >= SIGN_INS_PER_HOUR:
                return False
            self.signins[jf_id] = recent + [now]
            return True
