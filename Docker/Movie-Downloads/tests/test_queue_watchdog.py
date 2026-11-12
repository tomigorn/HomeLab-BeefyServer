#!/usr/bin/env python3
"""scripts/queue-watchdog.py: which queue items alert and when, the one-alert-per-condition state, guard event files and sending, against fake Radarr/Sonarr and a fake sender.
Usage: python3 tests/test_queue_watchdog.py"""
import calendar
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("qw", ROOT / "scripts" / "queue-watchdog.py")
qw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qw)
MIN, HOUR = 60, 3600
T0 = 1_800_000_000


def utc(*t):
    return calendar.timegm(t + (0,) * (6 - len(t)))

BLOCKED = {"id": 5, "downloadId": "abc", "title": "A.Separation.2026.2160p.WEB-DL-HDSWEB", "status": "completed", "trackedDownloadStatus": "warning",
           "trackedDownloadState": "importBlocked", "sizeleft": 0, "movie": {"title": "A Separation", "year": 2011},
           "statusMessages": [{"title": "A.Separation.2026", "messages": ["Found matching movie via grab history, but release was matched to movie by ID. Manual Import required."]}]}
DOWNLOADING = {"id": 6, "downloadId": "dl1", "title": "Some.Film.2020.1080p-X", "status": "downloading", "trackedDownloadStatus": "ok",
               "trackedDownloadState": "downloading", "sizeleft": 1000, "movie": {"title": "Some Film", "year": 2020}}
EP = {"id": 7, "downloadId": "ep1", "title": "Show.S02E03.1080p-X", "status": "failed", "trackedDownloadStatus": "error", "trackedDownloadState": "downloading",
      "sizeleft": 5, "series": {"title": "Show", "year": 2019}, "episode": {"seasonNumber": 2, "episodeNumber": 3}, "statusMessages": []}


def digest(d):
    f = pathlib.Path(d) / "watchdog-digest.json"
    return json.loads(f.read_text()) if f.exists() else []


def sends(d):
    f = pathlib.Path(d) / "watchdog-sends.log"
    return f.read_text().splitlines() if f.exists() else []


WINTER = {"23:00": utc(2027, 1, 15, 22), "06:59": utc(2027, 1, 16, 5, 59), "07:00": utc(2027, 1, 16, 6), "21:59": utc(2027, 1, 15, 20, 59)}
SUMMER = {"23:00": utc(2027, 7, 15, 21), "06:59": utc(2027, 7, 16, 4, 59), "07:00": utc(2027, 7, 16, 5), "21:59": utc(2027, 7, 15, 19, 59)}


def fake(records=(), fail=False, health=(), status=None, indexers=(), health_fail=False, logs=()):
    def call(method, path, query=None, body=None):
        if fail:
            raise qw.ApiError("down")
        assert method == "GET", (method, path)
        if path == "/health":
            if health_fail:
                raise qw.ApiError("health down")
            return list(health)
        if path == "/indexerstatus":
            if status is None:
                raise qw.ApiError("GET /indexerstatus: HTTP Error 404")
            return list(status)
        if path == "/indexer":
            return list(indexers)
        if path == "/log":
            return {"records": list(logs)}
        assert path == "/queue", (method, path)
        return {"totalRecords": len(records), "records": list(records)}
    return call


def hc(names, source="IndexerStatusCheck", long=False):
    pre = "Indexers unavailable due to failures" + (" for more than 6 hours" if long else "")
    return {"source": source, "type": "warning", "message": f"{pre}: {', '.join(names)}", "wikiUrl": "https://wiki.servarr.com"}


