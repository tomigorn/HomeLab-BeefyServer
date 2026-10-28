#!/usr/bin/env python3
"""scripts/night-downloads.py: low tariff, night movies in parallel, one TV series at a time in episode order, the 06:00 pause with its 15-min grace, download-now titles, the state file for fastpi, against fake clients.
Usage: python3 tests/test_night_downloads.py"""
import datetime
import email.utils
import http.server
import importlib.util
import json
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("nd", ROOT / "scripts" / "night-downloads.py")
nd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nd)

MON_DAY = datetime.datetime(2026, 10, 26, 14, 0)
MON_2205 = datetime.datetime(2026, 10, 26, 22, 5)
TUE_0559 = datetime.datetime(2026, 10, 27, 5, 59)
TUE_0600 = datetime.datetime(2026, 10, 27, 6, 0)
TUE_0602 = datetime.datetime(2026, 10, 27, 6, 2)
SAT_2159 = datetime.datetime(2026, 10, 31, 21, 59)
SUN_NOON = datetime.datetime(2026, 11, 1, 12, 0)
INF = 8640000


def tor(h, added, state="stoppedDL", cat="sonarr-night", eta=INF, name=None):
    return {"hash": h, "name": name or h, "category": cat, "state": state, "added_on": added, "eta": eta}


def job(n, index, status="Paused", cat="tv-night", timeleft="0:00:00", mbleft="1000", added=0):
    return {"nzo_id": n, "filename": n, "cat": cat, "status": status, "index": index, "timeleft": timeleft, "mbleft": mbleft, "time_added": added}


T_NIGHT = tor("AAA", 100, cat="radarr-night", name="Film.A.2020")
T_NIGHT_V4 = tor("bbb", 200, state="pausedDL", name="Show.S01E01")
T_NOW_PAUSED = tor("ccc", 50, cat="radarr", name="Manual.pause")
T_SEEDED = tor("eee", 10, state="stoppedUP", cat="radarr-night")
T_SEEDING = tor("fff", 20, state="uploading", cat="radarr-night")
J_NIGHT = job("SABnzbd_nzo_1", 0, cat="movies-night")
J_NOW = job("SABnzbd_nzo_2", 1, cat="movies")


def plan(now, torrents=(), slots=(), urgent=(), titles=None, stalled=None, kbps=1000, positions=None):
    return nd.plan(now, list(torrents), list(slots), set(urgent), titles or {}, stalled or {}, kbps, positions or {})


class Tariff(unittest.TestCase):
    def test_nights_and_sunday_are_low(self):
        for dt in (MON_2205, TUE_0559, SUN_NOON, datetime.datetime(2026, 11, 1, 0, 1), datetime.datetime(2026, 11, 1, 23, 59)):
            self.assertTrue(nd.low_tariff(dt), dt)

    def test_weekdays_and_saturday_by_day_are_high(self):
        for dt in (MON_DAY, TUE_0600, SAT_2159, datetime.datetime(2026, 11, 2, 6, 0)):
            self.assertFalse(nd.low_tariff(dt), dt)


class Eta(unittest.TestCase):
    def test_qbit_eta_known_and_unknown(self):
        self.assertEqual(nd.qbit_eta(tor("a", 1, state="downloading", eta=840)), 840)
        self.assertIsNone(nd.qbit_eta(tor("a", 1, state="downloading", eta=INF)))
        self.assertIsNone(nd.qbit_eta(tor("a", 1, state="stalledDL", eta=300)))
        self.assertIsNone(nd.qbit_eta({"state": "downloading"}))

    def test_sab_timeleft_parsed_with_days(self):
        self.assertEqual(nd.sab_eta(job("n", 0, "Downloading", timeleft="0:14:00"), 500), 840)
        self.assertEqual(nd.sab_eta(job("n", 0, "Downloading", timeleft="1:02:03:04"), 500), 93784)

    def test_sab_eta_unknown_without_speed_or_time(self):
        self.assertIsNone(nd.sab_eta(job("n", 0, "Downloading", timeleft="0:05:00"), 0))
        self.assertIsNone(nd.sab_eta(job("n", 0, "Downloading", timeleft="0:00:00"), 500))
        self.assertIsNone(nd.sab_eta(job("n", 0, "Queued", timeleft="0:05:00"), 500))
        self.assertIsNone(nd.sab_eta(job("n", 0, "Downloading", timeleft="soon"), 500))


