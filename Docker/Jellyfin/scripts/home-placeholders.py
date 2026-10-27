#!/usr/bin/env python3
"""Upsert home-placeholders/empty-sections.js (empty Home Screen Sections keep their heading) into the JavaScript Injector plugin config.

  home-placeholders.py
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
NAME = "Empty home sections"
SOURCE = HERE.parent / "home-placeholders" / "empty-sections.js"

_spec = importlib.util.spec_from_file_location("tracker_link", HERE / "tracker-link.py")
_tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tl)


def main(argv, api=None, source=None, out=sys.stdout):
    if argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return _tl.main([], api=api, source=source, out=out, name=NAME, source_file=SOURCE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
