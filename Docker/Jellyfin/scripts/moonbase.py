#!/usr/bin/env python3
"""Configures the Moonbase plugin (Moonfin apps): safe server settings and the default home layout for every user (the web home order).
Users whose stored value is still the previous default follow the new default; the app's own default of a key new here is replaced once per user, profile and key (markers in STATE_FILE); their own choices stay.
A lost (after markers were written: sibling .written), corrupt or odd state file counts every marker as done. A failed user write is retried next run against the defaults it was compared with (state "pending"); with a lost state the markers are kept for it too, so no app default is adopted later. An empty list and null count as equal. One run at a time (sibling .lock).
Idempotent. Usage: moonbase.py [--dry-run | --check]  (--check exits 1 when the config or a user differs)"""
import copy
import json
import pathlib
import re
import sys
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api, file_lock  # noqa: E402
from trending_model import COMING, HOME, LIBRARIES  # noqa: E402

PLUGIN_ID = "8c5d0e914f2a4b6d9e3f1a7c8d9e0f2b"
SERVER_URL = "https://jellyfin.holy-grail.ch"
TOP_MOVIES, TOP_SHOWS = (name for _, _, name in HOME)
GEMS = "Hidden Gems"
END_ROWS = ("Harry Potter Collection", "The Lord of the Rings Collection")
ROW_SETS = (COMING[1], TOP_SHOWS, TOP_MOVIES, GEMS, *END_ROWS)
INJECTOR = "JavaScript Injector"
STUDIO_ENTRY = "Home networks"
PROFILES = ("global", "desktop", "mobile", "tv")
STATE_FILE = pathlib.Path.home() / ".local" / "state" / "jellyfin-moonbase.json"
# Moonfin's own defaults for the keys set here first: the apps write them back, so before the first run they are no choice; afterwards they may be one.
CLIENT_DEFAULTS = {"displayFavoritesRows": False, "displayStudiosRows": False, "displayGenresRows": False, "genresRowItemFilter": "both", "studiosRowSelectedIds": "", "mediaBarAutoAdvance": True}

# Seerr = our facade (limits enforced there), no push relay (third-party cloud), no server-wide Similar override, no fetch tasks without keys.
SERVER = {
    "EnableSettingsSync": True,
    "SeerrEnabled": True,
    "SeerrUrl": "http://jellyfin-popular:8099",
    "SeerrDisplayName": "Requests",
    "PublicServerUrl": None,
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
    "mediaBarAutoAdvance": True,
    "homeRowsStyle": "v2",
    "detailScreenStyle": "modern",
    "homeBackdropEnabled": True,
    "detailsBackdropEnabled": True,
    "displayCollectionsRows": True,
    "collectionsRowSortBy": "name",
    "collectionsRowSortOrder": "ascending",
    "displaySeerrRows": True,
    "seerrEnabled": True,
    "seasonalRowEnabled": False,
    "mergeContinueWatchingNextUp": False,  # the merged row dedupes by episode, not series: one show twice
    "displayFavoritesRows": True,
    "displayStudiosRows": True,
    "displayGenresRows": True,
    "displayRewatchRow": True,
    "displaySinceYouWatchedRows": True,
    "genresRowItemFilter": "movies",
}


def builtin(kind, order=0):
    return {"type": kind, "enabled": True, "order": order}


def collection(item, order=0):
    return {"kind": "pluginDynamic", "pluginSource": "collections", "type": "none", "enabled": True, "order": order,
            "serverId": SERVER_URL, "pluginSection": "collection", "pluginAdditionalData": item["Id"], "pluginDisplayText": item["Name"]}


def sections(sets):
    """The web home order; collections missing or empty for a non-admin user left out, the service and genre lists not on home."""
    full = {item["Name"]: item for item in sets if item["Count"] > 0}

    def coll(*names):
        return [collection(full[n]) for n in names if n in full]

    out = [builtin("resume"), builtin("nextup"), *coll(COMING[1]), builtin("favoritemovies"), builtin("favoriteseries"), builtin("latestmedia"),
           *coll(TOP_SHOWS), builtin("studios"), builtin("rewatch"), *coll(TOP_MOVIES), builtin("sinceyouwatched1"), *coll(GEMS), builtin("genres"), *coll(*END_ROWS)]
    for order, section in enumerate(out):
        section["order"] = order
    return out


def studio_ids(injector_config):
    """The Jellyfin studio ids of the web's studio and network cards (studio-logos.py), studios first; None without the entry."""
    script = next((s.get("Script") or "" for s in injector_config.get("CustomJavaScripts") or [] if s.get("Name") == STUDIO_ENTRY), None)
    if script is None:
        return None
    ids = []
    for var in ("HssStudios", "HssNetworks"):
        m = re.search(rf"^window\.{var} = (.*);$", script, re.M)
        for card in json.loads(m.group(1)) if m else []:
            ids += [i for i in card.get("ids") or [] if i not in ids]
    return ",".join(ids)


def wanted(config, sets, folders, studios):
    new = copy.deepcopy(config)
    new.update(SERVER)
    libraries = [f["ItemId"] for f in folders if f.get("CollectionType") in ("movies", "tvshows") and f.get("Name") not in LIBRARIES.values()]
    defaults = {**(new.get("DefaultUserSettings") or {}), **DEFAULTS, "mediaBarLibraryIds": libraries, "homeSections": sections(sets), "homeRowOrder": None}
    if studios is not None:
        defaults["studiosRowSelectedIds"] = studios
    new["DefaultUserSettings"] = defaults
    return new


def blank(value):
    return None if value in ([], None) else value


