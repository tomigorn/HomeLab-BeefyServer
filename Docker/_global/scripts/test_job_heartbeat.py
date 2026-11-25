import json
import os
import tempfile
import unittest

import job_heartbeat as hb

NOW = 1_764_000_000.0
NOW_MONO = 100_000.0
BOOT = NOW - NOW_MONO

TIMERS = """\
Unit=nightly.service
TimersCalendar={ OnCalendar=*-*-* 04:05:00 ; next_elapse=Thu 2026-11-26 04:05:00 CET }
RandomizedDelayUSec=0
Id=nightly.timer
ActiveState=active

Unit=weekdays.service
TimersCalendar={ OnCalendar=Mon..Fri *-*-* 04:05:00 Europe/Zurich ; next_elapse=Thu 2026-11-26 04:05:00 CET }
RandomizedDelayUSec=1h
Id=weekdays.timer
ActiveState=active

Unit=minutely.service
TimersMonotonic={ OnUnitActiveUSec=1min ; next_elapse=10h 3min }
TimersMonotonic={ OnBootUSec=1min ; next_elapse=1min }
RandomizedDelayUSec=0
Id=minutely.timer
ActiveState=active

Unit=bootonly.service
TimersMonotonic={ OnBootUSec=5min ; next_elapse=5min }
RandomizedDelayUSec=0
Id=bootonly.timer
ActiveState=active

Unit=neverran.service
TimersCalendar={ OnCalendar=*-*-* 04:05:00 ; next_elapse=Thu 2026-11-26 04:05:00 CET }
RandomizedDelayUSec=0
Id=neverran.timer
ActiveState=active

Unit=broken.service
TimersCalendar={ OnCalendar=*-*-* 04:05:00 ; next_elapse=Thu 2026-11-26 04:05:00 CET }
RandomizedDelayUSec=0
Id=broken.timer
ActiveState=active
"""

SERVICES = {
    "nightly.service": dict(Result="success", ActiveState="inactive", ExecMainCode="1", ExecMainStatus="0", ExecMainExitTimestampMonotonic=str(int((NOW_MONO - 3600) * 1e6))),
    "weekdays.service": dict(Result="success", ActiveState="inactive", ExecMainCode="1", ExecMainStatus="0", ExecMainExitTimestampMonotonic=str(int((NOW_MONO - 7200) * 1e6))),
    "minutely.service": dict(Result="success", ActiveState="activating", ExecMainCode="0", ExecMainStatus="0", ExecMainExitTimestampMonotonic="0"),
    "bootonly.service": dict(Result="success", ActiveState="inactive", ExecMainCode="1", ExecMainStatus="0", ExecMainExitTimestampMonotonic=str(int((NOW_MONO - 60) * 1e6))),
    "neverran.service": dict(Result="success", ActiveState="inactive", ExecMainCode="0", ExecMainStatus="0", ExecMainExitTimestampMonotonic="0"),
    "broken.service": dict(Result="exit-code", ActiveState="failed", ExecMainCode="1", ExecMainStatus="1", ExecMainExitTimestampMonotonic=str(int((NOW_MONO - 300) * 1e6))),
}

ANALYZE = {
    "*-*-* 04:05:00": """\
Normalized form: *-*-* 04:05:00
    Next elapse: Thu 2026-11-26 04:05:00 CET
       (in UTC): Thu 2026-11-26 03:05:00 UTC
       From now: 19h left
   Iteration #2: Fri 2026-11-27 04:05:00 CET
       (in UTC): Fri 2026-11-27 03:05:00 UTC
       From now: 1 day 19h left
   Iteration #3: Sat 2026-11-28 04:05:00 CET
""",
    "Mon..Fri *-*-* 04:05:00 Europe/Zurich": """\
Normalized form: Mon..Fri *-*-* 04:05:00 Europe/Zurich
    Next elapse: Thu 2026-11-26 04:05:00 CET
   Iteration #2: Fri 2026-11-27 04:05:00 CET
   Iteration #3: Mon 2026-11-30 04:05:00 CET
""",
}

FAILED = """\
  broken.service loaded failed failed Broken job
● other.service loaded failed failed Something else
"""


