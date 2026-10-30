import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
import unittest.mock
import urllib.request

_spec = importlib.util.spec_from_file_location("abs_push", pathlib.Path(__file__).with_name("abs-push.py"))
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)

NOW = 1_800_000_000.0
JPEG = b"\xff\xd8\xff" + b"x" * 10
SERVICE = {"id": "svc", "username": "floppy-sync", "type": "admin", "isActive": True}


def entry(item, last, episode=None):
    e = {"libraryItemId": item, "lastUpdate": last, "currentTime": 60, "isFinished": False}
    if episode:
        e["episodeId"] = episode
    return e


def abs_user(uid, name, type_="user", active=True):
    return {"id": uid, "username": name, "type": type_, "isActive": active, "token": "secret-token", "email": "x@example.com"}


class FakeAbs:
    def __init__(self, users=None, progress=None, me=SERVICE, covers=None):
        self.me = me
        self.users = users or []
        self.progress = progress or {}
        self.covers = covers or {}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path == "/api/me":
            return self.me
        if path == "/api/users":
            return {"users": self.users}
        if path.startswith("/api/users/"):
            uid = path.split("/")[3]
            u = next(u for u in self.users if u["id"] == uid)
            return {**u, "mediaProgress": self.progress.get(uid, [])}
        if path.endswith("?expanded=1"):
            return {"id": path.split("/")[3].split("?")[0], "expanded": True}
        return {"id": path.split("/")[3], "media": {"coverPath": "/metadata/c.jpg"}}

    def cover(self, item_id):
        self.calls.append(f"cover {item_id}")
        return self.covers.get(item_id, ("image/jpeg", JPEG))


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return FakeResponse(b'{"ok": true}')


class GetOnlyTest(unittest.TestCase):
    def client(self):
        abs_ = ap.Abs("k")
        abs_.opener = FakeOpener()
        return abs_

    def test_get_sends_get_with_bearer(self):
        abs_ = self.client()
        self.assertEqual(abs_.get("/api/me"), {"ok": True})
        req = abs_.opener.requests[0]
        self.assertEqual((req.get_method(), req.full_url, req.data), ("GET", ap.ABS_URL + "/api/me", None))
        self.assertEqual(req.get_header("Authorization"), "Bearer k")

    def test_refuses_every_other_method(self):
        abs_ = self.client()
        for method in ("POST", "PATCH", "PUT", "DELETE", "HEAD", "get"):
            with self.subTest(method=method), self.assertRaises(ap.AbsError):
                abs_._open("/api/me", method=method)
        self.assertEqual(abs_.opener.requests, [])

    def test_refuses_paths_outside_the_api(self):
        abs_ = self.client()
        for path in ("/login", "//evil.example.com/api/me", "@evil.example.com/api/me", "/api/../x"):
            with self.subTest(path=path), self.assertRaises(ap.AbsError):
                abs_._open(path)
        self.assertEqual(abs_.opener.requests, [])

    def test_no_write_helpers(self):
        for name in ("post", "patch", "put", "delete"):
            self.assertFalse(hasattr(ap.Abs, name))

    def test_redirects_are_not_followed(self):
        handler = ap.NoRedirect()
        req = urllib.request.Request(ap.ABS_URL + "/api/me")
        self.assertIsNone(handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/"))
        self.assertTrue(any(isinstance(h, ap.NoRedirect) for h in ap.Abs("k").opener.handlers))


class TargetsTest(unittest.TestCase):
    def test_floppy_users_minus_skip_and_exclude(self):
        floppy = ["zoe", "jane", "floppy-sync", "root", "abr", "bob"]
        self.assertEqual(ap.targets(floppy, ["bob", "# comment", ""]), ["jane", "zoe"])

    def test_read_exclude_ignores_missing_file_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ap.read_exclude(pathlib.Path(d, "missing")), [])
            p = pathlib.Path(d, "exclude")
            p.write_text("# who\nbob  \n\n jane # later\n")
            self.assertEqual(ap.read_exclude(p), ["bob", "jane"])

    def test_floppy_users_parses_the_result_line(self):
        out = 'noise\nRESULT ["zoe", "jane", "bad name"]\n'
        self.assertEqual(ap.floppy_users(lambda code: (0, out)), ["zoe", "jane"])
        with self.assertRaises(ap.AbsError):
            ap.floppy_users(lambda code: (1, "Traceback\n"))


