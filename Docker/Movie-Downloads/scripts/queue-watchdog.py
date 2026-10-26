#!/usr/bin/env python3
"""Queue watchdog (README "Queue watchdog"): one Telegram per stuck Radarr/Sonarr queue item and per wrong-film guard event.
Per (app, downloadId, condition), once until the condition clears and returns: blocked (importBlocked/importPending/failedPending state or warning/error status) for QW_BLOCKED_MIN minutes, stalled (downloading/queued, sizeleft not falling) for QW_STALL_HOURS hours, failed (status failed).
Guard events: scripts/grab-guard.sh writes <config dir>/guard-events/*.json inside the containers (no bot token there); each becomes a message and the file is deleted. Sent with _global/scripts/oob-alert.sh. Run by systemd/queue-watchdog.timer.
Usage: scripts/queue-watchdog.py"""
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple
from dataclasses import dataclass

STATE_DIR = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")) / "movie-downloads"
HERE = pathlib.Path(__file__).resolve().parent
BLOCKED_STATES = {"importBlocked", "importPending", "failedPending"}
ACTIVE = {"downloading", "queued"}
APPS = {"radarr": "includeUnknownMovieItems=true&includeMovie=true", "sonarr": "includeUnknownSeriesItems=true&includeSeries=true&includeEpisode=true"}
ACTIONS = {
    "removed": "removed from the queue and blocklisted, searching again",
    "marked-failed": "not in the queue: marked failed in history, blocklisted, searching again",
    "manual": "could not be removed: check by hand",
}
Alert = namedtuple("Alert", "key condition text")


class ApiError(Exception):
    pass


@dataclass
class Config:
    blocked_min: int = 30
    stall_hours: int = 6