def fake_runner(failed=FAILED):
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[0] == "systemd-analyze":
            return ANALYZE[argv[-1]]
        if "list-units" in argv:
            return failed
        if "*.timer" in argv:
            return TIMERS
        units = [a for a in argv if a.endswith(".service")]
        out = []
        for u in units:
            props = dict(Id=u, **SERVICES[u])
            out.append("\n".join(f"{k}={v}" for k, v in props.items()))
        return "\n\n".join(out) + "\n"

    run.calls = calls
    return run


def metrics(text):
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            out[name] = float(value)
    return out


class Parse(unittest.TestCase):
    def test_show_blocks_with_repeated_keys(self):
        blocks = hb.parse_show(TIMERS)
        self.assertEqual(len(blocks), 6)
        self.assertEqual(blocks[2]["TimersMonotonic"], ["{ OnUnitActiveUSec=1min ; next_elapse=10h 3min }", "{ OnBootUSec=1min ; next_elapse=1min }"])
        self.assertEqual(blocks[0]["Id"], "nightly.timer")

    def test_durations(self):
        self.assertEqual(hb.parse_duration("1min"), 60)
        self.assertEqual(hb.parse_duration("1h 30min"), 5400)
        self.assertEqual(hb.parse_duration("1d 2h"), 93600)
        self.assertEqual(hb.parse_duration("5.5s"), 5.5)
        self.assertEqual(hb.parse_duration("0"), 0)
        self.assertEqual(hb.parse_duration("1w"), 604800)

    def test_calendar_gap_is_the_longest_between_elapses(self):
        self.assertEqual(hb.gap_from_analyze(ANALYZE["Mon..Fri *-*-* 04:05:00 Europe/Zurich"]), 3 * 86400)
        self.assertEqual(hb.gap_from_analyze(ANALYZE["*-*-* 04:05:00"]), 86400)

    def test_max_age_adds_grace_and_random_delay(self):
        self.assertEqual(hb.max_age(60, 0), 60 + 900)
        self.assertEqual(hb.max_age(86400, 0), 86400 + 21600)
        self.assertEqual(hb.max_age(86400, 3600), 86400 + 21600 + 3600)


