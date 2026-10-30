import base64
import contextlib
import datetime
import hashlib
import hmac
import http.client
import io
import json
import pathlib
import re
import socket
import sys
import threading
import time
import unittest
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import facade  # noqa: E402
import test_app  # noqa: E402

KEY = bytes(range(32))
T0 = int(test_app.NOW.timestamp())


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(key, payload):
    body = b64(json.dumps(payload).encode())
    return f"s:{body}.{b64(hmac.new(key, body.encode(), hashlib.sha256).digest())}"


class Cookie(unittest.TestCase):
    def cookie(self, **kw):
        args = {"sid": "sid-1", "uid": 1000001, "iat": T0, "exp": T0 + facade.MAX_AGE, **kw}
        return facade.make_cookie(KEY, args["sid"], args["uid"], args["iat"], args["exp"])

    def test_cookie_round_trip(self):
        value = self.cookie()
        self.assertTrue(value.startswith("s:"))
        self.assertNotIn("=", value)
        got = facade.read_cookie(KEY, f"connect.sid={value}", T0 + 10)
        self.assertEqual(got, {"v": 1, "sid": "sid-1", "uid": 1000001, "iat": T0, "exp": T0 + facade.MAX_AGE})
        self.assertEqual(facade.set_cookie_header(value), f"connect.sid={value}; HttpOnly; Path=/; SameSite=Strict; Max-Age=2592000")
        self.assertEqual((facade.FIRST_UID, facade.COOKIE, facade.MAX_AGE, facade.ROTATE_AFTER), (1000001, "connect.sid", 2592000, 86400))

    def test_tampered_payload_or_mac_refused(self):
        value = self.cookie()
        body, mac = value[2:].split(".")
        forged = b64(json.dumps({"v": 1, "sid": "sid-1", "uid": 1000002, "iat": T0, "exp": T0 + facade.MAX_AGE}).encode())
        flipped = mac[:-1] + ("A" if mac[-1] != "A" else "B")
        for bad in [f"s:{forged}.{mac}", f"s:{body}.{flipped}", f"s:{body}.", f"s:{body}", f"s:.{mac}", f"s:{body}.{mac}.x", f"{body}.{mac}", "s:", ""]:
            self.assertIsNone(facade.read_cookie(KEY, f"connect.sid={bad}", T0 + 10), bad)

    def test_wrong_key_refused(self):
        self.assertIsNone(facade.read_cookie(b"\x01" * 32, f"connect.sid={self.cookie()}", T0 + 10))

    def test_expired_refused(self):
        value = self.cookie(exp=T0 + 100)
        self.assertIsNotNone(facade.read_cookie(KEY, f"connect.sid={value}", T0 + 99))
        self.assertIsNone(facade.read_cookie(KEY, f"connect.sid={value}", T0 + 100))
        self.assertIsNone(facade.read_cookie(KEY, f"connect.sid={value}", T0 + 10**6))

    def test_payload_shape_checked_even_when_signed(self):
        good = {"v": 1, "sid": "sid-1", "uid": 1000001, "iat": T0, "exp": T0 + 100}
        for bad in [{**good, "v": 2}, {**good, "v": True}, {**good, "v": "1"}, {**good, "uid": "1000001"}, {**good, "uid": True}, {**good, "exp": "x"},
                    {**good, "sid": 5}, {**good, "sid": ""}, {k: v for k, v in good.items() if k != "sid"}, [1, 2], "x", None]:
            self.assertIsNone(facade.read_cookie(KEY, f"connect.sid={sign(KEY, bad)}", T0 + 10), bad)
        self.assertIsNotNone(facade.read_cookie(KEY, f"connect.sid={sign(KEY, good)}", T0 + 10))
        raw = "s:" + b64(b"\xff\xfe not json") + "."
        body = raw[2:-1]
        self.assertIsNone(facade.read_cookie(KEY, f"connect.sid={raw}{b64(hmac.new(KEY, body.encode(), hashlib.sha256).digest())}", T0 + 10))

    def test_cookie_header_variants(self):
        value = self.cookie()
        enc = "s%3A" + value[2:]
        for header in [f"connect.sid={value}", f"connect.sid={enc}", "connect.sid=s%3a" + value[2:], f"theme=dark; connect.sid={enc}; lang=en",
                       f"  connect.sid={value}  ;other=1", f"a=1;connect.sid={value}", f"xconnect.sid=junk; connect.sid={value}"]:
            self.assertIsNotNone(facade.read_cookie(KEY, header, T0 + 10), header)
        long = "s:" + "A" * 1100 + "." + value.split(".")[1]
        for header in [None, "", "connect.sid", "connect.sid=", f"connect.sid={value}; connect.sid={value}", f"connect.sid={value}; connect.sid=garbage",
                       "connect.sid=garbage", "connect.sid=s%3A%%%.%%", "\x00\xff;;;==", f"connect.sid={long}", f"other={value}",
                       f"Connect.Sid={value}", f"connect.sid={value}x", "connect.sid=s:é.é"]:
            self.assertIsNone(facade.read_cookie(KEY, header, T0 + 10), header)


