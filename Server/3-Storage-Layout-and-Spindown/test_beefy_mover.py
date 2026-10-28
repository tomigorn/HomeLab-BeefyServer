#!/usr/bin/env python3
"""Tests for beefy_mover.py (nightly SSD -> HDD demotion policy).

The bytes are moved by tier-move (copy, verify, delete), covered by test-tier-move; the end-to-end class here runs the real tier-move against scratch branches.
Run: ./test_beefy_mover.py
"""
import datetime as dt
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bm", HERE / "beefy_mover.py")
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)

TZ = ZoneInfo("Europe/Zurich")
GIB = 1024 ** 3
MIB = 1024 ** 2
NOW = dt.datetime(2026, 10, 29, 4, 2, tzinfo=TZ)
DAY = 86400


class FakeStat:
    def __init__(self, total, free):
        self.f_frsize, self.f_blocks, self.f_bavail = 1, total, free


class Tree(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.hot, self.cold = self.tmp / "hot", self.tmp / "cold"
        self.cold.mkdir()
        (self.hot / "media/movies").mkdir(parents=True)
        (self.hot / "media/tv").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def file(self, rel, size=1000, age_days=10, branch=None):
        p = (branch or self.hot) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        t = NOW.timestamp() - age_days * DAY
        os.utime(p, (t, t))
        return p

    def conf(self, **kw):
        env = {"HOT": str(self.hot), "COLD": str(self.cold), "START_USED_PCT": "70", "TARGET_USED_PCT": "60",
               "MAX_MOVE_GIB": "500", "SETTLE_HOURS": "0", "DEADLINE": "04:40", "RATE_MIB_S": "150",
               "HDD_RESERVE_PCT": "10", "DRY_RUN": "0", "PINS": str(self.tmp / "pins")}
        env.update({k: str(v) for k, v in kw.items()})
        return bm.Conf.from_env(env)


class Scan(Tree):
    def test_units_are_movie_folders_and_whole_series_folders(self):
        self.file("media/movies/Dune (2021)/Dune.mkv")
        self.file("media/movies/Dune (2021)/Dune.nfo")
        self.file("media/movies/Dune (2021)/extras/Making of.mkv")
        self.file("media/tv/Show/Season 01/S01E01.mkv")
        self.file("media/tv/Show/Season 01/S01E02.mkv")
        self.file("media/tv/Show/Season 02/S02E01.mkv")
        self.file("media/tv/Show/poster.jpg")
        units = {u.rel: u for u in bm.scan(self.hot, ["media/movies", "media/tv"])}
        self.assertEqual(sorted(units), ["media/movies/Dune (2021)", "media/tv/Show"])
        self.assertEqual(len(units["media/movies/Dune (2021)"].videos), 2)
        self.assertEqual(units["media/tv/Show"].size, 3000)

    def test_sidecar_only_folder_is_no_unit(self):
        self.file("media/movies/Gone/Gone.nfo")
        self.assertEqual(bm.scan(self.hot, ["media/movies"]), [])

    def test_episode_straight_in_the_series_folder_belongs_to_the_series(self):
        self.file("media/tv/Show/S01E01.mkv")
        self.file("media/tv/Show/Season 02/S02E01.mkv")
        self.file("media/tv/stray.mkv")
        self.assertEqual([(u.rel, len(u.videos)) for u in bm.scan(self.hot, ["media/tv"])], [("media/tv/Show", 2)])

    def test_missing_root_is_fine(self):
        self.assertEqual(bm.scan(self.hot, ["media/nope"]), [])


def snap(played=None, added=None, jf_paths=None):
    return bm.Snapshot(played or {}, added or {}, jf_paths if jf_paths is not None else set())


def ago(days):
    return NOW.timestamp() - days * DAY


class Classify(Tree):
    def unit(self, rel="media/movies/A", **kw):
        self.file(rel + "/A.mkv", **kw)
        return bm.scan(self.hot, ["media/movies"])[0]

    def test_eligible(self):
        self.assertIsNone(bm.skip_reason(self.unit(), self.conf(), NOW, pins=[], is_open=lambda p: False))

    def test_hardlinked_is_seeding(self):
        self.unit()
        os.link(self.hot / "media/movies/A/A.mkv", self.tmp / "seed.mkv")
        u = bm.scan(self.hot, ["media/movies"])[0]
        self.assertEqual(bm.skip_reason(u, self.conf(), NOW, [], lambda p: False), "seeding (hardlinked)")

    def test_open_file(self):
        self.assertEqual(bm.skip_reason(self.unit(), self.conf(), NOW, [], lambda p: True), "in use")

    def test_recent_change_is_an_import_in_progress(self):
        u = self.unit()
        self.assertEqual(bm.skip_reason(u, self.conf(SETTLE_HOURS=24), dt.datetime.now(TZ), [], lambda p: False), "changed in the last 24 h")

    def test_partial_import_file(self):
        self.unit()
        self.file("media/movies/A/A.mkv.partial~")
        u = bm.scan(self.hot, ["media/movies"])[0]
        self.assertEqual(bm.skip_reason(u, self.conf(), NOW, [], lambda p: False), "import in progress")

    def test_pinned(self):
        self.assertEqual(bm.skip_reason(self.unit(), self.conf(), NOW, ["media/movies/A"], lambda p: False), "pinned")

    def test_pins_file_parsing(self):
        p = self.tmp / "pins"
        p.write_text("# comment\n\nmedia/movies/A  # keep\n /srv/video/media/tv/B/\n")
        self.assertEqual(bm.read_pins(p), ["media/movies/A", "media/tv/B"])
        self.assertEqual(bm.read_pins(self.tmp / "missing"), [])


class Recency(Tree):
    def test_played_or_added_inside_the_idle_days(self):
        self.file("media/movies/A/A.mkv")
        u = bm.scan(self.hot, ["media/movies"])[0]
        self.assertEqual(bm.recency(u, snap({"media/movies/A": ago(29)}, {"media/movies/A": ago(100)}), NOW, 30), "played in the last 30 days")
        self.assertIsNone(bm.recency(u, snap({"media/movies/A": ago(31)}, {"media/movies/A": ago(100)}), NOW, 30))
        self.assertEqual(bm.recency(u, snap({}, {"media/movies/A": ago(10)}), NOW, 30), "added in the last 30 days")
        self.assertIsNone(bm.recency(u, snap({}, {"media/movies/A": ago(10)}), NOW, 7))

    def test_unknown_to_the_arrs_counts_as_added_today(self):
        self.file("media/movies/A/A.mkv", age_days=400)
        u = bm.scan(self.hot, ["media/movies"])[0]
        self.assertEqual(bm.recency(u, snap(), NOW, 7), "added in the last 7 days")

    def test_order_oldest_last_use_first(self):
        for t in ("New", "Old", "OldPlayed", "Mid"):
            self.file(f"media/movies/{t}/{t}.mkv")
        units = bm.scan(self.hot, ["media/movies"])
        sn = snap({"media/movies/OldPlayed": ago(45)},
                  {"media/movies/New": ago(40), "media/movies/Old": ago(300), "media/movies/OldPlayed": ago(400), "media/movies/Mid": ago(50)})
        self.assertEqual([u.rel for u in bm.order(units, sn, NOW)],
                         ["media/movies/Old", "media/movies/Mid", "media/movies/OldPlayed", "media/movies/New"])


def apis(users=(("u1",), ("u2",)), fail=None):
    def jf(path):
        if fail == "jf":
            raise OSError("down")
        if path == "/Users":
            return [{"Id": u[0]} for u in users]
        u1 = "userId=u1" in path
        return {"Items": [
            {"Path": "/media/tv/Show (2019)/Season 01/E1.mkv", "UserData": {"LastPlayedDate": "2026-09-01T20:00:00.1234567Z" if u1 else None}},
            {"Path": "/media/tv/Show (2019)/Season 04/E9.mkv", "UserData": {"LastPlayedDate": None if u1 else "2026-10-01T20:00:00.0000000Z"}},
            {"Path": "/media/movies/Dune/Dune.mkv", "UserData": {"Played": False}},
            {"Name": "no path"}]}

    def radarr(path):
        if fail == "radarr":
            raise OSError("down")
        return [{"path": "/data/media/movies/Dune", "movieFile": {"dateAdded": "2026-01-01T00:00:00Z"}}, {"path": "/data/media/movies/NoFile"}]

    def sonarr(path):
        if path == "/api/v3/series":
            return [{"id": 7, "path": "/data/media/tv/Show (2019)", "statistics": {"episodeFileCount": 2}}]
        if fail == "sonarr":
            raise ValueError("bad json")
        return [{"dateAdded": "2025-03-01T00:00:00Z"}, {"dateAdded": "2026-10-20T00:00:00Z"}]
    return jf, radarr, sonarr


class Snap(unittest.TestCase):
    ROOTS = ["media/movies", "media/tv"]

    def test_played_is_the_latest_play_per_title_across_users_and_episodes(self):
        sn = bm.snapshot(*apis(), self.ROOTS)
        self.assertEqual(sn.played, {"media/tv/Show (2019)": bm.jf_time("2026-10-01T20:00:00Z")})
        self.assertIn("media/movies/Dune/Dune.mkv", sn.jf_paths)

    def test_added_is_radarr_file_date_and_newest_sonarr_episode_file(self):
        sn = bm.snapshot(*apis(), self.ROOTS)
        self.assertEqual(sn.added, {"media/movies/Dune": bm.jf_time("2026-01-01T00:00:00Z"), "media/tv/Show (2019)": bm.jf_time("2026-10-20T00:00:00Z")})

    def test_any_source_failing_fails_closed(self):
        for f in ("jf", "radarr", "sonarr"):
            with self.assertRaises(bm.SourceError, msg=f):
                bm.snapshot(*apis(fail=f), self.ROOTS)
        with self.assertRaises(bm.SourceError):
            bm.snapshot(*apis(users=()), self.ROOTS)

    def test_jellyfin_times(self):
        self.assertEqual(bm.jf_time("2026-10-20T19:33:12.1234567Z"), dt.datetime(2026, 10, 20, 19, 33, 12, tzinfo=dt.timezone.utc).timestamp())
        self.assertIsNone(bm.jf_time("0001-01-01T00:00:00.0000000Z"))
        self.assertIsNone(bm.jf_time(None))


class Stub:
    """Fake disk: moving a unit frees its size on the SSD and uses it on the HDD."""

    def __init__(self, test, ssd_total, ssd_used, hdd_total=28000, hdd_used=0, fail=(), results=None):
        self.t, self.fail, self.moved, self.results = test, set(fail), [], results or {}
        self.disk = {"hot": [ssd_total, ssd_used], "cold": [hdd_total, hdd_used]}

    def statvfs(self, path):
        total, used = self.disk["hot" if str(path) == str(self.t.hot) else "cold"]
        return FakeStat(total, total - used)

    def tier_move(self, rel, branch):
        if rel in self.fail:
            return 1, "    VERIFY FAILED for x"
        if rel in self.results:
            return self.results[rel]
        size = sum(f.stat().st_size for f in (branch / rel).rglob("*.mkv"))
        self.disk["hot"][1] -= size
        self.disk["cold"][1] += size
        self.moved.append(rel)
        return 0, ""


class Run(Tree):
    AGES = [300, 200, 100, 50, 10]

    def setUp(self):
        super().setUp()
        for i, age in enumerate(self.AGES):
            self.file(f"media/movies/M{i}/M{i}.mkv", size=100, age_days=age)
        self.snap = snap({}, {f"media/movies/M{i}": ago(a) for i, a in enumerate(self.AGES)},
                         {f"media/movies/M{i}/M{i}.mkv" for i in range(5)})

    def go(self, stub, now=NOW, sn="default", **kw):
        logs = []
        res = bm.run(self.conf(**kw), now=now, statvfs=stub.statvfs, tier_move=stub.tier_move, is_open=lambda p: False,
                     snap=self.snap if sn == "default" else sn, log=logs.append)
        return res, logs

    def test_below_start_does_nothing_and_touches_no_hdd_path(self):
        s = Stub(self, 1000, 690)
        res, logs = self.go(s)
        self.assertEqual((s.moved, res["moved_titles"], res["success"]), ([], 0, True))
        self.assertNotIn("duplicates", res)
        self.assertTrue(any("nothing to do" in l for l in logs))

    def test_moves_oldest_until_target(self):
        s = Stub(self, 1000, 750)
        res, _ = self.go(s)
        self.assertEqual(s.moved, ["media/movies/M0", "media/movies/M1"])
        self.assertEqual((res["moved_titles"], res["moved_bytes"], res["success"]), (2, 200, True))
        self.assertAlmostEqual(res["ssd_after"], 0.55)

    def test_dry_run_moves_nothing_but_plans(self):
        s = Stub(self, 1000, 750)
        res, logs = self.go(s, DRY_RUN=1)
        self.assertEqual(s.moved, [])
        self.assertEqual(res["moved_titles"], 2)
        self.assertTrue(any("WOULD move" in l and "M0" in l and "added 2026-01-02" in l for l in logs))

    def test_data_cap(self):
        s = Stub(self, 1000, 900)
        res, logs = self.go(s, MAX_MOVE_GIB=150 / GIB)
        self.assertEqual(s.moved, ["media/movies/M0"])
        self.assertTrue(any("data cap" in l for l in logs))

    def test_deadline(self):
        s = Stub(self, 1000, 900)
        res, logs = self.go(s, now=dt.datetime(2026, 10, 29, 4, 41, tzinfo=TZ))
        self.assertEqual(s.moved, [])
        self.assertTrue(any("time limit" in l for l in logs))

    def test_deadline_counts_one_and_a_half_times_the_copy_time(self):
        s = Stub(self, 1000, 900)
        res, logs = self.go(s, now=dt.datetime(2026, 10, 29, 4, 38, tzinfo=TZ), RATE_MIB_S=100 / MIB / 100)
        self.assertEqual(s.moved, [])
        res, logs = self.go(s, now=dt.datetime(2026, 10, 29, 4, 38, tzinfo=TZ), RATE_MIB_S=100 / MIB / 70)
        self.assertEqual(s.moved[:1], ["media/movies/M0"])

    def test_hdd_reserve_stops(self):
        s = Stub(self, 1000, 900, hdd_total=1000, hdd_used=750)
        res, logs = self.go(s)
        self.assertEqual(s.moved, ["media/movies/M0"])
        self.assertTrue(any("HDD" in l and "reserve" in l for l in logs))

    def test_idle_rule_stops_at_the_first_recent_title(self):
        s = Stub(self, 1000, 740)
        res, logs = self.go(s, TARGET_USED_PCT=0)
        self.assertEqual(s.moved, ["media/movies/M0", "media/movies/M1", "media/movies/M2", "media/movies/M3"])
        self.assertTrue(any("added in the last 30 days" in l and "M4" in l for l in logs))

    def test_fallback_from_75_percent_takes_titles_older_than_7_days(self):
        self.file("media/movies/M5/M5.mkv", size=100)
        self.snap.added["media/movies/M5"] = ago(3)
        self.snap.jf_paths.add("media/movies/M5/M5.mkv")
        s = Stub(self, 10000, 10000)
        res, logs = self.go(s, TARGET_USED_PCT=0)
        self.assertEqual(s.moved, [f"media/movies/M{i}" for i in range(5)])
        self.assertTrue(any("added in the last 7 days" in l and "M5" in l for l in logs))

    def test_failure_is_counted_and_run_continues(self):
        s = Stub(self, 1000, 750, fail={"media/movies/M0"})
        res, _ = self.go(s)
        self.assertEqual(s.moved, ["media/movies/M1", "media/movies/M2"])
        self.assertEqual((res["failed_titles"], res["verify_failures"], res["success"]), (1, 1, False))

    def test_lock_busy_and_source_changed_are_skips_not_failures(self):
        s = Stub(self, 1000, 750, results={"media/movies/M0": (3, "media lock busy"), "media/movies/M1": (4, "SOURCE CHANGED during the copy")})
        res, _ = self.go(s)
        self.assertEqual(s.moved, ["media/movies/M2", "media/movies/M3"])
        self.assertEqual((res["skipped_titles"], res["failed_titles"], res["success"]), (2, 0, True))

    def test_skipped_units_are_counted(self):
        os.link(self.hot / "media/movies/M0/M0.mkv", self.tmp / "seed.mkv")
        s = Stub(self, 1000, 750)
        res, _ = self.go(s)
        self.assertEqual(s.moved, ["media/movies/M1", "media/movies/M2"])
        self.assertEqual(res["skipped_titles"], 1)

    def test_no_data_fails_closed(self):
        s = Stub(self, 1000, 900)
        res, logs = self.go(s, sn=None)
        self.assertEqual((s.moved, res["success"]), ([], False))

    def test_jellyfin_coverage_under_90_percent_fails_closed(self):
        self.snap.jf_paths.clear()
        s = Stub(self, 1000, 900)
        res, logs = self.go(s, now=dt.datetime.now(TZ) + dt.timedelta(days=2), DEADLINE="")
        self.assertEqual((s.moved, res["success"]), ([], False))
        self.assertTrue(any("Jellyfin knows only 0 %" in l for l in logs))

    def test_hdd_checks_duplicates_staging_and_overflow(self):
        self.file("media/movies/M0/M0.mkv", branch=self.cold)
        self.file(".tier-staging/media/movies/X/X.mkv", branch=self.cold)
        self.file("torrents/movies/spilled.mkv", branch=self.cold)
        self.file("usenet/complete/a.rar", branch=self.cold)
        s = Stub(self, 1000, 750)
        res, _ = self.go(s)
        self.assertEqual((res["duplicates"], res["staging_orphans"], res["hdd_overflow_files"]), (1, 1, 2))

    def test_plan_lists_titles_with_last_played_and_added_even_below_start(self):
        self.snap.played["media/movies/M0"] = ago(60)
        s = Stub(self, 1000, 100)
        lines = bm.plan(self.conf(), NOW, s.statvfs, lambda p: False, self.snap)
        self.assertEqual(len([l for l in lines if l.startswith("  candidate")]), 4)
        self.assertRegex(lines[1], r"candidate\s+100 B\s+played never\s+added 2026-04-12\s+media/movies/M1")
        self.assertTrue(any("played 2026-08-30" in l and "M0" in l for l in lines))
        self.assertTrue(any(l.startswith("  skip") and "M4" in l and "added in the last 30 days" in l for l in lines))


class TwoSsds(Tree):
    def test_a_title_split_over_both_ssds_moves_from_both_and_usage_is_summed(self):
        hot2 = self.tmp / "hot2"
        self.file("media/tv/S/Season 01/E1.mkv", size=100)
        self.file("media/tv/S/Season 02/E1.mkv", size=50, branch=hot2)
        self.file("media/movies/New/New.mkv", size=10, branch=hot2)
        units = {u.rel: u for u in bm.scan([self.hot, hot2], ["media/movies", "media/tv"])}
        self.assertEqual(units["media/tv/S"].size, 150)
        self.assertEqual(units["media/tv/S"].branches, [self.hot, hot2])
        st = {str(self.hot): FakeStat(1000, 100), str(hot2): FakeStat(1000, 900), str(self.cold): FakeStat(10 ** 9, 10 ** 9)}
        self.assertEqual(bm.usage(lambda p: st[str(p)], [self.hot, hot2]), (2000, 1000))
        calls = []
        sn = snap({}, {"media/tv/S": ago(100), "media/movies/New": ago(1)}, {"media/tv/S/Season 01/E1.mkv", "media/tv/S/Season 02/E1.mkv", "media/movies/New/New.mkv"})
        conf = self.conf(HOT=f"{self.hot} {hot2}", START_USED_PCT=40, TARGET_USED_PCT=10)
        res = bm.run(conf, NOW, lambda p: st[str(p)], lambda rel, b: calls.append((rel, b)) or (0, ""), lambda p: False, sn, lambda l: None)
        self.assertEqual(calls, [("media/tv/S", self.hot), ("media/tv/S", hot2)])
        self.assertEqual(res["moved_titles"], 1)

    def test_default_hot_tier_is_both_ssds(self):
        self.assertEqual([str(h) for h in bm.Conf.from_env({}).hots], ["/srv/.disks/ssd-hot", "/srv/.disks/ssd-hot2"])


class Prom(unittest.TestCase):
    def test_prom_text(self):
        res = {"started": 1793000000, "success": False, "dry_run": False, "moved_titles": 2, "moved_bytes": 5, "skipped_titles": 1,
               "failed_titles": 1, "verify_failures": 1, "duplicates": 0, "staging_orphans": 2, "hdd_overflow_files": 3, "ssd_before": 0.75, "ssd_after": 0.6}
        text = bm.prom(res, {"hdd": {"Start_Stop_Count": 954}, "ssd": {"Wear_Leveling_Count": 7}})
        self.assertTrue(text.startswith("beefy_mover_last_run_timestamp_seconds 1793000000\n"))
        for line in ("beefy_mover_last_run_success 0", 'beefy_mover_ssd_used_ratio{when="after"} 0.6', "beefy_mover_verify_failures 1",
                     "beefy_mover_staging_orphans 2", "beefy_mover_hdd_overflow_files 3",
                     'beefy_disk_smart_raw{disk="hdd",attribute="Start_Stop_Count"} 954', 'beefy_disk_smart_raw{disk="ssd",attribute="Wear_Leveling_Count"} 7'):
            self.assertIn(line + "\n", text)

    def test_smart_log_line(self):
        line = bm.smart_line(NOW, {"hdd": {"Start_Stop_Count": 956, "Power_Cycle_Count": 481}, "ssd": {"Power_Cycle_Count": 966}})
        self.assertEqual(line, "2026-10-29T04:02:00+01:00 hdd Power_Cycle_Count=481 Start_Stop_Count=956 | ssd Power_Cycle_Count=966\n")
        self.assertIsNone(bm.smart_line(NOW, {}))

    def test_smart_parses_and_never_wakes(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            out = json.dumps({"ata_smart_attributes": {"table": [{"name": "Start_Stop_Count", "raw": {"value": 954}}, {"name": "Temperature_Celsius", "raw": {"value": 34}}]}})
            return subprocess.CompletedProcess(cmd, 0, out, "")
        self.assertEqual(bm.smart("/dev/x", fake), {"Start_Stop_Count": 954})
        self.assertIn("standby", calls[0])
        self.assertEqual(bm.smart("/dev/x", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", "")), {})


class KeepAwake(unittest.TestCase):
    def test_takes_and_releases_only_its_own_hold(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "keep"
            with bm.keep_awake(f):
                self.assertTrue(f.exists())
            self.assertFalse(f.exists())
            f.write_text("")
            with bm.keep_awake(f):
                pass
            self.assertTrue(f.exists())


class EndToEnd(Tree):
    """Real tier-move against scratch branches: a move keeps owner, mode, times and content; a hardlinked copy is never moved."""

    def test_real_move_and_hardlink_skip(self):
        if not shutil.which("fuser"):
            self.skipTest("fuser missing")
        mkv = self.file("media/movies/T/T.mkv", size=300000, age_days=40)
        os.chmod(mkv, 0o664)
        self.file("media/movies/T/T.nfo", size=10)
        seeded = self.file("media/movies/S/S.mkv", size=300000, age_days=400)
        os.link(seeded, self.tmp / "seeding.mkv")
        before = mkv.stat()
        env = dict(os.environ, POOL=str(self.tmp / "pool"), HOT=str(self.hot), COLD=str(self.cold), TIER_LOCK=str(self.tmp / "media.lock"), TIER_HELPER=str(HERE / "tier_helper.py"))
        (self.tmp / "pool").mkdir()

        def tier_move(rel, branch):
            r = subprocess.run([str(HERE / "tier-move"), "demote", rel, "--apply", "--any-time"], env=dict(env, HOT=str(branch)), capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr
        st = {str(self.hot): FakeStat(1000, 100), str(self.cold): FakeStat(10 ** 9, 10 ** 9)}
        sn = snap({}, {"media/movies/T": ago(40), "media/movies/S": ago(400)}, {"media/movies/T/T.mkv", "media/movies/S/S.mkv"})
        res = bm.run(self.conf(), now=NOW, statvfs=lambda p: st[str(p)], tier_move=tier_move, is_open=bm.fuser_open, snap=sn, log=lambda l: None)
        moved = self.cold / "media/movies/T/T.mkv"
        self.assertTrue(moved.exists())
        self.assertFalse(mkv.exists())
        after = moved.stat()
        self.assertEqual((after.st_size, after.st_mode, after.st_uid, after.st_gid, int(after.st_mtime)),
                         (before.st_size, before.st_mode, before.st_uid, before.st_gid, int(before.st_mtime)))
        self.assertTrue((self.hot / "media/movies/T/T.nfo").exists())
        self.assertTrue(seeded.exists())
        self.assertFalse((self.cold / "media/movies/S").exists())
        self.assertEqual((res["moved_titles"], res["skipped_titles"], res["failed_titles"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
