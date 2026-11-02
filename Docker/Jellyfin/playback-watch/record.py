import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

KINDS = ("stall", "silent", "slow_transcode")
ACTIONS = ("downloads_slow", "background_low")
UNITS = ("subtitle-translate.service", "clamav-hostscan.service", "clamav-scan.service",
         "beefy-mover.service", "original-audio.service", "beefy-promoter.service")
CONTAINERS = ("subtitle-libretranslate", "subtitle-lingarr", "clamav", "clamav-queue",
              "movie-sabnzbd", "movie-qbittorrent")
DISKS = ("sda", "sdb", "sdc")
MAX_BYTES = 5 * 1024 * 1024
KEEP = 3
WINDOW = 0.5


def _cpu_times(text):
    for line in text.splitlines():
        if line.startswith("cpu "):
            vals = [int(x) for x in line.split()[1:]]
            return sum(vals[:8]), vals[3] + vals[4]
    raise ValueError("no cpu line")


def _io_ticks(text):
    ticks = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) > 12 and f[2] in DISKS:
            ticks[f[2]] = int(f[12])
    return ticks


def _probe(fn):
    try:
        return fn()
    except Exception:
        return None


def context(run=subprocess.run, read=Path.read_text, sleep=time.sleep, qbit_speed=None, sab_speed=None, autofix_active=None):
    def load():
        return [float(x) for x in read(Path("/proc/loadavg")).split()[:3]]

    def sample():
        return read(Path("/proc/stat")), read(Path("/proc/diskstats"))

    cpu = disk = None
    first = _probe(sample)
    if first:
        sleep(WINDOW)
        second = _probe(sample)
        if second:
            def calc_cpu():
                t1, i1 = _cpu_times(first[0])
                t2, i2 = _cpu_times(second[0])
                return 100.0 * (1 - (i2 - i1) / (t2 - t1))

            def calc_disk():
                a, b = _io_ticks(first[1]), _io_ticks(second[1])
                busy = [(b[d] - a[d]) / (WINDOW * 1000) * 100 for d in b if d in a]
                return min(100.0, max(busy))

            cpu, disk = _probe(calc_cpu), _probe(calc_disk)

    def units():
        out = []
        for u in UNITS:
            r = run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                out.append(u)
        return out

    def containers():
        r = run(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise RuntimeError("docker ps failed")
        names = r.stdout.split()
        return [c for c in CONTAINERS if c in names]

    return {
        "load": _probe(load), "cpu_percent": cpu, "disk_busy_percent": disk,
        "units": _probe(units), "containers": _probe(containers),
        "qbit_speed": qbit_speed, "sab_speed": sab_speed, "autofix_active": autofix_active,
    }


def _rotate(path):
    path = Path(path)
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
    except FileNotFoundError:
        return
    for i in range(KEEP, 0, -1):
        src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
        if i == KEEP:
            path.with_name(f"{path.name}.{i}").unlink(missing_ok=True)
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{i}"))


def _append(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(path)
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def append_incident(path, incident, ctx, clock=time.time):
    _append(path, {**incident, "time": clock(), "context": ctx})


def append_summary(path, summary, clock=time.time):
    _append(path, {**summary, "kind": "summary", "time": clock()})


class Counters:
    def __init__(self, path):
        self.path = Path(path)
        self.incidents = {}
        self.stall_seconds = {}
        self.autofix = {a: 0 for a in ACTIONS}
        self.last_incident = 0.0
        try:
            data = json.loads(self.path.read_text())
            for k, c, n in data.get("incidents", []):
                self.incidents[(k, c)] = n
            self.stall_seconds = {k: float(v) for k, v in data.get("stall_seconds", {}).items()}
            for a, n in data.get("autofix", {}).items():
                self.autofix[a] = n
            self.last_incident = float(data.get("last_incident", 0.0))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def inc_incident(self, kind, client, seconds, now):
        self.incidents[(kind, client)] = self.incidents.get((kind, client), 0) + 1
        if kind == "stall":
            self.stall_seconds[client] = self.stall_seconds.get(client, 0.0) + seconds
        self.last_incident = now
        self.save()

    def inc_autofix(self, action):
        self.autofix[action] = self.autofix.get(action, 0) + 1
        self.save()

    def save(self):
        data = {
            "incidents": [[k, c, n] for (k, c), n in sorted(self.incidents.items())],
            "stall_seconds": self.stall_seconds, "autofix": self.autofix, "last_incident": self.last_incident,
        }
        _atomic_write(self.path, json.dumps(data, sort_keys=True))


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(v):
    return repr(int(v)) if float(v).is_integer() else repr(float(v))


def write_prom(path, counters, active_by_method, autofix_active, last_poll):
    lines = []

    def metric(name, kind, help_, samples):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels)
            lines.append(f"{name}{{{lab}}} {_num(value)}" if lab else f"{name} {_num(value)}")

    clients = sorted({c for _, c in counters.incidents} | set(counters.stall_seconds))
    metric("jellyfin_playback_active", "gauge", "Playing sessions by play method.",
           [([("method", m)], n) for m, n in sorted(active_by_method.items())])
    metric("jellyfin_playback_incidents_total", "counter", "Playback incidents by kind and client.",
           [([("kind", k), ("client", c)], counters.incidents.get((k, c), 0)) for c in clients for k in KINDS])
    metric("jellyfin_playback_stall_seconds_total", "counter", "Seconds of detected stall by client.",
           [([("client", c)], counters.stall_seconds.get(c, 0.0)) for c in clients])
    metric("jellyfin_playback_autofix_total", "counter", "Auto-fix applications by action.",
           [([("action", a)], counters.autofix.get(a, 0)) for a in ACTIONS])
    metric("jellyfin_playback_autofix_active", "gauge", "Auto-fix currently applied (1) or not (0).",
           [([("action", a)], 1 if autofix_active.get(a) else 0) for a in ACTIONS])
    metric("jellyfin_playback_last_incident_timestamp_seconds", "gauge", "Unix time of the last incident, 0 if none.",
           [([], counters.last_incident)])
    metric("jellyfin_playback_watch_last_poll_timestamp_seconds", "gauge", "Unix time of the last successful poll.",
           [([], last_poll)])
    _atomic_write(path, "\n".join(lines) + "\n")


def _read_rows(path):
    path = Path(path)
    rows = []
    for p in [path.with_name(f"{path.name}.{i}") for i in range(KEEP, 0, -1)] + [path]:
        try:
            text = p.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def _short_reason(tc):
    reasons = (tc or {}).get("reasons")
    if isinstance(reasons, list):
        reasons = ",".join(str(r) for r in reasons)
    return (reasons or "-")[:40]


def _pct(v):
    return "-" if v is None else f"{v:.0f}%"


def report(path, since_seconds, now=time.time):
    cutoff = now() - since_seconds
    rows = sorted((r for r in _read_rows(path) if r.get("kind") in KINDS and r.get("time", 0) >= cutoff),
                  key=lambda r: r["time"])
    header = ["time", "kind", "seconds", "client", "device", "item", "method", "reason", "cpu", "disk"]
    table = [header]
    for r in rows:
        ctx = r.get("context") or {}
        table.append([
            datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M:%S"), r["kind"], f"{r.get('seconds', 0):.1f}",
            str(r.get("client") or "-"), str(r.get("device") or "-"), str(r.get("item") or "-"),
            str(r.get("play_method") or "-"), _short_reason(r.get("transcode")),
            _pct(ctx.get("cpu_percent")), _pct(ctx.get("disk_busy_percent")),
        ])
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    out = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in table]
    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}
    stall = sum(r.get("seconds", 0) for r in rows if r["kind"] == "stall")
    out.append("")
    out.append(f"{len(rows)} incidents: " + ", ".join(f"{k} {n}" for k, n in counts.items()))
    out.append(f"stall seconds total: {stall:.1f}")
    return "\n".join(out)
