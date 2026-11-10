#!/usr/bin/env python3
"""Queue watchdog (README "Queue watchdog"): Telegram for what needs a hand, the rest into a morning-digest metrics file.
Per (app, downloadId, condition), once until the condition clears and returns: blocked (importBlocked/importPending/failedPending state or warning/error status) for QW_BLOCKED_MIN minutes = Telegram; stalled (downloading/queued, sizeleft not falling) for QW_STALL_HOURS hours and failed (status failed) = digest.
Guard events: scripts/grab-guard.sh writes <config dir>/guard-events/*.json inside the containers (no bot token there); removed / marked-failed = digest, "check by hand" = Telegram; the file is deleted once handled. scripts/subtitle-check.py writes subtitle_repaired / subtitle_suspect events and scripts/forced-sync.py subtitle_forced_synced / subtitle_forced_unverified to /srv/appdata/bazarr/subtitle-check/events = digest. Sent with _global/scripts/oob-alert.sh. Run by systemd/queue-watchdog.timer.
Indexers: /health of each app; one Telegram while indexers are disabled after failures (IndexerStatusCheck / IndexerLongTermStatusCheck), recovery = digest. Expected backoffs are left out: Treasure Maps grab limit, The Pirate Bay "no results"; any indexer disabled for a request limit (429, "Request Limit", too many requests: lastError or its newest warning in /log) = digest.
Quiet hours 22:00-07:00 Europe/Zurich: Telegrams are held and sent on the first run from 07:00 if still due.
Removals (remove from the client, blocklist, search again; digest, Telegram only when the DELETE fails): a download blocked "matched to movie/series by ID" whose release year fails the guard's year rule (scripts/guard-year.jq) = guard_removed; a fake (executable / dangerous / encrypted file message, an output path with such an extension, or a completed folder with such files and no video) = malware_removed.
ID-matched imports: a completed download blocked only with "matched to movie/series by ID" is imported (ManualImport, once per download) when the grab history, title, year, season (Sonarr) and the app's own proposals all agree; otherwise the normal blocked alert names the reason. QW_AUTO_IMPORT=0 switches it off.
Partial season packs (Sonarr): a pack holding fewer episodes than grabbed, finished in the download client (qBittorrent / SABnzbd), every file one distinct episode of the season: its episodes are imported (or Sonarr already did), then the queue item is removed (download copy deleted, no blocklist) and the missing episodes searched; one digest line, no Telegram.
Digest: watchdog-digest.prom (node-exporter textfile, last 48 h) in the state dir, read by fastpi's morning digest. Every send, hold and digest event is a line in watchdog-sends.log.
Usage: scripts/queue-watchdog.py [--dry-run]   (--dry-run: only the removal and ID-match auto-import checks, prints, changes nothing)"""
import datetime
import html
import importlib.util
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
SUBTITLE_DIGEST = {"subtitle_repaired", "subtitle_suspect", "subtitle_forced_synced", "subtitle_forced_unverified"}
DEFAULT_EVENTS = "/srv/appdata/radarr/guard-events:/srv/appdata/sonarr/guard-events:/srv/appdata/bazarr/subtitle-check/events"
ID_MATCH = re.compile(r"matched to (movie|series) by ID")
PARTIAL_MSG = "One or more episodes expected in this release were not imported or missing"
QBIT_DONE = {"uploading", "stalledUP", "queuedUP", "pausedUP", "stoppedUP", "forcedUP"}
KEEP = {"removeFromClient": "true", "blocklist": "false", "skipRedownload": "true"}
PARTIAL_WAIT = 2 * 3600
RATE_LIMIT = re.compile(r"\b429\b|request limit|too many requests|rate limit", re.I)
FAKE_MSG = re.compile(r"(found|contains) (executable|potentially dangerous) file|encrypted download|password[- ]protected|requires a password", re.I)
FAKE_EXT = {".exe", ".scr", ".lnk", ".msi", ".bat", ".cmd", ".com", ".pif", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".hta", ".ps1", ".jar", ".cpl", ".apk", ".sh"}
FAKE_NAME_EXT = {".exe", ".scr", ".lnk", ".msi", ".bat", ".cmd", ".pif", ".vbs", ".ps1"}
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".wmv", ".mov", ".webm", ".mpg", ".mpeg", ".vob", ".iso", ".flv"}
YEAR_RULE = HERE / "guard-year.jq"
DELETE = {"removeFromClient": "true", "blocklist": "true", "skipRedownload": "false"}
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
    data_root: str = "/srv/video"


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


