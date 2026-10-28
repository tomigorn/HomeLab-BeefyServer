#!/usr/bin/env python3
"""Night downloads (README "Can wait: downloads at night"): resumes the downloads the untagged "night" clients added paused.
Low tariff (22:00-06:00 and all Sunday): every night movie resumes (the clients' own limits apply); TV runs one download at a time across both clients: one series at a time, FIFO by its first grab, within it season then episode; a torrent stalled for STALL_MIN lets the next one start too.
Outside the low tariff: night items still downloading are paused unless their ETA is under GRACE_MIN; they resume at the next low tariff where they stopped. Any time: the night items of titles tagged download-now resume and are never paused.
Download now whose search found nothing (searched, nothing queued, no search running, no file): the download-now tag is removed again, so a later grab downloads at night.
Writes ~/.local/state/movie-downloads/night-downloads.json ({"pending": n, "stalled": {hash: since}}), which fastpi reads to decide its 22:05 wake.
Writes the download ids waiting for the night (outside the low tariff) to /srv/appdata/indexer-bar/night-waiting.json for Seerr's night label (../Seerr/README.md "Night label"; env ND_PUBLIC, empty = off), and beside it request-state.json: the open Seerr requests that are "Can wait" and those not released yet (Seerr ids only).
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
AVAILABLE = 5
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


def request_state(now, requests, dn_tags, movies, series, episodes, queued=frozenset(), searching=None):
    can_wait, unreleased, no_release, searching = set(), {}, {}, searching or {}
    for q in requests:
        media, four_k = q.get("media") or {}, bool(q.get("is4k"))
        if q.get("status") not in OPEN_REQUEST or media.get("status4k" if four_k else "status") == AVAILABLE or not media.get("tmdbId"):
            continue
        tv = q.get("type") == "tv"
        key, dn = f"{'tv' if tv else 'movie'}/{media['tmdbId']}", dn_tags.get("sonarr" if tv else "radarr")
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
        if done:
            continue
        if not now_tag:
            can_wait.add(key)
        app = "sonarr" if tv else "radarr"
        if title is not None and not later and (app, ext) not in queued and not _searching(searching, app, ext):
            seasons = {x["seasonNumber"] for x in q.get("seasons") or []} if tv else None
            last = _last_search(title, episodes.get(ext) or [] if tv else None, seasons, now)
            if last:
                no_release[key] = max(no_release.get(key) or last, last)
        if later:
            unreleased[key] = when if unreleased.get(key) is None else min(unreleased[key], when or unreleased[key])
    return {"canWait": sorted(can_wait), "unreleased": unreleased, "noRelease": no_release}


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


def collect_request_state(now, seerr, apps, queued=frozenset(), searching=None):
    requests = [q for q in seerr("/request?take=1000&skip=0&filter=all&sort=added").get("results") or [] if q.get("status") in OPEN_REQUEST]
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
    return request_state(now, requests, dn, clean(titles["radarr"]), clean(titles["sonarr"]), episodes, queued, searching)


def write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data) + "\n")
    tmp.chmod(0o644)
    tmp.replace(path)


def run(now, qbit, sab, apps, state_path, dry_run=False, public_path=None, seerr=None):
    rc, urgent, titles, positions, searching = 0, set(), {}, {}, {}
    for app, api in apps.items():
        try:
            queue = api("/queue?page=1&pageSize=1000")
            urgent |= urgent_ids(api("/tag"), queue)
            titles |= title_ids(app, queue)
            positions |= episode_positions(queue)
            searching[app] = running_searches(api("/command"))
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
                write_json(pathlib.Path(public_path).parent / "request-state.json", collect_request_state(now, seerr, apps, queued, searching) | {"updated": now.astimezone().isoformat(timespec="seconds")})
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
    dry = "--dry-run" in sys.argv[1:]
    if os.environ.get("ND_NOW") and not dry:
        sys.exit("ND_NOW only with --dry-run")
    qbit = qbit_client(os.environ.get("ND_QBIT_URL", "http://127.0.0.1:8080") + "/api/v2", os.environ.get("ND_QBIT_USER", "admin"),
                       os.environ.get("ND_QBIT_PASS", HERE.parent / "secrets" / "qbit-webui.password"))
    sab = sab_client(os.environ.get("ND_SAB_URL", "http://127.0.0.1:8081"), os.environ.get("ND_SAB_KEY", HERE.parent / "secrets" / "hook" / "sabnzbd.apikey"))
    apps = {app: arr_client(app, os.environ.get(f"ND_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in APPS}
    seerr = seerr_client(os.environ.get("ND_SEERR_URL", "http://127.0.0.1:5055"), os.environ.get("ND_SEERR_KEY", HERE.parent.parent / "Seerr" / "secrets" / "api-key"))
    return run(now_from(os.environ), qbit, sab, apps, os.environ.get("ND_STATE", STATE), dry_run=dry, public_path=os.environ.get("ND_PUBLIC", PUBLIC), seerr=seerr)


if __name__ == "__main__":
    sys.exit(main())
