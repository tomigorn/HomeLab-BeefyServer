import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from importlib.machinery import SourceFileLoader

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_loader = SourceFileLoader("playback_watch", str(HERE / "playback-watch"))
_spec = importlib.util.spec_from_loader("playback_watch", _loader)
pw = importlib.util.module_from_spec(_spec)
_loader.exec_module(pw)

OWN = "1: lo    inet 127.0.0.1/8 scope host lo\n2: eno1    inet 192.168.1.102/24 brd 192.168.1.255 scope global eno1\n5: br-1    inet 172.28.11.1/24 scope global br-1\n"
KEY = "secret-key-123"


def session(sid="s1", pos=0, method="DirectPlay", item="Pilot"):
    return {"Id": sid, "UserName": "u", "Client": "Web", "DeviceName": "Firefox",
            "NowPlayingItem": {"Name": item, "RunTimeTicks": 36000 * 10_000_000},
            "PlayState": {"PositionTicks": pos * 10_000_000, "IsPaused": False, "PlayMethod": method}}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Runner:
    def __init__(self, ips):
        self.ips = list(ips)
        self.inspects = 0

    def __call__(self, cmd, **kw):
        if cmd[:2] == ["ip", "-o"]:
            return subprocess.CompletedProcess(cmd, 0, OWN, "")
        if "inspect" in cmd and cmd[-1] == "jellyfin":
            self.inspects += 1
            ip = self.ips[min(self.inspects - 1, len(self.ips) - 1)]
            return subprocess.CompletedProcess(cmd, 0 if ip is not None else 1, f" {ip} \n" if ip else "", "")
        raise AssertionError(f"unexpected command {cmd}")


class Http:
    def __init__(self, replies):
        self.replies = list(replies)
        self.urls = []
        self.headers = []

    def __call__(self, url, headers):
        self.urls.append(url)
        self.headers.append(headers)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return json.dumps(r).encode()


class Fix:
    def __init__(self):
        self.applied = False
        self.calls = []
        self.fail_reverts = 0
        self.pending = False

    def active(self):
        return {"downloads_slow": self.applied, "background_low": False}

    def apply(self, now):
        self.calls.append("apply")
        was, self.applied = self.applied, True
        if was:
            return []
        getattr(self, "on_action", lambda a: None)("downloads_slow")
        return ["downloads_slow"]

    def revert(self):
        self.calls.append("revert")
        if self.fail_reverts:
            self.fail_reverts -= 1
            return []
        was, self.applied = self.applied, False
        return ["downloads_slow"] if was else []

    def restore_from_state(self):
        self.calls.append("restore")
        return []

    def complete(self):
        return self.applied and not self.pending

    def download_speeds(self):
        return 100, 200


class FakeTracker:
    def __init__(self):
        self.incidents = []
        self.is_playing = 0
        self.episode = False
        self.end = None

    def update(self, sessions, now):
        out, self.incidents = self.incidents, []
        return out

    def playing(self):
        return self.is_playing

    def active_by_method(self):
        return {"DirectPlay": self.is_playing} if self.is_playing else {}

    def last_playing_end(self):
        return self.end

    def episode_active(self, now):
        return self.episode


