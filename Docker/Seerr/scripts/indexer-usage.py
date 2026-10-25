#!/usr/bin/env python3
"""Indexer bar (README "Indexer bar"): counts Prowlarr searches (queries + RSS) and grabs per indexer inside its limit window from indexer-bar/limits.toml, adds the subscription deadlines, and writes usage.json (names and counts only) for the bar the proxy shows above Seerr.
Prowlarr unreachable: usage.json says ok=false (the bar shows "usage unavailable"), subscriptions stay. Run by systemd/indexer-usage.timer.
Usage: scripts/indexer-usage.py"""
import datetime as dt
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
SUB_AMBER_DAYS, SUB_RED_DAYS = 14, 3
RANK = {"ok": 0, "warn": 1, "full": 2}


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
        lvl = "full" if left <= SUB_RED_DAYS else "warn" if left <= SUB_AMBER_DAYS else "ok"
        out.append({"name": s["name"], "kind": s.get("kind"), "lifetime": False, "expires": expires.isoformat(), "daysLeft": left, "expired": left < 0, "level": lvl})
    return out


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
    return {"indexers": limits, "subscriptions": subs}


def write(path, data):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".usage-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def run(cfg, api, out, now):
    data = {"ok": True, "generated": iso(now), "indexers": [], "subscriptions": subscriptions(cfg["subscriptions"], local_date(now))}
    try:
        indexers = api("/indexer")
        history = api("/history/since?date=" + urllib.parse.quote(iso(since(cfg["indexers"], now)), safe=":"))
        data.update(usage(indexers, history, cfg["indexers"], now))
    except ApiError as e:
        data["ok"] = False
        print(f"indexer-usage: Prowlarr unavailable: {e}", file=sys.stderr)
    write(out, data)
    return data


def client(config_xml, base):
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", pathlib.Path(config_xml).read_text()).group(1)

    def get(path):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + "/api/v1" + path, headers={"X-Api-Key": key}), timeout=20) as r:
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
    run(cfg, api, out, dt.datetime.now(UTC).replace(microsecond=0))


if __name__ == "__main__":
    main()
