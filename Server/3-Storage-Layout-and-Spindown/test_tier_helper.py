#!/usr/bin/env python3
"""Tests for tier_helper.py: the one place a media file is copied, verified and deleted.
Run: ./test_tier_helper.py"""
import importlib.util
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("th", HERE / "tier_helper.py")
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)
REL = "media/movies/Dune (2021)/Dune.mkv"


class Move(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hot, self.cold = self.tmp / "hot", self.tmp / "cold"
        self.cold.mkdir()
        self.src = self.hot / REL
        self.src.parent.mkdir(parents=True)
        os.chmod(self.hot / "media/movies", 0o2775)
        os.chmod(self.src.parent, 0o2770)
        self.src.write_bytes(os.urandom(300000))
        os.chmod(self.src, 0o664)
        os.utime(self.src, (1700000000, 1700000000))
        self.data = self.src.read_bytes()
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def move(self):
        return th.move(self.hot, self.cold, REL, log=self.logs.append)

    def test_moves_and_keeps_mode_times_content_and_dir_modes(self):
        self.assertEqual(self.move(), 0)
        out = self.cold / REL
        self.assertFalse(self.src.exists())
        self.assertEqual(out.read_bytes(), self.data)
        st = out.stat()
        self.assertEqual((oct(st.st_mode & 0o7777), int(st.st_mtime)), ("0o664", 1700000000))
        self.assertEqual(oct((self.cold / "media/movies").stat().st_mode & 0o7777), "0o2775")
        self.assertEqual(oct(out.parent.stat().st_mode & 0o7777), "0o2770")
        self.assertEqual(list((self.cold / th.STAGING).rglob("*.mkv")), [])

    def test_verify_failure_keeps_source_and_leaves_no_copy(self):
        orig = th.same_on_disk
        th.same_on_disk = lambda a, b: False
        try:
            self.assertEqual(self.move(), 1)
        finally:
            th.same_on_disk = orig
        self.assertEqual(self.src.read_bytes(), self.data)
        self.assertFalse((self.cold / REL).exists())
        self.assertEqual(list((self.cold / th.STAGING).rglob("*.mkv")), [])
        self.assertIn("VERIFY FAILED", self.logs[-1])

    def test_compare_really_compares(self):
        other = self.tmp / "o.mkv"
        other.write_bytes(self.data[:-1] + bytes([self.data[-1] ^ 1]))
        self.assertFalse(th.same_on_disk(self.src, other))
        other.write_bytes(self.data)
        self.assertTrue(th.same_on_disk(self.src, other))
        other.write_bytes(self.data + b"x")
        self.assertFalse(th.same_on_disk(self.src, other))

    def run_with(self, during):
        real = subprocess.run

        def fake(cmd, **kw):
            r = real(cmd, **kw)
            during()
            return r
        th.subprocess.run = fake
        try:
            return self.move()
        finally:
            th.subprocess.run = real

    def test_source_edited_in_place_during_copy_is_not_deleted(self):
        def edit():
            with open(self.src, "r+b") as f:
                f.write(b"EDIT")
            os.utime(self.src, (1700000000, 1700000000))
        self.assertEqual(self.run_with(edit), 4)
        self.assertTrue(self.src.read_bytes().startswith(b"EDIT"))
        self.assertFalse((self.cold / REL).exists())

    def test_source_replaced_during_copy(self):
        def replace():
            tmp = self.src.with_suffix(".new")
            tmp.write_bytes(self.data)
            os.replace(tmp, self.src)
        self.assertEqual(self.run_with(replace), 4)
        self.assertTrue(self.src.exists())
        self.assertFalse((self.cold / REL).exists())

    def test_source_deleted_during_copy_does_not_resurrect(self):
        self.assertEqual(self.run_with(lambda: self.src.unlink()), 4)
        self.assertFalse((self.cold / REL).exists())
        self.assertEqual(list((self.cold / th.STAGING).rglob("*.mkv")), [])

    def test_copy_failure(self):
        real = th.subprocess.run
        th.subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 23)
        try:
            self.assertEqual(self.move(), 1)
        finally:
            th.subprocess.run = real
        self.assertTrue(self.src.exists())
        self.assertFalse((self.cold / REL).exists())

    def test_existing_duplicate_on_the_destination_is_replaced(self):
        dup = self.cold / REL
        dup.parent.mkdir(parents=True)
        dup.write_bytes(b"stale half copy")
        self.assertEqual(self.move(), 0)
        self.assertEqual(dup.read_bytes(), self.data)


    def test_destination_dir_is_fsynced_before_the_source_is_deleted(self):
        events, real_fsync, real_unlink = [], th.os.fsync, th.os.unlink

        def fsync(fd):
            events.append(("fsync", os.readlink(f"/proc/self/fd/{fd}")))
            return real_fsync(fd)

        def unlink(path, *a, **kw):
            events.append(("unlink", str(path)))
            return real_unlink(path, *a, **kw)
        th.os.fsync, th.os.unlink = fsync, unlink
        try:
            self.assertEqual(self.move(), 0)
        finally:
            th.os.fsync, th.os.unlink = real_fsync, real_unlink
        out_dir = str((self.cold / REL).parent)
        gone = events.index(("unlink", str(self.src)))
        self.assertIn(("fsync", out_dir), events[:gone])
        self.assertIn(("fsync", str(self.cold)), events[:gone])


