#!/usr/bin/env python3
"""download-now/delete_early.py: the "Delete early" backend (README "Delete early") — group check from the forward-auth header, the test film, library and season checks, the weekly quota, Origin/JSON, job files for the beefy runner, answers only by the asked person, restore rights, the title state from the runner's state file, the plan preview (fan-favourite setters, viewers mid-watch), bulk deletes (My library).
Usage: python3 tests/test_delete_early_api.py"""
import datetime
import importlib.util
import json
import os
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
import delete_early as de  # noqa: E402

ORIGIN = "https://requests.example.com"
NOW = datetime.datetime(2026, 12, 20, 12, 0, tzinfo=datetime.timezone.utc)
GROUPS = "streaming-users|streaming-requesters"
USERS = {"admin": {"id": 1, "permissions": 2, "displayName": "Owner"}, "jane": {"id": 7, "permissions": 32, "displayName": "Jane Doe"},
         "joe": {"id": 5, "permissions": 32, "displayName": "Joe Bloggs"}, "viewer": {"id": 9, "permissions": 0, "displayName": "Viewer"}}
DOC = {"deleteEnabled": True, "enabledOn": "2026-11-16", "titles": {
    "movie/10": {"t": "New Film", "y": 2025, "kind": "movie", "due": "2027-02-01", "default": False, "reasons": [], "protected": False},
    "movie/11": {"t": "Old Film", "y": 1980, "kind": "movie", "due": "2027-02-01", "default": True, "reasons": ["old"], "protected": False},
    "movie/653": {"t": "Nosferatu", "y": 1922, "kind": "movie", "due": "2027-02-01", "default": True, "reasons": ["old"], "protected": True},
    "tv/20": {"t": "New Show", "y": 2025, "kind": "tv", "due": "2027-03-01", "default": False, "reasons": [], "protected": False, "tvdb": 4020}}}


def media(kind, tmdb, requesters=(), seasons=(), tvdb=None, title="X"):
    m = {"id": tmdb, "title" if kind == "movie" else "name": title, "mediaInfo": {"requests": [{"requestedBy": {"id": u, "displayName": f"user {u}"}, "status": 2} for u in requesters],
                                                                               "seasons": [{"seasonNumber": n, "status": 5} for n in seasons] + [{"seasonNumber": 9, "status": 1}]}}
    m["releaseDate" if kind == "movie" else "firstAirDate"] = "2025-01-01"
    if tvdb:
        m["externalIds"] = {"tvdbId": tvdb}
    return m


class World:
    def __init__(self, d):
        self.d = pathlib.Path(d)
        (self.d / "retention.json").write_text(json.dumps(DOC))
        (self.d / "store").mkdir()
        self.jobs = self.d / "jobs"
        self.jobs.mkdir()
        self.state = self.d / "delete-early.json"
        self.media = {"movie/10": media("movie", 10, [7], title="New Film"), "movie/11": media("movie", 11, [5], title="Old Film"), "movie/653": media("movie", 653, [1]),
                      "tv/20": media("tv", 20, [7], seasons=(1, 2), tvdb=4020, title="New Show"), "movie/30": media("movie", 30, [7])}

        def seerr(path, auth):
            if path == "/auth/me":
                return USERS.get(auth.get("X-Api-User"))
            k = path.strip("/")
            if k not in self.media:
                raise dn.ApiError("404")
            return self.media[k]

        self.deps = dn.Deps(seerr=seerr, arr=None, qbit=None, sab=None, origins={ORIGIN})
        self.deps.retention_file = str(self.d / "retention.json")
        self.deps.retention_keep = str(self.d / "store" / "keep.json")
        self.deps.delete_jobs = str(self.jobs)
        self.deps.delete_state = str(self.state)
        self.watching = self.d / "delete-early-watching.json"
        self.keep = self.d / "store" / "keep.json"

    def headers(self, who, groups=GROUPS, origin=ORIGIN):
        h = {"X-Api-Key": "k", "X-Api-User": who, "Origin": origin, "Content-Type": "application/json"} if who else {}
        if groups is not None and who:
            h["X-authentik-groups"] = groups
            h["X-authentik-username"] = who
        return h

    def get(self, who, query, groups=GROUPS):
        return de.handle(dn, "GET", "/_download-now/delete-early/state?" + query, self.headers(who, groups), b"", self.deps, now=NOW)

    def post(self, who, op, body, groups=GROUPS, origin=ORIGIN):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return de.handle(dn, "POST", "/_download-now/delete-early/" + op, self.headers(who, groups, origin), raw, self.deps, now=NOW)

    def job_files(self):
        return sorted(json.loads(p.read_text())["op"] + ":" + str(json.loads(p.read_text()).get("tmdb", json.loads(p.read_text()).get("id"))) for p in self.jobs.glob("*.req"))

    def jobs_list(self):
        return [json.loads(p.read_text()) for p in sorted(self.jobs.glob("*.req"))]

    def bulk(self, who, items, groups=GROUPS):
        return self.post(who, "bulk", {"items": [{"type": k.split("/")[0], "tmdbId": int(k.split("/")[1])} for k in items]}, groups)

    def runner_state(self, items):
        self.state.write_text(json.dumps({"items": {i["id"]: i for i in items}}))


