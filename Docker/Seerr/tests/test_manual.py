#!/usr/bin/env python3
"""download-now/manual.py: the "Manual interaction" page backend — which problems are found, how they are tracked, who may do what, against fake clients.
Usage: python3 tests/test_manual.py"""
import datetime
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "download-now"))
spec = importlib.util.spec_from_file_location("download_now", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
sys.modules["download_now"] = dn
spec.loader.exec_module(dn)
import manual  # noqa: E402

ORIGIN = "https://requests.example.com"
T = datetime.datetime(2026, 11, 15, 3, 0, tzinfo=datetime.timezone.utc).timestamp()
DAY = 86400
USER, ADMIN = {"id": 7, "displayName": "jane", "jellyfinUsername": "jane", "permissions": 8352}, {"id": 1, "displayName": "Jane Doe", "username": "janedoe", "permissions": 2}
OTHER = {"id": 5, "displayName": "joe", "jellyfinUsername": "joe", "permissions": 8352}
R_PROFILES = [{"id": 7, "name": "4K HDR - UHD Bluray + WEB (1080p fallback)"}, {"id": 10, "name": "4K HDR - UHD Bluray + WEB (1080p fallback) - English original"},
              {"id": 11, "name": "4K preferred · older versions allowed"}, {"id": 12, "name": "4K preferred · older versions allowed - English original"},
              {"id": 13, "name": "Swiss German (Mundart) only - any quality"}, {"id": 14, "name": "Anime - 4K HDR (1080p fallback)"}, {"id": 15, "name": "Anime - 4K HDR · older versions allowed"}]
S_PROFILES = [{"id": 7, "name": "4K HDR - WEB-2160p (1080p fallback)"}, {"id": 9, "name": "Anime - Bluray + WEB 1080p"}, {"id": 11, "name": "4K preferred · older versions allowed"},
              {"id": 15, "name": "Anime - Bluray + WEB 1080p · older versions allowed"}]
BY_ID = "Found matching series via grab history, but release was matched to series by ID. Automatic import is not possible. See the FAQ for details."


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def request(rid, kind, tmdb, uid=7, name="jane", status=2, ext=None, tvdb=None, created=T - 10 * DAY, seasons=(), media_id=None, profile=None):
    return {"id": rid, "type": kind, "status": status, "createdAt": iso(created), "profileId": profile, "rootFolder": None, "is4k": False,
            "seasons": [{"seasonNumber": n} for n in seasons], "requestedBy": {"id": uid, "displayName": name, "jellyfinUsername": name},
            "media": {"id": media_id or rid + 100, "tmdbId": tmdb, "tvdbId": tvdb, "externalServiceId": ext, "status": 3}}


def state(**kw):
    out = {"canWait": [], "unreleased": {}, "noRelease": {}, "searching": {}, "fresh": {}, "pending": [], "queue": {}, "imported": [], "soFar": [], "removed": []}
    out.update(kw)
    return out


def qrec(qid, kind="tv", dl="DL1", tds="importBlocked", status="completed", tid=54, tmdb=4040, year=2026, title="Company.Retreat.2026.S01.2160p.WEB-DL-GRP", msgs=(BY_ID,), ep=None):
    r = {"id": qid, "downloadId": dl, "trackedDownloadState": tds, "trackedDownloadStatus": "warning", "status": status, "title": title,
         "statusMessages": [{"title": title, "messages": list(msgs)}] if msgs else []}
    if kind == "tv":
        r.update(seriesId=tid, series={"id": tid, "title": "Company Retreat", "year": year, "tmdbId": tmdb, "tvdbId": 9090}, episodeId=ep)
    else:
        r.update(movieId=tid, movie={"id": tid, "title": "Some Film", "year": year, "tmdbId": tmdb})
    return r


class World:
    """Seerr (session + admin key) and Radarr/Sonarr as the manual backend sees them; records every write."""

    def __init__(self, me=USER, rs=None, requests=(), rqueue=(), squeue=(), titles=None, rs_age=0):
        self.me, self.requests, self.queues = me, list(requests), {"radarr": list(rqueue), "sonarr": list(squeue)}
        self.titles = titles or {}
        self.writes, self.admin_calls, self.gets = [], [], []
        self.tmp = tempfile.TemporaryDirectory()
        self.rs_file = pathlib.Path(self.tmp.name, "request-state.json")
        self.rs_file.write_text(json.dumps(rs if rs is not None else state()))
        os.utime(self.rs_file, (T - rs_age, T - rs_age))
        self.store = pathlib.Path(self.tmp.name, "state", "manual.json")
        self.lookups = {"radarr": [], "sonarr": []}
        self.releases, self.proposals, self.episodes = [], [], []
        self.seerr_media = {}
        self.request_calls, self.slow, self.odd_queue, self.token = 0, 0, False, None

    def seerr(self, path, auth):
        if path == "/auth/me":
            if self.me is None:
                raise dn.ApiError("seerr: 403")
            return self.me
        raise dn.ApiError("seerr: 404")

    def admin(self, path, method="GET", body=None):
        if method != "GET":
            self.admin_calls.append((method, path, body))
            return {}
        if path.startswith("/request?"):
            self.request_calls += 1
            time.sleep(self.slow)
            return {"results": self.requests}
        if path == "/settings/radarr":
            return [{"id": 0, "isDefault": True, "is4k": False, "activeProfileId": 7, "activeDirectory": "/data/media/movies"}]
        if path == "/settings/sonarr":
            return [{"id": 0, "isDefault": True, "is4k": False, "activeProfileId": 7, "activeDirectory": "/data/media/tv", "activeAnimeProfileId": 9, "activeAnimeDirectory": "/data/media/tv"}]
        if path in self.seerr_media:
            return self.seerr_media[path]
        if path.startswith("/movie/"):
            return {"id": int(path.split("/")[2]), "title": "Seerr Film", "releaseDate": "2004-05-01"}
        if path.startswith("/tv/"):
            return {"id": int(path.split("/")[2]), "name": "Seerr Series", "firstAirDate": "2019-01-01"}
        raise dn.ApiError(f"seerr admin {path}: 404")

    def arr(self, app):
        def call(path, method="GET", body=None, timeout=None):
            if method != "GET":
                self.writes.append((app, method, path, body))
                return {"id": 900} if path in ("/movie", "/series") else {}
            self.gets.append((app, path))
            if path.startswith("/queue"):
                return None if self.odd_queue else {"records": self.queues[app]}
            if path == "/qualityprofile":
                return R_PROFILES if app == "radarr" else S_PROFILES
            if path == "/rootfolder":
                return [{"path": "/data/media/movies" if app == "radarr" else "/data/media/tv"}]
            if "/lookup" in path:
                return self.lookups[app]
            if path.startswith("/release?"):
                return self.releases
            if path.startswith("/manualimport?"):
                return self.proposals
            if path.startswith("/episode?seriesId="):
                return self.episodes
            if path in self.titles:
                return self.titles[path]
            if path.startswith("/movie?tmdbId=") or path.startswith("/series?tvdbId="):
                return []
            raise dn.ApiError(f"{app} {path}: 404")
        return call

    def deps(self):
        return dn.Deps(seerr=self.seerr, arr=self.arr, qbit=None, sab=None, origins={ORIGIN}, seerr_admin=self.admin,
                       manual_file=str(self.store), request_state_file=str(self.rs_file))


def reset():
    manual.NOW = lambda: T
    manual.CACHE.clear()
    manual.OFFERS.clear()
    manual.THROTTLE.clear()


def get(w, path, me=None):
    status, out = dn.handle("GET", "/_download-now/manual/" + path, {"Cookie": "connect.sid=s%3Aabc"}, b"", w.deps())
    if isinstance(out, dict) and out.get("token"):
        w.token = out["token"]
    return status, out


def post(w, body, headers=None):
    h = {"Content-Type": "application/json", "Origin": ORIGIN, "Cookie": "connect.sid=s%3Aabc"}
    h.update(headers or {})
    if w.token and "token" not in body:
        body = dict(body, token=w.token)
    return dn.handle("POST", "/_download-now/manual/action", h, json.dumps(body).encode(), w.deps())


def problems(w):
    status, out = get(w, "problems")
    assert status == 200, (status, out)
    return out


def one(w, kind):
    found = [p for p in problems(w)["open"] if p["type"] == kind]
    assert len(found) == 1, found
    return found[0]


class Scan(unittest.TestCase):
    def setUp(self):
        reset()

    def test_title_removed_from_radarr_is_a_problem_with_its_requesters(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21), request(12, "movie", 10248, uid=5, name="joe", ext=21)])
        p = one(w, "removed")
        self.assertEqual((p["key"], p["title"], p["year"], p["requesters"]), ("movie/10248", "Seerr Film", 2004, ["jane", "joe"]))
        self.assertEqual(p["firstSeen"], iso(T).replace(".000Z", "Z"))

    def test_seerr_could_not_hand_it_over_is_unmatched(self):
        w = World(rs=state(queue={"tv/4040": {"state": "problem", "message": "", "code": "seerr"}}), requests=[request(13, "tv", 4040, status=4, tvdb=9090, seasons=(1,))])
        p = one(w, "unmatched")
        self.assertEqual((p["key"], p["title"], p["year"]), ("tv/4040", "Seerr Series", 2019))

    def test_nothing_found_for_days_is_a_problem_but_not_a_young_request(self):
        w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z", "movie/77": "2026-11-14T04:17:00Z"}),
                  requests=[request(11, "movie", 10248, ext=21), request(14, "movie", 77, ext=22, created=T - DAY)],
                  titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 13}})
        p = one(w, "no-release")
        self.assertEqual((p["key"], p["details"]["lastSearch"], p["details"]["profile"]), ("movie/10248", "2026-11-14T04:17:00Z", "Swiss German (Mundart) only - any quality"))
        self.assertIsNone(p["details"]["older"])

    def test_unreleased_title_is_never_a_problem(self):
        w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}, unreleased={"movie/10248": "2027-01-01"}), requests=[request(11, "movie", 10248, ext=21)])
        self.assertEqual(problems(w)["open"], [])

    def test_old_title_on_the_default_profile_suggests_the_older_versions_twin(self):
        for pid, older in ((7, "4K preferred · older versions allowed"), (10, "4K preferred · older versions allowed - English original"),
                           (14, "Anime - 4K HDR · older versions allowed"), (11, None), (13, None)):
            reset()
            w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                      titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": pid}})
            d = one(w, "no-release")["details"]
            self.assertEqual((d["older"], d["old"]), (older, True), pid)

    def test_recent_title_is_not_called_old_but_may_still_switch(self):
        w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                  titles={"/movie/21": {"id": 21, "title": "New Film", "year": 2024, "tmdbId": 10248, "qualityProfileId": 7}})
        d = one(w, "no-release")["details"]
        self.assertEqual((d["older"], d["old"]), ("4K preferred · older versions allowed", False))

    def test_blocked_import_matched_by_id_needs_import_one_problem_per_download(self):
        w = World(squeue=[qrec(1, ep=501), qrec(2, ep=502), qrec(3, dl="OTHER", tds="downloading", status="downloading", msgs=())])
        p = one(w, "import")
        self.assertEqual((p["key"], p["title"], p["year"], p["requesters"]), ("tv/4040", "Company Retreat", 2026, []))
        self.assertEqual(p["details"]["release"], "Company.Retreat.2026.S01.2160p.WEB-DL-GRP")
        self.assertEqual(p["details"]["episodes"], 2)

    def test_blocked_import_of_another_year_is_the_wrong_film(self):
        w = World(rqueue=[qrec(1, kind="movie", title="Some.Film.1987.1080p.BluRay-GRP", year=2024, tid=43, tmdb=673,
                               msgs=("Found matching movie via grab history, but release was matched to movie by ID.",))])
        p = one(w, "wrong-film")
        self.assertEqual((p["details"]["releaseYear"], p["details"]["year"]), (1987, 2024))

    def test_series_year_in_the_release_is_no_wrong_film(self):
        w = World(squeue=[qrec(1, title="Some.Show.2005.S10E01.1080p-GRP", year=2019, ep=501)])
        self.assertEqual(one(w, "import")["type"], "import")

    def test_requesters_never_show_an_email(self):
        odd = request(11, "movie", 10248, ext=21)
        odd["requestedBy"] = {"id": 5, "displayName": "joe@example.com", "email": "joe@example.com", "username": None, "plexUsername": None, "jellyfinUsername": None}
        named = request(12, "movie", 10248, uid=6, ext=21)
        named["requestedBy"] = {"id": 6, "displayName": "x@example.com", "email": "x@example.com", "username": "", "plexUsername": "plexjoe"}
        w = World(rs=state(removed=["movie/10248"]), requests=[odd, named])
        p = one(w, "removed")
        self.assertEqual(p["requesters"], ["user 5", "plexjoe"])
        self.assertNotIn("@", w.store.read_text())

    def test_failed_download_is_a_problem(self):
        w = World(rqueue=[qrec(1, kind="movie", tds="failed", status="failed", tid=43, tmdb=673, msgs=("Download failed",))])
        self.assertEqual(one(w, "failed")["details"]["message"], "Download failed")

    def test_paused_and_running_downloads_are_no_problem(self):
        w = World(squeue=[qrec(1, tds="downloading", status="paused", msgs=()), qrec(2, tds="importPending", status="completed", msgs=())])
        self.assertEqual(problems(w)["open"], [])

    def test_messages_lose_their_paths(self):
        w = World(rqueue=[qrec(1, kind="movie", tds="importBlocked", tid=43, tmdb=673, msgs=("No files found in /data/torrents/radarr/Some.Film",))])
        self.assertNotIn("/data", one(w, "import")["details"]["message"])

    def test_stale_request_state_is_ignored(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)], rs_age=3600)
        self.assertEqual(problems(w)["open"], [])


