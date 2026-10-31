#!/usr/bin/env python3
"""Live check of "Remove from Continue watching" (README "Remove from Continue watching") on the canary-test user only: a film and a just-started episode get a resume point, the web's write (PlaybackPositionTicks 0) must drop them from Resume and change nothing else (play count, last played, played, favourite), Next Up keeps the series, undo (old position) brings them back. The canary's data on these items is blank afterwards, also on failure.
Usage: continue-remove-check.py  (exit 0 = ok, 1 = FAIL)"""
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api as live_api  # noqa: E402

CANARY = "canary-test"
FILM = "Nosferatu"
TICKS = 18000000000
FIELDS = ("PlaybackPositionTicks", "PlayCount", "IsFavorite", "Played", "LastPlayedDate")


class Fail(Exception):
    pass


def main(argv, api=live_api, out=sys.stdout):
    if argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    users = [u["Id"] for u in api("GET", "/Users") or [] if u.get("Name") == CANARY]
    if not users:
        print(f"FAIL: no {CANARY} user", file=out)
        return 1
    uid = users[0]
    q = f"userId={uid}"

    def ud(i):
        d = api("GET", f"/UserItems/{i}/UserData?{q}") or {}
        return {k: d.get(k) for k in FIELDS}

    def write(i, body):
        api("POST", f"/UserItems/{i}/UserData?{q}", body)

    def resume():
        return {i["Id"] for i in api("GET", f"/UserItems/Resume?{q}&Limit=100&EnableImages=false")["Items"]}

    def next_up():
        return {i.get("SeriesId") for i in api("GET", f"/Shows/NextUp?{q}&Limit=100&EnableImages=false")["Items"]}

    def blank(i):
        api("DELETE", f"/UserPlayedItems/{i}?{q}")
        write(i, {"IsFavorite": False})

    film = (api("GET", f"/Items?{q}&Recursive=true&IncludeItemTypes=Movie&SearchTerm={FILM}&Limit=1")["Items"] or [None])[0]
    second = (api("GET", f"/Items?{q}&Recursive=true&IncludeItemTypes=Episode&ParentIndexNumber=1&IndexNumber=2&IsMissing=false&Limit=1&EnableImages=false")["Items"] or [None])[0]
    if not film or not second:
        print("FAIL: no test film or series", file=out)
        return 1
    series = second["SeriesId"]
    eps = [e for e in api("GET", f"/Shows/{series}/Episodes?{q}&IsMissing=false&EnableImages=false")["Items"] if e.get("ParentIndexNumber") == 1]
    ep = sorted(eps, key=lambda e: e.get("IndexNumber") or 0)[0]["Id"]
    items = (film["Id"], ep)
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        for i in items:
            blank(i)
        write(film["Id"], {"PlaybackPositionTicks": TICKS, "PlayCount": 1, "LastPlayedDate": now, "IsFavorite": True})
        write(ep, {"PlaybackPositionTicks": TICKS // 3, "PlayCount": 1, "LastPlayedDate": now})
        for i in items:
            before = ud(i)
            if i not in resume():
                raise Fail(f"{i} seeded but not in Resume")
            if i == ep and series not in next_up():
                raise Fail("seeded episode's series not in Next Up")
            write(i, {"PlaybackPositionTicks": 0})
            if i in resume():
                raise Fail(f"{i} still in Resume after the position write")
            after = ud(i)
            if after != dict(before, PlaybackPositionTicks=0):
                raise Fail(f"{i} changed more than the position: {before} -> {after}")
            if i == ep and series not in next_up():
                raise Fail("removing the episode hid its series from Next Up")
            write(i, {"PlaybackPositionTicks": before["PlaybackPositionTicks"]})
            if i not in resume() or ud(i) != before:
                raise Fail(f"{i} undo did not restore it")
    except Fail as e:
        print(f"FAIL: {e}", file=out)
        return 1
    finally:
        for i in items:
            blank(i)
    print("ok film and episode: only the resume point changes, undo restores, Next Up keeps the series", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
