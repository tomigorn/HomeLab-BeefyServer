#!/usr/bin/env python3
"""scripts/description-scout.py: description matching, movie-mapping guard, dedupe, error paths and the push payload, against fake Radarr/Prowlarr/FlareSolverr.
Usage: python3 tests/test_description_scout.py"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("scout", ROOT / "scripts" / "description-scout.py")
scout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scout)
LANGS = scout.load_languages(ROOT / "scripts" / "languages.tsv", ["gsw"])
GSW = LANGS[0]

RING = {"id": 21, "title": "The Ring Thing", "originalTitle": "The Ring Thing", "year": 2004, "secondaryYear": None,
        "imdbId": "tt0432028", "tmdbId": 10248, "monitored": True, "hasFile": False, "tags": [13]}
MAGNET = "magnet:?xt=urn:btih:180F491CAA6F63C4469294D239A5A98E02FBB9A5&dn=The%20Ring%20Thing"
TPB = {"guid": MAGNET, "indexer": "The Pirate Bay", "indexerId": 5, "title": "The Ring Thing (2004) [XVID]", "seeders": 3,
       "leechers": 1, "size": 912260375, "imdbId": 432028, "infoUrl": "https://thepiratebay.org/description.php?id=3872789",
       "publishDate": "2007-11-04T16:56:48Z", "protocol": "torrent"}
APIBAY = ('<html><head><meta charset="utf-8"></head><body><pre>{"id":3872789,"name":"The Ring Thing (2004) [XVID]","seeders":0,'
          '"descr":"The Ring Thing (2004)\\r\\n\\r\\nAudio: Vorbis\\r\\nLanguage: Schwyzerd&amp;Atilde;&amp;frac14;tsch &amp;amp; Deutsch\\r\\n",'
          '"imdb":"tt0432028","info_hash":"180F491CAA6F63C4469294D239A5A98E02FBB9A5"}</pre></body></html>')
PAGE_1337 = ('<html><body><div class="tab-pane active" id="description"><p><strong>Release: The.Ring.Thing.2004.DVDRip</strong>'
             '<br><br><strong>IMDB: </strong><a href="https://www.imdb.com/title/tt0432028/">imdb</a><br>Language &nbsp; : Schwiizertüütsch'
             '</p></div><!-- description --><div class="tab-pane" id="comments">Swiss German please!</div>'
             '<a href="magnet:?xt=urn:btih:ABCDEF&amp;dn=Ring">Magnet</a></body></html>')


def parsed(movie_id=21, year=2004):
    return {"movie": {"id": movie_id} if movie_id else None, "parsedMovieInfo": {"year": year}}


class Matching(unittest.TestCase):
    def test_mojibake_and_entities_match(self):
        for d in ("Language: Schwyzerd&Atilde;&frac14;tsch", "Language: SchwyzerdÃ¼tsch & Deutsch", "Sprache: Schweizerdeutsch",
                  "Audio: Swiss German, Subtitles: German", "Ton: Mundart"):
            with self.subTest(d=d):
                self.assertTrue(scout.description_matches(d, GSW["regex"]))

    def test_no_language_or_only_subtitles_do_not_match(self):
        for d in ("Language: Deutsch", "Plot: Middle Earth is in the heart of Switzerland, Swiss cheese fondue",
                  "Subtitles: Swiss German", "Audio: German\nUntertitel: Schweizerdeutsch", ""):
            with self.subTest(d=d):
                self.assertFalse(scout.description_matches(d, GSW["regex"]))

    def test_apibay_page(self):
        info = scout.tpb_from_apibay(APIBAY)
        self.assertIn("SchwyzerdÃ¼tsch", info["descr"])
        self.assertEqual(info["imdb"], "tt0432028")
        self.assertEqual(info["info_hash"], "180F491CAA6F63C4469294D239A5A98E02FBB9A5")
        self.assertTrue(scout.description_matches(info["descr"], GSW["regex"]))

    def test_1337x_page_reads_only_the_description_block(self):
        info = scout.x1337_from_page(PAGE_1337)
        self.assertIn("Schwiizertüütsch", info["descr"])
        self.assertNotIn("please", info["descr"])
        self.assertEqual(info["imdb"], "tt0432028")
        self.assertEqual(info["magnet"], "magnet:?xt=urn:btih:ABCDEF&dn=Ring")

    def test_language_token_matches_its_own_pattern(self):
        self.assertEqual(GSW["tag"], "audio-swiss-german")
        self.assertRegex(GSW["token"], GSW["regex"])

    def test_language_without_audio_pattern_is_skipped(self):
        self.assertEqual(scout.load_languages(ROOT / "scripts" / "languages.tsv", ["fi"]), [])

    def test_description_sources(self):
        self.assertEqual(scout.description_source(TPB), ("tpb", "3872789"))
        self.assertEqual(scout.description_source({"infoUrl": "https://knaben.xyz/thepiratebay/description.php?id=81469793"}), ("tpb", "81469793"))
        self.assertEqual(scout.description_source({"infoUrl": "https://1337x.to/torrent/1/x/"}), ("1337x", "https://1337x.to/torrent/1/x/"))
        self.assertIsNone(scout.description_source({"infoUrl": "https://knaben.xyz/rutracker/1"}))


class Mapping(unittest.TestCase):
    def test_same_movie(self):
        self.assertTrue(scout.mapping_ok(RING, parsed(), "tt0432028")[0])
        self.assertTrue(scout.mapping_ok(RING, parsed(), None)[0])

    def test_other_movie_other_year_or_other_imdb(self):
        self.assertFalse(scout.mapping_ok(RING, parsed(movie_id=None), None)[0])
        self.assertFalse(scout.mapping_ok(RING, parsed(movie_id=99), None)[0])
        self.assertFalse(scout.mapping_ok(RING, parsed(year=2005), None)[0])
        self.assertFalse(scout.mapping_ok(RING, parsed(year=0), None)[0])
        self.assertFalse(scout.mapping_ok(RING, parsed(), "tt9999999")[0])

    def test_secondary_year_counts(self):
        self.assertTrue(scout.mapping_ok(dict(RING, secondaryYear=2005), parsed(year=2005), None)[0])

    def test_push_payload(self):
        p = scout.push_payload(TPB, RING, GSW, MAGNET, {"id": 4, "name": "The Pirate Bay (Prowlarr)"})
        self.assertEqual(p["title"], "The Ring Thing (2004) [XVID].Swissgerman.DESC")
        self.assertEqual((p["magnetUrl"], p["downloadUrl"], p["protocol"]), (MAGNET, MAGNET, "torrent"))
        self.assertEqual((p["indexerId"], p["indexer"]), (4, "The Pirate Bay (Prowlarr)"))
        self.assertEqual((p["seeders"], p["size"], p["publishDate"], p["tmdbId"], p["imdbId"]), (3, 912260375, "2007-11-04T16:56:48Z", 10248, 432028))
        self.assertEqual(p["infoUrl"], TPB["infoUrl"])


class FakeApi:
    """Radarr + Prowlarr + FlareSolverr in one, records writes."""

    def __init__(self, movies=None, results=None, pages=None, fail=()):
        self.movies = [RING] if movies is None else movies
        self.results = [TPB] if results is None else results
        self.pages = {"https://apibay.org/t.php?id=3872789": APIBAY} if pages is None else pages
        self.fail = set(fail)
        self.pushes, self.fetches, self.searches = [], [], []

    def radarr(self, method, path, query=None, body=None):
        if "radarr" in self.fail:
            raise scout.ApiError("radarr down")
        if path == "/tag":
            return [{"id": 13, "label": "audio-swiss-german"}]
        if path == "/movie":
            return self.movies
        if path == "/indexer":
            return [{"id": 4, "name": "The Pirate Bay (Prowlarr)", "protocol": "torrent"}, {"id": 6, "name": "1337x (Prowlarr)", "protocol": "torrent"}]
        if path == "/parse":
            t = query["title"]
            return parsed() if t.startswith(("The Ring Thing", "The.Ring.Thing")) and "2004" in t else parsed(movie_id=None)
        if path == "/release/push" and method == "POST":
            self.pushes.append(body)
            return [{"approved": True, "rejections": []}]
        raise AssertionError(path)

    def prowlarr(self, method, path, query=None, body=None):
        if "prowlarr" in self.fail:
            raise scout.ApiError("prowlarr down")
        if path == "/indexer":
            return [{"id": 5, "name": "The Pirate Bay", "protocol": "torrent", "enable": True, "capabilities": {"movieSearchParams": ["q"]}},
                    {"id": 4, "name": "1337x", "protocol": "torrent", "enable": True, "capabilities": {"movieSearchParams": ["q"]}},
                    {"id": 1, "name": "NZBgeek", "protocol": "usenet", "enable": True, "capabilities": {"movieSearchParams": ["q", "imdbId"]}}]
        if path == "/search":
            self.searches.append(query)
            return self.results
        raise AssertionError(path)

    def fetch(self, url):
        self.fetches.append(url)
        if "fetch" in self.fail or url not in self.pages:
            raise scout.ApiError("fetch failed")
        return self.pages[url]


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state.json"
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_scout(self, api, dry_run=False, max_push=2, max_fetch=10, tmdb=None):
        s = scout.Scout(api.radarr, api.prowlarr, api.fetch, self.state, self.log.append, dry_run=dry_run, max_push=max_push, max_fetch=max_fetch)
        return s.run(LANGS, tmdb=tmdb)

    def test_match_with_seeders_is_pushed_once(self):
        api = FakeApi()
        self.assertEqual(self.run_scout(api), 0)
        self.assertEqual(len(api.pushes), 1)
        self.assertEqual(api.pushes[0]["title"], "The Ring Thing (2004) [XVID].Swissgerman.DESC")
        self.assertEqual(api.pushes[0]["magnetUrl"], MAGNET)
        self.assertEqual(self.run_scout(api), 0)
        self.assertEqual(len(api.pushes), 1)
        self.assertEqual(len(api.fetches), 1)

    def test_searches_torrent_indexers_only(self):
        api = FakeApi()
        self.run_scout(api)
        self.assertEqual(api.searches[0]["indexerIds"], [5, 4])
        self.assertEqual(api.searches[0]["query"], "The Ring Thing 2004")
        self.assertEqual(api.searches[0]["type"], "movie")

    def test_original_title_searched_too(self):
        api = FakeApi(movies=[dict(RING, originalTitle="Das Ding mit dem Ring")])
        self.run_scout(api)
        self.assertEqual([q["query"] for q in api.searches], ["The Ring Thing 2004", "Das Ding mit dem Ring 2004"])

    def test_dead_torrent_is_not_pushed_and_its_description_is_not_refetched(self):
        api = FakeApi(results=[dict(TPB, seeders=0)])
        self.run_scout(api)
        self.run_scout(api)
        self.assertEqual(api.pushes, [])
        self.assertEqual(len(api.fetches), 1)
        self.assertTrue(any("seeders 0" in l for l in self.log))

    def test_dry_run_pushes_nothing_and_logs_the_payload(self):
        api = FakeApi()
        self.run_scout(api, dry_run=True)
        self.assertEqual(api.pushes, [])
        self.assertTrue(any("would push" in l and ".Swissgerman.DESC" in l for l in self.log))
        self.assertFalse(self.state.exists() and json.loads(self.state.read_text()).get("btih:180F491CAA6F63C4469294D239A5A98E02FBB9A5", {}).get("pushed"))

    def test_untagged_with_file_or_unmonitored_movies_are_ignored(self):
        for m in (dict(RING, tags=[]), dict(RING, hasFile=True), dict(RING, monitored=False)):
            api = FakeApi(movies=[m])
            self.run_scout(api)
            self.assertEqual((api.searches, api.pushes), ([], []))

    def test_uncertain_mapping_is_never_pushed_or_fetched(self):
        for r in (dict(TPB, title="The Ring Thing (2005) [XVID]"), dict(TPB, title="Ring Things 2004"), dict(TPB, imdbId=1234567)):
            api = FakeApi(results=[r])
            self.run_scout(api)
            self.assertEqual((api.fetches, api.pushes), ([], []), r["title"])

    def test_imdb_in_description_must_agree(self):
        api = FakeApi(pages={"https://apibay.org/t.php?id=3872789": APIBAY.replace("tt0432028", "tt7654321")})
        self.run_scout(api)
        self.assertEqual(api.pushes, [])

    def test_name_already_matching_is_left_to_radarr(self):
        api = FakeApi(results=[dict(TPB, title="The.Ring.Thing.2004.SwissGerman.DVDRip")])
        self.run_scout(api)
        self.assertEqual((api.fetches, api.pushes), ([], []))

    def test_usenet_results_are_skipped(self):
        api = FakeApi(results=[dict(TPB, protocol="usenet")])
        self.run_scout(api)
        self.assertEqual((api.fetches, api.pushes), ([], []))

    def test_description_without_language_is_remembered(self):
        api = FakeApi(pages={"https://apibay.org/t.php?id=3872789": APIBAY.replace("Schwyzerd&amp;Atilde;&amp;frac14;tsch &amp;amp; ", "")})
        self.run_scout(api)
        self.run_scout(api)
        self.assertEqual(api.pushes, [])
        self.assertEqual(len(api.fetches), 1)

    def test_push_cap(self):
        other = dict(TPB, guid="magnet:?xt=urn:btih:BBBB&dn=x", infoUrl="https://thepiratebay.org/description.php?id=2", title="The.Ring.Thing.2004.DVDRip")
        api = FakeApi(results=[TPB, other], pages={"https://apibay.org/t.php?id=3872789": APIBAY,
                                                    "https://apibay.org/t.php?id=2": APIBAY.replace("180F491CAA6F63C4469294D239A5A98E02FBB9A5", "BBBB")})
        self.run_scout(api, max_push=1)
        self.assertEqual(len(api.pushes), 1)
        self.run_scout(api, max_push=1)
        self.assertEqual(len(api.pushes), 2)

    def test_fetch_error_is_retried_a_bounded_number_of_times(self):
        api = FakeApi(fail={"fetch"})
        for _ in range(5):
            self.assertEqual(self.run_scout(api), 0)
        self.assertEqual(len(api.fetches), scout.MAX_ERRORS)
        self.assertEqual(api.pushes, [])

    def test_api_error_skips_the_run(self):
        for f in ("radarr", "prowlarr"):
            api = FakeApi(fail={f})
            self.assertEqual(self.run_scout(api), 1)
            self.assertEqual(api.pushes, [])

    def test_only_the_requested_movie(self):
        api = FakeApi()
        self.run_scout(api, tmdb=1)
        self.assertEqual(api.searches, [])


if __name__ == "__main__":
    unittest.main()
