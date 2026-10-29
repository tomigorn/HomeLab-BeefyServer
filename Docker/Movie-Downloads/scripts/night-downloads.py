#!/usr/bin/env python3
"""Night downloads (README "Can wait: downloads at night"): resumes the downloads the untagged "night" clients added paused.
Low tariff (22:00-06:00 and all Sunday): every night movie resumes (the clients' own limits apply); TV runs one download at a time across both clients: one series at a time, FIFO by its first grab, within it season then episode; a torrent stalled for STALL_MIN lets the next one start too.
Outside the low tariff: night items still downloading are paused unless their ETA is under GRACE_MIN; they resume at the next low tariff where they stopped. Any time: the night items of titles tagged download-now resume and are never paused.
Download now whose search found nothing (searched, nothing queued, no search running, no file): the download-now tag is removed again, so a later grab downloads at night.
Low pool space (under MIN_FREE_GIB free): every downloading torrent is stopped and restarted after recovery (qBittorrent has no own low-space pause).
Writes ~/.local/state/movie-downloads/night-downloads.json ({"pending": n, "stalled": {hash: since}, "disk_held": [hash]}), which fastpi reads to decide its 22:05 wake.
Writes the download ids waiting for the night (outside the low tariff) to /srv/appdata/indexer-bar/night-waiting.json for Seerr's night label (../Seerr/README.md "Night label"; env ND_PUBLIC, empty = off), and beside it request-state.json: the open Seerr requests that are "Can wait" and those not released yet (Seerr ids only).
request-state.json also carries the live state of each open request (running search, just requested, waiting for approval, queue state, imported), so every requested title has a badge, and "sizes": every request's size on disk by Seerr request id (2-min run only, the 15 s run keeps them; ../Seerr/README.md "Request sizes").
Run by systemd/night-downloads.timer (every 2 min while awake, and at boot). --request-state (systemd/request-state.timer, every 15 s): rewrites only request-state.json, and only while a search runs or a request is from the last minutes.
Usage: scripts/night-downloads.py [--dry-run | --request-state]; ND_NOW=2026-10-27T22:10 simulates a clock (with --dry-run)."""
import fcntl
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
# qBittorrent has no low-space pause: below this pool free space (what the arrs see through mergerfs: SSD + HDD free) every downloading torrent is stopped, and restarted once free is back above it + the margin. Same value as Radarr/Sonarr "Minimum Free Space" and SABnzbd's pause (README "Storage limits").
MIN_FREE_GIB = int(os.environ.get("ND_MIN_FREE_GIB", "5758"))
RESUME_MARGIN_GIB = 100
QBIT_ETA_UNKNOWN = 8640000
GRACE_MIN = 15
STALL_MIN = int(os.environ.get("ND_STALL_MIN", "20"))
APPS = {"radarr": "includeMovie=true", "sonarr": "includeSeries=true&includeEpisode=true"}
EPISODE = re.compile(r"(?<![a-z0-9])s(\d{1,2})e(\d{1,3})", re.I)
SEASON = re.compile(r"(?<![a-z0-9])s(\d{1,2})(?![a-z0-9])", re.I)
LAST = (float("inf"), float("inf"))


class ApiError(Exception):
    pass


class NotFound(ApiError):
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
    write_json(path, {"waiting": p.waiting, "low_tariff": low_tariff(now), "updated": now.astimezone().isoformat(timespec="seconds")})


OPEN_REQUEST = {1, 2}
PENDING, APPROVED, FAILED = 1, 2, 4
FRESH_S = 5 * 60
PROBLEM_STATES = {"importBlocked", "failedPending", "failed"}
IMPORT_STATES = {"importPending", "importing"}
QUEUE_RANK = {"paused": 0, "downloading": 1, "importing": 2, "problem": 3}
AVAILABLE = 5
PARTLY = 4
ALL = frozenset({"*"})
SEARCHES = {"MoviesSearch": "movieIds", "SeriesSearch": "seriesId", "MissingMoviesSearch": None, "CutoffUnmetMoviesSearch": None,
            "EpisodeSearch": None, "SeasonSearch": "seriesId", "MissingEpisodeSearch": None, "CutoffUnmetEpisodeSearch": None}


