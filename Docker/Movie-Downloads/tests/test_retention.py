#!/usr/bin/env python3
"""scripts/retention.py: storage retention classifier (README "Storage retention") against fake Radarr, Sonarr, Seerr, Jellyfin and a fake pool; deletion only with fakes, off by default, and every guard that must hold before it may run.
Usage: python3 tests/test_retention.py"""
import datetime
import fcntl
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retention", HERE.parent / "scripts" / "retention.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime.datetime(2026, 12, 20, 12, 0, tzinfo=TZ)
TODAY = NOW.date()
GIB = 2**30


def movie(i, tmdb, title, year, added, size=10 * GIB, imdb=(6.5, 9000), tmdb_votes=500, tags=(), profile=1):
    return {"id": i, "tmdbId": tmdb, "title": title, "year": year, "hasFile": True, "path": f"/data/media/movies/{title} ({year})",
            "ratings": {"imdb": {"value": imdb[0], "votes": imdb[1]}, "tmdb": {"value": 7.0, "votes": tmdb_votes}}, "tags": list(tags), "qualityProfileId": profile,
            "monitored": True, "movieFile": {"id": 100 + i, "dateAdded": added, "size": size, "relativePath": f"{title} ({year}).mkv"}}


def series(i, tmdb, title, year, rating=(7.5, 20000), tags=(), profile=1, files=1):
    return {"id": i, "tmdbId": tmdb, "tvdbId": 9000 + i, "title": title, "year": year, "path": f"/data/media/tv/{title} ({year})", "ratings": {"value": rating[0], "votes": rating[1]},
            "tags": list(tags), "qualityProfileId": profile, "monitored": True, "statistics": {"episodeFileCount": files, "sizeOnDisk": 5 * GIB}}


def epfile(i, season, added, title, year, ep=1):
    return {"id": i, "seasonNumber": season, "dateAdded": added, "size": GIB, "relativePath": f"Season {season:02d}/{title} S{season:02d}E{ep:02d}.mkv",
            "path": f"/data/media/tv/{title} ({year})/Season {season:02d}/{title} S{season:02d}E{ep:02d}.mkv"}


PROFILES = [{"id": 1, "name": "4K HDR - UHD Bluray + WEB (1080p fallback)"}, {"id": 2, "name": "4K preferred · older versions allowed"}]
TAGS = [{"id": 1, "label": "keep-test"}, {"id": 2, "label": "audio-swiss-german"}, {"id": 3, "label": "download-now"}]
MOVIES = [
    movie(1, 1001, "New Film", 2025, "2026-11-01T10:00:00Z"),
    movie(2, 1002, "Old Film", 1990, "2026-11-02T10:00:00Z"),
    movie(3, 1003, "Great Film", 2024, "2026-11-03T10:00:00Z", imdb=(8.5, 300000)),
    movie(4, 653, "Nosferatu", 1922, "2026-10-01T10:00:00Z"),
    movie(5, 1005, "Niche Film", 2022, "2026-10-05T10:00:00Z", tmdb_votes=40),
    movie(6, 1006, "Swiss Film", 2025, "2026-10-06T10:00:00Z", tags=[2]),
    {"id": 7, "tmdbId": 1007, "title": "Wanted", "year": 2025, "hasFile": False, "path": "/data/media/movies/Wanted (2025)", "tags": [], "qualityProfileId": 1, "ratings": {}},
]
# 14: Sonarr has another TMDB id than Seerr (live: Company Retreat 319013 vs 312697); 15: no Seerr row at all
SERIES = [series(11, 2001, "New Show", 2025), series(12, 0, "No Tmdb", 2025), series(13, 2003, "Empty Show", 2025, files=0),
          series(14, 4000, "Mismatch Show", 2025), series(15, 2005, "Unmapped Show", 2025)]
EPFILES = {11: [epfile(1, 1, "2026-10-01T10:00:00Z", "New Show", 2025), epfile(2, 2, "2026-11-10T10:00:00Z", "New Show", 2025), epfile(3, 3, "2026-11-11T10:00:00Z", "New Show", 2025)],
           12: [epfile(4, 1, "2026-11-01T10:00:00Z", "No Tmdb", 2025)],
           14: [epfile(5, 1, "2026-11-12T10:00:00Z", "Mismatch Show", 2025)],
           15: [epfile(6, 1, "2026-11-01T10:00:00Z", "Unmapped Show", 2025)]}
