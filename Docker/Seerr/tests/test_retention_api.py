#!/usr/bin/env python3
"""download-now/retention_api.py: the "Keep forever" backend (README "Storage retention") — title state, batch for cards, the would-delete list (managers), keep toggles with the permission rule, Origin/JSON, limits and the keep store, against a fake Seerr and fake Radarr/Sonarr.
Usage: python3 tests/test_retention_api.py"""
import datetime
import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "download-now"))
spec = importlib.util.spec_from_file_location("download_now", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
sys.modules["download_now"] = dn
spec.loader.exec_module(dn)
import retention_api as ra  # noqa: E402

ORIGIN = "https://requests.example.com"
TODAY = datetime.date(2026, 12, 20)
ADMIN = {"id": 1, "permissions": 2}
JANE = {"id": 7, "permissions": 32}
JOE = {"id": 5, "permissions": 32}
VIEWER = {"id": 9, "permissions": 0}
USERS = {"admin": ADMIN, "jane": JANE, "joe": JOE, "viewer": VIEWER}

DOC = {"updated": "2026-12-20T10:00:00+01:00", "deleteEnabled": False, "enabledOn": None, "rules": {"deleteDays": 90, "soonDays": 7},
       "titles": {
           "movie/10": {"t": "New Film", "y": 2025, "kind": "movie", "imported": "2026-10-01", "ssdUntil": "2026-10-31", "hddFrom": "2026-10-31", "due": "2026-12-30", "default": False, "reasons": [], "protected": False, "tier": "hdd", "size": 2**34, "postponed": None},
           "movie/11": {"t": "Old Film", "y": 1980, "kind": "movie", "imported": "2026-10-01", "ssdUntil": "2026-10-31", "hddFrom": "2026-10-31", "due": "2026-12-30", "default": True, "reasons": ["old"], "protected": False, "tier": "hdd", "size": 2**33, "postponed": None},
           "movie/653": {"t": "Nosferatu", "y": 1922, "kind": "movie", "imported": "2026-10-01", "ssdUntil": "2026-10-31", "hddFrom": "2026-10-31", "due": "2026-12-30", "default": True, "reasons": ["old"], "protected": True, "tier": "ssd", "size": 2**30, "postponed": None},
           "tv/20": {"t": "New Show", "y": 2025, "kind": "tv", "imported": "2026-12-01", "ssdUntil": "2026-12-31", "hddFrom": "2026-12-31", "due": "2027-03-01", "default": False, "reasons": [], "protected": False, "tier": "ssd", "size": 2**35, "postponed": "in-progress", "tvdb": 4020},
           "tv/21": {"t": "Other Ids", "y": 2025, "kind": "tv", "imported": "2026-12-01", "ssdUntil": "2026-12-31", "hddFrom": "2026-12-31", "due": "2027-03-01", "default": False, "reasons": [], "protected": False, "tier": "ssd", "size": 2**33, "postponed": None, "tvdb": 4021},
       },
       "promote": [{"key": "tv/20", "title": "New Show", "seasons": [2], "played": "2026-12-20T09:00:00Z"}]}


def media(kind, tmdb, requesters=(), year="2025-05-01", tvdb=None):
    m = {"id": tmdb, "mediaInfo": {"requests": [{"requestedBy": {"id": u}, "status": 2} for u in requesters]}}
    if kind == "movie":
        m["releaseDate"] = year
    else:
        m["firstAirDate"] = year
        m["externalIds"] = {"tvdbId": tvdb}
    return m


class World:
    def __init__(self, d):
        self.d = pathlib.Path(d)
        self.data = self.d / "retention.json"
        self.data.write_text(json.dumps(DOC))
        self.keep = self.d / "store" / "keep.json"
        self.keep.parent.mkdir()
        self.media = {"movie/10": media("movie", 10, [7]), "movie/11": media("movie", 11, [5], "1980-01-01"), "tv/20": media("tv", 20, [7], tvdb=4020), "tv/22": media("tv", 22, [7], tvdb=4021),
                      "movie/30": media("movie", 30, [], "2000-03-03"), "movie/653": media("movie", 653, [1], "1922-03-04"), "tv/40": media("tv", 40, [], "2024-01-01", tvdb=4040)}
        self.arr_calls = []

        def seerr(path, auth):
            who = auth.get("X-Api-User")
            if path == "/auth/me":
                return USERS.get(who)
            kind, tmdb = path.strip("/").split("/")
            if f"{kind}/{tmdb}" not in self.media:
                raise dn.ApiError("404")
            return self.media[f"{kind}/{tmdb}"]

        def arr(app):
            def call(path, method="GET", body=None, timeout=15):
                self.arr_calls.append((app, path))
                if path == "/movie/lookup/tmdb?tmdbId=30":
                    return {"year": 2000, "ratings": {"imdb": {"value": 7.0, "votes": 1000}, "tmdb": {"votes": 900}}}
                if path == "/series/lookup?term=tvdb%3A4040":
                    return [{"year": 2024, "ratings": {"value": 8.6, "votes": 90000}}]
                raise dn.ApiError("404")
            return call

        self.deps = dn.Deps(seerr=seerr, arr=arr, qbit=None, sab=None, origins={ORIGIN})
        self.deps.retention_file = str(self.data)
        self.deps.retention_keep = str(self.keep)

    def get(self, who, path):
        return ra.handle(dn, "GET", "/_download-now/retention/" + path, {"X-Api-Key": "k", "X-Api-User": who} if who else {}, b"", self.deps, today=TODAY)

    def post(self, who, body, origin=ORIGIN, ctype="application/json"):
        h = {"X-Api-Key": "k", "X-Api-User": who, "Origin": origin, "Content-Type": ctype}
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return ra.handle(dn, "POST", "/_download-now/retention/keep", h, raw, self.deps, today=TODAY)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = World(self.tmp.name)
        ra.RECENT.clear()
        ra.LOOKUPS.clear()
        ra.LOOKUP_HITS.clear()

    def tearDown(self):
        self.tmp.cleanup()


class Title(Base):
    def test_needs_a_session(self):
        self.assertEqual(self.w.get(None, "title?type=movie&tmdb=10")[0], 403)

    def test_bad_input(self):
        for q in ("title?type=film&tmdb=10", "title?type=movie&tmdb=0", "title?type=movie&tmdb=abc", "nope"):
            self.assertIn(self.w.get("jane", q)[0], (400, 404), q)

    def test_temporary_title_with_dates_and_rights(self):
        code, r = self.w.get("jane", "title?type=movie&tmdb=10")
        self.assertEqual(code, 200)
        self.assertEqual((r["known"], r["state"], r["deleteOn"], r["soon"], r["ssdUntil"], r["tier"], r["canKeep"], r["canUnkeep"], r["deleteEnabled"]),
                         (True, "temporary", "2026-12-30", False, "2026-10-31", "hdd", True, True, False))

    def test_unkeep_needs_requester_or_admin(self):
        self.assertFalse(self.w.get("jane", "title?type=movie&tmdb=11")[1]["canUnkeep"])
        self.assertTrue(self.w.get("joe", "title?type=movie&tmdb=11")[1]["canUnkeep"])
        self.assertTrue(self.w.get("admin", "title?type=movie&tmdb=11")[1]["canUnkeep"])
        r = self.w.get("viewer", "title?type=movie&tmdb=11")[1]
        self.assertEqual((r["canKeep"], r["canUnkeep"]), (False, False))

    def test_protected_title_cannot_be_switched(self):
        r = self.w.get("admin", "title?type=movie&tmdb=653")[1]
        self.assertEqual((r["state"], r["source"], r["canKeep"], r["canUnkeep"]), ("keep", "protected", False, False))

    def test_postponed_title(self):
        r = self.w.get("jane", "title?type=tv&tmdb=20")[1]
        self.assertEqual((r["postponed"], r["deleteOn"], r["why"]), ("in-progress", "2027-03-31", "postponed"))

    def test_not_in_the_library_yet_default_from_radarr_lookup(self):
        code, r = self.w.get("jane", "title?type=movie&tmdb=30&default=1")
        self.assertEqual((code, r["known"], r["default"], r["reasons"], r["keep"], r["deleteOn"]), (200, False, True, ["old"], True, None))

    def test_not_in_the_library_series_default_from_sonarr_lookup(self):
        r = self.w.get("jane", "title?type=tv&tmdb=40&default=1")[1]
        self.assertEqual((r["known"], r["reasons"], r["keep"]), (False, ["goated"], True))

    def test_title_page_never_asks_radarr_and_lookups_are_cached(self):
        r = self.w.get("jane", "title?type=movie&tmdb=30")[1]
        self.assertEqual((r["known"], r["reasons"], self.w.arr_calls), (False, ["old"], []))
        self.w.get("jane", "title?type=movie&tmdb=30&default=1")
        self.w.get("joe", "title?type=movie&tmdb=30&default=1")
        self.assertEqual(len(self.w.arr_calls), 1)

    def test_lookup_failure_is_temporary_by_default(self):
        self.w.media["movie/31"] = media("movie", 31, [], "2025-01-01")
        r = self.w.get("jane", "title?type=movie&tmdb=31&default=1")[1]
        self.assertEqual((r["known"], r["default"], r["keep"]), (False, False, False))

    def test_unreadable_data_file(self):
        self.w.data.write_text("{")
        code, r = self.w.get("jane", "title?type=movie&tmdb=10")
        self.assertEqual((code, r["known"]), (200, False))


class Titles(Base):
    def test_batch_for_cards(self):
        code, r = self.w.get("jane", "titles?keys=movie/10,movie/11,tv/99,bogus")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(r["titles"]), ["movie/10", "movie/11"])
        self.assertEqual((r["titles"]["movie/10"]["state"], r["titles"]["movie/10"]["deleteOn"]), ("temporary", "2026-12-30"))
        self.assertEqual(r["titles"]["movie/11"]["state"], "keep")
        self.assertNotIn("size", r["titles"]["movie/10"])

    def test_batch_is_capped(self):
        keys = ",".join(f"movie/{i}" for i in range(1, 200))
        self.assertEqual(self.w.get("jane", "titles?keys=" + keys)[0], 400)


