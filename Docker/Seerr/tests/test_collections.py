#!/usr/bin/env python3
"""download-now/collections.py: the "Collections" page backend — query syntax, name resolution with a pick list, the preview, who may save / edit / delete, limits, the store Jellyfin reads and the night refresh, against a fake Seerr.
Usage: python3 tests/test_collections.py"""
import datetime
import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "download-now"))
spec = importlib.util.spec_from_file_location("download_now", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
sys.modules["download_now"] = dn
spec.loader.exec_module(dn)
import collections_page as cp  # noqa: E402

ORIGIN = "https://requests.example.com"
T = datetime.datetime(2026, 11, 15, 18, 0).timestamp()
USER = {"id": 7, "username": "jane", "permissions": 32 | 128}
OTHER = {"id": 5, "jellyfinUsername": "joe", "permissions": 32}
ADMIN = {"id": 1, "username": "janedoe", "permissions": 2}
VIEWER = {"id": 9, "username": "kim", "permissions": 0}


def film(i, title, date, pop=10.0, vote=6.5, genres=(35,), status=None, job=None, dept=None, character=None):
    r = {"id": i, "mediaType": "movie", "title": title, "releaseDate": date, "popularity": pop, "voteAverage": vote, "voteCount": 500, "genreIds": list(genres), "posterPath": f"/p{i}.jpg"}
    if status:
        r["mediaInfo"] = {"status": status}
    if job:
        r.update(job=job, department=dept or "Production")
    if character is not None:
        r["character"] = character
    return r


def show(i, name, date, pop=10.0, genres=(35,), status=None, job=None, character=None):
    r = {"id": i, "mediaType": "tv", "name": name, "firstAirDate": date, "popularity": pop, "voteAverage": 7.0, "voteCount": 300, "genreIds": list(genres), "posterPath": f"/s{i}.jpg"}
    if status:
        r["mediaInfo"] = {"status": status}
    if job:
        r.update(job=job, department="Production")
    if character is not None:
        r["character"] = character
    return r


PAT = {"id": 501, "mediaType": "person", "name": "Pat Example", "profilePath": "/pat.jpg", "knownFor": [{"title": "Fake Film A", "mediaType": "movie"}, {"name": "Fake Show", "mediaType": "tv"}]}
PAT2 = {"id": 502, "mediaType": "person", "name": "Pat Example", "profilePath": None, "knownFor": [{"title": "Another Thing", "mediaType": "movie"}]}
CREDITS = {
    "cast": [film(11, "Fake Film A", "2001-05-01", pop=30, status=5, character="Hero"), show(12, "Late Night Fake", "1990-01-01", genres=(10767,), character="Self"),
             show(13, "Fake Show", "2010-03-03", character="Lead")],
    "crew": [film(21, "Produced One", "1999-02-02", pop=5, job="Producer"), film(22, "Produced Two", "2015-07-07", pop=50, vote=7.9, status=5, job="Executive Producer"),
             film(23, "Directed", "2005-01-01", job="Director", dept="Directing"), film(24, "Written", "2008-01-01", job="Screenplay", dept="Writing"),
             film(25, "No Date Yet", "", job="Producer"), film(22, "Produced Two", "2015-07-07", pop=50, vote=7.9, status=5, job="Producer")],
}


class Seerr:
    """Seerr as the backend sees it: the caller's session (auth/me) and the admin key for TMDB data; counts every TMDB call."""

    def __init__(self, me=USER, routes=None):
        self.me, self.calls = me, []
        self.routes = {
            "/search?query=Pat%20Example&page=1&language=en": {"results": [PAT, PAT2, film(99, "Pat Example Story", "2020-01-01")]},
            "/search?query=Solo%20Person&page=1&language=en": {"results": [dict(PAT, id=601, name="Solo Person")]},
            "/person/501/combined_credits?language=en": CREDITS,
            "/person/601/combined_credits?language=en": CREDITS,
            "/person/501?language=en": {"id": 501, "name": "Pat Example"},
            "/search/company?query=Fake%20Studio&page=1&language=en": {"results": [{"id": 77, "name": "Fake Studio", "logoPath": None}]},
            "/discover/movies?studio=77&sortBy=popularity.desc&page=1&language=en": {"totalPages": 1, "results": [film(31, "Studio Film", "2011-01-01", status=4), film(32, "Studio Film 2", "1995-01-01")]},
            "/search/keyword?query=heist&page=1&language=en": {"results": [{"id": 9748, "name": "heist"}, {"id": 1, "name": "heist movie"}]},
            "/discover/movies?keywords=9748&sortBy=popularity.desc&page=1&language=en": {"totalPages": 1, "results": [film(21, "Produced One", "1999-02-02"), film(41, "Heist Film", "2003-01-01")]},
            "/discover/tv?keywords=9748&sortBy=popularity.desc&page=1&language=en": {"totalPages": 1, "results": []},
            "/discover/movies?genre=35&primaryReleaseDateGte=1990-01-01&primaryReleaseDateLte=1999-12-31&sortBy=popularity.desc&page=1&language=en": {"totalPages": 1, "results": [film(51, "Nineties Comedy", "1994-01-01")]},
            "/search?query=Fake%20Saga&page=1&language=en": {"results": [film(61, "Fake Saga", "2001-01-01"), film(62, "Fake Saga 2", "2003-01-01")]},
            "/movie/61?language=en": {"id": 61, "collection": {"id": 900, "name": "Fake Saga Collection", "posterPath": "/c.jpg"}},
            "/movie/62?language=en": {"id": 62, "collection": {"id": 900, "name": "Fake Saga Collection", "posterPath": "/c.jpg"}},
            "/collection/900?language=en": {"id": 900, "name": "Fake Saga Collection", "parts": [film(61, "Fake Saga", "2001-01-01", status=5), film(62, "Fake Saga 2", "2003-01-01")]},
        }
        self.routes.update(routes or {})

    def session(self, path, auth):
        if path == "/auth/me":
            if self.me is None:
                raise dn.ApiError("seerr: 403")
            return self.me
        raise dn.ApiError("seerr: 404")

    def admin(self, path, method="GET", body=None):
        assert method == "GET", (method, path)
        self.calls.append(path)
        if path in self.routes:
            return self.routes[path]
        raise dn.ApiError(f"seerr {path}: 404")


class World:
    def __init__(self, me=USER, routes=None):
        self.seerr = Seerr(me, routes)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = pathlib.Path(self.tmp.name, "collections", "lists.json")

    def deps(self):
        return dn.Deps(seerr=self.seerr.session, arr=None, qbit=None, sab=None, origins={ORIGIN}, seerr_admin=self.seerr.admin, collections_file=str(self.store))

    def as_user(self, me):
        self.seerr.me = me
        return self

    def get(self, path):
        return dn.handle("GET", "/_download-now/collections/" + path, {"Cookie": "connect.sid=s%3Aabc"}, b"", self.deps())

    def post(self, sub, body, headers=None, raw=None):
        h = {"Content-Type": "application/json", "Origin": ORIGIN, "Cookie": "connect.sid=s%3Aabc"}
        h.update(headers or {})
        return dn.handle("POST", "/_download-now/collections/" + sub, h, raw if raw is not None else json.dumps(body).encode(), self.deps())

    def save(self, name="Pat produced", query="producer:Solo Person", **kw):
        cp.THROTTLE.clear()
        return self.post("save", dict({"name": name, "query": query, "sort": "release", "max": 50}, **kw))

    def stored(self):
        return json.loads(self.store.read_text())


def reset(now=T):
    cp.NOW = lambda: now
    cp.CACHE.clear()
    cp.THROTTLE.clear()
    cp.FLOOD.clear()
    cp.MEM.clear()
    cp.BUSY.clear()


def q(text, **params):
    import urllib.parse
    return "preview?" + urllib.parse.urlencode(dict({"q": text}, **params))


class Parse(unittest.TestCase):
    def test_terms_and_free_names(self):
        self.assertEqual(cp.parse("producer:Adam Example genre:comedy year:1990-1999 type:movie"),
                         [("producer", "Adam Example"), ("genre", "comedy"), ("year", "1990-1999"), ("type", "movie")])

    def test_case_quotes_and_spacing(self):
        self.assertEqual(cp.parse('  Actor: "Pat  Example"   STUDIO:fake studio '), [("actor", "Pat Example"), ("studio", "fake studio")])

    def test_errors(self):
        for text, code in (("", "empty"), ("adam example", "syntax"), ("cast:Pat", "syntax"), ("producer:", "empty-term"), ("year:19", "year"), ("year:2010-1990", "year"),
                           ("type:book", "type"), ("genre:nope", "genre"), ("x" * 301, "too-long"), (" ".join(["genre:comedy"] * 7), "too-many"),
                           ("year:1990 year:2000", "twice"), ("type:movie type:tv", "twice"), ("genre:comedy", "anchor-or-filter")):
            with self.subTest(text=text[:40]):
                with self.assertRaises(cp.Refused) as e:
                    cp.check_terms(cp.parse(text)) if code in ("year", "type", "genre", "twice", "anchor-or-filter") else cp.parse(text)
                self.assertEqual(e.exception.error, code)

    def test_genre_alone_is_fine_with_another_filter_and_german_names(self):
        cp.check_terms(cp.parse("genre:komödie year:1990-1999"))
        self.assertEqual(cp.genre_ids("Komödie"), ("35", "35"))
        self.assertEqual(cp.genre_ids("horror"), ("27", None))


class Preview(unittest.TestCase):
    def setUp(self):
        reset()

    def test_ambiguous_name_asks_to_pick_with_photo_and_known_for(self):
        w = World()
        status, out = w.get(q("producer:Pat Example"))
        self.assertEqual(status, 200)
        self.assertEqual(out["status"], "pick")
        pick = out["picks"][0]
        self.assertEqual((pick["term"], pick["field"], pick["value"]), (0, "producer", "Pat Example"))
        self.assertEqual([o["id"] for o in pick["options"]], [501, 502])
        self.assertEqual(pick["options"][0]["photo"], "/pat.jpg")
        self.assertEqual(pick["options"][0]["known"], ["Fake Film A", "Fake Show"])

    def test_picked_person_gives_the_producer_titles_sorted_and_marked_owned(self):
        w = World()
        status, out = w.get(q("producer:Pat Example", pick="0:501"))
        self.assertEqual(status, 200, out)
        self.assertEqual(out["status"], "ok")
        self.assertEqual([(i["k"], i["id"]) for i in out["items"]], [("movie", 22), ("movie", 21), ("movie", 25)])
        self.assertEqual([i["o"] for i in out["items"]], [True, False, False])
        self.assertEqual(out["items"][0]["y"], 2015)
        self.assertEqual(out["query"], "producer:Pat Example")
        self.assertEqual((out["count"], out["owned"]), (3, 1))

    def test_single_exact_name_resolves_without_pick(self):
        status, out = World().get(q("producer:Solo Person"))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["items"]), 3)

    def test_actor_skips_talk_show_appearances_as_self(self):
        status, out = World().get(q("actor:Solo Person"))
        self.assertEqual([(i["k"], i["id"]) for i in out["items"]], [("tv", 13), ("movie", 11)])

    def test_director_writer_filters_and_sorts(self):
        w = World()
        self.assertEqual([i["id"] for i in w.get(q("director:Solo Person"))[1]["items"]], [23])
        self.assertEqual([i["id"] for i in w.get(q("writer:Solo Person"))[1]["items"]], [24])
        self.assertEqual([i["id"] for i in w.get(q("producer:Solo Person year:1990-2009"))[1]["items"]], [21])
        self.assertEqual([i["id"] for i in w.get(q("producer:Solo Person", sort="popularity"))[1]["items"]], [22, 25, 21])
        self.assertEqual([i["id"] for i in w.get(q("producer:Solo Person", sort="rating", max="1"))[1]["items"]], [22])
        self.assertEqual([i["id"] for i in w.get(q("actor:Solo Person type:series"))[1]["items"]], [13])

    def test_keyword_intersects_with_a_person(self):
        out = World().get(q("producer:Solo Person keyword:heist"))[1]
        self.assertEqual([i["id"] for i in out["items"]], [21])

    def test_studio_franchise_and_filters_only(self):
        w = World()
        self.assertEqual([(i["id"], i["o"]) for i in w.get(q("studio:Fake Studio"))[1]["items"]], [(31, True), (32, False)])
        out = w.get(q("franchise:Fake Saga"))[1]
        self.assertEqual([i["id"] for i in out["items"]], [62, 61])
        self.assertEqual([i["id"] for i in w.get(q("genre:comedy year:1990-1999 type:movie"))[1]["items"]], [51])

    def test_tmdb_answers_are_cached(self):
        w = World()
        w.get(q("producer:Solo Person"))
        n = len(w.seerr.calls)
        cp.THROTTLE.clear()
        w.get(q("producer:Solo Person", sort="popularity"))
        self.assertEqual(len(w.seerr.calls), n)

    def test_bad_input_is_400_with_a_code(self):
        w = World()
        self.assertEqual(w.get(q("cast:Pat")), (400, {"error": "syntax"}))
        self.assertEqual(w.get(q("producer:Solo Person", max="500")), (400, {"error": "max"}))
        self.assertEqual(w.get(q("producer:Solo Person", sort="name")), (400, {"error": "sort"}))
        self.assertEqual(w.get(q("producer:Pat Example", pick="0:x")), (400, {"error": "pick"}))
        self.assertEqual(w.get(q("producer:Pat Example", pick="0:999")), (400, {"error": "pick"}))

    def test_unknown_name_is_404_not_found(self):
        w = World(routes={"/search?query=Nobody%20Known&page=1&language=en": {"results": []}})
        self.assertEqual(w.get(q("producer:Nobody Known")), (404, {"error": "not-found", "term": 0}))

    def test_previews_are_rate_limited_per_user(self):
        w = World()
        for _ in range(cp.PREVIEW_MAX):
            self.assertEqual(w.get(q("producer:Solo Person"))[0], 200)
        self.assertEqual(w.get(q("producer:Solo Person")), (429, {"error": "slow-down"}))
        self.assertEqual(w.as_user(OTHER).get(q("producer:Solo Person"))[0], 200)

    def test_seerr_error_is_502_without_details(self):
        w = World(routes={"/person/601/combined_credits?language=en": None})
        del w.seerr.routes["/person/601/combined_credits?language=en"]
        status, out = w.get(q("producer:Solo Person"))
        self.assertEqual((status, out), (502, {"error": "not-reachable"}))