SEERR = [{"mediaType": "tv", "tmdbId": 2001, "tvdbId": 9011, "externalServiceId": 11}, {"mediaType": "tv", "tmdbId": 3999, "tvdbId": 9014, "externalServiceId": 77},
         {"mediaType": "movie", "tmdbId": 1001, "tvdbId": None, "externalServiceId": 1}]


def radarr(path, method="GET", body=None):
    return {"/movie": MOVIES, "/qualityprofile": PROFILES, "/tag": TAGS}[path]


def sonarr(path, method="GET", body=None):
    if path.startswith("/episodefile?seriesId="):
        return EPFILES.get(int(path.split("=")[1]), [])
    return {"/series": SERIES, "/qualityprofile": PROFILES, "/tag": TAGS}[path]


def jellyfin(resume=(), played=(), nextup=(), paths=None):
    def call(path):
        if path == "/Users":
            return [{"Id": "u1", "Name": "jane"}]
        if path.startswith("/Users/u1/Items/Resume"):
            return {"Items": [{"Path": p} for p in resume]}
        if path.startswith("/Shows/NextUp?userId=u1"):
            return {"Items": [{"Path": p} for p in nextup]}
        if path.startswith("/Items?userId=u1&Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path"):
            return {"Items": [{"Path": p} for p in (paths or [])]}
        if path.startswith("/Users/u1/Items?"):
            return {"Items": [{"Path": p, "UserData": {"LastPlayedDate": t}} for p, t in played]}
        raise AssertionError(path)
    return call


def library(seerr=SEERR):
    return rt.collect(rt.Apis(radarr, sonarr, None), seerr)


def keys(lib):
    return {t.key: t for t in lib.titles}


