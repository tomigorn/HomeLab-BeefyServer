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
import hashlib
import json
import os
import platform
import re
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


def parse_published_ports(docker_ports_text):
    """TCP host ports Docker publishes, from `docker ps --format '{{.Ports}}'`."""
    ports = set()
    for m in re.finditer(r":(\d+)(?:-(\d+))?->\d+(?:-\d+)?/tcp", docker_ports_text):
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        ports.update(range(lo, hi + 1))
    return ports


def parse_local_addrs(ip_text):
    """Own IPv4 addresses from `ip -o -4 addr show`."""
    return set(re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)/", ip_text))


def _ct_key(line):
    m = [re.search(r"\b%s=(\S+)" % k, line) for k in ("src", "sport", "dst", "dport")]
    return tuple(x.group(1) for x in m) if all(m) else None


def ct_flow_bytes(ct_text):
    """{(src, sport, dst, dport): bytes both directions} for flows carrying accounting (nf_conntrack_acct=1)."""
    out = {}
    for line in ct_text.splitlines():
        b = re.findall(r"\bbytes=(\d+)", line)
        key = _ct_key(line)
        if b and key:
            out[key] = sum(int(x) for x in b)
    return out


def active_ct_peers(ct_text, local_addrs, published_ports, exclude_ports,
                    est_timeout=None, max_idle=None, prev_bytes=None, min_bytes=None):
    """src:sport of ESTABLISHED conntrack flows whose ORIGINAL destination is one of
    our addresses and a Docker-published port, and which are in use.

    Connections DNAT'ed into a bridged container never show up in the host's `ss`,
    only in conntrack (`conntrack -L -p tcp --state ESTABLISHED`). The first
    dst=/dport= pair on a line is the original direction. A flow idle longer than
    max_idle seconds (conntrack keeps ESTABLISHED for est_timeout, 5 days) is
    treated as stale: a peer that vanished without FIN must not pin beefy awake.
    With byte accounting and prev_bytes given, a flow is in use only if it moved at
    least min_bytes since the last sample: an app left open holds a websocket or an
    event stream whose heartbeats would otherwise keep the flow fresh forever.
    """
    peers = []
    for line in ct_text.splitlines():
        if "ESTABLISHED" not in line:
            continue
        if est_timeout and max_idle is not None:
            f = line.split()
            if len(f) > 3 and f[2].isdigit() and est_timeout - int(f[2]) > max_idle:
                continue
        key = _ct_key(line)
        if not key:
            continue
        port = int(key[3])
        if not (key[2] in local_addrs and port in published_ports
                and port not in exclude_ports):
            continue
        b = re.findall(r"\bbytes=(\d+)", line)
        if b and prev_bytes is not None and min_bytes:
            if sum(int(x) for x in b) - prev_bytes.get(key, 0) < min_bytes:
                continue
        peers.append("%s:%s" % (key[0], key[1]))
    return peers


def count_ct_inbound(*args, **kwargs):
    """Number of in-use inbound conntrack flows (see active_ct_peers)."""
    return len(active_ct_peers(*args, **kwargs))


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


def default_route_iface(route_text):
    """Interface of the default route in a /proc/<pid>/net/route snapshot, or None."""
    for line in (route_text or "").splitlines()[1:]:
        f = line.split()
        if len(f) > 7 and f[1] == "00000000" and f[7] == "00000000":
            return f[0]
    return None


def vpn_uplink(configured, route_text):
    """The VPN container's uplink NIC: as configured, or its default-route NIC for "auto".

    Docker numbers a container's NICs by network attach order, so eth0 can be an idle
    second network (2026-11-03: seeding left on eth1 and kept beefy awake).
    """
    return default_route_iface(route_text) if configured == "auto" else configured


def vpn_upload_kbps(vpn_a, vpn_b, uplink, tunnel, secs):
    """VPN-container traffic that is not download, in kB/s, from two snapshots of its
    netns /proc/<pid>/net/dev: uplink rx+tx minus tunnel rx.

    Torrent uploads leave the primary NIC as encrypted uplink traffic. Subtracting this
    from net_kbps and disk_kbps lets seeding go unseen while downloads (tunnel rx) still keep beefy
    awake. A missing snapshot or a counter reset (container restarted) subtracts nothing.
    """
    if secs <= 0 or not vpn_a or not vpn_b or not uplink:
        return 0.0
    up_rx_a, up_tx_a = _nic_rx_tx(vpn_a, uplink)
    up_rx_b, up_tx_b = _nic_rx_tx(vpn_b, uplink)
    tun_rx_a, _ = _nic_rx_tx(vpn_a, tunnel)
    tun_rx_b, _ = _nic_rx_tx(vpn_b, tunnel)
    uplink_delta = (up_rx_b - up_rx_a) + (up_tx_b - up_tx_a)
    tun_rx_delta = tun_rx_b - tun_rx_a
    if uplink_delta < 0 or tun_rx_delta < 0:
        return 0.0
    return max(0, uplink_delta - tun_rx_delta) / 1000.0 / secs


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


IDLE_REASONS = ("conns", "ssh", "vscode", "cpu", "net", "disk", "inhibit", "probe_error")
_SAFE = re.compile(r"[^A-Za-z0-9 .,:_%/+-]")


