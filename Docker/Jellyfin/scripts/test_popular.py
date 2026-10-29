import datetime
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("popular", pathlib.Path(__file__).with_name("popular.py"))
pop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pop)

HEAD = "country_name\tcountry_iso2\tweek\tcategory\tweekly_rank\tshow_title\tseason_title\tcumulative_weeks_in_top_10"


def tsv(*rows):
    return [HEAD] + ["\t".join(str(c) for c in r) for r in rows]


def hit(kind, tid, title, popularity, original=None):
    key, okey = ("title", "originalTitle") if kind == "movie" else ("name", "originalName")
    return {"mediaType": kind, "id": tid, key: title, okey: original or title, "popularity": popularity}


def seasons(*names, aired="2020-01-01"):
    return [{"seasonNumber": 0, "name": "Specials", "airDate": aired}] + [{"seasonNumber": i, "name": n, "airDate": aired} for i, n in enumerate(names, 1)]


def rel(title, size_gb=5.0, seeders=None, protocol="usenet"):
    return {"title": title, "size": int(size_gb * 1e9), "seeders": seeders, "protocol": protocol}


class Netflix(unittest.TestCase):
    def test_newest_week_of_the_country_only(self):
        lines = tsv(
            ("Switzerland", "CH", "2026-10-11", "Films", 1, "Old", "N/A", 3),
            ("Switzerland", "CH", "2026-10-18", "TV", 2, "The Diplomat", "The Diplomat: Season 4", 1),
            ("Switzerland", "CH", "2026-10-18", "Films", 1, "Animals", "N/A", 2),
            ("Switzerland", "CH", "2026-10-18", "TV", 1, "Below", "Below: Limited Series", 2),
            ("Germany", "DE", "2026-10-25", "Films", 1, "Elsewhere", "N/A", 1),
        )
        week = pop.parse_week(lines, "CH")
        self.assertEqual(week["week"], "2026-10-18")
        self.assertEqual([e["title"] for e in week["movie"]], ["Animals"])
        self.assertEqual([(e["rank"], e["title"], e["season"]) for e in week["tv"]], [(1, "Below", "Below: Limited Series"), (2, "The Diplomat", "The Diplomat: Season 4")])
        self.assertIsNone(week["movie"][0]["season"])

    def test_no_rows_for_the_country(self):
        self.assertIsNone(pop.parse_week(tsv(("Germany", "DE", "2026-10-18", "Films", 1, "X", "N/A", 1)), "CH"))

    def test_due_week_is_out_on_tuesday(self):
        self.assertEqual(pop.due_week(datetime.datetime(2026, 10, 27, 22, 20)), "2026-10-25")
        self.assertEqual(pop.due_week(datetime.datetime(2026, 10, 26, 22, 20)), "2026-10-18")


class Match(unittest.TestCase):
    def test_exact_title_most_popular_of_the_kind(self):
        hits = [hit("tv", 1, "The Diplomat", 84.8), hit("tv", 2, "The Diplomat", 6.6), hit("movie", 3, "The Diplomat", 99)]
        self.assertEqual(pop.pick("The Diplomat", hits, "tv")["id"], 1)
        self.assertEqual(pop.pick("The Diplomat", hits, "movie")["id"], 3)

    def test_case_accents_punctuation_and_original_title(self):
        self.assertEqual(pop.pick("UNABOMBER", [hit("movie", 7, "Unabomber", 3)], "movie")["id"], 7)
        self.assertEqual(pop.pick("Amelie and Co", [hit("movie", 8, "Amélie & Co.", 2)], "movie")["id"], 8)
        self.assertEqual(pop.pick("La casa de papel", [hit("tv", 9, "Money Heist", 50, "La casa de papel")], "tv")["id"], 9)

    def test_no_loose_match(self):
        self.assertIsNone(pop.pick("Ice Age", [hit("movie", 1, "Ice Age: Collision Course", 40)], "movie"))

    def test_overrides(self):
        text = "# kind\tnetflix title\ttmdb id\ntv\tInstadocs: AI Gone Wild\t123\nmovie\tUNABOMBER\t456\n\n"
        self.assertEqual(pop.load_overrides(text), {("tv", "instadocs ai gone wild"): 123, ("movie", "unabomber"): 456})


