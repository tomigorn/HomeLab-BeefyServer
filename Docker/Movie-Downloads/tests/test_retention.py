#!/usr/bin/env python3
"""scripts/retention.py: storage retention classifier (README "Storage retention") against fake Radarr, Sonarr, Jellyfin and a fake pool; deletion only with fakes, off by default.
Usage: python3 tests/test_retention.py"""
import datetime
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


def movie(i, tmdb, title, year, added, size=10 * 2**30, imdb=(6.5, 9000), tmdb_votes=500, tags=(), profile=1):
    return {"id": i, "tmdbId": tmdb, "title": title, "year": year, "hasFile": True, "path": f"/data/media/movies/{title} ({year})",
            "ratings": {"imdb": {"value": imdb[0], "votes": imdb[1]}, "tmdb": {"value": 7.0, "votes": tmdb_votes}}, "tags": list(tags), "qualityProfileId": profile,
            "monitored": True, "movieFile": {"id": 100 + i, "dateAdded": added, "size": size, "relativePath": f"{title} ({year}).mkv"}}


def series(i, tmdb, title, year, rating=(7.5, 20000), tags=(), profile=1, files=1):
    return {"id": i, "tmdbId": tmdb, "tvdbId": 9000 + i, "title": title, "year": year, "path": f"/data/media/tv/{title} ({year})", "ratings": {"value": rating[0], "votes": rating[1]},
            "tags": list(tags), "qualityProfileId": profile, "monitored": True, "statistics": {"episodeFileCount": files, "sizeOnDisk": 5 * 2**30}}


