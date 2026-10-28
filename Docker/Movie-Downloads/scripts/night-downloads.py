#!/usr/bin/env python3
"""Night downloads (README "Can wait: downloads at night"): resumes the downloads the untagged "night" clients added paused.
Low tariff (22:00-06:00 and all Sunday): every night movie resumes (the clients' own limits apply); TV runs one download at a time across both clients: one series at a time, FIFO by its first grab, within it season then episode; a torrent stalled for STALL_MIN lets the next one start too.
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
NIGHT_TV = {"sonarr-night", "tv-night"}
TAG = "download-now"
PAUSED_QBIT = {"pausedDL", "stoppedDL"}
RUNNING_QBIT = {"downloading", "stalledDL", "metaDL", "forcedMetaDL", "forcedDL", "queuedDL", "checkingDL", "allocating"}
STALLED_QBIT = {"stalledDL", "metaDL", "forcedMetaDL"}
QBIT_ETA_UNKNOWN = 8640000
GRACE_MIN = 15
STALL_MIN = int(os.environ.get("ND_STALL_MIN", "20"))
APPS = {"radarr": "includeMovie=true", "sonarr": "includeSeries=true&includeEpisode=true"}
EPISODE = re.compile(r"(?<![a-z0-9])s(\d{1,2})e(\d{1,3})", re.I)
SEASON = re.compile(r"(?<![a-z0-9])s(\d{1,2})(?![a-z0-9])", re.I)
LAST = (float("inf"), float("inf"))


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
    top_hashes: list = field(default_factory=list)
    tv_order: list = field(default_factory=list)


@dataclass
class Item:
    id: str
    qbit: bool
    raw: dict
    paused: bool
    tv: bool
    name: str
    key: tuple
    act: str = ""


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


def episode_positions(queue):
    out = {}
    for rec in queue.get("records") or []:
        ep = rec.get("episode") or {}
        season = ep.get("seasonNumber", rec.get("seasonNumber"))
        if not rec.get("downloadId") or rec.get("movieId") or season is None:
            continue
        pos, dl = (season, ep.get("episodeNumber", 0)), rec["downloadId"].lower()
        out[dl] = min(out.get(dl, pos), pos)
    return out


def name_position(name):
    m = EPISODE.search(name or "")
    if m:
        return int(m[1]), int(m[2])
    m = SEASON.search(name or "")
    return (int(m[1]), 0) if m else None


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


def tv_order(items, titles, positions):
    group = lambda it: titles.get(it.id.lower(), ("download", it.id.lower()))
    first = {}
    for it in items:
        first[group(it)] = min(first.get(group(it), it.key), it.key)
    pos = lambda it: positions.get(it.id.lower()) or name_position(it.name) or LAST
    return sorted(items, key=lambda it: (first[group(it)], str(group(it)), pos(it), it.key))


def _hold(p, it):
    (p.pause_hashes if it.qbit else p.pause_nzos).append(it.id)
    it.act = "pause"
    p.pending += 1


def _day(p, it, eta):
    if it.paused:
        p.pending += 1
        it.act = "wait"
    else:
        e = eta(it)
        if e is None or e >= GRACE_MIN * 60:
            _hold(p, it)
        else:
            it.act = "grace"
    if it.act != "grace":
        p.waiting.append(it.id.lower())


def _resume(p, it):
    (p.hashes if it.qbit else p.nzos).append(it.id)
    it.act = "resume"


def plan(now, torrents, slots, urgent, titles=None, stalled=None, kbps=0, positions=None):
    low, p, titles, stalled, positions = low_tariff(now), Plan(), titles or {}, stalled or {}, positions or {}
    ts = now.timestamp()
    items = []
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
        items.append(Item(t["hash"], True, t, paused, t["category"] in NIGHT_TV, t.get("name") or "", (t.get("added_on") or 0, 0)))
    for s in slots:
        if s.get("cat") not in NIGHT_SAB:
            continue
        paused = s.get("status") == "Paused"
        if s["nzo_id"].lower() in urgent:
            if paused:
                p.nzos.append(s["nzo_id"])
            continue
        items.append(Item(s["nzo_id"], False, s, paused, s["cat"] in NIGHT_TV, s.get("filename") or "", (s.get("time_added") or 0, s.get("index", 0))))
    eta = lambda it: qbit_eta(it.raw) if it.qbit else sab_eta(it.raw, kbps)
    stalled_long = lambda it: it.qbit and it.id in p.stalled and ts - p.stalled[it.id] >= STALL_MIN * 60
    movies = [it for it in items if not it.tv]
    p.tv_order = tv_order([it for it in items if it.tv], titles, positions)
    if not low:
        for it in movies + p.tv_order:
            _day(p, it, eta)
        return p
    for it in movies:
        if it.paused:
            _resume(p, it)
        else:
            it.act = "running"
    taken = False
    for it in p.tv_order:
        if stalled_long(it):
            it.act = "stalled"
        elif taken:
            if it.paused:
                p.pending += 1
                it.act = "wait"
            else:
                _hold(p, it)
        else:
            taken = True
            if it.paused:
                _resume(p, it)
            else:
                it.act = "running"
            if it.qbit and (it.paused or it.raw.get("state") == "queuedDL"):
                p.top_hashes.append(it.id)
    return p


def write_public(path, p, now):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"waiting": p.waiting, "low_tariff": low_tariff(now), "updated": now.astimezone().isoformat(timespec="seconds")}) + "\n")
    tmp.chmod(0o644)
    tmp.replace(path)


def run(now, qbit, sab, apps, state_path, dry_run=False, public_path=None):
    rc, urgent, titles, positions = 0, set(), {}, {}
    for app, api in apps.items():
        try:
            queue = api("/queue?page=1&pageSize=1000")
            urgent |= urgent_ids(api("/tag"), queue)
            titles |= title_ids(app, queue)
            positions |= episode_positions(queue)
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
    p = plan(now, torrents, slots, urgent, titles, stalled, kbps, positions)
    names = {t["hash"]: t.get("name") for t in torrents} | {s["nzo_id"]: s.get("filename") for s in slots}
    verb = lambda v: f"would {v}" if dry_run else v
    for i in p.hashes + p.nzos:
        print(f"{verb('resume')}: {names.get(i)}")
    for i in p.pause_hashes + p.pause_nzos:
        print(f"{verb('pause')}: {names.get(i)}")
    print(f"low tariff: {low_tariff(now)}, waiting for the night: {p.pending}")
    if dry_run:
        for n, it in enumerate(p.tv_order, 1):
            pos = positions.get(it.id.lower()) or name_position(it.name)
            series = titles.get(it.id.lower(), ("", "?"))[1]
            print(f"tv {n:>3} series {series} {'S%02dE%02d' % pos if pos else '?'} {it.act or '-':<8} {'qbit' if it.qbit else 'sab '} {it.name[:70]}")
        return rc
    try:
        if p.hashes:
            qbit("/torrents/start", {"hashes": "|".join(p.hashes)})
        if p.pause_hashes:
            qbit("/torrents/stop", {"hashes": "|".join(p.pause_hashes)})
        if p.top_hashes:
            try:
                qbit("/torrents/topPrio", {"hashes": "|".join(p.top_hashes)})
            except ApiError as e:
                print(f"TV torrent not moved to the top of the qBittorrent queue: {e}", file=sys.stderr)
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


class Forbidden(ApiError):
    pass


class LoginError(ApiError):
    pass


def _fetch(req, timeout=30, opener=None):
    try:
        with (opener.open if opener else urllib.request.urlopen)(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        e.close()
        raise (Forbidden if e.code == 403 else ApiError)(f"{req.full_url.split('?')[0]}: {e}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(f"{req.full_url.split('?')[0]}: {e}") from None


def _open(req, timeout=30, opener=None):
    body = _fetch(req, timeout, opener)
    try:
        return json.loads(body) if body.strip() else None
    except ValueError as e:
        raise ApiError(f"{req.full_url.split('?')[0]}: {e}") from None


LEGACY = {"/torrents/start": "/torrents/resume", "/torrents/stop": "/torrents/pause"}


def now_from(env):
    return datetime.datetime.fromisoformat(env["ND_NOW"]) if env.get("ND_NOW") else datetime.datetime.now()


def qbit_client(base, user="admin", password_file=None):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def login():
        pw = pathlib.Path(password_file) if password_file else None
        if not pw or not pw.is_file():
            raise LoginError(f"{base}: 403 and no qBittorrent password file")
        form = urllib.parse.urlencode({"username": user, "password": pw.read_text().strip()}).encode()
        try:
            refused = _fetch(urllib.request.Request(base + "/auth/login", form), opener=opener).strip() == b"Fails."
        except ApiError:
            refused = True
        if refused:
            raise LoginError(f"{base}/auth/login: login refused")

    def send(path, body):
        try:
            return _open(urllib.request.Request(base + path, body), opener=opener)
        except Forbidden:
            login()
            return _open(urllib.request.Request(base + path, body), opener=opener)

    def call(path, data=None):
        if data is None:
            return send(path, None)
        body = urllib.parse.urlencode(data).encode()
        try:
            return send(path, body)
        except LoginError:
            raise
        except ApiError:
            if path not in LEGACY:
                raise
            return send(LEGACY[path], body)
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
    qbit = qbit_client(os.environ.get("ND_QBIT_URL", "http://127.0.0.1:8080") + "/api/v2", os.environ.get("ND_QBIT_USER", "admin"),
                       os.environ.get("ND_QBIT_PASS", HERE.parent / "secrets" / "qbit-webui.password"))
    sab = sab_client(os.environ.get("ND_SAB_URL", "http://127.0.0.1:8081"), os.environ.get("ND_SAB_KEY", HERE.parent / "secrets" / "hook" / "sabnzbd.apikey"))
    apps = {app: arr_client(app, os.environ.get(f"ND_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in APPS}
    return run(now_from(os.environ), qbit, sab, apps, os.environ.get("ND_STATE", STATE), dry_run=dry, public_path=os.environ.get("ND_PUBLIC", PUBLIC))


if __name__ == "__main__":
    sys.exit(main())