def rate_limited(app, api, bad):
    """Names in bad whose last error, or newest warning in the app log, is a request limit."""
    errors = {n: (i or {}).get("error") for n, i in bad.items()}
    if not all(errors.values()):
        try:
            logs = (api("GET", "/log", {"page": 1, "pageSize": 200, "sortKey": "time", "sortDirection": "descending", "level": "warn"}) or {}).get("records") or []
        except ApiError as e:
            print(f"{app}: log API error, indexer failures alert: {e}", file=sys.stderr)
            logs = []
        for n in [n for n, e in errors.items() if not e]:
            errors[n] = next((r.get("message") for r in logs if n in str(r.get("message"))), None)
    return {n for n, e in errors.items() if e and RATE_LIMIT.search(e)}


def indexer_check(app, api, state, out, now):
    key, lkey = f"indexers/{app}", f"ratelimited/{app}"
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
    limited = rate_limited(app, api, bad) if bad else set()
    bad = {n: i for n, i in bad.items() if n not in limited}
    if limited and not limited <= set(state.get(lkey) or []):
        out.digest("indexer_rate_limited", app, ", ".join(sorted(limited)))
    if limited:
        state[lkey] = sorted(limited)
    else:
        state.pop(lkey, None)
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


def words(text):
    return re.findall(r"\w+", str(text or "").casefold())


def part_of(part, whole):
    p, w = words(part), words(whole)
    return bool(p) and any(w[i:i + len(p)] == p for i in range(len(w) - len(p) + 1))


def strip_year(title):
    return re.sub(r"\s*\(?\b(19|20)\d{2}\)?\s*$", "", title or "")


def series_names(series):
    titles = [series.get("title")] + [a.get("title") for a in series.get("alternateTitles") or []]
    return {clean(strip_year(t)) for t in titles if t} - {""}


def id_match_groups(records):
    """{downloadId: records} of downloads whose every queue record is completed, importBlocked, and carries only the ID-match message."""
    groups = {}
    for rec in records:
        if rec.get("downloadId"):
            groups.setdefault(rec["downloadId"], []).append(rec)

    def only_id(rec):
        msgs = [m for sm in rec.get("statusMessages") or [] for m in sm.get("messages") or []]
        return (rec.get("status") == "completed" and rec.get("trackedDownloadState") == "importBlocked"
                and bool(msgs) and all(ID_MATCH.search(m) for m in msgs))
    return {d: recs for d, recs in groups.items() if all(only_id(r) for r in recs)}


def group_label(app, recs):
    if app == "radarr":
        return f"{label(app, recs[0])}: {recs[0].get('title')}"
    seasons = sorted({(r.get("episode") or {}).get("seasonNumber") for r in recs} - {None})
    name = label("sonarr", dict(recs[0], episode=None))
    return f"{name} {'/'.join(f'S{s:02d}' for s in seasons)}".rstrip() + f": {recs[0].get('title')}"


def id_import_files(app, api, download_id, recs, finished=None):
    """ManualImport files for an ID-matched download, or Refused(reason)."""
    return radarr_import_files(api, download_id, recs) if app == "radarr" else sonarr_import_files(api, download_id, recs, finished)


