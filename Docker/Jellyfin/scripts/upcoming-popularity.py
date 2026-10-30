#!/usr/bin/env python3
"""TMDB popularity and details of what is coming soon, for the home row "Coming soon" (home-placeholders/home-layout.js): ranks titles released in the same week, links titles in the library, opens the details overlay of the others.
Reads the Sonarr and Radarr calendars, asks Seerr (its TMDB access, no own key) for each title's details and writes the JavaScript Injector entry "Home upcoming":
window.HssUpcomingPop = {"s:<Sonarr series id>" | "m:<Radarr movie id>": popularity}, window.HssUpcomingInfo = {key: {tmdb, tvdb, imdb, d: details id}};
the details themselves go to coming.json next to the popular allow-list (served by the popular backend by id only, never requestable); coming-list.json beside it lists those titles in the Coming soon order for the trending sync (a series with the season "s" and episode "e" of its soonest episode).
Never prints API keys. Usage: upcoming-popularity.py [--dry-run]"""
import contextlib
import datetime
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import jellyfin_api  # noqa: E402
import tmdb_details  # noqa: E402

NAME = "Home upcoming"
PLUGIN = "JavaScript Injector"
SEERR = "http://127.0.0.1:5055"
SEERR_KEY = HERE.parent.parent / "Seerr" / "secrets" / "api-key"
ARR = {"Sonarr": ("http://127.0.0.1:8989", "/srv/appdata/sonarr/config.xml"), "Radarr": ("http://127.0.0.1:7878", "/srv/appdata/radarr/config.xml")}
# A little longer than the plugin's windows (Sonarr 1 month, Radarr 3 months, home-sections.py).
DAYS = 100
SIDECAR = pathlib.Path("/srv/appdata/jellyfin-popular/coming.json")


def release_date(entry, today):
    """ISO date of the calendar entry: an episode's air date; a movie's earliest release not before today, else its earliest."""
    if entry.get("airDateUtc"):
        return entry["airDateUtc"][:10]
    dates = sorted(v[:10] for v in (entry.get(k) for k in ("inCinemas", "digitalRelease", "physicalRelease")) if v)
    return next((d for d in dates if d >= today.isoformat()), dates[0] if dates else None)


def titles(episodes, movies, today=None):
    """{key: (kind, TMDB id, TVDB id, IMDb id, date, (season, episode) | None)} for the calendar entries; one per series (its first episode)."""
    today = today or datetime.date.today()
    out = {}
    for e in episodes or []:
        s = e.get("series") or {}
        if e.get("seriesId") and s.get("tmdbId"):
            key, date = f"s:{e['seriesId']}", release_date(e, today)
            if key not in out or (date and (not out[key][4] or date < out[key][4])):
                ep = (e["seasonNumber"], e.get("episodeNumber")) if isinstance(e.get("seasonNumber"), int) else None
                out[key] = ("tv", s["tmdbId"], s.get("tvdbId"), s.get("imdbId"), date, ep)
    for m in movies or []:
        if m.get("id") and m.get("tmdbId"):
            out[f"m:{m['id']}"] = ("movie", m["tmdbId"], None, m.get("imdbId"), release_date(m, today), None)
    return out


def ids_of(title):
    """The provider ids the browser matches against the library's ProviderIds (strings, like Jellyfin's)."""
    _, tmdb, tvdb, imdb = title[:4]
    out = {"tmdb": str(int(tmdb))}
    if isinstance(tvdb, int) and tvdb > 0:
        out["tvdb"] = str(tvdb)
    if isinstance(imdb, str) and tmdb_details.IMDB_RE.match(imdb):
        out["imdb"] = imdb
    return out


def _js(data):
    return json.dumps(dict(sorted(data.items())), separators=(",", ":"), sort_keys=True).replace("</", "<\\/")


def pop_script(pops, info=None):
    src = "window.HssUpcomingPop = " + _js(pops) + ";\n"
    return src + ("window.HssUpcomingInfo = " + _js(info) + ";\n" if info is not None else "")


