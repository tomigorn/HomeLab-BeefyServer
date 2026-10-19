#!/usr/bin/env python3
"""Scan a finished download with clamd (INSTREAM) and quarantine infected files.

SABnzbd post-processing script:  clamav-hook.py <final dir> [SAB's other args]
qBittorrent on torrent finished:  clamav-hook.py --qbit "%I" "%F"
exit: 0 clean or not scanned (clamd unreachable), 1 infected, 2 usage
"""
import datetime
import fcntl
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
MAX_BYTES = int(os.environ.get("HOOK_MAX_BYTES", "2146435072"))
CHUNK = 1 << 20


class ScanError(Exception):
    pass


def connect():
    deadline = time.monotonic() + CLAMD_WAIT
    while True:
        try:
            return socket.create_connection((CLAMD_HOST, CLAMD_PORT), timeout=600)
        except OSError as e:
            if time.monotonic() >= deadline:
                raise ScanError("clamd %s:%d unreachable: %s" % (CLAMD_HOST, CLAMD_PORT, e))
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
            raise ScanError("%s: %s" % (path, e))
    reply = reply.rstrip(b"\0").decode(errors="replace").strip()
    if reply == "stream: OK":
        return None
    m = re.fullmatch(r"stream: (.+) FOUND", reply)
    if m:
        return m.group(1)
    raise ScanError("%s: %s" % (path, reply or "no reply"))


def files_under(path):
    q = os.path.realpath(QUARANTINE)
    if os.path.isfile(path):
        yield path
        return
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if os.path.realpath(os.path.join(root, d)) != q]
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.isfile(p) and not os.path.islink(p):
                yield p


def quarantine(path, sig):
    dest = os.path.join(QUARANTINE, os.path.basename(path))
    n = 0
    while os.path.exists(dest):
        n += 1
        dest = "%s.%d" % (os.path.join(QUARANTINE, os.path.basename(path)), n)
    shutil.move(path, dest)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with open(os.path.join(QUARANTINE, "index.txt"), "a") as f:
        f.write("%s\t%s\t%s\n" % (now, path, sig))


def qbit(torrent_hash):
    for endpoint, data in (("stop", {"hashes": torrent_hash}), ("addTags", {"hashes": torrent_hash, "tags": "infected"})):
        try:
            req = urllib.request.Request(QBIT_API + "/api/v2/torrents/" + endpoint, data=urllib.parse.urlencode(data).encode())
            urllib.request.urlopen(req, timeout=10).read()
        except OSError as e:
            print("qBittorrent %s failed: %s" % (endpoint, e))


def write_metrics(scanned, errors, infected, skipped):
    prom = os.path.join(TEXTFILE, "clamav-hook.prom")
    L = '{host="%s",mode="hook"}' % HOST
    with open(os.path.join(TEXTFILE, ".clamav-hook.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        total = 0
        try:
            with open(prom) as f:
                for line in f:
                    if line.startswith("clamav_infected_total{"):
                        total = int(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        total += infected
        rows = [
            ("clamav_files_scanned", "gauge", scanned),
            ("clamav_files_skipped_large", "gauge", skipped),
            ("clamav_infected_last_run", "gauge", infected),
            ("clamav_infected_total", "counter", total),
            ("clamav_last_run_timestamp_seconds", "gauge", int(time.time())),
            ("clamav_scan_errors", "gauge", errors),
        ]
        tmp = prom + ".tmp"
        with open(tmp, "w") as f:
            for name, kind, value in rows:
                f.write("# TYPE %s %s\n%s%s %d\n" % (name, kind, name, L, value))
        os.replace(tmp, prom)


def main(argv):
    torrent_hash = None
    if argv[:1] == ["--qbit"]:
        if len(argv) < 3:
            return usage()
        torrent_hash, path = argv[1], argv[2]
    elif argv:
        path = argv[0]
    else:
        return usage()

    scanned = errors = skipped = 0
    hits = []
    for p in files_under(path):
        try:
            if os.path.getsize(p) > MAX_BYTES:
                skipped += 1
                continue
            sig = instream(p)
            scanned += 1
        except (ScanError, OSError) as e:
            errors += 1
            print("ERROR: %s" % e)
            if isinstance(e, ScanError) and "unreachable" in str(e):
                break
            continue
        if sig:
            quarantine(p, sig)
            hits.append((p, sig))
            print("%s: %s FOUND, moved to %s" % (p, sig, QUARANTINE))

    try:
        write_metrics(scanned, errors, len(hits), skipped)
    except OSError as e:
        print("metrics not written: %s" % e)
    if hits:
        if torrent_hash:
            qbit(torrent_hash)
        print("ClamAV: %d infected, quarantined: %s" % (len(hits), ", ".join("%s (%s)" % (os.path.basename(p), s) for p, s in hits)))
        return 1
    if errors:
        print("ClamAV: not scanned completely (%d errors), the nightly scan covers it" % errors)
        return 0
    print("ClamAV: clean, %d scanned, %d over 2 GiB skipped" % (scanned, skipped))
    return 0


def usage():
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