def radarr_import_files(api, download_id, recs):
    mids = {r.get("movieId") for r in recs}
    if len(mids) != 1 or None in mids:
        raise Refused("queue records name no single movie")
    mid = mids.pop()
    movie = api("GET", f"/movie/{mid}") or {}
    grabs = [h for h in (api("GET", "/history", {"downloadId": download_id, "pageSize": 1000}) or {}).get("records") or [] if h.get("eventType") == "grabbed"]
    if not grabs:
        raise Refused("no grab history for this download")
    for h in grabs:
        data = h.get("data") or {}
        if h.get("movieId") != mid or data.get("movieMatchType") != "Id" or not movie.get("tmdbId") or str(data.get("tmdbId")) != str(movie.get("tmdbId")):
            raise Refused("grab history is not this movie by its TMDB id")
    info = (api("GET", "/parse", {"title": recs[0].get("title")}) or {}).get("parsedMovieInfo") or {}
    year = info.get("year") or 0
    if not year or year != movie.get("year"):
        raise Refused(f"release year {year or 'none'} is not the movie year {movie.get('year')}")
    parsed_words = {w for t in (info.get("movieTitles") or []) + [info.get("primaryMovieTitle")] for w in words(t)}
    names = [movie.get("title"), movie.get("originalTitle")] + [a.get("title") for a in movie.get("alternateTitles") or []]
    if not any(words(n) and set(words(n)) <= parsed_words for n in names):
        raise Refused(f'release title "{info.get("primaryMovieTitle")}" contains no title or alternate title of the movie')
    out_dir = (recs[0].get("outputPath") or "").rstrip("/")
    proposals = api("GET", "/manualimport", {"downloadId": download_id, "filterExistingFiles": "true"}) or []
    for p in proposals:
        if p.get("rejections"):
            raise Refused("Radarr rejects a file: " + "; ".join(str(r.get("reason")) for r in p["rejections"]))
        if (p.get("movie") or {}).get("id") != mid or not out_dir or not str(p.get("path", "")).startswith(out_dir + "/"):
            raise Refused("a proposed file is not this movie inside the download folder")
    if not proposals:
        raise Refused("Radarr proposes no file")
    keys = ("path", "folderName", "releaseGroup", "quality", "languages", "indexerFlags")
    return [{k: p.get(k) for k in keys} | {"movieId": mid, "downloadId": download_id} for p in proposals]


def sonarr_import_files(api, download_id, recs, finished=None):
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
    year = title.get("year") or 0
    titles = [series.get("title")] + [a.get("title") for a in series.get("alternateTitles") or []]
    loose = year and year == series.get("year") and any(part_of(parsed_title, strip_year(t)) for t in titles if t)
    if clean(parsed_title) not in series_names(series) and not loose:
        raise Refused(f'release title "{parsed_title}" is not the series title')
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
    covered = set(proposed)
    if not proposals or len(proposed) != len(covered) or not covered <= queued:
        raise Refused(f"proposed files ({len(proposed)}) do not cover exactly the {len(queued)} grabbed episodes")
    if covered != queued:
        part = f"partial pack ({len(covered)} of {len(queued)} episodes)"
        if not info.get("fullSeason"):
            raise Refused(f"proposed files ({len(proposed)}) do not cover exactly the {len(queued)} grabbed episodes")
        why = "download client not checked" if finished is None else finished(recs[0])
        if why:
            raise Refused(f"{part}, download not finished: {why}")
    keys = ("path", "folderName", "releaseGroup", "quality", "languages", "indexerFlags", "releaseType")
    return [{k: p.get(k) for k in keys} | {"seriesId": sid, "episodeIds": [e["id"] for e in p["episodes"]], "downloadId": download_id} for p in proposals]


def auto_import(app, api, records, state, out, now, cfg, finished=None):
    """ID-matched downloads: import the ones that pass, mark the rest; (download ids imported this run, rc)."""
    done, rc = set(), 0
    if not cfg.auto_import:
        return done, rc
    groups = id_match_groups(records)
    queued = {r.get("downloadId") for r in records}
    for key in [k for k in state if k.startswith((f"autoimport:{app}:", f"partialdone:{app}:")) and k.split(":", 2)[2] not in queued]:
        del state[key]
    for dl, recs in groups.items():
        key = f"autoimport:{app}:{dl}"
        if key in state:
            continue
        name = group_label(app, recs)
        try:
            files = id_import_files(app, api, dl, recs, finished)
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
        missing = missing_of(recs, files)
        if missing:
            state[f"partial:{app}:{dl}"] = partial_entry(recs, missing, r.get("id"), now)
        else:
            out.digest("auto_imported", app, name)
        done.add(dl)
    return done, rc


def missing_of(recs, files):
    covered = {i for f in files for i in f.get("episodeIds") or []}
    return sorted({r.get("episodeId") for r in recs if r.get("episodeId")} - covered)


def partial_entry(recs, missing, cmd, now):
    eps = {r.get("episodeId"): r.get("episode") or {} for r in recs}
    season = next(iter(eps.values())).get("seasonNumber") or 0
    labels = ", ".join(f"E{eps[i].get('episodeNumber', 0):02d}" for i in sorted(missing, key=lambda i: eps[i].get("episodeNumber", 0)))
    return {"cmd": cmd, "since": now, "imported": sorted(set(eps) - set(missing)), "missing": list(missing), "labels": labels,
            "title": f"{label('sonarr', dict(recs[0], episode=None))} S{season:02d}", "of": len(eps)}