class ServiceKeyTest(unittest.TestCase):
    def test_accepts_only_the_admin_service_user(self):
        ap.check_service(FakeAbs(me=SERVICE))
        for me in [{**SERVICE, "username": "zoe"}, {**SERVICE, "type": "user"}, {**SERVICE, "type": "root"}, {**SERVICE, "isActive": False}]:
            with self.subTest(me=me), self.assertRaises(ap.AbsError):
                ap.check_service(FakeAbs(me=me))


class SyncedUsersTest(unittest.TestCase):
    def test_matches_targets_and_skips_service_root_inactive(self):
        users = [abs_user("u1", "zoe"), abs_user("u2", "jane", "admin"), abs_user("u3", "root", "root"), abs_user("u4", "floppy-sync", "admin"),
                 abs_user("u5", "abr"), abs_user("u6", "gone", active=False), abs_user("u7", "nofloppy"), abs_user("u8", "boss", "root")]
        got = ap.synced_users(FakeAbs(users), ["zoe", "jane", "root", "floppy-sync", "abr", "gone", "boss"])
        self.assertEqual(got, [("u2", "jane"), ("u1", "zoe")])

    def test_rejects_unsafe_ids(self):
        with self.assertRaises(ap.AbsError):
            ap.synced_users(FakeAbs([abs_user("../x", "zoe")]), ["zoe"])


