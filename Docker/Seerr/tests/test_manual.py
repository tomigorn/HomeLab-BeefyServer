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
import types
import unittest
import urllib.parse

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
        self.retention = pathlib.Path(self.tmp.name, "retention.json")
        self.retention_keep = pathlib.Path(self.tmp.name, "keep.json")
        self.lookups = {"radarr": [], "sonarr": []}
        self.releases, self.proposals, self.episodes = [], [], []
        self.tags, self.history = [], []
        self.seerr_media = {}
        self.profiles = {"radarr": R_PROFILES, "sonarr": S_PROFILES}
        self.request_calls, self.slow, self.odd_queue, self.token = 0, 0, False, None
        self.clamav = pathlib.Path(self.tmp.name, "clamav-results.jsonl")

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
                if path == "/tag":
                    return {"id": 901, "label": body["label"]}
                return {"id": 900} if path in ("/movie", "/series") else {}
            self.gets.append((app, path))
            if path.startswith("/queue"):
                return None if self.odd_queue else {"records": self.queues[app]}
            if path == "/qualityprofile":
                return self.profiles[app]
            if path == "/rootfolder":
                return [{"path": "/data/media/movies" if app == "radarr" else "/data/media/tv"}]
            if "/lookup" in path:
                return self.lookups[app]
            if path.startswith("/release?"):
                return self.releases
            if path == "/tag":
                return self.tags
            if path.startswith("/history/"):
                return self.history
            if path.startswith("/history?"):
                dl = urllib.parse.parse_qs(path.partition("?")[2]).get("downloadId", [""])[0]
                return {"records": [h for h in self.history if h.get("downloadId") == dl]}
            if path.startswith("/episode?episodeIds="):
                ids = {int(x) for x in urllib.parse.parse_qs(path.partition("?")[2]).get("episodeIds", [])}
                return [e for e in self.episodes if e.get("id") in ids]
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
                       manual_file=str(self.store), request_state_file=str(self.rs_file), clamav_results=str(self.clamav),
                       retention_file=str(self.retention), retention_keep=str(self.retention_keep))

    def retained(self, key, default):
        self.retention.write_text(json.dumps({"titles": {key: {"kind": key.split("/")[0], "due": "2027-03-01", "default": default, "reasons": ["old"] if default else [], "protected": key == "movie/653"}}}))

    def scanned(self, path, verdict="clean", sig=None, at="2026-11-15T02:00:00Z", size=None, **extra):
        if size is None:
            size = os.path.getsize(path) if os.path.exists(path) else 1
        if not os.path.exists(path):
            FAKE_FILES.setdefault(path, (size, T - DAY))
        with open(self.clamav, "a") as f:
            f.write(json.dumps({"at": at, "path": path, "size": size, "verdict": verdict, "signature": sig, "mode": "queue", **extra}) + "\n")


FAKE_FILES = {}


def fake_stat(path):
    """Download files of the fixtures that only exist as /data/... paths: (size, mtime)."""
    if path in FAKE_FILES:
        size, mtime = FAKE_FILES[path]
        return types.SimpleNamespace(st_size=size, st_mtime=mtime)
    return os.stat(path)


def reset():
    FAKE_FILES.clear()
    manual.STAT = fake_stat
    manual.NOW = lambda: T
    manual.CACHE.clear()
    manual.OFFERS.clear()
    manual.THROTTLE.clear()
    manual.FLOOD.clear()
    manual.SEARCHES.clear()
    manual.MAIN_FILE.clear()
    manual.KICK = manual.drain


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

    def test_queue_problem_finds_its_seerr_request_by_the_arr_id_or_tvdb(self):
        for req in (request(167, "tv", 312697, name="joe", ext=54), request(167, "tv", 312697, name="joe", tvdb=9090)):
            reset()
            w = World(squeue=[qrec(1, ep=501)], requests=[req])
            p = one(w, "import")
            self.assertEqual((p["key"], p["requesters"]), ("tv/312697", ["joe"]))

    def test_open_count_counts_problems_not_titles(self):
        w = World(squeue=[qrec(1, dl="A", ep=501), qrec(2, dl="B", ep=502)])
        self.assertEqual(len(problems(w)["open"]), 2)
        self.assertEqual(get(w, "count"), (200, {"open": 2}))

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
        for x in self.w.proposals:
            self.w.scanned(x["path"])
        self.pid = one(self.w, "import")["id"]

    def test_files_show_names_and_the_probable_episode_never_paths(self):
        status, out = get(self.w, f"files?id={self.pid}")
        self.assertEqual(status, 200)
        self.assertEqual([(f["n"], f["name"], f["episodeIds"], f["probable"]) for f in out["files"]],
                         [(0, "Company.Retreat.S01E01.mkv", [501], True), (1, "Company.Retreat.1x02.mkv", [502], True)])
        self.assertEqual([e["label"] for e in out["episodes"]], ["S01E01", "S01E02"])
        self.assertNotIn("/data", json.dumps(out))
        self.assertIn(("sonarr", "/manualimport?downloadId=DL1&filterExistingFiles=true"), self.w.gets)
        self.assertFalse(any("folder=" in g[1] or "seriesId=" in g[1] for g in self.w.gets if g[1].startswith("/manualimport")))

    def test_only_the_downloads_own_files_are_listed_and_importable(self):
        library = {"path": "/data/media/tv/CR/Season 01/CR - S01E06.mkv", "relativePath": "Season 01/CR - S01E06.mkv", "size": 5, "quality": {}, "languages": [],
                   "episodes": [{"id": 506, "seasonNumber": 1, "episodeNumber": 6}], "rejections": [], "downloadId": None}
        other = dict(library, path="/data/usenet/complete/tv/X/x.mkv", relativePath="x.mkv", downloadId="OTHER")
        self.w.proposals = [library, other] + self.w.proposals
        out = get(self.w, f"files?id={self.pid}")[1]
        self.assertEqual([f["name"] for f in out["files"]], ["Company.Retreat.S01E01.mkv", "Company.Retreat.1x02.mkv"])
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 2, "episodeIds": [501]}]})[0], 400)
        self.assertEqual(self.w.writes, [])

    def test_every_episode_is_offered_specials_last(self):
        self.w.episodes = [{"id": 900, "seasonNumber": 0, "episodeNumber": 1}, {"id": 610, "seasonNumber": 2, "episodeNumber": 1}] + EPISODES
        out = get(self.w, f"files?id={self.pid}")[1]
        self.assertEqual([e["label"] for e in out["episodes"]], ["S01E01", "S01E02", "S02E01", "S00E01"])

    def test_a_file_without_an_episode_in_its_name_gets_the_queue_records_episode(self):
        reset()
        w = World(squeue=[qrec(1, dl="DL9", ep=502)])
        w.episodes = EPISODES
        w.proposals = [{"path": "/data/usenet/complete/tv/x/abc123.mkv", "relativePath": "abc123.mkv", "size": 9, "quality": {}, "languages": [], "episodes": [], "rejections": [], "downloadId": "dl9"}]
        pid = one(w, "import")["id"]
        self.assertEqual(get(w, f"files?id={pid}")[1]["files"][0]["episodeIds"], [502])

    def test_opening_the_list_again_reuses_it(self):
        first = get(self.w, f"files?id={self.pid}")[1]
        again = get(self.w, f"files?id={self.pid}")
        self.assertEqual((again[0], again[1]["token"], again[1]["files"]), (200, first["token"], first["files"]))
        self.assertEqual(sum(1 for g in self.w.gets if g[1].startswith("/manualimport")), 1)

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
        self.assertEqual(get(self.w, f"files?id={self.pid}&refresh=1")[0], 429)

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

    def test_a_sample_needs_an_admin(self):
        self.w.proposals[1]["rejections"] = [{"reason": "Unable to determine if file is a sample"}]
        get(self.w, f"files?id={self.pid}")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 1, "episodeIds": [502]}]})[0], 403)
        self.assertEqual(self.w.writes, [])

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
        w.proposals = [{"path": "/data/torrents/radarr/a/small.mkv", "relativePath": "a/small.mkv", "size": 10, "quality": {}, "languages": [], "movie": None, "rejections": [], "downloadId": "DL1"},
                       {"path": "/data/torrents/radarr/a/film.mkv", "relativePath": "a/film.mkv", "size": 9_000_000_000, "quality": {}, "languages": [], "movie": None, "rejections": [], "downloadId": "DL1"}]
        w.scanned("/data/torrents/radarr/a/film.mkv")
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
             "languages": [{"name": "German"}], "seeders": 4, "leechers": 1, "customFormatScore": 10, "rejected": True, "rejections": ["Quality DVD not wanted in profile"], "indexer": "Tracker", "protocol": "torrent", "age": 100, "mappedMovieId": 21},
            {"guid": "https://tracker.example.com/dl?apikey=secret&id=2", "indexerId": 3, "title": "The.Ring.Thing.2004.German.720p", "size": 2_000_000_000, "quality": {"quality": {"name": "WEBDL-720p"}},
             "languages": [{"name": "German"}], "seeders": 9, "customFormatScore": 50, "rejected": False, "rejections": [], "indexer": "Tracker", "protocol": "torrent", "age": 30, "mappedMovieId": 21}]


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

    def test_reopening_the_release_list_reuses_it(self):
        first = get(self.w, f"releases?id={self.pid}")[1]
        manual.NOW = lambda: T + 300
        os.utime(self.w.rs_file, (T + 300, T + 300))
        again = get(self.w, f"releases?id={self.pid}")[1]
        self.assertEqual(again["token"], first["token"])
        self.assertEqual(sum(1 for g in self.w.gets if g[1].startswith("/release")), 1)
        get(self.w, f"releases?id={self.pid}&refresh=1")
        self.assertEqual(sum(1 for g in self.w.gets if g[1].startswith("/release")), 2)

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

    def test_only_releases_the_arr_matched_to_this_title_are_listed(self):
        other = dict(RELEASES[1], guid="g3", title="Spider-Man.Brand.New.Day.2026.2160p", mappedMovieId=142, rejected=True, rejections=["Wrong movie"])
        unknown = dict(RELEASES[1], guid="g4", title="Project.Hail.Mary.2026.1080p", mappedMovieId=None, rejected=True, rejections=["Unknown Movie"])
        cf = dict(RELEASES[1], guid="g5", title="The.Ring.Thing.2004.PLDUB.DVDRip", rejected=True, customFormatScore=-10000,
                  rejections=["Custom Formats Language: Not Original have score -49996050 below Movie's profile minimum -10000"])
        self.w.releases = [other, RELEASES[1], unknown, cf]
        out = get(self.w, f"releases?id={self.pid}")[1]
        self.assertEqual([(r["title"], r["rejected"]) for r in out["releases"]], [("The.Ring.Thing.2004.German.720p", False), ("The.Ring.Thing.2004.PLDUB.DVDRip", True)])
        self.assertEqual(out["dropped"], 2)
        self.assertEqual(out["releases"][1]["reasons"], ["cf-score"])
        self.assertEqual(post(self.w, {"id": self.pid, "action": "grab", "release": 0})[0], 200)
        self.assertEqual(self.w.writes[0][3]["guid"], RELEASES[1]["guid"])

    def test_release_reasons_in_plain_codes(self):
        for msg, code in {"Quality DVD-R is not wanted in profile": "quality", "Does not contain one of the required terms: x": "required-terms",
                          "Existing file on disk is of equal or higher preference: WEBDL-2160p v1": "existing", "Not enough seeders: 0. Minimum seeders: 1": "seeders",
                          "Custom Formats LQ have score -29997750 below Movie's profile minimum -10000": "cf-score", "Release is blocklisted": "blocklisted",
                          "4.2 GB is larger than maximum allowed 3.9 GB": "size", "Something else": "unknown"}.items():
            self.assertEqual(manual.release_reason(msg), code, msg)

    def test_series_keeps_only_its_own_season(self):
        reset()
        w = World(rs=state(noRelease={"tv/4040": "2026-11-14T04:17:00Z"}), requests=[request(13, "tv", 4040, ext=54, tvdb=9090, seasons=(2,))],
                  titles={"/series/54": {"id": 54, "title": "Seerr Series", "year": 2019, "tvdbId": 9090, "tmdbId": 4040, "qualityProfileId": 7}})
        base = dict(RELEASES[1], mappedMovieId=None, mappedSeriesId=54)
        w.releases = [dict(base, guid="a", title="S02", mappedSeasonNumber=2), dict(base, guid="b", title="S01", mappedSeasonNumber=1), dict(base, guid="c", title="Other", mappedSeriesId=99, mappedSeasonNumber=2)]
        pid = one(w, "no-release")["id"]
        out = get(w, f"releases?id={pid}")[1]
        self.assertEqual(([r["title"] for r in out["releases"]], out["dropped"]), (["S02"], 2))

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


    def test_a_delete_that_fails_halfway_records_what_went(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21), request(12, "movie", 10248, uid=5, name="joe", ext=21)])
        w.me = ADMIN
        reset()
        pid = one(w, "removed")["id"]
        plain = w.admin

        def flaky(path, method="GET", body=None):
            if method == "DELETE" and path == "/request/12":
                raise dn.ApiError("seerr: 500")
            return plain(path, method, body)
        w.admin = flaky
        self.assertEqual(post(w, {"id": pid, "action": "delete", "confirm": "delete"})[0], 502)
        last = json.loads(w.store.read_text())["problems"][pid]["actions"][-1]
        self.assertEqual((last["action"], last["user"]), ("delete", "janedoe"))
        self.assertIn("11", last["result"])
        self.assertIn("12", last["result"])
        self.assertEqual(w.admin_calls, [("DELETE", "/request/11", None)])


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

    def test_offers_are_stored_under_their_lock(self):
        done = threading.Event()
        with manual.OFFERS_LOCK:
            t = threading.Thread(target=lambda: (manual.keep("aaaaaaaaaaaa", "lookup", USER, []), done.set()))
            t.start()
            self.assertFalse(done.wait(0.2))
        t.join(2)
        self.assertTrue(done.is_set())

    def test_env_wires_the_state_files(self):
        d = dn.Deps(seerr=None, arr=None, qbit=None, sab=None)
        self.assertIsNone(d.manual_file)
        self.assertEqual(manual.KEEP_DAYS, 180)

