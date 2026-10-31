#!/usr/bin/env python3
# Runs hook/download-integrity.py against fake media, a fake par2, a fake ClamAV hook and a fake qBittorrent API.
# Usage: python3 tests/test_integrity.py
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hook", "download-integrity.py")
MIB = 1 << 20
MKV = b"\x1a\x45\xdf\xa3"


def media(mib, header=MKV, zero_blocks=(), tail=12345):
    """mib MiB of non-zero bytes after a container header, with the given aligned 1 MiB blocks zeroed."""
    data = bytearray(os.urandom(mib * MIB + tail).replace(b"\0", b"\1"))
    data[: len(header)] = header
    for b in zero_blocks:
        data[b * MIB:(b + 1) * MIB] = bytes(MIB)
    return bytes(data)


class FakeQbit(http.server.ThreadingHTTPServer):
    """password set: like qBittorrent without the localhost bypass, every call needs the SID of a login first."""
    def __init__(self, password=None):
        self.calls = []
        self.logins = []
        self.password = password
        super().__init__(("127.0.0.1", 0), FakeQbitHandler)


class FakeQbitHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if self.path == "/api/v2/auth/login":
            form = urllib.parse.parse_qs(body)
            self.server.logins.append(form.get("username", [""])[0])
            ok = form.get("password", [""])[0] == self.server.password
            self.send_response(204 if ok else 200)
            if ok:
                self.send_header("Set-Cookie", "QBT_SID_8080=good; HttpOnly; path=/")
            self.end_headers()
            if not ok:
                self.wfile.write(b"Fails.")
            return
        if self.server.password is not None and "QBT_SID_8080=good" not in (self.headers.get("Cookie") or ""):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return
        self.server.calls.append((self.path, body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ok.")

    def log_message(self, *a):
        pass


class IntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = self.tmp.name
        self.job = os.path.join(self.t, "complete", "tv", "Some.Show.S01E01")
        self.textfile = os.path.join(self.t, "textfile")
        os.makedirs(self.job)
        os.makedirs(self.textfile)
        self.marker = os.path.join(self.t, "clamav-ran")
        self.clamav = os.path.join(self.t, "clamav-hook.py")
        with open(self.clamav, "w") as f:
            f.write("import sys\nopen(%r, 'w').write(' '.join(sys.argv[1:]))\nsys.exit(0)\n" % self.marker)
        self.par2_log = os.path.join(self.t, "par2-calls")
        self.par2 = os.path.join(self.t, "par2")
        self.fake_par2(0)

    def fake_par2(self, code, out=""):
        with open(self.par2, "w") as f:
            f.write("#!/bin/sh\necho \"$@\" >> %s\nprintf '%%s\\n' '%s'\nexit %d\n" % (self.par2_log, out, code))
        os.chmod(self.par2, 0o755)

    def write(self, name, data):
        p = os.path.join(self.job, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def run_hook(self, *args, **env):
        e = dict(os.environ)
        e.update(TEXTFILE=self.textfile, HOST="beefy", CLAMAV_HOOK=self.clamav, PAR2=self.par2,
                 QBIT_API="http://127.0.0.1:1")
        e.update(env)
        return subprocess.run([sys.executable, HOOK, *args], env=e, capture_output=True, text=True, timeout=120)

    def sab(self, status="0", **env):
        return self.run_hook(self.job, "Some.Show.S01E01.nzb", "Some.Show.S01E01", "", "tv", "", status, **env)

    def metric(self, name, client="sabnzbd"):
        try:
            with open(os.path.join(self.textfile, "download-integrity.prom")) as f:
                for line in f:
                    if line.startswith('%s{host="beefy",client="%s"}' % (name, client)):
                        return float(line.split()[1])
        except FileNotFoundError:
            pass
        return None

    def hits(self):
        try:
            with open(os.path.join(self.textfile, "download-integrity-hits.tsv")) as f:
                return [line.rstrip("\n").split("\t") for line in f]
        except FileNotFoundError:
            return []

    def test_clean_job_hands_over_to_clamav_with_the_same_arguments(self):
        self.write("ep.mkv", media(3))
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.marker) as f:
            self.assertEqual(f.read(), "%s Some.Show.S01E01.nzb Some.Show.S01E01  tv  0" % self.job)
        self.assertEqual(self.metric("download_integrity_checked_total"), 1)
        self.assertEqual(self.metric("download_integrity_corrupt_total"), 0)
        self.assertEqual(self.hits(), [])

    def test_aligned_zero_mebibyte_fails_the_job_and_skips_clamav(self):
        p = self.write("ep.mkv", media(6, zero_blocks=[3]))
        r = self.sab()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.marker))
        self.assertIn("1 MiB of zeros", r.stdout)
        self.assertTrue(os.path.exists(p), "the data stays for the failed job")
        self.assertEqual(self.metric("download_integrity_corrupt_total"), 1)
        self.assertGreater(self.metric("download_integrity_last_corrupt_timestamp_seconds"), 0)
        hit = self.hits()
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0][1:4], ["sabnzbd", "Some.Show.S01E01", p])
        self.assertIn("zeros", hit[0][4])

    def test_zeros_not_covering_a_whole_aligned_mebibyte_pass(self):
        data = bytearray(media(4))
        data[MIB + MIB // 2:2 * MIB + MIB // 2] = bytes(MIB)
        self.write("ep.mkv", bytes(data))
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.marker))

    def test_zero_threshold_is_configurable(self):
        self.write("ep.mkv", media(6, zero_blocks=[2]))
        r = self.sab(INTEGRITY_ZERO_MIB="2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.write("ep.mkv", media(6, zero_blocks=[2, 4]))
        r = self.sab(INTEGRITY_ZERO_MIB="2")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_zeroed_header_fails(self):
        self.write("ep.mkv", media(0, header=b"\0\0\0\0", tail=5000))
        r = self.sab()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("header", r.stdout)

    def test_mp4_and_ts_headers_are_recognised(self):
        self.write("a.mp4", media(1, header=b"\0\0\0\x20ftypisom"))
        ts = bytearray(media(1, header=b"\x47"))
        ts[188] = 0x47
        self.write("b.ts", bytes(ts))
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.write("c.mp4", media(1, header=b"\0\0\0\x20junk"))
        r = self.sab()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_non_media_files_are_not_scanned_for_zeros(self):
        self.write("ep.nfo", bytes(3 * MIB))
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_par2_verify_ok_removes_par2_files_and_trusts_the_set(self):
        self.write("ep.mkv", media(4, zero_blocks=[2]))
        main = self.write("ep.par2", b"PAR2")
        vol = self.write("ep.vol00+01.par2", b"PAR2")
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.par2_log) as f:
            call = f.read().split()
        self.assertEqual(call[0], "verify")
        self.assertEqual(call[1], main)
        self.assertIn(os.path.join(self.job, "ep.mkv"), call[2:])
        self.assertFalse(os.path.exists(main) or os.path.exists(vol))
        self.assertTrue(os.path.exists(self.marker))

    def test_par2_damaged_target_fails_the_job(self):
        self.fake_par2(2, 'Scanning: 100.0%\rTarget: "ep.mkv" - damaged. Found 95 of 100 data blocks.')
        self.write("ep.mkv", media(2))
        self.write("ep.par2", b"PAR2")
        r = self.sab()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("par2", r.stdout)
        self.assertEqual(self.hits()[0][3], os.path.join(self.job, "ep.mkv"))
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(os.path.exists(os.path.join(self.job, "ep.par2")))

    def test_par2_missing_target_only_falls_back_to_the_zero_scan(self):
        self.fake_par2(2, 'Target: "ep.nfo" - missing.')
        self.write("ep.mkv", media(3))
        self.write("ep.par2", b"PAR2")
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.marker))
        self.write("ep.mkv", media(3, zero_blocks=[1]))
        r = self.sab()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_missing_par2_binary_falls_back_to_the_zero_scan(self):
        self.write("ep.mkv", media(4, zero_blocks=[1]))
        self.write("ep.par2", b"PAR2")
        r = self.sab(PAR2=os.path.join(self.t, "nope"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("zeros", r.stdout)

    def test_failed_sab_job_is_not_checked(self):
        self.write("ep.mkv", media(4, zero_blocks=[1]))
        r = self.sab(status="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.marker))
        self.assertIsNone(self.metric("download_integrity_checked_total"))

    def test_unreadable_file_does_not_block_the_job(self):
        if os.geteuid() == 0:
            self.skipTest("root reads everything")
        p = self.write("ep.mkv", media(1))
        os.chmod(p, 0)
        r = self.sab()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.marker))
        self.assertEqual(self.metric("download_integrity_errors_total"), 1)

    def test_counters_add_up_across_runs(self):
        self.write("ep.mkv", media(2))
        self.sab()
        self.sab()
        self.write("ep.mkv", media(2, zero_blocks=[1]))
        self.sab()
        self.assertEqual(self.metric("download_integrity_checked_total"), 3)
        self.assertEqual(self.metric("download_integrity_corrupt_total"), 1)

    def test_qbit_corrupt_torrent_is_rechecked_and_tagged(self):
        qbit = FakeQbit()
        threading.Thread(target=qbit.serve_forever, daemon=True).start()
        self.addCleanup(qbit.server_close)
        self.addCleanup(qbit.shutdown)
        self.write("ep.mkv", media(3, zero_blocks=[1]))
        r = self.run_hook("--qbit", "abc123", self.job, QBIT_API="http://127.0.0.1:%d" % qbit.server_address[1])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.marker))
        paths = [c[0] for c in qbit.calls]
        self.assertIn("/api/v2/torrents/recheck", paths)
        self.assertIn(("/api/v2/torrents/addTags", "hashes=abc123&tags=corrupt"), qbit.calls)
        self.assertEqual(self.metric("download_integrity_corrupt_total", "qbittorrent"), 1)

    def test_qbit_without_localhost_bypass_logs_in(self):
        qbit = FakeQbit(password="s3cret")
        threading.Thread(target=qbit.serve_forever, daemon=True).start()
        self.addCleanup(qbit.server_close)
        self.addCleanup(qbit.shutdown)
        pw = os.path.join(self.t, "qbit.password")
        with open(pw, "w") as f:
            f.write("s3cret\n")
        self.write("ep.mkv", media(3, zero_blocks=[1]))
        r = self.run_hook("--qbit", "abc123", self.job, QBIT_API="http://127.0.0.1:%d" % qbit.server_address[1], QBIT_PASS_FILE=pw)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(qbit.logins, ["admin"])
        self.assertEqual([c[0] for c in qbit.calls], ["/api/v2/torrents/recheck", "/api/v2/torrents/addTags"])

    def test_qbit_clean_torrent_hands_over_to_clamav(self):
        self.write("ep.mkv", media(2))
        r = self.run_hook("--qbit", "abc123", self.job)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.marker) as f:
            self.assertEqual(f.read(), "--qbit abc123 %s" % self.job)

    def test_check_mode_reports_without_handing_over(self):
        bad = self.write("bad.mkv", media(3, zero_blocks=[1]))
        good = self.write("good.mkv", media(3))
        r = self.run_hook("--check", bad, good)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(bad, r.stdout)
        self.assertNotIn("CORRUPT\t" + good, r.stdout)
        self.assertFalse(os.path.exists(self.marker))
        self.assertEqual(self.hits(), [])
        r = self.run_hook("--check", good)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
