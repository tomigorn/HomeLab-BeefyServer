import importlib.util
import json
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("abs_push", pathlib.Path(__file__).with_name("abs-push.py"))
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)

NOW = 1_800_000_000.0
JPEG = b"\xff\xd8\xff" + b"x" * 10


def entry(item, last, episode=None):
    e = {"libraryItemId": item, "lastUpdate": last, "currentTime": 60, "isFinished": False}
    if episode:
        e["episodeId"] = episode
    return e


class FakeAbs:
    def __init__(self, me, covers=None):
        self.me = me
        self.covers = covers or {}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path == "/api/me":
            return self.me
        if path.endswith("?expanded=1"):
            return {"id": path.split("/")[3].split("?")[0], "expanded": True}
        return {"id": path.split("/")[3], "media": {"coverPath": "/metadata/c.jpg"}}

    def cover(self, item_id):
        self.calls.append(f"cover {item_id}")
        return self.covers.get(item_id, ("image/jpeg", JPEG))


def me(*entries, type_="user"):
    return {"username": "member", "type": type_, "email": "x@example.com", "mediaProgress": list(entries)}


class KeysTest(unittest.TestCase):
    def test_reads_one_key_per_member(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "key_alice").write_text("k1\n")
            pathlib.Path(d, "key_bob.b").write_text("k2")
            pathlib.Path(d, "other").write_text("x")
            self.assertEqual(ap.member_keys(d), {"alice": "k1", "bob.b": "k2"})

    def test_rejects_unsafe_names(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "key_a b").write_text("k")
            self.assertEqual(ap.member_keys(d), {})


class PlanTest(unittest.TestCase):
    def test_first_run_pushes_everything(self):
        abs_ = FakeAbs(me(entry("b1", 5), entry("p1", 7, "e1")))
        payload, nxt = ap.build_payload("alice", abs_, {}, NOW)
        self.assertEqual(payload["user"], "alice")
        self.assertEqual(set(payload["items"]), {"b1", "p1?expanded=1"})
        self.assertEqual(set(payload["covers"]), {"b1"})
        self.assertEqual(nxt["progress"], {"b1": 5, "p1/e1": 7})
        self.assertEqual(nxt["covers"], {"b1": NOW})

    def test_payload_carries_no_account_details(self):
        payload, _ = ap.build_payload("alice", FakeAbs(me(entry("b1", 5))), {}, NOW)
        self.assertEqual(set(payload["me"]), {"type", "mediaProgress"})

    def test_nothing_changed_pushes_nothing(self):
        abs_ = FakeAbs(me(entry("b1", 5)))
        state = {"progress": {"b1": 5}, "covers": {"b1": NOW - 3600}}
        self.assertEqual(ap.build_payload("alice", abs_, state, NOW), (None, state))
        self.assertEqual(abs_.calls, ["/api/me"])

    def test_changed_progress_pushes_without_fresh_covers(self):
        abs_ = FakeAbs(me(entry("b1", 6)))
        state = {"progress": {"b1": 5}, "covers": {"b1": NOW - 3600}}
        payload, nxt = ap.build_payload("alice", abs_, state, NOW)
        self.assertEqual(payload["covers"], {})
        self.assertEqual(nxt["progress"], {"b1": 6})
        self.assertEqual(nxt["covers"], {"b1": NOW - 3600})

    def test_stale_cover_alone_triggers_a_push(self):
        abs_ = FakeAbs(me(entry("b1", 5)))
        state = {"progress": {"b1": 5}, "covers": {"b1": NOW - ap.COVER_REFRESH_SECONDS - 1}}
        payload, nxt = ap.build_payload("alice", abs_, state, NOW)
        self.assertEqual(set(payload["covers"]), {"b1"})
        self.assertEqual(nxt["covers"]["b1"], NOW)

    def test_refuses_admin_keys(self):
        for t in ("admin", "root"):
            with self.subTest(t=t), self.assertRaises(ap.AbsError):
                ap.build_payload("alice", FakeAbs(me(entry("b1", 5), type_=t)), {}, NOW)

    def test_bad_cover_is_skipped_and_retried(self):
        for cover in [("text/html", b"<html>"), ("image/jpeg", b"x" * (ap.MAX_COVER_BYTES + 1)), None]:
            with self.subTest(cover=cover and cover[0]):
                abs_ = FakeAbs(me(entry("b1", 5)), covers={"b1": cover})
                payload, nxt = ap.build_payload("alice", abs_, {}, NOW)
                self.assertEqual(payload["covers"], {})
                self.assertNotIn("b1", nxt["covers"])

    def test_cover_is_base64(self):
        payload, _ = ap.build_payload("alice", FakeAbs(me(entry("b1", 5))), {}, NOW)
        self.assertEqual(payload["covers"]["b1"]["type"], "image/jpeg")
        self.assertEqual(ap.base64.b64decode(payload["covers"]["b1"]["data"]), JPEG)

    def test_item_without_cover_fetches_none(self):
        abs_ = FakeAbs(me(entry("b1", 5)))
        abs_.get = lambda path, _g=abs_.get: {"id": "b1", "media": {}} if path == "/api/items/b1" else _g(path)
        payload, _ = ap.build_payload("alice", abs_, {}, NOW)
        self.assertEqual(payload["covers"], {})

    def test_deleted_item_is_left_out(self):
        abs_ = FakeAbs(me(entry("b1", 5), entry("b2", 5)))
        real = abs_.get

        def get(path):
            if path == "/api/items/b2":
                raise ap.AbsNotFound("404")
            return real(path)

        abs_.get = get
        payload, nxt = ap.build_payload("alice", abs_, {}, NOW)
        self.assertEqual(set(payload["items"]), {"b1"})
        self.assertEqual(nxt["progress"], {"b1": 5, "b2": 5})

    def test_rejects_unsafe_item_ids(self):
        with self.assertRaises(ap.AbsError):
            ap.build_payload("alice", FakeAbs(me(entry("../x", 5))), {}, NOW)


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