class Sessions(test_app.Base):
    def setUp(self):
        super().setUp()
        self.sessions = facade.Sessions(self.core)
        self.sessions.init()

    def test_tables_created_idempotently(self):
        self.sessions.init()
        with self.core.db() as con:
            names = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual({"facade_users", "sessions", "requests", "rejects"}, names)

    def test_uid_stable_and_never_1(self):
        self.assertEqual(self.sessions.uid_for("a"), 1000001)
        self.assertEqual(self.sessions.uid_for("a"), 1000001)
        self.assertEqual(self.sessions.uid_for("b"), 1000002)
        self.assertEqual(facade.Sessions(self.core).uid_for("a"), 1000001)
        self.assertEqual(self.sessions.uid_for("c"), 1000003)

    def test_open_and_active(self):
        sid = self.sessions.open("a")
        self.assertGreaterEqual(len(sid), 32)
        self.assertNotEqual(sid, self.sessions.open("a"))
        self.assertTrue(self.sessions.active(sid, "a"))
        self.assertFalse(self.sessions.active("unknown", "a"))

    def test_revoked_session_inactive(self):
        sid = self.sessions.open("a")
        other = self.sessions.open("a")
        self.sessions.revoke(sid)
        self.assertFalse(self.sessions.active(sid, "a"))
        self.assertTrue(self.sessions.active(other, "a"))

    def test_session_bound_to_jf_id(self):
        sid = self.sessions.open("a")
        self.assertFalse(self.sessions.active(sid, "b"))

    def test_touch_updates_last_seen(self):
        sid = self.sessions.open("a")
        self.now = test_app.NOW + datetime.timedelta(days=2)
        self.sessions.touch(sid)
        with self.core.db() as con:
            created, last_seen = con.execute("SELECT created, last_seen FROM sessions WHERE sid = ?", (sid,)).fetchone()
        self.assertEqual(created, self.core.iso(test_app.NOW))
        self.assertEqual(last_seen, self.core.iso(self.now))

    def test_prune_drops_old_and_revoked(self):
        old = self.sessions.open("a")
        old_used = self.sessions.open("a")
        self.now = test_app.NOW + datetime.timedelta(days=20)
        revoked = self.sessions.open("b")
        self.sessions.revoke(revoked)
        fresh = self.sessions.open("b")
        self.sessions.touch(old_used)
        self.now = test_app.NOW + datetime.timedelta(days=31)
        self.sessions.prune()
        with self.core.db() as con:
            left = {s for (s,) in con.execute("SELECT sid FROM sessions")}
        self.assertEqual(left, {old_used, fresh})
        self.assertEqual(self.sessions.uid_for("a"), 1000001)