class Day(unittest.TestCase):
    def test_paused_night_items_stay_paused_and_are_pending(self):
        p = plan(MON_DAY, [T_NIGHT, T_NIGHT_V4, T_NOW_PAUSED, T_SEEDED, T_SEEDING], [J_NIGHT, J_NOW])
        self.assertEqual((p.hashes, p.nzos, p.pause_hashes, p.pause_nzos), ([], [], [], []))
        self.assertEqual(p.pending, 3)

    def test_0600_grace_boundary_14_15_unknown_qbit(self):
        ts = [tor("t14", 1, "downloading", eta=14 * 60), tor("t15", 2, "downloading", eta=15 * 60), tor("tinf", 3, "downloading"), tor("tst", 4, "stalledDL", eta=60)]
        p = plan(TUE_0602, ts)
        self.assertEqual(p.pause_hashes, ["t15", "tinf", "tst"])
        self.assertEqual(p.hashes, [])
        self.assertEqual(p.pending, 3)

    def test_0600_grace_boundary_14_15_unknown_sab(self):
        js = [job("j14", 0, "Downloading", timeleft="0:14:59"), job("j15", 1, "Downloading", timeleft="0:15:00"), job("j0", 2, "Downloading", timeleft="0:00:00"), job("jq", 3, "Queued", timeleft="0:01:00")]
        p = plan(TUE_0600, slots=js)
        self.assertEqual(p.pause_nzos, ["j15", "j0", "jq"])
        self.assertEqual(p.pending, 3)

    def test_waiting_for_seerr_are_the_pending_night_items_lowercase(self):
        ts = [T_NIGHT, tor("T15", 2, "downloading", eta=15 * 60), tor("t14", 3, "downloading", eta=14 * 60), tor("urgp", 4)]
        p = plan(TUE_0602, ts, [J_NIGHT, J_NOW], urgent={"urgp"})
        self.assertEqual(sorted(p.waiting), ["aaa", "sabnzbd_nzo_1", "t15"])
        self.assertEqual(len(p.waiting), p.pending)

    def test_sab_without_speed_pauses_everything(self):
        p = plan(TUE_0600, slots=[job("j", 0, "Downloading", timeleft="0:02:00")], kbps=0)
        self.assertEqual(p.pause_nzos, ["j"])

    def test_download_now_untouched_by_the_pause(self):
        ts = [tor("now1", 1, "downloading", cat="radarr"), tor("urg", 2, "downloading"), tor("urgp", 3)]
        js = [job("SABnzbd_nzo_9", 0, "Downloading", cat="tv"), job("SABnzbd_nzo_8", 1, "Downloading", timeleft="5:00:00")]
        p = plan(TUE_0602, ts, js, urgent={"urg", "urgp", "sabnzbd_nzo_8"})
        self.assertEqual((p.pause_hashes, p.pause_nzos), ([], []))
        self.assertEqual(p.hashes, ["urgp"])
        self.assertEqual(p.pending, 0)

    def test_download_now_title_resumes_by_day_case_insensitive(self):
        p = plan(MON_DAY, [T_NIGHT, T_NIGHT_V4], [J_NIGHT], {"aaa", "sabnzbd_nzo_1"})
        self.assertEqual((p.hashes, p.nzos, p.pending), (["AAA"], ["SABnzbd_nzo_1"], 1))

    def test_sunday_never_pauses(self):
        p = plan(SUN_NOON, [tor("t", 1, "downloading", cat="radarr-night")], [job("j", 0, "Downloading", timeleft="9:00:00")])
        self.assertEqual((p.pause_hashes, p.pause_nzos), ([], []))


