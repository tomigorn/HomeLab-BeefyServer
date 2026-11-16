#!/usr/bin/env python3
"""Franchise BoxSets on the TMDB collection of their members: an automatic set can match the wrong TMDB collection by name (LOTR got 1173608 "The Making of", X Collection got 1083423 "X-Deal"); when every member names one other collection, the set takes that id, its overview is cleared and its metadata and art refreshed.
Idempotent; nightly in jellyfin-studio-logos.service after the library scan. Usage: collection-ids.py [--dry-run | --check]  (--check exits 1 when a set would change)"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api  # noqa: E402

REFRESH = "MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=false&ReplaceAllImages=true"


def plan(sets, members):
    """[(set id, name, current TMDB id, members' TMDB collection id)] for sets whose members agree on another collection."""
    out = []
    for s in sets:
        current = (s.get("ProviderIds") or {}).get("Tmdb")
        if not current:
            continue
        ids = {m["ProviderIds"]["TmdbCollection"] for m in members.get(s["Id"], []) if (m.get("ProviderIds") or {}).get("TmdbCollection")}
        if len(ids) == 1 and current not in ids:
            out.append((s["Id"], s["Name"], current, ids.pop()))
    return out


def fix(call, admin, set_id, tmdb):
    item = call("GET", f"/Users/{admin}/Items/{set_id}")
    call("POST", f"/Items/{set_id}", {**item, "ProviderIds": {**(item.get("ProviderIds") or {}), "Tmdb": tmdb}, "Overview": ""})
    call("POST", f"/Items/{set_id}/Refresh?{REFRESH}")


def main(argv):
    preview = "--check" in argv or "--dry-run" in argv
    admin = next(u["Id"] for u in api("GET", "/Users") if u["Policy"]["IsAdministrator"])
    sets = api("GET", f"/Users/{admin}/Items?IncludeItemTypes=BoxSet&Recursive=true&Fields=ProviderIds")["Items"]
    members = {s["Id"]: api("GET", f"/Users/{admin}/Items?ParentId={s['Id']}&Fields=ProviderIds")["Items"] for s in sets if (s.get("ProviderIds") or {}).get("Tmdb")}
    changes = plan(sets, members)
    for set_id, name, old, new in changes:
        print(f"{name}: TMDB {old} -> {new}{'' if preview else ' (fixed)'}")
        if not preview:
            fix(api, admin, set_id, new)
    print(f"{len(changes)} set{'' if len(changes) == 1 else 's'} {'would change' if preview else 'changed'}")
    return 1 if changes and "--check" in argv else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