FFPROBE_E09 = {"format": {"duration": "1860.4", "size": "1590000000"}, "streams": [
    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "color_transfer": "bt709"},
    {"codec_type": "audio", "codec_name": "eac3", "channels": 6, "tags": {"language": "por"}},
    {"codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "eng"}},
    {"codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "por"}},
    {"codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}}]}
FFPROBE_UHD = {"format": {"duration": "9000"}, "streams": [
    {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 1600, "color_transfer": "smpte2084", "side_data_list": [{"side_data_type": "DOVI configuration record"}]},
    {"codec_type": "audio", "codec_name": "truehd", "channels": 8, "tags": {"language": "eng"}},
    {"codec_type": "audio", "codec_name": "dts", "profile": "DTS-HD MA", "channels": 6}]}


class Reasons(unittest.TestCase):
    def test_arr_messages_map_to_plain_reasons(self):
        cases = {
            BY_ID: "id-match",
            "Found matching movie via grab history, but release was matched to movie by ID. Manual Import required.": "id-match",
            "No files found are eligible for import in /data/usenet/complete/tv/x": "no-eligible",
            "Not an upgrade for existing episode file(s)": "not-upgrade",
            "Not a Custom Format upgrade for existing movie file(s)": "not-upgrade",
            "Sample": "sample",
            "Unable to determine if file is a sample": "sample",
            "Unable to parse file": "unparsable",
            "Episode 9 was unexpected considering the folder name": "not-in-release",
            "Dub only: no original-language audio": "dub-only",
            "Download failed": "failed",
            "Something entirely new": "unknown",
            "": "unknown",
        }
        for msg, code in cases.items():
            self.assertEqual(manual.reason_code(msg), code, msg)

    def test_problems_carry_their_reason(self):
        reset()
        w = World(squeue=[qrec(1, ep=501)], rqueue=[qrec(2, kind="movie", tid=43, tmdb=673, title="Some.Film.1987.1080p-GRP", year=2024, msgs=("matched to movie by ID",))],
                  rs=state(removed=["movie/10248"], noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)])
        found = {p["type"]: p["details"]["reason"] for p in problems(w)["open"]}
        self.assertEqual(found, {"import": "id-match", "wrong-film": "wrong-film", "removed": "removed", "no-release": "no-release"})
        self.assertEqual(one(w, "import")["details"]["message"], BY_ID)

PROFILE = {"id": 10, "name": "4K HDR - WEB-2160p (1080p fallback) - English original", "cutoff": 1003, "items": [
    {"quality": {"id": 0, "name": "Unknown", "resolution": 0}, "allowed": False},
    {"id": 1001, "name": "WEB 720p", "allowed": False, "items": [{"quality": {"id": 5, "name": "WEBDL-720p", "resolution": 720}}]},
    {"quality": {"id": 7, "name": "Bluray-1080p", "resolution": 1080}, "allowed": False},
    {"id": 1002, "name": "WEB 1080p", "allowed": True, "items": [{"quality": {"id": 3, "name": "WEBDL-1080p", "resolution": 1080}}, {"quality": {"id": 15, "name": "WEBRip-1080p", "resolution": 1080}}]},
    {"id": 1003, "name": "WEB 2160p", "allowed": True, "items": [{"quality": {"id": 18, "name": "WEBDL-2160p", "resolution": 2160}}]}]}
SDR_PROFILE = {"id": 8, "name": "WEB-1080p", "cutoff": 1002, "items": PROFILE["items"][:4]}


def media(**kw):
    m = {"resolution": "1080p", "video": "H.264", "hdr": None, "audio": [{"language": "por", "codec": "E-AC3", "channels": "5.1"}], "subtitles": [], "size": 1_600_000_000, "minutes": 31}
    m.update(kw)
    return m


def ctx(**kw):
    c = {"kind": "tv", "original": "English", "profile": PROFILE, "quality": 3, "expected": 30}
    c.update(kw)
    return c


def marks(m, c):
    return {x["rule"]: x["mark"] for x in manual.checks(m, c)}


class Checks(unittest.TestCase):
    def test_running_time_boundaries(self):
        for minutes, mark in ((44, "ok"), (36, "ok"), (45, "warn"), (50, "warn"), (51, "bad"), (30, "warn"), (29, "bad"), (40, "ok")):
            self.assertEqual(marks(media(minutes=minutes), ctx(expected=40))["runtime"], mark, minutes)
        r = next(x for x in manual.checks(media(minutes=31), ctx(expected=30)) if x["rule"] == "runtime")
        self.assertEqual((r["minutes"], r["expected"]), (31, 30))
        self.assertNotIn("runtime", marks(media(minutes=None), ctx()))
        self.assertNotIn("runtime", marks(media(), ctx(expected=None)))

    def test_original_language_and_english(self):
        m = marks(media(), ctx())
        self.assertEqual(m["original"], "bad")
        self.assertNotIn("english", m)
        self.assertEqual(m["german"], "none")
        both = media(audio=[{"language": "por"}, {"language": "eng"}])
        self.assertEqual((marks(both, ctx(original="Portuguese"))["original"], marks(both, ctx(original="Portuguese"))["english"]), ("ok", "ok"))
        self.assertEqual(marks(media(), ctx(original="Portuguese"))["english"], "warn")
        self.assertEqual(marks(media(audio=[{"language": None}]), ctx())["original"], "none")
        self.assertEqual(marks(media(), ctx(original=None))["original"], "none")

    def test_german_audio_or_subtitles(self):
        self.assertEqual(marks(media(subtitles=["ger"]), ctx())["german"], "ok")
        self.assertEqual(marks(media(audio=[{"language": "deu"}]), ctx())["german"], "ok")
        self.assertEqual(marks(media(audio=[{"language": "gsw"}]), ctx())["german"], "ok")

    def test_quality_against_the_profile(self):
        self.assertEqual(marks(media(), ctx(quality=18))["quality"], "ok")
        self.assertEqual(marks(media(), ctx(quality=3))["quality"], "ok")
        self.assertEqual(marks(media(), ctx(quality=7))["quality"], "bad")
        self.assertEqual(marks(media(), ctx(quality=None))["quality"], "none")
        q = next(x for x in manual.checks(media(), ctx(quality=3)) if x["rule"] == "quality")
        self.assertEqual((q["quality"], q["upgrade"]), ("WEBDL-1080p", "WEB 2160p"))
        top = next(x for x in manual.checks(media(), ctx(quality=18)) if x["rule"] == "quality")
        self.assertIsNone(top["upgrade"])

    def test_hdr(self):
        self.assertEqual(marks(media(), ctx())["hdr"], "ok")
        sdr = next(x for x in manual.checks(media(), ctx()) if x["rule"] == "hdr")
        self.assertEqual((sdr["hdr"], sdr["upgrade"]), ("SDR", True))
        self.assertEqual(marks(media(hdr="HDR10"), ctx())["hdr"], "ok")
        self.assertEqual(marks(media(hdr="Dolby Vision + HDR10"), ctx())["hdr"], "ok")
        self.assertEqual(marks(media(hdr="Dolby Vision"), ctx())["hdr"], "bad")
        self.assertEqual(marks(media(), ctx(profile=SDR_PROFILE))["hdr"], "ok")
        self.assertFalse(next(x for x in manual.checks(media(), ctx(profile=SDR_PROFILE)) if x["rule"] == "hdr")["upgrade"])

    def test_size_is_informative_warning_only_when_very_big(self):
        self.assertEqual(marks(media(size=41_000_000_000, minutes=80), ctx(kind="movie", expected=80))["size"], "warn")
        self.assertEqual(marks(media(size=10_000_000_000, minutes=30), ctx(expected=30))["size"], "none")
        self.assertEqual(marks(media(size=10_500_000_000, minutes=30), ctx(expected=30))["size"], "warn")
        s = next(x for x in manual.checks(media(size=1_600_000_000, minutes=30), ctx()) if x["rule"] == "size")
        self.assertEqual(s["gbh"], 3.2)

    def test_dolby_vision_fallback_is_named(self):
        dv = lambda cid: {"streams": [{"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160, "color_transfer": "smpte2084",
                                       "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_bl_signal_compatibility_id": cid}]}]}
        self.assertEqual(manual.summarise(dv(1), 1)["hdr"], "Dolby Vision + HDR10")
        self.assertEqual(manual.summarise(dv(0), 1)["hdr"], "Dolby Vision")
        self.assertEqual(manual.summarise(dv(4), 1)["hdr"], "Dolby Vision + HLG")

    def test_rule_limits_live_in_one_place(self):
        self.assertEqual((manual.RULES["runtime_ok"], manual.RULES["runtime_warn"], manual.RULES["gb_per_hour_film"], manual.RULES["gb_per_hour_episode"]), (0.10, 0.25, 30, 20))


class Media(unittest.TestCase):
    def setUp(self):
        reset()
        manual.PROBE_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name, "complete")
        (self.root / "tv" / "E09").mkdir(parents=True)
        self.file = self.root / "tv" / "E09" / "Show.S01E09.mkv"
        self.file.write_bytes(b"x" * 100)
        (self.root / "tv" / "E09" / "Show.S01E09.sample.mkv").write_bytes(b"x" * 10)
        self.outside = pathlib.Path(self.tmp.name, "library.mkv")
        self.outside.write_bytes(b"y")
        (self.root / "tv" / "escape.mkv").symlink_to(self.outside)
        self.runs = []
        self.real_ffprobe = manual.FFPROBE
        manual.FFPROBE = lambda path: (self.runs.append(path), FFPROBE_E09)[1]

    def tearDown(self):
        manual.FFPROBE = self.real_ffprobe
        self.tmp.cleanup()

    def deps(self):
        return dn.Deps(seerr=None, arr=None, qbit=None, sab=None, download_roots=(str(self.root),))

    def e09_world(self):
        w = World(squeue=[qrec(1, ep=501)])
        series = dict(w.queues["sonarr"][0]["series"], originalLanguage={"id": 1, "name": "English"}, runtime=30, qualityProfileId=10)
        w.queues["sonarr"][0].update(series=series, quality={"quality": {"id": 3, "name": "WEBDL-1080p", "resolution": 1080}})
        w.titles["/series/54"] = series
        w.profiles["sonarr"] = S_PROFILES + [PROFILE]
        return w

    def test_ffprobe_is_summarised(self):
        self.assertEqual(manual.summarise(FFPROBE_E09, 1590000000), {"resolution": "1080p", "video": "H.264", "hdr": None, "audio": [
            {"language": "por", "codec": "E-AC3", "channels": "5.1"}, {"language": "eng", "codec": "AAC", "channels": "2.0"}],
            "subtitles": ["por", "eng"], "size": 1590000000, "minutes": 31})
        uhd = manual.summarise(FFPROBE_UHD, 5)
        self.assertEqual((uhd["resolution"], uhd["video"], uhd["hdr"], uhd["audio"][0]["codec"], uhd["audio"][0]["channels"], uhd["audio"][1]), ("2160p", "HEVC", "Dolby Vision", "TrueHD", "7.1", {"language": None, "codec": "DTS-HD MA", "channels": "5.1"}))

    def test_only_files_inside_the_download_folders_are_probed(self):
        self.assertIsNotNone(manual.media_of(self.deps(), str(self.file)))
        for bad in (str(self.outside), str(self.root / "tv" / "escape.mkv"), str(self.root / "tv" / "E09" / ".." / ".." / ".." / "library.mkv"), str(self.root / "tv" / "E09")):
            self.assertIsNone(manual.media_of(self.deps(), bad), bad)
        self.assertEqual(self.runs, [str(self.file.resolve())])
        no_roots = dn.Deps(seerr=None, arr=None, qbit=None, sab=None)
        self.assertIsNone(manual.media_of(no_roots, str(self.file)))

    def test_ffprobe_reads_local_media_containers_only(self):
        cmd = manual.ffprobe_command("/data/x.mkv")
        self.assertEqual(cmd[cmd.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(cmd[cmd.index("-format_whitelist") + 1], "matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,avi,mpegts")
        self.assertEqual(cmd[-1], "file:/data/x.mkv")

    def test_only_media_extensions_are_probed(self):
        for name in ("Show.S01E09.m3u8", "Show.S01E09.nfo", "playlist.txt", "Show.S01E09"):
            p = self.root / "tv" / "E09" / name
            p.write_bytes(b"#EXTM3U")
            self.assertIsNone(manual.media_of(self.deps(), str(p)), name)
        self.assertEqual(self.runs, [])

    def test_a_category_folder_is_never_walked_the_downloads_own_files_are_used(self):
        other = self.root / "tv" / "Other.Show.S01E01"
        other.mkdir()
        (other / "Other.Show.S01E01.mkv").write_bytes(b"z" * 5000)
        w = self.e09_world()
        w.queues["sonarr"][0]["outputPath"] = str(self.root / "tv")
        w.proposals = [{"path": str(self.file), "relativePath": "Show.S01E09.mkv", "size": 100, "quality": {}, "languages": [], "episodes": [], "rejections": [], "downloadId": "DL1"}]
        deps = w.deps()
        deps.download_roots = (str(self.root),)
        dn.handle("GET", "/_download-now/manual/problems", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)
        self.assertEqual(self.runs, [str(self.file.resolve())])

    def test_probes_never_run_while_the_problem_list_is_locked(self):
        held = []
        manual.FFPROBE = lambda path: (held.append(manual.STORE_LOCK.locked()), FFPROBE_E09)[1]
        w = self.e09_world()
        w.queues["sonarr"][0]["outputPath"] = str(self.root / "tv" / "E09")
        deps = w.deps()
        deps.download_roots = (str(self.root),)
        dn.handle("GET", "/_download-now/manual/problems", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)
        self.assertEqual(held, [False])

    def test_probes_of_one_scan_stop_at_the_budget(self):
        manual.FFPROBE = lambda path: (self.runs.append(path), time.sleep(0.05), FFPROBE_E09)[2]
        self.addCleanup(setattr, manual, "PROBE_SCAN_BUDGET", manual.PROBE_SCAN_BUDGET)
        manual.PROBE_SCAN_BUDGET = 0.01
        files = []
        for n in range(3):
            d = self.root / "tv" / f"D{n}"
            d.mkdir()
            (d / f"S01E0{n}.mkv").write_bytes(b"v" * (n + 1))
            files.append(d)
        w = World(squeue=[qrec(n, dl=f"DL{n}", ep=501 + n) for n in range(3)])
        for n, d in enumerate(files):
            w.queues["sonarr"][n]["outputPath"] = str(d)
        deps = w.deps()
        deps.download_roots = (str(self.root),)
        dn.handle("GET", "/_download-now/manual/problems", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)
        self.assertEqual(len(self.runs), 1)

    def test_probes_are_cached_by_path_size_and_time(self):
        manual.media_of(self.deps(), str(self.file))
        manual.media_of(self.deps(), str(self.file))
        self.assertEqual(len(self.runs), 1)
        self.file.write_bytes(b"x" * 200)
        manual.media_of(self.deps(), str(self.file))
        self.assertEqual(len(self.runs), 2)

    def test_the_card_gets_the_biggest_video_of_the_download_not_the_sample(self):
        w = self.e09_world()
        w.queues["sonarr"][0]["outputPath"] = str(self.root / "tv" / "E09")
        deps = w.deps()
        deps.download_roots = (str(self.root),)
        dn.handle("GET", "/_download-now/manual/problems", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)
        status, out = dn.handle("GET", "/_download-now/manual/problems", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)
        self.assertEqual(out["open"][0]["details"]["media"]["video"], "H.264")
        self.assertEqual({c["rule"]: c["mark"] for c in out["open"][0]["details"]["checks"]}, {"original": "ok", "german": "none", "quality": "ok", "hdr": "ok", "runtime": "ok", "size": "none"})
        self.assertEqual(self.runs, [str(self.file.resolve())])
        self.assertNotIn(self.tmp.name, json.dumps(out))

    def test_file_list_has_each_files_media(self):
        w = self.e09_world()
        w.episodes = EPISODES
        w.proposals = [{"path": str(self.file), "relativePath": "Show.S01E09.mkv", "size": 100, "quality": {}, "languages": [], "episodes": [], "rejections": [], "downloadId": "DL1"}]
        deps = w.deps()
        deps.download_roots = (str(self.root),)
        pid = dn.handle("GET", "/_download-now/manual/problems", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)[1]["open"][0]["id"]
        out = dn.handle("GET", f"/_download-now/manual/files?id={pid}", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)[1]
        self.assertEqual(out["files"][0]["media"]["audio"][0]["language"], "por")
        self.assertEqual({c["rule"]: c["mark"] for c in out["files"][0]["checks"]}["runtime"], "ok")
        self.assertNotIn(self.tmp.name, json.dumps(out))

    def test_file_rejections_carry_a_reason(self):
        w = World(squeue=[qrec(1, ep=501)])
        w.episodes = EPISODES
        w.proposals = [{"path": "/x/a.mkv", "relativePath": "a.mkv", "size": 1, "quality": {}, "languages": [], "episodes": [], "rejections": [{"reason": "Not an upgrade for existing episode file(s)"}], "downloadId": "DL1"}]
        pid = one(w, "import")["id"]
        f = get(w, f"files?id={pid}")[1]["files"][0]
        self.assertEqual(f["reasons"], ["not-upgrade"])


class Virus(unittest.TestCase):
    def setUp(self):
        reset()
        manual.CLAMAV_CACHE.clear()
        self.w = World(squeue=[qrec(1, ep=501)])
        self.w.episodes = EPISODES
        self.path = "/data/usenet/complete/tv/CR/CR.S01E01.mkv"
        self.w.proposals = [{"path": self.path, "relativePath": "CR.S01E01.mkv", "size": 5, "quality": {}, "languages": [], "episodes": [], "rejections": [], "downloadId": "DL1"}]
        self.pid = one(self.w, "import")["id"]

    def files(self):
        manual.OFFERS.clear()
        manual.THROTTLE.clear()
        return get(self.w, f"files?id={self.pid}")[1]["files"][0]["virus"]

    def imp(self):
        manual.THROTTLE.clear()
        return post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 0, "episodeIds": [501]}]})

    def test_states_from_the_scan_log(self):
        self.assertEqual(self.files()["state"], "unknown")
        self.w.scanned(self.path, "queued")
        self.assertEqual(self.files()["state"], "pending")
        self.w.scanned(self.path, "clean", at="2026-11-15T02:05:00Z")
        self.assertEqual(self.files(), {"state": "clean", "at": "2026-11-15T02:05:00Z", "signature": None})
        self.w.scanned(self.path, "infected", "Win.Test.EICAR_HDB-1")
        self.assertEqual((self.files()["state"], self.files()["signature"]), ("infected", "Win.Test.EICAR_HDB-1"))
        self.w.scanned(self.path, "skipped")
        self.assertEqual(self.files()["state"], "unknown")

    def test_too_big_for_clamd_but_parts_clean_and_runtime_fits_imports_for_everyone(self):
        self.w.titles["/series/54"] = {"id": 54, "title": "Company Retreat", "runtime": 30}
        self.w.scanned(self.path, "parts-clean", minutes=31, parts=2, video=True)
        v = self.files()
        self.assertEqual((v["state"], v["minutes"], v["runtimeFits"], v["expected"]), ("parts-clean", 31, True, 30))
        self.assertEqual(self.imp()[0], 200)

    def test_parts_clean_with_a_wrong_running_time_is_for_admins(self):
        self.w.titles["/series/54"] = {"id": 54, "title": "Company Retreat", "runtime": 30}
        self.w.scanned(self.path, "parts-clean", minutes=60, parts=1, video=True)
        v = self.files()
        self.assertEqual((v["runtimeFits"], v["expected"]), (False, 30))
        self.assertEqual(self.imp(), (403, {"error": "virus scan pending"}))
        self.w.me = ADMIN
        self.files()
        self.assertEqual(self.imp()[0], 200)

    def test_parts_without_a_known_running_time_are_for_admins(self):
        self.w.scanned(self.path, "parts-clean", minutes=31, parts=1, video=True)
        v = self.files()
        self.assertEqual((v["runtimeFits"], v["expected"]), (False, None))
        self.assertEqual(self.imp(), (403, {"error": "virus scan pending"}))

    def test_a_kept_file_list_carries_the_same_running_time_result_the_gate_uses(self):
        self.w.titles["/series/54"] = {"id": 54, "title": "Company Retreat", "runtime": 30}
        self.w.scanned(self.path, "clean")
        self.assertNotIn("runtimeFits", self.files())
        self.w.scanned(self.path, "parts-clean", minutes=60, parts=1, video=True)
        v = get(self.w, f"files?id={self.pid}")[1]["files"][0]["virus"]
        self.assertEqual((v["state"], v["runtimeFits"], v["expected"]), ("parts-clean", False, 30))
        self.assertEqual(self.imp(), (403, {"error": "virus scan pending"}))
        self.w.scanned(self.path, "parts-clean", minutes=29, parts=1, video=True)
        v = get(self.w, f"files?id={self.pid}")[1]["files"][0]["virus"]
        self.assertEqual((v["runtimeFits"], v["expected"]), (True, 30))
        self.assertEqual(self.imp()[0], 200)

    def test_malware_in_the_parts_is_malware(self):
        self.w.scanned(self.path, "parts-infected", "Win.Test.EICAR_HDB-1")
        self.w.me = ADMIN
        self.assertEqual(self.files()["state"], "infected")
        self.assertEqual(self.imp(), (403, {"error": "malware"}))

    def test_a_failed_parts_scan_is_for_admins(self):
        self.w.scanned(self.path, "parts-error", why="no video stream")
        v = self.files()
        self.assertEqual((v["state"], v["why"]), ("parts-error", "no video stream"))
        self.assertEqual(self.imp(), (403, {"error": "virus scan pending"}))
        self.w.me = ADMIN
        self.files()
        self.assertEqual(self.imp()[0], 200)

    def test_a_replaced_file_is_not_clean_any_more(self):
        self.w.scanned(self.path)
        self.assertEqual(self.files()["state"], "clean")
        FAKE_FILES[self.path] = (2, T - DAY)
        self.assertEqual(self.files()["state"], "unknown")
        FAKE_FILES[self.path] = (1, T)
        self.assertEqual(self.files()["state"], "unknown")
        FAKE_FILES[self.path] = (1, T - DAY)
        self.assertEqual(self.files()["state"], "clean")

    def test_a_vanished_file_is_not_clean(self):
        self.w.scanned(self.path)
        del FAKE_FILES[self.path]
        self.assertEqual(self.files()["state"], "unknown")

    def test_the_rotated_log_counts_too(self):
        FAKE_FILES[self.path] = (5, T - 2 * DAY)
        pathlib.Path(str(self.w.clamav) + ".1").write_text(json.dumps({"at": "2026-11-14T01:00:00Z", "path": self.path, "size": 5, "verdict": "clean", "signature": None}) + "\n")
        self.assertEqual(self.files()["state"], "clean")

    def test_clean_file_imports_for_everyone(self):
        self.w.scanned(self.path)
        self.files()
        self.assertEqual(self.imp()[0], 200)

    def test_malware_is_never_imported_not_even_by_an_admin(self):
        self.w.scanned(self.path, "infected", "Win.Test.EICAR_HDB-1")
        for me in (USER, ADMIN):
            self.w.me = me
            self.files()
            status, out = self.imp()
            self.assertEqual(status, 403)
            self.assertEqual(out["error"], "malware")
        self.assertEqual(self.w.writes, [])

    def test_pending_or_unknown_scan_imports_only_for_admins(self):
        for verdict in (None, "queued", "skipped", "error"):
            reset()
            manual.CLAMAV_CACHE.clear()
            self.w.me = USER
            if verdict:
                self.w.scanned(self.path, verdict)
            one(self.w, "import")
            self.files()
            status, out = self.imp()
            self.assertEqual((status, out.get("error")), (403, "virus scan pending"), verdict)
        self.w.me = ADMIN
        self.files()
        self.assertEqual(self.imp()[0], 200)

    def test_the_card_shows_the_scan_of_the_main_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name, "complete")
        (root / "tv" / "CR").mkdir(parents=True)
        main = root / "tv" / "CR" / "CR.S01E01.mkv"
        main.write_bytes(b"x" * 10)
        os.utime(main, (T - DAY, T - DAY))
        self.w.queues["sonarr"][0]["outputPath"] = str(root / "tv" / "CR")
        self.w.scanned(str(main.resolve()), "clean")
        deps = self.w.deps()
        deps.download_roots = (str(root),)
        real = manual.FFPROBE
        manual.FFPROBE = lambda path: None
        self.addCleanup(setattr, manual, "FFPROBE", real)
        manual.CACHE.clear()
        out = dn.handle("GET", "/_download-now/manual/problems", {"Cookie": "connect.sid=s%3Aabc"}, b"", deps)[1]
        self.assertEqual(out["open"][0]["details"]["virus"]["state"], "clean")


