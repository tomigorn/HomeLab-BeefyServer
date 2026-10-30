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
import unittest.mock
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
        self.me_error, self.logout_error, self.admin_down, self.delay = None, None, False, 0

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
            if self.logout_error:
                raise self.logout_error
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
            self.assertEqual((status, out, extra), (401, {"message": facade.TV_FAIL}, []))
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

    def test_authenticate_prunes_dead_sessions(self):
        _, cookie = self.sign_in()
        self.call("POST", "auth/logout", cookie=cookie)
        self.sign_in()
        self.assertEqual([r[1] for r in self.session_row()].count(1), 0)
        self.later(facade.MAX_AGE + 10)
        self.sign_in()
        self.assertEqual(len(self.session_row()), 1)

    def test_deeply_nested_body_is_400(self):
        status, out, extra = self.call("POST", "auth/jellyfin/quickconnect/authenticate", body=b"[" * 200000)
        self.assertEqual((status, out, extra), (400, {"message": "Bad request"}, []))

    def test_failed_token_logout_is_closed_and_logged_without_token(self):
        for err, name in [(http_error(500), "HTTPError"), (OSError("reset"), "OSError")]:
            self.lines.clear()
            self.jf.logout_error = err
            status, out, _ = self.authenticate(self.initiate())
            self.assertEqual(status, 200, out)
            self.assertIn(f"facade logout failed: {name}", self.lines)
            self.assertFalse(any(TOKEN in l for l in self.lines))
            if isinstance(err, urllib.error.HTTPError):
                self.assertTrue(err.fp.closed)

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
        self.assertEqual([r for _, r, _ in self.session_row()], [1])

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


SERVICE = {"id": 20, "email": "jellyfin-popular", "permissions": 160, "userType": 2}
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
        for path in ["issue", "issue?take=10&skip=0&filter=open"]:
            self.assertEqual(self.get(path)[:2], (200, {"pageInfo": {"pages": 0, "pageSize": 25, "results": 0, "page": 1}, "results": []}), path)
            self.assertEqual(self.get(path, cookie=None)[0], 401, path)
        for cookie in (True, None):
            self.assertEqual(self.get("issue/3", cookie=cookie), (404, {"message": "Not found"}, []))
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


class RequestSeerr(test_app.FakeSeerr):
    def __init__(self):
        super().__init__()
        self.users.append(dict(SERVICE))
        self.error, self.details_error, self.tags = None, None, [{"id": 5, "label": "download-now"}]

    def __call__(self, method, path, body=None, user=None):
        if method == "GET" and path.startswith("/api/v1/service/") and path.count("/") == 5:
            return {"tags": self.tags}
        if method == "GET" and path.startswith(("/api/v1/movie/", "/api/v1/tv/")) and self.details_error:
            raise self.details_error
        if method == "POST" and path == "/api/v1/request":
            if self.error:
                raise self.error
            self.posts.append(("request", body, user))
            rid = 900 + len(self.posts)
            return {"id": rid, "status": 2, "type": body["mediaType"], "is4k": False, "media": {"tmdbId": body["mediaId"], "status": 3},
                    "requestedBy": {"id": user, "email": "jellyfin-popular", "displayName": "Jellyfin popular", "plexToken": "x"},
                    "modifiedBy": {"id": user, "email": "jellyfin-popular"}, "seasons": [{"id": 1, "seasonNumber": s, "status": 2} for s in body.get("seasons", [])]}
        return super().__call__(method, path, body, user)


