#!/usr/bin/env python3
"""Per-timer heartbeat for the node-exporter textfile collector.

Writes the last successful finish and last result of every enabled timer's service (system and user scope) plus the list of failed units, so a job that stops running or fails silently becomes an alert (prometheus/rules/job-heartbeat.yml). The last success is kept in a state file because systemd forgets it at reboot (beefy powers off every night).

Usage: job_heartbeat.py --host fastpi --out /var/lib/node-exporter/textfile/job_heartbeat.prom --state ~/.local/state/job-heartbeat/state.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

PROPS_TIMER = "Id,Unit,ActiveState,TimersCalendar,TimersMonotonic,RandomizedDelayUSec"
PROPS_SERVICE = "Id,Result,ActiveState,ExecMainCode,ExecMainStatus,ExecMainExitTimestampMonotonic"
MULTI = {"TimersCalendar", "TimersMonotonic"}
UNITS = {"us": 1e-6, "usec": 1e-6, "ms": 1e-3, "msec": 1e-3, "s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600, "hr": 3600, "d": 86400, "w": 604800, "month": 2629800, "y": 31557600}
ITERATIONS = 10
SCOPES = [("system", ["systemctl"]), ("user", ["systemctl", "--user"])]


def parse_show(text):
    blocks, cur = [], {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        key, _, value = line.partition("=")
        if key in MULTI:
            cur.setdefault(key, []).append(value)
        else:
            cur[key] = value
    return blocks


def parse_duration(text):
    text = text.strip()
    if text in ("", "0", "infinity"):
        return 0 if text != "infinity" else float("inf")
    total = 0.0
    for num, unit in re.findall(r"([0-9.]+)\s*([a-z]+)", text):
        total += float(num) * UNITS[unit]
    return total


def gap_from_analyze(text):
    stamps = []
    for line in text.splitlines():
        if re.match(r"\s*(Next elapse|Iteration #\d+):", line):
            m = re.search(r"(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)", line)
            if m:
                y, mo, d, h, mi, s = map(int, m.groups())
                stamps.append(time.mktime((y, mo, d, h, mi, s, 0, 0, -1)))
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    return max(gaps) if gaps else None


def max_age(period, randomized):
    return period + max(900, period / 4) + randomized


def calendar_spec(entry):
    m = re.search(r"OnCalendar=(.*?) ;", entry)
    return m.group(1).strip() if m else None


def timer_period(props, run, gaps):
    periods = []
    for entry in props.get("TimersMonotonic", []):
        m = re.search(r"(OnUnitActiveUSec|OnUnitInactiveUSec)=(.*?) ;", entry)
        if m:
            periods.append(parse_duration(m.group(2)))
    cal = []
    for entry in props.get("TimersCalendar", []):
        spec = calendar_spec(entry)
        if not spec:
            continue
        if spec not in gaps:
            try:
                gap = gap_from_analyze(run(["systemd-analyze", "calendar", f"--iterations={ITERATIONS}", spec]))
            except (OSError, subprocess.CalledProcessError):
                gap = None
            if gap is None:
                continue
            gaps[spec] = gap
        cal.append(gaps[spec])
    periods = [p for p in periods if p > 0]
    if cal:
        periods.append(min(cal))
    return min(periods) if periods else None


def esc(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build(host, scopes, run, state, now, now_mono, boot_time, meta=True):
    gaps = dict(state.get("_gaps", {}))
    new_state = {"_gaps": gaps}
    series = {n: [] for n in ("success", "result", "first", "age", "failed")}

    for scope, base in scopes:
        old = state.get(scope, {})
        mine = new_state.setdefault(scope, {})
        timers = [b for b in parse_show(run(base + ["show", "*.timer", "--no-pager", "-p", PROPS_TIMER])) if b.get("ActiveState") == "active" and b.get("Unit", "").endswith(".service")]
        units = sorted({b["Unit"] for b in timers})
        svc = {}
        if units:
            for b in parse_show(run(base + ["show", "--no-pager", "-p", PROPS_SERVICE] + units)):
                svc[b.get("Id")] = b
        periods = {}
        for t in timers:
            p = timer_period(t, run, gaps)
            if p:
                randomized = parse_duration(t.get("RandomizedDelayUSec", "0"))
                periods[t["Unit"]] = min(periods.get(t["Unit"], float("inf")), max_age(p, randomized))
        for unit in units:
            rec = dict(old.get(unit, {}))
            rec.setdefault("first_seen", now)
            b = svc.get(unit, {})
            mono = int(b.get("ExecMainExitTimestampMonotonic", "0") or 0)
            running = b.get("ActiveState") in ("activating", "active", "deactivating", "reloading")
            ran = mono > 0 and not running
            ok = ran and b.get("Result") == "success" and b.get("ExecMainCode") == "1" and b.get("ExecMainStatus") == "0"
            failed_run = (b.get("Result", "success") != "success" or b.get("ActiveState") == "failed") or (ran and not ok)
            if ok:
                finished = now - (now_mono - mono / 1e6)
                rec["last_success"] = max(finished, rec.get("last_success", 0))
            mine[unit] = rec
            lbl = f'host="{host}",scope="{scope}",unit="{esc(unit)}"'
            series["result"].append(f"systemd_job_last_result{{{lbl}}} {1 if failed_run else 0}")
            series["first"].append(f"systemd_job_first_seen_timestamp_seconds{{{lbl}}} {rec['first_seen']:.0f}")
            if "last_success" in rec:
                series["success"].append(f"systemd_job_last_success_timestamp_seconds{{{lbl}}} {rec['last_success']:.0f}")
            if unit in periods:
                series["age"].append(f"systemd_job_max_age_seconds{{{lbl}}} {periods[unit]:.0f}")
        for line in run(base + ["list-units", "--failed", "--all", "--plain", "--no-legend", "--no-pager"]).splitlines():
            parts = line.replace("●", " ").split()
            if parts:
                series["failed"].append(f'systemd_unit_failed{{host="{host}",scope="{scope}",unit="{esc(parts[0])}"}} 1')

    out = []
    for key, name, help_ in (
        ("success", "systemd_job_last_success_timestamp_seconds", "Finish time of the last successful run of the timer's service"),
        ("result", "systemd_job_last_result", "1 if the last run of the timer's service failed, else 0"),
        ("first", "systemd_job_first_seen_timestamp_seconds", "When the heartbeat first saw the timer (age base before a first success)"),
        ("age", "systemd_job_max_age_seconds", "Longest allowed time since the last success, from the timer's schedule plus grace"),
        ("failed", "systemd_unit_failed", "Units in failed state"),
    ):
        if series[key]:
            if meta:
                out.append(f"# HELP {name} {help_}")
                out.append(f"# TYPE {name} gauge")
            out.extend(series[key])
    out.append(f'systemd_job_host_boot_time_seconds{{host="{host}"}} {boot_time:.0f}')
    out.append(f'systemd_job_updated_timestamp_seconds{{host="{host}"}} {now:.0f}')
    return "\n".join(out) + "\n", new_state


def load_state(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_atomic(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hb.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def run_cmd(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=60).stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--no-meta", action="store_true", help="leave out HELP/TYPE lines (beefy's file is merged with fastpi's by node-exporter)")
    args = ap.parse_args()
    now, now_mono = time.time(), time.monotonic()
    text, new_state = build(args.host, SCOPES, run_cmd, load_state(args.state), now, now_mono, now - time.clock_gettime(time.CLOCK_BOOTTIME), not args.no_meta)
    write_atomic(args.state, json.dumps(new_state, indent=1, sort_keys=True))
    write_atomic(args.out, text)


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError) as e:
        sys.exit(f"job_heartbeat: {e}")
