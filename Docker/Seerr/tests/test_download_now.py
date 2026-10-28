#!/usr/bin/env python3
"""download-now/download_now.py: who may press, what a press does to Radarr/Sonarr, qBittorrent and SABnzbd, against fake clients.
Usage: python3 tests/test_download_now.py"""
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dn", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dn)

TAG = 64
ORIGIN = "https://requests.example.com"


def movie(mid=43, tags=(63,), has_file=False):
    return {"id": mid, "title": "Some Film", "tmdbId": 673, "tags": list(tags), "hasFile": has_file, "monitored": True}


def series(sid=17, tags=(), files=3, count=12):
    return {"id": sid, "title": "Some Series", "tvdbId": 999, "tags": list(tags), "statistics": {"episodeFileCount": files, "episodeCount": count}}


def rec(dl, title=43, kind="movie"):
    return {("movieId" if kind == "movie" else "seriesId"): title, "downloadId": dl}


def tor(h, cat="radarr-night", state="stoppedDL", progress=0.0):
    return {"hash": h, "category": cat, "state": state, "progress": progress}


def job(n, cat="movies-night", status="Paused"):
    return {"nzo_id": n, "cat": cat, "status": status}


def own_media(*users, status=2, sid=43):
    return {"mediaInfo": {"externalServiceId": sid, "tvdbId": 999, "requests": [{"id": 50 + u, "status": status, "requestedBy": {"id": u}} for u in users]}}


class Plan(unittest.TestCase):
    def test_paused_night_torrent_moves_to_the_normal_category_and_starts(self):
        p = dn.plan("movie", movie(), TAG, [rec("ABC")], [tor("abc")], [])
        self.assertEqual(p["state"], "started")
        self.assertTrue(p["add_tag"])
        self.assertEqual(p["qbit"], [("abc", "radarr", True)])
        self.assertEqual(p["released"], ["abc"])
        self.assertFalse(p["search"])

    def test_running_night_torrent_moves_without_a_resume(self):
        p = dn.plan("movie", movie(), TAG, [rec("abc")], [tor("abc", state="downloading")], [])
        self.assertEqual(p["qbit"], [("abc", "radarr", False)])

    def test_finished_night_torrent_is_left_alone(self):
        p = dn.plan("movie", movie(), TAG, [rec("abc")], [tor("abc", state="stalledUP", progress=1.0)], [])
        self.assertEqual((p["state"], p["qbit"]), ("downloading", []))

    def test_paused_night_nzb_moves_and_resumes(self):
        p = dn.plan("tv", series(), TAG, [rec("SABnzbd_nzo_1", 17, "tv"), rec("SABnzbd_nzo_2", 17, "tv")], [], [job("SABnzbd_nzo_1", "tv-night"), job("SABnzbd_nzo_2", "tv-night", "Downloading")])
        self.assertEqual(p["state"], "started")
        self.assertEqual(p["sab"], [("SABnzbd_nzo_1", "tv", True), ("SABnzbd_nzo_2", "tv", False)])
        self.assertEqual(p["released"], ["sabnzbd_nzo_1", "sabnzbd_nzo_2"])

    def test_only_this_titles_downloads_are_touched(self):
        p = dn.plan("movie", movie(43), TAG, [rec("abc", 43), rec("def", 44)], [tor("abc"), tor("def")], [job("n1")])
        self.assertEqual([q[0] for q in p["qbit"]], ["abc"])
        self.assertEqual(p["sab"], [])

    def test_tag_already_there_is_not_added_again(self):
        p = dn.plan("movie", movie(tags=(63, TAG)), TAG, [rec("abc")], [tor("abc")], [])
        self.assertFalse(p["add_tag"])

    def test_normal_category_download_is_already_downloading(self):
        p = dn.plan("movie", movie(), TAG, [rec("abc")], [tor("abc", cat="radarr", state="downloading")], [])
        self.assertEqual(p["state"], "downloading")
        self.assertFalse(p["add_tag"])
        self.assertEqual((p["qbit"], p["sab"], p["search"]), ([], [], False))

    def test_hand_paused_normal_download_is_not_resumed(self):
        p = dn.plan("movie", movie(), TAG, [rec("abc")], [tor("abc", cat="radarr", state="stoppedDL")], [])
        self.assertEqual((p["state"], p["qbit"]), ("downloading", []))

    def test_nothing_grabbed_and_missing_searches_with_the_tag(self):
        p = dn.plan("movie", movie(), TAG, [], [], [])
        self.assertEqual(p["state"], "searching")
        self.assertTrue(p["add_tag"])
        self.assertTrue(p["search"])

    def test_series_with_missing_episodes_searches(self):
        self.assertEqual(dn.plan("tv", series(files=3, count=12), TAG, [], [], [])["state"], "searching")

    def test_downloaded_film_does_nothing(self):
        p = dn.plan("movie", movie(has_file=True), TAG, [], [], [])
        self.assertEqual(p["state"], "available")
        self.assertEqual((p["add_tag"], p["search"], p["qbit"], p["sab"]), (False, False, [], []))

    def test_complete_series_does_nothing(self):
        self.assertEqual(dn.plan("tv", series(files=12, count=12), TAG, [], [], [])["state"], "available")

    def test_sonarr_night_category_maps_to_sonarr(self):
        p = dn.plan("tv", series(), TAG, [rec("abc", 17, "tv")], [tor("abc", cat="sonarr-night")], [])
        self.assertEqual(p["qbit"], [("abc", "sonarr", True)])