class CreateRequest(FacadeBase):
    MOVIE = {"mediaId": 101, "mediaType": "movie"}
    TV = {"mediaId": 202, "mediaType": "tv", "seasons": "all"}
    ME = {"id": facade.FIRST_UID, "displayName": "Jane Doe"}

    def setUp(self):
        super().setUp()
        self.seerr = RequestSeerr()
        self.core.seerr = self.seerr
        _, self.cookie = self.sign_in()

    def post(self, body, cookie=True):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return self.call("POST", "request", cookie=self.cookie if cookie is True else cookie, body=raw)

    def requests(self):
        return [p for p in self.seerr.posts if p[0] == "request"]

    def fill_requests(self, n, user=JF_ID):
        with self.core.db() as con:
            for _ in range(n):
                con.execute("INSERT INTO requests (user_id, ref, at, day, done) VALUES (?, 'x', ?, ?, 1)", (user, self.core.iso(self.now), self.core.day(self.now)))

    def test_needs_cookie(self):
        self.assertEqual(self.post(self.MOVIE, cookie=None)[:2], (401, {"message": "Unauthorized"}))
        self.assertEqual(self.requests(), [])

    def test_success_201_scrubbed_requested_by_me(self):
        status, out, _ = self.post(self.MOVIE)
        self.assertEqual(status, 201)
        self.assertEqual((out["id"], out["requestedBy"], out["modifiedBy"]), (900 + len(self.seerr.posts), self.ME, self.ME))
        self.assertNotIn("jellyfin-popular", json.dumps(out))
        self.assertNotIn("plexToken", json.dumps(out))
        self.assertEqual(self.requests()[-1], ("request", {"mediaType": "movie", "mediaId": 101}, 20))
        self.assertEqual(self.core.requested_ids(JF_ID), [out["id"]])

    def test_server_0_absent_is_night_1_is_now_with_tag(self):
        for body, mode, tags in [(self.MOVIE, "night", None), (dict(self.MOVIE, serverId=0), "night", None), (dict(self.MOVIE, serverId=None), "night", None), (dict(self.MOVIE, serverId=1), "now", [5])]:
            status, out, _ = self.post(body)
            self.assertEqual(status, 201, (body, out))
            self.assertEqual(self.requests()[-1][1].get("tags"), tags, body)
            self.assertEqual(self.audit()[-1]["mode"], mode, body)

    def test_other_server_profile_or_root_400(self):
        for extra in ({"serverId": 2}, {"serverId": -1}, {"serverId": "1"}, {"serverId": True}, {"profileId": 1}, {"profileId": "0"}, {"rootFolder": "/movies"}, {"rootFolder": ""}):
            self.assertEqual(self.post(dict(self.MOVIE, **extra))[:2], (400, {"message": "Bad request"}), extra)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.post(dict(self.MOVIE, serverId=0, profileId=0, rootFolder="Library", tvdbId=None, imdbId="tt0000001"))[0], 201)

    def test_is4k_refused(self):
        self.assertEqual(self.post(dict(self.MOVIE, is4k=True))[:2], (403, {"message": "4K is not offered"}))
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.post(dict(self.MOVIE, is4k=False))[0], 201)

    def test_extra_keys_400(self):
        for extra in ({"userId": 1}, {"tags": [5]}, {"languageProfileId": 1}, {"rootFolderId": 0}):
            self.assertEqual(self.post(dict(self.MOVIE, **extra))[:2], (400, {"message": "Bad request"}), extra)
        self.assertEqual(self.requests(), [])

    def test_malformed_bodies_are_400(self):
        sent = []
        self.core.send = lambda *a, **k: sent.append(a)
        bodies = [b"[]", b"null", b"5", b'"x"', b"{bad", b"", b"\xff\xfe", json.dumps([self.MOVIE]).encode(),
                  {"mediaType": "movie"}, {"mediaId": 101}, {"mediaId": "101", "mediaType": "movie"}, {"mediaId": 101.0, "mediaType": "movie"},
                  {"mediaId": True, "mediaType": "movie"}, {"mediaId": 0, "mediaType": "movie"}, {"mediaId": -5, "mediaType": "movie"}, {"mediaId": 10 ** 12, "mediaType": "movie"},
                  {"mediaId": 101, "mediaType": "person"}, {"mediaId": 101, "mediaType": "Movie"}, {"mediaId": 101, "mediaType": None},
                  {"mediaId": 202, "mediaType": "tv", "seasons": []}, {"mediaId": 202, "mediaType": "tv", "seasons": ["4"]}, {"mediaId": 202, "mediaType": "tv", "seasons": [4.0]},
                  {"mediaId": 202, "mediaType": "tv", "seasons": [True]}, {"mediaId": 202, "mediaType": "tv", "seasons": "ALL"}, {"mediaId": 202, "mediaType": "tv", "seasons": {"4": 1}},
                  {"mediaId": 202, "mediaType": "tv", "seasons": [-1]}, {"mediaId": 202, "mediaType": "tv", "seasons": list(range(200))}, {"mediaId": 202, "mediaType": "tv"},
                  {"mediaId": 101, "mediaType": "movie", "seasons": [1]}, {"mediaId": 101, "mediaType": "movie", "seasons": "all"},
                  dict(self.MOVIE, is4k="true"), dict(self.MOVIE, is4k=1), dict(self.MOVIE, tvdbId="x"), dict(self.MOVIE, imdbId=5), dict(self.MOVIE, imdbId="x" * 100)]
        for body in bodies:
            self.core.hits.clear()
            self.assertEqual(self.post(body)[:2], (400, {"message": "Bad request"}), body)
        self.assertEqual((sent, self.requests()), ([], []))

    def test_not_json_or_too_large(self):
        headers = {"Content-Type": "text/plain", "Cookie": f"connect.sid={self.cookie}"}
        self.assertEqual(self.facade.handle("POST", "/api/v1/request", headers, json.dumps(self.MOVIE).encode())[:2], (415, {"message": "JSON only"}))
        headers["Content-Type"] = "application/json; charset=utf-8"
        self.assertEqual(self.facade.handle("POST", "/api/v1/request", headers, json.dumps(self.MOVIE).encode())[0], 201)
        self.assertEqual(self.post(json.dumps(dict(self.MOVIE, imdbId=" " * 2100)).encode())[:2], (413, {"message": "Too large"}))

    def test_tv_all_or_list_with_season_requests_only_that_season(self):
        for seasons in ("all", [4], [1, 2, 3, 4, 5]):
            self.seerr.media = {}
            status, out, _ = self.post(dict(self.TV, seasons=seasons))
            self.assertEqual(status, 201, (seasons, out))
            self.assertEqual(self.requests()[-1][1], {"mediaType": "tv", "mediaId": 202, "seasons": [4]}, seasons)
            self.assertEqual([s["seasonNumber"] for s in out["seasons"]], [4])

    def test_tv_list_without_season_403_message(self):
        self.assertEqual(self.post(dict(self.TV, seasons=[1, 3]))[:2], (403, {"message": "Only season 4 can be requested"}))
        self.assertEqual(self.requests(), [])

    def test_not_listed_vs_no_release_messages(self):
        d = pathlib.Path(self.tmp.name)
        (d / "details.json").write_text(json.dumps({"updated": "2026-10-28T04:39:00", "entries": {"d" * 20: {"k": "tv", "id": 77}}}))
        not_listed = (403, {"message": "Only titles from the Popular lists can be requested"})
        no_release = (403, {"message": "No good release yet; checked nightly"})
        self.assertEqual(self.post({"mediaId": 999, "mediaType": "movie"})[:2], not_listed)
        self.assertEqual(self.post({"mediaId": 101, "mediaType": "tv", "seasons": "all"})[:2], not_listed)
        self.assertEqual(self.post({"mediaId": 303, "mediaType": "movie"})[:2], no_release)
        self.assertEqual(self.post({"mediaId": 77, "mediaType": "tv", "seasons": [1]})[:2], no_release)
        self.assertEqual(self.requests(), [])
        self.assertEqual([a["decision"] for a in self.audit()[-4:]], ["unknown entry", "unknown entry", "refused: no release", "refused: no release"])

    def error_rows(self):
        """(setup, body, status, message) per row of the spec Errors table."""
        def media(status, req=None):
            return lambda: self.seerr.media.update({"movie/101": {"status": status, "requests": req or []}})

        def attr(obj, name, value):
            return lambda: setattr(obj, name, value() if callable(value) else value)
        return [
            (media(5), self.MOVIE, 409, "Already in the library or requested"),
            (media(3, [{"status": 2}]), self.MOVIE, 409, "Already in the library or requested"),
            (media(1, [{"status": 3}]), self.MOVIE, 403, "This title was declined by an admin"),
            (lambda: None, {"mediaId": 999, "mediaType": "movie"}, 403, "Only titles from the Popular lists can be requested"),
            (lambda: None, {"mediaId": 303, "mediaType": "movie"}, 403, "No good release yet; checked nightly"),
            (lambda: self.fill_requests(10), self.MOVIE, 403, "Request quota reached: 10 per 7 days"),
            (attr(self.core.cfg, "daily_max", 0), self.MOVIE, 403, "Today's request quota for everyone is used up"),
            (attr(self.seerr, "tags", []), dict(self.MOVIE, serverId=1), 503, "Download now is not available, choose Tonight"),
            (attr(self.seerr, "error", lambda: seerr_http_error(500)), self.MOVIE, 500, "Seerr could not take the request"),
            (attr(self.seerr, "error", socket.timeout("timed out")), self.MOVIE, 500, "Seerr could not take the request"),
            (attr(self.seerr, "details_error", OSError("refused")), self.MOVIE, 500, "Seerr could not take the request"),
            (attr(self.seerr, "details_error", lambda: seerr_http_error(502)), self.MOVIE, 500, "Seerr could not take the request"),
            (attr(self.seerr, "users", [dict(u) for u in self.seerr.users if u["id"] != 20]), self.MOVIE, 503, "Requests are not set up"),
        ]

    def test_each_error_row_status_and_message(self):
        for i in range(len(self.error_rows())):
            self.tearDown()
            self.setUp()
            setup, body, status, message = self.error_rows()[i]
            setup()
            got = self.post(body)
            self.assertEqual(got, (status, {"message": message}, []), (i, body))
            if status == 403 and "quota" in message.lower():
                self.assertIn("quota", got[1]["message"].lower())
            self.assertEqual(self.requests(), [], i)
            self.assertNotIn("user", [p[0] for p in self.seerr.posts], i)
            self.assertEqual(self.core.requested_ids(JF_ID), [], i)
        self.assertEqual(self.core.overview(JF_ID)[0], 10)

    def test_quota_message_follows_config(self):
        self.core.cfg.limit, self.core.cfg.days = 3, 14
        self.fill_requests(3)
        self.assertEqual(self.post(self.MOVIE)[:2], (403, {"message": "Request quota reached: 3 per 14 days"}))

    def test_never_202(self):
        statuses = set()
        for i in range(len(self.error_rows())):
            self.tearDown()
            self.setUp()
            self.error_rows()[i][0]()
            statuses.add(self.post(self.error_rows()[i][1])[0])
            statuses.add(self.post(self.TV)[0])
            self.core.cfg.dry_flag.touch()
            statuses.add(self.post(self.TV)[0])
        self.assertNotIn(202, statuses)
        self.assertIn(201, statuses)

    def test_rate_limit_shared_with_page(self):
        page_token = "d" * 32
        with unittest.mock.patch.dict(test_app.USERS, {page_token: dict(JANE)}):
            for _ in range(self.core.cfg.rate_per_min - 1):
                self.assertEqual(test_app.Base.post(self, {"id": "tok-movie-xxxxxxxxxxxy"}, token=page_token)[0], 403)
            self.assertEqual(self.post({"mediaId": 999, "mediaType": "movie"})[0], 403)
            self.assertEqual(self.post(self.MOVIE)[:2], (429, {"message": "Too many requests, try again in a minute"}))
            self.assertEqual(test_app.Base.post(self, {"id": "tok-movie-xxxxxxxxxxxx"}, token=page_token)[0], 429)
        self.assertEqual(self.requests(), [])

    def test_audit_line_via_moonfin(self):
        self.post(dict(self.MOVIE, serverId=1))
        a = self.audit()[-1]
        self.assertEqual({k: a[k] for k in ("user", "userId", "entry", "mode", "via", "decision", "title", "seerrUser", "seerrVia")},
                         {"user": "Jane Doe", "userId": JF_ID, "entry": "movie/101", "mode": "now", "via": "moonfin", "decision": "requested", "title": "Animals",
                          "seerrUser": 20, "seerrVia": "service user: no Seerr account"})
        self.assertEqual(a["requestId"], self.core.requested_ids(JF_ID)[0])

    def test_dry_run_no_seerr_post_audit_dry_run(self):
        self.core.cfg.dry_flag.touch()
        status, out, _ = self.post(self.TV)
        self.assertEqual((status, out), (201, {"id": 0, "status": 1, "type": "tv", "is4k": False, "media": {"tmdbId": 202, "mediaType": "tv", "status": 1},
                                                 "requestedBy": self.ME, "seasons": [{"id": 0, "seasonNumber": 4, "status": 1}]}))
        status, out, _ = self.post(dict(self.MOVIE, serverId=1))
        self.assertEqual((status, out["type"], out["seasons"], out["media"]), (201, "movie", [], {"tmdbId": 101, "mediaType": "movie", "status": 1}))
        self.assertEqual(self.requests(), [])
        self.assertEqual([(a["decision"], a["via"], a["entry"]) for a in self.audit()[-2:]], [("dry-run", "moonfin", "tv/202/s4"), ("dry-run", "moonfin", "movie/101")])
        self.assertEqual((self.core.overview(JF_ID)[0], self.core.requested_ids(JF_ID)), (10, []))
        self.seerr.media["movie/101"] = {"status": 5, "requests": []}
        self.assertEqual(self.post(self.MOVIE)[:2], (409, {"message": "Already in the library or requested"}))

    def test_dry_run_still_refuses_over_limit_and_daily_cap(self):
        self.core.cfg.dry_flag.touch()
        self.fill_requests(10)
        self.assertEqual(self.post(self.MOVIE)[:2], (403, {"message": "Request quota reached: 10 per 7 days"}))
        self.tearDown()
        self.setUp()
        self.core.cfg.dry_flag.touch()
        self.core.cfg.daily_max = 3
        self.fill_requests(3, user="someone-else")
        self.assertEqual(self.post(self.MOVIE)[:2], (403, {"message": "Today's request quota for everyone is used up"}))
        self.assertEqual(self.requests(), [])

    def test_dry_run_now_without_tag_503(self):
        self.core.cfg.dry_flag.touch()
        self.seerr.tags = []
        self.assertEqual(self.post(dict(self.MOVIE, serverId=1))[:2], (503, {"message": "Download now is not available, choose Tonight"}))
        self.assertEqual(self.post(self.MOVIE)[0], 201)
        self.assertEqual(self.requests(), [])

    def test_dry_run_no_service_user_503_like_real_path(self):
        self.seerr.users = [u for u in self.seerr.users if u["id"] != 20]
        real = self.post(self.MOVIE)
        self.core.cfg.dry_flag.touch()
        self.assertEqual(self.post(self.MOVIE), real)
        self.assertEqual(real[:2], (503, {"message": "Requests are not set up"}))
        self.assertEqual(self.seerr.posts, [])

    def test_odd_seerr_bodies_are_500_message(self):
        for odd in ({"no": "results"}, [1, 2], {"mediaInfo": "x"}):
            self.tearDown()
            self.setUp()
            inner = self.seerr
            self.core.seerr = lambda m, path, body=None, user=None, odd=odd: odd if m == "GET" and path.startswith(("/api/v1/user", "/api/v1/movie/")) else inner(m, path, body, user)
            self.assertEqual(self.post(self.MOVIE), (500, {"message": "Seerr could not take the request"}, []), odd)
            self.assertEqual((self.requests(), self.core.overview(JF_ID)[0]), ([], 10), odd)

    def test_no_service_user_created_by_facade_request(self):
        self.seerr.users = [u for u in self.seerr.users if u["id"] != 20]
        self.post(self.MOVIE)
        self.assertEqual(self.seerr.posts, [])
        self.assertEqual(self.audit()[-1]["decision"], "refused: no service user")


