#!/usr/bin/env python3
"""Puts Seerr's scheduled jobs into beefy's maintenance window (Server/9-Night-Schedule.md). Idempotent: a job already on its schedule is not touched.
The two pollers (download-sync every minute, jellyfin-recently-added-scan every 5 minutes) stay as they are. Usage: night-jobs.py [--dry-run]"""
import json
import pathlib
import sys
import urllib.request

BASE = "http://127.0.0.1:5055/api/v1"
KEY_FILE = pathlib.Path(__file__).resolve().parent.parent / "secrets" / "api-key"

# job id -> Seerr cron (second minute hour day month weekday), all Europe/Zurich
SCHEDULE = {
    "radarr-scan": "0 0 4 * * *",
    "download-sync-reset": "0 3 4 * * *",
    "sonarr-scan": "0 5 4 * * *",
    "jellyfin-full-scan": "0 22 4 * * *",
    "availability-sync": "0 30 4 * * *",
    "image-cache-cleanup": "0 32 4 * * *",
    "process-blocklisted-tags": "0 34 4 * * 0",
}


def plan(jobs):
    """[(id, cron)] for the listed jobs whose schedule differs."""
    return [(j["id"], SCHEDULE[j["id"]]) for j in jobs if j["id"] in SCHEDULE and j["cronSchedule"] != SCHEDULE[j["id"]]]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Api-Key": KEY_FILE.read_text().strip(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main(argv):
    changes = plan(call("GET", "/settings/jobs"))
    for job, cron in changes:
        print(f"{job}: {cron}")
        if "--dry-run" not in argv:
            call("POST", f"/settings/jobs/{job}/schedule", {"schedule": cron})
    print(f"{len(changes)} job(s) {'would change' if '--dry-run' in argv else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
