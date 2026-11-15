"""VirusTotal fingerprint check: looks up the SHA-256 of a downloaded file, never sends the file itself.

The hook and the queue worker append a "queued" line per hashed file to TEXTFILE/virustotal-results.jsonl; the worker's Checker
looks the hashes up (free API: 4 a minute, 500 a day) and appends the verdict for every path with that hash. The log is the
queue and the cache: a hash with a verdict is never looked up again.
"""
import calendar
import json
import os
import re
import time
import urllib.error
import urllib.request

LOG = "virustotal-results.jsonl"
STATE = ".virustotal-limits.json"
API = "https://www.virustotal.com/api/v3/files/"
PER_MINUTE = int(os.environ.get("VT_PER_MINUTE", "4"))
PER_DAY = int(os.environ.get("VT_PER_DAY", "500"))
RETRY = int(os.environ.get("VT_RETRY", "900"))
POLL = float(os.environ.get("VT_POLL", "15"))
MALWARE_AT = 3
MAX_BYTES = int(os.environ.get("RESULTS_MAX_BYTES", str(5 * 1024 * 1024)))
SHA = re.compile(r"[0-9a-f]{64}")
FINAL = ("malware", "flagged", "clean", "unknown")


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def epoch(s):
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0


def append(textfile, line, max_bytes=MAX_BYTES):
    out = os.path.join(textfile, LOG)
    try:
        if os.path.getsize(out) > max_bytes:
            os.replace(out, out + ".1")
    except FileNotFoundError:
        pass
    # 664: the hook (SABnzbd, qBittorrent) and clamav-queue all append to it
    fd = os.open(out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o664)
    try:
        try:
            os.fchmod(fd, 0o664)
        except OSError:
            pass
        os.write(fd, (json.dumps(line) + "\n").encode())
    finally:
        os.close(fd)


def queue(textfile, path, sha256, size, clock=time.time, max_bytes=MAX_BYTES):
    """Queue one hashed file for the lookup. Never raises: a scan never fails because of it."""
    if not isinstance(sha256, str) or not SHA.fullmatch(sha256):
        return
    at = stamp(clock())
    try:
        append(textfile, {"at": at, "path": path, "size": size, "sha256": sha256, "hashed": at, "vt": "queued"}, max_bytes)
    except OSError as e:
        print("VirusTotal queue not written (%s): %s" % (e, path))


def read_log(textfile):
    lines = []
    for name in (LOG + ".1", LOG):
        try:
            with open(os.path.join(textfile, name), encoding="utf-8", errors="replace") as f:
                for raw in f:
                    try:
                        x = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(x, dict) and isinstance(x.get("path"), str) and SHA.fullmatch(str(x.get("sha256"))):
                        lines.append(x)
        except OSError:
            continue
    return lines


def classify(body):
    s = ((body.get("data") or {}).get("attributes") or {}).get("last_analysis_stats") or {}
    n = {k: int(s.get(k) or 0) for k in ("malicious", "suspicious", "undetected", "harmless")}
    engines = sum(n.values())
    m = n["malicious"]
    verdict = "malware" if m >= MALWARE_AT else "flagged" if m else "clean"
    return {"vt": verdict, "malicious": m, "suspicious": n["suspicious"], "engines": engines}


class Quota(Exception):
    pass


class Limiter:
    """Lookups per minute and per UTC day (VirusTotal's quota day), kept on disk across restarts."""

    def __init__(self, path, clock, per_minute, per_day):
        self.path, self.clock, self.per_minute, self.per_day = path, clock, per_minute, per_day
        try:
            with open(path) as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        self.day = s.get("day") if isinstance(s.get("day"), str) else None
        self.used = int(s.get("used") or 0)
        self.recent = [float(x) for x in s.get("recent") or [] if isinstance(x, (int, float))]
        self.paused_until = float(s.get("paused_until") or 0)

    def today(self):
        return time.strftime("%Y-%m-%d", time.gmtime(self.clock()))

    def lookups_today(self):
        return self.used if self.day == self.today() else 0

    def allow(self):
        t = self.clock()
        if self.day != self.today():
            self.day, self.used = self.today(), 0
        self.recent = [x for x in self.recent if t - x < 60]
        return t >= self.paused_until and len(self.recent) < self.per_minute and self.used < self.per_day

    def take(self):
        self.recent.append(self.clock())
        self.used += 1
        self.save()

    def pause(self, seconds):
        self.paused_until = self.clock() + seconds
        self.save()

    def save(self):
        if self.day is None:
            self.day = self.today()
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"day": self.day, "used": self.used, "recent": self.recent, "paused_until": self.paused_until}, f)
            os.replace(tmp, self.path)
        except OSError as e:
            print("VirusTotal limits not saved: %s" % e)


