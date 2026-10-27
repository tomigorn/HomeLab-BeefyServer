#!/usr/bin/env python3
"""scripts/indexer-usage.py: counting per indexer window (rolling 24 h, calendar day Europe/Zurich incl. DST), levels, reset times, unlimited indexers, subscription deadlines, Prowlarr down, and that usage.json holds only names and counts.
Usage: python3 tests/test_indexer_usage.py"""
import datetime as dt
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("iu", ROOT / "scripts" / "indexer-usage.py")
iu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iu)
UTC = dt.timezone.utc

INDEXERS = [
    {"id": 3, "name": "Treasure Maps", "protocol": "usenet", "enable": True},
    {"id": 2, "name": "NzbPlanet", "protocol": "usenet", "enable": True},
    {"id": 1, "name": "NZBgeek", "protocol": "usenet", "enable": True},
    {"id": 4, "name": "1337x", "protocol": "torrent", "enable": True},
    {"id": 9, "name": "Old One", "protocol": "torrent", "enable": False},
]
LIMITS = {
    "Treasure Maps": {"searches": 100, "downloads": 5, "window": "rolling"},
    "NzbPlanet": {"searches": 20000, "window": "day"},
}
DOWNLOADS_ONLY = {"Treasure Maps": {"downloads": 400, "window": "rolling"}}


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


def ev(indexer_id, when, event="indexerQuery"):
    return {"id": 1, "indexerId": indexer_id, "date": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "successful": True, "eventType": event,
            "data": {"url": "https://example.com/api?apikey=SECRETKEY123", "host": "172.28.10.202", "source": "Sonarr", "query": "Some Film"}}


def row(result, name):
    return next(r for r in result["indexers"] if r["name"] == name)


class Rolling(unittest.TestCase):
    NOW = utc(2026, 10, 26, 12, 0, 0)

    def test_downloads_only_limit_leaves_searches_unlimited(self):
        h = [ev(3, self.NOW - dt.timedelta(hours=1)) for _ in range(150)] + [ev(3, self.NOW - dt.timedelta(hours=2), "releaseGrabbed")]
        r = row(iu.usage(INDEXERS, h, DOWNLOADS_ONLY, self.NOW), "Treasure Maps")
        self.assertEqual((r["searches"]["used"], r["searches"]["limit"], r["searches"]["level"]), (150, None, "ok"))
        self.assertEqual((r["downloads"]["used"], r["downloads"]["limit"], r["downloads"]["level"]), (1, 400, "ok"))
        self.assertTrue(r["limited"])

    def test_counts_queries_rss_and_grabs_inside_24h(self):
        h = [ev(3, self.NOW - dt.timedelta(hours=1)), ev(3, self.NOW - dt.timedelta(hours=2), "indexerRss"),
             ev(3, self.NOW - dt.timedelta(hours=3), "releaseGrabbed"), ev(3, self.NOW - dt.timedelta(hours=25)),
             ev(3, self.NOW - dt.timedelta(hours=4), "indexerAuth"), ev(2, self.NOW - dt.timedelta(hours=1))]
        r = row(iu.usage(INDEXERS, h, LIMITS, self.NOW), "Treasure Maps")
        self.assertEqual((r["searches"]["used"], r["searches"]["limit"]), (2, 100))
        self.assertEqual((r["downloads"]["used"], r["downloads"]["limit"]), (1, 5))
        self.assertEqual(r["window"], "rolling")

    def test_event_exactly_24h_old_has_aged_out(self):
        h = [ev(3, self.NOW - dt.timedelta(hours=24)), ev(3, self.NOW - dt.timedelta(hours=24) + dt.timedelta(seconds=1))]
        self.assertEqual(row(iu.usage(INDEXERS, h, LIMITS, self.NOW), "Treasure Maps")["searches"]["used"], 1)

    def test_levels(self):
        for used, level in ((0, "ok"), (69, "ok"), (70, "warn"), (94, "warn"), (95, "full"), (100, "full"), (120, "full")):
            h = [ev(3, self.NOW - dt.timedelta(minutes=i + 1)) for i in range(used)]
            self.assertEqual(row(iu.usage(INDEXERS, h, LIMITS, self.NOW), "Treasure Maps")["searches"]["level"], level, used)

    def test_indexer_level_is_the_worst_metric(self):
        h = [ev(3, self.NOW - dt.timedelta(minutes=i + 1), "releaseGrabbed") for i in range(5)]
        r = row(iu.usage(INDEXERS, h, LIMITS, self.NOW), "Treasure Maps")
        self.assertEqual((r["searches"]["level"], r["downloads"]["level"], r["level"]), ("ok", "full", "full"))

    def test_reset_is_when_the_oldest_counted_event_ages_out(self):
        oldest = self.NOW - dt.timedelta(hours=20)
        h = [ev(3, oldest, "releaseGrabbed")] + [ev(3, self.NOW - dt.timedelta(hours=1), "releaseGrabbed") for _ in range(4)]
        d = row(iu.usage(INDEXERS, list(reversed(h)), LIMITS, self.NOW), "Treasure Maps")["downloads"]
        self.assertEqual(d["used"], 5)
        self.assertEqual(d["resetsAt"], "2026-10-26T16:00:00Z")

    def test_over_the_limit_frees_a_slot_when_enough_events_aged_out(self):
        times = [self.NOW - dt.timedelta(hours=h) for h in (23, 22, 21, 1, 1, 1, 1)]
        d = row(iu.usage(INDEXERS, [ev(3, t, "releaseGrabbed") for t in times], LIMITS, self.NOW), "Treasure Maps")["downloads"]
        self.assertEqual(d["used"], 7)
        self.assertEqual(d["resetsAt"], "2026-10-26T15:00:00Z")

    def test_nothing_counted_no_reset(self):
        self.assertIsNone(row(iu.usage(INDEXERS, [], LIMITS, self.NOW), "Treasure Maps")["searches"]["resetsAt"])