JANE = {"Id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "Name": "Jane Doe", "Policy": {"IsDisabled": False}}
JF_ID = "aaaaaaaabbbbccccddddeeeeeeeeeeee"
TOKEN = "t" * 32
DEVICE_RE = re.compile(r"hss-seerr-[0-9a-f]{16}")


def http_error(code):
    return urllib.error.HTTPError("http://jellyfin:8096/x", code, "error", {}, io.BytesIO(b""))


class FakeJellyfin:
    def __init__(self):
        self.calls, self.admin_calls = [], []
        self.me = dict(JANE)
        self.users = {JF_ID: dict(JANE)}
        self.me_error, self.admin_down, self.delay = None, False, 0

    def __call__(self, method, path, body=None, device_id=None, token=None):
        self.calls.append((method, path, device_id, token))
        if (method, path) == ("POST", "/QuickConnect/Initiate"):
            return {"Code": "123456", "Secret": "jf-secret", "DeviceId": device_id, "Authenticated": False}
        if (method, path) == ("POST", "/Users/AuthenticateWithQuickConnect"):
            assert body == {"Secret": "jf-secret"}, body
            time.sleep(self.delay)
            return {"AccessToken": TOKEN, "User": dict(JANE)}
        if (method, path) == ("GET", "/Users/Me"):
            if self.me_error:
                raise self.me_error
            return self.me
        if (method, path) == ("POST", "/Sessions/Logout"):
            return None
        raise AssertionError((method, path))

    def admin(self, method, path, body=None):
        self.admin_calls.append((method, path))
        if self.admin_down:
            raise OSError("connection refused")
        jf_id = path.rsplit("/", 1)[1]
        if method != "GET" or jf_id not in self.users:
            raise http_error(404)
        return self.users[jf_id]


class FacadeBase(test_app.Base):
    def setUp(self):
        super().setUp()
        self.jf = FakeJellyfin()
        self.core.jellyfin_admin = self.jf.admin
        self.facade = facade.Facade(self.core, KEY, self.jf)
        self.lines = []
        self.facade.log = self.lines.append

    def call(self, method, path, cookie=None, body=b""):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = f"connect.sid={cookie}"
        return self.facade.handle(method, "/api/v1/" + path, headers, body)

    def initiate(self):
        status, out, _ = self.call("POST", "auth/jellyfin/quickconnect/initiate", body=b"{}")
        self.assertEqual(status, 200, out)
        return out["secret"]

    def authenticate(self, handle):
        return self.call("POST", "auth/jellyfin/quickconnect/authenticate", body=json.dumps({"secret": handle}).encode())

    def sign_in(self):
        status, out, extra = self.authenticate(self.initiate())
        self.assertEqual(status, 200, out)
        return out, cookie_of(extra)

    def later(self, seconds):
        self.now = self.now + datetime.timedelta(seconds=seconds)

    def session_row(self):
        with self.core.db() as con:
            return con.execute("SELECT sid, revoked, last_seen FROM sessions").fetchall()


def cookie_of(extra):
    values = [v for k, v in extra if k == "Set-Cookie"]
    if not values:
        return None
    assert len(values) == 1, values
    return values[0].split(";")[0].partition("=")[2]


class SignIn(FacadeBase):
    def test_password_login_refused_without_jellyfin_call(self):
        for path in ["auth/jellyfin", "auth/local"]:
            with contextlib.redirect_stdout(io.StringIO()):
                status, out, extra = self.call("POST", path, body=json.dumps({"username": "jane", "password": "pw"}).encode())
            self.assertEqual((status, out, extra), (401, {"message": "Use Quick Connect to sign in"}, []))
        self.assertEqual(self.jf.calls, [])
        self.assertEqual(self.jf.admin_calls, [])
        self.assertEqual(self.core.rejected().get("facade password"), 2)

    def test_quick_connect_happy_path_sets_cookie_and_logs_out(self):
        status, out, extra = self.call("POST", "auth/jellyfin/quickconnect/initiate", body=b"{}")
        self.assertEqual(status, 200)
        self.assertEqual(set(out), {"code", "secret"})
        self.assertEqual(out["code"], "123456")
        self.assertEqual(len(out["secret"]), 32)
        self.assertNotIn("jf-secret", json.dumps(out))
        self.assertEqual(extra, [])
        status, me, extra = self.authenticate(out["secret"])
        self.assertEqual(status, 200, me)
        self.assertEqual(me, self.facade.me({"jf_id": JF_ID, "name": "Jane Doe", "uid": 1000001}))
        self.assertEqual([(m, p) for m, p, _, _ in self.jf.calls],
                         [("POST", "/QuickConnect/Initiate"), ("POST", "/Users/AuthenticateWithQuickConnect"), ("GET", "/Users/Me"), ("POST", "/Sessions/Logout")])
        devices = {d for _, _, d, _ in self.jf.calls}
        self.assertEqual(len(devices), 1)
        self.assertRegex(devices.pop(), DEVICE_RE)
        self.assertEqual([t for _, _, _, t in self.jf.calls], [None, None, TOKEN, TOKEN])
        self.assertFalse(any("/Devices" in p for _, p, _, _ in self.jf.calls))
        self.assertFalse(any("/Devices" in p for _, p in self.jf.admin_calls))
        cookie = cookie_of(extra)
        payload = facade.read_cookie(KEY, f"connect.sid={cookie}", T0)
        self.assertEqual(payload["uid"], 1000001)
        self.assertTrue(self.facade.sessions.active(payload["sid"], JF_ID))
        self.assertIn("HttpOnly", extra[0][1])
        status, again, _ = self.call("GET", "auth/me", cookie=cookie)
        self.assertEqual((status, again), (200, me))

    def test_logout_called_when_users_me_fails(self):
        for err, code in [(http_error(500), 503), (OSError("reset"), 503), (http_error(401), 401)]:
            self.jf.calls.clear()
            self.jf.me_error = err
            status, out, extra = self.authenticate(self.initiate())
            self.assertEqual(status, code, out)
            self.assertIn("message", out)
            self.assertEqual(extra, [])
            self.assertEqual(self.jf.calls[-1][:2], ("POST", "/Sessions/Logout"))
            self.assertEqual(self.jf.calls[-1][3], TOKEN)
        self.assertEqual(self.session_row(), [])

    def test_handle_is_single_use_under_concurrency(self):
        handle = self.initiate()
        self.jf.delay = 0.05
        barrier = threading.Barrier(2)
        results = []

        def go():
            barrier.wait()
            results.append(self.authenticate(handle)[:2])

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(s for s, _ in results), [200, 401])
        self.assertIn((401, {"message": "Quick Connect expired"}), results)
        self.assertEqual(len(self.session_row()), 1)
        self.assertEqual(sum(p == "/Users/AuthenticateWithQuickConnect" for _, p, _, _ in self.jf.calls), 1)
        self.assertEqual(self.authenticate(handle)[:2], (401, {"message": "Quick Connect expired"}))

    def test_handle_expires_after_5_min(self):
        handle = self.initiate()
        self.later(300)
        self.assertEqual(self.authenticate(handle)[:2], (401, {"message": "Quick Connect expired"}))
        self.assertEqual(self.authenticate("x" * 32)[:2], (401, {"message": "Quick Connect expired"}))
        for bad in [b"", b"[]", b"null", b"{", json.dumps({"secret": 5}).encode(), b"{}"]:
            status, out, _ = self.call("POST", "auth/jellyfin/quickconnect/authenticate", body=bad)
            self.assertIn(status, (400, 401), bad)
            self.assertIn("message", out)
        self.assertFalse(any(p == "/Users/AuthenticateWithQuickConnect" for _, p, _, _ in self.jf.calls))
        fresh = self.initiate()
        self.later(299)
        self.assertEqual(self.authenticate(fresh)[0], 200)

    def test_initiate_rate_and_pending_limits(self):
        handles = [self.initiate() for _ in range(8)]
        status, out, _ = self.call("POST", "auth/jellyfin/quickconnect/initiate", body=b"{}")
        self.assertEqual(status, 429)
        self.assertIn("message", out)
        self.assertEqual(self.authenticate(handles.pop())[0], 200)
        self.assertEqual(self.authenticate(handles.pop())[0], 200)
        handles += [self.initiate()]
        self.assertEqual(self.authenticate(handles.pop())[0], 200)
        initiates = sum(p == "/QuickConnect/Initiate" for _, p, _, _ in self.jf.calls)
        self.assertEqual(initiates, 9)
        status, out, _ = self.call("POST", "auth/jellyfin/quickconnect/initiate", body=b"{}")
        self.assertEqual((status, set(out)), (429, {"message"}))
        self.assertEqual(sum(p == "/QuickConnect/Initiate" for _, p, _, _ in self.jf.calls), initiates)
        self.later(301)
        self.assertEqual(len(self.facade.pending), 6)
        self.initiate()
        self.assertEqual(len(self.facade.pending), 1)

    def test_sign_in_limit_5_per_hour(self):
        for _ in range(5):
            self.sign_in()
            self.later(60)
        status, out, extra = self.authenticate(self.initiate())
        self.assertEqual((status, set(out), extra), (429, {"message"}, []))
        self.assertEqual(len(self.session_row()), 5)
        self.later(3600 - 300)
        self.sign_in()

    def test_disabled_user_gets_no_session(self):
        self.jf.me = {**JANE, "Policy": {"IsDisabled": True}}
        status, out, extra = self.authenticate(self.initiate())
        self.assertEqual((status, set(out), extra), (403, {"message"}, []))
        self.assertEqual(self.session_row(), [])
        self.assertEqual(self.jf.calls[-1][:2], ("POST", "/Sessions/Logout"))

    def test_me_id_never_1_no_admin_bit(self):
        me, _ = self.sign_in()
        self.assertEqual(me, {"id": 1000001, "displayName": "Jane Doe", "username": "Jane Doe", "email": None, "avatar": None, "permissions": 8224,
                              "userType": 2, "requestCount": 0, "warnings": [], "settings": {"streamingRegion": "CH", "locale": None}})
        self.assertEqual(facade.REQUESTER, 8224)
        self.assertFalse(me["permissions"] & 2)
        self.assertGreaterEqual(me["id"], facade.FIRST_UID)

    def test_auth_paths_with_the_wrong_method_refused(self):
        for method, path in [("GET", "auth/jellyfin"), ("GET", "auth/logout"), ("GET", "auth/jellyfin/quickconnect/initiate")]:
            self.assertEqual(self.call(method, path)[:2], (404, {"message": "Not found"}), path)
        self.assertEqual(self.call("POST", "auth/me")[:2], (403, {"message": "Not available in this app"}))
        self.assertEqual(self.facade.handle("GET", "/auth/me", {}, b"")[:2], (404, {"message": "Not found"}))
        self.assertEqual(self.jf.calls, [])