class Tracking(unittest.TestCase):
    def setUp(self):
        reset()

    def removed_world(self):
        return World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)])

    def test_record_keeps_first_seen_and_moves_last_seen(self):
        w = self.removed_world()
        first = one(w, "removed")
        manual.NOW = lambda: T + 3600
        os.utime(w.rs_file, (T + 3600, T + 3600))
        manual.CACHE.clear()
        again = one(w, "removed")
        self.assertEqual((again["id"], again["firstSeen"]), (first["id"], first["firstSeen"]))
        self.assertNotEqual(again["lastSeen"], first["lastSeen"])

    def test_gone_condition_moves_the_problem_to_done(self):
        w = self.removed_world()
        pid = one(w, "removed")["id"]
        w.rs_file.write_text(json.dumps(state()))
        os.utime(w.rs_file, (T, T))
        manual.CACHE.clear()
        out = problems(w)
        self.assertEqual(out["open"], [])
        self.assertEqual([(d["id"], d["resolved"]["how"]) for d in out["done"]], [(pid, "gone")])

    def test_incomplete_scan_resolves_nothing(self):
        w = self.removed_world()
        one(w, "removed")
        w.rs_file.write_text(json.dumps(state()))
        os.utime(w.rs_file, (T - 3600, T - 3600))
        manual.CACHE.clear()
        self.assertEqual(len(problems(w)["open"]), 1)

    def test_old_done_records_are_dropped(self):
        w = self.removed_world()
        one(w, "removed")
        w.rs_file.write_text(json.dumps(state()))
        os.utime(w.rs_file, (T, T))
        manual.CACHE.clear()
        problems(w)
        manual.NOW = lambda: T + 181 * DAY
        os.utime(w.rs_file, (T + 181 * DAY, T + 181 * DAY))
        manual.CACHE.clear()
        self.assertEqual(problems(w)["done"], [])

    def test_store_is_small_json_without_paths(self):
        w = World(rqueue=[qrec(1, kind="movie", tid=43, tmdb=673, msgs=("No files found in /data/torrents/radarr/Some.Film",))])
        one(w, "import")
        text = w.store.read_text()
        self.assertNotIn("/data", text)
        self.assertIn("problems", json.loads(text))

    def test_problem_back_after_done_is_open_again(self):
        w = self.removed_world()
        one(w, "removed")
        w.rs_file.write_text(json.dumps(state()))
        os.utime(w.rs_file, (T, T))
        manual.CACHE.clear()
        problems(w)
        w.rs_file.write_text(json.dumps(state(removed=["movie/10248"])))
        os.utime(w.rs_file, (T, T))
        manual.CACHE.clear()
        p = one(w, "removed")
        self.assertIsNone(p["resolved"])
        self.assertEqual(p["actions"][-1]["action"], "reopened")

    def test_empty_arr_answer_resolves_nothing(self):
        w = World(rqueue=[qrec(1, kind="movie", tid=43, tmdb=673, msgs=("No files found",))])
        one(w, "import")
        w.odd_queue = True
        manual.CACHE.clear()
        self.assertEqual(len(problems(w)["open"]), 1)

    def test_full_seerr_page_resolves_nothing(self):
        w = self.removed_world()
        one(w, "removed")
        w.rs_file.write_text(json.dumps(state()))
        os.utime(w.rs_file, (T, T))
        w.requests = [request(1000 + n, "movie", 5000 + n) for n in range(1000)]
        manual.CACHE.clear()
        self.assertEqual(len(problems(w)["open"]), 1)

    def test_unreadable_store_is_never_overwritten(self):
        w = self.removed_world()
        w.store.parent.mkdir(parents=True)
        w.store.write_text('{"problems": {"old": ')
        problems(w)
        self.assertEqual(w.store.read_text(), '{"problems": {"old": ')

    def test_save_keeps_a_backup_of_the_last_list(self):
        w = self.removed_world()
        one(w, "removed")
        before = w.store.read_text()
        manual.CACHE.clear()
        problems(w)
        self.assertEqual(w.store.with_name("manual.json.bak").read_text(), before)

    def test_concurrent_refreshes_share_one_scan(self):
        w = self.removed_world()
        w.slow = 0.2
        deps = w.deps()
        threads = [threading.Thread(target=manual.refresh, args=(dn, deps, True)) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(w.request_calls, 1)

    def test_count_is_the_number_of_open_problems(self):
        w = self.removed_world()
        self.assertEqual(get(w, "count"), (200, {"open": 1}))


class Gate(unittest.TestCase):
    def setUp(self):
        reset()

    def test_no_session_is_refused(self):
        w = World(me=None)
        self.assertEqual(get(w, "problems")[0], 403)
        self.assertEqual(dn.handle("GET", "/_download-now/manual/problems", {}, b"", w.deps())[0], 403)

    def test_post_needs_origin_and_json(self):
        w = World()
        self.assertEqual(post(w, {}, {"Origin": "https://evil.example.com"})[0], 403)
        self.assertEqual(post(w, {}, {"Content-Type": "text/plain"})[0], 415)

    def test_every_user_sees_every_problem_admins_know_they_are(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, uid=5, name="joe", ext=21)])
        out = problems(w)
        self.assertEqual((len(out["open"]), out["admin"]), (1, False))
        w.me = ADMIN
        self.assertTrue(problems(w)["admin"])

    def test_history_export_is_for_admins(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)])
        problems(w)
        self.assertEqual(get(w, "history")[0], 403)
        w.me = ADMIN
        status, out = get(w, "history")
        self.assertEqual((status, len(out["problems"])), (200, 1))

    def test_unknown_problem_or_bad_id_is_refused(self):
        w = World()
        self.assertEqual(post(w, {"id": "0123456789ab", "action": "readd"})[0], 404)
        self.assertEqual(post(w, {"id": "../etc", "action": "readd"})[0], 400)
        self.assertEqual(get(w, "lookup?id=zz")[0], 400)

    def test_action_must_fit_the_problem(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)])
        pid = one(w, "removed")["id"]
        self.assertEqual(post(w, {"id": pid, "action": "grab", "release": 0})[0], 400)
        self.assertEqual(post(w, {"id": pid, "action": "rm -rf"})[0], 400)
        self.assertEqual(w.writes + w.admin_calls, [])

    def test_refused_actions_never_force_a_scan(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)])
        pid = one(w, "removed")["id"]
        manual.NOW = lambda: T + 10
        os.utime(w.rs_file, (T + 10, T + 10))
        calls = w.request_calls
        for body in ({"id": pid, "action": "grab"}, {"id": pid, "action": "delete", "confirm": "delete"}):
            self.assertIn(post(w, body)[0], (400, 403))
        self.assertEqual(w.request_calls, calls)
        manual.THROTTLE[("act", pid)] = T + 5
        self.assertEqual(post(w, {"id": pid, "action": "readd"})[0], 429)
        self.assertEqual(w.request_calls, calls)

    def test_too_many_parallel_calls_are_turned_away(self):
        w = World()
        taken = 0
        while manual.SLOTS.acquire(blocking=False):
            taken += 1
        try:
            self.assertEqual(get(w, "problems")[0], 429)
        finally:
            for _ in range(taken):
                manual.SLOTS.release()
        self.assertGreater(taken, 0)
        self.assertEqual(get(w, "problems")[0], 200)

    def test_unknown_path_is_404(self):
        self.assertEqual(get(World(), "nothing")[0], 404)


