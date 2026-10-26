#!/usr/bin/env python3
"""Scan a finished download with clamd (INSTREAM) and quarantine infected files.

SABnzbd post-processing script:  clamav-hook.py <final dir> [SAB's other args]
qBittorrent on torrent finished:  clamav-hook.py --qbit "%I" "%F"
Background queue worker:          clamav-hook.py --worker [--once]
exit: 0 clean, queued, failed SAB job or not scanned (clamd unreachable), 1 infected, 2 usage
"""
import datetime
import fcntl
import json
import os
import re
import shutil
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

CLAMD_HOST = os.environ.get("CLAMD_HOST", "clamav")
CLAMD_PORT = int(os.environ.get("CLAMD_PORT", "3310"))
CLAMD_WAIT = float(os.environ.get("CLAMD_WAIT", "120"))
QUARANTINE = os.environ.get("QUARANTINE", "/data/quarantine")
TEXTFILE = os.environ.get("TEXTFILE", "/clamav")
HOST = os.environ.get("HOST", "beefy")
QBIT_API = os.environ.get("QBIT_API", "http://127.0.0.1:8080")
# Paths in index.txt as the clamav container sees them, so "scan.sh restore" can move a file back.
PATH_MAP = os.environ.get("PATH_MAP", "/data=/scan/ssd-hot")
MAX_BYTES = int(os.environ.get("HOOK_MAX_BYTES", "2146435072"))
# Media files are hardlinked here and scanned by the worker; missing directory = everything synchronous.
QUEUE = os.environ.get("QUEUE", "/data/.clamav-queue")
# Where the worker looks for the other hardlinks of an infected file (imported, seeding copies).
WALK_ROOT = os.environ.get("WALK_ROOT", "/data")
POLL = float(os.environ.get("QUEUE_POLL", "5"))
STALE_TMP = float(os.environ.get("QUEUE_STALE_TMP", "3600"))
# Touched before every file; the container healthcheck reads its age.
HEARTBEAT = os.environ.get("HEARTBEAT", "/tmp/clamav-queue-alive")
CHUNK = 1 << 20

MEDIA_EXT = {
    "mkv", "mp4", "m4v", "avi", "mov", "wmv", "ts", "m2ts", "webm",
    "m4a", "m4b", "mp3", "flac", "opus", "ogg", "aac",
    "srt", "ass", "ssa", "sub", "idx", "sup", "nfo", "txt", "jpg", "jpeg", "png",
}
# Executables and archives under a media name stay synchronous.
RISKY_MAGIC = (b"MZ", b"\x7fELF", b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf\x27\x1c", b"#!", b"\xca\xfe\xba\xbe", b"\xd0\xcf\x11\xe0", b"MSCF")


class ScanError(Exception):
    pass


class Retry(ScanError):
    """No verdict from clamd (connection lost, no reply): worth another try."""


class Unreachable(Retry):
    pass


def connect():
    deadline = time.monotonic() + CLAMD_WAIT
    while True:
        try:
            return socket.create_connection((CLAMD_HOST, CLAMD_PORT), timeout=600)
        except OSError as e:
            if time.monotonic() >= deadline:
                raise Unreachable("clamd %s:%d unreachable: %s" % (CLAMD_HOST, CLAMD_PORT, e))
            time.sleep(5)


def instream(path):
    """Return None when clean, the signature when infected; raise ScanError otherwise."""
    with connect() as s, open(path, "rb") as f:
        try:
            s.sendall(b"zINSTREAM\0")
            while chunk := f.read(CHUNK):
                s.sendall(struct.pack("!I", len(chunk)) + chunk)
            s.sendall(struct.pack("!I", 0))
        except OSError:
            pass
        reply = b""
        try:
            while not reply.endswith(b"\0"):
                data = s.recv(4096)
                if not data:
                    break
                reply += data
        except OSError as e:
            raise Retry("%s: %s" % (path, e))
    reply = reply.rstrip(b"\0").decode(errors="replace").strip()
    if reply == "stream: OK":
        return None
    m = re.fullmatch(r"stream: (.+) FOUND", reply)
    if m:
        return m.group(1)
    if not reply:
        raise Retry("%s: no reply" % path)
    raise ScanError("%s: %s" % (path, reply or "no reply"))


def skip_dirs():
    return {os.path.realpath(QUARANTINE), os.path.realpath(QUEUE)}


def files_under(path):
    skip = skip_dirs()
    if os.path.isfile(path):
        yield path
        return
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if os.path.realpath(os.path.join(root, d)) not in skip]
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.isfile(p) and not os.path.islink(p):
                yield p


