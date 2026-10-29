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


def req(type_, tmdb, ext, status=2, mstatus=3, tags=(), seasons=(), is4k=False, created=None):
    media = {"tmdbId": tmdb, "status": mstatus if not is4k else 1, "status4k": mstatus if is4k else 1, "externalServiceId": None if is4k else ext, "externalServiceId4k": ext if is4k else None}
    return {"type": type_, "status": status, "is4k": is4k, "tags": list(tags), "seasons": [{"seasonNumber": n} for n in seasons], "media": media, **({"createdAt": created} if created else {})}


def movie(i, tags=(), has_file=False, available=True, min_av="released", **dates):
    return {"id": i, "tags": list(tags), "hasFile": has_file, "isAvailable": available, "minimumAvailability": min_av, **dates}


def ep(season, number, air=None, has_file=False):
    return {"seasonNumber": season, "episodeNumber": number, "airDateUtc": air, "hasFile": has_file, "monitored": True}


DN = {"radarr": 64, "sonarr": 61}


NEW_KEYS = ("searching", "fresh", "pending", "queue", "imported", "soFar")


def rstate(requests, movies=(), series=(), episodes=None, now=MON_DAY, queued=(), searching=None, commands=None, queue=None, full=False):
    out = nd.request_state(now, requests, DN, {m["id"]: m for m in movies}, {s["id"]: s for s in series}, episodes or {}, set(queued), {"radarr": set(), "sonarr": set()} if searching is None else searching, commands, queue)
    return out if full else {k: v for k, v in out.items() if k not in NEW_KEYS or v}


