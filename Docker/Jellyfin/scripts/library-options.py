#!/usr/bin/env python3
"""Sets Jellyfin library options: automatic movie collections on the Movies library (franchises become BoxSets).
Idempotent. Usage: library-options.py [--dry-run | --check]  (--check exits 1 when a library differs)"""
import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jellyfin_api import api  # noqa: E402

WANT = {"movies": {"AutomaticallyAddToCollection": True}}


def wanted(folders):
    """[(library id, full LibraryOptions)] for every library whose options differ from WANT."""
    out = []
    for f in folders:
        want = WANT.get(f.get("CollectionType"))
        if want and any(f["LibraryOptions"].get(k) != v for k, v in want.items()):
            out.append((f["ItemId"], {**copy.deepcopy(f["LibraryOptions"]), **want}))
    return out


def main(argv):
    changes = wanted(api("GET", "/Library/VirtualFolders"))
    if "--check" in argv or "--dry-run" in argv:
        print(f"{len(changes)} librar{'y' if len(changes) == 1 else 'ies'} would change")
        return 1 if changes and "--check" in argv else 0
    for lib_id, options in changes:
        api("POST", "/Library/VirtualFolders/LibraryOptions", {"Id": lib_id, "LibraryOptions": options})
    print(f"{len(changes)} librar{'y' if len(changes) == 1 else 'ies'} changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
