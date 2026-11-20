#!/usr/bin/env python3
"""Quality block (README "Quality check", owner 2026-11-18): acts on the clamav-queue worker's quality check (Antivirus README "Quality check", quality-results.jsonl).
A broken download (video timestamp jump back over 1 s, no video or no audio stream) still in the Radarr/Sonarr queue is removed with blocklist and searched again, so it is never imported; one already imported is undone the language-block way (new file removed or the previous one put back from the recycle bin, release blocklisted, searched again).
Notes never block: a container read error and a duration outside the TMDB/TVDB runtime by more than 10 % and 5 min are only recorded (Seerr Manual page, Done tab).
Not found in either app yet: retried for PENDING_HOURS. The test film and hand imports are never blocked. QB_ACTION=log decides and records only.
Usage: scripts/quality-block.py [--dry-run] | -h   (run by systemd/quality-block.path and quality-block.timer)"""
import importlib.util
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("language_block", HERE / "language-block.py")
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)

RESULTS = pathlib.Path(os.environ.get("QB_RESULTS", "/var/lib/clamav-scan/textfile/quality-results.jsonl"))
STATE = pathlib.Path(os.environ.get("QB_STATE", lb.STATE_DIR / "quality-block.json"))
ACTION = os.environ.get("QB_ACTION", "block")
PENDING_HOURS = 24
IMPORTS_KEEP_DAYS = 14
SEEN_KEEP_DAYS = 30
DURATION_SHARE = 0.10
DURATION_MINUTES = 5
# phone versions (README "Phone versions") are no Radarr/Sonarr downloads: scripts/phone-fetch.py acts on their results
PHONE_DIR = "/data/usenet/complete/phone/"


def load_state(path):
    p = pathlib.Path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: not an object")
    return {"entries": list(data.get("entries") or []), "downloads": list(data.get("downloads") or []), "seen": dict(data.get("seen") or {}),
            "pending": dict(data.get("pending") or {})}


def save_state(path, state):
    lb.save_state(path, state)


def line_key(x):
    return f"{x.get('at')}|{x.get('path')}"


def new_lines(results, state):
    """Lines of the results file and its rotated .1, oldest first, not yet seen."""
    out = []
    for p in (pathlib.Path(str(results) + ".1"), pathlib.Path(results)):
        try:
            text = p.read_text()
        except OSError:
            continue
        for raw in text.splitlines():
            try:
                x = json.loads(raw)
            except ValueError:
                continue
            if isinstance(x, dict) and x.get("path") and line_key(x) not in state["seen"]:
                out.append(x)
    return out


def read_imports(path, now, prune=False):
    """The Connect hook's import log (<epoch> TAB <language-block queue line>), newer than IMPORTS_KEEP_DAYS; prune rewrites the file without the old lines."""
    p = pathlib.Path(path)
    try:
        text = p.read_text()
    except OSError:
        return []
    items, keep = [], []
    for raw in text.splitlines():
        at, _, rest = raw.partition("\t")
        try:
            at = float(at)
        except ValueError:
            continue
        if at < now - IMPORTS_KEEP_DAYS * 86400:
            continue
        keep.append(raw)
        it = lb.parse_queue_line(rest)
        if it:
            items.append(dict(it, at=at))
    if prune and len(keep) < len(text.splitlines()):
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text("".join(k + "\n" for k in keep))
        os.replace(tmp, p)
    return items


def queue_match(api, path):
    got = api("GET", "/queue", {"page": 1, "pageSize": 1000})
    for r in (got.get("records") if isinstance(got, dict) else got) or []:
        out = str(r.get("outputPath") or "").rstrip("/")
        if out and (path == out or path.startswith(out + "/")):
            return r
    return None


def import_matches(api, path):
    got = api("GET", "/history", {"page": 1, "pageSize": 250, "sortKey": "date", "sortDirection": "descending", "eventType": 3})
    return [r for r in (got.get("records") if isinstance(got, dict) else got) or [] if (r.get("data") or {}).get("droppedPath") == path]


