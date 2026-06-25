#!/usr/bin/env python3
"""Beefy idle-watcher — powers beefy off (S5) after sustained inactivity.

A host systemd service (root). Every SAMPLE_INTERVAL it evaluates four probes —
inbound service connections, interactive SSH, background CPU/net, disk I/O — plus
a manual inhibit file. When all are idle for IDLE_MINUTES it powers off (or, in
dry-run, just logs). WOL stays armed across poweroff, so a request re-wakes beefy
via fastpi's Traefik path. See 8-Idle-Watcher.md for the design.

Stdlib only. The parser/decision functions are pure (unit-tested); the I/O glue
is a thin layer that reads /proc, ss, who and calls systemctl poweroff.
"""
import os
import subprocess
import sys
import time

# ----------------------------------------------------------------------------
# Pure functions (unit-tested in test_beefy_idle_watcher.py)
# ----------------------------------------------------------------------------

def _is_loopback(host):
    hc = host.strip("[]")
    return hc.startswith("127.") or hc == "::1" or "%lo" in hc


def parse_listen_ports(ss_ltn_text):
    """Non-loopback listening ports from `ss -ltnH` output."""
    ports = set()
    for line in ss_ltn_text.splitlines():
        for tok in line.split():
            host, sep, port = tok.rpartition(":")
            if not sep or not host or not port.isdigit():
                continue
            if _is_loopback(host):
                continue
            ports.add(int(port))
    return ports


def count_inbound(ss_estab_text, listen_ports, exclude_ports):
    """Count established conns whose LOCAL port is a service port we host.

    `ss -tnH state established` lines carry two addr:port tokens (local, peer) in
    that order. Inbound to one of our listening ports (minus excluded, e.g. 22)
    means someone is using a service.
    """
    n = 0
    for line in ss_estab_text.splitlines():
        toks = [t for t in line.split()
                if t.rpartition(":")[2].isdigit() and t.rpartition(":")[0]]
        if not toks:
            continue
        lport = int(toks[0].rpartition(":")[2])
        if lport in listen_ports and lport not in exclude_ports:
            n += 1
    return n


def interactive_sessions(who_text):
    """Number of interactive login sessions (`who` lines)."""
    return sum(1 for ln in who_text.splitlines() if ln.strip())


def _cpu_idle_total(stat_text):
    for line in stat_text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            nums = [int(x) for x in parts[1:]]
            idle = nums[3] + nums[4]            # idle + iowait
            return idle, sum(nums)
    return 0, 0


def cpu_busy_pct(stat_a, stat_b):
    """Whole-host CPU busy %% between two /proc/stat snapshots."""
    idle_a, tot_a = _cpu_idle_total(stat_a)
    idle_b, tot_b = _cpu_idle_total(stat_b)
    d_tot = tot_b - tot_a
    if d_tot <= 0:
        return 0.0
    d_busy = d_tot - (idle_b - idle_a)
    return d_busy / d_tot * 100.0


def _nic_rx_tx(dev_text, nic):
    for line in dev_text.splitlines():
        name, sep, rest = line.partition(":")
        if sep and name.strip() == nic:
            f = rest.split()
            return int(f[0]), int(f[8])         # rx_bytes, tx_bytes
    return 0, 0


def net_kbps(dev_a, dev_b, nic, secs):
    """NIC throughput (rx+tx) in kB/s between two /proc/net/dev snapshots."""
    if secs <= 0:
        return 0.0
    rx_a, tx_a = _nic_rx_tx(dev_a, nic)
    rx_b, tx_b = _nic_rx_tx(dev_b, nic)
    return ((rx_b - rx_a) + (tx_b - tx_a)) / 1000.0 / secs


def _disk_sectors(ds_text, disks):
    total = 0
    want = set(disks)
    for line in ds_text.splitlines():
        p = line.split()
        if len(p) > 9 and p[2] in want:
            total += int(p[5]) + int(p[9])      # sectors_read + sectors_written
    return total


def disk_kbps(ds_a, ds_b, disks, secs):
    """Data-disk I/O (read+write) in kB/s between two /proc/diskstats snapshots.

    Sectors are 512 bytes.
    """
    if secs <= 0:
        return 0.0
    delta = _disk_sectors(ds_b, disks) - _disk_sectors(ds_a, disks)
    return delta * 512 / 1000.0 / secs


def evaluate(probes, inhibit):
    """(busy, reasons) from per-probe booleans + the inhibit flag."""
    reasons = []
    if inhibit:
        reasons.append("inhibit")
    reasons += [k for k, v in probes.items() if v]
    return (bool(reasons), reasons)


def update_idle(busy, idle_since, now):
    return now if busy else idle_since


def should_sleep(idle_since, now, idle_minutes):
    return (now - idle_since) >= idle_minutes * 60


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