class Stop:
    def __init__(self, clock, polls):
        self.clock, self.left = clock, polls
        self.waits = []

    def is_set(self):
        return self.left <= 0

    def wait(self, timeout):
        self.waits.append(timeout)
        self.left -= 1
        self.clock.t += timeout


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        (self.dir / "api-key").write_text(KEY + "\n")
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def watcher(self, run, http, clock=None, fix=None, tracker=None, context=None):
        clock = clock or Clock()
        return pw.Watcher(run=run, http=http, clock=clock, key_file=self.dir / "api-key", fix=fix or Fix(),
                          state_dir=self.dir / "state", log=self.logs.append, tracker=tracker,
                          context=context or (lambda **kw: {"load": None, **kw}))

    def test_loop_writes_prom_each_poll(self):
        clock = Clock()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[session()]]), clock=clock)
        prom = self.dir / "state" / "playback.prom"
        seen = []
        real = pw.record.write_prom

        def spy(path, *a):
            real(path, *a)
            seen.append(prom.read_text())
        pw.record.write_prom = spy
        try:
            w.run(Stop(clock, 3))
        finally:
            pw.record.write_prom = real
        self.assertGreaterEqual(len(seen), 3)
        polls = [line for text in seen for line in text.splitlines() if line.startswith("jellyfin_playback_watch_last_poll_timestamp_seconds ")]
        self.assertEqual(len(set(polls[:3])), 3)
        self.assertIn('jellyfin_playback_active{method="DirectPlay"} 1', seen[0])

    def test_poll_waits_5s_between_polls(self):
        clock = Clock()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock)
        stop = Stop(clock, 2)
        w.run(stop)
        self.assertEqual(stop.waits[0], 5.0)

    def test_bridge_ip_reresolved_after_failure(self):
        run = Runner(["172.28.11.3", "172.28.11.9"])
        http = Http([OSError("connection refused"), [], []])
        w = self.watcher(run, http)
        w.poll()
        w.poll()
        w.poll()
        self.assertEqual(run.inspects, 2)
        self.assertTrue(http.urls[0].startswith("http://172.28.11.3:8096/Sessions?ActiveWithinSeconds=60"))
        self.assertTrue(http.urls[1].startswith("http://172.28.11.9:8096/"))
        self.assertTrue(http.urls[2].startswith("http://172.28.11.9:8096/"))
        self.assertEqual(http.headers[0], {"Authorization": f'MediaBrowser Token="{KEY}"'})

    def test_never_uses_localhost(self):
        for bad in ("127.0.0.1", "localhost", "::1", "192.168.1.102", "172.28.11.1", "0.0.0.0", None):
            http = Http([[]])
            w = self.watcher(Runner([bad]), http)
            w.poll()
            self.assertEqual(http.urls, [], bad)
        self.assertTrue(any("refus" in m for m in self.logs))

    def test_key_never_logged(self):
        w = self.watcher(Runner(["172.28.11.3"]), Http([pw.urllib.error.HTTPError("http://x/Sessions", 401, "Unauthorized", {}, None)]))
        w.poll()
        w.poll()
        self.assertTrue(self.logs)
        self.assertFalse(any(KEY in m for m in self.logs))

    def test_revert_10min_after_last_playback(self):
        clock, fix, tr = Clock(), Fix(), FakeTracker()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock, fix=fix, tracker=tr)
        tr.is_playing, tr.episode = 1, True
        w.poll()
        self.assertEqual(fix.calls, ["apply"])
        tr.episode = False
        clock.t += 5
        w.poll()
        self.assertEqual(fix.calls, ["apply"])
        tr.is_playing, tr.end = 0, clock.t
        for _ in range(119):
            clock.t += 5
            w.poll()
        self.assertNotIn("revert", fix.calls)
        clock.t += 5
        fix.fail_reverts = 1
        w.poll()
        self.assertEqual(fix.calls.count("revert"), 1)
        self.assertTrue(fix.applied)
        tr.is_playing = 1
        clock.t += 5
        w.poll()
        self.assertEqual(fix.calls.count("revert"), 2)
        self.assertFalse(fix.applied)
        clock.t += 5
        w.poll()
        self.assertEqual(fix.calls.count("revert"), 2)

    def test_apply_throttled_to_60s_once_complete(self):
        clock, fix, tr = Clock(), Fix(), FakeTracker()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock, fix=fix, tracker=tr)
        tr.is_playing, tr.episode = 1, True
        fix.pending = True
        for _ in range(3):
            w.poll()
            clock.t += 5
        self.assertEqual(fix.calls.count("apply"), 3)
        fix.pending = False
        for _ in range(11):
            w.poll()
            clock.t += 5
        self.assertEqual(fix.calls.count("apply"), 3)
        w.poll()
        self.assertEqual(fix.calls.count("apply"), 4)
        fix.pending = True
        clock.t += 5
        w.poll()
        self.assertEqual(fix.calls.count("apply"), 5)

    def test_downloads_bridge_ip_with_localhost_fallback(self):
        calls = []
        fail = {"172.28.10.5"}

        def http(url, data=None):
            calls.append(url)
            if any(f"//{ip}:" in url for ip in fail):
                raise pw.urllib.error.URLError("connection refused")
            return b"ok"

        def run(cmd, **kw):
            if cmd[:2] == ["ip", "-o"]:
                return subprocess.CompletedProcess(cmd, 0, OWN, "")
            self.assertEqual(cmd[-1], "movie-gluetun")
            return subprocess.CompletedProcess(cmd, 0, "clamd=172.28.18.3 movie-downloads=172.28.10.5 \n", "")
        dl = pw.DownloadsHttp(run, http, self.logs.append)
        self.assertEqual(dl(pw.DOWNLOADS_BASE + ":8080/api/v2/app/version"), b"ok")
        self.assertEqual(calls, ["http://172.28.10.5:8080/api/v2/app/version", "http://127.0.0.1:8080/api/v2/app/version"])
        fail.clear()
        calls.clear()
        dl(pw.DOWNLOADS_BASE + ":8081/api?mode=version")
        self.assertEqual(calls, ["http://172.28.10.5:8081/api?mode=version"])
        calls.clear()
        dl(pw.DOWNLOADS_BASE + ":8081/api?mode=queue")
        self.assertEqual(calls, ["http://172.28.10.5:8081/api?mode=queue"])

    def test_downloads_http_errors_not_retried_on_localhost(self):
        calls = []

        def http(url, data=None):
            calls.append(url)
            raise pw.autofix.HttpError(403, url)

        def run(cmd, **kw):
            out = OWN if cmd[:2] == ["ip", "-o"] else "movie-downloads=172.28.10.5 \n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        dl = pw.DownloadsHttp(run, http, self.logs.append)
        with self.assertRaises(pw.autofix.HttpError):
            dl(pw.DOWNLOADS_BASE + ":8080/api/v2/app/preferences")
        self.assertEqual(calls, ["http://172.28.10.5:8080/api/v2/app/preferences"])

    def test_downloads_refuses_own_address(self):
        calls = []

        def run(cmd, **kw):
            out = OWN if cmd[:2] == ["ip", "-o"] else "movie-downloads=192.168.1.102 \n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        dl = pw.DownloadsHttp(run, lambda url, data=None: calls.append(url) or b"", self.logs.append)
        dl(pw.DOWNLOADS_BASE + ":8080/x")
        self.assertEqual(calls, ["http://127.0.0.1:8080/x"])

    def test_incident_recorded_and_counted(self):
        clock, fix, tr = Clock(), Fix(), FakeTracker()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock, fix=fix, tracker=tr)
        tr.incidents = [{"kind": "stall", "seconds": 6.0, "session_id": "s1", "user": "u", "client": "Web",
                         "device": "d", "item": "Pilot", "play_method": "Transcode", "transcode": None}]
        tr.is_playing, tr.episode = 1, True
        w.poll()
        rows = [json.loads(line) for line in (self.dir / "state" / "incidents.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["kind"], "stall")
        self.assertEqual(rows[0]["context"]["qbit_speed"], 100)
        self.assertEqual(rows[0]["context"]["sab_speed"], 200)
        self.assertEqual(w.counters.incidents[("stall", "Web")], 1)
        self.assertEqual(w.counters.autofix["downloads_slow"], 1)
        self.assertFalse(any("Pilot" in m or " u " in m for m in self.logs))

    def test_session_summary_on_end(self):
        clock = Clock()
        http = Http([[session(pos=0)], [session(pos=5)], []])
        w = self.watcher(Runner(["172.28.11.3"]), http, clock=clock)
        w.poll()
        clock.t += 5
        w.poll()
        clock.t += 5
        w.poll()
        rows = [json.loads(line) for line in (self.dir / "state" / "incidents.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "summary")
        self.assertEqual(rows[0]["item"], "Pilot")
        self.assertEqual(rows[0]["duration"], 10.0)
        self.assertEqual(rows[0]["incidents"], {})

    def test_poll_exception_does_not_stop_loop(self):
        clock, tr = Clock(), FakeTracker()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock, tracker=tr)
        calls = []

        def boom(sessions, now):
            calls.append(now)
            raise RuntimeError("tracker broke")
        tr.update = boom
        w.counters.save = lambda: (_ for _ in ()).throw(OSError("disk full"))
        w.run(Stop(clock, 3))
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(any("tracker broke" in m for m in self.logs))
        self.assertTrue((self.dir / "state" / "playback.prom").exists())

    def test_counter_save_error_does_not_stop_poll(self):
        clock, tr = Clock(), FakeTracker()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock, tracker=tr)
        w.counters.save = lambda: (_ for _ in ()).throw(OSError("disk full"))
        tr.incidents = [{"kind": "stall", "seconds": 6.0, "session_id": "s1", "user": "u", "client": "Web",
                         "device": "d", "item": "Pilot", "play_method": "Transcode", "transcode": None}] * 2
        tr.is_playing, tr.episode = 1, True
        w.poll()
        self.assertEqual(w.fix.calls, ["apply"])
        self.assertTrue(any("disk full" in m for m in self.logs))

    def test_sigterm_reverts(self):
        clock, fix = Clock(), Fix()
        fix.applied = True
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock, fix=fix)
        stop = threading.Event()
        old = pw.install_signals(stop)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            self.assertTrue(stop.wait(2))
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)
        w.run(stop)
        self.assertEqual(fix.calls, ["restore", "revert"])
        self.assertFalse(fix.applied)
        self.assertIn('jellyfin_playback_autofix_active{action="downloads_slow"} 0',
                      (self.dir / "state" / "playback.prom").read_text())

    def test_start_restores_saved_state(self):
        clock, fix = Clock(), Fix()
        w = self.watcher(Runner(["172.28.11.3"]), Http([[]]), clock=clock, fix=fix)
        w.run(Stop(clock, 1))
        self.assertEqual(fix.calls[0], "restore")

    def test_parse_since(self):
        self.assertEqual(pw.parse_duration("24h"), 86400)
        self.assertEqual(pw.parse_duration("90m"), 5400)
        self.assertEqual(pw.parse_duration("2d"), 172800)
        self.assertEqual(pw.parse_duration("30s"), 30)
        self.assertEqual(pw.parse_duration("45"), 45)
        with self.assertRaises(ValueError):
            pw.parse_duration("1w")

    def test_report_subcommand(self):
        state = self.dir / "state"
        pw.record.append_incident(state / "incidents.jsonl", {"kind": "silent", "seconds": 45.0, "client": "Web"}, {})
        out = []
        rc = pw.main(["report", "--since", "1h"], state_dir=state, out=out.append)
        self.assertEqual(rc, 0)
        self.assertIn("silent 1", out[0])


if __name__ == "__main__":
    unittest.main()