class List(Base):
    def test_managers_only(self):
        self.assertEqual(self.w.get("jane", "list")[0], 403)
        code, r = self.w.get("admin", "list")
        self.assertEqual(code, 200)
        self.assertEqual([i["key"] for i in r["items"]], ["movie/10", "tv/21", "tv/20"])
        self.assertEqual(r["counts"], {"titles": 5, "keep": 2, "temporary": 3, "old": 2, "niche": 0, "goated": 0, "override": 0, "protected": 1, "soon": 0})
        self.assertEqual(r["promote"][0]["seasons"], [2])
        self.assertFalse(r["deleteEnabled"])


class Series(Base):
    def test_title_found_by_tvdb_when_seerr_and_sonarr_disagree_on_tmdb(self):
        r = self.w.get("jane", "title?type=tv&tmdb=22")[1]
        self.assertEqual((r["known"], r["key"], r["entryKey"]), (True, "tv/22", "tv/21"))

    def test_override_found_by_tvdb(self):
        self.w.keep.write_text(json.dumps({"titles": {"tv/99": {"keep": True, "tvdb": 4020}}}))
        r = self.w.get("jane", "title?type=tv&tmdb=20")[1]
        self.assertEqual((r["state"], r["source"]), ("keep", "override"))
        self.assertEqual(self.w.get("jane", "titles?keys=tv/20")[1]["titles"]["tv/20"]["state"], "keep")

    def test_keep_stores_the_tvdb_id_with_the_series(self):
        self.assertEqual(self.w.post("jane", {"type": "tv", "tmdbId": 20, "keep": True})[0], 200)
        self.assertEqual(json.loads(self.w.keep.read_text())["titles"]["tv/20"], {"keep": True, "tvdb": 4020, "by": 7, "at": json.loads(self.w.keep.read_text())["titles"]["tv/20"]["at"]})