class VirusTotal(unittest.TestCase):
    """Hash-only lookups from ../Antivirus (virustotal-results.jsonl next to ClamAV's log): per file, last line wins."""

    files = Virus.files
    imp = Virus.imp

    def setUp(self):
        Virus.setUp(self)
        manual.VT_CACHE.clear()

    def vt(self, verdict, hashed="2026-11-15T02:00:00Z", size=None, **extra):
        size = FAKE_FILES.get(self.path, (1,))[0] if size is None else size
        FAKE_FILES.setdefault(self.path, (size, T - DAY))
        with open(pathlib.Path(self.w.clamav).with_name("virustotal-results.jsonl"), "a") as f:
            f.write(json.dumps({"at": "2026-11-15T02:10:00Z", "path": self.path, "size": size, "sha256": "a" * 64, "hashed": hashed, "vt": verdict, **extra}) + "\n")

    def test_each_verdict_reaches_the_page(self):
        self.w.scanned(self.path)
        self.assertIsNone(self.files().get("vt"))
        cases = [("queued", {}, {"state": "queued"}),
                 ("error", {"why": "HTTP 500"}, {"state": "unchecked"}),
                 ("unknown", {"checked": "2026-11-15T02:10:00Z"}, {"state": "unknown", "checked": "2026-11-15T02:10:00Z"}),
                 ("clean", {"malicious": 0, "engines": 70, "checked": "2026-11-15T02:10:00Z"}, {"state": "clean", "malicious": 0, "engines": 70, "checked": "2026-11-15T02:10:00Z"}),
                 ("flagged", {"malicious": 2, "engines": 70, "checked": "2026-11-15T02:10:00Z"}, {"state": "flagged", "malicious": 2, "engines": 70, "checked": "2026-11-15T02:10:00Z"}),
                 ("malware", {"malicious": 41, "engines": 70, "checked": "2026-11-15T02:10:00Z"}, {"state": "malware", "malicious": 41, "engines": 70, "checked": "2026-11-15T02:10:00Z"})]
        for verdict, extra, want in cases:
            self.vt(verdict, **extra)
            self.assertEqual(self.files()["vt"], want, verdict)

    def test_waiting_for_clamav_means_the_fingerprint_check_is_queued_too(self):
        self.w.scanned(self.path, "queued")
        self.assertEqual(self.files()["vt"], {"state": "queued"})

    def test_a_replaced_file_drops_the_verdict(self):
        self.w.scanned(self.path)
        self.vt("malware", malicious=41, engines=70)
        FAKE_FILES[self.path] = (2, T - DAY)
        self.assertIsNone(self.files().get("vt"))
        FAKE_FILES[self.path] = (1, T)
        self.assertIsNone(self.files().get("vt"))

    def test_known_malware_is_never_imported_not_even_by_an_admin(self):
        self.w.scanned(self.path)
        self.vt("malware", malicious=3, engines=70)
        for me in (USER, ADMIN):
            self.w.me = me
            self.files()
            self.assertEqual(self.imp(), (403, {"error": "malware"}))
        self.assertEqual(self.w.writes, [])

    def test_flagged_by_one_or_two_scanners_imports_only_for_admins(self):
        self.w.scanned(self.path)
        self.vt("flagged", malicious=1, engines=70)
        self.files()
        self.assertEqual(self.imp(), (403, {"error": "flagged"}))
        self.w.me = ADMIN
        self.files()
        self.assertEqual(self.imp()[0], 200)

    def test_clean_unknown_unchecked_and_queued_change_nothing(self):
        for verdict in ("clean", "unknown", "error", "queued"):
            self.setUp()
            self.w.scanned(self.path)
            self.vt(verdict)
            self.files()
            self.assertEqual(self.imp()[0], 200, verdict)
        self.setUp()
        self.w.scanned(self.path, "queued")
        self.vt("unknown")
        self.files()
        self.assertEqual(self.imp(), (403, {"error": "virus scan pending"}))


