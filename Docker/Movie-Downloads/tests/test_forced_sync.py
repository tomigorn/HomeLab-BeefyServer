#!/usr/bin/env python3
"""scripts/forced-sync.py: offset-only sync of forced subtitles against the full subtitle of the same title, with generated fixtures.
Usage: python3 tests/test_forced_sync.py"""
import importlib.util
import json
import os
import pathlib
import random
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fs", ROOT / "scripts" / "forced-sync.py")
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)


def stamp(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt(cues, crlf=False):
    text = "".join(f"{i}\n{stamp(s)} --> {stamp(e)}\n{t}\n\n" for i, (s, e, t) in enumerate(cues, 1))
    return (text.replace("\n", "\r\n") if crlf else text).encode()


def full_cues(n=400, seed=1):
    rnd, t, out = random.Random(seed), 20.0, []
    for i in range(n):
        d = rnd.uniform(1.2, 4.0)
        out.append((t, t + d, f"line {i}"))
        t += d + rnd.uniform(0.5, 9.0)
    return out


FULL = full_cues()
PICK = FULL[10::37]


def forced_cues(shift=0.0, part=None):
    out = []
    for i, (s, e, t) in enumerate(PICK):
        sh = shift if part is None else part(i)
        out.append((s + sh, e + sh, f"forced {i}"))
    return out


class Measure(unittest.TestCase):
    def test_shift_of_165_is_found(self):
        r = fs.measure(forced_cues(1.65), FULL)
        self.assertEqual(r.status, "offset")
        self.assertAlmostEqual(r.offset, -1.65, delta=0.1)

    def test_in_sync_is_reported_as_zero(self):
        self.assertEqual(fs.measure(forced_cues(0.0), FULL).status, "in-sync")

    def test_negative_shift(self):
        self.assertAlmostEqual(fs.measure(forced_cues(-3.7), FULL).offset, 3.7, delta=0.1)

    def test_thirds_that_disagree_are_unverified(self):
        n = len(PICK)
        r = fs.measure(forced_cues(part=lambda i: 1.65 if i < n // 2 else -2.0), FULL)
        self.assertEqual(r.status, "unverified")
        self.assertIn("disagree", r.reason)

    def test_too_few_cues(self):
        r = fs.measure(forced_cues(1.65)[:4], FULL)
        self.assertEqual((r.status, "fewer" in r.reason), ("unverified", True))

    def test_offset_over_30_seconds(self):
        r = fs.measure(forced_cues(45.0), FULL)
        self.assertEqual(r.status, "unverified")

    def test_unrelated_text_is_unverified(self):
        other = full_cues(300, seed=99)[5::31]
        self.assertEqual(fs.measure([(s, e, "x") for s, e, _ in other], FULL).status, "unverified")


class Shift(unittest.TestCase):
    def test_timestamps_move_and_the_rest_is_kept(self):
        data = srt([(10.0, 12.5, "Hello\nthere"), (20.0, 21.0, "<i>Bye</i>")], crlf=True)
        out = fs.shift_srt(data, -1.65)
        self.assertEqual(out, srt([(8.35, 10.85, "Hello\nthere"), (18.35, 19.35, "<i>Bye</i>")], crlf=True))

    def test_bom_is_kept_and_start_never_below_zero(self):
        out = fs.shift_srt(b"\xef\xbb\xbf" + srt([(1.0, 3.0, "a")]), -2.0)
        self.assertTrue(out.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"00:00:00,000 --> 00:00:01,000", out)


class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.branch = self.tmp / "ssd" / "media"
        self.merged = self.tmp / "merged" / "media"
        self.state = self.tmp / "state"
        self.d = "movies/Film (2020)"
        for root in (self.branch, self.merged):
            (root / self.d).mkdir(parents=True)
        self.cfg = fs.Config(self.state, log_name="forced-sync.log")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def put(self, name, data):
        for root in (self.branch, self.merged):
            (root / self.d / name).write_bytes(data)
        return self.merged / self.d / name

    def run_job(self, **kw):
        return fs.scan([(self.branch, self.merged)], self.cfg, **kw)

    def events(self):
        return sorted(json.loads(p.read_text())["kind"] for p in (self.state / "events").glob("*.json")) if (self.state / "events").exists() else []

    def test_forced_is_shifted_back_with_backup_event_and_state(self):
        self.put("Film.en.srt", srt(FULL))
        forced = self.put("Film.en.forced.srt", srt(forced_cues(1.65)))
        res = self.run_job()
        self.assertEqual((res["synced"], res["unverified"]), (1, 0))
        self.assertEqual(fs.measure(fs.parse_srt(forced.read_bytes()), FULL).status, "in-sync")
        backups = list((self.state / "backup").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), srt(forced_cues(1.65)))
        self.assertEqual(self.events(), ["subtitle_forced_synced"])
        self.assertTrue((self.state / "forced-sync-state.json").exists())

    def test_second_run_changes_and_reports_nothing(self):
        self.put("Film.en.srt", srt(FULL))
        forced = self.put("Film.en.forced.srt", srt(forced_cues(1.65)))
        self.run_job()
        before = forced.read_bytes()
        n_events = len(self.events())
        res = self.run_job()
        self.assertEqual((res["synced"], res["checked"]), (0, 0))
        self.assertEqual(forced.read_bytes(), before)
        self.assertEqual(len(self.events()), n_events)
        self.assertEqual(len(list((self.state / "backup").iterdir())), 1)

    def test_new_reference_makes_an_unverified_file_retried(self):
        forced = self.put("Film.en.forced.srt", srt(forced_cues(1.65)))
        self.assertEqual(self.run_job()["unverified"], 1)
        self.assertEqual(self.run_job()["checked"], 0)
        self.put("Film.en.srt", srt(FULL))
        self.assertEqual(self.run_job()["synced"], 1)
        self.assertNotEqual(forced.read_bytes(), srt(forced_cues(1.65)))

    def test_unverified_is_left_alone_and_reported_once(self):
        n = len(PICK)
        data = srt(forced_cues(part=lambda i: 1.65 if i < n // 2 else -2.0))
        self.put("Film.en.srt", srt(FULL))
        forced = self.put("Film.en.forced.srt", data)
        self.run_job()
        self.run_job()
        self.assertEqual(forced.read_bytes(), data)
        self.assertEqual(self.events(), ["subtitle_forced_unverified"])
        self.assertFalse((self.state / "backup").exists())

    def test_in_sync_file_is_untouched_and_silent(self):
        self.put("Film.en.srt", srt(FULL))
        data = srt(forced_cues(0.0))
        forced = self.put("Film.en.forced.srt", data)
        res = self.run_job()
        self.assertEqual((res["in_sync"], forced.read_bytes(), self.events()), (1, data, []))

    def test_reference_falls_back_to_english_then_german(self):
        self.put("Film.de.srt", srt(FULL))
        forced = self.put("Film.fr.forced.srt", srt(forced_cues(1.65)))
        self.assertEqual(self.run_job()["synced"], 1)
        self.assertEqual(fs.measure(fs.parse_srt(forced.read_bytes()), FULL).status, "in-sync")

    def test_no_reference_is_unverified(self):
        self.put("Film.en.forced.srt", srt(forced_cues(1.65)))
        self.assertEqual(self.run_job()["unverified"], 1)

    def test_other_title_in_the_same_folder_is_not_a_reference(self):
        self.put("Other.en.srt", srt(FULL))
        self.put("Film.en.forced.srt", srt(forced_cues(1.65)))
        self.assertEqual(self.run_job()["unverified"], 1)

    def test_forced_file_is_not_its_own_reference_and_hi_full_counts(self):
        self.put("Film.en.hi.srt", srt(FULL))
        self.put("Film.en.forced.srt", srt(forced_cues(1.65)))
        self.assertEqual(self.run_job()["synced"], 1)

    def test_dry_run_writes_nothing(self):
        self.put("Film.en.srt", srt(FULL))
        data = srt(forced_cues(1.65))
        forced = self.put("Film.en.forced.srt", data)
        self.cfg.dry_run = True
        self.run_job()
        self.assertEqual((forced.read_bytes(), self.state.exists()), (data, False))

    def test_written_through_the_merged_path_only(self):
        self.put("Film.en.srt", srt(FULL))
        data = srt(forced_cues(1.65))
        self.put("Film.en.forced.srt", data)
        self.run_job()
        self.assertEqual((self.branch / self.d / "Film.en.forced.srt").read_bytes(), data)
        self.assertNotEqual((self.merged / self.d / "Film.en.forced.srt").read_bytes(), data)

    def test_main_scan_entry_point(self):
        self.put("Film.en.srt", srt(FULL))
        self.put("Film.en.forced.srt", srt(forced_cues(1.65)))
        rc = fs.main(["--scan", f"{self.branch}={self.merged}"], env={"FS_STATE": str(self.state)})
        self.assertEqual(rc, 0)
        self.assertIn("1 synced", (self.state / "forced-sync.log").read_text())

    def test_status_file_for_the_post_update_check(self):
        self.run_job()
        st = json.loads((self.state / "forced-sync-status.json").read_text())
        self.assertIn("last_run", st)


if __name__ == "__main__":
    unittest.main(verbosity=1)