class Fifo(unittest.TestCase):
    def test_qbit_resumes_only_the_oldest_torrent(self):
        p = plan(MON_2205, [tor("new", 300), tor("old", 100), tor("mid", 200), T_SEEDED, T_SEEDING])
        self.assertEqual((p.hashes, p.pause_hashes, p.pending), (["old"], [], 2))

    def test_qbit_running_head_keeps_the_slot(self):
        p = plan(MON_2205, [tor("old", 100, "downloading"), tor("new", 200)])
        self.assertEqual((p.hashes, p.pause_hashes, p.pending), ([], [], 1))

    def test_qbit_running_later_torrents_are_paused_back(self):
        p = plan(MON_2205, [tor("old", 100), tor("new", 200, "downloading"), tor("newer", 300, "queuedDL")])
        self.assertEqual(p.hashes, ["old"])
        self.assertEqual(p.pause_hashes, ["new", "newer"])
        self.assertEqual(p.pending, 2)

    def test_qbit_stalled_head_releases_the_next_after_stall_min(self):
        now_ts = MON_2205.timestamp()
        ts = [tor("old", 100, "stalledDL"), tor("new", 200)]
        fresh = plan(MON_2205, ts, stalled={"old": now_ts - (nd.STALL_MIN * 60 - 60)})
        self.assertEqual((fresh.hashes, fresh.pending), ([], 1))
        long = plan(MON_2205, ts, stalled={"old": now_ts - nd.STALL_MIN * 60})
        self.assertEqual((long.hashes, long.pause_hashes, long.pending), (["new"], [], 0))

    def test_stall_clock_starts_on_first_sight_and_resets(self):
        now_ts = MON_2205.timestamp()
        p = plan(MON_2205, [tor("a", 1, "stalledDL"), tor("b", 2, "metaDL"), tor("c", 3, "downloading")], stalled={"b": 5.0, "c": 7.0, "gone": 1.0})
        self.assertEqual(p.stalled, {"a": now_ts, "b": 5.0})

    def test_sab_resumes_only_the_first_job_in_queue_order(self):
        p = plan(MON_2205, slots=[job("j2", 2), job("j0", 0), job("j1", 1), J_NOW])
        self.assertEqual((p.nzos, p.pause_nzos, p.pending), (["j0"], [], 2))

    def test_sab_running_later_job_is_paused_back(self):
        p = plan(MON_2205, slots=[job("j0", 0), job("j1", 1, "Downloading")])
        self.assertEqual((p.nzos, p.pause_nzos), (["j0"], ["j1"]))

    def test_one_series_completes_before_the_next_starts(self):
        titles = {"boys1": ("sonarr", 1), "vox1": ("sonarr", 2), "vox2": ("sonarr", 2), "boys5": ("sonarr", 1)}
        js = [job("boys1", 0, "Downloading", added=10), job("vox1", 1, added=20), job("vox2", 2, added=30), job("boys5", 3, added=40)]
        p = plan(MON_2205, slots=js, titles=titles)
        self.assertEqual([i.id for i in p.tv_order], ["boys1", "boys5", "vox1", "vox2"])
        self.assertEqual((p.nzos, p.pause_nzos, p.pending), ([], [], 3))

    def test_resume_order_after_a_0600_pause(self):
        ts = [tor("t1", 100, "stoppedDL", cat="radarr-night"), tor("t2", 200, cat="radarr-night"), tor("e1", 300), tor("e2", 400)]
        js = [job("j0", 0, cat="movies-night"), job("e3", 1, added=500)]
        p = plan(MON_2205, ts, js)
        self.assertEqual((p.hashes, p.nzos, p.pending), (["t1", "t2", "e1"], ["j0"], 2))

    def test_download_now_runs_beside_the_night_slot(self):
        ts = [tor("old", 100, "downloading"), tor("urg", 200), tor("now", 50, "downloading", cat="radarr")]
        p = plan(MON_2205, ts, urgent={"urg"})
        self.assertEqual((p.hashes, p.pause_hashes), (["urg"], []))

    def test_nothing_waits_for_the_night_during_the_night(self):
        p = plan(MON_2205, [T_NIGHT, tor("zzz", 300)], [J_NIGHT, job("SABnzbd_nzo_5", 1)])
        self.assertEqual((p.pending, p.waiting), (1, []))

    def test_paused_item_outside_night_categories_is_never_touched(self):
        p = plan(MON_2205, [T_NOW_PAUSED], [J_NOW], {"ccc", "sabnzbd_nzo_2"})
        self.assertEqual((p.hashes, p.nzos, p.pending), ([], [], 0))


