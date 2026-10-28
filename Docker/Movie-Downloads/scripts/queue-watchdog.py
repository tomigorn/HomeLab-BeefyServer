#!/usr/bin/env python3
"""Queue watchdog (README "Queue watchdog"): Telegram for what needs a hand, the rest into a morning-digest metrics file.
Per (app, downloadId, condition), once until the condition clears and returns: blocked (importBlocked/importPending/failedPending state or warning/error status) for QW_BLOCKED_MIN minutes = Telegram; stalled (downloading/queued, sizeleft not falling) for QW_STALL_HOURS hours and failed (status failed) = digest.
Guard events: scripts/grab-guard.sh writes <config dir>/guard-events/*.json inside the containers (no bot token there); removed / marked-failed = digest, "check by hand" = Telegram; the file is deleted once handled. scripts/subtitle-check.py writes subtitle_repaired / subtitle_suspect events to /srv/appdata/bazarr/subtitle-check/events = digest. Sent with _global/scripts/oob-alert.sh. Run by systemd/queue-watchdog.timer.
Indexers: /health of each app; one Telegram while indexers are disabled after failures (IndexerStatusCheck / IndexerLongTermStatusCheck), recovery = digest. Expected backoffs are left out: Treasure Maps grab limit, The Pirate Bay "no results".
Quiet hours 22:00-07:00 Europe/Zurich: Telegrams are held and sent on the first run from 07:00 if still due.
ID-matched Sonarr imports: a completed download Sonarr blocks only with "matched to series by ID" is imported (ManualImport, once per download) when the grab history, title, year, season and Sonarr's own proposals all agree; otherwise the normal blocked alert names the reason. QW_AUTO_IMPORT=0 switches it off.
Digest: watchdog-digest.prom (node-exporter textfile, last 48 h) in the state dir, read by fastpi's morning digest. Every send, hold and digest event is a line in watchdog-sends.log.
Usage: scripts/queue-watchdog.py [--dry-run]   (--dry-run: only the ID-match auto-import check, prints, changes nothing)"""
import datetime
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
import zoneinfo
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
INDEXER_CHECKS = {"IndexerStatusCheck", "IndexerLongTermStatusCheck"}
EXPECTED = {"treasure maps": re.compile(r"429|limit|quota|too many", re.I), "the pirate bay": re.compile(r"no results", re.I)}
Alert = namedtuple("Alert", "key condition text title", defaults=("",))
TZ = "Europe/Zurich"
GUARD_DIGEST = {"removed", "marked-failed"}
SUBTITLE_DIGEST = {"subtitle_repaired", "subtitle_suspect"}
DEFAULT_EVENTS = "/srv/appdata/radarr/guard-events:/srv/appdata/sonarr/guard-events:/srv/appdata/bazarr/subtitle-check/events"
ID_MATCH = "matched to series by ID"
YEAR_SLACK = 1
DIGEST_HOURS, DIGEST_MAX, SENDS_MAX, TITLE_MAX = 48, 40, 1000, 80


class ApiError(Exception):
    pass


@dataclass
class Config:
    blocked_min: int = 30
    stall_hours: int = 6
    tz: str = TZ
    quiet_from: int = 22
    quiet_until: int = 7
    auto_import: bool = True


def in_quiet_hours(now, cfg):
    hour = datetime.datetime.fromtimestamp(now, zoneinfo.ZoneInfo(cfg.tz)).hour
    return hour >= cfg.quiet_from or hour < cfg.quiet_until


def write_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def log_send(path, now, kind, status, title):
    """One line per sent, held or digested event, the last SENDS_MAX kept."""
    path = pathlib.Path(path)
    stamp = datetime.datetime.fromtimestamp(now, zoneinfo.ZoneInfo(TZ)).isoformat(timespec="seconds")
    old = path.read_text().splitlines() if path.exists() else []
    lines = old + [f"{stamp} {kind} {status} {' '.join(str(title).split())}"]
    write_atomic(path, "\n".join(lines[-SENDS_MAX:]) + "\n")


