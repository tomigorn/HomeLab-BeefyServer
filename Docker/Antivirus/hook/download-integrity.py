#!/usr/bin/env python3
"""Check a finished download on disk before it can be imported, then hand over to the ClamAV hook.

SABnzbd post-processing script:  download-integrity.py <final dir> [SAB's other args]
qBittorrent on torrent finished:  download-integrity.py --qbit "%I" "%F"
By hand (report only):            download-integrity.py --check <file or dir>...
Checks: par2 verify of the set when par2 files are there (then removed), else every video file: container header and no
aligned 1 MiB block of zeros. Data is flushed and dropped from the page cache first, so the disk is read, not memory.
exit: clean -> whatever clamav-hook.py returns; 1 corrupt (SABnzbd job failed, torrent rechecked and tagged); 2 usage
"""
import datetime
import fcntl
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qbit_session import Qbit  # noqa: E402

TEXTFILE = os.environ.get("TEXTFILE", "/clamav")
HOST = os.environ.get("HOST", "beefy")
QBIT_API = os.environ.get("QBIT_API", "http://127.0.0.1:8080")
CLAMAV_HOOK = os.environ.get("CLAMAV_HOOK", os.path.join(os.path.dirname(os.path.abspath(__file__)), "clamav-hook.py"))
PAR2 = os.environ.get("PAR2", "par2")
ZERO_MIB = int(os.environ.get("INTEGRITY_ZERO_MIB", "1"))
SKIP_DIRS = {"quarantine", ".clamav-queue", ".corrupt-hold"}
MIB = 1 << 20
ZERO = bytes(MIB)

VIDEO_EXT = {"mkv", "webm", "mp4", "m4v", "mov", "avi", "ts", "m2ts", "wmv", "mpg", "mpeg"}
MP4_BOXES = {b"ftyp", b"styp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot"}


def ext(path):
    name = os.path.basename(path)
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def header_ok(path, head):
    e = ext(path)
    if e in ("mkv", "webm"):
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if e in ("mp4", "m4v", "mov"):
        return head[4:8] in MP4_BOXES
    if e == "avi":
        return head[:4] == b"RIFF" and head[8:12] == b"AVI "
    if e in ("ts", "m2ts"):
        return (head[:1] == b"\x47" and head[188:189] == b"\x47") or (head[4:5] == b"\x47" and head[196:197] == b"\x47")
    return True


def drop_cache(path):
    """Write back what is still dirty and drop it from the page cache, so the next read comes from the disk."""
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def scan_video(path):
    """Return a reason string when the file is corrupt, else None."""
    drop_cache(path)
    zero = longest = run = 0
    first = None
    with open(path, "rb", buffering=0) as f:
        head = f.read(MIB)
        if not header_ok(path, head):
            return "container header missing (%s)" % (head[:8].hex() or "empty")
        block = 0
        while head:
            if head == ZERO:
                zero += 1
                run += 1
                longest = max(longest, run)
                if first is None:
                    first = block
            else:
                run = 0
            block += 1
            head = f.read(MIB)
        try:
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass
    if zero >= ZERO_MIB:
        return "%d MiB of zeros in %d MiB (first at %d MiB, longest run %d MiB)" % (zero, block, first, longest)
    return None


def files_under(path):
    if os.path.isfile(path):
        yield path
        return
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.isfile(p) and not os.path.islink(p):
                yield p


def par2_main(par2s):
    plain = [p for p in par2s if not re.search(r"\.vol\d+[+-]\d+\.par2$", p, re.I)]
    return min(plain or par2s, key=lambda p: (len(os.path.basename(p)), p))


def par2_verify(par2s, others):
    """True verified, the damaged file names, or None for no verdict (par2 missing, a target missing or misnamed)."""
    binary = shutil.which(PAR2) if os.sep not in PAR2 else (PAR2 if os.access(PAR2, os.X_OK) else None)
    if not binary:
        print("par2 not available, zero scan instead")
        return None
    main = par2_main(par2s)
    try:
        r = subprocess.run([binary, "verify", main] + others, cwd=os.path.dirname(main), capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as e:
        print("par2 verify not run (%s), zero scan instead" % e)
        return None
    damaged = re.findall(r'Target: "([^"\r\n]+)" - damaged', r.stdout)
    if damaged:
        print("par2 verify of %s: damaged on disk: %s" % (os.path.basename(main), ", ".join(damaged)))
        return damaged
    if r.returncode != 0:
        print("par2 verify of %s gave no verdict (exit %d), zero scan instead" % (os.path.basename(main), r.returncode))
        return None
    return True


def check(path, use_par2=True):
    """Return (corrupt [(file, reason)], errors, checked files)."""
    corrupt, errors, checked = [], 0, 0
    files = list(files_under(path))
    par2s = [p for p in files if ext(p) == "par2"]
    if use_par2 and par2s:
        for p in files:
            if ext(p) in VIDEO_EXT:
                try:
                    drop_cache(p)
                except OSError:
                    pass
        verified = par2_verify(par2s, [p for p in files if ext(p) != "par2"])
        videos = sum(1 for p in files if ext(p) in VIDEO_EXT)
        if isinstance(verified, list):
            base = os.path.dirname(par2_main(par2s))
            return [(os.path.join(base, n), "par2 verify: damaged on disk") for n in verified], 0, videos
        if verified:
            for p in par2s:
                try:
                    os.unlink(p)
                except OSError as e:
                    print("par2 file not removed (%s): %s" % (e, p))
            print("par2 verify OK")
            return [], 0, videos
    for p in files:
        if ext(p) not in VIDEO_EXT:
            continue
        try:
            reason = scan_video(p)
        except OSError as e:
            errors += 1
            print("ERROR: not checked: %s" % e)
            continue
        checked += 1
        if reason:
            corrupt.append((p, reason))
    return corrupt, errors, checked


def append_line(path, line):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o664)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


