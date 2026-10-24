#!/usr/bin/env python3
"""scripts/original-audio.py: language name mapping, the decision per file, mkvmerge JSON parsing, the mkvpropedit arguments.
Usage: python3 tests/test_original_audio.py"""
import importlib.util
import pathlib
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


if __name__ == "__main__":
    unittest.main()