class CalendarDay(unittest.TestCase):
    def test_counts_since_local_midnight_and_resets_at_next_midnight(self):
        now = utc(2026, 7, 10, 10, 0)            # 12:00 CEST
        h = [ev(2, utc(2026, 7, 9, 22, 30)),     # 00:30 local today
             ev(2, utc(2026, 7, 9, 21, 59)),     # 23:59 local yesterday
             ev(2, utc(2026, 7, 10, 9, 0), "indexerRss")]
        s = row(iu.usage(INDEXERS, h, LIMITS, now), "NzbPlanet")["searches"]
        self.assertEqual((s["used"], s["limit"]), (2, 20000))
        self.assertEqual(s["resetsAt"], "2026-07-10T22:00:00Z")
        self.assertEqual(row(iu.usage(INDEXERS, h, LIMITS, now), "NzbPlanet")["window"], "day")

    def test_winter_offset(self):
        now = utc(2026, 1, 15, 22, 30)            # 23:30 CET
        h = [ev(2, utc(2026, 1, 14, 23, 0)), ev(2, utc(2026, 1, 14, 22, 59))]
        s = row(iu.usage(INDEXERS, h, LIMITS, now), "NzbPlanet")["searches"]
        self.assertEqual(s["used"], 1)
        self.assertEqual(s["resetsAt"], "2026-01-15T23:00:00Z")

    def test_dst_end_day_has_25_hours(self):
        now = utc(2026, 10, 25, 22, 30)           # 23:30 CET, day began 00:00 CEST = 24.5 h ago
        h = [ev(2, utc(2026, 10, 24, 22, 15)), ev(2, utc(2026, 10, 24, 21, 45))]
        s = row(iu.usage(INDEXERS, h, LIMITS, now), "NzbPlanet")["searches"]
        self.assertEqual(s["used"], 1)
        self.assertEqual(s["resetsAt"], "2026-10-25T23:00:00Z")
        self.assertLessEqual(iu.since(LIMITS, now), utc(2026, 10, 24, 22, 0))

    def test_dst_start_day_has_23_hours(self):
        now = utc(2026, 3, 29, 12, 0)             # 14:00 CEST
        s = row(iu.usage(INDEXERS, [ev(2, utc(2026, 3, 28, 23, 0))], LIMITS, now), "NzbPlanet")["searches"]
        self.assertEqual(s["used"], 1)
        self.assertEqual(s["resetsAt"], "2026-03-29T22:00:00Z")

    def test_unlimited_downloads_on_a_limited_indexer(self):
        now = utc(2026, 7, 10, 10, 0)
        d = row(iu.usage(INDEXERS, [ev(2, utc(2026, 7, 10, 9, 0), "releaseGrabbed")], LIMITS, now), "NzbPlanet")["downloads"]
        self.assertEqual((d["used"], d["limit"], d["level"], d["resetsAt"]), (1, None, "ok", None))