class Decide(unittest.TestCase):
    def setUp(self):
        self.state = {}

    def run_app(self, app, records, now, **cfg):
        alerts, self.state = qw.evaluate(app, records, self.state, now, qw.Config(**cfg))
        return alerts

    def test_blocked_alerts_after_30_minutes_once(self):
        self.assertEqual(self.run_app("radarr", [BLOCKED], T0), [])
        self.assertEqual(self.run_app("radarr", [BLOCKED], T0 + 29 * MIN), [])
        [a] = self.run_app("radarr", [BLOCKED], T0 + 31 * MIN)
        self.assertEqual(self.run_app("radarr", [BLOCKED], T0 + 5 * HOUR), [])
        self.assertEqual(a.key, "radarr:abc")
        self.assertEqual(a.condition, "blocked")
        for part in ("Radarr", "A Separation (2011)", "A.Separation.2026.2160p.WEB-DL-HDSWEB", "importBlocked", "Manual Import required", "31 min", "Radarr → Activity → Queue"):
            self.assertIn(part, a.text)

    def test_alert_again_only_after_the_condition_cleared(self):
        self.run_app("radarr", [BLOCKED], T0)
        self.assertEqual(len(self.run_app("radarr", [BLOCKED], T0 + 31 * MIN)), 1)
        self.run_app("radarr", [dict(BLOCKED, trackedDownloadState="downloading", trackedDownloadStatus="ok")], T0 + 40 * MIN)
        self.assertEqual(self.run_app("radarr", [BLOCKED], T0 + 50 * MIN), [])
        self.assertEqual(len(self.run_app("radarr", [BLOCKED], T0 + 81 * MIN)), 1)

    def test_pending_states_and_warning_status_count(self):
        for extra in ({"trackedDownloadState": "importPending", "trackedDownloadStatus": "ok"}, {"trackedDownloadState": "failedPending", "trackedDownloadStatus": "ok"},
                      {"trackedDownloadState": "downloading", "trackedDownloadStatus": "warning"}, {"trackedDownloadState": "downloading", "trackedDownloadStatus": "error"}):
            self.state = {}
            rec = dict(DOWNLOADING, **extra)
            self.run_app("radarr", [rec], T0)
            self.assertEqual(len(self.run_app("radarr", [rec], T0 + 31 * MIN)), 1, extra)

    def test_healthy_item_never_alerts(self):
        self.run_app("radarr", [DOWNLOADING], T0)
        self.assertEqual(self.run_app("radarr", [dict(DOWNLOADING, sizeleft=900)], T0 + 3 * HOUR), [])

    def test_stalled_download_alerts_after_6_hours_without_progress(self):
        self.run_app("radarr", [DOWNLOADING], T0)
        self.assertEqual(self.run_app("radarr", [DOWNLOADING], T0 + 5 * HOUR), [])
        [a] = self.run_app("radarr", [DOWNLOADING], T0 + 6 * HOUR + MIN)
        self.assertEqual(a.condition, "stalled")
        self.assertIn("6 h", a.text)

    def test_progress_resets_the_stall_clock(self):
        self.run_app("radarr", [DOWNLOADING], T0)
        self.run_app("radarr", [dict(DOWNLOADING, sizeleft=999)], T0 + 5 * HOUR)
        self.assertEqual(self.run_app("radarr", [dict(DOWNLOADING, sizeleft=999)], T0 + 10 * HOUR), [])
        self.assertEqual(len(self.run_app("radarr", [dict(DOWNLOADING, sizeleft=999)], T0 + 11 * HOUR + MIN)), 1)

    def test_a_stall_that_resumed_and_stalls_again_alerts_again(self):
        self.run_app("radarr", [DOWNLOADING], T0)
        self.assertEqual(len(self.run_app("radarr", [DOWNLOADING], T0 + 7 * HOUR)), 1)
        self.run_app("radarr", [dict(DOWNLOADING, sizeleft=500)], T0 + 8 * HOUR)
        self.assertEqual(len(self.run_app("radarr", [dict(DOWNLOADING, sizeleft=500)], T0 + 15 * HOUR)), 1)

    def test_queued_counts_as_stalled_but_paused_does_not(self):
        q = dict(DOWNLOADING, status="queued")
        self.run_app("radarr", [q], T0)
        self.assertEqual(len(self.run_app("radarr", [q], T0 + 7 * HOUR)), 1)
        self.state = {}
        p = dict(DOWNLOADING, status="paused")
        self.run_app("radarr", [p], T0)
        self.assertEqual(self.run_app("radarr", [p], T0 + 7 * HOUR), [])

    def test_night_download_paused_all_day_then_resumed_starts_a_fresh_stall_clock(self):
        p = dict(DOWNLOADING, status="paused", title="Film.night.2020", downloadId="night1")
        for h in range(0, 16):
            self.assertEqual(self.run_app("radarr", [p], T0 + h * HOUR), [])
        r = dict(p, status="downloading")
        self.assertEqual(self.run_app("radarr", [r], T0 + 16 * HOUR), [])
        self.assertEqual(self.run_app("radarr", [r], T0 + 21 * HOUR), [])
        self.assertEqual(len(self.run_app("radarr", [r], T0 + 23 * HOUR)), 1)

    def test_failed_alerts_at_once(self):
        [a] = self.run_app("sonarr", [EP], T0)
        self.assertEqual(a.condition, "failed")
        self.assertIn("Show (2019) S02E03", a.text)
        self.assertIn("Sonarr → Activity → Queue", a.text)
        self.assertEqual(self.run_app("sonarr", [EP], T0 + MIN), [])

    def test_failed_and_blocked_are_separate_conditions(self):
        rec = dict(EP, trackedDownloadState="importBlocked")
        self.run_app("sonarr", [rec], T0)
        self.assertEqual(sorted(a.condition for a in self.run_app("sonarr", [rec], T0 + 31 * MIN)), ["blocked"])

    def test_unknown_item_without_movie_uses_the_release_name(self):
        rec = dict(BLOCKED)
        del rec["movie"]
        self.run_app("radarr", [rec], T0)
        [a] = self.run_app("radarr", [rec], T0 + 31 * MIN)
        self.assertIn("A.Separation.2026.2160p.WEB-DL-HDSWEB", a.text)
        self.assertNotIn("None", a.text)

    def test_state_is_cleaned_for_downloads_that_left_the_queue(self):
        self.run_app("radarr", [BLOCKED, DOWNLOADING], T0)
        self.run_app("radarr", [DOWNLOADING], T0 + MIN)
        self.assertEqual(list(self.state), ["radarr:dl1"])

    def test_other_apps_state_is_kept(self):
        self.run_app("radarr", [BLOCKED], T0)
        self.run_app("sonarr", [], T0 + MIN)
        self.assertIn("radarr:abc", self.state)

    def test_thresholds_come_from_the_config(self):
        self.run_app("radarr", [BLOCKED], T0, blocked_min=5)
        self.assertEqual(len(self.run_app("radarr", [BLOCKED], T0 + 6 * MIN, blocked_min=5)), 1)

    def test_html_in_titles_is_escaped(self):
        rec = dict(BLOCKED, title="Tom & <b>Jerry</b>")
        self.run_app("radarr", [rec], T0)
        [a] = self.run_app("radarr", [rec], T0 + 31 * MIN)
        self.assertIn("Tom &amp; &lt;b&gt;Jerry&lt;/b&gt;", a.text)

    def test_message_is_a_titled_bullet_list_with_details_collapsed(self):
        self.run_app("radarr", [BLOCKED], T0)
        [a] = self.run_app("radarr", [BLOCKED], T0 + 31 * MIN)
        head, body = a.text.split("\n\n", 1)
        self.assertEqual(head, "🟠 <b>Radarr queue: blocked</b>")
        self.assertTrue(body.startswith("• "))
        self.assertTrue(a.text.endswith("Radarr → Activity → Queue</blockquote>"))
        self.assertIn("<blockquote expandable>💡 Message: ", a.text)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.state = self.dir / "state.json"
        self.events = self.dir / "events"
        self.events.mkdir()
        self.sent = []
        self.fail_send = False

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, text):
        if self.fail_send:
            return False
        self.sent.append(text)
        return True

    def go(self, apps, now):
        return qw.run(apps, self.state, [self.events], self.send, qw.Config(), now)

    def test_alert_is_sent_once_and_state_survives_runs(self):
        apps = {"radarr": fake([BLOCKED]), "sonarr": fake()}
        self.assertEqual(self.go(apps, T0), 0)
        self.assertEqual(self.go(apps, T0 + 31 * MIN), 0)
        self.assertEqual(self.go(apps, T0 + 46 * MIN), 0)
        self.assertEqual(len(self.sent), 1)

    def test_failed_send_is_retried_next_run(self):
        apps = {"radarr": fake([BLOCKED])}
        self.go(apps, T0)
        self.fail_send = True
        self.assertEqual(self.go(apps, T0 + 31 * MIN), 1)
        self.fail_send = False
        self.assertEqual(self.go(apps, T0 + 46 * MIN), 0)
        self.assertEqual(len(self.sent), 1)

    def test_empty_queues_send_nothing_and_exit_0(self):
        self.assertEqual(self.go({"radarr": fake(), "sonarr": fake()}, T0), 0)
        self.assertEqual(self.sent, [])

    def test_api_error_keeps_the_state_and_exits_1(self):
        self.go({"radarr": fake([BLOCKED])}, T0)
        self.assertEqual(self.go({"radarr": fake(fail=True)}, T0 + 10 * MIN), 1)
        self.go({"radarr": fake([BLOCKED])}, T0 + 31 * MIN)
        self.assertEqual(len(self.sent), 1)

    def event(self, name="1.json", **kw):
        ev = {"app": "radarr", "action": "removed", "title": "A Separation", "year": 2011, "release": "A.Separation.2026.2160p-X", "releaseYear": 2026}
        (self.events / name).write_text(json.dumps(dict(ev, **kw)))

    def test_guard_events_are_deleted_only_manual_is_sent(self):
        self.event("1.json")
        self.event("2.json", app="sonarr", action="marked-failed")
        self.event("3.json", action="manual")
        self.assertEqual(self.go({"radarr": fake()}, T0), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(list(self.events.glob("*.json")), [])
        [manual] = self.sent
        for part in ("Wrong-film guard", "Radarr", "A Separation (2011)", "A.Separation.2026.2160p-X", "2026", "check by hand"):
            self.assertIn(part, manual)
        self.assertTrue(manual.startswith("🔴 <b>Wrong-film guard: Radarr</b>\n\n• "))
        self.assertIn("<blockquote expandable>Radarr → Activity → Queue</blockquote>", manual)

    def test_removed_guard_message_text_is_unchanged(self):
        first = qw.event_text({"app": "radarr", "action": "removed", "title": "A Separation", "year": 2011, "release": "A.Separation.2026.2160p-X", "releaseYear": 2026})
        self.assertTrue(first.startswith("🟠 <b>Wrong-film guard: Radarr</b>\n\n• "))
        self.assertIn("marked failed", qw.event_text({"app": "sonarr", "action": "marked-failed"}))

    def test_dub_only_event_message(self):
        first = qw.event_text({"app": "radarr", "action": "removed", "reason": "dub-only", "release": "X.2011.MULTi.1080p-GRP", "languages": ["German", "French"],
                               "original": "Persian", "title": "A Separation", "year": 2011, "releaseYear": 2011})
        self.event("2.json", reason="dub-only", action="manual", release="Y-GRP", languages=["German", "French"], original="Persian")
        self.assertEqual(self.go({"radarr": fake()}, T0), 0)
        self.sent.insert(0, first)
        self.assertTrue(first.startswith("🟠 <b>Dub-only release removed: Radarr</b>\n\n• "))
        for part in ("A Separation (2011)", "<code>X.2011.MULTi.1080p-GRP</code>", "German, French", "Persian", "removed from the queue"):
            self.assertIn(part, first)
        self.assertNotIn("release year", first)
        self.assertTrue(self.sent[1].startswith("🔴 <b>Dub-only release: Radarr</b>"))
        self.assertIn("check by hand", self.sent[1])

    def test_guard_event_stays_when_the_send_fails(self):
        self.event(action="manual")
        self.fail_send = True
        self.assertEqual(self.go({"radarr": fake()}, T0), 1)
        self.assertEqual(len(list(self.events.glob("*.json"))), 1)

    def test_unreadable_event_is_set_aside_not_retried_forever(self):
        (self.events / "bad.json").write_text("{nope")
        self.assertEqual(self.go({"radarr": fake()}, T0), 0)
        self.assertEqual(list(self.events.glob("*.json")), [])
        self.assertEqual(len(list(self.events.glob("*.bad"))), 1)

    def test_tmp_files_of_a_running_guard_are_left_alone(self):
        (self.events / "9.json.tmp").write_text("{}")
        self.go({"radarr": fake()}, T0)
        self.assertTrue((self.events / "9.json.tmp").exists())


class QuietHours(unittest.TestCase):
    def test_quiet_from_22_to_7_zurich_time_in_winter_and_summer(self):
        cfg = qw.Config()
        for times in (WINTER, SUMMER):
            self.assertTrue(qw.in_quiet_hours(times["23:00"], cfg))
            self.assertTrue(qw.in_quiet_hours(times["06:59"], cfg))
            self.assertFalse(qw.in_quiet_hours(times["07:00"], cfg))
            self.assertFalse(qw.in_quiet_hours(times["21:59"], cfg))
            self.assertTrue(qw.in_quiet_hours(times["21:59"] + MIN, cfg))


class Routing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.events = self.dir / "events"
        self.events.mkdir()
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, text):
        self.sent.append(text)
        return True

    def go(self, apps, now):
        return qw.run(apps, self.dir / "queue-watchdog.json", [self.events], self.send, qw.Config(), now)

    def event(self, name="1.json", **kw):
        ev = {"app": "radarr", "action": "removed", "title": "A Separation", "year": 2011, "release": "A.Separation.2026.2160p-X", "releaseYear": 2026}
        (self.events / name).write_text(json.dumps(dict(ev, **kw)))

    def test_failed_download_goes_to_the_digest_not_telegram(self):
        self.assertEqual(self.go({"sonarr": fake([EP])}, T0), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual([(e["kind"], e["app"], e["title"], e["ts"]) for e in digest(self.dir)], [("failed", "sonarr", "Show (2019) S02E03", T0)])
        self.go({"sonarr": fake([EP])}, T0 + 15 * MIN)
        self.assertEqual(len(digest(self.dir)), 1)

    def test_stalled_download_goes_to_the_digest_not_telegram(self):
        apps = {"radarr": fake([DOWNLOADING])}
        self.go(apps, T0)
        self.go(apps, T0 + 6 * HOUR + MIN)
        self.assertEqual(self.sent, [])
        self.assertEqual([(e["kind"], e["title"]) for e in digest(self.dir)], [("stalled", "Some Film (2020)")])

    def test_removed_and_marked_failed_guard_events_go_to_the_digest(self):
        self.event("1.json")
        self.event("2.json", app="sonarr", action="marked-failed", title="Show", year=2019)
        self.event("3.json", reason="dub-only", languages=["German"], original="Persian", title="Other", year=2020)
        self.go({"radarr": fake()}, T0)
        self.assertEqual(self.sent, [])
        self.assertEqual(sorted((e["kind"], e["app"], e["title"]) for e in digest(self.dir)),
                         [("guard_removed", "radarr", "A Separation (2011)"), ("guard_removed", "radarr", "Other (2020)"), ("guard_removed", "sonarr", "Show (2019)")])
        self.assertEqual(list(self.events.glob("*.json")), [])

    def test_subtitle_check_events_go_to_the_digest(self):
        (self.events / "1.json").write_text(json.dumps({"app": "bazarr", "kind": "subtitle_repaired", "title": "The Hunt (2012) · en", "reasons": [], "counts": {"x": 1}}))
        (self.events / "2.json").write_text(json.dumps({"app": "bazarr", "kind": "subtitle_suspect", "title": "Show (2019) S01E02 · de", "reasons": ["not UTF-8"]}))
        self.go({"radarr": fake()}, T0)
        self.assertEqual(self.sent, [])
        self.assertEqual(sorted((e["kind"], e["app"], e["title"]) for e in digest(self.dir)),
                         [("subtitle_repaired", "bazarr", "The Hunt (2012) · en"), ("subtitle_suspect", "bazarr", "Show (2019) S01E02 · de")])
        self.assertEqual(list(self.events.glob("*.json")), [])

    def test_forced_sync_events_go_to_the_digest(self):
        (self.events / "1.json").write_text(json.dumps({"app": "bazarr", "kind": "subtitle_forced_synced", "title": "Film (2020) · de", "reasons": ["shifted -1.65 s"]}))
        (self.events / "2.json").write_text(json.dumps({"app": "bazarr", "kind": "subtitle_forced_unverified", "title": "Show (2019) S01E02 · en", "reasons": ["parts disagree"]}))
        self.go({"radarr": fake()}, T0)
        self.assertEqual(self.sent, [])
        self.assertEqual(sorted(e["kind"] for e in digest(self.dir)), ["subtitle_forced_synced", "subtitle_forced_unverified"])
        self.assertEqual(list(self.events.glob("*.json")), [])

    def test_subtitle_check_events_dir_is_watched_by_default(self):
        self.assertIn("/srv/appdata/bazarr/subtitle-check/events", qw.DEFAULT_EVENTS.split(":"))

    def test_blocked_is_held_overnight_and_sent_at_7(self):
        for times in (WINTER, SUMMER):
            self.setUp()
            apps = {"radarr": fake([BLOCKED])}
            self.go(apps, times["23:00"] - 31 * MIN)
            self.go(apps, times["23:00"])
            self.go(apps, times["06:59"])
            self.assertEqual(self.sent, [])
            self.go(apps, times["07:00"])
            self.assertEqual(len(self.sent), 1)
            self.assertTrue(self.sent[0].startswith("🟠 <b>Radarr queue: blocked</b>"))
            self.go(apps, times["07:00"] + 15 * MIN)
            self.assertEqual(len(self.sent), 1)
            self.assertEqual([l.split()[1:3] for l in sends(self.dir)], [["blocked", "held"], ["blocked", "sent"]])
            self.tearDown()

    def test_blocked_that_cleared_overnight_is_never_sent(self):
        self.go({"radarr": fake([BLOCKED])}, WINTER["23:00"] - 31 * MIN)
        self.go({"radarr": fake([BLOCKED])}, WINTER["23:00"])
        self.go({"radarr": fake()}, WINTER["06:59"])
        self.go({"radarr": fake()}, WINTER["07:00"])
        self.assertEqual(self.sent, [])

    def test_blocked_by_day_is_sent_at_once(self):
        apps = {"radarr": fake([BLOCKED])}
        self.go(apps, WINTER["21:59"] - 31 * MIN)
        self.go(apps, WINTER["21:59"])
        self.assertEqual(len(self.sent), 1)

    def test_manual_guard_event_is_held_overnight_and_kept(self):
        self.event(action="manual")
        self.go({"radarr": fake()}, SUMMER["23:00"])
        self.go({"radarr": fake()}, SUMMER["06:59"])
        self.assertEqual(self.sent, [])
        self.assertEqual(len(list(self.events.glob("*.json"))), 1)
        self.go({"radarr": fake()}, SUMMER["07:00"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("check by hand", self.sent[0])
        self.assertEqual(list(self.events.glob("*.json")), [])

    def test_indexers_disabled_is_held_overnight(self):
        bad = {"radarr": fake(health=[hc([NZB])])}
        self.go(bad, WINTER["23:00"])
        self.go(bad, WINTER["06:59"])
        self.assertEqual(self.sent, [])
        self.go(bad, WINTER["07:00"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("indexers disabled", self.sent[0])


class DigestFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_exact_format(self):
        text = qw.render_digest([{"kind": "guard_removed", "app": "radarr", "title": "Some Film (2024)", "ts": 1793070000}], 1793070100)
        self.assertEqual(text, "\n".join([
            "# HELP beefy_watchdog_event_timestamp_seconds Auto-handled queue-watchdog event (digest only), one series per event, last 48 h.",
            "# TYPE beefy_watchdog_event_timestamp_seconds gauge",
            'beefy_watchdog_event_timestamp_seconds{kind="guard_removed",app="radarr",title="Some Film (2024)"} 1793070000',
            "# HELP beefy_watchdog_digest_updated_timestamp_seconds Last queue-watchdog run that wrote this file.",
            "# TYPE beefy_watchdog_digest_updated_timestamp_seconds gauge",
            "beefy_watchdog_digest_updated_timestamp_seconds 1793070100",
        ]) + "\n")

    def test_label_values_are_escaped_and_title_trimmed(self):
        text = qw.render_digest([{"kind": "failed", "app": "radarr", "title": 'a\\b"c\nd' + "x" * 100, "ts": T0}], T0)
        line = [l for l in text.splitlines() if l.startswith("beefy_watchdog_event_timestamp_seconds{")][0]
        self.assertIn('title="a\\\\b\\"c\\nd' + "x" * 73 + '"}', line)

    def test_prune_dedups_keeps_48h_and_caps_at_40_newest_first(self):
        ev = [{"kind": "failed", "app": "radarr", "title": f"F{i}", "ts": T0 - i * MIN} for i in range(50)]
        ev += [{"kind": "failed", "app": "radarr", "title": "F3", "ts": T0 + MIN}, {"kind": "stalled", "app": "radarr", "title": "old", "ts": T0 - 49 * HOUR}]
        out = qw.prune_digest(ev, T0 + MIN)
        self.assertEqual(len(out), 40)
        self.assertEqual(out[0], {"kind": "failed", "app": "radarr", "title": "F3", "ts": T0 + MIN})
        self.assertEqual([e["ts"] for e in out], sorted((e["ts"] for e in out), reverse=True))
        self.assertEqual(sum(e["title"] == "F3" for e in out), 1)
        self.assertNotIn("old", [e["title"] for e in out])

    def test_same_title_other_kind_is_its_own_series(self):
        out = qw.prune_digest([{"kind": "failed", "app": "radarr", "title": "A", "ts": T0}, {"kind": "stalled", "app": "radarr", "title": "A", "ts": T0}], T0)
        self.assertEqual(len(out), 2)

    def test_file_is_written_on_an_empty_run(self):
        qw.run({"radarr": fake()}, self.dir / "queue-watchdog.json", [], lambda t: True, qw.Config(), T0)
        text = (self.dir / "watchdog-digest.prom").read_text()
        self.assertIn("beefy_watchdog_digest_updated_timestamp_seconds 1800000000\n", text)
        self.assertNotIn("beefy_watchdog_event_timestamp_seconds{", text)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["queue-watchdog.json", "watchdog-digest.json", "watchdog-digest.prom"])

    def test_file_is_written_even_when_an_app_is_down(self):
        qw.run({"radarr": fake(fail=True)}, self.dir / "queue-watchdog.json", [], lambda t: True, qw.Config(), T0)
        self.assertTrue((self.dir / "watchdog-digest.prom").exists())

    def test_digest_events_from_a_run_land_in_the_prom_file(self):
        qw.run({"sonarr": fake([EP])}, self.dir / "queue-watchdog.json", [], lambda t: True, qw.Config(), T0)
        self.assertIn('beefy_watchdog_event_timestamp_seconds{kind="failed",app="sonarr",title="Show (2019) S02E03"} 1800000000', (self.dir / "watchdog-digest.prom").read_text())


class SendsLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sent_and_digest_lines(self):
        qw.run({"radarr": fake([BLOCKED]), "sonarr": fake([EP])}, self.dir / "queue-watchdog.json", [], lambda t: True, qw.Config(), T0)
        qw.run({"radarr": fake([BLOCKED]), "sonarr": fake()}, self.dir / "queue-watchdog.json", [], lambda t: True, qw.Config(), T0 + 31 * MIN)
        lines = sends(self.dir)
        self.assertEqual([l.split(" ", 3)[1:] for l in lines], [["failed", "digest", "Show (2019) S02E03"], ["blocked", "sent", "A Separation (2011)"]])
        self.assertTrue(lines[0].startswith("2027-01-15T09:00:00+01:00 "))

    def test_failed_send_is_not_logged(self):
        qw.run({"radarr": fake(health=[hc([NZB])])}, self.dir / "queue-watchdog.json", [], lambda t: False, qw.Config(), T0)
        self.assertEqual(sends(self.dir), [])

    def test_log_keeps_the_last_1000_lines(self):
        f = self.dir / "watchdog-sends.log"
        f.write_text("".join(f"old {i}\n" for i in range(1200)))
        qw.log_send(f, T0, "failed", "digest", "X")
        lines = f.read_text().splitlines()
        self.assertEqual(len(lines), 1000)
        self.assertEqual(lines[0], "old 201")
        self.assertTrue(lines[-1].endswith(" failed digest X"))


NZB, NZBP, TM, TPB = "NZBgeek (Prowlarr)", "NzbPlanet (Prowlarr)", "Treasure Maps (Prowlarr)", "The Pirate Bay (Prowlarr)"


class IndexerHealth(unittest.TestCase):
    def test_failure_names_come_from_both_health_checks(self):
        self.assertEqual(qw.failed_indexers([hc([NZB, NZBP])]), {NZB: None, NZBP: None})
        self.assertEqual(qw.failed_indexers([hc([NZB], "IndexerLongTermStatusCheck", True)]), {NZB: None})

    def test_unrelated_health_entries_are_ignored(self):
        other = {"source": "IndexerRssCheck", "type": "warning", "message": "No indexers available with RSS sync enabled"}
        self.assertEqual(qw.failed_indexers([other, hc([NZB])]), {NZB: None})
        self.assertEqual(qw.failed_indexers([]), {})

    def test_status_records_add_disabled_until_and_last_error(self):
        status = [{"indexerId": 1, "disabledTill": "2026-10-27T10:00:00Z", "mostRecentFailure": "2026-10-26T10:00:00Z", "lastError": "401 Unauthorized"}]
        got = qw.failed_indexers([hc([NZB])], status, [{"id": 1, "name": NZB}])
        self.assertEqual(got[NZB]["until"], "2026-10-27T10:00:00Z")
        self.assertEqual(got[NZB]["error"], "401 Unauthorized")

    def test_treasure_maps_grab_limit_is_expected(self):
        for err in (None, "HTTP 429 Too Many Requests", "Daily grab limit reached"):
            info = None if err is None else {"until": None, "error": err}
            self.assertEqual(qw.unexpected({TM: info}), {}, err)

    def test_treasure_maps_with_another_error_alerts(self):
        self.assertEqual(list(qw.unexpected({TM: {"until": None, "error": "401 Unauthorized"}})), [TM])

    def test_pirate_bay_flaky_no_results_is_expected(self):
        for err in (None, "No results in configured categories"):
            info = None if err is None else {"until": None, "error": err}
            self.assertEqual(qw.unexpected({TPB: info}), {}, err)
        self.assertEqual(list(qw.unexpected({TPB: {"until": None, "error": "Connection refused"}})), [TPB])

    def test_other_indexers_are_never_exempt(self):
        self.assertEqual(list(qw.unexpected({NZB: {"until": None, "error": "429 limit"}, TM: None, TPB: None})), [NZB])

    def test_message_has_app_names_date_fix_and_collapsed_details(self):
        text = qw.indexer_message("sonarr", {NZB: {"until": "2026-10-27T10:00:00Z", "error": "401 Unauthorized"}, NZBP: None}, T0)
        head, body = text.split("\n\n", 1)
        self.assertEqual(head, "🟠 <b>Sonarr: indexers disabled after failures</b>")
        self.assertRegex(body, r"^📅 \d{4}-\d\d-\d\d \d\d:\d\d\n• ")
        self.assertIn("<blockquote expandable>", text)
        for part in (NZB, NZBP, "2026-10-27T10:00:00Z", "401 Unauthorized", "Sonarr → System → Status", "POST /api/v3/indexer/testall"):
            self.assertIn(part, text)

    def test_message_escapes_html(self):
        self.assertIn("&lt;x&gt;", qw.indexer_message("radarr", {"<x>": None}, T0))


class IndexerRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.state = self.dir / "state.json"
        self.sent = []
        self.fail_send = False

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, text):
        if self.fail_send:
            return False
        self.sent.append(text)
        return True

    def go(self, apps, now):
        return qw.run(apps, self.state, [], self.send, qw.Config(), now)

    def test_one_alert_then_silence_then_recovered(self):
        bad = {"sonarr": fake(health=[hc([NZB, NZBP])])}
        self.assertEqual(self.go(bad, T0), 0)
        self.assertEqual(self.go(bad, T0 + 15 * MIN), 0)
        self.assertEqual(self.go(bad, T0 + 30 * MIN), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("indexers disabled", self.sent[0])
        self.assertEqual(self.go({"sonarr": fake()}, T0 + 45 * MIN), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual([(e["kind"], e["app"]) for e in digest(self.dir)], [("indexer_recovered", "sonarr")])
        self.assertEqual(self.go({"sonarr": fake()}, T0 + 60 * MIN), 0)
        self.assertEqual(len(self.sent), 1)

    def test_alert_again_after_it_cleared_and_came_back(self):
        bad = {"radarr": fake(health=[hc([NZB])])}
        self.go(bad, T0)
        self.go({"radarr": fake()}, T0 + 15 * MIN)
        self.go(bad, T0 + 30 * MIN)
        self.assertEqual(len(self.sent), 2)

    def test_apps_are_tracked_separately(self):
        self.go({"radarr": fake(health=[hc([NZB])]), "sonarr": fake(health=[hc([NZB])])}, T0)
        self.assertEqual(len(self.sent), 2)

    def test_only_exempt_indexers_send_nothing(self):
        self.assertEqual(self.go({"radarr": fake(health=[hc([TM, TPB])])}, T0), 0)
        self.assertEqual(self.sent, [])

    def test_exempt_indexers_are_left_out_of_the_alert(self):
        self.go({"radarr": fake(health=[hc([TM, NZB, TPB])])}, T0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn(NZB, self.sent[0])
        self.assertNotIn("Treasure Maps", self.sent[0])
        self.assertNotIn("Pirate Bay", self.sent[0])

    def test_no_recovered_message_when_only_exempt_ones_remain(self):
        self.go({"radarr": fake(health=[hc([NZB, TPB])])}, T0)
        self.go({"radarr": fake(health=[hc([TPB])])}, T0 + 15 * MIN)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(digest(self.dir)), 1)
        self.go({"radarr": fake(health=[hc([TPB])])}, T0 + 30 * MIN)
        self.assertEqual(len(digest(self.dir)), 1)

    def test_failed_send_is_retried_next_run(self):
        bad = {"radarr": fake(health=[hc([NZB])])}
        self.fail_send = True
        self.assertEqual(self.go(bad, T0), 1)
        self.fail_send = False
        self.assertEqual(self.go(bad, T0 + 15 * MIN), 0)
        self.assertEqual(len(self.sent), 1)

    def test_recovered_needs_no_send(self):
        self.go({"radarr": fake(health=[hc([NZB])])}, T0)
        self.fail_send = True
        self.assertEqual(self.go({"radarr": fake()}, T0 + 15 * MIN), 0)
        self.assertEqual(len(self.sent), 1)
        [e] = digest(self.dir)
        self.assertEqual((e["kind"], e["title"]), ("indexer_recovered", NZB))

    def test_health_api_error_keeps_the_state_and_does_not_fake_a_recovery(self):
        self.go({"radarr": fake(health=[hc([NZB])])}, T0)
        self.assertEqual(self.go({"radarr": fake(health_fail=True)}, T0 + 15 * MIN), 1)
        self.go({"radarr": fake(health=[hc([NZB])])}, T0 + 30 * MIN)
        self.assertEqual(len(self.sent), 1)

    def test_missing_indexerstatus_endpoint_is_not_an_error(self):
        self.assertEqual(self.go({"radarr": fake(health=[hc([NZB])], status=None)}, T0), 0)
        self.assertEqual(len(self.sent), 1)

    def test_indexerstatus_details_reach_the_message(self):
        status = [{"indexerId": 1, "disabledTill": "2026-10-27T10:00:00Z", "lastError": "401 Unauthorized"}]
        self.go({"radarr": fake(health=[hc([NZB])], status=status, indexers=[{"id": 1, "name": NZB}])}, T0)
        self.assertIn("401 Unauthorized", self.sent[0])

    def test_indexer_state_does_not_disturb_queue_state(self):
        self.go({"radarr": fake([BLOCKED], health=[hc([NZB])])}, T0)
        self.go({"radarr": fake([BLOCKED], health=[hc([NZB])])}, T0 + 31 * MIN)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("radarr:abc", json.loads(self.state.read_text()))


ID_MSG = "Found matching series via grab history, but release was matched to series by ID. Automatic import is not possible. See the FAQ for details."
PACK = "The.Boys.2024.S04.2160p.AMZN.WEB-DL.H.265.HDR.DDP5.1-PTerWEB"
OUT = f"/data/usenet/complete/tv/{PACK}"
BOYS = {"id": 16, "title": "The Boys", "year": 2019, "tvdbId": 355567, "alternateTitles": [{"title": "The Boys 2019", "seasonNumber": -1}, {"title": "Пацаны", "seasonNumber": -1}]}
AIR = {1: "2024-06-13", 2: "2024-06-13", 3: "2024-06-13", 4: "2024-06-20", 5: "2024-06-27", 6: "2024-07-04", 7: "2024-07-11", 8: "2024-07-18"}


def pack_queue(title=PACK, eps=range(1, 9), messages=(ID_MSG,), state="importBlocked", status="completed"):
    return [{"id": 100 + n, "downloadId": "boys4", "title": title, "status": status, "trackedDownloadStatus": "warning", "trackedDownloadState": state,
             "sizeleft": 0, "seriesId": 16, "episodeId": 1000 + n, "outputPath": OUT, "series": {"title": "The Boys", "year": 2019},
             "episode": {"id": 1000 + n, "seasonNumber": 4, "episodeNumber": n, "airDate": AIR.get(n, "2024-07-18")},
             "statusMessages": [{"title": title, "messages": list(messages)}]} for n in eps]


def pack_history(eps=range(1, 9), series_id=16, match="Id", tvdb="355567", event="grabbed"):
    return [{"id": 1300 + n, "eventType": event, "seriesId": series_id, "episodeId": 1000 + n, "downloadId": "boys4",
             "data": {"seriesMatchType": match, "tvdbId": tvdb, "releaseSource": "Search", "releaseType": "SeasonPack"}} for n in eps]


def pack_proposals(eps=range(1, 9), series_id=16, rejections=(), folder=OUT):
    return [{"id": 600 + n, "path": f"{folder}/The.Boys.2024.S04E{n:02d}.2160p.AMZN.WEB-DL.H.265.HDR.DDP5.1-PTerWEB.mkv", "folderName": PACK,
             "series": {"id": series_id}, "seasonNumber": 4, "episodes": [{"id": 1000 + n, "seasonNumber": 4, "episodeNumber": n}],
             "quality": {"quality": {"id": 18, "name": "WEBDL-2160p"}, "revision": {"version": 1}}, "languages": [{"id": 1, "name": "English"}],
             "releaseGroup": "PTerWEB", "releaseType": "seasonPack", "indexerFlags": 0, "rejections": list(rejections)} for n in eps]


def parsed(title_wo_year="The Boys", year=2024, season=4, full=True, eps=()):
    return {"parsedEpisodeInfo": {"seriesTitle": f"{title_wo_year} {year}" if year else title_wo_year, "seasonNumber": season, "fullSeason": full, "episodeNumbers": list(eps),
                                  "seriesTitleInfo": {"title": f"{title_wo_year} {year}" if year else title_wo_year, "titleWithoutYear": title_wo_year, "year": year}}}


class FakeSonarr:
    def __init__(self, queue=None, history=None, proposals=None, parse=None, series=BOYS, fail_on=()):
        self.queue = pack_queue() if queue is None else queue
        self.history = pack_history() if history is None else history
        self.proposals = pack_proposals() if proposals is None else proposals
        self.parse = parsed() if parse is None else parse
        self.series, self.fail_on, self.posts, self.paths = series, set(fail_on), [], []

    def __call__(self, method, path, query=None, body=None):
        self.paths.append(path)
        if path in self.fail_on:
            raise qw.ApiError(f"{method} {path}: down")
        if method == "POST":
            assert path == "/command", path
            self.posts.append(body)
            return {"id": 77, "name": body["name"], "status": "queued"}
        if path == "/queue":
            return {"totalRecords": len(self.queue), "records": list(self.queue)}
        if path in ("/health", "/indexerstatus", "/indexer"):
            return []
        if path == f"/series/{self.series['id']}":
            return self.series
        if path == "/history":
            assert query["downloadId"] == "boys4", query
            return {"totalRecords": len(self.history), "records": list(self.history)}
        if path == "/parse":
            assert query["title"], query
            return self.parse
        if path == "/manualimport":
            assert query["downloadId"] == "boys4" and "seriesId" not in query, query
            return list(self.proposals)
        raise AssertionError((method, path))


class AutoImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.state = self.dir / "state.json"
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, text):
        self.sent.append(text)
        return True

    def go(self, api, now, **cfg):
        return qw.run({"sonarr": api}, self.state, [], self.send, qw.Config(**cfg), now)

    def refused(self, api):
        self.assertEqual(self.go(api, T0), 0)
        self.assertEqual(api.posts, [])
        self.go(api, T0 + 31 * MIN)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Sonarr queue: blocked", self.sent[0])
        self.assertIn("Auto-import: not done", self.sent[0])
        return self.sent[0]

    def test_id_matched_season_pack_of_the_grabbed_series_is_imported(self):
        api = FakeSonarr()
        self.assertEqual(self.go(api, T0), 0)
        [cmd] = api.posts
        self.assertEqual((cmd["name"], cmd["importMode"]), ("ManualImport", "auto"))
        self.assertEqual(sorted(f["episodeIds"][0] for f in cmd["files"]), list(range(1001, 1009)))
        f = cmd["files"][0]
        self.assertEqual((f["seriesId"], f["downloadId"], f["releaseGroup"], f["quality"]["quality"]["name"]), (16, "boys4", "PTerWEB", "WEBDL-2160p"))
        self.assertTrue(f["path"].startswith(OUT + "/"))
        [e] = digest(self.dir)
        self.assertEqual((e["kind"], e["app"], e["title"]), ("auto_imported", "sonarr", f"The Boys (2019) S04: {PACK}"))
        self.assertTrue(any(" auto_imported digest " in line for line in sends(self.dir)))

    def test_imported_once_then_the_normal_blocked_alert_if_it_stays_blocked(self):
        api = FakeSonarr()
        self.go(api, T0)
        self.go(api, T0 + 15 * MIN)
        self.go(api, T0 + 30 * MIN)
        self.assertEqual(len(api.posts), 1)
        self.assertEqual(self.sent, [])
        self.go(api, T0 + 46 * MIN)
        self.assertEqual(len(api.posts), 1)
        self.assertEqual(len(self.sent), 1)

    def test_attempt_state_is_dropped_once_the_download_left_the_queue(self):
        api = FakeSonarr()
        self.go(api, T0)
        api.queue = []
        self.go(api, T0 + 15 * MIN)
        self.assertNotIn("autoimport:sonarr:boys4", json.loads(self.state.read_text()))

    def test_single_episode_release_is_imported(self):
        title = "The.Boys.2024.S04E05.2160p.AMZN.WEB-DL-PTerWEB"
        api = FakeSonarr(queue=pack_queue(title, eps=[5]), history=pack_history([5]), proposals=pack_proposals([5]), parse=parsed(full=False, eps=[5]))
        self.go(api, T0)
        self.assertEqual(len(api.posts), 1)

    def test_alternate_title_counts(self):
        api = FakeSonarr(series=dict(BOYS, title="Pacany"), parse=parsed("Пацаны"))
        self.go(api, T0)
        self.assertEqual(len(api.posts), 1)

    def test_other_title_is_refused(self):
        self.assertIn("title", self.refused(FakeSonarr(parse=parsed("The Boys Presents Diabolical"))))

    def test_year_outside_the_series_and_episode_air_years_is_refused(self):
        self.assertIn("year", self.refused(FakeSonarr(parse=parsed(year=2021))))

    def test_season_mismatch_is_refused(self):
        self.refused(FakeSonarr(parse=parsed(season=3)))

    def test_episode_numbers_must_match_for_a_single_episode(self):
        api = FakeSonarr(queue=pack_queue(eps=[5]), history=pack_history([5]), proposals=pack_proposals([5]), parse=parsed(full=False, eps=[6]))
        self.refused(api)

    def test_grab_history_must_be_this_series_by_id_with_the_same_tvdb_id(self):
        for hist in (pack_history(series_id=17), pack_history(match="Title"), pack_history(tvdb="1"), [], pack_history(event="downloadFolderImported")):
            self.setUp()
            self.refused(FakeSonarr(history=hist))

    def test_grabbed_episodes_must_equal_the_queued_ones(self):
        self.refused(FakeSonarr(history=pack_history(range(1, 8))))

    def test_proposals_must_cover_exactly_the_grabbed_episodes(self):
        for props in (pack_proposals(range(1, 8)), pack_proposals() + pack_proposals([3]), pack_proposals(list(range(1, 9)) + [9]), []):
            self.setUp()
            self.refused(FakeSonarr(proposals=props))

    def test_proposal_rejection_or_other_series_or_foreign_path_is_refused(self):
        rejected = pack_proposals()
        rejected[2] = dict(rejected[2], rejections=[{"reason": "Sample", "type": "permanent"}])
        for props in (rejected, pack_proposals(series_id=17), pack_proposals(folder="/data/media/tv/The Boys (2019) [tvdbid-355567]/Season 04")):
            self.setUp()
            self.refused(FakeSonarr(proposals=props))

    def test_any_other_status_message_is_left_alone(self):
        api = FakeSonarr(queue=pack_queue(messages=(ID_MSG, "Sample")))
        self.go(api, T0)
        self.assertEqual(api.posts, [])
        self.assertNotIn("/manualimport", api.paths)

    def test_other_blocked_reasons_are_not_candidates(self):
        api = FakeSonarr(queue=pack_queue(messages=("No files found are eligible for import",)))
        self.go(api, T0)
        self.assertNotIn("/parse", api.paths)

    def test_mixed_download_with_a_record_not_id_matched_is_left_alone(self):
        q = pack_queue()
        q[0] = dict(q[0], trackedDownloadState="importPending")
        api = FakeSonarr(queue=q)
        self.go(api, T0)
        self.assertEqual(api.posts, [])
        self.assertNotIn("/manualimport", api.paths)


    def test_switched_off_by_config(self):
        api = FakeSonarr()
        self.go(api, T0, auto_import=False)
        self.assertEqual(api.posts, [])
        self.assertNotIn("/manualimport", api.paths)

    def test_api_error_during_the_check_is_retried_next_run(self):
        api = FakeSonarr(fail_on={"/manualimport"})
        self.assertEqual(self.go(api, T0), 1)
        self.assertEqual(api.posts, [])
        api.fail_on = set()
        self.go(api, T0 + 15 * MIN)
        self.assertEqual(len(api.posts), 1)

    def test_failed_command_post_is_not_retried_and_falls_back_to_the_alert(self):
        api = FakeSonarr(fail_on={"/command"})
        self.assertEqual(self.go(api, T0), 1)
        self.go(api, T0 + 15 * MIN)
        self.assertEqual(api.paths.count("/command"), 1)
        self.go(api, T0 + 46 * MIN)
        self.assertEqual(len(self.sent), 1)

    def test_dry_run_reports_and_changes_nothing(self):
        api = FakeSonarr()
        lines = qw.dry_run({"sonarr": api}, qw.Config())
        self.assertEqual(api.posts, [])
        self.assertFalse(self.state.exists())
        self.assertIn(f"would import: The Boys (2019) S04: {PACK} (8 files)", lines)
        self.assertEqual(lines[-1], "id-match auto-import: 1 candidate(s), 1 importable")

    def test_dry_run_names_the_reason(self):
        lines = qw.dry_run({"sonarr": FakeSonarr(parse=parsed(year=2021))}, qw.Config())
        self.assertTrue(any(line.startswith("not imported: ") and "year" in line for line in lines), lines)
        self.assertEqual(lines[-1], "id-match auto-import: 1 candidate(s), 0 importable")


def warn(text):
    return {"time": "2026-11-08T17:43:24Z", "level": "warn", "logger": "Torznab", "message": text}


X1, X1R, KN = "1337x (Prowlarr)", "1337x (old-rare) (Prowlarr)", "Knaben (Prowlarr)"
LIMIT = "API Request Limit reached for {}. Disabled for 00:05:00"


class RateLimit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.state = self.dir / "state.json"
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, text):
        self.sent.append(text)
        return True

    def go(self, api, now):
        return qw.run({"radarr": api}, self.state, [], self.send, qw.Config(), now)

    def test_request_limit_from_the_log_goes_to_the_digest(self):
        self.assertEqual(self.go(fake(health=[hc([X1])], logs=[warn(LIMIT.format(X1))]), T0), 0)
        self.assertEqual(self.sent, [])
        [e] = digest(self.dir)
        self.assertEqual((e["kind"], e["app"], e["title"]), ("indexer_rate_limited", "radarr", X1))

    def test_429_and_too_many_requests_in_last_error_go_to_the_digest(self):
        for err in ("HTTP 429", "Too Many Requests", "Request limit reached: 100"):
            self.setUp()
            status = [{"indexerId": 4, "disabledTill": "2026-11-08T17:48:24Z", "lastError": err}]
            self.go(fake(health=[hc([KN])], status=status, indexers=[{"id": 4, "name": KN}]), T0)
            self.assertEqual(self.sent, [], err)
            self.assertEqual([e["kind"] for e in digest(self.dir)], ["indexer_rate_limited"], err)

    def test_other_failure_of_the_same_indexer_alerts_at_once(self):
        self.go(fake(health=[hc([X1])], logs=[warn(f"{X1} server is currently unavailable. http://gluetun:9696/4/api?t=movie Http request timed out")]), T0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn(X1, self.sent[0])

    def test_newest_log_line_of_that_indexer_decides(self):
        logs = [warn(f"{X1} server is currently unavailable. Http request timed out"), warn(LIMIT.format(X1))]
        self.go(fake(health=[hc([X1])], logs=logs), T0)
        self.assertEqual(len(self.sent), 1)

    def test_old_rare_copy_line_does_not_count_for_the_main_indexer(self):
        self.go(fake(health=[hc([X1])], logs=[warn(LIMIT.format(X1R))]), T0)
        self.assertEqual(len(self.sent), 1)

    def test_mixed_alert_names_only_the_real_failure(self):
        status = [{"indexerId": 1, "lastError": "401 Unauthorized"}]
        self.go(fake(health=[hc([X1, NZB])], status=status, indexers=[{"id": 1, "name": NZB}], logs=[warn(LIMIT.format(X1))]), T0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn(NZB, self.sent[0])
        self.assertNotIn("1337x", self.sent[0])
        self.assertEqual([e["title"] for e in digest(self.dir)], [X1])

    def test_digest_once_while_limited_again_after_it_cleared(self):
        limited = fake(health=[hc([X1])], logs=[warn(LIMIT.format(X1))])
        self.go(limited, T0)
        self.go(limited, T0 + 15 * MIN)
        self.assertEqual(sum(" indexer_rate_limited digest " in line for line in sends(self.dir)), 1)
        self.go(fake(), T0 + 30 * MIN)
        self.go(limited, T0 + 45 * MIN)
        self.assertEqual(sum(" indexer_rate_limited digest " in line for line in sends(self.dir)), 2)
        self.assertEqual(self.sent, [])

    def test_log_api_error_falls_back_to_the_alert(self):
        def api(method, path, query=None, body=None):
            if path == "/log":
                raise qw.ApiError("GET /log: down")
            return fake(health=[hc([X1])])(method, path, query, body)
        self.assertEqual(self.go(api, T0), 0)
        self.assertEqual(len(self.sent), 1)


RADARR_ID = "Found matching movie via grab history, but release was matched to movie by ID. Manual Import required."
MOVIES = {
    139: {"id": 139, "title": "Traffic", "originalTitle": "Traffic", "year": 2000, "secondaryYear": None, "tmdbId": 1900,
          "alternateTitles": [{"title": "Traffic - Macht des Kartells"}, {"title": "Tráfico"}]},
    147: {"id": 147, "title": "Avengers: Endgame", "originalTitle": "Avengers: Endgame", "year": 2019, "tmdbId": 299534, "alternateTitles": [{"title": "Avengers 4"}]},
    90: {"id": 90, "title": "Analyze This", "originalTitle": "Analyze This", "year": 1999, "tmdbId": 9535, "alternateTitles": [{"title": "Reine Nervensache"}]},
    104: {"id": 104, "title": "Drive", "originalTitle": "Drive", "year": 2011, "tmdbId": 64690, "alternateTitles": []},
}
RELEASES = {
    139: ("Traffic.-.Die.Macht.des.Kartells.Special.Edition.2000.German.DTS.DL.1080p.BluRay.x264-AVG", ["Traffic -. Die Macht des Kartells"], 2000),
    147: ("Avengers.4.Endgame.2019.German.Dubbed.DTSHD.DL.2160p.UHD.BluRay.HDR.x265.PS", ["Avengers 4 Endgame"], 2019),
    90: ("Reine.Nervensache.2.2002.German.DL.1080p.BluRay.x264-MOViESTARS", ["Reine Nervensache 2"], 2002),
    104: ("Drive.1997.Directors.Cut.2160p.UHD.BluRay.HDR.DoVi.TrueHD.7.1.Atmos.x265-SPHD", ["Drive"], 1997),
}


def movie_rec(mid, title=None, state="importBlocked", messages=(RADARR_ID,), status="completed", out=None):
    title = title or RELEASES[mid][0]
    return {"id": 3000 + mid, "downloadId": f"dl{mid}", "title": title, "status": status, "trackedDownloadStatus": "warning", "trackedDownloadState": state,
            "sizeleft": 0, "movieId": mid, "outputPath": out or f"/data/usenet/complete/movies/{title}/", "movie": {"title": MOVIES[mid]["title"], "year": MOVIES[mid]["year"]},
            "statusMessages": [{"title": title, "messages": list(messages)}]}


class FakeRadarr:
    def __init__(self, queue, movies=MOVIES, releases=RELEASES, match="Id", rejections=(), fail_on=(), proposal_movie=None):
        self.queue, self.movies, self.releases, self.match = queue, movies, dict(releases), match
        self.rejections, self.fail_on, self.proposal_movie = list(rejections), set(fail_on), proposal_movie
        self.posts, self.deletes, self.paths = [], [], []

    def by_dl(self, dl):
        return next(r for r in self.queue if r["downloadId"] == dl)

    def __call__(self, method, path, query=None, body=None):
        self.paths.append(f"{method} {path}")
        if f"{method} {path}" in self.fail_on or path in self.fail_on:
            raise qw.ApiError(f"{method} {path}: down")
        if method == "DELETE":
            self.deletes.append((path, dict(query or {})))
            return None
        if method == "POST":
            assert path == "/command", path
            self.posts.append(body)
            return {"id": 88, "name": body["name"]}
        if path == "/queue":
            return {"totalRecords": len(self.queue), "records": list(self.queue)}
        if path in ("/health", "/indexerstatus", "/indexer"):
            return []
        if path.startswith("/movie/"):
            return self.movies[int(path.rsplit("/", 1)[1])]
        if path == "/parse":
            mid = next(m for m, (t, _, _) in self.releases.items() if t == query["title"])
            _, titles, year = self.releases[mid]
            return {"title": query["title"], "parsedMovieInfo": {"movieTitles": titles, "primaryMovieTitle": titles[0], "year": year}}
        if path == "/history":
            rec = self.by_dl(query["downloadId"])
            mid = rec["movieId"]
            return {"records": [{"id": 1, "eventType": "grabbed", "movieId": mid, "downloadId": rec["downloadId"],
                                 "data": {"movieMatchType": self.match, "tmdbId": str(self.movies[mid]["tmdbId"])}}]}
        if path == "/manualimport":
            rec = self.by_dl(query["downloadId"])
            mid = rec["movieId"]
            return [{"id": 9, "path": rec["outputPath"].rstrip("/") + "/" + rec["title"] + ".mkv", "folderName": rec["title"], "movie": {"id": self.proposal_movie or mid},
                     "quality": {"quality": {"id": 7, "name": "Bluray-1080p"}}, "languages": [{"id": 4, "name": "German"}], "releaseGroup": "AVG", "indexerFlags": 0,
                     "downloadId": rec["downloadId"], "rejections": list(self.rejections)}]
        raise AssertionError((method, path))


DELETE_QUERY = {"removeFromClient": "true", "blocklist": "true", "skipRedownload": "false"}


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.state = self.dir / "state.json"
        self.sent = []
        self.fail_send = False

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, text):
        if self.fail_send:
            return False
        self.sent.append(text)
        return True

    def go(self, apps, now, **cfg):
        return qw.run(apps, self.state, [], self.send, qw.Config(**cfg), now)


class Backstop(Harness):
    def test_wrong_year_id_match_is_removed_blocklisted_once(self):
        api = FakeRadarr([movie_rec(104), movie_rec(90)])
        self.assertEqual(self.go({"radarr": api}, T0), 0)
        self.assertEqual(sorted(api.deletes), [("/queue/3090", DELETE_QUERY), ("/queue/3104", DELETE_QUERY)])
        self.assertEqual(api.posts, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(sorted((e["kind"], e["app"]) for e in digest(self.dir)), [("guard_removed", "radarr")] * 2)
        self.assertTrue(any("Drive (2011)" in e["title"] for e in digest(self.dir)))
        self.go({"radarr": api}, T0 + 15 * MIN)
        self.assertEqual(len(api.deletes), 2)

    def test_right_year_id_match_is_never_removed(self):
        api = FakeRadarr([movie_rec(139, state="importPending"), movie_rec(147, messages=(RADARR_ID, "other"))])
        self.go({"radarr": api}, T0)
        self.assertEqual(api.deletes, [])

    def test_two_years_off_and_the_secondary_year_are_kept_like_the_guard(self):
        movies = {**MOVIES, 104: dict(MOVIES[104], year=1999), 90: dict(MOVIES[90], secondaryYear=2002)}
        api = FakeRadarr([movie_rec(104, messages=(RADARR_ID, "x")), movie_rec(90, messages=(RADARR_ID, "x"))], movies=movies)
        self.go({"radarr": api}, T0)
        self.assertEqual(api.deletes, [])

    def test_release_without_a_year_is_kept(self):
        rel = {**RELEASES, 104: ("Drive.Directors.Cut.2160p-SPHD", ["Drive"], 0)}
        api = FakeRadarr([movie_rec(104, title="Drive.Directors.Cut.2160p-SPHD")], releases=rel)
        self.go({"radarr": api}, T0)
        self.assertEqual(api.deletes, [])

    def test_wrong_year_without_the_id_match_message_is_left_alone(self):
        api = FakeRadarr([movie_rec(104, messages=("No files found are eligible for import",))])
        self.go({"radarr": api}, T0)
        self.assertEqual(api.deletes, [])
        self.assertNotIn("GET /parse", api.paths)

    def test_importing_or_downloading_items_are_not_candidates(self):
        api = FakeRadarr([movie_rec(104, state="downloading", status="downloading"), movie_rec(90, state="importing")])
        self.go({"radarr": api}, T0)
        self.assertEqual(api.deletes, [])

    def test_failed_delete_sends_one_telegram_and_is_retried(self):
        api = FakeRadarr([movie_rec(104)], fail_on={"DELETE /queue/3104"})
        self.assertEqual(self.go({"radarr": api}, T0), 1)
        self.assertEqual(len(self.sent), 1)
        for part in ("Drive (2011)", "Drive.1997", "could not be removed", "Radarr → Activity → Queue"):
            self.assertIn(part, self.sent[0])
        self.go({"radarr": api}, T0 + 15 * MIN)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(api.paths.count("DELETE /queue/3104"), 2)
        self.assertEqual(digest(self.dir), [])

    def test_api_error_while_checking_removes_nothing(self):
        api = FakeRadarr([movie_rec(104)], fail_on={"GET /parse"})
        self.assertEqual(self.go({"radarr": api}, T0), 1)
        self.assertEqual(api.deletes, [])

    def test_sonarr_pack_of_a_same_named_older_series_is_removed(self):
        series = dict(BOYS, firstAired="2019-07-26T00:00:00Z", lastAired="2024-07-18T00:00:00Z", status="continuing")
        api = FakeSonarr(parse=parsed(year=2008), series=series, fail_on={"/manualimport"})
        api.deletes = []
        orig = api.__call__

        def call(method, path, query=None, body=None):
            if method == "DELETE":
                api.deletes.append(path)
                return None
            return orig(method, path, query, body)
        self.go({"sonarr": call}, T0)
        self.assertEqual(api.deletes, ["/queue/101"])
        self.assertEqual([e["kind"] for e in digest(self.dir)], ["guard_removed"])

    def test_sonarr_season_air_year_is_kept(self):
        series = dict(BOYS, firstAired="2019-07-26T00:00:00Z", lastAired="2024-07-18T00:00:00Z", status="continuing")
        api = FakeSonarr(series=series)
        self.go({"sonarr": api}, T0)
        self.assertEqual(len(api.posts), 1)


class RadarrAutoImport(Harness):
    def test_traffic_and_endgame_are_imported(self):
        api = FakeRadarr([movie_rec(139), movie_rec(147)])
        self.assertEqual(self.go({"radarr": api}, T0), 0)
        self.assertEqual(api.deletes, [])
        self.assertEqual(len(api.posts), 2)
        files = {f["movieId"]: f for cmd in api.posts for f in cmd["files"]}
        self.assertEqual(sorted(files), [139, 147])
        f = files[139]
        self.assertEqual((api.posts[0]["name"], api.posts[0]["importMode"]), ("ManualImport", "auto"))
        self.assertEqual((f["downloadId"], f["releaseGroup"], f["quality"]["quality"]["name"]), ("dl139", "AVG", "Bluray-1080p"))
        self.assertTrue(f["path"].startswith("/data/usenet/complete/movies/Traffic."))
        self.assertEqual(sorted(e["kind"] for e in digest(self.dir)), ["auto_imported", "auto_imported"])
        self.assertEqual(self.sent, [])

    def test_imported_once(self):
        api = FakeRadarr([movie_rec(139)])
        self.go({"radarr": api}, T0)
        self.go({"radarr": api}, T0 + 15 * MIN)
        self.assertEqual(len(api.posts), 1)

    def test_only_an_alternate_title_matches(self):
        movies = {**MOVIES, 139: dict(MOVIES[139], title="Traffic (US)", originalTitle="Traffic (US)")}
        api = FakeRadarr([movie_rec(139)], movies=movies)
        self.go({"radarr": api}, T0)
        self.assertEqual(len(api.posts), 1)

    def check(self, api, mid):
        return qw.id_import_files("radarr", api, f"dl{mid}", [r for r in api.queue if r["movieId"] == mid])

    def test_analyze_this_2002_and_drive_1997_are_refused(self):
        api = FakeRadarr([movie_rec(90), movie_rec(104)])
        for mid in (90, 104):
            with self.assertRaisesRegex(qw.Refused, "year"):
                self.check(api, mid)

    def test_title_words_with_another_year_are_refused(self):
        rel = {**RELEASES, 104: ("Drive.2012.1080p.BluRay-X", ["Drive"], 2012)}
        api = FakeRadarr([movie_rec(104, title="Drive.2012.1080p.BluRay-X")], releases=rel)
        self.go({"radarr": api}, T0)
        self.assertEqual((api.posts, api.deletes), ([], []))
        with self.assertRaisesRegex(qw.Refused, "year"):
            self.check(api, 104)

    def test_same_year_but_a_title_word_missing_is_refused(self):
        rel = {**RELEASES, 139: ("Die.Macht.des.Kartells.2000.German.DL.1080p-AVG", ["Die Macht des Kartells"], 2000)}
        api = FakeRadarr([movie_rec(139, title="Die.Macht.des.Kartells.2000.German.DL.1080p-AVG")], releases=rel)
        with self.assertRaisesRegex(qw.Refused, "title"):
            self.check(api, 139)

    def test_history_must_be_this_movie_by_id_with_the_same_tmdb_id(self):
        with self.assertRaisesRegex(qw.Refused, "TMDB"):
            self.check(FakeRadarr([movie_rec(139)], match="Title"), 139)
        movies = dict(MOVIES)
        api = FakeRadarr([movie_rec(139)], movies=movies)
        orig = api.__call__

        def call(method, path, query=None, body=None):
            r = orig(method, path, query, body)
            if path == "/history":
                r["records"][0]["data"]["tmdbId"] = "1"
            return r
        with self.assertRaisesRegex(qw.Refused, "TMDB"):
            qw.id_import_files("radarr", call, "dl139", [movie_rec(139)])

    def test_rejected_or_foreign_proposal_is_refused(self):
        with self.assertRaisesRegex(qw.Refused, "rejects"):
            self.check(FakeRadarr([movie_rec(139)], rejections=[{"reason": "Sample", "type": "permanent"}]), 139)
        with self.assertRaisesRegex(qw.Refused, "proposed"):
            self.check(FakeRadarr([movie_rec(139)], proposal_movie=5), 139)

    def test_refused_item_gets_the_reason_in_the_blocked_alert(self):
        rel = {**RELEASES, 139: ("Die.Macht.des.Kartells.2000.German.DL.1080p-AVG", ["Die Macht des Kartells"], 2000)}
        api = FakeRadarr([movie_rec(139, title="Die.Macht.des.Kartells.2000.German.DL.1080p-AVG")], releases=rel)
        self.go({"radarr": api}, T0)
        self.go({"radarr": api}, T0 + 31 * MIN)
        [text] = self.sent
        self.assertIn("Auto-import: not done", text)
        self.assertIn("title", text)

    def test_dry_run_covers_radarr(self):
        api = FakeRadarr([movie_rec(139), movie_rec(104)])
        lines = qw.dry_run({"radarr": api}, qw.Config())
        self.assertEqual((api.posts, api.deletes), ([], []))
        self.assertTrue(any(line.startswith("would import: Traffic (2000)") for line in lines), lines)
        self.assertTrue(any(line.startswith("would remove (guard_removed): Drive (2011)") for line in lines), lines)
        self.assertEqual(lines[-1], "id-match auto-import: 1 candidate(s), 1 importable")


JURY = {"id": 32, "title": "Jury Duty Presents: Behind the Scenes of Company Retreat", "year": 2026, "tvdbId": 466000, "alternateTitles": []}
RETREAT = "Company.Retreat.2026.S01.Complete.2160p.AMZN.WEB-DL.H.265.DDP5.1-UBWEB"


def retreat(year=2026):
    out = f"/data/usenet/complete/tv/{RETREAT}"
    queue = [dict(r, seriesId=32, outputPath=out, series={"title": JURY["title"], "year": 2026}, title=RETREAT,
                  episode=dict(r["episode"], seasonNumber=1, airDate="2026-10-02")) for r in pack_queue(eps=range(1, 11))]
    hist = [dict(h, seriesId=32, data=dict(h["data"], tvdbId="466000")) for h in pack_history(range(1, 11))]
    props = [dict(p, series={"id": 32}, path=f"{out}/Company.Retreat.S01E{n:02d}.mkv", episodes=[dict(p["episodes"][0], seasonNumber=1)])
             for n, p in zip(range(1, 11), pack_proposals(range(1, 11)))]
    return FakeSonarr(queue=queue, history=hist, proposals=props, parse=parsed("Company Retreat", year, season=1), series=JURY)


class SonarrLooseTitle(Harness):
    def test_jury_duty_company_retreat_is_imported(self):
        api = retreat()
        self.go({"sonarr": api}, T0)
        self.assertEqual(len(api.posts), 1)
        self.assertEqual(len(api.posts[0]["files"]), 10)

    def test_title_part_with_another_year_is_refused(self):
        api = retreat(2025)
        self.go({"sonarr": api}, T0)
        self.assertEqual(api.posts, [])
        with self.assertRaisesRegex(qw.Refused, "title"):
            qw.id_import_files("sonarr", api, "boys4", api.queue)

    def test_part_of_a_word_does_not_count(self):
        api = retreat()
        api.parse = parsed("pany Retreat", 2026, season=1)
        with self.assertRaisesRegex(qw.Refused, "title"):
            qw.id_import_files("sonarr", api, "boys4", api.queue)


VQ = "VisionQuest S01E08 Forever 1080p WEB H264-GRACE"
EXE_MSG = "Caution: Found executable file with extension: '.exe'"


def vq_rec(messages=(EXE_MSG,), out=f"/data/torrents/movies/{VQ}.exe", state="importPending", status="completed"):
    return {"id": 766, "downloadId": "26F9F9B9", "title": VQ, "status": status, "trackedDownloadStatus": "warning", "trackedDownloadState": state,
            "sizeleft": 0, "seriesId": 22, "episodeId": 2208, "outputPath": out, "series": {"title": "VisionQuest", "year": 2026},
            "episode": {"id": 2208, "seasonNumber": 1, "episodeNumber": 8, "airDate": "2026-11-01"}, "statusMessages": [{"title": VQ, "messages": list(messages)}]}


class Fakes(Harness):
    def api(self, records, fail_delete=False):
        self.deletes = []

        def call(method, path, query=None, body=None):
            if method == "DELETE":
                if fail_delete:
                    raise qw.ApiError("DELETE: 500")
                self.deletes.append((path, dict(query or {})))
                return None
            assert method == "GET" and path in ("/queue", "/health", "/indexerstatus", "/indexer"), (method, path)
            return {"totalRecords": len(records), "records": list(records)} if path == "/queue" else []
        return call

    def test_visionquest_exe_is_removed_blocklisted_once(self):
        api = self.api([vq_rec()])
        self.assertEqual(self.go({"sonarr": api}, T0), 0)
        self.assertEqual(self.deletes, [("/queue/766", DELETE_QUERY)])
        [e] = digest(self.dir)
        self.assertEqual((e["kind"], e["app"]), ("malware_removed", "sonarr"))
        self.assertIn("VisionQuest (2026) S01: VisionQuest S01E08", e["title"])
        self.assertEqual(self.sent, [])
        self.go({"sonarr": api}, T0 + 15 * MIN)
        self.assertEqual(len(self.deletes), 1)

    def test_dangerous_extensions_and_encrypted_messages_count(self):
        for msg in ("Caution: Found potentially dangerous file with extension: .lnk", "Caution: Found executable file with extension: '.bat'",
                    "Encrypted download detected", "Download 'x' contains executable file, marking as failed"):
            self.setUp()
            api = self.api([vq_rec(messages=(msg,), out="/data/torrents/tv/VQ")])
            self.go({"sonarr": api}, T0)
            self.assertEqual(len(self.deletes), 1, msg)

    def test_output_file_with_a_dangerous_extension_counts_without_a_message(self):
        for ext in (".exe", ".scr", ".lnk", ".msi", ".bat", ".cmd", ".pif", ".vbs", ".ps1"):
            self.setUp()
            api = self.api([vq_rec(messages=(), out=f"/data/torrents/tv/{VQ}{ext}", state="downloading", status="downloading")])
            self.go({"sonarr": api}, T0)
            self.assertEqual(len(self.deletes), 1, ext)

    def test_release_folder_named_like_a_domain_is_kept(self):
        api = self.api([vq_rec(messages=(), out="/data/torrents/tv/Some.Show.S01E01.1080p-Torrent9.com", state="downloading", status="downloading")])
        self.go({"sonarr": api}, T0)
        self.assertEqual(self.deletes, [])

    def test_completed_folder_with_only_dangerous_files_counts(self):
        root = self.dir / "data"
        d = root / "torrents" / "tv" / VQ
        d.mkdir(parents=True)
        (d / "VisionQuest.S01E08.exe").write_bytes(b"MZ")
        (d / "README.txt").write_text("password inside")
        api = self.api([vq_rec(messages=(), out=f"/data/torrents/tv/{VQ}", state="importBlocked")])
        qw.run({"sonarr": api}, self.state, [], self.send, qw.Config(data_root=str(root)), T0)
        self.assertEqual(len(self.deletes), 1)

    def test_folder_with_a_video_is_kept(self):
        root = self.dir / "data"
        d = root / "torrents" / "tv" / VQ
        d.mkdir(parents=True)
        (d / "VisionQuest.S01E08.mkv").write_bytes(b"x")
        (d / "setup.exe").write_bytes(b"MZ")
        api = self.api([vq_rec(messages=(), out=f"/data/torrents/tv/{VQ}", state="importBlocked")])
        qw.run({"sonarr": api}, self.state, [], self.send, qw.Config(data_root=str(root)), T0)
        self.assertEqual(self.deletes, [])

    def test_normal_items_and_failed_ones_are_left_alone(self):
        api = self.api([dict(EP), dict(DOWNLOADING), vq_rec(status="failed", state="failedPending")])
        self.go({"sonarr": api}, T0)
        self.assertEqual(self.deletes, [])

    def test_failed_removal_sends_one_telegram(self):
        api = self.api([vq_rec()], fail_delete=True)
        self.assertEqual(self.go({"sonarr": api}, T0), 1)
        self.go({"sonarr": api}, T0 + 15 * MIN)
        self.assertEqual(len(self.sent), 1)
        for part in ("VisionQuest", "could not be removed", "executable"):
            self.assertIn(part, self.sent[0])

PARTIAL_MSG = "One or more episodes expected in this release were not imported or missing from the release"
ALL10 = range(1, 11)
REPACK = {"name": "PartialPack"}


def own_partial_queue(extra=()):
    q = pack_queue(eps=ALL10, state="importPending")
    for r in q:
        r["statusMessages"] = [{"title": PARTIAL_MSG, "messages": []}] + list(extra)
    return q


class FakePartial(FakeSonarr):
    def __init__(self, cmd_status="completed", **kw):
        kw.setdefault("queue", pack_queue(eps=ALL10))
        kw.setdefault("history", pack_history(ALL10))
        kw.setdefault("proposals", pack_proposals())
        super().__init__(**kw)
        self.cmd_status, self.has_file, self.deletes = cmd_status, set(), []

    def __call__(self, method, path, query=None, body=None):
        if method == "DELETE":
            self.paths.append(path)
            self.deletes.append((path, dict(query or {})))
            self.queue = []
            return None
        if method == "GET" and path.startswith("/command/"):
            self.paths.append(path)
            return {"id": int(path.rsplit("/", 1)[1]), "name": "ManualImport", "status": self.cmd_status}
        if method == "GET" and path == "/episode":
            self.paths.append(path)
            if path in self.fail_on:
                raise qw.ApiError("GET /episode: down")
            ids = list(query["episodeIds"])
            return [{"id": i, "hasFile": i in self.has_file} for i in ids]
        return super().__call__(method, path, query, body)


DONE = lambda rec: None
KEEP_QUERY = {"removeFromClient": "true", "blocklist": "false", "skipRedownload": "true"}
PARTIAL_TITLE = "The Boys (2019) S04: imported 8 of 10, searching E09, E10"


class PartialPack(Harness):
    def go(self, apps, now, finished=DONE, **cfg):
        return qw.run(apps, self.state, [], self.send, qw.Config(**cfg), now, finished=finished)

    def test_finished_partial_pack_is_imported_then_the_missing_episodes_are_searched(self):
        api = FakePartial()
        self.assertEqual(self.go({"sonarr": api}, T0), 0)
        [cmd] = api.posts
        self.assertEqual((cmd["name"], sorted(f["episodeIds"][0] for f in cmd["files"])), ("ManualImport", list(range(1001, 1009))))
        self.assertEqual(api.deletes, [])
        api.has_file = set(range(1001, 1009))
        self.assertEqual(self.go({"sonarr": api}, T0 + 15 * MIN), 0)
        self.assertEqual(api.deletes, [("/queue/101", KEEP_QUERY)])
        self.assertEqual(api.posts[1], {"name": "EpisodeSearch", "episodeIds": [1009, 1010]})
        self.go({"sonarr": api}, T0 + 30 * MIN)
        self.go({"sonarr": api}, T0 + 60 * MIN)
        self.assertEqual(len(api.posts), 2)
        self.assertEqual(len(api.deletes), 1)
        self.assertEqual([(e["kind"], e["app"], e["title"]) for e in digest(self.dir)], [("partial_imported", "sonarr", PARTIAL_TITLE)])
        self.assertEqual(self.sent, [])

    def test_no_blocked_telegram_while_the_import_runs(self):
        api = FakePartial(cmd_status="started")
        self.go({"sonarr": api}, T0)
        self.go({"sonarr": api}, T0 + 31 * MIN)
        self.go({"sonarr": api}, T0 + 46 * MIN)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(api.posts), 1)

    def test_partial_pack_still_post_processing_is_left_alone(self):
        api = FakePartial()
        busy = lambda rec: "SABnzbd: Extracting"
        self.go({"sonarr": api}, T0, finished=busy)
        self.assertEqual(api.posts, [])
        self.go({"sonarr": api}, T0 + 31 * MIN, finished=busy)
        self.assertEqual(api.posts, [])
        [msg] = self.sent
        self.assertIn("Auto-import: not done", msg)
        self.assertIn("Extracting", msg)

    def test_partial_pack_without_a_client_check_is_left_alone(self):
        api = FakePartial()
        self.go({"sonarr": api}, T0, finished=None)
        self.assertEqual(api.posts, [])

    def test_one_unparseable_file_blocks_the_import(self):
        odd = dict(pack_proposals([1])[0], id=699, path=f"{OUT}/extras.mkv", episodes=[], rejections=[{"reason": "Unable to parse file", "type": "permanent"}])
        api = FakePartial(proposals=pack_proposals() + [odd])
        self.go({"sonarr": api}, T0)
        self.assertEqual(api.posts, [])

    def test_a_sample_file_blocks_the_import(self):
        props = pack_proposals()
        props[0] = dict(props[0], rejections=[{"reason": "Sample", "type": "permanent"}])
        api = FakePartial(proposals=props)
        self.go({"sonarr": api}, T0)
        self.assertEqual(api.posts, [])

    def test_season_mismatch_blocks_the_import(self):
        props = pack_proposals()
        props[7] = dict(props[7], seasonNumber=3, episodes=[{"id": 2008, "seasonNumber": 3, "episodeNumber": 8}])
        for api in (FakePartial(proposals=props), FakePartial(parse=parsed(season=3))):
            self.setUp()
            self.go({"sonarr": api}, T0)
            self.assertEqual(api.posts, [])

    def test_two_files_for_one_episode_block_the_import(self):
        api = FakePartial(proposals=pack_proposals(range(1, 8)) + pack_proposals([3]))
        self.go({"sonarr": api}, T0)
        self.assertEqual(api.posts, [])

    def test_fake_file_takes_the_existing_removal_path(self):
        api = FakePartial(queue=pack_queue(eps=ALL10, messages=(ID_MSG, EXE_MSG)))
        self.go({"sonarr": api}, T0)
        self.assertEqual(api.deletes, [("/queue/101", DELETE_QUERY)])
        self.assertEqual(api.posts, [])
        self.assertEqual([e["kind"] for e in digest(self.dir)], ["malware_removed"])

    def test_import_that_did_not_land_falls_back_to_the_blocked_alert(self):
        api = FakePartial(cmd_status="failed")
        self.go({"sonarr": api}, T0)
        self.go({"sonarr": api}, T0 + 15 * MIN)
        self.go({"sonarr": api}, T0 + 46 * MIN)
        self.assertEqual(api.deletes, [])
        self.assertEqual(len(api.posts), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Sonarr queue: blocked", self.sent[0])

    def test_api_errors_are_retried_then_given_up_after_2_hours(self):
        api = FakePartial()
        self.go({"sonarr": api}, T0)
        api.fail_on = {"/episode"}
        self.assertEqual(self.go({"sonarr": api}, T0 + 15 * MIN), 1)
        self.go({"sonarr": api}, T0 + 2 * HOUR + MIN)
        self.assertNotIn("partial:sonarr:boys4", json.loads(self.state.read_text()))
        self.assertEqual((api.deletes, len(api.posts)), ([], 1))

    def test_sonarr_own_partial_import_gets_the_missing_episodes_searched(self):
        hist = pack_history(ALL10) + pack_history(range(1, 9), event="downloadFolderImported")
        api = FakePartial(queue=own_partial_queue(), history=hist)
        api.has_file = set(range(1001, 1009))
        self.assertEqual(self.go({"sonarr": api}, T0), 0)
        self.assertEqual(api.deletes, [("/queue/101", KEEP_QUERY)])
        self.assertEqual(api.posts, [{"name": "EpisodeSearch", "episodeIds": [1009, 1010]}])
        self.assertEqual([e["title"] for e in digest(self.dir)], [PARTIAL_TITLE])
        self.assertEqual(self.sent, [])

    def test_sonarr_own_partial_with_a_file_it_rejected_stays_blocked(self):
        hist = pack_history(ALL10) + pack_history(range(1, 9), event="downloadFolderImported")
        api = FakePartial(queue=own_partial_queue([{"title": "The.Boys.S04E09.sample.mkv", "messages": ["Sample"]}]), history=hist)
        api.has_file = set(range(1001, 1009))
        self.go({"sonarr": api}, T0)
        self.assertEqual((api.deletes, api.posts), ([], []))

    def test_sonarr_own_partial_still_post_processing_is_left_alone(self):
        hist = pack_history(ALL10) + pack_history(range(1, 9), event="downloadFolderImported")
        api = FakePartial(queue=own_partial_queue(), history=hist)
        api.has_file = set(range(1001, 1009))
        self.go({"sonarr": api}, T0, finished=lambda rec: "qBittorrent: moving")
        self.assertEqual((api.deletes, api.posts), ([], []))

    def test_dry_run_names_the_partial_import(self):
        lines = qw.dry_run({"sonarr": FakePartial()}, qw.Config(), finished=DONE)
        self.assertIn(f"would import 8 of 10, then search E09, E10: The Boys (2019) S04: {PACK} (8 files)", lines)


class DownloadFinished(unittest.TestCase):
    def check(self, rec, torrents=(), history=(), queue=()):
        qbit = lambda path, data=None: list(torrents)
        sab = lambda params: {"history": {"slots": list(history)}} if params["mode"] == "history" else {"queue": {"slots": list(queue)}}
        return qw.download_finished(rec, qbit, sab)

    def test_qbittorrent_states(self):
        rec = {"downloadId": "ABCDEF", "protocol": "torrent"}
        for state in ("stoppedUP", "pausedUP", "uploading", "stalledUP", "queuedUP", "forcedUP"):
            self.assertIsNone(self.check(rec, [{"hash": "abcdef", "state": state, "progress": 1}]), state)
        for state in ("moving", "checkingUP", "checkingDL", "checkingResumeData", "downloading", "stoppedDL", "missingFiles", "error"):
            self.assertIn(state, self.check(rec, [{"hash": "abcdef", "state": state, "progress": 1}]))
        self.assertIsNotNone(self.check(rec, [{"hash": "abcdef", "state": "stoppedUP", "progress": 0.98}]))
        self.assertIsNotNone(self.check(rec, []))

    def test_sabnzbd_post_processing(self):
        rec = {"downloadId": "SABnzbd_nzo_x1", "protocol": "usenet"}
        self.assertIsNone(self.check(rec, history=[{"nzo_id": "SABnzbd_nzo_x1", "status": "Completed"}]))
        for status in ("Extracting", "Verifying", "Repairing", "Moving", "Running", "Failed"):
            self.assertIn(status, self.check(rec, history=[{"nzo_id": "SABnzbd_nzo_x1", "status": status}]))
        self.assertIsNotNone(self.check(rec, history=[{"nzo_id": "SABnzbd_nzo_x1", "status": "Completed"}], queue=[{"nzo_id": "SABnzbd_nzo_x1"}]))
        self.assertIsNotNone(self.check(rec))

    def test_unknown_protocol_or_client_error(self):
        self.assertIsNotNone(qw.download_finished({"downloadId": "x", "protocol": "unknown"}, None, None))

        def down(*a, **k):
            raise qw.ApiError("down")
        self.assertIn("down", qw.download_finished({"downloadId": "x", "protocol": "torrent"}, down, down))


class TestFilm(unittest.TestCase):
    """The permanent test film (public-domain-tests.md): missing = digest line only, never Telegram, never a failed run."""
    NOSF = {"id": 148, "tmdbId": 653, "title": "Nosferatu", "year": 1922, "hasFile": True, "tags": [63, 70]}
    TAGS = [{"id": 63, "label": "old-rare"}, {"id": 70, "label": "keep-test"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def radarr(self, movies, queue=(), tags=None, fail=False):
        base = fake(queue)

        def call(method, path, query=None, body=None):
            if path == "/movie":
                if fail:
                    raise qw.ApiError("GET /movie: down")
                return [m for m in movies if str(m["tmdbId"]) == str((query or {}).get("tmdbId"))]
            if path == "/tag":
                return self.TAGS if tags is None else tags
            return base(method, path, query, body)
        return call

    def go(self, radarr):
        return qw.run({"radarr": radarr}, self.dir / "queue-watchdog.json", [], lambda t: self.sent.append(t) or True, qw.Config(), T0, film=qw.test_film)

    def kinds(self):
        return [(e["kind"], e["app"], e["title"]) for e in digest(self.dir)]

    def test_present_with_file_and_tag_is_quiet(self):
        self.assertEqual(self.go(self.radarr([self.NOSF])), 0)
        self.assertEqual(self.kinds(), [])

    def test_not_in_radarr_is_a_digest_line_not_a_telegram(self):
        self.assertEqual(self.go(self.radarr([])), 0)
        self.assertEqual(self.kinds(), [("test_film_missing", "radarr", "Nosferatu (1922)")])
        self.assertEqual(self.sent, [])

    def test_no_file_and_nothing_queued_is_missing(self):
        self.go(self.radarr([dict(self.NOSF, hasFile=False)]))
        self.assertEqual(self.kinds(), [("test_film_missing", "radarr", "Nosferatu (1922)")])

    def test_no_file_but_downloading_is_quiet(self):
        self.go(self.radarr([dict(self.NOSF, hasFile=False)], queue=[dict(DOWNLOADING, movieId=148, status="paused")]))
        self.assertEqual(self.kinds(), [])

    def test_lost_keep_test_tag_is_its_own_line(self):
        self.go(self.radarr([dict(self.NOSF, tags=[63])]))
        self.assertEqual(self.kinds(), [("test_film_untagged", "radarr", "Nosferatu (1922)")])

    def test_one_line_per_day_not_per_run(self):
        r = self.radarr([])
        self.go(r)
        qw.run({"radarr": r}, self.dir / "queue-watchdog.json", [], lambda t: True, qw.Config(), T0 + 15 * MIN, film=qw.test_film)
        self.assertEqual(len(digest(self.dir)), 1)

    def test_api_error_exits_1_without_a_missing_line(self):
        self.assertEqual(self.go(self.radarr([], fail=True)), 1)
        self.assertEqual(self.kinds(), [])

    def test_lands_in_the_prom_file_for_the_morning_digest(self):
        self.go(self.radarr([]))
        self.assertIn('beefy_watchdog_event_timestamp_seconds{kind="test_film_missing",app="radarr",title="Nosferatu (1922)"} 1800000000', (self.dir / "watchdog-digest.prom").read_text())

    def test_main_runs_the_check(self):
        self.assertIn("film=test_film", (ROOT / "scripts" / "queue-watchdog.py").read_text())


if __name__ == "__main__":
    unittest.main()