def first_inbound_peer(ss_estab_text, listen_ports, exclude_ports, ct_text,
                       local_addrs=(), published_ports=()):
    """peer ip:port of the first inbound connection (ss first, then conntrack), else ''."""
    for line in ss_estab_text.splitlines():
        toks = [t for t in line.split()
                if t.rpartition(":")[2].isdigit() and t.rpartition(":")[0]]
        if len(toks) < 2:
            continue
        lport = int(toks[0].rpartition(":")[2])
        if lport in listen_ports and lport not in exclude_ports:
            return toks[1]
    for line in ct_text.splitlines():
        if "ESTABLISHED" not in line:
            continue
        src = re.search(r"\bsrc=(\S+)", line)
        sport = re.search(r"\bsport=(\d+)", line)
        dst = re.search(r"\bdst=(\S+)", line)
        dport = re.search(r"\bdport=(\d+)", line)
        if not (src and sport and dst and dport):
            continue
        port = int(dport.group(1))
        if dst.group(1) in local_addrs and port in published_ports and port not in exclude_ports:
            return "%s:%s" % (src.group(1), sport.group(1))
    return ""


def idle_detail(reasons, peer, stats):
    """Short human text for what keeps beefy awake: reason words and the peer ip only (a Prometheus label, so no ports or live numbers: those are gauges)."""
    parts = []
    if "inhibit" in reasons:
        parts.append("keep-awake file")
    if "vscode" in reasons:
        parts.append("VS Code Remote")
    if "conns" in reasons and peer:
        parts.append("conn from %s" % peer.rsplit(":", 1)[0])
    if "ssh" in reasons:
        parts.append("interactive ssh x%d" % stats.get("ssh", 1))
    parts += [r for r in ("cpu", "net", "disk") if r in reasons]
    return ", ".join(parts)


def parse_btime(stat_text):
    """Boot time (epoch seconds) from /proc/stat, None when absent."""
    for line in stat_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "btime" and parts[1].isdigit():
            return int(parts[1])
    return None


IDLE_GAUGES = (("cpu", "beefy_idle_cpu_percent"), ("net", "beefy_idle_net_kbytes_per_second"), ("disk", "beefy_idle_disk_kbytes_per_second"),
              ("cpu_temp", "beefy_idle_cpu_temp_celsius"), ("pch_temp", "beefy_idle_pch_temp_celsius"))


def read_hwmon_temps(root="/sys/class/hwmon"):
    """CPU package (coretemp) and PCH (pch_cometlake) temperature in degrees C; a missing chip is left out."""
    out = {}
    try:
        chips = sorted(os.listdir(root))
    except OSError:
        return out
    for c in chips:
        base = os.path.join(root, c)
        try:
            name = open(os.path.join(base, "name")).read().strip()
            if name == "coretemp":
                key = "cpu_temp"
                for i in range(1, 20):
                    if open(os.path.join(base, "temp%d_label" % i)).read().strip() == "Package id 0":
                        out[key] = int(open(os.path.join(base, "temp%d_input" % i)).read()) / 1000.0
                        break
            elif name == "pch_cometlake":
                out["pch_temp"] = int(open(os.path.join(base, "temp1_input")).read()) / 1000.0
        except (OSError, ValueError):
            continue
    return out


def render_idle_metrics(reasons, detail, ts, boot_time=None, stats=None):
    """node-exporter textfile body: one 0/1 per reason, the detail, the write time, the boot time, the measured cpu/net/disk."""
    safe = _SAFE.sub("", str(detail))[:80]
    lines = ["# TYPE beefy_idle_busy gauge"]
    lines += ['beefy_idle_busy{reason="%s"} %d' % (r, int(r in reasons)) for r in IDLE_REASONS]
    lines += ["# TYPE beefy_idle_info gauge", 'beefy_idle_info{detail="%s"} 1' % safe,
              "# TYPE beefy_idle_updated_timestamp_seconds gauge",
              "beefy_idle_updated_timestamp_seconds %d" % ts]
    for key, name in IDLE_GAUGES:
        if stats and key in stats:
            lines += ["# TYPE %s gauge" % name, "%s %.1f" % (name, stats[key])]
    if boot_time is not None:
        lines += ["# HELP beefy_boot_time_seconds Boot time of beefy (btime from /proc/stat), epoch seconds.",
                  "# TYPE beefy_boot_time_seconds gauge",
                  "beefy_boot_time_seconds %d" % boot_time]
    return "\n".join(lines) + "\n"


def write_idle_metrics(path, body):
    """Atomic write; a failure never affects the idle decision."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as e:
        log("WARN: metrics write failed: %s" % e)


# Aggregate resource figures for fastpi's activity page: written to RESOURCES_FILE, served read-only by the activity gate.

def mem_used_total(meminfo_text):
    """(used, total) RAM bytes from /proc/meminfo (used = MemTotal - MemAvailable), None when absent."""
    kb = {}
    for line in meminfo_text.splitlines():
        k, _, rest = line.partition(":")
        f = rest.split()
        if f and f[0].isdigit():
            kb[k.strip()] = int(f[0])
    if "MemTotal" not in kb or "MemAvailable" not in kb:
        return None
    return (kb["MemTotal"] - kb["MemAvailable"]) * 1024, kb["MemTotal"] * 1024


def cgroup_cpu_usec(cpu_stat_text):
    for line in cpu_stat_text.splitlines():
        f = line.split()
        if len(f) == 2 and f[0] == "usage_usec" and f[1].isdigit():
            return int(f[1])
    return None


def cgroup_mem_bytes(current_text, stat_text):
    """A cgroup's memory without its inactive page cache (as `docker stats` shows it), None when unreadable."""
    cur = current_text.strip()
    if not cur.isdigit():
        return None
    inactive = 0
    for line in stat_text.splitlines():
        f = line.split()
        if len(f) == 2 and f[0] == "inactive_file" and f[1].isdigit():
            inactive = int(f[1])
    return max(0, int(cur) - inactive)


