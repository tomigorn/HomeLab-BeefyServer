#!/usr/bin/env python3
"""Tests for beefy_branch_order.py (emptier hot SSD first in the mergerfs branch order).
Run: ./test_beefy_branch_order.py
"""
import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bo", HERE / "beefy_branch_order.py")
bo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bo)

A, B, H = "/srv/.disks/ssd-hot", "/srv/.disks/ssd-hot2", "/srv/.disks/hdd-cold"
POOL = f"{A}=RW,1479G:{B}=RW,1479G:{H}=RW,2800G"
SWAPPED = f"{B}=RW,1479G:{A}=RW,1479G:{H}=RW,2800G"


class Decide(unittest.TestCase):
    def test_emptier_second_ssd_moves_first(self):
        self.assertEqual(bo.decide(POOL, {A: 60.0, B: 40.0}), SWAPPED)

    def test_already_emptier_first_keeps_order(self):
        self.assertIsNone(bo.decide(POOL, {A: 40.0, B: 60.0}))

    def test_within_two_points_keeps_order(self):
        self.assertIsNone(bo.decide(POOL, {A: 51.9, B: 50.0}))
        self.assertIsNone(bo.decide(SWAPPED, {A: 50.0, B: 51.9}))

    def test_just_over_two_points_swaps(self):
        self.assertEqual(bo.decide(SWAPPED, {A: 50.0, B: 52.1}), POOL)

    def test_options_of_each_branch_travel_with_it(self):
        s = f"{A}=RW,100G:{B}=NC,200G:{H}=RW,2800G"
        self.assertEqual(bo.decide(s, {A: 90.0, B: 10.0}), f"{B}=NC,200G:{A}=RW,100G:{H}=RW,2800G")

    def test_hdd_is_put_last(self):
        s = f"{A}=RW,1479G:{H}=RW,2800G:{B}=RW,1479G"
        self.assertEqual(bo.decide(s, {A: 40.0, B: 60.0}), POOL)

    def test_missing_ssd_is_an_error(self):
        with self.assertRaises(ValueError):
            bo.decide(f"{A}=RW,1479G:{H}=RW,2800G", {A: 40.0})


class Check(unittest.TestCase):
    def test_ok_when_emptier_first_and_hdd_last(self):
        self.assertEqual(bo.check(POOL, {A: 40.0, B: 60.0}), "ok")

    def test_ok_within_two_points(self):
        self.assertEqual(bo.check(POOL, {A: 51.0, B: 50.0}), "ok")

    def test_names_a_fuller_first_ssd(self):
        self.assertEqual(bo.check(POOL, {A: 60.0, B: 40.0}), "fuller-first")

    def test_names_hdd_not_last(self):
        self.assertEqual(bo.check(f"{H}=RW,2800G:{A}=RW,1479G:{B}=RW,1479G", {A: 40.0, B: 60.0}), "hdd-not-last")


class Used(unittest.TestCase):
    def test_used_percent_from_statvfs(self):
        class St:
            f_blocks, f_bavail = 1000, 250
        self.assertEqual(bo.used_pct("/x", statvfs=lambda p: St), 75.0)


if __name__ == "__main__":
    unittest.main()
