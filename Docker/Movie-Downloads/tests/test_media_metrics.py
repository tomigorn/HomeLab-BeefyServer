#!/usr/bin/env python3
"""scripts/media-metrics.py: request, download and indexer figures for fastpi's morning dashboard.
Usage: python3 tests/test_media_metrics.py"""
import datetime
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("mm", ROOT / "scripts" / "media-metrics.py")
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)

TZ = mm.TZ


class Window(unittest.TestCase):
    def test_inside_the_night(self):
        s, e = mm.night_window(datetime.datetime(2026, 10, 29, 1, 30, tzinfo=TZ))
        self.assertEqual((s, e), (datetime.datetime(2026, 10, 28, 22, 0, tzinfo=TZ), datetime.datetime(2026, 10, 29, 6, 0, tzinfo=TZ)))

    def test_by_day_is_last_night(self):
        s, _ = mm.night_window(datetime.datetime(2026, 10, 29, 14, 0, tzinfo=TZ))
        self.assertEqual(s, datetime.datetime(2026, 10, 28, 22, 0, tzinfo=TZ))

    def test_after_22(self):
        s, _ = mm.night_window(datetime.datetime(2026, 10, 29, 22, 5, tzinfo=TZ))
        self.assertEqual(s, datetime.datetime(2026, 10, 29, 22, 0, tzinfo=TZ))


class Lines(unittest.TestCase):
    STATE = {"canWait": ["movie/1", "tv/2"], "unreleased": {"movie/3": None}, "noRelease": {}, "searching": {"movie/4": {}},
             "pending": [], "imported": ["movie/5"], "queue": {"movie/1": {"state": "downloading"}, "tv/2": {"state": "downloading"}, "tv/6": {"state": "problem", "message": "x"}}}

    def test_request_state(self):
        t = mm.request_state_lines(self.STATE)
        self.assertIn('beefy_request_badges{state="can_wait"} 2', t)
        self.assertIn('beefy_request_badges{state="unreleased"} 1', t)
        self.assertIn('beefy_request_badges{state="no_release"} 0', t)
        self.assertIn('beefy_request_badges{state="searching"} 1', t)
        self.assertIn('beefy_downloads_queue{state="downloading"} 2', t)
        self.assertIn('beefy_downloads_queue{state="problem"} 1', t)
        self.assertIn('beefy_downloads_queue{state="paused"} 0', t)

    def test_seerr_counts(self):
        t = mm.seerr_lines({"total": 44, "movie": 34, "tv": 10, "pending": 0, "approved": 9, "declined": 0, "processing": 9, "available": 0, "completed": 35})
        self.assertIn('beefy_requests{state="total"} 44', t)
        self.assertIn('beefy_requests{state="processing"} 9', t)
        self.assertNotIn('state="movie"', t)

    def test_night(self):
        start = datetime.datetime(2026, 10, 28, 22, 0, tzinfo=TZ)
        end = start + datetime.timedelta(hours=8)
        hist = {"radarr": [{"date": "2026-10-28T21:30:00Z", "data": {"size": "1000"}}, {"date": "2026-10-28T20:59:00Z", "data": {"size": "5"}},
                           {"date": "2026-10-29T05:01:00Z", "data": {"size": "7"}}],
                "sonarr": [{"date": "2026-10-29T01:00:00Z", "data": {}}]}
        t = mm.night_lines({"pending": 3}, {"waiting": [1, 2]}, hist, start, end)
        self.assertIn('beefy_downloads_night_imported{app="radarr"} 1', t)
        self.assertIn('beefy_downloads_night_imported_bytes{app="radarr"} 1000', t)
        self.assertIn('beefy_downloads_night_imported{app="sonarr"} 1', t)
        self.assertIn('beefy_downloads_night_imported_bytes{app="sonarr"} 0', t)
        self.assertIn("beefy_downloads_night_pending 3", t)
        self.assertIn("beefy_downloads_night_waiting 2", t)
        self.assertIn(f"beefy_downloads_night_window_start_timestamp_seconds {int(start.timestamp())}", t)

    def test_retention(self):
        t = mm.retention_lines({"deleteEnabled": False, "summary": {"keep": 94, "temporary": 55, "leaving7d": 2, "next": "2027-01-23"}})
        self.assertIn('beefy_retention_titles{state="keep"} 94', t)
        self.assertIn('beefy_retention_titles{state="temporary"} 55', t)
        self.assertIn("beefy_retention_leaving_7d 2", t)
        self.assertIn("beefy_retention_delete_enabled 0", t)
        self.assertEqual(mm.retention_lines({"titles": {}}), "")

    def test_retention_bytes(self):
        t = mm.retention_lines({"summary": {"keep": 1, "temporary": 1, "leaving7d": 0, "keepBytes": 30, "temporaryBytes": 20, "leaving7dBytes": 5}})
        self.assertIn('beefy_retention_bytes{state="keep"} 30', t)
        self.assertIn('beefy_retention_bytes{state="temporary"} 20', t)
        self.assertIn("beefy_retention_leaving_7d_bytes 5", t)
        self.assertNotIn("beefy_retention_bytes", mm.retention_lines({"summary": {"keep": 1, "temporary": 1}}))

    def test_indexers(self):
        t = mm.indexer_lines({"ok": True, "generated": "2026-10-29T00:28:03Z", "indexers": [
            {"name": "Treasure Maps", "searches": {"used": 272, "limit": None}, "downloads": {"used": 13, "limit": 400}}]})
        self.assertIn('indexer_used{host="beefy",indexer="Treasure Maps",kind="downloads"} 13', t)
        self.assertIn('indexer_limit{host="beefy",indexer="Treasure Maps",kind="downloads"} 400', t)
        self.assertNotIn('indexer_limit{host="beefy",indexer="Treasure Maps",kind="searches"}', t)
        self.assertIn('indexer_bar_generated_timestamp_seconds{host="beefy"} 1793233683', t)


