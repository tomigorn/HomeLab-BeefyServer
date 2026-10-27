#!/usr/bin/env python3
"""scripts/subtitle-check.py: mojibake / encoding detection, the unambiguous repair, backups, event files, the Bazarr hook and the nightly scan of changed files, against fixtures in tests/fixtures/subtitles.
Usage: python3 tests/test_subtitle_check.py"""
import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "subtitles"
spec = importlib.util.spec_from_file_location("sc", ROOT / "scripts" / "subtitle-check.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)
VOBSUB = b"\x00\x00\x01\xba\x44\x00\x04\x00\x04\x01\x01\x89\xc3\xf8" + bytes(range(256)) * 4


def fixture(name):
    return (FIX / name).read_bytes()


class Analyse(unittest.TestCase):
    def test_the_hunt_notes_are_repaired_lower_and_upper_case(self):
        r = sc.analyse(fixture("hunt.en.srt"), "en")
        self.assertEqual(r.status, "repaired")
        self.assertEqual(r.fixed.count("♪"), 5)
        self.assertNotIn("™", r.fixed)
        self.assertIn("♪ A child is born in Bethlehem ♪", r.fixed)
        self.assertIn("I’m going to cry again. Lucas…", r.fixed)
        self.assertEqual(r.counts, {"в™Є → ♪": 3, "В™Є → ♪": 2})

    def test_german_cp1252_mojibake_is_repaired_and_crlf_kept(self):
        r = sc.analyse(fixture("umlauts-cp1252.de.srt"), "de")
        self.assertEqual(r.status, "repaired")
        self.assertIn("Schöne Grüße aus München", r.fixed)
        self.assertIn("„Das ist ärgerlich“ – wirklich.", r.fixed)
        self.assertEqual(r.fixed.count("\r\n"), fixture("umlauts-cp1252.de.srt").count(b"\r\n"))

    def test_german_cp1251_mojibake_is_repaired(self):
        r = sc.analyse(fixture("umlauts-cp1251.de.srt"), "de")
        self.assertEqual(r.status, "repaired")
        self.assertIn("Schöne Grüße, das ist ärgerlich.", r.fixed)

    def test_cp1252_note_is_repaired(self):
        r = sc.analyse("1\n00:00:01,000 --> 00:00:02,000\nâ™ª la la â™ª\n".encode(), "en")
        self.assertEqual((r.status, r.fixed.count("♪")), ("repaired", 2))

    def test_double_encoding_is_repaired_in_passes(self):
        r = sc.analyse(fixture("double.en.srt"), "en")
        self.assertEqual(r.status, "repaired")
        self.assertIn("It ’s fine.", r.fixed)

    def test_clean_file_is_untouched(self):
        r = sc.analyse(fixture("clean.de.srt"), "de")
        self.assertEqual((r.status, r.fixed, r.reasons), ("ok", None, []))

    def test_russian_text_is_not_mistaken_for_mojibake(self):
        self.assertEqual(sc.analyse(fixture("russian.ru.srt"), "ru").status, "ok")

    def test_leftover_after_repair_means_report_only(self):
        r = sc.analyse(fixture("dangling.en.srt"), "en")
        self.assertEqual(r.status, "suspect")
        self.assertIsNone(r.fixed)
        self.assertIn("mojibake residue", " ".join(r.reasons))
        self.assertIn("C1 control", " ".join(r.reasons))

    def test_replacement_character_is_reported(self):
        r = sc.analyse(fixture("fffd.en.srt"), "en")
        self.assertEqual((r.status, r.fixed), ("suspect", None))
        self.assertIn("U+FFFD", " ".join(r.reasons))

    def test_not_utf8_is_reported(self):
        r = sc.analyse(fixture("latin1.de.srt"), "de")
        self.assertEqual((r.status, r.fixed), ("suspect", None))
        self.assertIn("not UTF-8", " ".join(r.reasons))

    def test_bom_is_kept(self):
        r = sc.analyse(fixture("bom.fr.srt"), "fr")
        self.assertEqual(r.status, "repaired")
        self.assertTrue(r.output.startswith(b"\xef\xbb\xbf"))
        self.assertIn("Café with BOM.", r.fixed)

    def test_binary_vobsub_is_skipped(self):
        self.assertEqual(sc.analyse(VOBSUB, "de").status, "binary")

    def test_text_microdvd_sub_is_checked(self):
        self.assertEqual(sc.analyse(fixture("microdvd.de.sub"), "de").status, "repaired")

    def test_repaired_output_is_stable(self):
        r = sc.analyse(fixture("hunt.en.srt"), "en")
        self.assertEqual(sc.analyse(r.output, "en").status, "ok")


class Names(unittest.TestCase):
    def test_language_from_the_file_name(self):
        self.assertEqual(sc.language("Film (2012) - [x]-VECTOR.en.srt"), "en")
        self.assertEqual(sc.language("Film.de.forced.srt"), "de")
        self.assertEqual(sc.language("Film.pt-BR.hi.srt"), "pt-br")
        self.assertEqual(sc.language("Film.srt"), "und")

    def test_title_of_a_movie_and_an_episode(self):
        self.assertEqual(sc.title("/data/media/movies/The Hunt (2012) [tmdbid-103663]/The Hunt (2012) - x.en.srt"), "The Hunt (2012) · en")
        self.assertEqual(sc.title("/data/media/tv/Money Heist (2017) [tvdbid-1]/Season 01/Money Heist (2017) - S01E03 - x.de.srt"), "Money Heist (2017) S01E03 · de")


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.lib = self.tmp / "media" / "movies" / "The Hunt (2012) [tmdbid-103663]"
        self.lib.mkdir(parents=True)
        self.state = self.tmp / "state"
        self.cfg = sc.Config(self.state)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def put(self, fixture_name, name=None, data=None):
        p = self.lib / (name or fixture_name)
        p.write_bytes(data if data is not None else fixture(fixture_name))
        return p

    def events(self):
        return [json.loads(f.read_text()) for f in sorted((self.state / "events").glob("*.json"))]

    def log(self):
        return (self.state / self.cfg.log_name).read_text()

    def test_repair_writes_file_backup_log_and_digest_event(self):
        p = self.put("hunt.en.srt", "The Hunt (2012) - x.en.srt")
        before = p.read_bytes()
        self.assertEqual(sc.check_file(p, self.cfg, source="bazarr"), "repaired")
        self.assertEqual(p.read_text().count("♪"), 5)
        [backup] = list((self.state / "backup").rglob("*.en.srt"))
        self.assertEqual(backup.read_bytes(), before)
        self.assertNotIn(str(self.lib), str(backup.parent).replace(str(self.state), ""))
        self.assertIn("repaired", self.log())
        self.assertIn(str(backup), self.log())
        [ev] = self.events()
        self.assertEqual((ev["app"], ev["kind"], ev["title"]), ("bazarr", "subtitle_repaired", "The Hunt (2012) · en"))
        self.assertEqual(ev["path"], str(p))

    def test_mode_is_kept(self):
        p = self.put("hunt.en.srt")
        os.chmod(p, 0o664)
        sc.check_file(p, self.cfg)
        self.assertEqual(p.stat().st_mode & 0o777, 0o664)

    def test_second_run_changes_nothing(self):
        p = self.put("umlauts-cp1252.de.srt")
        sc.check_file(p, self.cfg)
        after = p.read_bytes()
        self.assertEqual(sc.check_file(p, self.cfg), "ok")
        self.assertEqual(p.read_bytes(), after)
        self.assertEqual(len(list((self.state / "backup").rglob("*.srt"))), 1)
        self.assertEqual(len(self.events()), 1)

    def test_clean_file_is_not_written_and_makes_no_event(self):
        p = self.put("clean.de.srt")
        mtime = p.stat().st_mtime_ns
        self.assertEqual(sc.check_file(p, self.cfg), "ok")
        self.assertEqual(p.stat().st_mtime_ns, mtime)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.state / "backup").exists())

    def test_suspect_file_is_left_alone_and_reported(self):
        p = self.put("dangling.en.srt")
        before = p.read_bytes()
        self.assertEqual(sc.check_file(p, self.cfg), "suspect")
        self.assertEqual(p.read_bytes(), before)
        [ev] = self.events()
        self.assertEqual(ev["kind"], "subtitle_suspect")
        self.assertTrue(ev["reasons"])
        self.assertIn("suspect", self.log())

    def test_dry_run_writes_nothing(self):
        p = self.put("hunt.en.srt")
        before = p.read_bytes()
        cfg = sc.Config(self.state, dry_run=True)
        self.assertEqual(sc.check_file(p, cfg), "repaired")
        self.assertEqual(p.read_bytes(), before)
        self.assertFalse(self.state.exists())

    def test_binary_and_other_extensions_are_skipped(self):
        self.assertEqual(sc.check_file(self.put("x", "Film.sub", VOBSUB), self.cfg), "binary")
        self.assertEqual(sc.check_file(self.put("x", "Film.idx", b"# VobSub index file, v7\n"), self.cfg), "skipped")
        self.assertEqual(self.events(), [])

    def test_symlink_is_skipped(self):
        target = self.put("hunt.en.srt")
        link = self.lib / "link.en.srt"
        link.symlink_to(target)
        self.assertEqual(sc.check_file(link, self.cfg), "skipped")

    def test_hook_entry_point(self):
        p = self.put("hunt.en.srt")
        self.assertEqual(sc.main([str(p), "opensubtitlescom", "955197"], env={"SC_STATE": str(self.state)}), 0)
        self.assertIn("♪", p.read_text())
        self.assertIn("opensubtitlescom 955197", self.log())
        [ev] = self.events()
        self.assertEqual(ev["source"], "opensubtitlescom 955197")

    def test_hook_with_a_missing_file_fails_quietly(self):
        self.assertEqual(sc.main([str(self.lib / "gone.en.srt")], env={"SC_STATE": str(self.state)}), 1)


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.branch = self.tmp / "ssd" / "media"
        self.merged = self.tmp / "merged" / "media"
        self.state = self.tmp / "state"
        self.cfg = sc.Config(self.state, log_name="scan.log")
        for root in (self.branch, self.merged):
            (root / "movies" / "Film (2020)").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def put(self, rel, data, age=0):
        for root in (self.branch, self.merged):
            p = root / rel
            p.write_bytes(data)
            t = time.time() - age
            os.utime(p, (t, t))
        return self.branch / rel, self.merged / rel

    def scan(self, now):
        return sc.scan([(self.branch, self.merged)], self.cfg, now=now)

    def test_first_run_checks_all_then_only_changed_files(self):
        _, merged = self.put("movies/Film (2020)/Film.en.srt", fixture("hunt.en.srt"))
        self.put("movies/Film (2020)/Film.de.srt", fixture("clean.de.srt"))
        res = self.scan(time.time())
        self.assertEqual(res, {"checked": 2, "repaired": 1, "suspect": 0})
        self.assertIn("♪", merged.read_text())
        self.assertEqual(self.scan(time.time() + 5)["checked"], 0)

    def test_new_file_after_the_last_run_is_checked(self):
        self.put("movies/Film (2020)/Film.de.srt", fixture("clean.de.srt"), age=3600)
        self.scan(time.time())
        time.sleep(0.01)
        self.put("movies/Film (2020)/New.de.srt", fixture("umlauts-cp1252.de.srt"))
        self.assertEqual(self.scan(time.time())["repaired"], 1)

    def test_repair_is_written_through_the_merged_path(self):
        branch, merged = self.put("movies/Film (2020)/Film.en.srt", fixture("hunt.en.srt"))
        self.scan(time.time())
        self.assertIn("♪", merged.read_text())
        self.assertNotIn("♪", branch.read_text())

    def test_last_run_is_saved(self):
        self.scan(1_800_000_000)
        self.assertEqual(json.loads((self.state / "scan-state.json").read_text())["last_run"], 1_800_000_000)


if __name__ == "__main__":
    unittest.main(verbosity=1)
