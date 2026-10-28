#!/usr/bin/env python3
"""Sets the Home Screen Sections plugin's admin config: on for every user, the owner's sections in order (README "Home screen sections"), Upcoming from Sonarr/Radarr.
Locked: users cannot switch Modular Home off or change the sections. Every user gets useModularHome=true too.
Idempotent, never prints API keys. Usage: home-sections.py [--dry-run | --check]"""
import copy
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api  # noqa: E402

PLUGIN_ID = "b8298e012697407ab44daa8dc795e850"
ORDER = [
    "ContinueWatching", "NextUp", "RecentlyAddedMovies", "RecentlyAddedShows",
    "BecauseYouWatched", "HiddenGems", "FinishTheseCollections", "MyList", "CollectionsSection",
    "Studio", "UpcomingShows", "UpcomingMovies",
]
LIMITS = {"BecauseYouWatched": (1, 3)}
# Pagination makes the web client send a fresh page hash per home load; without it the plugin reuses one section list per user for as long as it is opened within the hour.
PAGE_SIZE = 30
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
    cfg["AllowUserOverride"] = False
    cfg["LazyLoadEnabled"], cfg["NumSectionsPerPage"] = True, PAGE_SIZE
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
        s.update(Enabled=sid in ORDER, AllowUserOverride=False, LowerLimit=lower, UpperLimit=upper,
                 OrderIndex=ORDER.index(sid) + 1 if sid in ORDER else len(ORDER) + 1 + i)
        out.append(s)
    for s in existing.values():
        s["AllowUserOverride"] = False
    # Stored in display order: the user API lists enabled sections in this order (empty-section placeholders).
    cfg["SectionSettings"] = sorted(out, key=lambda s: s["OrderIndex"]) + list(existing.values())
    return cfg


def effective(cfg, types, prefs):
    """What the plugin shows a user with these display prefs: (modular home on, enabled sections in order or None when the user can change them)."""
    if cfg.get("AllowUserOverride") and (prefs or {}).get("useModularHome") is not None:
        on = prefs["useModularHome"] == "true"
    else:
        on = bool(cfg.get("Enabled"))
    settings = {s["SectionId"]: s for s in cfg.get("SectionSettings") or []}
    if any(t["Section"] not in settings or settings[t["Section"]].get("AllowUserOverride", True) for t in types):
        return on, None
    names = {t["Section"] for t in types}
    on_list = sorted((s for s in settings.values() if s["Enabled"] and s["SectionId"] in names), key=lambda s: s["OrderIndex"])
    return on, [s["SectionId"] for s in on_list]


def check_users(cfg, types, users):
    """users = [{Name, Id, CustomPrefs}]. Returns (lines, all ok)."""
    lines, bad = [], 0
    for u in users:
        on, sections = effective(cfg, types, u.get("CustomPrefs"))
        problems = ([] if on else ["modular home off"]) + ([] if sections == ORDER else ["sections not locked to the owner order"])
        bad += bool(problems)
        lines += [f"{u['Name']}: {p}" for p in problems]
    lines.append(f"all {len(users)} users ok" if not bad else f"{bad} of {len(users)} users diverge")
    return lines, not bad


def with_modular_home(prefs):
    out = copy.deepcopy(prefs)
    out["CustomPrefs"] = {**(out.get("CustomPrefs") or {}), "useModularHome": "true"}
    return out


def prefs_path(uid):
    return f"/DisplayPreferences/usersettings?userId={uid}&client=emby"


def summary(cfg):
    on = sorted((s for s in cfg["SectionSettings"] if s["Enabled"]), key=lambda s: s["OrderIndex"])
    lines = [f"Enabled={cfg['Enabled']} AllowUserOverride={cfg['AllowUserOverride']}"]
    lines += [f"{n}. {s['SectionId']}" for n, s in enumerate(on, 1)]
    lines += [f"{name}: {cfg[name]['Url']} key {'set' if cfg[name].get('ApiKey') else 'MISSING'}, next {cfg[name].get('UpcomingTimeframeValue')} {cfg[name].get('UpcomingTimeframeUnit')}" for name in ARR]
    return "\n".join(lines)


def main(argv):
    if "--check" in argv:
        types = api("GET", "/ModularHomeViews/Sections")["Items"]
        cfg = api("GET", f"/Plugins/{PLUGIN_ID}/Configuration")
        users = [{"Name": u["Name"], "Id": u["Id"], "CustomPrefs": api("GET", prefs_path(u["Id"])).get("CustomPrefs")} for u in api("GET", "/Users")]
        lines, ok = check_users(cfg, types, users)
        print("\n".join(lines))
        return 0 if ok else 1
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
    if "--dry-run" not in argv:
        for u in api("GET", "/Users"):
            prefs = api("GET", prefs_path(u["Id"]))
            if (prefs.get("CustomPrefs") or {}).get("useModularHome") != "true":
                api("POST", prefs_path(u["Id"]), with_modular_home(prefs))
                print(f"{u['Name']}: useModularHome on")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
