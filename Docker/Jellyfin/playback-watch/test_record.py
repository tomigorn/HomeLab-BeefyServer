import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import record as rec  # noqa: E402


def incident(kind="stall", seconds=6.0, client="Jellyfin Web", item="Show - Pilot"):
    return {
        "kind": kind, "seconds": seconds, "session_id": "s1", "user": "u", "client": client,
        "device": "Firefox", "item": item, "play_method": "Transcode",
        "transcode": {"reasons": ["VideoCodecNotSupported"], "hw_accel": None},
    }


CTX = {"load": [1.0, 0.5, 0.2], "cpu_percent": 12.5, "disk_busy_percent": 3.0}


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jsonl_rotation(self):
        path = self.dir / "incidents.jsonl"
        path.write_text("x" * (5 * 1024 * 1024 + 1))
        (self.dir / "incidents.jsonl.1").write_text("one")
        (self.dir / "incidents.jsonl.2").write_text("two")
        (self.dir / "incidents.jsonl.3").write_text("three")
        rec.append_incident(path, incident(), CTX, clock=lambda: 1000.0)
        self.assertEqual((self.dir / "incidents.jsonl.1").read_text()[:3], "xxx")
        self.assertEqual((self.dir / "incidents.jsonl.2").read_text(), "one")
        self.assertEqual((self.dir / "incidents.jsonl.3").read_text(), "two")
        self.assertFalse((self.dir / "incidents.jsonl.4").exists())
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["kind"], "stall")
        self.assertEqual(row["time"], 1000.0)
        self.assertEqual(row["context"]["cpu_percent"], 12.5)
        self.assertEqual(lines[0], json.dumps(row, sort_keys=True))

    def test_summary_line(self):
        path = self.dir / "incidents.jsonl"
        rec.append_summary(path, {"item": "x", "stalls": 2}, clock=lambda: 5.0)
        row = json.loads(path.read_text())
        self.assertEqual(row["kind"], "summary")
        self.assertEqual(row["stalls"], 2)

    def test_prom_format_and_labels_escaped(self):
        c = rec.Counters(self.dir / "state.json")
        c.inc_incident("stall", 'We"b\\ \nX', 6.5, 1234.0)
        c.inc_autofix("downloads_slow")
        out = self.dir / "playback.prom"
        rec.write_prom(out, c, {"Transcode": 1}, {"downloads_slow": True, "background_low": False}, 99.0)
        text = out.read_text()
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o644)
        for name in ("jellyfin_playback_active", "jellyfin_playback_incidents_total",
                     "jellyfin_playback_stall_seconds_total", "jellyfin_playback_autofix_total",
                     "jellyfin_playback_autofix_active", "jellyfin_playback_last_incident_timestamp_seconds",
                     "jellyfin_playback_watch_last_poll_timestamp_seconds"):
            self.assertEqual(text.count(f"# HELP {name} "), 1, name)
            self.assertEqual(text.count(f"# TYPE {name} "), 1, name)
        esc = 'We\\"b\\\\ \\nX'
        self.assertIn(f'jellyfin_playback_incidents_total{{kind="stall",client="{esc}"}} 1', text)
        self.assertIn(f'jellyfin_playback_incidents_total{{kind="silent",client="{esc}"}} 0', text)
        self.assertIn(f'jellyfin_playback_incidents_total{{kind="slow_transcode",client="{esc}"}} 0', text)
        self.assertIn(f'jellyfin_playback_stall_seconds_total{{client="{esc}"}} 6.5', text)
        self.assertIn('jellyfin_playback_active{method="Transcode"} 1', text)
        self.assertIn('jellyfin_playback_autofix_total{action="downloads_slow"} 1', text)
        self.assertIn('jellyfin_playback_autofix_total{action="background_low"} 0', text)
        self.assertIn('jellyfin_playback_autofix_active{action="downloads_slow"} 1', text)
        self.assertIn('jellyfin_playback_autofix_active{action="background_low"} 0', text)
        self.assertIn("jellyfin_playback_last_incident_timestamp_seconds 1234", text)
        self.assertIn("jellyfin_playback_watch_last_poll_timestamp_seconds 99", text)
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_prom_empty_state_has_stable_series(self):
        c = rec.Counters(self.dir / "state.json")
        out = self.dir / "playback.prom"
        rec.write_prom(out, c, {}, {}, 1.0)
        text = out.read_text()
        self.assertIn('jellyfin_playback_autofix_total{action="downloads_slow"} 0', text)
        self.assertIn('jellyfin_playback_autofix_active{action="background_low"} 0', text)
        self.assertIn("jellyfin_playback_last_incident_timestamp_seconds 0", text)

    def test_counters_survive_reload(self):
        p = self.dir / "state.json"
        c = rec.Counters(p)
        c.inc_incident("stall", "Web", 4.0, 10.0)
        c.inc_incident("stall", "Web", 2.0, 20.0)
        c.inc_autofix("background_low")
        c2 = rec.Counters(p)
        self.assertEqual(c2.incidents[("stall", "Web")], 2)
        self.assertEqual(c2.stall_seconds["Web"], 6.0)
        self.assertEqual(c2.autofix["background_low"], 1)
        self.assertEqual(c2.last_incident, 20.0)

    def test_private_files_are_0600(self):
        log = self.dir / "incidents.jsonl"
        rec.append_incident(log, incident(), CTX, clock=lambda: 1.0)
        rec.append_summary(log, {"session_id": "s1"}, clock=lambda: 2.0)
        c = rec.Counters(self.dir / "state.json")
        c.inc_autofix("downloads_slow")
        self.assertEqual(stat.S_IMODE(os.stat(log).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "state.json").st_mode), 0o600)

    def test_counters_corrupt_file_starts_empty(self):
        p = self.dir / "state.json"
        p.write_text("{nope")
        self.assertEqual(rec.Counters(p).autofix["downloads_slow"], 0)

    def test_report_filters_since(self):
        path = self.dir / "incidents.jsonl"
        now = 1_000_000.0
        rec.append_incident(path, incident(item="Old"), CTX, clock=lambda: now - 7200)
        rec.append_incident(path, incident(kind="silent", seconds=50, item="New"), CTX, clock=lambda: now - 600)
        rec.append_incident(path, incident(item="Newest"), CTX, clock=lambda: now - 60)
        rec.append_summary(path, {"item": "S"}, clock=lambda: now - 30)
        text = rec.report(path, 3600, now=lambda: now)
        self.assertNotIn("Old", text)
        self.assertIn("New", text)
        self.assertLess(text.index("New"), text.index("Newest"))
        self.assertIn("VideoCodecNotSupported", text)
        self.assertIn("silent", text)
        self.assertIn("2 incidents", text)
        self.assertIn("stall 1", text)

    def test_report_empty(self):
        self.assertIn("0 incidents", rec.report(self.dir / "none.jsonl", 3600, now=lambda: 1.0))

    def test_context_tolerates_failing_commands(self):
        def boom(*a, **k):
            raise OSError("no")

        def bad_read(self_or_path, *a, **k):
            raise OSError("no")

        ctx = rec.context(run=boom, read=bad_read, sleep=lambda s: None)
        self.assertIsNone(ctx["load"])
        self.assertIsNone(ctx["cpu_percent"])
        self.assertIsNone(ctx["disk_busy_percent"])
        self.assertIsNone(ctx["units"])
        self.assertIsNone(ctx["containers"])
        self.assertIsNone(ctx["qbit_speed"])

    def test_context_happy_path(self):
        reads = iter([
            "cpu  100 0 100 800 0 0 0 0 0 0\n",
            "cpu  150 0 150 900 0 0 0 0 0 0\n",
        ])
        disk1 = "   8  0 sda 1 0 1 1 1 0 1 1 0 1000 1 0 0 0 0 0 0\n   8 16 sdb 1 0 1 1 1 0 1 1 0 0 1 0 0 0 0 0 0\n"
        disk2 = "   8  0 sda 1 0 1 1 1 0 1 1 0 1250 1 0 0 0 0 0 0\n   8 16 sdb 1 0 1 1 1 0 1 1 0 0 1 0 0 0 0 0 0\n"
        diskreads = iter([disk1, disk2])

        def read(p, *a, **k):
            p = str(p)
            if p.endswith("loadavg"):
                return "1.50 1.00 0.50 1/100 123\n"
            if p.endswith("stat"):
                return next(reads)
            if p.endswith("diskstats"):
                return next(diskreads)
            raise OSError

        class R:
            def __init__(self, out, rc=0):
                self.stdout, self.returncode = out, rc

        def run(cmd, **k):
            if cmd[0] == "systemctl":
                return R("active\n" if cmd[-1] == "clamav-scan.service" else "inactive\n", 0 if cmd[-1] == "clamav-scan.service" else 3)
            return R("jellyfin\nclamav\nother\n")

        ctx = rec.context(run=run, read=read, sleep=lambda s: None, qbit_speed=100, sab_speed=5)
        self.assertEqual(ctx["load"], [1.5, 1.0, 0.5])
        self.assertAlmostEqual(ctx["cpu_percent"], 50.0)
        self.assertAlmostEqual(ctx["disk_busy_percent"], 50.0)
        self.assertEqual(ctx["units"], ["clamav-scan.service"])
        self.assertEqual(ctx["containers"], ["clamav"])
        self.assertEqual((ctx["qbit_speed"], ctx["sab_speed"]), (100, 5))


if __name__ == "__main__":
    unittest.main()