class RequestState(unittest.TestCase):
    def test_can_wait_movie_without_a_release_yet(self):
        self.assertEqual(rstate([req("movie", 100, 54)], [movie(54)]), {"canWait": ["movie/100"], "unreleased": {}, "noRelease": {}, "fresh": {"movie/100": None}})

    def test_download_now_tag_on_the_radarr_title_is_not_can_wait(self):
        self.assertEqual(rstate([req("movie", 100, 54)], [movie(54, tags=[1, 64])])["canWait"], [])

    def test_seerr_tag_counts_until_radarr_has_the_title_then_radarr_decides(self):
        self.assertEqual(rstate([req("movie", 100, None, status=1, tags=[64])])["canWait"], [])
        self.assertEqual(rstate([req("movie", 100, 54, tags=[64])], [movie(54)])["canWait"], ["movie/100"])
        self.assertEqual(rstate([req("movie", 100, None, status=1)])["canWait"], ["movie/100"])

    def test_done_declined_and_available_are_left_out_failed_is_a_problem(self):
        rs = [req("movie", 1, 1, mstatus=5), req("movie", 2, 2, status=3), req("movie", 3, 3, status=4), req("movie", 4, 4, status=5), req("movie", 5, 5)]
        self.assertEqual(rstate(rs, [movie(i) for i in (1, 2, 3, 4)] + [movie(5, has_file=True)]), {"canWait": [], "unreleased": {}, "noRelease": {}, "queue": {"movie/3": {"state": "problem", "message": "", "code": "seerr"}}, "imported": ["movie/5"]})

    def test_4k_request_uses_the_4k_status_and_service_id(self):
        self.assertEqual(rstate([req("movie", 100, 9, is4k=True)], [movie(9)])["canWait"], ["movie/100"])

    def test_unreleased_movie_with_its_release_date(self):
        rs = [req("movie", 53, 53), req("movie", 52, 52, tags=[64])]
        ms = [movie(53, available=False, inCinemas="2026-11-04T00:00:00Z", releaseDate="2027-02-02T00:00:00Z"), movie(52, tags=[64], available=False, inCinemas="2027-02-10T00:00:00Z", digitalRelease="2027-04-01T00:00:00Z", releaseDate="2027-04-01T00:00:00Z")]
        self.assertEqual(rstate(rs, ms), {"canWait": ["movie/53"], "unreleased": {"movie/53": "2027-02-02", "movie/52": "2027-04-01"}, "noRelease": {}})

    def test_unreleased_movie_date_follows_minimum_availability_and_past_dates_are_dropped(self):
        ms = [movie(1, available=False, min_av="inCinemas", inCinemas="2026-11-04T00:00:00Z", releaseDate="2027-02-02T00:00:00Z"), movie(2, available=False, releaseDate="2026-01-01T00:00:00Z"), movie(3, available=False)]
        self.assertEqual(rstate([req("movie", i, i) for i in (1, 2, 3)], ms)["unreleased"], {"movie/1": "2026-11-04", "movie/2": None, "movie/3": None})

    def test_series_whose_missing_episodes_have_not_aired(self):
        eps = {19: [ep(0, 1), ep(1, 1, "2026-12-25T05:00:00Z"), ep(1, 2, "2027-01-01T05:00:00Z"), ep(1, 3)]}
        self.assertEqual(rstate([req("tv", 300, 19, seasons=[1])], series=[{"id": 19, "tags": [], "status": "upcoming"}], episodes=eps), {"canWait": ["tv/300"], "unreleased": {"tv/300": "2026-12-25"}, "noRelease": {}})

    def test_series_with_an_aired_missing_episode_is_released(self):
        eps = {22: [ep(1, 1, "2026-10-14T01:00:00Z"), ep(1, 2, "2026-11-04T02:00:00Z")]}
        self.assertEqual(rstate([req("tv", 301, 22, seasons=[1])], series=[{"id": 22, "tags": [], "status": "continuing"}], episodes=eps), {"canWait": ["tv/301"], "unreleased": {}, "noRelease": {}, "fresh": {"tv/301": None}})

    def test_series_only_the_unaired_new_season_is_missing(self):
        eps = {11: [ep(1, 1, "2022-09-01T07:00:00Z", True), ep(2, 1, "2024-08-29T07:00:00Z", True), ep(3, 1, "2026-11-11T08:00:00Z")]}
        out = rstate([req("tv", 84773, 11, mstatus=4, seasons=[1, 2, 3])], series=[{"id": 11, "tags": [], "status": "continuing"}], episodes=eps)
        self.assertEqual(out, {"canWait": ["tv/84773"], "unreleased": {"tv/84773": "2026-11-11"}, "noRelease": {}, "soFar": ["tv/84773"]})

    def test_series_with_every_requested_episode_downloaded_is_complete_so_far(self):
        eps = {21: [ep(1, 1, "2024-01-06T15:00:00Z", True), ep(2, 1, "2025-01-04T15:00:00Z")]}
        self.assertEqual(rstate([req("tv", 127532, 21, mstatus=4, seasons=[1])], series=[{"id": 21, "tags": [], "status": "continuing"}], episodes=eps), {"canWait": ["tv/127532"], "unreleased": {}, "noRelease": {}, "soFar": ["tv/127532"]})
        self.assertEqual(rstate([req("tv", 127532, 21, mstatus=3, seasons=[1])], series=[{"id": 21, "tags": [], "status": "continuing"}], episodes=eps)["imported"], ["tv/127532"])

    def test_upcoming_series_without_episodes_uses_next_airing(self):
        out = rstate([req("tv", 5, 5, seasons=[1])], series=[{"id": 5, "tags": [61], "status": "upcoming", "nextAiring": "2027-03-01T20:00:00Z"}], episodes={5: []})
        self.assertEqual(out, {"canWait": [], "unreleased": {"tv/5": "2027-03-01"}, "noRelease": {}})

    def test_searched_without_a_release_gets_the_compass(self):
        searched = movie(54, lastSearchTime="2026-10-28T09:54:09Z")
        self.assertEqual(rstate([req("movie", 100, 54)], [searched])["noRelease"], {"movie/100": "2026-10-28T09:54:09Z"})
        self.assertEqual(rstate([req("movie", 100, 54)], [searched], queued={("radarr", 54)})["noRelease"], {})
        self.assertEqual(rstate([req("movie", 100, 54)], [searched], searching={"radarr": {54}})["noRelease"], {})
        self.assertEqual(rstate([req("movie", 100, 54)], [searched], searching={"radarr": nd.ALL})["noRelease"], {})
        self.assertEqual(rstate([req("movie", 100, 54)], [searched], searching={})["noRelease"], {})
        self.assertEqual(rstate([req("movie", 100, 54)], [movie(54)])["noRelease"], {})
        self.assertEqual(rstate([req("movie", 100, 54)], [movie(54, available=False, lastSearchTime="2026-10-28T09:54:09Z")])["noRelease"], {})

    def test_series_compass_from_the_missing_aired_episodes(self):
        eps = {22: [ep(1, 1, "2026-10-14T01:00:00Z") | {"lastSearchTime": "2026-10-27T23:55:29Z"}, ep(1, 2, "2026-10-21T01:00:00Z") | {"lastSearchTime": "2026-10-28T01:00:00Z"}, ep(1, 3, "2026-11-04T02:00:00Z")]}
        sr = [{"id": 22, "tags": [], "status": "continuing"}]
        self.assertEqual(rstate([req("tv", 301, 22, seasons=[1])], series=sr, episodes=eps)["noRelease"], {"tv/301": "2026-10-28T01:00:00Z"})
        self.assertEqual(rstate([req("tv", 301, 22, seasons=[1])], series=sr, episodes=eps, queued={("sonarr", 22)})["noRelease"], {})

    def test_running_searches_by_app(self):
        cmds = [{"name": "MoviesSearch", "status": "started", "body": {"movieIds": [53]}}, {"name": "MoviesSearch", "status": "queued", "body": {"movieIds": [55]}},
                {"name": "MoviesSearch", "status": "completed", "body": {"movieIds": [51]}}, {"name": "RssSync", "status": "started", "body": {}}]
        self.assertEqual(nd.running_searches(cmds), {53, 55})
        self.assertIs(nd.running_searches(cmds + [{"name": "MissingMoviesSearch", "status": "started", "body": {}}]), nd.ALL)
        self.assertEqual(nd.running_searches([{"name": "SeriesSearch", "status": "queued", "body": {"seriesId": 4}}]), {4})
        self.assertIs(nd.running_searches([{"name": "EpisodeSearch", "status": "queued", "body": {"episodeIds": [9]}}]), nd.ALL)
        self.assertEqual(nd.running_searches({"records": []}), set())

    def test_collect_asks_only_for_titles_with_open_requests(self):
        calls = []
        def seerr(path):
            calls.append(("seerr", path))
            return {"results": [req("movie", 100, 54), req("tv", 300, 19, seasons=[1]), req("movie", 7, 7, mstatus=5)]}
        def arr(app):
            def call(path):
                calls.append((app, path))
                if path == "/tag":
                    return [{"id": DN[app], "label": "download-now"}]
                return {"/movie/54": movie(54), "/series/19": {"id": 19, "tags": [], "status": "upcoming"}, "/episode?seriesId=19": [ep(1, 1)]}[path]
            return call
        out = nd.collect_request_state(MON_DAY, seerr, {"radarr": arr("radarr"), "sonarr": arr("sonarr")})
        self.assertEqual(out, {"canWait": ["movie/100", "tv/300"], "unreleased": {"tv/300": None}, "noRelease": {}, "searching": {}, "fresh": {"movie/100": None}, "pending": [], "queue": {}, "imported": [], "soFar": []})
        self.assertNotIn(("radarr", "/movie/7"), calls)
        self.assertEqual(calls[0], ("seerr", "/request?take=1000&skip=0&filter=all&sort=added"))

    def test_collect_title_gone_from_radarr_counts_as_not_added(self):
        def arr(path):
            if path == "/tag":
                return []
            raise nd.NotFound(path)
        out = nd.collect_request_state(MON_DAY, lambda p: {"results": [req("movie", 100, 54)]}, {"radarr": arr, "sonarr": arr})
        self.assertEqual(out["canWait"], ["movie/100"])


