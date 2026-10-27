"""Unit tests for beefy_idle_watcher pure functions.

Fixtures are real samples captured from beefy (2026-06-25). Run:
    python3 -m unittest test_beefy_idle_watcher -v
"""
import unittest

import beefy_idle_watcher as biw
from beefy_idle_watcher import (
    parse_listen_ports, count_inbound, count_interactive_ssh, vscode_remote_active,
    cpu_busy_pct, net_kbps, disk_kbps,
    parse_published_ports, parse_local_addrs, count_ct_inbound,
    evaluate, update_idle, should_sleep, load_config,
    vpn_upload_kbps,
)


class TestConnections(unittest.TestCase):
    def test_parse_listen_ports_drops_loopback(self):
        txt = ("LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n"
               "LISTEN 0 4096 0.0.0.0:9001 0.0.0.0:*\n"
               "LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\n"
               "LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n"
               "LISTEN 0 4096 [::]:22 [::]:*\n"
               "LISTEN 0 4096 [::1]:631 [::]:*\n")
        self.assertEqual(parse_listen_ports(txt), {22, 9001})

    def test_count_inbound_excludes_ssh_and_outbound(self):
        txt = ("ESTAB 0 0 192.168.1.102:9001 192.168.1.50:55512\n"
               "ESTAB 0 0 192.168.1.102:22 192.168.1.2:39778\n"
               "ESTAB 0 0 192.168.1.102:51118 160.79.104.10:443\n")
        self.assertEqual(count_inbound(txt, {22, 9001}, {22}), 1)

    def test_count_inbound_none(self):
        self.assertEqual(count_inbound("", {22, 9001}, {22}), 0)


class TestConntrack(unittest.TestCase):
    PORTS = ("0.0.0.0:8090->8080/tcp, [::]:8090->8080/tcp\n"
             "8080/tcp\n"
             "0.0.0.0:8080-8081->8080-8081/tcp, 0.0.0.0:9696->9696/tcp, 8388/udp\n"
             "127.0.0.1:5000->5000/tcp, 0.0.0.0:53->53/udp\n")

    def test_published_ports_tcp_only_expands_ranges(self):
        self.assertEqual(parse_published_ports(self.PORTS),
                         {8090, 8080, 8081, 9696, 5000})

    def test_published_ports_empty(self):
        self.assertEqual(parse_published_ports(""), set())

    def test_local_addrs(self):
        txt = ("2: enp6s0    inet 192.168.1.102/24 brd 192.168.1.255 scope global enp6s0\n"
               "1: lo    inet 127.0.0.1/8 scope host lo\n"
               "5: br-1    inet 172.28.14.1/24 brd 172.28.14.255 scope global br-1\n")
        self.assertEqual(parse_local_addrs(txt),
                         {"192.168.1.102", "127.0.0.1", "172.28.14.1"})

    CT = (
        "tcp 6 431992 ESTABLISHED src=192.168.1.2 dst=192.168.1.102 sport=53840 dport=8090 src=172.28.14.4 dst=192.168.1.2 sport=8080 dport=53840 [ASSURED] mark=0 use=1\n"
        "tcp 6 431997 ESTABLISHED src=172.28.14.2 dst=172.65.90.26 sport=56090 dport=443 src=172.65.90.26 dst=192.168.1.102 sport=443 dport=56090 [ASSURED] mark=0 use=1\n"
        "tcp 6 431990 ESTABLISHED src=192.168.1.2 dst=192.168.1.102 sport=38198 dport=9001 src=172.18.0.2 dst=192.168.1.2 sport=9001 dport=38198 [ASSURED] mark=0 use=1\n"
        "tcp 6 431999 ESTABLISHED src=192.168.1.2 dst=192.168.1.102 sport=38102 dport=22 src=192.168.1.102 dst=192.168.1.2 sport=22 dport=38102 [ASSURED] mark=0 use=1\n"
        "tcp 6 100 ESTABLISHED src=172.28.14.4 dst=192.168.1.2 sport=40000 dport=8090 src=192.168.1.2 dst=172.28.14.4 sport=8090 dport=40000 [ASSURED] mark=0 use=1\n"
        "tcp 6 118 TIME_WAIT src=192.168.1.2 dst=192.168.1.102 sport=1 dport=8090 src=172.28.14.4 dst=192.168.1.2 sport=8080 dport=1 [ASSURED] mark=0 use=1\n")
    LOCAL = {"192.168.1.102", "127.0.0.1"}

    def test_counts_dnat_flow_to_published_port(self):
        self.assertEqual(count_ct_inbound(self.CT, self.LOCAL, {8090, 9001}, {22, 9001}), 1)

    def test_ignores_outbound_and_non_established_and_foreign_dst(self):
        self.assertEqual(count_ct_inbound(self.CT, self.LOCAL, {8090}, set()), 1)

    def test_exclude_port(self):
        self.assertEqual(count_ct_inbound(self.CT, self.LOCAL, {8090, 9001}, {8090, 9001}), 0)

    def test_stale_flow_ignored(self):
        stale = self.CT.replace("431992", "431000")
        self.assertEqual(count_ct_inbound(stale, self.LOCAL, {8090}, set(), 432000, 300), 0)
        self.assertEqual(count_ct_inbound(self.CT, self.LOCAL, {8090}, set(), 432000, 300), 1)

    def test_empty(self):
        self.assertEqual(count_ct_inbound("", self.LOCAL, {8090}, set()), 0)

    def test_unpublished_port_not_counted(self):
        self.assertEqual(count_ct_inbound(self.CT, self.LOCAL, {9999}, set()), 0)


