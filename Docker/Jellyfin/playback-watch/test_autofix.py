import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import autofix as af  # noqa: E402

UNITS = ("subtitle-translate.service", "clamav-hostscan.service", "clamav-scan.service",
         "beefy-mover.service", "original-audio.service", "beefy-promoter.service")
CONTAINERS = ("subtitle-libretranslate", "subtitle-lingarr", "clamav", "clamav-queue",
              "movie-sabnzbd", "movie-qbittorrent")


class FakeQbit:
    def __init__(self, up_limit=0, alt_dl=10240, alt_up=10240, mode=0, speed=1000):
        self.prefs_ = {"up_limit": up_limit, "dl_limit": 0, "alt_dl_limit": alt_dl, "alt_up_limit": alt_up}
        self.mode = mode
        self.speed = speed
        self.down = False

    def _check(self):
        if self.down:
            raise OSError("connection refused")

    def prefs(self):
        self._check()
        return dict(self.prefs_)

    def set_prefs(self, values):
        self._check()
        self.prefs_.update(values)

    def speed_mode(self):
        self._check()
        return self.mode

    def toggle_speed_mode(self):
        self._check()
        self.mode = 1 - self.mode

    def dl_speed(self):
        self._check()
        return self.speed


class FakeSab:
    def __init__(self, limit="0", limit_abs="0", speed=2048):
        self.limit, self.limit_abs, self.speed = limit, limit_abs, speed
        self.down = False
        self.sets = []

    def _check(self):
        if self.down:
            raise OSError("connection refused")

    def speedlimit(self):
        self._check()
        return self.limit, self.limit_abs

    def set_speedlimit(self, value):
        self._check()
        self.sets.append(value)
        self.limit = value

    def dl_speed(self):
        self._check()
        return self.speed


class FakeSystemd:
    def __init__(self, active=(), running=()):
        self.active = set(active)
        self.running = {c: f"docker-{c}id.scope" for c in running}
        self.weights = {}
        self.preset = {}
        self.calls = []
        self.fail = set()
        self.gone = set()
        self.unreadable = set()

    def active_units(self, units):
        return [u for u in units if u in self.active]

    def container_scopes(self, names):
        return [self.running[n] for n in names if n in self.running]

    def weights_of(self, unit):
        if unit in self.unreadable:
            raise RuntimeError(f"{unit}: show failed")
        return self.weights.get(unit, self.preset.get(unit, (100, 100)))

    def set_weight(self, unit, cpu, io):
        self.calls.append((unit, cpu, io))
        if unit in self.fail or unit in self.gone:
            raise RuntimeError(f"{unit}: failed")
        self.weights[unit] = (cpu, io)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "sub" / "autofix.json"
        self.qbit = FakeQbit()
        self.sab = FakeSab()
        self.sd = FakeSystemd(active={"beefy-promoter.service", "clamav-scan.service"}, running={"clamav", "movie-qbittorrent"})
        self.logs = []
        self.counted = []

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, qbit=None, sab=None, sd=None):
        return af.AutoFix(qbit or self.qbit, sab or self.sab, sd or self.sd, self.state,
                          log=self.logs.append, on_action=self.counted.append)