class Season(unittest.TestCase):
    TODAY = "2026-10-29"

    def test_named(self):
        self.assertEqual(pop.season_number("The Diplomat", "The Diplomat: Season 4", seasons("1", "2", "3", "4"), self.TODAY), 4)
        self.assertEqual(pop.season_number("Peaky", "Peaky: Series 2", seasons("1", "2"), self.TODAY), 2)
        self.assertIsNone(pop.season_number("X", "X: Season 5", seasons("1"), self.TODAY))

    def test_limited_series_and_tmdb_season_names(self):
        self.assertEqual(pop.season_number("Below", "Below: Limited Series", seasons("Below"), self.TODAY), 1)
        s = seasons("Monster: The Jeffrey Dahmer Story", "The Lyle and Erik Menendez Story", "The Ed Gein Story")
        self.assertEqual(pop.season_number("Monster", "Monster: The Ed Gein Story", s, self.TODAY), 3)

    def test_unnamed_is_the_latest_aired(self):
        s = seasons("A", "B", "C")
        s[3]["airDate"] = "2027-03-01"
        self.assertEqual(pop.season_number("Show", None, s, self.TODAY), 2)
        self.assertEqual(pop.season_number("Show", "Show: Part 2", s, self.TODAY), 2)
        self.assertIsNone(pop.season_number("Show", None, [{"seasonNumber": 0}], self.TODAY))


class Release(unittest.TestCase):
    def ok(self, r, kind="movie", lang="en", title="Inception", year=2010, season=None, text=False):
        return pop.suitable(r, kind, lang, title, year, season, text)

    def test_resolution(self):
        self.assertEqual(self.ok(rel("Inception.2010.2160p.UHD.BluRay.x265-GRP", 30)), "2160p")
        self.assertEqual(self.ok(rel("Inception.2010.1080p.NF.WEB-DL.DDP5.1.H.264-TURG", 4.4)), "1080p")
        self.assertIsNone(self.ok(rel("inception.2010.720p.web.h264-jff")))
        self.assertIsNone(self.ok(rel("Inception [2010]")))

    def test_rejects_cams_and_tiny_files(self):
        self.assertIsNone(self.ok(rel("Inception.2010.1080p.HDCAM.x264")))
        self.assertIsNone(self.ok(rel("Inception.2010.1080p.TS.x264")))
        self.assertIsNone(self.ok(rel("Inception.2010.1080p.WEB.x264", 0.9)))

    def test_dub_without_original_is_rejected(self):
        self.assertIsNone(self.ok(rel("Inception.2010.German.1080p.BluRay.x264-GRP")))
        self.assertIsNone(self.ok(rel("Inception.(2010).VFF.2160p.DTS.5.1.HEVC-Serpico")))
        self.assertEqual(self.ok(rel("Inception.2010.German.DL.1080p.BluRay.x264-GRP")), "1080p")
        self.assertEqual(self.ok(rel("Inception.2010.BluRay.1080p.x265.10bit.2Audio.MNHD-FRDS", 7)), "1080p")
        self.assertEqual(self.ok(rel("Dark.S01.German.1080p.WEB.x264-GRP", 9), "tv", "de", "Dark", None, 1), "1080p")

    def test_torrents_need_a_seeder(self):
        self.assertIsNone(self.ok(rel("Inception.2010.1080p.WEB.x264", 5, 0, "torrent")))
        self.assertEqual(self.ok(rel("Inception.2010.1080p.WEB.x264", 5, 3, "torrent")), "1080p")

    def test_tv_sizes(self):
        self.assertEqual(self.ok(rel("The.Diplomat.US.S04E03.1080p.HEVC.x265-MeGusta", 0.6), "tv", "en", "The Diplomat", None, 4), "1080p")
        self.assertIsNone(self.ok(rel("The.Diplomat.US.S04E03.1080p.x265", 0.1), "tv", "en", "The Diplomat", None, 4))
        self.assertIsNone(self.ok(rel("The.Diplomat.S04.1080p.x265", 0.5), "tv", "en", "The Diplomat", None, 4))

    def test_text_search_needs_the_name(self):
        self.assertEqual(self.ok(rel("Inception 2010 1080p AMZN WEB-DL DDP5 1 H 265-ViSTA", 4, 10, "torrent"), text=True), "1080p")
        self.assertIsNone(self.ok(rel("Inception Behind the Scenes 2010 1080p WEB", 4, 10, "torrent"), text=True))
        self.assertIsNone(self.ok(rel("Inception 2003 1080p WEB", 4, 10, "torrent"), text=True))
        tv = rel("The.Diplomat.2023.S04.2160p.10bit.NF.WEBRip.DDP5.1.HEVC.x265-Vyndros", 23.5, 172, "torrent")
        self.assertEqual(pop.suitable(tv, "tv", "en", "The Diplomat", 2023, 4, True), "2160p")
        self.assertIsNone(pop.suitable(tv, "tv", "en", "The Diplomat", 2023, 3, True))

    def test_best(self):
        rs = [rel("Inception.2010.720p.x264"), rel("Inception.2010.1080p.WEB.x264"), rel("Inception.2010.2160p.WEB.x265", 20)]
        self.assertEqual(pop.best(rs, "movie", "en", "Inception", 2010, None, False), "2160p")
        self.assertIsNone(pop.best([], "movie", "en", "Inception", 2010, None, False))