COUNTERS = ("download_integrity_checked_total", "download_integrity_corrupt_total", "download_integrity_errors_total")
GAUGES = ("download_integrity_last_run_timestamp_seconds", "download_integrity_last_corrupt_timestamp_seconds")
SAMPLE = re.compile(r'^(\w+)\{host="[^"]*",client="([^"]*)"\} (\S+)$')


def write_metrics(client, corrupt, errors):
    prom = os.path.join(TEXTFILE, "download-integrity.prom")
    with open(os.path.join(TEXTFILE, ".download-integrity.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        samples = {}
        try:
            with open(prom) as f:
                for line in f:
                    m = SAMPLE.match(line.strip())
                    if m:
                        samples.setdefault(m.group(1), {})[m.group(2)] = float(m.group(3))
        except OSError:
            pass
        now = int(time.time())
        add = {"download_integrity_checked_total": 1, "download_integrity_corrupt_total": 1 if corrupt else 0,
               "download_integrity_errors_total": errors}
        for name, n in add.items():
            s = samples.setdefault(name, {})
            s[client] = s.get(client, 0) + n
        samples.setdefault("download_integrity_last_run_timestamp_seconds", {})[client] = now
        last = samples.setdefault("download_integrity_last_corrupt_timestamp_seconds", {})
        last[client] = now if corrupt else last.get(client, 0)
        tmp = prom + ".tmp"
        with open(tmp, "w") as f:
            for name in COUNTERS + GAUGES:
                if samples.get(name):
                    f.write("# TYPE %s %s\n" % (name, "counter" if name in COUNTERS else "gauge"))
                    for c in sorted(samples[name]):
                        f.write('%s{host="%s",client="%s"} %d\n' % (name, HOST, c, samples[name][c]))
        os.chmod(tmp, 0o664)
        os.replace(tmp, prom)


def record(client, title, corrupt, errors):
    try:
        write_metrics(client, bool(corrupt), errors)
    except OSError as e:
        print("metrics not written: %s" % e)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    title = " ".join(str(title or "").split())
    for p, reason in corrupt:
        try:
            append_line(os.path.join(TEXTFILE, "download-integrity-hits.tsv"), "%s\t%s\t%s\t%s\t%s\n" % (now, client, title, p, reason))
        except OSError as e:
            print("ERROR: download-integrity-hits.tsv not written (%s)" % e)


def qbit(torrent_hash):
    api = Qbit(QBIT_API)
    for endpoint, data in (("recheck", {"hashes": torrent_hash}), ("addTags", {"hashes": torrent_hash, "tags": "corrupt"})):
        try:
            api.post("torrents/" + endpoint, data)
        except OSError as e:
            print("qBittorrent %s failed: %s" % (endpoint, e))


def hand_over(argv):
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, CLAMAV_HOOK] + argv)


def main(argv):
    if argv[:1] == ["--check"]:
        if len(argv) < 2:
            return usage()
        bad = False
        for path in argv[1:]:
            corrupt, errors, _ = check(path, use_par2=False)
            for p, reason in corrupt:
                print("CORRUPT\t%s\t%s" % (p, reason))
            bad = bad or bool(corrupt) or bool(errors)
        return 1 if bad else 0
    torrent_hash = None
    if argv[:1] == ["--qbit"]:
        if len(argv) < 3:
            return usage()
        torrent_hash, path, client = argv[1], argv[2], "qbittorrent"
        title = os.path.basename(path.rstrip("/"))
    elif argv:
        path, client = argv[0], "sabnzbd"
        title = argv[2] if len(argv) > 2 and argv[2] else os.path.basename(path.rstrip("/"))
        status = argv[6] if len(argv) > 6 else os.environ.get("SAB_PP_STATUS", "0")
        if status.strip() not in ("", "0"):
            hand_over(argv)
    else:
        return usage()

    corrupt, errors, checked = check(path, use_par2=torrent_hash is None)
    record(client, title, corrupt, errors)
    if corrupt:
        for p, reason in corrupt:
            print("CORRUPT %s: %s" % (p, reason))
        if torrent_hash:
            qbit(torrent_hash)
            print("Integrity: corrupt on disk, torrent rechecked (bad pieces download again) and tagged corrupt")
        else:
            print("Integrity: corrupt on disk, job failed so Sonarr/Radarr blocklist it and search again")
        return 1
    print("Integrity: OK (%d video files checked%s)" % (checked, ", %d not readable" % errors if errors else ""))
    hand_over(argv)


def usage():
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