class Conflicts(Base):
    def test_keep_wins_over_a_temporary_record_of_the_same_series(self):
        self.w.keep.write_text(json.dumps({"titles": {"tv/20": {"keep": False, "tvdb": 4020}, "tv/99": {"keep": True, "tvdb": 4020}}}))
        self.assertEqual(self.w.get("jane", "title?type=tv&tmdb=20")[1]["state"], "keep")
        self.assertEqual(self.w.get("jane", "titles?keys=tv/20")[1]["titles"]["tv/20"]["state"], "keep")

    def test_choice_for_a_series_updates_every_record_of_its_tvdb_id(self):
        self.w.keep.write_text(json.dumps({"titles": {"tv/99": {"keep": True, "tvdb": 4020}}}))
        code, r = self.w.post("jane", {"type": "tv", "tmdbId": 20, "keep": False})
        self.assertEqual((code, r["state"]), (200, "temporary"))
        self.assertEqual(json.loads(self.w.keep.read_text())["titles"]["tv/99"]["keep"], False)


class Store(Base):
    def test_permission_denied_is_unreadable_never_missing(self):
        locked = pathlib.Path(self.tmp.name, "locked")
        locked.mkdir()
        (locked / "keep.json").write_text(json.dumps({"titles": {"movie/10": {"keep": True}}}))
        self.w.deps.retention_keep = str(locked / "keep.json")
        locked.chmod(0)
        try:
            code = self.w.post("jane", {"type": "movie", "tmdbId": 10, "keep": False})[0]
            kept = ra.is_kept(self.w.deps, "movie/10")
        finally:
            locked.chmod(0o755)
        self.assertEqual((code, kept), (503, True))
        self.assertEqual(json.loads((locked / "keep.json").read_text())["titles"]["movie/10"]["keep"], True)

    def test_write_never_leaves_a_moment_without_the_file_and_keeps_a_backup(self):
        self.w.keep.write_text(json.dumps({"titles": {"movie/1": {"keep": True}}}))
        seen, real_replace, real_rename, synced = [], ra.os.replace, ra.os.rename, []
        def replace(a, b):
            seen.append(self.w.keep.exists())
            return real_replace(a, b)
        def rename(a, b):
            raise AssertionError("rename")
        ra.os.replace, ra.os.rename = replace, rename
        real_fsync = ra.os.fsync
        ra.os.fsync = lambda fd: (synced.append(fd), real_fsync(fd))
        try:
            ra.write_store(self.w.keep, {"titles": {"movie/2": {"keep": False}}})
        finally:
            ra.os.replace, ra.os.rename, ra.os.fsync = real_replace, real_rename, real_fsync
        self.assertTrue(seen and all(seen))
        self.assertTrue(synced)
        self.assertEqual(json.loads(self.w.keep.read_text())["titles"], {"movie/2": {"keep": False}})
        self.assertEqual(json.loads(self.w.keep.with_name("keep.json.bak").read_text())["titles"], {"movie/1": {"keep": True}})
        self.assertEqual(stat.S_IMODE(os.stat(self.w.keep).st_mode), 0o640)

    def test_first_write_has_no_backup(self):
        ra.write_store(self.w.keep, {"titles": {}})
        self.assertFalse(self.w.keep.with_name("keep.json.bak").exists())

    def test_missing_store_with_a_backup_is_not_written(self):
        self.w.keep.with_name("keep.json.bak").write_text(json.dumps({"titles": {}}))
        self.assertEqual(self.w.post("jane", {"type": "movie", "tmdbId": 10, "keep": True})[0], 503)
        self.assertFalse(self.w.keep.exists())