def own_partial_groups(records):
    """{downloadId: records} of downloads Sonarr imported in part and holds only with its "episodes missing" message."""
    def only_partial(rec):
        sms = rec.get("statusMessages") or []
        return (rec.get("status") == "completed" and rec.get("trackedDownloadState") in ("importBlocked", "importPending") and bool(sms)
                and all(PARTIAL_MSG in str(sm.get("title")) and not sm.get("messages") for sm in sms))
    return {d: recs for d, recs in groups_of(records).items() if all(only_partial(r) for r in recs)}


def own_partial_missing(api, download_id, recs, finished):
    """Missing episode ids of a pack Sonarr imported in part, or Refused(reason)."""
    sids = {r.get("seriesId") for r in recs}
    seasons = {(r.get("episode") or {}).get("seasonNumber") for r in recs}
    if len(sids) != 1 or None in sids or len(seasons) != 1:
        raise Refused("queue records name no single series and season")
    queued = {r.get("episodeId") for r in recs}
    hist = (api("GET", "/history", {"downloadId": download_id, "pageSize": 1000}) or {}).get("records") or []
    grabs = [h for h in hist if h.get("eventType") == "grabbed"]
    sid = next(iter(sids))
    if not grabs or any(h.get("seriesId") != sid for h in grabs) or {h.get("episodeId") for h in grabs} != queued:
        raise Refused("grab history is not exactly the queued episodes of this series")
    imported = [h.get("episodeId") for h in hist if h.get("eventType") == "downloadFolderImported"]
    if not imported or len(imported) != len(set(imported)) or not set(imported) < queued:
        raise Refused(f"imported episodes ({len(imported)}) are not distinct queued episodes with some missing")
    why = "download client not checked" if finished is None else finished(recs[0])
    if why:
        raise Refused(f"partial pack, download not finished: {why}")
    return sorted(queued - set(imported))


def own_partials(app, api, records, state, now, cfg, finished=None):
    """Packs Sonarr imported in part: queue them for finish_partials; rc."""
    rc = 0
    if app != "sonarr" or not cfg.auto_import:
        return rc
    for dl, recs in own_partial_groups(records).items():
        if f"partial:{app}:{dl}" in state or f"partialdone:{app}:{dl}" in state:
            continue
        name = group_label(app, recs)
        try:
            missing = own_partial_missing(api, dl, recs, finished)
        except Refused as e:
            print(f"partial pack left blocked: {name}: {e}")
            for r in recs:
                r["autoImport"] = str(e)
            continue
        except ApiError as e:
            print(f"partial pack check failed, retried next run: {name}: {e}", file=sys.stderr)
            rc = 1
            continue
        state[f"partial:{app}:{dl}"] = partial_entry(recs, missing, None, now)
    return rc


def finish_partials(app, api, records, state, out, now):
    """Partial packs whose import landed: remove the queue item (no blocklist), search the missing episodes; (download ids handled or still importing, rc)."""
    busy, rc = set(), 0
    queue_ids = {}
    for r in records:
        queue_ids.setdefault(r.get("downloadId"), r.get("id"))
    for key in [k for k in state if k.startswith(f"partial:{app}:")]:
        dl, p = key.split(":", 2)[2], state[key]
        try:
            if p.get("cmd") is not None:
                if p["since"] == now:
                    busy.add(dl)
                    continue
                status = (api("GET", f"/command/{p['cmd']}") or {}).get("status")
                if status in ("queued", "started") and now - p["since"] < PARTIAL_WAIT:
                    busy.add(dl)
                    continue
                if status != "completed":
                    print(f"partial import did not finish ({status}), left blocked: {p['title']}", file=sys.stderr)
                    del state[key]
                    continue
            have = {e.get("id") for e in api("GET", "/episode", {"episodeIds": p["imported"]}) or [] if e.get("hasFile")}
            if have != set(p["imported"]):
                print(f"partial import: {len(have)} of {len(p['imported'])} episodes have a file, left blocked: {p['title']}", file=sys.stderr)
                del state[key]
                continue
            if not p.get("removed") and dl in queue_ids:
                api("DELETE", f"/queue/{queue_ids[dl]}", KEEP)
            p["removed"] = True
            api("POST", "/command", body={"name": "EpisodeSearch", "episodeIds": p["missing"]})
        except ApiError as e:
            if now - p["since"] > PARTIAL_WAIT:
                print(f"partial pack: given up, left blocked: {p['title']}: {e}", file=sys.stderr)
                del state[key]
                continue
            print(f"partial pack: retried next run: {p['title']}: {e}", file=sys.stderr)
            rc = 1
            busy.add(dl)
            continue
        text = f"{p['title']}: imported {len(p['imported'])} of {p['of']}, searching {p['labels']}"
        print(f"partial pack: {text}")
        out.digest("partial_imported", app, text)
        del state[key]
        state[f"partialdone:{app}:{dl}"] = now
        busy.add(dl)
    return busy, rc