def is_media(path):
    ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
    if ext not in MEDIA_EXT:
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return False
    return not head.startswith(RISKY_MAGIC)


def append_line(path, line):
    # 664: the hook (SABnzbd, qBittorrent) and clamav-queue all append to it.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o664)
    try:
        try:
            os.fchmod(fd, 0o664)
        except OSError:
            pass
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def quarantine(path, sig, where=None, mode="hook", title=""):
    """Move path into the quarantine; index.txt records `where` (default path) as the place to restore to.

    index.txt keeps exactly 3 fields (scan.sh restore); who quarantined it (mode, job title) goes to TEXTFILE/clamav-hits.tsv.
    """
    where = where or path
    base = os.path.basename(where)
    dest = os.path.join(QUARANTINE, base)
    n = 0
    while os.path.exists(dest):
        n += 1
        dest = "%s.%03d" % (os.path.join(QUARANTINE, base), n)
    shutil.move(path, dest)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    cpath = container_path(where)
    try:
        append_line(os.path.join(QUARANTINE, "index.txt"), "%s\t%s\t%s\n" % (now, cpath, sig))
    except OSError as e:
        print("ERROR: index.txt not written (%s): %s\t%s" % (e, cpath, sig))
    title = " ".join(str(title or "").split())
    try:
        append_line(os.path.join(TEXTFILE, "clamav-hits.tsv"), "%s\t%s\t%s\t%s\t%s\n" % (now, mode, title, cpath, sig))
    except OSError as e:
        print("ERROR: clamav-hits.tsv not written (%s): %s\t%s" % (e, cpath, sig))
    return dest


def container_path(path):
    src, _, dst = PATH_MAP.partition("=")
    if src and (path == src or path.startswith(src + "/")):
        return dst + path[len(src):]
    return path


def qbit(torrent_hash):
    for endpoint, data in (("stop", {"hashes": torrent_hash}), ("addTags", {"hashes": torrent_hash, "tags": "infected"})):
        try:
            req = urllib.request.Request(QBIT_API + "/api/v2/torrents/" + endpoint, data=urllib.parse.urlencode(data).encode())
            urllib.request.urlopen(req, timeout=10).read()
        except OSError as e:
            print("qBittorrent %s failed: %s" % (endpoint, e))


METRICS = (
    ("clamav_files_scanned", "gauge"),
    ("clamav_files_skipped_large", "gauge"),
    ("clamav_files_queued", "gauge"),
    ("clamav_infected_last_run", "gauge"),
    ("clamav_infected_total", "counter"),
    ("clamav_last_run_timestamp_seconds", "gauge"),
    ("clamav_queue_jobs", "gauge"),
    ("clamav_scan_errors", "gauge"),
)
SAMPLE = re.compile(r'^(\w+)\{host="[^"]*",mode="([^"]*)"\} (\S+)$')