class Kept(Base):
    def test_kept_titles_for_other_backends(self):
        self.assertTrue(ra.is_kept(self.w.deps, "movie/11"))
        self.assertTrue(ra.is_kept(self.w.deps, "movie/653"))
        self.assertFalse(ra.is_kept(self.w.deps, "movie/10"))
        self.assertTrue(ra.is_kept(self.w.deps, "movie/999"))
        self.w.keep.write_text(json.dumps({"titles": {"movie/10": {"keep": True}}}))
        self.assertTrue(ra.is_kept(self.w.deps, "movie/10"))
        self.w.data.write_text("{")
        self.assertTrue(ra.is_kept(self.w.deps, "movie/10"))


class Lookups(Base):
    def test_uncached_default_lookups_are_rate_limited_per_user(self):
        self.w.media["movie/31"] = media("movie", 31, [], "2000-01-01")
        old, ra.LOOKUP_PER_MIN = ra.LOOKUP_PER_MIN, 1
        try:
            self.w.get("jane", "title?type=movie&tmdb=30&default=1")
            r = self.w.get("jane", "title?type=movie&tmdb=31&default=1")[1]
        finally:
            ra.LOOKUP_PER_MIN = old
        self.assertEqual(len(self.w.arr_calls), 1)
        self.assertEqual(r["reasons"], ["old"])


