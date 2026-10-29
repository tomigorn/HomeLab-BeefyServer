#!/usr/bin/env python3
"""TMDB popularity and details of what is coming soon, for the home row "Coming soon" (home-placeholders/home-layout.js): ranks titles released in the same week, links titles in the library, opens the details overlay of the others.
Reads the Sonarr and Radarr calendars, asks Seerr (its TMDB access, no own key) for each title's details and writes the JavaScript Injector entry "Home upcoming":
window.HssUpcomingPop = {"s:<Sonarr series id>" | "m:<Radarr movie id>": popularity}, window.HssUpcomingInfo = {key: {tmdb, tvdb, imdb, d: details id}};
the details themselves go to coming.json next to the popular allow-list (served by the popular backend by id only, never requestable).
Never prints API keys. Usage: upcoming-popularity.py [--dry-run]"""
import contextlib
import datetime
import json
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


def titles(episodes, movies):
    """{key: (kind, TMDB id, TVDB id, IMDb id)} for the calendar entries; one per series."""
    out = {}
    for e in episodes or []:
        s = e.get("series") or {}
        if e.get("seriesId") and s.get("tmdbId"):
            out[f"s:{e['seriesId']}"] = ("tv", s["tmdbId"], s.get("tvdbId"), s.get("imdbId"))
    for m in movies or []:
        if m.get("id") and m.get("tmdbId"):
            out[f"m:{m['id']}"] = ("movie", m["tmdbId"], None, m.get("imdbId"))
    return out


def ids_of(title):
    """The provider ids the browser matches against the library's ProviderIds (strings, like Jellyfin's)."""
    _, tmdb, tvdb, imdb = title
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


def main(argv, api=None, arr=_arr, seerr=_seerr, today=None, out=sys.stdout, sidecar=SIDECAR):
    if any(a != "--dry-run" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if api is None:
        api = jellyfin_api.api
    start = today or datetime.date.today()
    window = {"start": start.isoformat(), "end": (start + datetime.timedelta(days=DAYS)).isoformat()}
    wanted = titles(arr("Sonarr", "calendar", {**window, "includeSeries": "true"}), arr("Radarr", "calendar", window))
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
        tmdb_details.write_sidecar(sidecar, datetime.datetime.now().isoformat(timespec="seconds"), infos, tokens)
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
