#!/usr/bin/env python3
"""scripts/original-audio.py: language name mapping, the decision per file, mkvmerge JSON parsing, the mkvpropedit arguments.
Usage: python3 tests/test_original_audio.py"""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("oa", ROOT / "scripts" / "original-audio.py")
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)

MKV_JSON = {
    "tracks": [
        {"id": 0, "type": "video", "properties": {"language": "und"}},
        {"id": 1, "type": "audio", "properties": {"language": "ger", "track_name": "German 5.1", "default_track": True}},
        {"id": 2, "type": "audio", "properties": {"language": "spa", "track_name": "Spanish 5.1", "default_track": False}},
        {"id": 3, "type": "subtitles", "properties": {"language": "eng", "default_track": False}},
        {"id": 4, "type": "audio", "properties": {"language": "spa", "default_track": False, "flag_commentary": True}},
    ]
}


def t(lang, name="", default=False, n=1):
    return {"n": n, "lang": lang, "name": name, "default": default}


class Mapping(unittest.TestCase):
    def test_both_code_families(self):
        self.assertEqual(oa.language_codes("German"), {"ger", "deu"})
        self.assertEqual(oa.language_codes("chinese"), {"chi", "zho"})
        self.assertIn("nob", oa.language_codes("Norwegian"))
        self.assertIn("isl", oa.language_codes("Icelandic"))

    def test_unknown(self):
        self.assertIsNone(oa.language_codes("Klingon"))
        self.assertIsNone(oa.language_codes(None))


class Parse(unittest.TestCase):
    def test_audio_only_with_audio_index(self):
        tracks = oa.parse_tracks(MKV_JSON)
        self.assertEqual([x["n"] for x in tracks], [1, 2, 3])
        self.assertEqual(tracks[0], {"n": 1, "lang": "ger", "name": "German 5.1", "default": True, "commentary": False})
        self.assertEqual(tracks[1]["lang"], "spa")
        self.assertTrue(tracks[2]["commentary"])
        self.assertEqual(tracks[2]["name"], "")

    def test_missing_language_is_und(self):
        self.assertEqual(oa.parse_tracks({"tracks": [{"type": "audio", "properties": {}}]})[0]["lang"], "und")


class Decide(unittest.TestCase):
    def test_single_match_sets_default_and_name(self):
        p = oa.decide("Spanish", [t("ger", "German 5.1", True, 1), t("spa", "Spanish 5.1", False, 2)])
        self.assertEqual(p["action"], "set")
        self.assertEqual(p["track"], 2)
        self.assertEqual(p["edits"], [("a1", ["flag-default=0"]), ("a2", ["flag-default=1", "name=Spanish 5.1 (Original)"])])

    def test_empty_name(self):
        p = oa.decide("Spanish", [t("spa", "", False, 1)])
        self.assertEqual(p["edits"], [("a1", ["flag-default=1", "name=Spanish (Original)"])])

    def test_already_correct_is_noop(self):
        p = oa.decide("Spanish", [t("ger", "German", False, 1), t("spa", "Spanish (Original)", True, 2)])
        self.assertEqual(p["action"], "ok")

    def test_original_in_name_kept(self):
        p = oa.decide("Spanish", [t("spa", "Spanish Original", False, 1)])
        self.assertEqual(p["edits"], [("a1", ["flag-default=1"])])

    def test_unrelated_name_replaced(self):
        p = oa.decide("English", [t("eng", "United States (Original)", True, 1)])
        self.assertEqual(p["edits"], [("a1", ["name=English (Original)"])])
        p = oa.decide("Japanese", [t("jpn", "Mono Mix", True, 1)])
        self.assertEqual(p["edits"], [("a1", ["name=Japanese (Original)"])])

    def test_only_flag_fix_when_name_ok(self):
        p = oa.decide("Spanish", [t("ger", "", True, 1), t("spa", "Spanish (Original)", True, 2)])
        self.assertEqual(p["edits"], [("a1", ["flag-default=0"])])

    def test_dub_only_skipped(self):
        p = oa.decide("Spanish", [t("ger", "", True)])
        self.assertEqual(p["action"], "skip")
        self.assertIn("no Spanish audio track", p["reason"])

    def test_und_not_counted(self):
        p = oa.decide("Spanish", [t("und", "", True, 1), t("ger", "", False, 2)])
        self.assertEqual(p["action"], "skip")

    def test_alternate_code_matches(self):
        p = oa.decide("German", [t("deu", "", False, 1)])
        self.assertEqual(p["action"], "set")

    def test_commentary_by_name_loses_and_gets_flag(self):
        p = oa.decide("English", [t("eng", "Director Commentary", True, 1), t("eng", "English", False, 2)])
        self.assertEqual(p["track"], 2)
        self.assertEqual(p["edits"][0], ("a1", ["flag-default=0", "flag-commentary=1"]))

    def test_commentary_words(self):
        for w in ("Kommentar", "Commentaire", "commentary"):
            self.assertEqual(oa.decide("English", [t("eng", w, False, 1), t("eng", "", False, 2)])["track"], 2)

    def test_commentary_flag_loses_without_extra_edit(self):
        a = t("eng", "", False, 1)
        a["commentary"] = True
        p = oa.decide("English", [a, t("eng", "", True, 2)])
        self.assertEqual(p["track"], 2)
        self.assertEqual([e[0] for e in p["edits"]], ["a2"])

    def test_commentary_flag_not_repeated(self):
        a = t("eng", "Commentary", False, 1)
        a["commentary"] = True
        p = oa.decide("English", [a, t("eng", "English (Original)", True, 2)])
        self.assertEqual(p["action"], "ok")

    def test_only_commentary_skipped(self):
        p = oa.decide("English", [t("eng", "Commentary", True, 1)])
        self.assertEqual(p["action"], "skip")

    def test_several_prefers_default(self):
        p = oa.decide("English", [t("eng", "English", False, 1), t("eng", "English 2.0", True, 2)])
        self.assertEqual(p["track"], 2)
        self.assertEqual(p["candidates"], 2)

    def test_several_none_default_takes_first(self):
        p = oa.decide("English", [t("eng", "English", False, 1), t("eng", "English 2.0", False, 2)])
        self.assertEqual(p["track"], 1)
        self.assertNotIn(("a2", ["flag-default=0"]), p["edits"])

    def test_non_mkv(self):
        p = oa.decide("Spanish", [], suffix=".mp4")
        self.assertEqual(p, {"action": "skip", "reason": "not mkv, skipped"})

    def test_linked_file(self):
        p = oa.decide("Spanish", [t("spa")], nlink=2)
        self.assertEqual(p["action"], "skip")
        self.assertIn("seeding", p["reason"])

    def test_unknown_language(self):
        p = oa.decide("Klingon", [t("spa")])
        self.assertEqual(p["action"], "skip")
        self.assertIn("unknown language", p["reason"])