def sreq(rid, type_, tmdb, ext, seasons=(), is4k=False, mstatus=5, status=2):
    return {"id": rid, **req(type_, tmdb, ext, status=status, mstatus=mstatus, seasons=seasons, is4k=is4k)}


def season(n, files, count, size):
    return {"seasonNumber": n, "statistics": {"episodeFileCount": files, "episodeCount": count, "totalEpisodeCount": count + 2, "sizeOnDisk": size}}


FELLOWSHIP = {"id": 59, "hasFile": True, "movieFile": {"size": 45327147274}, "sizeOnDisk": 45327147274}
SHOW = {"id": 11, "seasons": [season(0, 0, 0, 0), season(1, 8, 8, 82003416606), season(2, 5, 8, 40000000000), season(3, 0, 0, 0)]}


class Sizes(unittest.TestCase):
    def test_movie_size_is_its_file_size(self):
        out = nd.request_sizes([sreq(62, "movie", 120, 59)], {59: FELLOWSHIP}, {}, {})
        self.assertEqual(out, {"62": {"size": 45327147274}})

    def test_series_sums_only_the_requested_seasons(self):
        out = nd.request_sizes([sreq(31, "tv", 84773, 11, seasons=[1])], {}, {11: SHOW}, {})
        self.assertEqual(out, {"31": {"size": 82003416606}})

    def test_series_with_missing_episodes_counts_them(self):
        out = nd.request_sizes([sreq(31, "tv", 84773, 11, seasons=[1, 2, 3])], {}, {11: SHOW}, {})
        self.assertEqual(out, {"31": {"size": 122003416606, "files": 13, "of": 16}})

    def test_not_downloaded_yet_shows_the_queued_size_once_per_download(self):
        queues = {"radarr": {"records": [{"movieId": 54, "downloadId": "A", "size": 5378662671}, {"movieId": 7, "downloadId": "B", "size": 9}]},
                  "sonarr": {"records": [{"seriesId": 4, "seasonNumber": 1, "downloadId": "P", "size": 52587106587},
                                         {"seriesId": 4, "seasonNumber": 1, "downloadId": "p", "size": 52587106587},
                                         {"seriesId": 4, "episode": {"seasonNumber": 2}, "downloadId": "Q", "size": 1000},
                                         {"seriesId": 4, "seasonNumber": 3, "downloadId": "R", "size": 7}]}}
        out = nd.request_sizes([sreq(57, "movie", 1480613, 54, mstatus=3), sreq(54, "tv", 213375, 4, seasons=[1, 2], mstatus=3)],
                               {54: {"id": 54, "hasFile": False, "sizeOnDisk": 0}}, {4: {"id": 4, "seasons": [season(1, 0, 8, 0), season(2, 0, 8, 0)]}}, queues)
        self.assertEqual(out, {"57": {"queued": 5378662671}, "54": {"queued": 52587107587}})

    def test_unknown_title_4k_and_nothing_on_disk_or_queued_are_left_out(self):
        out = nd.request_sizes([sreq(1, "movie", 1, None), sreq(2, "movie", 2, 99), sreq(3, "movie", 3, 59, is4k=True), sreq(4, "movie", 4, 54, mstatus=3)],
                               {59: FELLOWSHIP, 54: {"id": 54, "hasFile": False, "sizeOnDisk": 0}}, {}, {"radarr": {"records": []}})
        self.assertEqual(out, {})

    def test_collect_adds_the_sizes_of_every_request_when_given_the_queues(self):
        calls = []
        def seerr(path):
            return {"results": [sreq(62, "movie", 120, 59), sreq(31, "tv", 84773, 11, seasons=[1])]}
        def arr(app):
            def call(path):
                calls.append((app, path))
                return {"/tag": [], "/movie": [FELLOWSHIP], "/series": [SHOW]}[path]
            return call
        apps = {"radarr": arr("radarr"), "sonarr": arr("sonarr")}
        out = nd.collect_request_state(MON_DAY, seerr, apps, queues={"radarr": {"records": []}, "sonarr": {"records": []}})
        self.assertEqual(out["sizes"], {"62": {"size": 45327147274}, "31": {"size": 82003416606}})
        self.assertNotIn("sizes", nd.collect_request_state(MON_DAY, seerr, apps))
        self.assertEqual(calls.count(("radarr", "/movie")), 1)

    def test_media_sizes_for_every_title_by_tmdb_id(self):
        fellowship = FELLOWSHIP | {"tmdbId": 120, "movieFile": {"size": 45327147274, "quality": {"quality": {"resolution": 2160}}, "mediaInfo": {"videoDynamicRangeType": "HDR10"}}}
        plain = {"id": 3, "tmdbId": 7972, "hasFile": True, "movieFile": {"size": 7190407839, "quality": {"quality": {"resolution": 1080}}, "mediaInfo": {"videoDynamicRangeType": ""}}}
        waiting = {"id": 54, "tmdbId": 1480613, "hasFile": False, "sizeOnDisk": 0}
        nothing = {"id": 55, "tmdbId": 1, "hasFile": False, "sizeOnDisk": 0}
        show = SHOW | {"tmdbId": 84773}
        queues = {"radarr": {"records": [{"movieId": 54, "downloadId": "A", "size": 5378662671}]}, "sonarr": {"records": []}}
        out = nd.media_sizes({59: fellowship, 3: plain, 54: waiting, 55: nothing}, {11: show, 12: {"id": 12, "tmdbId": 9, "seasons": [season(1, 0, 4, 0)]}}, queues)
        self.assertEqual(out, {
            "movie/120": {"size": 45327147274, "quality": "2160p HDR10"},
            "movie/7972": {"size": 7190407839, "quality": "1080p"},
            "movie/1480613": {"queued": 5378662671},
            "tv/84773": {"size": 122003416606, "files": 13, "of": 16, "seasons": {"1": {"size": 82003416606, "files": 8, "of": 8}, "2": {"size": 40000000000, "files": 5, "of": 8}}},
        })

    def test_collect_adds_the_media_sizes_beside_the_request_sizes(self):
        def arr(app):
            def call(path):
                return {"/tag": [], "/movie": [FELLOWSHIP | {"tmdbId": 120}], "/series": []}[path]
            return call
        out = nd.collect_request_state(MON_DAY, lambda p: {"results": []}, {"radarr": arr("radarr"), "sonarr": arr("sonarr")}, queues={})
        self.assertEqual(out["media"], {"movie/120": {"size": 45327147274}})

    def test_collect_sizes_unreadable_leaves_them_out(self):
        def arr(path):
            if path == "/tag":
                return []
            raise nd.ApiError("down")
        out = nd.collect_request_state(MON_DAY, lambda p: {"results": [sreq(62, "movie", 120, 59)]}, {"radarr": arr, "sonarr": arr}, queues={})
        self.assertNotIn("sizes", out)