class Allowed(unittest.TestCase):
    def test_admin_and_manage_requests_may(self):
        self.assertTrue(dn.allowed({"id": 1, "permissions": 2}))
        self.assertTrue(dn.allowed({"id": 3, "permissions": 16 | 32}))

    def test_own_request_needs_the_same_user_and_a_live_request(self):
        mine = {"mediaInfo": {"requests": [{"status": 2, "requestedBy": {"id": 7}}]}}
        self.assertTrue(dn.owns({"id": 7, "permissions": 8352}, mine))
        self.assertFalse(dn.owns({"id": 5, "permissions": 8352}, mine))
        self.assertFalse(dn.owns({"id": 7}, {"mediaInfo": {"requests": [{"status": 3, "requestedBy": {"id": 7}}]}}))
        for me in (None, {}, {"id": True}, {"id": "7"}):
            self.assertFalse(dn.owns(me, mine), me)

    def test_requesters_and_strangers_may_not(self):
        for me in ({"id": 5, "permissions": 8352}, {"permissions": "2"}, {}, None, [], {"id": 1, "permissions": None}):
            self.assertFalse(dn.allowed(me), me)


def cmd(name, status, queued, started=None, **body):
    return {"name": name, "status": status, "queued": queued, "started": started, "ended": None, "body": body}


class Search(unittest.TestCase):
    def test_running_search_for_the_title(self):
        cmds = [cmd("MoviesSearch", "started", "2026-10-28T09:55:12Z", "2026-10-28T09:55:12Z", movieIds=[43])]
        self.assertEqual(dn.search_info("movie", 43, cmds, None), {"status": "started", "started": "2026-10-28T09:55:12Z", "ahead": 0, "last": None})

    def test_queued_search_counts_the_searches_before_it(self):
        cmds = [cmd("MoviesSearch", "started", "2026-10-28T09:55:12Z", "2026-10-28T09:55:12Z", movieIds=[53]), cmd("RssSync", "started", "2026-10-28T09:55:00Z"),
                cmd("MissingMoviesSearch", "queued", "2026-10-28T09:56:00Z"), cmd("MoviesSearch", "queued", "2026-10-28T09:57:00Z", movieIds=[43]),
                cmd("MoviesSearch", "queued", "2026-10-28T09:58:00Z", movieIds=[55]), cmd("MoviesSearch", "completed", "2026-10-28T09:50:00Z", movieIds=[51])]
        self.assertEqual(dn.search_info("movie", 43, cmds, "2026-10-27T10:00:00Z"), {"status": "queued", "started": None, "ahead": 2, "last": "2026-10-27T10:00:00Z"})

    def test_finished_searches_leave_the_last_search_time(self):
        cmds = [cmd("MoviesSearch", "completed", "2026-10-28T09:50:00Z", movieIds=[43])]
        self.assertEqual(dn.search_info("movie", 43, cmds, "2026-10-28T09:54:09Z"), {"status": None, "started": None, "ahead": 0, "last": "2026-10-28T09:54:09Z"})

    def test_series_search_by_series_id(self):
        cmds = [cmd("SeriesSearch", "started", "2026-10-28T09:55:12Z", "2026-10-28T09:55:13Z", seriesId=17), cmd("SeriesSearch", "started", "2026-10-28T09:55:12Z", seriesId=18)]
        self.assertEqual(dn.search_info("tv", 17, cmds, None)["status"], "started")

    def test_odd_command_list_is_no_search(self):
        self.assertEqual(dn.search_info("movie", 43, {"records": []}, None), {"status": None, "started": None, "ahead": 0, "last": None})


