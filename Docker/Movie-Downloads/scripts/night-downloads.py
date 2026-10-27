#!/usr/bin/env python3
"""Night downloads (README "Can wait: downloads at night"): resumes the downloads the untagged "night" clients added paused.
Low tariff (22:00-06:00 and all Sunday): FIFO, one night download per client at a time (qBittorrent by added_on, SABnzbd by queue position, one title finished before the next); a torrent stalled for STALL_MIN lets the next one start too.
Outside the low tariff: night items still downloading are paused unless their ETA is under GRACE_MIN; they resume at the next low tariff where they stopped. Any time: the night items of titles tagged download-now resume and are never paused.
Writes ~/.local/state/movie-downloads/night-downloads.json ({"pending": n, "stalled": {hash: since}}), which fastpi reads to decide its 22:05 wake.
Writes the download ids waiting for the night (outside the low tariff) to /srv/appdata/indexer-bar/night-waiting.json for Seerr's night label (../Seerr/README.md "Night label"; env ND_PUBLIC, empty = off).
Run by systemd/night-downloads.timer (every 2 min while awake, and at boot). Usage: scripts/night-downloads.py [--dry-run]; ND_NOW=2026-10-27T22:10 simulates a clock (with --dry-run)."""
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
PUBLIC = "/srv/appdata/indexer-bar/night-waiting.json"
NIGHT_QBIT = {"radarr-night", "sonarr-night"}
NIGHT_SAB = {"movies-night", "tv-night"}
TAG = "download-now"
PAUSED_QBIT = {"pausedDL", "stoppedDL"}
RUNNING_QBIT = {"downloading", "stalledDL", "metaDL", "forcedMetaDL", "forcedDL", "queuedDL", "checkingDL", "allocating"}
STALLED_QBIT = {"stalledDL", "metaDL", "forcedMetaDL"}
QBIT_ETA_UNKNOWN = 8640000
GRACE_MIN = 15
STALL_MIN = int(os.environ.get("ND_STALL_MIN", "20"))
APPS = {"radarr": "includeMovie=true", "sonarr": "includeSeries=true"}


class ApiError(Exception):
    pass


@dataclass
class Plan:
    hashes: list = field(default_factory=list)
    nzos: list = field(default_factory=list)
    pause_hashes: list = field(default_factory=list)
    pause_nzos: list = field(default_factory=list)
    pending: int = 0
    stalled: dict = field(default_factory=dict)
    waiting: list = field(default_factory=list)


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


def title_ids(app, queue):
    out = {}
    for rec in queue.get("records") or []:
        title = rec.get("movieId") or rec.get("seriesId")
        if rec.get("downloadId") and title:
            out[rec["downloadId"].lower()] = (app, title)
    return out


def qbit_eta(t):
    eta = t.get("eta")
    if t.get("state") in STALLED_QBIT or not isinstance(eta, int) or not 0 <= eta < QBIT_ETA_UNKNOWN:
        return None
    return eta


def sab_eta(s, kbps):
    try:
        parts = [int(x) for x in str(s.get("timeleft", "")).split(":")]
        secs = sum(v * m for v, m in zip(reversed(parts), (1, 60, 3600, 86400)))
        left = float(s.get("mbleft") or 0)
    except ValueError:
        return None
    if kbps <= 0 or s.get("status") != "Downloading" or (secs == 0 and left > 0):
        return None
    return secs


def qbit_order(t):
    return t.get("added_on") or 0


def sab_order(s):
    return s.get("index", 0)


def fifo(items, key, order, titles):
    group = lambda it: titles.get(it[key].lower(), it[key].lower())
    first = {}
    for it in items:
        first[group(it)] = min(first.get(group(it), order(it)), order(it))
    return sorted(items, key=lambda it: (first[group(it)], order(it)))


def _client(p, low, items, key, eta, stalled_long, resume, pause, titles, order):
    taken = False
    for it in fifo(items, key, order, titles):
        before = p.pending
        if it["_paused"]:
            if low and not taken:
                resume.append(it[key])
                taken = True
            else:
                p.pending += 1
        elif not low:
            e = eta(it)
            if e is None or e >= GRACE_MIN * 60:
                pause.append(it[key])
                p.pending += 1
        elif stalled_long(it):
            continue
        elif taken:
            pause.append(it[key])
            p.pending += 1
        else:
            taken = True
        if not low and p.pending > before:
            p.waiting.append(it[key].lower())