class Unmatched(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World(rs=state(queue={"tv/4040": {"state": "problem", "message": "", "code": "seerr"}}),
                       requests=[request(13, "tv", 4040, status=4, tvdb=9090, seasons=(1, 2))])
        self.w.lookups["sonarr"] = [{"title": "Other Show", "year": 2001, "tvdbId": 1111, "overview": "x" * 500, "seasons": [{"seasonNumber": 1}]},
                                    {"title": "Seerr Series", "year": 2019, "tvdbId": 9090, "seasons": [{"seasonNumber": 0}, {"seasonNumber": 1}, {"seasonNumber": 2}, {"seasonNumber": 3}]},
                                    {"title": "Seerr Series", "year": 1990, "tvdbId": 2222, "id": 77, "seasons": []}]
        self.pid = one(self.w, "unmatched")["id"]

    def test_lookup_marks_the_most_probable_match(self):
        status, out = get(self.w, f"lookup?id={self.pid}&term=Seerr%20Series")
        self.assertEqual(status, 200)
        self.assertEqual([(r["title"], r["year"], r["probable"]) for r in out["results"]], [("Other Show", 2001, False), ("Seerr Series", 2019, True), ("Seerr Series", 1990, False)])
        self.assertEqual(out["results"][2]["inLibrary"], True)
        self.assertLessEqual(len(out["results"][0]["overview"]), 200)
        self.assertIn(("sonarr", "/series/lookup?term=Seerr+Series"), self.w.gets)

    def test_lookups_are_rate_limited_per_user(self):
        self.assertEqual(get(self.w, f"lookup?id={self.pid}")[0], 200)
        self.assertEqual(get(self.w, f"lookup?id={self.pid}")[0], 429)

    def test_link_needs_a_pick_from_the_lookup(self):
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 1})[0], 400)
        get(self.w, f"lookup?id={self.pid}")
        manual.THROTTLE.pop(("act", self.pid), None)
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 9})[0], 400)
        self.assertEqual(self.w.writes, [])

    def test_link_adds_the_series_with_the_requested_seasons_and_retries_seerr(self):
        get(self.w, f"lookup?id={self.pid}")
        status, out = post(self.w, {"id": self.pid, "action": "link", "pick": 1})
        self.assertEqual(status, 200, out)
        (app, method, path, body), = self.w.writes
        self.assertEqual((app, method, path, body["tvdbId"], body["qualityProfileId"], body["rootFolderPath"], body["monitored"]),
                         ("sonarr", "POST", "/series", 9090, 7, "/data/media/tv", True))
        self.assertEqual([(s["seasonNumber"], s["monitored"]) for s in body["seasons"]], [(0, False), (1, True), (2, True), (3, False)])
        self.assertTrue(body["addOptions"]["searchForMissingEpisodes"])
        self.assertEqual(self.w.admin_calls, [("POST", "/request/13/retry", None)])
        self.assertEqual(out["problem"]["resolved"]["how"], "link")
        self.assertEqual((out["problem"]["actions"][-1]["user"], out["problem"]["actions"][-1]["action"]), ("jane", "link"))

    def test_a_series_already_in_sonarr_is_not_added_again(self):
        self.w.me = ADMIN
        get(self.w, f"lookup?id={self.pid}")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 2})[0], 200)
        self.assertEqual(self.w.writes, [])

    def test_a_requester_cannot_link_another_title(self):
        get(self.w, f"lookup?id={self.pid}&term=Other")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 0})[0], 403)
        self.assertEqual(self.w.writes, [])

    def test_an_admin_may_link_another_title(self):
        self.w.me = ADMIN
        get(self.w, f"lookup?id={self.pid}&term=Other")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 0})[0], 200)
        self.assertEqual(self.w.writes[0][3]["tvdbId"], 1111)

    def test_link_needs_the_token_of_the_callers_own_list(self):
        mine = get(self.w, f"lookup?id={self.pid}")[1]["token"]
        self.w.me = OTHER
        theirs = get(self.w, f"lookup?id={self.pid}&term=Other")[1]["token"]
        self.assertNotEqual(mine, theirs)
        self.w.me = USER
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 1, "token": theirs})[0], 400)
        manual.THROTTLE.pop(("act", self.pid), None)
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 1, "token": mine})[0], 200)
        self.assertEqual(self.w.writes[0][3]["tvdbId"], 9090)

    def test_actions_are_rate_limited_per_problem(self):
        get(self.w, f"lookup?id={self.pid}")
        post(self.w, {"id": self.pid, "action": "link", "pick": 1})
        self.assertEqual(post(self.w, {"id": self.pid, "action": "link", "pick": 1})[0], 429)


