#!/usr/bin/env python3
"""Nightly sweep: unmarks every trending placeholder for every user (films, versions, series, seasons and episodes of "Trending items" / "Trending shows" plus the TV hook index): DELETE /UserPlayedItems clears Played, play count, last played and position (12.4 ignores Played in POST /UserItems/{id}/UserData).
Idempotent, prints counts only; a user that fails (HTTP error, timeout) is counted and skipped, exit 1 at the end. Usage: trending-unwatched.py [--dry-run | --check]  (--check: number of placeholders still played, exit 1 when any)"""
import json
import pathlib
import sys
import urllib.error
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from trending_model import LIBRARY, SHOWS_LIBRARY, library_id  # noqa: E402

INDEX = pathlib.Path("/srv/appdata/jellyfin-popular/trending-index.json")
TYPES = "Movie,Series,Season,Episode"
CHUNK = 60


def marked(ud):
    return bool(ud.get("Played") or ud.get("PlaybackPositionTicks") or ud.get("PlayCount"))


def index_ids(path):
    try:
        return sorted(json.loads(pathlib.Path(path).read_text())["items"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def user_items(api, uid, libs, extra):
    seen = {}
    for lib in libs:
        for i in api("GET", f"/Items?ParentId={lib}&Recursive=true&IncludeItemTypes={TYPES}&userId={uid}&EnableImages=false")["Items"]:
            seen[i["Id"]] = i.get("UserData") or {}
    rest = [i for i in extra if i not in seen]
    for n in range(0, len(rest), CHUNK):
        for i in api("GET", f"/Items?Ids={','.join(rest[n:n + CHUNK])}&userId={uid}&EnableImages=false")["Items"]:
            seen[i["Id"]] = i.get("UserData") or {}
    for iid in [i for i in rest if i not in seen]:
        # alternate versions are missing from Ids= lists
        try:
            seen[iid] = api("GET", f"/Items/{urllib.parse.quote(iid)}?userId={uid}").get("UserData") or {}
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    return seen


def main(argv, api=None, index_file=INDEX, out=sys.stdout):
    if argv not in ([], ["--dry-run"], ["--check"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if api is None:
        import jellyfin_api
        api = jellyfin_api.api
    folders = api("GET", "/Library/VirtualFolders")
    libs = [library_id(folders, name) for name in (LIBRARY, SHOWS_LIBRARY)]
    if not all(libs):
        print("trending library missing", file=sys.stderr)
        return 1
    extra = index_ids(index_file)
    users = api("GET", "/Users")
    played = reset = failed = 0
    for u in users:
        uid = u["Id"].replace("-", "")
        try:
            for iid, ud in user_items(api, uid, libs, extra).items():
                played += ud.get("Played") is True
                if not marked(ud):
                    continue
                if argv == []:
                    api("DELETE", f"/UserPlayedItems/{urllib.parse.quote(iid)}?userId={uid}")
                reset += 1
        except OSError as e:
            # HTTPError, URLError and timeouts
            failed += 1
            print(f"user failed {type(e).__name__}", file=sys.stderr)
    tail = f", {failed} users failed" if failed else ""
    if argv == ["--check"]:
        print(f"{played} placeholders played{tail}", file=out)
        return 1 if played or failed else 0
    print(f"{len(users)} users: {reset} marked placeholders {'would be reset' if argv else 'reset'}{tail}", file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