class TestSessions(unittest.TestCase):
    # Real `ps -eo args` titles captured from beefy: @pts = interactive,
    # @notty = automation, [priv] = the parent, plus unrelated processes.
    PS = ("sshd-session: buntu@pts/0\n"
          "sshd-session: buntu@notty\n"
          "sshd-session: buntu [priv]\n"
          "sshd-session: root@pts/3\n"
          "-bash\n"
          "/usr/lib/systemd/systemd --user\n"
          "python3 -m http.server 8080\n")

    def test_counts_only_interactive_pts(self):
        self.assertEqual(count_interactive_ssh(self.PS), 2)   # buntu@pts/0, root@pts/3

    def test_no_interactive(self):
        self.assertEqual(count_interactive_ssh(""), 0)
        self.assertEqual(count_interactive_ssh("sshd-session: buntu@notty\n-bash\n"), 0)


class TestVSCode(unittest.TestCase):
    # Real `.vscode-server` node server line captured from beefy.
    SERVER = ("/home/buntu/.vscode-server/cli/servers/Stable-7e7950df/server/node "
              "/home/buntu/.vscode-server/cli/servers/Stable-7e7950df/server/out/"
              "server-main.js --connection-token=remotessh --start-server\n")

    def test_detects_running_server(self):
        ps = ("sshd-session: buntu@notty\n" + self.SERVER + "-bash\n")
        self.assertTrue(vscode_remote_active(ps))

    def test_absent_when_not_connected(self):
        # The CLI/agent processes alone (no running server-main.js) do not count.
        ps = ("sshd-session: buntu@notty\n"
              "/home/buntu/.vscode-server/code-93cfdd48 --cli-data-dir x agent host\n"
              "-bash\n")
        self.assertFalse(vscode_remote_active(ps))


class TestRates(unittest.TestCase):
    def test_cpu_busy_pct(self):
        a = "cpu  100 0 100 1000 0 0 0 0 0 0\n"
        b = "cpu  150 0 150 1700 0 0 0 0 0 0\n"   # busy +100, idle +700, total +800
        self.assertAlmostEqual(cpu_busy_pct(a, b), 12.5, places=1)

    def test_cpu_no_delta(self):
        a = "cpu  1 1 1 1 1 0 0 0 0 0\n"
        self.assertEqual(cpu_busy_pct(a, a), 0.0)

    def test_net_kbps(self):
        a = "enp6s0: 1000 0 0 0 0 0 0 0 2000 0\n"
        b = "enp6s0: 6000 0 0 0 0 0 0 0 7000 0\n"  # +5000 rx +5000 tx = 10000B/10s/1000
        self.assertAlmostEqual(net_kbps(a, b, "enp6s0", 10), 1.0, places=2)

    def test_net_missing_nic(self):
        self.assertEqual(net_kbps("lo: 1 2 3\n", "lo: 1 2 3\n", "enp6s0", 10), 0.0)

    def test_disk_kbps(self):
        a = "8 0 sda 0 0 1000 0 0 0 1000 0 0 0 0\n"
        b = "8 0 sda 0 0 3000 0 0 0 1000 0 0 0 0\n"  # +2000 sectors *512 /10s /1000
        self.assertAlmostEqual(disk_kbps(a, b, ["sda"], 10), 102.4, places=1)

    def test_disk_multi(self):
        a = ("8 0 sda 0 0 100 0 0 0 0 0 0\n"
             "8 16 sdb 0 0 0 0 0 0 100 0 0\n")
        b = ("8 0 sda 0 0 200 0 0 0 0 0 0\n"
             "8 16 sdb 0 0 0 0 0 0 300 0 0\n")  # sda +100 read, sdb +200 write = 300 sectors
        self.assertAlmostEqual(disk_kbps(a, b, ["sda", "sdb"], 10),
                               300 * 512 / 1000.0 / 10, places=2)

    def test_net_clamps_counter_reset(self):
        # b < a (NIC removed/re-added resets the counter) must read 0, not negative
        a = "enp6s0: 9000 0 0 0 0 0 0 0 9000 0\n"
        b = "enp6s0: 100 0 0 0 0 0 0 0 100 0\n"
        self.assertEqual(net_kbps(a, b, "enp6s0", 10), 0.0)

    def test_disk_clamps_counter_reset(self):
        a = "8 0 sda 0 0 9000 0 0 0 9000 0 0\n"
        b = "8 0 sda 0 0 100 0 0 0 100 0 0\n"
        self.assertEqual(disk_kbps(a, b, ["sda"], 10), 0.0)