class Lists(unittest.TestCase):
    def test_merge_by_popularity(self):
        movies = [{"id": 1, "popularity": 50}, {"id": 2, "popularity": 10}]
        shows = [{"id": 3, "popularity": 30}, {"id": 1, "popularity": 5}]
        self.assertEqual(pop.merge(movies, shows, 3), [("movie", 1), ("tv", 3), ("movie", 2)])

    def test_refs(self):
        self.assertEqual(pop.ref("movie", 5, None), "movie/5")
        self.assertEqual(pop.ref("tv", 5, 3), "tv/5/s3")

    def test_due(self):
        now = datetime.datetime(2026, 10, 29, 4, 39)
        self.assertTrue(pop.check_due(None, now))
        # no release: again the next night (also 04:39 after 22:20 the evening before); found: weekly
        self.assertFalse(pop.check_due({"res": None, "at": "2026-10-29T00:39:00"}, now))
        self.assertTrue(pop.check_due({"res": None, "at": "2026-10-28T04:39:00"}, now))
        self.assertTrue(pop.check_due({"res": None, "at": "2026-10-28T22:20:00"}, datetime.datetime(2026, 10, 29, 22, 20)))
        self.assertFalse(pop.check_due({"res": "1080p", "at": "2026-10-25T04:39:00"}, now))
        self.assertTrue(pop.check_due({"res": "1080p", "at": "2026-10-21T04:39:00"}, now))


class Script(unittest.TestCase):
    def test_script_is_json_and_cannot_close_the_tag(self):
        src = pop.script({"rows": [{"t": "</script><b>"}]})
        self.assertTrue(src.startswith("window.HssPopular = "))
        self.assertNotIn("</", src)
        self.assertEqual(json.loads(src[len("window.HssPopular = "):].rstrip().rstrip(";").replace("<\\/", "</")), {"rows": [{"t": "</script><b>"}]})