def write_metrics(mode, values, infected):
    """Replace this mode's samples in clamav-hook.prom; the hook (mode hook) and the worker (mode queue) share the file."""
    prom = os.path.join(TEXTFILE, "clamav-hook.prom")
    with open(os.path.join(TEXTFILE, ".clamav-hook.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        samples = {}
        try:
            with open(prom) as f:
                for line in f:
                    m = SAMPLE.match(line.strip())
                    if m:
                        samples.setdefault(m.group(1), {})[m.group(2)] = int(float(m.group(3)))
        except OSError:
            pass
        total = samples.get("clamav_infected_total", {}).get(mode, 0) + infected
        for name, _ in METRICS:
            samples.get(name, {}).pop(mode, None)
        own = dict(values, clamav_infected_last_run=infected, clamav_infected_total=total,
                   clamav_last_run_timestamp_seconds=int(time.time()))
        for name, value in own.items():
            samples.setdefault(name, {})[mode] = value
        tmp = prom + ".tmp"
        with open(tmp, "w") as f:
            for name, kind in METRICS:
                if samples.get(name):
                    f.write("# TYPE %s %s\n" % (name, kind))
                    for m in sorted(samples[name]):
                        f.write('%s{host="%s",mode="%s"} %d\n' % (name, HOST, m, samples[name][m]))
        os.replace(tmp, prom)


def metrics(mode, values, infected):
    try:
        write_metrics(mode, values, infected)
    except OSError as e:
        print("metrics not written: %s" % e)


def enqueue(files, title, torrent_hash):
    """Hardlink files into a new queue job; return the files that could not be queued."""
    if not files or not os.path.isdir(QUEUE):
        return files
    name = "%s-%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid())
    tmp = os.path.join(QUEUE, name + ".tmp")
    try:
        os.mkdir(tmp)
        os.chmod(tmp, 0o2770)
    except OSError as e:
        print("queue not usable (%s), scanning here" % e)
        return files
    entries, left = [], []
    for i, p in enumerate(files, 1):
        link = "%04d" % i
        try:
            os.link(p, os.path.join(tmp, link))
            entries.append({"link": link, "path": p})
        except OSError as e:
            print("not queued (%s): %s" % (e, p))
            left.append(p)
    try:
        with open(os.path.join(tmp, "job.json"), "w") as f:
            json.dump({"title": title, "hash": torrent_hash, "queued": int(time.time()), "files": entries}, f)
        os.rename(tmp, os.path.join(QUEUE, name))
    except OSError as e:
        print("queue job not finished (%s), the worker takes it over later" % e)
    return left


def main(argv):
    if argv[:1] == ["--worker"]:
        return worker("--once" in argv[1:])
    torrent_hash = None
    if argv[:1] == ["--qbit"]:
        if len(argv) < 3:
            return usage()
        torrent_hash, path = argv[1], argv[2]
        title = os.path.basename(path.rstrip("/"))
    elif argv:
        path = argv[0]
        title = argv[2] if len(argv) > 2 and argv[2] else os.path.basename(path.rstrip("/"))
        status = argv[6] if len(argv) > 6 else os.environ.get("SAB_PP_STATUS", "0")
        if status.strip() not in ("", "0"):
            print("ClamAV: job failed in SABnzbd (status %s), not scanned" % status)
            return 0
    else:
        return usage()

    scanned = errors = skipped = 0
    hits = []
    media, sync = [], []
    for p in files_under(path):
        try:
            if os.path.getsize(p) > MAX_BYTES:
                skipped += 1
                continue
        except OSError as e:
            errors += 1
            print("ERROR: %s" % e)
            continue
        (media if is_media(p) else sync).append(p)
    left = enqueue(media, title, torrent_hash)
    queued = len(media) - len(left)
    sync += left

    for p in sync:
        try:
            sig = instream(p)
            scanned += 1
        except (ScanError, OSError) as e:
            errors += 1
            print("ERROR: %s" % e)
            if isinstance(e, Unreachable):
                break
            continue
        if sig:
            quarantine(p, sig, mode="hook", title=title)
            hits.append((p, sig))
            print("%s: %s FOUND, moved to %s" % (p, sig, QUARANTINE))

    metrics("hook", {"clamav_files_scanned": scanned, "clamav_files_skipped_large": skipped,
                     "clamav_files_queued": queued, "clamav_scan_errors": errors}, len(hits))
    if hits:
        if torrent_hash:
            qbit(torrent_hash)
        print("ClamAV: %d infected, quarantined: %s" % (len(hits), ", ".join("%s (%s)" % (os.path.basename(p), s) for p, s in hits)))
        return 1
    if errors:
        print("ClamAV: not scanned completely (%d errors), the nightly scan covers it" % errors)
        return 0
    print("ClamAV: clean, %d scanned, %d queued for the background scan, %d over 2 GiB skipped" % (scanned, queued, skipped))
    return 0


# Background worker: one job at a time, FIFO by name; a link leaves the queue only with a verdict.

def pending_jobs():
    jobs = []
    try:
        names = sorted(os.listdir(QUEUE))
    except OSError as e:
        print("queue %s not readable: %s" % (QUEUE, e))
        return jobs
    for n in names:
        p = os.path.join(QUEUE, n)
        if n.startswith(".") or not os.path.isdir(p):
            continue
        if n.endswith(".tmp"):
            try:
                if time.time() - os.stat(p).st_mtime < STALE_TMP:
                    continue
                os.rename(p, p[:-4])
                p = p[:-4]
            except OSError:
                continue
        jobs.append(p)
    return jobs


def other_links(path):
    """Every other hardlink of path under WALK_ROOT (library import, seeding copy), outside queue and quarantine."""
    st = os.stat(path)
    want = st.st_nlink - 1
    found = []
    if want <= 0:
        return found
    skip = skip_dirs()
    for root, dirs, names in os.walk(WALK_ROOT):
        dirs[:] = [d for d in dirs if os.path.realpath(os.path.join(root, d)) not in skip]
        for n in names:
            p = os.path.join(root, n)
            try:
                s = os.lstat(p)
            except OSError:
                continue
            if s.st_ino == st.st_ino and s.st_dev == st.st_dev:
                found.append(p)
                if len(found) >= want:
                    return found
    return found


def handle_hit(link, orig, sig, job):
    others = other_links(link)
    where = next((p for p in others if "/media/" in p), others[0] if others else orig)
    removed = []
    # The other links first: until the queue link is quarantined, a retry finds the hit again.
    for p in others:
        try:
            os.unlink(p)
            removed.append(p)
        except OSError as e:
            print("ERROR: could not remove %s: %s" % (p, e))
    dest = quarantine(link, sig, where, mode="queue", title=job.get("title"))
    failed = len(others) - len(removed)
    if job.get("hash"):
        qbit(job["hash"])
    print("ClamAV queue: INFECTED %s: %s (%s), quarantined as %s%s" % (
        job.get("title"), where, sig, dest, ", removed " + ", ".join(removed) if removed else ""))
    return failed


# Hits that could not be quarantined yet: counted once, retried until they can.
UNHANDLED = set()
TRIES = {}


def process_job(jobdir):
    """Scan every link of one job. False: clamd unreachable, the job stays for the next try."""
    try:
        with open(os.path.join(jobdir, "job.json")) as f:
            job = json.load(f)
    except (OSError, ValueError):
        job = {"title": os.path.basename(jobdir), "hash": None, "files": []}
    paths = {e.get("link"): e.get("path") for e in job.get("files", [])}
    scanned = errors = hits = 0
    for n in sorted(os.listdir(jobdir)):
        link = os.path.join(jobdir, n)
        if n == "job.json" or n.startswith(".") or not os.path.isfile(link):
            continue
        orig = paths.get(n) or link
        alive()
        try:
            sig = instream(link)
        except Retry as e:
            TRIES[link] = TRIES.get(link, 0) + 1
            if isinstance(e, Unreachable) or TRIES[link] < 3:
                print("ERROR: %s, job %s kept" % (e, os.path.basename(jobdir)))
                return False
            errors += 1
            print("ERROR: %s (%s), 3 tries, dropped from the queue" % (e, orig))
            os.unlink(link)
            continue
        except (ScanError, OSError) as e:
            errors += 1
            print("ERROR: %s (%s), dropped from the queue" % (e, orig))
            os.unlink(link)
            continue
        scanned += 1
        if sig:
            hits += 1
            try:
                errors += handle_hit(link, orig, sig, job)
            except OSError as e:
                print("ERROR: INFECTED %s: %s (%s) not quarantined: %s, retried in 30 s" % (job.get("title"), orig, sig, e))
                if link not in UNHANDLED:
                    UNHANDLED.add(link)
                    metrics("queue", {"clamav_files_scanned": scanned, "clamav_scan_errors": errors + 1}, hits)
                return False
        else:
            os.unlink(link)
    try:
        os.unlink(os.path.join(jobdir, "job.json"))
    except FileNotFoundError:
        pass
    os.rmdir(jobdir)
    metrics("queue", {"clamav_files_scanned": scanned, "clamav_files_skipped_large": 0, "clamav_scan_errors": errors,
                      "clamav_queue_jobs": len(pending_jobs())}, hits)
    print("ClamAV queue: %s: %d scanned, %d infected, %d errors" % (job.get("title"), scanned, hits, errors))
    return True


def alive():
    try:
        with open(HEARTBEAT, "w"):
            pass
    except OSError:
        pass


def worker(once):
    print("ClamAV queue worker on %s, clamd %s:%d" % (QUEUE, CLAMD_HOST, CLAMD_PORT), flush=True)
    while True:
        alive()
        jobs = pending_jobs()
        for j in jobs:
            if not process_job(j):
                if once:
                    return 0
                time.sleep(30)
                break
            sys.stdout.flush()
        else:
            if once:
                return 0
            if not jobs:
                time.sleep(POLL)


def usage():
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
