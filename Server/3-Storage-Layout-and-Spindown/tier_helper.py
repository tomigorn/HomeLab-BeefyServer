#!/usr/bin/env python3
"""tier-helper - crash-safe move of ONE file between the /srv/video branches, called by tier-move.

  tier-helper move <src branch> <dst branch> <rel path> [--bwlimit KBPS]

1. note the source's (inode, size, ctime ns)
2. rsync -aHAX into <dst branch>/.tier-staging/<rel> (an interrupted copy never sits under the real name)
3. fsync, drop both files from the page cache, compare them read back from disk (dropping cache as it goes, so a night of moves does not evict everything else)
4. source unchanged and still there? else discard the copy (original-audio edits in place, Radarr/Sonarr upgrades replace files)
5. create missing parent dirs with the source dirs' owner/mode, rename the copy into place (same filesystem, atomic)
6. fsync the destination dirs (the rename is on disk before the source can go: beefy has no UPS), re-check the source once more, then delete it
Exit: 0 moved, 1 copy/verify failed, 4 source changed or vanished (copy discarded). The source is never touched unless 0.

  tier-helper recover <dst branch> <other branch>...

Leftovers in <dst>/.tier-staging (a killed run or a power cut), called by tier-move under the media lock before it moves anything:
original still on another branch -> leftover removed; no original and nothing in place -> renamed into place (it is the only copy);
the same file already in place -> leftover removed; anything else (a different file in place, a partial rsync temp without its original) -> kept in <dst>/.tier-recovered for a look by hand.
Exit: 0, or 5 when something was kept aside.
"""
import os
import pathlib
import re
import subprocess
import sys

STAGING = ".tier-staging"
RECOVERED = ".tier-recovered"
RSYNC_TEMP = re.compile(r"\.(.+)\.[A-Za-z0-9]{6}")
CHUNK = 8 * 1024 * 1024


def sig(path):
    st = os.stat(path)
    return st.st_ino, st.st_size, st.st_ctime_ns