def mfile(res, size, hdr="", hdr_type=""):
    return {"size": size, "quality": {"quality": {"resolution": res}}, "mediaInfo": {"videoDynamicRange": hdr, "videoDynamicRangeType": hdr_type}}


class Library(unittest.TestCase):
    def test_quality_classes(self):
        self.assertEqual(mm.quality_class(mfile(2160, 1, "HDR", "DV HDR10")), "4k_hdr")
        self.assertEqual(mm.quality_class(mfile(2160, 1, "", "HLG")), "4k_hdr")
        self.assertEqual(mm.quality_class(mfile(2160, 1)), "4k_sdr")
        self.assertEqual(mm.quality_class(mfile(1080, 1, "HDR", "HDR10")), "1080p")
        self.assertEqual(mm.quality_class(mfile(720, 1)), "other")
        self.assertEqual(mm.quality_class({"size": 1}), "other")

    def test_bytes_titles_and_tiers(self):
        movies = [{"tmdbId": 1, "movieFile": mfile(2160, 100, "HDR", "HDR10")}, {"tmdbId": 2, "movieFile": mfile(2160, 40, "HDR", "HDR10")},
                  {"tmdbId": 3, "movieFile": mfile(1080, 10)}, {"tmdbId": 4}]
        series = [({"tvdbId": 9}, [mfile(1080, 30), mfile(1080, 30), mfile(2160, 20)]), ({"tvdbId": 8}, [mfile(480, 5)])]
        retention = {"movie/1": {"size": 100, "hddBytes": 100}, "movie/2": {"size": 40, "hddBytes": 0}, "tv/77": {"tvdb": 9, "size": 80, "hddBytes": 40}}
        t = mm.library_lines(movies, series, retention)
        self.assertIn('beefy_library_bytes{kind="movie",quality="4k_hdr",tier="hdd"} 100', t)
        self.assertIn('beefy_library_bytes{kind="movie",quality="4k_hdr",tier="ssd"} 40', t)
        self.assertIn('beefy_library_bytes{kind="movie",quality="1080p",tier="unknown"} 10', t)
        self.assertIn('beefy_library_bytes{kind="movie",quality="4k_sdr",tier="ssd"} 0', t)
        self.assertNotIn('beefy_library_bytes{kind="movie",quality="4k_sdr",tier="unknown"}', t)
        self.assertIn('beefy_library_titles{kind="movie",quality="4k_hdr"} 2', t)
        self.assertIn('beefy_library_bytes{kind="series",quality="1080p",tier="ssd"} 30', t)
        self.assertIn('beefy_library_bytes{kind="series",quality="1080p",tier="hdd"} 30', t)
        self.assertIn('beefy_library_bytes{kind="series",quality="4k_sdr",tier="hdd"} 10', t)
        self.assertIn('beefy_library_bytes{kind="series",quality="other",tier="unknown"} 5', t)
        self.assertIn('beefy_library_titles{kind="series",quality="1080p"} 1', t)
        self.assertIn('beefy_library_titles{kind="series",quality="4k_sdr"} 0', t)
        self.assertIn('beefy_library_files{kind="series",quality="1080p"} 2', t)

    def test_refreshed_at_most_hourly(self):
        calls = []

        def build():
            calls.append(1)
            return f"x {len(calls)}\n"
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d, "library.prom")
            self.assertEqual(mm.cached(cache, build, now=1000.0), ("x 1\n", True))
            self.assertEqual(mm.cached(cache, build, now=cache.stat().st_mtime + 3500), ("x 1\n", True))
            self.assertEqual(mm.cached(cache, build, now=cache.stat().st_mtime + 3700), ("x 2\n", True))

    def test_a_failed_refresh_keeps_the_old_figures(self):
        def boom():
            raise OSError("down")
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d, "library.prom")
            self.assertEqual(mm.cached(cache, boom, now=0.0), ("", False))
            cache.write_text("old\n")
            self.assertEqual(mm.cached(cache, boom, now=cache.stat().st_mtime + 7200), ("old\n", False))
            self.assertEqual(cache.read_text(), "old\n")

if __name__ == "__main__":
    unittest.main()