class Session(FacadeBase):
    def test_auth_me_without_cookie_403_shape(self):
        forged = facade.make_cookie(b"\x02" * 32, "sid", 1000001, T0, T0 + 100)
        for cookie in [None, "garbage", forged]:
            self.assertEqual(self.call("GET", "auth/me", cookie=cookie), (403, {"status": 403, "error": "Unauthorized"}, []))
        self.assertEqual(self.jf.admin_calls, [])

    def test_deleted_or_disabled_after_sign_in_loses_session_after_60s(self):
        for change in ["delete", "disable"]:
            self.jf.users[JF_ID] = dict(JANE)
            _, cookie = self.sign_in()
            self.later(30)
            if change == "delete":
                del self.jf.users[JF_ID]
            else:
                self.jf.users[JF_ID] = {**JANE, "Policy": {"IsDisabled": True}}
            self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[0], 200, change)
            self.later(31)
            self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[:2], (403, {"status": 403, "error": "Unauthorized"}), change)
            self.jf.users[JF_ID] = dict(JANE)
            self.later(61)
            self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[0], 403, change)
            self.later(3600)
        self.assertEqual([r for _, r, _ in self.session_row()], [1, 1])

    def test_recheck_jellyfin_down_is_503(self):
        _, cookie = self.sign_in()
        self.later(61)
        self.jf.admin_down = True
        status, out, extra = self.call("GET", "auth/me", cookie=cookie)
        self.assertEqual((status, set(out), extra), (503, {"message"}, []))
        self.assertEqual([r for _, r, _ in self.session_row()], [0])
        self.jf.admin_down = False
        self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[0], 200)
        self.core.jellyfin_admin = None
        self.later(61)
        self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[0], 503)

    def test_name_follows_jellyfin_rename(self):
        _, cookie = self.sign_in()
        self.jf.users[JF_ID] = {**JANE, "Name": "Jane Roe"}
        self.later(61)
        status, me, _ = self.call("GET", "auth/me", cookie=cookie)
        self.assertEqual((status, me["displayName"], me["id"]), (200, "Jane Roe", 1000001))

    def test_cookie_rotated_after_one_day(self):
        _, cookie = self.sign_in()
        sid = facade.read_cookie(KEY, f"connect.sid={cookie}", T0)["sid"]
        self.later(86400)
        self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[2], [])
        self.later(1)
        status, _, extra = self.call("GET", "auth/me", cookie=cookie)
        self.assertEqual(status, 200)
        fresh = cookie_of(extra)
        now = T0 + 86401
        payload = facade.read_cookie(KEY, f"connect.sid={fresh}", now)
        self.assertEqual((payload["sid"], payload["iat"], payload["exp"]), (sid, now, now + facade.MAX_AGE))
        self.assertEqual(self.session_row(), [(sid, 0, self.core.iso(self.now))])
        self.assertEqual(self.call("GET", "auth/me", cookie=fresh)[2], [])
        self.later(1)
        self.assertEqual(self.call("GET", "auth/me", cookie="s%3A" + cookie[2:])[0], 200)

    def test_logout_revokes(self):
        _, cookie = self.sign_in()
        self.assertEqual(self.call("POST", "auth/logout", cookie=cookie), (200, {"status": "ok"}, []))
        self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[0], 403)
        self.assertEqual([r for _, r, _ in self.session_row()], [1])
        self.assertEqual(self.call("POST", "auth/logout"), (200, {"status": "ok"}, []))

    def test_no_token_or_cookie_in_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            handle = self.initiate()
            _, _, extra = self.authenticate(handle)
            cookie = cookie_of(extra)
            self.later(86401)
            self.call("GET", "auth/me", cookie=cookie)
            self.call("POST", "auth/logout", cookie=cookie)
            self.call("POST", "auth/jellyfin", body=b'{"password": "pw-secret"}')
            self.later(120)
            self.call("POST", "auth/local", body=b'{"password": "pw-secret"}')
        text = out.getvalue() + "\n".join(self.lines)
        self.assertIn("facade GET auth/me 200 " + JF_ID, self.lines)
        self.assertIn("facade POST auth/jellyfin 401 -", self.lines)
        for secret in [TOKEN, handle, "jf-secret", cookie, cookie[2:].split(".")[0], cookie.split(".")[1], "pw-secret", KEY.hex()]:
            self.assertNotIn(secret, text)


