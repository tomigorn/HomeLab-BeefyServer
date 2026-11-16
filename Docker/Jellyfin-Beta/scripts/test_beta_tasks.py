import unittest

import beta_tasks


class Keep(unittest.TestCase):
    def test_only_startup_triggers_stay(self):
        task = {"Triggers": [{"Type": "StartupTrigger"}, {"Type": "DailyTrigger", "TimeOfDayTicks": 1}, {"Type": "IntervalTrigger", "IntervalTicks": 2}]}
        self.assertEqual(beta_tasks.keep(task), [{"Type": "StartupTrigger"}])

    def test_changes_lists_only_tasks_with_timed_triggers(self):
        tasks = [{"Id": "a", "Name": "Scan", "Triggers": [{"Type": "DailyTrigger"}]}, {"Id": "b", "Name": "Startup", "Triggers": [{"Type": "StartupTrigger"}]}, {"Id": "c", "Name": "Off", "Triggers": []}]
        self.assertEqual([t["Id"] for t, _ in beta_tasks.changes(tasks)], ["a"])


if __name__ == "__main__":
    unittest.main()