class TestDecision(unittest.TestCase):
    def test_evaluate_inhibit_forces_busy(self):
        busy, reasons = evaluate(
            {"conns": False, "ssh": False, "cpu": False, "net": False, "disk": False},
            inhibit=True)
        self.assertTrue(busy)
        self.assertIn("inhibit", reasons)

    def test_evaluate_idle(self):
        busy, reasons = evaluate(
            {"conns": False, "ssh": False, "cpu": False, "net": False, "disk": False},
            inhibit=False)
        self.assertFalse(busy)
        self.assertEqual(reasons, [])

    def test_evaluate_names_busy_probes(self):
        busy, reasons = evaluate(
            {"conns": True, "ssh": False, "cpu": False, "net": False, "disk": True},
            inhibit=False)
        self.assertTrue(busy)
        self.assertEqual(set(reasons), {"conns", "disk"})

    def test_update_idle(self):
        self.assertEqual(update_idle(True, 1000, 1500), 1500)   # busy resets
        self.assertEqual(update_idle(False, 1000, 1500), 1000)  # idle keeps

    def test_should_sleep_boundary(self):
        self.assertFalse(should_sleep(1000, 1000 + 899, 15))    # 14m59s
        self.assertTrue(should_sleep(1000, 1000 + 900, 15))     # exactly 15m


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = load_config({})
        self.assertEqual(cfg["DRY_RUN"], True)
        self.assertEqual(cfg["IDLE_MINUTES"], 15)
        self.assertEqual(cfg["CPU_BUSY_PCT"], 15.0)
        self.assertEqual(cfg["DATA_DISKS"], ["sda", "sdb", "sdc"])
        self.assertEqual(cfg["EXCLUDE_PORTS"], {22})

    def test_overrides(self):
        cfg = load_config({"DRY_RUN": "0", "IDLE_MINUTES": "5",
                           "DATA_DISKS": "sda sdb", "EXCLUDE_PORTS": "22 9001"})
        self.assertEqual(cfg["DRY_RUN"], False)
        self.assertEqual(cfg["IDLE_MINUTES"], 5)
        self.assertEqual(cfg["DATA_DISKS"], ["sda", "sdb"])
        self.assertEqual(cfg["EXCLUDE_PORTS"], {22, 9001})

    def test_tolerates_inline_comments_and_quotes(self):
        # systemd EnvironmentFile keeps inline '# comments' and quotes literal —
        # the parser must survive them (regression: ValueError on int()).
        cfg = load_config({
            "SAMPLE_INTERVAL": "60        # seconds between samples",
            "CPU_BUSY_PCT": "15           # percent",
            "DATA_DISKS": '"sda sdb sdc"  # data disks, not the OS nvme',
            "EXCLUDE_PORTS": '"22"        # ssh',
            "DRY_RUN": "1                 # log only",
        })
        self.assertEqual(cfg["SAMPLE_INTERVAL"], 60)
        self.assertEqual(cfg["CPU_BUSY_PCT"], 15.0)
        self.assertEqual(cfg["DATA_DISKS"], ["sda", "sdb", "sdc"])
        self.assertEqual(cfg["EXCLUDE_PORTS"], {22})
        self.assertEqual(cfg["DRY_RUN"], True)