def plan(now, torrents, slots, urgent, titles=None, stalled=None, kbps=0):
    low, p, titles, stalled = low_tariff(now), Plan(), titles or {}, stalled or {}
    ts = now.timestamp()
    night_t, night_s = [], []
    for t in torrents:
        if t.get("category") not in NIGHT_QBIT or t.get("state") not in PAUSED_QBIT | RUNNING_QBIT:
            continue
        paused = t["state"] in PAUSED_QBIT
        if t["hash"].lower() in urgent:
            if paused:
                p.hashes.append(t["hash"])
            continue
        if t["state"] in STALLED_QBIT:
            p.stalled[t["hash"]] = stalled.get(t["hash"], ts)
        night_t.append({**t, "_paused": paused})
    for s in slots:
        if s.get("cat") not in NIGHT_SAB:
            continue
        paused = s.get("status") == "Paused"
        if s["nzo_id"].lower() in urgent:
            if paused:
                p.nzos.append(s["nzo_id"])
            continue
        night_s.append({**s, "_paused": paused})
    stalled_long = lambda t: t["hash"] in p.stalled and ts - p.stalled[t["hash"]] >= STALL_MIN * 60
    _client(p, low, night_t, "hash", qbit_eta, stalled_long, p.hashes, p.pause_hashes, titles, qbit_order)
    _client(p, low, night_s, "nzo_id", lambda s: sab_eta(s, kbps), lambda s: False, p.nzos, p.pause_nzos, titles, sab_order)
    return p


def write_public(path, p, now):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"waiting": p.waiting, "low_tariff": low_tariff(now), "updated": now.astimezone().isoformat(timespec="seconds")}) + "\n")
    tmp.chmod(0o644)
    tmp.replace(path)


def run(now, qbit, sab, apps, state_path, dry_run=False, public_path=None):
    rc, urgent, titles = 0, set(), {}
    for app, api in apps.items():
        try:
            queue = api("/queue?page=1&pageSize=1000")
            urgent |= urgent_ids(api("/tag"), queue)
            titles |= title_ids(app, queue)
        except ApiError as e:
            print(f"{app}: {e}", file=sys.stderr)
            rc = 1
    try:
        torrents = qbit("/torrents/info")
        queue = sab({"mode": "queue", "limit": "1000"})["queue"]
        slots = queue["slots"]
        kbps = float(queue.get("kbpersec") or 0)
    except (ApiError, KeyError, TypeError, ValueError) as e:
        print(f"download client not readable, state kept: {e}", file=sys.stderr)
        return 1
    state_path = pathlib.Path(state_path)
    try:
        stalled = json.loads(state_path.read_text()).get("stalled") or {}
    except (OSError, ValueError, AttributeError):
        stalled = {}
    p = plan(now, torrents, slots, urgent, titles, stalled, kbps)
    names = {t["hash"]: t.get("name") for t in torrents} | {s["nzo_id"]: s.get("filename") for s in slots}
    verb = lambda v: f"would {v}" if dry_run else v
    for i in p.hashes + p.nzos:
        print(f"{verb('resume')}: {names.get(i)}")
    for i in p.pause_hashes + p.pause_nzos:
        print(f"{verb('pause')}: {names.get(i)}")
    print(f"low tariff: {low_tariff(now)}, waiting for the night: {p.pending}")
    if dry_run:
        return rc
    try:
        if p.hashes:
            qbit("/torrents/start", {"hashes": "|".join(p.hashes)})
        if p.pause_hashes:
            qbit("/torrents/stop", {"hashes": "|".join(p.pause_hashes)})
        for n in p.nzos:
            sab({"mode": "queue", "name": "resume", "value": n})
        for n in p.pause_nzos:
            sab({"mode": "queue", "name": "pause", "value": n})
    except ApiError as e:
        print(f"resume/pause failed: {e}", file=sys.stderr)
        rc = 1
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pending": p.pending, "low_tariff": low_tariff(now), "updated": now.isoformat(timespec="seconds"), "stalled": p.stalled}) + "\n")
    tmp.replace(state_path)
    if public_path:
        try:
            write_public(public_path, p, now)
        except OSError as e:
            print(f"Seerr night label not written: {e}", file=sys.stderr)
            rc = 1
    return rc


def _open(req, timeout=30):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body.strip() else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ApiError(f"{req.full_url.split('?')[0]}: {e}") from None


LEGACY = {"/torrents/start": "/torrents/resume", "/torrents/stop": "/torrents/pause"}


def now_from(env):
    return datetime.datetime.fromisoformat(env["ND_NOW"]) if env.get("ND_NOW") else datetime.datetime.now()


def qbit_client(base):
    def call(path, data=None):
        if path in LEGACY:
            body = urllib.parse.urlencode(data).encode()
            try:
                return _open(urllib.request.Request(base + path, body))
            except ApiError:
                return _open(urllib.request.Request(base + LEGACY[path], body))
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
    if os.environ.get("ND_NOW") and not dry:
        sys.exit("ND_NOW only with --dry-run")
    qbit = qbit_client(os.environ.get("ND_QBIT_URL", "http://127.0.0.1:8080") + "/api/v2")
    sab = sab_client(os.environ.get("ND_SAB_URL", "http://127.0.0.1:8081"), os.environ.get("ND_SAB_KEY", HERE.parent / "secrets" / "hook" / "sabnzbd.apikey"))
    apps = {app: arr_client(app, os.environ.get(f"ND_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in APPS}
    return run(now_from(os.environ), qbit, sab, apps, os.environ.get("ND_STATE", STATE), dry_run=dry, public_path=os.environ.get("ND_PUBLIC", PUBLIC))


if __name__ == "__main__":
    sys.exit(main())