def cgroup_cpu_pct(usec_a, usec_b, secs, ncpu):
    """A cgroup's CPU time as a share of the whole host's, in %; None when unknown or the counter reset."""
    if usec_a is None or usec_b is None or secs <= 0 or not ncpu or usec_b < usec_a:
        return None
    return (usec_b - usec_a) / 1e6 / secs / ncpu * 100.0


def nic_tx_kbit(dev_a, dev_b, nic, secs):
    """Bytes the NIC sent between two /proc/net/dev snapshots, in kbit/s."""
    if secs <= 0:
        return 0.0
    _, tx_a = _nic_rx_tx(dev_a, nic)
    _, tx_b = _nic_rx_tx(dev_b, nic)
    return max(0, tx_b - tx_a) * 8 / 1000.0 / secs


def gpu_busy_pct(busy_a, busy_b, secs):
    """Busy % of the busiest i915 engine between two {engine: busy ns} readings, None when unknown."""
    if not busy_a or not busy_b or secs <= 0:
        return None
    pcts = []
    for e, b in busy_b.items():
        if e not in busy_a:
            continue
        if b < busy_a[e]:
            return None
        pcts.append((b - busy_a[e]) / 1e9 / secs * 100.0)
    return min(100.0, max(pcts)) if pcts else None


def i915_busy_events(root="/sys/bus/event_source/devices/i915"):
    """(pmu type, cpu, {engine: config}) of the i915 PMU's busy events, None without the PMU."""
    try:
        ptype = int(_read(os.path.join(root, "type")))
        cpu = int(_read(os.path.join(root, "cpumask")).strip().split(",")[0].split("-")[0])
        events = {}
        for name in sorted(os.listdir(os.path.join(root, "events"))):
            if name.endswith("-busy"):
                m = re.search(r"config=(0x[0-9a-fA-F]+|\d+)", _read(os.path.join(root, "events", name)))
                if m:
                    events[name[:-len("-busy")]] = int(m.group(1), 0)
        return ptype, cpu, events
    except (OSError, ValueError):
        return None


def cgroup_dir(proc_cgroup_text):
    """cgroup v2 directory of a process from /proc/<pid>/cgroup, None when not on the unified hierarchy."""
    for line in proc_cgroup_text.splitlines():
        if line.startswith("0::/"):
            return "/sys/fs/cgroup" + line[3:].strip()
    return None


def nic_rx_kbit(dev_a, dev_b, nic, secs):
    """Bytes a NIC received between two /proc/net/dev snapshots, in kbit/s; None without both snapshots."""
    if not dev_a or not dev_b or secs <= 0:
        return None
    rx_a, _ = _nic_rx_tx(dev_a, nic)
    rx_b, _ = _nic_rx_tx(dev_b, nic)
    return max(0, rx_b - rx_a) * 8 / 1000.0 / secs


def disk_write_kbit(ds_a, ds_b, disks, secs):
    """Data-disk writes in kbit/s between two /proc/diskstats snapshots (512-byte sectors)."""
    if secs <= 0:
        return 0.0

    def written(text):
        return sum(int(p[9]) for p in (l.split() for l in text.splitlines()) if len(p) > 9 and p[2] in set(disks))
    return max(0, written(ds_b) - written(ds_a)) * 512 * 8 / 1000.0 / secs


def _diskstat_rows(text):
    return {p[2]: [int(x) for x in p[3:13]] for p in (l.split() for l in (text or "").splitlines()) if len(p) >= 13}


def _disk_mounts(mounts_text):
    """disk basename -> first mount point of the disk or one of its partitions, from /proc/mounts."""
    out = {}
    for line in (mounts_text or "").splitlines():
        f = line.split()
        if len(f) >= 2 and f[0].startswith("/dev/"):
            dev = re.sub(r"(?<=[a-z])\d+$|(?<=\d)p\d+$", "", f[0][len("/dev/"):])
            out.setdefault(dev, f[1])
    return out


_SPIN_RE = re.compile(r"\s(SPUN-DOWN|SLEEP)\s.*\sread=(\d+)\s+written=(\d+)")
_KIND_ORDER = {"ssd": 0, "hdd": 1, "disk": 2}


def disk_loads(ds_a, ds_b, disks, secs, rotational, mounts_text, spin_line, statvfs=None, limits=None):
    """One entry per data disk: type (ssd/hdd from the kernel's rotational flag), number within its type by mount point, busy % (io_ticks), read/write kbit/s.
    An HDD is asleep only when the spin-state log's last line says parked and its sector counters are the ones logged then (no I/O since). With statvfs: space of the disk's mount point and the type's limits."""
    a, b = _diskstat_rows(ds_a), _diskstat_rows(ds_b)
    mounts = _disk_mounts(mounts_text)
    spin = _SPIN_RE.search(spin_line or "")
    out = []
    for d in disks:
        if d not in b:
            continue
        r = rotational.get(d)
        kind = "disk" if r is None else ("hdd" if r else "ssd")
        cur, old = b[d], a.get(d, b[d])
        delta = [max(0, x - y) for x, y in zip(cur, old)]
        e = {"kind": kind, "_sort": (_KIND_ORDER[kind], mounts.get(d, "~"), d),
             "busy_pct": min(100.0, delta[9] / (secs * 1000.0) * 100.0) if secs > 0 else 0.0,
             "read_kbps": delta[2] * 512 * 8 / 1000.0 / secs if secs > 0 else 0.0,
             "write_kbps": delta[6] * 512 * 8 / 1000.0 / secs if secs > 0 else 0.0}
        if kind == "hdd" and spin and (int(spin.group(2)), int(spin.group(3))) == (cur[2], cur[6]):
            e["asleep"] = True
        if statvfs and d in mounts:
            try:
                st = statvfs(mounts[d])
                total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
                e["used_bytes"], e["total_bytes"] = total - free, total
                if limits and kind in limits:
                    e["yellow_pct"], e["orange_pct"], e["limit_pct"] = limits[kind]
            except OSError:
                pass
        out.append(e)
    out.sort(key=lambda e: e["_sort"])
    seen, numbered = {}, []
    for e in out:
        del e["_sort"]
        kind = e.pop("kind")
        seen[kind] = seen.get(kind, 0) + 1
        numbered.append({"kind": kind, "n": seen[kind], **e})
    return numbered