class Collect(unittest.TestCase):
    def test_titles_with_files_only_keyed_by_seerr_ids(self):
        k = keys(library())
        self.assertIn("movie/1001", k)
        self.assertIn("tv/2001", k)
        self.assertNotIn("movie/1007", k)
        self.assertNotIn("tv/2003", k)
        self.assertEqual(library().skipped, ["No Tmdb"])

    def test_series_mapped_through_seerr_by_service_id_or_tvdb(self):
        k = keys(library())
        self.assertIn("tv/3999", k)
        self.assertNotIn("tv/4000", k)
        self.assertEqual((k["tv/3999"].tvdb, k["tv/3999"].unmapped), (9014, False))

    def test_series_without_seerr_row_is_unmapped(self):
        k = keys(library())
        self.assertTrue(k["tv/2005"].unmapped)
        self.assertTrue(all(t.unmapped for t in library([]).titles if t.kind == "tv"))

    def test_series_clock_is_its_latest_import(self):
        t = keys(library())["tv/2001"]
        self.assertEqual(t.imported, datetime.date(2026, 11, 11))
        self.assertEqual(t.added_at, datetime.datetime(2026, 11, 11, 10, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(sorted(t.seasons), [1, 2, 3])

    def test_movie_fields(self):
        t = keys(library())["movie/1006"]
        self.assertEqual((t.imported, t.tags, t.rel, t.tmdb), (datetime.date(2026, 10, 6), ["audio-swiss-german"], "media/movies/Swiss Film (2025)", 1006))
        self.assertEqual(t.videos, ["media/movies/Swiss Film (2025)/Swiss Film (2025).mkv"])


class Classify(unittest.TestCase):
    def run_it(self, overrides=None, progress=(), collections=(), hot=(), hdd=(100, 10), pending=0):
        return rt.classify(library().titles, overrides or {}, set(collections), set(progress), set(hot), hdd, TODAY, pending)

    def test_defaults_by_category(self):
        e = self.run_it()
        self.assertEqual((e["movie/1001"]["default"], e["movie/1001"]["reasons"]), (False, []))
        self.assertEqual(e["movie/1002"]["reasons"], ["old"])
        self.assertEqual(e["movie/1003"]["reasons"], ["goated"])
        self.assertEqual(e["movie/1005"]["reasons"], ["niche"])
        self.assertEqual(e["movie/1006"]["reasons"], ["niche"])

    def test_dates_test_film_and_unmapped_series_protected(self):
        e = self.run_it()
        self.assertEqual((e["movie/1001"]["ssdUntil"], e["movie/1001"]["due"]), ("2026-12-01", "2027-01-30"))
        self.assertTrue(e["movie/653"]["protected"])
        self.assertTrue(e["tv/2005"]["protected"])
        self.assertFalse(e["tv/3999"]["protected"])
        self.assertEqual(e["tv/3999"]["tvdb"], 9014)

    def test_override_found_by_tvdb_too(self):
        e = self.run_it()
        self.assertTrue(rt.override_for({"tvdb/9014": True}, "tv/3999", e["tv/3999"]))
        self.assertIsNone(rt.override_for({}, "tv/3999", e["tv/3999"]))
        self.assertFalse(rt.override_for({"tv/3999": False, "tvdb/9014": True}, "tv/3999", e["tv/3999"]))

    def test_tier_and_bytes_on_the_hdd(self):
        hot = {"media/movies/New Film (2025)/New Film (2025).mkv", "media/tv/New Show (2025)/Season 02/New Show S02E01.mkv"}
        e = self.run_it(hot=hot)
        self.assertEqual((e["movie/1001"]["tier"], e["movie/1001"]["hddBytes"]), ("ssd", 0))
        self.assertEqual((e["movie/1002"]["tier"], e["movie/1002"]["hddBytes"]), ("hdd", 10 * GIB))
        self.assertEqual((e["tv/2001"]["tier"], e["tv/2001"]["hddBytes"]), ("split", int(5 * GIB * 2 / 3)))

    def test_postponed_by_progress_or_own_collection(self):
        e = self.run_it(progress={"movie/1001"}, collections={"movie/1002", "tv/2001"})
        self.assertEqual(e["movie/1001"]["postponed"], "in-progress")
        self.assertEqual(e["tv/2001"]["postponed"], "collection")

    def test_space_rule_oldest_temporary_hdd_bytes_first_until_80(self):
        e = self.run_it(hdd=(100 * GIB, 86 * GIB))
        self.assertEqual(e["movie/1001"]["space"], "2026-12-20")
        self.assertIsNone(e["tv/2001"].get("space"))
        self.assertIsNone(e["movie/653"].get("space"))

    def test_space_rule_skips_titles_still_on_the_ssd(self):
        e = self.run_it(hdd=(100 * GIB, 86 * GIB), hot={"media/movies/New Film (2025)/New Film (2025).mkv"})
        self.assertIsNone(e["movie/1001"].get("space"))
        self.assertEqual(e["tv/2001"]["space"], "2026-12-20")
        self.assertEqual(e["tv/3999"]["space"], "2026-12-20")

    def test_space_rule_respects_overrides_and_the_recycle_bin(self):
        e = self.run_it(overrides={"movie/1001": True}, hdd=(100 * GIB, 86 * GIB))
        self.assertIsNone(e["movie/1001"].get("space"))
        self.assertEqual(e["tv/2001"]["space"], "2026-12-20")
        e = self.run_it(hdd=(100 * GIB, 86 * GIB), pending=2 * GIB)
        self.assertFalse(any(v.get("space") for v in e.values()))

    def test_no_space_rule_below_85(self):
        e = self.run_it(hdd=(100 * GIB, 85 * GIB))
        self.assertFalse(any(v.get("space") for v in e.values()))


class Jellyfin(unittest.TestCase):
    def test_in_progress_is_resume_next_up_and_plays_of_the_last_14_days(self):
        lib = library()
        jf = jellyfin(resume=["/media/movies/New Film (2025)/New Film (2025).mkv", "/elsewhere/x.mkv"],
                      nextup=["/media/tv/New Show (2025)/Season 02/New Show S02E01.mkv"],
                      played=[("/media/movies/Old Film (1990)/Old Film (1990).mkv", "2026-12-10T09:00:00Z"),
                              ("/media/movies/Great Film (2024)/Great Film (2024).mkv", "2026-11-01T09:00:00Z")])
        self.assertEqual(rt.in_progress(jf, lib.titles, NOW), {"movie/1001", "tv/2001", "movie/1002"})

    def test_coverage_of_library_files_known_to_jellyfin(self):
        lib = library()
        files = [f for t in lib.titles for f in (t.videos or [x for s in t.seasons.values() for x in s])]
        self.assertEqual(rt.coverage(jellyfin(paths=["/" + f for f in files]), lib.titles, NOW), 1.0)
        self.assertLess(rt.coverage(jellyfin(paths=["/" + files[0]]), lib.titles, NOW), 0.9)

    def test_promotion_wants_the_watched_and_next_season_on_the_ssd(self):
        lib = library()
        jf = jellyfin(played=[("/media/tv/New Show (2025)/Season 01/New Show S01E01.mkv", "2026-12-20T09:00:00Z"),
                              ("/media/tv/New Show (2025)/Season 03/New Show S03E01.mkv", "2026-12-01T09:00:00Z")])
        plays = rt.recent_plays(jf, lib.titles, NOW - datetime.timedelta(hours=24))
        self.assertEqual(plays, {"tv/2001": (1, "2026-12-20T09:00:00Z")})
        hot = {"media/tv/New Show (2025)/Season 01/New Show S01E01.mkv"}
        self.assertEqual(rt.promotions(lib.titles, plays, hot), [{"key": "tv/2001", "title": "New Show", "seasons": [2], "played": "2026-12-20T09:00:00Z"}])


class Pool(unittest.TestCase):
    def test_hot_scan_lists_videos_of_every_hot_branch(self):
        with tempfile.TemporaryDirectory() as d:
            for b, rel in (("a", "media/movies/X (2020)/X (2020).mkv"), ("b", "media/tv/Y (2021)/Season 01/Y S01E01.mkv"), ("a", "media/movies/X (2020)/X.nfo")):
                p = pathlib.Path(d, b, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
            got = rt.hot_videos([pathlib.Path(d, "a"), pathlib.Path(d, "b"), pathlib.Path(d, "missing")], ["media/movies", "media/tv"])
        self.assertEqual(got, {"media/movies/X (2020)/X (2020).mkv", "media/tv/Y (2021)/Season 01/Y S01E01.mkv"})

    def test_hdd_not_mounted_aborts(self):
        st = type("S", (), {"f_blocks": 100, "f_frsize": 1, "f_bavail": 40})()
        self.assertEqual(rt.hdd_usage("/x", lambda p: True, lambda p: st), (100, 60))
        with self.assertRaises(rt.NotMounted):
            rt.hdd_usage("/x", lambda p: False, lambda p: st)

    def test_media_lock_waits_and_gives_up(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "beefy-media.lock")
            with rt.media_lock(p, 0) as got:
                self.assertTrue(got)
                with rt.media_lock(p, 0) as again:
                    self.assertFalse(again)


class Output(unittest.TestCase):
    def test_document_carries_titles_promotions_switch_state_and_problems(self):
        entries = {"movie/1": {"t": "X"}}
        doc = rt.document(entries, [{"key": "tv/2"}], NOW, rt.Switch(False, None), (100, 50), ["No Tmdb"], problems=["keep.json unreadable"])
        self.assertEqual((doc["deleteEnabled"], doc["enabledOn"], doc["hdd"]["usedPct"], doc["titles"], doc["skipped"], doc["problems"]), (False, None, 50.0, entries, 1, ["keep.json unreadable"]))

    def test_summary_for_the_morning_metrics(self):
        lib = library()
        e = rt.classify(lib.titles, {}, set(), set(), set(), (100, 10), datetime.date(2027, 1, 25))
        self.assertEqual(rt.summary(e, {"movie/1001": True}, datetime.date(2027, 1, 25), None), {"keep": 7, "temporary": 2, "leaving7d": 0, "next": "2027-02-09"})
        self.assertEqual(rt.summary(e, {}, datetime.date(2027, 1, 25), None)["leaving7d"], 1)

    def test_switch_needs_both_flag_and_date(self):
        self.assertEqual(rt.Switch.from_env({}), rt.Switch(False, None))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "1"}), rt.Switch(False, None))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "0", "RETENTION_ENABLED_ON": "2026-12-01"}), rt.Switch(False, None))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "1", "RETENTION_ENABLED_ON": "2026-12-01"}), rt.Switch(True, datetime.date(2026, 12, 1)))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "1", "RETENTION_ENABLED_ON": "soon"}), rt.Switch(False, None))

    def test_collections_titles_and_unreadable_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "lists.json")
            p.write_text(json.dumps({"lists": [{"id": "user-a", "items": [{"k": "movie", "id": 5}, {"k": "tv", "id": 7}, {"k": "x", "id": 1}]}]}))
            self.assertEqual(rt.read_collections(p), ({"movie/5", "tv/7"}, True))
            p.write_text(json.dumps({"lists": {"user-a": {}}}))
            self.assertEqual(rt.read_collections(p), (set(), False))
            p.write_text(json.dumps({"lists": [{"id": "user-a", "items": [{"k": "movie", "id": 5}, {"k": "tv", "id": 7}]}]}))
            self.assertEqual(rt.read_collections(p), ({"movie/5", "tv/7"}, True))
            p.write_text("{")
            self.assertEqual(rt.read_collections(p), (set(), False))
            self.assertEqual(rt.read_collections(pathlib.Path(d, "none.json")), (set(), False))


class KeepStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = pathlib.Path(self.tmp.name, "keep.json")
        self.bak = pathlib.Path(self.tmp.name, "keep.json.bak")

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_ids_of_a_series(self):
        self.p.write_text(json.dumps({"titles": {"movie/1": {"keep": True}, "tv/2": {"keep": False, "tvdb": 92}, "bad": {"keep": True}, "movie/3": {"keep": "yes"}}}))
        self.assertEqual(rt.read_overrides(self.p), ({"movie/1": True, "tv/2": False, "tvdb/92": False}, None))

    def test_no_store_yet_is_fine(self):
        self.assertEqual(rt.read_overrides(self.p), ({}, None))

    def test_missing_while_a_backup_exists_is_a_problem(self):
        self.bak.write_text(json.dumps({"titles": {}}))
        self.assertEqual(rt.read_overrides(self.p)[0], {})
        self.assertIn("missing", rt.read_overrides(self.p)[1])

    def test_unreadable_or_malformed_is_a_problem(self):
        for bad in ("{", json.dumps([1]), json.dumps({"titles": [1]})):
            self.p.write_text(bad)
            self.assertIsNotNone(rt.read_overrides(self.p)[1], bad)


class Soon(unittest.TestCase):
    def test_first_leaving_soon_day_kept_reset_by_postponement_change_or_keep(self):
        e = {"movie/1": {"due": "2026-12-24", "default": False, "postponed": None}, "movie/2": {"due": "2027-03-01", "default": False, "postponed": None},
             "movie/3": {"due": "2026-12-22", "default": False, "postponed": "in-progress"}}
        s = rt.update_soon(e, {}, TODAY, None, {})
        self.assertEqual(s, {"movie/1": {"since": "2026-12-20", "p": False}})
        s = rt.update_soon(e, {}, TODAY + datetime.timedelta(days=1), None, s)
        self.assertEqual(s["movie/1"]["since"], "2026-12-20")
        self.assertEqual(rt.update_soon(e, {"movie/1": True}, TODAY, None, s), {})
        e["movie/3"]["postponed"] = None
        s2 = rt.update_soon(e, {}, TODAY, None, {"movie/3": {"since": "2026-12-01", "p": True}})
        self.assertEqual(s2["movie/3"], {"since": "2026-12-20", "p": False})


