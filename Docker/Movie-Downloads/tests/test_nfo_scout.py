#!/usr/bin/env python3
"""scripts/nfo-scout.py: NFO audio-language parser (fixtures in tests/fixtures/nfo), indexer answers, title amendment and parse round trip, Treasure Maps budget, state and skips, push payload, against fake Prowlarr/Radarr/Sonarr/indexers.
Usage: python3 tests/test_nfo_scout.py"""
import importlib.util
import json
import pathlib
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("nfo", ROOT / "scripts" / "nfo-scout.py")
nfo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nfo)
FIX = ROOT / "tests" / "fixtures" / "nfo"
NOW = 1_800_000_000.0


class Parser(unittest.TestCase):
    def test_fixtures(self):
        expected = json.loads((FIX / "expected.json").read_text())
        self.assertGreaterEqual(len(expected), 10)
        for name, langs in expected.items():
            with self.subTest(nfo=name):
                self.assertEqual(nfo.audio_languages((FIX / name).read_text(encoding="utf-8")), langs)

    def test_every_language_maps_to_a_radarr_name(self):
        names = {l["name"] for l in json.loads((ROOT / "tests" / "fixtures" / "radarr-languages.json").read_text())}
        self.assertLessEqual(set(nfo.LEXICON.values()), names)

    def test_small_cases(self):
        cases = {
            "Audio: German, English": ["German", "English"],
            "AUDIO: DE / EN": ["German", "English"],
            "Audio: de, en": [],
            "Audio: English DTS-ES, German": ["English", "German"],
            "Language: Deutsch, Englisch\nSubtitles: French": ["German", "English"],
            "Audio: German subtitles, English AC3": [],
            "Audio: Japanese\nThe film is in Japanese and English.": ["Japanese"],
            "Audio: Portuguese (Brazil), English": ["Portuguese (Brazil)", "English"],
            "Audio: Latino, Castellano": ["Spanish (Latino)", "Spanish"],
            "": [],
        }
        for text, langs in cases.items():
            with self.subTest(text=text):
                self.assertEqual(nfo.audio_languages(text), langs)


RSS_NFO = ('<?xml version="1.0" encoding="utf-8" ?><rss><channel><title>NFO</title><item><title>X.2011.MULTi.1080p-GRP</title>'
           '<guid isPermaLink="true">https://api.example.net/details/abcdef0123456789abcdef0123456789</guid>'
           '<description>  AUDIO ....: Persian, French\n  SUBS .....: English</description></item></channel></rss>')
FEED = RSS_NFO.replace("abcdef0123456789abcdef0123456789", "ffffffffffffffffffffffffffffffff")
DETAILS = ('<rss><channel><item><title>A.Separation.2011.MULTi.1080p.BluRay.x264-ROUGH</title><guid isPermaLink="true">https://nzbgeek.info/geekseek.php?guid=abcdef0123456789abcdef0123456789</guid>'
           '<newznab:attr name="category" value="2000"/><newznab:attr name="language" value="French"/><newznab:attr name="language" value="Persian"/>'
           '<newznab:attr name="subs" value="English"/></item></channel></rss>')
GID = "abcdef0123456789abcdef0123456789"