class ServersAndQuota(FacadeBase):
    def setUp(self):
        super().setUp()
        self.seerr = ProxySeerr()
        self.core.seerr = self.seerr
        out, self.cookie = self.sign_in()
        self.uid = out["id"]

    def get(self, path, cookie=True):
        return self.call("GET", path, cookie=self.cookie if cookie is True else cookie)

    def test_service_lists_two_servers_tonight_default(self):
        for kind in ("radarr", "sonarr"):
            status, out, _ = self.get(f"service/{kind}")
            self.assertEqual(status, 200)
            self.assertEqual([(s["id"], s["name"], s["isDefault"], s["is4k"], s["activeProfileId"], s["activeDirectory"]) for s in out],
                             [(0, "🌙 Tonight · lower power cost", True, False, 0, "Library"), (1, "⚡ Now · daytime price", False, False, 0, "Library")])
        self.assertEqual(self.seerr.gets, [])
        self.assertEqual(self.get("service/radarr", cookie=None)[0], 401)

    def test_service_details_0_1_and_unknown_404(self):
        for kind in ("radarr", "sonarr"):
            for sid in (0, 1):
                status, out, _ = self.get(f"service/{kind}/{sid}")
                self.assertEqual(status, 200)
                self.assertEqual(out["server"]["id"], sid)
                self.assertEqual(out["profiles"], [{"id": 0, "name": "Best found release (1080p/2160p)"}])
                self.assertEqual(out["rootFolders"], [{"id": 0, "path": "Library", "freeSpace": None, "totalSpace": None}])
                self.assertEqual(out["tags"], [])
                if kind == "sonarr":
                    self.assertEqual(out["languageProfiles"], [])
                    self.assertEqual((out["server"]["activeAnimeProfileId"], out["server"]["activeAnimeDirectory"]), (None, None))
                else:
                    self.assertNotIn("languageProfiles", out)
            status, out, _ = self.get(f"service/{kind}/2")
            self.assertEqual((status, list(out)), (404, ["message"]))
        self.assertEqual(self.seerr.gets, [])

    def fill(self, n, user=JF_ID, day=None):
        with self.core.db() as con:
            for _ in range(n):
                con.execute("INSERT INTO requests (user_id, ref, at, day, done) VALUES (?, 'x', ?, ?, 1)", (user, self.core.iso(self.now), day or self.core.day(self.now)))

    def test_quota_left_and_restricted_rows(self):
        path = f"user/{self.uid}/quota"
        self.fill(3)
        status, out, _ = self.get(path)
        self.assertEqual(status, 200)
        self.assertEqual(out, {"movie": {"days": 7, "limit": 10, "used": 3, "remaining": 7, "restricted": False},
                               "tv": {"days": 7, "limit": 0, "used": 0, "remaining": None, "restricted": False}})
        self.fill(7)
        out = self.get(path)[1]
        self.assertEqual(out, {"movie": {"days": 7, "limit": 10, "used": 10, "remaining": 0, "restricted": True},
                               "tv": {"days": 7, "limit": 10, "used": 10, "remaining": 0, "restricted": True}})

    def test_quota_daily_cap_full_is_restricted(self):
        self.core.cfg.daily_max = 4
        self.fill(4, user="someone-else")
        self.fill(2)
        out = self.get(f"user/{self.uid}/quota")[1]
        self.assertEqual(out["movie"], {"days": 7, "limit": 10, "used": 2, "remaining": 0, "restricted": True})
        self.assertEqual((out["tv"]["restricted"], out["tv"]["remaining"]), (True, 0))

    def test_quota_follows_config(self):
        self.core.cfg.limit, self.core.cfg.days = 5, 3
        self.fill(1)
        self.assertEqual(self.get(f"user/{self.uid}/quota")[1]["movie"], {"days": 3, "limit": 5, "used": 1, "remaining": 4, "restricted": False})

    def test_quota_other_user_403(self):
        for uid in (self.uid + 1, 1, 0, 99999999):
            status, out, _ = self.get(f"user/{uid}/quota")
            self.assertEqual((status, list(out)), (403, ["message"]), uid)
        self.assertEqual(self.get(f"user/{self.uid}/quota", cookie=None)[0], 401)


