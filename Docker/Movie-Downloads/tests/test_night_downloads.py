#!/usr/bin/env python3
"""scripts/night-downloads.py: low tariff, which paused night downloads resume, download-now titles, the state file for fastpi, against fake clients.
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
SAT_2159 = datetime.datetime(2026, 10, 31, 21, 59)
SUN_NOON = datetime.datetime(2026, 11, 1, 12, 0)

T_NIGHT = {"hash": "AAA", "name": "Film.A.2020", "category": "radarr-night", "state": "stoppedDL"}
T_NIGHT_V4 = {"hash": "bbb", "name": "Show.S01E01", "category": "sonarr-night", "state": "pausedDL"}
T_NOW_PAUSED = {"hash": "ccc", "name": "Manual.pause", "category": "radarr", "state": "stoppedDL"}
T_RUNNING = {"hash": "ddd", "name": "Film.D", "category": "radarr-night", "state": "downloading"}
T_SEEDED = {"hash": "eee", "name": "Film.E", "category": "radarr-night", "state": "stoppedUP"}
J_NIGHT = {"nzo_id": "SABnzbd_nzo_1", "filename": "Film.F", "cat": "movies-night", "status": "Paused"}
J_NOW = {"nzo_id": "SABnzbd_nzo_2", "filename": "Film.G", "cat": "movies", "status": "Paused"}
J_RUNNING = {"nzo_id": "SABnzbd_nzo_3", "filename": "Film.H", "cat": "tv-night", "status": "Downloading"}


class Tariff(unittest.TestCase):
    def test_nights_and_sunday_are_low(self):
        for dt in (MON_2205, TUE_0559, SUN_NOON, datetime.datetime(2026, 11, 1, 0, 1), datetime.datetime(2026, 11, 1, 23, 59)):
            self.assertTrue(nd.low_tariff(dt), dt)

    def test_weekdays_and_saturday_by_day_are_high(self):
        for dt in (MON_DAY, TUE_0600, SAT_2159, datetime.datetime(2026, 11, 2, 6, 0)):
            self.assertFalse(nd.low_tariff(dt), dt)


class Plan(unittest.TestCase):
    def test_by_day_nothing_resumes_and_paused_night_items_are_pending(self):
        p = nd.plan(MON_DAY, [T_NIGHT, T_NIGHT_V4, T_NOW_PAUSED, T_RUNNING, T_SEEDED], [J_NIGHT, J_NOW, J_RUNNING], set())
        self.assertEqual((p.hashes, p.nzos), ([], []))
        self.assertEqual(p.pending, 3)

    def test_low_tariff_resumes_only_paused_night_items(self):
        p = nd.plan(MON_2205, [T_NIGHT, T_NIGHT_V4, T_NOW_PAUSED, T_RUNNING, T_SEEDED], [J_NIGHT, J_NOW, J_RUNNING], set())
        self.assertEqual(p.hashes, ["AAA", "bbb"])
        self.assertEqual(p.nzos, ["SABnzbd_nzo_1"])
        self.assertEqual(p.pending, 0)

    def test_sunday_resumes_at_once(self):
        p = nd.plan(SUN_NOON, [T_NIGHT], [J_NIGHT], set())
        self.assertEqual((p.hashes, p.nzos, p.pending), (["AAA"], ["SABnzbd_nzo_1"], 0))

    def test_download_now_title_resumes_by_day_case_insensitive(self):
        p = nd.plan(MON_DAY, [T_NIGHT, T_NIGHT_V4], [J_NIGHT], {"aaa", "sabnzbd_nzo_1"})
        self.assertEqual((p.hashes, p.nzos, p.pending), (["AAA"], ["SABnzbd_nzo_1"], 1))

    def test_paused_item_outside_night_categories_is_never_touched(self):
        p = nd.plan(MON_2205, [T_NOW_PAUSED], [J_NOW], {"ccc", "sabnzbd_nzo_2"})
        self.assertEqual((p.hashes, p.nzos, p.pending), ([], [], 0))


class Urgent(unittest.TestCase):
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


class Fake:
    def __init__(self, torrents=(), slots=(), fail=()):
        self.torrents, self.slots, self.fail, self.calls = list(torrents), list(slots), set(fail), []

    def qbit(self, path, data=None):
        self.calls.append(("qbit", path, data))
        if "qbit" in self.fail:
            raise nd.ApiError("qbit down")
        if path == "/torrents/info":
            return self.torrents
        if path == "/torrents/start":
            return None
        raise AssertionError(path)

    def sab(self, params):
        self.calls.append(("sab", params))
        if "sab" in self.fail:
            raise nd.ApiError("sab down")
        if params.get("mode") == "queue" and "name" not in params:
            return {"queue": {"slots": self.slots}}
        return {"status": True}

    def arr(self, app):
        def call(path):
            self.calls.append((app, path))
            if app in self.fail:
                raise nd.ApiError(f"{app} down")
            if path == "/tag":
                return [{"id": 70, "label": "download-now"}]
            return {"totalRecords": 1, "records": [{"downloadId": "bbb", "series": {"tags": [70]}}]} if app == "sonarr" else {"records": []}
        return call


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "night-downloads.json"

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, f, now, dry=False):
        return nd.run(now, f.qbit, f.sab, {"radarr": f.arr("radarr"), "sonarr": f.arr("sonarr")}, self.state, dry_run=dry)

    def test_low_tariff_resumes_and_writes_zero_pending(self):
        f = Fake([T_NIGHT, T_NIGHT_V4], [J_NIGHT])
        self.assertEqual(self.go(f, MON_2205), 0)
        self.assertIn(("qbit", "/torrents/start", {"hashes": "AAA|bbb"}), f.calls)
        self.assertIn(("sab", {"mode": "queue", "name": "resume", "value": "SABnzbd_nzo_1"}), f.calls)
        s = json.loads(self.state.read_text())
        self.assertEqual((s["pending"], s["low_tariff"]), (0, True))

    def test_by_day_resumes_download_now_and_counts_the_rest(self):
        f = Fake([T_NIGHT, T_NIGHT_V4], [J_NIGHT])
        self.assertEqual(self.go(f, MON_DAY), 0)
        self.assertIn(("qbit", "/torrents/start", {"hashes": "bbb"}), f.calls)
        self.assertFalse([c for c in f.calls if c[0] == "sab" and c[1].get("name") == "resume"])
        self.assertEqual(json.loads(self.state.read_text())["pending"], 2)

    def test_dry_run_changes_nothing(self):
        f = Fake([T_NIGHT], [J_NIGHT])
        self.assertEqual(self.go(f, MON_2205, dry=True), 0)
        self.assertFalse([c for c in f.calls if c[1] == "/torrents/start" or (c[0] == "sab" and c[1].get("name"))])
        self.assertFalse(self.state.exists())

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


if __name__ == "__main__":
    unittest.main()
