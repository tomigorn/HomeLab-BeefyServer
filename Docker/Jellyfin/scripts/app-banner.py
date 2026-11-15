#!/usr/bin/env python3
"""Upsert app-banner/app-banner.js (phone browsers: "get the Moonfin app" with a store link) into the JavaScript Injector plugin config.

  app-banner.py
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("tracker_link", HERE / "tracker-link.py")
_tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tl)

NAME = "Moonfin app banner"
SOURCE = HERE.parent / "app-banner" / "app-banner.js"


def main(argv, api=None, out=sys.stdout):
    return _tl.main(argv, api=api, out=out, name=NAME, source_file=SOURCE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