T_SHOW = {"s1e1": ("sonarr", 1), "s1e2": ("sonarr", 1), "s2": ("sonarr", 1), "s2e3": ("sonarr", 1), "other": ("sonarr", 2)}
P_SHOW = {"s1e1": (1, 1), "s1e2": (1, 2), "s2": (2, 1), "s2e3": (2, 3), "other": (1, 1)}


class Rule(unittest.TestCase):
    def test_night_movies_all_resume_in_parallel(self):
        ts = [tor("m1", 100, cat="radarr-night"), tor("m2", 200, cat="radarr-night"), tor("m3", 300, "downloading", cat="radarr-night")]
        js = [job("n1", 0, cat="movies-night"), job("n2", 1, "Downloading", cat="movies-night")]
        p = plan(MON_2205, ts, js)
        self.assertEqual((p.hashes, p.nzos, p.pause_hashes, p.pause_nzos, p.pending), (["m1", "m2"], ["n1"], [], [], 0))

    def test_episodes_in_season_then_episode_order_across_clients(self):
        ts = [tor("s2", 50, name="Show.S02.1080p"), tor("s1e2", 60)]
        js = [job("s2e3", 0, added=10), job("s1e1", 1, added=90)]
        p = plan(MON_2205, ts, js, titles=T_SHOW, positions=P_SHOW)
        self.assertEqual([i.id for i in p.tv_order], ["s1e1", "s1e2", "s2", "s2e3"])
        self.assertEqual((p.hashes, p.nzos, p.pending), ([], ["s1e1"], 3))

    def test_later_episode_running_is_paused_back_for_the_earlier(self):
        ts = [tor("s1e2", 60, "downloading")]
        js = [job("s1e1", 0, added=90)]
        p = plan(MON_2205, ts, js, titles=T_SHOW, positions=P_SHOW)
        self.assertEqual((p.nzos, p.pause_hashes, p.pending), (["s1e1"], ["s1e2"], 1))

    def test_series_fifo_by_first_grab_one_series_across_clients(self):
        ts = [tor("other", 100, "downloading")]
        js = [job("s1e2", 0, added=50), job("s1e1", 1, added=300)]
        p = plan(MON_2205, ts, js, titles=T_SHOW, positions=P_SHOW)
        self.assertEqual([i.id for i in p.tv_order], ["s1e1", "s1e2", "other"])
        self.assertEqual((p.nzos, p.pause_hashes, p.pending), (["s1e1"], ["other"], 2))

    def test_next_series_starts_when_the_current_has_nothing_left(self):
        p = plan(MON_2205, [tor("other", 100), tor("done", 10, "uploading")], titles=T_SHOW, positions=P_SHOW)
        self.assertEqual((p.hashes, p.pending), (["other"], 0))

    def test_one_series_beside_several_movies(self):
        ts = [tor("m1", 100, cat="radarr-night"), tor("m2", 200, cat="radarr-night"), tor("other", 150)]
        js = [job("s1e1", 0, added=50), job("n1", 1, cat="movies-night"), job("s1e2", 2, added=60)]
        p = plan(MON_2205, ts, js, titles=T_SHOW, positions=P_SHOW)
        self.assertEqual((sorted(p.hashes), p.nzos, p.pending), (["m1", "m2"], ["n1", "s1e1"], 2))

    def test_running_tv_torrent_head_goes_to_the_top_of_the_qbit_queue(self):
        p = plan(MON_2205, [tor("s1e1", 10), tor("m1", 5, "downloading", cat="radarr-night")], titles=T_SHOW, positions=P_SHOW)
        self.assertEqual((p.hashes, p.top_hashes), (["s1e1"], ["s1e1"]))
        q = plan(MON_2205, [tor("s1e1", 10, "queuedDL")], titles=T_SHOW, positions=P_SHOW)
        self.assertEqual(q.top_hashes, ["s1e1"])
        r = plan(MON_2205, [tor("s1e1", 10, "downloading")], titles=T_SHOW, positions=P_SHOW)
        self.assertEqual(r.top_hashes, [])
        self.assertEqual(plan(MON_2205, slots=[job("s1e1", 0)], titles=T_SHOW, positions=P_SHOW).top_hashes, [])

    def test_stalled_tv_head_lets_the_next_episode_start(self):
        ts = [tor("s1e1", 10, "stalledDL"), tor("s1e2", 20)]
        p = plan(MON_2205, ts, titles=T_SHOW, positions=P_SHOW, stalled={"s1e1": MON_2205.timestamp() - nd.STALL_MIN * 60})
        self.assertEqual((p.hashes, p.pause_hashes), (["s1e2"], []))

    def test_unknown_position_from_the_name_and_last_without(self):
        self.assertEqual(nd.name_position("Show.S03E07.1080p"), (3, 7))
        self.assertEqual(nd.name_position("Show.s2e10v2"), (2, 10))
        self.assertEqual(nd.name_position("Show.S02.1080p.BluRay"), (2, 0))
        self.assertEqual(nd.name_position("[Grp] Show - 12 [1080p]"), None)
        ts = [tor("x", 10, name="Show.S01E05"), tor("y", 20, name="Show.S01E02"), tor("z", 5, name="Show - 01")]
        titles = {"x": ("sonarr", 5), "y": ("sonarr", 5), "z": ("sonarr", 5)}
        self.assertEqual([i.id for i in plan(MON_2205, ts, titles=titles).tv_order], ["y", "x", "z"])

    def test_by_day_tv_and_movies_still_pause_and_wait(self):
        ts = [tor("m1", 1, "downloading", cat="radarr-night", eta=3600), tor("s1e1", 2, "downloading", eta=3600), tor("s1e2", 3)]
        p = plan(TUE_0602, ts, titles=T_SHOW, positions=P_SHOW)
        self.assertEqual((p.hashes, sorted(p.pause_hashes), p.pending, sorted(p.waiting)), ([], ["m1", "s1e1"], 3, ["m1", "s1e1", "s1e2"]))
        self.assertEqual(p.top_hashes, [])


