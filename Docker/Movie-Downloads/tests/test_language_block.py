#!/usr/bin/env python3
"""scripts/language-block.py: the audio decision (every edge case), queue lines, recycle-bin paths and the block action against a fake Radarr/Sonarr.
Usage: python3 tests/test_language_block.py"""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lb", ROOT / "scripts" / "language-block.py")
lb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lb)


def a(lang, name="", commentary=False):
    return {"lang": lang, "name": name, "commentary": commentary}


class Decide(unittest.TestCase):
    def v(self, tracks, original="English", **kw):
        return lb.decide(tracks, original, **kw)["verdict"]

    def test_original_alone_is_fine(self):
        self.assertEqual(self.v([a("spa")], "Spanish"), "ok")

    def test_german_alone_is_fine(self):
        self.assertEqual(self.v([a("ger")], "Korean"), "ok")
        self.assertEqual(self.v([a("deu")], "Korean"), "ok")

    def test_english_alone_is_fine(self):
        self.assertEqual(self.v([a("eng")], "French"), "ok")

    def test_multi_without_any_of_the_three_is_blocked(self):
        d = lb.decide([a("fre"), a("ita")], "English")
        self.assertEqual(d["verdict"], "block")
        self.assertIn("French", d["reason"])
        self.assertIn("Italian", d["reason"])

    def test_reason_names_each_wanted_language_once(self):
        self.assertEqual(lb.decide([a("rus")], "English")["reason"], "no German or English audio, found: Russian")
        self.assertEqual(lb.decide([a("rus")], "Korean")["reason"], "no German, English or Korean audio, found: Russian")

    def test_dub_only_of_a_foreign_film_is_blocked(self):
        self.assertEqual(self.v([a("rus")], "Japanese"), "block")

    def test_und_alone_is_unknown(self):
        self.assertEqual(self.v([a("und")]), "unknown")
        self.assertEqual(self.v([a("")]), "unknown")

    def test_und_next_to_a_bad_language_is_unknown(self):
        self.assertEqual(self.v([a("fre"), a("und")]), "unknown")

    def test_und_next_to_a_good_language_is_ok(self):
        self.assertEqual(self.v([a("und"), a("eng")]), "ok")

    def test_mul_zxx_and_unknown_codes_are_unknown(self):
        for code in ("mul", "zxx", "qaa", "xyz"):
            self.assertEqual(self.v([a("fre"), a(code)]), "unknown", code)

    def test_und_track_name_counts(self):
        self.assertEqual(self.v([a("und", "English 5.1")]), "ok")
        self.assertEqual(self.v([a("und", "Deutsch DTS")], "Japanese"), "ok")
        self.assertEqual(self.v([a("und", "Français"), a("ita")], "English"), "block")

    def test_und_track_name_without_language_stays_unknown(self):
        self.assertEqual(self.v([a("fre"), a("und", "Surround 5.1")]), "unknown")

    def test_original_unknown_only_german_or_english_count(self):
        self.assertEqual(self.v([a("eng")], None), "ok")
        self.assertEqual(self.v([a("ger")], "Klingon"), "ok")
        self.assertEqual(self.v([a("fre")], None), "block")
        self.assertEqual(self.v([a("fre")], "Klingon"), "block")

    def test_original_name_with_region(self):
        self.assertEqual(self.v([a("por")], "Portuguese (Brazil)"), "ok")

    def test_anime_japanese_original_counts(self):
        self.assertEqual(self.v([a("jpn")], "Japanese"), "ok")
        self.assertEqual(self.v([a("jpn"), a("spa")], "Japanese"), "ok")

    def test_swiss_german_counts_as_german(self):
        self.assertEqual(self.v([a("gsw")], "English", tags=["audio-swiss-german"]), "ok")
        self.assertEqual(self.v([a("gsw")], "English"), "ok")
        self.assertEqual(self.v([{"lang": "und", "ietf": "de-CH", "name": "", "commentary": False}], "English"), "ok")
        self.assertEqual(self.v([a("und", "Schwiizerdütsch")], "English", tags=["audio-swiss-german"]), "ok")

    def test_override_is_never_blocked(self):
        d = lb.decide([a("fre"), a("ita")], "English", tags=["override"])
        self.assertEqual(d["verdict"], "skip")
        self.assertIn("anyway", d["reason"])

    def test_test_film_and_silent_films_are_skipped(self):
        self.assertEqual(self.v([a("fre")], "German", test_film=True), "skip")
        self.assertEqual(self.v([a("fre")], "German", year=1922), "skip")
        self.assertEqual(self.v([a("fre")], "German", year=1930), "block")

    def test_no_audio_is_unknown(self):
        self.assertEqual(self.v([]), "unknown")

    def test_commentary_never_counts(self):
        self.assertEqual(self.v([a("fre"), a("eng", "Director's commentary")]), "block")
        self.assertEqual(self.v([a("fre"), a("eng", commentary=True)]), "block")

    def test_only_commentary_is_unknown(self):
        self.assertEqual(self.v([a("eng", commentary=True)]), "unknown")