def download_finished(rec, qbit, sab):
    """None when the download client reports the download complete and done with post-processing, else why not."""
    dl = str(rec.get("downloadId") or "")
    try:
        if rec.get("protocol") == "torrent":
            t = next((t for t in qbit(f"/torrents/info?hashes={dl.lower()}") or [] if str(t.get("hash", "")).lower() == dl.lower()), None)
            if not t:
                return "qBittorrent: torrent not found"
            if t.get("state") not in QBIT_DONE or t.get("progress") != 1:
                return f"qBittorrent: {t.get('state')}, {float(t.get('progress') or 0) * 100:.0f} %"
            return None
        if rec.get("protocol") == "usenet":
            if any(q.get("nzo_id") == dl for q in ((sab({"mode": "queue", "nzo_ids": dl}) or {}).get("queue") or {}).get("slots") or []):
                return "SABnzbd: still in the queue"
            h = next((h for h in ((sab({"mode": "history", "nzo_ids": dl}) or {}).get("history") or {}).get("slots") or [] if h.get("nzo_id") == dl), None)
            if not h:
                return "SABnzbd: not in the history"
            return None if h.get("status") == "Completed" else f"SABnzbd: {h.get('status')}"
    except (ApiError, OSError, ValueError, AttributeError) as e:
        return f"download client not reachable: {e}"
    return f"unknown protocol {rec.get('protocol')}"


def groups_of(records):
    groups = {}
    for rec in records:
        if rec.get("downloadId"):
            groups.setdefault(rec["downloadId"], []).append(rec)
    return groups


def fake_reason(recs, cfg):
    """Why a download is a fake (executable, dangerous or encrypted files), or None."""
    rec = recs[0]
    if rec.get("status") == "failed":
        return None
    msg = next((m for r in recs for sm in r.get("statusMessages") or [] for m in sm.get("messages") or [] if FAKE_MSG.search(m)), None)
    if msg:
        return msg
    out = str(rec.get("outputPath") or "").rstrip("/")
    if pathlib.PurePosixPath(out).suffix.lower() in FAKE_NAME_EXT:
        return f"downloaded file {pathlib.PurePosixPath(out).name}"
    if rec.get("status") == "completed" and out.startswith("/data/"):
        root = pathlib.Path(cfg.data_root) / out[len("/data/"):]
        files = [f for f in root.rglob("*") if f.is_file()][:1000] if root.is_dir() else []
        exts = {f.suffix.lower() for f in files}
        if exts & FAKE_EXT and not exts & VIDEO_EXT:
            return "the download holds only " + ", ".join(sorted(exts & FAKE_EXT)) + " files, no video"
    return None


def year_fits(facts):
    r = subprocess.run(["jq", "-e", "-f", str(YEAR_RULE)], input=json.dumps(facts), capture_output=True, text=True, timeout=30)
    if r.returncode not in (0, 1):
        raise ApiError(f"year rule: {r.stderr.strip()}")
    return r.returncode == 0


def year_of(date):
    return int(date[:4]) if str(date or "")[:4].isdigit() else 0


