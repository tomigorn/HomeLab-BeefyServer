#!/usr/bin/env python3
"""Upsert the home scripts into the JavaScript Injector plugin config: home-placeholders/empty-sections.js (empty Home Screen Sections keep their heading, our rows) and home-layout.js (row groups, My List band, Because you watched, Coming soon).

  home-placeholders.py
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SOURCES = {
    "Empty home sections": HERE.parent / "home-placeholders" / "empty-sections.js",
    "Home layout": HERE.parent / "home-placeholders" / "home-layout.js",
}

_spec = importlib.util.spec_from_file_location("tracker_link", HERE / "tracker-link.py")
_tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tl)


def main(argv, api=None, sources=None, out=sys.stdout):
    if argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    for name, path in SOURCES.items():
        code = _tl.main([], api=api, source=(sources or {}).get(name), out=out, name=name, source_file=path)
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
