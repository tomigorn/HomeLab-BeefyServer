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


def fake(records=(), fail=False, health=(), status=None, indexers=(), health_fail=False):
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
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1].startswith("✅ <b>Sonarr: indexers recovered</b>"))
        self.assertEqual(self.go({"sonarr": fake()}, T0 + 60 * MIN), 0)
        self.assertEqual(len(self.sent), 2)

    def test_alert_again_after_it_cleared_and_came_back(self):
        bad = {"radarr": fake(health=[hc([NZB])])}
        self.go(bad, T0)
        self.go({"radarr": fake()}, T0 + 15 * MIN)
        self.go(bad, T0 + 30 * MIN)
        self.assertEqual(len(self.sent), 3)

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
        self.assertEqual(len(self.sent), 2)
        self.assertIn("recovered", self.sent[1])
        self.go({"radarr": fake(health=[hc([TPB])])}, T0 + 30 * MIN)
        self.assertEqual(len(self.sent), 2)

    def test_failed_send_is_retried_next_run(self):
        bad = {"radarr": fake(health=[hc([NZB])])}
        self.fail_send = True
        self.assertEqual(self.go(bad, T0), 1)
        self.fail_send = False
        self.assertEqual(self.go(bad, T0 + 15 * MIN), 0)
        self.assertEqual(len(self.sent), 1)

    def test_failed_recovered_send_is_retried(self):
        self.go({"radarr": fake(health=[hc([NZB])])}, T0)
        self.fail_send = True
        self.assertEqual(self.go({"radarr": fake()}, T0 + 15 * MIN), 1)
        self.fail_send = False
        self.assertEqual(self.go({"radarr": fake()}, T0 + 30 * MIN), 0)
        self.assertEqual(len(self.sent), 2)

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


if __name__ == "__main__":
    unittest.main()