def upsert(config, script):
    entry = {"Name": NAME, "Script": script, "Enabled": True, "RequiresAuthentication": True}
    scripts = [s for s in config.get("CustomJavaScripts") or [] if s.get("Name") != NAME]
    return {**config, "CustomJavaScripts": scripts + [entry]}


def _arr(name, path, query):
    url, conf = ARR[name]
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", pathlib.Path(conf).read_text()).group(1)
    req = urllib.request.Request(f"{url}/api/v3/{path}?{urllib.parse.urlencode(query)}")
    req.add_header("X-Api-Key", key)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _seerr(path):
    req = urllib.request.Request(SEERR + path)
    req.add_header("X-Api-Key", SEERR_KEY.read_text().strip())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def week_key(date):
    iso = datetime.date.fromisoformat(date).isocalendar()
    return iso[0], iso[1]


def coming_list(wanted, infos, pops):
    """Titles with details in the order of the web's Coming soon row: ISO week of the date, popularity descending, title."""
    items = []
    for key, t in wanted.items():
        ref = f"{t[0]}/{int(t[1])}"
        if ref in infos and t[4]:
            items.append((key, ref, t))
    items.sort(key=lambda x: (week_key(x[2][4]), -(pops.get(x[0]) if pops.get(x[0]) is not None else -1), (infos[x[1]].get("t") or {}).get("en") or ""))
    return [{"ref": ref, "kind": t[0], "tmdb": int(t[1]), "d": None, "date": t[4], "pop": pops.get(key), **({"s": t[5][0], "e": t[5][1]} if t[5] else {})} for key, ref, t in items]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv, api=None, arr=_arr, seerr=_seerr, today=None, out=sys.stdout, sidecar=SIDECAR):
    if any(a != "--dry-run" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if api is None:
        api = jellyfin_api.api
    start = today or datetime.date.today()
    window = {"start": start.isoformat(), "end": (start + datetime.timedelta(days=DAYS)).isoformat()}
    wanted = titles(arr("Sonarr", "calendar", {**window, "includeSeries": "true"}), arr("Radarr", "calendar", window), start)
    pops, infos, info = {}, {}, {}
    for key, t in wanted.items():
        kind, tmdb = t[:2]
        info[key] = ids_of(t)
        try:
            en, infos[f"{kind}/{int(tmdb)}"] = tmdb_details.fetch(seerr, kind, tmdb)
        except Exception as e:
            print(f"skipped {key}: {type(e).__name__}", file=out)
            continue
        p = en.get("popularity")
        if isinstance(p, (int, float)) and not isinstance(p, bool):
            pops[key] = round(float(p), 1)
    tokens = tmdb_details.assign(sidecar, infos)
    for key, t in wanted.items():
        ref = f"{t[0]}/{int(t[1])}"
        if ref in infos:
            info[key]["d"] = tokens[ref]
    if "--dry-run" not in argv:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        tmdb_details.write_sidecar(sidecar, stamp, infos, tokens)
        items = coming_list(wanted, infos, pops)
        for i in items:
            i["d"] = tokens[i["ref"]]
        write_json(sidecar.with_name("coming-list.json"), {"updated": stamp, "items": items})
    plugin_id = next(p["Id"] for p in api("GET", "/Plugins") if p["Name"] == PLUGIN)
    path = f"/Plugins/{plugin_id}/Configuration"
    script = pop_script(pops, info)
    with contextlib.nullcontext() if "--dry-run" in argv else jellyfin_api.injector_lock():
        config = api("GET", path)
        changed = next((s.get("Script") for s in config.get("CustomJavaScripts") or [] if s.get("Name") == NAME), None) != script
        if changed and "--dry-run" not in argv:
            api("POST", path, upsert(config, script))
    verb = ("would change" if "--dry-run" in argv else "changed") if changed else "unchanged"
    print(f"{len(pops)} of {len(wanted)} upcoming titles ranked, {len(infos)} with details, {verb}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
