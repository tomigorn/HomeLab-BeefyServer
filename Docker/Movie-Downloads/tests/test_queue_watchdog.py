#!/usr/bin/env python3
"""scripts/queue-watchdog.py: which queue items alert and when, the one-alert-per-condition state, guard event files and sending, against fake Radarr/Sonarr and a fake sender.
Usage: python3 tests/test_queue_watchdog.py"""
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

BLOCKED = {"id": 5, "downloadId": "abc", "title": "A.Separation.2026.2160p.WEB-DL-HDSWEB", "status": "completed", "trackedDownloadStatus": "warning",
           "trackedDownloadState": "importBlocked", "sizeleft": 0, "movie": {"title": "A Separation", "year": 2011},
           "statusMessages": [{"title": "A.Separation.2026", "messages": ["Found matching movie via grab history, but release was matched to movie by ID. Manual Import required."]}]}
DOWNLOADING = {"id": 6, "downloadId": "dl1", "title": "Some.Film.2020.1080p-X", "status": "downloading", "trackedDownloadStatus": "ok",
               "trackedDownloadState": "downloading", "sizeleft": 1000, "movie": {"title": "Some Film", "year": 2020}}
EP = {"id": 7, "downloadId": "ep1", "title": "Show.S02E03.1080p-X", "status": "failed", "trackedDownloadStatus": "error", "trackedDownloadState": "downloading",
      "sizeleft": 5, "series": {"title": "Show", "year": 2019}, "episode": {"seasonNumber": 2, "episodeNumber": 3}, "statusMessages": []}


def fake(records=(), fail=False):
    def call(method, path, query=None, body=None):
        if fail:
            raise qw.ApiError("down")
        assert method == "GET" and path == "/queue", (method, path)
        return {"totalRecords": len(records), "records": list(records)}
    return call


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

    def test_guard_events_become_messages_and_are_deleted(self):
        self.event("1.json")
        self.event("2.json", app="sonarr", action="marked-failed")
        self.event("3.json", action="manual")
        self.assertEqual(self.go({"radarr": fake()}, T0), 0)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(list(self.events.glob("*.json")), [])
        first = self.sent[0]
        for part in ("Wrong-film guard", "Radarr", "A Separation (2011)", "A.Separation.2026.2160p-X", "2026", "removed"):
            self.assertIn(part, first)
        self.assertIn("marked failed", self.sent[1])
        self.assertIn("Sonarr", self.sent[1])
        self.assertIn("check by hand", self.sent[2])
        self.assertTrue(first.startswith("🟠 <b>Wrong-film guard: Radarr</b>\n\n• "))
        self.assertIn("<blockquote expandable>Radarr → Activity → Queue</blockquote>", self.sent[2])

    def test_dub_only_event_message(self):
        self.event(reason="dub-only", release="X.2011.MULTi.1080p-GRP", languages=["German", "French"], original="Persian", title="A Separation", releaseYear=2011)
        self.event("2.json", reason="dub-only", action="manual", release="Y-GRP", languages=["German", "French"], original="Persian")
        self.assertEqual(self.go({"radarr": fake()}, T0), 0)
        first = self.sent[0]
        self.assertTrue(first.startswith("🟠 <b>Dub-only release removed: Radarr</b>\n\n• "))
        for part in ("A Separation (2011)", "<code>X.2011.MULTi.1080p-GRP</code>", "German, French", "Persian", "removed from the queue"):
            self.assertIn(part, first)
        self.assertNotIn("release year", first)
        self.assertTrue(self.sent[1].startswith("🔴 <b>Dub-only release: Radarr</b>"))
        self.assertIn("check by hand", self.sent[1])

    def test_guard_event_stays_when_the_send_fails(self):
        self.event()
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


if __name__ == "__main__":
    unittest.main()