class Arr(unittest.TestCase):
    def test_positions_are_the_first_episode_of_each_download(self):
        queue = {"records": [
            {"downloadId": "PACK", "seasonNumber": 2, "episode": {"seasonNumber": 2, "episodeNumber": 3}},
            {"downloadId": "PACK", "seasonNumber": 2, "episode": {"seasonNumber": 2, "episodeNumber": 1}},
            {"downloadId": "ep", "seasonNumber": 1, "episode": {"seasonNumber": 1, "episodeNumber": 7}},
            {"downloadId": "noep", "seasonNumber": 4},
            {"downloadId": "movie", "movieId": 3},
        ]}
        self.assertEqual(nd.episode_positions(queue), {"pack": (2, 1), "ep": (1, 7), "noep": (4, 0)})

    def test_download_ids_of_titles_tagged_download_now(self):
        tags = [{"id": 3, "label": "audio-german"}, {"id": 70, "label": "download-now"}]
        queue = {"totalRecords": 3, "records": [
            {"downloadId": "AAA", "movie": {"tags": [70, 3]}},
            {"downloadId": "XYZ", "movie": {"tags": [3]}},
            {"downloadId": "SABnzbd_nzo_1", "series": {"tags": [70]}},
        ]}
        self.assertEqual(nd.urgent_ids(tags, queue), {"aaa", "sabnzbd_nzo_1"})

    def test_no_tag_no_urgent(self):
        self.assertEqual(nd.urgent_ids([{"id": 3, "label": "audio-german"}], {"records": [{"downloadId": "A", "movie": {"tags": [3]}}]}), set())

    def test_titles_map_download_ids_to_the_title(self):
        queue = {"records": [{"downloadId": "AAA", "seriesId": 7}, {"downloadId": "bbb", "movieId": 3}, {"seriesId": 1}]}
        self.assertEqual(nd.title_ids("sonarr", queue), {"aaa": ("sonarr", 7), "bbb": ("sonarr", 3)})


