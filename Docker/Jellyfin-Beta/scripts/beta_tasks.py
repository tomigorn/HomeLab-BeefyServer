#!/usr/bin/env python3
"""Strips every timed trigger (daily, weekly, interval) from the beta's scheduled tasks; startup triggers stay (plugins register their pages and scripts there).
Usage: beta_tasks.py [--check]  (--check exits 1 when a task still has a timed trigger)"""
import sys

from beta_api import api


def keep(task):
    return [t for t in task.get("Triggers") or [] if t.get("Type") == "StartupTrigger"]


def changes(tasks):
    return [(t, keep(t)) for t in tasks if len(keep(t)) != len(t.get("Triggers") or [])]


def main(argv):
    if any(a != "--check" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    todo = changes(api("GET", "/ScheduledTasks"))
    for task, triggers in todo:
        print(("would strip " if argv else "stripped ") + task["Name"])
        if not argv:
            api("POST", f"/ScheduledTasks/{task['Id']}/Triggers", triggers)
    if not todo:
        print("no timed triggers")
    return 1 if todo and argv else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