class Answers(unittest.TestCase):
    def test_getnfo_rss(self):
        self.assertEqual(nfo.nfo_text(RSS_NFO, GID).strip().splitlines()[0].strip(), "AUDIO ....: Persian, French")

    def test_getnfo_plain_text(self):
        self.assertEqual(nfo.nfo_text("Audio: German, English\n", GID), "Audio: German, English\n")

    def test_no_nfo(self):
        self.assertIsNone(nfo.nfo_text('<?xml version="1.0"?><error code="300" description="No such item"/>', GID))

    def test_unsupported_function_or_a_feed_instead_of_the_item(self):
        for body in ('<error code="202" description="No such function (getnfo)"/>', FEED):
            with self.subTest(body=body[:40]):
                with self.assertRaises(nfo.Unsupported):
                    nfo.nfo_text(body, GID)

    def test_limits_and_refusals_stop_the_indexer(self):
        for body in ('<error code="500" description="Request limit reached"/>', '<error code="110" description="Request rejected"/>',
                     '<error code="100" description="Incorrect user credentials"/>'):
            with self.subTest(body=body):
                with self.assertRaises(nfo.Refused):
                    nfo.nfo_text(body, GID)

    def test_details_languages(self):
        self.assertEqual(nfo.details_languages(DETAILS, GID), ["French", "Persian"])
        self.assertEqual(nfo.details_languages(DETAILS.replace('<newznab:attr name="language" value="French"/><newznab:attr name="language" value="Persian"/>', ""), GID), [])
        with self.assertRaises(nfo.Unsupported):
            nfo.details_languages(DETAILS.replace(GID, "f" * 32), GID)

    def test_guid_id(self):
        self.assertEqual(nfo.guid_id("https://nzbgeek.info/geekseek.php?guid=9d3f6dee225e866545c9b1a96a31f461"), "9d3f6dee225e866545c9b1a96a31f461")
        self.assertEqual(nfo.guid_id("https://api.nzbplanet.net/details/f4566cb882f8b5ff09cf9b52d9c3690f"), "f4566cb882f8b5ff09cf9b52d9c3690f")
        self.assertIsNone(nfo.guid_id("magnet:?xt=urn:btih:180F"))


class Titles(unittest.TestCase):
    def test_multi_only(self):
        self.assertTrue(nfo.multi_marker("The.Secret.In.Their.Eyes.2009.MULTi.1080p.BluRay.x264-FHD"))
        self.assertTrue(nfo.multi_marker("separation.2011.multi.1080p.bluray.x264-rough"))
        self.assertTrue(nfo.multi_marker("Film 2020 Multi-Audio 1080p"))
        self.assertFalse(nfo.multi_marker("Multiverse.of.Madness.2022.1080p"))
        self.assertFalse(nfo.multi_marker("Film.2020.German.ML.1080p"))

    def test_amend(self):
        self.assertEqual(nfo.amend_title("The.Secret.In.Their.Eyes.2009.MULTi.1080p.BluRay.x264-FHD", ["Spanish", "French"]),
                         "The.Secret.In.Their.Eyes.2009.MULTi.Spanish.French.NFO.1080p.BluRay.x264-FHD")
        self.assertEqual(nfo.amend_title("Im.Still.Here.2024.MULTi.2160p.WEB-DL.H265-X", ["Portuguese (Brazil)", "English"]),
                         "Im.Still.Here.2024.MULTi.Brazilian.English.NFO.2160p.WEB-DL.H265-X")
        self.assertEqual(nfo.amend_title("Film.2020.Multi-Audio.1080p-X", ["Spanish (Latino)", "English"]), "Film.2020.Multi-Audio.Latino.English.NFO.1080p-X")


def fake_parse(movie_id=7, parseable=("Spanish", "French", "English", "German", "Persian", "Italian", "Japanese"), shift_quality=False):
    def parse(title):
        langs = [n for n in parseable if f".{n}." in f".{title}."] or ["Spanish"]
        q = "Bluray-2160p" if "2160p" in title else "Bluray-1080p"
        if shift_quality and ".NFO." in title:
            q = "Unknown"
        return {"movie": {"id": movie_id}, "parsedMovieInfo": {"year": 2009, "quality": {"quality": {"name": q, "resolution": 2160 if "2160p" in title else 1080}},
                "releaseGroup": title.rsplit("-", 1)[-1]}, "languages": [{"name": n} for n in langs], "customFormatScore": 1000 * len(langs)}
    return parse


class RoundTrip(unittest.TestCase):
    T = "The.Secret.In.Their.Eyes.2009.MULTi.1080p.BluRay.x264-FHD"

    def test_exact(self):
        title, langs, why = nfo.verified_title(self.T, ["Spanish", "French"], "Spanish", fake_parse())
        self.assertEqual((title, langs), ("The.Secret.In.Their.Eyes.2009.MULTi.Spanish.French.NFO.1080p.BluRay.x264-FHD", ["Spanish", "French"]))

    def test_unparseable_extra_is_dropped(self):
        title, langs, _ = nfo.verified_title(self.T, ["Spanish", "French", "Czech"], "Spanish", fake_parse())
        self.assertEqual(langs, ["Spanish", "French"])
        self.assertNotIn("Czech", title)

    def test_unparseable_main_language_fails(self):
        title, _, why = nfo.verified_title(self.T, ["Spanish", "German"], "Spanish", fake_parse(parseable=("Spanish",)))
        self.assertIsNone(title)
        self.assertIn("German", why)

    def test_quality_or_group_change_fails(self):
        self.assertIsNone(nfo.verified_title(self.T, ["Spanish", "French"], "Spanish", fake_parse(shift_quality=True))[0])

    def test_single_language_left_fails(self):
        self.assertIsNone(nfo.verified_title(self.T, ["Spanish", "Czech"], "Spanish", fake_parse())[0])