def last_line(path):
    """The last non-empty line of a file (read from its end), None when unreadable."""
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
    except OSError:
        return None
    return lines[-1].strip() if lines else None


def core_busy_pcts(stat_a, stat_b):
    """Busy % of each CPU (cpu0, cpu1, …) between two /proc/stat snapshots."""
    def cores(text):
        return {p[0]: [int(x) for x in p[1:]] for p in (l.split() for l in text.splitlines()) if p and re.fullmatch(r"cpu\d+", p[0])}
    a, b = cores(stat_a), cores(stat_b)
    out = []
    for name in sorted(b, key=lambda n: int(n[3:])):
        na, nb = a.get(name, b[name]), b[name]
        tot = sum(nb) - sum(na)
        idle = (nb[3] + nb[4]) - (na[3] + na[4])
        out.append((tot - idle) / tot * 100.0 if tot > 0 else 0.0)
    return out


def swap_used_total(meminfo_text):
    """(used, total) swap bytes from /proc/meminfo, None when absent."""
    kb = {}
    for line in meminfo_text.splitlines():
        k, _, rest = line.partition(":")
        f = rest.split()
        if f and f[0].isdigit():
            kb[k.strip()] = int(f[0])
    if "SwapTotal" not in kb or "SwapFree" not in kb:
        return None
    return (kb["SwapTotal"] - kb["SwapFree"]) * 1024, kb["SwapTotal"] * 1024


HISTORY_KEYS = ("cpu_pct", "mem_pct", "gpu_pct", "down_kbps", "up_kbps")


def remember(history, figures, keep):
    """Appends this cycle's overview figures to `history` (key -> last `keep` values; disks by type and number)."""
    mem, tot = figures.get("mem_used_bytes"), figures.get("mem_total_bytes")
    vals = {"cpu_pct": figures.get("cpu_pct"), "mem_pct": mem / tot * 100.0 if mem is not None and tot else None,
            "gpu_pct": figures.get("gpu_pct"), "down_kbps": figures.get("down_kbps"), "up_kbps": figures.get("up_kbps")}
    for d in figures.get("disks") or []:
        vals["disk:%s%d" % (d["kind"], d["n"])] = d.get("busy_pct")
    for k, v in vals.items():
        history[k] = (history.get(k, []) + [v])[-keep:]


def attach_history(figures, history):
    figures["hist"] = {k: history[k] for k in HISTORY_KEYS if k in history}
    for d in figures.get("disks") or []:
        h = history.get("disk:%s%d" % (d["kind"], d["n"]))
        if h is not None:
            d["busy_hist"] = h


def ffmpeg_stream_key(argv):
    """sha256 (first 16 hex) of the input file of a Jellyfin stream's ffmpeg (it writes to the transcodes folder); None for other ffmpeg runs."""
    if not any(b"/transcodes/" in a for a in argv):
        return None
    for flag, arg in zip(argv, argv[1:]):
        if flag == b"-i" and arg.startswith(b"file:"):
            return hashlib.sha256(arg[5:]).hexdigest()[:16]
    return None


def proc_cpu_start(stat_text):
    """(utime + stime, starttime) in clock ticks from /proc/<pid>/stat, None when unreadable."""
    f = stat_text.rpartition(")")[2].split()
    if len(f) < 20 or not (f[11].isdigit() and f[12].isdigit() and f[19].isdigit()):
        return None
    return int(f[11]) + int(f[12]), int(f[19])


def drm_client_engines(fdinfo_text):
    """(drm-client-id, {engine: busy ns / engine capacity}) of a DRM fd's fdinfo; None for any other fd."""
    kv = {}
    for line in fdinfo_text.splitlines():
        k, _, v = line.partition(":")
        kv[k.strip()] = v.strip()
    client = kv.get("drm-client-id")
    if client is None:
        return None
    engines = {}
    for k, v in kv.items():
        if k.startswith("drm-engine-") and not k.startswith("drm-engine-capacity-") and v.endswith(" ns"):
            e = k[len("drm-engine-"):]
            cap = kv.get("drm-engine-capacity-" + e, "1")
            engines[e] = int(v[:-3]) / (int(cap) if cap.isdigit() and int(cap) > 0 else 1)
    return client, engines


