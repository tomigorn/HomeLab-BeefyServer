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


import re

_PTS_RE = re.compile(r"sshd[\w-]*:.*@pts/\d+")


def count_interactive_ssh(ps_text):
    """Number of interactive SSH logins, from `ps -eo args`.

    This box has no utmp (systemd), so `who` is always empty. OpenSSH sets each
    per-session process title to `sshd-session: <user>@pts/N` for an interactive
    (pty) login and `<user>@notty` for non-interactive automation. Counting the
    `@pts/N` titles tracks real interactive sessions exactly and ignores
    automation SSH (`ssh host 'cmd'`), the systemd `manager` sessions, and
    non-interactive tools like VS Code Remote's server — all of which `loginctl`
    fails to distinguish on this system.
    """
    return sum(1 for ln in ps_text.splitlines() if _PTS_RE.search(ln))


_VSCODE_RE = re.compile(r"\.vscode-server/.*server-main\.js")


def vscode_remote_active(ps_text):
    """True if a VS Code Remote-SSH server is running (user connected via VS Code).

    VS Code connects over SSH non-interactively (`@notty`, missed by the @pts
    probe) and runs a node server `.vscode-server/.../out/server-main.js`. Its
    presence means the IDE is attached; treat that as in-use. VS Code's own
    `--enable-remote-auto-shutdown` exits this server when the user is away,
    releasing beefy to sleep.
    """
    return any(_VSCODE_RE.search(ln) for ln in ps_text.splitlines())


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
    delta = (rx_b - rx_a) + (tx_b - tx_a)
    return max(0, delta) / 1000.0 / secs   # clamp counter reset/re-init (negative) to 0


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
    return max(0, delta) * 512 / 1000.0 / secs   # clamp counter reset (negative) to 0


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


def start_banner(version, cfg):
    """One-line startup banner (pure) — logged once at boot so journald records
    which version + thresholds the running daemon has."""
    return ("beefy-idle-watcher v%s start: dry_run=%s idle=%dm interval=%ds "
            "cpu>%.0f%% net>%.0fkB/s disk>%.0fkB/s disks=%s nic=%s"
            % (version, cfg["DRY_RUN"], cfg["IDLE_MINUTES"], cfg["SAMPLE_INTERVAL"],
               cfg["CPU_BUSY_PCT"], cfg["NET_BUSY_KBPS"], cfg["DISK_BUSY_KBPS"],
               ",".join(cfg["DATA_DISKS"]), cfg["PRIMARY_NIC"]))


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