def runtime(app, api, title, records):
    """Expected minutes: the film's runtime, or the sum of the file's episodes."""
    if app == "radarr":
        return title.get("runtime") or 0
    total = 0
    for eid in sorted({r.get("episodeId") for r in records if r.get("episodeId")}):
        total += (api("GET", f"/episode/{eid}") or {}).get("runtime") or 0
    return total


def duration_note(seconds, minutes):
    if not seconds or not minutes:
        return None
    have = seconds / 60
    if abs(have - minutes) > max(minutes * DURATION_SHARE, DURATION_MINUTES):
        return f"duration {have:.0f} min, expected {minutes} min"
    return None


def entry_for(app, title, x, now, file_name, download_id, release):
    kind = "movie" if app == "radarr" else "tv"
    return {"at": lb.ts(now), "app": app, "key": f"{kind}/{title['tmdbId']}" if title.get("tmdbId") else f"{app}:{title.get('id')}", "arrId": title.get("id"),
            "title": title.get("title"), "year": title.get("year"), "fileName": file_name, "downloadId": download_id or None, "verdict": x.get("verdict"),
            "reasons": list(x.get("reasons") or []), "notes": list(x.get("notes") or []), "release": (release or "")[:200] or None, "file": "kept",
            "blocked": False, "searched": False, "note": "", "checkedAt": x.get("at")}


def file_item(app, api, title, rec, imports):
    """The language-block queue item of an imported file: from the import log, else the file id from the app (no previous file to put back)."""
    path, dl = rec["data"].get("importedPath"), rec.get("downloadId")
    for it in reversed(imports):
        if it["path"] == path and (not dl or not it["downloadId"] or it["downloadId"].lower() == dl.lower()):
            return dict(it, downloadId=it["downloadId"] or dl), None
    if app == "radarr":
        files = api("GET", "/moviefile", {"movieId": title["id"]}) or []
    else:
        files = api("GET", "/episodefile", {"seriesId": title["id"]}) or []
    fid = next((str(f["id"]) for f in files if f.get("path") == path), "")
    return {"id": str(title["id"]), "path": path, "fileId": fid, "downloadId": dl, "upgrade": False, "deleted": [], "recycled": []}, \
        "no import log line: a previous file was not put back"


def handle(apis, x, imports, state, now=None, dry=False, action=ACTION, probe=None, host=None, tmdb=None):
    """One result line: ("done", entry or None) or ("pending", None) while neither app knows the file yet."""
    now = time.time() if now is None else now
    if x.get("verdict") not in ("ok", "broken") or x["path"].startswith(PHONE_DIR):
        return "done", None
    path = x["path"]
    try:
        for app, api in apis.items():
            q = queue_match(api, path) if x["verdict"] == "broken" else None
            if q:
                title = api("GET", ("/movie/" if app == "radarr" else "/series/") + str(q.get("movieId") or q.get("seriesId")))
                e = entry_for(app, title, x, now, os.path.basename(path), q.get("downloadId"), q.get("title"))
                if app == "radarr" and title.get("tmdbId") == lb.TEST_FILM:
                    e["note"] = "test film: not blocked"
                elif dry or action == "log":
                    e["file"] = "dry run" if dry else "log only"
                else:
                    api("DELETE", f"/queue/{q['id']}", {"removeFromClient": "true", "blocklist": "true", "skipRedownload": "false"})
                    e.update(file="never imported", blocked=True, searched=True)
                return "done", e
            recs = import_matches(api, path)
            if recs:
                return "done", imported(app, api, x, recs, imports.get(app) or [], state, now, dry, action, probe, host, tmdb)
    except lb.ApiError as err:
        print(f"quality-block: {path}: {err}, retried later", file=sys.stderr)
        return "pending", None
    first = state["pending"].setdefault(line_key(x), lb.ts(now))
    if x["verdict"] == "broken" and now - (lb.epoch(first) or now) > PENDING_HOURS * 3600:
        e = {"at": lb.ts(now), "app": None, "key": None, "title": x.get("title"), "fileName": os.path.basename(path), "verdict": "broken",
             "reasons": list(x.get("reasons") or []), "notes": list(x.get("notes") or []), "file": "not found", "blocked": False, "searched": False,
             "note": f"neither Radarr nor Sonarr knows this download after {PENDING_HOURS} h: nothing done", "checkedAt": x.get("at")}
        return "done", e
    if now - (lb.epoch(first) or now) > PENDING_HOURS * 3600:
        return "done", None
    return "pending", None