NOW_UTC = datetime.datetime(2026, 10, 28, 12, 25, tzinfo=datetime.timezone.utc)
CMDS = [{"name": "MoviesSearch", "status": "started", "queued": "2026-10-28T12:24:03Z", "started": "2026-10-28T12:24:03Z", "body": {"movieIds": [59]}},
        {"name": "MoviesSearch", "status": "queued", "queued": "2026-10-28T12:24:18Z", "body": {"movieIds": [60]}},
        {"name": "MoviesSearch", "status": "queued", "queued": "2026-10-28T12:24:30Z", "body": {"movieIds": [61, 59]}},
        {"name": "MoviesSearch", "status": "completed", "queued": "2026-10-28T12:00:00Z", "body": {"movieIds": [62]}}]


class SearchFeedback(unittest.TestCase):
    def test_search_commands_started_with_time_queued_with_searches_ahead(self):
        out = nd.search_commands(CMDS)
        self.assertEqual(out, {59: {"status": "started", "started": "2026-10-28T12:24:03Z"}, 60: {"status": "queued", "ahead": 1}, 61: {"status": "queued", "ahead": 2}})
        self.assertEqual(nd.search_commands([{"name": "SeriesSearch", "status": "queued", "queued": "a", "body": {"seriesId": 4}}]), {4: {"status": "queued", "ahead": 0}})
        self.assertEqual(nd.search_commands([{"name": "MissingMoviesSearch", "status": "started", "started": "x", "body": {}}]), {nd.ALL: {"status": "started", "started": "x"}})
        self.assertEqual(nd.search_commands({"records": []}), {})

    def test_searching_titles_by_seerr_key(self):
        out = rstate([req("movie", 120, 59), req("movie", 121, 60), req("movie", 122, 62, created="2026-10-28T12:20:00.000Z")], [movie(59), movie(60), movie(62, lastSearchTime="2026-10-28T12:21:00Z")],
                     now=NOW_UTC, searching={"radarr": {59, 60, 61}}, commands={"radarr": nd.search_commands(CMDS)}, full=True)
        self.assertEqual(out["searching"], {"movie/120": {"status": "started", "started": "2026-10-28T12:24:03Z"}, "movie/121": {"status": "queued", "ahead": 1}})
        self.assertEqual((out["fresh"], out["noRelease"]), ({}, {"movie/122": "2026-10-28T12:21:00Z"}))

    def test_a_search_over_all_missing_titles_counts_for_every_released_title_without_a_download(self):
        cmds = {"radarr": {nd.ALL: {"status": "started", "started": "2026-10-28T04:17:00Z"}}}
        out = rstate([req("movie", 1, 1), req("movie", 2, 2), req("movie", 3, 3)], [movie(1), movie(2, available=False), movie(3)], now=NOW_UTC, searching={"radarr": nd.ALL}, commands=cmds, queue={("radarr", 3): {"state": "downloading"}}, full=True)
        self.assertEqual(out["searching"], {"movie/1": {"status": "started", "started": "2026-10-28T04:17:00Z"}})

    def test_just_requested_without_a_search_yet_is_fresh_with_its_request_time(self):
        rs = [req("movie", 1, 1, created="2026-10-28T12:24:50.000Z"), req("movie", 2, None, created="2026-10-28T12:24:55.000Z"), req("movie", 3, 3, created="2026-10-28T12:00:00.000Z"), req("movie", 4, 4, status=1, created="2026-10-28T12:24:00.000Z")]
        out = rstate(rs, [movie(1), movie(3, lastSearchTime="2026-10-28T11:00:00Z"), movie(4)], now=NOW_UTC, full=True)
        self.assertEqual(out["fresh"], {"movie/1": "2026-10-28T12:24:50.000Z", "movie/2": "2026-10-28T12:24:55.000Z", "movie/3": "2026-10-28T12:00:00.000Z"})
        self.assertEqual(out["noRelease"], {})
        self.assertEqual(out["pending"], ["movie/4"])

    def test_queue_states_problem_over_importing_over_downloading_over_paused(self):
        recs = [{"movieId": 1, "status": "paused", "trackedDownloadState": "downloading", "trackedDownloadStatus": "ok"},
                {"movieId": 1, "status": "downloading", "trackedDownloadState": "downloading", "trackedDownloadStatus": "ok"},
                {"movieId": 2, "status": "completed", "trackedDownloadState": "importPending", "trackedDownloadStatus": "ok"},
                {"movieId": 3, "status": "completed", "trackedDownloadState": "importBlocked", "trackedDownloadStatus": "warning",
                 "statusMessages": [{"title": "Film.2020.2160p", "messages": ["No files found are eligible for import in /data/torrents/movies/Film.2020.2160p"]}]},
                {"movieId": 3, "status": "downloading", "trackedDownloadState": "downloading"},
                {"movieId": 4, "status": "paused"},
                {"movieId": 5, "status": "failed", "trackedDownloadState": "failedPending", "trackedDownloadStatus": "error", "errorMessage": "Download failed: see https://x.example.com/a?apikey=s3cr3t"},
                {"movieId": 6, "status": "downloading", "trackedDownloadStatus": "warning", "statusMessages": [{"messages": ["qBittorrent is downloading metadata"]}]},
                {"downloadId": "x"}]
        out = nd.queue_states("radarr", {"records": recs})
        self.assertEqual(out[("radarr", 1)], {"state": "downloading"})
        self.assertEqual(out[("radarr", 2)], {"state": "importing"})
        self.assertEqual(out[("radarr", 3)], {"state": "problem", "message": "No files found are eligible for import in …"})
        self.assertEqual(out[("radarr", 4)], {"state": "paused"})
        self.assertEqual(out[("radarr", 5)], {"state": "problem", "message": "Download failed: see …"})
        self.assertEqual(out[("radarr", 6)], {"state": "downloading"})
        self.assertEqual(len(out), 6)
        self.assertLessEqual(len(nd.brief({"errorMessage": "x" * 500})), 100)

    def test_queue_and_seerr_failed_by_seerr_key(self):
        out = rstate([req("movie", 1, 1), req("movie", 2, 2, status=4)], [movie(1, lastSearchTime="2026-10-28T11:00:00Z"), movie(2)], now=NOW_UTC, queue={("radarr", 1): {"state": "importing"}}, full=True)
        self.assertEqual(out["queue"], {"movie/1": {"state": "importing"}, "movie/2": {"state": "problem", "message": "", "code": "seerr"}})
        self.assertEqual(out["noRelease"], {})

    def test_file_on_disk_while_seerr_still_shows_the_request_is_imported(self):
        out = rstate([req("movie", 1, 1), req("tv", 2, 2, seasons=[1])], [movie(1, has_file=True)], [{"id": 2, "tags": [], "status": "continuing"}], {2: [ep(1, 1, "2026-01-01T00:00:00Z", True)]}, now=NOW_UTC, full=True)
        self.assertEqual(out["imported"], ["movie/1", "tv/2"])
        self.assertEqual(out["canWait"], [])

    def test_series_complete_so_far_aired_all_here_unaired_left(self):
        sr = [{"id": 30, "tags": [], "status": "continuing", "nextAiring": "2027-01-08T15:00:00Z"}]
        eps = {30: [ep(1, 1, "2024-01-04T15:00:00Z", True), ep(1, 2, "2024-01-11T15:00:00Z", True), ep(2, 1, "2027-01-08T15:00:00Z")]}
        out = rstate([req("tv", 400, 30, mstatus=4, seasons=[1, 2])], series=sr, episodes=eps, now=NOW_UTC, full=True)
        self.assertEqual((out["soFar"], out["unreleased"], out["imported"]), (["tv/400"], {"tv/400": "2027-01-08"}, []))
        eps[30][1]["hasFile"] = False
        out = rstate([req("tv", 400, 30, mstatus=4, seasons=[1, 2])], series=sr, episodes=eps, now=NOW_UTC, full=True)
        self.assertEqual(out["soFar"], [])

    def test_requested_seasons_done_while_seerr_shows_partly_is_complete_so_far_with_the_next_airing(self):
        sr = [{"id": 31, "tags": [], "status": "continuing", "nextAiring": "2027-01-08T15:00:00Z"}]
        out = rstate([req("tv", 401, 31, mstatus=4, seasons=[1])], series=sr, episodes={31: [ep(1, 1, "2024-01-04T15:00:00Z", True)]}, now=NOW_UTC, full=True)
        self.assertEqual((out["soFar"], out["unreleased"], out["imported"]), (["tv/401"], {"tv/401": "2027-01-08"}, []))
        ended = [{"id": 31, "tags": [], "status": "ended"}]
        out = rstate([req("tv", 401, 31, mstatus=4, seasons=[1])], series=ended, episodes={31: [ep(1, 1, "2024-01-04T15:00:00Z", True)]}, now=NOW_UTC, full=True)
        self.assertEqual((out["soFar"], out["unreleased"]), (["tv/401"], {}))

    def test_every_open_request_not_available_lands_in_a_state(self):
        later = "2027-01-01T00:00:00Z"
        rs = [req("movie", i, i, created="2026-10-28T12:00:00.000Z") for i in range(1, 9)] + [req("movie", 9, None), req("movie", 10, 10, status=1), req("movie", 11, 11, status=4), req("movie", 12, 12, tags=[64])]
        ms = [movie(1), movie(2, lastSearchTime="2026-10-28T12:10:00Z"), movie(3, lastSearchTime="2026-10-28T11:00:00Z"), movie(4, available=False, releaseDate=later), movie(5), movie(6), movie(7), movie(8, has_file=True), movie(10), movie(11), movie(12, tags=[64])]
        out = rstate(rs, ms, now=NOW_UTC, searching={"radarr": {5}}, commands={"radarr": {5: {"status": "started", "started": "x"}}},
                     queue={("radarr", 6): {"state": "paused"}, ("radarr", 7): {"state": "problem", "message": "m"}}, full=True)
        covered = set(out["searching"]) | set(out["fresh"]) | set(out["pending"]) | set(out["queue"]) | set(out["unreleased"]) | set(out["noRelease"]) | set(out["imported"])
        self.assertEqual(covered, {f"movie/{i}" for i in range(1, 13)})


