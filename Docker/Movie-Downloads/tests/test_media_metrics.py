#!/usr/bin/env python3
"""scripts/media-metrics.py: request, download and indexer figures for fastpi's morning dashboard.
Usage: python3 tests/test_media_metrics.py"""
import datetime
import importlib.util
import pathlib
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

    def test_indexers(self):
        t = mm.indexer_lines({"ok": True, "generated": "2026-10-29T00:28:03Z", "indexers": [
            {"name": "Treasure Maps", "searches": {"used": 272, "limit": None}, "downloads": {"used": 13, "limit": 400}}]})
        self.assertIn('indexer_used{host="beefy",indexer="Treasure Maps",kind="downloads"} 13', t)
        self.assertIn('indexer_limit{host="beefy",indexer="Treasure Maps",kind="downloads"} 400', t)
        self.assertNotIn('indexer_limit{host="beefy",indexer="Treasure Maps",kind="searches"}', t)
        self.assertIn('indexer_bar_generated_timestamp_seconds{host="beefy"} 1793233683', t)


if __name__ == "__main__":
    unittest.main()