def running_searches(commands):
    out = set()
    for c in commands if isinstance(commands, list) else []:
        if c.get("name") not in SEARCHES or c.get("status") not in ("queued", "started"):
            continue
        field_ = SEARCHES[c["name"]]
        ids = (c.get("body") or {}).get(field_) if field_ else None
        if ids is None:
            return ALL
        out |= set(ids if isinstance(ids, list) else [ids])
    return out


def search_commands(commands):
    """Each title's running search: {"status": "started", "started": time} or {"status": "queued", "ahead": searches queued before it}; a search over all missing titles under ALL."""
    run = sorted((c for c in commands if isinstance(c, dict) and c.get("name") in SEARCHES and c.get("status") in ("queued", "started")) if isinstance(commands, list) else [],
                 key=lambda c: str(c.get("queued") or ""))
    out = {}
    for i, c in enumerate(run):
        info = {"status": "started", "started": c.get("started")} if c["status"] == "started" else {"status": "queued", "ahead": i}
        field_ = SEARCHES[c["name"]]
        ids = (c.get("body") or {}).get(field_) if field_ else None
        for t in [ALL] if ids is None else ids if isinstance(ids, list) else [ids]:
            old = out.get(t)
            if old is None or (old["status"] == "queued" and (info["status"] == "started" or info["ahead"] < old["ahead"])):
                out[t] = info
    return out


def brief(rec):
    msgs = [m for sm in rec.get("statusMessages") or [] for m in sm.get("messages") or []] + [rec.get("errorMessage") or ""]
    text = next((m for m in msgs if m), "")
    text = re.sub(r"https?://\S+|(?:/[^\s/]+)+/?", "…", str(text)).strip()
    return text if len(text) <= 100 else text[:99] + "…"


def queue_states(app, queue):
    """(app, title id) -> its downloads' state: problem (import blocked, failed) > importing > downloading > paused."""
    out = {}
    for rec in queue.get("records") or [] if isinstance(queue, dict) else []:
        tid = rec.get("movieId") or rec.get("seriesId")
        if not tid:
            continue
        tds = rec.get("trackedDownloadState")
        if tds in PROBLEM_STATES or rec.get("trackedDownloadStatus") == "error" or rec.get("status") == "failed":
            st = {"state": "problem", "message": brief(rec)}
        elif tds in IMPORT_STATES:
            st = {"state": "importing"}
        else:
            st = {"state": "paused" if str(rec.get("status")).lower() == "paused" else "downloading"}
        old = out.get((app, tid))
        if old is None or QUEUE_RANK[st["state"]] > QUEUE_RANK[old["state"]]:
            out[(app, tid)] = st
    return out


def _ts(value):
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() if value else None
    except ValueError:
        return None


def _searching(searching, app, ext):
    s = searching.get(app)
    return s is None or s is ALL or ext in s


def _last_search(title, eps, seasons, now):
    if eps is None:
        return title.get("lastSearchTime")
    times = [e.get("lastSearchTime") for e in eps if (seasons is None or e.get("seasonNumber") in seasons) and not e.get("hasFile") and _aired(e, now)]
    return max(filter(None, times), default=None)


def _date(value, now):
    if not value:
        return None
    d = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d.astimezone().date().isoformat() if d.timestamp() > now.timestamp() else None


def _aired(e, now):
    air = e.get("airDateUtc")
    return bool(air) and datetime.datetime.fromisoformat(air.replace("Z", "+00:00")).timestamp() <= now.timestamp()


def _movie_release(m, now):
    if m.get("isAvailable", True):
        return False, None
    if m.get("minimumAvailability") == "inCinemas":
        return True, _date(m.get("inCinemas"), now)
    return True, _date(m.get("releaseDate") or min(filter(None, (m.get("digitalRelease"), m.get("physicalRelease"))), default=None), now)


def _series_release(s, eps, seasons, now):
    wanted = [e for e in eps if e.get("seasonNumber") in seasons]
    missing = [e for e in wanted if not e.get("hasFile")]
    if not wanted:
        return s.get("status") == "upcoming", False, _date(s.get("nextAiring"), now)
    if not missing:
        return False, True, None
    if any(_aired(e, now) for e in missing):
        return False, False, None
    return True, False, min(filter(None, (_date(e.get("airDateUtc"), now) for e in missing)), default=None)


