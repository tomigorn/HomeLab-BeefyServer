import datetime
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("upcoming_popularity", pathlib.Path(__file__).with_name("upcoming-popularity.py"))
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)

EPISODES = [{"seriesId": 22, "series": {"tmdbId": 213375, "tvdbId": 403245, "imdbId": "tt9140554"}}, {"seriesId": 22, "series": {"tmdbId": 213375, "tvdbId": 403245, "imdbId": "tt9140554"}},
            {"seriesId": 11, "series": {"tmdbId": 84773, "tvdbId": 0, "imdbId": "bad id"}}, {"seriesId": 5, "series": {}}]
MOVIES = [{"id": 7, "tmdbId": 550, "imdbId": "tt0137523"}, {"id": 8}]


class FakeJellyfin:
    def __init__(self):
        self.config = {"CustomJavaScripts": [{"Name": "Other", "Script": "x()"}]}
        self.posts = []

    def api(self, method, path, body=None):
        if path == "/Plugins":
            return [{"Name": "JavaScript Injector", "Id": "js"}]
        if method == "POST":
            self.posts.append(body)
            self.config = body
            return None
        return self.config


class Titles(unittest.TestCase):
    def test_one_entry_per_series_and_movie_with_a_tmdb_id(self):
        self.assertEqual({k: v[:2] for k, v in up.titles(EPISODES, MOVIES).items()}, {"s:22": ("tv", 213375), "s:11": ("tv", 84773), "m:7": ("movie", 550)})

    def test_provider_ids_for_the_library_match_only_well_formed(self):
        t = up.titles(EPISODES, MOVIES)
        self.assertEqual(up.ids_of(t["s:22"]), {"tmdb": "213375", "tvdb": "403245", "imdb": "tt9140554"})
        self.assertEqual(up.ids_of(t["s:11"]), {"tmdb": "84773"})
        self.assertEqual(up.ids_of(t["m:7"]), {"tmdb": "550", "imdb": "tt0137523"})

    def test_script_is_plain_sorted_data(self):
        self.assertEqual(up.pop_script({"s:2": 1.5, "m:1": 20.0}), 'window.HssUpcomingPop = {"m:1":20.0,"s:2":1.5};\n')
        src = up.pop_script({"s:2": 1.5}, {"s:2": {"tmdb": "9", "d": "</script>"}})
        self.assertEqual(src, 'window.HssUpcomingPop = {"s:2":1.5};\nwindow.HssUpcomingInfo = {"s:2":{"d":"<\\/script>","tmdb":"9"}};\n')


class Main(unittest.TestCase):
    def arr(self, name, path, query):
        self.asked.append((name, path, query))
        return EPISODES if name == "Sonarr" else MOVIES

    def seerr(self, path):
        kind, tmdb = path.split("?")[0].split("/")[3:5]
        if tmdb == "84773":
            raise OSError("down")
        if path.endswith("ratings") or path.endswith("ratingscombined"):
            return {}
        title = {"213375": "Rings", "550": "Fight Club"}[tmdb]
        return {"popularity": {"213375": 266.3651, "550": 61.04}[tmdb], "title" if kind == "movie" else "name": title, "overview": "o"}

    def run_main(self, jf, argv):
        out = io.StringIO()
        code = up.main(argv, api=jf.api, arr=self.arr, seerr=self.seerr, today=datetime.date(2026, 10, 29), out=out, sidecar=self.side)
        return code, out.getvalue()

    def setUp(self):
        self.asked = []
        self.tmp = tempfile.TemporaryDirectory()
        self.side = pathlib.Path(self.tmp.name) / "coming.json"

    def tearDown(self):
        self.tmp.cleanup()

    def info(self, jf):
        src = jf.config["CustomJavaScripts"][-1]["Script"]
        return json.loads(src.split("window.HssUpcomingInfo = ")[1].rstrip().rstrip(";").replace("<\\/", "</"))

    def test_writes_the_entry_then_nothing_on_rerun(self):
        jf = FakeJellyfin()
        code, text = self.run_main(jf, [])
        self.assertEqual(code, 0)
        entry = jf.config["CustomJavaScripts"][-1]
        self.assertEqual({k: v for k, v in entry.items() if k != "Script"}, {"Name": "Home upcoming", "Enabled": True, "RequiresAuthentication": True})
        self.assertTrue(entry["Script"].startswith('window.HssUpcomingPop = {"m:7":61.0,"s:22":266.4};\n'))
        info = self.info(jf)
        side = json.loads(self.side.read_text())
        self.assertEqual(set(info), {"m:7", "s:22", "s:11"})
        self.assertEqual({k: v for k, v in info["s:11"].items()}, {"tmdb": "84773"}, "no details: ids only, for the library match")
        self.assertEqual(sorted(side["entries"]), sorted([info["m:7"]["d"], info["s:22"]["d"]]))
        self.assertEqual(side["entries"][info["s:22"]["d"]]["t"]["en"], "Rings")
        self.assertEqual(side["entries"][info["m:7"]["d"]]["ref"], "movie/550")
        self.assertEqual(jf.config["CustomJavaScripts"][0]["Name"], "Other")
        self.assertIn("skipped s:11: OSError", text)
        self.assertEqual(text.splitlines()[-1], "2 of 3 upcoming titles ranked, 2 with details, changed")
        self.assertEqual(self.asked[0], ("Sonarr", "calendar", {"start": "2026-10-29", "end": "2027-02-06", "includeSeries": "true"}))
        first = self.info(jf)
        code, text = self.run_main(jf, [])
        self.assertEqual(len(jf.posts), 1, "same ids on the next run: the entry is unchanged")
        self.assertEqual(self.info(jf), first)
        self.assertTrue(text.endswith("unchanged\n"))

    def test_dry_run_changes_nothing(self):
        jf = FakeJellyfin()
        code, text = self.run_main(jf, ["--dry-run"])
        self.assertEqual(jf.posts, [])
        self.assertFalse(self.side.exists())
        self.assertTrue(text.endswith("would change\n"))

    def test_unknown_arguments(self):
        self.assertEqual(up.main(["--x"], api=FakeJellyfin().api), 2)


if __name__ == "__main__":
    unittest.main()
