#!/usr/bin/env python3
"""Baseline logic of lint.py. Usage: python3 .github/scripts/test_lint.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lint  # noqa: E402


def f(file, code, line=1):
    return {"file": file, "code": code, "line": line, "message": "m"}


def entry(file, code, count, reason="r"):
    return {"file": file, "code": code, "count": count, "reason": reason}


class Evaluate(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(lint.evaluate([], [])[0], [])

    def test_new_finding_fails(self):
        bad, _ = lint.evaluate([f("a.sh", "SC2086")], [])
        self.assertEqual(len(bad), 1)
        self.assertIn("a.sh:1", bad[0])

    def test_baselined_passes(self):
        bad, notes = lint.evaluate([f("a.sh", "SC2086")], [entry("a.sh", "SC2086", 1)])
        self.assertEqual((bad, notes), ([], []))

    def test_over_baseline_fails(self):
        found = [f("a.sh", "SC2086", 1), f("a.sh", "SC2086", 2)]
        bad, _ = lint.evaluate(found, [entry("a.sh", "SC2086", 1)])
        self.assertEqual(len(bad), 2)
        self.assertIn("baseline allows 1", bad[0])

    def test_other_code_in_same_file_fails(self):
        bad, _ = lint.evaluate([f("a.sh", "SC2164")], [entry("a.sh", "SC2086", 1)])
        self.assertTrue(any("SC2164" in b for b in bad))

    def test_stale_entry_fails(self):
        bad, _ = lint.evaluate([], [entry("a.sh", "SC2086", 1)])
        self.assertEqual(len(bad), 1)
        self.assertIn("stale", bad[0])

    def test_fewer_than_baseline_is_a_note(self):
        bad, notes = lint.evaluate([f("a.sh", "SC2086")], [entry("a.sh", "SC2086", 3)])
        self.assertEqual(bad, [])
        self.assertEqual(len(notes), 1)

    def test_baseline_needs_reason(self):
        with self.assertRaises(ValueError):
            lint.check_entries([entry("a.sh", "SC2086", 1, reason=" ")])


class Parse(unittest.TestCase):
    def test_shellcheck_json1(self):
        out = '{"comments":[{"file":"a.sh","line":3,"level":"warning","code":2086,"message":"x"}]}'
        self.assertEqual(lint.parse_shellcheck(out), [{"file": "a.sh", "line": 3, "code": "SC2086", "message": "x"}])

    def test_hadolint_json_drops_info(self):
        out = '[{"file":"D","line":4,"level":"warning","code":"DL3018","message":"x"},{"file":"D","line":5,"level":"info","code":"DL3066","message":"y"}]'
        self.assertEqual([x["code"] for x in lint.parse_hadolint(out)], ["DL3018"])


if __name__ == "__main__":
    unittest.main()
