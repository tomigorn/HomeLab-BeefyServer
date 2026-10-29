#!/usr/bin/env python3
"""TMDB popularity of what is coming soon, for the home row "Coming soon" (home-placeholders/home-layout.js): ranks titles released in the same week.
Reads the Sonarr and Radarr calendars, asks Seerr (its TMDB access, no own key) for each title's popularity and writes the JavaScript Injector entry "Home upcoming" (window.HssUpcomingPop = {"s:<Sonarr series id>" | "m:<Radarr movie id>": popularity}).
Never prints API keys. Usage: upcoming-popularity.py [--dry-run]"""
import contextlib
import datetime
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

import jellyfin_api

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NAME = "Home upcoming"
PLUGIN = "JavaScript Injector"
SEERR = "http://127.0.0.1:5055"
SEERR_KEY = HERE.parent.parent / "Seerr" / "secrets" / "api-key"
ARR = {"Sonarr": ("http://127.0.0.1:8989", "/srv/appdata/sonarr/config.xml"), "Radarr": ("http://127.0.0.1:7878", "/srv/appdata/radarr/config.xml")}
# A little longer than the plugin's windows (Sonarr 1 month, Radarr 3 months, home-sections.py).
DAYS = 100


def titles(episodes, movies):
    """{key: (kind, TMDB id)} for the calendar entries; one per series."""
    out = {}
    for e in episodes or []:
        tmdb = (e.get("series") or {}).get("tmdbId")
        if e.get("seriesId") and tmdb:
            out[f"s:{e['seriesId']}"] = ("tv", tmdb)
    for m in movies or []:
        if m.get("id") and m.get("tmdbId"):
            out[f"m:{m['id']}"] = ("movie", m["tmdbId"])
    return out


def pop_script(pops):
    return "window.HssUpcomingPop = " + json.dumps(dict(sorted(pops.items())), separators=(",", ":")) + ";\n"


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


def _seerr(kind, tmdb):
    req = urllib.request.Request(f"{SEERR}/api/v1/{kind}/{int(tmdb)}")
    req.add_header("X-Api-Key", SEERR_KEY.read_text().strip())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main(argv, api=None, arr=_arr, seerr=_seerr, today=None, out=sys.stdout):
    if any(a != "--dry-run" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if api is None:
        api = jellyfin_api.api
    start = today or datetime.date.today()
    window = {"start": start.isoformat(), "end": (start + datetime.timedelta(days=DAYS)).isoformat()}
    wanted = titles(arr("Sonarr", "calendar", {**window, "includeSeries": "true"}), arr("Radarr", "calendar", window))
    pops = {}
    for key, (kind, tmdb) in wanted.items():
        try:
            p = seerr(kind, tmdb).get("popularity")
        except Exception as e:
            print(f"skipped {key}: {type(e).__name__}", file=out)
            continue
        if isinstance(p, (int, float)):
            pops[key] = round(float(p), 1)
    plugin_id = next(p["Id"] for p in api("GET", "/Plugins") if p["Name"] == PLUGIN)
    path = f"/Plugins/{plugin_id}/Configuration"
    script = pop_script(pops)
    with contextlib.nullcontext() if "--dry-run" in argv else jellyfin_api.injector_lock():
        config = api("GET", path)
        changed = next((s.get("Script") for s in config.get("CustomJavaScripts") or [] if s.get("Name") == NAME), None) != script
        if changed and "--dry-run" not in argv:
            api("POST", path, upsert(config, script))
    verb = ("would change" if "--dry-run" in argv else "changed") if changed else "unchanged"
    print(f"{len(pops)} of {len(wanted)} upcoming titles ranked, {verb}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