def fake_request(rid, status=2, kind="movie", media_status=3, by=None, created="2026-10-20T10:00:00.000Z", updated=None, is4k=False):
    media = {"id": rid, "tmdbId": rid * 10, "mediaType": kind, "status": media_status, "status4k": 1}
    return {"id": rid, "status": status, "type": kind, "is4k": is4k, "createdAt": created, "updatedAt": updated or created, "media": media,
            "requestedBy": by or {"id": 20, "email": "jellyfin-popular", "displayName": "Jellyfin popular", "plexToken": "x"},
            "modifiedBy": {"id": 1, "email": "admin@example.com", "displayName": "Admin"}, "seasons": []}


class MineSeerr(ProxySeerr):
    def __init__(self):
        super().__init__()
        self.requests, self.linked_ids, self.fetches = {}, [], []

    def __call__(self, method, path, body=None, user=None):
        if method == "GET" and path.startswith("/api/v1/request?"):
            self.gets.append((method, path, user))
            return {"pageInfo": {}, "results": [{"id": i} for i in self.linked_ids]}
        if method == "GET" and path.startswith("/api/v1/request/"):
            rid = int(path.rsplit("/", 1)[1])
            self.fetches.append((rid, user))
            if self.error:
                raise self.error
            if rid not in self.requests:
                raise seerr_http_error(404)
            return json.loads(json.dumps(self.requests[rid]))
        return super().__call__(method, path, body, user)