SERVICE = {"id": 20, "email": "jellyfin-popular", "permissions": 160}
P_PATHS = [
    "status", "settings/public", "settings/discover",
    "discover/trending?page=1", "discover/movies?page=2&genre=18", "discover/tv", "discover/movies/top?limit=20&offset=0", "discover/tv/top",
    "discover/movies/upcoming", "discover/tv/upcoming", "discover/genreslider/movie", "discover/genreslider/tv", "discover/movies/genre/18",
    "discover/movies/studio/2", "discover/tv/network/49", "discover/keyword/9715/movies", "discover/movies/language/de",
    "search?query=Am%C3%A9lie&page=1", "movie/550", "tv/1399", "tv/tvdb/121361", "movie/550/similar?page=1", "movie/550/recommendations",
    "tv/1399/similar", "tv/1399/recommendations", "person/287", "person/287/combined_credits", "collection/10",
    "media?filter=allavailable&sort=mediaAdded&take=20", "languages", "regions", "genres/movie", "genres/tv",
    "watchproviders/regions", "watchproviders/movies?watchRegion=CH", "watchproviders/tv", "studio/2", "network/49", "keyword/9715",
    "certifications/movie", "certifications/tv", "backdrops", "issue/count",
]


class ProxySeerr(test_app.FakeSeerr):
    def __init__(self):
        super().__init__()
        self.users.append(dict(SERVICE))
        self.gets, self.answer, self.error = [], {}, None

    def __call__(self, method, path, body=None, user=None):
        if path.startswith("/api/v1/user"):
            return super().__call__(method, path, body, user)
        self.gets.append((method, path, user))
        if self.error:
            raise self.error
        return self.answer.get(path, {"page": 1, "results": [], "path": path})