class TestUploadsIgnored(unittest.TestCase):
    # VPN container netns /proc/<pid>/net/dev: eth0 = encrypted uplink, tun0 = tunnel payload
    def dev(self, eth_rx, eth_tx, tun_rx, tun_tx):
        return ("    lo: 5 0 0 0 0 0 0 0 5 0\n"
                "  eth0: %d 0 0 0 0 0 0 0 %d 0\n"
                "  tun0: %d 0 0 0 0 0 0 0 %d 0\n" % (eth_rx, eth_tx, tun_rx, tun_tx))

    def test_seeding_counts_as_vpn_upload(self):
        a = self.dev(0, 0, 0, 0)
        b = self.dev(40_000, 1_060_000, 30_000, 1_000_000)  # 1 MB/s seeding for 1 s, acks in
        self.assertAlmostEqual(vpn_upload_kbps(a, b, "eth0", "tun0", 1), 1070.0, places=1)

    def test_download_through_vpn_still_counts(self):
        a = self.dev(0, 0, 0, 0)
        b = self.dev(1_050_000, 30_000, 1_000_000, 20_000)  # 1 MB/s download for 1 s
        self.assertAlmostEqual(vpn_upload_kbps(a, b, "eth0", "tun0", 1), 80.0, places=1)

    def test_missing_vpn_namespace_subtracts_nothing(self):
        self.assertEqual(vpn_upload_kbps(None, self.dev(9, 9, 0, 0), "eth0", "tun0", 1), 0.0)
        self.assertEqual(vpn_upload_kbps(self.dev(0, 0, 0, 0), None, "eth0", "tun0", 1), 0.0)

    def test_vpn_counter_reset_subtracts_nothing(self):
        a = self.dev(9_000_000, 9_000_000, 0, 0)
        b = self.dev(100, 100, 0, 0)  # container restarted: new netns, counters from 0
        self.assertEqual(vpn_upload_kbps(a, b, "eth0", "tun0", 10), 0.0)


    def test_config_defaults_off_and_overrides(self):
        cfg = load_config({})
        self.assertEqual(cfg["VPN_CONTAINER"], "")
        cfg = load_config({"VPN_CONTAINER": "movie-gluetun", "VPN_UPLINK": "eth0", "VPN_TUNNEL": "tun0"})
        self.assertEqual((cfg["VPN_CONTAINER"], cfg["VPN_UPLINK"], cfg["VPN_TUNNEL"]),
                         ("movie-gluetun", "eth0", "tun0"))


