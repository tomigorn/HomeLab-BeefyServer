#!/usr/bin/env python3
"""Exit 0 when nobody is playing in Jellyfin, else 1 with the count (guard of the weekly rebuild, Docker/_global/rebuild-local.conf)."""
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))


def verdict(sessions):
    if sessions is None:
        return 1, "Jellyfin sessions unreadable"
    playing = sum(1 for s in sessions if s.get("NowPlayingItem"))
    return (1, f"{playing} playing in Jellyfin") if playing else (0, "no playback")


def main():
    from jellyfin_api import api
    try:
        sessions = api("GET", "/Sessions?activeWithinSeconds=960")
    except Exception:  # noqa: BLE001
        sessions = None
    rc, msg = verdict(sessions)
    print(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