def prune_digest(events, now):
    """Events of the last DIGEST_HOURS, one per (kind, app, title) with the newest time, newest first, at most DIGEST_MAX."""
    newest = {}
    for e in events:
        k = (e["kind"], e["app"], e["title"])
        if e["ts"] > now - DIGEST_HOURS * 3600 and (k not in newest or e["ts"] > newest[k]["ts"]):
            newest[k] = e
    return sorted(newest.values(), key=lambda e: e["ts"], reverse=True)[:DIGEST_MAX]


def _lv(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_digest(events, now):
    lines = ["# HELP beefy_watchdog_event_timestamp_seconds Auto-handled queue-watchdog event (digest only), one series per event, last 48 h.",
             "# TYPE beefy_watchdog_event_timestamp_seconds gauge"]
    lines += [f'beefy_watchdog_event_timestamp_seconds{{kind="{_lv(e["kind"])}",app="{_lv(e["app"])}",title="{_lv(str(e["title"])[:TITLE_MAX])}"}} {int(e["ts"])}'
              for e in prune_digest(events, now)]
    lines += ["# HELP beefy_watchdog_digest_updated_timestamp_seconds Last queue-watchdog run that wrote this file.",
              "# TYPE beefy_watchdog_digest_updated_timestamp_seconds gauge",
              f"beefy_watchdog_digest_updated_timestamp_seconds {int(now)}"]
    return "\n".join(lines) + "\n"


class Out:
    """Telegram (held in quiet hours) or digest, every outcome logged."""

    def __init__(self, send, now, cfg, held_before, log_path):
        self.send, self.now, self.quiet, self.log = send, now, in_quiet_hours(now, cfg), log_path
        self.held_before, self.held, self.events = held_before, {}, []

    def immediate(self, hold_id, kind, title, text):
        """'sent', 'held' or 'failed'."""
        if self.quiet:
            self.held[hold_id] = self.held_before.get(hold_id, self.now)
            if hold_id not in self.held_before:
                log_send(self.log, self.now, kind, "held", title)
            return "held"
        if not self.send(text):
            return "failed"
        log_send(self.log, self.now, kind, "sent", title)
        return "sent"

    def digest(self, kind, app, title):
        self.events.append({"kind": kind, "app": app, "title": title, "ts": int(self.now)})
        log_send(self.log, self.now, kind, "digest", title)


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
    if rec.get("autoImport"):
        note.append(f"🤖 Auto-import: not done ({html.escape(rec['autoImport'])})")
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
                alerts.append(Alert(key, cond, message(app, rec, cond, since[cond], now), label(app, rec)))
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


def event_title(ev):
    return f"{ev.get('title', '?')} ({ev['year']})" if ev.get("year") else str(ev.get("title", "?"))


def send_events(dirs, out):
    rc = 0
    for d in dirs:
        for f in sorted(pathlib.Path(d).glob("*.json")):
            try:
                ev = json.loads(f.read_text())
                if ev.get("kind") in SUBTITLE_DIGEST:
                    out.digest(ev["kind"], str(ev.get("app", "bazarr")), str(ev.get("title", f.stem)))
                    f.unlink()
                    continue
                text = event_text(ev)
            except (ValueError, OSError, AttributeError):
                f.rename(f.with_suffix(".bad"))
                continue
            if ev.get("action") in GUARD_DIGEST:
                out.digest("guard_removed", str(ev.get("app", "?")), event_title(ev))
                f.unlink()
                continue
            r = out.immediate(f"event:{f}", "guard_manual", event_title(ev), text)
            if r == "sent":
                f.unlink()
            rc |= r == "failed"
    return int(rc)


def fetch(app, api):
    records, page = [], 1
    while True:
        r = api("GET", "/queue", {"page": page, "pageSize": 1000}) or {}
        records += r.get("records") or []
        if page * 1000 >= (r.get("totalRecords") or 0):
            return records
        page += 1


def failed_indexers(health, status=None, indexers=()):
    """{indexer name: None or {until, error, last}} for indexers the app disabled after failures."""
    by_name = {i.get("name"): i.get("id") for i in indexers}
    by_id = {r.get("indexerId"): r for r in status or []}
    out = {}
    for h in health:
        msg = h.get("message") or ""
        if h.get("source") not in INDEXER_CHECKS and "unavailable due to failures" not in msg:
            continue
        for name in msg.partition(": ")[2].split(", "):
            name = name.strip()
            rec = by_id.get(by_name.get(name))
            out[name] = {"until": rec.get("disabledTill"), "error": rec.get("lastError"), "last": rec.get("mostRecentFailure")} if rec else None
    return out


def unexpected(failed):
    """Failed indexers minus the expected backoffs; an unknown error counts as the expected one."""
    out = {}
    for name, info in failed.items():
        pat = next((p for k, p in EXPECTED.items() if k in name.lower()), None)
        error = (info or {}).get("error")
        if pat and (not error or pat.search(error)):
            continue
        out[name] = info
    return out


def indexer_message(app, failed, now):
    app_name = app.capitalize()
    lines = [f"🟠 <b>{app_name}: indexers disabled after failures</b>", "", f"📅 {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}"]
    lines += [f"• 🔍 {html.escape(name)}" for name in failed]
    details = []
    for name, info in failed.items():
        info = info or {}
        extra = [f"disabled until {info['until']}"] if info.get("until") else []
        extra += [f"last error: {info['error']}"] if info.get("error") else []
        if extra:
            details.append(f"{html.escape(name)}: {html.escape('; '.join(extra))}")
    details.append(f"💡 Fix: {app_name} → System → Status → test all indexers, or <code>POST /api/v3/indexer/testall</code>")
    lines.append("<blockquote expandable>" + "\n".join(details) + "</blockquote>")
    return "\n".join(lines)


def indexer_check(app, api, state, out, now):
    key = f"indexers/{app}"
    try:
        health = api("GET", "/health") or []
        try:
            status, indexers = api("GET", "/indexerstatus") or [], api("GET", "/indexer") or []
        except ApiError:
            status, indexers = [], []
    except ApiError as e:
        print(f"{app}: health API error, state kept: {e}", file=sys.stderr)
        return 1
    bad = unexpected(failed_indexers(health, status, indexers))
    if bad and key not in state:
        r = out.immediate(key, "indexers_disabled", ", ".join(sorted(bad)), indexer_message(app, bad, now))
        if r == "sent":
            state[key] = {"names": sorted(bad), "since": now}
        return int(r == "failed")
    if not bad and key in state:
        out.digest("indexer_recovered", app, ", ".join(state[key].get("names") or []))
        del state[key]
    return 0


class Refused(Exception):
    pass


def clean(title):
    return re.sub(r"[\W_]+", "", str(title or "").casefold())


def series_names(series):
    titles = [series.get("title")] + [a.get("title") for a in series.get("alternateTitles") or []]
    return {clean(re.sub(r"\s*\(?\b(19|20)\d{2}\)?\s*$", "", t or "")) for t in titles if t} - {""}


def id_match_groups(records):
    """{downloadId: records} of downloads whose every queue record is completed, importBlocked, and carries only the ID-match message."""
    groups = {}
    for rec in records:
        if rec.get("downloadId"):
            groups.setdefault(rec["downloadId"], []).append(rec)

    def only_id(rec):
        msgs = [m for sm in rec.get("statusMessages") or [] for m in sm.get("messages") or []]
        return (rec.get("status") == "completed" and rec.get("trackedDownloadState") == "importBlocked"
                and bool(msgs) and all(ID_MATCH in m for m in msgs))
    return {d: recs for d, recs in groups.items() if all(only_id(r) for r in recs)}


def group_label(recs):
    seasons = sorted({(r.get("episode") or {}).get("seasonNumber") for r in recs} - {None})
    name = label("sonarr", dict(recs[0], episode=None))
    return f"{name} {'/'.join(f'S{s:02d}' for s in seasons)}".rstrip() + f": {recs[0].get('title')}"


def id_import_files(api, download_id, recs):
    """ManualImport files for an ID-matched download, or Refused(reason)."""
    sids = {r.get("seriesId") for r in recs}
    if len(sids) != 1 or None in sids:
        raise Refused("queue records name no single series")
    sid = sids.pop()
    series = api("GET", f"/series/{sid}") or {}
    queued = {r.get("episodeId") for r in recs}
    eps = [r.get("episode") or {} for r in recs]
    seasons = {e.get("seasonNumber") for e in eps}
    grabs = [h for h in (api("GET", "/history", {"downloadId": download_id, "pageSize": 1000}) or {}).get("records") or [] if h.get("eventType") == "grabbed"]
    if not grabs:
        raise Refused("no grab history for this download")
    for h in grabs:
        data = h.get("data") or {}
        if h.get("seriesId") != sid or data.get("seriesMatchType") != "Id" or not series.get("tvdbId") or str(data.get("tvdbId")) != str(series.get("tvdbId")):
            raise Refused("grab history is not this series by its TVDB id")
    if {h.get("episodeId") for h in grabs} != queued:
        raise Refused("grabbed episodes differ from the queued ones")
    info = (api("GET", "/parse", {"title": recs[0].get("title")}) or {}).get("parsedEpisodeInfo") or {}
    title = info.get("seriesTitleInfo") or {}
    parsed_title = title.get("titleWithoutYear") or info.get("seriesTitle")
    if clean(parsed_title) not in series_names(series):
        raise Refused(f'release title "{parsed_title}" is not the series title')
    year = title.get("year") or 0
    years = {series.get("year")} | {int(e["airDate"][:4]) for e in eps if (e.get("airDate") or "")[:4].isdigit()}
    if year and not any(y and abs(year - y) <= YEAR_SLACK for y in years):
        raise Refused(f"release year {year} fits neither the series year nor the episode air years")
    if seasons != {info.get("seasonNumber")}:
        raise Refused(f"release season {info.get('seasonNumber')} is not the queued season")
    if not info.get("fullSeason") and set(info.get("episodeNumbers") or []) != {e.get("episodeNumber") for e in eps}:
        raise Refused("release episode numbers differ from the queued ones")
    out_dir = (recs[0].get("outputPath") or "").rstrip("/")
    proposals = api("GET", "/manualimport", {"downloadId": download_id, "filterExistingFiles": "true"}) or []
    proposed = [e.get("id") for p in proposals for e in p.get("episodes") or []]
    for p in proposals:
        if p.get("rejections"):
            raise Refused("Sonarr rejects a file: " + "; ".join(str(r.get("reason")) for r in p["rejections"]))
        if (p.get("series") or {}).get("id") != sid or not p.get("episodes") or not out_dir or not str(p.get("path", "")).startswith(out_dir + "/"):
            raise Refused("a proposed file is not this series' episode inside the download folder")
    if not proposals or len(proposed) != len(set(proposed)) or set(proposed) != queued:
        raise Refused("proposed files do not cover exactly the grabbed episodes")
    keys = ("path", "folderName", "releaseGroup", "quality", "languages", "indexerFlags", "releaseType")
    return [{k: p.get(k) for k in keys} | {"seriesId": sid, "episodeIds": [e["id"] for e in p["episodes"]], "downloadId": download_id} for p in proposals]


def auto_import(app, api, records, state, out, now, cfg):
    """ID-matched Sonarr downloads: import the ones that pass, mark the rest; (download ids imported this run, rc)."""
    done, rc = set(), 0
    if app != "sonarr" or not cfg.auto_import:
        return done, rc
    groups = id_match_groups(records)
    queued = {r.get("downloadId") for r in records}
    for key in [k for k in state if k.startswith("autoimport:sonarr:") and k.split(":", 2)[2] not in queued]:
        del state[key]
    for dl, recs in groups.items():
        key = f"autoimport:sonarr:{dl}"
        if key in state:
            continue
        name = group_label(recs)
        try:
            files = id_import_files(api, dl, recs)
        except Refused as e:
            print(f"auto-import refused: {name}: {e}")
            for r in recs:
                r["autoImport"] = str(e)
            continue
        except ApiError as e:
            print(f"auto-import check failed, retried next run: {name}: {e}", file=sys.stderr)
            rc = 1
            continue
        state[key] = now
        try:
            r = api("POST", "/command", body={"name": "ManualImport", "importMode": "auto", "files": files}) or {}
        except ApiError as e:
            print(f"auto-import command failed, not retried: {name}: {e}", file=sys.stderr)
            rc = 1
            continue
        print(f"auto-import: {name} ({len(files)} files, command {r.get('id')})")
        out.digest("auto_imported", app, name)
        done.add(dl)
    return done, rc


def dry_run(apps, cfg):
    """Lines describing what auto_import would do; no command, no state, no message."""
    lines, n, ok = [], 0, 0
    api = apps.get("sonarr")
    if not cfg.auto_import:
        lines.append("auto-import switched off (QW_AUTO_IMPORT=0)")
    elif api:
        for dl, recs in id_match_groups(fetch("sonarr", api)).items():
            n += 1
            name = group_label(recs)
            try:
                files = id_import_files(api, dl, recs)
            except Refused as e:
                lines.append(f"not imported: {name}: {e}")
                continue
            ok += 1
            lines.append(f"would import: {name} ({len(files)} files)")
    lines.append(f"id-match auto-import: {n} candidate(s), {ok} importable")
    return lines


def run(apps, state_path, event_dirs, send, cfg, now=None):
    now = time.time() if now is None else now
    state_path = pathlib.Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    digest_path = state_path.parent / "watchdog-digest.json"
    out = Out(send, now, cfg, state.pop("held", {}), state_path.parent / "watchdog-sends.log")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    rc = 0
    for app, api in apps.items():
        rc |= indexer_check(app, api, state, out, now)
        try:
            records = fetch(app, api)
        except ApiError as e:
            print(f"{app}: API error, state kept: {e}", file=sys.stderr)
            rc = 1
            continue
        imported, r = auto_import(app, api, records, state, out, now, cfg)
        rc |= r
        alerts, state = evaluate(app, [rec for rec in records if rec.get("downloadId") not in imported], state, now, cfg)
        for a in alerts:
            if a.condition != "blocked":
                out.digest(a.condition, app, a.title)
                continue
            r = out.immediate(f"{a.key}:blocked", "blocked", a.title, a.text)
            if r != "sent":
                state[a.key]["alerted"].remove(a.condition)
            rc |= r == "failed"
    rc |= send_events(event_dirs, out)
    if out.held:
        state["held"] = out.held
    write_atomic(state_path, json.dumps(state, indent=1, sort_keys=True))
    events = prune_digest((json.loads(digest_path.read_text()) if digest_path.exists() else []) + out.events, now)
    write_atomic(digest_path, json.dumps(events, indent=1))
    write_atomic(state_path.parent / "watchdog-digest.prom", render_digest(events, now))
    return int(rc)


def client(app, config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(method, path, query=None, body=None):
        q = APPS[app] + ("&" + urllib.parse.urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = {"X-Api-Key": get("ApiKey")} | ({"Content-Type": "application/json"} if data else {})
        req = urllib.request.Request(f"{base}{path}?{q}", data=data, method=method, headers=headers)
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
    dirs = os.environ.get("QW_EVENTS", DEFAULT_EVENTS).split(":")
    cfg = Config(int(os.environ.get("QW_BLOCKED_MIN", 30)), int(os.environ.get("QW_STALL_HOURS", 6)), auto_import=os.environ.get("QW_AUTO_IMPORT", "1") != "0")
    if sys.argv[1:] == ["--dry-run"]:
        try:
            print("\n".join(dry_run(apps, cfg)))
        except ApiError as e:
            print(f"API error: {e}", file=sys.stderr)
            return 1
        return 0
    if sys.argv[1:]:
        print("usage: " + __doc__.rsplit("Usage: ", 1)[1], file=sys.stderr)
        return 2
    script = os.environ.get("QW_ALERT", HERE.parent.parent / "_global" / "scripts" / "oob-alert.sh")
    return run(apps, os.environ.get("QW_STATE", STATE_DIR / "queue-watchdog.json"), dirs, sender(script), cfg)


if __name__ == "__main__":
    sys.exit(main())