EPISODES = [{"id": 501, "seasonNumber": 1, "episodeNumber": 1, "title": "Pilot"}, {"id": 502, "seasonNumber": 1, "episodeNumber": 2, "title": "Two"}]


class Import(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World(squeue=[qrec(1, ep=501), qrec(2, ep=502)])
        self.w.episodes = EPISODES
        q = {"quality": {"name": "WEBDL-2160p"}, "revision": {"version": 1}}
        self.w.proposals = [
            {"path": "/data/torrents/sonarr/CR/Company.Retreat.S01E01.mkv", "relativePath": "Company.Retreat.S01E01.mkv", "size": 3_000_000_000, "quality": q, "languages": [{"id": 1, "name": "English"}],
             "releaseGroup": "GRP", "episodes": [], "rejections": [{"reason": "matched to series by ID"}], "downloadId": "DL1", "indexerFlags": 0, "releaseType": "singleEpisode"},
            {"path": "/data/torrents/sonarr/CR/Company.Retreat.1x02.mkv", "relativePath": "Company.Retreat.1x02.mkv", "size": 2_000_000_000, "quality": q, "languages": [],
             "releaseGroup": "GRP", "episodes": [{"id": 502, "seasonNumber": 1, "episodeNumber": 2}], "rejections": [], "downloadId": "DL1", "indexerFlags": 0, "releaseType": "singleEpisode"}]
        self.pid = one(self.w, "import")["id"]

    def test_files_show_names_and_the_probable_episode_never_paths(self):
        status, out = get(self.w, f"files?id={self.pid}")
        self.assertEqual(status, 200)
        self.assertEqual([(f["n"], f["name"], f["episodeIds"], f["probable"]) for f in out["files"]],
                         [(0, "Company.Retreat.S01E01.mkv", [501], True), (1, "Company.Retreat.1x02.mkv", [502], True)])
        self.assertEqual([e["label"] for e in out["episodes"]], ["S01E01", "S01E02"])
        self.assertNotIn("/data", json.dumps(out))
        self.assertIn(("sonarr", "/manualimport?downloadId=DL1&seriesId=54&filterExistingFiles=false"), self.w.gets)

    def test_import_sends_the_server_side_paths_with_the_picked_episodes(self):
        get(self.w, f"files?id={self.pid}")
        status, out = post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 0, "episodeIds": [501]}, {"n": 1, "episodeIds": [502]}]})
        self.assertEqual(status, 200, out)
        (app, method, path, body), = self.w.writes
        self.assertEqual((app, method, path, body["name"], body["importMode"]), ("sonarr", "POST", "/command", "ManualImport", "auto"))
        self.assertEqual([(f["path"], f["seriesId"], f["episodeIds"], f["downloadId"]) for f in body["files"]],
                         [("/data/torrents/sonarr/CR/Company.Retreat.S01E01.mkv", 54, [501], "DL1"), ("/data/torrents/sonarr/CR/Company.Retreat.1x02.mkv", 54, [502], "DL1")])

    def test_import_refuses_episodes_of_another_series_and_unknown_files(self):
        get(self.w, f"files?id={self.pid}")
        for files in ([{"n": 0, "episodeIds": [999]}], [{"n": 5, "episodeIds": [501]}], [], [{"n": 0, "episodeIds": []}], "x", [{"n": 0, "episodeIds": [501]}, {"n": 0, "episodeIds": [502]}]):
            manual.THROTTLE.pop(("act", self.pid), None)
            self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": files})[0], 400, files)
        self.assertEqual(self.w.writes, [])

    def test_file_lists_are_rate_limited(self):
        self.assertEqual(get(self.w, f"files?id={self.pid}")[0], 200)
        self.assertEqual(get(self.w, f"files?id={self.pid}")[0], 429)

    def test_a_file_the_arr_rejected_needs_an_admin(self):
        self.w.proposals[1]["rejections"] = [{"reason": "Not an upgrade for existing episode file(s)"}]
        get(self.w, f"files?id={self.pid}")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 1, "episodeIds": [502]}]})[0], 403)
        self.assertEqual(self.w.writes, [])
        self.w.me = ADMIN
        manual.THROTTLE.clear()
        get(self.w, f"files?id={self.pid}")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 1, "episodeIds": [502]}]})[0], 200)

    def test_an_unmatched_file_may_be_mapped_by_anyone(self):
        self.w.proposals[0]["rejections"] = [{"reason": "Unable to determine if file is a sample"}, {"reason": "Episode not found"}]
        self.w.proposals[1]["rejections"] = [{"reason": "Unable to parse file"}]
        get(self.w, f"files?id={self.pid}")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 1, "episodeIds": [502]}]})[0], 200)

    def test_an_episode_with_a_file_needs_an_admin(self):
        self.w.episodes = [dict(EPISODES[0], hasFile=True), EPISODES[1]]
        get(self.w, f"files?id={self.pid}")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 0, "episodeIds": [501]}]})[0], 403)
        self.assertEqual(self.w.writes, [])

    def test_import_without_the_file_list_first_is_refused(self):
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 0, "episodeIds": [501]}]})[0], 400)

    def test_movie_import_uses_the_problems_movie(self):
        reset()
        w = World(rqueue=[qrec(1, kind="movie", tid=43, tmdb=673, year=2024, title="Some.Film.2024.1080p-GRP", msgs=("matched to movie by ID",))])
        w.proposals = [{"path": "/data/torrents/radarr/a/small.mkv", "relativePath": "a/small.mkv", "size": 10, "quality": {}, "languages": [], "movie": None, "rejections": []},
                       {"path": "/data/torrents/radarr/a/film.mkv", "relativePath": "a/film.mkv", "size": 9_000_000_000, "quality": {}, "languages": [], "movie": None, "rejections": []}]
        pid = one(w, "import")["id"]
        out = get(w, f"files?id={pid}")[1]
        self.assertEqual([(f["name"], f["probable"]) for f in out["files"]], [("small.mkv", False), ("film.mkv", True)])
        self.assertEqual(post(w, {"id": pid, "action": "import", "files": [{"n": 1}]})[0], 200)
        self.assertEqual([(f["path"], f["movieId"]) for f in w.writes[0][3]["files"]], [("/data/torrents/radarr/a/film.mkv", 43)])