class SearchQueue(unittest.TestCase):
    """Release searches run one after another; a second one waits instead of being refused."""

    def setUp(self):
        reset()
        self.w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z", "movie/77": "2026-11-14T04:17:00Z", "movie/88": "2026-11-14T04:17:00Z"}),
                       requests=[request(11, "movie", 10248, ext=21), request(12, "movie", 77, ext=22), request(13, "movie", 88, ext=23)],
                       titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 13},
                               "/movie/22": {"id": 22, "title": "Tied Up", "year": 2026, "tmdbId": 77, "qualityProfileId": 7},
                               "/movie/23": {"id": 23, "title": "Third", "year": 2020, "tmdbId": 88, "qualityProfileId": 7}})
        self.w.releases = RELEASES
        self.ids = {p["title"]: p["id"] for p in problems(self.w)["open"]}
        manual.KICK = lambda deps: None
        self.addCleanup(setattr, manual, "QUEUE_MAX", manual.QUEUE_MAX)
        self.addCleanup(setattr, manual, "QUEUE_PER_USER", manual.QUEUE_PER_USER)

    def rel(self, title):
        return get(self.w, f"releases?id={self.ids[title]}")

    def test_two_problems_back_to_back_both_complete(self):
        first = self.rel("Tied Up")
        self.w.me = OTHER
        second = self.rel("The Ring Thing")
        self.assertEqual((first[0], first[1]["state"]), (200, "queued"))
        self.assertEqual((second[0], second[1]["state"], second[1]["waitingFor"], second[1]["ahead"]), (200, "queued", "Tied Up", 1))
        manual.drain(self.w.deps())
        for title in ("Tied Up", "The Ring Thing"):
            status, out = self.rel(title)
            self.assertEqual((status, out["state"]), (200, "done"), title)
        self.assertEqual(len(self.rel("The Ring Thing")[1]["releases"]), 2)
        self.assertEqual(sum(1 for g in self.w.gets if g[1].startswith("/release")), 2)

    def test_asking_again_while_waiting_is_no_second_search(self):
        self.rel("Tied Up")
        self.assertEqual(self.rel("Tied Up")[1]["state"], "queued")
        manual.drain(self.w.deps())
        self.assertEqual(sum(1 for g in self.w.gets if g[1].startswith("/release")), 1)

    def test_a_full_queue_is_refused_honestly(self):
        manual.QUEUE_MAX = 1
        self.rel("Tied Up")
        self.w.me = OTHER
        self.assertEqual(self.rel("The Ring Thing"), (429, {"error": "search queue full"}))

    def test_one_user_has_one_search_waiting_and_one_running(self):
        self.assertEqual(manual.QUEUE_PER_USER, 1)
        self.rel("Tied Up")
        self.assertEqual(self.rel("The Ring Thing"), (429, {"error": "too many searches"}))
        manual.SEARCHES[self.ids["Tied Up"]]["state"] = "running"
        self.assertEqual(self.rel("The Ring Thing")[1]["state"], "queued")
        self.assertEqual(self.rel("Third"), (429, {"error": "too many searches"}))
        self.w.me = OTHER
        self.assertEqual(self.rel("Third")[1]["state"], "queued")

    def test_a_failed_search_is_forgotten_after_ten_minutes(self):
        good = self.w.arr

        def broken(app):
            def call(path, method="GET", body=None, timeout=None):
                if path.startswith("/release"):
                    raise dn.ApiError("radarr: 500")
                return good(app)(path, method, body)
            return call
        self.w.arr = broken
        self.rel("Tied Up")
        manual.drain(self.w.deps())
        self.w.arr = good
        manual.NOW = lambda: T + 11 * 60
        os.utime(self.w.rs_file, (T + 660, T + 660))
        manual.CACHE.clear()
        manual.drain(self.w.deps())
        self.assertNotIn(self.ids["Tied Up"], manual.SEARCHES)
        self.assertEqual(self.rel("Tied Up")[1]["state"], "queued")

    def test_a_failed_search_says_so_and_can_be_asked_again(self):
        good = self.w.arr

        def broken(app):
            def call(path, method="GET", body=None, timeout=None):
                if path.startswith("/release"):
                    raise dn.ApiError("radarr: 500")
                return good(app)(path, method, body)
            return call
        self.w.arr = broken
        self.rel("Tied Up")
        manual.drain(self.w.deps())
        self.assertEqual(self.rel("Tied Up"), (502, {"error": "not reachable"}))
        self.w.arr = good
        self.assertEqual(self.rel("Tied Up")[1]["state"], "queued")


class ClickingThrough(unittest.TestCase):
    """A user going from one problem to the next never sees an error."""

    def setUp(self):
        reset()

    def test_lookups_on_different_problems_back_to_back(self):
        w = World(rs=state(queue={"tv/4040": {"state": "problem", "code": "seerr"}, "tv/5050": {"state": "problem", "code": "seerr"}}),
                  requests=[request(13, "tv", 4040, status=4, tvdb=9090), request(14, "tv", 5050, status=4, tvdb=9191)])
        for p in problems(w)["open"]:
            self.assertEqual(get(w, f"lookup?id={p['id']}")[0], 200)

    def test_file_lists_of_one_problem_for_two_users_back_to_back(self):
        w = World(squeue=[qrec(1, ep=501)])
        w.episodes = EPISODES
        w.proposals = [{"path": "/data/usenet/complete/tv/x/a.S01E01.mkv", "relativePath": "a.S01E01.mkv", "size": 1, "quality": {}, "languages": [], "episodes": [], "rejections": [], "downloadId": "DL1"}]
        pid = one(w, "import")["id"]
        self.assertEqual(get(w, f"files?id={pid}")[0], 200)
        w.me = OTHER
        self.assertEqual(get(w, f"files?id={pid}")[0], 200)
        self.assertEqual(sum(1 for g in w.gets if g[1].startswith("/manualimport")), 1)

    def test_flooding_is_refused(self):
        w = World(rs=state(queue={"tv/4040": {"state": "problem", "code": "seerr"}}), requests=[request(13, "tv", 4040, status=4, tvdb=9090)])
        pid = one(w, "unmatched")["id"]
        codes = []
        for n in range(manual.FLOOD_MAX + 1):
            manual.NOW = lambda n=n: T + 3 * n / (manual.FLOOD_MAX + 1)
            manual.THROTTLE.clear()
            codes.append(get(w, f"lookup?id={pid}")[0])
        self.assertEqual(codes[-1], 429)
        self.assertEqual(set(codes[:-1]), {200})