def stream_figures(prev, cur, secs, ncpu, hz, uptime):
    """CPU (share of the whole host) and GPU (busiest engine) per stream key over the cycle; a process new in this cycle counts over its own lifetime."""
    acc = {}
    for pid, p in cur.items():
        old = prev.get(pid)
        if old and old["start"] == p["start"] and old["key"] == p["key"]:
            span, cpu0, gpu0 = secs, old["cpu"], old["gpu"]
        else:
            span, cpu0, gpu0 = min(secs, uptime - p["start"] / hz), 0, {}
        if span <= 0 or not ncpu:
            continue
        a = acc.setdefault(p["key"], {"cpu": 0.0, "gpu": {}})
        a["cpu"] += max(0, p["cpu"] - cpu0) / hz / span / ncpu * 100.0
        for e, ns in p["gpu"].items():
            a["gpu"][e] = a["gpu"].get(e, 0.0) + max(0.0, ns - gpu0.get(e, 0.0)) / 1e9 / span * 100.0
    return [{"key": k, "cpu_pct": round(min(100.0, a["cpu"]), 1),
             "gpu_pct": round(min(100.0, max(a["gpu"].values())), 1) if a["gpu"] else None}
            for k, a in sorted(acc.items())]


def ffmpeg_task(comm, argv):
    """Which Jellyfin background job an ffmpeg/ffprobe process is, from its arguments (no titles); None for a stream or another process."""
    if comm not in ("ffmpeg", "ffprobe") or not argv:
        return None
    if comm == "ffprobe":
        return "keyframe extraction" if b"nokey" in argv or b"-show_packets" in argv else "media probing"
    if ffmpeg_stream_key(argv) or any(b"/transcodes/" in a for a in argv):
        return None
    joined = b" ".join(argv)
    if b"chromaprint" in joined or b"blackframe" in joined or b"silencedetect" in joined:
        return "intro/credits detection"
    if argv[-1].endswith(b"%08d.jpg") or b"%08d" in argv[-1]:
        return "preview images"
    if b"-vframes" in argv or b"-frames:v" in argv:
        return "chapter images"
    return "other ffmpeg"


def split_figures(figures):
    """(stream figures, background task figures) from stream_figures over every Jellyfin ffmpeg/ffprobe."""
    streams = [f for f in figures if not f["key"].startswith("task:")]
    tasks = [{"task": f["key"][len("task:"):], "cpu_pct": f["cpu_pct"], "gpu_pct": f["gpu_pct"]} for f in figures if f["key"].startswith("task:")]
    return streams, tasks


def sab_post_state(comms):
    """SABnzbd's post-processing from its process names: unpacking (unrar, 7-Zip), verifying (par2), else None."""
    if any(c in ("unrar", "7z", "7zz", "7za") for c in comms):
        return "unpacking"
    if any(c.startswith("par2") for c in comms):
        return "verifying"
    return None


RESOURCE_FIELDS = (("cpu_pct", 1), ("jellyfin_cpu_pct", 1), ("gpu_pct", 1), ("mem_used_bytes", None),
                   ("mem_total_bytes", None), ("jellyfin_mem_bytes", None), ("up_kbps", 0),
                   ("dl_cpu_pct", 1), ("dl_mem_bytes", None), ("dl_down_kbps", 0), ("disk_write_kbps", 0),
                   ("swap_used_bytes", None), ("swap_total_bytes", None), ("down_kbps", 0))


def _fig(key, v):
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if key.endswith("_bytes"):
        return int(v)
    return round(v, 1) if key.endswith("_pct") or key.endswith("_hist") else int(round(v))


def render_resources(ts, interval, figures):
    """resources.json: numbers only (no names, no addresses); an unknown figure is left out."""
    out = {"v": 1, "updated": int(ts), "interval_s": int(round(interval))}
    for key, digits in RESOURCE_FIELDS:
        v = figures.get(key)
        if v is None:
            continue
        out[key] = int(v) if digits is None else (int(round(v)) if digits == 0 else round(v, digits))
    if figures.get("sab_post") in ("unpacking", "verifying"):
        out["sab_post"] = figures["sab_post"]
    for key in ("streams", "background"):
        if figures.get(key) is not None:
            out[key] = [{k: v for k, v in s.items() if v is not None} for s in figures[key]]
    if figures.get("cores_pct") is not None:
        out["cores_pct"] = [round(v, 1) for v in figures["cores_pct"]]
    if figures.get("disks") is not None:
        out["disks"] = [{k: ([_fig(k, x) for x in v] if isinstance(v, list) else _fig(k, v)) for k, v in d.items() if v is not None}
                        for d in figures["disks"]]
    if figures.get("hist"):
        out["hist"] = {k: [_fig(k, x) for x in v] for k, v in figures["hist"].items()}
    return json.dumps(out, separators=(",", ":")) + "\n"


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
    "CT_MAX_IDLE": 300,
    "CT_MIN_BYTES": 1024,
    "VPN_CONTAINER": "",
    "VPN_UPLINK": "auto",
    "VPN_TUNNEL": "tun0",
    "METRICS_FILE": "/run/beefy-idle.prom",
    "RESOURCES_FILE": "/run/beefy-activity/resources.json",
    "JELLYFIN_CONTAINER": "jellyfin",
    "DOWNLOAD_CONTAINERS": ["movie-sabnzbd", "movie-qbittorrent", "movie-gluetun"],
    "SAB_CONTAINER": "movie-sabnzbd",
    # written by Server/3-Storage-Layout-and-Spindown/hdd-spinstate.sh (smartctl -n standby, never wakes the disk); empty = off
    "SPINSTATE_LOG": "/var/log/hdd-spinstate.log",
    # used % per disk type: mover/yellow, orange, limit (same as Docker/Seerr/scripts/indexer-usage.py DISKS)
    "DISK_LIMITS": {"ssd": (70, 75, 80), "hdd": (80, 85, 90)},
    # cycles of overview history in resources.json (sparklines)
    "HISTORY_SAMPLES": 15,
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
    for key in ("IDLE_MINUTES", "SAMPLE_INTERVAL", "CT_MAX_IDLE", "CT_MIN_BYTES", "HISTORY_SAMPLES"):
        if key in env:
            cfg[key] = int(_clean(env[key]))
    for key in ("CPU_BUSY_PCT", "NET_BUSY_KBPS", "DISK_BUSY_KBPS"):
        if key in env:
            cfg[key] = float(_clean(env[key]))
    if "PRIMARY_NIC" in env:
        cfg["PRIMARY_NIC"] = _clean(env["PRIMARY_NIC"])
    if "DATA_DISKS" in env:
        cfg["DATA_DISKS"] = _clean(env["DATA_DISKS"]).split()
    if "DOWNLOAD_CONTAINERS" in env:
        cfg["DOWNLOAD_CONTAINERS"] = _clean(env["DOWNLOAD_CONTAINERS"]).split()
    if "EXCLUDE_PORTS" in env:
        cfg["EXCLUDE_PORTS"] = {int(p) for p in _clean(env["EXCLUDE_PORTS"]).split()}
    for key in ("INHIBIT_FILE", "METRICS_FILE", "VPN_CONTAINER", "VPN_UPLINK", "VPN_TUNNEL", "RESOURCES_FILE", "JELLYFIN_CONTAINER", "SAB_CONTAINER", "SPINSTATE_LOG"):
        if key in env:
            cfg[key] = _clean(env[key])
    cfg["DISK_LIMITS"] = dict(cfg["DISK_LIMITS"])
    for kind in ("ssd", "hdd"):
        if "DISK_LIMITS_" + kind.upper() in env:
            cfg["DISK_LIMITS"][kind] = tuple(int(x) for x in _clean(env["DISK_LIMITS_" + kind.upper()]).split())
    return cfg


