import datetime
import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("upcoming_popularity", pathlib.Path(__file__).with_name("upcoming-popularity.py"))
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)

EPISODES = [{"seriesId": 22, "series": {"tmdbId": 213375}}, {"seriesId": 22, "series": {"tmdbId": 213375}}, {"seriesId": 11, "series": {"tmdbId": 84773}}, {"seriesId": 5, "series": {}}]
MOVIES = [{"id": 7, "tmdbId": 550}, {"id": 8}]


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
        self.assertEqual(up.titles(EPISODES, MOVIES), {"s:22": ("tv", 213375), "s:11": ("tv", 84773), "m:7": ("movie", 550)})

    def test_script_is_plain_sorted_data(self):
        self.assertEqual(up.pop_script({"s:2": 1.5, "m:1": 20.0}), 'window.HssUpcomingPop = {"m:1":20.0,"s:2":1.5};\n')


class Main(unittest.TestCase):
    def setUp(self):
        self.asked = []

    def arr(self, name, path, query):
        self.asked.append((name, path, query))
        return EPISODES if name == "Sonarr" else MOVIES

    def seerr(self, kind, tmdb):
        if tmdb == 84773:
            raise OSError("down")
        return {"popularity": {213375: 266.3651, 550: 61.04}[tmdb]}

    def run_main(self, jf, argv):
        out = io.StringIO()
        code = up.main(argv, api=jf.api, arr=self.arr, seerr=self.seerr, today=datetime.date(2026, 10, 29), out=out)
        return code, out.getvalue()

    def test_writes_the_entry_then_nothing_on_rerun(self):
        jf = FakeJellyfin()
        code, text = self.run_main(jf, [])
        self.assertEqual(code, 0)
        entry = jf.config["CustomJavaScripts"][-1]
        self.assertEqual(entry, {"Name": "Home upcoming", "Script": 'window.HssUpcomingPop = {"m:7":61.0,"s:22":266.4};\n', "Enabled": True, "RequiresAuthentication": True})
        self.assertEqual(jf.config["CustomJavaScripts"][0]["Name"], "Other")
        self.assertIn("skipped s:11: OSError", text)
        self.assertEqual(text.splitlines()[-1], "2 of 3 upcoming titles ranked, changed")
        self.assertEqual(self.asked[0], ("Sonarr", "calendar", {"start": "2026-10-29", "end": "2027-02-06", "includeSeries": "true"}))
        code, text = self.run_main(jf, [])
        self.assertEqual(len(jf.posts), 1)
        self.assertTrue(text.endswith("unchanged\n"))

    def test_dry_run_changes_nothing(self):
        jf = FakeJellyfin()
        code, text = self.run_main(jf, ["--dry-run"])
        self.assertEqual(jf.posts, [])
        self.assertTrue(text.endswith("would change\n"))

    def test_unknown_arguments(self):
        self.assertEqual(up.main(["--x"], api=FakeJellyfin().api), 2)


if __name__ == "__main__":
    unittest.main()