class Recover(unittest.TestCase):
    """Leftovers in <dst>/.tier-staging after a crash: never delete the only copy."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hot, self.hot2, self.cold = self.tmp / "hot", self.tmp / "hot2", self.tmp / "cold"
        for b in (self.hot, self.hot2, self.cold):
            b.mkdir()
        self.staged = self.cold / th.STAGING / REL
        self.staged.parent.mkdir(parents=True)
        self.data = os.urandom(5000)
        self.staged.write_bytes(self.data)
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def recover(self):
        return th.recover(self.cold, [self.hot, self.hot2], log=self.logs.append)

    def test_power_cut_after_source_delete_puts_the_only_copy_in_place(self):
        (self.hot / "media/movies/Dune (2021)").mkdir(parents=True)
        os.chmod(self.hot / "media/movies/Dune (2021)", 0o2770)
        self.assertEqual(self.recover(), 0)
        out = self.cold / REL
        self.assertEqual(out.read_bytes(), self.data)
        self.assertFalse(self.staged.exists())
        self.assertEqual(oct(out.parent.stat().st_mode & 0o7777), "0o2770")
        self.assertIn("RECOVERED", " ".join(self.logs))

    def test_recovers_without_any_source_dir_left(self):
        self.assertEqual(self.recover(), 0)
        self.assertEqual((self.cold / REL).read_bytes(), self.data)

    def test_leftover_whose_original_still_exists_is_removed(self):
        src = self.hot2 / REL
        src.parent.mkdir(parents=True)
        src.write_bytes(b"original")
        self.assertEqual(self.recover(), 0)
        self.assertFalse(self.staged.exists())
        self.assertFalse((self.cold / REL).exists())
        self.assertEqual(src.read_bytes(), b"original")

    def test_partial_rsync_temp_with_original_is_removed(self):
        tmpname = self.staged.parent / ".Dune.mkv.Ab12Cd"
        self.staged.rename(tmpname)
        src = self.hot / REL
        src.parent.mkdir(parents=True)
        src.write_bytes(b"original")
        self.assertEqual(self.recover(), 0)
        self.assertFalse(tmpname.exists())

    def test_partial_rsync_temp_without_original_is_kept_aside(self):
        tmpname = self.staged.parent / ".Dune.mkv.Ab12Cd"
        self.staged.rename(tmpname)
        self.assertEqual(self.recover(), 1)
        kept = self.cold / th.RECOVERED / pathlib.PurePosixPath(REL).parent / ".Dune.mkv.Ab12Cd"
        self.assertEqual(kept.read_bytes(), self.data)
        self.assertFalse((self.cold / REL).exists())

    def test_identical_file_already_in_place_drops_the_leftover(self):
        out = self.cold / REL
        out.parent.mkdir(parents=True)
        out.write_bytes(self.data)
        self.assertEqual(self.recover(), 0)
        self.assertFalse(self.staged.exists())
        self.assertEqual(out.read_bytes(), self.data)

    def test_different_file_in_place_keeps_both(self):
        out = self.cold / REL
        out.parent.mkdir(parents=True)
        out.write_bytes(b"other")
        self.assertEqual(self.recover(), 1)
        self.assertEqual(out.read_bytes(), b"other")
        self.assertEqual((self.cold / th.RECOVERED / REL).read_bytes(), self.data)
        self.assertIn("KEPT", " ".join(self.logs))

    def test_no_staging_dir_is_fine(self):
        shutil.rmtree(self.cold / th.STAGING)
        self.assertEqual(self.recover(), 0)

    def test_empty_staging_dirs_are_pruned(self):
        self.assertEqual(self.recover(), 0)
        self.assertEqual([p for p in (self.cold / th.STAGING).rglob("*")], [])

if __name__ == "__main__":
    unittest.main()
