import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("region_rows", pathlib.Path(__file__).with_name("region-rows.py"))
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def item(iid, kind, name, genres, locations=None, added="2026-01-01T00:00:00Z", tmdb="1"):
    """A Jellyfin item as /Items returns it (series carry no ProductionLocations)."""
    out = {"Id": iid, "Type": kind, "Name": name, "Genres": genres, "DateCreated": added, "ProviderIds": {"Tmdb": tmdb}, "Path": f"/media/{iid}"}
    if locations is not None:
        out["ProductionLocations"] = locations
    return out


# Real metadata (Jellyfin item + Seerr/TMDB details, read 2026-11-22).
RING_THING = (item("ring", "Movie", "The Ring Thing", ["Comedy"], ["Switzerland"], tmdb="10248"),
              {"originalLanguage": "de", "originCountry": None, "productionCountries": [{"iso_3166_1": "CH", "name": "Switzerland"}]})
MORE_THAN_HONEY = (item("honey", "Movie", "More Than Honey", ["Documentary"], ["Switzerland", "Germany", "Austria"], tmdb="124075"),
                   {"originalLanguage": "de", "originCountry": None, "productionCountries": [{"iso_3166_1": c} for c in ("CH", "DE", "AT")]})
THE_KILLING = (item("killing", "Series", "The Killing", ["Crime", "Mystery", "Drama"], tmdb="32368"),
               {"originalLanguage": "da", "originCountry": ["DK"], "productionCountries": [{"iso_3166_1": "DK", "name": "Denmark"}]})
WILDER = (item("wilder", "Series", "Wilder", ["Crime"], tmdb="75816"),
          {"originalLanguage": "de", "originCountry": ["CH"], "productionCountries": [{"iso_3166_1": "CH"}]})
GOOD_NIGHT = (item("goodnight", "Movie", "Good Night, and Good Luck.", ["Drama", "History"], ["France", "Japan", "United Kingdom", "United States of America"], tmdb="3291"),
              {"originalLanguage": "en", "originCountry": None, "productionCountries": [{"iso_3166_1": c} for c in ("FR", "JP", "GB", "US")]})
ANOTHER_ROUND = (item("round", "Movie", "Another Round", ["Drama"], ["Denmark", "Netherlands", "Sweden"], tmdb="580175"),
                 {"originalLanguage": "da", "originCountry": None, "productionCountries": [{"iso_3166_1": c} for c in ("DK", "NL", "SE")]})
WILD_TALES = (item("wild", "Movie", "Wild Tales", ["Drama", "Thriller", "Comedy"], ["Argentina", "Spain", "France", "United Kingdom"], tmdb="265195"),
              {"originalLanguage": "es", "originCountry": None, "productionCountries": [{"iso_3166_1": c} for c in ("AR", "ES", "FR", "GB")]})
RICH_FLU = (item("flu", "Movie", "Rich Flu", ["Thriller", "Science Fiction"], ["Spain", "Chile"], tmdb="934584"),
            {"originalLanguage": "es", "originCountry": None, "productionCountries": [{"iso_3166_1": c} for c in ("ES", "CL")]})
ONODA = (item("onoda", "Movie", "Onoda: 10,000 Nights in the Jungle", ["Drama", "Adventure", "War", "History"], ["France", "Japan", "Belgium", "Italy", "Germany", "Cambodia"], tmdb="695089"),
         {"originalLanguage": "fr", "originCountry": None, "productionCountries": [{"iso_3166_1": c} for c in ("FR", "JP", "BE", "IT", "DE", "KH")]})
# TMDB movie 1527461 "AIR - Le Voyage Dans La Lune": original language only, no production country, no genre.
ONLY_LANGUAGE = (item("air", "Movie", "AIR - Le Voyage Dans La Lune", [], [], tmdb="1527461"),
                 {"originalLanguage": "fr", "originCountry": None, "productionCountries": []})
TOKYO_GODFATHERS = (item("tokyo", "Movie", "Tokyo Godfathers", ["Animation", "Drama", "Comedy"], ["Japan"], tmdb="13398"),
                    {"originalLanguage": "ja", "originCountry": None, "productionCountries": [{"iso_3166_1": "JP"}]})
FRIEREN = (item("frieren", "Series", "Frieren: Beyond Journey's End", ["Animation", "Action & Adventure", "Drama", "Sci-Fi & Fantasy"], tmdb="209867"),
           {"originalLanguage": "ja", "originCountry": ["JP"], "productionCountries": [{"iso_3166_1": "JP"}]})
SEVEN_SAMURAI = (item("samurai", "Movie", "Seven Samurai", ["Action", "Drama"], ["Japan"], tmdb="346"),
                 {"originalLanguage": "ja", "originCountry": None, "productionCountries": [{"iso_3166_1": "JP"}]})