def usage(used=10, limit=100, age=60, ok=True, window="rolling"):
    return {"ok": ok, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - age)),
            "indexers": [{"name": "Treasure Maps", "window": window, "searches": {"used": used, "limit": limit}}]}


class Budget(unittest.TestCase):
    def budget(self, calls=(), **kw):
        b = nfo.Budget({"Treasure Maps": list(calls)}, now=NOW, tm_day=10, tm_pct=70, usage=kw.pop("u", usage()), max_fetch=200)
        return b

    def test_tm_daily_cap(self):
        self.assertTrue(self.budget([NOW - 100] * 9).allowed("Treasure Maps"))
        self.assertFalse(self.budget([NOW - 100] * 10).allowed("Treasure Maps"))
        self.assertTrue(self.budget([NOW - 90000] * 10).allowed("Treasure Maps"))

    def test_tm_70_percent_counts_own_calls_too(self):
        self.assertFalse(self.budget([NOW - 100] * 5, u=usage(used=65)).allowed("Treasure Maps"))
        self.assertTrue(self.budget([NOW - 100] * 5, u=usage(used=64)).allowed("Treasure Maps"))
        self.assertFalse(self.budget(u=usage(used=70)).allowed("Treasure Maps"))

    def test_tm_unknown_usage_is_no(self):
        for u in (None, usage(age=3600), usage(ok=False), {"ok": True, "generated": usage()["generated"], "indexers": []}):
            with self.subTest(u=u):
                self.assertFalse(self.budget(u=u).allowed("Treasure Maps"))

    def test_tm_without_limit_only_the_daily_cap(self):
        self.assertTrue(self.budget(u=usage(used=5000, limit=None)).allowed("Treasure Maps"))

    def test_spend_and_run_cap(self):
        b = self.budget()
        for _ in range(10):
            self.assertTrue(b.allowed("Treasure Maps"))
            b.spend("Treasure Maps")
        self.assertFalse(b.allowed("Treasure Maps"))
        b = nfo.Budget({}, now=NOW, tm_day=10, tm_pct=70, usage=usage(), max_fetch=2)
        b.spend("NZBgeek"); b.spend("NZBgeek")
        self.assertFalse(b.allowed("NZBgeek"))
        self.assertTrue(b.allowed("NzbPlanet"))

    def test_old_calls_are_pruned(self):
        b = self.budget([NOW - 90000, NOW - 10])
        self.assertEqual(b.calls["Treasure Maps"], [NOW - 10])


SECRET = {"id": 7, "title": "The Secret in Their Eyes", "year": 2009, "imdbId": "tt1305806", "tmdbId": 25376, "monitored": True, "hasFile": True,
          "tags": [], "originalLanguage": {"name": "Spanish"}, "secondaryYear": None}
P_GEEK = {"id": 1, "name": "NZBgeek", "protocol": "usenet", "enable": True, "capabilities": {"movieSearchParams": ["q", "imdbId"], "tvSearchParams": ["q", "tvdbId"]}}
P_PLANET = dict(P_GEEK, id=2, name="NzbPlanet")
P_TM = dict(P_GEEK, id=3, name="Treasure Maps")
P_INDEX = dict(P_GEEK, id=8, name="NZBIndex")
P_TPB = dict(P_GEEK, id=5, name="The Pirate Bay", protocol="torrent")


def rel(idx, title, gid, pid):
    host = {"NZBgeek": "https://nzbgeek.info/geekseek.php?guid=", "NzbPlanet": "https://api.nzbplanet.net/details/", "Treasure Maps": "https://treasure-maps.com/details/", "NZBIndex": "https://nzbindex.com/nzb/"}[idx]
    return {"guid": host + gid, "indexer": idx, "indexerId": pid, "title": title, "protocol": "usenet", "size": 13370177000, "publishDate": "2012-03-30T01:05:02Z",
            "downloadUrl": f"http://127.0.0.1:9696/{pid}/download?apikey=SECRETKEY&link=abc&file=x", "infoUrl": host + gid}