class FakeWorld:
    """Seerr, Radarr/Sonarr, qBittorrent and SABnzbd as the handler sees them; records every write."""

    def __init__(self, perms=2, title=None, queue=(), torrents=(), slots=(), media=None, kind="movie", commands=None, episodes=None):
        self.perms, self.kind, self.commands, self.episodes = perms, kind, commands, episodes
        self.title = title if title is not None else (movie() if kind == "movie" else series())
        self.queue, self.torrents, self.slots = list(queue), list(torrents), list(slots)
        self.media = media if media is not None else {"mediaInfo": {"externalServiceId": self.title["id"], "tvdbId": 999}}
        self.writes, self.seerr_auth = [], []

    def seerr(self, path, auth):
        self.seerr_auth.append(auth)
        if path == "/auth/me":
            if self.perms is None:
                raise dn.ApiError("seerr: 403")
            return {"id": 7, "permissions": self.perms}
        return self.media

    def arr(self, app):
        def call(path, method="GET", body=None):
            if method != "GET":
                self.writes.append((app, method, path, body))
                return {}
            if path == "/tag":
                return [{"id": 63, "label": "lang-en"}, {"id": TAG, "label": "download-now"}]
            if path.startswith("/queue"):
                return {"records": self.queue}
            if path == "/command" and self.commands is not None:
                return self.commands
            if path.startswith("/episode?seriesId=") and self.episodes is not None:
                return self.episodes
            if path in ("/movie/43", "/series/17"):
                return self.title
            if path.startswith("/movie?tmdbId=") or path.startswith("/series?tvdbId="):
                return [self.title]
            raise dn.ApiError(f"{app} {path}: 404")
        return call

    def qbit(self, path, data=None):
        if data is not None:
            self.writes.append(("qbit", path, data))
            return None
        return self.torrents

    def sab(self, params):
        if params.get("mode") == "queue" and "name" not in params:
            return {"queue": {"slots": self.slots}}
        self.writes.append(("sab", params))
        return {"status": True}

    def deps(self):
        return dn.Deps(seerr=self.seerr, arr=self.arr, qbit=self.qbit, sab=self.sab, origins={ORIGIN})


def post(world, body, headers=None):
    h = {"Content-Type": "application/json", "Origin": ORIGIN, "Cookie": "connect.sid=s%3Aabc"}
    h.update(headers or {})
    return dn.handle("POST", "/_download-now/api", h, json.dumps(body).encode(), world.deps())


def get(world, query="type=movie&tmdb=673", headers=None):
    h = {"Cookie": "connect.sid=s%3Aabc"}
    h.update(headers or {})
    return dn.handle("GET", "/_download-now/api?" + query, h, b"", world.deps())


