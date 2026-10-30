#!/usr/bin/env python3
"""Moonfin media bar per user: a BoxSet "For you" per Moonfin user (tags hss-media-bar + hss-media-bar-<userId>) with up to 40 random real titles the user has not touched: unwatched films (not played, no resume position) and series with no episode played or started, backdrop required, no trending placeholders.
Every other user's policy blocks the owner tag, so a set is visible only to its owner. The user's Moonfin media bar reads it (source random, no libraries, collection = the set); a user's own media bar choice (other source, libraries or collections) stays.
Members still eligible stay, watched ones leave, the set is filled up; once a day a fresh random draw. Nothing eligible leaves the set unchanged. Sets of gone users are deleted.
Idempotent. Usage: media-bar.py [--dry-run | --check]  (--check exits 1 when a set, a block or a user's bar setting is missing)"""
import datetime
import json
import os
import pathlib
import random
import sys
import urllib.error
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api as jf_api, file_lock  # noqa: E402
from trending_model import LIBRARIES  # noqa: E402

NAME = "For you"
SORT = f"99 {NAME}"
TAG = "hss-media-bar"
SIZE = 40
TRENDING_PATH = "/config/trending/"
PROFILES = ("global", "desktop", "mobile", "tv")
KEYS = ("mediaBarSourceType", "mediaBarLibraryIds", "mediaBarCollectionIds")
STATE = pathlib.Path.home() / ".local" / "state" / "jellyfin-media-bar.json"
# Shared with moonbase.py: both write Moonfin user settings.
LOCK = pathlib.Path.home() / ".local" / "state" / "jellyfin-moonbase.lock"


def owner_tag(uid):
    return f"{TAG}-{uid}"


def real(item):
    return not (item.get("Path") or "").startswith(TRENDING_PATH) and bool(item.get("BackdropImageTags"))


def eligible(movies, shows, started):
    out = [m["Id"] for m in movies if real(m) and not (m.get("UserData") or {}).get("Played") and not (m.get("UserData") or {}).get("PlaybackPositionTicks")]
    return out + [s["Id"] for s in shows if real(s) and s["Id"] not in started]


def choose(have, pool, size, rng, resample):
    if resample:
        return rng.sample(pool, min(size, len(pool)))
    keep = [i for i in have if i in pool][:size]
    rest = [i for i in pool if i not in keep]
    return keep + rng.sample(rest, min(size - len(keep), len(rest)))


def blocked_tags(have, uid, owners):
    foreign = [t for t in have or [] if not t.startswith(TAG + "-")]
    return foreign + [owner_tag(o) for o in sorted(owners) if o != uid]


def settings_change(settings, sid, ours, libraries):
    """{profile: bar keys} for profiles still on a default bar (old Latest default, Moonfin's random, or one of our sets); profiles with an own choice stay."""
    want = {"mediaBarSourceType": "random", "mediaBarLibraryIds": [], "mediaBarCollectionIds": [sid]}
    out = {}
    for name in PROFILES:
        profile = settings.get(name)
        if not isinstance(profile, dict):
            if name != "global":
                continue
            profile = {}
        elif name != "global" and not any(k in profile for k in KEYS):
            continue
        libs, colls = profile.get(KEYS[1]) or [], profile.get(KEYS[2]) or []
        managed = profile.get(KEYS[0]) in (None, "recentlyAdded", "random") and (not libs or set(libs) == set(libraries)) and set(colls) <= set(ours)
        if managed and any(profile.get(k) != v for k, v in want.items()):
            out[name] = dict(want)
    return out


def library_folders(api):
    return [f for f in api("GET", "/Library/VirtualFolders") if f.get("CollectionType") in ("movies", "tvshows") and f.get("Name") not in LIBRARIES.values()]


def pool(api, uid, folders):
    movies, shows, started = [], [], set()
    fields = "Recursive=true&Fields=Path&EnableImageTypes=Backdrop&ImageTypeLimit=1"
    for f in folders:
        lib = f["ItemId"]
        if f["CollectionType"] == "movies":
            movies += api("GET", f"/Items?userId={uid}&ParentId={lib}&IncludeItemTypes=Movie&Filters=IsUnplayed&{fields}")["Items"]
            continue
        shows += api("GET", f"/Items?userId={uid}&ParentId={lib}&IncludeItemTypes=Series&{fields}")["Items"]
        for flt in ("IsPlayed", "IsResumable"):
            eps = api("GET", f"/Items?userId={uid}&ParentId={lib}&IncludeItemTypes=Episode&Recursive=true&Filters={flt}&Fields=SeriesId&EnableImages=false&EnableUserData=false")["Items"]
            started |= {e.get("SeriesId") for e in eps}
    return eligible(movies, shows, started)


