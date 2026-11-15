#!/usr/bin/env python3
"""Upsert app-banner/app-banner.js (phone browsers: "get the Moonfin app" with a store link) and app-banner/hide-header-button.js (no Moonbase "Open Moonfin" header button) into the JavaScript Injector plugin config.

  app-banner.py
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("tracker_link", HERE / "tracker-link.py")
_tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tl)

# The header rule is public (index.html loads public.js at once): signed-in scripts arrive seconds later, after the button has shown.
ENTRIES = (
    ("Moonfin app banner", HERE.parent / "app-banner" / "app-banner.js", True),
    ("Hide Moonfin header button", HERE.parent / "app-banner" / "hide-header-button.js", False),
)


def main(argv, api=None, out=sys.stdout):
    for name, source, requires_auth in ENTRIES:
        rc = _tl.main(argv, api=api, out=out, name=name, source_file=source, requires_auth=requires_auth)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