class KeyChange(unittest.TestCase):
    def setUp(self):
        reset()

    def test_a_changed_key_moves_the_record_instead_of_closing_it(self):
        w = World(squeue=[qrec(1, ep=501)])
        first = one(w, "import")
        self.assertEqual(first["key"], "tv/4040")
        w.requests = [request(167, "tv", 312697, name="joe", ext=54)]
        manual.NOW = lambda: T + 600
        os.utime(w.rs_file, (T + 600, T + 600))
        manual.CACHE.clear()
        out = problems(w)
        self.assertEqual(out["done"], [])
        (p,) = out["open"]
        self.assertEqual((p["key"], p["firstSeen"], p["requesters"]), ("tv/312697", first["firstSeen"], ["joe"]))

    def test_a_record_closed_by_an_old_key_change_is_merged_back(self):
        w = World(squeue=[qrec(1, ep=501)], requests=[request(167, "tv", 312697, name="joe", ext=54)])
        old = {"id": "0123456789ab", "type": "import", "key": "tv/4040", "title": "Company Retreat", "year": 2026, "requesters": [], "details": {"release": "Company.Retreat.2026.S01.2160p.WEB-DL-GRP"},
               "firstSeen": "2026-11-15T01:00:00Z", "lastSeen": "2026-11-15T02:00:00Z", "actions": [{"at": "2026-11-15T01:30:00Z", "user": "jane", "userId": 7, "action": "reopened", "result": None}],
               "resolved": {"at": "2026-11-15T02:30:00Z", "how": "gone"}}
        w.store.parent.mkdir(parents=True)
        w.store.write_text(json.dumps({"problems": {old["id"]: old}}))
        out = problems(w)
        self.assertEqual(out["done"], [])
        (p,) = out["open"]
        self.assertEqual((p["key"], p["firstSeen"], len(p["actions"])), ("tv/312697", "2026-11-15T01:00:00Z", 1))
        self.assertNotIn("0123456789ab", json.loads(w.store.read_text())["problems"])

    def test_how_a_problem_went_away(self):
        w = World(squeue=[qrec(1, ep=501)], rs=state(removed=["movie/10248"], noRelease={"movie/77": "2026-11-14T04:17:00Z"}),
                  requests=[request(11, "movie", 10248, ext=21), request(12, "movie", 77, ext=22)])
        problems(w)
        w.queues["sonarr"] = []
        w.rs_file.write_text(json.dumps(state()))
        os.utime(w.rs_file, (T, T))
        manual.CACHE.clear()
        done = {p["type"]: p["resolved"]["how"] for p in problems(w)["done"]}
        self.assertEqual(done, {"import": "imported", "no-release": "found", "removed": "gone"})


class Links(unittest.TestCase):
    def test_details_links_keep_only_harmless_parts(self):
        same = ["https://treasure-maps.com/details/7f3b2c9a1e4d5f6a7b8c9d0e1f2a3b4c", "https://thepiratebay.org/description.php?id=78123456",
                "https://nzbgeek.info/geekseek.php?guid=00ba44e84141ab985531452d88d34c0d", "https://knaben.xyz/thepiratebay/?id=99", "https://eztvx.to/ep/123/x/"]
        for url in same:
            self.assertEqual(manual.safe_link(url), url, url)
        self.assertEqual(manual.safe_link("https://x.example.com/details/1?apikey=SECRET&id=5"), "https://x.example.com/details/1?id=5")
        self.assertEqual(manual.safe_link("https://x.example.com/d?passkey=a&r=1&i=2&uid=3&token=t&api_key=k&key=k&rsskey=k"), "https://x.example.com/d")
        self.assertEqual(manual.safe_link("https://x.example.com/d#frag"), "https://x.example.com/d")

    def test_details_links_that_could_leak_are_dropped(self):
        for url in ("magnet:?xt=urn:btih:abc", "http://gluetun/3/download?apikey=k&link=x", "http://192.168.1.2/x", "http://10.0.0.1/x", "http://172.20.0.1/x",
                    "http://127.0.0.1:9696/x", "http://localhost:9696/x", "https://prowlarr.example.com/x", "https://beefy.homelab/x", "https://nas.local/x",
                    "https://x.example.com/d?q=0123456789abcdef0123456789abcdef", "javascript:alert(1)", "https://user:pw@x.example.com/", "https://x.example.com/rss/passkey/7f3b2c9a1e4d5f6a7b8c9d0e1f2a3b4c",
                    "", None, "https://[::1]/x", "ftp://x.example.com/"):
            self.assertIsNone(manual.safe_link(url), url)

    def test_release_list_has_details_links_never_download_links(self):
        reset()
        w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                  titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 13}})
        w.releases = [dict(RELEASES[1], infoUrl="https://thepiratebay.org/description.php?id=1", downloadUrl="http://gluetun/5/download?apikey=k", magnetUrl="magnet:?xt=urn:btih:x"),
                      dict(RELEASES[1], guid="g2", protocol="usenet", indexer="NZBgeek (Prowlarr)", infoUrl="http://192.168.1.2/details/1", title="The.Ring.Thing.2004.Swiss.720p")]
        pid = one(w, "no-release")["id"]
        out = get(w, f"releases?id={pid}")[1]
        self.assertEqual([(r["link"], r["private"]) for r in out["releases"]], [("https://thepiratebay.org/description.php?id=1", False), (None, True)])
        text = json.dumps(out)
        for leak in ("gluetun", "apikey", "magnet", "192.168"):
            self.assertNotIn(leak, text)

GEEK_GID = "00ba44e84141ab985531452d88d34c0d"
BTIH = "180F491CAA6F63C4469294D239A5A98E02FBB9A5"


class Scouts(unittest.TestCase):
    """What the NFO / description scouts read about a release, and reading one on demand through the beefy-side runner."""

    def setUp(self):
        reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scouts = pathlib.Path(self.tmp.name, "scouts")
        self.scouts.mkdir()
        self.req = pathlib.Path(self.tmp.name, "requests")
        self.req.mkdir()
        self.w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                       titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 13}})
        geek = dict(RELEASES[1], guid=f"https://nzbgeek.info/geekseek.php?guid={GEEK_GID}", indexer="NZBgeek (Prowlarr)", protocol="usenet", title="The.Ring.Thing.2004.MULTi.720p",
                    infoUrl=f"https://nzbgeek.info/geekseek.php?guid={GEEK_GID}", customFormatScore=60)
        tpb = dict(RELEASES[1], guid=f"magnet:?xt=urn:btih:{BTIH}&dn=x", indexer="The Pirate Bay (Prowlarr)", protocol="torrent", title="The Ring Thing (2004) [XVID]",
                   infoUrl="https://thepiratebay.org/description.php?id=3872789", customFormatScore=50)
        eztv = dict(RELEASES[1], guid="magnet:?xt=urn:btih:" + "A" * 40, indexer="EZTV (Prowlarr)", protocol="torrent", title="Other", infoUrl="https://eztvx.to/ep/1/x/", customFormatScore=40)
        self.w.releases = [geek, tpb, eztv]
        self.deps = self.w.deps()
        self.deps.scout_dir = str(self.scouts)
        self.deps.scout_requests = str(self.req)
        self.pid = self.call("GET", "problems")[1]["open"][0]["id"]

    def call(self, method, path, body=None, me=None):
        h = {"Cookie": "connect.sid=s%3Aabc"}
        if method == "POST":
            h.update({"Content-Type": "application/json", "Origin": ORIGIN})
        return dn.handle(method, "/_download-now/manual/" + path, h, json.dumps(body).encode() if body is not None else b"", self.deps)

    def releases(self):
        out = self.call("GET", f"releases?id={self.pid}")[1]
        return out, {r["title"]: r for r in out["releases"]}

    def test_what_the_scouts_already_read_is_on_each_release(self):
        (self.scouts / "nfo-scout.json").write_text(json.dumps({"releases": {f"NZBgeek|{GEEK_GID}": {
            "title": "The.Ring.Thing.2004.MULTi.720p", "langs": ["German", "French"], "lines": ["Audio: German, French"], "verdict": "langs"}}}))
        (self.scouts / "description-scout.json").write_text(json.dumps({f"read|btih:{BTIH}": {"langs": ["Swiss German"], "lines": ["Language: Schwyzerdütsch"]}}))
        _, rel = self.releases()
        self.assertEqual(rel["The.Ring.Thing.2004.MULTi.720p"]["scout"], {"state": "read", "source": "nfo", "langs": ["German", "French"], "lines": ["Audio: German, French"]})
        self.assertEqual(rel["The Ring Thing (2004) [XVID]"]["scout"], {"state": "read", "source": "description", "langs": ["Swiss German"], "lines": ["Language: Schwyzerdütsch"]})
        self.assertEqual((rel["Other"]["scout"], rel["Other"]["readable"]), (None, False))

    def test_the_description_scouts_own_match_counts(self):
        (self.scouts / "description-scout.json").write_text(json.dumps({f"btih:{BTIH}": {"verdict": "match", "title": "x"}}))
        _, rel = self.releases()
        self.assertEqual(rel["The Ring Thing (2004) [XVID]"]["scout"], {"state": "read", "source": "description", "langs": ["Swiss German"], "lines": []})

    def test_reading_on_demand_queues_a_request_for_the_runner(self):
        out, rel = self.releases()
        n = rel["The Ring Thing (2004) [XVID]"]["n"]
        self.assertTrue(rel["The Ring Thing (2004) [XVID]"]["readable"])
        status, res = self.call("POST", "action", {"id": self.pid, "action": "describe", "release": n, "token": out["token"]})
        self.assertEqual(status, 200, res)
        (f,) = list(self.req.glob("*.req"))
        self.assertRegex(f.name, r"^[0-9a-f]{16}\.req$")
        req = json.loads(f.read_text())
        self.assertEqual((req["indexer"], req["protocol"], req["infoUrl"], req["guid"][:20]), ("The Pirate Bay (Prowlarr)", "torrent", "https://thepiratebay.org/description.php?id=3872789", "magnet:?xt=urn:btih:"))
        self.assertNotIn("apikey", f.read_text())
        _, rel = self.releases()
        self.assertEqual(rel["The Ring Thing (2004) [XVID]"]["scout"], {"state": "pending"})
        f.unlink()
        f.with_name(f.name[:-4] + ".result.json").write_text(json.dumps({"state": "refused", "why": "daily limit reached, try tomorrow"}))
        _, rel = self.releases()
        self.assertEqual(rel["The Ring Thing (2004) [XVID]"]["scout"], {"state": "refused", "why": "daily limit reached, try tomorrow"})

    def test_an_unreadable_release_or_a_stale_list_cannot_be_asked_for(self):
        out, rel = self.releases()
        self.assertEqual(self.call("POST", "action", {"id": self.pid, "action": "describe", "release": rel["Other"]["n"], "token": out["token"]})[0], 400)
        self.assertEqual(self.call("POST", "action", {"id": self.pid, "action": "describe", "release": 0, "token": "nope"})[0], 400)
        self.assertEqual(list(self.req.iterdir()), [])

    def test_reads_do_not_count_as_fixes_and_have_their_own_limit(self):
        out, rel = self.releases()
        for title in ("The.Ring.Thing.2004.MULTi.720p", "The Ring Thing (2004) [XVID]"):
            self.assertEqual(self.call("POST", "action", {"id": self.pid, "action": "describe", "release": rel[title]["n"], "token": out["token"]})[0], 200)
        self.assertNotIn(("act", self.pid), manual.THROTTLE)
        self.addCleanup(setattr, manual, "READS_PER_USER", manual.READS_PER_USER)
        manual.READS_PER_USER = 2
        self.w.releases.append(dict(self.w.releases[0], guid="https://nzbgeek.info/geekseek.php?guid=" + "c" * 32, title="Third.MULTi"))
        manual.CACHE.clear()
        manual.OFFERS.clear()
        manual.SEARCHES.clear()
        out, rel = self.releases()
        self.assertEqual(self.call("POST", "action", {"id": self.pid, "action": "describe", "release": rel["Third.MULTi"]["n"], "token": out["token"]}), (429, {"error": "too many reads"}))


SEEDLESS = {"guid": "https://tracker.example.com/dl?apikey=secret&id=7", "indexerId": 3, "title": "The Ring Thing (2004) [XVID]", "size": 700_000_000, "quality": {"quality": {"name": "DVD"}},
            "languages": [{"name": "German"}], "seeders": 0, "leechers": 0, "customFormatScore": 0, "rejected": True,
            "rejections": ["Does not contain one of the required terms: Mundart", "Not enough seeders: 0. Minimum seeders: 1"], "indexer": "Tracker", "protocol": "torrent", "age": 900, "mappedMovieId": 21}
STANDARD = dict(SEEDLESS, guid="https://tracker.example.com/dl?apikey=secret&id=8", title="The.Ring.Thing.2004.Mundart.DVDRip", seeders=5, rejected=True,
                rejections=["Release in queue already meets cutoff: DVD"])
LOCAL_4AM = lambda t: time.gmtime(t + 3600)