class Fast(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.public = pathlib.Path(self.tmp.name) / "seerr" / "night-waiting.json"
        self.rs = self.public.parent / "request-state.json"
        self.lock = pathlib.Path(self.tmp.name) / "request-state.lock"

    def tearDown(self):
        self.tmp.cleanup()

    def apps(self, commands, calls):
        def arr(app):
            def call(path):
                calls.append((app, path))
                if path == "/command":
                    return commands.get(app, [])
                if path == "/tag":
                    return []
                if path.startswith("/queue"):
                    return {"records": []}
                return movie(59)
            return call
        return {"radarr": arr("radarr"), "sonarr": arr("sonarr")}

    def seerr(self, results, calls):
        def call(path):
            calls.append(("seerr", path))
            return {"results": results}
        return call

    def test_idle_reads_only_the_commands_and_the_newest_requests_and_writes_nothing(self):
        calls = []
        self.assertEqual(nd.run_fast(NOW_UTC, self.apps({}, calls), self.seerr([req("movie", 1, 1, created="2026-10-28T11:00:00.000Z")], calls), self.public, self.lock), 0)
        self.assertFalse(self.rs.exists())
        self.assertEqual(calls, [("radarr", "/command"), ("sonarr", "/command"), ("seerr", "/request?take=10&skip=0&filter=all&sort=added")])

    def test_a_running_search_writes_the_request_state(self):
        calls = []
        self.assertEqual(nd.run_fast(NOW_UTC, self.apps({"radarr": CMDS}, calls), self.seerr([req("movie", 120, 59)], calls), self.public, self.lock), 0)
        out = json.loads(self.rs.read_text())
        self.assertEqual(out["searching"], {"movie/120": {"status": "started", "started": "2026-10-28T12:24:03Z"}})
        self.assertIn("updated", out)

    def test_a_request_from_the_last_minutes_writes_the_request_state(self):
        calls = []
        nd.run_fast(NOW_UTC, self.apps({}, calls), self.seerr([req("movie", 120, 59, created="2026-10-28T12:24:30.000Z")], calls), self.public, self.lock)
        self.assertEqual(json.loads(self.rs.read_text())["fresh"], {"movie/120": "2026-10-28T12:24:30.000Z"})

    def test_search_just_ended_one_more_write_clears_it(self):
        self.rs.parent.mkdir(parents=True)
        self.rs.write_text(json.dumps({"searching": {"movie/120": {"status": "started"}}}))
        calls = []
        nd.run_fast(NOW_UTC, self.apps({}, calls), self.seerr([req("movie", 120, 59)], calls), self.public, self.lock)
        self.assertEqual(json.loads(self.rs.read_text())["searching"], {})

    def test_busy_lock_skips(self):
        import fcntl
        with open(self.lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            calls = []
            self.assertEqual(nd.run_fast(NOW_UTC, self.apps({"radarr": CMDS}, calls), self.seerr([req("movie", 120, 59)], calls), self.public, self.lock), 0)
        self.assertFalse(self.rs.exists())

    def test_a_fast_write_keeps_the_sizes_of_the_last_full_run(self):
        self.rs.parent.mkdir(parents=True)
        self.rs.write_text(json.dumps({"sizes": {"62": {"size": 45327147274}}, "media": {"movie/120": {"size": 45327147274}}}))
        calls = []
        nd.run_fast(NOW_UTC, self.apps({"radarr": CMDS}, calls), self.seerr([req("movie", 120, 59)], calls), self.public, self.lock)
        out = json.loads(self.rs.read_text())
        self.assertEqual(out["sizes"], {"62": {"size": 45327147274}})
        self.assertEqual(out["media"], {"movie/120": {"size": 45327147274}})
        self.assertNotIn(("radarr", "/movie"), calls)

    def test_arr_down_fails_without_writing(self):
        def down(path):
            raise nd.ApiError("down")
        self.assertEqual(nd.run_fast(NOW_UTC, {"radarr": down, "sonarr": down}, self.seerr([], []), self.public, self.lock), 1)
        self.assertFalse(self.rs.exists())


class Revert(unittest.TestCase):
    def arr(self, titles, episodes=None, dn=64):
        calls = []
        def call(path, method="GET", body=None):
            calls.append((path, method, body))
            if path == "/tag":
                return [{"id": dn, "label": "download-now"}]
            if path == f"/tag/detail/{dn}":
                return {"movieIds": [t["id"] for t in titles], "seriesIds": [t["id"] for t in titles]}
            if path.startswith("/movie/") and method == "GET" or path.startswith("/series/") and method == "GET":
                return next(t for t in titles if t["id"] == int(path.rsplit("/", 1)[1]))
            if path.startswith("/episode?seriesId="):
                return (episodes or {}).get(int(path.split("=")[1]), [])
            return None
        return call, calls

    def edits(self, calls):
        return [(p, b) for p, m, b in calls if m == "PUT"]

    def test_download_now_searched_nothing_found_goes_back_to_the_night(self):
        call, calls = self.arr([movie(54, tags=[1, 64], lastSearchTime="2026-10-28T09:54:09Z"), movie(53, tags=[64], available=False, lastSearchTime="2026-10-28T10:00:00Z")])
        out = nd.revert_unfound({"radarr": call}, set(), {"radarr": set()})
        self.assertEqual(self.edits(calls), [("/movie/editor", {"movieIds": [54, 53], "tags": [64], "applyTags": "remove"})])
        self.assertEqual(out, ["radarr: movie 54 download-now removed: searched, nothing found", "radarr: movie 53 download-now removed: searched, nothing found"])

    def test_kept_while_grabbed_searching_downloaded_or_never_searched(self):
        titles = [movie(1, tags=[64], lastSearchTime="x"), movie(2, tags=[64], lastSearchTime="x"), movie(3, tags=[64], has_file=True, lastSearchTime="x"), movie(4, tags=[64])]
        call, calls = self.arr(titles)
        self.assertEqual(nd.revert_unfound({"radarr": call}, {("radarr", 1)}, {"radarr": {2}}), [])
        self.assertEqual(self.edits(calls), [])
        call, calls = self.arr([movie(5, tags=[64], lastSearchTime="x")])
        self.assertEqual(nd.revert_unfound({"radarr": call}, set(), {"radarr": nd.ALL}), [])
        self.assertEqual(nd.revert_unfound({"radarr": call}, set(), {}), [])

    def test_series_searched_with_nothing_found(self):
        sr = [{"id": 22, "tags": [61], "statistics": {"episodeFileCount": 0, "episodeCount": 8}}, {"id": 23, "tags": [61], "statistics": {"episodeFileCount": 0, "episodeCount": 8}}]
        eps = {22: [ep(1, 1, "2026-10-14T01:00:00Z") | {"lastSearchTime": "2026-10-28T01:00:00Z"}], 23: [ep(1, 1, "2026-10-14T01:00:00Z")]}
        call, calls = self.arr(sr, eps, dn=61)
        self.assertEqual(nd.revert_unfound({"sonarr": call}, set(), {"sonarr": set()}), ["sonarr: series 22 download-now removed: searched, nothing found"])
        self.assertEqual(self.edits(calls), [("/series/editor", {"seriesIds": [22], "tags": [61], "applyTags": "remove"})])

    def test_dry_run_only_reports(self):
        call, calls = self.arr([movie(54, tags=[64], lastSearchTime="x")])
        self.assertEqual(nd.revert_unfound({"radarr": call}, set(), {"radarr": set()}, dry_run=True), ["radarr: movie 54 download-now would be removed: searched, nothing found"])
        self.assertEqual(self.edits(calls), [])


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

    def go(self, f, now, dry=False, seerr=None):
        return nd.run(now, f.qbit, f.sab, {"radarr": f.arr("radarr"), "sonarr": f.arr("sonarr")}, self.state, dry_run=dry, public_path=self.public, seerr=seerr)

    def test_request_state_written_beside_the_night_list(self):
        f = Fake([T_NIGHT], [J_NIGHT])
        self.assertEqual(self.go(f, MON_2205, seerr=lambda p: {"results": [req("movie", 100, None, status=1)]}), 0)
        rs = self.public.parent / "request-state.json"
        out = json.loads(rs.read_text())
        self.assertEqual((out["canWait"], out["unreleased"]), (["movie/100"], {}))
        self.assertIsNotNone(datetime.datetime.fromisoformat(out["updated"]).tzinfo)
        self.assertEqual(rs.stat().st_mode & 0o777, 0o644)

    def test_seerr_down_keeps_the_old_request_state_and_fails_but_the_night_list_is_written(self):
        rs = self.public.parent / "request-state.json"
        rs.parent.mkdir(parents=True)
        rs.write_text('{"old": true}')
        def down(path):
            raise nd.ApiError("seerr down")
        self.assertEqual(self.go(Fake([T_NIGHT], [J_NIGHT]), MON_DAY, seerr=down), 1)
        self.assertEqual(json.loads(rs.read_text()), {"old": True})
        self.assertTrue(self.public.exists())

    def test_dry_run_writes_no_request_state(self):
        self.go(Fake([]), MON_DAY, dry=True, seerr=lambda p: {"results": []})
        self.assertFalse((self.public.parent / "request-state.json").exists())

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

    def test_full_run_writes_the_sizes_from_the_queue(self):
        f = Fake([])
        arr = f.arr
        def sized(app):
            call = arr(app)
            def inner(path):
                if path == "/movie":
                    return [{"id": 54, "hasFile": False, "sizeOnDisk": 0}]
                if path == "/series":
                    return []
                if app == "radarr" and path.startswith("/queue"):
                    return {"records": [{"movieId": 54, "downloadId": "A", "size": 5378662671}]}
                return call(path)
            return inner
        f.arr = sized
        self.assertEqual(self.go(f, MON_DAY, seerr=lambda p: {"results": [sreq(57, "movie", 1480613, 54, mstatus=3)]}), 0)
        self.assertEqual(json.loads((self.public.parent / "request-state.json").read_text())["sizes"], {"57": {"queued": 5378662671}})

    def test_sizes_unreadable_keep_the_old_sizes(self):
        rs = self.public.parent / "request-state.json"
        rs.parent.mkdir(parents=True)
        rs.write_text(json.dumps({"sizes": {"62": {"size": 1}}}))
        self.assertEqual(self.go(Fake([]), MON_DAY, seerr=lambda p: {"results": []}), 0)
        self.assertEqual(json.loads(rs.read_text())["sizes"], {"62": {"size": 1}})

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


class DiskHold(unittest.TestCase):
    setUp, tearDown = Run.setUp, Run.tearDown
    GIB = 1024 ** 3
    LOW, OK, BETWEEN = 5700 * GIB, 5900 * GIB, 5800 * GIB

    def go(self, f, now, free):
        return nd.run(now, f.qbit, f.sab, {"radarr": f.arr("radarr"), "sonarr": f.arr("sonarr")}, self.state, public_path=self.public, free=free)

    def test_low_space_stops_every_downloading_torrent_and_starts_none(self):
        f = Fake([T_NIGHT, tor("ddd", 5, state="downloading", cat="radarr"), tor("ggg", 6, state="stalledDL", cat="sonarr"), T_SEEDING], [])
        self.go(f, MON_2205, self.LOW)
        acts = f.actions()
        self.assertEqual(acts, [("qbit", "/torrents/stop", {"hashes": "ddd|ggg"})])
        self.assertEqual(json.loads(self.state.read_text())["disk_held"], ["ddd", "ggg"])

    def test_held_until_free_is_back_above_the_margin_then_only_held_ones_restart(self):
        f = Fake([tor("ddd", 5, state="downloading", cat="radarr")], [])
        self.go(f, MON_DAY, self.LOW)
        f = Fake([tor("ddd", 5, state="stoppedDL", cat="radarr"), T_NOW_PAUSED], [])
        self.go(f, MON_DAY, self.BETWEEN)
        self.assertEqual(f.actions(), [])
        self.assertEqual(json.loads(self.state.read_text())["disk_held"], ["ddd"])
        self.go(f, MON_DAY, self.OK)
        self.assertEqual(f.actions(), [("qbit", "/torrents/start", {"hashes": "ddd"})])
        self.assertEqual(json.loads(self.state.read_text())["disk_held"], [])

    def test_held_night_torrent_is_left_to_the_night_rules(self):
        f = Fake([tor("AAA", 100, state="downloading", cat="radarr-night")], [])
        self.go(f, MON_2205, self.LOW)
        f = Fake([tor("AAA", 100, state="stoppedDL", cat="radarr-night")], [])
        self.go(f, MON_DAY, self.OK)
        self.assertEqual(f.actions(), [])

    def test_free_unknown_changes_nothing(self):
        f = Fake([T_NIGHT], [J_NIGHT])
        self.go(f, MON_2205, None)
        self.assertIn(("qbit", "/torrents/start", {"hashes": "AAA"}), f.actions())

    def test_threshold_defaults(self):
        self.assertEqual((nd.MIN_FREE_GIB, nd.RESUME_MARGIN_GIB), (5758, 100))


if __name__ == "__main__":
    unittest.main()
