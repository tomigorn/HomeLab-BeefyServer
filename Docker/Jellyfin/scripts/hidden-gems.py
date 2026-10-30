#!/usr/bin/env python3
"""Keeps the collection "Hidden Gems": the newest-added films and series of the real libraries with a community rating of 7.5 or more, 20 titles, newest first.
Only the BoxSet tagged hss-hidden-gems is touched. Idempotent. Usage: hidden-gems.py [--dry-run]"""
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jellyfin_api  # noqa: E402

NAME = "Hidden Gems"
TAG = "hss-hidden-gems"
SIZE = 20
MIN_RATING = 7.5
TRENDING_PATH = "/config/trending/"


def pick(items, size=SIZE):
    real = [i for i in items if not (i.get("Path") or "").startswith(TRENDING_PATH) and (i.get("CommunityRating") or 0) >= MIN_RATING]
    real.sort(key=lambda i: i.get("DateCreated") or "", reverse=True)
    return [i["Id"] for i in real[:size]]


def order_steps(have, want):
    """(drop, add): Jellyfin appends new members, so everything after the common head is removed and added again in order."""
    keep = 0
    while keep < min(len(have), len(want)) and have[keep] == want[keep]:
        keep += 1
    return have[keep:], want[keep:]


def main(argv, api=None):
    if any(a != "--dry-run" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    api = api or jellyfin_api.api
    dry = "--dry-run" in argv
    admin = next(u["Id"] for u in api("GET", "/Users") if u["Policy"]["IsAdministrator"])
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
    changes = 0
    item = api("GET", f"/Items/{sid}?userId={admin}")
    tags = list(item.get("Tags") or [])
    if TAG not in tags or item.get("DisplayOrder") != "Default" or item.get("LockData") is not True or item.get("Name") != NAME:
        changes += 1
        if not dry:
            api("POST", f"/Items/{sid}", {**item, "Name": NAME, "Tags": tags + [TAG] if TAG not in tags else tags, "DisplayOrder": "Default", "LockData": True})
    have = [i["Id"] for i in api("GET", f"/Items?ParentId={sid}&userId={admin}&EnableImages=false&EnableUserData=false")["Items"]]
    drop, add = order_steps(have, want)
    if drop or add:
        changes += 1
        fresh = [i for i in add if i not in have]
        lead = fresh if add[:len(fresh)] == fresh else []
        if not dry:
            # Titles new to the set go in before the rest is dropped, so the set is never empty on the way.
            if lead:
                api("POST", f"/Collections/{sid}/Items?ids={','.join(lead)}")
            if drop:
                api("DELETE", f"/Collections/{sid}/Items?ids={','.join(drop)}")
            if add[len(lead):]:
                api("POST", f"/Collections/{sid}/Items?ids={','.join(add[len(lead):])}")
    verb = "would change" if dry else "changed"
    print(f"{NAME}: {verb} ({len(want)} titles, -{len(drop)} +{len(add)})" if changes else f"{NAME}: unchanged ({len(want)} titles)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