# ----------------------------------------------------------------------------
# I/O glue
# ----------------------------------------------------------------------------

def _run(cmd, strict=True):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if strict and r.returncode != 0:
            log("WARN: %s exited %d: %s" % (" ".join(cmd), r.returncode, r.stderr.strip()[:200]))
            return None
        return r.stdout
    except Exception as e:
        # Return None (NOT "") so a failed probe propagates an error and the cycle
        # fails BUSY, rather than silently parsing to "idle" (fail-open -> could
        # sleep while in use, e.g. if ss/ps fails under fork pressure).
        log("WARN: %s failed: %s" % (" ".join(cmd), e))
        return None


def inhibited(path):
    """The shared keep-awake file, or any holder's file in <path>.d/ (beefy-keep-awake on <holder>). An unreadable dir counts as held."""
    if os.path.exists(path):
        return True
    try:
        return bool(os.listdir(path + ".d"))
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _ct_est_timeout():
    try:
        return int(_read("/proc/sys/net/netfilter/nf_conntrack_tcp_timeout_established"))
    except ValueError:
        return None


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def log(msg):
    print(msg, flush=True)


VERSION = "1.11.1"   # bump on each release; logged at startup so journald records it


# Elapsed time uses the MONOTONIC clock, never the wall clock: an NTP correction or
# a manual `timedatectl set-time` must not change how long beefy has been idle (else
# the daemon could power off early or late). All idle/rate deltas derive from this.
_now = time.monotonic


def _container_pid(name):
    out = _run(["docker", "inspect", "-f", "{{.State.Pid}}", name], strict=False) if name else None
    pid = (out or "").strip()
    return pid if pid.isdigit() and pid != "0" else None


class I915Busy:
    """Engine busy counters of the i915 PMU (as intel_gpu_top reads them), kept open between cycles; cumulative ns, so a read costs nothing extra."""

    def __init__(self):
        self.fds = {}
        found = i915_busy_events() if platform.machine() == "x86_64" else None
        if not found:
            return
        ptype, cpu, events = found
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)

        class Attr(ctypes.Structure):
            _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32), ("config", ctypes.c_uint64),
                        ("rest", ctypes.c_uint8 * 112)]

        for engine, config in events.items():
            a = Attr(type=ptype, size=ctypes.sizeof(Attr), config=config)
            fd = libc.syscall(298, ctypes.byref(a), -1, cpu, -1, 0)   # perf_event_open on x86_64
            if fd < 0:
                log("WARN: i915 %s-busy unavailable (errno %d): no GPU figure" % (engine, ctypes.get_errno()))
                continue
            self.fds[engine] = fd

    def read(self):
        if not self.fds:
            return None
        try:
            return {e: int.from_bytes(os.read(fd, 8), sys.byteorder) for e, fd in self.fds.items()}
        except OSError:
            return None


def _container_cgroups(names):
    """{name: cgroup dir} of the running containers among names, from one docker inspect."""
    names = [n for n in names if n]
    out = _run(["docker", "inspect", "-f", "{{.Name}} {{.State.Pid}}"] + names, strict=False) if names else None
    dirs = {}
    for line in (out or "").splitlines():
        f = line.split()
        if len(f) == 2 and f[1].isdigit() and f[1] != "0":
            d = cgroup_dir(_read("/proc/%s/cgroup" % f[1]))
            if d:
                dirs[f[0].lstrip("/")] = d
    return dirs


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def _pid_gpu(pid):
    """Busy ns per engine summed over the DRM clients a process holds (each client once)."""
    clients = {}
    try:
        fds = os.listdir("/proc/%s/fdinfo" % pid)
    except OSError:
        return {}
    for fd in fds:
        c = drm_client_engines(_read("/proc/%s/fdinfo/%s" % (pid, fd)))
        if c:
            clients[c[0]] = c[1]
    total = {}
    for engines in clients.values():
        for e, ns in engines.items():
            total[e] = total.get(e, 0.0) + ns
    return total