def drop_cache(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def same_on_disk(a, b):
    drop_cache(a)
    drop_cache(b)
    fa, fb = os.open(a, os.O_RDONLY), os.open(b, os.O_RDONLY)
    try:
        if os.fstat(fa).st_size != os.fstat(fb).st_size:
            return False
        off = 0
        while True:
            x, y = os.read(fa, CHUNK), os.read(fb, CHUNK)
            if x != y:
                return False
            if not x:
                return True
            os.posix_fadvise(fa, off, len(x), os.POSIX_FADV_DONTNEED)
            os.posix_fadvise(fb, off, len(x), os.POSIX_FADV_DONTNEED)
            off += len(x)
    finally:
        os.close(fa)
        os.close(fb)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_up(branch, rel_dir):
    """fsync rel_dir and every parent up to the branch root: the rename and any new dirs are durable."""
    cur = pathlib.Path(branch) / rel_dir
    while True:
        fsync_dir(cur)
        if cur == pathlib.Path(branch):
            return
        cur = cur.parent


def clone_dirs(src_branch, dst_branch, rel_dir):
    """Create rel_dir under dst_branch, each new level with the owner, group and mode of the same level under src_branch."""
    cur_src, cur_dst = pathlib.Path(src_branch), pathlib.Path(dst_branch)
    for part in pathlib.PurePosixPath(rel_dir).parts:
        cur_src, cur_dst = cur_src / part, cur_dst / part
        if cur_dst.is_dir():
            continue
        cur_dst.mkdir()
        st = cur_src.stat()
        try:
            os.chown(cur_dst, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        os.chmod(cur_dst, st.st_mode & 0o7777)


def unchanged(src, before, stage, rel, log):
    try:
        if sig(src) == before:
            return 0
        log(f"SOURCE CHANGED during the copy: {rel} - copy discarded, try again next run")
    except FileNotFoundError:
        log(f"SOURCE VANISHED during the copy: {rel} - copy discarded")
    stage.unlink(missing_ok=True)
    return 4


def move(src_branch, dst_branch, rel, bwlimit=None, log=print):
    src = pathlib.Path(src_branch) / rel
    out = pathlib.Path(dst_branch) / rel
    stage = pathlib.Path(dst_branch) / STAGING / rel
    before = sig(src)
    stage.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-aHAX"] + ([f"--bwlimit={bwlimit}"] if bwlimit else []) + [str(src), str(stage)]
    if subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode != 0:
        stage.unlink(missing_ok=True)
        log(f"copy FAILED for {rel} - source left untouched")
        return 1
    if unchanged(src, before, stage, rel, log) != 0:
        return 4
    if not same_on_disk(src, stage):
        stage.unlink(missing_ok=True)
        log(f"VERIFY FAILED for {rel} - copy discarded, source left untouched")
        return 1
    if unchanged(src, before, stage, rel, log) != 0:
        return 4
    rel_dir = str(pathlib.PurePosixPath(rel).parent)
    clone_dirs(src_branch, dst_branch, rel_dir)
    os.rename(stage, out)
    fsync_up(dst_branch, rel_dir)
    fsync_dir(stage.parent)
    try:
        changed = sig(src) != before
    except FileNotFoundError:
        changed = True
    if changed:
        out.unlink(missing_ok=True)
        log(f"SOURCE CHANGED during the rename: {rel} - copy removed again")
        return 4
    src.unlink()
    fsync_dir(src.parent)
    log(f"ok  {rel}")
    return 0


def place_dirs(dst_branch, rel_dir, others, staging_root):
    """Missing dirs of rel_dir under dst_branch, owner/mode from another branch that has them, else from the staging copy."""
    tpl = next((b for b in others if (pathlib.Path(b) / rel_dir).is_dir()), None)
    if tpl is not None:
        clone_dirs(tpl, dst_branch, rel_dir)
    else:
        clone_dirs(staging_root, dst_branch, rel_dir)


def recover(dst_branch, others, log=print):
    dst = pathlib.Path(dst_branch)
    root = dst / STAGING
    if not root.is_dir():
        return 0
    kept = 0
    for p in sorted(x for x in root.rglob("*") if not x.is_dir()):
        rel = p.relative_to(root)
        m = RSYNC_TEMP.fullmatch(rel.name)
        name = rel.with_name(m.group(1)) if m else rel
        orig = next((pathlib.Path(b) / name for b in others if (pathlib.Path(b) / name).exists()), None)
        out = dst / rel
        if orig is not None:
            p.unlink()
            log(f"staging leftover removed, original still at {orig}: {rel}")
            continue
        if not m and not out.exists():
            place_dirs(dst, str(rel.parent), others, root)
            os.rename(p, out)
            fsync_up(dst, str(rel.parent))
            log(f"RECOVERED {rel}: its only copy was in {STAGING} (the run stopped after the source was deleted)")
            continue
        if not m and out.is_file() and same_on_disk(p, out):
            p.unlink()
            log(f"staging leftover removed, the same file is in place: {rel}")
            continue
        q = dst / RECOVERED / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        os.rename(p, q)
        kept += 1
        log(f"KEPT {rel} in {dst / RECOVERED}: no original on {', '.join(map(str, others))} and {'a partial copy' if m else 'a different file in place'} - check by hand")
    for d in sorted((x for x in root.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    return kept


def main(argv):
    if len(argv) >= 4 and argv[0] == "move":
        bw = argv[argv.index("--bwlimit") + 1] if "--bwlimit" in argv else None
        return move(argv[1], argv[2], argv[3], bw)
    if len(argv) >= 2 and argv[0] == "recover":
        return 5 if recover(argv[1], argv[2:]) else 0
    print("\n\n".join(__doc__.split("\n\n")[1:4:2]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
