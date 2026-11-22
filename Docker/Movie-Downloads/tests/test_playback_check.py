#!/usr/bin/env python3
"""scripts/playback-check.py: the playback safety net (README "Playback check"): detector on Jellyfin's activity log entries, then the file check; broken → the quality block, healthy → a Manual note. Fakes for Jellyfin, ffprobe and Radarr/Sonarr, invented names.
Usage: python3 tests/test_playback_check.py"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pc", ROOT / "scripts" / "playback-check.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

FILM = "movies/A Film (2020) [tmdbid-4242]/A Film (2020) - [WEBDL-1080p].mkv"
EPISODE = "tv/A Show (2024) [tvdbid-77]/Season 01/A Show (2024) - S01E08 - Pilot.mkv"
NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC, 09:00 in Zurich
TV, WEB = "Living Room TV", "Browser"
FILM_ID, EP_ID = "f" * 32, "e" * 32


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".0000000Z"


def play(item, start, secs, user="u1", device=TV, nid=[0]):
    """The two activity entries of one playback: started at `start`, stopped `secs` later (None = no stop entry yet)."""
    out = []
    for kind, t, verb in (("VideoPlayback", start, "is playing"), ("VideoPlaybackStopped", None if secs is None else start + secs, "has finished playing")):
        if t is None:
            continue
        nid[0] += 1
        out.append({"Id": nid[0], "Name": f"Jane {verb} Some Title on {device}", "Type": kind, "ItemId": item, "Date": iso(t), "UserId": user, "Severity": "Information"})
    return out


def burst(item, first, secs=(10, 20, 30), gap=60, **kw):
    out = []
    for i, s in enumerate(secs):
        out += play(item, first + i * gap, s, **kw)
    return out


class Pairing(unittest.TestCase):
    def test_start_and_stop_make_one_play_with_its_length(self):
        [p] = pc.plays(play(FILM_ID, NOW, 25))
        self.assertEqual((p["item"], p["start"], p["stop"], p["device"]), (FILM_ID, NOW, NOW + 25, TV))

    def test_a_start_without_a_stop_has_no_stop(self):
        [p] = pc.plays(play(FILM_ID, NOW, None))
        self.assertIsNone(p["stop"])

    def test_same_item_on_two_devices_is_paired_per_device(self):
        ev = play(FILM_ID, NOW, 100) + play(FILM_ID, NOW + 5, 10, device=WEB)
        got = {p["device"]: p["stop"] - p["start"] for p in pc.plays(ev)}
        self.assertEqual(got, {TV: 100, WEB: 10})

    def test_other_entry_types_and_odd_entries_are_ignored(self):
        ev = [{"Id": 1, "Type": "SessionStarted", "Date": iso(NOW), "Name": "x"}, {"Id": 2, "Type": "VideoPlayback", "Date": "garbage", "ItemId": FILM_ID, "UserId": "u", "Name": "a on b"},
              {"Id": 3, "Type": "VideoPlayback", "Date": iso(NOW)}]
        self.assertEqual(pc.plays(ev), [])


class Detector(unittest.TestCase):
    def hits(self, ev):
        return pc.hits(pc.plays(ev))

    def test_three_short_starts_within_15_minutes_are_a_hit(self):
        [h] = self.hits(burst(FILM_ID, NOW))
        self.assertEqual((h["item"], h["count"], h["devices"], h["start"], h["last"]), (FILM_ID, 3, [TV], NOW, NOW + 120 + 30))

    def test_two_are_not(self):
        self.assertEqual(self.hits(burst(FILM_ID, NOW, secs=(10, 20))), [])

    def test_a_play_that_ran_longer_than_a_minute_does_not_count(self):
        self.assertEqual(self.hits(burst(FILM_ID, NOW, secs=(10, 61, 20))), [])
        self.assertEqual(len(self.hits(burst(FILM_ID, NOW, secs=(10, 60, 20)))), 1)

    def test_a_start_without_a_stop_does_not_count(self):
        self.assertEqual(self.hits(burst(FILM_ID, NOW, secs=(10, None, 20))), [])

    def test_starts_spread_over_more_than_15_minutes_are_not(self):
        self.assertEqual(self.hits(burst(FILM_ID, NOW, gap=500)), [])
        self.assertEqual(len(self.hits(burst(FILM_ID, NOW, gap=450))), 1)

    def test_a_long_play_between_the_short_ones_does_not_hide_the_hit(self):
        self.assertEqual(len(self.hits(burst(FILM_ID, NOW, secs=(10, 20, 900, 30)))), 1)

    def test_different_items_do_not_combine(self):
        ev = play(FILM_ID, NOW, 10) + play(EP_ID, NOW + 60, 10) + play(FILM_ID, NOW + 120, 10) + play(EP_ID, NOW + 180, 10)
        self.assertEqual(self.hits(ev), [])

    def test_clients_and_users_may_mix(self):
        ev = play(FILM_ID, NOW, 15) + play(FILM_ID, NOW + 40, 30, device=WEB) + play(FILM_ID, NOW + 90, 12, user="u2", device="Phone")
        [h] = self.hits(ev)
        self.assertEqual(h["devices"], ["Living Room TV", "Browser", "Phone"])

    def test_a_long_burst_is_one_hit(self):
        [h] = self.hits(burst(FILM_ID, NOW, secs=(5, 6, 7, 8, 9, 10, 11), gap=30))
        self.assertEqual(h["count"], 7)

    def test_a_second_burst_much_later_is_a_second_hit(self):
        ev = burst(FILM_ID, NOW) + burst(FILM_ID, NOW + 7200)
        self.assertEqual([h["start"] for h in self.hits(ev)], [NOW, NOW + 7200])


class FakeArr:
    def __init__(self, app, files=(), fail=(), tmdb=4242):
        self.app, self.calls, self.files, self.fail, self.tmdb = app, [], files, set(fail), tmdb

    def __call__(self, method, path, query=None, body=None):
        self.calls.append((method, path, query, body))
        if (method, path.split("/")[1]) in self.fail:
            raise pc.lb.ApiError(f"{method} {path}: boom")
        if path == "/history" and (query or {}).get("downloadId"):
            return {"records": [{"id": 601, "eventType": "grabbed", "downloadId": "DL1", "sourceTitle": "A.2020.1080p-GRP"}]}
        if path == "/moviefile":
            return [{"id": 77, "path": "/data/media/" + FILM}]
        full = "/data/media/" + FILM
        if self.app == "radarr":
            if path == "/movie":
                return [{"id": 12, "title": "A Film", "year": 2020, "tmdbId": 4242, "runtime": 120, "tags": [], "hasFile": True, "movieFile": {"id": 77, "path": full}}]
            if path == "/history/movie":
                return [h for h in self.files]
            if path == "/movie/12":
                return {"id": 12, "title": "A Film", "year": 2020, "tmdbId": self.tmdb, "runtime": 120, "tags": []}
        else:
            if path == "/series":
                return [{"id": 5, "title": "A Show", "year": 2024, "tmdbId": 99, "path": "/data/media/tv/A Show (2024) [tvdbid-77]"}]
            if path == "/series/5":
                return {"id": 5, "title": "A Show", "year": 2024, "tmdbId": 99, "runtime": 45, "tags": []}
            if path == "/episodefile":
                return [{"id": 501, "path": "/data/media/" + EPISODE}]
            if path == "/episode" and (query or {}).get("episodeFileId"):
                return [{"id": 31}]
            if path == "/episode":
                return [{"id": 31, "episodeFileId": 501, "runtime": 45}, {"id": 32, "episodeFileId": 0}]
            if path == "/history":
                return {"records": self.files}
            if path.startswith("/episode/"):
                return {"id": 31, "runtime": 45}
        if path == "/history":
            return {"records": [{"id": 601, "eventType": "grabbed", "downloadId": "DL1", "sourceTitle": "A.2020.1080p-GRP"}]}
        if path == "/config/mediamanagement":
            return {"recycleBin": "/data/.recyclebin"}
        if path == "/rootfolder":
            return [{"path": "/data/media/movies"}]
        return {}

    def made(self, method, prefix):
        return [c for c in self.calls if c[0] == method and c[1].startswith(prefix)]


def import_record(path, dl="DL1", app="radarr"):
    r = {"id": 900, "eventType": "downloadFolderImported", "downloadId": dl, "sourceTitle": "A.2020.1080p-GRP", "date": "2027-01-10T08:00:00Z", "data": {"droppedPath": "/data/usenet/x.mkv", "importedPath": path}}
    r.update({"movieId": 12} if app == "radarr" else {"seriesId": 5, "episodeId": 31})
    return r


class World:
    def __init__(self, entries=None, files=(FILM,), imports=True, clock=NOW, action="block", libstate=None, check=None, lock_busy=False, radarr=None):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.host = self.t / "media"
        for rel in files:
            f = self.host / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x" * 100)
        self.entries = burst(FILM_ID, NOW - 400) if entries is None else entries
        self.state, self.libstate = self.t / "state" / "playback-check.json", self.t / "state" / "library-check.json"
        if libstate is not None:
            self.libstate.parent.mkdir(parents=True, exist_ok=True)
            self.libstate.write_text(json.dumps(libstate))
        self.items = {FILM_ID: {"path": "/media/" + FILM, "name": "A Film"}, EP_ID: {"path": "/media/" + EPISODE, "name": "Pilot"}}
        self.radarr = radarr or FakeArr("radarr", [import_record("/data/media/" + FILM)] if imports else [])
        self.sonarr = FakeArr("sonarr")
        self.checked, self.verdict, self.clock, self.lock_busy, self.action = [], check or {"verdict": "ok", "reasons": [], "notes": [], "duration": 7200.0}, clock, lock_busy, action
        self.since = []

    def activity(self, since):
        self.since.append(since)
        return [e for e in self.entries if e["Date"] >= iso(since)]

    def item(self, item_id):
        return self.items.get(item_id)

    def check(self, path):
        self.checked.append(path)
        v = self.verdict
        return v.pop(0) if isinstance(v, list) else v

    @contextlib.contextmanager
    def lock(self):
        if self.lock_busy:
            raise pc.LockBusy("busy")
        yield

    def deps(self):
        return pc.Deps(str(self.state), str(self.libstate), str(self.host), self.activity, self.item, self.check, {"radarr": self.radarr, "sonarr": self.sonarr},
                       {}, lambda: self.clock, self.lock, self.action, None, lambda: None)

    def run(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            state = pc.run(self.deps())
        self.out = out.getvalue()
        return state


class Healthy(unittest.TestCase):
    def test_a_hit_runs_the_file_check_and_leaves_a_manual_note(self):
        w = World()
        state = w.run()
        self.assertEqual(w.checked, ["/data/media/" + FILM])
        [e] = state["entries"]
        self.assertEqual((e["verdict"], e["source"], e["notes"], e["blocked"], e["file"], e["title"], e["key"], e["fileName"]),
                         ("note", "playback", ["file OK, probably the client"], False, "kept", "A Film", "movie/4242", "A Film (2020) - [WEBDL-1080p].mkv"))
        self.assertIn("3 starts", e["note"])
        self.assertIn(TV, e["note"])
        self.assertEqual(state["queue"], [])

    def test_a_healthy_file_never_changes_anything_in_radarr_or_sonarr(self):
        w = World()
        w.run()
        self.assertEqual([c for c in w.radarr.calls + w.sonarr.calls if c[0] != "GET"], [])

    def test_the_same_hit_in_the_next_run_does_nothing_again(self):
        w = World()
        w.run()
        w.clock += 300
        state = w.run()
        self.assertEqual(len(w.checked), 1)
        self.assertEqual(len(state["entries"]), 1)

    def test_a_new_burst_on_the_unchanged_file_within_a_day_is_not_checked_again(self):
        w = World()
        w.run()
        w.entries += burst(FILM_ID, NOW + 1000)
        w.clock = NOW + 1500
        state = w.run()
        self.assertEqual((len(w.checked), len(state["entries"])), (1, 1))

    def test_a_changed_file_is_checked_again(self):
        w = World()
        w.run()
        f = w.host / FILM
        f.write_bytes(b"y" * 200)
        w.entries += burst(FILM_ID, NOW + 1000)
        w.clock = NOW + 1500
        w.run()
        self.assertEqual(len(w.checked), 2)

    def test_no_hit_checks_nothing(self):
        w = World(entries=burst(FILM_ID, NOW - 400, secs=(10, 20)))
        state = w.run()
        self.assertEqual((w.checked, state["entries"], state["queue"]), ([], [], []))

    def test_only_the_recent_log_is_read(self):
        w = World()
        w.run()
        self.assertEqual(w.since, [NOW - pc.LOOKBACK])

    def test_an_episode_gets_the_series_key(self):
        w = World(entries=burst(EP_ID, NOW - 400), files=(FILM, EPISODE))
        [e] = w.run()["entries"]
        self.assertEqual((e["app"], e["key"], e["title"]), ("sonarr", "tv/99", "A Show"))

    def test_an_item_the_apps_do_not_know_still_gets_the_note(self):
        w = World(imports=False)
        w.radarr = FakeArr("radarr", fail={("GET", "movie")})
        [e] = w.run()["entries"]
        self.assertEqual((e["verdict"], e["key"], e["title"]), ("note", None, "A Film"))

    def test_a_cached_library_check_result_is_used_for_the_unchanged_file(self):
        w = World()
        st = os.stat(w.host / FILM)
        w.libstate.parent.mkdir(parents=True, exist_ok=True)
        w.libstate.write_text(json.dumps({"run": {}, "files": {"/data/media/" + FILM: {"size": st.st_size, "mtime": st.st_mtime_ns, "verdict": "ok", "reasons": [], "notes": [], "checkedAt": "2027-01-10T00:00:00Z"}}}))
        [e] = w.run()["entries"]
        self.assertEqual((w.checked, e["verdict"]), ([], "note"))

    def test_a_cached_result_of_a_changed_file_is_not_used(self):
        w = World(libstate={"run": {}, "files": {"/data/media/" + FILM: {"size": 1, "mtime": 1, "verdict": "ok", "reasons": [], "notes": []}}})
        w.run()
        self.assertEqual(len(w.checked), 1)

    def test_an_item_gone_from_jellyfin_or_disk_is_dropped(self):
        w = World(files=())
        state = w.run()
        self.assertEqual((w.checked, state["entries"], state["queue"]), ([], [], []))
        w2 = World()
        w2.items = {}
        self.assertEqual(w2.run()["queue"], [])


class Broken(unittest.TestCase):
    BAD = {"verdict": "broken", "reasons": ["timestamp jump 280.9 s -> 118.0 s"], "notes": [], "duration": 7200.0}

    def test_a_broken_library_file_is_removed_blocklisted_and_searched_again(self):
        w = World(check=self.BAD)
        [e] = w.run()["entries"]
        self.assertEqual((e["verdict"], e["file"], e["blocked"], e["searched"], e["source"]), ("broken", "removed", True, True, "playback"))
        self.assertEqual(len(w.radarr.made("DELETE", "/moviefile/77")), 1)
        self.assertEqual(len(w.radarr.made("POST", "/history/failed/601")), 1)
        self.assertEqual(w.radarr.made("POST", "/command")[-1][3], {"name": "MoviesSearch", "movieIds": [12]})
        self.assertEqual(e["reasons"], ["timestamp jump 280.9 s -> 118.0 s"])

    def test_the_import_log_of_an_upgrade_puts_the_previous_file_back(self):
        w = World(check=self.BAD)
        old = w.t / "recycle" / "old.mkv"
        old.parent.mkdir()
        old.write_text("old")
        deleted = "/data/media/movies/A Film (2020) [tmdbid-4242]/A Film (2020) - old.mkv"
        imports = {"radarr": [{"at": NOW - 60, "id": "12", "path": "/data/media/" + FILM, "fileId": "77", "downloadId": "DL1", "upgrade": True, "deleted": [deleted], "recycled": [str(old)]}]}
        deps = w.deps()._replace(imports=imports, host=lambda p: str(w.t / "recycle" / "old.mkv") if p == str(old) else str(w.host / p.split("/data/media/")[-1]))
        with contextlib.redirect_stdout(io.StringIO()):
            state = pc.run(deps)
        self.assertEqual(state["entries"][0]["file"], "previous restored")

    def test_log_mode_decides_only(self):
        w = World(check=self.BAD, action="log")
        [e] = w.run()["entries"]
        self.assertEqual((e["file"], e["blocked"]), ("log only", False))
        self.assertEqual([c for c in w.radarr.calls if c[0] != "GET"], [])

    def test_no_import_record_is_kept_and_shown_for_an_admin(self):
        w = World(check=self.BAD, imports=False)
        [e] = w.run()["entries"]
        self.assertEqual((e["verdict"], e["blocked"], e["file"]), ("broken", False, "kept"))
        self.assertIn("no import record", e["note"])
        self.assertEqual([c for c in w.radarr.calls if c[0] != "GET"], [])

    def test_hand_import_is_not_blocked(self):
        w = World(check=self.BAD, radarr=FakeArr("radarr", [import_record("/data/media/" + FILM, dl=None)]))
        [e] = w.run()["entries"]
        self.assertEqual((e["blocked"], e["file"]), (False, "kept"))

    def test_the_test_film_is_never_touched(self):
        arr = FakeArr("radarr", [import_record("/data/media/" + FILM)], tmdb=653)
        w = World(check=self.BAD, radarr=arr)
        [e] = w.run()["entries"]
        self.assertEqual((e["blocked"], e["file"]), (False, "kept"))
        self.assertEqual([c for c in arr.calls if c[0] != "GET"], [])

    def test_an_episode_is_found_through_its_series_and_searched_again(self):
        w = World(entries=burst(EP_ID, NOW - 400), files=(FILM, EPISODE), check=self.BAD)
        w.sonarr = FakeArr("sonarr", [import_record("/data/media/" + EPISODE, app="sonarr")])
        [e] = w.run()["entries"]
        self.assertEqual((e["blocked"], e["searched"], e["key"]), (True, True, "tv/99"))
        self.assertEqual(len(w.sonarr.made("DELETE", "/episodefile/501")), 1)
        self.assertEqual(w.sonarr.made("POST", "/command")[-1][3], {"name": "EpisodeSearch", "episodeIds": [31]})

    def test_a_busy_media_lock_keeps_the_result_and_acts_later_without_checking_again(self):
        w = World(check=self.BAD, lock_busy=True)
        state = w.run()
        self.assertEqual((state["entries"], len(state["queue"]), len(w.checked)), ([], 1, 1))
        self.assertEqual(state["queue"][0]["result"]["verdict"], "broken")
        w.lock_busy = False
        w.clock += 300
        state = w.run()
        self.assertEqual((len(w.checked), state["queue"], state["entries"][0]["blocked"]), (1, [], True))


class Robust(unittest.TestCase):
    def test_a_failed_check_is_retried_and_dropped_after_three(self):
        w = World(check={"verdict": "error", "reasons": [], "notes": [], "why": "ffprobe: killed"})
        for i in range(3):
            state = w.run()
            w.clock += 300
        self.assertEqual((len(w.checked), state["queue"], state["entries"]), (3, [], []))
        self.assertIn("gave up", w.out)

    def test_the_night_window_does_nothing(self):
        t = time.mktime(time.strptime("2027-01-15 04:10:00", "%Y-%m-%d %H:%M:%S"))
        w = World(entries=burst(FILM_ID, t - 400), clock=t)
        state = w.run()
        self.assertEqual((w.checked, w.since, state.get("queue")), ([], [], None))

    def test_a_broken_state_file_stops_without_touching_it(self):
        w = World()
        w.state.parent.mkdir(parents=True)
        w.state.write_text("{broken")
        with self.assertRaises(ValueError):
            w.run()
        self.assertEqual(w.state.read_text(), "{broken")
        self.assertEqual(w.checked, [])

    def test_old_entries_are_pruned(self):
        w = World(entries=[])
        w.state.parent.mkdir(parents=True)
        w.state.write_text(json.dumps({"entries": [{"at": "2020-01-01T00:00:00Z"}, {"at": pc.lb.ts(NOW - 3600)}], "downloads": [], "seenHits": {"x": NOW - 10 * 86400}, "checked": {}}))
        state = w.run()
        self.assertEqual(len(state["entries"]), 1)
        self.assertEqual(state["seenHits"], {})


class Paths(unittest.TestCase):
    def test_jellyfin_paths_map_to_the_apps_paths(self):
        self.assertEqual(pc.apps_path("/media/movies/X/x.mkv"), "/data/media/movies/X/x.mkv")
        self.assertEqual(pc.apps_path("/data/media/tv/X/x.mkv"), "/data/media/tv/X/x.mkv")
        self.assertIsNone(pc.apps_path("/somewhere/else.mkv"))
        self.assertIsNone(pc.apps_path(None))

    def test_the_host_file_is_under_the_idle_io_view(self):
        self.assertEqual(pc.host_file("/srv/dlpool/media", "/data/media/movies/X/x.mkv"), "/srv/dlpool/media/movies/X/x.mkv")

    def test_the_media_lock_waits_then_gives_up(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lock")
            with pc.media_lock(path, wait=1, sleep=lambda s: time.sleep(0.2)):
                other = os.open(path, os.O_RDWR)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.close(other)
                with self.assertRaises(pc.LockBusy):
                    with pc.media_lock(path, wait=0.5, sleep=lambda s: time.sleep(0.2)):
                        pass
            with pc.media_lock(path, wait=0.5):
                pass

    def test_since_accepts_hours_days_and_all(self):
        self.assertIsNone(pc.parse_since("all"))
        self.assertAlmostEqual(pc.parse_since("2d"), time.time() - 172800, delta=5)
        with self.assertRaises(ValueError):
            pc.parse_since("soon")


class Cli(unittest.TestCase):
    def test_help_names_the_commands(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(pc.main(["-h"]), 0)
        for word in ("run", "replay", "status"):
            self.assertIn(word, out.getvalue())

    def test_unknown_arguments_print_a_hint(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(pc.main(["nope"]), 2)
        self.assertIn("next:", err.getvalue())

    def test_replay_lists_the_hits_and_changes_nothing(self):
        ev = burst(FILM_ID, NOW) + burst(EP_ID, NOW + 5000, secs=(5, 6, 7, 8))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            n = pc.replay(ev, lambda i: {"path": "/media/x.mkv", "name": "Name " + i[:1]})
        self.assertEqual(n, 2)
        self.assertIn("Name f", out.getvalue())
        self.assertIn("4 starts", out.getvalue())


if __name__ == "__main__":
    unittest.main()
