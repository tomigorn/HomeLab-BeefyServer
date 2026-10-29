#!/usr/bin/env python3
"""Tests for smart_exporter.py.   python3 test_smart_exporter.py"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smart_exporter as ex  # noqa: E402

LSBLK = {"blockdevices": [
    {"name": "sda", "type": "disk", "tran": "usb", "rota": True, "mountpoints": [], "model": "Expansion",
     "children": [{"name": "sda1", "type": "part", "mountpoints": ["/mnt/seagate-black"]}]},
    {"name": "sdc", "type": "disk", "tran": "sata", "rota": True, "mountpoints": [None], "model": "ST30000NM004K",
     "children": [{"name": "sdc1", "type": "part", "mountpoints": ["/srv/.disks/hdd-cold"]}]},
    {"name": "nvme0n1", "type": "disk", "tran": "nvme", "rota": False, "mountpoints": [], "model": "PM961",
     "children": [{"name": "nvme0n1p1", "type": "part", "mountpoints": ["/boot/firmware"]},
                  {"name": "nvme0n1p2", "type": "part", "mountpoints": ["/"]}]},
    {"name": "sdd", "type": "disk", "tran": "usb", "rota": False, "mountpoints": [], "model": "Stick"},
    {"name": "loop0", "type": "loop", "mountpoints": ["/snap/x"]},
    {"name": "zram0", "type": "disk", "mountpoints": ["[SWAP]"]},
]}

SSD = {"smartctl": {"exit_status": 0}, "model_name": "Samsung SSD 870 QVO 8TB", "device": {"type": "sat", "protocol": "ATA"},
       "rotation_rate": 0, "smart_status": {"passed": True}, "temperature": {"current": 36},
       "power_on_time": {"hours": 10989}, "power_cycle_count": 970,
       "ata_smart_attributes": {"table": [
           {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "raw": {"value": 0}},
           {"id": 177, "name": "Wear_Leveling_Count", "value": 99, "raw": {"value": 7}},
           {"id": 187, "name": "Uncorrectable_Error_Cnt", "value": 100, "raw": {"value": 0}},
           {"id": 199, "name": "CRC_Error_Count", "value": 100, "raw": {"value": 2}}]}}
HDD = {"smartctl": {"exit_status": 0}, "model_name": "ST30000NM004K", "device": {"type": "sat", "protocol": "ATA"},
       "rotation_rate": 7200, "smart_status": {"passed": False}, "temperature": {"current": 41},
       "power_on_time": {"hours": 900}, "power_cycle_count": 481,
       "ata_smart_attributes": {"table": [
           {"id": 4, "name": "Start_Stop_Count", "value": 99, "raw": {"value": 956}},
           {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "raw": {"value": 8}},
           {"id": 197, "name": "Current_Pending_Sector", "value": 100, "raw": {"value": 1}},
           {"id": 198, "name": "Offline_Uncorrectable", "value": 100, "raw": {"value": 0}}]}}
NVME = {"smartctl": {"exit_status": 0}, "model_name": "Samsung SSD 970 EVO 1TB", "device": {"type": "nvme", "protocol": "NVMe"},
        "smart_status": {"passed": True}, "temperature": {"current": 35}, "power_on_time": {"hours": 7559}, "power_cycle_count": 2938,
        "nvme_smart_health_information_log": {"critical_warning": 0, "percentage_used": 2, "media_errors": 0, "available_spare": 100}}
ASLEEP = {"smartctl": {"exit_status": 2, "messages": [{"string": "Device is in SLEEP mode, exit(2)"}]}, "device": {"type": "sat"}}


class Discover(unittest.TestCase):
    def test_disks_named_by_mountpoint_root_is_system(self):
        self.assertEqual(ex.discover(LSBLK), [
            {"dev": "/dev/sda", "disk": "seagate-black", "rota": True, "tran": "usb", "model": "Expansion"},
            {"dev": "/dev/sdc", "disk": "hdd-cold", "rota": True, "tran": "sata", "model": "ST30000NM004K"},
            {"dev": "/dev/nvme0n1", "disk": "system", "rota": False, "tran": "nvme", "model": "PM961"},
            {"dev": "/dev/sdd", "disk": "sdd", "rota": False, "tran": "usb", "model": "Stick"}])


class Parse(unittest.TestCase):
    def test_sata_ssd(self):
        v = ex.parse(SSD, rota=False)
        self.assertEqual(v["type"], "ssd")
        self.assertEqual((v["healthy"], v["temperature_celsius"], v["power_on_hours"], v["power_cycles"]), (1, 36, 10989, 970))
        self.assertEqual((v["reallocated_sectors"], v["media_errors"], v["crc_errors"]), (0, 0, 2))
        self.assertAlmostEqual(v["wear_ratio"], 0.01)
        self.assertNotIn("pending_sectors", v)

    def test_hdd(self):
        v = ex.parse(HDD, rota=True)
        self.assertEqual(v["type"], "hdd")
        self.assertEqual((v["healthy"], v["start_stop_count"], v["reallocated_sectors"], v["pending_sectors"], v["offline_uncorrectable"]), (0, 956, 8, 1, 0))
        self.assertNotIn("wear_ratio", v)

    def test_nvme(self):
        v = ex.parse(NVME, rota=False)
        self.assertEqual(v["type"], "nvme")
        self.assertEqual((v["media_errors"], v["nvme_critical_warning"], v["power_cycles"]), (0, 0, 2938))
        self.assertAlmostEqual(v["wear_ratio"], 0.02)

    def test_asleep_is_none(self):
        self.assertIsNone(ex.parse(ASLEEP, rota=True))

    def test_no_smart_raises(self):
        with self.assertRaises(ValueError):
            ex.parse({"smartctl": {"exit_status": 1}}, rota=False)


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")

    def run_once(self, outputs, now):
        disks = [{"dev": "/dev/sdc", "disk": "hdd-cold", "rota": True, "tran": "sata", "model": "ST30000NM004K"},
                 {"dev": "/dev/nvme0n1", "disk": "system", "rota": False, "tran": "nvme", "model": "Samsung 970"}]
        return ex.collect("beefy", disks, lambda dev: outputs[dev], self.state, now)

    def test_awake_values_then_cached_while_asleep(self):
        text = self.run_once({"/dev/sdc": HDD, "/dev/nvme0n1": NVME}, 1000)
        self.assertIn('smart_standby{host="beefy",disk="hdd-cold"} 0', text)
        self.assertIn('smart_pending_sectors{host="beefy",disk="hdd-cold"} 1', text)
        self.assertIn('smart_device_info{host="beefy",disk="hdd-cold",model="ST30000NM004K",type="hdd",transport="sata"} 1', text)
        self.assertIn('smart_last_read_timestamp_seconds{host="beefy",disk="hdd-cold"} 1000', text)
        self.assertIn('smart_exporter_errors{host="beefy"} 0', text)
        text = self.run_once({"/dev/sdc": ASLEEP, "/dev/nvme0n1": NVME}, 2000)
        self.assertIn('smart_standby{host="beefy",disk="hdd-cold"} 1', text)
        self.assertIn('smart_pending_sectors{host="beefy",disk="hdd-cold"} 1', text)
        self.assertIn('smart_last_read_timestamp_seconds{host="beefy",disk="hdd-cold"} 1000', text)
        self.assertIn('smart_last_read_timestamp_seconds{host="beefy",disk="system"} 2000', text)
        self.assertIn('smart_exporter_last_run_timestamp_seconds{host="beefy"} 2000', text)

    def test_asleep_never_read_has_only_standby(self):
        text = self.run_once({"/dev/sdc": ASLEEP, "/dev/nvme0n1": NVME}, 1000)
        self.assertIn('smart_standby{host="beefy",disk="hdd-cold"} 1', text)
        self.assertNotIn('smart_healthy{host="beefy",disk="hdd-cold"}', text)

    def test_unreadable_disk_counts_as_error_and_keeps_cache(self):
        self.run_once({"/dev/sdc": HDD, "/dev/nvme0n1": NVME}, 1000)
        text = self.run_once({"/dev/sdc": {"smartctl": {"exit_status": 1}}, "/dev/nvme0n1": NVME}, 2000)
        self.assertIn('smart_exporter_errors{host="beefy"} 1', text)
        self.assertIn('smart_reallocated_sectors{host="beefy",disk="hdd-cold"} 8', text)

    def test_timestamps_keep_full_precision(self):
        text = self.run_once({"/dev/sdc": HDD, "/dev/nvme0n1": NVME}, 1793233657.5)
        self.assertIn('smart_exporter_last_run_timestamp_seconds{host="beefy"} 1793233657', text)
        self.assertIn('smart_last_read_timestamp_seconds{host="beefy",disk="system"} 1793233657', text)

    def test_state_file_is_json(self):
        self.run_once({"/dev/sdc": HDD, "/dev/nvme0n1": NVME}, 1000)
        with open(self.state) as f:
            self.assertEqual(json.load(f)["hdd-cold"]["ts"], 1000)


class Smartctl(unittest.TestCase):
    def test_command_never_wakes(self):
        self.assertEqual(ex.smartctl_cmd("/dev/sdc")[:4], ["smartctl", "-n", "standby", "-j"])


if __name__ == "__main__":
    unittest.main()