class PlanTest(unittest.TestCase):
    def build(self, entries, state=None, covers=None):
        abs_ = FakeAbs([abs_user("u1", "alice")], {"u1": entries}, covers=covers)
        return abs_, ap.build_payload("alice", "u1", abs_, state or {}, NOW)

    def test_first_run_pushes_everything(self):
        _, (payload, nxt) = self.build([entry("b1", 5), entry("p1", 7, "e1")])
        self.assertEqual(payload["user"], "alice")
        self.assertEqual(set(payload["items"]), {"b1", "p1?expanded=1"})
        self.assertEqual(set(payload["covers"]), {"b1"})
        self.assertEqual(nxt["progress"], {"b1": 5, "p1/e1": 7})
        self.assertEqual(nxt["covers"], {"b1": NOW})

    def test_payload_carries_no_account_details(self):
        _, (payload, _) = self.build([{**entry("b1", 5), "displayTitle": "T", "coverPath": "/c", "mediaUpdatedAt": 1, "displaySubtitle": "S"}])
        self.assertEqual(set(payload["me"]), {"type", "mediaProgress"})
        self.assertEqual(payload["me"]["type"], "user")
        self.assertEqual(payload["me"]["mediaProgress"], [entry("b1", 5)])
        self.assertNotIn("secret-token", json.dumps(payload))

    def test_nothing_changed_pushes_nothing(self):
        state = {"progress": {"b1": 5}, "covers": {"b1": NOW - 3600}}
        abs_, result = self.build([entry("b1", 5)], state)
        self.assertEqual(result, (None, state))
        self.assertEqual(abs_.calls, ["/api/users/u1"])

    def test_changed_progress_pushes_without_fresh_covers(self):
        _, (payload, nxt) = self.build([entry("b1", 6)], {"progress": {"b1": 5}, "covers": {"b1": NOW - 3600}})
        self.assertEqual(payload["covers"], {})
        self.assertEqual(nxt["progress"], {"b1": 6})
        self.assertEqual(nxt["covers"], {"b1": NOW - 3600})

    def test_stale_cover_alone_triggers_a_push(self):
        _, (payload, nxt) = self.build([entry("b1", 5)], {"progress": {"b1": 5}, "covers": {"b1": NOW - ap.COVER_REFRESH_SECONDS - 1}})
        self.assertEqual(set(payload["covers"]), {"b1"})
        self.assertEqual(nxt["covers"]["b1"], NOW)

    def test_bad_cover_is_skipped_and_retried(self):
        for cover in [("text/html", b"<html>"), ("image/jpeg", b"x" * (ap.MAX_COVER_BYTES + 1)), None]:
            with self.subTest(cover=cover and cover[0]):
                _, (payload, nxt) = self.build([entry("b1", 5)], covers={"b1": cover})
                self.assertEqual(payload["covers"], {})
                self.assertNotIn("b1", nxt["covers"])

    def test_cover_is_base64(self):
        _, (payload, _) = self.build([entry("b1", 5)])
        self.assertEqual(payload["covers"]["b1"]["type"], "image/jpeg")
        self.assertEqual(ap.base64.b64decode(payload["covers"]["b1"]["data"]), JPEG)

    def test_item_without_cover_fetches_none(self):
        abs_ = FakeAbs([abs_user("u1", "alice")], {"u1": [entry("b1", 5)]})
        abs_.get = lambda path, _g=abs_.get: {"id": "b1", "media": {}} if path == "/api/items/b1" else _g(path)
        payload, _ = ap.build_payload("alice", "u1", abs_, {}, NOW)
        self.assertEqual(payload["covers"], {})

    def test_deleted_item_is_left_out(self):
        abs_ = FakeAbs([abs_user("u1", "alice")], {"u1": [entry("b1", 5), entry("b2", 5)]})
        real = abs_.get

        def get(path):
            if path == "/api/items/b2":
                raise ap.AbsNotFound("404")
            return real(path)

        abs_.get = get
        payload, nxt = ap.build_payload("alice", "u1", abs_, {}, NOW)
        self.assertEqual(set(payload["items"]), {"b1"})
        self.assertEqual(nxt["progress"], {"b1": 5, "b2": 5})

    def test_rejects_unsafe_item_ids(self):
        with self.assertRaises(ap.AbsError):
            self.build([entry("../x", 5)])


class SpoolTest(unittest.TestCase):
    def test_pending_becomes_state_only_after_apply(self):
        with tempfile.TemporaryDirectory() as d:
            ap.write_json(pathlib.Path(d, "pending", "alice.json"), {"payload": {}, "next_state": {"progress": {"b1": 5}}})
            self.assertEqual(ap.load_state(d, "alice"), {})
            ap.mark_applied(d, "alice")
            self.assertEqual(ap.load_state(d, "alice"), {"progress": {"b1": 5}})
            self.assertFalse(pathlib.Path(d, "applied", "alice.json").exists())
            self.assertEqual(ap.load_state(d, "alice"), {"progress": {"b1": 5}})

    def test_files_are_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "pending", "alice.json")
            ap.write_json(p, {})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)