def _stream_procs(cg):
    """{pid: key, cpu ticks, start, gpu} of the ffmpeg/ffprobe processes in the Jellyfin cgroup: a stream's file key, or task:<background job>."""
    procs = {}
    for pid in _read(cg + "/cgroup.procs").split():
        comm = _read("/proc/%s/comm" % pid).strip()
        if comm not in ("ffmpeg", "ffprobe"):
            continue
        argv = _read_bytes("/proc/%s/cmdline" % pid).split(b"\0")
        while argv and argv[-1] == b"":
            argv.pop()
        task = ffmpeg_task(comm, argv)
        key = "task:" + task if task else ffmpeg_stream_key(argv)
        st = proc_cpu_start(_read("/proc/%s/stat" % pid))
        if key and st:
            procs[pid] = {"key": key, "cpu": st[0], "start": st[1], "gpu": _pid_gpu(pid)}
    return procs


def _resource_snapshot(cfg, gpu):
    try:
        cgs = _container_cgroups([cfg["JELLYFIN_CONTAINER"]] + cfg["DOWNLOAD_CONTAINERS"])
        cg = cgs.get(cfg["JELLYFIN_CONTAINER"])
        sab = cgs.get(cfg["SAB_CONTAINER"])
        return {
            "jf_cpu": cgroup_cpu_usec(_read(cg + "/cpu.stat")) if cg else None,
            "jf_mem": cgroup_mem_bytes(_read(cg + "/memory.current"), _read(cg + "/memory.stat")) if cg else None,
            "gpu": gpu.read() if gpu else None,
            "streams": _stream_procs(cg) if cg else None,
            "uptime": float(_read("/proc/uptime").split()[0]),
            "dl_cpu": {n: cgroup_cpu_usec(_read(cgs[n] + "/cpu.stat")) for n in cfg["DOWNLOAD_CONTAINERS"] if n in cgs},
            "dl_mem": [cgroup_mem_bytes(_read(cgs[n] + "/memory.current"), _read(cgs[n] + "/memory.stat")) for n in cfg["DOWNLOAD_CONTAINERS"] if n in cgs],
            "sab_post": sab_post_state([_read("/proc/%s/comm" % p).strip() for p in _read(sab + "/cgroup.procs").split()]) if sab else None,
        }
    except Exception as e:
        log("WARN: resource snapshot failed: %s" % e)
        return {}


def download_cpu_pct(prev, cur, secs, ncpu):
    """Summed CPU share of the download containers present in both snapshots; None when none is."""
    pcts = [cgroup_cpu_pct(prev.get(n), cur.get(n), secs, ncpu) for n in cur]
    pcts = [p for p in pcts if p is not None]
    return sum(pcts) if pcts else None


def _rotational(disks):
    out = {}
    for d in disks:
        v = _read("/sys/block/%s/queue/rotational" % d).strip()
        out[d] = None if v not in ("0", "1") else v == "1"
    return out


def resource_figures(prev, cur, secs, cfg, cpu):
    meminfo = _read("/proc/meminfo")
    mem = mem_used_total(meminfo)
    swap = swap_used_total(meminfo)
    p, c = prev["res"], cur["res"]
    dl_mem = [m for m in c.get("dl_mem", []) if m is not None]
    same_vpn = prev["vpn_pid"] == cur["vpn_pid"]
    return {
        "cpu_pct": cpu,
        "jellyfin_cpu_pct": cgroup_cpu_pct(p.get("jf_cpu"), c.get("jf_cpu"), secs, os.cpu_count()),
        "gpu_pct": gpu_busy_pct(p.get("gpu"), c.get("gpu"), secs),
        **dict(zip(("streams", "background"), split_figures(stream_figures(p.get("streams") or {}, c["streams"], secs, os.cpu_count(), os.sysconf("SC_CLK_TCK"), c["uptime"]))
                                                    if c.get("streams") is not None else (None, None))),
        "dl_cpu_pct": download_cpu_pct(p.get("dl_cpu", {}), c.get("dl_cpu", {}), secs, os.cpu_count()),
        "dl_mem_bytes": sum(dl_mem) if dl_mem else None,
        "dl_down_kbps": nic_rx_kbit(prev["vpn_dev"], cur["vpn_dev"], cfg["VPN_TUNNEL"], secs) if same_vpn else None,
        "disk_write_kbps": disk_write_kbit(prev["ds"], cur["ds"], cfg["DATA_DISKS"], secs),
        "sab_post": c.get("sab_post"),
        "mem_used_bytes": mem[0] if mem else None,
        "mem_total_bytes": mem[1] if mem else None,
        "jellyfin_mem_bytes": c.get("jf_mem"),
        "up_kbps": nic_tx_kbit(prev["dev"], cur["dev"], cfg["PRIMARY_NIC"], secs),
        "down_kbps": nic_rx_kbit(prev["dev"], cur["dev"], cfg["PRIMARY_NIC"], secs),
        "cores_pct": core_busy_pcts(prev["stat"], cur["stat"]),
        "swap_used_bytes": swap[0] if swap else None,
        "swap_total_bytes": swap[1] if swap else None,
        # /proc and statvfs only: answered from kernel memory, never a command to a parked HDD
        "disks": disk_loads(prev["ds"], cur["ds"], cfg["DATA_DISKS"], secs, _rotational(cfg["DATA_DISKS"]), _read("/proc/mounts"),
                            last_line(cfg["SPINSTATE_LOG"]), os.statvfs, cfg["DISK_LIMITS"]),
    }


