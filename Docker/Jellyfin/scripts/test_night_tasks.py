import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("night_tasks", pathlib.Path(__file__).with_name("night-tasks.py"))
nt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nt)


def task(name, *triggers):
    return {"Id": name.lower().replace(" ", "-"), "Name": name, "Triggers": list(triggers)}


DAILY = lambda hm: {"Type": "DailyTrigger", "TimeOfDayTicks": nt.ticks(hm)}
INTERVAL = {"Type": "IntervalTrigger", "IntervalTicks": 864000000000}


class Ticks(unittest.TestCase):
    def test_ticks_are_100ns_since_midnight(self):
        self.assertEqual(nt.ticks("04:00"), 4 * 3600 * 10**7)
        self.assertEqual(nt.ticks("04:08"), (4 * 3600 + 8 * 60) * 10**7)


class Plan(unittest.TestCase):
    def test_interval_task_becomes_a_daily_trigger_in_the_window(self):
        plan = nt.plan([task("Clean Cache Directory", INTERVAL)])
        self.assertEqual(plan, [("clean-cache-directory", "Clean Cache Directory", [DAILY("04:40")])])

    def test_startup_trigger_is_replaced_by_the_daily_one(self):
        plan = nt.plan([task("Clean Transcode Directory", {"Type": "StartupTrigger"}, INTERVAL)])
        self.assertEqual(plan[0][2], [DAILY("04:42")])

    def test_the_javascript_injector_keeps_its_startup_trigger(self):
        self.assertEqual(nt.plan([task("JavaScript Injector Startup", {"Type": "StartupTrigger"})]), [])

    def test_weekly_tasks_get_a_sunday_trigger(self):
        plan = nt.plan([task("Refresh People", {"Type": "IntervalTrigger", "IntervalTicks": 6048000000000})])
        self.assertEqual(plan[0][2], [{"Type": "WeeklyTrigger", "DayOfWeek": "Sunday", "TimeOfDayTicks": nt.ticks("04:31")}])

    def test_task_already_on_its_trigger_is_left_alone(self):
        self.assertEqual(nt.plan([task("Scan Media Library", DAILY("04:10"))]), [])

    def test_task_without_trigger_that_is_not_listed_is_left_alone(self):
        self.assertEqual(nt.plan([task("Keyframe Extractor"), task("Unknown Plugin Task", INTERVAL)]), [])

    def test_every_scheduled_time_is_inside_the_window(self):
        for name, (hm, _day) in nt.SCHEDULE.items():
            self.assertTrue("04:00" <= hm <= "04:45", f"{name} {hm}")


if __name__ == "__main__":
    unittest.main()
