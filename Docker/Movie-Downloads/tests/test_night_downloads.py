#!/usr/bin/env python3
"""scripts/night-downloads.py: low tariff, FIFO one night download per client, the 06:00 pause with its 15-min grace, download-now titles, the state file for fastpi, against fake clients.
Usage: python3 tests/test_night_downloads.py"""
import datetime
import importlib.util
import json
import pathlib
import tempfile
import unittest

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


def job(n, index, status="Paused", cat="tv-night", timeleft="0:00:00", mbleft="1000"):
    return {"nzo_id": n, "filename": n, "cat": cat, "status": status, "index": index, "timeleft": timeleft, "mbleft": mbleft}


T_NIGHT = tor("AAA", 100, cat="radarr-night", name="Film.A.2020")
T_NIGHT_V4 = tor("bbb", 200, state="pausedDL", name="Show.S01E01")
T_NOW_PAUSED = tor("ccc", 50, cat="radarr", name="Manual.pause")
T_SEEDED = tor("eee", 10, state="stoppedUP", cat="radarr-night")
T_SEEDING = tor("fff", 20, state="uploading", cat="radarr-night")
J_NIGHT = job("SABnzbd_nzo_1", 0, cat="movies-night")
J_NOW = job("SABnzbd_nzo_2", 1, cat="movies")


def plan(now, torrents=(), slots=(), urgent=(), titles=None, stalled=None, kbps=1000):
    return nd.plan(now, list(torrents), list(slots), set(urgent), titles or {}, stalled or {}, kbps)


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
        p = plan(SUN_NOON, [tor("t", 1, "downloading")], [job("j", 0, "Downloading", timeleft="9:00:00")])
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

    def test_one_title_completes_before_the_next_starts(self):
        titles = {"boys1": ("sonarr", 1), "vox1": ("sonarr", 2), "vox2": ("sonarr", 2), "boys5": ("sonarr", 1)}
        js = [job("boys1", 0, "Downloading"), job("vox1", 1), job("vox2", 2), job("boys5", 3)]
        self.assertEqual([s["nzo_id"] for s in nd.fifo(js, "nzo_id", nd.sab_order, titles)], ["boys1", "boys5", "vox1", "vox2"])
        p = plan(MON_2205, slots=js, titles=titles)
        self.assertEqual((p.nzos, p.pause_nzos, p.pending), ([], [], 3))

    def test_resume_order_after_a_0600_pause(self):
        ts = [tor("t1", 100, "stoppedDL"), tor("t2", 200)]
        js = [job("j0", 0), job("j1", 1)]
        p = plan(MON_2205, ts, js)
        self.assertEqual((p.hashes, p.nzos), (["t1"], ["j0"]))

    def test_download_now_runs_beside_the_night_slot(self):
        ts = [tor("old", 100, "downloading"), tor("urg", 200), tor("now", 50, "downloading", cat="radarr")]
        p = plan(MON_2205, ts, urgent={"urg"})
        self.assertEqual((p.hashes, p.pause_hashes), (["urg"], []))

    def test_nothing_waits_for_the_night_during_the_night(self):
        p = plan(MON_2205, [T_NIGHT, tor("zzz", 300)], [J_NIGHT, job("SABnzbd_nzo_5", 1)])
        self.assertEqual((p.pending, p.waiting), (2, []))

    def test_paused_item_outside_night_categories_is_never_touched(self):
        p = plan(MON_2205, [T_NOW_PAUSED], [J_NOW], {"ccc", "sabnzbd_nzo_2"})
        self.assertEqual((p.hashes, p.nzos, p.pending), ([], [], 0))


class Arr(unittest.TestCase):
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
        if path in ("/torrents/start", "/torrents/stop"):
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
        self.assertEqual(f.actions(), [("qbit", "/torrents/start", {"hashes": "AAA"}), ("sab", {"mode": "queue", "name": "resume", "value": "SABnzbd_nzo_1"})])
        s = json.loads(self.state.read_text())
        self.assertEqual((s["pending"], s["low_tariff"]), (2, True))

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
        self.assertEqual(f2.actions(), [("qbit", "/torrents/start", {"hashes": "new"})])

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

    def test_arr_down_still_resumes_at_night(self):
        f = Fake([T_NIGHT], [], fail={"radarr", "sonarr"})
        self.assertEqual(self.go(f, MON_2205), 1)
        self.assertIn(("qbit", "/torrents/start", {"hashes": "AAA"}), f.calls)
        self.assertEqual(json.loads(self.state.read_text())["pending"], 0)


class Clock(unittest.TestCase):
    def test_nd_now_overrides_the_clock(self):
        self.assertEqual(nd.now_from({"ND_NOW": "2026-10-27T22:10"}), datetime.datetime(2026, 10, 27, 22, 10))
        self.assertIsInstance(nd.now_from({}), datetime.datetime)


if __name__ == "__main__":
    unittest.main()
