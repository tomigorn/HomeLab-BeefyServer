import http.server
import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import unittest

_spec = importlib.util.spec_from_file_location("abs_key_rotate", pathlib.Path(__file__).with_name("abs-key-rotate.py"))
kr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kr)

NOW = 1_800_000_000
DAY = 86400


def iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeAbs(http.server.ThreadingHTTPServer):
    """ABS 2.37 API keys: tokens map to users; admins list, create and delete keys."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.users = {"svc": {"id": "svc", "username": "floppy-sync", "type": "admin", "isActive": True}, "abr": {"id": "abr", "username": "abr", "type": "user", "isActive": True}}
        self.keys = {}
        self.calls = []
        self.fail_new_me = False
        self.expires_in_ms = False
        self.counter = 0

    def add(self, token, user, name, expires_at=None, kid=None):
        self.counter += 1
        kid = kid or "k%d" % self.counter
        self.keys[kid] = {"id": kid, "name": name, "userId": user, "isActive": True, "expiresAt": iso(expires_at) if expires_at else None, "token": token}
        return kid

    def user_of(self, token):
        for k in self.keys.values():
            if k["token"] == token and k["isActive"]:
                return self.users[k["userId"]]
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, data=None):
        body = json.dumps(data).encode() if data is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self, method):
        s = self.server
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        s.calls.append((method, self.path, token))
        user = s.user_of(token)
        if not user:
            return self.reply(401)
        if self.path == "/api/me":
            if s.fail_new_me and token.startswith("new"):
                return self.reply(500)
            return self.reply(200, user)
        if user["type"] != "admin":
            return self.reply(403)
        if self.path == "/api/api-keys" and method == "GET":
            return self.reply(200, {"apiKeys": [{k: v for k, v in x.items() if k != "token"} for x in s.keys.values()]})
        if self.path == "/api/api-keys" and method == "POST":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            token = "new%d" % (s.counter + 1)
            kid = s.add(token, body["userId"], body["name"], NOW + (body["expiresIn"] // 1000 if s.expires_in_ms else body["expiresIn"]))
            return self.reply(200, {"apiKey": {**{k: v for k, v in s.keys[kid].items() if k != "token"}, "apiKey": token}})
        if self.path.startswith("/api/api-keys/") and method == "DELETE":
            kid = self.path.rsplit("/", 1)[1]
            if kid not in s.keys:
                return self.reply(404)
            del s.keys[kid]
            # ABS answers a delete with sendStatus(200): text "OK", not JSON
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
            return
        return self.reply(404)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_DELETE(self):
        self.handle_any("DELETE")


class RotateTest(unittest.TestCase):
    def setUp(self):
        self.abs = FakeAbs()
        threading.Thread(target=self.abs.serve_forever, daemon=True).start()
        self.addCleanup(self.abs.server_close)
        self.addCleanup(self.abs.shutdown)
        self.base = "http://127.0.0.1:%d" % self.abs.server_address[1]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.key_file = self.dir / "floppy-sync-abs-key"
        self.state = self.dir / "state"
        self.alerts = []

    def run_auto(self, now=NOW):
        return kr.auto(self.key_file, self.state, base=self.base, now=now, alert=self.alerts.append)

    def use(self, token):
        self.key_file.write_text(token + "\n")
        os.chmod(self.key_file, 0o600)

    def test_key_without_expiry_is_replaced_by_one_expiring_in_365_days(self):
        old = self.abs.add("oldtoken", "svc", "floppy-sync")
        self.abs.add("abrtoken", "abr", "audiobookrequest")
        self.use("oldtoken")
        r = self.run_auto()
        self.assertTrue(r["ok"])
        self.assertTrue(r["rotated"])
        new = self.key_file.read_text().strip()
        self.assertTrue(new.startswith("new"))
        self.assertEqual(oct(self.key_file.stat().st_mode & 0o777), "0o600")
        self.assertNotIn(old, self.abs.keys, "old key deleted")
        mine = [k for k in self.abs.keys.values() if k["userId"] == "svc"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["name"], "floppy-sync")
        self.assertEqual(r["expires"], NOW + 365 * DAY)
        self.assertIn("abrtoken", [k["token"] for k in self.abs.keys.values()], "other keys untouched")
        self.assertEqual(json.loads(self.state.joinpath("status.json").read_text())["expires"], NOW + 365 * DAY)
        self.assertEqual(self.alerts, [])

    def test_key_with_more_than_45_days_left_stays(self):
        self.abs.add("oldtoken", "svc", "floppy-sync", NOW + 46 * DAY)
        self.use("oldtoken")
        r = self.run_auto()
        self.assertEqual((r["ok"], r["rotated"], r["expires"]), (True, False, NOW + 46 * DAY))
        self.assertEqual(self.key_file.read_text().strip(), "oldtoken")
        self.assertFalse([c for c in self.abs.calls if c[0] != "GET"])

    def test_key_within_45_days_is_rotated(self):
        self.abs.add("oldtoken", "svc", "floppy-sync", NOW + 44 * DAY)
        self.use("oldtoken")
        self.assertTrue(self.run_auto()["rotated"])

    def test_failed_verify_keeps_the_old_key_deletes_the_new_and_alerts(self):
        old = self.abs.add("oldtoken", "svc", "floppy-sync")
        self.use("oldtoken")
        self.abs.fail_new_me = True
        r = self.run_auto()
        self.assertFalse(r["ok"])
        self.assertEqual(self.key_file.read_text().strip(), "oldtoken")
        self.assertEqual(list(self.abs.keys), [old])
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("rotation failed", self.alerts[0])
        self.assertNotIn("oldtoken", self.alerts[0])

    def test_new_key_with_a_short_expiry_is_deleted_old_kept(self):
        old = self.abs.add("oldtoken", "svc", "floppy-sync")
        self.use("oldtoken")
        self.abs.expires_in_ms = True
        r = self.run_auto()
        self.assertFalse(r["ok"])
        self.assertEqual(self.key_file.read_text().strip(), "oldtoken")
        self.assertEqual(list(self.abs.keys), [old])

    def test_alert_when_less_than_30_days_left(self):
        self.abs.add("oldtoken", "svc", "floppy-sync", NOW + 20 * DAY)
        self.use("oldtoken")
        self.abs.fail_new_me = True
        self.run_auto()
        self.assertTrue(any("20 days" in a for a in self.alerts), self.alerts)

    def test_wrong_user_key_is_refused(self):
        self.abs.add("abrtoken", "abr", "audiobookrequest")
        self.use("abrtoken")
        r = self.run_auto()
        self.assertFalse(r["ok"])
        self.assertFalse([c for c in self.abs.calls if c[0] != "GET"])

    def test_two_keys_named_floppy_sync_use_the_remembered_id(self):
        self.abs.add("stray", "svc", "floppy-sync", NOW + 300 * DAY)
        cur = self.abs.add("oldtoken", "svc", "floppy-sync")
        self.use("oldtoken")
        self.assertFalse(self.run_auto()["ok"], "ambiguous without a remembered id")
        self.state.mkdir(exist_ok=True)
        (self.state / "key-id").write_text(cur)
        r = self.run_auto()
        self.assertTrue(r["rotated"])
        self.assertNotIn(cur, self.abs.keys)
        self.assertEqual((self.state / "key-id").read_text().strip(), [k for k, v in self.abs.keys.items() if v["token"] == self.key_file.read_text().strip()][0])

    def test_client_refuses_other_paths(self):
        c = kr.AbsAdmin("t", self.base)
        with self.assertRaises(kr.AbsError):
            c.call("DELETE", "/api/users/x")
        with self.assertRaises(kr.AbsError):
            c.call("GET", "/api/api-keys/../users")


if __name__ == "__main__":
    unittest.main()