class Collect(unittest.TestCase):
    def build(self, state=None, failed=FAILED):
        runner = fake_runner(failed)
        text, new = hb.build("fastpi", [("system", ["systemctl"])], runner, state or {}, NOW, NOW_MONO, BOOT)
        return metrics(text), new, runner, text

    def key(self, name, unit, scope="system"):
        return f'{name}{{host="fastpi",scope="{scope}",unit="{unit}"}}'

    def test_success_timestamp_and_result(self):
        m, _, _, _ = self.build()
        self.assertAlmostEqual(m[self.key("systemd_job_last_success_timestamp_seconds", "nightly.service")], NOW - 3600, delta=1)
        self.assertEqual(m[self.key("systemd_job_last_result", "nightly.service")], 0)

    def test_max_age_per_timer(self):
        m, _, _, _ = self.build()
        k = "systemd_job_max_age_seconds"
        self.assertEqual(m[self.key(k, "nightly.service")], 86400 + 21600)
        self.assertEqual(m[self.key(k, "weekdays.service")], 3 * 86400 + 3 * 86400 // 4 + 3600)
        self.assertEqual(m[self.key(k, "minutely.service")], 60 + 900)

    def test_boot_only_timer_has_no_max_age(self):
        m, _, _, _ = self.build()
        self.assertNotIn(self.key("systemd_job_max_age_seconds", "bootonly.service"), m)
        self.assertIn(self.key("systemd_job_last_success_timestamp_seconds", "bootonly.service"), m)

    def test_failed_service_reports_result_1_and_no_new_success(self):
        m, _, _, _ = self.build()
        self.assertEqual(m[self.key("systemd_job_last_result", "broken.service")], 1)
        self.assertNotIn(self.key("systemd_job_last_success_timestamp_seconds", "broken.service"), m)

    def test_failed_service_keeps_the_stored_success(self):
        state = {"system": {"broken.service": {"last_success": NOW - 90000, "first_seen": NOW - 99999}}}
        m, _, _, _ = self.build(state)
        self.assertEqual(m[self.key("systemd_job_last_success_timestamp_seconds", "broken.service")], NOW - 90000)

    def test_running_service_keeps_stored_success_and_result_unknown_is_not_failure(self):
        state = {"system": {"minutely.service": {"last_success": NOW - 50, "first_seen": NOW - 999}}}
        m, _, _, _ = self.build(state)
        self.assertEqual(m[self.key("systemd_job_last_success_timestamp_seconds", "minutely.service")], NOW - 50)
        self.assertEqual(m[self.key("systemd_job_last_result", "minutely.service")], 0)

    def test_never_ran_has_first_seen_but_no_success(self):
        m, new, _, _ = self.build()
        self.assertNotIn(self.key("systemd_job_last_success_timestamp_seconds", "neverran.service"), m)
        self.assertEqual(m[self.key("systemd_job_first_seen_timestamp_seconds", "neverran.service")], NOW)
        self.assertEqual(new["system"]["neverran.service"]["first_seen"], NOW)

    def test_first_seen_is_kept_across_runs(self):
        state = {"system": {"neverran.service": {"first_seen": NOW - 5000}}}
        m, _, _, _ = self.build(state)
        self.assertEqual(m[self.key("systemd_job_first_seen_timestamp_seconds", "neverran.service")], NOW - 5000)

    def test_state_remembers_success_after_the_service_state_is_lost(self):
        _, new, _, _ = self.build()
        reboot_services = dict(SERVICES)
        reboot_services["nightly.service"] = dict(Result="success", ActiveState="inactive", ExecMainCode="0", ExecMainStatus="0", ExecMainExitTimestampMonotonic="0")
        old = dict(SERVICES)
        SERVICES.update(reboot_services)
        try:
            m, _, _, _ = self.build(new)
        finally:
            SERVICES.update(old)
        self.assertAlmostEqual(m[self.key("systemd_job_last_success_timestamp_seconds", "nightly.service")], NOW - 3600, delta=1)

    def test_reset_failed_does_not_turn_a_failed_run_into_a_success(self):
        old = SERVICES["broken.service"]
        SERVICES["broken.service"] = dict(old, Result="success", ActiveState="inactive")
        try:
            m, _, _, _ = self.build()
        finally:
            SERVICES["broken.service"] = old
        self.assertNotIn(self.key("systemd_job_last_success_timestamp_seconds", "broken.service"), m)
        self.assertEqual(m[self.key("systemd_job_last_result", "broken.service")], 1)

    def test_failed_units_listed_with_scope(self):
        m, _, _, _ = self.build()
        self.assertEqual(m[self.key("systemd_unit_failed", "broken.service")], 1)
        self.assertEqual(m[self.key("systemd_unit_failed", "other.service")], 1)

    def test_host_series(self):
        m, _, _, _ = self.build()
        self.assertEqual(m['systemd_job_host_boot_time_seconds{host="fastpi"}'], BOOT)
        self.assertEqual(m['systemd_job_updated_timestamp_seconds{host="fastpi"}'], NOW)

    def test_calendar_gaps_are_cached(self):
        _, new, runner, _ = self.build()
        n = sum(1 for c in runner.calls if c[0] == "systemd-analyze")
        self.assertEqual(n, 2)
        runner2 = fake_runner()
        hb.build("fastpi", [("system", ["systemctl"])], runner2, new, NOW, NOW_MONO, BOOT)
        self.assertEqual(sum(1 for c in runner2.calls if c[0] == "systemd-analyze"), 0)

    def test_no_meta_drops_help_and_type_lines(self):
        text, _ = hb.build("beefy", [("system", ["systemctl"])], fake_runner(), {}, NOW, NOW_MONO, BOOT, meta=False)
        self.assertNotIn("# HELP", text)
        self.assertNotIn("# TYPE", text)
        self.assertIn('systemd_job_last_result{host="beefy"', text)

    def test_output_has_no_duplicate_series(self):
        _, _, _, text = self.build()
        series = [l.rsplit(" ", 1)[0] for l in text.splitlines() if l and not l.startswith("#")]
        self.assertEqual(len(series), len(set(series)))


class Files(unittest.TestCase):
    def test_state_roundtrip_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            self.assertEqual(hb.load_state(p), {})
            hb.write_atomic(p, json.dumps({"a": 1}))
            self.assertEqual(hb.load_state(p), {"a": 1})
            with open(p, "w") as f:
                f.write("{broken")
            self.assertEqual(hb.load_state(p), {})


if __name__ == "__main__":
    unittest.main()