class Fake:
    def __init__(self, fail=None, wrong=None):
        self.calls, self.fail, self.wrong = [], fail, wrong

    def __call__(self, app):
        def call(path, method="GET", body=None):
            self.calls.append((app, method, path, body))
            if self.fail and (method, path) == self.fail:
                raise OSError("boom")
            if path.startswith("/movie/") and method == "GET":
                m = json.loads(json.dumps(next(x for x in MOVIES if x["id"] == int(path.split("/")[2]))))
                if self.wrong == path:
                    m["tmdbId"] = 1
                return m
            if path.startswith("/series/") and method == "GET":
                s = json.loads(json.dumps(next(x for x in SERIES if x["id"] == int(path.split("/")[2]))))
                if self.wrong == path:
                    s["tvdbId"] = 1
                return s
            if path.startswith("/episodefile?seriesId="):
                return EPFILES.get(int(path.split("=")[1]), [])
            if path == "/tag" and method == "GET":
                return TAGS
            if path == "/tag" and method == "POST":
                return {"id": 9, "label": body["label"]}
            return {}
        return call


NIGHT = datetime.datetime(2027, 2, 10, 4, 35, tzinfo=TZ)
ON = rt.Switch(True, datetime.date(2026, 12, 1))


def night(**kw):
    base = dict(problems=[], soon={k: {"since": "2027-01-01", "p": False} for k in ("movie/1001", "tv/2001", "tv/3999", "tv/2005", "movie/653")},
                busy=set(), hdd_used=10, last={}, clock_ok=True, first_on=datetime.date(2026, 12, 1))
    base.update(kw)
    return rt.Night(**base)


