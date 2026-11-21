#!/usr/bin/env python3
"""scripts/recycle-bin.py: the 7-day recycle bin cleanup counted from the move into the bin (README "Recycle bin"), on a throw-away bin with invented files.
Usage: python3 tests/test_recycle_bin.py"""
import importlib.util
import io
import os
import pathlib
import tempfile
import time
import unittest
from contextlib import redirect_stdout

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("recycle_bin", HERE.parent / "scripts" / "recycle-bin.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

DAY = 86400


class Stat:
    def __init__(self, mtime, ctime):
        self.st_mtime, self.st_ctime = mtime, ctime


class Rule(unittest.TestCase):
    def test_age_counts_from_the_move_not_from_the_file_date(self):
        now = 100 * DAY
        # a file dated years ago, moved into the bin a day ago: the move (ctime) counts
        self.assertEqual(rb.moved_at(Stat(1 * DAY, 99 * DAY)), 99 * DAY)
        self.assertFalse(rb.expired(Stat(1 * DAY, 99 * DAY), now))
        self.assertFalse(rb.expired(Stat(1 * DAY, now - 7 * DAY + 60), now))
        self.assertTrue(rb.expired(Stat(1 * DAY, now - 7 * DAY), now))

    def test_a_newer_file_date_than_the_move_keeps_it_longer(self):
        now = 100 * DAY
        self.assertFalse(rb.expired(Stat(95 * DAY, 80 * DAY), now))
        self.assertTrue(rb.expired(Stat(93 * DAY, 80 * DAY), now))


class Sweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = pathlib.Path(self.tmp.name, ".recyclebin")
        self.bin.mkdir()
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, size=10):
        p = self.bin / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        # an old file date, as an arr leaves it when its touch is refused
        os.utime(p, (time.time() - 400 * DAY, time.time() - 400 * DAY))
        return p

    def sweep(self, now, dry=False):
        return rb.sweep(self.bin, now, dry=dry, log=self.logs.append)

    def test_a_file_moved_just_now_stays_7_days_even_with_an_old_file_date(self):
        f = self.put("Some Film (2001) [tmdbid-1]/Some Film (2001).mkv")
        now = time.time()
        r = self.sweep(now + 7 * DAY - 120)
        self.assertTrue(f.exists())
        self.assertEqual((len(r.deleted), len(r.kept)), (0, 1))
        r = self.sweep(now + 7 * DAY + 60)
        self.assertFalse(f.exists())
        self.assertEqual([p for p, _ in r.deleted], [f])

    def test_dry_run_deletes_nothing_and_lists_every_file(self):
        f = self.put("Some Show (2020) [tvdbid-2]/Season 01/Some Show - S01E01.mkv")
        r = self.sweep(time.time() + 30 * DAY, dry=True)
        self.assertTrue(f.exists())
        self.assertEqual(len(r.deleted), 1)
        self.assertTrue(any("would delete" in l and "S01E01" in l for l in self.logs))

    def test_empty_folders_go_once_a_day_old_the_bin_itself_stays(self):
        self.put("Gone Film (1999) [tmdbid-3]/Gone Film (1999).mkv")
        fresh = self.bin / "Fresh Film (2002) [tmdbid-4]"
        fresh.mkdir()
        # an arr creates the folder right before it moves a file in: a new empty folder stays
        self.sweep(time.time())
        self.assertTrue(fresh.exists())
        self.sweep(time.time() + 8 * DAY)
        self.assertFalse((self.bin / "Gone Film (1999) [tmdbid-3]").exists())
        self.assertFalse(fresh.exists())
        self.assertTrue(self.bin.is_dir())

    def test_a_symlink_is_removed_never_followed(self):
        outside = pathlib.Path(self.tmp.name, "library.mkv")
        outside.write_bytes(b"keep me")
        link = self.bin / "Linked (2000)" / "link.mkv"
        link.parent.mkdir()
        link.symlink_to(outside)
        self.sweep(time.time() + 8 * DAY)
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(outside.read_bytes(), b"keep me")

    def test_an_undeletable_file_is_reported_and_the_rest_goes_on(self):
        a = self.put("A (2000)/a.mkv")
        b = self.put("B (2000)/b.mkv")
        real = os.unlink

        def unlink(p, *args, **kw):
            if str(p) == str(a):
                raise PermissionError(13, "denied")
            return real(p, *args, **kw)
        rb.os.unlink = unlink
        try:
            r = self.sweep(time.time() + 8 * DAY)
        finally:
            rb.os.unlink = real
        self.assertTrue(a.exists())
        self.assertFalse(b.exists())
        self.assertEqual(len(r.errors), 1)


class Guards(unittest.TestCase):
    def test_only_a_folder_named_recyclebin_on_a_mounted_pool(self):
        with tempfile.TemporaryDirectory() as d:
            other = pathlib.Path(d, "media")
            other.mkdir()
            self.assertIn(".recyclebin", rb.refusal(other, mounted=lambda p: True, clock="yes"))
            binp = pathlib.Path(d, ".recyclebin")
            binp.mkdir()
            self.assertIn("not mounted", rb.refusal(binp, mounted=lambda p: False, clock="yes"))
            self.assertIsNone(rb.refusal(binp, mounted=lambda p: True, clock="yes"))
            link = pathlib.Path(d, "x", ".recyclebin")
            link.parent.mkdir()
            link.symlink_to(binp)
            self.assertIn("symlink", rb.refusal(link, mounted=lambda p: True, clock="yes"))

    def test_an_unsynced_clock_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            binp = pathlib.Path(d, ".recyclebin")
            binp.mkdir()
            self.assertIn("NTP", rb.refusal(binp, mounted=lambda p: True, clock="no"))
            self.assertIn("NTP", rb.refusal(binp, mounted=lambda p: True, clock=None))

    def test_main_refuses_with_a_hint_and_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with redirect_stdout(out):
                code = rb.main(["--bin", str(pathlib.Path(d, "nope"))])
            self.assertEqual(code, 1)
            self.assertIn("nothing deleted", out.getvalue())
            self.assertIn("recycle-bin.py --dry-run", out.getvalue())


class Units(unittest.TestCase):
    def test_nightly_in_a_free_window_slot_sandboxed_writing_only_the_bin(self):
        d = HERE.parent / "systemd"
        timer = (d / "recycle-bin.timer").read_text()
        self.assertIn("OnCalendar=*-*-* 04:37:00 Europe/Zurich", timer)
        self.assertIn("Persistent=false", timer)
        svc = (d / "recycle-bin.service").read_text()
        self.assertIn("ReadWritePaths=/srv/video/.recyclebin\n", svc)
        self.assertIn("User=buntu", svc)
        self.assertIn("ProtectSystem=strict", svc)


if __name__ == "__main__":
    unittest.main()