class SpokenLanguages(unittest.TestCase):
    def test_spoken_language_counts_as_original(self):
        d = lb.decide([a("ara")], "English", spoken={"ara", "eng"})
        self.assertEqual(d["verdict"], "ok")

    def test_spoken_languages_do_not_accept_others(self):
        d = lb.decide([a("fre")], "English", spoken={"ara"})
        self.assertEqual(d["verdict"], "block")
        self.assertIn("Arabic", d["reason"])

    def test_unreadable_tmdb_never_blocks(self):
        d = lb.decide([a("fre")], "English", spoken_error="Seerr: timed out")
        self.assertEqual(d["verdict"], "unknown")
        self.assertIn("TMDB languages not readable", d["reason"])
        self.assertEqual(lb.decide([a("eng")], "French", spoken_error="x")["verdict"], "ok")

    def test_codes_from_seerr_movie_and_tv_details(self):
        movie = {"originalLanguage": "en", "spokenLanguages": [{"english_name": "Arabic", "iso_639_1": "ar", "name": "x"}]}
        self.assertEqual(lb.tmdb_codes(movie), {"ara", "eng"})
        tv = {"originalLanguage": "es", "spokenLanguages": [{"englishName": "Russian", "iso_639_1": "ru"}]}
        self.assertEqual(lb.tmdb_codes(tv), {"spa", "rus"})
        self.assertEqual(lb.tmdb_codes({"originalLanguage": "xx", "spokenLanguages": [{"english_name": "No Language", "iso_639_1": "xx"}]}), set())
        self.assertEqual(lb.tmdb_codes({"originalLanguage": "de", "spokenLanguages": None}), {"ger", "deu"})


class TmdbCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "language-block-tmdb.json"
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, path):
        self.calls.append(path)
        if path == "/movie/9":
            raise lb.ApiError("GET /movie/9: boom")
        return {"originalLanguage": "en", "spokenLanguages": [{"english_name": "Russian", "iso_639_1": "ru"}]}

    def test_fetched_once_per_title_and_kept_on_disk(self):
        c = lb.TmdbLanguages(self.fetch, self.path, now=1000.0)
        self.assertEqual(c.get("movie", 926676), ({"rus", "eng"}, None))
        self.assertEqual(c.get("movie", 926676), ({"rus", "eng"}, None))
        self.assertEqual(c.get("tv", 5), ({"rus", "eng"}, None))
        c.save()
        self.assertEqual(self.calls, ["/movie/926676", "/tv/5"])
        again = lb.TmdbLanguages(self.fetch, self.path, now=2000.0)
        self.assertEqual(again.get("movie", 926676), ({"rus", "eng"}, None))
        self.assertEqual(len(self.calls), 2)

    def test_expired_entry_is_fetched_again(self):
        c = lb.TmdbLanguages(self.fetch, self.path, now=1000.0)
        c.get("movie", 1)
        c.save()
        lb.TmdbLanguages(self.fetch, self.path, now=1000.0 + lb.TMDB_KEEP_DAYS * 86400 + 1).get("movie", 1)
        self.assertEqual(self.calls, ["/movie/1", "/movie/1"])

    def test_error_is_reported_and_not_cached(self):
        c = lb.TmdbLanguages(self.fetch, self.path, now=1000.0)
        codes, err = c.get("movie", 9)
        self.assertEqual(codes, set())
        self.assertIn("boom", err)
        c.get("movie", 9)
        self.assertEqual(self.calls, ["/movie/9", "/movie/9"])

    def test_no_tmdb_id_is_no_lookup(self):
        c = lb.TmdbLanguages(self.fetch, self.path, now=1000.0)
        self.assertEqual(c.get("tv", 0), (set(), None))
        self.assertEqual(self.calls, [])

    def test_broken_cache_file_starts_empty(self):
        self.path.write_text("{broken")
        self.assertEqual(lb.TmdbLanguages(self.fetch, self.path, now=1000.0).get("movie", 1)[0], {"rus", "eng"})