class Gate(unittest.TestCase):
    def setUp(self):
        reset()

    def test_no_session_is_403_everywhere(self):
        w = World(me=None)
        self.assertEqual(w.get("lists")[0], 403)
        self.assertEqual(w.get(q("producer:Solo Person"))[0], 403)
        self.assertEqual(w.save()[0], 403)

    def test_post_needs_origin_and_json_and_a_small_body(self):
        w = World()
        self.assertEqual(w.post("save", {"name": "x"}, {"Origin": "https://evil.example.com"})[0], 403)
        self.assertEqual(w.post("save", {"name": "x"}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(w.post("save", None, raw=b"{nope")[0], 400)
        self.assertEqual(w.post("save", None, raw=b"[]")[0], 400)
        self.assertEqual(w.post("save", None, raw=json.dumps({"name": "x" * 5000}).encode())[0], 413)

    def test_unknown_paths_and_methods_are_404(self):
        w = World()
        self.assertEqual(w.get("save")[0], 404)
        self.assertEqual(w.post("lists", {})[0], 404)
        self.assertEqual(w.get("../manual/problems")[0], 404)

    def test_who_may_create(self):
        w = World(me=VIEWER)
        self.assertEqual(w.save(), (403, {"error": "forbidden"}))
        self.assertEqual(w.get("lists")[1]["me"]["create"], False)


class Lists(unittest.TestCase):
    def setUp(self):
        reset()

    def test_save_stores_the_resolved_list_for_jellyfin(self):
        w = World()
        status, out = w.save(name="  Pat  produced ")
        self.assertEqual(status, 200, out)
        lst = w.stored()["lists"][0]
        self.assertEqual(lst["id"], "user-pat-produced")
        self.assertEqual(lst["name"], "Pat produced")
        self.assertEqual((lst["owner"], lst["ownerName"]), (7, "jane"))
        self.assertEqual(lst["terms"], [{"f": "producer", "id": 601, "name": "Solo Person"}])
        self.assertEqual(lst["query"], "producer:Solo Person")
        self.assertEqual([(i["k"], i["id"]) for i in lst["items"]], [("movie", 22), ("movie", 21), ("movie", 25)])
        self.assertEqual(stat.S_IMODE(os.stat(w.store).st_mode), 0o640)

    def test_save_with_an_ambiguous_name_asks_for_a_pick_first(self):
        w = World()
        status, out = w.save(query="producer:Pat Example")
        self.assertEqual((status, out["status"]), (409, "pick"))
        self.assertFalse(w.store.exists())
        self.assertEqual(w.save(query="producer:Pat Example", picks={"0": 501})[0], 200)

    def test_listing_shows_everyones_lists_with_rights(self):
        w = World()
        w.save()
        w.as_user(OTHER).save(name="Joe list", query="studio:Fake Studio")
        out = w.as_user(USER).get("lists")[1]
        self.assertEqual([(l["name"], l["ownerName"], l["canEdit"]) for l in out["lists"]], [("Joe list", "joe", False), ("Pat produced", "jane", True)])
        self.assertEqual(out["lists"][0]["count"], 2)
        self.assertEqual(out["lists"][0]["owned"], 1)
        self.assertEqual(out["limits"], {"total": 20, "perUser": 5, "mine": 1, "all": 2})
        self.assertTrue(all(l["canEdit"] for l in w.as_user(ADMIN).get("lists")[1]["lists"]))

    def test_edit_and_delete_only_by_creator_or_admin(self):
        w = World()
        w.save()
        lid = w.stored()["lists"][0]["id"]
        self.assertEqual(w.as_user(OTHER).save(id=lid, name="Taken over")[0], 403)
        cp.THROTTLE.clear()
        self.assertEqual(w.as_user(OTHER).post("delete", {"id": lid, "confirm": "delete"})[0], 403)
        self.assertEqual(w.as_user(USER).save(id=lid, name="Renamed", query="producer:Solo Person year:1990-1999")[0], 200)
        lst = w.stored()["lists"][0]
        self.assertEqual((lst["id"], lst["name"], len(lst["items"])), (lid, "Renamed", 1))
        cp.THROTTLE.clear()
        self.assertEqual(w.as_user(ADMIN).post("delete", {"id": lid})[0], 400)
        cp.THROTTLE.clear()
        self.assertEqual(w.as_user(ADMIN).post("delete", {"id": lid, "confirm": "delete"})[0], 200)
        self.assertEqual(w.stored()["lists"], [])

    def test_unknown_list_is_404(self):
        w = World()
        self.assertEqual(w.save(id="user-nope")[0], 404)
        cp.THROTTLE.clear()
        self.assertEqual(w.post("delete", {"id": "user-nope", "confirm": "delete"})[0], 404)

    def test_limits_per_user_and_in_total(self):
        w = World()
        for i in range(5):
            self.assertEqual(w.save(name=f"Mine {i}")[0], 200)
        self.assertEqual(w.save(name="Mine 6"), (409, {"error": "limit-user"}))
        cp.LIMIT_TOTAL = 6
        try:
            self.assertEqual(w.as_user(OTHER).save(name="Joe one")[0], 200)
            self.assertEqual(w.as_user(ADMIN).save(name="Admin one"), (409, {"error": "limit-total"}))
        finally:
            cp.LIMIT_TOTAL = 20

    def test_names_are_checked_and_unique(self):
        w = World()
        for name, code in (("", "name"), ("x" * 61, "name"), ("bad\nname", "name"), ("<b>", "name")):
            with self.subTest(name=name[:10]):
                self.assertEqual(w.save(name=name), (400, {"error": code}))
        self.assertEqual(w.save(name="Pat produced")[0], 200)
        self.assertEqual(w.save(name="PAT PRODUCED"), (409, {"error": "name-taken"}))
        self.assertEqual(w.save(name="Coming soon"), (409, {"error": "name-taken"}))
        self.assertEqual(w.save(name="Pat produced!")[0], 200)
        self.assertEqual(sorted(l["id"] for l in w.stored()["lists"]), ["user-pat-produced", "user-pat-produced-2"])

    def test_empty_result_is_not_saved(self):
        w = World(routes={"/person/601/combined_credits?language=en": {"cast": [], "crew": []}})
        self.assertEqual(w.save(), (400, {"error": "no-titles"}))

    def test_saves_are_rate_limited(self):
        w = World()
        self.assertEqual(w.post("save", {"name": "One", "query": "producer:Solo Person", "sort": "release", "max": 50})[0], 200)
        self.assertEqual(w.post("save", {"name": "Two", "query": "producer:Solo Person", "sort": "release", "max": 50})[0], 429)

    def test_unreadable_store_is_never_overwritten(self):
        w = World()
        w.store.parent.mkdir(parents=True)
        w.store.write_text("{broken")
        self.assertEqual(w.save()[0], 503)
        self.assertEqual(w.store.read_text(), "{broken")

    def test_store_keeps_a_backup(self):
        w = World()
        w.save()
        w.save(name="Second")
        self.assertEqual(len(json.loads(w.store.with_name("lists.json.bak").read_text())["lists"]), 1)


class Night(unittest.TestCase):
    def setUp(self):
        reset(datetime.datetime(2026, 11, 16, 4, 5).timestamp())

    def test_refresh_once_a_night_inside_the_window_and_only_writes_on_change(self):
        w = World()
        w.save()
        mtime = os.stat(w.store).st_mtime_ns
        cp.CACHE.clear()
        self.assertEqual(cp.nightly(dn, w.deps()), 1)
        self.assertEqual(os.stat(w.store).st_mtime_ns, mtime)
        self.assertEqual(cp.nightly(dn, w.deps()), 0)
        w.seerr.routes["/person/601/combined_credits?language=en"] = {"cast": [], "crew": CREDITS["crew"] + [film(26, "New Release", "2026-11-01", job="Producer")]}
        cp.CACHE.clear()
        cp.NOW = lambda: datetime.datetime(2026, 11, 17, 4, 5).timestamp()
        self.assertEqual(cp.nightly(dn, w.deps()), 1)
        self.assertEqual(w.stored()["lists"][0]["items"][0]["id"], 26)
        cp.NOW = lambda: datetime.datetime(2026, 11, 18, 12, 0).timestamp()
        self.assertEqual(cp.nightly(dn, w.deps()), 0)

    def test_a_failing_list_keeps_its_titles(self):
        w = World()
        w.save()
        del w.seerr.routes["/person/601/combined_credits?language=en"]
        cp.CACHE.clear()
        cp.nightly(dn, w.deps())
        self.assertEqual(len(w.stored()["lists"][0]["items"]), 3)


class Hardening(unittest.TestCase):
    """Findings of the security review: memory, threads, races, names, file modes."""

    def setUp(self):
        reset()

    def test_cache_keeps_only_the_fields_used_and_stays_under_its_size(self):
        big = dict(CREDITS)
        big["cast"] = [dict(film(1000 + i, f"Film {i}", "2001-01-01", character="Hero"), overview="x" * 5000, backdropPath="/b.jpg") for i in range(50)]
        w = World(routes={"/person/601/combined_credits?language=en": big})
        w.get(q("actor:Solo Person"))
        entry = cp.CACHE["/person/601/combined_credits?language=en"]
        self.assertNotIn("overview", json.dumps(entry[1]))
        self.assertLess(entry[2], 50 * 400)
        cp.CACHE_BYTES, old = 2000, cp.CACHE_BYTES
        try:
            cp.CACHE.clear()
            cp.FLOOD.clear()
            w.get(q("actor:Solo Person"))
            self.assertLessEqual(sum(e[2] for e in cp.CACHE.values()), 2000 + 20000)
            self.assertLessEqual(len(cp.CACHE), 2)
        finally:
            cp.CACHE_BYTES = old

    def test_expired_answers_are_dropped_on_insert(self):
        w = World()
        w.get(q("producer:Solo Person"))
        n = len(cp.CACHE)
        cp.NOW = lambda: T + cp.CACHE_TTL + 1
        cp.FLOOD.clear()
        w.get(q("studio:Fake Studio"))
        self.assertLess(len(cp.CACHE), n + 2)
        self.assertTrue(all(T + cp.CACHE_TTL + 1 - e[0] < cp.CACHE_TTL for e in cp.CACHE.values()))

    def test_busy_server_answers_429_at_once_instead_of_queueing(self):
        w = World()
        for _ in range(cp.IN_FLIGHT):
            self.assertTrue(cp.SLOTS.acquire(blocking=False))
        try:
            self.assertEqual(w.get(q("producer:Solo Person")), (429, {"error": "busy"}))
            self.assertEqual(w.save(), (429, {"error": "busy"}))
        finally:
            for _ in range(cp.IN_FLIGHT):
                cp.SLOTS.release()
        self.assertEqual(w.get(q("producer:Solo Person"))[0], 200)

    def test_one_preview_or_save_per_user_at_a_time(self):
        w = World()
        cp.BUSY.add(USER["id"])
        self.assertEqual(w.get(q("producer:Solo Person")), (429, {"error": "busy"}))
        self.assertEqual(w.as_user(OTHER).get(q("producer:Solo Person"))[0], 200)
        cp.BUSY.clear()
        self.assertEqual(w.as_user(USER).get(q("producer:Solo Person"))[0], 200)
        self.assertEqual(cp.BUSY, set())

    def test_tmdb_calls_per_query_are_capped(self):
        w = World()
        cp.CALL_BUDGET, old = 2, cp.CALL_BUDGET
        try:
            self.assertEqual(w.get(q("producer:Solo Person keyword:heist")), (400, {"error": "too-broad"}))
        finally:
            cp.CALL_BUDGET = old

    def test_non_object_picks_are_400(self):
        w = World()
        for picks in ([1], "0:501", 5):
            cp.THROTTLE.clear()
            self.assertEqual(w.save(picks=picks), (400, {"error": "pick"}))

    def test_backup_is_group_readable_only(self):
        w = World()
        w.save()
        w.save(name="Second")
        self.assertEqual(stat.S_IMODE(os.stat(w.store.with_name("lists.json.bak")).st_mode), 0o640)

    def test_creator_names_outside_the_name_charset_become_user_n(self):
        w = World(me={"id": 12, "username": "<b>evil</b>", "permissions": 32})
        w.save()
        self.assertEqual(w.stored()["lists"][0]["ownerName"], "user 12")
        w.as_user({"id": 13, "jellyfinUsername": "Kim Example", "permissions": 32}).save(name="Kims")
        self.assertEqual(w.stored()["lists"][1]["ownerName"], "Kim Example")

    def test_edit_rechecks_owner_and_list_inside_the_lock(self):
        w = World()
        w.save()
        lid = w.stored()["lists"][0]["id"]
        real = cp.run_query

        def swap(deps, req):
            out = real(deps, req)
            lists = cp.load(deps)
            lists[0].update(owner=5, created="2030-01-01T00:00:00Z")
            cp.save(deps, lists)
            return out
        cp.run_query = swap
        try:
            self.assertEqual(w.save(id=lid, name="Mine again")[0], 409)
        finally:
            cp.run_query = real
        self.assertEqual(w.stored()["lists"][0]["name"], "Pat produced")

    def test_night_refresh_skips_a_list_edited_meanwhile(self):
        cp.NOW = lambda: datetime.datetime(2026, 11, 16, 4, 5).timestamp()
        w = World()
        w.save()
        w.seerr.routes["/person/601/combined_credits?language=en"] = {"cast": [], "crew": [film(26, "New Release", "2026-11-01", job="Producer")]}
        cp.CACHE.clear()
        real = cp.evaluate

        def edit_during(deps, terms, sort, cap, **kw):
            out = real(deps, terms, sort, cap, **kw)
            lists = cp.load(deps)
            lists[0].update(name="Edited", updated="2026-11-16T03:06:00Z", items=[{"k": "movie", "id": 1, "t": "x", "y": 2000, "p": None, "o": False}])
            cp.save(deps, lists)
            return out
        cp.evaluate = edit_during
        try:
            cp.nightly(dn, w.deps())
        finally:
            cp.evaluate = real
        lst = w.stored()["lists"][0]
        self.assertEqual((lst["name"], [i["id"] for i in lst["items"]]), ("Edited", [1]))


if __name__ == "__main__":
    unittest.main()