class Handler(unittest.TestCase):
    def test_press_moves_tags_and_resumes(self):
        w = FakeWorld(queue=[rec("abc")], torrents=[tor("abc")])
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual(status, 200)
        self.assertEqual(out["state"], "started")
        self.assertEqual(out["released"], ["abc"])
        self.assertIn(("radarr", "PUT", "/movie/editor", {"movieIds": [43], "tags": [TAG], "applyTags": "add"}), w.writes)
        self.assertIn(("qbit", "/torrents/setCategory", {"hashes": "abc", "category": "radarr"}), w.writes)
        self.assertIn(("qbit", "/torrents/start", {"hashes": "abc"}), w.writes)
        self.assertEqual([x for x in w.writes if x[0] == "radarr" and x[2] == "/command"], [])

    def test_tag_goes_on_before_anything_moves(self):
        w = FakeWorld(queue=[rec("abc")], torrents=[tor("abc")])
        post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual(w.writes[0][:3], ("radarr", "PUT", "/movie/editor"))

    def test_press_on_nzb_changes_category_then_resumes(self):
        w = FakeWorld(queue=[rec("SABnzbd_nzo_1")], slots=[job("SABnzbd_nzo_1")])
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        sab = [x[1] for x in w.writes if x[0] == "sab"]
        self.assertEqual(sab, [{"mode": "change_cat", "value": "SABnzbd_nzo_1", "value2": "movies"}, {"mode": "queue", "name": "resume", "value": "SABnzbd_nzo_1"}])

    def test_press_without_a_grab_searches(self):
        dn.SEARCHED.clear()
        w = FakeWorld()
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual(out["state"], "searching")
        self.assertIn(("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [43]}), w.writes)

    def test_series_search_uses_series_search(self):
        dn.SEARCHED.clear()
        w = FakeWorld(kind="tv")
        status, out = post(w, {"type": "tv", "tmdbId": 1399})
        self.assertIn(("sonarr", "PUT", "/series/editor", {"seriesIds": [17], "tags": [TAG], "applyTags": "add"}), w.writes)
        self.assertIn(("sonarr", "POST", "/command", {"name": "SeriesSearch", "seriesId": 17}), w.writes)

    def test_already_downloading_writes_nothing(self):
        w = FakeWorld(queue=[rec("abc")], torrents=[tor("abc", cat="radarr", state="downloading")])
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual((status, out["state"], w.writes), (200, "downloading", []))

    def test_get_reports_the_search_and_the_download_now_tag(self):
        w = FakeWorld(title=movie(tags=(TAG,)) | {"lastSearchTime": "2026-10-28T09:54:09Z"}, commands=[cmd("MoviesSearch", "completed", "2026-10-28T09:50:00Z", movieIds=[43])])
        status, out = get(w)
        self.assertEqual((status, out["state"], out["now"]), (200, "searching", True))
        self.assertEqual(out["search"], {"status": None, "started": None, "ahead": 0, "last": "2026-10-28T09:54:09Z"})

    def test_series_last_search_is_the_latest_missing_episode_search(self):
        eps = [{"hasFile": True, "lastSearchTime": "2026-10-28T11:00:00Z"}, {"hasFile": False, "lastSearchTime": "2026-10-28T10:00:00Z"}, {"hasFile": False, "lastSearchTime": None}]
        status, out = get(FakeWorld(kind="tv", commands=[], episodes=eps), "type=tv&tmdb=1399")
        self.assertEqual((out["search"]["last"], out["now"]), ("2026-10-28T10:00:00Z", False))

    def test_command_list_not_readable_is_no_search_info_not_an_error(self):
        status, out = get(FakeWorld())
        self.assertEqual((status, out["search"]), (200, None))

    def test_press_answers_with_the_search_it_queued(self):
        dn.SEARCHED.clear()
        w = FakeWorld(commands=[])
        orig = w.arr
        def arr(app):
            call = orig(app)
            def wrapped(path, method="GET", body=None):
                if path == "/command" and method == "POST":
                    w.commands.append(cmd("MoviesSearch", "queued", "2026-10-28T10:12:00Z", movieIds=[43]))
                return call(path, method, body)
            return wrapped
        w.arr = arr
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual((out["state"], out["search"]["status"], out["now"]), ("searching", "queued", True))

    def test_get_is_a_dry_run(self):
        w = FakeWorld(queue=[rec("abc")], torrents=[tor("abc")])
        status, out = get(w)
        self.assertEqual((status, out["state"], out["released"]), (200, "started", ["abc"]))
        self.assertEqual(w.writes, [])

    def test_requester_is_refused_and_nothing_happens(self):
        w = FakeWorld(perms=8352, queue=[rec("abc")], torrents=[tor("abc")])
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673})[0], 403)
        self.assertEqual(get(w)[0], 403)
        self.assertEqual(w.writes, [])

    def test_requester_may_press_on_a_title_they_requested(self):
        w = FakeWorld(perms=8352, queue=[rec("abc")], torrents=[tor("abc")], media=own_media(7))
        self.assertEqual(get(w)[:1], (200,))
        self.assertEqual(w.writes, [])
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual((status, out["state"]), (200, "started"))
        self.assertIn(("qbit", "/torrents/start", {"hashes": "abc"}), w.writes)

    def test_requester_is_refused_on_someone_elses_title(self):
        w = FakeWorld(perms=8352, queue=[rec("abc")], torrents=[tor("abc")], media=own_media(5))
        self.assertEqual(get(w), (403, {"state": "error", "error": "forbidden"}))
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673})[0], 403)
        self.assertEqual(w.writes, [])

    def test_a_declined_own_request_does_not_count(self):
        w = FakeWorld(perms=8352, queue=[rec("abc")], torrents=[tor("abc")], media=own_media(7, status=3))
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673})[0], 403)
        self.assertEqual(w.writes, [])

    def test_one_own_request_among_others_is_enough(self):
        w = FakeWorld(perms=8352, kind="tv", media=own_media(5, 7, sid=17))
        self.assertEqual(get(w, "type=tv&tmdb=1399")[0], 200)

    def test_requester_is_refused_when_seerr_cannot_show_the_title(self):
        w = FakeWorld(perms=8352)
        seerr = w.seerr
        def unknown(path, auth):
            if path != "/auth/me":
                raise dn.ApiError("seerr: 404")
            return seerr(path, auth)
        w.seerr = unknown
        self.assertEqual(get(w)[0], 403)

    def test_odd_request_shapes_are_refused_not_a_crash(self):
        for media in ([], {"mediaInfo": []}, {"mediaInfo": {"requests": "x"}}, {"mediaInfo": {"requests": [None, {"requestedBy": 7}, {"requestedBy": {"id": "7"}}]}}):
            self.assertEqual(get(FakeWorld(perms=8352, media=media))[0], 403, media)

    def test_seerr_is_asked_for_the_title_once(self):
        w = FakeWorld(perms=8352, media=own_media(7))
        get(w)
        self.assertEqual(len(w.seerr_auth), 2)

    def test_no_session_is_refused(self):
        w = FakeWorld(perms=None)
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673})[0], 403)
        self.assertEqual(w.writes, [])

    def test_only_seerrs_auth_headers_reach_seerr(self):
        w = FakeWorld()
        get(w, headers={"Cookie": "connect.sid=s%3Aabc; other=1", "X-Api-Key": "k", "X-Api-User": "5", "Authorization": "Bearer x", "X-Forwarded-User": "admin"})
        self.assertEqual(w.seerr_auth[0], {"Cookie": "connect.sid=s%3Aabc", "X-Api-Key": "k", "X-Api-User": "5"})

    def test_post_from_another_origin_is_refused(self):
        w = FakeWorld(queue=[rec("abc")], torrents=[tor("abc")])
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(w.writes, [])

    def test_post_without_origin_or_json_is_refused(self):
        w = FakeWorld(queue=[rec("abc")], torrents=[tor("abc")])
        h = {"Content-Type": "application/json", "Cookie": "connect.sid=s%3Aabc"}
        self.assertEqual(dn.handle("POST", "/_download-now/api", h, b'{"type":"movie","tmdbId":673}', w.deps())[0], 403)
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(w.writes, [])

    def test_bad_input_is_400(self):
        w = FakeWorld()
        for body in ({"type": "person", "tmdbId": 1}, {"type": "movie", "tmdbId": "1; rm"}, {"type": "movie", "tmdbId": -1}, {"type": "movie"}):
            self.assertEqual(post(w, body)[0], 400, body)
        self.assertEqual(dn.handle("POST", "/_download-now/api", {"Content-Type": "application/json", "Origin": ORIGIN}, b"{", w.deps())[0], 400)
        self.assertEqual(get(w, "type=movie&tmdb=abc")[0], 400)

    def test_title_not_in_radarr_is_not_found(self):
        w = FakeWorld(media={"mediaInfo": None})
        w.arr = lambda app: (lambda path, method="GET", body=None: [] if path.startswith("/movie?") else {"records": []} if path.startswith("/queue") else [{"id": TAG, "label": "download-now"}])
        status, out = get(w)
        self.assertEqual((status, out["state"]), (200, "not-found"))
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673})[:1], (200,))

    def test_title_seerr_does_not_know_is_not_found_not_an_error(self):
        w = FakeWorld()
        seerr = w.seerr
        def unknown(path, auth):
            if path != "/auth/me":
                raise dn.ApiError("seerr: 404")
            return seerr(path, auth)
        w.seerr = unknown
        w.arr = lambda app: (lambda path, method="GET", body=None: [] if path.startswith("/movie?") else {"records": []} if path.startswith("/queue") else [{"id": TAG, "label": "download-now"}])
        self.assertEqual(get(w, "type=movie&tmdb=999999999"), (200, {"state": "not-found", "app": "radarr", "released": [], "tagged": False, "moved": 0, "now": False, "search": None}))

    def test_falls_back_to_lookup_without_seerr_service_id(self):
        w = FakeWorld(media={"mediaInfo": {"externalServiceId": None}}, queue=[rec("abc")], torrents=[tor("abc")])
        self.assertEqual(get(w)[1]["state"], "started")

    def test_backend_error_is_502_without_details_of_the_url(self):
        w = FakeWorld()
        def broken(path, data=None):
            raise dn.ApiError("http://gluetun:8081/api?apikey=SECRET: refused")
        w.qbit = broken
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual((status, out["state"]), (502, "error"))
        self.assertNotIn("SECRET", json.dumps(out))

    def test_seerr_link_to_another_title_falls_back_to_the_tmdb_lookup(self):
        w = FakeWorld(media={"mediaInfo": {"externalServiceId": 99}}, queue=[rec("abc")], torrents=[tor("abc")])
        arr = w.arr
        def other(app):
            call = arr(app)
            return lambda path, method="GET", body=None: {**movie(99), "tmdbId": 1} if path == "/movie/99" else call(path, method, body)
        w.arr = other
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual(out["state"], "started")
        self.assertIn(("radarr", "PUT", "/movie/editor", {"movieIds": [43], "tags": [TAG], "applyTags": "add"}), w.writes)

    def test_sab_refusal_is_an_error_not_started(self):
        w = FakeWorld(queue=[rec("SABnzbd_nzo_1")], slots=[job("SABnzbd_nzo_1")])
        sab = w.sab
        w.sab = lambda params: {"status": False, "error": "nope"} if params.get("mode") == "change_cat" else sab(params)
        self.assertEqual(post(w, {"type": "movie", "tmdbId": 673})[1]["state"], "error")

    def test_second_search_within_the_cooldown_is_not_sent_again(self):
        dn.SEARCHED.clear()
        w = FakeWorld()
        post(w, {"type": "movie", "tmdbId": 673})
        status, out = post(w, {"type": "movie", "tmdbId": 673})
        self.assertEqual(out["state"], "searching")
        self.assertEqual(len([x for x in w.writes if x[2:3] == ("/command",)]), 1)

    def test_odd_upstream_shapes_are_502_not_a_crash(self):
        w = FakeWorld()
        arr = w.arr
        w.arr = lambda app: (lambda path, method="GET", body=None: None if path.startswith("/queue") else arr(app)(path, method, body))
        self.assertEqual(get(w)[0], 502)

    def test_health_needs_no_login(self):
        self.assertEqual(dn.handle("GET", "/_download-now/health", {}, b"", FakeWorld(perms=None).deps()), (200, {"ok": True}))

    def test_unknown_path_is_404(self):
        self.assertEqual(dn.handle("GET", "/_download-now/other", {}, b"", FakeWorld().deps())[0], 404)


class Redact(unittest.TestCase):
    def test_query_strings_are_cut(self):
        self.assertEqual(dn.redact("http://gluetun:8081/api?apikey=SECRET&mode=queue: timed out"), "http://gluetun:8081/api: timed out")


if __name__ == "__main__":
    unittest.main()
