import unittest

import app


class FakeLookup:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def __call__(self, kind, tmdb_id):
        self.calls.append((kind, tmdb_id))
        return self.result


class ResolveTest(unittest.TestCase):
    def test_known_movie_redirects_to_item(self):
        lookup = FakeLookup("met_abc")
        self.assertEqual(app.resolve("movie", "603", "The Matrix", lookup), (302, "/media/item/met_abc"))
        self.assertEqual(lookup.calls, [("movie", "603")])

    def test_known_show_redirects_to_item(self):
        lookup = FakeLookup("met_dark")
        self.assertEqual(app.resolve("show", "70523", "Dark", lookup), (302, "/media/item/met_dark"))
        self.assertEqual(lookup.calls, [("show", "70523")])

    def test_unknown_falls_back_to_search(self):
        self.assertEqual(app.resolve("movie", "603", "The Matrix", FakeLookup()), (302, "/media/search/movie?query=The%20Matrix&source=TMDB"))

    def test_unknown_show_searches_shows(self):
        self.assertEqual(app.resolve("show", "70523", "Dark", FakeLookup()), (302, "/media/search/show?query=Dark&source=TMDB"))

    def test_fallback_encodes_title(self):
        status, location = app.resolve("movie", "1", "Tom & Jerry #1/2 Ü", FakeLookup())
        self.assertEqual(status, 302)
        self.assertEqual(location, "/media/search/movie?query=Tom%20%26%20Jerry%20%231%2F2%20%C3%9C&source=TMDB")

    def test_non_numeric_id_rejected(self):
        lookup = FakeLookup("met_abc")
        status, _ = app.resolve("movie", "1;drop", "x", lookup)
        self.assertEqual(status, 400)
        self.assertEqual(lookup.calls, [])

    def test_unknown_kind_rejected(self):
        lookup = FakeLookup("met_abc")
        status, _ = app.resolve("episode", "603", "x", lookup)
        self.assertEqual(status, 400)
        self.assertEqual(lookup.calls, [])

    def test_empty_title_falls_back_to_dashboard(self):
        self.assertEqual(app.resolve("movie", "603", "", FakeLookup()), (302, "/"))


class RouteTest(unittest.TestCase):
    def test_parse_go_path(self):
        self.assertEqual(app.parse("/go/tmdb/movie/603?title=The%20Matrix"), ("movie", "603", "The Matrix"))

    def test_parse_without_title(self):
        self.assertEqual(app.parse("/go/tmdb/show/70523"), ("show", "70523", ""))

    def test_parse_other_path(self):
        self.assertIsNone(app.parse("/go/imdb/movie/tt1"))
        self.assertIsNone(app.parse("/go/tmdb/movie/603/extra"))


if __name__ == "__main__":
    unittest.main()