class SymlinkTest(unittest.TestCase):
    def test_write_json_does_not_follow_a_planted_tmp_link(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            victim = pathlib.Path(outside, "victim")
            victim.write_text("keep")
            victim.chmod(0o600)
            pathlib.Path(d, "pending").mkdir()
            for name in ("alice.tmp", "alice.json.tmp"):
                pathlib.Path(d, "pending", name).symlink_to(victim)
            p = pathlib.Path(d, "pending", "alice.json")
            ap.write_json(p, {"a": 1}, mode=0o644)
            self.assertEqual((victim.read_text(), victim.stat().st_mode & 0o777), ("keep", 0o600))
            self.assertFalse(p.is_symlink())
            self.assertEqual((json.loads(p.read_text()), p.stat().st_mode & 0o777), ({"a": 1}, 0o644))

    def test_write_json_refuses_a_linked_directory(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            pathlib.Path(d, "pending").symlink_to(outside)
            with self.assertRaises(OSError):
                ap.write_json(pathlib.Path(d, "pending", "alice.json"), {})
            self.assertEqual(list(pathlib.Path(outside).iterdir()), [])

    def test_mark_applied_refuses_a_linked_directory(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            ap.write_json(pathlib.Path(d, "pending", "alice.json"), {"payload": {}, "next_state": {}})
            pathlib.Path(d, "applied").symlink_to(outside)
            with self.assertRaises(OSError):
                ap.mark_applied(d, "alice")
            self.assertEqual(list(pathlib.Path(outside).iterdir()), [])

    def test_apply_skips_a_linked_pending_file(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            secret = pathlib.Path(outside, "secret.json")
            secret.write_text(json.dumps({"payload": {"user": "root-only"}}))
            pathlib.Path(d, "pending").mkdir()
            pathlib.Path(d, "pending", "alice.json").symlink_to(secret)
            seen = []
            ok = ap.apply_all(d, {"alice"}, lambda stdin: (seen.append(stdin), (0, "RESULT ok\n"))[1])
            self.assertEqual((ok, seen), (False, []))


class FetchTest(unittest.TestCase):
    def run_fetch(self, fake, targets, prepare=None):
        with tempfile.TemporaryDirectory() as creds, tempfile.TemporaryDirectory() as d:
            pathlib.Path(creds, "abs-key").write_text("k1\n")
            ap.write_json(pathlib.Path(d, "targets.json"), targets)
            if prepare:
                prepare(d)
            keys = []
            orig, ap.Abs = ap.Abs, lambda key: keys.append(key) or fake
            try:
                ap.fetch(creds, d, NOW)
            finally:
                ap.Abs = orig
            pending = sorted(p.stem for p in pathlib.Path(d, "pending").glob("*.json"))
            return keys, pending, json.loads(pathlib.Path(d, "fetch-failed.json").read_text())

    def test_syncs_matching_users_with_the_service_key(self):
        fake = FakeAbs([abs_user("u1", "alice"), abs_user("u2", "bob"), abs_user("u3", "carol")], {"u1": [entry("b1", 5)], "u2": [entry("b2", 5)]})

        def prepare(d):
            ap.write_json(pathlib.Path(d, "state", "bob.json"), {"progress": {"b2": 5}, "covers": {"b2": NOW}})

        keys, pending, failed = self.run_fetch(fake, ["alice", "bob"], prepare)
        self.assertEqual((keys, pending, failed), (["k1"], ["alice"], []))
        self.assertNotIn("/api/users/u3", fake.calls)

    def test_wrong_key_syncs_nobody(self):
        fake = FakeAbs([abs_user("u1", "alice")], {"u1": [entry("b1", 5)]}, me={**SERVICE, "username": "zoe"})
        _, pending, failed = self.run_fetch(fake, ["alice"])
        self.assertEqual((pending, failed), ([], ["floppy-sync"]))
        self.assertEqual(fake.calls, ["/api/me"])

    def test_failed_user_is_recorded(self):
        fake = FakeAbs([abs_user("u1", "alice"), abs_user("u2", "bob")], {"u1": [entry("../x", 5)], "u2": [entry("b2", 5)]})
        _, pending, failed = self.run_fetch(fake, ["alice", "bob"])
        self.assertEqual((pending, failed), (["bob"], ["alice"]))


class ApplyTest(unittest.TestCase):
    def run_apply(self, out, code=0, user="alice", allowed=("alice",)):
        with tempfile.TemporaryDirectory() as d:
            ap.write_json(pathlib.Path(d, "pending", "alice.json"), {"payload": {"user": user}, "next_state": {"progress": {}}})
            seen = []

            def runner(stdin):
                seen.append(json.loads(stdin))
                return code, out

            ok = ap.apply_all(d, set(allowed), runner)
            return ok, seen, pathlib.Path(d, "pending", "alice.json").exists(), pathlib.Path(d, "applied", "alice.json").exists()

    def test_success_marks_applied(self):
        ok, seen, pending, applied = self.run_apply("noise\nRESULT ok alice books=1\n")
        self.assertEqual((ok, seen, pending, applied), (True, [{"user": "alice"}], False, True))

    def test_payload_for_another_user_is_refused(self):
        ok, seen, pending, applied = self.run_apply("RESULT ok zoe\n", user="zoe", allowed=("alice", "zoe"))
        self.assertEqual((ok, seen, pending, applied), (False, [], True, False))

    def test_member_not_in_targets_is_refused(self):
        ok, seen, pending, applied = self.run_apply("RESULT ok alice\n", allowed=("bob",))
        self.assertEqual((ok, seen, pending, applied), (False, [], True, False))

    def test_main_apply_checks_against_fresh_targets(self):
        calls = []
        orig = ap.floppy_users, ap.apply_all
        ap.floppy_users = lambda: ["alice", "root", "floppy-sync"]
        ap.apply_all = lambda d, allowed: calls.append(allowed) or True
        try:
            with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"STATE_DIRECTORY": d}):
                self.assertEqual(ap.main(["abs-push.py", "apply"]), 0)
        finally:
            ap.floppy_users, ap.apply_all = orig
        self.assertEqual(calls, [{"alice"}])

    def test_main_apply_fails_without_floppy_user_list(self):
        def broken():
            raise ap.AbsError("Floppy user list failed (exit 1)")
        orig = ap.floppy_users, ap.apply_all
        ap.floppy_users, ap.apply_all = broken, lambda d, allowed: self.fail("applied without targets")
        try:
            with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"STATE_DIRECTORY": d}):
                self.assertEqual(ap.main(["abs-push.py", "apply"]), 1)
        finally:
            ap.floppy_users, ap.apply_all = orig

    def test_failure_keeps_pending(self):
        for out, code in [("RESULT refused x\n", 0), ("Traceback\n", 1), ("", 0)]:
            with self.subTest(out=out):
                ok, _, pending, applied = self.run_apply(out, code)
                self.assertEqual((ok, pending, applied), (False, True, False))


