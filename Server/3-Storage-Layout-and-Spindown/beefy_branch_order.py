#!/usr/bin/env python3
"""beefy-branch-order - keep the emptier hot SSD first in each mergerfs view of the pool (category.create=ff fills the first branch).

Swaps ssd-hot and ssd-hot2 at runtime (xattr user.mergerfs.branches) when the first one is fuller by more than SWAP_POINTS used-percent points; the cold HDD always stays last. Reads statvfs of the two SSDs only, never touches the HDD. Logs only when it changes something.
Usage: beefy-branch-order [--check] [MOUNT...]   (default: /srv/video /srv/dlpool, a view that is not mounted is skipped)
  --check: print "ok" per mount, or what is wrong (post-update check host.branch-order); exit 1 when not ok
"""
import os
import sys

SSDS = ("/srv/.disks/ssd-hot", "/srv/.disks/ssd-hot2")
HDD = "/srv/.disks/hdd-cold"
SWAP_POINTS = 2.0
MOUNTS = ("/srv/video", "/srv/dlpool")
XATTR = "user.mergerfs.branches"


def _split(branches):
    entries = branches.split(":")
    paths = [e.split("=", 1)[0] for e in entries]
    if not all(s in paths for s in SSDS):
        raise ValueError(f"branches lack {', '.join(s for s in SSDS if s not in paths)}: {branches}")
    return entries, paths


def decide(branches, used):
    """New branch string, or None when the order is already right."""
    entries, paths = _split(branches)
    ssd = [e for e, p in zip(entries, paths) if p in SSDS]
    other = [e for e, p in zip(entries, paths) if p not in SSDS and p != HDD]
    hdd = [e for e, p in zip(entries, paths) if p == HDD]
    first, second = (p.split("=", 1)[0] for p in ssd)
    if used[first] - used[second] > SWAP_POINTS:
        ssd.reverse()
    new = ":".join(ssd + other + hdd)
    return None if new == branches else new


def check(branches, used):
    entries, paths = _split(branches)
    if HDD in paths and paths[-1] != HDD:
        return "hdd-not-last"
    first, second = [p for p in paths if p in SSDS]
    if paths[0] != first or used[first] - used[second] > SWAP_POINTS:
        return "fuller-first"
    return "ok"


def used_pct(path, statvfs=os.statvfs):
    st = statvfs(path)
    return 100.0 * (1 - st.f_bavail / st.f_blocks)


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__.strip())
        return 0
    want_check = "--check" in argv
    mounts = [a for a in argv if not a.startswith("-")] or list(MOUNTS)
    used = {s: used_pct(s) for s in SSDS}
    rc = 0
    for m in mounts:
        ctl = os.path.join(m, ".mergerfs")
        if not os.path.exists(ctl):
            continue
        cur = os.getxattr(ctl, XATTR).decode()
        if want_check:
            res = check(cur, used)
            print(f"{m} {res}")
            rc |= res != "ok"
            continue
        new = decide(cur, used)
        if new:
            os.setxattr(ctl, XATTR, new.encode())
            got = os.getxattr(ctl, XATTR).decode()
            pct = ", ".join(f"{os.path.basename(s)} {used[s]:.1f} %" for s in SSDS)
            print(f"{m}: branches {cur} -> {got} ({pct})")
            if got != new:
                print(f"{m}: mergerfs kept {got}, wanted {new}", file=sys.stderr)
                rc = 1
    if want_check and rc:
        print("fix: sudo beefy-branch-order   (or: systemctl start beefy-branch-order.service)", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
