#!/usr/bin/env python3
"""Night search (README "Upgrades"): inside the maintenance window, searches what RSS cannot have caught while beefy slept, within the Treasure Maps budget.
Order: monitored episodes aired in the last 14 days without a file, monitored movies released (digital/physical) in the last 60 days without a file, then a few cutoff-unmet movies and episodes (least recently searched first).
A season with 3+ missing episodes is one SeasonSearch. Budget: at most NS_TM_MAX searches (default 200) and never above NS_TM_MAX_PCT % of Treasure Maps' window (bar: /srv/appdata/indexer-bar/usage.json); what does not fit is logged and comes first next night.
Waits for the searches to finish, so the maintenance window keeps beefy awake meanwhile. Usage: scripts/night-search.py [--dry-run]"""
import datetime
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

USAGE = "/srv/appdata/indexer-bar/usage.json"
INDEXER = "Treasure Maps"
BAR_MAX_AGE = 900
DONE = {"completed", "failed", "aborted", "cancelled", "orphaned"}


class ApiError(Exception):
    pass


@dataclass
class Unit:
    app: str
    command: str
    body: dict
    cost: int
    label: str


def parse(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def sort_key(item):
    t = parse(item.get("lastSearchTime"))
    return (t is not None, t or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), item["id"])


def fresh(item, now, min_gap_hours):
    t = parse(item.get("lastSearchTime"))
    return t is None or now - t >= datetime.timedelta(hours=min_gap_hours)


def new_episodes(episodes, now, days, min_gap_hours, queued):
    out = []
    for e in episodes:
        aired = parse(e.get("airDateUtc"))
        s = e.get("series") or {}
        season_ok = any(x["seasonNumber"] == e["seasonNumber"] and x["monitored"] for x in s.get("seasons", []))
        if (e.get("monitored") and s.get("monitored") and season_ok and not e.get("hasFile") and aired
                and now - datetime.timedelta(days=days) <= aired <= now
                and e["id"] not in queued and fresh(e, now, min_gap_hours)):
            out.append(e)
    return sorted(out, key=sort_key)


def new_films(movies, now, days, min_gap_hours, queued):
    out = []
    for m in movies:
        dates = [d for d in (parse(m.get("digitalRelease")), parse(m.get("physicalRelease"))) if d and d <= now]
        if (m.get("monitored") and m.get("isAvailable") and not m.get("hasFile") and dates
                and max(dates) >= now - datetime.timedelta(days=days)
                and m["id"] not in queued and fresh(m, now, min_gap_hours)):
            out.append(m)
    return sorted(out, key=sort_key)


def ep_label(e):
    return f"{(e.get('series') or {}).get('title', e.get('seriesId'))} S{e['seasonNumber']}E{e['episodeNumber']}"


def episode_units(episodes, season_min, cost_episode, cost_season):
    groups = {}
    for e in episodes:
        groups.setdefault((e["seriesId"], e["seasonNumber"]), []).append(e)
    units = []
    for (series, season), g in groups.items():
        title = (g[0].get("series") or {}).get("title", series)
        if season_min and len(g) >= season_min:
            units.append(Unit("sonarr", "SeasonSearch", {"seriesId": series, "seasonNumber": season}, cost_season, f"{title} S{season} ({len(g)} missing)"))
        else:
            units.append(Unit("sonarr", "EpisodeSearch", {"episodeIds": [e["id"] for e in g]}, cost_episode * len(g), ", ".join(ep_label(e) for e in g)))
    return units


def movie_units(movies, cost_movie):
    return [Unit("radarr", "MoviesSearch", {"movieIds": [m["id"] for m in movies]}, cost_movie * len(movies), ", ".join(m["title"] for m in movies))] if movies else []


def tm_budget(usage, now, cap, pct, fallback):
    try:
        gen = parse(usage["generated"])
        s = next(i for i in usage["indexers"] if i["name"] == INDEXER)["searches"]
        if not usage.get("ok") or (now - gen).total_seconds() > BAR_MAX_AGE:
            return fallback
        if s["limit"] is None:
            return cap
        return max(0, min(cap, int(s["limit"] * pct / 100) - s["used"]))
    except (KeyError, TypeError, StopIteration, ValueError):
        return fallback


def apply_budget(units, budget):
    chosen, skipped, left = [], [], budget
    for u in units:
        if u.cost <= left:
            chosen.append(u)
            left -= u.cost
        else:
            skipped.append(u)
    return chosen, skipped


def cutoff_pick(records, now, min_gap_hours, queued, exclude, count):
    ok = [r for r in records if r["id"] not in queued and r["id"] not in exclude and fresh(r, now, min_gap_hours)]
    return ok[:count]


