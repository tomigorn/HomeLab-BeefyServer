#!/usr/bin/env python3
# Runs hook/clamav-hook.py against a fake clamd (INSTREAM) and a fake qBittorrent API.
# Usage: python3 tests/test_hook.py   (host, or inside movie-sabnzbd / movie-qbittorrent)
import http.server
import os
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hook", "clamav-hook.py")
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
SIG = "Win.Test.EICAR_HDB-1"


class FakeClamd(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, reply=None):
        self.streams = []
        self.reply = reply
        super().__init__(("127.0.0.1", 0), FakeClamdHandler)


class FakeClamdHandler(socketserver.StreamRequestHandler):
    def handle(self):
        cmd = b""
        while not cmd.endswith(b"\0"):
            c = self.rfile.read(1)
            if not c:
                return
            cmd += c
        if cmd != b"zINSTREAM\0":
            self.wfile.write(b"UNKNOWN COMMAND\0")
            return
        data = b""
        while True:
            (n,) = struct.unpack("!I", self.rfile.read(4))
            if n == 0:
                break
            data += self.rfile.read(n)
        self.server.streams.append(data)
        if self.server.reply:
            self.wfile.write(self.server.reply)
        elif b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
            self.wfile.write(("stream: %s FOUND\0" % SIG).encode())
        else:
            self.wfile.write(b"stream: OK\0")


class FakeQbit(http.server.ThreadingHTTPServer):
    def __init__(self):
        self.calls = []
        super().__init__(("127.0.0.1", 0), FakeQbitHandler)


class FakeQbitHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self.server.calls.append((self.path, body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ok.")

    def log_message(self, *a):
        pass


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.job = os.path.join(self.t, "data", "usenet", "complete", "movies", "Some.Movie.2026")
        self.quarantine = os.path.join(self.t, "data", "quarantine")
        self.textfile = os.path.join(self.t, "textfile")
        os.makedirs(self.job)
        os.makedirs(self.quarantine)
        os.makedirs(self.textfile)
        self.clamd = serve(FakeClamd())
        self.addCleanup(self.clamd.server_close)
        self.addCleanup(self.clamd.shutdown)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data, where=None):
        p = os.path.join(where or self.job, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def run_hook(self, *args, **env):
        e = dict(os.environ)
        e.update(CLAMD_HOST="127.0.0.1", CLAMD_PORT=str(self.clamd.server_address[1]), CLAMD_WAIT="0",
                 QUARANTINE=self.quarantine, TEXTFILE=self.textfile, HOST="beefy", QBIT_API="http://127.0.0.1:1")
        e.update(env)
        return subprocess.run([sys.executable, HOOK, *args], env=e, capture_output=True, text=True, timeout=60)

    def metric(self, name):
        with open(os.path.join(self.textfile, "clamav-hook.prom")) as f:
            for line in f:
                if line.startswith(name + "{"):
                    return line.split()[1]
        return None

    def sab(self, **env):
        # SABnzbd: final dir, nzb name, clean name, indexer report, category, group, status
        return self.run_hook(self.job, "Some.Movie.2026.nzb", "Some Movie", "", "movies", "", "0", **env)

    def test_clean_job_exits_0_and_keeps_files(self):
        p = self.write("movie.mkv", b"clean video bytes")
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(p))
        self.assertEqual(os.listdir(self.quarantine), [])
        self.assertEqual(self.clamd.streams, [b"clean video bytes"])

    def test_infected_file_moved_to_quarantine_and_exit_1(self):
        bad = self.write("setup.exe", EICAR)
        good = self.write("movie.mkv", b"clean")
        r = self.sab()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(bad))
        self.assertTrue(os.path.exists(good))
        self.assertTrue(os.path.exists(os.path.join(self.quarantine, "setup.exe")))
        self.assertIn(SIG, r.stdout.strip().splitlines()[-1])

    def test_index_line_has_date_original_path_and_signature(self):
        bad = self.write("setup.exe", EICAR)
        self.sab()
        with open(os.path.join(self.quarantine, "index.txt")) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        date, path, sig = lines[0].split("\t")
        self.assertRegex(date, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(path, bad)
        self.assertEqual(sig, SIG)

    def test_metrics_of_an_infected_run(self):
        self.write("setup.exe", EICAR)
        self.write("movie.mkv", b"clean")
        self.sab()
        L = '{host="beefy",mode="hook"}'
        self.assertEqual(self.metric("clamav_infected_last_run"), "1")
        self.assertEqual(self.metric("clamav_files_scanned"), "2")
        self.assertEqual(self.metric("clamav_scan_errors"), "0")
        self.assertEqual(self.metric("clamav_files_skipped_large"), "0")
        self.assertEqual(self.metric("clamav_infected_total"), "1")
        with open(os.path.join(self.textfile, "clamav-hook.prom")) as f:
            text = f.read()
        self.assertIn("clamav_infected_total" + L + " 1\n", text)
        self.assertIn("# TYPE clamav_infected_total counter\n", text)
        self.assertIn("# TYPE clamav_infected_last_run gauge\n", text)
        self.assertRegex(text, r"clamav_last_run_timestamp_seconds\{host=\"beefy\",mode=\"hook\"\} \d{10}\n")

    def test_infected_total_accumulates_across_runs(self):
        self.write("a.exe", EICAR)
        self.sab()
        self.write("b.exe", EICAR)
        self.sab()
        self.assertEqual(self.metric("clamav_infected_total"), "2")
        self.write("movie.mkv", b"clean")
        self.sab()
        self.assertEqual(self.metric("clamav_infected_total"), "2")
        self.assertEqual(self.metric("clamav_infected_last_run"), "0")

    def test_file_over_the_limit_is_skipped_not_streamed(self):
        self.write("big.mkv", b"x" * 2048)
        r = self.sab(HOOK_MAX_BYTES="1024")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.clamd.streams, [])
        self.assertEqual(self.metric("clamav_files_skipped_large"), "1")
        self.assertEqual(self.metric("clamav_files_scanned"), "0")

    def test_clamd_unreachable_fails_open_with_error_metric(self):
        p = self.write("movie.mkv", b"clean")
        r = self.sab(CLAMD_PORT=str(free_port()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(p))
        self.assertEqual(self.metric("clamav_scan_errors"), "1")
        self.assertIn("not scanned", r.stdout.strip().splitlines()[-1])

    def test_clamd_error_reply_is_an_error_not_a_hit(self):
        self.clamd.reply = b"INSTREAM size limit exceeded. ERROR\0"
        p = self.write("movie.mkv", b"clean")
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(p))
        self.assertEqual(self.metric("clamav_scan_errors"), "1")
        self.assertEqual(self.metric("clamav_infected_last_run"), "0")

    def test_quarantine_name_clash_keeps_both(self):
        self.write("setup.exe", EICAR, where=self.quarantine)
        self.write("setup.exe", EICAR)
        self.sab()
        self.assertEqual(sorted(os.listdir(self.quarantine)), ["index.txt", "setup.exe", "setup.exe.1"])

    def test_quarantine_inside_the_scanned_tree_is_not_rescanned(self):
        self.quarantine = os.path.join(self.job, "quarantine")
        os.makedirs(self.quarantine)
        self.write("old.exe", EICAR, where=self.quarantine)
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.clamd.streams, [])

    def test_qbit_single_file_infected_stops_and_tags_the_torrent(self):
        qbit = serve(FakeQbit())
        self.addCleanup(qbit.server_close)
        self.addCleanup(qbit.shutdown)
        bad = self.write("Some.Movie.2026.exe", EICAR)
        r = self.run_hook("--qbit", "abc123", bad, QBIT_API="http://127.0.0.1:%d" % qbit.server_address[1])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(bad))
        self.assertIn(("/api/v2/torrents/stop", "hashes=abc123"), qbit.calls)
        self.assertIn(("/api/v2/torrents/addTags", "hashes=abc123&tags=infected"), qbit.calls)

    def test_qbit_clean_torrent_makes_no_api_calls(self):
        qbit = serve(FakeQbit())
        self.addCleanup(qbit.server_close)
        self.addCleanup(qbit.shutdown)
        self.write("movie.mkv", b"clean")
        r = self.run_hook("--qbit", "abc123", self.job, QBIT_API="http://127.0.0.1:%d" % qbit.server_address[1])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(qbit.calls, [])

    def test_qbit_api_down_still_quarantines(self):
        bad = self.write("setup.exe", EICAR)
        r = self.run_hook("--qbit", "abc123", self.job)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(bad))

    def test_no_path_is_a_usage_error(self):
        r = self.run_hook()
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