def imported(app, api, x, recs, imports, state, now, dry, action, probe, host, tmdb):
    rec = recs[0]
    title = api("GET", ("/movie/" if app == "radarr" else "/series/") + str(rec.get("movieId") or rec.get("seriesId")))
    e = entry_for(app, title, x, now, os.path.basename(rec["data"].get("importedPath") or x["path"]), rec.get("downloadId"), rec.get("sourceTitle"))
    note = duration_note(x.get("duration"), runtime(app, api, title, recs))
    if note:
        e["notes"].append(note)
    if x["verdict"] == "ok":
        if not e["notes"]:
            return None
        e["verdict"] = "note"
        return e
    notes = []
    if app == "radarr" and title.get("tmdbId") == lb.TEST_FILM:
        e["note"] = "test film: not blocked"
        return e
    if not rec.get("downloadId"):
        e["note"] = "imported by hand (no download id): not blocked"
        return e
    if dry or action == "log":
        e["file"] = "dry run" if dry else "log only"
        return e
    grab = lb.find_grab(api, rec["downloadId"])
    if grab is None:
        e["note"] = "no grab event for this download: not blocked"
        return e
    item, why = file_item(app, api, title, rec, imports)
    if why:
        notes.append(why)
    lb.block_release(app, api, item, title, grab, probe or lb.mkvmerge_tracks, state, e, notes, host, now, tmdb)
    e["note"] = "; ".join(notes)
    return e


def prune(state, now):
    state["entries"] = [x for x in state["entries"] if lb.recent(x, now)]
    state["downloads"] = [x for x in state["downloads"] if lb.recent(x, now)]
    state["seen"] = {k: v for k, v in state["seen"].items() if (lb.epoch(v) or now) >= now - SEEN_KEEP_DAYS * 86400}
    state["pending"] = {k: v for k, v in state["pending"].items() if k not in state["seen"]}


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    dry = "--dry-run" in argv
    if [a for a in argv if a != "--dry-run"]:
        print("usage: scripts/quality-block.py [--dry-run] | -h", file=sys.stderr)
        return 2
    now = time.time()
    try:
        state = load_state(STATE)
    except (OSError, ValueError) as e:
        print(f"quality-block: {STATE} unreadable, nothing done: {e}", file=sys.stderr)
        return 1
    apis, imports, rc = {}, {}, 0
    for app in ("radarr", "sonarr"):
        config = os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")
        try:
            apis[app] = lb.client(config)
        except OSError as e:
            print(f"{app}: {e}", file=sys.stderr)
            rc = 1
            continue
        imports[app] = read_imports(pathlib.Path(config).parent / "quality-block.imports", now, prune=not dry)
    lines = new_lines(RESULTS, state)
    tmdb = lb.tmdb_languages() if any(x.get("verdict") == "broken" for x in lines) else None
    for x in lines:
        status, e = handle(apis, x, imports, state, now=now, dry=dry, tmdb=tmdb)
        if status == "pending":
            continue
        state["seen"][line_key(x)] = lb.ts(now)
        if e:
            state["entries"].append(e)
            what = "; ".join(e["reasons"] + e["notes"])
            print(f"{e.get('app')}: {e.get('title')} ({e.get('year')}): {e['fileName']}: {e['verdict']}: {what}; file {e['file']}"
                  f"{', blocklisted' if e['blocked'] else ''}{', searched again' if e['searched'] else ''}{'; ' + e['note'] if e['note'] else ''}")
    if not dry:
        prune(state, now)
        save_state(STATE, state)
    if tmdb:
        tmdb.save()
    pending = len(state["pending"])
    if pending:
        print(f"quality-block: {pending} checked file(s) not in Radarr/Sonarr yet; next: systemctl list-timers quality-block.timer")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
