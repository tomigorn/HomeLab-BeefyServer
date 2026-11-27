#!/usr/bin/env python3
"""download-now/my_library.py: the My library list (README "My library") — every downloaded title with its state (temporary, keep forever, leaving soon, in the recycle bin), marks, what an early delete would need, the asked-of-you requests and both weekly counts, from the same rules as the title page.
Usage: python3 tests/test_my_library.py"""
import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "download-now"))
spec = importlib.util.spec_from_file_location("download_now", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
sys.modules["download_now"] = dn
spec.loader.exec_module(dn)
import my_library as ml  # noqa: E402

NOW = datetime.datetime(2026, 12, 20, 12, 0, tzinfo=datetime.timezone.utc)
GROUPS = "streaming-users|streaming-requesters"
USERS = {"admin": {"id": 1, "permissions": 2, "displayName": "Owner"}, "jane": {"id": 7, "permissions": 32, "displayName": "Jane Doe"},
         "joe": {"id": 5, "permissions": 32, "displayName": "Joe Bloggs"}, "viewer": {"id": 9, "permissions": 0, "displayName": "Viewer"}}
DOC = {"deleteEnabled": True, "enabledOn": "2026-11-16", "titles": {
    "movie/10": {"t": "New Film", "y": 2025, "kind": "movie", "due": "2027-02-01", "default": False, "reasons": [], "protected": False, "size": 2**33, "tier": "ssd"},
    "movie/11": {"t": "Old Film", "y": 1980, "kind": "movie", "due": "2027-02-01", "default": True, "reasons": ["old"], "protected": False, "size": 2**32, "tier": "hdd"},
    "movie/12": {"t": "Soon Film", "y": 2024, "kind": "movie", "due": "2026-12-24", "default": False, "reasons": [], "protected": False, "size": 2**31, "tier": "hdd"},
    "movie/653": {"t": "Nosferatu", "y": 1922, "kind": "movie", "due": "2027-02-01", "default": True, "reasons": ["old"], "protected": True},
    "tv/20": {"t": "New Show", "y": 2025, "kind": "tv", "due": "2027-03-01", "default": False, "reasons": [], "protected": False, "tvdb": 4020}}}


def item(i, **kw):
    base = {"id": i, "kind": "movie", "tmdb": 40, "season": None, "title": "Gone Film", "year": 2023, "by": 7, "byName": "Jane Doe", "at": "2026-12-19T10:00:00+00:00", "state": "deleted",
            "requesters": [5], "asks": [], "deletedAt": "2026-12-19T10:00:00+00:00", "binUntil": "2026-12-26T10:00:00+00:00"}
    base.update(kw)
    return base


class World:
    def __init__(self, d):
        self.d = pathlib.Path(d)
        (self.d / "retention.json").write_text(json.dumps(DOC))
        (self.d / "store").mkdir()
        (self.d / "jobs").mkdir()
        self.state = self.d / "delete-early.json"
        self.keep = self.d / "store" / "keep.json"
        self.requests = {7: [{"media": {"mediaType": "movie", "tmdbId": 12}}], 5: [{"media": {"mediaType": "movie", "tmdbId": 11}}]}
        self.calls = []

        def seerr(path, auth):
            self.calls.append(path)
            me = USERS.get(auth.get("X-Api-User"))
            if path == "/auth/me":
                return me
            if path.startswith("/request?"):
                return {"pageInfo": {"pages": 1}, "results": self.requests.get(me["id"], [])}
            raise dn.ApiError("404")

        self.deps = dn.Deps(seerr=seerr, arr=None, qbit=None, sab=None, origins={"https://requests.example.com"})
        self.deps.retention_file = str(self.d / "retention.json")
        self.deps.retention_keep = str(self.keep)
        self.deps.delete_jobs = str(self.d / "jobs")
        self.deps.delete_state = str(self.state)

    def get(self, who, groups=GROUPS, path="list"):
        h = {"X-Api-Key": "k", "X-Api-User": who} if who else {}
        if who and groups:
            h["X-authentik-groups"] = groups
        return ml.handle(dn, "GET", "/_download-now/library/" + path, h, b"", self.deps, now=NOW)

    def runner_state(self, items):
        self.state.write_text(json.dumps({"items": {i["id"]: i for i in items}}))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = World(self.tmp.name)
        ml.MINE.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def by_key(self, r):
        return {t["key"]: t for t in r["titles"]}


class List(Base):
    def test_needs_a_session_and_only_get_list(self):
        self.assertEqual(self.w.get(None)[0], 403)
        self.assertEqual(self.w.get("jane", path="other")[0], 404)

    def test_every_downloaded_title_with_its_state(self):
        code, r = self.w.get("jane")
        self.assertEqual(code, 200)
        t = self.by_key(r)
        self.assertEqual(sorted(t), ["movie/10", "movie/11", "movie/12", "movie/653", "tv/20"])
        self.assertEqual((t["movie/10"]["state"], t["movie/10"]["deleteOn"], t["movie/10"]["size"]), ("temporary", "2027-02-01", 2**33))
        self.assertEqual((t["movie/11"]["state"], t["movie/11"]["source"], t["movie/11"]["reasons"]), ("keep", "default", ["old"]))
        self.assertEqual((t["movie/12"]["state"], t["movie/12"]["deleteOn"]), ("soon", "2026-12-24"))
        self.assertEqual(t["movie/653"]["source"], "protected")
        self.assertEqual((r["deleteEnabled"], r["group"]), (True, True))

    def test_rights_and_refusals_from_the_shared_rules(self):
        t = self.by_key(self.w.get("jane")[1])
        self.assertEqual((t["movie/10"]["canMark"], t["movie/10"]["del"]), (True, {"allowed": True, "reason": None, "plan": []}))
        self.assertEqual((t["movie/11"]["canMark"], t["movie/11"]["del"]["reason"]), (False, "kept"))
        self.assertEqual(t["movie/653"]["del"]["reason"], "protected")

    def test_without_the_group_nothing_is_allowed(self):
        r = self.w.get("jane", groups=None)[1]
        t = self.by_key(r)
        self.assertEqual((r["group"], t["movie/10"]["canMark"], t["movie/10"]["del"]["reason"]), (False, False, "group"))

    def test_fan_favourite_marks_and_plan(self):
        self.w.keep.write_text(json.dumps({"titles": {"movie/10": {"keep": True, "marks": {"7": {"at": "2026-12-19T10:00:00+00:00", "name": "Jane Doe"}}}},
                                           "marked": [{"by": 7, "key": "movie/10", "at": "2026-12-19T10:00:00+00:00"}]}))
        jane, joe = self.by_key(self.w.get("jane")[1]), self.w.get("joe")[1]
        self.assertEqual((jane["movie/10"]["state"], jane["movie/10"]["source"], jane["movie/10"]["canUnmark"], jane["movie/10"]["mine"]), ("keep", "fan", True, True))
        self.assertEqual(self.by_key(joe)["movie/10"]["del"]["plan"], [{"reason": "fan", "names": ["Jane Doe"]}])
        self.assertEqual(self.w.get("jane")[1]["quota"], {"delete": {"used": 0, "max": 10}, "marks": {"used": 1, "max": 5}})

    def test_mine_is_requested_or_marked_by_me(self):
        t = self.by_key(self.w.get("jane")[1])
        self.assertEqual([k for k, v in t.items() if v["mine"]], ["movie/12"])
        self.assertEqual([k for k, v in self.by_key(self.w.get("joe")[1]).items() if v["mine"]], ["movie/11"])

    def test_requests_unreadable_means_mine_unknown(self):
        def broken(path, auth):
            if path == "/auth/me":
                return USERS["jane"]
            raise dn.ApiError("500")
        self.w.deps.seerr = broken
        r = self.w.get("jane")[1]
        self.assertFalse(r["mineKnown"])
        self.assertTrue(r["titles"])


class Bin(Base):
    def test_whole_title_in_the_recycle_bin(self):
        self.w.runner_state([item("a")])
        r = self.w.get("joe")[1]
        t = self.by_key(r)["movie/40"]
        self.assertEqual((t["state"], t["t"], t["y"], t["items"][0]["canRestore"], t["items"][0]["binUntil"]), ("bin", "Gone Film", 2023, True, "2026-12-26T10:00:00+00:00"))
        self.assertEqual(t["del"]["allowed"], False)

    def test_bin_emptied_is_gone(self):
        self.w.runner_state([item("a", binUntil="2026-12-20T11:00:00+00:00")])
        self.assertNotIn("movie/40", self.by_key(self.w.get("joe")[1]))

    def test_deletions_count(self):
        self.w.runner_state([item("a"), item("b", tmdb=41, state="denied")])
        self.assertEqual(self.w.get("jane")[1]["quota"]["delete"], {"used": 1, "max": 10})


class Asked(Base):
    def test_requests_waiting_for_me(self):
        self.w.runner_state([item("p", tmdb=10, title="New Film", state="pending", by=5, byName="Joe Bloggs", deletedAt=None, binUntil=None, expires="2026-12-26T10:00:00+00:00",
                                  asks=[{"reason": "fan", "approvers": [7], "names": ["Jane Doe"], "answer": None}])])
        r = self.w.get("jane")[1]
        (a,) = r["asked"]
        self.assertEqual((a["key"], a["t"], a["item"]["id"], a["item"]["approver"], a["item"]["byName"]), ("movie/10", "New Film", "p", True, "Joe Bloggs"))
        self.assertEqual(self.w.get("joe")[1]["asked"], [])
        self.assertEqual(self.by_key(self.w.get("joe")[1])["movie/10"]["items"][0]["state"], "pending")


class Routing(Base):
    def test_download_now_routes_the_library(self):
        h = {"X-Api-Key": "k", "X-Api-User": "jane", "X-authentik-groups": GROUPS}
        self.assertEqual(dn.handle("GET", "/_download-now/library/list", h, b"", self.w.deps)[0], 200)


if __name__ == "__main__":
    unittest.main()
