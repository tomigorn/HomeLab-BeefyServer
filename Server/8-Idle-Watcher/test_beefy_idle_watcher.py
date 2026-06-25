"""Unit tests for beefy_idle_watcher pure functions.

Fixtures are real samples captured from beefy (2026-06-25). Run:
    python3 -m unittest test_beefy_idle_watcher -v
"""
import unittest

from beefy_idle_watcher import (
    parse_listen_ports, count_inbound, interactive_sessions,
    cpu_busy_pct, net_kbps, disk_kbps,
    evaluate, update_idle, should_sleep, load_config,
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


class TestSessions(unittest.TestCase):
    def test_interactive_sessions(self):
        self.assertEqual(interactive_sessions(""), 0)
        self.assertEqual(interactive_sessions("\n  \n"), 0)
        self.assertEqual(
            interactive_sessions("buntu pts/0 2026-06-25 22:10 (192.168.1.50)\n"), 1)


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


if __name__ == "__main__":
    unittest.main()