def _clean(raw):
    """Normalise an EnvironmentFile value.

    systemd keeps an inline `# comment` and surrounding quotes as part of the
    literal value, so strip a trailing comment, whitespace, and wrapping quotes.
    """
    v = raw.split("#", 1)[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    return v


def load_config(env):
    """Merge environment (systemd EnvironmentFile) over defaults, typed."""
    cfg = dict(_DEFAULTS)
    if "DRY_RUN" in env:
        cfg["DRY_RUN"] = _clean(env["DRY_RUN"]) not in ("0", "false", "False", "")
    for key in ("IDLE_MINUTES", "SAMPLE_INTERVAL"):
        if key in env:
            cfg[key] = int(_clean(env[key]))
    for key in ("CPU_BUSY_PCT", "NET_BUSY_KBPS", "DISK_BUSY_KBPS"):
        if key in env:
            cfg[key] = float(_clean(env[key]))
    if "PRIMARY_NIC" in env:
        cfg["PRIMARY_NIC"] = _clean(env["PRIMARY_NIC"])
    if "DATA_DISKS" in env:
        cfg["DATA_DISKS"] = _clean(env["DATA_DISKS"]).split()
    if "EXCLUDE_PORTS" in env:
        cfg["EXCLUDE_PORTS"] = {int(p) for p in _clean(env["EXCLUDE_PORTS"]).split()}
    if "INHIBIT_FILE" in env:
        cfg["INHIBIT_FILE"] = _clean(env["INHIBIT_FILE"])
    return cfg


# ----------------------------------------------------------------------------
# I/O glue
# ----------------------------------------------------------------------------

def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        # Return None (NOT "") so a failed probe propagates an error and the cycle
        # fails BUSY, rather than silently parsing to "idle" (fail-open -> could
        # sleep while in use, e.g. if ss/ps fails under fork pressure).
        log("WARN: %s failed: %s" % (" ".join(cmd), e))
        return None


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def log(msg):
    print(msg, flush=True)


VERSION = "1.1.0"   # bump on each release; logged at startup so journald records it


# Elapsed time uses the MONOTONIC clock, never the wall clock: an NTP correction or
# a manual `timedatectl set-time` must not change how long beefy has been idle (else
# the daemon could power off early or late). All idle/rate deltas derive from this.
_now = time.monotonic


def _snapshot():
    return {
        "t": _now(),
        "stat": _read("/proc/stat"),
        "dev": _read("/proc/net/dev"),
        "ds": _read("/proc/diskstats"),
    }


def main():
    cfg = load_config(os.environ)
    log(start_banner(VERSION, cfg))

    prev = _snapshot()
    idle_since = _now()
    time.sleep(cfg["SAMPLE_INTERVAL"])

    while True:
        now = _now()
        cur = _snapshot()
        secs = cur["t"] - prev["t"]

        # Any failure computing the probes (a failed ss/ps -> None, a malformed
        # /proc line -> ValueError, etc.) must fail BUSY, never silently idle.
        try:
            cpu = cpu_busy_pct(prev["stat"], cur["stat"])
            net = net_kbps(prev["dev"], cur["dev"], cfg["PRIMARY_NIC"], secs)
            disk = disk_kbps(prev["ds"], cur["ds"], cfg["DATA_DISKS"], secs)
            listen = parse_listen_ports(_run(["ss", "-ltnH"]))
            conns = count_inbound(_run(["ss", "-tnH", "state", "established"]),
                                  listen, cfg["EXCLUDE_PORTS"])
            ps = _run(["ps", "-eo", "args"])
            ssh = count_interactive_ssh(ps)
            vscode = vscode_remote_active(ps)
            inhibit = os.path.exists(cfg["INHIBIT_FILE"])
            probes = {
                "conns": conns > 0,
                "ssh": ssh > 0,
                "vscode": vscode,
                "cpu": cpu > cfg["CPU_BUSY_PCT"],
                "net": net > cfg["NET_BUSY_KBPS"],
                "disk": disk > cfg["DISK_BUSY_KBPS"],
            }
            busy, reasons = evaluate(probes, inhibit)
            stats = ("conns=%d ssh=%d vscode=%d cpu=%2.0f%% net=%.0fkB/s "
                     "disk=%.0fkB/s inhibit=%d"
                     % (conns, ssh, int(vscode), cpu, net, disk, int(inhibit)))
        except Exception as e:
            log("WARN probe cycle failed (%s) -> treating as BUSY" % e)
            busy, reasons, stats = True, ["probe_error"], "probe error"

        idle_since = update_idle(busy, idle_since, now)
        idle_min = (now - idle_since) / 60.0
        log("idle=%4.1fm %s -> %s%s"
            % (idle_min, stats, "BUSY" if busy else "idle",
               (" (" + ",".join(reasons) + ")") if reasons else ""))

        if not busy and should_sleep(idle_since, now, cfg["IDLE_MINUTES"]):
            if cfg["DRY_RUN"]:
                log("WOULD power off now (idle %d min, dry-run)" % cfg["IDLE_MINUTES"])
            else:
                log("idle %d min -> systemctl poweroff" % cfg["IDLE_MINUTES"])
                res = subprocess.run(["systemctl", "poweroff"])
                if res.returncode == 0:
                    return
                # poweroff failed: don't exit (Restart=on-failure won't fire on a
                # clean exit) -- log and retry on the next cycle.
                log("poweroff FAILED rc=%d -- will retry next cycle" % res.returncode)

        prev = cur
        time.sleep(cfg["SAMPLE_INTERVAL"])


if __name__ == "__main__":
    sys.exit(main())
