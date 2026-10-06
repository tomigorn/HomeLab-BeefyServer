#!/usr/bin/env python3
"""Tests for beefy-promoter's decision logic.

The promotion itself is tier-move's job and is covered by test-tier-move. What is
tested here is everything around it: is this title actually cold, should it be
promoted again, and can a network caller make this service do something stupid.

The path tests matter most. /promote is reachable from the LAN and its argument is
handed to a tool that deletes files, so a traversal that escapes the pool would be
the worst bug in this repo.

Run: ./test_beefy_promoter.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest


def load(hot, cold, **env):
    os.environ.update({"HOT": hot, "COLD": cold, "POOL": "/srv/video",
                       "DRY_RUN": "1", "TIER_MOVE": "/bin/true"})
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location(
        "bp", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "beefy_promoter.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot = os.path.join(self.tmp, "hot")
        self.cold = os.path.join(self.tmp, "cold")
        os.makedirs(os.path.join(self.hot, "media/movies/HotFilm"))
        os.makedirs(os.path.join(self.cold, "media/movies/ColdFilm"))
        self.m = load(self.hot, self.cold)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPathSafety(Base):
    """A network caller must not be able to point this outside the pool."""

    def test_rejects_traversal(self):
        for bad in ("../../etc/passwd", "media/../../etc", "media/movies/../../..",
                    "/etc/passwd", "media/./movies", "", None, 42,
                    "media/movies/x\x00y"):
            self.assertIsNone(self.m.safe_relpath(bad), f"accepted {bad!r}")

    def test_rejects_outside_media_subdir(self):
        # torrents/ is seeding and usenet/ is mid-import; neither may be promoted.
        self.assertIsNone(self.m.safe_relpath("torrents/movies/X"))
        self.assertIsNone(self.m.safe_relpath("usenet/complete/movies/X"))

    def test_accepts_and_normalises_good_paths(self):
        self.assertEqual(self.m.safe_relpath("media/movies/Dune"), "media/movies/Dune")
        self.assertEqual(self.m.safe_relpath("/srv/video/media/movies/Dune"),
                         "media/movies/Dune")
        self.assertEqual(self.m.safe_relpath("media/movies/Dune/"), "media/movies/Dune")


class TestTierDetection(Base):
    def test_identifies_each_branch(self):
        self.assertEqual(self.m.tier_of("media/movies/HotFilm"), "hot")
        self.assertEqual(self.m.tier_of("media/movies/ColdFilm"), "cold")
        self.assertIsNone(self.m.tier_of("media/movies/Nonexistent"))

    def test_both_branches(self):
        os.makedirs(os.path.join(self.hot, "media/movies/ColdFilm"))
        self.assertEqual(self.m.tier_of("media/movies/ColdFilm"), "both")


class TestSubmitPolicy(Base):
    def test_cold_title_is_queued(self):
        ok, reason = self.m.PROMOTER.submit("media/movies/ColdFilm")
        self.assertTrue(ok)
        self.assertEqual(reason, "queued")

    def test_already_hot_is_refused(self):
        # The whole point is avoiding pointless QVO writes.
        ok, reason = self.m.PROMOTER.submit("media/movies/HotFilm")
        self.assertFalse(ok)
        self.assertEqual(reason, "already hot")

    def test_missing_is_refused(self):
        ok, reason = self.m.PROMOTER.submit("media/movies/Nope")
        self.assertFalse(ok)
        self.assertIn("not found", reason)

    def test_duplicate_submit_is_refused(self):
        self.m.PROMOTER.submit("media/movies/ColdFilm")
        ok, reason = self.m.PROMOTER.submit("media/movies/ColdFilm")
        self.assertFalse(ok)
        self.assertEqual(reason, "already queued")

    def test_recently_promoted_is_deduped(self):
        # A detail page gets opened repeatedly; one user action can fire several
        # webhooks. Promoting twice is wasted write endurance.
        self.m.PROMOTER.done_at["media/movies/ColdFilm"] = time.time()
        ok, reason = self.m.PROMOTER.submit("media/movies/ColdFilm")
        self.assertFalse(ok)
        self.assertIn("promoted", reason)

    def test_dedupe_expires(self):
        self.m.PROMOTER.done_at["media/movies/ColdFilm"] = time.time() - 99999
        ok, _ = self.m.PROMOTER.submit("media/movies/ColdFilm")
        self.assertTrue(ok)

    def test_rate_limit_stops_a_browsing_spree(self):
        now = time.time()
        for i in range(self.m.MAX_PER_HOUR):
            self.m.PROMOTER.recent.append((now, f"media/movies/X{i}", "promoted"))
        ok, reason = self.m.PROMOTER.submit("media/movies/ColdFilm")
        self.assertFalse(ok)
        self.assertIn("rate limited", reason)

    def test_rate_limit_only_counts_the_last_hour(self):
        old = time.time() - 7200
        for i in range(self.m.MAX_PER_HOUR * 2):
            self.m.PROMOTER.recent.append((old, f"media/movies/X{i}", "promoted"))
        ok, _ = self.m.PROMOTER.submit("media/movies/ColdFilm")
        self.assertTrue(ok)

    def test_refusals_do_not_count_toward_the_rate_limit(self):
        now = time.time()
        for i in range(self.m.MAX_PER_HOUR * 2):
            self.m.PROMOTER.recent.append((now, f"media/movies/X{i}", "refused"))
        ok, _ = self.m.PROMOTER.submit("media/movies/ColdFilm")
        self.assertTrue(ok)


class TestJellyfinPayload(Base):
    """Jellyfin reports CONTAINER paths; the pool is mounted at /media there."""

    def test_explicit_path_wins(self):
        self.assertEqual(self.m.extract_path({"path": "media/movies/Dune"}),
                         "media/movies/Dune")

    def test_container_path_is_rewritten_and_folder_taken(self):
        got = self.m.extract_path({"Path": "/media/movies/Dune/Dune.mkv"})
        self.assertEqual(got, "media/movies/Dune")

    def test_folder_payload_kept_as_is(self):
        self.assertEqual(self.m.extract_path({"Path": "/media/movies/Dune"}),
                         "media/movies/Dune")

    def test_unusable_payload(self):
        self.assertIsNone(self.m.extract_path({"Name": "Dune"}))
        self.assertIsNone(self.m.extract_path("not a dict"))

    def test_end_to_end_rejects_hostile_jellyfin_payload(self):
        raw = self.m.extract_path({"Path": "/media/../../../etc/shadow"})
        self.assertIsNone(self.m.safe_relpath(raw) if raw else None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
