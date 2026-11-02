import http.cookiejar
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from record import CONTAINERS, UNITS

QBIT_ALT_DL = 2 * 1024 * 1024
SAB_LIMIT = "2M"
LOW, DEFAULT = 10, 100


class HttpError(Exception):
    def __init__(self, code, url):
        super().__init__(f"{url.split('?')[0]}: HTTP {code}")
        self.code = code


def make_http(timeout=10):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(url, data=None):
        try:
            with opener.open(urllib.request.Request(url, data), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            e.close()
            raise HttpError(e.code, url) from None
    return call


def _json(body):
    return json.loads(body) if body and body.strip() else None


class Qbit:
    def __init__(self, base, password_file, http, user="admin"):
        self.api = base.rstrip("/") + "/api/v2"
        self.password_file = Path(password_file)
        self.http = http
        self.user = user

    def _login(self):
        form = urllib.parse.urlencode({"username": self.user, "password": self.password_file.read_text().strip()}).encode()
        if self.http(self.api + "/auth/login", form).strip() == b"Fails.":
            raise HttpError(403, self.api + "/auth/login")

    def _call(self, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            return self.http(self.api + path, body)
        except HttpError as e:
            if e.code != 403:
                raise
            self._login()
            return self.http(self.api + path, body)

    def prefs(self):
        return _json(self._call("/app/preferences"))

    def set_prefs(self, values):
        self._call("/app/setPreferences", {"json": json.dumps(values)})

    def speed_mode(self):
        return int(self._call("/transfer/speedLimitsMode").strip())

    def toggle_speed_mode(self):
        self._call("/transfer/toggleSpeedLimitsMode", {})

    def dl_speed(self):
        return int(_json(self._call("/transfer/info"))["dl_info_speed"])


class Sab:
    def __init__(self, base, api_key, http):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.http = http

    def _call(self, params):
        query = urllib.parse.urlencode({**params, "output": "json", "apikey": self.api_key})
        return _json(self.http(f"{self.base}/api?{query}"))

    def _queue(self):
        return self._call({"mode": "queue", "limit": "1"})["queue"]

    def speedlimit(self):
        q = self._queue()
        return str(q.get("speedlimit", "")), str(q.get("speedlimit_abs", ""))

    def set_speedlimit(self, value):
        self._call({"mode": "config", "name": "speedlimit", "value": value})

    def dl_speed(self):
        return int(float(self._queue()["kbpersec"]) * 1024)


class Systemd:
    def __init__(self, run=subprocess.run):
        self.run = run

    def active_units(self, units):
        return [u for u in units if self.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=10).returncode == 0]

    def container_scopes(self, names):
        scopes = []
        for n in names:
            r = self.run(["sudo", "-n", "docker", "inspect", "-f", "{{.State.Running}} {{.Id}}", n],
                         capture_output=True, text=True, timeout=20)
            f = r.stdout.split()
            if r.returncode == 0 and len(f) == 2 and f[0] == "true":
                scopes.append(f"docker-{f[1]}.scope")
        return scopes

    def weights_of(self, unit):
        r = self.run(["systemctl", "show", "-p", "CPUWeight", "-p", "IOWeight", unit], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            raise RuntimeError(f"{unit}: show failed: {r.stderr.strip()}")
        props = dict(line.partition("=")[::2] for line in r.stdout.splitlines())
        return tuple(int(v) if v.strip().isdigit() else DEFAULT for v in (props.get("CPUWeight", ""), props.get("IOWeight", "")))

    def set_weight(self, unit, cpu, io):
        r = self.run(["sudo", "-n", "systemctl", "set-property", "--runtime", unit, f"CPUWeight={cpu}", f"IOWeight={io}"],
                     capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise RuntimeError(f"{unit}: set-property failed: {r.stderr.strip()}")


class AutoFix:
    ACTIONS = ("downloads_slow", "background_low")

    def __init__(self, qbit, sab, systemd, state_path, log=print, on_action=None):
        self.qbit, self.sab, self.systemd = qbit, sab, systemd
        self.state_path = Path(state_path)
        self.log = log
        self.on_action = on_action or (lambda action: None)
        self.applied = self._load()
        self._complete = False

    def _load(self):
        try:
            applied = json.loads(self.state_path.read_text())["applied"]
            return {a: applied[a] for a in self.ACTIONS if isinstance(applied.get(a), dict)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return {}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"applied": self.applied}, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_path)

    def _try(self, what, fn):
        try:
            fn()
            return True
        except Exception as e:
            self.log(f"autofix: {what} failed: {e}")
            self._complete = False
            return False

    def complete(self):
        return self._complete and bool(self.applied)

    def active(self):
        return {a: a in self.applied for a in self.ACTIONS}

    def apply(self, now):
        was = set(self.applied)
        self._complete = True
        self._apply_downloads(now)
        self._apply_background(now)
        done = [a for a in self.ACTIONS if a in self.applied and a not in was]
        for a in done:
            self.log(f"autofix: applied {a}")
            self.on_action(a)
        return done

    def _apply_downloads(self, now):
        entry = self.applied.setdefault("downloads_slow", {"since": now})

        def qbit():
            if "qbit" not in entry:
                prefs, mode = self.qbit.prefs(), self.qbit.speed_mode()
                entry["qbit"] = {"alt_dl_limit": prefs["alt_dl_limit"], "alt_up_limit": prefs["alt_up_limit"],
                                 "up_limit": prefs["up_limit"], "mode": mode, "pending": True}
                self._save()
            saved = entry["qbit"]
            if not saved.get("pending"):
                return
            if saved["mode"] == 0:
                self.qbit.set_prefs({"alt_dl_limit": QBIT_ALT_DL, "alt_up_limit": saved["up_limit"]})
                if self.qbit.speed_mode() == 0:
                    self.qbit.toggle_speed_mode()
            else:
                cur = saved["alt_dl_limit"]
                self.qbit.set_prefs({"alt_dl_limit": QBIT_ALT_DL if cur <= 0 else min(cur, QBIT_ALT_DL)})
            saved["pending"] = False

        def sab():
            if "sab" not in entry:
                limit, limit_abs = self.sab.speedlimit()
                entry["sab"] = {"speedlimit": limit, "speedlimit_abs": limit_abs, "pending": True}
                self._save()
            if entry["sab"].get("pending"):
                self.sab.set_speedlimit(SAB_LIMIT)
                entry["sab"]["pending"] = False

        self._try("qbit downloads_slow", qbit)
        self._try("sab downloads_slow", sab)
        if "qbit" not in entry and "sab" not in entry:
            del self.applied["downloads_slow"]
        self._save()

    def _apply_background(self, now):
        try:
            targets = self.systemd.active_units(UNITS) + self.systemd.container_scopes(CONTAINERS)
        except Exception as e:
            self.log(f"autofix: background_low lookup failed: {e}")
            self._complete = False
            return
        entry = self.applied.setdefault("background_low", {"since": now, "units": {}})
        units = entry["units"]
        for unit in targets:
            if unit in units:
                continue
            try:
                units[unit] = list(self.systemd.weights_of(unit))
            except Exception as e:
                self.log(f"autofix: read weights of {unit} failed: {e}")
                self._complete = False
                continue
            self._save()
            if not self._try(f"lower {unit}", lambda u=unit: self.systemd.set_weight(u, LOW, LOW)):
                del units[unit]
        if not units:
            del self.applied["background_low"]
        self._save()

    def _revert_qbit(self, saved):
        if saved["mode"] == 0:
            self.qbit.set_prefs({"alt_dl_limit": saved["alt_dl_limit"], "alt_up_limit": saved["alt_up_limit"]})
            if self.qbit.speed_mode() == 1:
                self.qbit.toggle_speed_mode()
        else:
            self.qbit.set_prefs({"alt_dl_limit": saved["alt_dl_limit"]})

    def _revert_sab(self, saved):
        abs_ = saved.get("speedlimit_abs")
        self.sab.set_speedlimit(abs_ if abs_ not in (None, "") else saved.get("speedlimit", ""))

    def revert(self):
        self._complete = False
        if not self.applied:
            return []
        done = []
        entry = self.applied.get("downloads_slow")
        if entry is not None:
            if "qbit" in entry and self._try("qbit revert", lambda: self._revert_qbit(entry["qbit"])):
                del entry["qbit"]
            if "sab" in entry and self._try("sab revert", lambda: self._revert_sab(entry["sab"])):
                del entry["sab"]
            if "qbit" not in entry and "sab" not in entry:
                del self.applied["downloads_slow"]
                done.append("downloads_slow")
        entry = self.applied.get("background_low")
        if entry is not None:
            keep = {}
            for unit, (cpu, io) in entry.get("units", {}).items():
                if not self._try(f"restore {unit}", lambda u=unit, c=cpu, i=io: self.systemd.set_weight(u, c, i)) and not unit.endswith(".scope"):
                    keep[unit] = [cpu, io]
            entry["units"] = keep
            if not keep:
                del self.applied["background_low"]
                done.append("background_low")
        self._save()
        for a in done:
            self.log(f"autofix: reverted {a}")
        return done

    def restore_from_state(self):
        self.applied = self._load()
        return self.revert()

    def download_speeds(self):
        def get(fn):
            try:
                return fn()
            except Exception:
                return None
        return get(self.qbit.dl_speed), get(self.sab.dl_speed)