class Block(unittest.TestCase):
    def setUp(self):
        reset()

    def test_wrong_film_is_removed_blocklisted_and_searched_again(self):
        w = World(rqueue=[qrec(31, kind="movie", title="Some.Film.1987.1080p-GRP", year=2024, tid=43, tmdb=673, msgs=("matched to movie by ID",))])
        pid = one(w, "wrong-film")["id"]
        status, out = post(w, {"id": pid, "action": "block"})
        self.assertEqual(status, 200, out)
        self.assertEqual(w.writes, [("radarr", "DELETE", "/queue/bulk?removeFromClient=true&blocklist=true", {"ids": [31]}),
                                    ("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [43]})])

    def test_series_episodes_are_searched_again(self):
        w = World(squeue=[qrec(1, tds="failed", status="failed", ep=501, msgs=("failed",)), qrec(2, tds="failed", status="failed", ep=502, msgs=("failed",))])
        pid = one(w, "failed")["id"]
        self.assertEqual(post(w, {"id": pid, "action": "block"})[0], 200)
        self.assertEqual(w.writes[1], ("sonarr", "POST", "/command", {"name": "EpisodeSearch", "episodeIds": [501, 502]}))


RELEASES = [{"guid": "https://tracker.example.com/dl?apikey=secret&id=1", "indexerId": 3, "title": "The.Ring.Thing.2004.DVDRip", "size": 700_000_000, "quality": {"quality": {"name": "DVD"}},
             "languages": [{"name": "German"}], "seeders": 4, "leechers": 1, "customFormatScore": 10, "rejected": True, "rejections": ["Quality DVD not wanted in profile"], "indexer": "Tracker", "protocol": "torrent", "age": 100},
            {"guid": "https://tracker.example.com/dl?apikey=secret&id=2", "indexerId": 3, "title": "The.Ring.Thing.2004.German.720p", "size": 2_000_000_000, "quality": {"quality": {"name": "WEBDL-720p"}},
             "languages": [{"name": "German"}], "seeders": 9, "customFormatScore": 50, "rejected": False, "rejections": [], "indexer": "Tracker", "protocol": "torrent", "age": 30}]


class NoRelease(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                       titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 7}})
        self.w.releases = RELEASES
        self.pid = one(self.w, "no-release")["id"]

    def test_release_list_without_download_links(self):
        status, out = get(self.w, f"releases?id={self.pid}")
        self.assertEqual(status, 200)
        self.assertNotIn("apikey", json.dumps(out))
        self.assertEqual([(r["title"], r["rejected"]) for r in out["releases"]], [("The.Ring.Thing.2004.German.720p", False), ("The.Ring.Thing.2004.DVDRip", True)])
        r = out["releases"][1]
        self.assertEqual((r["quality"], r["languages"], r["seeders"], r["score"], r["rejections"]), ("DVD", ["German"], 4, 10, ["Quality DVD not wanted in profile"]))
        self.assertIn(("radarr", "/release?movieId=21"), self.w.gets)

    def test_release_search_runs_at_most_every_two_minutes(self):
        get(self.w, f"releases?id={self.pid}")
        get(self.w, f"releases?id={self.pid}")
        self.assertEqual(sum(1 for g in self.w.gets if g[1].startswith("/release")), 1)

    def test_rejected_or_unknown_release_cannot_be_grabbed(self):
        out = get(self.w, f"releases?id={self.pid}")[1]
        bad = next(r["n"] for r in out["releases"] if r["rejected"])
        for n in (bad, 99, "0"):
            manual.THROTTLE.pop(("act", self.pid), None)
            self.assertEqual(post(self.w, {"id": self.pid, "action": "grab", "release": n})[0], 400)
        self.assertEqual(self.w.writes, [])

    def test_grab_sends_the_arr_its_own_release(self):
        out = get(self.w, f"releases?id={self.pid}")[1]
        good = next(r["n"] for r in out["releases"] if not r["rejected"])
        self.assertEqual(post(self.w, {"id": self.pid, "action": "grab", "release": good})[0], 200)
        self.assertEqual(self.w.writes, [("radarr", "POST", "/release", {"guid": "https://tracker.example.com/dl?apikey=secret&id=2", "indexerId": 3})])

    def test_old_release_list_cannot_be_grabbed_from(self):
        out = get(self.w, f"releases?id={self.pid}")[1]
        good = next(r["n"] for r in out["releases"] if not r["rejected"])
        manual.NOW = lambda: T + 3600
        os.utime(self.w.rs_file, (T + 3600, T + 3600))
        manual.CACHE.clear()
        self.assertEqual(post(self.w, {"id": self.pid, "action": "grab", "release": good})[0], 400)

    def test_switch_to_older_versions_profile_and_search(self):
        status, out = post(self.w, {"id": self.pid, "action": "profile"})
        self.assertEqual(status, 200, out)
        self.assertEqual(self.w.writes, [("radarr", "PUT", "/movie/editor", {"movieIds": [21], "qualityProfileId": 11}),
                                         ("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [21]})])
        self.assertIsNone(out["problem"]["resolved"])

    def test_no_switch_when_there_is_no_twin(self):
        reset()
        w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                  titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 13}})
        pid = one(w, "no-release")["id"]
        self.assertEqual(post(w, {"id": pid, "action": "profile"})[0], 400)

    def test_series_release_list_is_per_requested_season(self):
        reset()
        w = World(rs=state(noRelease={"tv/4040": "2026-11-14T04:17:00Z"}), requests=[request(13, "tv", 4040, ext=54, tvdb=9090, seasons=(2, 3))],
                  titles={"/series/54": {"id": 54, "title": "Seerr Series", "year": 2019, "tvdbId": 9090, "tmdbId": 4040, "qualityProfileId": 7}})
        pid = one(w, "no-release")["id"]
        self.assertEqual(get(w, f"releases?id={pid}")[0], 200)
        self.assertIn(("sonarr", "/release?seriesId=54&seasonNumber=2"), w.gets)
        manual.THROTTLE.clear()
        self.assertEqual(get(w, f"releases?id={pid}&season=3")[0], 200)
        self.assertIn(("sonarr", "/release?seriesId=54&seasonNumber=3"), w.gets)
        self.assertEqual(get(w, f"releases?id={pid}&season=9")[0], 400)


class Removed(unittest.TestCase):
    def setUp(self):
        reset()
        self.w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21, media_id=16, profile=13)])
        self.w.lookups["radarr"] = {"title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "images": []}
        self.pid = one(self.w, "removed")["id"]

    def test_add_again_with_the_requests_profile_monitored_and_searched(self):
        status, out = post(self.w, {"id": self.pid, "action": "readd"})
        self.assertEqual(status, 200, out)
        (app, method, path, body), = self.w.writes
        self.assertEqual((app, method, path, body["tmdbId"], body["qualityProfileId"], body["rootFolderPath"], body["monitored"], body["addOptions"]),
                         ("radarr", "POST", "/movie", 10248, 13, "/data/media/movies", True, {"searchForMovie": True}))
        self.assertIn(("radarr", "/movie/lookup/tmdb?tmdbId=10248"), self.w.gets)
        self.assertEqual(self.w.admin_calls, [])

    def test_delete_is_for_admins_only(self):
        self.assertEqual(post(self.w, {"id": self.pid, "action": "delete", "confirm": "delete"})[0], 403)
        self.assertEqual(self.w.admin_calls, [])

    def test_admin_delete_needs_the_confirmation(self):
        self.w.me = ADMIN
        self.assertEqual(post(self.w, {"id": self.pid, "action": "delete"})[0], 400)
        self.assertEqual(self.w.admin_calls, [])
        status, out = post(self.w, {"id": self.pid, "action": "delete", "confirm": "delete"})
        self.assertEqual(status, 200, out)
        self.assertEqual(self.w.admin_calls, [("DELETE", "/request/11", None)])
        self.assertEqual(out["problem"]["resolved"]["how"], "delete")


class Backend(unittest.TestCase):
    def setUp(self):
        reset()

    def test_arr_error_is_502_without_details(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)])
        pid = one(w, "removed")["id"]

        def broken(app):
            def call(path, method="GET", body=None, timeout=None):
                raise dn.ApiError("http://radarr:7878/api/v3/movie: 500")
            return call
        w.arr = broken
        status, out = post(w, {"id": pid, "action": "readd"})
        self.assertEqual(status, 502)
        self.assertNotIn("radarr:7878", json.dumps(out))

    def test_a_failed_action_still_counts_for_the_throttle(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)])
        pid = one(w, "removed")["id"]
        good = w.arr

        def broken(app):
            def call(path, method="GET", body=None, timeout=None):
                if "lookup" in path:
                    raise dn.ApiError("radarr: 500")
                return good(app)(path, method, body)
            return call
        w.arr = broken
        self.assertEqual(post(w, {"id": pid, "action": "readd"})[0], 502)
        self.assertEqual(post(w, {"id": pid, "action": "readd"})[0], 429)

    def test_env_wires_the_state_files(self):
        d = dn.Deps(seerr=None, arr=None, qbit=None, sab=None)
        self.assertIsNone(d.manual_file)
        self.assertEqual(manual.KEEP_DAYS, 180)


if __name__ == "__main__":
    unittest.main()
