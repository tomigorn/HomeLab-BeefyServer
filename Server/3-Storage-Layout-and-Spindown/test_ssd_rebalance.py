#!/usr/bin/env python3
"""Tests for ssd_rebalance.py (one-off SSD -> SSD rebalance of whole titles with their hardlinks).
Run: ./test_ssd_rebalance.py
"""
import datetime as dt
import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rb", HERE / "ssd_rebalance.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

TZ = ZoneInfo("Europe/Zurich")
GB = 10 ** 9


class Units(unittest.TestCase):
    def test_movie_unit_is_the_title_folder(self):
        self.assertEqual(rb.unit_of("media/movies/Dune (2021)/Dune.mkv"), "media/movies/Dune (2021)")
        self.assertEqual(rb.unit_of("media/movies/Dune (2021)/extras/x.mkv"), "media/movies/Dune (2021)")

    def test_tv_unit_is_the_season_and_series_level_files_their_own_unit(self):
        self.assertEqual(rb.unit_of("media/tv/Show/Season 01/e1.mkv"), "media/tv/Show/Season 01")
        self.assertEqual(rb.unit_of("media/tv/Show/tvshow.nfo"), "media/tv/Show")

    def test_files_outside_titles_are_no_unit(self):
        self.assertIsNone(rb.unit_of("media/movies/stray.mkv"))
        self.assertIsNone(rb.unit_of("torrents/movies/x/x.mkv"))
        self.assertIsNone(rb.unit_of("media/music/a/b.flac"))


class Plan(unittest.TestCase):
    def test_need_is_half_the_difference(self):
        self.assertEqual(rb.need(800, 200), 300)
        self.assertEqual(rb.need(200, 800), 0)

    def test_pick_takes_the_largest_unit_that_fits(self):
        sizes = {"a": 50, "b": 120, "c": 90}
        self.assertEqual(rb.pick(sizes, need=100, tol=10, room=1000), "c")

    def test_pick_may_overshoot_by_the_tolerance(self):
        self.assertEqual(rb.pick({"a": 105}, need=100, tol=10, room=1000), "a")

    def test_pick_stops_when_within_tolerance(self):
        self.assertIsNone(rb.pick({"a": 5}, need=10, tol=10, room=1000))

    def test_pick_never_fills_the_target_past_its_room(self):
        self.assertIsNone(rb.pick({"a": 90}, need=100, tol=10, room=80))
        self.assertEqual(rb.pick({"a": 90, "b": 70}, need=100, tol=10, room=80), "b")

    def test_pick_skips_empty_units(self):
        self.assertIsNone(rb.pick({"a": 0}, need=100, tol=10, room=1000))


class Window(unittest.TestCase):
    def at(self, h, m):
        return dt.datetime(2026, 11, 17, h, m, tzinfo=TZ)

    def test_night_window_pauses(self):
        self.assertTrue(rb.in_pause_window(self.at(3, 50)))
        self.assertTrue(rb.in_pause_window(self.at(4, 49)))
        self.assertFalse(rb.in_pause_window(self.at(4, 50)))
        self.assertFalse(rb.in_pause_window(self.at(3, 49)))

    def test_no_start_that_cannot_finish_before_the_window(self):
        rate = 100 * 10 ** 6
        self.assertTrue(rb.may_start(self.at(3, 30), 60 * rate, rate))
        self.assertFalse(rb.may_start(self.at(3, 45), 600 * rate, rate))
        self.assertTrue(rb.may_start(self.at(5, 0), 6000 * rate, rate))

    def test_streaming_means_a_session_plays_something(self):
        self.assertFalse(rb.streaming([]))
        self.assertFalse(rb.streaming([{"UserName": "a"}]))
        self.assertTrue(rb.streaming([{"UserName": "a"}, {"NowPlayingItem": {"Name": "x"}}]))