class MyRequests(FacadeBase):
    def setUp(self):
        super().setUp()
        self.seerr = MineSeerr()
        self.core.seerr = self.seerr
        out, self.cookie = self.sign_in()
        self.uid = out["id"]
        self.me = {"id": self.uid, "displayName": "Jane Doe"}

    def get(self, path, cookie=True):
        return self.call("GET", path, cookie=self.cookie if cookie is True else cookie)

    def add(self, req, mine=True):
        self.seerr.requests[req["id"]] = req
        if mine:
            self.core.remember(JF_ID, req["id"])

    def ids(self, path):
        status, out, _ = self.get(path)
        self.assertEqual(status, 200, out)
        return [r["id"] for r in out["results"]]

    def test_cold_cache_fetches_are_capped(self):
        for i in range(1, facade.MAX_TAKE + 51):
            self.add(fake_request(i))
        self.assertEqual(len(self.get("request?take=100")[1]["results"]), facade.MAX_TAKE)
        self.assertEqual(len(self.seerr.fetches), facade.MAX_TAKE)

    def test_cold_cache_stops_after_first_seerr_error(self):
        for i in range(1, 6):
            self.add(fake_request(i))
        self.seerr.error = OSError("timed out")
        self.assertEqual(self.get("request")[0], 502)
        self.assertEqual(len(self.seerr.fetches), 1)

    def test_request_list_only_mine_paged_filtered_sorted(self):
        for i in range(1, 6):
            self.add(fake_request(i, created=f"2026-10-2{i}T10:00:00.000Z", updated=f"2026-10-2{6 - i}T10:00:00.000Z"))
        self.add(fake_request(6, status=1, media_status=2))
        self.add(fake_request(7, status=3, media_status=1))
        self.add(fake_request(8, status=5, media_status=5))
        self.add(fake_request(9, status=2, media_status=5))
        self.add(fake_request(10, status=4, media_status=1))
        self.add(fake_request(11, kind="tv", status=2, media_status=4))
        self.add(fake_request(12, status=2, media_status=7))
        self.add(fake_request(50), mine=False)
        everything = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        self.assertEqual(self.ids("request?take=100"), everything)
        status, out, _ = self.get("request?take=5&skip=5")
        self.assertEqual(out["pageInfo"], {"pages": 3, "pageSize": 5, "results": 12, "page": 2})
        self.assertEqual([r["id"] for r in out["results"]], [7, 6, 5, 4, 3])
        self.assertEqual(self.ids("request?take=5&skip=10"), [2, 1])
        self.assertEqual(self.get("request")[1]["pageInfo"], {"pages": 2, "pageSize": 10, "results": 12, "page": 1})
        self.assertEqual(self.ids("request?take=1000"), everything)
        self.assertEqual(self.ids("request?take=100&filter=pending"), [6])
        self.assertEqual(self.ids("request?take=100&filter=approved"), [12, 11, 9, 5, 4, 3, 2, 1])
        self.assertEqual(self.ids("request?take=100&filter=processing"), [11, 5, 4, 3, 2, 1])
        self.assertEqual(self.ids("request?take=100&filter=unavailable"), [11, 6, 5, 4, 3, 2, 1])
        self.assertEqual(self.ids("request?take=100&filter=available"), [8])
        self.assertEqual(self.ids("request?take=100&filter=completed"), [8])
        self.assertEqual(self.ids("request?take=100&filter=deleted"), [])
        self.assertEqual(self.ids("request?take=100&filter=failed"), [10])
        self.assertEqual(self.ids("request?take=100&filter=bogus"), everything)
        self.assertEqual(self.ids("request?take=3&sort=modified"), [1, 2, 3])
        self.assertEqual(self.ids("request?take=3&sort=added"), [12, 11, 10])
        self.assertEqual(self.ids("request?take=3&sort=modified&sortDirection=asc"), [12, 11, 10])
        self.assertEqual(self.ids("request?take=100&mediaType=tv"), [11])
        self.assertNotIn(50, self.ids("request?take=100"))

    def test_request_list_other_requested_by_403(self):
        for who in (1, self.uid + 1, 7):
            status, out, _ = self.get(f"request?requestedBy={who}")
            self.assertEqual((status, list(out)), (403, ["message"]), who)
        self.add(fake_request(1))
        self.assertEqual(self.ids(f"request?requestedBy={self.uid}"), [1])
        for bad in ("x", "1.5", "-1"):
            self.assertEqual(self.get(f"request?requestedBy={bad}")[0], 400, bad)
        for bad in ("take=0", "take=x", "skip=-1", "skip=x", "take=-3"):
            self.assertEqual(self.get(f"request?{bad}")[0], 400, bad)

    def test_list_requests_never_leak_other_users(self):
        other = {"id": 55, "email": "john@example.com", "displayName": "John Roe", "plexToken": "y"}
        self.add(fake_request(1))
        self.add(fake_request(2, by=other), mine=False)
        self.add(fake_request(3, by=other), mine=False)
        status, out, _ = self.get("request?take=100")
        self.assertEqual([r["id"] for r in out["results"]], [1])
        self.assertEqual({r["requestedBy"]["id"] for r in out["results"]}, {self.uid})
        self.assertEqual(out["results"][0]["modifiedBy"], facade.HOUSEHOLD)
        blob = json.dumps(out)
        for needle in ("@", "plexToken", "John", "jellyfin-popular", "Admin"):
            self.assertNotIn(needle, blob)
        self.assertEqual(self.get("request/2")[0], 403)
        self.assertEqual(self.get("request/3")[0], 403)
        self.assertEqual(self.get("request/count")[1]["total"], 1)
        self.assertNotIn(2, [i for i, _ in self.seerr.fetches])
        self.assertNotIn(3, [i for i, _ in self.seerr.fetches])

    def test_other_users_request_in_linked_list_still_shown_only_if_seerr_says_mine(self):
        self.seerr.users[0]["jellyfinUserId"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.add(fake_request(1))
        self.add(fake_request(2, by={"id": 7, "email": "jane@example.com", "displayName": "Jane Doe"}), mine=False)
        self.seerr.linked_ids = [2]
        self.assertEqual(self.ids("request?take=100"), [2, 1])
        self.assertTrue(any("requestedBy=7" in p and "take=100" in p and u is None for _, p, u in self.seerr.gets))
        self.assertTrue(all(u is None for _, u in self.seerr.fetches))
        out = self.get("request?take=100")[1]
        self.assertEqual([r["requestedBy"] for r in out["results"]], [self.me, self.me])

    def test_other_users_requests_in_media_never_appear(self):
        other = {"id": 55, "displayName": "John Roe"}
        mine_nested = {"id": 1, "status": 2, "requestedBy": {"id": 20}}
        r1 = fake_request(1)
        r1["media"]["requests"] = [mine_nested, {"id": 777, "status": 4, "requestedBy": other}, {"id": True, "status": 2}]
        r2 = fake_request(2)
        r2["media"]["requests"] = [{"id": 888, "status": 3, "requestedBy": other}]
        self.add(r1)
        self.add(r2)
        for path in ("request?take=100", "request/1", "request/2"):
            out = self.get(path)[1]
            blob = json.dumps(out)
            for needle in ("777", "888", "John"):
                self.assertNotIn(needle, blob, path)
        media = self.get("request/1")[1]["media"]
        self.assertEqual([x["id"] for x in media["requests"]], [1])
        self.assertEqual(media["requests"][0]["requestedBy"], self.me)
        self.assertEqual(self.get("request/2")[1]["media"]["requests"], [])

    def test_page_number_follows_seerr_formula(self):
        for i in range(1, 8):
            self.add(fake_request(i))
        self.assertEqual(self.get("request?take=5&skip=3")[1]["pageInfo"], {"pages": 2, "pageSize": 5, "results": 7, "page": 2})
        self.assertEqual(self.get("request?take=5&skip=0")[1]["pageInfo"]["page"], 1)
        self.assertEqual(self.get("request?take=5&skip=5")[1]["pageInfo"]["page"], 2)

    def test_no_linked_user_no_seerr_list_call(self):
        self.add(fake_request(1))
        self.get("request")
        self.assertEqual([p for _, p, _ in self.seerr.gets if "/request?" in p], [])

    def test_request_count_numbers(self):
        self.add(fake_request(1, status=1, media_status=2))
        self.add(fake_request(2, status=2, media_status=3))
        self.add(fake_request(3, status=2, media_status=5, kind="tv"))
        self.add(fake_request(4, status=3, media_status=1))
        self.add(fake_request(5, status=5, media_status=5, kind="tv"))
        self.add(fake_request(6, status=4, media_status=1))
        self.add(fake_request(50, status=1), mine=False)
        status, out, _ = self.get("request/count")
        self.assertEqual((status, out), (200, {"total": 6, "movie": 4, "tv": 2, "pending": 1, "approved": 2, "declined": 1, "processing": 1, "available": 1, "completed": 1}))

    def test_request_count_empty(self):
        self.assertEqual(self.get("request/count")[1], {"total": 0, "movie": 0, "tv": 0, "pending": 0, "approved": 0, "declined": 0, "processing": 0, "available": 0, "completed": 0})
        self.assertEqual(self.get("request")[1]["results"], [])

    def test_request_by_id_only_mine(self):
        self.add(fake_request(1, kind="tv"))
        self.add(fake_request(2), mine=False)
        status, out, _ = self.get("request/1")
        self.assertEqual((status, out["id"], out["requestedBy"], out["media"]["tmdbId"]), (200, 1, self.me, 10))
        self.assertNotIn("email", json.dumps(out))
        self.assertEqual(self.get("request/2")[0], 403)
        self.assertEqual(self.get("request/999")[0], 403)
        self.assertEqual(self.get("request/1", cookie=None)[0], 401)

    def test_vanished_request_is_dropped(self):
        self.core.remember(JF_ID, 77)
        self.add(fake_request(1))
        self.assertEqual(self.ids("request"), [1])
        self.assertEqual(self.get("request/77")[0], 403)
        self.assertEqual(self.get("request/count")[1]["total"], 1)

    def test_requests_cached_60_seconds(self):
        self.add(fake_request(1))
        self.get("request")
        self.get("request/1")
        self.get("request/count")
        self.assertEqual(len(self.seerr.fetches), 1)
        self.later(59)
        self.get("request")
        self.assertEqual(len(self.seerr.fetches), 1)
        self.later(2)
        self.get("request")
        self.assertEqual(len(self.seerr.fetches), 2)

    def test_seerr_down_is_502_json(self):
        self.add(fake_request(1))
        for err in (seerr_http_error(500), socket.timeout("timed out"), OSError("refused")):
            self.seerr.error = err
            self.facade.cache.clear()
            for path in ("request", "request/count", "request/1"):
                status, out, _ = self.get(path)
                self.assertEqual((status, out), (502, {"message": "Seerr is not reachable"}), (path, err))

    def test_new_request_shows_up_in_proxy_scrub_as_mine(self):
        self.add(fake_request(9))
        self.seerr.answer["/api/v1/movie/1"] = {"id": 1, "mediaInfo": {"requests": [{"id": 9, "requestedBy": {"id": 20, "email": "x@example.com"}}]}}
        self.assertEqual(self.get("movie/1")[1]["mediaInfo"]["requests"][0]["requestedBy"], self.me)


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


class Listener(FacadeBase):
    def setUp(self):
        super().setUp()
        self.seerr = ProxySeerr()
        self.core.seerr = self.seerr
        self.handler_core = facade.FacadeHandler.core
        facade.FacadeHandler.core = self.facade
        self.srv = facade.make_facade_server(("127.0.0.1", 0), workers=2, backlog=2)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.lines = []
        self.facade.log = self.lines.append

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        facade.FacadeHandler.core = self.handler_core
        super().tearDown()

    def call(self, method, path, body=b"", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def test_facade_server_sets_cookie_and_json(self):
        status, headers, raw = self.call("POST", "/api/v1/auth/jellyfin", b"{}")
        self.assertEqual(status, 401)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIsInstance(json.loads(raw), dict)
        self.seerr.answer["/api/v1/status"] = {"version": "2.0"}
        status, headers, raw = self.call("GET", "/api/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("version", json.loads(raw))
        status, headers, raw = self.call("POST", "/api/v1/auth/jellyfin/quickconnect/initiate")
        self.assertEqual(status, 200)
        code = json.loads(raw)
        status, headers, raw = self.call("POST", "/api/v1/auth/jellyfin/quickconnect/authenticate", json.dumps({"secret": code["secret"]}).encode())
        self.assertEqual(status, 200)
        self.assertIn("connect.sid=", headers["Set-Cookie"])

    def test_put_delete_reach_the_facade(self):
        self.assertEqual(self.call("DELETE", "/api/v1/request/1")[0], 403)
        self.assertEqual(self.call("PUT", "/api/v1/request/1", b"{}")[0], 403)
        self.assertEqual(self.call("PATCH", "/api/v1/request/1", b"{}")[0], 403)
        self.assertEqual([l.split()[1] for l in self.lines if not l.startswith("facade refused")], ["DELETE", "PUT", "PATCH"])

    def test_oversize_body_413(self):
        status, _, raw = self.call("POST", "/api/v1/request", b"x" * 2049)
        self.assertEqual(status, 413)
        self.assertIsInstance(json.loads(raw), dict)
        self.assertEqual(self.call("POST", "/api/v1/auth/jellyfin", b"x" * 2048)[0], 400)

    def test_access_log_has_no_query_or_cookie(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.call("GET", "/api/v1/search?query=secretterm", headers={"Cookie": "connect.sid=cookiesecret"})
        self.assertNotIn("secretterm", out.getvalue())
        self.assertNotIn("cookiesecret", out.getvalue())
        self.assertNotIn("GET /api", out.getvalue())

    def test_internal_error_is_json(self):
        def boom(*a):
            raise RuntimeError("x")
        self.facade.handle = boom
        status, headers, raw = self.call("GET", "/api/v1/status")
        self.assertEqual((status, headers["Content-Type"]), (500, "application/json"))
        self.assertIsInstance(json.loads(raw), dict)


class MakeFacade(unittest.TestCase):
    def test_start_facade_survives_failures(self):
        import app
        for boom in (lambda core: (_ for _ in ()).throw(RuntimeError("/secret/path")), lambda core: facade.Facade(core, None, None)):
            out = io.StringIO()
            with unittest.mock.patch.object(facade, "make_facade", boom), contextlib.redirect_stdout(out):
                app.start_facade(object())
            self.assertRegex(out.getvalue(), r"^facade off: \w+\n$")
            self.assertNotIn("secret", out.getvalue())

    def test_start_facade_survives_bind_failure(self):
        import app
        out = io.StringIO()
        fake = unittest.mock.Mock()
        with unittest.mock.patch.object(facade, "make_facade", lambda core: fake), \
                unittest.mock.patch.object(facade, "make_facade_server", side_effect=OSError("/x in use")), \
                contextlib.redirect_stdout(out):
            app.start_facade(object())
        self.assertEqual(out.getvalue(), "facade off: OSError\n")

    def test_start_facade_hands_its_tv_codes_to_the_page_backend_only_when_listening(self):
        import app
        fake, srv = unittest.mock.Mock(), unittest.mock.Mock()
        core = type("C", (), {"tv_codes": None})()
        with unittest.mock.patch.object(facade, "make_facade", lambda c: fake), unittest.mock.patch.object(facade, "make_facade_server", lambda addr: srv):
            app.start_facade(core)
        self.assertIs(core.tv_codes, fake.codes)

    def test_make_facade_without_key_is_none(self):
        core = type("C", (), {})()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(facade.make_facade(core, {"POP_FACADE_SECRET_FILE": "/nonexistent/facade.secret"}))
            self.assertIsNone(facade.make_facade(core, {}))
        self.assertEqual(out.getvalue(), "facade off: no key\n" * 2)

    def test_make_facade_builds_with_key(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "facade.secret"
            p.write_text(KEY.hex())
            base = test_app.Base()
            base.setUp()
            try:
                f = facade.make_facade(base.core, {"POP_FACADE_SECRET_FILE": str(p)})
                self.assertIsInstance(f, facade.Facade)
                self.assertEqual(f.key, KEY)
            finally:
                base.tearDown()

    def test_header_validated(self):
        self.assertEqual(facade.jf_header("a", "b c", "d", "1"), 'MediaBrowser Client="a", Device="b c", DeviceId="d", Version="1"')
        self.assertTrue(facade.jf_header("a", "b", "d", "1", "tok").endswith(', Token="tok"'))
        for bad in ('x"y', "a,b", "a=b", "", "x" * 65, "a\nb"):
            with self.assertRaises(ValueError):
                facade.jf_header("a", "b", bad, "1")

    def test_production_jf_turns_http_exceptions_into_oserror(self):
        seen = {}

        class Resp:
            def read(self):
                raise http.client.IncompleteRead(b"par")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen["auth"] = req.get_header("Authorization")
            return Resp()
        jf = facade.make_jf("http://jellyfin:8096", opener=opener)
        with self.assertRaises(OSError):
            jf("GET", "/Users/Me", device_id="dev1", token="tok")
        self.assertIn('DeviceId="dev1"', seen["auth"])
        self.assertIn('Token="tok"', seen["auth"])


JANE_POP = test_app.USERS[test_app.TOKEN]
JANE_POP_ID = "40e05f0a9e964fd9b225eb1379dfcfa4"
CODE_RE = re.compile(r"[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{10}")


class TvCode(FacadeBase):
    def setUp(self):
        super().setUp()
        self.core.tv_codes = self.facade.codes
        self.jf.users[JANE_POP_ID] = dict(JANE_POP)
        self.out = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.out))

    def issue(self, token=test_app.TOKEN, origin=test_app.ORIGIN, body=b"{}", ctype="application/json"):
        headers = {"Content-Type": ctype}
        if origin:
            headers["Origin"] = origin
        if token:
            headers["X-Emby-Token"] = token
        return self.core.handle("POST", "/hss-popular/tv-code", headers, body)

    def code(self):
        status, out = self.issue()
        self.assertEqual(status, 200, out)
        return out["code"]

    def login(self, username, password, path="auth/jellyfin", field="username"):
        return self.call("POST", path, body=json.dumps({field: username, "password": password}).encode())

    def test_issue_returns_a_10_char_code_valid_10_min(self):
        status, out = self.issue()
        self.assertEqual(status, 200, out)
        self.assertEqual(set(out), {"code", "expires", "minutes", "user"})
        self.assertRegex(out["code"], CODE_RE)
        self.assertEqual(out["minutes"], 10)
        self.assertEqual(out["user"], "jane")
        self.assertEqual(datetime.datetime.fromisoformat(out["expires"]), test_app.NOW + datetime.timedelta(minutes=10))
        self.assertEqual(facade.TV_ALPHABET, "ABCDEFGHJKMNPQRSTUVWXYZ23456789")
        self.assertNotEqual(self.code(), out["code"])

    def test_issue_refusals(self):
        self.assertEqual(self.issue(token=None)[0], 401)
        self.assertEqual(self.issue(token="z" * 32)[0], 401)
        self.assertEqual(self.issue(origin=None)[0], 403)
        self.assertEqual(self.issue(origin="https://evil.example.com")[0], 403)
        self.assertEqual(self.issue(ctype="text/plain")[0], 415)
        for bad in [b"", b"[]", b"{", b'{"user": "guest"}', b'{"userId": "11111111222233334444555555555555"}', b"x" * 3000]:
            self.assertIn(self.issue(body=bad)[0], (400, 413), bad)
        self.assertEqual(self.issue(token="c" * 32)[0], 403)
        self.core.tv_codes = None
        self.assertEqual(self.issue()[0], 503)
        self.assertEqual(self.facade.codes.codes, {})

    def test_issue_limit_5_per_hour(self):
        for _ in range(5):
            self.code()
            self.later(60)
        status, out = self.issue()
        self.assertEqual(status, 429)
        self.assertNotIn("code", out)
        self.later(3600 - 4 * 60)
        self.code()

    def test_code_stored_only_as_a_keyed_hash(self):
        code = self.code()
        stored = repr(self.facade.codes.codes) + repr(vars(self.facade.codes))
        self.assertNotIn(code, stored)
        self.assertNotIn(code.lower(), stored)
        name, digest, _ = self.facade.codes.codes[JANE_POP_ID]
        self.assertEqual((name, len(digest)), ("jane", 32))
        self.assertNotEqual(digest, hashlib.sha256(code.encode()).digest())

    def test_sign_in_with_username_and_code_same_session_as_quick_connect(self):
        code = self.code()
        status, me, extra = self.login("JANE", code)
        self.assertEqual(status, 200, me)
        self.assertEqual(me, self.facade.me({"uid": 1000001, "name": "jane"}))
        self.assertFalse(me["permissions"] & 2)
        cookie = cookie_of(extra)
        self.assertIn("HttpOnly", extra[0][1])
        self.assertEqual(self.call("GET", "auth/me", cookie=cookie)[:2], (200, me))
        self.assertTrue(self.facade.sessions.active(facade.read_cookie(KEY, f"connect.sid={cookie}", T0)["sid"], JANE_POP_ID))
        self.assertEqual(self.jf.calls, [])
        self.assertIn("tv code: sign-in", [a["decision"] for a in self.audit()])

    def test_code_typed_in_lower_case_with_spaces_or_dash_and_local_auth(self):
        code = self.code()
        self.assertEqual(self.login(" jane ", f"{code[:5].lower()}-{code[5:].lower()} ")[0], 200)
        code = self.code()
        self.assertEqual(self.login("jane", f"{code[:5]} {code[5:]}", path="auth/local", field="email")[0], 200)

    def test_single_use(self):
        code = self.code()
        self.assertEqual(self.login("jane", code)[0], 200)
        self.assertEqual(self.login("jane", code)[:2], (401, {"message": facade.TV_FAIL}))

    def test_single_use_under_concurrency(self):
        code = self.code()
        barrier = threading.Barrier(8)
        results = []

        def go():
            barrier.wait()
            results.append(self.login("jane", code)[0])

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(200), 1)
        self.assertLessEqual(set(results), {200, 401, 429})
        self.assertEqual(len(self.session_row()), 1)

    def test_expires_after_10_min(self):
        code = self.code()
        self.later(599)
        self.assertEqual(self.login("jane", code)[0], 200)
        code = self.code()
        self.later(600)
        self.assertEqual(self.login("jane", code)[:2], (401, {"message": facade.TV_FAIL}))

    def test_new_code_invalidates_the_previous(self):
        old = self.code()
        new = self.code()
        self.assertEqual(self.login("jane", old)[0], 401)
        self.assertEqual(self.login("jane", new)[0], 200)

    def test_code_bound_to_its_user(self):
        code = self.code()
        self.assertEqual(self.login("guest", code)[:2], (401, {"message": facade.TV_FAIL}))
        self.assertEqual(self.login("Jane Doe", code)[0], 401)
        self.assertEqual(self.login("jane", code)[0], 200)

    def test_no_user_enumeration(self):
        self.code()
        answers = [self.login(u, p) for u, p in [("jane", "WRONGCODE2"), ("nobody", "WRONGCODE2"), ("guest", "ABCDEFGHJK"), ("jane", "real-password"), ("", ""), ("jane", None), (5, "x")]]
        self.assertEqual(len({json.dumps(a) for a in answers}), 1, answers)
        self.assertEqual(answers[0], (401, {"message": facade.TV_FAIL}, []))
        self.assertEqual(self.jf.calls, [])

    def test_compare_is_constant_time_also_for_unknown_users(self):
        self.code()
        real = hmac.compare_digest
        seen = []
        with unittest.mock.patch.object(facade.hmac, "compare_digest", side_effect=lambda a, b: seen.append(1) or real(a, b)):
            self.login("nobody", "ABCDEFGHJK")
            self.login("jane", "ABCDEFGHJK")
        self.assertEqual(len(seen), 2)

    def test_lockout_after_5_failures_burns_the_code(self):
        code = self.code()
        for _ in range(4):
            self.assertEqual(self.login("Jane", "WRONGCODE2")[0], 401)
        self.assertEqual(self.login("jane", "WRONGCODE2")[:2], (429, {"message": facade.TV_LOCKED}))
        self.assertEqual(self.login("jane", code)[:2], (429, {"message": facade.TV_LOCKED}))
        self.later(15 * 60)
        self.assertEqual(self.login("jane", code)[0], 401)
        self.assertEqual(self.lines.count('facade tv code lockout "jane"'), 1)
        self.assertEqual(self.lines.count('facade tv code failed "Jane"'), 4)
        self.assertNotIn("lockout", pathlib.Path(self.tmp.name, "audit.jsonl").read_text())

    def test_issuing_a_new_code_lifts_the_lockout(self):
        self.code()
        for _ in range(5):
            self.login("jane", "WRONGCODE2")
        self.assertEqual(self.login("jane", self.code())[0], 200)

    def test_unknown_users_are_locked_out_too(self):
        for _ in range(5):
            self.login("nobody", "WRONGCODE2")
        self.assertEqual(self.login("NOBODY", "WRONGCODE2")[:2], (429, {"message": facade.TV_LOCKED}))
        self.assertEqual(self.login("other", "WRONGCODE2")[0], 401)

    def test_failures_older_than_the_window_are_forgotten(self):
        self.code()
        for _ in range(4):
            self.login("jane", "WRONGCODE2")
        self.later(15 * 60)
        self.login("jane", "WRONGCODE2")
        code = self.code()
        self.assertEqual(self.login("jane", code)[0], 200)

    def test_global_failure_budget_brakes_all_code_sign_ins_for_a_minute(self):
        code = self.code()
        for i in range(facade.TV_FAILS_GLOBAL):
            self.assertEqual(self.login(f"user{i}", "WRONGCODE2")[0], 401)
        self.assertEqual(self.login("jane", code)[:2], (429, {"message": facade.TV_BUSY}))
        self.assertEqual(self.login("someone", "WRONGCODE2")[:2], (429, {"message": facade.TV_BUSY}))
        self.later(60)
        self.assertEqual(self.login("jane", code)[0], 200)
        self.assertEqual([a["decision"] for a in self.audit()].count("tv code: brake"), 1)
        self.assertEqual(facade.TV_FAILS_GLOBAL, 20)

    def test_disabled_or_renamed_user_gets_no_session(self):
        code = self.code()
        self.jf.users[JANE_POP_ID] = {**JANE_POP, "Policy": {"IsDisabled": True}}
        self.assertEqual(self.login("jane", code)[0], 403)
        self.jf.users[JANE_POP_ID] = {**JANE_POP, "Name": "janet"}
        self.later(61)
        code = self.code()
        self.core.auth_cache.clear()
        self.assertEqual(self.login("jane", code)[0], 401)
        self.assertEqual(self.session_row(), [])

    def test_jellyfin_down_is_503_without_session(self):
        code = self.code()
        self.jf.admin_down = True
        self.assertEqual(self.login("jane", code)[0], 503)
        self.assertEqual(self.session_row(), [])

    def test_bad_bodies(self):
        for bad in [b"{", b"[]", b"null", b"[" * 200000]:
            self.assertEqual(self.call("POST", "auth/jellyfin", body=bad)[:2], (400, {"message": "Bad request"}), bad[:5])
        self.assertEqual(self.login("j" * 65, "ABCDEFGHJK")[0], 401)

    def test_code_and_tokens_never_logged(self):
        code = self.code()
        self.login("jane", "WRONGCODE2")
        _, _, extra = self.login("jane", code)
        cookie = cookie_of(extra)
        text = self.out.getvalue() + "\n".join(self.lines) + pathlib.Path(self.tmp.name, "audit.jsonl").read_text()
        for secret in [code, code.lower(), "WRONGCODE2", test_app.TOKEN, cookie, cookie[2:].split(".")[0]]:
            self.assertNotIn(secret, text)
        audit = {a["decision"]: a for a in self.audit()}
        self.assertEqual((audit["tv code: issued"]["userId"], audit["tv code: issued"]["user"]), (JANE_POP_ID, "jane"))
        self.assertIn('facade tv code failed "jane"', self.lines)
        self.assertNotIn("tv code: failed", audit)
        self.assertEqual(audit["tv code: sign-in"]["userId"], JANE_POP_ID)
        self.assertIn("facade POST auth/jellyfin 200 " + JANE_POP_ID, self.lines)

    def test_audit_name_of_a_failure_is_cut_and_printable(self):
        self.login("x\n" * 50, "WRONGCODE2")
        line = [l for l in self.lines if l.startswith("facade tv code failed")][-1]
        self.assertEqual(line, 'facade tv code failed "' + "x" * 50 + '"')
        self.login("y" * 100, "WRONGCODE2")
        self.assertEqual(self.lines[-2], 'facade tv code failed "' + "y" * 64 + '"')


if __name__ == "__main__":
    unittest.main()