class Checker:
    def __init__(self, textfile, key_file, opener=urllib.request.urlopen, clock=time.time, per_minute=PER_MINUTE, per_day=PER_DAY, on_metrics=None):
        self.textfile, self.key_file, self.opener, self.clock, self.on_metrics = textfile, key_file, opener, clock, on_metrics
        self.limiter = Limiter(os.path.join(textfile, STATE), clock, per_minute, per_day)
        self.said_no_key = False

    def key(self):
        try:
            with open(self.key_file) as f:
                k = f.read().strip()
        except OSError:
            k = ""
        if not k and not self.said_no_key:
            print("VirusTotal: no API key in %s, fingerprints stay queued" % self.key_file)
            self.said_no_key = True
        return k or None

    def lookup(self, sha, key):
        req = urllib.request.Request(API + sha, headers={"x-apikey": key, "Accept": "application/json"}, method="GET")
        try:
            with self.opener(req, timeout=30) as r:
                return classify(json.loads(r.read()))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"vt": "unknown"}
            if e.code == 429:
                raise Quota()
            return {"vt": "error", "why": "HTTP %d" % e.code}
        except (OSError, ValueError, AttributeError) as e:
            return {"vt": "error", "why": type(e).__name__}

    def write(self, src, result):
        line = {"at": stamp(self.clock()), "path": src["path"], "size": src.get("size"), "sha256": src["sha256"], "hashed": src.get("hashed"), **result}
        try:
            append(self.textfile, line)
        except OSError as e:
            print("VirusTotal result not written (%s): %s" % (e, src["path"]))
            return
        if result["vt"] != "error" or src.get("vt") != "error":
            extra = " (%s of %s scanners)" % (result.get("malicious"), result.get("engines")) if "engines" in result else " (%s)" % result["why"] if result.get("why") else ""
            print("VirusTotal: %s: %s%s" % (src["path"], result["vt"], extra))

    def step(self):
        """Resolve what is queued: cached hashes at once, the rest by lookup within the limits."""
        latest, cache, failed = {}, {}, {}
        for i, x in enumerate(read_log(self.textfile)):
            latest[x["path"]] = (i, x)
            if x.get("vt") in FINAL and x.get("checked") is not None:
                cache[x["sha256"]] = x
            elif x.get("vt") == "error":
                failed[x["sha256"]] = max(failed.get(x["sha256"], 0), epoch(x.get("at")))
        pending = [x for _, x in sorted(latest.values(), key=lambda p: p[0]) if x.get("vt") in ("queued", "error")]
        key = None
        stop = False
        for x in pending:
            sha = x["sha256"]
            hit = cache.get(sha)
            if hit:
                self.write(x, {k: hit[k] for k in ("vt", "malicious", "suspicious", "engines", "checked") if k in hit})
                continue
            if stop or self.clock() - failed.get(sha, 0) < RETRY:
                continue
            key = key or self.key()
            if not key or not self.limiter.allow():
                stop = True
                continue
            self.limiter.take()
            try:
                result = self.lookup(sha, key)
            except Quota:
                self.limiter.pause(60)
                result = {"vt": "error", "why": "quota"}
                stop = True
            if result["vt"] in FINAL:
                result["checked"] = stamp(self.clock())
                cache[sha] = dict(result, sha256=sha)
            else:
                failed[sha] = self.clock()
            self.write(x, result)

    def counts(self):
        latest = {}
        for x in read_log(self.textfile):
            latest[x["path"]] = x.get("vt")
        out = {}
        for v in latest.values():
            if v in FINAL + ("queued", "error"):
                out[v] = out.get(v, 0) + 1
        out["lookups_today"] = self.limiter.lookups_today()
        return out

    def metrics(self):
        if self.on_metrics:
            self.on_metrics(self.counts())

    def run(self):
        print("VirusTotal fingerprint check on (hash lookups only, %d/min, %d/day)" % (self.limiter.per_minute, self.limiter.per_day), flush=True)
        while True:
            try:
                self.step()
                self.metrics()
            except Exception as e:  # the lookups must never stop the scan worker
                print("VirusTotal: %s" % type(e).__name__, flush=True)
            time.sleep(POLL)