def epfile(i, season, added, title, year, ep=1):
    return {"id": i, "seasonNumber": season, "dateAdded": added, "size": 2**30, "relativePath": f"Season {season:02d}/{title} S{season:02d}E{ep:02d}.mkv",
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
SERIES = [series(11, 2001, "New Show", 2025), series(12, 0, "No Tmdb", 2025), series(13, 2003, "Empty Show", 2025, files=0)]
EPFILES = {11: [epfile(1, 1, "2026-10-01T10:00:00Z", "New Show", 2025), epfile(2, 2, "2026-11-10T10:00:00Z", "New Show", 2025), epfile(3, 3, "2026-11-11T10:00:00Z", "New Show", 2025)],
           12: [epfile(4, 1, "2026-11-01T10:00:00Z", "No Tmdb", 2025)]}


def radarr(path, method="GET", body=None):
    if path == "/movie":
        return MOVIES
    if path == "/qualityprofile":
        return PROFILES
    if path == "/tag":
        return TAGS
    raise AssertionError(path)


def sonarr(path, method="GET", body=None):
    if path == "/series":
        return SERIES
    if path == "/qualityprofile":
        return PROFILES
    if path == "/tag":
        return TAGS
    if path.startswith("/episodefile?seriesId="):
        return EPFILES.get(int(path.split("=")[1]), [])
    raise AssertionError(path)


def jellyfin(resume=(), played=()):
    def call(path):
        if path == "/Users":
            return [{"Id": "u1", "Name": "jane"}]
        if path.startswith("/Users/u1/Items/Resume"):
            return {"Items": [{"Path": p} for p in resume]}
        if path.startswith("/Users/u1/Items?"):
            return {"Items": [{"Path": p, "UserData": {"LastPlayedDate": t}} for p, t in played]}
        raise AssertionError(path)
    return call


def library():
    return rt.collect(rt.Apis(radarr, sonarr, None))


class Collect(unittest.TestCase):
    def test_titles_with_files_only_keyed_by_tmdb(self):
        lib = library()
        keys = [t.key for t in lib.titles]
        self.assertIn("movie/1001", keys)
        self.assertIn("tv/2001", keys)
        self.assertNotIn("movie/1007", keys)
        self.assertNotIn("tv/2003", keys)
        self.assertEqual(lib.skipped, ["No Tmdb"])

    def test_series_clock_is_its_latest_import(self):
        t = {t.key: t for t in library().titles}["tv/2001"]
        self.assertEqual(t.imported, datetime.date(2026, 11, 11))
        self.assertEqual(sorted(t.seasons), [1, 2, 3])
        self.assertEqual(t.seasons[2], ["media/tv/New Show (2025)/Season 02/New Show S02E01.mkv"])

    def test_movie_fields(self):
        t = {t.key: t for t in library().titles}["movie/1006"]
        self.assertEqual((t.imported, t.tags, t.rel), (datetime.date(2026, 10, 6), ["audio-swiss-german"], "media/movies/Swiss Film (2025)"))
        self.assertEqual(t.videos, ["media/movies/Swiss Film (2025)/Swiss Film (2025).mkv"])


class Classify(unittest.TestCase):
    def run_it(self, overrides=None, progress=(), collections=(), hot=(), hdd=(100, 10)):
        return rt.classify(library().titles, overrides or {}, set(collections), set(progress), set(hot), hdd, TODAY)

    def test_defaults_by_category(self):
        e = self.run_it()
        self.assertEqual((e["movie/1001"]["default"], e["movie/1001"]["reasons"]), (False, []))
        self.assertEqual(e["movie/1002"]["reasons"], ["old"])
        self.assertEqual(e["movie/1003"]["reasons"], ["goated"])
        self.assertEqual(e["movie/1005"]["reasons"], ["niche"])
        self.assertEqual(e["movie/1006"]["reasons"], ["niche"])
        self.assertTrue(e["movie/1002"]["default"])

    def test_dates_and_test_film(self):
        e = self.run_it()
        self.assertEqual((e["movie/1001"]["ssdUntil"], e["movie/1001"]["due"]), ("2026-12-01", "2027-01-30"))
        self.assertTrue(e["movie/653"]["protected"])

    def test_tier_from_the_ssd_scan_only(self):
        hot = {"media/movies/New Film (2025)/New Film (2025).mkv", "media/tv/New Show (2025)/Season 02/New Show S02E01.mkv"}
        e = self.run_it(hot=hot)
        self.assertEqual(e["movie/1001"]["tier"], "ssd")
        self.assertEqual(e["movie/1002"]["tier"], "hdd")
        self.assertEqual(e["tv/2001"]["tier"], "split")

    def test_postponed_by_progress_or_own_collection(self):
        e = self.run_it(progress={"movie/1001"}, collections={"movie/1002", "tv/2001"})
        self.assertEqual(e["movie/1001"]["postponed"], "in-progress")
        self.assertEqual(e["tv/2001"]["postponed"], "collection")
        self.assertIsNone(e["movie/1003"]["postponed"])

    def test_space_rule_oldest_temporary_hdd_titles_first_until_80(self):
        # HDD 86 of 100 GiB used: free 6 GiB by deleting temporary titles past day 30, oldest import first
        e = self.run_it(hdd=(100 * 2**30, 86 * 2**30))
        self.assertEqual(e["movie/1001"]["space"], "2026-12-20")
        self.assertIsNone(e["tv/2001"].get("space"))
        self.assertIsNone(e["movie/1002"].get("space"))
        self.assertIsNone(e["movie/653"].get("space"))

    def test_space_rule_respects_overrides(self):
        e = self.run_it(overrides={"movie/1001": True}, hdd=(100 * 2**30, 86 * 2**30))
        self.assertIsNone(e["movie/1001"].get("space"))
        self.assertEqual(e["tv/2001"]["space"], "2026-12-20")

    def test_no_space_rule_below_85(self):
        e = self.run_it(hdd=(100 * 2**30, 85 * 2**30))
        self.assertFalse(any(v.get("space") for v in e.values()))


class Jellyfin(unittest.TestCase):
    def test_progress_maps_paths_to_titles(self):
        lib = library()
        jf = jellyfin(resume=["/media/movies/New Film (2025)/New Film (2025).mkv", "/media/tv/New Show (2025)/Season 01/New Show S01E01.mkv", "/elsewhere/x.mkv"])
        self.assertEqual(rt.in_progress(jf, lib.titles), {"movie/1001", "tv/2001"})

    def test_promotion_wants_the_watched_and_next_season_on_the_ssd(self):
        lib = library()
        jf = jellyfin(played=[("/media/tv/New Show (2025)/Season 01/New Show S01E01.mkv", "2026-12-20T09:00:00Z"),
                              ("/media/tv/New Show (2025)/Season 03/New Show S03E01.mkv", "2026-12-01T09:00:00Z"),
                              ("/media/movies/Old Film (1990)/Old Film (1990).mkv", "2026-12-20T09:00:00Z")])
        plays = rt.recent_plays(jf, lib.titles, NOW - datetime.timedelta(hours=24))
        self.assertEqual(plays, {"tv/2001": (1, "2026-12-20T09:00:00Z")})
        hot = {"media/tv/New Show (2025)/Season 01/New Show S01E01.mkv"}
        self.assertEqual(rt.promotions(lib.titles, plays, hot), [{"key": "tv/2001", "title": "New Show", "seasons": [2], "played": "2026-12-20T09:00:00Z"}])
        both = hot | {"media/tv/New Show (2025)/Season 02/New Show S02E01.mkv"}
        self.assertEqual(rt.promotions(lib.titles, plays, both), [])


class Pool(unittest.TestCase):
    def test_hot_scan_lists_videos_of_every_hot_branch(self):
        with tempfile.TemporaryDirectory() as d:
            for b, rel in (("a", "media/movies/X (2020)/X (2020).mkv"), ("b", "media/tv/Y (2021)/Season 01/Y S01E01.mkv"), ("a", "media/movies/X (2020)/X.nfo")):
                p = pathlib.Path(d, b, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
            got = rt.hot_videos([pathlib.Path(d, "a"), pathlib.Path(d, "b"), pathlib.Path(d, "missing")], ["media/movies", "media/tv"])
        self.assertEqual(got, {"media/movies/X (2020)/X (2020).mkv", "media/tv/Y (2021)/Season 01/Y S01E01.mkv"})


class Output(unittest.TestCase):
    def test_document_carries_titles_promotions_and_switch_state(self):
        entries = {"movie/1": {"t": "X"}}
        doc = rt.document(entries, [{"key": "tv/2"}], NOW, rt.Switch(False, None), (100, 50), ["No Tmdb"])
        self.assertEqual((doc["deleteEnabled"], doc["enabledOn"], doc["hdd"]["usedPct"], doc["titles"], doc["skipped"]), (False, None, 50.0, entries, 1))
        self.assertEqual(doc["rules"]["deleteDays"], 90)

    def test_switch_needs_both_flag_and_date(self):
        self.assertEqual(rt.Switch.from_env({}), rt.Switch(False, None))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "1"}), rt.Switch(False, None))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "0", "RETENTION_ENABLED_ON": "2026-12-01"}), rt.Switch(False, None))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "1", "RETENTION_ENABLED_ON": "2026-12-01"}), rt.Switch(True, datetime.date(2026, 12, 1)))
        self.assertEqual(rt.Switch.from_env({"RETENTION_DELETE": "1", "RETENTION_ENABLED_ON": "soon"}), rt.Switch(False, None))

    def test_overrides_read_from_the_keep_store(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "keep.json")
            p.write_text(json.dumps({"titles": {"movie/1": {"keep": True}, "tv/2": {"keep": False}, "bad": {"keep": True}, "movie/3": {"keep": "yes"}}}))
            self.assertEqual(rt.read_overrides(p), {"movie/1": True, "tv/2": False})
            self.assertEqual(rt.read_overrides(pathlib.Path(d, "none.json")), {})

    def test_collections_titles(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "lists.json")
            p.write_text(json.dumps({"lists": {"user-a": {"titles": [{"k": "movie", "id": 5}, {"k": "tv", "id": 7}, {"k": "x", "id": 1}]}}}))
            self.assertEqual(rt.read_collections(p), {"movie/5", "tv/7"})


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, app):
        def call(path, method="GET", body=None):
            self.calls.append((app, method, path, body))
            if path.startswith("/movie/") and method == "GET":
                m = next(x for x in MOVIES if x["id"] == int(path.split("/")[2]))
                return json.loads(json.dumps(m))
            if path.startswith("/series/") and method == "GET":
                return json.loads(json.dumps(next(x for x in SERIES if x["id"] == int(path.split("/")[2]))))
            if path.startswith("/episodefile?seriesId="):
                return EPFILES.get(int(path.split("=")[1]), [])
            if path == "/tag" and method == "GET":
                return TAGS
            if path == "/tag" and method == "POST":
                return {"id": 9, "label": body["label"]}
            return {}
        return call