THE_OFFICE = (item("office", "Series", "The Office", ["Comedy"], tmdb="2996"),
              {"originalLanguage": "en", "originCountry": ["GB"], "productionCountries": [{"iso_3166_1": "GB"}]})


def regions(sample):
    it, raw = sample
    return rr.regions(rr.facts(it, rr.slim(raw)))


class ClassifyTest(unittest.TestCase):
    def test_swiss_film(self):
        self.assertEqual(regions(RING_THING), {"swiss"})

    def test_swiss_co_production_with_germany_and_austria(self):
        self.assertEqual(regions(MORE_THAN_HONEY), {"swiss"})

    def test_danish_series_is_nordic_and_nordic_noir(self):
        self.assertEqual(regions(THE_KILLING), {"nordic", "nordic-noir", "world-thrillers"})

    def test_swiss_series_by_origin_country(self):
        self.assertEqual(regions(WILDER), {"swiss", "world-thrillers"})

    def test_english_film_with_a_french_co_producer_is_no_region(self):
        self.assertEqual(regions(GOOD_NIGHT), set())

    def test_danish_co_production_is_nordic(self):
        self.assertEqual(regions(ANOTHER_ROUND), {"nordic"})

    def test_argentine_spanish_co_production_is_spanish_and_latin_american(self):
        self.assertEqual(regions(WILD_TALES), {"spanish", "latin", "world-thrillers"})

    def test_spanish_film_with_a_chilean_co_producer_is_not_latin_american(self):
        self.assertEqual(regions(RICH_FLU), {"spanish", "world-thrillers"})

    def test_french_language_co_production_is_french(self):
        self.assertEqual(regions(ONODA), {"french"})

    def test_film_with_only_its_original_language(self):
        self.assertEqual(regions(ONLY_LANGUAGE), {"french"})

    def test_japanese_animation_is_anime_live_action_is_japanese(self):
        self.assertEqual(regions(TOKYO_GODFATHERS), {"anime"})
        self.assertEqual(regions(FRIEREN), {"anime"})
        self.assertEqual(regions(SEVEN_SAMURAI), {"japanese", "asian-action"})

    def test_british_series_by_origin(self):
        self.assertEqual(regions(THE_OFFICE), {"british"})

    def test_without_tmdb_details_jellyfin_locations_decide(self):
        it, _ = RING_THING
        self.assertEqual(rr.regions(rr.facts(it, None)), {"swiss"})
        it, _ = GOOD_NIGHT
        self.assertEqual(rr.regions(rr.facts(it, None)), set())

    def test_series_without_details_has_no_region(self):
        it, _ = THE_KILLING
        self.assertEqual(rr.regions(rr.facts(it, None)), set())


class PlanTest(unittest.TestCase):
    def test_rows_newest_added_first_swiss_made_first(self):
        tank = item("tank", "Movie", "The Tank", ["War"], ["Germany"], added="2026-11-01T00:00:00Z")
        ring = dict(RING_THING[0], DateCreated="2025-01-01T00:00:00Z")
        cache = {"movie:1": {"lang": "de", "countries": ["DE"], "origin": []}, "movie:10248": rr.slim(RING_THING[1])}
        tank["ProviderIds"] = {"Tmdb": "1"}
        rows = rr.plan([tank, ring], cache)
        self.assertEqual(rows["swiss"], ["ring", "tank"])

    def test_placeholders_are_left_out(self):
        ph = dict(RING_THING[0], Path="/config/trending/library/x")
        self.assertEqual(rr.plan([ph], {"movie:10248": rr.slim(RING_THING[1])}), {})

    def test_at_most_size_titles(self):
        items = [dict(RING_THING[0], Id=f"i{n:02d}", DateCreated=f"2026-10-{n:02d}T00:00:00Z") for n in range(1, 31)]
        self.assertEqual(len(rr.plan(items, {"movie:10248": rr.slim(RING_THING[1])})["swiss"]), rr.SIZE)


def ids(n, prefix):
    return [f"{prefix}{i}" for i in range(n)]


