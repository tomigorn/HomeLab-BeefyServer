import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tmdb_details as td  # noqa: E402


def video(key, type_="Trailer", size=1080, site="YouTube", name=None):
    return {"key": key, "type": type_, "size": size, "site": site, "name": name or key, "url": "https://www.youtube.com/watch?v=" + key}


MOVIE_EN = {
    "id": 550, "title": "Fight Club", "overview": "An insomniac office worker...", "releaseDate": "1999-10-15", "runtime": 139,
    "posterPath": "/en.jpg", "backdropPath": "/back.jpg", "imdbId": "tt0137523", "voteAverage": 8.438, "voteCount": 30000,
    "genres": [{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}],
    "releases": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "R"}]}, {"iso_3166_1": "CH", "release_dates": [{"certification": ""}, {"certification": "18"}]}]},
    "credits": {"cast": [{"name": f"Actor {i}", "character": f"Role {i}", "profilePath": f"/p{i}.jpg", "order": i} for i in range(12, 0, -1)]},
    "relatedVideos": [video("aaaaaaaaaaa", "Teaser", 2160), video("bbbbbbbbbbb", "Trailer", 720), video("ccccccccccc", "Trailer", 1080),
                      video("vimeo123456", site="Vimeo"), video("bad key!!!!"), video("ddddddddddd", "Featurette")],
}
MOVIE_DE = dict(MOVIE_EN, title="Fight Club (DE)", overview="Ein Yuppie...", posterPath="/de.jpg", genres=[{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}],
                relatedVideos=[video("eeeeeeeeeee", "Trailer", 1080), video("ccccccccccc", "Trailer", 1080)])
TV_EN = {
    "id": 1399, "name": "Game of Thrones", "overview": "Seven noble families...", "firstAirDate": "2011-04-17", "episodeRunTime": [],
    "lastEpisodeToAir": {"runtime": 80}, "numberOfSeasons": 8, "posterPath": "/got.jpg", "backdropPath": "/gotb.jpg",
    "externalIds": {"imdbId": "tt0944947"}, "voteAverage": 8.475, "voteCount": 25000, "genres": [{"id": 10765, "name": "Sci-Fi & Fantasy"}],
    "contentRatings": {"results": [{"iso_3166_1": "US", "rating": "TV-MA"}, {"iso_3166_1": "CH", "rating": "16"}]},
    "credits": {"cast": [{"name": "Peter Dinklage", "character": "Tyrion", "profilePath": None, "order": 0}]}, "relatedVideos": [],
}
TV_DE = dict(TV_EN, name="Game of Thrones", overview="", genres=[{"id": 10765, "name": "Sci-Fi & Fantasy"}])