def item(i, **kw):
    base = {"id": i, "kind": "movie", "tmdb": 10, "season": None, "title": "New Film", "by": 7, "byName": "Jane Doe", "at": "2026-12-19T10:00:00+00:00", "state": "deleted",
            "requesters": [7], "asks": [], "deletedAt": "2026-12-19T10:00:00+00:00", "binUntil": "2026-12-26T10:00:00+00:00"}
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = World(self.tmp.name)
        de.RECENT.clear()

    def tearDown(self):
        self.tmp.cleanup()


class Group(Base):
    def test_needs_a_session(self):
        self.assertEqual(self.w.get(None, "type=movie&tmdb=10")[0], 403)

    def test_without_the_group_header_nobody_may_delete(self):
        code, r = self.w.get("admin", "type=movie&tmdb=10", groups=None)
        self.assertEqual((code, r["allowed"], r["reason"]), (200, False, "group"))
        self.assertEqual(self.w.post("admin", "delete", {"type": "movie", "tmdbId": 10}, groups=None)[0], 403)
        self.assertEqual(self.w.job_files(), [])

    def test_other_groups_are_refused(self):
        self.assertEqual(self.w.post("jane", "delete", {"type": "movie", "tmdbId": 10}, groups="streaming-users|streaming-requesters-old")[0], 403)
        self.assertEqual(self.w.post("jane", "delete", {"type": "movie", "tmdbId": 10}, groups="streaming-users")[0], 403)

    def test_member_without_a_request_right_is_refused(self):
        self.assertEqual(self.w.post("viewer", "delete", {"type": "movie", "tmdbId": 10})[0], 403)

    def test_member_may_delete(self):
        code, r = self.w.get("jane", "type=movie&tmdb=10")
        self.assertEqual((code, r["allowed"], r["reason"], r["quota"]), (200, True, None, {"used": 0, "max": 10}))