def user_settings(api, uid):
    try:
        return api("GET", f"/Moonfin/Settings/{uid}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def load_state(path):
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def members(api, sid, uid):
    return [i["Id"] for i in api("GET", f"/Items?ParentId={sid}&userId={uid}&EnableImages=false&EnableUserData=false")["Items"]]


def run(argv, api=jf_api, state_path=STATE, today=None, rng=None, log=print):
    check, dry = "--check" in argv, "--dry-run" in argv
    write = not (check or dry)
    today = today or datetime.date.today().isoformat()
    rng = rng or random.SystemRandom()
    state = load_state(state_path)
    created = dict(state.get("created") or {})
    history = {k: list(v) for k, v in (state.get("sets") or {}).items()}
    resample = state.get("day") != today
    users = api("GET", "/Users")
    uids = [u["Id"] for u in users]
    folders = library_folders(api)
    libraries = [f["ItemId"] for f in folders]
    sets, extra = {}, []
    for box in api("GET", "/Items?Recursive=true&IncludeItemTypes=BoxSet&Tags=" + TAG + "&Fields=Tags&EnableImages=false&EnableUserData=false")["Items"]:
        owner = next((t[len(TAG) + 1:] for t in box.get("Tags") or [] if t.startswith(TAG + "-")), None)
        if (owner is None or owner in sets) and box["Id"] not in created.values():
            extra.append(box["Id"])
        if owner and owner not in sets:
            sets[owner] = box["Id"]
    for uid, bid in created.items():
        if uid not in sets and uid in uids:
            sets[uid] = bid
    problems, changes = [], []
    gone = [uid for uid in sets if uid not in uids]
    plans = {}
    for uid in uids:
        settings = user_settings(api, uid)
        if not settings:
            continue
        titles = pool(api, uid, folders)
        if uid not in sets and not titles:
            continue
        plans[uid] = (settings, titles)
    owners = (set(sets) - set(gone)) | set(plans)
    for user in users:
        uid = user["Id"]
        policy = api("GET", f"/Users/{uid}")["Policy"]
        tags = blocked_tags(policy.get("BlockedTags"), uid, owners)
        if tags != (policy.get("BlockedTags") or []):
            problems.append(f"user {uid}: blocks differ")
            if write:
                api("POST", f"/Users/{uid}/Policy", {**policy, "BlockedTags": tags})
    for uid in gone:
        problems.append(f"set of gone user {uid}")
        if write:
            api("DELETE", f"/Items/{sets.pop(uid)}")
            created.pop(uid, None)
    for uid, (settings, titles) in plans.items():
        sid = sets.get(uid)
        if sid is None:
            problems.append(f"user {uid}: no set")
            if not write:
                continue
            want = choose([], titles, SIZE, rng, True)
            sid = api("POST", f"/Collections?name={urllib.parse.quote(NAME + ' ' + uid[:8])}&ids={','.join(want)}&isLocked=true")["Id"]
            sets[uid] = created[uid] = sid
            save_state(state_path, {**state, "created": created})
            changes.append(f"user {uid}: created")
        try:
            item = api("GET", f"/Items/{sid}?userId={uid}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            problems.append(f"user {uid}: set {sid} gone, recreated next run")
            created.pop(uid, None)
            continue
        tags = sorted(set(item.get("Tags") or []) | {TAG, owner_tag(uid)})
        if item.get("Name") != NAME or sorted(item.get("Tags") or []) != tags or item.get("ForcedSortName") != SORT or item.get("LockData") is not True:
            problems.append(f"user {uid}: set metadata")
            if write:
                api("POST", f"/Items/{sid}", {**item, "Name": NAME, "Tags": tags, "ForcedSortName": SORT, "LockData": True})
        have = members(api, sid, uid)
        if titles and not check:
            want = choose(have, titles, SIZE, rng, resample)
            fresh, stale = [i for i in want if i not in have], [i for i in have if i not in want]
            if fresh or stale:
                changes.append(f"user {uid}: +{len(fresh)} -{len(stale)}")
                if write:
                    if fresh:
                        api("POST", f"/Collections/{sid}/Items?ids={','.join(fresh)}")
                    if stale:
                        api("DELETE", f"/Collections/{sid}/Items?ids={','.join(stale)}")
        elif not titles:
            log(f"user {uid}: nothing eligible, set unchanged")
        ours = set(history.get(uid) or []) | {sid}
        history[uid] = sorted(ours)
        change = settings_change(settings, sid, ours, libraries)
        if change:
            problems.append(f"user {uid}: bar setting")
            if write:
                api("POST", f"/Moonfin/Settings/{uid}", {"settings": {"syncEnabled": settings.get("syncEnabled", True), **change}, "clientId": "hss-media-bar", "mergeMode": "merge"})
        created.pop(uid, None)
    for bid in extra:
        log(f"untracked set {bid} left alone")
    if check:
        log("ok " + f"{len(plans)} users" if not problems else "drift: " + "; ".join(problems))
        return 1 if problems else 0
    done = problems + changes
    if dry:
        log("would change: " + "; ".join(done) if done else "unchanged")
        return 0
    new_state = {**state, "created": created, "sets": history, "day": today}
    if new_state != state:
        save_state(state_path, new_state)
    log("changed: " + "; ".join(done) if done else "unchanged")
    return 0


def main(argv):
    if any(a not in ("--dry-run", "--check") for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if argv:
        return run(argv)
    with file_lock(LOCK, timeout=600, step=1):
        return run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
