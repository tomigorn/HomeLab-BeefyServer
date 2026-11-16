#!/usr/bin/env python3
"""Tests for scripts/codec-size-report.py (dry run of the codec minimum over the library). Usage: python3 tests/test_codec_size_report.py"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("csr", ROOT / "scripts" / "codec-size-report.py")
csr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csr)
MIB = 1048576
PROFILES = [{"id": 1, "name": "4K HDR - UHD Bluray + WEB (1080p fallback)"}, {"id": 2, "name": "Anime - 4K HDR (1080p fallback)"}]


def movie(i, title, codec, mib, runtime=100, quality="Bluray-1080p", profile=1, **kw):
    f = {"size": mib * MIB, "quality": {"quality": {"name": quality}}, "mediaInfo": {"videoCodec": codec}, "relativePath": f"{title}.mkv"}
    return {"id": i, "title": title, "year": 2010, "runtime": runtime, "qualityProfileId": profile, "genres": [], "movieFile": f} | kw


class Items(unittest.TestCase):
    def test_movies_with_a_file_become_items(self):
        items = csr.movie_items([movie(1, "A", "x264", 2000), {"id": 2, "title": "No file", "year": 2020, "runtime": 90, "qualityProfileId": 1}], PROFILES)
        self.assertEqual(items, [{"app": "radarr", "title": "A (2010)", "file": "A.mkv", "quality": "Bluray-1080p", "codec": "x264", "size": 2000 * MIB,
                                  "runtime": 100, "episodes": 1, "anime": False}])

    def test_anime_film_by_profile_or_japanese_animation(self):
        items = csr.movie_items([movie(1, "A", "x265", 2000, profile=2),
                                 movie(2, "B", "x265", 2000, genres=["Animation"], originalLanguage={"name": "Japanese"}),
                                 movie(3, "C", "x265", 2000, genres=["Animation"], originalLanguage={"name": "English"})], PROFILES)
        self.assertEqual([i["anime"] for i in items], [True, True, False])

    def test_episode_files_count_their_episodes(self):
        series = [{"id": 7, "title": "Show", "year": 2019, "runtime": 24, "seriesType": "anime"}]
        files = {7: [{"id": 70, "size": 800 * MIB, "quality": {"quality": {"name": "Bluray-1080p"}}, "mediaInfo": {"videoCodec": "HEVC"}, "relativePath": "S01E01-E02.mkv"},
                     {"id": 71, "size": 300 * MIB, "quality": {"quality": {"name": "WEBDL-1080p"}}, "mediaInfo": {}, "relativePath": "S01E03.x264.mkv"}]}
        episodes = {7: [{"episodeFileId": 70}, {"episodeFileId": 70}, {"episodeFileId": 71}, {"episodeFileId": 0}]}
        items = csr.episode_items(series, files, episodes)
        self.assertEqual([(i["title"], i["file"], i["codec"], i["episodes"], i["runtime"], i["anime"]) for i in items],
                         [("Show (2019)", "S01E01-E02.mkv", "HEVC", 2, 24, True), ("Show (2019)", "S01E03.x264.mkv", "", 1, 24, True)])


class Check(unittest.TestCase):
    def test_rule_is_the_guard_jq_file(self):
        items = [{"app": "radarr", "title": "A", "file": "A.x264.mkv", "quality": "Bluray-1080p", "codec": "x264", "size": 2000 * MIB, "runtime": 100, "episodes": 1, "anime": False},
                 {"app": "sonarr", "title": "B", "file": "B.mkv", "quality": "Bluray-1080p", "codec": "", "size": 400 * MIB, "runtime": 24, "episodes": 1, "anime": True},
                 {"app": "radarr", "title": "C", "file": "C.mkv", "quality": "Bluray-2160p", "codec": "x265", "size": 100 * MIB, "runtime": 100, "episodes": 1, "anime": False}]
        out = csr.check(items)
        self.assertEqual([(o["codec"], o["min"], o["perMin"], o["ok"], o["checked"]) for o in out],
                         [("x264", 25, 20, False, True), ("unknown", 25, 16.7, False, True), ("x265", 15, 1, True, False)])

    def test_summary_counts_and_examples(self):
        out = [{"app": "radarr", "title": "A", "file": "a", "anime": False, "codec": "x264", "min": 25, "perMin": 20, "ok": False, "checked": True, "quality": "Bluray-1080p"},
               {"app": "radarr", "title": "B", "file": "b", "anime": False, "codec": "x265", "min": 15, "perMin": 30, "ok": True, "checked": True, "quality": "Bluray-1080p"},
               {"app": "sonarr", "title": "S", "file": "s", "anime": True, "codec": "x265", "min": 15, "perMin": 12, "ok": False, "checked": True, "quality": "Bluray-1080p"},
               {"app": "radarr", "title": "U", "file": "u", "anime": False, "codec": "x265", "min": 15, "perMin": 50, "ok": True, "checked": False, "quality": "Bluray-2160p"}]
        lines = csr.summary(out, examples=5)
        self.assertIn("radarr: 2 files checked, 1 below (x264 1/1, x265 0/1)", lines)
        self.assertIn("sonarr: 1 files checked, 1 below (x265 1/1); anime 1 below of 1", lines)
        self.assertIn("  below: A (x264 20 MB/min < 25, Bluray-1080p) a", lines)
        self.assertIn("  below: S [anime] (x265 12 MB/min < 15, Bluray-1080p) s", lines)
        self.assertEqual(sum(l.startswith("  below:") for l in csr.summary(out, examples=1)), 2)


if __name__ == "__main__":
    unittest.main()
