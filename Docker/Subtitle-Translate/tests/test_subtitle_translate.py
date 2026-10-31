#!/usr/bin/env python3
"""scripts/subtitle-translate.py: source choice, naming, timing-exact SRT rewrite, replacement by human subtitles, run loop with fake APIs.
Usage: python3 tests/test_subtitle_translate.py"""
import datetime as dt
import importlib.util
import json
import os
import pathlib
import re
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("st", ROOT / "scripts" / "subtitle-translate.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

SUPPORTED = {"en", "de", "fr", "es", "it", "ja", "zh", "zt", "pt", "ru", "sv", "da"}


def sub(lang, hi=False, forced=False, path=None, track=None):
    return {"code2": lang, "hi": hi, "forced": forced, "path": path, "embedded_track_id": track}


def ext(lang, hi=False, forced=False, name="Movie"):
    flags = (".hi" if hi else "") + (".forced" if forced else "")
    return sub(lang, hi, forced, path=f"/data/media/movies/M/{name}.{lang}{flags}.srt")


def plan_of(subs, original):
    return st.plan(st.human(subs), original, SUPPORTED)


def picked(p):
    return {t: (s["code2"], s["hi"], "ext" if s["path"] else "emb") for t, s in ((t, c[0]) for t, c in p.items())}


SRC = """﻿1
00:00:27,611 --> 00:00:29,113
(DRUMROLL PLAYING)

2
00:00:43,377 --> 00:00:46,130
<i>Wiggle your big toe.</i>
Come on.

3
00:01:02,146 --> 00:01:07,151
♪ ♪

4
00:01:52,488 --> 00:01:53,697
You know...
"""


class Plan(unittest.TestCase):
    def test_only_german_missing_takes_english_hi_first(self):
        p = plan_of([ext("en"), ext("en", hi=True), ext("fr", hi=True), sub("en", track=2)], "fr")
        self.assertEqual(picked(p), {"de": ("en", True, "ext")})

    def test_only_german_missing_english_full_before_original_hi(self):
        p = plan_of([ext("en"), ext("fr", hi=True)], "fr")
        self.assertEqual(picked(p), {"de": ("en", False, "ext")})

    def test_only_english_missing_takes_german_hi_then_full(self):
        self.assertEqual(picked(plan_of([ext("de"), ext("de", hi=True), ext("es")], "es")), {"en": ("de", True, "ext")})
        self.assertEqual(picked(plan_of([ext("de"), ext("es", hi=True)], "es")), {"en": ("de", False, "ext")})

    def test_both_missing_french_only_makes_both_from_the_same_source(self):
        p = plan_of([ext("fr")], "ja")
        self.assertEqual(picked(p), {"de": ("fr", False, "ext"), "en": ("fr", False, "ext")})

    def test_both_missing_original_hi_then_full_then_any_other(self):
        self.assertEqual(picked(plan_of([ext("it"), ext("ja"), ext("ja", hi=True)], "ja")),
                         {"de": ("ja", True, "ext"), "en": ("ja", True, "ext")})
        self.assertEqual(picked(plan_of([ext("it"), ext("ja")], "ja")), {"de": ("ja", False, "ext"), "en": ("ja", False, "ext")})
        self.assertEqual(picked(plan_of([ext("it")], "ja")), {"de": ("it", False, "ext"), "en": ("it", False, "ext")})

    def test_any_other_prefers_hi(self):
        self.assertEqual(picked(plan_of([ext("it"), ext("es", hi=True)], "ja"))["de"], ("es", True, "ext"))

    def test_forced_only_gives_nothing(self):
        self.assertEqual(plan_of([ext("en", forced=True), ext("de", forced=True), sub("fr", forced=True, track=3)], "fr"), {})

    def test_forced_never_a_source_and_never_counts_as_present(self):
        p = plan_of([ext("de", forced=True), ext("en")], "en")
        self.assertEqual(picked(p), {"de": ("en", False, "ext")})

    def test_nothing_missing(self):
        self.assertEqual(plan_of([ext("de"), ext("en", hi=True)], "en"), {})

    def test_embedded_counts_as_present_and_as_source(self):
        self.assertEqual(plan_of([sub("de", track=4), sub("en", track=3)], "en"), {})
        self.assertEqual(picked(plan_of([sub("en", hi=True, track=3), sub("en", track=2)], "en")), {"de": ("en", True, "emb")})

    def test_external_before_embedded_in_the_same_rung(self):
        p = plan_of([sub("en", hi=True, track=3), ext("en", hi=True)], "en")
        self.assertEqual(picked(p), {"de": ("en", True, "ext")})
        self.assertEqual(len(p["de"]), 2)

    def test_unsupported_language_is_no_source(self):
        self.assertEqual(plan_of([ext("ta")], "ta"), {})

    def test_never_chain_from_a_translated_file(self):
        subs = [ext("fr"), sub("en", path="/data/media/movies/M/Movie.English (translated).en.srt")]
        self.assertEqual(picked(plan_of(subs, "fr")), {"de": ("fr", False, "ext"), "en": ("fr", False, "ext")})

    def test_unknown_original_skips_the_original_rungs(self):
        self.assertEqual(picked(plan_of([ext("it")], None)), {"de": ("it", False, "ext"), "en": ("it", False, "ext")})


class Naming(unittest.TestCase):
    V = "/srv/video/media/movies/M/Movie (2011) - [Bluray-2160p].mkv"

    def test_names(self):
        self.assertEqual(st.out_path(self.V, "de", False), "/srv/video/media/movies/M/Movie (2011) - [Bluray-2160p].German (translated).de.srt")
        self.assertEqual(st.out_path(self.V, "en", True), "/srv/video/media/movies/M/Movie (2011) - [Bluray-2160p].English (translated).en.hi.srt")
        self.assertTrue(st.is_translated(st.out_path(self.V, "en", True)))
        self.assertFalse(st.is_translated("/x/Movie.en.hi.srt"))

    def test_bazarr_strict_match_does_not_see_the_file(self):
        # Mirror of Bazarr 1.6 subliminal_patch _search_external_subtitles (strict): tag stripped, language code stripped, rest must equal the video name.
        video = pathlib.Path(self.V).stem.lower()
        for lang, hi in (("de", False), ("en", True)):
            root = pathlib.Path(st.out_path(self.V, lang, hi)).stem
            parts = root.rsplit(".", 1)
            if parts[1].lower() in ("hi", "cc", "sdh", "forced"):
                root = parts[0]
            bare = re.sub(r"\.([^-.]{2,3})(?:-[A-Za-z]{2,})?$", "", root).lower()
            self.assertNotEqual(bare, video)


class Srt(unittest.TestCase):
    def test_rewrite_keeps_every_timestamp_and_index(self):
        cues = st.parse_srt(SRC)
        self.assertEqual([c["time"] for c in cues], ["00:00:27,611 --> 00:00:29,113", "00:00:43,377 --> 00:00:46,130",
                                                     "00:01:02,146 --> 00:01:07,151", "00:01:52,488 --> 00:01:53,697"])
        out = st.render_srt(cues, {0: "(TROMMELWIRBEL)", 1: "<i>Wackle mit dem großen Zeh.</i>\nKomm schon.", 3: "Weisst du..."})
        again = st.parse_srt(out)
        self.assertEqual([c["time"] for c in again], [c["time"] for c in cues])
        self.assertEqual([c["index"] for c in again], ["1", "2", "3", "4"])
        self.assertEqual(again[1]["text"], "<i>Wackle mit dem großen Zeh.</i>\nKomm schon.")
        self.assertEqual(again[2]["text"], "♪ ♪")
        self.assertFalse(out.startswith("﻿"))

    def test_crlf_and_missing_trailing_blank(self):
        cues = st.parse_srt(SRC.replace("\n", "\r\n").rstrip())
        self.assertEqual(len(cues), 4)
        self.assertEqual(cues[3]["text"], "You know...")

    def test_lines_to_translate_skip_sound_only_and_empty(self):
        cues = st.parse_srt(SRC)
        self.assertEqual([i for i, _ in st.translatable(cues)], [0, 1, 3])

    def test_all_caps_cues_go_out_in_sentence_case_and_come_back_in_caps(self):
        cfg = st.Config(chunk=10)
        seen = []

        class Api:
            def translate(self, src, tgt, lines, title):
                seen.extend(lines)
                return {p: {"(Drumroll playing)": "(trommelwirbel spielt)", "Do you find me sadistic?": "Findest du mich sadistisch?"}.get(t, t) for p, t in lines}

        cues = [{"index": "1", "time": "t", "text": "(DRUMROLL PLAYING)"}, {"index": "2", "time": "t", "text": "Do you find me sadistic?"}]
        done = st.translate_cues(cfg, Api(), cues, "en", "de", "x")
        self.assertEqual(seen, [(0, "(Drumroll playing)"), (1, "Do you find me sadistic?")])
        self.assertEqual(done, {0: "(TROMMELWIRBEL SPIELT)", 1: "Findest du mich sadistisch?"})

    def test_caps_keep_tags_lowercase(self):
        self.assertEqual(st.upper_text("<i>(funky fanfare)</i> {\\an8}x"), "<i>(FUNKY FANFARE)</i> {\\an8}X")

    def test_ass_to_cues(self):
        ass = ("[Script Info]\nTitle: x\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
               "Dialogue: 0,0:00:01.23,0:00:03.40,Default,,0,0,0,,{\\an8}Hello,\\Nworld\n"
               "Comment: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,note\n"
               "Dialogue: 0,0:01:00.00,0:01:02.05,Default,,0,0,0,,Bye\n")
        cues = st.parse_ass(ass)
        self.assertEqual([(c["time"], c["text"]) for c in cues],
                         [("00:00:01,230 --> 00:00:03,400", "Hello,\nworld"), ("00:01:00,000 --> 00:01:02,050", "Bye")])


class Due(unittest.TestCase):
    def test_wait(self):
        now = dt.datetime(2026, 10, 31, 4, 46, tzinfo=dt.timezone.utc)
        self.assertFalse(st.due("2026-10-29T12:00:00Z", now, 48))
        self.assertTrue(st.due("2026-10-29T04:00:00Z", now, 48))
        self.assertFalse(st.due(None, now, 48))


class FakeApis:
    def __init__(self, root, movies, translate=None):
        self.root, self.movies, self.calls, self.refreshed = root, movies, [], []
        self.translate_fn = translate or (lambda src, tgt, lines: {p: f"[{tgt}] {t}" for p, t in lines})

    def bazarr_items(self):
        return [dict(m["bazarr"], kind="movie") for m in self.movies]

    def arr_info(self, item):
        m = next(m for m in self.movies if m["bazarr"]["path"] == item["path"])
        return m["original"], m["added"]

    def translate(self, src, tgt, lines, title):
        self.calls.append((src, tgt, len(lines), title))
        return self.translate_fn(src, tgt, lines)

    def extract(self, video, track, dest):
        pathlib.Path(dest).write_text(SRC, encoding="utf-8")
        return True

    def refresh(self, paths):
        self.refreshed += list(paths)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "media/movies/A").mkdir(parents=True)
        self.video = self.root / "media/movies/A/A (2020).mkv"
        self.video.write_bytes(b"x")
        self.cfg = st.Config(data_host=str(self.root), state_dir=str(self.root / "state"), wait_hours=48, supported=SUPPORTED,
                             stop_at=None, max_titles=0, dry_run=False, chunk=2)
        self.now = dt.datetime(2026, 10, 31, 4, 46, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def movie(self, subs, original="English", added="2026-10-20T00:00:00Z"):
        return {"bazarr": {"path": "/data/media/movies/A/A (2020).mkv", "title": "A", "radarrId": 1, "subtitles": subs},
                "original": original, "added": added}

    def write_ext(self, lang, hi=False):
        p = self.root / f"media/movies/A/A (2020).{lang}{'.hi' if hi else ''}.srt"
        p.write_text(SRC, encoding="utf-8")
        return sub(lang, hi, path="/data/media/movies/A/" + p.name)

    def test_german_from_english_hi_written_with_same_timestamps(self):
        apis = FakeApis(self.root, [self.movie([self.write_ext("en"), self.write_ext("en", hi=True)])])
        report = st.run(self.cfg, apis, self.now)
        out = self.root / "media/movies/A/A (2020).German (translated).de.hi.srt"
        self.assertTrue(out.exists(), report)
        self.assertEqual([c["time"] for c in st.parse_srt(out.read_text())], [c["time"] for c in st.parse_srt(SRC)])
        self.assertIn("[de] <i>Wiggle your big toe.</i>\nCome on.", out.read_text())
        self.assertEqual({(c[0], c[1]) for c in apis.calls}, {("en", "de")})
        self.assertEqual(sum(c[2] for c in apis.calls), 3)
        self.assertEqual(apis.refreshed, [str(self.video)])
        self.assertEqual(report["translated"][0]["source"], "external en hi")
        state = json.loads((self.root / "state/state.json").read_text())
        self.assertIn(str(out), state["translations"])

    def test_french_only_gives_both(self):
        apis = FakeApis(self.root, [self.movie([self.write_ext("fr")], original="French")])
        st.run(self.cfg, apis, self.now)
        names = sorted(p.name for p in (self.root / "media/movies/A").glob("*translated*"))
        self.assertEqual(names, ["A (2020).English (translated).en.srt", "A (2020).German (translated).de.srt"])

    def test_embedded_source_is_extracted(self):
        apis = FakeApis(self.root, [self.movie([sub("en", hi=True, track=3)])])
        st.run(self.cfg, apis, self.now)
        self.assertTrue((self.root / "media/movies/A/A (2020).German (translated).de.hi.srt").exists())

    def test_extraction_error_is_a_failure_not_a_bitmap(self):
        apis = FakeApis(self.root, [self.movie([sub("en", hi=True, track=3)])])

        def boom(video, s, dest):
            raise RuntimeError("mkvextract exit 159")
        apis.extract = boom
        report = st.run(self.cfg, apis, self.now)
        self.assertEqual(len(report["failed"]), 1)
        self.assertEqual(json.loads((self.root / "state/state.json").read_text())["no_text"], {})

    def test_too_new_waits(self):
        apis = FakeApis(self.root, [self.movie([self.write_ext("en")], added="2026-10-30T12:00:00Z")])
        report = st.run(self.cfg, apis, self.now)
        self.assertEqual(apis.calls, [])
        self.assertEqual(report["waiting"], 1)

    def test_existing_translation_not_redone(self):
        apis = FakeApis(self.root, [self.movie([self.write_ext("en")])])
        st.run(self.cfg, apis, self.now)
        n = len(apis.calls)
        st.run(self.cfg, apis, self.now)
        self.assertEqual(len(apis.calls), n)

    def test_human_subtitle_replaces_translation(self):
        en = self.write_ext("en")
        apis = FakeApis(self.root, [self.movie([en])])
        st.run(self.cfg, apis, self.now)
        out = self.root / "media/movies/A/A (2020).German (translated).de.srt"
        self.assertTrue(out.exists())
        apis.movies[0]["bazarr"]["subtitles"] = [en, self.write_ext("de")]
        apis.refreshed.clear()
        report = st.run(self.cfg, apis, self.now)
        self.assertFalse(out.exists())
        self.assertEqual(report["replaced"], [str(out)])
        self.assertEqual(apis.refreshed, [str(self.video)])

    def test_forced_human_does_not_replace(self):
        en = self.write_ext("en")
        apis = FakeApis(self.root, [self.movie([en])])
        st.run(self.cfg, apis, self.now)
        apis.movies[0]["bazarr"]["subtitles"] = [en, sub("de", forced=True, path="/data/media/movies/A/A (2020).de.forced.srt")]
        st.run(self.cfg, apis, self.now)
        self.assertTrue((self.root / "media/movies/A/A (2020).German (translated).de.srt").exists())

    def test_dry_run_writes_nothing(self):
        self.cfg.dry_run = True
        apis = FakeApis(self.root, [self.movie([self.write_ext("en")])])
        report = st.run(self.cfg, apis, self.now)
        self.assertEqual(apis.calls, [])
        self.assertEqual(report["would_translate"], [{"video": "A (2020).mkv", "target": "de", "source": "external en"}])
        self.assertFalse(list((self.root / "media/movies/A").glob("*translated*")))

    def test_bad_translation_count_is_rejected(self):
        apis = FakeApis(self.root, [self.movie([self.write_ext("en")])], translate=lambda s, t, lines: {})
        report = st.run(self.cfg, apis, self.now)
        self.assertFalse(list((self.root / "media/movies/A").glob("*translated*")))
        self.assertEqual(len(report["failed"]), 1)

    def test_stop_at_starts_nothing_after_the_deadline(self):
        self.cfg.stop_at = dt.time(5, 50)
        apis = FakeApis(self.root, [self.movie([self.write_ext("en")])])
        report = st.run(self.cfg, apis, dt.datetime(2026, 10, 31, 5, 55, tzinfo=dt.timezone.utc))
        self.assertEqual(apis.calls, [])
        self.assertEqual(report["left_for_next_night"], 1)

    def test_no_keys_in_argv(self):
        src = (ROOT / "scripts" / "subtitle-translate.py").read_text()
        self.assertNotRegex(src, r"apikey=|X-Api-Key: \$")


if __name__ == "__main__":
    unittest.main()