class Override(unittest.TestCase):
    """Nothing found for 7 days: rejected releases may be downloaded anyway by every requester; a nightly search then looks for a version the profile allows."""

    def world(self, days=10, me=USER):
        w = World(me=me, rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21, created=T - days * DAY)],
                  titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 13}})
        w.releases = [SEEDLESS]
        return w, one(w, "no-release")["id"]

    def setUp(self):
        reset()
        manual.LOCAL = LOCAL_4AM

    def override(self, w, pid, **extra):
        out = get(w, f"releases?id={pid}")[1]
        return post(w, dict({"id": pid, "action": "override", "release": out["releases"][0]["n"], "token": out["token"]}, **extra))

    def test_release_list_offers_the_override_only_after_seven_days(self):
        w, pid = self.world(days=5)
        self.assertFalse(get(w, f"releases?id={pid}")[1]["override"])
        reset()
        w, pid = self.world(days=8)
        self.assertTrue(get(w, f"releases?id={pid}")[1]["override"])

    def test_override_before_seven_days_is_refused(self):
        w, pid = self.world(days=5)
        self.assertEqual(self.override(w, pid), (403, {"error": "too early"}))
        self.assertEqual(w.writes, [])

    def test_every_requester_may_download_a_rejected_release_anyway(self):
        w, pid = self.world(me=OTHER)
        w.tags = [{"id": 4, "label": "download-now"}]
        status, out = self.override(w, pid)
        self.assertEqual(status, 200, out)
        self.assertEqual(w.writes, [("radarr", "POST", "/tag", {"label": "override"}),
                                    ("radarr", "PUT", "/movie/editor", {"movieIds": [21], "tags": [901], "applyTags": "add"}),
                                    ("radarr", "POST", "/release", {"guid": SEEDLESS["guid"], "indexerId": 3})])

    def test_existing_override_tag_is_reused(self):
        w, pid = self.world()
        w.tags = [{"id": 6, "label": "override"}]
        self.assertEqual(self.override(w, pid)[0], 200)
        self.assertEqual(w.writes[0], ("radarr", "PUT", "/movie/editor", {"movieIds": [21], "tags": [6], "applyTags": "add"}))

    def test_override_is_logged_with_the_user_in_the_lasting_list(self):
        w, pid = self.world()
        out = self.override(w, pid)[1]
        rec = out["problem"]
        self.assertEqual(rec["resolved"]["how"], "override")
        self.assertEqual((rec["actions"][-1]["user"], rec["actions"][-1]["action"]), ("jane", "override"))
        o = rec["override"]
        self.assertEqual((o["release"], o["seeders"], o["protocol"], o["user"], o["state"]), ("The Ring Thing (2004) [XVID]", 0, "torrent", "jane", "grabbed"))
        self.assertNotIn("apikey", json.dumps(problems(w)))
        stored = json.loads(w.store.read_text())["problems"][pid]
        self.assertEqual(stored["override"]["state"], "grabbed")

    def test_allowed_release_is_no_override(self):
        w, pid = self.world()
        w.releases = [RELEASES[1]]
        self.assertEqual(self.override(w, pid)[0], 400)
        self.assertEqual(w.writes, [])

    def test_override_needs_the_users_own_fresh_list(self):
        w, pid = self.world()
        out = get(w, f"releases?id={pid}")[1]
        self.assertEqual(post(w, {"id": pid, "action": "override", "release": 0, "token": "nope"})[0], 400)
        w.me = OTHER
        manual.THROTTLE.clear()
        self.assertEqual(post(w, {"id": pid, "action": "override", "release": 0, "token": out["token"]})[0], 400)
        self.assertEqual(w.writes, [])

    def overridden(self):
        w, pid = self.world()
        self.override(w, pid)
        w.writes.clear()
        w.gets.clear()
        w.rs_file.write_text(json.dumps(state()))
        os.utime(w.rs_file, (T, T))
        w.queues["radarr"] = [dict(qrec(31, kind="movie", dl="HASH1", tds="downloading", status="queued", tid=21, tmdb=10248, title="The Ring Thing (2004) [XVID]", msgs=()), trackedDownloadStatus="ok")]
        manual.CACHE.clear()
        return w, pid

    def stored(self, w, pid):
        return json.loads(w.store.read_text())["problems"][pid]

    def test_override_download_is_tracked_in_the_queue(self):
        w, pid = self.overridden()
        problems(w)
        o = self.stored(w, pid)["override"]
        self.assertEqual((o["state"], o["downloadIds"]), ("downloading", ["hash1"]))

    def test_nightly_search_replaces_a_waiting_override_by_a_version_the_profile_allows(self):
        w, pid = self.overridden()
        problems(w)
        w.releases = [SEEDLESS, STANDARD]
        manual.nightly(dn, w.deps())
        self.assertEqual(w.writes, [("radarr", "DELETE", "/queue/bulk?removeFromClient=true&blocklist=false", {"ids": [31]}),
                                    ("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [21]})])
        o = self.stored(w, pid)["override"]
        self.assertEqual((o["state"], o["lastCheck"]), ("replacing", manual.ts(T)))

    def test_nightly_search_keeps_the_override_while_nothing_better_exists(self):
        w, pid = self.overridden()
        problems(w)
        w.releases = [SEEDLESS, dict(STANDARD, rejections=["Release in queue already meets cutoff: DVD", "Not enough seeders: 0. Minimum seeders: 1"])]
        manual.nightly(dn, w.deps())
        self.assertEqual(w.writes, [])
        self.assertIn(("radarr", "/release?movieId=21"), w.gets)
        self.assertEqual(self.stored(w, pid)["override"]["lastCheck"], manual.ts(T))

    def test_nightly_search_runs_once_a_night_inside_the_window(self):
        w, pid = self.overridden()
        problems(w)
        manual.LOCAL = lambda t: time.gmtime(t + 7 * 3600)
        manual.nightly(dn, w.deps())
        self.assertFalse(any(g[1].startswith("/release") for g in w.gets))
        manual.LOCAL = LOCAL_4AM
        manual.nightly(dn, w.deps())
        manual.nightly(dn, w.deps())
        self.assertEqual(sum(1 for g in w.gets if g[1].startswith("/release")), 1)

    def test_finished_override_gets_a_normal_search_every_night(self):
        w, pid = self.overridden()
        problems(w)
        w.queues["radarr"] = []
        manual.CACHE.clear()
        manual.NOW = lambda: T + 2000
        os.utime(w.rs_file, (T + 2000, T + 2000))
        problems(w)
        self.assertEqual(self.stored(w, pid)["override"]["state"], "finished")
        w.history = [{"eventType": "downloadFolderImported", "downloadId": "HASH1", "date": iso(T + 1000), "movieId": 21}]
        manual.nightly(dn, w.deps())
        self.assertEqual(w.writes, [("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [21]})])

    def test_import_of_another_download_ends_the_nightly_search(self):
        w, pid = self.overridden()
        problems(w)
        w.queues["radarr"] = []
        w.tags = [{"id": 6, "label": "override"}]
        manual.CACHE.clear()
        manual.NOW = lambda: T + 2000
        os.utime(w.rs_file, (T + 2000, T + 2000))
        problems(w)
        w.history = [{"eventType": "downloadFolderImported", "downloadId": "HASH1", "date": iso(T + 1000), "movieId": 21},
                     {"eventType": "downloadFolderImported", "downloadId": "HASH2", "date": iso(T + 1500), "movieId": 21, "sourceTitle": "The.Ring.Thing.2004.Mundart.DVDRip"}]
        manual.nightly(dn, w.deps())
        self.assertEqual(w.writes, [("radarr", "PUT", "/movie/editor", {"movieIds": [21], "tags": [6], "applyTags": "remove"})])
        self.assertEqual(self.stored(w, pid)["override"]["state"], "replaced")

    def test_an_open_override_is_never_dropped_from_the_list(self):
        w, pid = self.overridden()
        problems(w)
        later = T + 200 * DAY
        manual.NOW = lambda: later
        manual.CACHE.clear()
        os.utime(w.rs_file, (later, later))
        problems(w)
        self.assertIn(pid, json.loads(w.store.read_text())["problems"])

    def test_downloading_override_offers_block_blocks_it_and_ends_the_nightly_search(self):
        w, pid = self.overridden()
        w.tags = [{"id": 6, "label": "override"}]
        done = next(p for p in problems(w)["done"] if p["id"] == pid)
        self.assertEqual(done["offers"], ["block"])
        manual.THROTTLE.clear()
        status, out = post(w, {"id": pid, "action": "block"})
        self.assertEqual(status, 200, out)
        self.assertEqual(w.writes, [("radarr", "DELETE", "/queue/bulk?removeFromClient=true&blocklist=true", {"ids": [31]}),
                                    ("radarr", "PUT", "/movie/editor", {"movieIds": [21], "tags": [6], "applyTags": "remove"}),
                                    ("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [21]})])
        rec = self.stored(w, pid)
        self.assertEqual((rec["resolved"]["how"], rec["override"]["state"], rec["actions"][-1]["user"]), ("block", "blocked", "jane"))
        w.writes.clear()
        manual.nightly(dn, w.deps())
        self.assertEqual(w.writes, [])

    def test_finished_override_has_no_block(self):
        w, pid = self.overridden()
        problems(w)
        w.queues["radarr"] = []
        manual.CACHE.clear()
        manual.NOW = lambda: T + 2000
        os.utime(w.rs_file, (T + 2000, T + 2000))
        done = next(p for p in problems(w)["done"] if p["id"] == pid)
        self.assertEqual(done["offers"], [])
        self.assertEqual(post(w, {"id": pid, "action": "block"})[0], 404)
        self.assertEqual(w.writes, [])


TANK = {"id": 43, "title": "The Tank", "year": 2025, "tmdbId": 1252037, "originalTitle": "Der Tiger",
        "alternateTitles": [{"title": "Тигр"}, {"title": "猛虎战车"}, {"title": "der tiger"}, {"title": "The Tank"}, {"title": "猛虎末路"}, {"title": "Der Tiger"},
                            {"title": "Tiger"}, {"title": "Le Tigre"}, {"title": "La Tigre"}]}


def tank_world(release="The.Tiger.2025.2160p.AMZN.WEB-DL.DUAL.DDP5.1.Atmos.HDR.H.265-FLUX", **movie):
    r = qrec(31, kind="movie", title=release, year=2025, tid=43, tmdb=1252037, msgs=("Found matching movie via grab history, but release was matched to movie by ID.",))
    r["movie"] = dict(TANK, **movie)
    return World(rqueue=[r], requests=[request(11, "movie", 1252037, ext=43)])


class WrongRelease(unittest.TestCase):
    """Every card with a concrete download may block that release and search again (same action as the wrong film)."""

    def setUp(self):
        reset()

    def offered(self, w, kind):
        return one(w, kind)["offers"]

    def test_block_is_offered_exactly_where_there_is_a_download(self):
        cases = {
            "import": World(squeue=[qrec(1, ep=501)]),
            "wrong-film": World(rqueue=[qrec(31, kind="movie", title="Some.Film.1987.1080p-GRP", year=2024, tid=43, tmdb=673, msgs=("matched to movie by ID",))]),
            "failed": World(squeue=[qrec(1, tds="failed", status="failed", ep=501, msgs=("failed",))]),
        }
        for kind, w in cases.items():
            reset()
            self.assertIn("block", self.offered(w, kind), kind)
        without = {
            "removed": World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)]),
            "unmatched": World(rs=state(queue={"tv/4040": {"state": "problem", "message": "", "code": "seerr"}}), requests=[request(13, "tv", 4040, status=4, tvdb=9090, seasons=(1,))]),
            "no-release": World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                                titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 7}}),
        }
        for kind, w in without.items():
            reset()
            self.assertNotIn("block", self.offered(w, kind), kind)
            manual.THROTTLE.clear()
            self.assertEqual(post(w, {"id": one(w, kind)["id"], "action": "block"})[0], 400, kind)
            self.assertEqual(w.writes, [], kind)

    def test_admin_only_actions_are_offered_to_admins_only(self):
        w = World(rs=state(removed=["movie/10248"]), requests=[request(11, "movie", 10248, ext=21)])
        self.assertEqual(self.offered(w, "removed"), ["readd"])
        w.me = ADMIN
        self.assertEqual(self.offered(w, "removed"), ["delete", "readd"])

    def test_not_importable_release_is_removed_blocklisted_and_searched_again(self):
        w = World(squeue=[qrec(1, ep=501), qrec(2, ep=502)], requests=[request(13, "tv", 4040, tvdb=9090, seasons=(1,))])
        pid = one(w, "import")["id"]
        status, out = post(w, {"id": pid, "action": "block"})
        self.assertEqual(status, 200, out)
        self.assertEqual(w.writes, [("sonarr", "DELETE", "/queue/bulk?removeFromClient=true&blocklist=true", {"ids": [1, 2]}),
                                    ("sonarr", "POST", "/command", {"name": "EpisodeSearch", "episodeIds": [501, 502]})])
        rec = out["problem"]
        self.assertEqual(rec["resolved"]["how"], "block")
        self.assertEqual((rec["actions"][-1]["user"], rec["actions"][-1]["action"]), ("jane", "block"))
        self.assertEqual(json.loads(w.store.read_text())["problems"][pid]["resolved"]["how"], "block")

    def test_every_page_user_may_block_but_only_with_origin_and_not_twice_in_a_row(self):
        w = World(me=OTHER, rqueue=[qrec(31, kind="movie", title="Some.Film.2024.1080p-GRP", year=2024, tid=43, tmdb=673, msgs=(BY_ID,))])
        pid = one(w, "import")["id"]
        self.assertEqual(post(w, {"id": pid, "action": "block"}, {"Origin": "https://evil.example.com"})[0], 403)
        self.assertEqual(w.writes, [])
        self.assertEqual(post(w, {"id": pid, "action": "block"})[0], 200)
        self.assertEqual(post(w, {"id": pid, "action": "block"})[0], 429)
        self.assertEqual(len(w.writes), 2)


class OtherNames(unittest.TestCase):
    """The title's other names on the card, so a foreign-language release name can be recognised."""

    def setUp(self):
        reset()

    def test_names_original_first_deduplicated_without_the_title_at_most_six(self):
        d = one(tank_world(), "import")["details"]
        self.assertEqual(d["names"], ["Der Tiger", "Тигр", "猛虎战车", "猛虎末路", "Tiger", "Le Tigre"])
        self.assertIsNone(d["nameMatch"])

    def test_release_named_like_the_original_title_says_so(self):
        d = one(tank_world("Der.Tiger.2025.German.DL.2160p.WEB.H265-GRP"), "import")["details"]
        self.assertEqual(d["nameMatch"], {"name": "Der Tiger", "original": True})

    def test_release_named_like_another_name(self):
        d = one(tank_world("Le Tigre (2025) 1080p BluRay x264"), "import")["details"]
        self.assertEqual(d["nameMatch"], {"name": "Le Tigre", "original": False})

    def test_release_named_like_the_title_itself_needs_no_line(self):
        self.assertIsNone(one(tank_world("The.Tank.2025.1080p.WEB-DL-GRP"), "import")["details"]["nameMatch"])

    def test_no_other_names(self):
        d = one(tank_world(originalTitle="The Tank", alternateTitles=[]), "import")["details"]
        self.assertEqual(d["names"], [])

    def test_series_names_from_sonarr(self):
        r = qrec(1, ep=501, title="Haus.des.Geldes.S01E01.German.1080p-GRP")
        r["series"]["alternateTitles"] = [{"title": "La Casa de Papel", "seasonNumber": -1}, {"title": "Haus des Geldes", "seasonNumber": -1}, {"title": "Company Retreat"}]
        d = one(World(squeue=[r]), "import")["details"]
        self.assertEqual(d["names"], ["La Casa de Papel", "Haus des Geldes"])
        self.assertEqual(d["nameMatch"], {"name": "Haus des Geldes", "original": False})

    def test_nothing_found_card_has_the_names_too(self):
        w = World(rs=state(noRelease={"movie/10248": "2026-11-14T04:17:00Z"}), requests=[request(11, "movie", 10248, ext=21)],
                  titles={"/movie/21": {"id": 21, "title": "The Ring Thing", "year": 2004, "tmdbId": 10248, "qualityProfileId": 7, "originalTitle": "Das Ring-Ding", "alternateTitles": [{"title": "Ringding"}]}})
        self.assertEqual(one(w, "no-release")["details"]["names"], ["Das Ring-Ding", "Ringding"])

    def test_names_are_kept_with_the_problem(self):
        w = tank_world()
        pid = one(w, "import")["id"]
        self.assertEqual(json.loads(w.store.read_text())["problems"][pid]["details"]["names"][0], "Der Tiger")


class ImportedByHand(unittest.TestCase):
    """Radarr/Sonarr keep a queue entry imported by hand as importBlocked: the problem is solved, the leftover entry goes (never blocklisted, client files left)."""

    def setUp(self):
        reset()
        self.w = World(rqueue=[qrec(31, kind="movie", title="The.Tiger.2025.2160p-FLUX", year=2025, tid=43, tmdb=1252037, msgs=(BY_ID,))],
                       titles={"/movie/43": {"id": 43, "title": "The Tank", "hasFile": False}})
        self.pid = one(self.w, "import")["id"]

    def later(self, s):
        manual.NOW = lambda: T + s
        os.utime(self.w.rs_file, (T + s, T + s))
        manual.CACHE.clear()

    def imported(self, at=T + 30, event="downloadFolderImported"):
        self.w.titles["/movie/43"]["hasFile"] = True
        self.w.history = [{"eventType": "grabbed", "downloadId": "DL1", "date": iso(T - DAY)}, {"eventType": event, "downloadId": "DL1", "date": iso(at)}]

    def stored(self):
        return json.loads(self.w.store.read_text())["problems"][self.pid]

    def test_imported_then_queue_still_import_blocked_is_resolved_and_never_reopens(self):
        self.imported()
        self.later(60)
        problems(self.w)
        self.assertEqual(self.stored()["resolved"]["how"], "imported")
        self.assertEqual(self.w.writes, [("radarr", "DELETE", "/queue/31?removeFromClient=true&blocklist=false&skipRedownload=true", None)])
        self.later(3 * 3600)
        out = problems(self.w)
        self.assertEqual(out["open"], [])
        r = self.stored()
        self.assertEqual((r["resolved"]["how"], [a["action"] for a in r["actions"]]), ("imported", []))

    def test_import_from_the_page_stays_done_after_the_grace_time(self):
        self.w.proposals = [{"path": "/data/torrents/radarr/a/film.mkv", "relativePath": "film.mkv", "size": 9, "quality": {}, "languages": [], "episodes": [],
                             "rejections": [{"reason": "matched to movie by ID"}], "downloadId": "DL1", "movieId": 43}]
        self.w.scanned("/data/torrents/radarr/a/film.mkv")
        get(self.w, f"files?id={self.pid}")
        self.assertEqual(post(self.w, {"id": self.pid, "action": "import", "files": [{"n": 0, "episodeIds": []}]})[0], 200)
        self.imported()
        self.later(3 * 3600)
        problems(self.w)
        r = self.stored()
        self.assertEqual((r["resolved"]["how"], [a["action"] for a in r["actions"]]), ("import", ["import"]))
        self.assertIn(("radarr", "DELETE", "/queue/31?removeFromClient=true&blocklist=false&skipRedownload=true", None), self.w.writes)

    def test_movie_file_imported_counts_too(self):
        self.imported(event="movieFileImported")
        self.later(60)
        problems(self.w)
        self.assertEqual(self.stored()["resolved"]["how"], "imported")

    def test_an_import_from_before_the_problem_does_not_count(self):
        self.imported(at=T - 3600)
        self.later(60)
        self.assertEqual(one(self.w, "import")["id"], self.pid)
        self.assertEqual(self.w.writes, [])

    def test_without_the_file_in_the_library_it_stays_open(self):
        self.imported()
        self.w.titles["/movie/43"]["hasFile"] = False
        self.later(60)
        self.assertEqual(one(self.w, "import")["id"], self.pid)
        self.assertEqual(self.w.writes, [])

    def test_series_needs_every_episode_of_the_download_on_disk(self):
        reset()
        w = World(squeue=[qrec(1, ep=501), qrec(2, ep=502)])
        pid = one(w, "import")["id"]
        w.history = [{"eventType": "downloadFolderImported", "downloadId": "DL1", "date": iso(T + 30)}]
        w.episodes = [{"id": 501, "hasFile": True}, {"id": 502, "hasFile": False}]
        manual.NOW = lambda: T + 60
        manual.CACHE.clear()
        self.assertEqual(one(w, "import")["id"], pid)
        self.assertEqual(w.writes, [])
        w.episodes[1]["hasFile"] = True
        manual.CACHE.clear()
        self.assertEqual(problems(w)["open"], [])
        self.assertEqual(w.writes, [("sonarr", "DELETE", "/queue/1?removeFromClient=true&blocklist=false&skipRedownload=true", None),
                                    ("sonarr", "DELETE", "/queue/2?removeFromClient=true&blocklist=false&skipRedownload=true", None)])



TIGER = "The.Tiger.2025.2160p.AMZN.WEB-DL.DUAL.DDP5.1.Atmos.HDR.H.265-FLUX"
LOWER = "The.Tank.2025.German.DL.1080p.WEB.H264-GRP"
NOT_UPGRADE = "Not an upgrade for existing movie file. Existing quality: WEBDL-2160p. New Quality WEBDL-1080p."
REPLACE_RELEASES = [
    {"guid": "https://indexer.example.com/details/aaa", "indexerId": 4, "title": TIGER, "size": 20_000_000_000, "quality": {"quality": {"name": "WEBDL-2160p"}}, "customFormatScore": 900,
     "rejected": True, "rejections": ["Existing file on disk is of equal or higher preference: WEBDL-2160p"], "indexer": "NZBgeek", "protocol": "usenet", "mappedMovieId": 43},
    {"guid": "https://indexer.example.com/details/bbb", "indexerId": 4, "title": LOWER, "size": 6_000_000_000, "quality": {"quality": {"name": "WEBDL-1080p"}}, "customFormatScore": 500,
     "rejected": True, "rejections": ["Existing file on disk is of equal or higher preference: WEBDL-2160p"], "indexer": "NZBgeek", "protocol": "usenet", "mappedMovieId": 43},
    {"guid": "https://indexer.example.com/details/ccc", "indexerId": 4, "title": "The.Tank.2025.TELESYNC.x264-BAD", "size": 1_000_000_000, "quality": {"quality": {"name": "TELESYNC"}},
     "customFormatScore": -500, "rejected": True, "rejections": ["Custom Formats score of -500 is below the minimum of 0", "Existing file on disk is of equal or higher preference: WEBDL-2160p"],
     "indexer": "NZBgeek", "protocol": "usenet", "mappedMovieId": 43}]
NEW_FILE = "/data/usenet/complete/movies/The.Tank.2025.German.DL.1080p/the.tank.mkv"


def tank_done(me=USER):
    """The Tank: an import problem solved, its file from a known grab (history: grabbed + imported with the file's id)."""
    w = tank_world(TIGER)
    pid = one(w, "import")["id"]
    w.titles["/movie/43"] = dict(TANK, hasFile=True, runtime=95, qualityProfileId=7, movieFile={"id": 159, "quality": {"quality": {"id": 19, "name": "WEBDL-2160p"}}})
    w.seerr_media["/movie/1252037"] = {"id": 1252037, "title": "The Tank", "mediaInfo": {"externalServiceId": 43}}
    w.history = [{"id": 451, "eventType": "downloadFolderImported", "date": iso(T - 3600), "downloadId": "DL1", "sourceTitle": TIGER, "data": {"fileId": "159"}},
                 {"id": 400, "eventType": "grabbed", "date": iso(T - 7200), "downloadId": "DL1", "sourceTitle": TIGER, "data": {"indexer": "NZBgeek (Prowlarr)", "guid": "https://indexer.example.com/details/aaa"}}]
    w.queues["radarr"] = []
    manual.CACHE.clear()
    problems(w)
    w.me = me
    w.releases = [dict(x) for x in REPLACE_RELEASES]
    return w, pid


def done_rec(w, pid):
    return next(r for r in problems(w)["done"] if r["id"] == pid)


def open_rec(w, pid):
    return next(r for r in problems(w)["open"] if r["id"] == pid)


def stored(w, pid):
    return json.loads(w.store.read_text())["problems"][pid]


class ReleaseActions(unittest.TestCase):
    """Done entries whose file came from a known release: pick another release (replaces it the way Radarr/Sonarr upgrade) or block it (markAsFailed of its grab)."""

    def setUp(self):
        reset()

    def later(self, w, s):
        manual.NOW = lambda: T + s
        os.utime(w.rs_file, (T + s, T + s))
        manual.CACHE.clear()
        manual.THROTTLE.clear()

    def pick(self, w, pid, title=LOWER, **extra):
        out = get(w, f"releases?id={pid}")[1]
        n = next(x["n"] for x in out["releases"] if x["title"] == title)
        return post(w, dict({"id": pid, "action": "replace", "release": n, "token": out["token"]}, **extra))

    def reject(self, w, pid, **extra):
        return post(w, dict({"id": pid, "action": "reject", "confirm": "reject", "ref": done_rec(w, pid)["current"]["ref"]}, **extra))

    def test_done_entry_with_a_known_release_offers_both_actions_to_every_user(self):
        w, pid = tank_done(me=OTHER)
        r = done_rec(w, pid)
        self.assertEqual(r["offers"], ["reject", "replace"])
        self.assertEqual((r["current"]["release"], r["current"]["quality"]), (TIGER, "WEBDL-2160p"))
        self.assertFalse({"grabId", "downloadId", "fileIds"} & set(r["current"]))
        self.assertNotIn("indexer.example.com", json.dumps(problems(w)))

    def test_no_known_release_no_actions(self):
        w, pid = tank_done()
        w.history = [h for h in w.history if h["eventType"] != "grabbed"]
        self.later(w, 2 * 3600)
        self.assertEqual(done_rec(w, pid)["offers"], [])
        self.assertEqual(post(w, {"id": pid, "action": "reject", "confirm": "reject", "ref": "x"})[0], 404)
        reset()
        w, pid = tank_done()
        w.titles["/movie/43"]["movieFile"]["id"] = 170
        self.later(w, 2 * 3600)
        self.assertEqual(done_rec(w, pid)["offers"], [])
        self.assertEqual(w.writes, [])

    def test_release_list_marks_the_current_release_and_picks_what_only_the_current_file_rejects(self):
        w, pid = tank_done()
        status, out = get(w, f"releases?id={pid}")
        self.assertEqual(status, 200, out)
        self.assertIn(("radarr", "/release?movieId=43"), w.gets)
        rows = {x["title"]: x for x in out["releases"]}
        self.assertTrue(rows[TIGER]["current"] and rows[TIGER]["rejected"])
        self.assertFalse(rows[LOWER]["rejected"])
        self.assertTrue(rows["The.Tank.2025.TELESYNC.x264-BAD"]["rejected"])
        self.assertFalse(out["override"])
        self.assertNotIn("details/bbb", json.dumps(out))

    def test_pick_grabs_the_release_and_moves_the_entry_back_to_open_as_replacing(self):
        w, pid = tank_done(me=OTHER)
        status, out = self.pick(w, pid)
        self.assertEqual(status, 200, out)
        self.assertEqual(w.writes, [("radarr", "POST", "/release", {"guid": "https://indexer.example.com/details/bbb", "indexerId": 4})])
        rec = open_rec(w, pid)
        self.assertEqual((rec["replace"]["mode"], rec["replace"]["release"], rec["replace"]["from"], rec["replace"]["user"]), ("replace", LOWER, TIGER, "joe"))
        self.assertEqual(rec["offers"], [])
        self.assertEqual((rec["actions"][-1]["user"], rec["actions"][-1]["action"]), ("joe", "replace"))
        self.assertEqual(problems(w)["done"], [])

    def test_the_current_and_rejected_releases_need_more_than_a_pick(self):
        w, pid = tank_done()
        self.assertEqual(self.pick(w, pid, title=TIGER)[0], 400)
        manual.THROTTLE.clear()
        self.assertEqual(self.pick(w, pid, title="The.Tank.2025.TELESYNC.x264-BAD"), (403, {"error": "too early"}))
        self.assertEqual(w.writes, [])
        self.later(w, 8 * DAY)
        self.assertTrue(get(w, f"releases?id={pid}&refresh=1")[1]["override"])
        self.assertEqual(self.pick(w, pid, title="The.Tank.2025.TELESYNC.x264-BAD")[0], 200)

    def test_pick_needs_the_callers_own_list_of_this_title_and_the_origin(self):
        w, pid = tank_done()
        out = get(w, f"releases?id={pid}")[1]
        self.assertEqual(out["releases"][0]["title"], LOWER)
        self.assertEqual(post(w, {"id": pid, "action": "replace", "release": 0, "token": out["token"]}, {"Origin": "https://evil.example.com"})[0], 403)
        self.assertEqual(post(w, {"id": pid, "action": "replace", "release": 0, "token": "nope"})[0], 400)
        manual.THROTTLE.clear()
        w.me = OTHER
        self.assertEqual(post(w, {"id": pid, "action": "replace", "release": 0, "token": out["token"]})[0], 400)
        manual.THROTTLE.clear()
        w.me = USER
        manual.OFFERS[(pid, "releases", USER["id"])][2]["arrId"] = 99
        self.assertEqual(post(w, {"id": pid, "action": "replace", "release": 0, "token": out["token"]})[0], 400)
        self.assertEqual(w.writes, [])

    def replacing(self, verdict="queued"):
        w, pid = tank_done()
        self.pick(w, pid)
        w.queues["radarr"] = [dict(qrec(32, kind="movie", dl="DLN", title=LOWER, year=2025, tid=43, tmdb=1252037, msgs=(NOT_UPGRADE,)), movie=TANK)]
        w.proposals = [{"path": NEW_FILE, "relativePath": "the.tank.mkv", "size": 1, "quality": {"quality": {"id": 3, "name": "WEBDL-1080p"}}, "languages": [{"id": 4, "name": "German"}],
                        "episodes": [], "rejections": [{"reason": NOT_UPGRADE}], "downloadId": "DLN", "movieId": 43}]
        w.scanned(NEW_FILE, verdict=verdict)
        w.writes.clear()
        self.later(w, 600)
        return w, pid

    def test_a_not_better_pick_waits_for_the_virus_scan_and_never_shows_as_an_import_problem(self):
        w, pid = self.replacing("queued")
        out = problems(w)
        self.assertEqual([r["id"] for r in out["open"]], [pid])
        self.assertEqual(w.writes, [])

    def test_a_clean_not_better_pick_is_imported_over_the_old_file_never_deleted_first(self):
        w, pid = self.replacing("clean")
        problems(w)
        self.assertEqual(len(w.writes), 1)
        app, method, path, body = w.writes[0]
        self.assertEqual((app, method, path, body["name"], body["importMode"]), ("radarr", "POST", "/command", "ManualImport", "auto"))
        self.assertEqual([(f["path"], f["movieId"], f["downloadId"]) for f in body["files"]], [(NEW_FILE, 43, "DLN")])
        self.later(w, 700)
        problems(w)
        self.assertEqual(len(w.writes), 1)
        self.assertFalse(any("/moviefile" in x[2] or "/episodefile" in x[2] for x in w.writes))
        self.assertIsNone(stored(w, pid)["resolved"])

    def test_replacing_ends_when_the_picked_release_is_imported(self):
        w, pid = self.replacing("clean")
        problems(w)
        w.queues["radarr"] = []
        w.titles["/movie/43"]["movieFile"] = {"id": 170, "quality": {"quality": {"id": 3, "name": "WEBDL-1080p"}}}
        w.history = [{"id": 471, "eventType": "downloadFolderImported", "date": iso(T + 900), "downloadId": "DLN", "sourceTitle": LOWER, "data": {"fileId": "170"}},
                     {"id": 460, "eventType": "grabbed", "date": iso(T + 5), "downloadId": "DLN", "sourceTitle": LOWER, "data": {}}] + w.history
        self.later(w, 1200)
        r = done_rec(w, pid)
        self.assertEqual(r["resolved"]["how"], "replaced")
        self.assertEqual(r["actions"][-1]["action"], "replaced")
        self.assertEqual(r["current"]["release"], LOWER)
        self.assertEqual(stored(w, pid)["replace"]["state"], "done")

    def test_a_pick_imported_between_two_scans_still_counts(self):
        w, pid = tank_done()
        self.pick(w, pid)
        w.titles["/movie/43"]["movieFile"] = {"id": 170, "quality": {"quality": {"id": 3, "name": "WEBDL-1080p"}}}
        w.history = [{"id": 471, "eventType": "downloadFolderImported", "date": iso(T + 900), "downloadId": "DLN", "sourceTitle": LOWER, "data": {"fileId": "170"}},
                     {"id": 460, "eventType": "grabbed", "date": iso(T + 5), "downloadId": "DLN", "sourceTitle": LOWER, "data": {}}] + w.history
        self.later(w, 3600)
        self.assertEqual(done_rec(w, pid)["resolved"]["how"], "replaced")

    def test_malware_in_the_pick_is_never_imported_the_old_file_stays(self):
        w, pid = self.replacing("infected")
        problems(w)
        self.assertEqual(w.writes, [])
        self.assertEqual(stored(w, pid)["resolved"]["how"], "replace-failed")
        self.later(w, 700)
        self.assertEqual([r["type"] for r in problems(w)["open"]], ["import"])

    def test_block_marks_its_grab_failed_keeps_the_file_and_searches(self):
        w, pid = tank_done(me=OTHER)
        status, out = self.reject(w, pid)
        self.assertEqual(status, 200, out)
        self.assertEqual(w.writes, [("radarr", "POST", "/history/failed/400", None), ("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [43]})])
        rec = open_rec(w, pid)
        self.assertEqual((rec["replace"]["mode"], rec["replace"]["keep"], rec["replace"]["from"]), ("reject", True, TIGER))
        self.assertEqual((rec["actions"][-1]["user"], rec["actions"][-1]["action"]), ("joe", "reject"))

    def test_block_may_delete_the_current_file_when_asked(self):
        w, pid = tank_done()
        w.retained("movie/1252037", False)
        self.assertEqual(self.reject(w, pid, keep=False)[0], 200)
        self.assertEqual(w.writes, [("radarr", "POST", "/history/failed/400", None), ("radarr", "DELETE", "/moviefile/159", None),
                                    ("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [43]})])
        self.assertFalse(open_rec(w, pid)["replace"]["keep"])

    def test_block_never_deletes_the_file_of_a_kept_forever_title(self):
        for default, keep in ((True, None), (False, True)):
            manual.THROTTLE.clear()
            w, pid = tank_done()
            w.retained("movie/1252037", default)
            if keep:
                w.retention_keep.write_text(json.dumps({"titles": {"movie/1252037": {"keep": True}}}))
            self.assertEqual(self.reject(w, pid, keep=False), (403, {"error": "kept forever"}))
            self.assertEqual(w.writes, [])
            manual.THROTTLE.clear()
            self.assertEqual(self.reject(w, pid)[0], 200)
            self.assertNotIn(("radarr", "DELETE", "/moviefile/159", None), w.writes)

    def test_block_deletes_no_file_without_retention_data(self):
        w, pid = tank_done()
        self.assertEqual(self.reject(w, pid, keep=False), (403, {"error": "kept forever"}))
        self.assertEqual(w.writes, [])

    def test_block_needs_the_confirmation_the_release_seen_and_not_twice_in_a_row(self):
        w, pid = tank_done()
        self.assertEqual(post(w, {"id": pid, "action": "reject", "ref": done_rec(w, pid)["current"]["ref"]})[0], 400)
        self.assertEqual(post(w, {"id": pid, "action": "reject", "confirm": "reject", "ref": "0123456789ab"}), (409, {"error": "release changed"}))
        self.assertEqual(w.writes, [])
        manual.THROTTLE.clear()
        self.assertEqual(self.reject(w, pid)[0], 200)
        self.assertEqual(post(w, {"id": pid, "action": "replace", "release": 0})[0], 404)

    def test_blocked_entry_is_done_again_when_another_release_is_imported(self):
        w, pid = tank_done()
        self.reject(w, pid)
        self.later(w, 600)
        self.assertEqual(open_rec(w, pid)["replace"]["state"], "searching")
        w.history = [{"id": 480, "eventType": "downloadFolderImported", "date": iso(T + 900), "downloadId": "DL9", "sourceTitle": "Other", "data": {"fileId": "171"}}] + w.history
        self.later(w, 1200)
        problems(w)
        self.assertEqual(stored(w, pid)["resolved"]["how"], "replaced")

    def test_series_block_fails_the_grab_of_its_episodes_and_searches_them(self):
        w = World(squeue=[qrec(1, dl="DL9", ep=2045, tid=40, tmdb=319013, title="Show.S01E09.1080p-Full3R")], requests=[request(13, "tv", 312697, ext=40, seasons=(1,))])
        pid = one(w, "import")["id"]
        w.seerr_media["/tv/312697"] = {"id": 312697, "name": "Company Retreat", "mediaInfo": {"externalServiceId": 40}}
        w.titles["/series/40"] = {"id": 40, "title": "Company Retreat", "tmdbId": 319013}
        w.episodes = [{"id": 2045, "seasonNumber": 1, "episodeNumber": 9, "hasFile": True, "episodeFileId": 1166}, {"id": 2046, "seasonNumber": 1, "episodeNumber": 10, "hasFile": True, "episodeFileId": 1161}]
        w.history = [{"id": 3310, "eventType": "downloadFolderImported", "date": iso(T - 60), "episodeId": 2045, "downloadId": "DL9", "sourceTitle": "Show.S01E09.1080p-Full3R", "data": {"fileId": "1166"}},
                     {"id": 3241, "eventType": "grabbed", "date": iso(T - 3600), "episodeId": 2045, "downloadId": "DL9", "sourceTitle": "Show.S01E09.1080p-Full3R", "data": {}}]
        w.queues["sonarr"] = []
        manual.CACHE.clear()
        r = done_rec(w, pid)
        self.assertEqual((r["offers"], r["current"]["season"]), (["reject", "replace"], 1))
        w.retained(r["key"], False)
        self.assertEqual(post(w, {"id": pid, "action": "reject", "confirm": "reject", "ref": r["current"]["ref"], "keep": False})[0], 200)
        self.assertEqual(w.writes, [("sonarr", "POST", "/history/failed/3241", None), ("sonarr", "DELETE", "/episodefile/1166", None),
                                    ("sonarr", "POST", "/command", {"name": "EpisodeSearch", "episodeIds": [2045]})])


if __name__ == "__main__":
    unittest.main()