def duration(seconds):
    m = int(seconds // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60} min"


def label(app, rec):
    item = rec.get("movie") if app == "radarr" else rec.get("series")
    if not item or not item.get("title"):
        return rec.get("title") or "unknown"
    name = f"{item['title']} ({item['year']})" if item.get("year") else item["title"]
    ep = rec.get("episode")
    if app == "sonarr" and ep:
        name += f" S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
    return name


def message(app, rec, condition, since, now):
    msgs = "; ".join(f"{m.get('title')}: {' '.join(m.get('messages') or [])}".strip(": ") for m in rec.get("statusMessages") or [])
    state = f"{rec.get('status')} / {rec.get('trackedDownloadState')} / {rec.get('trackedDownloadStatus')}"
    app_name = app.capitalize()
    icon = "🔴" if condition == "failed" else "🟠"
    lines = [f"{icon} <b>{app_name} queue: {condition}</b>", "", f"• 🎬 {html.escape(label(app, rec))}", f"• Release: <code>{html.escape(rec.get('title') or '')}</code>", f"• State: {html.escape(state)}"]
    if since is not None:
        lines.append(f"• For: {duration(now - since)}")
    note = [f"💡 Message: {html.escape(msgs)}"] if msgs else []
    lines.append("<blockquote expandable>" + "\n".join(note + [f"{app_name} → Activity → Queue"]) + "</blockquote>")
    return "\n".join(lines)


def evaluate(app, records, state, now, cfg):
    """(alerts, new state) for one app's queue; state entries of its downloads that left the queue are dropped."""
    keep, alerts = {}, []
    for rec in records:
        key = f"{app}:{rec.get('downloadId') or 'id' + str(rec.get('id'))}"
        st = state.get(key) or {"first": {}, "alerted": [], "left": None, "since": now}
        status = rec.get("status")
        left = rec.get("sizeleft")
        if status in ACTIVE:
            if st["left"] is None or left is None or left < st["left"]:
                st["left"], st["since"] = left, now
                if "stalled" in st["alerted"]:
                    st["alerted"].remove("stalled")
        else:
            st["left"], st["since"] = None, now
        active = {
            "blocked": rec.get("trackedDownloadState") in BLOCKED_STATES or rec.get("trackedDownloadStatus") in ("warning", "error"),
            "stalled": status in ACTIVE,
            "failed": status == "failed",
        }
        for cond, on in active.items():
            if on:
                st["first"].setdefault(cond, now)
            else:
                st["first"].pop(cond, None)
                if cond in st["alerted"]:
                    st["alerted"].remove(cond)
        due = {
            "blocked": active["blocked"] and now - st["first"].get("blocked", now) > cfg.blocked_min * 60,
            "stalled": active["stalled"] and now - st["since"] > cfg.stall_hours * 3600,
            "failed": active["failed"],
        }
        since = {"blocked": st["first"].get("blocked"), "stalled": st["since"], "failed": None}
        for cond, d in due.items():
            if d and cond not in st["alerted"]:
                st["alerted"].append(cond)
                alerts.append(Alert(key, cond, message(app, rec, cond, since[cond], now)))
        keep[key] = st
    return alerts, {k: v for k, v in state.items() if not k.startswith(app + ":")} | keep


def event_text(ev):
    app = str(ev.get("app", "?")).capitalize()
    year = f" ({ev['year']})" if ev.get("year") else ""
    manual = ev.get("action") == "manual"
    dub = ev.get("reason") == "dub-only"
    lines = [
        f"{'🔴' if manual else '🟠'} <b>{('Dub-only release' if manual else 'Dub-only release removed') if dub else 'Wrong-film guard'}: {app}</b>",
        "",
        f"• 🎬 {html.escape(str(ev.get('title', '?')))}{year}",
        f"• Grabbed: <code>{html.escape(str(ev.get('release', '?')))}</code>" + ("" if dub else f" (release year {ev.get('releaseYear', '?')})"),
    ]
    if dub:
        lines.append(f"• 🗣 Languages: {html.escape(', '.join(ev.get('languages') or []))} (original {html.escape(str(ev.get('original', '?')))} missing)")
    lines.append(f"• Result: {ACTIONS.get(ev.get('action'), 'check by hand')}")
    if manual:
        lines.append(f"<blockquote expandable>{app} → Activity → Queue</blockquote>")
    return "\n".join(lines)


def send_events(dirs, send):
    rc = 0
    for d in dirs:
        for f in sorted(pathlib.Path(d).glob("*.json")):
            try:
                text = event_text(json.loads(f.read_text()))
            except (ValueError, OSError):
                f.rename(f.with_suffix(".bad"))
                continue
            if send(text):
                f.unlink()
            else:
                rc = 1
    return rc


def fetch(app, api):
    records, page = [], 1
    while True:
        r = api("GET", "/queue", {"page": page, "pageSize": 1000}) or {}
        records += r.get("records") or []
        if page * 1000 >= (r.get("totalRecords") or 0):
            return records
        page += 1


def run(apps, state_path, event_dirs, send, cfg, now=None):
    now = time.time() if now is None else now
    state_path = pathlib.Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    rc = 0
    for app, api in apps.items():
        try:
            records = fetch(app, api)
        except ApiError as e:
            print(f"{app}: API error, state kept: {e}", file=sys.stderr)
            rc = 1
            continue
        alerts, state = evaluate(app, records, state, now, cfg)
        for a in alerts:
            if not send(a.text):
                state[a.key]["alerted"].remove(a.condition)
                rc = 1
    rc |= send_events(event_dirs, send)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(state_path)
    return rc


def client(app, config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(method, path, query=None, body=None):
        q = APPS[app] + ("&" + urllib.parse.urlencode(query) if query else "")
        req = urllib.request.Request(f"{base}{path}?{q}", method=method, headers={"X-Api-Key": get("ApiKey")})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ApiError(f"{method} {path}: {e}") from None
    return call


def sender(script):
    def send(text):
        r = subprocess.run([str(script), text], capture_output=True, text=True, timeout=60)
        if r.returncode:
            print(f"alert not sent: {r.stderr.strip()}", file=sys.stderr)
        return r.returncode == 0
    return send


def main():
    apps = {app: client(app, os.environ.get(f"QW_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in APPS}
    dirs = os.environ.get("QW_EVENTS", "/srv/appdata/radarr/guard-events:/srv/appdata/sonarr/guard-events").split(":")
    cfg = Config(int(os.environ.get("QW_BLOCKED_MIN", 30)), int(os.environ.get("QW_STALL_HOURS", 6)))
    script = os.environ.get("QW_ALERT", HERE.parent.parent / "_global" / "scripts" / "oob-alert.sh")
    return run(apps, os.environ.get("QW_STATE", STATE_DIR / "queue-watchdog.json"), dirs, sender(script), cfg)


if __name__ == "__main__":
    sys.exit(main())