def write_resources(path, body):
    try:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    except OSError as e:
        log("WARN: resources dir: %s" % e)
        return
    write_idle_metrics(path, body)


def _snapshot(cfg, gpu=None):
    vpn_pid = _container_pid(cfg["VPN_CONTAINER"])
    return {
        "t": _now(),
        "stat": _read("/proc/stat"),
        "dev": _read("/proc/net/dev"),
        "ds": _read("/proc/diskstats"),
        "vpn_pid": vpn_pid,
        "vpn_dev": _read("/proc/%s/net/dev" % vpn_pid) if vpn_pid else None,
        "vpn_route": _read("/proc/%s/net/route" % vpn_pid) if vpn_pid else None,
        "res": _resource_snapshot(cfg, gpu),
    }


def main():
    cfg = load_config(os.environ)
    log(start_banner(VERSION, cfg))

    gpu = I915Busy()
    history = {}
    prev = _snapshot(cfg, gpu)
    prev_ct_bytes = None
    idle_since = _now()
    time.sleep(cfg["SAMPLE_INTERVAL"])

    while True:
        now = _now()
        cur = _snapshot(cfg, gpu)
        secs = cur["t"] - prev["t"]

        # Any failure computing the probes (a failed ss/ps -> None, a malformed
        # /proc line -> ValueError, etc.) must fail BUSY, never silently idle.
        try:
            cpu = cpu_busy_pct(prev["stat"], cur["stat"])
            same_vpn = prev["vpn_pid"] == cur["vpn_pid"]
            vpn_up = vpn_upload_kbps(prev["vpn_dev"], cur["vpn_dev"], vpn_uplink(cfg["VPN_UPLINK"], cur["vpn_route"]),
                                     cfg["VPN_TUNNEL"], secs) if same_vpn else 0.0
            net = max(0.0, net_kbps(prev["dev"], cur["dev"], cfg["PRIMARY_NIC"], secs) - vpn_up)
            # uploaded bytes were read from the data disks first (through mergerfs, so not attributable per process)
            disk = max(0.0, disk_kbps(prev["ds"], cur["ds"], cfg["DATA_DISKS"], secs) - vpn_up)
            listen = parse_listen_ports(_run(["ss", "-ltnH"]))
            estab = _run(["ss", "-tnH", "state", "established"])
            conns = count_inbound(estab, listen, cfg["EXCLUDE_PORTS"])
            ct_text = _run(["conntrack", "-L", "-p", "tcp", "--state", "ESTABLISHED"])
            local_addrs = parse_local_addrs(_run(["ip", "-o", "-4", "addr", "show"]))
            published = parse_published_ports(_run(["docker", "ps", "--format", "{{.Ports}}"]))
            ct_peers = active_ct_peers(ct_text, local_addrs, published, cfg["EXCLUDE_PORTS"],
                                       _ct_est_timeout(), cfg["CT_MAX_IDLE"],
                                       prev_ct_bytes, cfg["CT_MIN_BYTES"])
            prev_ct_bytes = ct_flow_bytes(ct_text)
            ct = len(ct_peers)
            ps = _run(["ps", "-eo", "args"])
            ssh = count_interactive_ssh(ps)
            vscode = vscode_remote_active(ps)
            inhibit = inhibited(cfg["INHIBIT_FILE"])
            probes = {
                "conns": conns + ct > 0,
                "ssh": ssh > 0,
                "vscode": vscode,
                "cpu": cpu > cfg["CPU_BUSY_PCT"],
                "net": net > cfg["NET_BUSY_KBPS"],
                "disk": disk > cfg["DISK_BUSY_KBPS"],
            }
            busy, reasons = evaluate(probes, inhibit)
            peer = (first_inbound_peer(estab, listen, cfg["EXCLUDE_PORTS"], "")
                    or (ct_peers[0] if ct_peers else ""))
            gauges = {"ssh": ssh, "cpu": cpu, "net": net, "disk": disk, **read_hwmon_temps()}
            detail = idle_detail(reasons, peer, gauges)
            stats = ("conns=%d+%d ssh=%d vscode=%d cpu=%2.0f%% net=%.0fkB/s "
                     "disk=%.0fkB/s inhibit=%d ignored-vpn-up=%.0fkB/s"
                     % (conns, ct, ssh, int(vscode), cpu, net, disk, int(inhibit), vpn_up))
        except Exception as e:
            log("WARN probe cycle failed (%s) -> treating as BUSY" % e)
            busy, reasons, stats = True, ["probe_error"], "probe error"
            detail, gauges = "probe error", None

        write_idle_metrics(cfg["METRICS_FILE"], render_idle_metrics(reasons, detail, int(time.time()),
                                                                  parse_btime(cur["stat"]), gauges))
        # never part of the idle decision; a stale file shows as unavailable on fastpi
        if cfg["RESOURCES_FILE"]:
            try:
                figures = resource_figures(prev, cur, secs, cfg, cpu_busy_pct(prev["stat"], cur["stat"]))
                remember(history, figures, cfg["HISTORY_SAMPLES"])
                attach_history(figures, history)
                write_resources(cfg["RESOURCES_FILE"], render_resources(time.time(), secs, figures))
            except Exception as e:
                log("WARN: resources failed: %s" % e)
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