class FakeTmdb:
    def __init__(self, codes=None, error=None):
        self.codes, self.error, self.asked = codes or set(), error, []

    def get(self, kind, tmdb_id):
        self.asked.append((kind, tmdb_id))
        return (set(), self.error) if self.error else (set(self.codes), None)


class Parse(unittest.TestCase):
    def test_mkvmerge_audio_tracks(self):
        info = {"tracks": [
            {"type": "video", "properties": {"language": "und"}},
            {"type": "audio", "properties": {"language": "fre", "language_ietf": "fr", "track_name": "VFF"}},
            {"type": "audio", "properties": {"language": "ger", "language_ietf": "de-CH", "flag_commentary": True}},
            {"type": "audio", "properties": {}},
            {"type": "subtitles", "properties": {"language": "eng"}},
        ]}
        self.assertEqual(lb.parse_mkvmerge(info), [
            {"lang": "fre", "ietf": "fr", "name": "VFF", "commentary": False},
            {"lang": "ger", "ietf": "de-CH", "name": "", "commentary": True},
            {"lang": "und", "ietf": "", "name": "", "commentary": False},
        ])

    def test_arr_media_info_languages(self):
        self.assertEqual([t["lang"] for t in lb.parse_arr_languages("eng/ger")], ["eng", "ger"])
        self.assertEqual([t["lang"] for t in lb.parse_arr_languages("")], [])
        self.assertEqual([t["lang"] for t in lb.parse_arr_languages("fre / ")], ["fre", "und"])

    def test_queue_line(self):
        line = "12\t/data/media/movies/A/A.mkv\t77\tSABnzbd_nzo_x\tTrue\t/data/media/movies/A/old.mkv|/data/media/movies/A/old2.mkv\t/data/.recyclebin/A/old.mkv|"
        self.assertEqual(lb.parse_queue_line(line), {
            "id": "12", "path": "/data/media/movies/A/A.mkv", "fileId": "77", "downloadId": "SABnzbd_nzo_x", "upgrade": True,
            "deleted": ["/data/media/movies/A/old.mkv", "/data/media/movies/A/old2.mkv"], "recycled": ["/data/.recyclebin/A/old.mkv", ""]})

    def test_queue_line_short_and_bad(self):
        self.assertEqual(lb.parse_queue_line("5\t/data/tv/S/1.mkv\t9\t\tFalse\t\t")["deleted"], [])
        self.assertIsNone(lb.parse_queue_line(""))
        self.assertIsNone(lb.parse_queue_line("5"))

    def test_recycle_path(self):
        self.assertEqual(lb.recycle_path("/data/.recyclebin", ["/data/media/movies"], "/data/media/movies/A (2020)/A.mkv"), "/data/.recyclebin/A (2020)/A.mkv")
        self.assertEqual(lb.recycle_path("/data/.recyclebin/", ["/data/media/tv/"], "/data/media/tv/S/Season 01/S - S01E01.mkv"), "/data/.recyclebin/S/Season 01/S - S01E01.mkv")
        self.assertIsNone(lb.recycle_path("", ["/data/media/movies"], "/data/media/movies/A/A.mkv"))
        self.assertIsNone(lb.recycle_path("/data/.recyclebin", ["/data/media/movies"], "/elsewhere/A.mkv"))