class Projection(unittest.TestCase):
    def test_movie(self):
        ratings = {"rt": {"criticsScore": 81, "audienceScore": 96, "url": "x"}, "imdb": {"criticsScore": 8.8}}
        p = td.project("movie", 550, MOVIE_EN, MOVIE_DE, ratings)
        self.assertEqual((p["k"], p["id"], p["y"], p["rt"], p["ns"], p["cert"], p["imdb"]), ("movie", 550, 1999, 139, None, "18", "tt0137523"))
        self.assertEqual(p["t"], {"en": "Fight Club", "de": "Fight Club (DE)"})
        self.assertEqual(p["o"], {"en": "An insomniac office worker...", "de": "Ein Yuppie..."})
        self.assertEqual(p["g"], {"en": ["Drama", "Thriller"], "de": ["Drama", "Thriller"]})
        self.assertEqual((p["p"], p["b"]), ({"en": "/en.jpg", "de": "/de.jpg"}, "/back.jpg"))
        self.assertEqual(p["sc"], {"imdb": 8.8, "rtc": 81, "rta": 96, "tmdb": 8.4})
        self.assertEqual([c["n"] for c in p["cast"]], ["Actor 1", "Actor 2", "Actor 3", "Actor 4", "Actor 5", "Actor 6", "Actor 7", "Actor 8", "Actor 9", "Actor 10"])
        self.assertEqual(p["cast"][0], {"n": "Actor 1", "c": "Role 1", "p": "/p1.jpg"})
        json.dumps(p)

    def test_tv_runtime_seasons_and_certification(self):
        p = td.project("tv", 1399, TV_EN, TV_DE, {"criticsScore": 89, "audienceScore": 85})
        self.assertEqual((p["rt"], p["ns"], p["cert"], p["imdb"], p["y"]), (80, 8, "16", "tt0944947", 2011))
        self.assertEqual(p["sc"], {"imdb": None, "rtc": 89, "rta": 85, "tmdb": 8.5})
        self.assertEqual(p["o"]["de"], "")
        self.assertEqual(p["cast"], [{"n": "Peter Dinklage", "c": "Tyrion", "p": None}])

    def test_unsafe_values_are_dropped(self):
        en = dict(MOVIE_EN, imdbId="javascript:alert(1)", posterPath="../x.jpg", backdropPath="/ok_1.jpg", voteCount=0)
        p = td.project("movie", 550, en, dict(MOVIE_DE, posterPath=None), None)
        self.assertEqual((p["imdb"], p["p"], p["b"]), (None, {"en": None, "de": None}, "/ok_1.jpg"))
        self.assertEqual(p["sc"], {"imdb": None, "rtc": None, "rta": None, "tmdb": None})
        bad = dict(MOVIE_EN, credits={"cast": [{"name": "Bad Path", "character": "X", "profilePath": "https://evil.example.com/x.jpg", "order": 0}]})
        self.assertEqual(td.project("movie", 550, bad, MOVIE_DE, None)["cast"], [{"n": "Bad Path", "c": "X", "p": None}])

    def test_no_certification_for_switzerland(self):
        en = dict(MOVIE_EN, releases={"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "R"}]}]})
        self.assertIsNone(td.project("movie", 550, en, MOVIE_DE, None)["cert"])


class Trailers(unittest.TestCase):
    def test_youtube_trailers_and_teasers_per_language_trailers_first_larger_first(self):
        v = td.videos(MOVIE_EN["relatedVideos"], MOVIE_DE["relatedVideos"])
        self.assertEqual([(x["key"], x["lang"]) for x in v], [("ccccccccccc", "en"), ("bbbbbbbbbbb", "en"), ("aaaaaaaaaaa", "en"), ("eeeeeeeeeee", "de")])
        self.assertEqual(v[0], {"key": "ccccccccccc", "name": "ccccccccccc", "type": "Trailer", "size": 1080, "lang": "en"})

    def test_at_most_five_per_language(self):
        many = [video("k%010d" % i, size=i) for i in range(8)]
        v = td.videos(many, [])
        self.assertEqual(len(v), 5)
        self.assertEqual(v[0]["key"], "k0000000007")


class Ratings(unittest.TestCase):
    def test_movie_uses_combined_tv_uses_ratings(self):
        asked = []

        def seerr(path):
            asked.append(path)
            return {"x": 1}
        self.assertEqual(td.ratings(seerr, "movie", 550), {"x": 1})
        self.assertEqual(td.ratings(seerr, "tv", 1399), {"x": 1})
        self.assertEqual(asked, ["/api/v1/movie/550/ratingscombined", "/api/v1/tv/1399/ratings"])

    def test_missing_ratings_are_none_not_a_failure(self):
        def seerr(path):
            raise urllib.error.HTTPError(path, 404, "nf", None, None)
        self.assertIsNone(td.ratings(seerr, "movie", 550))


class Fetch(unittest.TestCase):
    def test_fetch_asks_both_languages_and_the_ratings(self):
        asked = []

        def seerr(path):
            asked.append(path)
            if path.endswith("ratingscombined"):
                return {"imdb": {"criticsScore": 7.0}}
            return MOVIE_DE if "language=de" in path else MOVIE_EN
        en, p = td.fetch(seerr, "movie", 550)
        self.assertEqual(en["title"], "Fight Club")
        self.assertEqual(p["sc"]["imdb"], 7.0)
        self.assertEqual(asked, ["/api/v1/movie/550?language=en", "/api/v1/movie/550?language=de", "/api/v1/movie/550/ratingscombined"])


class Sidecar(unittest.TestCase):
    def test_stable_tokens_and_atomic_world_readable_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "details.json"
            first = td.write_sidecar(f, "2026-10-29T04:39:00", {"movie/550": {"k": "movie"}, "tv/1399": {"k": "tv"}})
            self.assertEqual(set(first), {"movie/550", "tv/1399"})
            self.assertTrue(all(td.TOKEN_RE.match(t) for t in first.values()))
            again = td.write_sidecar(f, "2026-10-30T04:39:00", {"movie/550": {"k": "movie", "new": 1}})
            self.assertEqual(again, {"movie/550": first["movie/550"]})
            data = json.loads(f.read_text())
            self.assertEqual(data["updated"], "2026-10-30T04:39:00")
            self.assertEqual(data["entries"], {first["movie/550"]: {"k": "movie", "new": 1, "ref": "movie/550"}})
            self.assertEqual(f.stat().st_mode & 0o777, 0o644)
            self.assertFalse(f.with_name("details.json.tmp").exists())
            self.assertEqual(td.assign(f, ["movie/550", "tv/1"])["movie/550"], first["movie/550"])
            self.assertNotEqual(td.assign(f, ["tv/1"])["tv/1"], first["tv/1399"])


if __name__ == "__main__":
    unittest.main()