class Unlimited(unittest.TestCase):
    NOW = utc(2026, 10, 26, 12, 0)

    def test_unlisted_indexers_are_unlimited_and_counted_over_24h(self):
        h = [ev(1, self.NOW - dt.timedelta(hours=2)), ev(1, self.NOW - dt.timedelta(hours=30)), ev(1, self.NOW - dt.timedelta(hours=1), "releaseGrabbed")]
        r = row(iu.usage(INDEXERS, h, LIMITS, self.NOW), "NZBgeek")
        self.assertFalse(r["limited"])
        self.assertEqual((r["searches"]["used"], r["searches"]["limit"], r["downloads"]["used"], r["level"]), (1, None, 1, "ok"))

    def test_order_limited_first_then_by_name_and_disabled_hidden(self):
        names = [r["name"] for r in iu.usage(INDEXERS, [], LIMITS, self.NOW)["indexers"]]
        self.assertEqual(names, ["Treasure Maps", "NzbPlanet", "1337x", "NZBgeek"])

    def test_configured_but_missing_in_prowlarr_is_skipped(self):
        res = iu.usage(INDEXERS[2:], [], LIMITS, self.NOW)
        self.assertNotIn("Treasure Maps", [r["name"] for r in res["indexers"]])


class Subscriptions(unittest.TestCase):
    SUBS = [{"name": "A", "kind": "indexer", "expires": dt.date(2026, 11, 18)},
            {"name": "B", "kind": "VPN", "expires": dt.date(2026, 11, 1)},
            {"name": "G", "kind": "VPN", "expires": dt.date(2026, 11, 25)},
            {"name": "C", "kind": "indexer", "expires": dt.date(2026, 10, 29)},
            {"name": "D", "kind": "Usenet provider", "expires": dt.date(2026, 10, 26)},
            {"name": "E", "kind": "indexer", "expires": dt.date(2026, 10, 20)},
            {"name": "F", "kind": "indexer", "lifetime": True}]

    def test_days_left_and_levels(self):
        out = {s["name"]: s for s in iu.subscriptions(self.SUBS, dt.date(2026, 10, 26))}
        self.assertEqual((out["A"]["daysLeft"], out["A"]["level"], out["A"]["expires"]), (23, "yellow", "2026-11-18"))
        self.assertEqual((out["B"]["daysLeft"], out["B"]["level"]), (6, "orange"))
        self.assertEqual((out["C"]["daysLeft"], out["C"]["level"]), (3, "orange"))
        self.assertEqual((out["G"]["daysLeft"], out["G"]["level"]), (30, "yellow"))
        self.assertEqual((out["D"]["daysLeft"], out["D"]["level"], out["D"]["expired"]), (0, "full", False))
        self.assertEqual((out["E"]["daysLeft"], out["E"]["level"], out["E"]["expired"]), (-6, "full", True))
        self.assertEqual((out["F"]["lifetime"], out["F"]["level"], out["F"]["daysLeft"], out["F"]["expires"]), (True, "ok", None, None))

    def test_threshold_boundaries(self):
        today = dt.date(2026, 10, 26)
        want = {31: "ok", 30: "yellow", 8: "yellow", 7: "orange", 1: "orange", 0: "full", -1: "full", -2: "full"}
        for left, level in want.items():
            subs = [{"name": "X", "expires": today + dt.timedelta(days=left)}]
            self.assertEqual(iu.subscriptions(subs, today)[0]["level"], level, left)

    def test_today_is_the_zurich_date(self):
        self.assertEqual(iu.local_date(utc(2026, 10, 26, 23, 30)), dt.date(2026, 10, 27))

    def test_bad_entry_is_an_error(self):
        with self.assertRaises(ValueError):
            iu.subscriptions([{"name": "Z"}], dt.date(2026, 10, 26))


