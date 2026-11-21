#!/usr/bin/env python3
"""Recycle bin cleanup (README "Recycle bin"): a file in /srv/video/.recyclebin is deleted 7 days after it was moved there, counted from the move, not from the file's own date.
Radarr/Sonarr's own cleanup compares the file date (mtime), and their touch after the move is refused because the files belong to SABnzbd, Bazarr or buntu, so a years-old file went at the next cleanup. Their cleanup is off (RecycleBinCleanupDays 0); this runs instead.
Moved at = the later of the file's change time (ctime: set by the rename into the bin, never set back) and its file date.
Empty folders go once they are a day old. Refuses (deletes nothing) unless the bin is a real folder named .recyclebin on a mounted pool and the clock is NTP-synced.
Usage: scripts/recycle-bin.py [--dry-run] [--days N] [--bin PATH] | -h"""
import argparse
import dataclasses
import datetime
import os
import pathlib
import stat
import subprocess
import sys

DAY = 86400
DAYS = 7
DIR_GRACE = DAY
BIN = "/srv/video/.recyclebin"
GIB = 1024 ** 3


@dataclasses.dataclass
class Result:
    deleted: list = dataclasses.field(default_factory=list)
    kept: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)


def moved_at(st):
    return max(st.st_mtime, st.st_ctime)


def expired(st, now, days=DAYS):
    return now - moved_at(st) >= days * DAY


def when(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def sweep(bin_dir, now, days=DAYS, dry=False, log=print):
    """Deletes expired files (symlinks removed, never followed), then empty folders a day old; dry: lists what it would do."""
    r = Result()
    bin_dir = pathlib.Path(bin_dir)
    for dirpath, dirs, files in os.walk(bin_dir, topdown=False):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(dirpath, d))]:
            p = pathlib.Path(dirpath, name)
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                continue
            size = st.st_size if stat.S_ISREG(st.st_mode) else 0
            rel = p.relative_to(bin_dir)
            if not expired(st, now, days):
                r.kept.append((p, moved_at(st) + days * DAY, size))
                if dry:
                    log(f"recycle-bin: keep until {when(moved_at(st) + days * DAY)}  {rel}")
                continue
            if dry:
                r.deleted.append((p, size))
                log(f"recycle-bin: would delete (moved {when(moved_at(st))})  {rel}")
                continue
            try:
                os.unlink(p)
            except OSError as e:
                r.errors.append((p, e))
                log(f"recycle-bin: ERROR {rel}: {type(e).__name__}: {e}")
                continue
            r.deleted.append((p, size))
            log(f"recycle-bin: deleted (moved {when(moved_at(st))})  {rel}")
        d = pathlib.Path(dirpath)
        if dry or d == bin_dir or os.path.islink(d):
            continue
        try:
            if not any(os.scandir(d)) and now - moved_at(os.lstat(d)) >= DIR_GRACE:
                os.rmdir(d)
        except OSError:
            pass
    return r


def timedatectl():
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out in ("yes", "no") else None


def refusal(bin_dir, mounted=os.path.ismount, clock=None):
    p = pathlib.Path(bin_dir)
    if p.name != ".recyclebin":
        return f"{p} is not a folder named .recyclebin"
    if p.is_symlink():
        return f"{p} is a symlink"
    if not p.is_dir():
        return f"{p} is missing"
    if not mounted(str(p.parent)):
        return f"{p.parent} is not mounted"
    if clock != "yes":
        return "the clock is not NTP-synced" if clock == "no" else "NTP state unreadable"
    return None


def human(n):
    return f"{n / GIB:.1f} GiB"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Delete recycle bin files 7 days after they were moved there (not by file date).",
                                 epilog="Examples: scripts/recycle-bin.py --dry-run   (every file with its delete date)")
    ap.add_argument("--dry-run", action="store_true", help="list every file with its delete date, delete nothing")
    ap.add_argument("--days", type=int, default=DAYS, help=f"days after the move (default {DAYS})")
    ap.add_argument("--bin", default=BIN, help=f"the recycle bin (default {BIN})")
    a = ap.parse_args(argv)
    if a.days < 1:
        ap.error("--days must be at least 1")
    why = refusal(a.bin, clock=timedatectl() if not a.dry_run else "yes")
    if why:
        print(f"recycle-bin: {why} - nothing deleted")
        print("next: scripts/recycle-bin.py --dry-run   (check the bin), timedatectl, findmnt /srv/video")
        return 1
    r = sweep(a.bin, datetime.datetime.now().timestamp(), a.days, a.dry_run)
    nxt = min(r.kept, key=lambda k: k[1], default=None)
    verb = "would delete" if a.dry_run else "deleted"
    print(f"recycle-bin: {verb} {len(r.deleted)} files ({human(sum(s for _, s in r.deleted))}), kept {len(r.kept)} ({human(sum(k[2] for k in r.kept))})"
          + (f", next on {when(nxt[1])}: {nxt[0].relative_to(a.bin)}" if nxt else "") + (f", {len(r.errors)} errors" if r.errors else ""))
    if not a.dry_run:
        print("next: scripts/recycle-bin.py --dry-run   (every file left with its delete date)")
    return 1 if r.errors else 0


if __name__ == "__main__":
    sys.exit(main())