class Delete(Base):
    def test_writes_a_job_for_the_runner(self):
        code, r = self.w.post("joe", "delete", {"type": "movie", "tmdbId": 10})
        self.assertEqual((code, r["state"]), (202, "queued"))
        (job,) = self.w.jobs_list()
        self.assertEqual({k: job[k] for k in ("op", "kind", "tmdb", "season", "user", "userName", "account", "requesters", "title")},
                         {"op": "delete", "kind": "movie", "tmdb": 10, "season": None, "user": 5, "userName": "Joe Bloggs", "account": "joe", "requesters": [7], "title": "New Film"})
        self.assertEqual(job["id"], r["id"])

    def test_automatic_keep_forever_never(self):
        for who in ("jane", "joe", "admin"):
            de.RECENT.clear()
            self.assertEqual(self.w.post(who, "delete", {"type": "movie", "tmdbId": 11}), (403, {"error": "kept"}), who)
        self.assertEqual(self.w.get("joe", "type=movie&tmdb=11")[1]["reason"], "kept")
        self.assertEqual(self.w.job_files(), [])

    def test_test_film_never(self):
        code, r = self.w.post("admin", "delete", {"type": "movie", "tmdbId": 653})
        self.assertEqual((code, r["error"]), (403, "protected"))
        self.assertEqual(self.w.get("admin", "type=movie&tmdb=653")[1]["reason"], "protected")
        self.assertEqual(self.w.job_files(), [])

    def test_not_in_the_library(self):
        self.assertEqual(self.w.post("jane", "delete", {"type": "movie", "tmdbId": 30})[0], 409)
        self.assertEqual(self.w.get("jane", "type=movie&tmdb=30")[1]["reason"], "library")

    def test_series_whole_or_one_available_season(self):
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 2})[0], 202)
        de.RECENT.clear()
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 9})[0], 400)
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 1})[0], 202)
        jobs = self.w.jobs_list()
        self.assertEqual(sorted(j["season"] for j in jobs), [1, 2])
        self.assertEqual({j["tvdb"] for j in jobs}, {4020})
        self.assertEqual(self.w.get("jane", "type=tv&tmdb=20")[1]["seasons"], [1, 2])

    def test_season_only_for_series(self):
        self.assertEqual(self.w.post("jane", "delete", {"type": "movie", "tmdbId": 10, "season": 1})[0], 400)

    def test_twice_is_refused_while_queued_pending_or_in_the_bin(self):
        self.assertEqual(self.w.post("jane", "delete", {"type": "movie", "tmdbId": 10})[0], 202)
        de.RECENT.clear()
        self.assertEqual(self.w.post("joe", "delete", {"type": "movie", "tmdbId": 10})[0], 409)
        for p in self.w.jobs.glob("*.req"):
            p.unlink()
        self.w.runner_state([item("a")])
        self.assertEqual(self.w.post("joe", "delete", {"type": "movie", "tmdbId": 10})[0], 409)

    def test_whole_series_blocks_a_season_and_back(self):
        self.w.runner_state([item("a", kind="tv", tmdb=20, season=None)])
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 1})[0], 409)
        self.w.runner_state([item("a", kind="tv", tmdb=20, season=1)])
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 2})[0], 202)
        de.RECENT.clear()
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": None})[0], 409)

    def test_origin_json_and_size(self):
        self.assertEqual(self.w.post("jane", "delete", {"type": "movie", "tmdbId": 10}, origin="https://evil.example")[0], 403)
        h = self.w.headers("jane")
        h["Content-Type"] = "text/plain"
        self.assertEqual(de.handle(dn, "POST", "/_download-now/delete-early/delete", h, b"{}", self.w.deps, now=NOW)[0], 415)
        self.assertEqual(self.w.post("jane", "delete", b"{" + b" " * 2000 + b"}")[0], 413)
        for bad in ({"type": "film", "tmdbId": 10}, {"type": "movie", "tmdbId": "10"}, {"type": "movie", "tmdbId": True}, [1]):
            self.assertEqual(self.w.post("jane", "delete", bad)[0], 400, bad)

    def test_unknown_routes(self):
        self.assertEqual(de.handle(dn, "GET", "/_download-now/delete-early/delete", self.w.headers("jane"), b"", self.w.deps, now=NOW)[0], 404)


class Quota(Base):
    def test_ten_per_week_counting_runner_state_and_queued_jobs(self):
        done = [item(str(i), tmdb=100 + i, at=(NOW - datetime.timedelta(days=6)).isoformat()) for i in range(8)]
        old = [item("old", tmdb=99, at=(NOW - datetime.timedelta(days=8)).isoformat())]
        denied = [item("no", tmdb=98, state="denied")]
        other = [item("o", tmdb=97, by=5)]
        self.w.runner_state(done + old + denied + other)
        self.assertEqual(self.w.get("jane", "type=movie&tmdb=10")[1]["quota"], {"used": 8, "max": 10})
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 1})[0], 202)
        de.RECENT.clear()
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 2})[0], 202)
        de.RECENT.clear()
        code, r = self.w.post("jane", "delete", {"type": "movie", "tmdbId": 10})
        self.assertEqual((code, r["error"]), (429, "quota"))
        self.assertEqual(self.w.get("jane", "type=movie&tmdb=10")[1]["quota"], {"used": 10, "max": 10})


