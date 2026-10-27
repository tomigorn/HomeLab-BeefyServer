#!/usr/bin/env python3
"""Sets the Home Screen Sections plugin's admin config: on for every user, the owner's sections in order (README "Home screen sections"), Upcoming from Sonarr/Radarr.
Idempotent, never prints API keys. Usage: home-sections.py [--dry-run]"""
import copy
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api  # noqa: E402

PLUGIN_ID = "b8298e012697407ab44daa8dc795e850"
ORDER = [
    "MyMedia", "ContinueWatching", "NextUp", "RecentlyAddedMovies", "RecentlyAddedShows",
    "WatchAgain", "BecauseYouWatched", "HiddenGems", "FinishTheseCollections", "MyList", "CollectionsSection",
    "Studio", "Starring", "DirectedBy", "UpcomingShows", "UpcomingMovies",
]
LIMITS = {"BecauseYouWatched": (1, 3)}
# Jellyfin reaches the arrs through beefy's published ports (hairpin), no shared network.
# How far ahead Upcoming Shows / Upcoming Movies look (owner 2026-10-27).
WINDOW = {"Sonarr": (1, "Months"), "Radarr": (3, "Months")}
ARR = {"Sonarr": ("http://192.168.1.102:8989", "/srv/appdata/sonarr/config.xml"), "Radarr": ("http://192.168.1.102:7878", "/srv/appdata/radarr/config.xml")}


def arr_key(xml):
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", xml).group(1)


def apply(cfg, types, libs, arr):
    """cfg with the wanted settings; types = /ModularHomeViews/Sections items, libs = {collection type: library id}, arr = {name: (url, key)}."""
    cfg = copy.deepcopy(cfg)
    cfg["Enabled"] = True
    cfg["AllowUserOverride"] = True
    cfg["DateFormat"], cfg["DateDelimiter"] = "DD/MM/YYYY", "."
    cfg["DefaultMoviesLibraryId"] = libs.get("movies", "")
    cfg["DefaultTVShowsLibraryId"] = libs.get("tvshows", "")
    for name, (url, key) in arr.items():
        cfg.setdefault(name, {}).update(Url=url, ApiKey=key)
        if name in WINDOW:
            cfg[name].update(UpcomingTimeframeValue=WINDOW[name][0], UpcomingTimeframeUnit=WINDOW[name][1])
    existing = {s["SectionId"]: s for s in cfg.get("SectionSettings") or []}
    out = []
    for i, t in enumerate(types):
        sid = t["Section"]
        s = existing.pop(sid, None) or {"SectionId": sid, "ViewMode": t.get("ViewMode") or "Landscape", "HideWatchedItems": False, "PluginConfigurations": []}
        lower, upper = LIMITS.get(sid, (1, 1))
        s.update(Enabled=sid in ORDER, AllowUserOverride=True, LowerLimit=lower, UpperLimit=upper,
                 OrderIndex=ORDER.index(sid) + 1 if sid in ORDER else len(ORDER) + 1 + i)
        out.append(s)
    # Stored in display order: the user API lists enabled sections in this order (empty-section placeholders).
    cfg["SectionSettings"] = sorted(out, key=lambda s: s["OrderIndex"]) + list(existing.values())
    return cfg


def summary(cfg):
    on = sorted((s for s in cfg["SectionSettings"] if s["Enabled"]), key=lambda s: s["OrderIndex"])
    lines = [f"Enabled={cfg['Enabled']} AllowUserOverride={cfg['AllowUserOverride']}"]
    lines += [f"{n}. {s['SectionId']}" for n, s in enumerate(on, 1)]
    lines += [f"{name}: {cfg[name]['Url']} key {'set' if cfg[name].get('ApiKey') else 'MISSING'}, next {cfg[name].get('UpcomingTimeframeValue')} {cfg[name].get('UpcomingTimeframeUnit')}" for name in ARR]
    return "\n".join(lines)


def main(argv):
    arr = {name: (url, arr_key(pathlib.Path(path).read_text())) for name, (url, path) in ARR.items()}
    libs = {v["CollectionType"]: v["ItemId"] for v in api("GET", "/Library/VirtualFolders")}
    types = api("GET", "/ModularHomeViews/Sections")["Items"]
    current = api("GET", f"/Plugins/{PLUGIN_ID}/Configuration")
    wanted = apply(current, types, libs, arr)
    print(summary(wanted))
    if wanted == current:
        print("unchanged")
    elif "--dry-run" in argv:
        print("would change")
    else:
        api("POST", f"/Plugins/{PLUGIN_ID}/Configuration", wanted)
        print("changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