class Keep(Base):
    def test_anyone_who_may_request_can_keep_any_title(self):
        code, r = self.w.post("jane", {"type": "movie", "tmdbId": 11, "keep": True})
        self.assertEqual((code, r["state"], r["source"]), (200, "keep", "override"))
        code, r = self.w.post("jane", {"type": "tv", "tmdbId": 20, "keep": True})
        self.assertEqual(code, 429)
        ra.RECENT.clear()
        self.assertEqual(self.w.post("jane", {"type": "tv", "tmdbId": 20, "keep": True})[0], 200)
        store = json.loads(self.w.keep.read_text())
        self.assertEqual(store["titles"]["movie/11"]["keep"], True)
        self.assertEqual(store["titles"]["movie/11"]["by"], 7)
        self.assertEqual(stat.S_IMODE(os.stat(self.w.keep).st_mode), 0o640)

    def test_unkeep_only_requester_or_admin(self):
        self.assertEqual(self.w.post("jane", {"type": "movie", "tmdbId": 11, "keep": False})[0], 403)
        self.assertEqual(self.w.post("joe", {"type": "movie", "tmdbId": 11, "keep": False})[1]["state"], "temporary")
        ra.RECENT.clear()
        self.assertEqual(self.w.post("admin", {"type": "movie", "tmdbId": 11, "keep": True})[0], 200)

    def test_viewer_and_protected_refused(self):
        self.assertEqual(self.w.post("viewer", {"type": "movie", "tmdbId": 10, "keep": True})[0], 403)
        self.assertEqual(self.w.post("admin", {"type": "movie", "tmdbId": 653, "keep": False})[0], 403)

    def test_origin_json_and_shape(self):
        self.assertEqual(self.w.post("jane", {"type": "movie", "tmdbId": 10, "keep": True}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.w.post("jane", {"type": "movie", "tmdbId": 10, "keep": True}, ctype="text/plain")[0], 415)
        self.assertEqual(self.w.post("jane", b"x" * 2000)[0], 413)
        for bad in ({"type": "movie", "tmdbId": 10, "keep": "yes"}, {"type": "film", "tmdbId": 10, "keep": True}, {"type": "movie", "tmdbId": -1, "keep": True}, [1]):
            self.assertEqual(self.w.post("jane", bad)[0], 400, bad)

    def test_hourly_limit(self):
        for i in range(ra.PER_HOUR):
            ra.RECENT.setdefault(7, []).append(0)
        ra.RECENT[7] = [ra.now_ts() - 60] * ra.PER_HOUR
        self.assertEqual(self.w.post("jane", {"type": "movie", "tmdbId": 10, "keep": True})[0], 429)

    def test_unreadable_store_is_never_overwritten(self):
        self.w.keep.write_text("{broken")
        self.assertEqual(self.w.post("jane", {"type": "movie", "tmdbId": 10, "keep": True})[0], 503)
        self.assertEqual(self.w.keep.read_text(), "{broken")

    def test_title_unknown_to_seerr(self):
        self.assertEqual(self.w.post("jane", {"type": "movie", "tmdbId": 77, "keep": True})[0], 404)

    def test_state_reflects_the_store(self):
        self.w.post("joe", {"type": "movie", "tmdbId": 11, "keep": False})
        r = self.w.get("jane", "titles?keys=movie/11")[1]
        self.assertEqual(r["titles"]["movie/11"]["state"], "temporary")


class Routing(Base):
    def test_download_now_routes_retention(self):
        code, r = dn.handle("GET", "/_download-now/retention/title?type=movie&tmdb=10", {"X-Api-Key": "k", "X-Api-User": "jane"}, b"", self.w.deps)
        self.assertEqual((code, r["key"]), (200, "movie/10"))

    def test_env_wires_the_files(self):
        self.assertEqual(dn.Deps(seerr=None, arr=None, qbit=None, sab=None).retention_file, None)


if __name__ == "__main__":
    unittest.main()
