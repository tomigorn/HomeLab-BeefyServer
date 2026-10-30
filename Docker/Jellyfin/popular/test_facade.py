import base64
import datetime
import hashlib
import hmac
import json
import pathlib
import sys
import unittest

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