class Fake:
    def __init__(self, torrents=(), slots=(), fail=(), kbps="1000"):
        self.torrents, self.slots, self.fail, self.kbps, self.calls = list(torrents), list(slots), set(fail), kbps, []

    def qbit(self, path, data=None):
        self.calls.append(("qbit", path, data))
        if "qbit" in self.fail:
            raise nd.ApiError("qbit down")
        if path == "/torrents/info":
            return self.torrents
        if path in ("/torrents/start", "/torrents/stop", "/torrents/topPrio"):
            return None
        raise AssertionError(path)

    def sab(self, params):
        self.calls.append(("sab", params))
        if "sab" in self.fail:
            raise nd.ApiError("sab down")
        if params.get("mode") == "queue" and "name" not in params:
            return {"queue": {"slots": self.slots, "kbpersec": self.kbps}}
        return {"status": True}

    def arr(self, app):
        def call(path):
            self.calls.append((app, path))
            if app in self.fail:
                raise nd.ApiError(f"{app} down")
            if path == "/tag":
                return [{"id": 70, "label": "download-now"}]
            return {"totalRecords": 1, "records": [{"downloadId": "bbb", "seriesId": 4, "series": {"tags": [70]}}]} if app == "sonarr" else {"records": []}
        return call

    def actions(self):
        return [c for c in self.calls if (c[0] == "qbit" and c[1] != "/torrents/info") or (c[0] == "sab" and c[1].get("name"))]


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "night-downloads.json"
        self.public = pathlib.Path(self.tmp.name) / "seerr" / "night-waiting.json"

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, f, now, dry=False):
        return nd.run(now, f.qbit, f.sab, {"radarr": f.arr("radarr"), "sonarr": f.arr("sonarr")}, self.state, dry_run=dry, public_path=self.public)

    def test_low_tariff_resumes_the_head_and_writes_pending(self):
        f = Fake([T_NIGHT, tor("zzz", 300)], [J_NIGHT, job("SABnzbd_nzo_5", 1)])
        self.assertEqual(self.go(f, MON_2205), 0)
        self.assertEqual(f.actions(), [("qbit", "/torrents/start", {"hashes": "AAA"}), ("sab", {"mode": "queue", "name": "resume", "value": "SABnzbd_nzo_1"}), ("sab", {"mode": "queue", "name": "resume", "value": "SABnzbd_nzo_5"})])
        s = json.loads(self.state.read_text())
        self.assertEqual((s["pending"], s["low_tariff"]), (1, True))

    def test_by_day_resumes_download_now_and_counts_the_rest(self):
        f = Fake([T_NIGHT, T_NIGHT_V4], [J_NIGHT])
        self.assertEqual(self.go(f, MON_DAY), 0)
        self.assertEqual(f.actions(), [("qbit", "/torrents/start", {"hashes": "bbb"})])
        self.assertEqual(json.loads(self.state.read_text())["pending"], 2)

    def test_0600_pauses_and_counts_for_the_next_night(self):
        f = Fake([tor("t", 1, "downloading", eta=3600), tor("ok", 2, "downloading", eta=60)], [job("SABnzbd_nzo_1", 0, "Downloading", timeleft="2:00:00")])
        self.assertEqual(self.go(f, TUE_0602), 0)
        self.assertEqual(f.actions(), [("qbit", "/torrents/stop", {"hashes": "t"}), ("sab", {"mode": "queue", "name": "pause", "value": "SABnzbd_nzo_1"})])
        self.assertEqual(json.loads(self.state.read_text())["pending"], 2)

    def test_stall_clock_persists_in_the_state_file(self):
        f = Fake([tor("old", 100, "stalledDL"), tor("new", 200)])
        self.go(f, MON_2205)
        self.assertEqual(json.loads(self.state.read_text())["stalled"], {"old": MON_2205.timestamp()})
        later = MON_2205 + datetime.timedelta(minutes=nd.STALL_MIN)
        f2 = Fake([tor("old", 100, "stalledDL"), tor("new", 200)])
        self.go(f2, later)
        self.assertEqual(f2.actions(), [("qbit", "/torrents/start", {"hashes": "new"}), ("qbit", "/torrents/topPrio", {"hashes": "new"})])

    def test_seerr_file_lists_the_waiting_ids_by_day(self):
        f = Fake([T_NIGHT, T_NIGHT_V4], [J_NIGHT])
        self.assertEqual(self.go(f, MON_DAY), 0)
        pub = json.loads(self.public.read_text())
        self.assertEqual((sorted(pub["waiting"]), pub["low_tariff"]), (["aaa", "sabnzbd_nzo_1"], False))
        self.assertIsNotNone(datetime.datetime.fromisoformat(pub["updated"]).tzinfo)
        self.assertEqual(self.public.stat().st_mode & 0o777, 0o644)
        self.assertNotIn("waiting", json.loads(self.state.read_text()))

    def test_seerr_file_is_empty_during_the_night(self):
        self.go(Fake([T_NIGHT, tor("zzz", 300)], [J_NIGHT]), MON_2205)
        self.assertEqual(json.loads(self.public.read_text())["waiting"], [])

    def test_seerr_file_not_writable_fails_but_the_state_is_written(self):
        self.public = pathlib.Path("/proc/nope/night-waiting.json")
        self.assertEqual(self.go(Fake([T_NIGHT], [J_NIGHT]), MON_DAY), 1)
        self.assertEqual(json.loads(self.state.read_text())["pending"], 2)

    def test_dry_run_changes_nothing(self):
        f = Fake([T_NIGHT, tor("t", 1, "downloading", eta=3600)], [J_NIGHT])
        self.assertEqual(self.go(f, MON_2205, dry=True), 0)
        self.assertEqual(self.go(f, TUE_0602, dry=True), 0)
        self.assertEqual(f.actions(), [])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.public.exists())

    def test_client_down_keeps_the_old_state_and_fails(self):
        self.state.write_text('{"pending": 4}')
        f = Fake([T_NIGHT], [J_NIGHT], fail={"sab"})
        self.assertEqual(self.go(f, MON_DAY), 1)
        self.assertEqual(json.loads(self.state.read_text())["pending"], 4)

    def test_tv_head_torrent_gets_top_priority_and_its_failure_is_not_fatal(self):
        f = Fake([tor("bbb", 100), tor("ccc", 50, cat="radarr-night")])
        f.arr = lambda app: (lambda path: {"records": []} if path.startswith("/queue") else [])
        self.assertEqual(self.go(f, MON_2205), 0)
        self.assertEqual(f.actions(), [("qbit", "/torrents/start", {"hashes": "ccc|bbb"}), ("qbit", "/torrents/topPrio", {"hashes": "bbb"})])
        qbit = f.qbit
        f.qbit = lambda path, data=None: (_ for _ in ()).throw(nd.ApiError("409 queueing off")) if path == "/torrents/topPrio" else qbit(path, data)
        self.assertEqual(self.go(f, MON_2205), 0)
        self.assertEqual(json.loads(self.state.read_text())["pending"], 0)

    def test_sonarr_queue_asks_for_the_episodes(self):
        f = Fake([])
        self.go(f, MON_2205)
        self.assertIn(("sonarr", "/queue?page=1&pageSize=1000"), f.calls)
        self.assertEqual(nd.APPS["sonarr"], "includeSeries=true&includeEpisode=true")

    def test_arr_down_still_resumes_at_night(self):
        f = Fake([T_NIGHT], [], fail={"radarr", "sonarr"})
        self.assertEqual(self.go(f, MON_2205), 1)
        self.assertIn(("qbit", "/torrents/start", {"hashes": "AAA"}), f.calls)
        self.assertEqual(json.loads(self.state.read_text())["pending"], 0)


