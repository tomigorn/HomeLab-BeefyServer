#!/usr/bin/env python3
"""Keeps the collection "Hidden Gems": the newest-added films and series of the real libraries with a community rating of 7.5 or more, 20 titles, newest first.
Only the BoxSet tagged hss-hidden-gems is touched; its Moonfin detail order (collection_order.py) is written for every user, its poster and backdrop (trending_art.py, gem mark, first posters fanned) uploaded when they change; grid sort title "91 Hidden Gems". Idempotent. Usage: hidden-gems.py [--dry-run]"""
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import collection_order  # noqa: E402
import jellyfin_api  # noqa: E402
import trending_art as art  # noqa: E402

NAME = "Hidden Gems"
TAG = "hss-hidden-gems"
SIZE = 20
MIN_RATING = 7.5
TRENDING_PATH = "/config/trending/"
# Grid position right after Coming soon (trending_model.COMING_SORT), before the real collections.
SORT = f"91 {NAME}"
STATE = pathlib.Path.home() / ".local" / "state" / "jellyfin-hidden-gems.json"
ORIGINALS = pathlib.Path("/srv/appdata/jellyfin/trending/originals")


def pick(items, size=SIZE):
    real = [i for i in items if not (i.get("Path") or "").startswith(TRENDING_PATH) and (i.get("CommunityRating") or 0) >= MIN_RATING]
    real.sort(key=lambda i: i.get("DateCreated") or "", reverse=True)
    return [i["Id"] for i in real[:size]]


def steps(have, want):
    """[(method, ids)] to turn the members have into want: new titles first, then stale ones out, then the order; never empty on the way (Jellyfin appends new members), and nothing at all without titles."""
    if not want:
        return []
    out = []
    fresh = [i for i in want if i not in have]
    stale = [i for i in have if i not in want]
    if fresh:
        out.append(("POST", fresh))
    if stale:
        out.append(("DELETE", stale))
    cur = [i for i in have if i in want] + fresh
    keep = 0
    while keep < len(cur) and cur[keep] == want[keep]:
        keep += 1
    if keep == len(want):
        return out
    if keep:
        return out + [("DELETE", want[keep:]), ("POST", want[keep:])]
    for i in want:
        out += [("DELETE", [i]), ("POST", [i])]
    return out


def moonfin_order(users, orders, dry):
    collection_order.sync("gems", users, orders, dry, print)


def image(raw, iid, kind, originals):
    """A member's image: the kept original of a marked poster, else Jellyfin's; None when it has none."""
    if kind == "Primary" and (originals / f"{iid}.jpg").is_file():
        return (originals / f"{iid}.jpg").read_bytes()
    size = "maxHeight=900" if kind == "Primary" else "maxWidth=1920"
    try:
        return raw("GET", f"/Items/{iid}/Images/{kind}?{size}", None, "image/jpeg")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def artwork(raw, sid, want, dry, state, originals):
    """Primary poster (gem mark, first 3 posters fanned) and Backdrop (first title's backdrop) of the list, uploaded when either changed. Returns 1 when uploaded."""
    posters = [p for p in (image(raw, i, "Primary", originals) for i in want[:3]) if p]
    back = next((b for b in (image(raw, i, "Backdrop/0", originals) for i in want[:3]) if b), None)
    poster = art.collection_poster("gems", NAME, None, posters)
    backdrop = art.collection_backdrop("gems", NAME, None, back, posters)
    digest = hashlib.sha256(poster + backdrop).hexdigest()
    try:
        saved = json.loads(state.read_text())
    except (OSError, ValueError):
        saved = {}
    if saved.get("art") == digest and saved.get("set") == sid:
        return 0
    if dry:
        print(f"would upload art for {NAME}")
        return 1
    raw("POST", f"/Items/{sid}/Images/Primary", base64.b64encode(poster), "image/jpeg")
    raw("POST", f"/Items/{sid}/Images/Backdrop/0", base64.b64encode(backdrop), "image/jpeg")
    state.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.with_name(f".{state.name}.tmp")
    tmp.write_text(json.dumps({"art": digest, "set": sid}))
    os.chmod(tmp, 0o600)
    os.replace(tmp, state)
    return 1


def main(argv, api=None, order=moonfin_order, raw=None, state=STATE, originals=ORIGINALS):
    if any(a != "--dry-run" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    api = api or jellyfin_api.api
    raw = raw or jellyfin_api.api_raw
    dry = "--dry-run" in argv
    users = api("GET", "/Users")
    admin = next(u["Id"] for u in users if u["Policy"]["IsAdministrator"])
    items = api("GET", f"/Items?userId={admin}&Recursive=true&IncludeItemTypes=Movie,Series&MinCommunityRating={MIN_RATING}&SortBy=DateCreated,SortName&SortOrder=Descending"
                       "&Limit=200&Fields=Path,DateCreated&EnableImages=false&EnableUserData=false")["Items"]
    want = pick(items)
    boxes = api("GET", "/Items?Recursive=true&IncludeItemTypes=BoxSet&Fields=Tags&EnableImages=false&EnableUserData=false")["Items"]
    sid = next((b["Id"] for b in boxes if TAG in (b.get("Tags") or [])), None)
    if sid is None:
        if not want:
            print("no titles, no collection")
            return 0
        if dry:
            print(f"would create {NAME} with {len(want)} titles")
            return 0
        sid = api("POST", f"/Collections?name={urllib.parse.quote(NAME)}&ids={','.join(want)}&isLocked=true")["Id"]
        print(f"created {NAME} with {len(want)} titles")
    elif not want:
        print(f"no titles: {NAME} left unchanged")
        return 0
    changes = 0
    item = api("GET", f"/Items/{sid}?userId={admin}")
    tags = list(item.get("Tags") or [])
    if TAG not in tags or item.get("DisplayOrder") != "Default" or item.get("LockData") is not True or item.get("Name") != NAME or item.get("ForcedSortName") != SORT:
        changes += 1
        if not dry:
            api("POST", f"/Items/{sid}", {**item, "Name": NAME, "Tags": tags + [TAG] if TAG not in tags else tags, "DisplayOrder": "Default", "LockData": True, "ForcedSortName": SORT})
    have = [i["Id"] for i in api("GET", f"/Items?ParentId={sid}&userId={admin}&EnableImages=false&EnableUserData=false")["Items"]]
    todo = steps(have, want)
    if todo:
        changes += 1
        if not dry:
            for method, ids in todo:
                api(method, f"/Collections/{sid}/Items?ids={','.join(ids)}")
    try:
        order([u["Id"] for u in users], {sid: want}, dry)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"{NAME}: Moonfin order failed: {type(e).__name__}")
    try:
        changes += artwork(raw, sid, want, dry, state, originals)
    except (OSError, ValueError) as e:
        print(f"{NAME}: art failed: {type(e).__name__}")
    verb = "would change" if dry else "changed"
    print(f"{NAME}: {verb} ({len(want)} titles, {len(todo)} steps)" if changes else f"{NAME}: unchanged ({len(want)} titles)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