class Config(unittest.TestCase):
    def test_repo_config_loads(self):
        cfg = iu.load_config(ROOT / "indexer-bar" / "limits.toml")
        self.assertEqual(cfg["indexers"]["Treasure Maps"], {"downloads": 400, "window": "rolling"})
        self.assertEqual(cfg["indexers"]["NzbPlanet"], {"searches": 20000, "window": "day"})
        self.assertEqual({s["name"] for s in cfg["subscriptions"]}, {"Treasure Maps", "Eweka", "ProtonVPN Plus", "NZBgeek", "NzbPlanet"})

    def test_bad_window_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "l.toml"
            p.write_text('[indexers."X"]\nsearches = 5\nwindow = "week"\n')
            with self.assertRaises(ValueError):
                iu.load_config(p)


class Run(unittest.TestCase):
    NOW = utc(2026, 10, 26, 12, 0)
    CFG = {"indexers": LIMITS, "subscriptions": [{"name": "Eweka", "kind": "Usenet provider", "expires": dt.date(2026, 11, 18)}]}

    def test_writes_only_names_and_counts(self):
        h = [ev(3, self.NOW - dt.timedelta(hours=1)), ev(1, self.NOW - dt.timedelta(hours=1), "releaseGrabbed")]
        calls = []

        def api(path):
            calls.append(path)
            return INDEXERS if path == "/indexer" else h
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "usage.json"
            iu.run(self.CFG, api, out, self.NOW)
            text = out.read_text()
            data = json.loads(text)
        self.assertTrue(data["ok"])
        self.assertEqual(data["generated"], "2026-10-26T12:00:00Z")
        self.assertEqual(calls, ["/indexer", "/history/since?date=2026-10-25T12:00:00Z"])
        for leak in ("apikey", "SECRETKEY", "172.28", "Sonarr", "Some Film", "example.com"):
            self.assertNotIn(leak, text)
        self.assertEqual(data["subscriptions"][0]["daysLeft"], 23)

    def test_prowlarr_down_still_writes_subscriptions(self):
        def api(path):
            raise iu.ApiError("connection refused")
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "usage.json"
            out.write_text('{"ok": true, "indexers": [{"name": "stale"}]}')
            iu.run(self.CFG, api, out, self.NOW)
            data = json.loads(out.read_text())
        self.assertFalse(data["ok"])
        self.assertEqual(data["indexers"], [])
        self.assertEqual(data["subscriptions"][0]["name"], "Eweka")
        self.assertNotIn("connection refused", json.dumps(data))

    def test_since_covers_rolling_and_day_windows(self):
        self.assertEqual(iu.since(LIMITS, self.NOW), utc(2026, 10, 25, 12, 0))
        self.assertEqual(iu.since({"X": {"searches": 1, "window": "day"}}, utc(2026, 10, 25, 22, 30)), utc(2026, 10, 24, 22, 0))


if __name__ == "__main__":
    unittest.main()
