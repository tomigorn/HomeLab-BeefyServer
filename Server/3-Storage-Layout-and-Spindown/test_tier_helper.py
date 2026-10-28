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


if __name__ == "__main__":
    unittest.main()