class ReadProxy(FacadeBase):
    def setUp(self):
        super().setUp()
        self.seerr = ProxySeerr()
        self.core.seerr = self.seerr
        _, self.cookie = self.sign_in()

    def get(self, path, cookie=True):
        return self.call("GET", path, cookie=self.cookie if cookie is True else cookie)

    def test_every_enumerated_read_path_proxied_as_service_user(self):
        for path in P_PATHS:
            status, out, _ = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(self.seerr.gets[-1], ("GET", "/api/v1/" + path, 20), path)
        self.assertEqual({u for _, _, u in self.seerr.gets}, {20})
        self.assertEqual(self.seerr.posts, [])
        self.assertIn("facade GET movie/550 200 " + JF_ID, self.lines)
        self.assertIn("facade GET search 200 " + JF_ID, self.lines)

    def test_status_and_settings_public_need_no_cookie(self):
        self.seerr.answer["/api/v1/settings/public"] = {"movie4kEnabled": True, "series4kEnabled": True, "partialRequestsEnabled": True}
        self.seerr.answer["/api/v1/status"] = {"version": "2.7.3"}
        self.assertEqual(self.get("status", cookie=None)[:2], (200, {"version": "2.7.3"}))
        status, out, _ = self.get("settings/public", cookie=None)
        self.assertEqual((status, out), (200, {"movie4kEnabled": False, "series4kEnabled": False, "partialRequestsEnabled": True}))
        self.assertEqual(self.get("settings/public")[1]["movie4kEnabled"], False)
        self.assertIn("facade GET status 200 -", self.lines)

    def test_unknown_gets_404_and_warn_once_per_minute(self):
        paths = ["settings/radarr", "settings/main", "user", "user/5", "user/5/requests", "status/appdata", "auth/plex", "discover/watchlist/x", "media/5/watch_data", "tv/1399/season/1"]
        for _ in range(3):
            for path in paths:
                self.assertEqual(self.get(path)[:2], (404, {"message": "Not found"}), path)
                self.assertEqual(self.get(path, cookie=None)[0], 404, path)
        self.assertEqual(self.seerr.gets, [])
        warned = [line for line in self.lines if line.startswith("facade refused")]
        self.assertEqual(warned, [f"facade refused GET {p}" for p in paths])
        self.later(59)
        self.get("user")
        self.assertEqual(len([line for line in self.lines if line.startswith("facade refused")]), len(paths))
        self.later(1)
        self.get("user")
        self.assertEqual([line for line in self.lines if line.startswith("facade refused")][-1], "facade refused GET user")
        self.assertEqual(len([line for line in self.lines if line.startswith("facade refused")]), len(paths) + 1)

    def test_writes_refused_403(self):
        cases = [(m, p) for m in ("POST", "PUT", "DELETE") for p in ("request/5", "request/5/approve", "request/5/decline", "issue", "watchlist",
                                                                       "user/5/settings/main", "settings/notifications/webhook", "movie/550", "status")]
        for method, path in cases:
            for cookie in (self.cookie, None):
                status, out, extra = self.call(method, path, cookie=cookie, body=b"{}")
                self.assertEqual((status, out, extra), (403, {"message": "Not available in this app"}, []), (method, path))
        self.assertEqual((self.seerr.gets, self.seerr.posts), ([], []))
        self.assertIn("facade refused POST request/5", self.lines)
        self.assertNotIn("facade refused DELETE request/5", self.lines)
        self.assertIn("facade PUT issue 403 -", self.lines)
        self.assertEqual(self.call("GET\x1b[2J", "request/5")[0], 403)
        self.assertEqual(self.lines[-1], "facade - request/5 403 -")
        self.assertFalse([line for line in self.lines if "\x1b" in line])

    def test_reads_need_cookie_401(self):
        forged = facade.make_cookie(b"\x02" * 32, "sid", 1000001, T0, T0 + 100)
        for path in ["movie/550", "discover/trending", "search?query=x", "issue/count", "discover/watchlist", "blocklist", "service/radarr", "request", "user/1000001/quota"]:
            for cookie in (None, "garbage", forged):
                self.assertEqual(self.get(path, cookie=cookie), (401, {"message": "Unauthorized"}, []), (path, cookie))
        self.assertEqual(self.seerr.gets, [])

    def test_path_guard_encoded_variants(self):
        for raw in ["/api/v1/%2e%2e/settings/main", "/api/v1/Settings/Radarr", "/api/v1/movie//550", "/api/v1/movie/550/", "/api/v1//movie/550",
                    "/api/v1/movie/../settings/main", "/api/v1/movie/..", "/api/v1/", "/api/v1/movie%2F550", "/api/v1/" + "a" * 121, "/api/v1/movie/550\r\nX: y",
                    "/api/v1/MOVIE/550", "/api/v1/movie/55%30", "/api/v1/movie/550;x", "/api/v1/discover/trending/"]:
            for method in ("GET", "POST"):
                status, out, extra = self.facade.handle(method, raw, {"Cookie": f"connect.sid={self.cookie}"}, b"")
                self.assertEqual((status, out, extra), (404, {"message": "Not found"}, []), (method, raw))
        q = "query=" + "a" * (1024 - len("query="))
        self.assertEqual(self.get("search?" + q)[0], 200)
        self.assertEqual(self.seerr.gets[-1][1], "/api/v1/search?" + q)
        for bad in ["search?" + q + "a", "search?query=a b", "search?query=\r\nX-Api-User: 1", "search?query=é", "search?query=a\x00"]:
            self.assertEqual(self.get(bad), (400, {"message": "Bad request"}, []), bad)
        self.assertEqual(self.get("search?query=Am%C3%A9lie&page=1")[0], 200)
        self.assertEqual(self.seerr.gets[-1][1], "/api/v1/search?query=Am%C3%A9lie&page=1")
        self.assertEqual(len(self.seerr.gets), 2)
        self.assertFalse([line for line in self.lines if "\r" in line or "\n" in line or "%2e" in line or "X-Api" in line])

    def test_scrub_removes_secrets_everywhere(self):
        user = {"id": 7, "email": "jane@example.com", "displayName": "Jane Doe", "apiKey": "k1", "plexToken": "p1", "jellyfinAuthToken": "j1",
                "jellyfinDeviceId": "d1", "password": "pw", "resetPasswordGuid": "g1", "plexId": 5, "jellyfinUserId": "u1", "plexUsername": "jane",
                "jellyfinUsername": "jane", "username": "jane", "avatar": "/a.png", "permissions": 2, "settings": {"locale": "de", "pgpKey": "x"}}
        fixture = {"id": 550, "mediaInfo": {"id": 3, "jellyfinMediaId": "abc", "serviceUrl": "http://radarr:7878/movie/550", "serviceUrl4k": "http://radarr4k:7878/x",
                   "externalServiceId": 12, "externalServiceId4k": 13, "externalServiceSlug": "fight-club", "externalServiceSlug4k": "s", "requests": [{"id": 9, "requestedBy": dict(user), "modifiedBy": dict(user),
                   "media": {"issues": [{"createdBy": dict(user), "comments": [{"user": dict(user), "message": "hi"}]}]}}]},
                   "deep": [[{"a": {"b": {"settings": {"email": "x@example.com"}, "apiKey": "k2", "keep": 1}}}]], "email": "top@example.com",
                   "results": [{"user": dict(user), "title": "Dune"}, {"requestedBy": None}, {"modifiedBy": 5}]}
        out = facade.scrub(fixture, 1000001, set())
        text = json.dumps(out)
        for key in ["email", "apiKey", "plexToken", "jellyfinAuthToken", "jellyfinDeviceId", "password", "resetPasswordGuid", "plexId", "jellyfinUserId", "settings",
                    "plexUsername", "jellyfinUsername", "username", "permissions", "avatar", "serviceUrl", "serviceUrl4k", "externalService"]:
            self.assertNotIn(f'"{key}"', text, key)
        self.assertNotIn("@", text)
        self.assertNotIn("radarr", text)
        self.assertNotIn('"externalService', text)
        self.assertEqual(facade.scrub([[{"x": {"externalServiceFoo": 1, "serviceUrl": "u", "keep": 2}}]], 1000001, set()), [[{"x": {"keep": 2}}]])
        for secret in ["k1", "p1", "j1", "d1", "pw", "g1", "u1", "k2", "jane"]:
            self.assertNotIn(f'"{secret}"', text, secret)
        self.assertEqual(out["deep"], [[{"a": {"b": {"keep": 1}}}]])
        self.assertEqual(out["mediaInfo"]["jellyfinMediaId"], "abc")
        self.assertEqual(out["results"], [{"user": facade.HOUSEHOLD, "title": "Dune"}, {"requestedBy": None}, {"modifiedBy": facade.HOUSEHOLD}])
        self.assertEqual(facade.HOUSEHOLD, {"id": 0, "displayName": "Household"})
        self.assertEqual(fixture["mediaInfo"]["requests"][0]["requestedBy"]["email"], "jane@example.com")

    def test_scrub_applied_to_proxied_bodies(self):
        self.seerr.answer["/api/v1/movie/550"] = {"id": 550, "mediaInfo": {"requests": [{"id": 9, "requestedBy": {"id": 1, "email": "admin@example.com", "displayName": "Admin"}}]}}
        status, out, _ = self.get("movie/550")
        self.assertEqual((status, out["mediaInfo"]["requests"][0]["requestedBy"]), (200, facade.HOUSEHOLD))
        self.assertNotIn("@", json.dumps(out))

    def test_requested_by_rewritten_mine_vs_household(self):
        jane = {"id": 7, "displayName": "Jane Doe", "email": "jane@example.com"}
        admin = {"id": 1, "displayName": "Admin", "email": "admin@example.com"}
        fixture = {"results": [{"id": 5, "requestedBy": dict(jane), "modifiedBy": dict(admin)}, {"id": 6, "requestedBy": dict(jane), "modifiedBy": dict(jane)},
                               {"id": 7, "requestedBy": dict(admin), "modifiedBy": None}, {"id": True, "requestedBy": dict(jane)}],
                   "mediaInfo": {"requests": [{"id": 6, "requestedBy": dict(jane)}, {"id": 8, "requestedBy": dict(jane)}]}}
        out = facade.scrub(fixture, 1000001, {5, 6, 7, 1}, me_name="Jane Doe")
        me = {"id": 1000001, "displayName": "Jane Doe"}
        r = out["results"]
        self.assertEqual([(x["requestedBy"], x["modifiedBy"]) for x in r[:3]], [(me, facade.HOUSEHOLD), (me, me), (me, None)])
        self.assertEqual(r[3]["requestedBy"], facade.HOUSEHOLD)
        self.assertEqual([x["requestedBy"] for x in out["mediaInfo"]["requests"]], [me, facade.HOUSEHOLD])
        self.assertEqual(facade.scrub({"id": 5, "requestedBy": dict(jane)}, 1000001, {5})["requestedBy"], {"id": 1000001, "displayName": "You"})
        self.assertNotIn(1, [x["requestedBy"]["id"] for x in r if x["requestedBy"]])

    def test_seerr_non_json_and_errors_are_502_json(self):
        bad = [seerr_http_error(500), seerr_http_error(502), seerr_http_error(503), socket.timeout("timed out"),
               OSError("connection refused"), ValueError("Expecting value: <html>login</html>"), http.client.IncompleteRead(b"<html>"),
               http.client.RemoteDisconnected("gone"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")]
        for err in bad:
            self.seerr.error = err
            for path in ["movie/550", "status"]:
                status, out, extra = self.get(path)
                self.assertEqual((status, out, extra), (502, {"message": "Seerr is not reachable"}, []), (err, path))
        self.seerr.error = None
        for value in [None, "<html>login</html>", 5, True]:
            self.seerr.answer["/api/v1/movie/550"] = value
            self.assertEqual(self.get("movie/550")[:2], (502, {"message": "Seerr is not reachable"}), value)
        self.assertEqual(self.seerr.posts, [])

    def test_seerr_4xx_is_404_json(self):
        for code in (400, 401, 403, 404, 422):
            self.seerr.error = seerr_http_error(code)
            for path in ["movie/999999999", "status"]:
                self.assertEqual(self.get(path), (404, {"message": "Not found"}, []), (code, path))

    def test_bugs_are_not_reported_as_seerr_down(self):
        for err in (KeyError("x"), TypeError("x")):
            self.seerr.error = err
            with self.assertRaises(type(err)):
                self.get("movie/550")

    def test_issues_empty_count_proxied(self):
        for path in ["issue", "issue?take=10&skip=0&filter=open", "issue/3"]:
            self.assertEqual(self.get(path)[:2], (200, {"pageInfo": {"pages": 0, "pageSize": 25, "results": 0, "page": 1}, "results": []}), path)
            self.assertEqual(self.get(path, cookie=None)[0], 401, path)
        self.assertEqual(self.seerr.gets, [])
        self.assertEqual(self.get("issue/count")[0], 200)
        self.assertEqual(self.seerr.gets, [("GET", "/api/v1/issue/count", 20)])

    def test_watchlist_and_blocklist_empty_pages(self):
        self.assertEqual(self.get("discover/watchlist?page=1")[:2], (200, {"page": 1, "totalPages": 1, "totalResults": 0, "results": []}))
        for path in ["blocklist", "blacklist?take=25&skip=0"]:
            self.assertEqual(self.get(path)[:2], (200, {"pageInfo": {"pages": 0, "pageSize": 25, "results": 0, "page": 1}, "results": []}), path)
        self.assertEqual(self.seerr.gets, [])

    def test_no_service_user_503_and_nothing_created(self):
        self.seerr.users = [u for u in self.seerr.users if u["id"] != 20]
        for path in ["movie/550", "status", "settings/public"]:
            status, out, extra = self.get(path)
            self.assertEqual((status, set(out), extra), (503, {"message"}, []), path)
        self.assertEqual((self.seerr.gets, self.seerr.posts), ([], []))

    def test_service_user_id_1_never_used(self):
        self.seerr.users = [u for u in self.seerr.users if u["id"] != 20] + [{"id": 1, "email": "jellyfin-popular", "permissions": 160}]
        self.assertEqual(self.get("movie/550")[0], 503)
        self.assertEqual(self.seerr.gets, [])

    def test_proxy_rotates_old_cookie(self):
        self.later(86401)
        status, _, extra = self.get("movie/550")
        self.assertEqual(status, 200)
        self.assertIsNotNone(cookie_of(extra))


def seerr_http_error(code):
    return urllib.error.HTTPError("http://seerr:5055/api/v1/x", code, "error", {}, io.BytesIO(b"<html>Seerr login secret-page</html>"))


class LoadKey(unittest.TestCase):
    def test_load_key_missing_or_short_is_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "facade.secret"
            self.assertIsNone(facade.load_key(p))
            for bad in ["", "ab" * 31, "zz" * 32, "ab" * 33]:
                p.write_text(bad)
                self.assertIsNone(facade.load_key(p), bad)
            p.write_text(KEY.hex() + "\n")
            self.assertEqual(facade.load_key(p), KEY)
            self.assertIsNone(facade.load_key(None))


if __name__ == "__main__":
    unittest.main()
