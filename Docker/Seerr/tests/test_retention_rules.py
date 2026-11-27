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

    def test_older_versions_profile_of_an_old_title_is_just_old(self):
        self.assertEqual(rr.reasons("movie", 1990, 2026, niche_votes=5000, profile="4K preferred · older versions allowed"), ["old"])

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

    def test_fan_mark_keeps_a_temporary_title(self):
        e = rr.effective(entry(), True, self.today)
        self.assertEqual((e["state"], e["keep"], e["source"], e["deleteOn"]), ("keep", True, "fan", None))

    def test_automatic_keep_forever_cannot_be_switched_off(self):
        for ov in (False, None, True):
            e = rr.effective(entry(default=True), ov, self.today)
            self.assertEqual((e["state"], e["source"], e["deleteOn"]), ("keep", "default", None), ov)

    def test_no_mark_is_temporary(self):
        self.assertEqual(rr.effective(entry(), False, self.today)["state"], "temporary")

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


NOW = datetime.datetime(2026, 11, 27, 12, 0, tzinfo=datetime.timezone.utc)


def at(days_ago):
    return (NOW - datetime.timedelta(days=days_ago)).isoformat(timespec="seconds")


class Marks(unittest.TestCase):
    def test_marks_of_a_record(self):
        rec = {"keep": True, "marks": {"5": {"at": at(1), "name": "Jane"}, "9": {"at": at(2), "name": "Max"}}}
        self.assertEqual(rr.marks(rec), {5: {"at": at(1), "name": "Jane"}, 9: {"at": at(2), "name": "Max"}})

    def test_old_keep_record_is_a_mark_by_whoever_ticked_it(self):
        self.assertEqual(rr.marks({"keep": True, "by": 7, "at": at(3)}), {7: {"at": at(3), "name": None}})
        self.assertEqual(rr.marks({"keep": True}), {0: {"at": None, "name": None}})

    def test_old_let_it_leave_record_is_no_mark(self):
        self.assertEqual(rr.marks({"keep": False, "by": 5, "at": at(3)}), {})
        self.assertEqual(rr.marks(None), {})
        self.assertEqual(rr.marks({"marks": {"x": {}, "0": {}, "3": "no"}}), {})

    def test_five_marks_per_person_in_seven_days(self):
        log = [{"by": 5, "key": f"movie/{i}", "at": at(i)} for i in range(1, 6)] + [{"by": 6, "key": "movie/9", "at": at(1)}]
        self.assertEqual(rr.marks_used(log, 5, NOW), 5)
        self.assertEqual(rr.MARK_WEEK_MAX, 5)
        self.assertFalse(rr.may_add_mark(log, 5, NOW))
        self.assertTrue(rr.may_add_mark(log, 6, NOW))

    def test_older_than_seven_days_frees_the_slot(self):
        log = [{"by": 5, "key": "movie/1", "at": at(7.5)}, {"by": 5, "key": "movie/2", "at": at(6)}]
        self.assertEqual(rr.marks_used(log, 5, NOW), 1)

    def test_removing_a_mark_keeps_the_slot_used(self):
        log = [{"by": 5, "key": "movie/1", "at": at(1)}]
        self.assertEqual(rr.marks_used(log, 5, NOW), 1)

    def test_only_the_setter_removes_a_mark(self):
        rec = {"keep": True, "marks": {"5": {"at": at(1), "name": "Jane"}}}
        self.assertTrue(rr.may_remove_mark(rec, 5))
        self.assertFalse(rr.may_remove_mark(rec, 1))

    def test_override_from_marks(self):
        self.assertTrue(rr.fan_override({"marks": {"5": {"at": at(1)}}}))
        self.assertIsNone(rr.fan_override({"keep": False, "by": 5}))


class Plan(unittest.TestCase):
    def plan(self, **kw):
        args = {"key": "movie/10", "tags": [], "entry": entry(), "setters": {}, "watchers": [], "deleter": 5}
        args.update(kw)
        return rr.delete_plan(**args)

    def test_temporary_title_goes_to_the_bin_at_once(self):
        self.assertEqual(self.plan(), {"refused": None, "asks": []})

    def test_never_the_test_film_keep_test_or_automatic_keep(self):
        self.assertEqual(self.plan(key="movie/653")["refused"], "protected")
        self.assertEqual(self.plan(tags=["keep-test"])["refused"], "protected")
        self.assertEqual(self.plan(entry=entry(protected=True))["refused"], "protected")
        self.assertEqual(self.plan(entry=entry(default=True))["refused"], "kept")

    def test_not_on_the_server(self):
        self.assertEqual(self.plan(entry=None)["refused"], "library")

    def test_fan_favourite_asks_each_setter_but_not_the_deleter(self):
        p = self.plan(setters={7: "Jane", 8: "Max", 5: "Me"})
        self.assertEqual(p["asks"], [{"reason": "fan", "approvers": [7, 8], "names": ["Jane", "Max"]}])
        self.assertEqual(self.plan(setters={5: "Me"})["asks"], [])

    def test_mark_without_a_recorded_setter_is_answered_by_the_owner(self):
        self.assertEqual(self.plan(setters={0: None})["asks"], [{"reason": "fan", "approvers": [rr.OWNER], "names": [None]}])

    def test_someone_else_watching_asks_that_viewer_own_watching_not(self):
        p = self.plan(watchers=[(9, "Ann"), (5, "Me"), (9, "Ann")])
        self.assertEqual(p["asks"], [{"reason": "watching", "approvers": [9], "names": ["Ann"]}])

    def test_viewer_without_account_is_answered_by_the_owner(self):
        p = self.plan(watchers=[(None, None)])
        self.assertEqual(p["asks"], [{"reason": "watching", "approvers": [rr.OWNER], "names": [None]}])
        self.assertEqual(self.plan(watchers=[(None, None)], deleter=rr.OWNER)["asks"], [])

    def test_fan_first_then_watchers(self):
        p = self.plan(setters={7: "Jane"}, watchers=[(9, "Ann")])
        self.assertEqual([a["reason"] for a in p["asks"]], ["fan", "watching"])

    def test_limits(self):
        self.assertEqual((rr.DELETE_WEEK_MAX, rr.ASK_DAYS, rr.BIN_DAYS, rr.WATCH_DAYS), (10, 7, 7, 30))


if __name__ == "__main__":
    unittest.main()
