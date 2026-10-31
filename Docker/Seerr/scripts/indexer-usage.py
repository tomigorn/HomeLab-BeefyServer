#!/usr/bin/env python3
"""Indexer bar (README "Indexer bar"): counts Prowlarr searches (queries + RSS) and grabs per indexer inside its limit window from indexer-bar/limits.toml, adds the subscription deadlines and the SSD/HDD fill level (statvfs of each branch), and writes usage.json (names and counts only) for the bar the proxy shows above Seerr.
Subtitle providers (limits.toml [subtitles.*]) from Bazarr: downloads per UTC day from its history (exact), API searches estimated from its finished jobs (≈, state in IB_SUB_STATE), paused providers from /api/providers.
Storage also goes to IB_STORAGE_PROM (default ~/.local/state/indexer-bar/storage.prom) for fastpi's alerts.
Prowlarr unreachable: usage.json says ok=false (the bar shows "usage unavailable"), subscriptions stay. Run by systemd/indexer-usage.timer.
Usage: scripts/indexer-usage.py"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Zurich")
UTC = dt.timezone.utc
DAY = dt.timedelta(hours=24)
SEARCH_EVENTS = {"indexerQuery", "indexerRss"}
GRAB_EVENTS = {"releaseGrabbed"}
WINDOWS = {"rolling", "day"}
AMBER, RED = 0.70, 0.95
SUB_YELLOW_DAYS, SUB_ORANGE_DAYS, SUB_RED_DAYS = 30, 7, 0
RANK = {"ok": 0, "yellow": 1, "warn": 2, "orange": 3, "full": 4}
DISKS = [{"name": "SSD", "id": "ssd", "paths": ["/srv/.disks/ssd-hot", "/srv/.disks/ssd-hot2"], "yellow": 0.70, "red": 0.80},
         {"name": "HDD", "id": "hdd", "paths": ["/srv/.disks/hdd-cold"], "yellow": 0.80, "red": 0.90}]
# Bar texts (indexer-bar/schema.md): the shared bar.js has no Seerr wording.
DISK_TEXT = {
    "ssd": {"partsLabel": "SSDs", "sub": {"en": "fast tier, new downloads", "de": "schnell, neue Downloads"},
            "hint": {"en": "Above {yellow} % the night mover moves titles nobody played for 30 days (oldest first) to the HDD, down to 60 %; from {red} % new downloads go straight to the HDD.",
                     "de": "Ab {yellow} % verschiebt der Nacht-Mover Titel, die 30 Tage niemand geschaut hat (älteste zuerst), auf die HDD, bis 60 %; ab {red} % landen neue Downloads direkt auf der HDD."}},
    "hdd": {"sub": {"en": "archive", "de": "Archiv"},
            "hint": {"en": "Archive for older titles. From {red} % new downloads are no longer imported.", "de": "Archiv für ältere Titel. Ab {red} % werden neue Downloads nicht mehr importiert."}},
}
NOTE = {"en": "When a limited indexer is used up, requests may wait until it resets; the other indexers keep searching. Searches made by hand on the indexer websites are not counted.",
        "de": "Ist ein begrenzter Indexer aufgebraucht, warten Anfragen eventuell, bis er zurückgesetzt wird; die anderen Indexer suchen weiter. Von Hand auf den Indexer-Websites gemachte Suchen werden nicht gezählt."}
PROM = pathlib.Path.home() / ".local/state/indexer-bar/storage.prom"
SUB_STATE = pathlib.Path.home() / ".local/state/indexer-bar/subtitle-searches.json"
BAZARR_TZ = TZ
SUB_DOWNLOAD_ACTIONS = {1, 2, 3}
# Provider calls per searched item: a movie is one search, an episode is searched per language with season fallback.
MOVIE_CALLS, EPISODE_CALLS = 1, 3
SEARCH_JOBS = [(re.compile(r"^Downloaded missing subtitles for .* \(\d{4}\)$"), "movie-once"),
               (re.compile(r"^Downloaded missing subtitles for "), "episode"),
               (re.compile(r"^Searched for missing movies subtitles$"), "movie"),
               (re.compile(r"^Searched for missing series subtitles$"), "episode"),
               (re.compile(r"^Tried to upgrade movies subtitles$"), "movie"),
               (re.compile(r"^Tried to upgrade episodes subtitles$"), "episode"),
               (re.compile(r"^Searched \S+ for "), "once")]
PAUSE_TEXT = {"DownloadLimitExceeded": "download limit reached", "APIThrottled": "rate limited", "TooManyRequests": "rate limited",
              "ServiceUnavailable": "service down", "AuthenticationError": "login failed"}


class ApiError(Exception):
    pass


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def local_date(now):
    return now.astimezone(TZ).date()


def local_midnight(day):
    return dt.datetime.combine(day, dt.time(0), TZ).astimezone(UTC)


def utc_midnight(now, days=0):
    return dt.datetime.combine(now.astimezone(UTC).date() + dt.timedelta(days=days), dt.time(0), UTC)


def window_start(window, now):
    return now - DAY if window == "rolling" else local_midnight(local_date(now))


def since(limits, now):
    return min([now - DAY] + [window_start(c.get("window", "rolling"), now) for c in limits.values()])


def level(used, limit):
    if limit is None:
        return "ok"
    if limit <= 0 or used / limit >= RED:
        return "full"
    return "warn" if used / limit >= AMBER else "ok"


def metric(times, limit, window, now):
    times = sorted(times)
    used = len(times)
    resets = None
    if limit is not None and times:
        resets = times[max(0, used - limit)] + DAY if window == "rolling" else local_midnight(local_date(now) + dt.timedelta(days=1))
    return {"used": used, "limit": limit, "level": level(used, limit), "resetsAt": iso(resets)}


def usage(indexers, history, limits, now):
    events = {}
    for e in history:
        try:
            events.setdefault(e["indexerId"], []).append((e["eventType"], parse_time(e["date"])))
        except (KeyError, TypeError, ValueError):
            continue
    order = list(limits)
    rows = []
    for ix in indexers:
        if not ix.get("enable", True):
            continue
        cfg = limits.get(ix["name"], {})
        window = cfg.get("window", "rolling")
        start = window_start(window, now)
        inside = [(kind, t) for kind, t in events.get(ix["id"], []) if (t > start if window == "rolling" else t >= start) and t <= now]
        searches = metric([t for kind, t in inside if kind in SEARCH_EVENTS], cfg.get("searches"), window, now)
        downloads = metric([t for kind, t in inside if kind in GRAB_EVENTS], cfg.get("downloads"), window, now)
        rows.append({"name": ix["name"], "protocol": ix.get("protocol"), "limited": bool(cfg), "window": window,
                     "level": max(searches["level"], downloads["level"], key=RANK.get), "searches": searches, "downloads": downloads})
    rows.sort(key=lambda r: (0, order.index(r["name"])) if r["limited"] else (1, r["name"].lower()))
    return {"indexers": rows}


def job_requests(job):
    name, items = job.get("job_name") or "", job.get("progress_max") or 0
    for pattern, kind in SEARCH_JOBS:
        if pattern.search(name):
            if kind == "movie-once":
                return MOVIE_CALLS if items else 0
            if kind == "once":
                return MOVIE_CALLS
            return items * (EPISODE_CALLS if kind == "episode" else MOVIE_CALLS)
    return 0


def count_searches(jobs, state, now):
    day = now.astimezone(UTC).date().isoformat()
    seen = dict(state.get("seen", {})) if state.get("day") == day else {}
    start = utc_midnight(now)
    for j in jobs:
        if j.get("status") not in ("completed", "failed"):
            continue
        try:
            when = parse_time(j["last_run_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if not start <= when <= now:
            continue
        key = hashlib.sha1(f'{j.get("job_id")}|{j.get("job_name")}|{j["last_run_time"]}'.encode()).hexdigest()[:16]
        seen.setdefault(key, job_requests(j))
    return sum(seen.values()), {"day": day, "seen": seen}


def history_downloads(rows, provider, now):
    start, n = utc_midnight(now), 0
    for r in rows:
        if r.get("provider") != provider or r.get("action") not in SUB_DOWNLOAD_ACTIONS:
            continue
        try:
            when = dt.datetime.strptime(r["parsed_timestamp"], "%m/%d/%y %H:%M:%S").replace(tzinfo=BAZARR_TZ)
        except (KeyError, TypeError, ValueError):
            continue
        n += start <= when.astimezone(UTC) <= now
    return n


def sub_metric(used, limit, approx, now):
    m = {"used": used, "limit": limit, "level": level(used, limit), "resetsAt": iso(utc_midnight(now, 1)) if limit is not None else None}
    if approx:
        m["approx"] = True
    return m


def subtitles(cfg, status, downloads, searches_used, now):
    state = {s.get("name"): s for s in status}
    rows = []
    for pid, c in cfg.items():
        approx = set(c.get("approx", []))
        searches = sub_metric(searches_used if c.get("searches") is not None or "searches" in approx else 0, c.get("searches"), "searches" in approx, now)
        dls = sub_metric(downloads.get(pid, 0), c.get("downloads"), "downloads" in approx, now)
        row = {"name": c.get("name", pid), "id": pid, "window": "utc-day", "status": "ok", "searches": searches, "downloads": dls}
        s = state.get(pid, {})
        if s.get("status") and s["status"] != "Good":
            retry = s.get("retry")
            row.update(status="paused", reason=s["status"], retry=retry,
                       statusText=PAUSE_TEXT.get(s["status"], f'paused ({s["status"]})') + (f", retry {retry}" if retry and retry != "-" else ""))
        row["level"] = "full" if row["status"] == "paused" else max(searches["level"], dls["level"], key=RANK.get)
        rows.append(row)
    return rows


def bazarr_usage(cfg, api, state_path, now):
    status = api("/providers").get("data", [])
    jobs = api("/system/jobs").get("data", [])
    rows = []
    for kind in ("movies", "episodes"):
        rows += api(f"/{kind}/history?start=0&length=500").get("data", [])
    state = {}
    try:
        state = json.loads(pathlib.Path(state_path).read_text())
    except (OSError, ValueError):
        pass
    used, state = count_searches(jobs, state, now)
    pathlib.Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    write(state_path, state)
    return subtitles(cfg, status, {pid: history_downloads(rows, pid, now) for pid in cfg}, used, now)


def subscriptions(subs, today):
    out = []
    for s in subs:
        if s.get("lifetime"):
            out.append({"name": s["name"], "kind": s.get("kind"), "lifetime": True, "expires": None, "daysLeft": None, "expired": False, "level": "ok"})
            continue
        expires = s.get("expires")
        if not isinstance(expires, dt.date):
            raise ValueError(f"subscription {s.get('name')!r}: needs expires = YYYY-MM-DD or lifetime = true")
        left = (expires - today).days
        lvl = "full" if left <= SUB_RED_DAYS else "orange" if left <= SUB_ORANGE_DAYS else "yellow" if left <= SUB_YELLOW_DAYS else "ok"
        out.append({"name": s["name"], "kind": s.get("kind"), "lifetime": False, "expires": expires.isoformat(), "daysLeft": left, "expired": left < 0, "level": lvl})
    return out


def storage(disks, statvfs=os.statvfs):
    out = []
    for d in disks:
        sizes = []
        for path in d["paths"]:
            try:
                st = statvfs(path)
            except OSError:
                continue
            sizes.append((st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize))
        if not sizes:
            continue
        total, free = sum(t for t, _ in sizes), sum(f for _, f in sizes)
        used = total - free
        ratio = used / total if total else 0
        lvl = "full" if ratio >= d["red"] else "yellow" if ratio >= d["yellow"] else "ok"
        out.append({"name": d["name"], "id": d["id"], "total": total, "used": used, "free": free, "pct": round(100 * ratio, 1),
                    "level": lvl, "yellow": round(100 * d["yellow"]), "red": round(100 * d["red"])})
        if len(d["paths"]) > 1:
            out[-1]["parts"] = [round(100 * (t - f) / t, 1) if t else 0 for t, f in sizes]
    return out


def bar_disk(d):
    t = DISK_TEXT.get(d["id"], {})
    out = {k: v for k, v in d.items() if k != "id"}
    if "sub" in t:
        out["sub"] = t["sub"]
    if "hint" in t:
        out["hint"] = {lang: text.format(yellow=d["yellow"], red=d["red"]) for lang, text in t["hint"].items()}
    if "partsLabel" in t:
        out["partsLabel"] = t["partsLabel"]
    return out


def storage_prom(disks, now):
    lines = []
    for metric, key in (("size", "total"), ("avail", "free")):
        lines += [f'beefy_storage_{metric}_bytes{{branch="{d["id"]}"}} {d[key]}' for d in disks]
    lines.append(f"beefy_storage_updated_timestamp_seconds {int(now.timestamp())}")
    return "\n".join(lines) + "\n"


def load_config(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    limits = cfg.get("indexers", {})
    for name, c in limits.items():
        if c.get("window", "rolling") not in WINDOWS:
            raise ValueError(f"indexer {name!r}: window must be one of {sorted(WINDOWS)}")
        for k in ("searches", "downloads"):
            if k in c and (not isinstance(c[k], int) or c[k] < 0):
                raise ValueError(f"indexer {name!r}: {k} must be a whole number")
    subs = cfg.get("subscriptions", [])
    subscriptions(subs, dt.date.today())
    subtitle_cfg = cfg.get("subtitles", {})
    for pid, c in subtitle_cfg.items():
        for k in ("searches", "downloads"):
            if k in c and (not isinstance(c[k], int) or c[k] < 0):
                raise ValueError(f"subtitles {pid!r}: {k} must be a whole number")
    return {"indexers": limits, "subscriptions": subs, "subtitles": subtitle_cfg}


def write(path, data):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".usage-")
    with os.fdopen(fd, "w") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def run(cfg, api, out, now, disks=DISKS, statvfs=os.statvfs, prom=None, bazarr=None, sub_state=SUB_STATE):
    disk_rows = storage(disks, statvfs)
    data = {"ok": True, "generated": iso(now), "indexers": [], "subscriptions": subscriptions(cfg["subscriptions"], local_date(now)),
            "storage": [bar_disk(d) for d in disk_rows], "note": NOTE}
    try:
        indexers = api("/indexer")
        history = api("/history/since?date=" + urllib.parse.quote(iso(since(cfg["indexers"], now)), safe=":"))
        data.update(usage(indexers, history, cfg["indexers"], now))
    except ApiError as e:
        data["ok"] = False
        print(f"indexer-usage: Prowlarr unavailable: {e}", file=sys.stderr)
    if bazarr and cfg.get("subtitles"):
        try:
            data["subtitles"] = bazarr_usage(cfg["subtitles"], bazarr, sub_state, now)
            data["subtitlesOk"] = True
        except (ApiError, AttributeError) as e:
            data["subtitles"], data["subtitlesOk"] = [], False
            print(f"indexer-usage: Bazarr unavailable: {e}", file=sys.stderr)
    write(out, data)
    if prom and disk_rows:
        pathlib.Path(prom).parent.mkdir(parents=True, exist_ok=True)
        write(prom, storage_prom(disk_rows, now))
    return data


def client(config_xml, base, key=None, prefix="/api/v1", header="X-Api-Key"):
    key = key or re.search(r"<ApiKey>([^<]+)</ApiKey>", pathlib.Path(config_xml).read_text()).group(1)

    def get(path):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + prefix + path, headers={header: key}), timeout=20) as r:
                return json.load(r)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError(type(e).__name__ + (f" {e.code}" if isinstance(e, urllib.error.HTTPError) else "")) from None
    return get


def main():
    cfg = load_config(os.environ.get("IB_CONFIG", ROOT / "indexer-bar" / "limits.toml"))
    out = os.environ.get("IB_OUT", "/srv/appdata/indexer-bar/usage.json")
    try:
        api = client(os.environ.get("IB_PROWLARR_CONFIG", "/srv/appdata/prowlarr/config.xml"), os.environ.get("IB_PROWLARR_URL", "http://127.0.0.1:9696"))
    except (OSError, AttributeError):
        def api(path):
            raise ApiError("no Prowlarr API key")
    try:
        bkey = pathlib.Path(os.environ.get("IB_BAZARR_KEY", ROOT.parent / "Movie-Downloads" / "secrets" / "hook" / "bazarr.apikey")).read_text().strip()
        bazarr = client(None, os.environ.get("IB_BAZARR_URL", "http://127.0.0.1:6767"), key=bkey, prefix="/api", header="X-API-KEY")
    except OSError:
        def bazarr(path):
            raise ApiError("no Bazarr API key")
    run(cfg, api, out, dt.datetime.now(UTC).replace(microsecond=0), prom=os.environ.get("IB_STORAGE_PROM", PROM),
        bazarr=bazarr, sub_state=os.environ.get("IB_SUB_STATE", SUB_STATE))


if __name__ == "__main__":
    main()