def request_state(now, requests, dn_tags, movies, series, episodes, queued=frozenset(), searching=None, commands=None, queue=None):
    can_wait, unreleased, no_release, searching, commands, queue = set(), {}, {}, searching or {}, commands or {}, queue or {}
    live, fresh, pending, imported, queue_out, so_far = {}, {}, set(), set(), {}, set()
    queued = set(queued) | set(queue)
    for q in requests:
        media, four_k = q.get("media") or {}, bool(q.get("is4k"))
        if media.get("status4k" if four_k else "status") == AVAILABLE or not media.get("tmdbId"):
            continue
        tv = q.get("type") == "tv"
        key, dn = f"{'tv' if tv else 'movie'}/{media['tmdbId']}", dn_tags.get("sonarr" if tv else "radarr")
        if q.get("status") == FAILED:
            queue_out[key] = {"state": "problem", "message": "", "code": "seerr"}
            continue
        if q.get("status") not in OPEN_REQUEST:
            continue
        ext = media.get("externalServiceId4k" if four_k else "externalServiceId")
        title = (series if tv else movies).get(ext) if ext else None
        if title is None:
            now_tag = dn is not None and dn in (q.get("tags") or [])
            later, done, when = False, False, None
        else:
            now_tag = dn is not None and dn in (title.get("tags") or [])
            if tv:
                later, done, when = _series_release(title, episodes.get(ext) or [], {x["seasonNumber"] for x in q.get("seasons") or []}, now)
            else:
                done = bool(title.get("hasFile"))
                later, when = _movie_release(title, now)
        if tv and title is not None:
            wanted = [e for e in episodes.get(ext) or [] if e.get("seasonNumber") in {x["seasonNumber"] for x in q.get("seasons") or []}]
            if later and any(e.get("hasFile") for e in wanted):
                so_far.add(key)
            if done and media.get("status4k" if four_k else "status") == PARTLY:
                so_far.add(key)
                nxt = _date(title.get("nextAiring"), now)
                if nxt:
                    unreleased[key] = nxt
                if not now_tag and title.get("status") != "ended":
                    can_wait.add(key)
                continue
        if done:
            imported.add(key)
            continue
        if not now_tag:
            can_wait.add(key)
        app = "sonarr" if tv else "radarr"
        if q.get("status") == PENDING:
            pending.add(key)
        qs = queue.get((app, ext))
        has_dl = bool(qs) or (app, ext) in queued
        if qs and QUEUE_RANK[qs["state"]] > QUEUE_RANK.get((queue_out.get(key) or {}).get("state"), -1):
            queue_out[key] = qs
        cmd = commands.get(app) or {}
        info = cmd.get(ext) or (cmd.get(ALL) if title is not None and not later and not has_dl else None)
        if info and not later:
            live[key] = info
        elif q.get("status") == APPROVED and not later and not has_dl:
            seasons = {x["seasonNumber"] for x in q.get("seasons") or []} if tv else None
            last = _last_search(title, episodes.get(ext) or [] if tv else None, seasons, now) if title is not None else None
            created = q.get("createdAt")
            if not last or (_ts(created) and _ts(last) < _ts(created)):
                fresh[key] = created
            elif not _searching(searching, app, ext):
                no_release[key] = max(no_release.get(key) or last, last)
        if later:
            unreleased[key] = when if unreleased.get(key) is None else min(unreleased[key], when or unreleased[key])
    for key in set(fresh) & (set(no_release) | set(live)):
        del fresh[key]
    imported -= set(live) | set(fresh) | pending | set(queue_out) | set(unreleased) | set(no_release)
    return {"canWait": sorted(can_wait - imported), "unreleased": unreleased, "noRelease": no_release, "searching": live, "fresh": fresh,
            "pending": sorted(pending), "queue": queue_out, "imported": sorted(imported), "soFar": sorted(so_far)}


