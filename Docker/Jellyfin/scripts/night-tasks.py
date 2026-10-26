#!/usr/bin/env python3
"""Puts Jellyfin's scheduled tasks into beefy's maintenance window (Server/9-Night-Schedule.md): fixed daily/Sunday triggers 04:08-04:44 instead of intervals and startup triggers that run on whatever wake comes (README "Scheduled tasks").
Idempotent: a task already on its trigger is not touched. Usage: night-tasks.py [--dry-run]"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api  # noqa: E402

# task name -> (time of day, None = daily or the weekday). "JavaScript Injector Startup" keeps its startup trigger on purpose.
SCHEDULE = {
    "Update IMDb Ratings": ("04:08", None),
    "Scan Media Library": ("04:10", None),
    "Generate Trickplay Images": ("04:24", None),
    "Extract Chapter Images": ("04:30", None),
    "Refresh People": ("04:31", "Sunday"),
    "Refresh upcoming and missing episodes (TheMovieDb)": ("04:32", "Sunday"),
    "Media Segment Scan": ("04:33", None),
    "Download missing subtitles": ("04:35", None),
    "Audio Normalization": ("04:36", None),
    "Download missing lyrics": ("04:37", None),
    "Refresh Channels": ("04:38", None),
    "Refresh Guide": ("04:39", None),
    "Clean Cache Directory": ("04:40", None),
    "Clean Log Directory": ("04:41", None),
    "Clean Transcode Directory": ("04:42", None),
    "Update Plugins": ("04:43", None),
    "Optimize database": ("04:44", None),
}


def ticks(hm):
    h, m = hm.split(":")
    return (int(h) * 3600 + int(m) * 60) * 10**7


def trigger(hm, day):
    t = {"Type": "DailyTrigger" if day is None else "WeeklyTrigger", "TimeOfDayTicks": ticks(hm)}
    if day:
        t["DayOfWeek"] = day
    return t


def plan(tasks):
    """[(id, name, wanted triggers)] for the listed tasks whose triggers differ."""
    out = []
    for t in tasks:
        want = SCHEDULE.get(t["Name"])
        if not want:
            continue
        triggers = [trigger(*want)]
        if t.get("Triggers") != triggers:
            out.append((t["Id"], t["Name"], triggers))
    return out


def main(argv):
    changes = plan(api("GET", "/ScheduledTasks"))
    for tid, name, triggers in changes:
        print(f"{name}: {triggers[0].get('DayOfWeek', 'daily')} {triggers[0]['TimeOfDayTicks'] // 10**7 // 3600:02d}:{triggers[0]['TimeOfDayTicks'] // 10**7 // 60 % 60:02d}")
        if "--dry-run" not in argv:
            api("POST", f"/ScheduledTasks/{tid}/Triggers", triggers)
    print(f"{len(changes)} task(s) {'would change' if '--dry-run' in argv else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
