#!/usr/bin/env python3
"""download-now/retention_rules.py: storage retention rules (README "Storage retention") — keep-forever default by category, stage dates, postponement, the space rule, keep overrides and the grace after switch-on.
Usage: python3 tests/test_retention_rules.py"""
import datetime
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "download-now"))
import retention_rules as rr  # noqa: E402

D = datetime.date


class Reasons(unittest.TestCase):
    def test_old_is_twelve_years_or_more(self):
        self.assertEqual(rr.reasons("movie", 2014, 2026), ["old"])
        self.assertEqual(rr.reasons("movie", 2015, 2026, niche_votes=5000), [])

    def test_niche_is_few_votes_and_at_least_two_years_old(self):
        self.assertEqual(rr.reasons("movie", 2024, 2026, niche_votes=99), ["niche"])
        self.assertEqual(rr.reasons("movie", 2025, 2026, niche_votes=10), [])
        self.assertEqual(rr.reasons("movie", 2024, 2026, niche_votes=100), [])
        self.assertEqual(rr.reasons("tv", 2020, 2026, niche_votes=999), ["niche"])
        self.assertEqual(rr.reasons("tv", 2020, 2026, niche_votes=1000), [])

    def test_unknown_votes_is_not_niche(self):
        self.assertEqual(rr.reasons("movie", 2020, 2026), [])

    def test_swiss_german_and_older_versions_profile_are_niche(self):
        self.assertEqual(rr.reasons("movie", 2025, 2026, niche_votes=5000, tags=["audio-swiss-german"]), ["niche"])
        self.assertEqual(rr.reasons("movie", 2025, 2026, niche_votes=5000, profile="4K preferred · older versions allowed"), ["niche"])
        self.assertEqual(rr.reasons("movie", 2025, 2026, niche_votes=5000, profile="Swiss German (Mundart) only - any quality"), ["niche"])

    def test_goated_needs_rating_and_votes(self):
        self.assertEqual(rr.reasons("movie", 2025, 2026, niche_votes=5000, rating=8.0, votes=25000), ["goated"])
        self.assertEqual(rr.reasons("movie", 2025, 2026, niche_votes=5000, rating=8.4, votes=24999), [])
        self.assertEqual(rr.reasons("movie", 2025, 2026, niche_votes=5000, rating=7.9, votes=900000), [])

    def test_several_reasons_in_fixed_order(self):
        self.assertEqual(rr.reasons("movie", 1994, 2026, niche_votes=50, rating=9.3, votes=3000000), ["old", "niche", "goated"])

    def test_no_year_counts_neither_old_nor_niche(self):
        self.assertEqual(rr.reasons("movie", 0, 2026, niche_votes=1), [])
        self.assertEqual(rr.reasons("movie", None, 2026, niche_votes=1), [])


class Schedule(unittest.TestCase):
    def test_thirty_days_ssd_then_hdd_then_delete_at_ninety(self):
        s = rr.schedule(D(2026, 11, 1))
        self.assertEqual(s, {"imported": "2026-11-01", "ssdUntil": "2026-12-01", "hddFrom": "2026-12-01", "due": "2027-01-30"})


def entry(due="2027-01-30", default=False, **kw):
    return {"due": due, "default": default, "reasons": ["old"] if default else [], **kw}


class Effective(unittest.TestCase):
    today = D(2026, 12, 1)

    def test_temporary_title_leaves_on_its_due_date(self):
        e = rr.effective(entry(), None, self.today)
        self.assertEqual((e["state"], e["keep"], e["source"], e["deleteOn"], e["soon"], e["overdue"]), ("temporary", False, "default", "2027-01-30", False, False))

    def test_default_keep_forever_has_no_date(self):
        e = rr.effective(entry(default=True), None, self.today)
        self.assertEqual((e["state"], e["keep"], e["source"], e["deleteOn"]), ("keep", True, "default", None))

    def test_override_beats_the_default_both_ways(self):
        self.assertEqual(rr.effective(entry(), True, self.today)["state"], "keep")
        self.assertEqual(rr.effective(entry(), True, self.today)["source"], "override")
        e = rr.effective(entry(default=True), False, self.today)
        self.assertEqual((e["state"], e["source"], e["deleteOn"]), ("temporary", "override", "2027-01-30"))

    def test_test_film_is_always_kept(self):
        e = rr.effective(entry(protected=True), False, self.today)
        self.assertEqual((e["state"], e["source"], e["deleteOn"]), ("keep", "protected", None))

    def test_leaving_soon_within_seven_days(self):
        self.assertTrue(rr.effective(entry(due="2026-12-08"), None, self.today)["soon"])
        self.assertFalse(rr.effective(entry(due="2026-12-09"), None, self.today)["soon"])

    def test_overdue_is_soon_and_overdue(self):
        e = rr.effective(entry(due="2026-11-20"), None, self.today)
        self.assertEqual((e["soon"], e["overdue"]), (True, True))

    def test_space_rule_brings_the_date_forward(self):
        e = rr.effective(entry(space="2026-12-02"), None, self.today)
        self.assertEqual((e["deleteOn"], e["why"]), ("2026-12-02", "space"))

    def test_postponed_at_most_thirty_days(self):
        e = rr.effective(entry(due="2026-12-05", postponed="in-progress"), None, self.today)
        self.assertEqual((e["deleteOn"], e["postponed"], e["why"]), ("2027-01-04", "in-progress", "postponed"))

    def test_grace_after_switch_on(self):
        e = rr.effective(entry(due="2026-11-01"), None, self.today, enabled_on=D(2026, 11, 30))
        self.assertEqual((e["deleteOn"], e["why"]), ("2026-12-14", "grace"))
        self.assertEqual(rr.effective(entry(), None, self.today, enabled_on=D(2026, 11, 30))["deleteOn"], "2027-01-30")

    def test_unknown_due_date_is_never_deleted(self):
        e = rr.effective(entry(due=None), None, self.today)
        self.assertEqual((e["state"], e["deleteOn"], e["soon"]), ("temporary", None, False))


class Keys(unittest.TestCase):
    def test_key_and_parse(self):
        self.assertEqual(rr.key("movie", 603), "movie/603")
        self.assertEqual(rr.parse_key("tv/1399"), ("tv", 1399))
        for bad in ("movie/0", "film/1", "tv/1/2", "movie/x", "", None, "movie/12345678901"):
            self.assertIsNone(rr.parse_key(bad), bad)

    def test_test_film(self):
        self.assertTrue(rr.protected("movie/653", []))
        self.assertTrue(rr.protected("movie/1", ["keep-test"]))
        self.assertFalse(rr.protected("tv/653", []))


if __name__ == "__main__":
    unittest.main()