class FakeQbit(http.server.BaseHTTPRequestHandler):
    sid = "s1"
    log = []
    legacy = False

    def log_message(self, *a):
        pass

    def reply(self, code, body=b"", cookie=None):
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", f"QBT_SID_8080={cookie}; HttpOnly; SameSite=Lax; expires={email.utils.formatdate(time.time() + 3600, usegmt=True)}; path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.handle_any()

    def do_POST(self):
        self.handle_any()

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        FakeQbit.log.append((self.command, self.path, body))
        if self.path == "/api/v2/auth/login":
            form = urllib.parse.parse_qs(body)
            if form.get("username") == ["admin"] and form.get("password") == ["s3cret pw&="]:
                return self.reply(200, b"Ok.", cookie=FakeQbit.sid) if FakeQbit.legacy else self.reply(204, cookie=FakeQbit.sid)
            return self.reply(200, b"Fails.") if FakeQbit.legacy else self.reply(401, b"Fails.")
        if f"QBT_SID_8080={FakeQbit.sid}" not in (self.headers.get("Cookie") or ""):
            return self.reply(403, b"Forbidden")
        if self.path == "/api/v2/torrents/info":
            return self.reply(200, b'[{"hash": "aaa"}]')
        if self.path == "/api/v2/torrents/start":
            return self.reply(200)
        return self.reply(404)


class QbitLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeQbit)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}/api/v2"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pw = pathlib.Path(self.tmp.name) / "qbit-webui.password"
        self.pw.write_text("s3cret pw&=\n")
        FakeQbit.sid, FakeQbit.log[:], FakeQbit.legacy = "s1", [], False

    def tearDown(self):
        self.tmp.cleanup()

    def logins(self):
        return [e for e in FakeQbit.log if e[1] == "/api/v2/auth/login"]

    def test_logs_in_on_403_and_retries(self):
        qbit = nd.qbit_client(self.base, "admin", self.pw)
        self.assertEqual(qbit("/torrents/info"), [{"hash": "aaa"}])
        self.assertEqual(len(self.logins()), 1)
        self.assertNotIn("s3cret", "".join(e[1] for e in FakeQbit.log))

    def test_session_is_kept_between_calls(self):
        qbit = nd.qbit_client(self.base, "admin", self.pw)
        qbit("/torrents/info")
        qbit("/torrents/start", {"hashes": "aaa"})
        self.assertEqual(len(self.logins()), 1)

    def test_expired_session_logs_in_again(self):
        qbit = nd.qbit_client(self.base, "admin", self.pw)
        qbit("/torrents/info")
        FakeQbit.sid = "s2"
        self.assertEqual(qbit("/torrents/info"), [{"hash": "aaa"}])
        self.assertEqual(len(self.logins()), 2)

    def test_wrong_password_fails_after_one_login(self):
        self.pw.write_text("wrong")
        qbit = nd.qbit_client(self.base, "admin", self.pw)
        with self.assertRaisesRegex(nd.ApiError, "login refused"):
            qbit("/torrents/info")
        self.assertEqual(len(self.logins()), 1)

    def test_qbit_4_login_ok_and_fails_bodies(self):
        FakeQbit.legacy = True
        self.assertEqual(nd.qbit_client(self.base, "admin", self.pw)("/torrents/info"), [{"hash": "aaa"}])
        self.pw.write_text("wrong")
        with self.assertRaisesRegex(nd.ApiError, "login refused"):
            nd.qbit_client(self.base, "admin", self.pw)("/torrents/info")

    def test_wrong_password_no_second_login_through_the_legacy_path(self):
        self.pw.write_text("wrong")
        qbit = nd.qbit_client(self.base, "admin", self.pw)
        with self.assertRaisesRegex(nd.ApiError, "login refused"):
            qbit("/torrents/start", {"hashes": "aaa"})
        self.assertEqual(len(self.logins()), 1)

    def test_no_password_file_fails_on_403(self):
        qbit = nd.qbit_client(self.base, "admin", pathlib.Path(self.tmp.name) / "missing")
        with self.assertRaisesRegex(nd.ApiError, "no qBittorrent password"):
            qbit("/torrents/info")
        self.assertEqual(self.logins(), [])


class Clock(unittest.TestCase):
    def test_nd_now_overrides_the_clock(self):
        self.assertEqual(nd.now_from({"ND_NOW": "2026-10-27T22:10"}), datetime.datetime(2026, 10, 27, 22, 10))
        self.assertIsInstance(nd.now_from({}), datetime.datetime)


if __name__ == "__main__":
    unittest.main()