def wrong_year(app, api, recs):
    """Why an ID-matched blocked download is the wrong film (the guard's year rule), or None."""
    def id_matched(rec):
        return rec.get("trackedDownloadState") in ("importBlocked", "importPending") and any(
            ID_MATCH.search(m) for sm in rec.get("statusMessages") or [] for m in sm.get("messages") or [])
    rec = recs[0]
    tid = rec.get("movieId") if app == "radarr" else rec.get("seriesId")
    if not tid or not all(id_matched(r) for r in recs):
        return None
    parsed = api("GET", "/parse", {"title": rec.get("title")}) or {}
    facts = {"app": app, "second": 0, "first": 0, "last": 0, "ended": True, "now": datetime.date.today().year, "air": []}
    if app == "radarr":
        meta = api("GET", f"/movie/{tid}") or {}
        facts |= {"got": (parsed.get("parsedMovieInfo") or {}).get("year") or 0, "second": meta.get("secondaryYear") or 0}
    else:
        meta = api("GET", f"/series/{tid}") or {}
        facts |= {"got": ((parsed.get("parsedEpisodeInfo") or {}).get("seriesTitleInfo") or {}).get("year") or 0,
                  "first": year_of(meta.get("firstAired")), "last": year_of(meta.get("lastAired")), "ended": meta.get("status") == "ended",
                  "air": sorted({year_of((r.get("episode") or {}).get("airDate")) for r in recs} - {0})}
    facts["year"] = meta.get("year") or 0
    return None if year_fits(facts) else f"release year {facts['got']}, not {facts['year']}"


def removal(app, api, recs, cfg):
    """(digest kind, reason) when the download is to be removed and blocklisted, else None."""
    why = fake_reason(recs, cfg)
    if why:
        return "malware_removed", why
    why = wrong_year(app, api, recs)
    return ("guard_removed", why) if why else None


def removal_failed_text(app, kind, recs, why, err):
    app_name = app.capitalize()
    head = "Fake download" if kind == "malware_removed" else "Wrong-film backstop"
    return "\n".join([f"🔴 <b>{head}: {app_name}</b>", "", f"• 🎬 {html.escape(label(app, recs[0]))}", f"• Release: <code>{html.escape(recs[0].get('title') or '')}</code>",
                      f"• Why: {html.escape(why)}", "• Result: could not be removed: check by hand",
                      f"<blockquote expandable>{html.escape(err)}\n{app_name} → Activity → Queue</blockquote>"])


def removals(app, api, records, state, out, now, cfg):
    """Removes wrong-year ID matches and fakes from the client with blocklist; (handled download ids, rc)."""
    handled, rc = set(), 0
    groups = groups_of(records)
    for key in [k for k in state if k.startswith((f"removed:{app}:", f"removefail:{app}:")) and k.split(":", 2)[2] not in groups]:
        del state[key]
    for dl, recs in groups.items():
        if f"removed:{app}:{dl}" in state:
            handled.add(dl)
            continue
        name = group_label(app, recs)
        try:
            found = removal(app, api, recs, cfg)
        except ApiError as e:
            print(f"removal check failed, retried next run: {name}: {e}", file=sys.stderr)
            rc = 1
            continue
        if not found:
            continue
        kind, why = found
        handled.add(dl)
        try:
            api("DELETE", f"/queue/{recs[0]['id']}", DELETE)
        except ApiError as e:
            print(f"{kind}: removal failed, retried next run: {name}: {why}: {e}", file=sys.stderr)
            rc = 1
            fkey = f"removefail:{app}:{dl}"
            if fkey not in state:
                r = out.immediate(fkey, kind.replace("removed", "manual"), name, removal_failed_text(app, kind, recs, why, str(e)))
                if r == "sent":
                    state[fkey] = now
            continue
        state[f"removed:{app}:{dl}"] = now
        state.pop(f"removefail:{app}:{dl}", None)
        print(f"{kind}: {name}: {why}: removed, blocklisted, searching again")
        out.digest(kind, app, name)
    return handled, rc