def changed(old, new):
    keys = [k for k in SERVER if old.get(k) != new.get(k)]
    od, nd = old.get("DefaultUserSettings") or {}, new.get("DefaultUserSettings") or {}
    return keys + [f"DefaultUserSettings.{k}" for k in nd if blank(od.get(k)) != blank(nd[k])]


def layout(rows):
    return [(r.get("type"), r.get("pluginSource"), r.get("pluginAdditionalData")) for r in sorted(rows or [], key=lambda r: r.get("order") or 0) if r.get("enabled", True)]


def adopt(settings, old, new, done=frozenset()):
    """{profile: {key: new default}} for stored values that are no own choice: the previous default (a layout by its enabled rows) or, unless done holds "profile:key", the app's default of a key new here."""
    out = {}
    for name in PROFILES:
        profile = settings.get(name)
        if not isinstance(profile, dict):
            continue
        change = {}
        for key, value in new.items():
            have = profile.get(key)
            if value is None or have is None or have == value:
                continue
            if key == "homeSections":
                untouched = layout(have) == layout(old.get(key))
            else:
                untouched = have == old.get(key) or (key in CLIENT_DEFAULTS and f"{name}:{key}" not in done and have == CLIENT_DEFAULTS[key])
            if untouched:
                change[key] = value
        if change:
            out[name] = change
    return out


def client_markers(settings):
    return {f"{name}:{key}" for name in PROFILES if isinstance(settings.get(name), dict) for key in CLIENT_DEFAULTS}


def load_state():
    """(state, trusted): an untrusted state (lost after markers were written, corrupt, odd shape) must not let any app default be adopted again."""
    try:
        state = json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {}, not STATE_FILE.with_suffix(".written").exists()
    except (OSError, ValueError):
        return {}, False
    if not isinstance(state, dict):
        return {}, False
    migrated, pending = state.get("migrated", {}), state.get("pending", {})
    if not isinstance(migrated, dict) or not all(isinstance(v, list) for v in migrated.values()) or not isinstance(pending, dict) or not all(isinstance(v, dict) for v in pending.values()):
        return {}, False
    return state, True


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)
    STATE_FILE.with_suffix(".written").touch()


def lists():
    """The home-row collections by name, counted as a non-admin user sees them (without a user a BoxSet counts 0)."""
    user = next(u["Id"] for u in api("GET", "/Users") if not u["Policy"]["IsAdministrator"])
    items = api("GET", "/Items?Recursive=true&IncludeItemTypes=BoxSet&SortBy=SortName&CollapseBoxSetItems=false&EnableImages=false")["Items"]
    return [{"Id": i["Id"], "Name": i["Name"], "Count": api("GET", f"/Items?userId={user}&ParentId={i['Id']}&Limit=0&EnableImages=false")["TotalRecordCount"]}
            for i in items if i["Name"] in ROW_SETS]


def injector_config():
    plugin = next((p["Id"] for p in api("GET", "/Plugins") or [] if p.get("Name") == INJECTOR), None)
    return api("GET", f"/Plugins/{plugin}/Configuration") if plugin else {}


def user_settings(uid):
    try:
        return api("GET", f"/Moonfin/Settings/{uid}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main(argv):
    if "--check" in argv or "--dry-run" in argv:
        return run(argv)
    with file_lock(STATE_FILE.with_suffix(".lock"), timeout=600, step=1):
        return run(argv)


def run(argv):
    path = f"/Plugins/{PLUGIN_ID}/Configuration"
    old = api("GET", path)
    new = wanted(old, lists(), api("GET", "/Library/VirtualFolders"), studio_ids(injector_config()))
    diff = changed(old, new)
    state, trusted = load_state()
    if not trusted:
        print(f"state {STATE_FILE} lost or unreadable: every app default counts as migrated")
    migrated, pending = dict(state.get("migrated") or {}), dict(state.get("pending") or {})
    before = {k: dict(v) for k, v in pending.items()}
    users = {}
    for user in api("GET", "/Users"):
        settings = user_settings(user["Id"])
        if not settings:
            continue
        done = set(migrated.get(user["Id"]) or []) | (set() if trusted else client_markers(settings))
        base = pending.get(user["Id"]) or old.get("DefaultUserSettings") or {}
        change = adopt(settings, base, new["DefaultUserSettings"], done)
        if change:
            users[user["Id"]] = (settings, change, base, sorted(done | client_markers(settings)))
        else:
            pending.pop(user["Id"], None)
            migrated[user["Id"]] = sorted(done | client_markers(settings))
    keys = sorted({k for _, change, _, _ in users.values() for p in change.values() for k in p})
    summary = ("config unchanged" if not diff else "config: " + ", ".join(diff)) + f"; {len(users)} user(s) to the new defaults" + (f" ({', '.join(keys)})" if keys else "")
    if "--check" in argv or "--dry-run" in argv:
        print("would change: " + summary if diff or users else "unchanged")
        return 1 if (diff or users) and "--check" in argv else 0
    failed = []
    for uid, (settings, change, base, markers) in users.items():
        try:
            api("POST", f"/Moonfin/Settings/{uid}", {"settings": {"syncEnabled": settings.get("syncEnabled", True), **change}, "clientId": "moonbase-defaults", "mergeMode": "merge"})
        except (urllib.error.URLError, OSError) as e:
            print(f"user {uid}: write failed ({type(e).__name__}), retried next run")
            failed.append(uid)
            pending[uid] = base
            if not trusted:
                migrated[uid] = markers
            continue
        pending.pop(uid, None)
        migrated[uid] = markers
    if diff:
        api("POST", path, new)
    if not trusted or migrated != (state.get("migrated") or {}) or pending != before:
        save_state({**state, "migrated": migrated, "pending": pending})
    elif migrated:
        STATE_FILE.with_suffix(".written").touch()
    print("changed: " + summary if diff or users else "unchanged")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