class Delete(unittest.TestCase):
    def setUp(self):
        lib = library()
        self.titles = lib.titles
        self.e = rt.classify(lib.titles, {}, set(), set(), set(), (100, 10), NIGHT.date())
        self.logs = []

    def run_it(self, arr=None, switch=ON, n=None, overrides=None, when=NIGHT, **kw):
        self.arr = arr or Fake()
        return rt.delete_due(self.titles, self.e, overrides or {}, when, switch, self.arr, self.logs.append, n or night(), **kw)

    def deletes(self):
        return [c[2] for c in self.arr.calls if c[1] == "DELETE"]

    def test_off_by_default_nothing_is_called(self):
        self.assertEqual(self.run_it(switch=rt.Switch(False, None)), (0, 0))
        self.assertEqual(self.arr.calls, [])
        self.assertTrue(any("observe mode" in l for l in self.logs))

    def test_on_unmonitors_and_tags_first_then_deletes_files_only(self):
        self.assertEqual(self.run_it(), (3, 0))
        movie = [c for c in self.arr.calls if c[0] == "radarr" and c[1] in ("PUT", "DELETE")]
        self.assertEqual([(c[1], c[2]) for c in movie], [("PUT", "/movie/1"), ("DELETE", "/moviefile/101")])
        self.assertEqual((movie[0][3]["monitored"], movie[0][3]["tags"]), (False, [9]))
        show = [(c[1], c[2], c[3]) for c in self.arr.calls if c[0] == "sonarr" and c[1] in ("PUT", "DELETE")]
        self.assertEqual([s[:2] for s in show], [("PUT", "/series/11"), ("DELETE", "/episodefile/bulk"), ("PUT", "/series/14"), ("DELETE", "/episodefile/bulk")])
        self.assertEqual(show[1][2], {"episodeFileIds": [1, 2, 3]})
        self.assertFalse(any(c[1] == "DELETE" and (c[2].startswith("/movie/") or c[2].startswith("/series/")) for c in self.arr.calls))

    def test_never_keep_forever_test_film_unmapped_or_overrides(self):
        self.run_it(overrides={"movie/1001": True, "tvdb/9014": True})
        self.assertEqual(self.deletes(), ["/episodefile/bulk"])
        self.assertFalse(any(c[2] in ("/series/15", "/movie/4") for c in self.arr.calls))

    def test_id_mismatch_stops_the_run(self):
        self.assertEqual(self.run_it(arr=Fake(wrong="/movie/1"))[0], 1)
        self.assertEqual([c[1] for c in self.arr.calls if c[1] != "GET"], [])
        self.assertTrue(any("does not match" in l for l in self.logs))

    def test_first_error_stops_the_run_and_counts_against_the_cap(self):
        self.assertEqual(self.run_it(arr=Fake(fail=("PUT", "/movie/1")))[0], 1)
        self.assertEqual(self.deletes(), [])
        self.assertFalse(any(c[0] == "sonarr" for c in self.arr.calls))
        self.assertTrue(any("stopped" in l for l in self.logs))

    def test_per_night_cap(self):
        self.assertEqual(self.run_it(cap=1), (1, 0))
        self.assertEqual(self.deletes(), ["/moviefile/101"])

    def test_refused_on_any_data_problem(self):
        self.assertEqual(self.run_it(n=night(problems=["keep.json unreadable"])), (0, 0))
        self.assertEqual(self.arr.calls, [])
        self.assertTrue(any("keep.json unreadable" in l for l in self.logs))

    def test_refused_outside_the_night_window_without_clock_or_with_a_bad_switch_date(self):
        for kw in ({"when": NIGHT.replace(hour=12)}, {"n": night(clock_ok=False)}, {"switch": rt.Switch(True, datetime.date(2027, 2, 12))},
                   {"n": night(first_on=datetime.date(2027, 1, 1))}):
            self.logs.clear()
            self.assertEqual(self.run_it(**kw), (0, 0), kw)
            self.assertEqual(self.arr.calls, [])

    def test_grace_after_switch_on_holds_overdue_titles(self):
        self.assertEqual(self.run_it(switch=rt.Switch(True, datetime.date(2027, 2, 1)), n=night(first_on=datetime.date(2027, 2, 1))), (0, 0))

    def test_only_after_seven_days_of_leaving_soon(self):
        soon = night().soon
        soon["movie/1001"] = {"since": "2027-02-05", "p": False}
        del soon["tv/2001"]
        self.run_it(n=night(soon=soon))
        self.assertFalse(any(c[2] in ("/movie/1", "/series/11") for c in self.arr.calls))

    def test_queue_items_and_fresh_imports_are_skipped(self):
        self.run_it(n=night(busy={"movie/1001"}))
        self.assertNotIn("/moviefile/101", self.deletes())
        self.titles[0].added_at = NIGHT - datetime.timedelta(hours=2)
        self.run_it()
        self.assertNotIn("/moviefile/101", self.deletes())

    def test_space_deletions_have_their_own_cap_and_need_the_hdd_to_drop(self):
        for k in ("movie/1001", "tv/2001", "tv/3999"):
            self.e[k]["due"] = "2027-05-01"
            self.e[k]["space"] = "2027-02-10"
        self.assertEqual(self.run_it(space_cap=1), (1, 1))
        self.assertEqual(self.run_it(n=night(last={"spaceDeleted": 1, "hddUsed": 50}, hdd_used=60)), (0, 0))
        self.assertTrue(any("did not drop" in l for l in self.logs))
        self.assertEqual(self.run_it(n=night(last={"spaceDeleted": 1, "hddUsed": 50}, hdd_used=40))[1], 3)

    def test_deletions_are_recorded_with_seerr_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "gone.json")
            self.run_it(gone_file=p)
            self.assertEqual(sorted(g["key"] for g in rt.read_gone(p)), ["movie/1001", "tv/2001", "tv/3999"])
            self.assertEqual({g["key"]: g.get("tvdb") for g in rt.read_gone(p)}["tv/3999"], 9014)