def revert_unfound(apps, queued, searching, dry_run=False, now=None):
    """Download now whose search found nothing: the download-now tag goes again, so a later grab (tonight's 04:17 search) downloads at night."""
    now, out = now or datetime.datetime.now(), []
    for app, api in apps.items():
        tv = app == "sonarr"
        dn = next((t["id"] for t in api("/tag") or [] if t.get("label") == TAG), None)
        if dn is None or app not in searching or searching[app] is ALL:
            continue
        detail = api(f"/tag/detail/{dn}") or {}
        drop = []
        for ext in detail.get("seriesIds" if tv else "movieIds") or []:
            if (app, ext) in queued or ext in searching[app]:
                continue
            title = api(f"/{'series' if tv else 'movie'}/{ext}")
            if dn not in (title.get("tags") or []):
                continue
            if tv:
                st = title.get("statistics") or {}
                if (st.get("episodeFileCount") or 0) >= (st.get("episodeCount") or 0) > 0:
                    continue
                eps = api(f"/episode?seriesId={ext}") or []
                last = max(filter(None, (e.get("lastSearchTime") for e in eps if not e.get("hasFile"))), default=None)
            else:
                if title.get("hasFile"):
                    continue
                last = title.get("lastSearchTime")
            if last:
                drop.append(ext)
        if not drop:
            continue
        kind = "series" if tv else "movie"
        if not dry_run:
            api(f"/{kind}/editor", "PUT", {"seriesIds" if tv else "movieIds": drop, "tags": [dn], "applyTags": "remove"})
        out += [f"{app}: {kind} {ext} download-now {'would be removed' if dry_run else 'removed'}: searched, nothing found" for ext in drop]
    return out


def _queued(records, match):
    sizes = {}
    for rec in records:
        if rec.get("downloadId") and match(rec):
            sizes[rec["downloadId"].lower()] = rec.get("size") or 0
    return sum(sizes.values())


def request_sizes(requests, movies, series, queues):
    """Seerr request id -> {"size": bytes on disk} (a series: its requested seasons, plus "files"/"of" while episodes are missing), else {"queued": bytes of its downloads in the queue}; unknown, 4K and nothing at all are left out."""
    out = {}
    for q in requests:
        media, tv = q.get("media") or {}, q.get("type") == "tv"
        ext = media.get("externalServiceId")
        title = (series if tv else movies).get(ext) if ext and not q.get("is4k") and q.get("id") is not None else None
        if title is None:
            continue
        queue = queues.get("sonarr" if tv else "radarr")
        records = (queue.get("records") or []) if isinstance(queue, dict) else []
        if tv:
            wanted = {x.get("seasonNumber") for x in q.get("seasons") or []}
            stats = [x.get("statistics") or {} for x in title.get("seasons") or [] if not wanted or x.get("seasonNumber") in wanted]
            size = sum(x.get("sizeOnDisk") or 0 for x in stats)
            files, of = sum(x.get("episodeFileCount") or 0 for x in stats), sum(x.get("episodeCount") or 0 for x in stats)
            entry = {"size": size} | ({"files": files, "of": of} if files < of else {})
            queued = _queued(records, lambda r: r.get("seriesId") == ext and (not wanted or (r.get("episode") or {}).get("seasonNumber", r.get("seasonNumber")) in wanted))
        else:
            size = (title.get("movieFile") or {}).get("size") or title.get("sizeOnDisk") or 0
            entry = {"size": size}
            queued = _queued(records, lambda r: r.get("movieId") == ext)
        if size:
            out[str(q["id"])] = entry
        elif queued:
            out[str(q["id"])] = {"queued": queued}
    return out


def _all(api, path):
    items = api(path)
    if not isinstance(items, list):
        raise ApiError(f"{path}: not a list")
    return {x["id"]: x for x in items if isinstance(x, dict) and "id" in x}


def collect_request_state(now, seerr, apps, queued=frozenset(), searching=None, commands=None, queue=None, queues=None):
    """queues (the full run's raw Radarr/Sonarr queues): also every request's size ("sizes"), left out when the arrs cannot be read."""
    every = seerr("/request?take=1000&skip=0&filter=all&sort=added").get("results") or []
    requests = [q for q in every if q.get("status") in OPEN_REQUEST | {FAILED}]
    dn, titles, episodes = {}, {"radarr": {}, "sonarr": {}}, {}
    for app, api in apps.items():
        dn[app] = next((t["id"] for t in api("/tag") or [] if t.get("label") == TAG), None)
    for q in requests:
        media, four_k = q.get("media") or {}, bool(q.get("is4k"))
        ext = media.get("externalServiceId4k" if four_k else "externalServiceId")
        if not ext or media.get("status4k" if four_k else "status") == AVAILABLE:
            continue
        app = "sonarr" if q.get("type") == "tv" else "radarr"
        if ext in titles[app]:
            continue
        try:
            titles[app][ext] = apps[app](f"/{'series' if app == 'sonarr' else 'movie'}/{ext}")
            if app == "sonarr":
                episodes[ext] = apps[app](f"/episode?seriesId={ext}")
        except NotFound:
            titles[app][ext] = None
    clean = lambda d: {k: v for k, v in d.items() if v is not None}
    out = request_state(now, requests, dn, clean(titles["radarr"]), clean(titles["sonarr"]), episodes, queued, searching, commands, queue)
    if queues is not None:
        try:
            out["sizes"] = request_sizes(every, _all(apps["radarr"], "/movie"), _all(apps["sonarr"], "/series"), queues)
        except (ApiError, KeyError, TypeError) as e:
            print(f"request sizes not read, the last ones kept: {e}", file=sys.stderr)
    return out