class FetchTest(unittest.TestCase):
    def test_spools_only_members_with_changes(self):
        with tempfile.TemporaryDirectory() as creds, tempfile.TemporaryDirectory() as d:
            pathlib.Path(creds, "key_alice").write_text("k1")
            pathlib.Path(creds, "key_bob").write_text("k2")
            fakes = {"k1": FakeAbs(me(entry("b1", 5))), "k2": FakeAbs(me(entry("b2", 5)))}
            ap.write_json(pathlib.Path(d, "state", "bob.json"), {"progress": {"b2": 5}, "covers": {"b2": NOW}})
            orig, ap.Abs = ap.Abs, lambda key: fakes[key]
            try:
                ap.fetch(creds, d, NOW)
            finally:
                ap.Abs = orig
            self.assertTrue(pathlib.Path(d, "pending", "alice.json").exists())
            self.assertFalse(pathlib.Path(d, "pending", "bob.json").exists())
            self.assertTrue(pathlib.Path(d, "applied").is_dir())
            self.assertEqual(json.loads(pathlib.Path(d, "fetch-failed.json").read_text()), [])

    def test_failed_member_is_recorded(self):
        with tempfile.TemporaryDirectory() as creds, tempfile.TemporaryDirectory() as d:
            pathlib.Path(creds, "key_alice").write_text("k1")
            orig, ap.Abs = ap.Abs, lambda key: FakeAbs(me(entry("b1", 5), type_="admin"))
            try:
                ap.fetch(creds, d, NOW)
            finally:
                ap.Abs = orig
            self.assertEqual(json.loads(pathlib.Path(d, "fetch-failed.json").read_text()), ["alice"])


class ApplyTest(unittest.TestCase):
    def run_apply(self, out, code=0):
        with tempfile.TemporaryDirectory() as d:
            ap.write_json(pathlib.Path(d, "pending", "alice.json"), {"payload": {"user": "alice"}, "next_state": {"progress": {}}})
            seen = []

            def runner(stdin):
                seen.append(json.loads(stdin))
                return code, out

            ok = ap.apply_all(d, runner)
            return ok, seen, pathlib.Path(d, "pending", "alice.json").exists(), pathlib.Path(d, "applied", "alice.json").exists()

    def test_success_marks_applied(self):
        ok, seen, pending, applied = self.run_apply("noise\nRESULT ok alice books=1\n")
        self.assertEqual((ok, seen, pending, applied), (True, [{"user": "alice"}], False, True))

    def test_failure_keeps_pending(self):
        for out, code in [("RESULT refused x\n", 0), ("Traceback\n", 1), ("", 0)]:
            with self.subTest(out=out):
                ok, _, pending, applied = self.run_apply(out, code)
                self.assertEqual((ok, pending, applied), (False, True, False))


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