class StoreKeyTest(unittest.TestCase):
    def store(self, me):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d, "key")
            orig, ap.Abs = ap.Abs, lambda key: FakeAbs(me=me)
            try:
                msg = ap.store_key("k1\n", path, chown=lambda p: None)
            finally:
                ap.Abs = orig
            return msg, path.exists() and (path.read_text(), path.stat().st_mode & 0o777)

    def test_stores_the_service_key_private(self):
        msg, stored = self.store(SERVICE)
        self.assertEqual(stored, ("k1\n", 0o600))
        self.assertIn("floppy-sync", msg)
        self.assertNotIn("k1", msg)

    def test_refuses_another_users_key(self):
        for me in [{**SERVICE, "username": "zoe"}, {**SERVICE, "type": "user"}]:
            with self.subTest(me=me), self.assertRaises(ap.AbsError):
                self.store(me)

    def test_refuses_empty_key(self):
        with self.assertRaises(ap.AbsError):
            ap.store_key("  \n", pathlib.Path("/nonexistent/key"))


class SelftestTest(unittest.TestCase):
    def test_selftest_is_a_dry_run_without_a_member(self):
        p = ap.selftest_payload()
        self.assertTrue(p["dry_run"])
        self.assertNotIn("user", p)
        self.assertEqual(len(p["me"]["mediaProgress"]), 1)
        self.assertEqual(set(p["items"]), {p["me"]["mediaProgress"][0]["libraryItemId"]})

    def test_selftest_needs_ok_from_floppy(self):
        self.assertTrue(ap.selftest(lambda stdin: (0, "RESULT ok dry-run\n")))
        self.assertFalse(ap.selftest(lambda stdin: (0, "RESULT ok x\n")))
        self.assertFalse(ap.selftest(lambda stdin: (1, "Traceback\n")))


if __name__ == "__main__":
    unittest.main()