class FakeApi:
    def __init__(self, results, nfos, movies=(SECRET,), wanted=(7,), parse=None, fail=()):
        self.results, self.nfos, self.movies, self.wanted, self.fail = results, nfos, list(movies), set(wanted), set(fail)
        self.parse = parse or fake_parse()
        self.pushes, self.fetches, self.searches = [], [], []

    def prowlarr(self, method, path, query=None, body=None):
        if path == "/indexer":
            return [P_GEEK, P_PLANET, P_TM, P_INDEX, P_TPB]
        if path == "/search":
            self.searches.append(query)
            ids = set(query["indexerIds"])
            return [r for r in self.results if r["indexerId"] in ids]
        raise AssertionError(path)

    def radarr(self, method, path, query=None, body=None):
        if "radarr" in self.fail:
            raise nfo.ApiError("down")
        if path == "/movie":
            return self.movies
        if path == "/tag":
            return [{"id": 13, "label": "audio-swiss-german"}]
        if path in ("/wanted/missing", "/wanted/cutoff"):
            recs = [{"id": i} for i in self.wanted] if path == "/wanted/cutoff" else []
            return {"records": recs, "totalRecords": len(recs)}
        if path == "/indexer":
            return [{"id": 11, "name": n + " (Prowlarr)", "protocol": "usenet", "fields": [{"name": "baseUrl", "value": f"http://gluetun:9696/{i}/"}]}
                    for n, i in (("NZBgeek", 1), ("NzbPlanet", 2), ("Treasure Maps", 3), ("NZBIndex", 8))]
        if path == "/parse":
            return self.parse(query["title"])
        if path == "/release/push":
            self.pushes.append(body)
            return [{"approved": True}]
        raise AssertionError(path)

    def fetch(self, indexer, function, gid):
        self.fetches.append((indexer, function, gid))
        body = self.nfos.get((indexer, gid))
        if isinstance(body, Exception):
            raise body
        return body if body is not None else '<error code="300" description="No such item"/>'


SECRET_GEEK = rel("NZBgeek", "The.Secret.In.Their.Eyes.2009.MULTi.1080p.BluRay.x264-FHD", "a" * 32, 1)
SECRET_PLANET = rel("NzbPlanet", "The.Secret.In.Their.Eyes.2009.MULTi.2160p.BluRay.x265-HDX", "b" * 32, 2)
SECRET_TM = rel("Treasure Maps", "The.Secret.In.Their.Eyes.2009.MULTi.720p.BluRay.x264-TMG", "c" * 32, 3)
PLAIN = rel("NzbPlanet", "The.Secret.In.Their.Eyes.2009.1080p.BluRay.x264-PLAIN", "d" * 32, 2)
GEEK_DETAILS = DETAILS.replace("French", "Spanish").replace("Persian", "French").replace(GID, "a" * 32)
PLANET_NFO = RSS_NFO.replace("Persian, French", "Spanish, French").replace(GID, "b" * 32)