def dry_run(apps, cfg, finished=None):
    """Lines describing what removals and auto_import would do; no command, no state, no message."""
    lines, n, ok = [], 0, 0
    if not cfg.auto_import:
        lines.append("auto-import switched off (QW_AUTO_IMPORT=0)")
    for app, api in apps.items():
        records = fetch(app, api)
        gone = set()
        for dl, recs in groups_of(records).items():
            found = removal(app, api, recs, cfg)
            if found:
                gone.add(dl)
                lines.append(f"would remove ({found[0]}): {group_label(app, recs)}: {found[1]}")
        if not cfg.auto_import:
            continue
        for dl, recs in id_match_groups([r for r in records if r.get("downloadId") not in gone]).items():
            n += 1
            name = group_label(app, recs)
            try:
                files = id_import_files(app, api, dl, recs, finished)
            except Refused as e:
                lines.append(f"not imported: {name}: {e}")
                continue
            ok += 1
            missing = missing_of(recs, files) if app == "sonarr" else []
            if missing:
                p = partial_entry(recs, missing, None, 0)
                lines.append(f"would import {len(p['imported'])} of {p['of']}, then search {p['labels']}: {name} ({len(files)} files)")
            else:
                lines.append(f"would import: {name} ({len(files)} files)")
        if app == "sonarr":
            for dl, recs in own_partial_groups(records).items():
                try:
                    p = partial_entry(recs, own_partial_missing(api, dl, recs, finished), None, 0)
                    lines.append(f"would search {p['labels']} ({len(p['imported'])} of {p['of']} imported by Sonarr): {group_label(app, recs)}")
                except Refused as e:
                    lines.append(f"partial pack left blocked: {group_label(app, recs)}: {e}")
    lines.append(f"id-match auto-import: {n} candidate(s), {ok} importable")
    return lines


def run(apps, state_path, event_dirs, send, cfg, now=None, finished=None):
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
        removed, r = removals(app, api, records, state, out, now, cfg)
        rc |= r
        records = [rec for rec in records if rec.get("downloadId") not in removed]
        imported, r = auto_import(app, api, records, state, out, now, cfg, finished)
        rc |= r
        rc |= own_partials(app, api, [rec for rec in records if rec.get("downloadId") not in imported], state, now, cfg, finished)
        busy, r = finish_partials(app, api, records, state, out, now)
        rc |= r
        alerts, state = evaluate(app, [rec for rec in records if rec.get("downloadId") not in imported | busy], state, now, cfg)
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
        q = APPS[app] + ("&" + urllib.parse.urlencode(query, doseq=True) if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = {"X-Api-Key": get("ApiKey")} | ({"Content-Type": "application/json"} if data else {})
        req = urllib.request.Request(f"{base}{path}?{q}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else None
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


def clients_finished():
    """download_finished bound to the real qBittorrent and SABnzbd (clients of scripts/night-downloads.py)."""
    spec = importlib.util.spec_from_file_location("night_downloads", HERE / "night-downloads.py")
    nd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nd)

    def wrap(make):
        def call(*a):
            try:
                return make()(*a)
            except Exception as e:
                raise ApiError(str(e)) from None
        return call
    secrets = HERE.parent / "secrets"
    qbit = wrap(lambda: nd.qbit_client(os.environ.get("QW_QBIT_URL", "http://127.0.0.1:8080") + "/api/v2", "admin", os.environ.get("QW_QBIT_PASS", secrets / "qbit-webui.password")))
    sab = wrap(lambda: nd.sab_client(os.environ.get("QW_SAB_URL", "http://127.0.0.1:8081"), os.environ.get("QW_SAB_KEY", secrets / "hook" / "sabnzbd.apikey")))
    return lambda rec: download_finished(rec, qbit, sab)


def main():
    apps = {app: client(app, os.environ.get(f"QW_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in APPS}
    dirs = os.environ.get("QW_EVENTS", DEFAULT_EVENTS).split(":")
    cfg = Config(int(os.environ.get("QW_BLOCKED_MIN", 30)), int(os.environ.get("QW_STALL_HOURS", 6)), auto_import=os.environ.get("QW_AUTO_IMPORT", "1") != "0",
                 data_root=os.environ.get("QW_DATA_ROOT", "/srv/video"))
    if sys.argv[1:] == ["--dry-run"]:
        try:
            print("\n".join(dry_run(apps, cfg, clients_finished())))
        except ApiError as e:
            print(f"API error: {e}", file=sys.stderr)
            return 1
        return 0
    if sys.argv[1:]:
        print("usage: " + __doc__.rsplit("Usage: ", 1)[1], file=sys.stderr)
        return 2
    script = os.environ.get("QW_ALERT", HERE.parent.parent / "_global" / "scripts" / "oob-alert.sh")
    return run(apps, os.environ.get("QW_STATE", STATE_DIR / "queue-watchdog.json"), dirs, sender(script), cfg, finished=clients_finished())


if __name__ == "__main__":
    sys.exit(main())