class Plan(Base):
    def test_temporary_title_goes_to_the_bin_at_once(self):
        self.assertEqual(self.w.get("joe", "type=movie&tmdb=10")[1]["plan"], [])

    def test_fan_favourite_asks_the_setter_not_the_setter_themself(self):
        self.w.keep.write_text(json.dumps({"titles": {"movie/10": {"keep": True, "marks": {"7": {"at": "2026-12-19T10:00:00+00:00", "name": "Jane Doe"}}}}}))
        self.assertEqual(self.w.get("joe", "type=movie&tmdb=10")[1]["plan"], [{"reason": "fan", "names": ["Jane Doe"]}])
        self.assertEqual(self.w.get("jane", "type=movie&tmdb=10")[1]["plan"], [])

    def test_viewers_mid_watch_from_the_runner_snapshot(self):
        self.w.watching.write_text(json.dumps({"updated": "2026-12-20T11:40:00+00:00", "titles": {"movie/10": [{"uid": 9, "name": "Viewer"}, {"uid": 7, "name": "Jane Doe"}]}}))
        self.assertEqual(self.w.get("jane", "type=movie&tmdb=10")[1]["plan"], [{"reason": "watching", "names": ["Viewer"]}])

    def test_unreadable_snapshot_is_no_watcher(self):
        self.w.watching.write_text("{")
        self.assertEqual(self.w.get("jane", "type=movie&tmdb=10")[1]["plan"], [])


class Bulk(Base):
    def test_writes_one_job_per_title(self):
        code, r = self.w.bulk("jane", ["movie/10", "tv/20"])
        self.assertEqual((code, len(r["ids"])), (202, 2))
        self.assertEqual(sorted((j["kind"], j["tmdb"], j["season"]) for j in self.w.jobs_list()), [("movie", 10, None), ("tv", 20, None)])

    def test_up_to_the_remaining_limit_only(self):
        self.w.runner_state([item(str(i), tmdb=100 + i) for i in range(9)])
        self.assertEqual(self.w.bulk("jane", ["movie/10", "tv/20"]), (429, {"error": "quota"}))
        self.assertEqual(self.w.job_files(), [])

    def test_any_refused_title_sends_nothing(self):
        code, r = self.w.bulk("jane", ["movie/10", "movie/653", "movie/11", "movie/30"])
        self.assertEqual((code, r), (409, {"error": "refused", "titles": {"movie/653": "protected", "movie/11": "kept", "movie/30": "library"}}))
        self.assertEqual(self.w.job_files(), [])

    def test_busy_title_is_refused(self):
        self.w.runner_state([item("a")])
        self.assertEqual(self.w.bulk("jane", ["movie/10"]), (409, {"error": "refused", "titles": {"movie/10": "already"}}))

    def test_shape(self):
        for bad in ([], ["movie/10", "movie/10"], [f"movie/{i}" for i in range(1, 12)]):
            de.RECENT.clear()
            self.assertEqual(self.w.bulk("jane", bad)[0], 400, bad)
        self.assertEqual(self.w.bulk("jane", ["movie/10"], groups=None)[0], 403)
        self.assertEqual(self.w.bulk("viewer", ["movie/10"])[0], 403)