class TestIdleMetrics(unittest.TestCase):
    def test_render_marks_active_reasons(self):
        out = biw.render_idle_metrics(["conns", "net"], "192.168.1.50:55512", 1000)
        self.assertIn('beefy_idle_busy{reason="conns"} 1', out)
        self.assertIn('beefy_idle_busy{reason="net"} 1', out)
        self.assertIn('beefy_idle_busy{reason="disk"} 0', out)
        self.assertIn('beefy_idle_info{detail="192.168.1.50:55512"} 1', out)
        self.assertIn("beefy_idle_updated_timestamp_seconds 1000", out)

    def test_render_idle_has_all_zero_and_empty_detail(self):
        out = biw.render_idle_metrics([], "", 5)
        self.assertNotIn("} 1\n", out.replace("beefy_idle_info{detail=\"\"} 1\n", ""))
        self.assertIn('beefy_idle_info{detail=""} 1', out)

    def test_render_covers_every_reason_label(self):
        out = biw.render_idle_metrics([], "", 5)
        for r in ("conns", "ssh", "vscode", "cpu", "net", "disk", "inhibit", "probe_error"):
            self.assertIn('beefy_idle_busy{reason="%s"} 0' % r, out)

    def test_detail_is_sanitised_and_short(self):
        out = biw.render_idle_metrics(["conns"], 'a"b\\c\nd{e}' + "x" * 200, 5)
        line = [l for l in out.splitlines() if l.startswith("beefy_idle_info")][0]
        detail = line.split('"')[1]
        self.assertLessEqual(len(detail), 80)
        for ch in '"\\{}\n':
            self.assertNotIn(ch, detail)

    def test_parse_btime_from_proc_stat(self):
        stat = "cpu  1 2 3 4\nintr 5\nctxt 99\nbtime 1793070000\nprocesses 7\n"
        self.assertEqual(biw.parse_btime(stat), 1793070000)

    def test_parse_btime_missing_is_none(self):
        self.assertIsNone(biw.parse_btime("cpu  1 2 3 4\n"))
        self.assertIsNone(biw.parse_btime(""))

    def test_render_includes_boot_time(self):
        out = biw.render_idle_metrics([], "", 5, 1793070000)
        self.assertIn("# HELP beefy_boot_time_seconds ", out)
        self.assertIn("# TYPE beefy_boot_time_seconds gauge", out)
        self.assertIn("beefy_boot_time_seconds 1793070000\n", out)

    def test_render_without_boot_time_omits_metric(self):
        self.assertNotIn("beefy_boot_time_seconds", biw.render_idle_metrics([], "", 5))

    def test_first_inbound_peer_from_ss(self):
        txt = ("ESTAB 0 0 192.168.1.102:22 192.168.1.2:39778\n"
               "ESTAB 0 0 192.168.1.102:8096 192.168.1.50:55512\n")
        self.assertEqual(biw.first_inbound_peer(txt, {22, 8096}, {22}, ""), "192.168.1.50:55512")

    def test_first_inbound_peer_falls_back_to_conntrack(self):
        ct = ("tcp 6 431992 ESTABLISHED src=192.168.1.2 dst=192.168.1.102 sport=53840 "
              "dport=8090 src=172.28.14.4 dst=192.168.1.2 sport=8080 dport=53840 [ASSURED]\n")
        self.assertEqual(
            biw.first_inbound_peer("", {22}, {22}, ct, {"192.168.1.102"}, {8090}),
            "192.168.1.2:53840")

    def test_first_inbound_peer_none(self):
        self.assertEqual(biw.first_inbound_peer("", {22}, {22}, ""), "")

    def test_detail_text_per_reason(self):
        self.assertEqual(biw.idle_detail(["inhibit"], "", {}), "keep-awake file")
        self.assertEqual(biw.idle_detail(["vscode"], "", {}), "VS Code Remote")
        self.assertEqual(biw.idle_detail(["conns"], "1.2.3.4:5", {}), "conn from 1.2.3.4:5")
        self.assertEqual(biw.idle_detail(["ssh"], "", {"ssh": 2}), "interactive ssh x2")
        self.assertEqual(biw.idle_detail(["cpu", "net"], "", {"cpu": 31.4, "net": 512.0}),
                         "cpu 31%, net 512 kB/s")
        self.assertEqual(biw.idle_detail([], "", {}), "")

    def test_write_idle_metrics_is_atomic_and_readable(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "idle.prom")
        biw.write_idle_metrics(path, "x 1\n")
        self.assertEqual(open(path).read(), "x 1\n")
        self.assertEqual(os.listdir(d), ["idle.prom"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_write_failure_is_swallowed(self):
        biw.write_idle_metrics("/nonexistent-dir/idle.prom", "x\n")

    def test_config_metrics_file(self):
        self.assertEqual(load_config({}) ["METRICS_FILE"], "/run/beefy-idle.prom")
        self.assertEqual(load_config({"METRICS_FILE": "/tmp/x.prom"})["METRICS_FILE"], "/tmp/x.prom")


class TestVersion(unittest.TestCase):
    def test_version_is_set(self):
        self.assertTrue(biw.VERSION and biw.VERSION[0].isdigit())

    def test_start_banner_includes_version_and_config(self):
        banner = biw.start_banner("9.9.9", load_config({}))
        self.assertIn("v9.9.9", banner)
        self.assertIn("dry_run", banner)
        self.assertIn("idle=15m", banner)


class TestClock(unittest.TestCase):
    def test_elapsed_clock_is_monotonic(self):
        # Idle timing must use a monotonic clock so a wall-clock step (NTP / manual
        # set-time) can't shorten or extend how long beefy is considered idle.
        self.assertIs(biw._now, biw.time.monotonic)

    def test_snapshot_time_comes_from_the_elapsed_clock(self):
        # _snapshot()["t"] (the rate-math denominator) must read the monotonic seam,
        # not the wall clock.
        real = biw._now
        try:
            biw._now = lambda: 4242.0
            self.assertEqual(biw._snapshot(load_config({}))["t"], 4242.0)
        finally:
            biw._now = real


if __name__ == "__main__":
    unittest.main()
