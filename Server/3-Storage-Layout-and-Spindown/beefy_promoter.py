#!/usr/bin/env python3
"""beefy-promoter - pull a cold title back to the SSD *before* it is played.

Storage doc §7. The naive "stream from the HDD while copying to SSD" does not work:
the player's open file handle stays bound to the cold branch for the whole session,
so the drive never gets to park. The fix is PRE-promotion - have the file on the SSD
before play starts, so mergerfs serves it from flash from the first byte.

WHAT IS BUILT HERE
    The promotion engine: decide whether a title is cold, promote it exactly once,
    serialise the copies, and refuse to thrash. Driven over HTTP so anything can
    trigger it.

WHAT IS DELIBERATELY NOT BUILT
    The detail-view DETECTOR. §7 offers two triggers - a Jellyfin webhook, or a
    fanotify watcher that distinguishes "opened the detail page" (poster + backdrop +
    logo + media-info read within ~2s) from "scrolled past a grid tile" (one small
    thumbnail). Both depend on what Jellyfin actually reads and when. Jellyfin is not
    deployed yet, so that threshold would be invented rather than measured, and a
    wrong one either promotes the whole library on a scroll or never fires at all.
    Wire the trigger once Jellyfin runs - this service is the thing it calls.

    Until then POST /promote is useful on its own: "I'm watching this tonight".

Endpoints
    POST /promote   {"path": "media/movies/Dune"}      explicit
                    {"Name": "...", "ItemType": "Movie"}  Jellyfin webhook shape
    GET  /status    queue, in-flight, recent history
    GET  /health
"""
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"

POOL = os.environ.get("POOL", "/srv/video")
HOT = os.environ.get("HOT", "/srv/.disks/ssd-hot")
COLD = os.environ.get("COLD", "/srv/.disks/hdd-cold")
TIER_MOVE = os.environ.get("TIER_MOVE", "/usr/local/sbin/tier-move")
MEDIA_SUBDIR = os.environ.get("MEDIA_SUBDIR", "media")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9002"))

# A detail page can be opened repeatedly, and a webhook can fire more than once for
# one user action. Promoting the same title twice is wasted QVO write endurance.
DEDUPE_SECONDS = int(os.environ.get("DEDUPE_SECONDS", "3600"))
# Someone browsing quickly must not queue the entire library. Older entries age out.
MAX_PER_HOUR = int(os.environ.get("MAX_PER_HOUR", "6"))
# Promotions run ONE at a time on purpose: two concurrent 50 GB copies would halve
# each other's throughput and leave both unfinished when play starts.
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)


