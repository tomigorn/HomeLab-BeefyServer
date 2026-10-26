#!/usr/bin/env python3
"""Night downloads (README "Can wait: downloads at night"): resumes the downloads the untagged "night" clients added paused.
Low tariff (22:00-06:00 and all Sunday): every paused item in the night categories resumes. Any time: the paused night items of titles tagged download-now resume.
Never pauses anything: a download still running at 06:00 finishes. Writes ~/.local/state/movie-downloads/night-downloads.json ({"pending": n}), which fastpi reads to decide its 22:05 wake.
Run by systemd/night-downloads.timer (every 2 min while awake, and at boot). Usage: scripts/night-downloads.py [--dry-run]"""
import datetime
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")) / "movie-downloads" / "night-downloads.json"
NIGHT_QBIT = {"radarr-night", "sonarr-night"}
NIGHT_SAB = {"movies-night", "tv-night"}
TAG = "download-now"
PAUSED_QBIT = {"pausedDL", "stoppedDL"}
APPS = {"radarr": "includeMovie=true", "sonarr": "includeSeries=true"}


class ApiError(Exception):
    pass


@dataclass
class Plan:
    hashes: list = field(default_factory=list)
    nzos: list = field(default_factory=list)
    pending: int = 0


def low_tariff(dt):
    return dt.weekday() == 6 or dt.hour >= 22 or dt.hour < 6


def urgent_ids(tags, queue):
    ids = {t["id"] for t in tags if t.get("label") == TAG}
    out = set()
    for rec in queue.get("records") or []:
        item = rec.get("movie") or rec.get("series") or {}
        if ids & set(item.get("tags") or []) and rec.get("downloadId"):
            out.add(rec["downloadId"].lower())
    return out


def plan(now, torrents, slots, urgent):
    low, p = low_tariff(now), Plan()
    for t in torrents:
        if t.get("category") in NIGHT_QBIT and t.get("state") in PAUSED_QBIT:
            if low or t["hash"].lower() in urgent:
                p.hashes.append(t["hash"])
            else:
                p.pending += 1
    for s in slots:
        if s.get("cat") in NIGHT_SAB and s.get("status") == "Paused":
            if low or s["nzo_id"].lower() in urgent:
                p.nzos.append(s["nzo_id"])
            else:
                p.pending += 1
    return p


def run(now, qbit, sab, apps, state_path, dry_run=False):
    rc, urgent = 0, set()
    for app, api in apps.items():
        try:
            urgent |= urgent_ids(api("/tag"), api("/queue?page=1&pageSize=1000"))
        except ApiError as e:
            print(f"{app}: {e}", file=sys.stderr)
            rc = 1
    try:
        torrents = qbit("/torrents/info")
        slots = sab({"mode": "queue", "limit": "1000"})["queue"]["slots"]
    except (ApiError, KeyError, TypeError) as e:
        print(f"download client not readable, state kept: {e}", file=sys.stderr)
        return 1
    p = plan(now, torrents, slots, urgent)
    names = {t["hash"]: t.get("name") for t in torrents} | {s["nzo_id"]: s.get("filename") for s in slots}
    for i in p.hashes + p.nzos:
        print(f"{'would resume' if dry_run else 'resume'}: {names.get(i)}")
    print(f"low tariff: {low_tariff(now)}, waiting for the night: {p.pending}")
    if dry_run:
        return rc
    try:
        if p.hashes:
            qbit("/torrents/start", {"hashes": "|".join(p.hashes)})
        for n in p.nzos:
            sab({"mode": "queue", "name": "resume", "value": n})
    except ApiError as e:
        print(f"resume failed: {e}", file=sys.stderr)
        rc = 1
    state_path = pathlib.Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pending": p.pending, "low_tariff": low_tariff(now), "updated": now.isoformat(timespec="seconds")}) + "\n")
    tmp.replace(state_path)
    return rc


def _open(req, timeout=30):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body.strip() else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ApiError(f"{req.full_url.split('?')[0]}: {e}") from None


def qbit_client(base):
    def call(path, data=None):
        if path == "/torrents/start":
            try:
                return _open(urllib.request.Request(base + path, urllib.parse.urlencode(data).encode()))
            except ApiError:
                return _open(urllib.request.Request(base + "/torrents/resume", urllib.parse.urlencode(data).encode()))
        return _open(urllib.request.Request(base + path))
    return call


def sab_client(base, keyfile):
    key = pathlib.Path(keyfile).read_text().strip()

    def call(params):
        return _open(urllib.request.Request(f"{base}/api?" + urllib.parse.urlencode({**params, "output": "json", "apikey": key})))
    return call


def arr_client(app, config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(path):
        sep = "&" if "?" in path else "?"
        extra = sep + APPS[app] if path.startswith("/queue") else ""
        return _open(urllib.request.Request(base + path + extra, headers={"X-Api-Key": get("ApiKey")}))
    return call


def main():
    dry = "--dry-run" in sys.argv[1:]
    qbit = qbit_client(os.environ.get("ND_QBIT_URL", "http://127.0.0.1:8080") + "/api/v2")
    sab = sab_client(os.environ.get("ND_SAB_URL", "http://127.0.0.1:8081"), os.environ.get("ND_SAB_KEY", HERE.parent / "secrets" / "hook" / "sabnzbd.apikey"))
    apps = {app: arr_client(app, os.environ.get(f"ND_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in APPS}
    return run(datetime.datetime.now(), qbit, sab, apps, os.environ.get("ND_STATE", STATE), dry_run=dry)


if __name__ == "__main__":
    sys.exit(main())