def write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data) + "\n")
    tmp.chmod(0o644)
    tmp.replace(path)


def write_request_state(now, seerr, apps, public_path, lock_path, queued, searching, commands, queue, wait=True, queues=None):
    """request-state.json under a lock shared with --request-state; False = the other run holds it. Without new sizes the file's last ones stay."""
    lock_path = pathlib.Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        data = collect_request_state(now, seerr, apps, queued, searching, commands, queue, queues)
        path = pathlib.Path(public_path).parent / "request-state.json"
        if "sizes" not in data:
            try:
                old = json.loads(path.read_text()).get("sizes")
            except (OSError, ValueError, AttributeError):
                old = None
            if isinstance(old, dict):
                data["sizes"] = old
        write_json(path, data | {"updated": now.astimezone().isoformat(timespec="seconds")})
    return True


def _recent(created, now):
    t = _ts(created)
    return t is not None and now.timestamp() - t < FRESH_S


def run_fast(now, apps, seerr, public_path, lock_path):
    """--request-state: request-state.json again while a search runs, a request is from the last minutes, or the last file still shows one; otherwise nothing is written."""
    try:
        cmds = {app: api("/command") for app, api in apps.items()}
        commands = {app: search_commands(c) for app, c in cmds.items()}
        active = any(commands.values())
        if not active:
            try:
                prev = json.loads((pathlib.Path(public_path).parent / "request-state.json").read_text())
            except (OSError, ValueError):
                prev = {}
            prev = prev if isinstance(prev, dict) else {}
            active = bool(prev.get("searching")) or any(_recent(v, now) for v in (prev.get("fresh") or {}).values())
        if not active:
            recent = seerr("/request?take=10&skip=0&filter=all&sort=added").get("results") or []
            active = any(q.get("status") in OPEN_REQUEST and _recent(q.get("createdAt"), now) for q in recent)
        if not active:
            return 0
        queue = {}
        for app, api in apps.items():
            queue |= queue_states(app, api("/queue?page=1&pageSize=1000"))
        searching = {app: running_searches(c) for app, c in cmds.items()}
        write_request_state(now, seerr, apps, public_path, lock_path, set(), searching, commands, queue, wait=False)
    except (ApiError, OSError, KeyError, TypeError, ValueError, AttributeError) as e:
        print(f"Seerr request state not written: {e}", file=sys.stderr)
        return 1
    return 0


def disk_hold(p, torrents, free, held):
    """Low pool space: stop every downloading torrent and start none; back above min + margin: restart the held ones (night ones stay with the night rules)."""
    if free is None:
        return held
    low = free < MIN_FREE_GIB * 1024 ** 3
    recovered = free >= (MIN_FREE_GIB + RESUME_MARGIN_GIB) * 1024 ** 3
    by_hash = {t["hash"]: t for t in torrents}
    held = [h for h in held if h in by_hash]
    if low or (held and not recovered):
        running = [t["hash"] for t in torrents if t.get("state") in RUNNING_QBIT]
        p.hashes, p.top_hashes = [], []
        p.pause_hashes = list(dict.fromkeys(p.pause_hashes + running))
        return list(dict.fromkeys(held + running))
    restart = [h for h in held if by_hash[h].get("category") not in NIGHT_QBIT and by_hash[h].get("state") in PAUSED_QBIT and h not in p.hashes]
    p.hashes = p.hashes + restart
    return []


