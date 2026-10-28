#!/usr/bin/env python3
# Runs hook/clamav-hook.py against a fake clamd (INSTREAM) and a fake qBittorrent API.
# Usage: python3 tests/test_hook.py   (host, or inside movie-sabnzbd / movie-qbittorrent)
import http.server
import json
import os
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
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
        self.data = os.path.join(self.t, "data")
        self.job = os.path.join(self.data, "usenet", "complete", "movies", "Some.Movie.2026")
        self.quarantine = os.path.join(self.data, "quarantine")
        self.textfile = os.path.join(self.t, "textfile")
        self.queue = os.path.join(self.data, ".clamav-queue")
        os.makedirs(self.job)
        os.makedirs(self.quarantine)
        os.makedirs(self.textfile)
        self.clamd = serve(FakeClamd())
        self.addCleanup(self.clamd.server_close)
        self.addCleanup(self.clamd.shutdown)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data, where=None):
        p = os.path.join(where or self.job, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def run_hook(self, *args, **env):
        e = dict(os.environ)
        e.pop("SAB_PP_STATUS", None)
        # No queue directory by default: every file is scanned synchronously, as before the queue existed.
        e.update(CLAMD_HOST="127.0.0.1", CLAMD_PORT=str(self.clamd.server_address[1]), CLAMD_WAIT="0",
                 PATH_MAP="", QUARANTINE=self.quarantine, TEXTFILE=self.textfile, HOST="beefy", QBIT_API="http://127.0.0.1:1",
                 QUEUE=os.path.join(self.t, "no-queue"), WALK_ROOT=self.data,
                 HEARTBEAT=os.path.join(self.t, "alive"))
        e.update(env)
        return subprocess.run([sys.executable, HOOK, *args], env=e, capture_output=True, text=True, timeout=60)

    def metric(self, name, mode="hook"):
        with open(os.path.join(self.textfile, "clamav-hook.prom")) as f:
            for line in f:
                if line.startswith('%s{host="beefy",mode="%s"}' % (name, mode)):
                    return line.split()[1]
        return None

    def sab(self, status="0", **env):
        # SABnzbd: final dir, nzb name, clean name, indexer report, category, group, status
        return self.run_hook(self.job, "Some.Movie.2026.nzb", "Some Movie", "", "movies", "", status, **env)

    def queued(self, **env):
        os.makedirs(self.queue, exist_ok=True)
        return self.sab(QUEUE=self.queue, **env)

    def jobs(self):
        return sorted(n for n in os.listdir(self.queue) if not n.startswith(".")) if os.path.isdir(self.queue) else []

    def worker(self, **env):
        return self.run_hook("--worker", "--once", QUEUE=self.queue, **env)

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

    def test_index_path_is_mapped_to_the_clamav_container_view_for_restore(self):
        bad = self.write("setup.exe", EICAR)
        self.sab(PATH_MAP="%s/data=/scan/ssd-hot" % self.t)
        with open(os.path.join(self.quarantine, "index.txt")) as f:
            path = f.read().split("\t")[1]
        self.assertEqual(path, "/scan/ssd-hot" + bad[len(self.t) + len("/data"):])

    def test_index_path_names_the_branch_that_has_the_file(self):
        bad = self.write("setup.exe", EICAR)
        self.sab(PATH_MAP="%s/data=%s/hot1:%s/data" % (self.t, self.t, self.t))
        self.assertEqual(self.index()[0][1], bad)

    def test_index_path_prefers_the_first_branch_when_it_has_the_file(self):
        bad = self.write("setup.exe", EICAR)
        rel = bad[len(self.data):]
        self.write(rel.lstrip("/"), b"x", where=os.path.join(self.t, "hot1"))
        self.sab(PATH_MAP="%s/data=%s/hot1:%s/data" % (self.t, self.t, self.t))
        self.assertEqual(self.index()[0][1], os.path.join(self.t, "hot1") + rel)

    def test_index_path_falls_back_to_the_first_branch_when_none_has_the_file(self):
        bad = self.write("setup.exe", EICAR)
        self.sab(PATH_MAP="%s/data=/scan/ssd-hot:/scan/ssd-hot2" % self.t)
        self.assertEqual(self.index()[0][1], "/scan/ssd-hot" + bad[len(self.data):])

    def test_worker_hit_resolves_the_branch_before_the_links_are_removed(self):
        mkv = self.write("movie.mkv", EICAR)
        self.queued()
        library = os.path.join(self.data, "media", "movies", "Some Movie (2026)", "Some Movie (2026).mkv")
        os.makedirs(os.path.dirname(library))
        os.rename(mkv, library)
        r = self.worker(PATH_MAP="%s/data=%s/hot1:%s/data" % (self.t, self.t, self.t))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(library))
        self.assertEqual(self.index()[0][1], library)
        self.assertEqual(self.hits_log()[0][3], library)

    def test_mergerfs_basepath_picks_the_branch_by_name(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.PATH_MAP = "/data=/scan/ssd-hot:/scan/ssd-hot2"
        seen = []

        def getxattr(p, name):
            seen.append((p, name))
            return b"/srv/.disks/ssd-hot2"
        orig = os.getxattr
        os.getxattr = getxattr
        try:
            self.assertEqual(mod.container_path("/data/media/a.mkv", "/data/.clamav-queue/j/0001"), "/scan/ssd-hot2/media/a.mkv")
            self.assertEqual(seen, [("/data/.clamav-queue/j/0001", "user.mergerfs.basepath")])
            os.getxattr = lambda p, n: b"/srv/.disks/ssd-hot"
            self.assertEqual(mod.container_path("/data/media/a.mkv"), "/scan/ssd-hot/media/a.mkv")
            os.getxattr = lambda p, n: b"/srv/.disks/hdd-cold"
            self.assertEqual(mod.container_path("/data/media/a.mkv"), "/scan/ssd-hot/media/a.mkv")
            self.assertEqual(mod.container_path("/other/a.mkv"), "/other/a.mkv")
        finally:
            os.getxattr = orig

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
        self.assertEqual(sorted(os.listdir(self.quarantine)), ["index.txt", "setup.exe", "setup.exe.001"])

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

    # Failed SABnzbd jobs

    def test_failed_sab_job_is_not_scanned_and_exits_0_at_once(self):
        for status in ("1", "2", "3", "-1"):
            p = self.write("movie.mkv", EICAR)
            r = self.sab(status=status)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.clamd.streams, [])
            self.assertTrue(os.path.exists(p))
            self.assertIn("failed", r.stdout.strip().splitlines()[-1])
        self.assertFalse(os.path.exists(os.path.join(self.textfile, "clamav-hook.prom")))

    def test_failed_status_from_the_environment(self):
        self.write("movie.mkv", b"clean")
        r = self.run_hook(self.job, SAB_PP_STATUS="2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.clamd.streams, [])

    def test_status_0_from_the_environment_is_scanned(self):
        self.write("movie.mkv", b"clean")
        self.run_hook(self.job, SAB_PP_STATUS="0")
        self.assertEqual(self.clamd.streams, [b"clean"])

    # Media to the background queue, everything else synchronously

    def test_media_files_go_to_the_queue_not_to_clamd(self):
        mkv = self.write("movie.mkv", b"clean video")
        srt = self.write("Subs/movie.en.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        r = self.queued()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.clamd.streams, [])
        self.assertTrue(os.path.exists(mkv))
        jobs = self.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0].endswith(".tmp"))
        jobdir = os.path.join(self.queue, jobs[0])
        with open(os.path.join(jobdir, "job.json")) as f:
            job = json.load(f)
        self.assertEqual(job["title"], "Some Movie")
        self.assertEqual(job["hash"], None)
        self.assertEqual(sorted(e["path"] for e in job["files"]), sorted([mkv, srt]))
        for e in job["files"]:
            self.assertTrue(os.path.samefile(os.path.join(jobdir, e["link"]), e["path"]))
        self.assertEqual(self.metric("clamav_files_queued"), "2")
        self.assertIn("queued", r.stdout.strip().splitlines()[-1])

    def test_executable_and_archive_are_scanned_synchronously_and_block(self):
        bad = self.write("setup.exe", EICAR)
        rar = self.write("movie.part01.rar", b"Rar!\x1a\x07\x00 clean")
        self.write("movie.mkv", b"clean video")
        r = self.queued()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(bad))
        self.assertTrue(os.path.exists(os.path.join(self.quarantine, "setup.exe")))
        self.assertEqual(sorted(self.clamd.streams), sorted([EICAR, b"Rar!\x1a\x07\x00 clean"]))
        self.assertTrue(os.path.exists(rar))

    def test_unknown_extension_is_scanned_synchronously(self):
        self.write("readme.xyz", b"clean")
        self.write("noext", b"clean too")
        r = self.queued()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(self.clamd.streams), [b"clean", b"clean too"])
        self.assertEqual(self.jobs(), [])

    def test_media_name_with_executable_content_is_synchronous(self):
        bad = self.write("movie.mkv", b"MZ\x90\x00" + EICAR)
        self.write("cover.jpg", b"PK\x03\x04 zip")
        r = self.queued()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(bad))
        self.assertEqual(len(self.clamd.streams), 2)
        self.assertEqual(self.jobs(), [])

    def test_qbit_media_goes_to_the_queue_with_the_hash(self):
        self.write("movie.mkv", b"clean video")
        os.makedirs(self.queue)
        r = self.run_hook("--qbit", "abc123", self.job, QUEUE=self.queue)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.clamd.streams, [])
        with open(os.path.join(self.queue, self.jobs()[0], "job.json")) as f:
            job = json.load(f)
        self.assertEqual(job["hash"], "abc123")
        self.assertEqual(job["title"], "Some.Movie.2026")

    # Background worker

    def test_worker_scans_clean_media_and_empties_the_queue(self):
        mkv = self.write("movie.mkv", b"clean video")
        self.queued()
        r = self.worker()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.clamd.streams, [b"clean video"])
        self.assertEqual(self.jobs(), [])
        self.assertTrue(os.path.exists(mkv))
        self.assertEqual(self.metric("clamav_files_scanned", "queue"), "1")
        self.assertEqual(self.metric("clamav_infected_last_run", "queue"), "0")
        self.assertEqual(self.metric("clamav_queue_jobs", "queue"), "0")
        self.assertIn("Some Movie", r.stdout)

    def test_worker_hit_after_import_quarantines_and_removes_every_link(self):
        mkv = self.write("movie.mkv", EICAR)
        self.queued()
        library = os.path.join(self.data, "media", "movies", "Some Movie (2026)", "Some Movie (2026).mkv")
        seed = os.path.join(self.data, "torrents", "movies", "seed.mkv")
        os.makedirs(os.path.dirname(library))
        os.makedirs(os.path.dirname(seed))
        os.rename(mkv, library)
        os.link(library, seed)
        r = self.worker()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(library))
        self.assertFalse(os.path.exists(seed))
        self.assertEqual(self.jobs(), [])
        with open(os.path.join(self.quarantine, "index.txt")) as f:
            date, path, sig = f.read().splitlines()[0].split("\t")
        # The library path, so "scan.sh restore" puts it back where the app expects it.
        self.assertEqual(path, library)
        self.assertTrue(os.path.exists(os.path.join(self.quarantine, "Some Movie (2026).mkv")))
        self.assertEqual(sig, SIG)
        self.assertEqual(self.metric("clamav_infected_last_run", "queue"), "1")
        self.assertEqual(self.metric("clamav_infected_total", "queue"), "1")
        self.assertIn("Some Movie", [l for l in r.stdout.splitlines() if SIG in l][0])

    def test_worker_hit_with_an_unwritable_index_still_removes_the_links(self):
        mkv = self.write("movie.mkv", EICAR)
        self.queued()
        index = self.write("index.txt", b"", where=self.quarantine)
        os.chmod(index, 0o444)
        r = self.worker()
        self.assertFalse(os.path.exists(mkv))
        self.assertTrue(os.path.exists(os.path.join(self.quarantine, "movie.mkv")))
        self.assertEqual(self.jobs(), [])
        self.assertIn("index.txt", r.stdout + r.stderr)
        self.assertEqual(self.metric("clamav_infected_last_run", "queue"), "1")

    def test_index_is_group_writable_for_the_other_writers(self):
        self.write("setup.exe", EICAR)
        old = os.umask(0o022)
        try:
            self.sab()
        finally:
            os.umask(old)
        self.assertEqual(os.stat(os.path.join(self.quarantine, "index.txt")).st_mode & 0o777, 0o664)

    # clamav-hits.tsv: who quarantined what (index.txt keeps its 3 fields for scan.sh restore)

    def hits_log(self):
        with open(os.path.join(self.textfile, "clamav-hits.tsv")) as f:
            return [l.split("\t") for l in f.read().splitlines()]

    def index(self):
        with open(os.path.join(self.quarantine, "index.txt")) as f:
            return [l.split("\t") for l in f.read().splitlines()]

    def test_hook_hit_logs_time_mode_title_path_signature(self):
        bad = self.write("setup.exe", EICAR)
        self.sab()
        self.assertEqual(self.hits_log(), [[self.index()[0][0], "hook", "Some Movie", bad, SIG]])
        self.assertEqual(len(self.index()[0]), 3)

    def test_qbit_hook_hit_logs_the_torrent_name_as_title(self):
        self.write("setup.exe", EICAR)
        self.run_hook("--qbit", "abc123", self.job)
        self.assertEqual(self.hits_log()[0][1:3], ["hook", "Some.Movie.2026"])

    def test_worker_hit_logs_queue_mode_job_title_and_the_index_path(self):
        self.write("movie.mkv", EICAR)
        self.queued()
        self.worker()
        time_, path, sig = self.index()[0]
        self.assertEqual(self.hits_log(), [[time_, "queue", "Some Movie", path, sig]])

    def test_hits_log_path_is_mapped_like_the_index(self):
        self.write("setup.exe", EICAR)
        self.sab(PATH_MAP="%s/data=/scan/ssd-hot" % self.t)
        self.assertEqual(self.hits_log()[0][3], self.index()[0][1])
        self.assertTrue(self.hits_log()[0][3].startswith("/scan/ssd-hot/"))

    def test_hits_log_title_cannot_break_the_columns(self):
        self.write("setup.exe", EICAR)
        self.run_hook(self.job, "x.nzb", "Bad\tTitle\nInjected", "", "movies", "", "0")
        self.assertEqual(len(self.hits_log()), 1)
        self.assertEqual(self.hits_log()[0][2], "Bad Title Injected")

    def test_hits_log_is_group_writable_for_the_other_writers(self):
        self.write("setup.exe", EICAR)
        old = os.umask(0o022)
        try:
            self.sab()
        finally:
            os.umask(old)
        self.assertEqual(os.stat(os.path.join(self.textfile, "clamav-hits.tsv")).st_mode & 0o777, 0o664)

    def test_unwritable_hits_log_still_quarantines_and_indexes(self):
        bad = self.write("setup.exe", EICAR)
        log = self.write("clamav-hits.tsv", b"", where=self.textfile)
        os.chmod(log, 0o444)
        r = self.sab()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(bad))
        self.assertEqual(len(self.index()), 1)
        self.assertIn("clamav-hits.tsv", r.stdout + r.stderr)

    def test_worker_hit_with_a_link_it_cannot_remove_is_an_error(self):
        mkv = self.write("movie.mkv", EICAR)
        self.queued()
        os.chmod(self.job, 0o555)
        self.addCleanup(os.chmod, self.job, 0o755)
        r = self.worker()
        self.assertTrue(os.path.exists(mkv))
        self.assertIn("could not remove", r.stdout)
        self.assertEqual(self.metric("clamav_scan_errors", "queue"), "1")
        self.assertEqual(self.metric("clamav_infected_last_run", "queue"), "1")

    def test_worker_hit_of_a_torrent_stops_and_tags_it(self):
        qbit = serve(FakeQbit())
        self.addCleanup(qbit.server_close)
        self.addCleanup(qbit.shutdown)
        self.write("movie.mkv", EICAR)
        os.makedirs(self.queue)
        self.run_hook("--qbit", "abc123", self.job, QUEUE=self.queue)
        self.worker(QBIT_API="http://127.0.0.1:%d" % qbit.server_address[1])
        self.assertIn(("/api/v2/torrents/stop", "hashes=abc123"), qbit.calls)
        self.assertIn(("/api/v2/torrents/addTags", "hashes=abc123&tags=infected"), qbit.calls)

    def test_worker_keeps_the_job_while_clamd_is_unreachable(self):
        self.write("movie.mkv", b"clean video")
        self.queued()
        r = self.worker(CLAMD_PORT=str(free_port()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.jobs()), 1)
        self.worker()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.clamd.streams, [b"clean video"])

    def test_worker_keeps_the_job_when_clamd_gives_no_verdict(self):
        self.write("movie.mkv", b"clean video")
        self.queued()
        self.clamd.reply = b"\0"
        self.worker()
        self.assertEqual(len(self.jobs()), 1)
        self.clamd.reply = None
        self.worker()
        self.assertEqual(self.jobs(), [])

    def test_worker_takes_over_a_job_left_half_written(self):
        mkv = self.write("movie.mkv", b"clean video")
        stale = os.path.join(self.queue, "20260101T000000-1.tmp")
        os.makedirs(stale)
        os.link(mkv, os.path.join(stale, "0001.mkv"))
        old = time.time() - 7200
        os.utime(stale, (old, old))
        self.worker()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.clamd.streams, [b"clean video"])

    def test_hook_and_worker_metrics_live_side_by_side(self):
        self.write("movie.mkv", EICAR)
        self.queued()
        self.worker()
        self.write("other.exe", b"clean")
        self.queued()
        self.assertEqual(self.metric("clamav_infected_last_run", "queue"), "1")
        self.assertEqual(self.metric("clamav_infected_last_run", "hook"), "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
