#!/usr/bin/env python3
"""Configures the Moonbase plugin (Moonfin apps): safe server settings and the default home layout for every user.
Idempotent. Usage: moonbase.py [--dry-run | --check]  (--check exits 1 when the config differs)"""
import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api  # noqa: E402
from trending_model import COMING, HOME, LIBRARY, TAG  # noqa: E402

PLUGIN_ID = "8c5d0e914f2a4b6d9e3f1a7c8d9e0f2b"
SERVER_URL = "https://jellyfin.holy-grail.ch"
LEADING = [name for _, _, name in HOME] + [COMING[1]]

# Seerr off (bypasses our limits), no push relay (third-party cloud), no server-wide Similar override, no fetch tasks without keys.
SERVER = {
    "EnableSettingsSync": True,
    "SeerrEnabled": False,
    "RecommendationsProviderEnabled": False,
    "PushRelayUrl": "",
    "ImdbListsEnabled": False,
    "MdblistOfficialListsEnabled": False,
    "StudioLogosEnabled": False,
}

DEFAULTS = {
    "navbarPosition": "left",
    "mediaBarMode": "moonfin",
    "mediaBarSourceType": "random",
    "homeRowsStyle": "v2",
    "detailScreenStyle": "modern",
    "homeBackdropEnabled": True,
    "detailsBackdropEnabled": True,
    "displayCollectionsRows": True,
    "collectionsRowSortBy": "name",
    "collectionsRowSortOrder": "ascending",
    "displaySeerrRows": False,
    "seasonalRowEnabled": False,
    "mergeContinueWatchingNextUp": False,  # the merged row dedupes by episode, not series: one show twice
}


def builtin(kind, order):
    return {"type": kind, "enabled": True, "order": order}


def collection(item, order):
    return {"kind": "pluginDynamic", "pluginSource": "collections", "type": "none", "enabled": True, "order": order,
            "serverId": SERVER_URL, "pluginSection": "collection", "pluginAdditionalData": item["Id"], "pluginDisplayText": item["Name"]}


def sections(lists):
    """Continue Watching, Next Up, the leading lists, Recently added, then the other lists; empty lists left out."""
    full = [item for item in lists if item["Count"] > 0]
    lead = [item for item in full if item["Name"] in LEADING]
    rest = [item for item in full if item["Name"] not in LEADING]
    out = [builtin("resume", 0), builtin("nextup", 1)]
    out += [collection(item, 0) for item in lead]
    out.append(builtin("latestmedia", 0))
    out += [collection(item, 0) for item in rest]
    for order, section in enumerate(out):
        section["order"] = order
    return out


def wanted(config, lists, folders):
    new = copy.deepcopy(config)
    new.update(SERVER)
    libraries = [f["ItemId"] for f in folders if f.get("CollectionType") in ("movies", "tvshows") and f.get("Name") != LIBRARY]
    new["DefaultUserSettings"] = {**(new.get("DefaultUserSettings") or {}), **DEFAULTS, "mediaBarLibraryIds": libraries,
                                  "homeSections": sections(lists), "homeRowOrder": None}
    return new


def changed(old, new):
    keys = [k for k in SERVER if old.get(k) != new.get(k)]
    od, nd = old.get("DefaultUserSettings") or {}, new.get("DefaultUserSettings") or {}
    return keys + [f"DefaultUserSettings.{k}" for k in nd if od.get(k) != nd[k]]


def lists():
    """Tagged list collections in SortName order, counted as a non-admin user sees them (without a user a BoxSet counts 0)."""
    user = next(u["Id"] for u in api("GET", "/Users") if not u["Policy"]["IsAdministrator"])
    items = api("GET", f"/Items?Recursive=true&IncludeItemTypes=BoxSet&Tags={TAG}&SortBy=SortName&CollapseBoxSetItems=false&EnableImages=false")["Items"]
    return [{"Id": i["Id"], "Name": i["Name"], "Count": api("GET", f"/Items?userId={user}&ParentId={i['Id']}&Limit=0&EnableImages=false")["TotalRecordCount"]} for i in items]


def main(argv):
    path = f"/Plugins/{PLUGIN_ID}/Configuration"
    old = api("GET", path)
    new = wanted(old, lists(), api("GET", "/Library/VirtualFolders"))
    diff = changed(old, new)
    if "--check" in argv or "--dry-run" in argv:
        print("unchanged" if not diff else "would change: " + ", ".join(diff))
        return 1 if diff and "--check" in argv else 0
    if diff:
        api("POST", path, new)
    print("unchanged" if not diff else "changed: " + ", ".join(diff))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
