#!/usr/bin/env python3
"""Upsert download-choice/download-choice.js (desktop browser and Jellyfin Media Player: Download asks "Full file" or "Phone version") into the JavaScript Injector plugin config.

  download-choice.py
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("tracker_link", HERE / "tracker-link.py")
tracker_link = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tracker_link)

NAME = "Download choice"
SOURCE = HERE.parent / "download-choice" / "download-choice.js"


def main(argv, api=None, out=sys.stdout):
    if argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return tracker_link.main([], api=api, out=out, name=NAME, source_file=SOURCE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