NIGHT = datetime.datetime(2027, 2, 10, 4, 10, tzinfo=TZ)


class Delete(unittest.TestCase):
    def entries(self):
        lib = library()
        return lib.titles, rt.classify(lib.titles, {}, set(), set(), set(), (100, 10), NIGHT.date())

    def test_off_by_default_nothing_is_called(self):
        titles, e = self.entries()
        arr = Fake()
        logs = []
        n = rt.delete_due(titles, e, {}, NIGHT, rt.Switch(False, None), arr, logs.append)
        self.assertEqual((n, arr.calls), (0, []))
        self.assertTrue(any("observe mode" in l for l in logs))

    def test_on_deletes_files_only_unmonitors_and_tags(self):
        titles, e = self.entries()
        arr = Fake()
        n = rt.delete_due(titles, e, {}, NIGHT, rt.Switch(True, datetime.date(2026, 12, 1)), arr, lambda l: None)
        deletes = [c for c in arr.calls if c[1] == "DELETE"]
        self.assertIn(("radarr", "DELETE", "/moviefile/101", None), deletes)
        self.assertIn(("sonarr", "DELETE", "/episodefile/bulk", {"episodeFileIds": [1, 2, 3]}), deletes)
        self.assertFalse(any(c[2].startswith("/movie/") and c[1] == "DELETE" for c in arr.calls))
        self.assertFalse(any("/series/" in c[2] and c[1] == "DELETE" for c in arr.calls))
        puts = {c[2]: c[3] for c in arr.calls if c[1] == "PUT"}
        self.assertEqual((puts["/movie/1"]["monitored"], puts["/movie/1"]["tags"]), (False, [9]))
        self.assertEqual(n, 2)

    def test_never_keep_forever_test_film_or_overrides(self):
        titles, e = self.entries()
        arr = Fake()
        rt.delete_due(titles, e, {"movie/1001": True}, NIGHT, rt.Switch(True, datetime.date(2026, 12, 1)), arr, lambda l: None)
        paths = [c[2] for c in arr.calls if c[1] == "DELETE"]
        for p in ("/moviefile/101", "/moviefile/102", "/moviefile/103", "/moviefile/104", "/moviefile/105", "/moviefile/106"):
            self.assertNotIn(p, paths)

    def test_refused_outside_the_night_window(self):
        titles, e = self.entries()
        arr = Fake()
        logs = []
        n = rt.delete_due(titles, e, {}, NIGHT.replace(hour=12), rt.Switch(True, datetime.date(2026, 12, 1)), arr, logs.append)
        self.assertEqual((n, arr.calls), (0, []))
        self.assertTrue(any("night window" in l for l in logs))

    def test_grace_after_switch_on_holds_overdue_titles(self):
        titles, e = self.entries()
        arr = Fake()
        n = rt.delete_due(titles, e, {}, NIGHT, rt.Switch(True, datetime.date(2027, 2, 1)), arr, lambda l: None)
        self.assertEqual((n, arr.calls), (0, []))

    def test_per_night_cap(self):
        titles, e = self.entries()
        arr = Fake()
        n = rt.delete_due(titles, e, {}, NIGHT, rt.Switch(True, datetime.date(2026, 12, 1)), arr, lambda l: None, cap=1)
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