class ChooseTest(unittest.TestCase):
    def test_too_small_rows_are_not_shown(self):
        self.assertEqual(rr.choose({"nordic": ids(5, "n"), "swiss": ids(3, "s"), "korean": ids(3, "k")}), ["nordic"])

    def test_owner_rows_first_then_the_biggest(self):
        rows = {"french": ids(4, "f"), "nordic": ids(5, "n"), "anime": ids(8, "a"), "japanese": ids(6, "j"), "latin": ids(4, "l")}
        self.assertEqual(rr.choose(rows), ["nordic", "french", "anime", "japanese", "latin"])

    def test_at_most_max_rows(self):
        rows = {k: ids(5, k) for k in ("nordic", "spanish", "french", "swiss", "korean", "japanese", "anime", "italian")}
        self.assertEqual(rr.choose(rows), ["nordic", "spanish", "french", "swiss", "korean", "japanese"])

    def test_region_rows_before_genre_cuts(self):
        rows = {k: ids(5, k) for k in ("nordic", "spanish", "french", "swiss", "japanese")}
        rows.update({"world-thrillers": ids(17, "w"), "anime": ids(4, "a"), "british": ids(6, "b")})
        self.assertEqual(rr.choose(rows), ["nordic", "spanish", "french", "swiss", "british", "japanese"])

    def test_a_row_mostly_inside_a_shown_row_is_left_out(self):
        rows = {"nordic": ids(8, "n"), "nordic-noir": ids(4, "n"), "anime": ids(4, "a")}
        self.assertEqual(rr.choose(rows), ["nordic", "anime"])


class FakeJellyfin:
    def __init__(self, boxes=None, members=None):
        self.boxes = boxes or []
        self.members = members or {}
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/Items?ParentId="):
            sid = path.split("=")[1].split("&")[0]
            return {"Items": [{"Id": i} for i in self.members.get(sid, [])]}
        if method == "GET" and path.startswith("/Items/"):
            sid = path.split("/")[2].split("?")[0]
            return next(dict(b) for b in self.boxes if b["Id"] == sid)
        if method == "POST" and path.startswith("/Collections?"):
            return {"Id": "new"}
        return None


class SyncTest(unittest.TestCase):
    def test_new_row_creates_a_tagged_locked_set(self):
        api = FakeJellyfin(boxes=[{"Id": "new", "Name": "Anime", "Tags": []}])
        sid = rr.sync_set(api, "anime", ["a", "b"], [], "admin", False)
        self.assertEqual(sid, "new")
        update = next(c for c in api.calls if c == ("POST", "/Items/new"))
        self.assertTrue(update)
        self.assertIn(("POST", "/Collections?name=Anime&ids=a,b&isLocked=true"), api.calls)

    def test_gone_rows_sets_are_deleted_others_never(self):
        boxes = [{"Id": "x", "Tags": ["hss-region", "hss-region-korean"]}, {"Id": "y", "Tags": ["hss-hidden-gems"]}, {"Id": "z", "Tags": ["hss-region", "hss-region-nordic"]}]
        api = FakeJellyfin(boxes=boxes)
        self.assertEqual(rr.drop_sets(api, boxes, {"nordic"}, False), ["x"])
        self.assertEqual([c for c in api.calls if c[0] == "DELETE"], [("DELETE", "/Items/x")])

    def test_dry_run_deletes_nothing(self):
        boxes = [{"Id": "x", "Tags": ["hss-region", "hss-region-korean"]}]
        api = FakeJellyfin(boxes=boxes)
        self.assertEqual(rr.drop_sets(api, boxes, set(), True), ["x"])
        self.assertEqual(api.calls, [])


class WebDataTest(unittest.TestCase):
    def test_entry_holds_rows_in_order_with_both_names(self):
        src = rr.web_source([("nordic", ["a", "b"]), ("anime", ["c"])], "/* script */")
        data = json.loads(src.split("window.HssRegionRows = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual(data, [{"id": "HssRegionNordic", "name": {"en": "Nordic Films & Series", "de": "Nordische Filme & Serien"}, "items": ["a", "b"]},
                                {"id": "HssRegionAnime", "name": {"en": "Anime", "de": "Anime"}, "items": ["c"]}])
        self.assertTrue(src.endswith("/* script */"))

    def test_row_ids_are_plain_words(self):
        for key in rr.ROWS:
            self.assertRegex(rr.row_id(key), r"^HssRegion[A-Za-z]+$")


class CacheTest(unittest.TestCase):
    def test_only_new_titles_are_looked_up_and_failures_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "c.json"
            path.write_text(json.dumps({"movie:1": {"lang": "fr", "countries": ["FR"], "origin": []}}))
            asked = []

            def seerr(ref):
                asked.append(ref)
                if ref == "movie:3":
                    raise OSError("down")
                return {"originalLanguage": "da", "originCountry": ["DK"], "productionCountries": [{"iso_3166_1": "DK"}]}
            items = [dict(item(f"i{n}", "Movie" if n != 2 else "Series", "x", []), ProviderIds={"Tmdb": str(n)}) for n in (1, 2, 3)]
            with contextlib.redirect_stdout(io.StringIO()):
                cache = rr.load_details(items, seerr, path)
            self.assertEqual(asked, ["tv:2", "movie:3"])
            self.assertEqual(sorted(cache), ["movie:1", "tv:2"])
            self.assertEqual(sorted(json.loads(path.read_text())), ["movie:1", "tv:2"])


if __name__ == "__main__":
    unittest.main()
