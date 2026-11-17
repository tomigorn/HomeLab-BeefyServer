#!/usr/bin/env python3
"""Beta only: the classic web home follows the row plan. Home Screen Sections keeps only row 1 (Continue Watching); rows 2-20 come from the injector entry "Beta rows" (rows.py, beta-rows.js).
Prod's home injector entries are switched off in the beta: their rows are not in the plan, and Coming soon cannot fill there (the beta's network has no path to Radarr/Sonarr, by design).
Usage: web_home.py [--check]  (--check exits 1 when something would change)"""
import copy
import pathlib
import sys

from beta_api import api

# Row 1 only (Next Up is not in the plan); Favourites and New on the Server come from beta-rows.js: the plugin's My List is a playlist, not the favourites, and its Recently Added has no 30-day limit.
ORDER = ["ContinueWatching"]
OFF = ("Empty home sections", "Home layout", "Home upcoming", "Home networks")
HSS, INJECTOR = "Home Screen Sections", "JavaScript Injector"


def sections(cfg, order=ORDER):
    new = copy.deepcopy(cfg)
    rest = len(order) + 1
    for s in new.get("SectionSettings") or []:
        sid = s["SectionId"]
        s["Enabled"] = sid in order
        s["AllowUserOverride"] = False
        if sid in order:
            s["OrderIndex"] = order.index(sid) + 1
        else:
            s["OrderIndex"] = rest
            rest += 1
    new["SectionSettings"] = sorted(new.get("SectionSettings") or [], key=lambda s: s["OrderIndex"])
    return new


def injector(cfg):
    new = copy.deepcopy(cfg)
    for s in new.get("CustomJavaScripts") or []:
        if s["Name"] in OFF:
            s["Enabled"] = False
    return new


def plugin(name):
    return next(p["Id"] for p in api("GET", "/Plugins") if p["Name"] == name)


def main(argv):
    if any(a != "--check" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    changed = []
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "Jellyfin" / "scripts"))
    import jellyfin_api
    for name, fix in ((HSS, sections), (INJECTOR, injector)):
        with jellyfin_api.injector_lock():
            path = f"/Plugins/{plugin(name)}/Configuration"
            old = api("GET", path)
            new = fix(old)
            if new != old:
                changed.append(name)
                if not argv:
                    api("POST", path, new)
    print(("would change: " if argv else "changed: ") + ", ".join(changed) if changed else "unchanged")
    return 1 if changed and argv else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