class AutoFixTest(Base):
    def test_apply_saves_and_sets_values(self):
        fix = self.make()
        self.assertEqual(fix.apply(100.0), ["downloads_slow", "background_low"])
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 2 * 1024 * 1024)
        self.assertEqual(self.qbit.mode, 1)
        self.assertEqual(self.sab.limit, "2M")
        lowered = {"beefy-promoter.service", "clamav-scan.service", "docker-clamavid.scope", "docker-movie-qbittorrentid.scope"}
        self.assertEqual(self.sd.weights, {u: (10, 10) for u in lowered})
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["applied"]["downloads_slow"]["qbit"]["alt_dl_limit"], 10240)
        self.assertEqual(saved["applied"]["downloads_slow"]["qbit"]["alt_up_limit"], 10240)
        self.assertEqual(saved["applied"]["downloads_slow"]["qbit"]["mode"], 0)
        self.assertEqual(saved["applied"]["downloads_slow"]["sab"]["speedlimit"], "0")
        self.assertEqual(saved["applied"]["background_low"]["units"], {u: [100, 100] for u in lowered})
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o600)
        self.assertEqual(fix.active(), {"downloads_slow": True, "background_low": True})
        self.assertEqual(self.counted, ["downloads_slow", "background_low"])

    def test_upload_limit_unchanged(self):
        for up in (0, 5_000_000):
            with self.subTest(up_limit=up):
                self.state.unlink(missing_ok=True)
                qbit = FakeQbit(up_limit=up, alt_up=10240)
                self.make(qbit=qbit).apply(1.0)
                self.assertEqual(qbit.prefs_["alt_up_limit"], up)
                self.assertEqual(qbit.prefs_["up_limit"], up)

    def test_revert_restores_saved_values(self):
        fix = self.make()
        fix.apply(1.0)
        self.assertEqual(fix.revert(), ["downloads_slow", "background_low"])
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 10240)
        self.assertEqual(self.qbit.prefs_["alt_up_limit"], 10240)
        self.assertEqual(self.qbit.mode, 0)
        self.assertEqual(self.sab.limit, "0")
        self.assertEqual(set(self.sd.weights.values()), {(100, 100)})
        self.assertEqual(len(self.sd.weights), 4)
        self.assertEqual(fix.active(), {"downloads_slow": False, "background_low": False})
        self.assertEqual(fix.revert(), [])

    def test_alt_mode_already_on_keeps_owner_limits(self):
        for alt_dl, expect in ((10240, 10240), (0, 2097152), (8 * 1024 * 1024, 2097152)):
            with self.subTest(alt_dl=alt_dl):
                self.state.unlink(missing_ok=True)
                qbit = FakeQbit(up_limit=0, alt_dl=alt_dl, alt_up=4096, mode=1)
                fix = self.make(qbit=qbit)
                fix.apply(1.0)
                self.assertEqual(qbit.mode, 1)
                self.assertEqual(qbit.prefs_["alt_dl_limit"], expect)
                self.assertEqual(qbit.prefs_["alt_up_limit"], 4096)
                qbit.prefs_["alt_up_limit"] = 5555
                fix.revert()
                self.assertEqual(qbit.mode, 1)
                self.assertEqual(qbit.prefs_["alt_dl_limit"], alt_dl)
                self.assertEqual(qbit.prefs_["alt_up_limit"], 5555)

    def test_configured_weight_restored(self):
        self.sd.preset["clamav-scan.service"] = (20, 100)
        self.sd.preset["docker-clamavid.scope"] = (21, 50)
        fix = self.make()
        fix.apply(1.0)
        self.assertEqual(self.sd.weights["clamav-scan.service"], (10, 10))
        fix.revert()
        self.assertEqual(self.sd.weights["clamav-scan.service"], (20, 100))
        self.assertEqual(self.sd.weights["docker-clamavid.scope"], (21, 50))
        self.assertEqual(self.sd.weights["beefy-promoter.service"], (100, 100))

    def test_crash_restore_uses_saved_weights(self):
        self.sd.preset["clamav-scan.service"] = (20, 100)
        self.make().apply(1.0)
        self.make().restore_from_state()
        self.assertEqual(self.sd.weights["clamav-scan.service"], (20, 100))
        self.assertEqual(self.sd.weights["beefy-promoter.service"], (100, 100))

    def test_unreadable_weights_skip_target(self):
        self.sd.unreadable.add("clamav-scan.service")
        fix = self.make()
        fix.apply(1.0)
        self.assertNotIn("clamav-scan.service", self.sd.weights)
        self.assertTrue(any("clamav-scan.service" in m for m in self.logs))

    def test_later_apply_retries_failed_parts(self):
        self.qbit.down = True
        self.sd.fail.add("beefy-promoter.service")
        fix = self.make()
        self.assertEqual(fix.apply(1.0), ["downloads_slow", "background_low"])
        sab_sets = list(self.sab.sets)
        self.qbit.down = False
        self.sd.fail.clear()
        self.assertEqual(fix.apply(2.0), [])
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 2097152)
        self.assertEqual(self.qbit.mode, 1)
        self.assertEqual(self.sd.weights["beefy-promoter.service"], (10, 10))
        self.assertEqual(self.sab.sets, sab_sets)
        self.assertEqual([c for c in self.sd.calls if c[0] == "clamav-scan.service"], [("clamav-scan.service", 10, 10)])
        self.assertEqual(self.counted, ["downloads_slow", "background_low"])
        fix.revert()
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 10240)
        self.assertEqual(self.qbit.mode, 0)
        self.assertEqual(self.sd.weights["beefy-promoter.service"], (100, 100))

    def test_failed_qbit_write_retried_without_resaving(self):
        orig = self.qbit.set_prefs
        self.qbit.set_prefs = lambda v: (_ for _ in ()).throw(OSError("timeout"))
        fix = self.make()
        fix.apply(1.0)
        self.qbit.set_prefs = orig
        self.qbit.prefs_["alt_dl_limit"] = 999
        fix.apply(2.0)
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 2097152)
        fix.revert()
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 10240)

    def test_sab_revert_uses_absolute_limit(self):
        sab = FakeSab(limit="100", limit_abs="5242880")
        fix = self.make(sab=sab)
        fix.apply(1.0)
        fix.revert()
        self.assertEqual(sab.sets, ["2M", "5242880"])

    def test_restore_after_crash(self):
        self.make().apply(1.0)
        fresh = self.make()
        self.assertEqual(fresh.active(), {"downloads_slow": True, "background_low": True})
        self.assertEqual(fresh.restore_from_state(), ["downloads_slow", "background_low"])
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 10240)
        self.assertEqual(self.qbit.mode, 0)
        self.assertEqual(self.sab.limit, "0")
        self.assertEqual(set(self.sd.weights.values()), {(100, 100)})
        self.assertEqual(json.loads(self.state.read_text())["applied"], {})
        self.assertEqual(self.make().restore_from_state(), [])

    def test_restore_without_state_file(self):
        self.assertEqual(self.make().restore_from_state(), [])
        self.assertEqual(self.sd.calls, [])

    def test_apply_idempotent(self):
        fix = self.make()
        fix.apply(1.0)
        calls, sets = list(self.sd.calls), list(self.sab.sets)
        self.assertEqual(fix.apply(2.0), [])
        self.assertEqual(self.sd.calls, calls)
        self.assertEqual(self.sab.sets, sets)
        self.assertEqual(self.qbit.mode, 1)
        self.assertEqual(self.counted, ["downloads_slow", "background_low"])

    def test_only_running_units_touched(self):
        sd = FakeSystemd(active={"beefy-promoter.service", "other.service"}, running={"clamav", "jellyfin"})
        fix = self.make(sd=sd)
        fix.apply(1.0)
        self.assertEqual(set(sd.weights), {"beefy-promoter.service", "docker-clamavid.scope"})
        sd.active.add("beefy-mover.service")
        fix.revert()
        self.assertEqual({u for u, c, i in sd.calls if c == 100}, {"beefy-promoter.service", "docker-clamavid.scope"})

    def test_vanished_scope_on_revert_is_fine(self):
        fix = self.make()
        fix.apply(1.0)
        self.sd.gone.add("docker-clamavid.scope")
        self.assertEqual(fix.revert(), ["downloads_slow", "background_low"])
        self.assertFalse(fix.active()["background_low"])

    def test_adapter_failure_logged_not_fatal(self):
        self.qbit.down = True
        self.sd.fail.add("beefy-promoter.service")
        fix = self.make()
        self.assertEqual(fix.apply(1.0), ["downloads_slow", "background_low"])
        self.assertEqual(self.sab.limit, "2M")
        self.assertEqual(self.sd.weights.get("clamav-scan.service"), (10, 10))
        self.assertTrue(any("qbit" in m.lower() for m in self.logs))
        self.assertTrue(any("beefy-promoter.service" in m for m in self.logs))
        saved = json.loads(self.state.read_text())["applied"]
        self.assertNotIn("qbit", saved["downloads_slow"])
        self.assertNotIn("beefy-promoter.service", saved["background_low"]["units"])
        self.qbit.down = False
        fix.revert()
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 10240)

    def test_all_clients_down_applies_nothing(self):
        self.qbit.down = self.sab.down = True
        sd = FakeSystemd()
        fix = self.make(sd=sd)
        self.assertEqual(fix.apply(1.0), [])
        self.assertEqual(fix.active(), {"downloads_slow": False, "background_low": False})
        self.assertEqual(self.counted, [])

    def test_failed_revert_stays_for_retry(self):
        fix = self.make()
        fix.apply(1.0)
        self.sab.down = True
        self.assertEqual(fix.revert(), ["background_low"])
        self.assertTrue(fix.active()["downloads_slow"])
        self.assertEqual(self.qbit.prefs_["alt_dl_limit"], 10240)
        self.sab.down = False
        self.assertEqual(self.make().restore_from_state(), ["downloads_slow"])
        self.assertEqual(self.sab.limit, "0")
        self.assertEqual(self.qbit.mode, 0)

    def test_corrupt_state_file_ignored(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("{not json")
        self.assertEqual(self.make().restore_from_state(), [])

    def test_download_speeds(self):
        self.assertEqual(self.make().download_speeds(), (1000, 2048))
        self.sab.down = True
        self.assertEqual(self.make().download_speeds(), (1000, None))


class FakeHttp:
    def __init__(self, routes, need_login=False):
        self.routes, self.need_login, self.calls = routes, need_login, []

    def __call__(self, url, data=None):
        self.calls.append((url, data))
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/auth/login"):
            self.need_login = False
            return b"Ok."
        if self.need_login:
            raise af.HttpError(403, url)
        body = self.routes.get(path, b"")
        return body(url, data) if callable(body) else body


class QbitAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pw = pathlib.Path(self.tmp.name) / "pw"
        self.pw.write_text("secret\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_logs_in_on_403_and_reads(self):
        http = FakeHttp({"/api/v2/app/preferences": b'{"alt_dl_limit": 1}', "/api/v2/transfer/speedLimitsMode": b"1",
                         "/api/v2/transfer/info": b'{"dl_info_speed": 42}'}, need_login=True)
        q = af.Qbit("http://127.0.0.1:8080", self.pw, http)
        self.assertEqual(q.prefs(), {"alt_dl_limit": 1})
        self.assertEqual(q.speed_mode(), 1)
        self.assertEqual(q.dl_speed(), 42)
        login = [d for u, d in http.calls if u.endswith("/auth/login")]
        self.assertEqual(len(login), 1)
        self.assertEqual(urllib.parse.parse_qs(login[0].decode()), {"username": ["admin"], "password": ["secret"]})

    def test_writes(self):
        http = FakeHttp({})
        q = af.Qbit("http://127.0.0.1:8080", self.pw, http)
        q.set_prefs({"alt_dl_limit": 2097152})
        q.toggle_speed_mode()
        (u1, d1), (u2, d2) = http.calls
        self.assertTrue(u1.endswith("/api/v2/app/setPreferences"))
        self.assertEqual(json.loads(urllib.parse.parse_qs(d1.decode())["json"][0]), {"alt_dl_limit": 2097152})
        self.assertTrue(u2.endswith("/api/v2/transfer/toggleSpeedLimitsMode"))
        self.assertIsNotNone(d2)

    def test_refused_login_raises(self):
        def http(url, data=None):
            if url.endswith("/auth/login"):
                return b"Fails."
            raise af.HttpError(403, url)
        with self.assertRaises(af.HttpError):
            af.Qbit("http://127.0.0.1:8080", self.pw, http).prefs()


class SabAdapterTest(unittest.TestCase):
    def test_endpoints(self):
        def queue(url, data):
            return b'{"queue": {"speedlimit": "50", "speedlimit_abs": "1048576", "kbpersec": "2.00"}}'
        http = FakeHttp({"/api": queue})
        s = af.Sab("http://127.0.0.1:8081", "KEY", http)
        self.assertEqual(s.speedlimit(), ("50", "1048576"))
        self.assertEqual(s.dl_speed(), 2048)
        http.routes["/api"] = b'{"status": true}'
        s.set_speedlimit("20")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(http.calls[-1][0]).query)
        self.assertEqual(q["mode"], ["config"])
        self.assertEqual(q["name"], ["speedlimit"])
        self.assertEqual(q["value"], ["20"])
        self.assertEqual(q["apikey"], ["KEY"])


class SystemdAdapterTest(unittest.TestCase):
    def test_commands(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(cmd, 0 if cmd[2] == "a.service" else 3, "", "")
            if cmd[:4] == ["sudo", "-n", "docker", "inspect"]:
                if cmd[-1] == "up":
                    return subprocess.CompletedProcess(cmd, 0, "true abc123\n", "")
                if cmd[-1] == "stopped":
                    return subprocess.CompletedProcess(cmd, 0, "false def456\n", "")
                return subprocess.CompletedProcess(cmd, 1, "", "No such object")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        sd = af.Systemd(run)
        self.assertEqual(sd.active_units(["a.service", "b.service"]), ["a.service"])
        self.assertEqual(sd.container_scopes(["up", "stopped", "missing"]), ["docker-abc123.scope"])
        sd.set_weight("a.service", 20, 100)
        self.assertEqual(calls[-1], ["sudo", "-n", "systemctl", "set-property", "--runtime", "a.service", "CPUWeight=20", "IOWeight=100"])

    def test_weights_of(self):
        def run(cmd, **kw):
            self.assertEqual(cmd[:6], ["systemctl", "show", "-p", "CPUWeight", "-p", "IOWeight"])
            out = {"set.service": "CPUWeight=20\nIOWeight=[not set]\n", "both.service": "IOWeight=50\nCPUWeight=21\n",
                   "empty.service": "CPUWeight=\nIOWeight=\n"}[cmd[-1]]
            return subprocess.CompletedProcess(cmd, 0, out, "")
        sd = af.Systemd(run)
        self.assertEqual(sd.weights_of("set.service"), (20, 100))
        self.assertEqual(sd.weights_of("both.service"), (21, 50))
        self.assertEqual(sd.weights_of("empty.service"), (100, 100))

    def test_weights_of_failure_raises(self):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        with self.assertRaises(RuntimeError):
            af.Systemd(run).weights_of("x.service")

    def test_set_weight_failure_raises(self):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "Unit not found")
        with self.assertRaises(RuntimeError):
            af.Systemd(run).set_weight("docker-x.scope", 100, 100)


class ListsTest(unittest.TestCase):
    def test_exact_lists(self):
        self.assertEqual(tuple(af.UNITS), UNITS)
        self.assertEqual(tuple(af.CONTAINERS), CONTAINERS)
        self.assertEqual(af.QBIT_ALT_DL, 2097152)
        self.assertEqual(af.SAB_LIMIT, "2M")


if __name__ == "__main__":
    unittest.main()
