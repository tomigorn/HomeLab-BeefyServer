import base64
import contextlib
import datetime
import hashlib
import hmac
import io
import json
import pathlib
import re
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

    def test_other_paths_404_until_routed(self):
        for method, path in [("GET", "status"), ("GET", "auth/jellyfin"), ("POST", "auth/me"), ("GET", "auth/logout")]:
            self.assertEqual(self.call(method, path)[:2], (404, {"message": "Not found"}), path)
        self.assertEqual(self.facade.handle("GET", "/auth/me", {}, b"")[:2], (404, {"message": "Not found"}))


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
        text = out.getvalue()
        for secret in [TOKEN, handle, "jf-secret", cookie, cookie[2:].split(".")[0], cookie.split(".")[1], "pw-secret", KEY.hex()]:
            self.assertNotIn(secret, text)


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