class Answers(Base):
    def pending(self):
        self.w.runner_state([item("p", state="pending", by=5, byName="Joe Bloggs", deletedAt=None, binUntil=None, expires="2026-12-26T10:00:00+00:00",
                                  asks=[{"reason": "keep", "approvers": [7], "names": ["Jane Doe"], "answer": None}])])

    def test_state_shows_the_box_only_to_the_asked_person(self):
        self.pending()
        (it,) = self.w.get("jane", "type=movie&tmdb=10")[1]["items"]
        self.assertEqual((it["state"], it["approver"], it["mine"], it["asks"][0]["names"]), ("pending", True, False, ["Jane Doe"]))
        (it,) = self.w.get("joe", "type=movie&tmdb=10")[1]["items"]
        self.assertEqual((it["approver"], it["mine"]), (False, True))

    def test_only_the_asked_person_answers(self):
        self.pending()
        self.assertEqual(self.w.post("joe", "answer", {"id": "p", "approve": True})[0], 403)
        self.assertEqual(self.w.post("admin", "answer", {"id": "p", "approve": True})[0], 403)
        code, r = self.w.post("jane", "answer", {"id": "p", "approve": False})
        self.assertEqual(code, 202)
        (job,) = self.w.jobs_list()
        self.assertEqual({k: job[k] for k in ("op", "id", "user", "approve")}, {"op": "answer", "id": "p", "user": 7, "approve": False})

    def test_expired_request_shows_as_denied_and_takes_no_answer(self):
        self.pending()
        later = NOW + datetime.timedelta(days=6, hours=12)
        (it,) = de.handle(dn, "GET", "/_download-now/delete-early/state?type=movie&tmdb=10", self.w.headers("jane"), b"", self.w.deps, now=later)[1]["items"]
        self.assertEqual((it["state"], it["approver"]), ("expired", False))
        raw = json.dumps({"id": "p", "approve": True}).encode()
        self.assertEqual(de.handle(dn, "POST", "/_download-now/delete-early/answer", self.w.headers("jane"), raw, self.w.deps, now=later)[0], 409)


class Restore(Base):
    def test_deleter_requester_and_admin_may_restore(self):
        self.w.runner_state([item("a", by=5, requesters=[7])])
        self.assertEqual(self.w.post("viewer", "restore", {"id": "a"}, groups="streaming-users")[0], 403)
        for who in ("joe", "jane", "admin"):
            de.RECENT.clear()
            self.assertEqual(self.w.post(who, "restore", {"id": "a"})[0], 202, who)
        self.assertEqual([j["op"] for j in self.w.jobs_list()], ["restore"])

    def test_a_stranger_may_not(self):
        USERS["sam"] = {"id": 12, "permissions": 32, "displayName": "Sam"}
        try:
            self.w.runner_state([item("a", by=5, requesters=[7])])
            self.assertEqual(self.w.post("sam", "restore", {"id": "a"})[0], 403)
        finally:
            del USERS["sam"]

    def test_not_after_the_bin_emptied(self):
        self.w.runner_state([item("a", binUntil="2026-12-20T11:00:00+00:00")])
        self.assertEqual(self.w.post("jane", "restore", {"id": "a"})[0], 409)
        self.assertEqual(self.w.get("jane", "type=movie&tmdb=10")[1]["items"], [])

    def test_state_lists_the_bin_with_its_date(self):
        self.w.runner_state([item("a", by=5, byName="Joe Bloggs")])
        r = self.w.get("jane", "type=movie&tmdb=10")[1]
        (it,) = r["items"]
        self.assertEqual((it["state"], it["byName"], it["binUntil"], it["canRestore"], it["season"]), ("deleted", "Joe Bloggs", "2026-12-26T10:00:00+00:00", True, None))
        self.assertTrue(r["allowed"])


class Limits(Base):
    def test_posts_are_spaced(self):
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 1})[0], 202)
        self.assertEqual(self.w.post("jane", "delete", {"type": "tv", "tmdbId": 20, "season": 2})[0], 429)

    def test_unreadable_runner_state_refuses_deleting(self):
        self.w.state.write_text("{broken")
        self.assertEqual(self.w.post("jane", "delete", {"type": "movie", "tmdbId": 10})[0], 503)


if __name__ == "__main__":
    unittest.main()