class FakeIo:
    def __init__(self):
        self.searches, self.scripts, self.tsv_fetches = [], [], 0
        self.injector = {"CustomJavaScripts": []}
        self.releases = {"movie/101": [rel("Animals.2026.1080p.NF.WEB-DL.x264", 4)], "tv/202/s4": [rel("The.Diplomat.US.S04E01.2160p.NF.WEB-DL.x265", 3)]}

    def tsv(self, known):
        self.tsv_fetches += 1
        return iter(tsv(
            ("Switzerland", "CH", "2026-10-18", "Films", 1, "Animals", "N/A", 2),
            ("Switzerland", "CH", "2026-10-18", "Films", 2, "Ghost", "N/A", 1),
            ("Switzerland", "CH", "2026-10-18", "Films", 3, "Nothing Found", "N/A", 1),
            ("Switzerland", "CH", "2026-10-18", "TV", 1, "The Diplomat", "The Diplomat: Season 4", 1),
        )), "lm"

    def seerr(self, path):
        if path.startswith("/api/v1/watchproviders"):
            return [{"id": 8, "name": "Netflix", "logoPath": "/nf.png"}]
        if path.startswith("/api/v1/search"):
            q = path.split("query=")[1].split("&")[0]
            return {"results": {"Animals": [hit("movie", 101, "Animals", 9)], "The%20Diplomat": [hit("tv", 202, "The Diplomat", 80)],
                                "Nothing%20Found": [hit("movie", 103, "Nothing Found", 1)], "Ghost": []}.get(q, [])}
        if path.startswith("/api/v1/discover/movies"):
            return {"results": [{"id": 104, "popularity": 90}, {"id": 101, "popularity": 50}] if "page=1" in path else []}
        if path.startswith("/api/v1/discover/tv"):
            return {"results": [{"id": 202, "popularity": 70}] if "page=1" in path else []}
        kind, tid = path.split("?")[0].split("/")[3:5]
        de = "language=de" in path
        base = {"originalLanguage": "en", "posterPath": f"/{tid}{'de' if de else ''}.jpg", "externalIds": {"imdbId": f"tt{tid}", "tvdbId": int(tid) * 10}}
        if kind == "movie":
            names = {"101": ("Animals", "Tiere"), "103": ("Nothing Found", "Nichts"), "104": ("Owned Film", "Eigener Film")}[tid]
            return dict(base, title=names[1] if de else names[0], releaseDate="2026-10-01", imdbId=f"tt{tid}")
        return dict(base, name="Diplomatische Beziehungen" if de else "The Diplomat", firstAirDate="2023-04-20", seasons=seasons("1", "2", "3", "4"))

    def indexers(self):
        return {"NzbPlanet": 2, "NZBgeek": 1, "Knaben": 7}

    def search(self, params):
        self.searches.append(params)
        q = params["query"]
        key = "movie/" + q.split("tt")[1].rstrip("}") if "ImdbId" in q else ("tv/%d/s%s" % (int(q.split(":")[1].split("}")[0]) // 10, q.split("Season:")[1].rstrip("}")) if "TvdbId" in q else None)
        return self.releases.get(key, []) if params["indexerIds"] == 2 else []

    def owned(self):
        return {"movie/104": "jf104"}

    def injector_get(self):
        return self.injector

    def injector_put(self, config):
        self.scripts.append(config)
        self.injector = config


class Main(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 21, 4, 39)

    def run_main(self, fake, sf, argv=(), now=None):
        out = io.StringIO()
        code = pop.main(list(argv), io_=fake, now=now or self.NOW, state_file=sf, overrides_file=pathlib.Path("/nonexistent"), out=out, sleep=lambda s: None,
                        services=[(8, "Netflix")], genres=[("drama", "Drama", "Drama", "18", "18")], allow_file=sf.with_name("allow.json"), prom_file=sf.with_name("p.prom"))
        return code, out.getvalue()

    def data(self, fake):
        src = next(s["Script"] for s in fake.injector["CustomJavaScripts"] if s["Name"] == pop.NAME)
        return json.loads(src[len("window.HssPopular = "):].rstrip().rstrip(";").replace("<\\/", "</"))

    def test_rows_show_only_owned_or_found(self):
        fake = FakeIo()
        with tempfile.TemporaryDirectory() as d:
            sf = pathlib.Path(d) / "s.json"
            self.assertEqual(self.run_main(fake, sf)[0], 0)
            data = self.data(fake)
            entry = fake.injector["CustomJavaScripts"][0]
            self.assertTrue(entry["RequiresAuthentication"])
            # every Netflix rank is listed: no release yet (n, checked date c) and not identified (u) included
            self.assertEqual([(i["r"], i["id"], i["f"], i.get("n"), i.get("c"), i.get("u")) for i in data["home"]["movies"]],
                             [(1, 101, "1080p", False, "2026-10-21", None), (2, None, None, True, None, True), (3, 103, None, True, "2026-10-21", None)])
            self.assertEqual(data["home"]["movies"][1]["t"], {"en": "Ghost", "de": "Ghost"})
            self.assertNotIn("x", data["home"]["movies"][2])
            self.assertEqual([(i["r"], i["id"], i["s"], i["f"], i["t"]["de"]) for i in data["home"]["shows"]], [(1, 202, 4, "2160p", "Diplomatische Beziehungen")])
            row = data["rows"][0]
            self.assertEqual((row["id"], row["name"]["en"], row["logo"]), ("service-8", "Netflix", "/nf.png"))
            self.assertEqual([(i["r"], i["k"], i["id"], i["o"], i["f"]) for i in row["items"]], [(1, "movie", 104, True, None), (2, "tv", 202, False, "2160p"), (3, "movie", 101, False, "1080p")])
            self.assertEqual(data["rows"][1]["name"], {"en": "Drama", "de": "Drama"})
            allow = json.loads(sf.with_name("allow.json").read_text())
            ids = {i["x"] for i in row["items"] if i.get("x")}
            self.assertEqual(ids, set(allow["entries"]))
            self.assertNotIn("x", row["items"][0])
            self.assertEqual(sorted((e["kind"], e["tmdb"], e["season"], e["res"]) for e in allow["entries"].values()), [("movie", 101, None, "1080p"), ("tv", 202, 4, "2160p")])
            self.assertEqual(data["home"]["movies"][0]["x"], row["items"][2]["x"])
            self.assertGreaterEqual(len(next(iter(ids))), 16)
            n = len(fake.searches)
            self.assertNotIn("movie/104", json.dumps(fake.searches))
            # second run: everything cached, nothing searched, script unchanged
            self.run_main(fake, sf)
            self.assertEqual(len(fake.searches), n)
            self.assertEqual(len(fake.scripts), 1)
            self.assertEqual(set(json.loads(sf.with_name("allow.json").read_text())["entries"]), ids)
            # the next night the titles without a release are searched again, found ones are not
            self.run_main(fake, sf, now=self.NOW + datetime.timedelta(days=1))
            again = fake.searches[n:]
            self.assertTrue([p for p in again if "tt103" in p["query"]])
            self.assertFalse([p for p in again if "tt101" in p["query"] or "TvdbId:2020" in p["query"]])
            # a release shows up: the title flips to requestable
            fake.releases["movie/103"] = [rel("Nothing.Found.2026.1080p.WEB.x264", 4)]
            self.run_main(fake, sf, now=self.NOW + datetime.timedelta(days=2))
            nf = self.data(fake)["home"]["movies"][2]
            self.assertEqual((nf["f"], nf["n"]), ("1080p", False))
            self.assertIn(nf["x"], json.loads(sf.with_name("allow.json").read_text())["entries"])
            # a week later the found ones are checked again
            n = len(fake.searches)
            self.run_main(fake, sf, now=self.NOW + datetime.timedelta(days=9))
            self.assertTrue([p for p in fake.searches[n:] if "tt101" in p["query"]])

    def test_rows_list_the_first_ten_whatever_their_state(self):
        fake = FakeIo()
        fake.releases = {}
        with tempfile.TemporaryDirectory() as d:
            self.run_main(fake, pathlib.Path(d) / "s.json")
            row = self.data(fake)["rows"][0]
        self.assertEqual([(i["r"], i["id"], i["o"], i["n"]) for i in row["items"]], [(1, 104, True, False), (2, 202, False, True), (3, 101, False, True)])

    def test_search_budget(self):
        fake = FakeIo()
        with tempfile.TemporaryDirectory() as d:
            sf = pathlib.Path(d) / "s.json"
            pop.SEARCH_MAX, old = 1, pop.SEARCH_MAX
            try:
                self.run_main(fake, sf)
            finally:
                pop.SEARCH_MAX = old
        self.assertEqual(len(fake.searches), 1)

    def test_tsv_from_a_file(self):
        fake = FakeIo()
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "c.tsv"
            f.write_text("\n".join(tsv(("Switzerland", "CH", "2026-10-11", "Films", 1, "Animals", "N/A", 1))))
            sf = pathlib.Path(d) / "s.json"
            self.run_main(fake, sf, ["--tsv", str(f)])
            self.assertEqual(fake.tsv_fetches, 0)
            self.assertEqual(json.loads(sf.read_text())["top"]["week"], "2026-10-11")
        self.assertEqual(self.run_main(FakeIo(), pathlib.Path("/nonexistent/x"), ["--tsv"])[0], 2)

    def test_dry_run_writes_nothing_and_searches_nothing(self):
        fake = FakeIo()
        with tempfile.TemporaryDirectory() as d:
            sf = pathlib.Path(d) / "s.json"
            code, text = self.run_main(fake, sf, ["--dry-run"])
            self.assertFalse(sf.exists())
            self.assertFalse(sf.with_name("allow.json").exists())
        self.assertEqual((code, fake.searches, fake.scripts), (0, [], []))
        self.assertIn("Ghost", text)

    def prom(self, sf):
        out = {}
        for line in sf.with_name("p.prom").read_text().splitlines():
            if line and not line.startswith("#"):
                k, v = line.rsplit(" ", 1)
                out[k] = float(v)
        return out

    def test_prom_file_after_a_run(self):
        fake = FakeIo()
        with tempfile.TemporaryDirectory() as d:
            sf = pathlib.Path(d) / "s.json"
            self.run_main(fake, sf)
            m = self.prom(sf)
            self.assertFalse(sf.with_name("p.prom.tmp").exists())
        self.assertEqual(m["jf_popular_last_run_timestamp_seconds"], self.NOW.timestamp())
        self.assertEqual(m["jf_popular_last_run_success"], 1)
        self.assertEqual(m['jf_popular_items{list="service-8"}'], 3)
        self.assertEqual(m['jf_popular_items_available{list="service-8"}'], 3)
        self.assertEqual(m['jf_popular_items_available{list="home-movies"}'], 1)
        self.assertIn('jf_popular_items{list="genre-drama"}', m)
        self.assertIn('jf_popular_items{list="home-shows"}', m)
        self.assertEqual(m["jf_popular_searches_total"], len(fake.searches))

    def test_prom_file_on_failure(self):
        fake = FakeIo()
        fake.owned = lambda: 1 / 0
        with tempfile.TemporaryDirectory() as d:
            sf = pathlib.Path(d) / "s.json"
            with self.assertRaises(ZeroDivisionError):
                self.run_main(fake, sf)
            m = self.prom(sf)
        self.assertEqual(m["jf_popular_last_run_success"], 0)
        self.assertIn("jf_popular_last_run_timestamp_seconds", m)

    def test_dry_run_writes_no_prom_file(self):
        with tempfile.TemporaryDirectory() as d:
            sf = pathlib.Path(d) / "s.json"
            self.run_main(FakeIo(), sf, ["--dry-run"])
            self.assertFalse(sf.with_name("p.prom").exists())


if __name__ == "__main__":
    unittest.main()