def run(now, radarr, sonarr, usage, cfg, dry_run=False, log=print, sleep=time.sleep):
    rc, units = 0, []
    gap = cfg["min_gap_hours"]

    def gather(app, fn):
        nonlocal rc
        try:
            return fn()
        except ApiError as e:
            log(f"{app}: {e}")
            rc = 1
            return None

    def sonarr_part():
        queued = {r["episodeId"] for r in sonarr("GET", "/queue?page=1&pageSize=1000")["records"]}
        start = (now - datetime.timedelta(days=cfg["new_ep_days"])).date().isoformat()
        cal = sonarr("GET", f"/calendar?start={start}&end={(now.date() + datetime.timedelta(days=1)).isoformat()}&unmonitored=false&includeSeries=true")
        new = new_episodes(cal, now, cfg["new_ep_days"], gap, queued)
        cut = []
        if cfg["cutoff_episodes"]:
            recs = sonarr("GET", "/wanted/cutoff?page=1&pageSize=50&monitored=true&sortKey=episodes.lastSearchTime&sortDirection=ascending&includeSeries=true")["records"]
            cut = cutoff_pick(recs, now, gap, queued, {e["id"] for e in new}, cfg["cutoff_episodes"])
        return new, cut

    def radarr_part():
        queued = {r["movieId"] for r in radarr("GET", "/queue?page=1&pageSize=1000")["records"]}
        new = new_films(radarr("GET", "/movie"), now, cfg["new_film_days"], gap, queued)
        cut = []
        if cfg["cutoff_movies"]:
            recs = radarr("GET", "/wanted/cutoff?page=1&pageSize=50&monitored=true&sortKey=movies.lastSearchTime&sortDirection=ascending")["records"]
            cut = cutoff_pick(recs, now, gap, queued, {m["id"] for m in new}, cfg["cutoff_movies"])
        return new, cut

    s = gather("sonarr", sonarr_part)
    r = gather("radarr", radarr_part)
    s_new, s_cut = s or ([], [])
    r_new, r_cut = r or ([], [])
    ce, cm = cfg["cost_episode"], cfg["cost_movie"]
    units += episode_units(s_new, cfg["season_min"], ce, cfg["cost_season"])
    units += [u for m in r_new for u in movie_units([m], cm)]
    units += [u for m in r_cut for u in movie_units([m], cm)]
    units += episode_units(s_cut, None, ce, cfg["cost_season"])

    budget = tm_budget(usage, now, cfg["cap"], cfg["pct"], cfg["fallback"])
    chosen, skipped = apply_budget(units, budget)
    log(f"candidates: new episodes {len(s_new)}, new films {len(r_new)}, cutoff movies {len(r_cut)}, cutoff episodes {len(s_cut)}")
    log(f"budget {budget} Treasure Maps searches (cap {cfg['cap']}, {cfg['pct']}% of its window)")
    for u in chosen:
        log(f"{'would search' if dry_run else 'search'} {u.app} {u.command} (cost {u.cost}): {u.label}")
    for u in skipped:
        log(f"skipped (budget) {u.app} {u.command} (cost {u.cost}): {u.label}")
    log(f"TM estimate {sum(u.cost for u in chosen)} of {budget}, {len(chosen)} commands, {len(skipped)} skipped")
    if dry_run:
        return rc

    apis, ids = {"radarr": radarr, "sonarr": sonarr}, []
    for u in chosen:
        try:
            res = apis[u.app]("POST", "/command", {"name": u.command, **u.body})
            ids.append((u.app, res["id"]))
        except (ApiError, KeyError, TypeError) as e:
            log(f"{u.app} {u.command} failed: {e}")
            rc = 1
    waited = 0
    while ids and waited <= cfg["wait_max"]:
        ids = [(a, i) for a, i in ids if (apis[a]("GET", f"/command/{i}") or {}).get("status") not in DONE]
        if ids:
            sleep(cfg["poll"])
            waited += max(cfg["poll"], 1)
    if ids:
        log(f"still running after {cfg['wait_max']}s: {len(ids)} commands")
    return rc


def _open(req, timeout=60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body.strip() else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ApiError(f"{req.full_url.split('?')[0]}: {e}") from None


def arr_client(config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        return _open(urllib.request.Request(base + path, data=data, method=method, headers={"X-Api-Key": get("ApiKey"), "Content-Type": "application/json"}))
    return call


def env_int(name, default):
    return int(os.environ.get(name, default))


def main():
    cfg = dict(cap=env_int("NS_TM_MAX", 200), pct=env_int("NS_TM_MAX_PCT", 70), fallback=env_int("NS_TM_FALLBACK", 10),
               new_ep_days=env_int("NS_NEW_EPISODE_DAYS", 14), new_film_days=env_int("NS_NEW_FILM_DAYS", 60), min_gap_hours=env_int("NS_MIN_GAP_HOURS", 20),
               season_min=env_int("NS_SEASON_MIN", 3), cutoff_movies=env_int("NS_CUTOFF_MOVIES", 5), cutoff_episodes=env_int("NS_CUTOFF_EPISODES", 5),
               cost_movie=env_int("NS_COST_MOVIE", 1), cost_episode=env_int("NS_COST_EPISODE", 1), cost_season=env_int("NS_COST_SEASON", 3),
               wait_max=env_int("NS_WAIT_MAX", 1800), poll=env_int("NS_POLL", 15))
    try:
        usage = json.loads(pathlib.Path(os.environ.get("NS_USAGE", USAGE)).read_text())
    except (OSError, ValueError):
        usage = None
    radarr = arr_client(os.environ.get("NS_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"))
    sonarr = arr_client(os.environ.get("NS_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"))
    return run(datetime.datetime.now(datetime.timezone.utc), radarr, sonarr, usage, cfg, dry_run="--dry-run" in sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
