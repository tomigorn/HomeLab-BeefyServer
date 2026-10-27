#!/usr/bin/env python3
"""Upsert tracker-link/rate-in-tracker.js into the JavaScript Injector plugin config.

  tracker-link.py
"""
import pathlib
import sys

PLUGIN = "JavaScript Injector"
NAME = "Rate in Tracker"
SOURCE = pathlib.Path(__file__).resolve().parent.parent / "tracker-link" / "rate-in-tracker.js"


def upsert_script(config, name, source):
    entry = {"Name": name, "Script": source, "Enabled": True, "RequiresAuthentication": True}
    scripts = list(config.get("CustomJavaScripts") or [])
    if any(s.get("Name") == name for s in scripts):
        scripts = [entry if s.get("Name") == name else s for s in scripts]
    else:
        scripts.append(entry)
    return {**config, "CustomJavaScripts": scripts}


def main(argv, api=None, source=None, out=sys.stdout, name=NAME, source_file=SOURCE):
    if api is None:
        import jellyfin_api
        api = jellyfin_api.api
    if argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        plugin_id = next(p["Id"] for p in api("GET", "/Plugins") if p["Name"] == PLUGIN)
    except StopIteration:
        print(f"plugin {PLUGIN} not installed", file=sys.stderr)
        return 1
    path = f"/Plugins/{plugin_id}/Configuration"
    source = source_file.read_text() if source is None else source
    api("POST", path, upsert_script(api("GET", path), name, source))
    print(f"upserted {name}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