class Units(unittest.TestCase):
    def test_delete_run_after_the_night_jobs_inside_the_window_with_the_media_lock(self):
        d = HERE.parent / "systemd"
        self.assertIn("OnCalendar=*-*-* 04:31:00 Europe/Zurich", (d / "retention-delete.timer").read_text())
        self.assertIn(" /run/lock", (d / "retention-delete.service").read_text())
        self.assertTrue(rt.in_night(datetime.datetime(2027, 1, 1, 4, 31, tzinfo=TZ)))
        self.assertLess(rt.LOCK_WAIT, 9 * 60)


class JellyfinLists(unittest.TestCase):
    def test_leaving_soon_and_gone_as_own_list_rows(self):
        lib = library()
        today = datetime.date(2027, 1, 25)
        e = rt.classify(lib.titles, {}, set(), set(), set(), (100, 10), today)
        gone = [{"key": "movie/1005", "t": "Niche Film", "y": 2022, "at": "2027-01-20"}, {"key": "bad"}]
        doc = rt.jellyfin_lists(e, {}, gone, today, datetime.date(2026, 12, 1))
        rows = {l["id"]: l for l in doc["lists"]}
        self.assertEqual(rows["user-retention-leaving"]["items"], [{"k": "movie", "id": 1001}])
        self.assertEqual(rows["user-retention-gone"]["items"], [{"k": "movie", "id": 1005}])

    def test_gone_records_are_kept_capped_and_count_the_recycle_bin(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "gone.json")
            for i in range(3):
                rt.record_gone(p, f"movie/{i + 1}", f"T{i}", 2020, datetime.date(2027, 1, 1 + i), cap=2, hdd_bytes=GIB)
            self.assertEqual([g["key"] for g in rt.read_gone(p)], ["movie/3", "movie/2"])
            self.assertEqual(rt.recycle_pending(rt.read_gone(p), datetime.date(2027, 1, 9)), GIB)


if __name__ == "__main__":
    unittest.main()