def run(now, qbit, sab, apps, state_path, dry_run=False, public_path=None, seerr=None, free=None):
    rc, urgent, titles, positions, searching, commands, qstates, queues = 0, set(), {}, {}, {}, {}, {}, {}
    for app, api in apps.items():
        try:
            queue = queues[app] = api("/queue?page=1&pageSize=1000")
            urgent |= urgent_ids(api("/tag"), queue)
            titles |= title_ids(app, queue)
            positions |= episode_positions(queue)
            qstates |= queue_states(app, queue)
            cmds = api("/command")
            searching[app] = running_searches(cmds)
            commands[app] = search_commands(cmds)
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
        old = json.loads(state_path.read_text())
        stalled, held = old.get("stalled") or {}, old.get("disk_held") or []
    except (OSError, ValueError, AttributeError):
        stalled, held = {}, []
    p = plan(now, torrents, slots, urgent, titles, stalled, kbps, positions)
    held = disk_hold(p, torrents, free, held)
    if held:
        print(f"low disk space: {len(held)} torrent(s) held until {MIN_FREE_GIB + RESUME_MARGIN_GIB} GiB are free")
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
    tmp.write_text(json.dumps({"pending": p.pending, "low_tariff": low_tariff(now), "updated": now.isoformat(timespec="seconds"), "stalled": p.stalled, "disk_held": held}) + "\n")
    tmp.replace(state_path)
    queued = set(titles.values())
    try:
        for line in revert_unfound(apps, queued, searching, now=now):
            print(line)
    except (ApiError, AttributeError, TypeError) as e:
        print(f"download-now check failed: {e}", file=sys.stderr)
        rc = 1
    if public_path:
        try:
            write_public(public_path, p, now)
        except OSError as e:
            print(f"Seerr night label not written: {e}", file=sys.stderr)
            rc = 1
        if seerr:
            try:
                write_request_state(now, seerr, apps, public_path, state_path.parent / "request-state.lock", queued, searching, commands, qstates, queues=queues)
            except (ApiError, OSError, KeyError, TypeError, ValueError) as e:
                print(f"Seerr request state not written: {e}", file=sys.stderr)
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
        raise {403: Forbidden, 404: NotFound}.get(e.code, ApiError)(f"{req.full_url.split('?')[0]}: {e}") from None
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

    def call(path, method="GET", body=None):
        sep = "&" if "?" in path else "?"
        extra = sep + APPS[app] if path.startswith("/queue") else ""
        data = json.dumps(body).encode() if body is not None else None
        return _open(urllib.request.Request(base + path + extra, data=data, method=method, headers={"X-Api-Key": get("ApiKey"), "Content-Type": "application/json"}))
    return call


def seerr_client(base, keyfile):
    key = pathlib.Path(keyfile).read_text().strip()

    def call(path):
        return _open(urllib.request.Request(base + "/api/v1" + path, headers={"X-Api-Key": key}))
    return call


def main():
    dry, fast = "--dry-run" in sys.argv[1:], "--request-state" in sys.argv[1:]
    if os.environ.get("ND_NOW") and not dry:
        sys.exit("ND_NOW only with --dry-run")
    qbit = qbit_client(os.environ.get("ND_QBIT_URL", "http://127.0.0.1:8080") + "/api/v2", os.environ.get("ND_QBIT_USER", "admin"),
                       os.environ.get("ND_QBIT_PASS", HERE.parent / "secrets" / "qbit-webui.password"))
    sab = sab_client(os.environ.get("ND_SAB_URL", "http://127.0.0.1:8081"), os.environ.get("ND_SAB_KEY", HERE.parent / "secrets" / "hook" / "sabnzbd.apikey"))
    apps = {app: arr_client(app, os.environ.get(f"ND_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in APPS}
    seerr = seerr_client(os.environ.get("ND_SEERR_URL", "http://127.0.0.1:5055"), os.environ.get("ND_SEERR_KEY", HERE.parent.parent / "Seerr" / "secrets" / "api-key"))
    if fast:
        state = pathlib.Path(os.environ.get("ND_STATE", STATE))
        return run_fast(datetime.datetime.now().astimezone(), apps, seerr, os.environ.get("ND_PUBLIC", PUBLIC) or PUBLIC, state.parent / "request-state.lock")
    try:
        st = os.statvfs(os.environ.get("ND_POOL", "/srv/video"))
        free = st.f_bavail * st.f_frsize
    except OSError:
        free = None
    return run(now_from(os.environ), qbit, sab, apps, os.environ.get("ND_STATE", STATE), dry_run=dry, public_path=os.environ.get("ND_PUBLIC", PUBLIC), seerr=seerr, free=free)


if __name__ == "__main__":
    sys.exit(main())