class FakeArr:
    def __init__(self, app="radarr", history=None, fail=()):
        self.app, self.calls, self.fail = app, [], set(fail)
        self.history = history if history is not None else [{"id": 501, "eventType": "grabbed", "downloadId": "DL1", "sourceTitle": "A.2020.MULTi.1080p-GRP"}]

    def __call__(self, method, path, query=None, body=None):
        self.calls.append((method, path, query, body))
        if (method, path.split("/")[1]) in self.fail or (method, path) in self.fail:
            raise lb.ApiError(f"{method} {path}: boom")
        if path == "/config/mediamanagement":
            return {"recycleBin": "/data/.recyclebin"}
        if path == "/rootfolder":
            return [{"path": "/data/media/movies"}, {"path": "/data/media/tv"}]
        if path == "/history":
            return {"records": [h for h in self.history if h.get("downloadId") == (query or {}).get("downloadId")], "totalRecords": 1}
        if path.startswith("/episode") and method == "GET":
            return [{"id": 31}, {"id": 32}]
        return {}

    def made(self, method, prefix):
        return [c for c in self.calls if c[0] == method and c[1].startswith(prefix)]


MOVIE = {"id": 12, "title": "A Film", "year": 2020, "tmdbId": 4242, "originalLanguage": {"name": "English"}, "tags": []}
BAD = [a("fre"), a("ita")]
GOOD = [a("eng")]


