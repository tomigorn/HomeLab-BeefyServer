#!/usr/bin/env python3
"""download-now/report.py: the problem report site (report.holy-grail.ch) against fake Radarr/Sonarr/Bazarr: who may report, limits, and what each reason does.
Usage: python3 tests/test_report.py"""
import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "download-now"))
spec = importlib.util.spec_from_file_location("download_now", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
sys.modules["download_now"] = dn
spec.loader.exec_module(dn)
import manual  # noqa: E402
import report  # noqa: E402

ORIGIN = "https://report.example.com"
SECRET = "s3cret-for-tests"
T = datetime.datetime(2026, 11, 20, 14, 0).timestamp()
DAY = 86400


def hdr(user="jane", groups="streaming-users", secret=SECRET, **extra):
    h = {"X-Report-Secret": secret, "X-authentik-username": user, "X-authentik-groups": groups}
    h.update(extra)
    return {k: v for k, v in h.items() if v is not None}


ADMIN = dict(user="janedoe", groups="streaming-users|streaming-admins")


def rel(title, guid, res=1080, source="bluray", rejected=False, rejections=(), score=100, langs=("English",), movie=12, series=None, season=None, full=False, seeders=10):
    x = {"guid": guid, "indexerId": 3, "title": title, "rejected": rejected, "rejections": list(rejections), "customFormatScore": score, "seeders": seeders,
         "quality": {"quality": {"name": f"{source}-{res}p", "resolution": res, "source": source}}, "languages": [{"name": n} for n in langs], "size": 5 * 10 ** 9,
         "protocol": "torrent", "indexer": "Example"}
    if series:
        x.update(mappedSeriesId=series, mappedSeasonNumber=season, fullSeason=full)
    else:
        x["mappedMovieId"] = movie
    return x


class World:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = pathlib.Path(self.tmp.name, "manual.json")
        self.writes, self.gets = [], []
        self.movies = [
            {"id": 12, "title": "Some Film", "originalTitle": "Ein Film", "year": 2004, "tmdbId": 4242, "hasFile": True, "tags": [], "originalLanguage": {"name": "German"},
             "alternateTitles": [{"title": "Another Name"}], "runtime": 100,
             "movieFile": {"id": 77, "path": "/data/media/movies/Some Film (2004)/Some.Film.mkv", "quality": {"quality": {"name": "Bluray-1080p", "resolution": 1080, "source": "bluray"}},
                           "mediaInfo": {"audioLanguages": "German/English"}, "sceneName": "Some.Film.2004.1080p.BluRay-GRP"}},
            {"id": 13, "title": "Missing Film", "year": 2010, "tmdbId": 5050, "hasFile": False, "tags": []},
            {"id": 14, "title": "Nosferatu", "year": 1922, "tmdbId": 653, "hasFile": True, "tags": [], "originalLanguage": {"name": "German"},
             "movieFile": {"id": 78, "path": "/data/media/movies/Nosferatu (1922)/Nosferatu.mkv", "quality": {"quality": {"name": "Bluray-1080p", "resolution": 1080}}, "mediaInfo": {"audioLanguages": ""}}},
        ]
        self.series = [{"id": 5, "title": "Company Retreat", "year": 2026, "tvdbId": 9090, "tmdbId": 4040, "tags": [], "originalLanguage": {"name": "English"},
                        "statistics": {"episodeFileCount": 2}}]
        self.episodes = [
            {"id": 501, "seriesId": 5, "seasonNumber": 1, "episodeNumber": 7, "title": "Seven", "hasFile": True, "episodeFileId": 601},
            {"id": 502, "seriesId": 5, "seasonNumber": 1, "episodeNumber": 8, "title": "Eight", "hasFile": True, "episodeFileId": 602},
            {"id": 503, "seriesId": 5, "seasonNumber": 2, "episodeNumber": 1, "title": "Later", "hasFile": False, "episodeFileId": 0},
        ]
        self.episodefiles = {602: {"id": 602, "path": "/data/media/tv/Company Retreat/Season 01/S01E08.mkv", "quality": {"quality": {"name": "WEBDL-1080p", "resolution": 1080, "source": "web"}},
                                   "mediaInfo": {"audioLanguages": "English"}}}
        self.history = {"radarr": [
            {"id": 9001, "eventType": "grabbed", "downloadId": "DLOLD", "sourceTitle": "Some.Film.2004.1080p.BluRay-GRP", "date": "2026-11-01T10:00:00Z"},
            {"id": 9002, "eventType": "downloadFolderImported", "downloadId": "DLOLD", "data": {"fileId": "77"}, "date": "2026-11-01T11:00:00Z"}],
            "sonarr": [
            {"id": 9101, "eventType": "grabbed", "downloadId": "DLEP", "episodeId": 502, "sourceTitle": "Company.Retreat.S01E08.1080p.WEB-DL-cakes", "date": "2026-11-17T10:00:00Z"},
            {"id": 9102, "eventType": "downloadFolderImported", "downloadId": "DLEP", "episodeId": 502, "data": {"fileId": "602"}, "date": "2026-11-17T11:00:00Z"}]}
        self.releases = {}
        self.tags = {"radarr": [{"id": 4, "label": "download-now"}], "sonarr": [{"id": 6, "label": "download-now"}]}
        self.queue = {"radarr": [], "sonarr": []}
        self.bazarr_calls = []
        self.checked = []
        self.images, self.cover = [], {"/MediaCover/12/poster-250.jpg": b"\xff\xd8jpeg"}
        self.watched_by = None

    def watched(self, user):
        return self.watched_by(user) if self.watched_by else None

    def arr(self, app):
        def call(path, method="GET", body=None, timeout=None):
            if method != "GET":
                self.writes.append((app, method, path, body))
                return {}
            self.gets.append((app, path))
            if path == "/movie":
                return self.movies
            if path == "/series":
                return self.series
            if path.startswith("/movie/"):
                return next((m for m in self.movies if m["id"] == int(path.split("/")[2])), None)
            if path.startswith("/series/"):
                return next((s for s in self.series if s["id"] == int(path.split("/")[2])), None)
            if path.startswith("/episode?seriesId="):
                return self.episodes
            if path.startswith("/episode/"):
                return next(e for e in self.episodes if e["id"] == int(path.split("/")[2]))
            if path.startswith("/episodefile/"):
                return self.episodefiles[int(path.split("/")[2])]
            if path.startswith("/history/"):
                return self.history[app]
            if path == "/tag":
                return self.tags[app]
            if path.startswith("/release?"):
                q = urllib.parse.parse_qs(path.partition("?")[2])
                return self.releases.get(tuple(sorted((k, v[0]) for k, v in q.items())), [])
            if path.startswith("/queue"):
                return {"records": self.queue[app]}
            raise dn.ApiError(f"{app} {path}: 404")
        return call

    def bazarr(self, path, method="GET"):
        self.bazarr_calls.append((method, path))
        return None

    def file_check(self, path):
        self.checked.append(path)
        return {"verdict": "broken", "reasons": ["timestamp jump 280.9 s -> 118.0 s"], "notes": [], "duration": 3000.0}

    def arr_image(self, app, path):
        self.images.append((app, path))
        if path not in self.cover:
            raise dn.ApiError(f"{app} {path}: 404")
        return self.cover[path]

    def deps(self):
        return dn.Deps(seerr=None, arr=self.arr, qbit=None, sab=None, origins={"https://requests.example.com"}, manual_file=str(self.store),
                       report_secret=SECRET, report_origin=ORIGIN, bazarr=self.bazarr, file_check=self.file_check, arr_image=self.arr_image, watched=self.watched)

    def records(self):
        return json.loads(self.store.read_text())["problems"] if self.store.exists() else {}


def reset():
    manual.NOW = lambda: T
    manual.CACHE.clear()
    report.NOW = lambda: T
    report.LIB.clear()
    report.QUEUE_CACHE.clear()
    report.KICK = lambda dn_, deps, pid: report.process(dn_, deps, pid)
    report.CHECK = lambda deps, pid, path: report.run_check(deps, pid, path)
    report.SETTLE = (0, 1)


def call(w, method, path, headers=None, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    h = dict(headers if headers is not None else hdr())
    if method == "POST":
        h.setdefault("Origin", ORIGIN)
        h.setdefault("Content-Type", "application/json")
    status, ctype, data = report.handle(dn, method, path, h, raw, w.deps())
    return status, (json.loads(data) if ctype.startswith("application/json") else data)


def send(w, ref, reason, episode=None, note="", headers=None):
    body = {"ref": ref, "reason": reason, "note": note}
    if episode is not None:
        body["episodeId"] = episode
    return call(w, "POST", "/api/report", headers, body)


class Access(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World()

    def test_secret_required(self):
        for h in (hdr(secret=None), hdr(secret="wrong")):
            self.assertEqual(call(self.w, "GET", "/api/me", h)[0], 403)

    def test_user_and_group_required(self):
        self.assertEqual(call(self.w, "GET", "/api/me", hdr(user=None))[0], 403)
        self.assertEqual(call(self.w, "GET", "/api/me", hdr(groups="cloud-users|streaming-requesters"))[0], 403)

    def test_me(self):
        status, out = call(self.w, "GET", "/api/me")
        self.assertEqual((status, out["user"], out["admin"], out["left"]), (200, "jane", False, 2))
        self.assertTrue(call(self.w, "GET", "/api/me", hdr(**ADMIN))[1]["admin"])

    def test_page_served_and_unknown_404(self):
        status, page = call(self.w, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"report.js", page)
        self.assertEqual(call(self.w, "GET", "/api/nope")[0], 404)
        self.assertEqual(call(self.w, "GET", "/../download_now.py")[0], 404)

    def test_post_needs_origin_and_json(self):
        self.assertEqual(send(self.w, "radarr:12", "video", headers=hdr(Origin="https://evil.example.com"))[0], 403)
        self.assertEqual(send(self.w, "radarr:12", "video", headers=hdr(**{"Content-Type": "text/plain"}))[0], 415)


class Library(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World()

    def test_search_finds_titles_with_files_only(self):
        out = call(self.w, "GET", "/api/search?q=film")[1]["results"]
        self.assertEqual([r["ref"] for r in out], ["radarr:12"])
        self.assertEqual(call(self.w, "GET", "/api/search?q=another")[1]["results"][0]["ref"], "radarr:12")
        self.assertEqual(call(self.w, "GET", "/api/search?q=retreat")[1]["results"][0], {"ref": "sonarr:5", "kind": "tv", "title": "Company Retreat", "year": 2026})
        self.assertEqual(call(self.w, "GET", "/api/search?q=missing")[1]["results"], [])
        self.assertEqual(call(self.w, "GET", "/api/search?q=")[0], 400)

    def test_episodes_with_files_by_season(self):
        out = call(self.w, "GET", "/api/episodes?ref=sonarr:5")[1]
        self.assertEqual(out["seasons"], [{"season": 1, "episodes": [{"id": 501, "episode": 7, "title": "Seven"}, {"id": 502, "episode": 8, "title": "Eight"}]}])
        self.assertEqual(call(self.w, "GET", "/api/episodes?ref=radarr:12")[0], 400)

    def test_resolve_from_jellyfin_ids(self):
        self.assertEqual(call(self.w, "GET", "/api/resolve?type=movie&tmdb=4242")[1], {"ref": "radarr:12", "kind": "movie", "title": "Some Film", "year": 2004, "episodeId": None})
        out = call(self.w, "GET", "/api/resolve?type=tv&tvdb=9090&season=1&episode=8")[1]
        self.assertEqual((out["ref"], out["episodeId"]), ("sonarr:5", 502))
        self.assertEqual(call(self.w, "GET", "/api/resolve?type=tv&tmdb=4040")[1]["episodeId"], None)
        self.assertEqual(call(self.w, "GET", "/api/resolve?type=movie&tmdb=1")[0], 404)


class Browse(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World()
        self.w.movies += [{"id": 15, "title": "Zebra Days", "year": 2020, "hasFile": True, "added": "2026-11-10T10:00:00Z", "tags": []},
                          {"id": 16, "title": "ant farm", "year": 1999, "hasFile": True, "added": "2026-01-02T10:00:00Z", "tags": []}]
        self.w.movies[0]["added"] = "2026-06-01T10:00:00Z"
        self.w.movies[2]["added"] = "2026-03-01T10:00:00Z"
        self.w.series += [{"id": 6, "title": "Alpha Show", "year": 2010, "tags": [], "added": "2026-11-20T10:00:00Z", "statistics": {"episodeFileCount": 1}},
                          {"id": 7, "title": "Empty Show", "year": 2015, "tags": [], "statistics": {"episodeFileCount": 0}}]
        self.w.series[0]["added"] = "2026-02-01T10:00:00Z"

    def get(self, kind, sort="az", page=1, headers=None):
        return call(self.w, "GET", f"/api/browse?kind={kind}&sort={sort}&page={page}", headers)

    def refs(self, kind, sort="az", page=1):
        return [i["ref"] for i in self.get(kind, sort, page)[1]["items"]]

    def test_films_and_series_apart_with_files_only(self):
        self.assertEqual(self.refs("movie"), ["radarr:16", "radarr:14", "radarr:12", "radarr:15"])
        self.assertEqual(self.refs("tv"), ["sonarr:6", "sonarr:5"])

    def test_row_has_title_year_and_poster_only(self):
        self.assertEqual(self.get("tv")[1]["items"][1], {"ref": "sonarr:5", "kind": "tv", "title": "Company Retreat", "year": 2026})

    def test_sorts(self):
        self.assertEqual(self.refs("movie", "year"), ["radarr:15", "radarr:12", "radarr:16", "radarr:14"])
        self.assertEqual(self.refs("movie", "added"), ["radarr:15", "radarr:12", "radarr:14", "radarr:16"])
        self.assertEqual(self.refs("tv", "added"), ["sonarr:6", "sonarr:5"])

    def test_pages(self):
        report.BROWSE_PAGE = 3
        try:
            one, two = self.get("movie", page=1)[1], self.get("movie", page=2)[1]
        finally:
            report.BROWSE_PAGE = 50
        self.assertEqual((one["total"], one["pages"], one["page"], len(one["items"]), len(two["items"])), (4, 2, 1, 3, 1))
        self.assertEqual(self.get("movie", page=9)[1]["items"], [])

    def test_bad_parameters(self):
        for q in ("kind=book&sort=az", "kind=movie&sort=nope", "kind=movie&sort=az&page=0", "kind=movie&sort=az&page=x", "kind=movie&sort=watched"):
            self.assertEqual(call(self.w, "GET", "/api/browse?" + q)[0], 400, q)

    def test_needs_the_login(self):
        self.assertEqual(self.get("movie", headers=hdr(secret="wrong"))[0], 403)
        self.assertEqual(self.get("movie", headers=hdr(groups="cloud-users"))[0], 403)

    def test_watched_sort_hidden_without_a_jellyfin_match(self):
        self.assertEqual(self.get("movie")[1]["sorts"], ["az", "year", "added"])
        self.w.watched_by = lambda user: None
        self.assertEqual(self.get("movie")[1]["sorts"], ["az", "year", "added"])
        self.assertEqual(self.get("movie", "watched")[0], 400)

    def test_watched_sort_most_recent_first_for_the_signed_in_user(self):
        seen = []
        self.w.watched_by = lambda user: (seen.append(user), ["radarr:14", "sonarr:5", "radarr:12", "radarr:99"])[1]
        out = self.get("movie", "watched")[1]
        self.assertEqual(out["sorts"], ["az", "year", "added", "watched"])
        self.assertEqual([i["ref"] for i in out["items"]], ["radarr:14", "radarr:12"])
        self.assertEqual([i["ref"] for i in self.get("tv", "watched")[1]["items"]], ["sonarr:5"])
        self.assertEqual(set(seen), {"jane"})

    def test_poster_proxied_from_the_arr_and_cacheable(self):
        status, ctype, data = report.handle(dn, "GET", "/api/poster?ref=radarr:12", hdr(), b"", self.w.deps())
        self.assertEqual((status, ctype, data), (200, "image/jpeg", b"\xff\xd8jpeg"))
        self.assertEqual(self.w.images, [("radarr", "/MediaCover/12/poster-250.jpg")])

    def test_poster_missing_or_unknown(self):
        self.assertEqual(call(self.w, "GET", "/api/poster?ref=radarr:15")[0], 404)
        self.assertEqual(call(self.w, "GET", "/api/poster?ref=radarr:13")[0], 400)
        self.assertEqual(call(self.w, "GET", "/api/poster?ref=../x")[0], 400)
        self.assertEqual(call(self.w, "GET", "/api/poster?ref=radarr:12", hdr(secret="wrong"))[0], 403)


class Limits(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World()
        report.KICK = lambda dn_, deps, pid: None

    def test_two_per_day(self):
        self.assertEqual(send(self.w, "radarr:12", "other", note="the dog barks")[0], 200)
        self.assertEqual(send(self.w, "sonarr:5", "other", episode=501, note="x")[0], 200)
        status, out = send(self.w, "sonarr:5", "other", episode=502, note="x")
        self.assertEqual((status, out["error"]), (429, "limit"))
        self.assertEqual(call(self.w, "GET", "/api/me")[1]["left"], 0)
        report.NOW = lambda: T + DAY
        self.assertEqual(send(self.w, "sonarr:5", "other", episode=502, note="x")[0], 200)

    def test_admins_unlimited(self):
        for ep in (501, 502):
            self.assertEqual(send(self.w, "sonarr:5", "other", episode=ep, note="x", headers=hdr(**ADMIN))[0], 200)
        self.assertEqual(send(self.w, "radarr:12", "other", note="x", headers=hdr(**ADMIN))[0], 200)

    def test_one_open_report_per_item(self):
        status, first = send(self.w, "sonarr:5", "video", episode=502)
        self.assertEqual(status, 200)
        status, out = send(self.w, "sonarr:5", "audio", episode=502, headers=hdr(user="joe"))
        self.assertEqual((status, out["error"], out["id"]), (409, "already reported", first["id"]))
        self.assertEqual(send(self.w, "sonarr:5", "video", episode=501, headers=hdr(user="joe"))[0], 200)

    def test_validation(self):
        self.assertEqual(send(self.w, "radarr:12", "bogus")[0], 400)
        self.assertEqual(send(self.w, "radarr:12", "other")[0], 400)
        self.assertEqual(send(self.w, "radarr:12", "video", note="x" * 301)[0], 400)
        self.assertEqual(send(self.w, "sonarr:5", "video")[0], 400)
        self.assertEqual(send(self.w, "sonarr:5", "video", episode=503)[0], 400)
        self.assertEqual(send(self.w, "radarr:13", "video")[0], 400)
        self.assertEqual(send(self.w, "lidarr:1", "video")[0], 400)


class Actions(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World()

    def status(self, rid, headers=None):
        return call(self.w, "GET", "/api/report?id=" + rid, headers)[1]

    def test_replace_movie_blocks_and_grabs_best_other(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.1080p.BluRay-GRP", "g-old", rejected=True, rejections=["blocklisted"]),
                                                rel("Some.Film.2004.720p.WEB-X", "g-low", res=720, score=10),
                                                rel("Some.Film.2004.1080p.BluRay-OTHER", "g-new", rejected=True, rejections=[{"reason": "Existing file meets cutoff"}])]
        status, out = send(self.w, "radarr:12", "video", note="blocks everywhere")
        self.assertEqual(status, 200)
        w = self.w.writes
        self.assertIn(("radarr", "POST", "/history/failed/9001", None), w)
        self.assertIn(("radarr", "PUT", "/movie/editor", {"movieIds": [12], "tags": [4], "applyTags": "add"}), w)
        self.assertIn(("radarr", "POST", "/release", {"guid": "g-new", "indexerId": 3}), w)
        self.assertLess(w.index(("radarr", "POST", "/history/failed/9001", None)), w.index(("radarr", "POST", "/release", {"guid": "g-new", "indexerId": 3})))
        r = self.w.records()[out["id"]]
        self.assertEqual((r["type"], r["replace"]["mode"], r["replace"]["release"], r["replace"]["state"]), ("report", "replace", "Some.Film.2004.1080p.BluRay-OTHER", "grabbed"))
        self.assertIsNone(r["resolved"])
        self.assertEqual(self.w.checked, ["/data/media/movies/Some Film (2004)/Some.Film.mkv"])
        self.assertEqual(r["details"]["fileCheck"]["verdict"], "broken")
        self.assertIn("timestamp jump 280.9 s -> 118.0 s", r["details"]["problems"])
        st = self.status(out["id"])
        self.assertEqual((st["status"], st["title"], st["reason"]), ("downloading", "Some Film", "video"))
        self.assertNotIn("fileCheck", st)
        self.w.queue["radarr"] = [{"movieId": 12, "downloadId": "DLNEW", "title": "Some.Film.2004.1080p.BluRay-OTHER", "size": 1000, "sizeleft": 250}]
        report.QUEUE_CACHE.clear()
        self.assertEqual(self.status(out["id"])["progress"], 75)
        self.assertEqual(self.status(out["id"], hdr(**ADMIN))["fileCheck"]["verdict"], "broken")

    def test_slow_search_is_retried_and_a_refused_block_does_not_stop_it(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.1080p.BluRay-OTHER", "g-new")]
        real = self.w.arr
        fails = {"release": 1, "block": 1}

        def arr(app):
            call = real(app)

            def wrapped(path, method="GET", body=None, timeout=None):
                if path.startswith("/release?") and fails["release"]:
                    fails["release"] -= 1
                    raise dn.ApiError("radarr /release: timed out")
                if path.startswith("/history/failed/") and fails["block"]:
                    fails["block"] -= 1
                    raise dn.ApiError("radarr /history/failed: 500")
                return call(path, method, body, timeout)
            return wrapped
        self.w.arr = arr
        report.RETRY_WAIT = 0
        out = send(self.w, "radarr:12", "video")[1]
        self.assertIn(("radarr", "POST", "/release", {"guid": "g-new", "indexerId": 3}), self.w.writes)
        self.assertEqual(self.w.records()[out["id"]]["report"]["status"], "downloading")

    def test_replace_prefers_at_least_the_current_resolution(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.XviD-LOW", "g-low", res=0, source="unknown", score=900),
                                                rel("Some.Film.2004.720p.WEB-X", "g-720", res=720, score=800),
                                                rel("Some.Film.2004.1080p.WEB-DL-OK", "g-1080", source="webdl", score=10)]
        send(self.w, "radarr:12", "video")
        self.assertIn(("radarr", "POST", "/release", {"guid": "g-1080", "indexerId": 3}), self.w.writes)

    def test_lower_quality_only_when_nothing_else(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.XviD-LOW", "g-low", res=0, source="unknown", score=900),
                                                rel("Some.Film.2004.720p.WEB-X", "g-720", res=720, score=800)]
        send(self.w, "radarr:12", "video")
        self.assertIn(("radarr", "POST", "/release", {"guid": "g-720", "indexerId": 3}), self.w.writes)

    def test_grab_leaves_the_night_category_like_download_now(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.1080p.BluRay-OTHER", "g-new")]
        self.w.queue["radarr"] = [{"movieId": 12, "downloadId": "ABC123", "title": "Some.Film.2004.1080p.BluRay-OTHER", "size": 10, "sizeleft": 10}]
        qbit = []

        def q(path, data=None):
            qbit.append((path, data))
            return [{"hash": "abc123", "category": "radarr-night", "state": "pausedDL", "progress": 0}] if path == "/torrents/info" else {}
        d = self.w.deps()
        d.qbit = q
        d.sab = lambda params: {"queue": {"slots": []}}
        report.handle(dn, "POST", "/api/report", {**hdr(), "Origin": ORIGIN, "Content-Type": "application/json"}, json.dumps({"ref": "radarr:12", "reason": "video"}).encode(), d)
        self.assertIn(("/torrents/setCategory", {"hashes": "abc123", "category": "radarr"}), qbit)
        self.assertIn(("/torrents/start", {"hashes": "abc123"}), qbit)
        self.assertFalse(any(x[2] == "/command" for x in self.w.writes))

    def test_same_release_under_another_spelling_is_skipped(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some Film 2004 1080p BluRay-grp", "g-same", score=900),
                                                rel("Some.Film.2004.1080p.BluRay-OTHER", "g-other", score=10)]
        send(self.w, "radarr:12", "video")
        self.assertIn(("radarr", "POST", "/release", {"guid": "g-other", "indexerId": 3}), self.w.writes)

    def test_nothing_found_stays_open_for_admins(self):
        status, out = send(self.w, "radarr:12", "start")
        r = self.w.records()[out["id"]]
        self.assertEqual(r["report"]["status"], "nothing-found")
        self.assertIsNone(r["resolved"])
        self.assertIn(("radarr", "POST", "/history/failed/9001", None), self.w.writes)
        self.assertFalse(any(x[2] == "/release" for x in self.w.writes))

    def test_quality_only_better_and_never_blocks(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.1080p.WEB-X", "g-same", source="webdl")]
        out = send(self.w, "radarr:12", "quality")[1]
        r = self.w.records()[out["id"]]
        self.assertEqual(r["report"]["status"], "nothing-better")
        self.assertTrue(r["resolved"])
        self.assertEqual(self.w.writes, [])
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.2160p.UHD.BluRay-X", "g-4k", res=2160, rejected=True, rejections=["Not an upgrade for existing movie file(s)"])]
        report.QUEUE_CACHE.clear()
        out = send(self.w, "radarr:12", "quality", headers=hdr(user="joe"))[1]
        self.assertIn(("radarr", "POST", "/release", {"guid": "g-4k", "indexerId": 3}), self.w.writes)
        self.assertFalse(any("/history/failed/" in x[2] for x in self.w.writes))

    def test_episode_single_release(self):
        self.w.releases[(("episodeId", "502"),)] = [rel("Company.Retreat.S01E08.1080p.WEB-DL-cakes", "g-cakes", series=5, season=1, rejected=True, rejections=["blocklisted"]),
                                                   rel("Company.Retreat.S01E08.1080p.WEB-DL-GOOD", "g-good", series=5, season=1)]
        out = send(self.w, "sonarr:5", "video", episode=502)[1]
        self.assertIn(("sonarr", "POST", "/history/failed/9101", None), self.w.writes)
        self.assertIn(("sonarr", "PUT", "/series/editor", {"seriesIds": [5], "tags": [6], "applyTags": "add"}), self.w.writes)
        self.assertIn(("sonarr", "POST", "/release", {"guid": "g-good", "indexerId": 3}), self.w.writes)
        o = self.w.records()[out["id"]]["replace"]
        self.assertEqual((o["episodeIds"], o.get("only"), o["kind"]), ([502], [502], "tv"))
        self.assertEqual(self.w.checked, ["/data/media/tv/Company Retreat/Season 01/S01E08.mkv"])

    def test_episode_season_pack_when_no_single(self):
        self.w.releases[(("seasonNumber", "1"), ("seriesId", "5"))] = [rel("Company.Retreat.S01.1080p.WEB-DL-PACK", "g-pack", series=5, season=1, full=True,
                                                                         rejected=True, rejections=["Existing file on disk meets cutoff"])]
        out = send(self.w, "sonarr:5", "audio", episode=502)[1]
        self.assertIn(("sonarr", "POST", "/release", {"guid": "g-pack", "indexerId": 3}), self.w.writes)
        r = self.w.records()[out["id"]]
        self.assertEqual((r["replace"]["only"], r["replace"]["pack"]), ([502], True))

    def test_language_ok_goes_to_admins(self):
        out = send(self.w, "radarr:12", "language")[1]
        r = self.w.records()[out["id"]]
        self.assertEqual(r["report"]["status"], "language-ok")
        self.assertIsNone(r["resolved"])
        self.assertEqual(self.w.writes, [])
        self.assertEqual(r["details"]["audio"], ["German", "English"])

    def test_language_missing_replaces_with_wanted_language(self):
        self.w.episodefiles[602]["mediaInfo"]["audioLanguages"] = "Spanish"
        self.w.releases[(("episodeId", "502"),)] = [rel("Company.Retreat.S01E08.1080p.WEB-DL.SPANISH-X", "g-es", series=5, season=1, langs=("Spanish",), score=500),
                                                   rel("Company.Retreat.S01E08.1080p.WEB-DL-EN", "g-en", series=5, season=1, langs=("English",), score=1)]
        send(self.w, "sonarr:5", "sound", episode=502)
        self.assertIn(("sonarr", "POST", "/release", {"guid": "g-en", "indexerId": 3}), self.w.writes)

    def test_subtitles_bazarr(self):
        out = send(self.w, "sonarr:5", "subtitles", episode=502)[1]
        self.assertEqual(self.w.bazarr_calls, [("PATCH", "/api/series?seriesid=5&action=search-missing")])
        send(self.w, "radarr:12", "subtitles")
        self.assertEqual(self.w.bazarr_calls[-1], ("PATCH", "/api/movies?radarrid=12&action=search-missing"))
        self.assertEqual(self.w.records()[out["id"]]["report"]["status"], "subtitles")
        self.assertEqual([x for x in self.w.writes if x[2] == "/release"], [])

    def test_other_waits_for_admin(self):
        out = send(self.w, "radarr:12", "other", note="the dog barks")[1]
        r = self.w.records()[out["id"]]
        self.assertEqual((r["report"]["status"], r["resolved"], r["details"]["note"]), ("review", None, "the dog barks"))
        self.assertEqual(self.w.writes, [])

    def test_test_film_is_never_touched(self):
        self.w.releases[(("movieId", "14"),)] = [rel("Nosferatu.1922.1080p.BluRay-X", "g-n", movie=14)]
        out = send(self.w, "radarr:14", "video")[1]
        self.assertEqual(self.w.records()[out["id"]]["report"]["status"], "review")
        self.assertEqual(self.w.writes, [])
        self.assertEqual(self.w.checked, [])

    def test_dry_run_changes_nothing(self):
        self.w.releases[(("movieId", "12"),)] = [rel("Some.Film.2004.1080p.BluRay-OTHER", "g-new")]
        d = self.w.deps()
        d.report_dry_run = True
        status, ctype, data = report.handle(dn, "POST", "/api/report", {**hdr(), "Origin": ORIGIN, "Content-Type": "application/json"},
                                            json.dumps({"ref": "radarr:12", "reason": "video"}).encode(), d)
        self.assertEqual(status, 200)
        self.assertEqual(self.w.writes, [])
        r = self.w.records()[json.loads(data)["id"]]
        self.assertEqual(r["report"]["status"], "dry-run")
        self.assertIn("Some.Film.2004.1080p.BluRay-OTHER", r["report"]["result"])

    def test_own_reports_only_admins_see_all(self):
        report.KICK = lambda dn_, deps, pid: None
        a = send(self.w, "radarr:12", "other", note="x")[1]["id"]
        b = send(self.w, "sonarr:5", "other", episode=502, note="y", headers=hdr(user="joe"))[1]["id"]
        self.assertEqual([r["id"] for r in call(self.w, "GET", "/api/reports")[1]["reports"]], [a])
        self.assertEqual(sorted(r["id"] for r in call(self.w, "GET", "/api/reports", hdr(**ADMIN))[1]["reports"]), sorted([a, b]))
        self.assertEqual(call(self.w, "GET", "/api/report?id=" + b)[0], 404)
        self.assertEqual(call(self.w, "GET", "/api/report?id=" + b, hdr(**ADMIN))[0], 200)


class ManualPage(unittest.TestCase):
    """Report records live in Manual interaction's store: never closed by its scan, admins only, packs import only the reported episode."""

    def setUp(self):
        reset()
        self.w = World()
        report.KICK = lambda dn_, deps, pid: None

    def test_scan_keeps_report_records_open(self):
        out = send(self.w, "radarr:12", "other", note="x")[1]
        records = self.w.records()
        manual.merge(records, {}, True, T + 3600)
        self.assertIsNone(records[out["id"]]["resolved"])

    def test_view_hides_reports_from_non_admins(self):
        send(self.w, "radarr:12", "other", note="x")
        self.assertEqual(manual.view(self.w.deps(), False)["open"], [])
        shown = manual.view(self.w.deps(), True)["open"]
        self.assertEqual([(p["type"], p["offers"]) for p in shown], [("report", ["dismiss"])])

    def test_dismiss_by_admin(self):
        rid = send(self.w, "radarr:12", "other", note="x")[1]["id"]
        me = {"id": 1, "displayName": "Jane Doe", "permissions": 2}
        out = manual.dismiss_report(self.w.deps(), me, rid)
        self.assertTrue(out["ok"])
        self.assertEqual(self.w.records()[rid]["resolved"]["how"], "dismiss")
        with self.assertRaises(manual.Refused):
            manual.dismiss_report(self.w.deps(), me, rid)

    def test_pending_import_held_back_as_not_an_upgrade_is_forced(self):
        o = {"kind": "movie", "arrId": 12, "release": "Some.Film.2004.2160p-NEW", "at": "2026-11-20T12:00:00Z", "downloadIds": ["dlnew"], "mode": "replace", "from": "old", "runtime": 100}
        q = [{"movieId": 12, "downloadId": "DLNEW", "title": "Some.Film.2004.2160p-NEW", "trackedDownloadState": "importPending", "trackedDownloadStatus": "warning",
              "statusMessages": [{"messages": ["Not a Custom Format upgrade for existing movie file(s). New: [German DL] (2000000) do not improve on Existing: [German DL] (2002750)"]}]},
             {"movieId": 12, "downloadId": "DLOTHER", "title": "x", "trackedDownloadState": "importPending", "trackedDownloadStatus": "ok", "statusMessages": []}]
        sent = []

        def arr(path, method="GET", body=None, timeout=None):
            if method == "POST":
                sent.append(body)
                return {}
            if path.startswith("/history/"):
                return []
            return [{"path": "/data/usenet/complete/new/film.mkv", "downloadId": "DLNEW", "rejections": ["Not a Custom Format upgrade for existing movie file(s)"]}]
        old_virus = manual.virus
        manual.virus = lambda deps, path: {"state": "clean"}
        try:
            state, result = manual.step_replacement(self.w.deps(), arr, o, q, T)
        finally:
            manual.virus = old_virus
        self.assertEqual((state, sent[0]["name"]), ("importing", "ManualImport"))
        q[0]["trackedDownloadStatus"] = "ok"
        q[0]["statusMessages"] = []
        sent.clear()
        o.pop("importAt", None)
        self.assertEqual(manual.step_replacement(self.w.deps(), arr, o, q, T)[0], "downloading")
        self.assertEqual(sent, [])

    def test_force_import_only_the_reported_episode(self):
        o = {"kind": "tv", "arrId": 5, "only": [502], "runtime": 30}
        props = [{"path": "/data/torrents/pack/S01E07.mkv", "downloadId": "DLP", "episodes": [{"id": 501}], "rejections": ["Not an upgrade"]},
                 {"path": "/data/torrents/pack/S01E08.mkv", "downloadId": "DLP", "episodes": [{"id": 502}], "rejections": ["Not an upgrade"]}]
        sent = []

        def arr(path, method="GET", body=None, timeout=None):
            if method == "POST":
                sent.append(body)
                return {}
            return props
        old_virus = manual.virus
        manual.virus = lambda deps, path: {"state": "clean"}
        try:
            blocked = [{"downloadId": "DLP", "statusMessages": [{"messages": ["Not an upgrade for existing episode file(s)"]}]}]
            state, _ = manual.force_import(self.w.deps(), arr, o, blocked, T)
        finally:
            manual.virus = old_virus
        self.assertEqual(state, "importing")
        self.assertEqual([f["path"] for f in sent[0]["files"]], ["/data/torrents/pack/S01E08.mkv"])

class WatchedClient(unittest.TestCase):
    LIB = {"radarr": [{"id": 14, "tmdbId": 603}, {"id": 12, "tmdbId": 11}, {"id": 7, "tmdbId": 99}], "sonarr": [{"id": 5, "tvdbId": 81189}, {"id": 6}]}

    def setUp(self):
        self.calls, self.now, self.answer = [], [1000.0], {"movie": [603, 5, 11], "tv": [81189, 4]}
        self.real = dn._open
        dn._open = self.fake
        self.client = dn.watched_client("http://popular:8100", "k3y", lambda app: self.LIB[app], clock=lambda: self.now[0])

    def tearDown(self):
        dn._open = self.real

    def fake(self, req, timeout=15):
        self.calls.append((req.full_url, req.get_header("X-watched-secret"), timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def test_refs_newest_first_only_titles_in_the_library(self):
        self.assertEqual(self.client("jane"), ["radarr:14", "radarr:12", "sonarr:5"])
        self.assertEqual(self.calls[0][:2], ("http://popular:8100/watched?user=jane", "k3y"))

    def test_user_name_is_quoted(self):
        self.client("a b&c")
        self.assertEqual(self.calls[0][0], "http://popular:8100/watched?user=a%20b%26c")

    def test_answer_cached_one_minute_per_user(self):
        self.client("jane")
        self.client("jane")
        self.now[0] += 61
        self.client("jane")
        self.client("bob")
        self.assertEqual(len(self.calls), 3)

    def test_unknown_user_or_error_is_none_and_not_kept(self):
        self.answer = dn.ApiError("/watched: 404")
        self.assertIsNone(self.client("jane"))
        self.answer = {"movie": [603], "tv": []}
        self.assertEqual(self.client("jane"), ["radarr:14"])

    def test_malformed_answer_is_none(self):
        for bad in (None, [], {"movie": "x"}, {"tv": [1]}):
            self.answer = bad
            self.assertIsNone(self.client("jane"), bad)


if __name__ == "__main__":
    unittest.main()