class Scout(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.dir.name) / "state.json"
        self.dub = pathlib.Path(self.dir.name) / "dub-only.json"
        self.log = []

    def tearDown(self):
        self.dir.cleanup()

    def run_scout(self, api, dry_run=False, max_push=3, u=None, **kw):
        kw.setdefault("dub_paths", {"Radarr": self.dub})
        s = nfo.Scout(radarr=api.radarr, sonarr=None, prowlarr=api.prowlarr, fetch=api.fetch, state_path=self.state, log=self.log.append,
                      dry_run=dry_run, max_push=max_push, usage=u if u is not None else usage(), now=NOW, **kw)
        return s.run()

    def dub_list(self):
        return json.loads(self.dub.read_text()) if self.dub.exists() else []

    def test_pushes_best_quality_first_with_languages_and_marker(self):
        api = FakeApi([SECRET_GEEK, SECRET_PLANET, PLAIN], {("NZBgeek", "a" * 32): GEEK_DETAILS, ("NzbPlanet", "b" * 32): PLANET_NFO})
        self.assertEqual(self.run_scout(api), 0)
        self.assertEqual([p["title"] for p in api.pushes], ["The.Secret.In.Their.Eyes.2009.MULTi.Spanish.French.NFO.2160p.BluRay.x265-HDX",
                                                           "The.Secret.In.Their.Eyes.2009.MULTi.Spanish.French.NFO.1080p.BluRay.x264-FHD"])
        p = api.pushes[0]
        self.assertEqual((p["protocol"], p["indexerId"], p["indexer"], p["tmdbId"], p["imdbId"]), ("usenet", 11, "NzbPlanet (Prowlarr)", 25376, 1305806))
        self.assertTrue(p["downloadUrl"].startswith("http://gluetun:9696/2/download?"))
        self.assertEqual(p["guid"], SECRET_PLANET["guid"])
        self.assertEqual([f[1] for f in api.fetches], ["getnfo", "details"])
        self.assertFalse(any("SECRETKEY" in l or "apikey" in l for l in self.log))

    def test_searches_only_usenet_indexers_with_a_source(self):
        api = FakeApi([], {})
        self.run_scout(api)
        self.assertEqual(sorted(i for q in api.searches for i in q["indexerIds"]), [1, 2, 3])

    def test_tm_skipped_at_70_percent(self):
        api = FakeApi([SECRET_TM], {("Treasure Maps", "c" * 32): RSS_NFO.replace(GID, "c" * 32)})
        self.run_scout(api, u=usage(used=70))
        self.assertEqual(api.fetches, [])
        self.assertNotIn(3, [i for q in api.searches for i in q["indexerIds"]])

    def test_tm_daily_getnfo_cap(self):
        rels = [rel("Treasure Maps", f"The.Secret.In.Their.Eyes.2009.MULTi.1080p.BluRay.x264-G{i}", f"{i:032x}", 3) for i in range(12)]
        api = FakeApi(rels, {})
        self.run_scout(api, max_push=0)
        self.assertEqual(len(api.fetches), 5)

    def test_state_none_and_pushed_are_not_redone(self):
        api = FakeApi([SECRET_GEEK, SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO})
        self.run_scout(api)
        self.assertEqual(len(api.pushes), 1)
        api2 = FakeApi([SECRET_GEEK, SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO})
        self.run_scout(api2)
        self.assertEqual((api2.fetches, api2.pushes), ([], []))

    def test_cached_languages_push_later_without_fetching(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO})
        self.run_scout(api, max_push=0)
        self.assertEqual((len(api.fetches), api.pushes), (1, []))
        api2 = FakeApi([SECRET_PLANET], {})
        self.run_scout(api2)
        self.assertEqual((api2.fetches, len(api2.pushes)), ([], 1))

    def test_dry_run_fetches_but_never_pushes(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO})
        self.run_scout(api, dry_run=True)
        self.assertEqual((len(api.fetches), api.pushes), (1, []))
        self.assertTrue(any("would push" in l and "Spanish.French.NFO" in l for l in self.log))
        self.assertFalse(any("SECRETKEY" in l for l in self.log))

    def test_unsupported_indexer_is_remembered(self):
        feed = FEED.replace("ffffffffffffffffffffffffffffffff", "9" * 32)
        api = FakeApi([SECRET_PLANET, dict(SECRET_PLANET, guid="https://api.nzbplanet.net/details/" + "e" * 32, title=SECRET_PLANET["title"] + "2")],
                      {("NzbPlanet", "b" * 32): feed})
        self.run_scout(api)
        self.assertEqual(len(api.fetches), 1)
        self.assertIn("NzbPlanet", json.loads(self.state.read_text())["unsupported"])
        api2 = FakeApi([SECRET_PLANET], {})
        self.run_scout(api2)
        self.assertEqual(api2.fetches, [])

    def test_indexer_without_source_is_never_fetched(self):
        api = FakeApi([rel("NZBIndex", SECRET_GEEK["title"], "f" * 32, 8)], {})
        self.run_scout(api)
        self.assertEqual(api.fetches, [])

    def test_no_language_or_one_language_no_push(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): RSS_NFO.replace("Persian, French", "MULTi").replace(GID, "b" * 32)})
        self.run_scout(api)
        self.assertEqual(api.pushes, [])
        self.assertEqual(json.loads(self.state.read_text())["releases"]["NzbPlanet|" + "b" * 32]["verdict"], "none")

    def test_original_language_missing_no_push(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): RSS_NFO.replace("Persian", "German").replace(GID, "b" * 32)})
        self.run_scout(api)
        self.assertEqual(api.pushes, [])

    def test_dub_only_release_is_listed_for_the_guard(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO.replace("Spanish, French", "German, French")})
        self.run_scout(api)
        self.assertEqual(api.pushes, [])
        self.assertEqual(self.dub_list(), [{"guid": "https://api.nzbplanet.net/details/" + "b" * 32, "title": SECRET_PLANET["title"], "indexer": "NzbPlanet",
                                            "langs": ["German", "French"], "original": "Spanish", "titleId": 25376, "at": int(NOW)}])
        self.assertEqual(oct(self.dub.stat().st_mode & 0o777), "0o664")

    def test_not_listed_when_the_original_is_there_or_the_nfo_is_thin(self):
        for nfo_text in (PLANET_NFO, PLANET_NFO.replace("Spanish, French", "German"), PLANET_NFO.replace("Spanish, French", "Latino, English"),
                         '<error code="300" description="No such item"/>'):
            with self.subTest(nfo=nfo_text[:60]):
                self.state.unlink(missing_ok=True)
                self.run_scout(FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): nfo_text}), max_push=0)
                self.assertEqual(self.dub_list(), [])

    def test_original_the_parser_cannot_name_is_never_listed(self):
        movie = dict(SECRET, originalLanguage={"name": "Tagalog"})
        self.run_scout(FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO.replace("Spanish, French", "German, French")}, movies=(movie,)))
        self.assertEqual(self.dub_list(), [])

    def test_dry_run_still_lists_and_a_rerun_does_not_duplicate(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO.replace("Spanish, French", "German, French")})
        self.run_scout(api, dry_run=True)
        self.run_scout(FakeApi([SECRET_PLANET], {}))
        self.assertEqual(len(self.dub_list()), 1)

    def test_entries_expire_after_30_days_and_others_are_kept(self):
        old = {"guid": "g1", "title": "Old", "indexer": "NZBgeek", "langs": ["German", "French"], "original": "Spanish", "titleId": 1, "at": int(NOW - 31 * 86400)}
        new = dict(old, guid="g2", title="New", at=int(NOW - 29 * 86400))
        self.dub.write_text(json.dumps([old, new]))
        self.run_scout(FakeApi([], {}))
        self.assertEqual([e["guid"] for e in self.dub_list()], ["g2"])

    def test_release_skipped_in_an_earlier_run_is_listed_without_fetching(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO.replace("Spanish, French", "German, French")})
        self.run_scout(api, dub_paths=None)
        self.assertFalse(self.dub.exists())
        api2 = FakeApi([SECRET_PLANET], {})
        self.run_scout(api2)
        self.assertEqual(api2.fetches, [])
        self.assertEqual([e["title"] for e in self.dub_list()], [SECRET_PLANET["title"]])

    def test_unreadable_list_is_rebuilt(self):
        self.dub.write_text("{nope")
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO.replace("Spanish, French", "German, French")})
        self.run_scout(api)
        self.assertEqual(len(self.dub_list()), 1)

    def test_titles_that_already_name_two_languages_or_other_movies_are_skipped(self):
        two = dict(SECRET_PLANET, title="The.Secret.In.Their.Eyes.2009.MULTi.French.Spanish.1080p-X")
        api = FakeApi([two], {}, parse=fake_parse())
        self.run_scout(api)
        other = FakeApi([SECRET_PLANET], {}, parse=fake_parse(movie_id=99))
        self.run_scout(other)
        self.assertEqual((api.fetches, other.fetches), ([], []))

    def test_swiss_german_titles_and_titles_at_cutoff_are_out_of_scope(self):
        api = FakeApi([SECRET_PLANET], {}, movies=[dict(SECRET, tags=[13])])
        self.run_scout(api)
        api2 = FakeApi([SECRET_PLANET], {}, wanted=())
        self.run_scout(api2)
        self.assertEqual((api.searches, api2.searches), ([], []))

    def test_fetch_errors_retry_then_drop(self):
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): nfo.ApiError("timeout")})
        for _ in range(4):
            self.run_scout(api)
        self.assertEqual(len(api.fetches), 3)

    def test_refused_stops_that_indexer_for_the_run(self):
        p2 = dict(SECRET_PLANET, guid="https://api.nzbplanet.net/details/" + "e" * 32, title=SECRET_PLANET["title"].replace("HDX", "HDY"))
        api = FakeApi([SECRET_PLANET, p2], {("NzbPlanet", "b" * 32): '<error code="500" description="Request limit reached"/>',
                                            ("NzbPlanet", "e" * 32): '<error code="500" description="Request limit reached"/>'})
        self.run_scout(api)
        self.assertEqual(len(api.fetches), 1)
        self.assertNotIn("NzbPlanet", json.loads(self.state.read_text()).get("unsupported", {}))

    def test_sonarr_series_push_carries_the_tvdb_id(self):
        heist = {"id": 4, "title": "Money Heist", "year": 2017, "tvdbId": 327417, "imdbId": "tt6468322", "monitored": True, "tags": [],
                 "originalLanguage": {"name": "Spanish"}}
        pack = rel("NzbPlanet", "Money.Heist.S01.MULTi.1080p.NF.WEB-DL.DDP5.1.x264-GRP", "b" * 32, 2)

        def sonarr(method, path, query=None, body=None):
            if path == "/series":
                return [heist]
            if path in ("/wanted/missing", "/wanted/cutoff"):
                recs = [{"id": 101, "seriesId": 4}] if path == "/wanted/missing" else []
                return {"records": recs, "totalRecords": len(recs)}
            if path == "/parse":
                p = fake_parse()(query["title"])
                return {"series": {"id": 4}, "episodes": [{"id": 101}], "parsedEpisodeInfo": dict(p["parsedMovieInfo"], seriesTitleInfo={"year": 0}),
                        "languages": p["languages"], "customFormatScore": p["customFormatScore"]}
            return api.radarr(method, path, query, body)
        api = FakeApi([pack], {("NzbPlanet", "b" * 32): PLANET_NFO})
        s = nfo.Scout(radarr=None, sonarr=sonarr, prowlarr=api.prowlarr, fetch=api.fetch, state_path=self.state, log=self.log.append, usage=usage(), now=NOW)
        self.assertEqual(s.run(), 0)
        self.assertEqual([q["type"] for q in api.searches], ["tvsearch"])
        self.assertEqual(api.searches[0]["query"], "{TvdbId:327417}")
        self.assertEqual(len(api.pushes), 1)
        self.assertEqual((api.pushes[0]["title"], api.pushes[0]["tvdbId"]), ("Money.Heist.S01.MULTi.Spanish.French.NFO.1080p.NF.WEB-DL.DDP5.1.x264-GRP", 327417))
        self.assertNotIn("tmdbId", api.pushes[0])

    def test_refusal_is_remembered_for_a_day(self):
        limit = '<error code="500" description="Request limit reached"/>'
        api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): limit})
        self.run_scout(api)
        api2 = FakeApi([SECRET_PLANET], {})
        self.run_scout(api2)
        self.assertEqual((len(api.fetches), api2.fetches), (1, []))
        s = nfo.Scout(radarr=api2.radarr, sonarr=None, prowlarr=api2.prowlarr, fetch=api2.fetch, state_path=self.state, log=self.log.append,
                      usage=usage(), now=NOW + 86400 + 1)
        s.run()
        self.assertEqual(len(api2.fetches), 1)

    def test_no_gain_or_rejected_score_is_not_pushed(self):
        for score in (lambda n: -50000000 + n, lambda n: 1000):
            def parse(title, base=fake_parse(), score=score):
                p = base(title)
                p["customFormatScore"] = score(p["customFormatScore"])
                return p
            api = FakeApi([SECRET_PLANET], {("NzbPlanet", "b" * 32): PLANET_NFO}, parse=parse)
            self.state.unlink(missing_ok=True)
            self.run_scout(api)
            self.assertEqual(api.pushes, [])

    def test_radarr_error_returns_1(self):
        self.assertEqual(self.run_scout(FakeApi([], {}, fail={"radarr"})), 1)


if __name__ == "__main__":
    unittest.main()