class Describe(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(oa.describe({"action": "ok"}, "Spanish"), "ok")
        self.assertEqual(oa.describe({"action": "set", "track": 2}, "Spanish"), "set Spanish track 2 default")
        self.assertEqual(oa.describe({"action": "set", "track": 2, "candidates": 2}, "Spanish"), "set Spanish track 2 default (of 2 candidates)")
        self.assertEqual(oa.describe({"action": "skip", "reason": "x"}, "Spanish"), "skip: x")

    def test_propedit_args(self):
        p = {"edits": [("a1", ["flag-default=0"]), ("a2", ["flag-default=1", "name=S (Original)"])]}
        self.assertEqual(oa.propedit_args("/f.mkv", p), ["/f.mkv", "--edit", "track:a1", "--set", "flag-default=0",
                         "--edit", "track:a2", "--set", "flag-default=1", "--set", "name=S (Original)"])


class Queue(unittest.TestCase):
    def test_parse_line_id_and_path(self):
        self.assertEqual(oa.parse_queue_line("12\t/data/media/movies/A (2020)/A.mkv"), ("12", "/data/media/movies/A (2020)/A.mkv"))

    def test_parse_line_ignores_blank_and_malformed(self):
        for line in ("", "   ", "no-tab-here", "\t/data/x.mkv", "7\t"):
            self.assertIsNone(oa.parse_queue_line(line), line)

    def test_claim_returns_unique_items_in_order_and_removes_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            q = pathlib.Path(d) / "original-audio.queue"
            q.write_text("1\t/data/a.mkv\n\njunk\n2\t/data/b.mkv\n1\t/data/a.mkv\n")
            self.assertEqual(oa.claim_queue(str(q)), [("1", "/data/a.mkv"), ("2", "/data/b.mkv")])
            self.assertFalse(q.exists())
            self.assertEqual(list(pathlib.Path(d).iterdir()), [])

    def test_claim_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(oa.claim_queue(str(pathlib.Path(d) / "none.queue")), [])

    def test_claim_takes_a_leftover_work_file_too(self):
        with tempfile.TemporaryDirectory() as d:
            q = pathlib.Path(d) / "original-audio.queue"
            (pathlib.Path(d) / "original-audio.queue.work").write_text("3\t/data/c.mkv\n")
            q.write_text("4\t/data/d.mkv\n")
            self.assertEqual(oa.claim_queue(str(q)), [("3", "/data/c.mkv"), ("4", "/data/d.mkv")])


class RestoreMtime(unittest.TestCase):
    def setUp(self):
        self.calls, self.real_utime, self.real_getxattr, self.real_run = [], oa.os.utime, oa.os.getxattr, oa.subprocess.run
        oa.subprocess.run = lambda *a, **k: self.fail("no subprocess (sudo) for the mtime")

    def tearDown(self):
        oa.os.utime, oa.os.getxattr, oa.subprocess.run = self.real_utime, self.real_getxattr, self.real_run

    def test_own_file_set_directly(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "a.mkv"
            f.write_text("x")
            oa.restore_mtime(str(f), 1_700_000_000_123_456_789)
            self.assertEqual(f.stat().st_mtime_ns, 1_700_000_000_123_456_789)

    def utime(self, path, ns):
        self.calls.append((path, ns))
        if path.startswith("/srv/video/"):
            raise PermissionError(1, "Operation not permitted", path)

    def test_mergerfs_file_of_another_uid_set_on_its_branch(self):
        oa.os.utime = self.utime
        oa.os.getxattr = lambda p, n: b"/srv/.disks/ssd-hot/media/a.mkv" if n == "user.mergerfs.fullpath" else self.fail(n)
        oa.restore_mtime("/srv/video/media/a.mkv", 5)
        self.assertEqual(self.calls[-1], ("/srv/.disks/ssd-hot/media/a.mkv", (5, 5)))

    def test_no_branch_path_keeps_the_permission_error(self):
        oa.os.utime = self.utime

        def no_xattr(p, n):
            raise OSError(61, "No data available")
        oa.os.getxattr = no_xattr
        with self.assertRaises(PermissionError):
            oa.restore_mtime("/srv/video/media/a.mkv", 5)


if __name__ == "__main__":
    unittest.main()
