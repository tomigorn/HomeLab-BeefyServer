#!/usr/bin/env python3
"""tier-helper - crash-safe move of ONE file between the /srv/video branches, called by tier-move.

  tier-helper move <src branch> <dst branch> <rel path> [--bwlimit KBPS]

1. note the source's (inode, size, ctime ns)
2. rsync -aHAX into <dst branch>/.tier-staging/<rel> (an interrupted copy never sits under the real name)
3. fsync, drop both files from the page cache, compare them read back from disk (dropping cache as it goes, so a night of moves does not evict everything else)
4. source unchanged and still there? else discard the copy (original-audio edits in place, Radarr/Sonarr upgrades replace files)
5. create missing parent dirs with the source dirs' owner/mode, rename the copy into place (same filesystem, atomic)
6. re-check the source once more, then delete it
Exit: 0 moved, 1 copy/verify failed, 4 source changed or vanished (copy discarded). The source is never touched unless 0.
"""
import os
import pathlib
import subprocess
import sys

STAGING = ".tier-staging"
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
    clone_dirs(src_branch, dst_branch, str(pathlib.PurePosixPath(rel).parent))
    os.rename(stage, out)
    try:
        changed = sig(src) != before
    except FileNotFoundError:
        changed = True
    if changed:
        out.unlink(missing_ok=True)
        log(f"SOURCE CHANGED during the rename: {rel} - copy removed again")
        return 4
    src.unlink()
    log(f"ok  {rel}")
    return 0


def main(argv):
    if len(argv) >= 4 and argv[0] == "move":
        bw = argv[argv.index("--bwlimit") + 1] if "--bwlimit" in argv else None
        return move(argv[1], argv[2], argv[3], bw)
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
