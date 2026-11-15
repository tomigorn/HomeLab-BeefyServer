#!/usr/bin/env python3
# hook/virustotal.py: hash-only VirusTotal lookups with a fake clock and a fake API.
# Usage: python3 tests/test_virustotal.py
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error

HOOK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hook")
sys.path.insert(0, HOOK_DIR)
import virustotal as vt  # noqa: E402

KEY = "fake-vt-key-for-tests"
A = "a" * 64
B = "b" * 64
C = "c" * 64


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def stats(malicious=0, suspicious=0, undetected=60, harmless=10):
    return {"data": {"attributes": {"last_analysis_stats": {"malicious": malicious, "suspicious": suspicious, "undetected": undetected, "harmless": harmless,
                                                             "timeout": 1, "type-unsupported": 5}}}}


class FakeApi:
    """sha256 → reply body, or an int = HTTP error status; every request is kept."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        sha = req.full_url.rsplit("/", 1)[-1]
        a = self.answers.get(sha, 404)
        if isinstance(a, int):
            raise urllib.error.HTTPError(req.full_url, a, "error %d" % a, {}, io.BytesIO(b'{"error": {"code": "x"}}'))
        if isinstance(a, Exception):
            raise a
        return Reply(a)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.clock = Clock()
        self.api = FakeApi()
        self.key_file = os.path.join(self.dir, "vt_api_key")
        with open(self.key_file, "w") as f:
            f.write(KEY + "\n")

    def checker(self, **kw):
        return vt.Checker(self.dir, self.key_file, opener=self.api, clock=self.clock, **kw)

    def lines(self):
        p = os.path.join(self.dir, vt.LOG)
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(x) for x in f if x.strip()]

    def last(self, path):
        return [x for x in self.lines() if x["path"] == path][-1]

    def queue(self, path, sha, size=10):
        vt.queue(self.dir, path, sha, size, clock=self.clock)


class Queue(Base):
    def test_queued_line_has_path_hash_size_and_time(self):
        self.queue("/data/x.mkv", A, 123)
        x = self.lines()[0]
        self.assertEqual((x["path"], x["sha256"], x["size"], x["vt"]), ("/data/x.mkv", A, 123, "queued"))
        self.assertTrue(x["at"].endswith("Z") and x["hashed"] == x["at"])

    def test_queue_never_raises(self):
        os.makedirs(os.path.join(self.dir, vt.LOG))
        self.queue("/data/x.mkv", A)

    def test_a_bad_hash_is_not_queued(self):
        self.queue("/data/x.mkv", "../../upload")
        self.assertEqual(self.lines(), [])


class Verdicts(Base):
    def test_three_or_more_engines_is_malware(self):
        self.api.answers[A] = stats(malicious=3)
        self.queue("/data/a.exe", A)
        self.checker().step()
        x = self.last("/data/a.exe")
        self.assertEqual((x["vt"], x["malicious"], x["engines"]), ("malware", 3, 73))

    def test_one_or_two_engines_is_flagged(self):
        for n in (1, 2):
            sha = str(n) * 64
            self.api.answers[sha] = stats(malicious=n)
            self.queue("/data/%d.mkv" % n, sha)
        self.checker().step()
        self.assertEqual([(self.last("/data/%d.mkv" % n)["vt"], self.last("/data/%d.mkv" % n)["malicious"]) for n in (1, 2)], [("flagged", 1), ("flagged", 2)])

    def test_known_and_clean(self):
        self.api.answers[A] = stats(suspicious=1)
        self.queue("/data/a.mkv", A)
        self.checker().step()
        x = self.last("/data/a.mkv")
        self.assertEqual((x["vt"], x["malicious"], x["suspicious"]), ("clean", 0, 1))

    def test_unknown_is_404(self):
        self.queue("/data/a.mkv", A)
        self.checker().step()
        self.assertEqual(self.last("/data/a.mkv")["vt"], "unknown")

    def test_failed_lookup_is_an_error_and_retried_later(self):
        self.api.answers[A] = 500
        self.queue("/data/a.mkv", A)
        c = self.checker()
        c.step()
        self.assertEqual(self.last("/data/a.mkv")["vt"], "error")
        self.api.answers[A] = stats(malicious=0)
        self.clock.t += 60
        c.step()
        self.assertEqual(len(self.api.requests), 1, "retried too soon")
        self.clock.t += vt.RETRY
        c.step()
        self.assertEqual(self.last("/data/a.mkv")["vt"], "clean")

    def test_network_error_is_an_error(self):
        self.api.answers[A] = urllib.error.URLError("down")
        self.queue("/data/a.mkv", A)
        self.checker().step()
        self.assertEqual(self.last("/data/a.mkv")["vt"], "error")

    def test_quota_reply_is_an_error_and_pauses_the_lookups(self):
        self.api.answers[A] = 429
        self.queue("/data/a.mkv", A)
        self.queue("/data/b.mkv", B)
        self.checker().step()
        self.assertEqual(len(self.api.requests), 1)
        self.assertEqual(self.last("/data/a.mkv")["vt"], "error")


class NoUpload(Base):
    def test_only_get_lookups_by_hash_are_sent(self):
        self.api.answers[A] = stats()
        self.queue("/data/a.mkv", A)
        self.queue("/data/b.mkv", B)
        self.checker().step()
        self.assertEqual(len(self.api.requests), 2)
        for r in self.api.requests:
            self.assertEqual(r.get_method(), "GET")
            self.assertIsNone(r.data)
            self.assertRegex(r.full_url, r"^https://www\.virustotal\.com/api/v3/files/[0-9a-f]{64}$")

    def test_the_code_has_no_upload_path(self):
        for name in ("virustotal.py", "clamav-hook.py"):
            with open(os.path.join(HOOK_DIR, name)) as f:
                src = f.read()
            for bad in ("upload", "/files\"", "/files'", "POST", "multipart", "/analyses", "/urls"):
                if name == "clamav-hook.py" and bad == "POST":
                    continue
                self.assertNotIn(bad.lower(), src.lower() if bad != "POST" else src, "%s in %s" % (bad, name))
        with open(os.path.join(HOOK_DIR, "virustotal.py")) as f:
            self.assertEqual(re.findall(r"https://[^\"' ]+", f.read()), ["https://www.virustotal.com/api/v3/files/"])


class Key(Base):
    def test_key_goes_in_the_header_only(self):
        self.api.answers[A] = stats()
        self.queue("/data/a.mkv", A)
        self.checker().step()
        r = self.api.requests[0]
        self.assertEqual(r.get_header("X-apikey"), KEY)
        self.assertNotIn(KEY, r.full_url)

    def test_key_never_in_logs_output_or_files(self):
        self.api.answers[A] = 401
        self.api.answers[B] = urllib.error.URLError("refused " + "x")
        self.queue("/data/a.mkv", A)
        self.queue("/data/b.mkv", B)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            c = self.checker()
            c.step()
            c.metrics()
        self.assertNotIn(KEY, out.getvalue())
        for n in os.listdir(self.dir):
            p = os.path.join(self.dir, n)
            if p != self.key_file and os.path.isfile(p):
                with open(p) as f:
                    self.assertNotIn(KEY, f.read(), n)

    def test_no_subprocess_in_the_module(self):
        with open(os.path.join(HOOK_DIR, "virustotal.py")) as f:
            src = f.read()
        self.assertNotIn("subprocess", src)
        self.assertNotIn("os.system", src)

    def test_missing_key_does_nothing(self):
        os.remove(self.key_file)
        self.queue("/data/a.mkv", A)
        self.checker().step()
        self.assertEqual(self.api.requests, [])
        self.assertEqual(self.last("/data/a.mkv")["vt"], "queued")


class Limits(Base):
    def test_four_lookups_a_minute(self):
        for i in range(6):
            self.queue("/data/%d.mkv" % i, "%x" % i * 64)
        c = self.checker()
        c.step()
        self.assertEqual(len(self.api.requests), 4)
        self.clock.t += 30
        c.step()
        self.assertEqual(len(self.api.requests), 4)
        self.clock.t += 31
        c.step()
        self.assertEqual(len(self.api.requests), 6)

    def test_500_lookups_a_day_survive_a_restart(self):
        c = self.checker()
        c.limiter.used = 499
        c.limiter.save()
        for i in range(3):
            self.queue("/data/%d.mkv" % i, "%x" % (i + 1) * 64)
        c = self.checker()
        c.step()
        self.assertEqual(len(self.api.requests), 1)
        self.clock.t += 3600
        self.checker().step()
        self.assertEqual(len(self.api.requests), 1)
        day = 86400
        self.clock.t = (self.clock.t // day + 1) * day + 5
        self.checker().step()
        self.assertEqual(len(self.api.requests), 3)

    def test_minute_window_survives_a_restart(self):
        for i in range(5):
            self.queue("/data/%d.mkv" % i, "%x" % (i + 1) * 64)
        self.checker().step()
        self.checker().step()
        self.assertEqual(len(self.api.requests), 4)

    def test_quota_reply_pauses_for_a_minute(self):
        self.api.answers[A] = 429
        self.queue("/data/a.mkv", A)
        self.queue("/data/b.mkv", B)
        c = self.checker()
        c.step()
        self.clock.t += 30
        c.step()
        self.assertEqual(len(self.api.requests), 1)
        self.clock.t += 31
        c.step()
        self.assertEqual(len(self.api.requests), 2)


class Cache(Base):
    def test_same_hash_is_looked_up_once(self):
        self.api.answers[A] = stats(malicious=5)
        self.queue("/data/a.mkv", A)
        self.queue("/data/copy-of-a.mkv", A)
        c = self.checker()
        c.step()
        self.queue("/data/later-a.mkv", A)
        c.step()
        self.assertEqual(len(self.api.requests), 1)
        self.assertEqual([self.last(p)["vt"] for p in ("/data/a.mkv", "/data/copy-of-a.mkv", "/data/later-a.mkv")], ["malware"] * 3)

    def test_cache_survives_a_restart_and_the_rotated_log(self):
        self.queue("/data/a.mkv", A)
        self.checker().step()
        os.replace(os.path.join(self.dir, vt.LOG), os.path.join(self.dir, vt.LOG + ".1"))
        self.queue("/data/b.mkv", A)
        self.checker().step()
        self.assertEqual(len(self.api.requests), 1)
        self.assertEqual(self.last("/data/b.mkv")["vt"], "unknown")

    def test_queue_survives_a_restart(self):
        self.queue("/data/a.mkv", A)
        self.queue("/data/b.mkv", B)
        self.checker(per_minute=1).step()
        self.clock.t += 61
        self.checker(per_minute=1).step()
        self.assertEqual(sorted(r.full_url[-64:] for r in self.api.requests), [A, B])

    def test_log_is_rotated_when_big(self):
        p = os.path.join(self.dir, vt.LOG)
        with open(p, "w") as f:
            f.write("x" * 100 + "\n")
        vt.queue(self.dir, "/data/a.mkv", A, 1, clock=self.clock, max_bytes=50)
        self.assertTrue(os.path.exists(p + ".1"))
        self.assertEqual(len(self.lines()), 1)

    def test_oldest_first(self):
        self.queue("/data/a.mkv", A)
        self.queue("/data/b.mkv", B)
        self.queue("/data/c.mkv", C)
        self.checker(per_minute=2).step()
        self.assertEqual([r.full_url[-64:] for r in self.api.requests], [A, B])


class Metrics(Base):
    def test_counts_by_verdict(self):
        self.api.answers[A] = stats(malicious=4)
        self.api.answers[B] = stats(malicious=1)
        for p, s in (("/data/a", A), ("/data/b", B), ("/data/c", C)):
            self.queue(p, s)
        self.queue("/data/d", "d" * 64)
        c = self.checker(per_minute=3)
        c.step()
        self.assertEqual(c.counts(), {"malware": 1, "flagged": 1, "unknown": 1, "queued": 1, "lookups_today": 3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