def safe_relpath(raw):
    """Normalise a caller-supplied path, or return None if it is not acceptable.

    Everything here is reachable from the network, and the value is handed to a tool
    that deletes files. Anything that escapes the pool, or is not under the media
    subdir, is refused rather than sanitised - quietly "fixing" a hostile path is how
    traversal bugs survive review.
    """
    if not raw or not isinstance(raw, str):
        return None
    rel = raw.strip()
    for prefix in (POOL + "/", POOL, "/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    rel = rel.strip("/")
    if not rel or "\x00" in rel:
        return None
    # Reject traversal outright. os.path.normpath alone is not enough: it would
    # happily turn "media/../../etc" into something outside the pool.
    if any(part in ("..", ".") for part in rel.split("/")):
        return None
    if os.path.normpath(rel) != rel:
        return None
    if not (rel == MEDIA_SUBDIR or rel.startswith(MEDIA_SUBDIR + "/")):
        return None
    return rel


def tier_of(rel):
    """'hot', 'cold', 'both' or None - which branch actually holds this path."""
    on_hot = os.path.exists(os.path.join(HOT, rel))
    on_cold = os.path.exists(os.path.join(COLD, rel))
    if on_hot and on_cold:
        return "both"
    if on_hot:
        return "hot"
    if on_cold:
        return "cold"
    return None


class Promoter:
    def __init__(self):
        self.q = deque()
        self.queued = set()
        self.inflight = None
        self.recent = deque(maxlen=50)      # (epoch, rel, outcome)
        self.done_at = {}                   # rel -> epoch of last promotion
        self.lock = threading.Lock()
        self.wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    def _rate_limited(self, now):
        cutoff = now - 3600
        recent = [t for t, _, o in self.recent if t >= cutoff and o == "promoted"]
        return len(recent) >= MAX_PER_HOUR

    def submit(self, rel):
        """Returns (accepted, reason). Never raises - this is network-facing."""
        now = time.time()
        with self.lock:
            tier = tier_of(rel)
            if tier is None:
                return False, "not found on either branch"
            if tier in ("hot", "both"):
                return False, "already hot"
            if rel in self.queued or rel == self.inflight:
                return False, "already queued"
            last = self.done_at.get(rel)
            if last and now - last < DEDUPE_SECONDS:
                return False, f"promoted {int(now - last)}s ago"
            if self._rate_limited(now):
                return False, f"rate limited ({MAX_PER_HOUR}/hour)"
            self.q.append(rel)
            self.queued.add(rel)
        self.wake.set()
        return True, "queued"

    def _worker(self):
        while True:
            self.wake.wait(timeout=5)
            self.wake.clear()
            while True:
                with self.lock:
                    if not self.q:
                        self.inflight = None
                        break
                    rel = self.q.popleft()
                    self.queued.discard(rel)
                    self.inflight = rel
                self._promote(rel)

    def _promote(self, rel):
        started = time.time()
        if DRY_RUN:
            log(f"promote: WOULD promote {rel}")
            outcome = "dry-run"
        else:
            cmd = [TIER_MOVE, "promote", rel, "--apply", "--any-time"]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
                if p.returncode == 0:
                    outcome = "promoted"
                    log(f"promote: ok {rel} in {time.time()-started:.0f}s")
                else:
                    # tier-move refuses open or hardlinked files by design; that is
                    # a skip, not a fault.
                    tail = (p.stdout or p.stderr or "").strip().splitlines()
                    outcome = "refused"
                    log(f"promote: refused {rel}: {tail[-1] if tail else '?'}")
            except subprocess.TimeoutExpired:
                outcome = "timeout"
                log(f"promote: TIMEOUT {rel}")
            except OSError as e:
                outcome = "error"
                log(f"promote: error {rel}: {e}")
        with self.lock:
            self.recent.append((time.time(), rel, outcome))
            if outcome in ("promoted", "dry-run"):
                self.done_at[rel] = time.time()
            self.inflight = None

    def status(self):
        with self.lock:
            return {
                "version": VERSION,
                "dry_run": DRY_RUN,
                "inflight": self.inflight,
                "queued": list(self.q),
                "recent": [
                    {"at": time.strftime("%H:%M:%S", time.localtime(t)),
                     "path": r, "outcome": o}
                    for t, r, o in list(self.recent)[-10:]
                ],
            }


PROMOTER = Promoter()


def extract_path(payload):
    """Pull a pool-relative path out of either an explicit or a Jellyfin body."""
    if not isinstance(payload, dict):
        return None
    if payload.get("path"):
        return payload["path"]
    # Jellyfin's webhook plugin sends the on-disk path under varying keys depending
    # on template and version; take the first that looks like one inside the pool.
    for key in ("Path", "ItemPath", "MediaSourcePath", "FilePath"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            # Jellyfin reports the CONTAINER path (/media/...), not the host path.
            v = re.sub(r"^/media/", f"{MEDIA_SUBDIR}/", v)
            # A file was given; promote its folder so sidecars are considered too.
            return os.path.dirname(v) if os.path.splitext(v)[1] else v
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = f"beefy-promoter/{VERSION}"

    def log_message(self, fmt, *args):
        pass  # the service logs what matters itself

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            return self._send(200, {"ok": True, "version": VERSION})
        if self.path.rstrip("/") == "/status":
            return self._send(200, PROMOTER.status())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/promote":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                return self._send(413, {"error": "body too large"})
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, OSError):
            return self._send(400, {"error": "invalid JSON"})

        raw = extract_path(payload)
        rel = safe_relpath(raw) if raw else None
        if not rel:
            return self._send(400, {"error": "no usable path", "got": raw})
        ok, reason = PROMOTER.submit(rel)
        log(f"promote request {rel}: {reason}")
        return self._send(202 if ok else 200, {"accepted": ok, "path": rel,
                                               "reason": reason})


def main():
    log(f"beefy-promoter v{VERSION} start: listen={LISTEN_HOST}:{LISTEN_PORT} "
        f"dry_run={DRY_RUN} dedupe={DEDUPE_SECONDS}s max_per_hour={MAX_PER_HOUR}")
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