class Act(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.host = lambda p: str(self.root / p.lstrip("/"))
        self.state = {"entries": [], "downloads": []}
        movie = self.root / "data/media/movies/A (2020)"
        movie.mkdir(parents=True)
        (movie / "A (2020) - [MULTi].mkv").write_text("new")
        self.item = {"id": "12", "path": "/data/media/movies/A (2020)/A (2020) - [MULTi].mkv", "fileId": "77", "downloadId": "DL1",
                     "upgrade": False, "deleted": [], "recycled": []}

    def tearDown(self):
        self.tmp.cleanup()

    def run_act(self, arr, item, title=MOVIE, probe=None, dry=False, now=1000.0, tmdb=None):
        probe = probe or (lambda host: BAD if "MULTi" in host else GOOD)
        return lb.act(arr.app, arr, item, title, probe, self.state, host=self.host, dry=dry, now=now, tmdb=tmdb)

    def test_tmdb_spoken_language_keeps_the_file(self):
        arr, tmdb = FakeArr(), FakeTmdb({"ara", "eng"})
        e = self.run_act(arr, self.item, probe=lambda h: [a("ara")], tmdb=tmdb)
        self.assertEqual((e["verdict"], e["file"]), ("ok", "kept"))
        self.assertEqual(tmdb.asked, [("movie", 4242)])
        self.assertFalse(arr.made("POST", "/") or arr.made("DELETE", "/"))

    def test_tmdb_asked_only_when_the_file_would_be_blocked(self):
        tmdb = FakeTmdb({"ara"})
        self.run_act(FakeArr(), self.item, probe=lambda h: GOOD, tmdb=tmdb)
        self.assertEqual(tmdb.asked, [])

    def test_tmdb_not_readable_keeps_the_file(self):
        arr = FakeArr()
        e = self.run_act(arr, self.item, tmdb=FakeTmdb(error="GET /movie/4242: timed out"))
        self.assertEqual(e["verdict"], "unknown")
        self.assertFalse(arr.made("POST", "/") or arr.made("DELETE", "/"))

    def test_tmdb_other_languages_still_blocked(self):
        arr = FakeArr()
        e = self.run_act(arr, self.item, tmdb=FakeTmdb({"ara"}))
        self.assertEqual((e["verdict"], e["file"]), ("block", "removed"))

    def old_in_recycle_bin(self, name="A (2020) - [Bluray-1080p].mkv", sidecar=True):
        rb = self.root / "data/.recyclebin/A (2020)"
        rb.mkdir(parents=True, exist_ok=True)
        (rb / name).write_text("old")
        if sidecar:
            (rb / name.replace(".mkv", ".en.srt")).write_text("subs")
        self.item.update(upgrade=True, deleted=[f"/data/media/movies/A (2020)/{name}"], recycled=[f"/data/.recyclebin/A (2020)/{name}"])
        return rb / name

    def test_good_file_does_nothing(self):
        arr = FakeArr()
        e = self.run_act(arr, self.item, probe=lambda h: GOOD)
        self.assertEqual(e["verdict"], "ok")
        self.assertFalse(arr.made("POST", "/") or arr.made("DELETE", "/"))
        self.assertEqual(self.state["entries"], [])

    def test_unknown_does_nothing(self):
        arr = FakeArr()
        e = self.run_act(arr, self.item, probe=lambda h: [a("und")])
        self.assertEqual(e["verdict"], "unknown")
        self.assertFalse(arr.made("POST", "/") or arr.made("DELETE", "/"))

    def test_override_does_nothing(self):
        arr = FakeArr()
        e = self.run_act(arr, self.item, title=dict(MOVIE, tags=["override"]))
        self.assertEqual(e["verdict"], "skip")
        self.assertFalse(arr.made("POST", "/") or arr.made("DELETE", "/"))

    def test_new_title_bad_file_removed_blocked_searched(self):
        arr = FakeArr()
        e = self.run_act(arr, self.item)
        self.assertEqual(e["verdict"], "block")
        self.assertEqual(e["file"], "removed")
        self.assertEqual(arr.made("DELETE", "/moviefile/77")[0][1], "/moviefile/77")
        self.assertEqual(arr.made("POST", "/history/failed/")[0][1], "/history/failed/501")
        self.assertEqual(arr.made("POST", "/command")[0][3], {"name": "MoviesSearch", "movieIds": [12]})
        logged = self.state["entries"][0]
        self.assertEqual((logged["key"], logged["release"], logged["languages"], logged["searched"]), ("movie/4242", "A.2020.MULTi.1080p-GRP", ["French", "Italian"], True))

    def test_upgrade_restores_acceptable_previous_file(self):
        old = self.old_in_recycle_bin()
        arr = FakeArr()
        e = self.run_act(arr, self.item)
        self.assertEqual(e["file"], "previous restored")
        movie = self.root / "data/media/movies/A (2020)"
        self.assertTrue((movie / old.name).exists())
        self.assertTrue((movie / old.name.replace(".mkv", ".en.srt")).exists())
        self.assertFalse(old.exists())
        self.assertTrue(arr.made("DELETE", "/moviefile/77"))
        self.assertEqual(arr.made("POST", "/command")[0][3], {"name": "RescanMovie", "movieId": 12})
        self.assertEqual(arr.made("POST", "/command")[1][3], {"name": "MoviesSearch", "movieIds": [12]})
        self.assertTrue(arr.made("POST", "/history/failed/501"))

    def test_restore_order_previous_back_before_new_deleted(self):
        self.old_in_recycle_bin()
        seen = []
        arr = FakeArr()
        real = arr.__call__

        def spy(method, path, query=None, body=None):
            if method == "DELETE":
                seen.append((self.root / "data/media/movies/A (2020)/A (2020) - [Bluray-1080p].mkv").exists())
            return real(method, path, query, body)
        spy.app = "radarr"
        spy.made = arr.made
        lb.act("radarr", spy, self.item, MOVIE, lambda h: BAD if "MULTi" in h else GOOD, self.state, host=self.host)
        self.assertEqual(seen, [True])

    def test_upgrade_previous_also_bad_keeps_new(self):
        old = self.old_in_recycle_bin()
        arr = FakeArr()
        e = self.run_act(arr, self.item, probe=lambda h: BAD)
        self.assertEqual(e["file"], "kept")
        self.assertTrue(old.exists())
        self.assertFalse(arr.made("DELETE", "/"))
        self.assertTrue(arr.made("POST", "/history/failed/501"))

    def test_upgrade_previous_missing_keeps_new(self):
        self.item.update(upgrade=True, deleted=["/data/media/movies/A (2020)/gone.mkv"], recycled=[""])
        arr = FakeArr()
        e = self.run_act(arr, self.item)
        self.assertEqual(e["file"], "kept")
        self.assertFalse(arr.made("DELETE", "/"))
        self.assertTrue((self.root / self.item["path"].lstrip("/")).exists())

    def test_upgrade_without_deleted_paths_keeps_new(self):
        self.item.update(upgrade=True)
        arr = FakeArr()
        self.assertEqual(self.run_act(arr, self.item)["file"], "kept")
        self.assertFalse(arr.made("DELETE", "/"))

    def test_recycle_path_computed_when_not_given(self):
        old = self.old_in_recycle_bin()
        self.item["recycled"] = []
        e = self.run_act(FakeArr(), self.item)
        self.assertEqual(e["file"], "previous restored")
        self.assertFalse(old.exists())

    def test_restore_target_taken_keeps_new(self):
        old = self.old_in_recycle_bin()
        (self.root / "data/media/movies/A (2020)" / old.name).write_text("someone else")
        arr = FakeArr()
        self.assertEqual(self.run_act(arr, self.item)["file"], "kept")
        self.assertTrue(old.exists())
        self.assertFalse(arr.made("DELETE", "/"))

    def test_delete_failing_undoes_restore(self):
        old = self.old_in_recycle_bin()
        arr = FakeArr(fail={("DELETE", "moviefile")})
        e = self.run_act(arr, self.item)
        self.assertEqual(e["file"], "kept")
        self.assertTrue(old.exists())
        self.assertTrue(old.with_name(old.name.replace(".mkv", ".en.srt")).exists())
        self.assertFalse((self.root / "data/media/movies/A (2020)" / old.name).exists())
        self.assertTrue(arr.made("POST", "/history/failed/501"))

    def test_no_grab_event_nothing_done(self):
        arr = FakeArr(history=[])
        e = self.run_act(arr, self.item)
        self.assertEqual(e["verdict"], "block")
        self.assertEqual(e["file"], "kept")
        self.assertIn("no grab", e["note"])
        self.assertFalse(arr.made("DELETE", "/") or arr.made("POST", "/"))
        self.assertTrue(self.state["entries"])

    def test_no_download_id_nothing_done(self):
        arr = FakeArr()
        e = self.run_act(arr, dict(self.item, downloadId=""))
        self.assertEqual(e["file"], "kept")
        self.assertFalse(arr.made("DELETE", "/") or arr.made("POST", "/"))

    def test_no_file_id_keeps_file_but_blocks(self):
        arr = FakeArr()
        e = self.run_act(arr, dict(self.item, fileId=""))
        self.assertEqual(e["file"], "kept")
        self.assertFalse(arr.made("DELETE", "/"))
        self.assertTrue(arr.made("POST", "/history/failed/501"))

    def test_dry_run_changes_nothing(self):
        old = self.old_in_recycle_bin()
        arr = FakeArr()
        e = self.run_act(arr, self.item, dry=True)
        self.assertEqual(e["verdict"], "block")
        self.assertTrue(old.exists())
        self.assertFalse(arr.made("DELETE", "/") or arr.made("POST", "/"))
        self.assertEqual(self.state["entries"], [])

    def test_failed_mark_once_per_download(self):
        arr = FakeArr()
        self.run_act(arr, self.item)
        second = dict(self.item, fileId="78", path="/data/media/movies/A (2020)/A (2020) - [MULTi].mkv")
        self.run_act(arr, second, now=1001.0)
        self.assertEqual(len(arr.made("POST", "/history/failed/")), 1)
        self.assertEqual(len(arr.made("DELETE", "/moviefile/")), 2)

    def test_sonarr_episode_file(self):
        series = {"id": 5, "title": "A Show", "year": 2019, "tmdbId": 99, "originalLanguage": {"name": "Japanese"}, "tags": []}
        show = self.root / "data/media/tv/A Show/Season 01"
        show.mkdir(parents=True)
        (show / "e1 [MULTi].mkv").write_text("new")
        item = {"id": "5", "path": "/data/media/tv/A Show/Season 01/e1 [MULTi].mkv", "fileId": "9", "downloadId": "DL1", "upgrade": False, "deleted": [], "recycled": []}
        arr = FakeArr("sonarr")
        e = lb.act("sonarr", arr, item, series, lambda h: [a("fre")], self.state, host=self.host)
        self.assertEqual(e["file"], "removed")
        self.assertTrue(arr.made("DELETE", "/episodefile/9"))
        self.assertEqual(arr.made("POST", "/command")[0][3], {"name": "EpisodeSearch", "episodeIds": [31, 32]})
        self.assertEqual(self.state["entries"][0]["key"], "tv/99")

    def test_state_keeps_180_days(self):
        self.state["entries"] = [{"at": "1970-01-01T00:00:00Z", "key": "movie/1"}]
        self.state["downloads"] = [{"app": "radarr", "downloadId": "OLD", "at": "1970-01-01T00:00:00Z"}]
        self.run_act(FakeArr(), self.item, now=200 * 86400.0)
        self.assertEqual([x["key"] for x in self.state["entries"]], ["movie/4242"])
        self.assertEqual([x["downloadId"] for x in self.state["downloads"]], ["DL1"])


class Store(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "language-block.json"
            self.assertEqual(lb.load_state(p), {"entries": [], "downloads": []})
            lb.save_state(p, {"entries": [{"key": "movie/1"}], "downloads": []})
            self.assertEqual(lb.load_state(p)["entries"], [{"key": "movie/1"}])
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o644")
            p.write_text("{broken")
            with self.assertRaises(ValueError):
                lb.load_state(p)


class Library(unittest.TestCase):
    def test_counts_from_arr_media_info(self):
        movies = [
            dict(MOVIE, id=1, movieFile={"path": "/data/media/movies/a.mkv", "mediaInfo": {"audioLanguages": "eng"}}),
            dict(MOVIE, id=2, title="B", movieFile={"path": "/data/media/movies/b.mkv", "mediaInfo": {"audioLanguages": "fre/ita"}}),
            dict(MOVIE, id=3, title="C", movieFile={"path": "/data/media/movies/c.mkv", "mediaInfo": {"audioLanguages": ""}}),
            dict(MOVIE, id=4, title="D"),
        ]
        api = lambda method, path, query=None, body=None: movies if path == "/movie" else []
        rows = list(lb.library_rows("radarr", api))
        self.assertEqual([(r["title"], r["verdict"]) for r in rows], [("A Film", "ok"), ("B", "block"), ("C", "unknown")])

    def test_tmdb_spoken_languages_in_the_library(self):
        movies = [dict(MOVIE, id=1, title="P", movieFile={"path": "/data/media/movies/p.mkv", "mediaInfo": {"audioLanguages": "ara"}})]
        series = [{"id": 5, "title": "S", "year": 2020, "tmdbId": 77, "originalLanguage": {"name": "English"}, "tags": []}]
        files = [{"path": "/data/media/tv/s/1.mkv", "mediaInfo": {"audioLanguages": "rus"}}]
        api = lambda method, path, query=None, body=None: movies if path == "/movie" else series if path == "/series" else files if path == "/episodefile" else []
        tmdb = FakeTmdb({"ara", "rus"})
        self.assertEqual([r["verdict"] for r in lb.library_rows("radarr", api, tmdb)], ["ok"])
        self.assertEqual([r["verdict"] for r in lb.library_rows("sonarr", api, tmdb)], ["ok"])
        self.assertEqual(tmdb.asked, [("movie", 4242), ("tv", 77)])


if __name__ == "__main__":
    unittest.main()