class Branches(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.src, self.dst = self.tmp / "ssd-hot", self.tmp / "ssd-hot2"
        self.dst.mkdir()
        self.journal = self.tmp / "journal.json"
        self.logs = []
        old = 1700000000
        self.movie = "media/movies/Dune (2021)/Dune.mkv"
        self.seed = "torrents/movies/Dune.2021/Dune.mkv"
        self.nfo = "media/movies/Dune (2021)/movie.nfo"
        for rel in (self.movie, self.seed, self.nfo):
            (self.src / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.src / self.movie).write_bytes(os.urandom(200000))
        os.link(self.src / self.movie, self.src / self.seed)
        (self.src / self.nfo).write_text("<movie/>")
        for rel in (self.movie, self.nfo):
            os.utime(self.src / rel, (old, old))
        self.data = (self.src / self.movie).read_bytes()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def mover(self, **kw):
        return rb.Mover(self.src, self.dst, self.journal, log=self.logs.append, **kw)

    def test_scan_groups_hardlinks_with_the_unit_and_counts_bytes_once(self):
        units = rb.scan(self.src)
        u = units["media/movies/Dune (2021)"]
        self.assertEqual(sorted(sorted(g) for g in u.groups), sorted([sorted([self.movie, self.seed]), [self.nfo]]))
        self.assertEqual(u.size, 200000 + len("<movie/>"))
        self.assertTrue(u.complete)

    def test_scan_marks_a_unit_incomplete_when_a_link_lives_elsewhere(self):
        os.link(self.src / self.movie, self.tmp / "outside.mkv")
        self.assertFalse(rb.scan(self.src)["media/movies/Dune (2021)"].complete)

    def test_moves_a_unit_with_its_hardlinks_kept_together(self):
        u = rb.scan(self.src)["media/movies/Dune (2021)"]
        self.assertEqual(self.mover().move_unit(u), "moved")
        a, b = self.dst / self.movie, self.dst / self.seed
        self.assertEqual(a.read_bytes(), self.data)
        self.assertTrue(os.path.samefile(a, b))
        self.assertEqual(a.stat().st_nlink, 2)
        self.assertEqual(int(a.stat().st_mtime), 1700000000)
        self.assertEqual((self.dst / self.nfo).read_text(), "<movie/>")
        self.assertFalse((self.src / self.movie).exists())
        self.assertFalse((self.src / self.seed).exists())
        self.assertFalse((self.src / "media/movies/Dune (2021)").exists())
        self.assertTrue((self.src / "torrents/movies").is_dir())
        self.assertFalse((self.dst / rb.STAGING).exists() and any((self.dst / rb.STAGING).rglob("*")))
        self.assertFalse(self.journal.exists())

    def test_skips_when_the_target_already_has_a_file_there(self):
        (self.dst / self.nfo).parent.mkdir(parents=True)
        (self.dst / self.nfo).write_text("other")
        u = rb.scan(self.src)["media/movies/Dune (2021)"]
        self.assertEqual(self.mover().move_unit(u), "skip")
        self.assertEqual((self.src / self.movie).read_bytes(), self.data)
        self.assertFalse((self.dst / self.movie).exists())

    def test_skips_open_files(self):
        u = rb.scan(self.src)["media/movies/Dune (2021)"]
        self.assertEqual(self.mover(is_open=lambda p: p.name == "Dune.mkv").move_unit(u), "skip")
        self.assertTrue((self.src / self.movie).exists())

    def test_skips_recently_changed_units(self):
        u = rb.scan(self.src)["media/movies/Dune (2021)"]
        os.utime(self.src / self.nfo)
        u = rb.scan(self.src)["media/movies/Dune (2021)"]
        self.assertEqual(self.mover().move_unit(u), "skip")

    def test_a_link_added_during_the_move_rolls_back(self):
        u = rb.scan(self.src)["media/movies/Dune (2021)"]
        m = self.mover()
        m.before_place = lambda: os.link(self.src / self.movie, self.src / "torrents/movies/Dune.2021/again.mkv")
        self.assertEqual(m.move_unit(u), "changed")
        self.assertEqual((self.src / self.movie).read_bytes(), self.data)
        self.assertEqual((self.src / self.movie).stat().st_nlink, 3)
        self.assertFalse((self.dst / self.movie).exists())
        self.assertFalse(self.journal.exists())

    def test_recover_after_a_crash_while_placing_removes_the_copies(self):
        (self.dst / self.movie).parent.mkdir(parents=True)
        shutil.copy2(self.src / self.movie, self.dst / self.movie)
        self.journal.write_text(json.dumps({"phase": "place", "rels": [self.movie, self.seed]}))
        self.mover().recover()
        self.assertFalse((self.dst / self.movie).exists())
        self.assertEqual((self.src / self.movie).read_bytes(), self.data)
        self.assertFalse(self.journal.exists())

    def test_recover_after_a_crash_while_deleting_finishes_the_delete(self):
        for rel in (self.movie, self.seed):
            (self.dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.src / self.movie, self.dst / self.movie)
        os.link(self.dst / self.movie, self.dst / self.seed)
        os.unlink(self.src / self.seed)
        self.journal.write_text(json.dumps({"phase": "delete", "rels": [self.movie, self.seed]}))
        self.mover().recover()
        self.assertFalse((self.src / self.movie).exists())
        self.assertEqual((self.dst / self.movie).read_bytes(), self.data)
        self.assertEqual((self.dst / self.movie).stat().st_nlink, 2)
        self.assertFalse(self.journal.exists())


class KeepAwake(unittest.TestCase):
    def test_own_holder_file_with_the_dir_made(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "beefy-keep-awake.d" / "ssd-rebalance"
            with rb.keep_awake(f):
                self.assertTrue(f.exists())
            self.assertFalse(f.exists())

    def test_default_is_its_own_holder_file(self):
        src = (HERE / "ssd_rebalance.py").read_text()
        self.assertIn('"--keep-awake", default="/run/beefy-keep-awake.d/ssd-rebalance"', src)


if __name__ == "__main__":
    unittest.main()