_DEFAULTS = {
    "DRY_RUN": True,
    "IDLE_MINUTES": 15,
    "SAMPLE_INTERVAL": 60,
    "CPU_BUSY_PCT": 15.0,
    "NET_BUSY_KBPS": 200.0,
    "DISK_BUSY_KBPS": 2000.0,
    "PRIMARY_NIC": "enp6s0",
    "DATA_DISKS": ["sda", "sdb", "sdc"],
    "EXCLUDE_PORTS": {22},
    "INHIBIT_FILE": "/run/beefy-keep-awake",
}


def load_config(env):
    """Merge environment (systemd EnvironmentFile) over defaults, typed."""
    cfg = dict(_DEFAULTS)
    if "DRY_RUN" in env:
        cfg["DRY_RUN"] = env["DRY_RUN"].strip() not in ("0", "false", "False", "")
    for key in ("IDLE_MINUTES", "SAMPLE_INTERVAL"):
        if key in env:
            cfg[key] = int(env[key])
    for key in ("CPU_BUSY_PCT", "NET_BUSY_KBPS", "DISK_BUSY_KBPS"):
        if key in env:
            cfg[key] = float(env[key])
    if "PRIMARY_NIC" in env:
        cfg["PRIMARY_NIC"] = env["PRIMARY_NIC"].strip()
    if "DATA_DISKS" in env:
        cfg["DATA_DISKS"] = env["DATA_DISKS"].split()
    if "EXCLUDE_PORTS" in env:
        cfg["EXCLUDE_PORTS"] = {int(p) for p in env["EXCLUDE_PORTS"].split()}
    if "INHIBIT_FILE" in env:
        cfg["INHIBIT_FILE"] = env["INHIBIT_FILE"].strip()
    return cfg


# ----------------------------------------------------------------------------
# I/O glue
# ----------------------------------------------------------------------------

def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception as e:                       # never let a probe crash the loop
        log("WARN: %s failed: %s" % (" ".join(cmd), e))
        return ""


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def log(msg):
    print(msg, flush=True)


def _snapshot():
    return {
        "t": time.time(),
        "stat": _read("/proc/stat"),
        "dev": _read("/proc/net/dev"),
        "ds": _read("/proc/diskstats"),
    }


def main():
    cfg = load_config(os.environ)
    log("beefy-idle-watcher start: dry_run=%s idle=%dm interval=%ds "
        "cpu>%.0f%% net>%.0fkB/s disk>%.0fkB/s disks=%s nic=%s"
        % (cfg["DRY_RUN"], cfg["IDLE_MINUTES"], cfg["SAMPLE_INTERVAL"],
           cfg["CPU_BUSY_PCT"], cfg["NET_BUSY_KBPS"], cfg["DISK_BUSY_KBPS"],
           ",".join(cfg["DATA_DISKS"]), cfg["PRIMARY_NIC"]))

    prev = _snapshot()
    idle_since = time.time()
    time.sleep(cfg["SAMPLE_INTERVAL"])

    while True:
        now = time.time()
        cur = _snapshot()
        secs = cur["t"] - prev["t"]

        cpu = cpu_busy_pct(prev["stat"], cur["stat"])
        net = net_kbps(prev["dev"], cur["dev"], cfg["PRIMARY_NIC"], secs)
        disk = disk_kbps(prev["ds"], cur["ds"], cfg["DATA_DISKS"], secs)
        listen = parse_listen_ports(_run(["ss", "-ltnH"]))
        conns = count_inbound(_run(["ss", "-tnH", "state", "established"]),
                              listen, cfg["EXCLUDE_PORTS"])
        ssh = interactive_sessions(_run(["who"]))
        inhibit = os.path.exists(cfg["INHIBIT_FILE"])

        probes = {
            "conns": conns > 0,
            "ssh": ssh > 0,
            "cpu": cpu > cfg["CPU_BUSY_PCT"],
            "net": net > cfg["NET_BUSY_KBPS"],
            "disk": disk > cfg["DISK_BUSY_KBPS"],
        }
        busy, reasons = evaluate(probes, inhibit)
        idle_since = update_idle(busy, idle_since, now)
        idle_min = (now - idle_since) / 60.0

        log("idle=%4.1fm conns=%d ssh=%d cpu=%2.0f%% net=%.0fkB/s disk=%.0fkB/s "
            "inhibit=%d -> %s%s"
            % (idle_min, conns, ssh, cpu, net, disk, int(inhibit),
               "BUSY" if busy else "idle",
               (" (" + ",".join(reasons) + ")") if reasons else ""))

        if not busy and should_sleep(idle_since, now, cfg["IDLE_MINUTES"]):
            if cfg["DRY_RUN"]:
                log("WOULD power off now (idle %d min, dry-run)" % cfg["IDLE_MINUTES"])
            else:
                log("idle %d min -> systemctl poweroff" % cfg["IDLE_MINUTES"])
                subprocess.run(["systemctl", "poweroff"])
                return

        prev = cur
        time.sleep(cfg["SAMPLE_INTERVAL"])


if __name__ == "__main__":
    sys.exit(main())
