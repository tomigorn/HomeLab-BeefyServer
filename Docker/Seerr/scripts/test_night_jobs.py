import importlib.util
import pathlib
import re
import unittest

_spec = importlib.util.spec_from_file_location("night_jobs", pathlib.Path(__file__).with_name("night-jobs.py"))
nj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nj)


class Plan(unittest.TestCase):
    def test_job_off_schedule_is_changed(self):
        self.assertEqual(nj.plan([{"id": "availability-sync", "cronSchedule": "0 0 5 * * *"}]), [("availability-sync", "0 30 4 * * *")])

    def test_job_on_schedule_is_left_alone(self):
        self.assertEqual(nj.plan([{"id": "radarr-scan", "cronSchedule": "0 0 4 * * *"}]), [])

    def test_pollers_are_never_touched(self):
        jobs = [{"id": "download-sync", "cronSchedule": "0 * * * * *"}, {"id": "jellyfin-recently-added-scan", "cronSchedule": "0 */5 * * * *"}]
        self.assertEqual(nj.plan(jobs), [])

    def test_every_job_runs_inside_the_window(self):
        for job, cron in nj.SCHEDULE.items():
            minute, hour = map(int, re.match(r"0 (\d+) (\d+) ", cron).groups())
            self.assertEqual(hour, 4, job)
            self.assertLessEqual(minute, 45, job)


if __name__ == "__main__":
    unittest.main()
