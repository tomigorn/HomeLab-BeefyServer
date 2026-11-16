"""Manual interaction (README "Manual interaction"): the page /_manual/ lists every download problem that needs a human, for every Seerr user, and lets them fix it.
Problems come from request-state.json (removed, Seerr hand-over failed, nothing found for days) and the Radarr/Sonarr queues (import blocked, wrong film, failed).
Every problem is tracked in one JSON file (first/last seen, actions, how it was resolved), kept 180 days after it is done; GET .../history exports it (admins).
The browser never sees keys, paths or download links: picks are indexes into what the backend itself read from Radarr/Sonarr minutes before."""
import datetime
import difflib
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse

NOW = time.time
CACHE = {}
OFFERS = {}
THROTTLE = {}
MEM = {}
STORE_LOCK = threading.Lock()
SEARCH_LOCK = threading.Lock()
THROTTLE_LOCK = threading.Lock()
OFFERS_LOCK = threading.Lock()
SLOTS = threading.BoundedSemaphore(4)
DAY = 86400
KEEP_DAYS = 180
NO_RELEASE_DAYS = 3
OVERRIDE_DAYS = 7
OVERRIDE_TAG = "override"
NIGHT_CHECK = (4 * 60, 4 * 60 + 40)
LOCAL = time.localtime
OVERRIDE_SETTLE = 1800
# rejections caused only by the override download itself (in the queue or as the file): the release is fine for the profile on its own
SELF_CAUSED = re.compile(r"\bqueue\b|existing (movie |episode )?file|not an upgrade|meets cutoff", re.I)
OLD_YEARS = 12
STALE = 600
SCAN_TTL = 60
OFFER_TTL = 600
RELEASE_GAP = 120
LOOKUP_GAP = 2
FILES_GAP = 10
# release searches wait in one FIFO (the arr runs them one after another anyway); a user clicking around is never refused, only flooding is
QUEUE_MAX = 5
QUEUE_PER_USER = 1
FLOOD_MAX = 20
FLOOD_WINDOW = 60
SEARCHES = {}
FLOOD = {}
QUEUE_LOCK = threading.Lock()
SEQ = [0]
SEERR_TAKE = 1000
ACTION_GAP = 20
GRACE = 1800
RELEASE_TIMEOUT = 120
DECLINED, FAILED_REQUEST = 3, 4
IMPORT_EVENTS = {"downloadFolderImported", "movieFileImported", "episodeFileImported"}
# a leftover queue entry of a download imported by hand (only once the library has the file): removed with its client copy like queue-watchdog's KEEP, never blocklisted
STALE_QUEUE = "removeFromClient=true&blocklist=false&skipRedownload=true"
PROBLEM_STATES = {"importBlocked", "importFailed", "failedPending", "failed"}
FAILED_STATES = {"failedPending", "failed"}
PID = re.compile(r"^[0-9a-f]{12}$")
ACTIONS = {"unmatched": {"link"}, "import": {"import", "block"}, "wrong-film": {"block"}, "failed": {"block"}, "no-release": {"profile", "grab", "override"}, "removed": {"readd", "delete"}}
RESOLVING = {"link", "import", "block", "grab", "override", "readd", "delete"}
OLDER = "older versions allowed"
NAMES_MAX = 6
# where the title part of a release name ends: its year, an episode tag or the resolution
TITLE_END = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)|\b[Ss]\d{1,2}[Ee]\d|\b\d{3,4}p\b")
DEFAULT_OLDER = "4K preferred · older versions allowed"
# Radarr/Sonarr status messages → a reason code the page words in EN/DE (first match wins)
REASONS = (
    ("id-match", re.compile(r"matched to (series|movie) by ID", re.I)),
    ("sample", re.compile(r"\bsample\b", re.I)),
    ("no-eligible", re.compile(r"no files found are eligible|no video files|not a video|invalid video file", re.I)),
    ("not-upgrade", re.compile(r"not (a custom format )?an? upgrade|not a custom format upgrade|existing (episode|movie) file", re.I)),
    ("unparsable", re.compile(r"unable to (parse|determine|identify)|unknown (series|movie|episode)", re.I)),
    ("not-in-release", re.compile(r"was unexpected|not found in (the )?release|missing from (the )?release|(episode|movie) .*not found", re.I)),
    ("dub-only", re.compile(r"\bdub|original[- ]language|not original", re.I)),
    ("failed", re.compile(r"\bfailed\b", re.I)),
)
# why Radarr/Sonarr rejected a release (first match), worded on the page
RELEASE_REASONS = (
    ("cf-score", re.compile(r"custom formats? .*score .*below", re.I)),
    ("required-terms", re.compile(r"required terms|must contain|contains one or more terms", re.I)),
    ("quality", re.compile(r"not wanted in profile|quality .*not (wanted|allowed)", re.I)),
    ("existing", re.compile(r"existing file", re.I)),
    ("seeders", re.compile(r"seeders", re.I)),
    ("blocklisted", re.compile(r"blocklist", re.I)),
    ("size", re.compile(r"larger than|smaller than|size", re.I)),
    ("wrong-title", re.compile(r"wrong (movie|series)|unknown (movie|series)|title mismatch", re.I)),
)
VIDEO = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm")
PROBE_CACHE = {}
PROBE_TIMEOUT = 20
PROBE_MAX = 20
PROBE_BUDGET = 60
PROBE_SCAN_BUDGET = 30
# local files in the usual video containers only: no network, no playlists (HLS, concat) that would open further inputs
PROBE_FORMATS = "matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,avi,mpegts"
MAIN_FILE = {}
CODECS = {"h264": "H.264", "hevc": "HEVC", "av1": "AV1", "mpeg4": "MPEG-4", "mpeg2video": "MPEG-2", "vc1": "VC-1", "vp9": "VP9",
          "eac3": "E-AC3", "ac3": "AC3", "truehd": "TrueHD", "dts": "DTS", "aac": "AAC", "flac": "FLAC", "opus": "Opus", "mp3": "MP3", "pcm_s16le": "PCM", "pcm_s24le": "PCM"}
SWISS = "Swiss German"
ID_MATCHED = re.compile(r"matched to (?:series|movie) by ID")
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
YEAR_SLACK = 2
EPISODE = (re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})(?:[ ._-]?[Ee-](\d{1,3}))?"), re.compile(r"(?<![\dx])(\d{1,2})x(\d{2,3})(?!\d)"))
NAMES = {"radarr": "Radarr", "sonarr": "Sonarr"}
# rejections a manual pick fixes (the arr could not tell which title or episode it is); any other one needs an admin
# a Done entry's current file from a known release (history: grabbed, then imported as this very file): checked at most hourly, a few per scan
CURRENT_TTL = 3600
CURRENT_MAX = 10
PUBLIC_CURRENT = ("ref", "release", "quality", "at", "season")
PUBLIC_REPLACE = ("mode", "release", "from", "user", "at", "state", "keep", "lastResult")
REPLACE_ACTIVE = {"grabbed", "downloading", "importing", "searching"}
REIMPORT_GAP = 1800
# Radarr/Sonarr hold back the import of a release that is not better than the file in the library; a release picked by hand to replace it is imported over it
NOT_BETTER = re.compile(r"not an? .*upgrade|existing (movie |episode )?file", re.I)
UNMATCHED = re.compile(r"unable to (parse|determine|identify|find)|not found|unknown (series|movie|episode)|matched to (series|movie) by id|no (series|movie|episode)s? (found|matched)", re.I)


def release_reason(message):
    for code, rx in RELEASE_REASONS:
        if rx.search(str(message or "")):
            return code
    return "unknown"


def reason_code(message):
    for code, rx in REASONS:
        if rx.search(str(message or "")):
            return code
    return "unknown"


def summarise(data, size):
    """ffprobe JSON → what the page shows: resolution, codec, HDR, audio (language, codec, channels), subtitle languages, size, minutes."""
    streams = [x for x in (data or {}).get("streams") or [] if isinstance(x, dict)]
    v = next((x for x in streams if x.get("codec_type") == "video" and not (x.get("disposition") or {}).get("attached_pic")), {})
    w, h = v.get("width") or 0, v.get("height") or 0
    res = next((name for name, wide, tall in (("2160p", 3200, 1800), ("1080p", 1700, 900), ("720p", 1100, 600), ("576p", 9999, 540), ("480p", 9999, 400)) if w >= wide or h >= tall), "SD") if v else None
    dovi = next((x for x in v.get("side_data_list") or [] if isinstance(x, dict) and ("DOVI" in str(x.get("side_data_type")) or "Dolby Vision" in str(x.get("side_data_type")))), None)
    if dovi is not None:
        hdr = "Dolby Vision" + {1: " + HDR10", 6: " + HDR10", 4: " + HLG", 2: " + SDR"}.get(dovi.get("dv_bl_signal_compatibility_id"), "")
    else:
        hdr = "HDR10" if v.get("color_transfer") == "smpte2084" else "HLG" if v.get("color_transfer") == "arib-std-b67" else None

    def lang(x):
        t = (x.get("tags") or {}).get("language")
        return t if isinstance(t, str) and t not in ("und", "") else None

    def channels(n):
        return {1: "1.0", 2: "2.0", 6: "5.1", 7: "6.1", 8: "7.1"}.get(n, f"{n} ch" if n else None)
    audio = [{"language": lang(x), "codec": x.get("profile") if x.get("codec_name") == "dts" and x.get("profile") and x.get("profile") != "DTS" else CODECS.get(x.get("codec_name"), str(x.get("codec_name") or "?").upper()),
              "channels": channels(x.get("channels"))} for x in streams if x.get("codec_type") == "audio"]
    try:
        minutes = round(float((data.get("format") or {}).get("duration")) / 60)
    except (TypeError, ValueError):
        minutes = None
    return {"resolution": res, "video": CODECS.get(v.get("codec_name"), str(v.get("codec_name") or "").upper() or None) if v else None, "hdr": hdr,
            "audio": audio[:12], "subtitles": [lang(x) or "?" for x in streams if x.get("codec_type") == "subtitle"][:20], "size": size, "minutes": minutes}


def ffprobe_command(path):
    return ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", PROBE_FORMATS,
            "-print_format", "json", "-show_format", "-show_streams", "file:" + path]


def run_ffprobe(path):
    try:
        r = subprocess.run(ffprobe_command(path), capture_output=True, timeout=PROBE_TIMEOUT, check=False)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


FFPROBE = run_ffprobe
STAT = os.stat


def inside(deps, path):
    """The real path when it lies inside a download folder (symlinks resolved first), else None."""
    roots = [os.path.realpath(x) for x in getattr(deps, "download_roots", None) or ()]
    if not roots or not isinstance(path, str) or not path:
        return None
    real = os.path.realpath(path)
    return real if any(real.startswith(x.rstrip("/") + "/") for x in roots) else None


def media_of(deps, path, run=True):
    """ffprobe summary of a download file (cached by path, size, mtime); run=False only reads the cache."""
    real = inside(deps, path)
    if not real or not real.lower().endswith(VIDEO) or not os.path.isfile(real):
        return None
    st = os.stat(real)
    key = (real, st.st_size, st.st_mtime_ns)
    if key not in PROBE_CACHE and not run:
        return None
    if key not in PROBE_CACHE:
        if len(PROBE_CACHE) > 300:
            PROBE_CACHE.clear()
        data = FFPROBE(real)
        PROBE_CACHE[key] = summarise(data, st.st_size) if data else None
    return PROBE_CACHE[key]


def depth_below_root(deps, real):
    for root in (os.path.realpath(x) for x in getattr(deps, "download_roots", None) or ()):
        if real.startswith(root.rstrip("/") + "/"):
            return len(os.path.relpath(real, root).split(os.sep))
    return 0


def biggest_video(deps, path):
    """The download's main video (largest, no sample) under its own folder, two levels deep at most; a category folder (tv, movies) right under a root is never walked."""
    real = inside(deps, path)
    if not real:
        return None
    if os.path.isfile(real):
        return real if real.lower().endswith(VIDEO) else None
    if depth_below_root(deps, real) < 2:
        return None
    best, todo = None, [(real, 0)]
    while todo:
        d, depth = todo.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False) and depth < 2:
                todo.append((e.path, depth + 1))
            elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(VIDEO) and "sample" not in e.name.lower():
                size = e.stat(follow_symlinks=False).st_size
                if best is None or size > best[0]:
                    best = (size, e.path)
    return best[1] if best else None


# our rules for a file (README "Manual interaction", file checks): running time off by at most runtime_ok = fine, up to runtime_warn = suspicious, more = likely the wrong file; GB per hour above these = "very big" like the Requests page
RULES = {"runtime_ok": 0.10, "runtime_warn": 0.25, "gb_per_hour_film": 30, "gb_per_hour_episode": 20}
LANG_CODES = {"english": {"eng", "en"}, "german": {"ger", "deu", "de", "gsw"}, "swiss german": {"gsw"}, "portuguese": {"por", "pt"}, "french": {"fre", "fra", "fr"},
              "spanish": {"spa", "es"}, "italian": {"ita", "it"}, "japanese": {"jpn", "ja"}, "korean": {"kor", "ko"}, "chinese": {"chi", "zho", "zh"},
              "mandarin": {"chi", "zho", "cmn", "zh"}, "cantonese": {"yue", "chi", "zho"}, "russian": {"rus", "ru"}, "dutch": {"dut", "nld", "nl"},
              "swedish": {"swe", "sv"}, "danish": {"dan", "da"}, "norwegian": {"nor", "nob", "nno", "no"}, "finnish": {"fin", "fi"}, "polish": {"pol", "pl"},
              "turkish": {"tur", "tr"}, "hindi": {"hin", "hi"}, "arabic": {"ara", "ar"}, "thai": {"tha", "th"}, "hebrew": {"heb", "he"}, "czech": {"cze", "ces", "cs"},
              "hungarian": {"hun", "hu"}, "greek": {"gre", "ell", "el"}, "icelandic": {"ice", "isl", "is"}, "persian": {"per", "fas", "fa"}, "indonesian": {"ind", "id"}}
GERMAN = LANG_CODES["german"]


def ladder(profile):
    """A Radarr/Sonarr quality profile as quality id → (rank, allowed, item name), plus the cutoff's rank, name and resolution."""
    out, cut = {}, (None, None, 0)
    for rank, it in enumerate((profile or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        quals = [x.get("quality") or {} for x in it.get("items") or []] if it.get("items") else [it.get("quality") or {}]
        name = it.get("name") or (quals[0].get("name") if quals else None)
        for q in quals:
            if is_int(q.get("id")):
                out[q["id"]] = (rank, bool(it.get("allowed")), q.get("name"))
        if (it.get("id") if it.get("items") else (it.get("quality") or {}).get("id")) == (profile or {}).get("cutoff"):
            cut = (rank, name, max([q.get("resolution") or 0 for q in quals] or [0]))
    return out, cut


def checks(m, c):
    """Every shown property against our rules: ok (✅), warn (⚠️), bad (❌), none (no rule applies / unknown)."""
    if not isinstance(m, dict):
        return []
    out = []
    langs = {a.get("language") for a in m.get("audio") or [] if isinstance(a, dict) and a.get("language")}
    original = str(c.get("original") or "").strip()
    codes = LANG_CODES.get(original.lower())
    if not codes or not langs:
        out.append({"rule": "original", "mark": "none", "language": original or None})
    else:
        out.append({"rule": "original", "mark": "ok" if langs & codes else "bad", "language": original})
    if original.lower() != "english":
        out.append({"rule": "english", "mark": "none" if not langs else "ok" if langs & LANG_CODES["english"] else "warn"})
    subs = {x for x in m.get("subtitles") or [] if x}
    out.append({"rule": "german", "mark": "ok" if (langs | subs) & GERMAN else "none"})
    steps, (cut_rank, cut_name, cut_res) = ladder(c.get("profile"))
    q = steps.get(c.get("quality"))
    # what the profile allows is fine (owner 2026-11-15: the 1080p fallback is part of a 4K profile); below its cutoff only a neutral "may upgrade later"
    if q is None:
        out.append({"rule": "quality", "mark": "none", "quality": None, "upgrade": None})
    else:
        below = cut_rank is not None and q[0] < cut_rank
        out.append({"rule": "quality", "mark": "ok" if q[1] else "bad", "quality": q[2], "upgrade": cut_name if q[1] and below else None})
    hdr = m.get("hdr")
    goal = "HDR" in str((c.get("profile") or {}).get("name") or "") and cut_res >= 2160
    bad = bool(hdr) and hdr.startswith("Dolby Vision") and not hdr.endswith(("HDR10", "HLG"))
    out.append({"rule": "hdr", "mark": "bad" if bad else "ok", "hdr": hdr or "SDR", "upgrade": not hdr and goal})
    minutes, expected = m.get("minutes"), c.get("expected")
    if minutes and expected:
        off = abs(minutes - expected) / expected
        out.append({"rule": "runtime", "mark": "ok" if off <= RULES["runtime_ok"] + 1e-9 else "warn" if off <= RULES["runtime_warn"] + 1e-9 else "bad", "minutes": minutes, "expected": expected})
    if m.get("size"):
        gbh = round(m["size"] / 1e9 / (minutes / 60), 1) if minutes else None
        limit = RULES["gb_per_hour_film"] if c.get("kind") == "movie" else RULES["gb_per_hour_episode"]
        out.append({"rule": "size", "mark": "warn" if gbh and gbh > limit else "none", "size": m["size"], "gbh": gbh, "limit": limit})
    return out


def check_context(t, profiles, kind, quality, expected):
    t = t if isinstance(t, dict) else {}
    profile = next((x for x in profiles or [] if isinstance(x, dict) and x.get("id") == t.get("qualityProfileId")), None)
    return {"kind": kind, "original": (t.get("originalLanguage") or {}).get("name"), "profile": profile, "quality": quality, "expected": expected or t.get("runtime") or None}


CLAMAV_CACHE = {}
VT_CACHE = {}


def scan_log(deps, vt=False):
    """ClamAV's per-file verdicts (Antivirus hook, clamav-results.jsonl and its rotated .1), or with vt the VirusTotal fingerprint verdicts next to it: path → last line."""
    path = getattr(deps, "clamav_results", None)
    if not path:
        return {}
    if vt:
        path = os.path.join(os.path.dirname(path), "virustotal-results.jsonl")
    cache = VT_CACHE if vt else CLAMAV_CACHE
    files = [path + ".1", path]
    try:
        key = tuple((os.stat(f).st_mtime_ns, os.stat(f).st_size) if os.path.exists(f) else None for f in files)
    except OSError:
        return {}
    if cache.get("key") == key:
        return cache["log"]
    log = {}
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        x = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(x, dict) and isinstance(x.get("path"), str):
                        log[x["path"]] = x
        except OSError:
            continue
    cache.update(key=key, log=log)
    return log


def runtime_fits(minutes, expected):
    """A too-big video whose parts scanned clean counts as safe only when its running time is plausible (the checks' ±runtime_warn)."""
    return bool(minutes and expected) and abs(minutes - expected) / expected <= RULES["runtime_warn"] + 1e-9


def with_runtime(scan, expected):
    """A parts-clean verdict with the running-time result the import gate decides on, so the page shows the same."""
    if scan.get("state") != "parts-clean":
        return scan
    return dict(scan, runtimeFits=runtime_fits(scan.get("minutes"), expected), expected=expected if is_int(expected) else None)


def import_safe(scan):
    return scan["state"] == "clean" or scan.get("runtimeFits") is True


def virus(deps, path):
    """clean / infected (with signature) / pending (queued for the background scan) / unknown (never scanned, too big, error)."""
    log = scan_log(deps)
    x = log.get(path) or (log.get(os.path.realpath(path)) if isinstance(path, str) else None) or {}
    state = {"clean": "clean", "infected": "infected", "queued": "pending", "parts-clean": "parts-clean", "parts-infected": "infected", "parts-error": "parts-error"}.get(x.get("verdict"), "unknown")
    if state in ("clean", "parts-clean"):
        # "clean" counts only for the very file that was scanned: same size, not changed after the scan
        try:
            st = STAT(path)
            fits = st.st_size == x.get("size") and (epoch(x.get("at")) or 0) >= st.st_mtime - 1
        except (OSError, TypeError):
            fits = False
        if not fits:
            state = "unknown"
    out = {"state": state, "at": x.get("at") if state != "unknown" else None, "signature": str(x.get("signature"))[:120] if state == "infected" and x.get("signature") else None}
    if state == "parts-clean":
        out.update(minutes=x.get("minutes") if is_int(x.get("minutes")) else None, parts=x.get("parts") if is_int(x.get("parts")) else None)
    if state == "parts-error":
        out["why"] = str(x.get("why") or "")[:80] or None
    vt = virustotal(deps, path)
    if vt is None and state == "pending":
        vt = {"state": "queued"}
    if vt:
        out["vt"] = vt
    return out


def virustotal(deps, path):
    """The VirusTotal fingerprint verdict of this very file (same size, hashed after its last change): queued / unchecked (lookup failed, retried) / unknown / clean / flagged / malware."""
    log = scan_log(deps, vt=True)
    x = log.get(path) or (log.get(os.path.realpath(path)) if isinstance(path, str) else None)
    if not x:
        return None
    try:
        st = STAT(path)
        if st.st_size != x.get("size") or (epoch(x.get("hashed")) or 0) < st.st_mtime - 1:
            return None
    except (OSError, TypeError):
        return None
    state = {"queued": "queued", "error": "unchecked", "unknown": "unknown", "clean": "clean", "flagged": "flagged", "malware": "malware"}.get(x.get("vt"))
    if not state:
        return None
    out = {"state": state}
    if state in ("clean", "flagged", "malware"):
        out.update(malicious=x.get("malicious") if is_int(x.get("malicious")) else None, engines=x.get("engines") if is_int(x.get("engines")) else None)
    if state in ("unknown", "clean", "flagged", "malware"):
        out["checked"] = x.get("checked") if isinstance(x.get("checked"), str) else None
    return out


class StoreUnreadable(Exception):
    pass


class Refused(Exception):
    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


def ts(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def epoch(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def brief(text):
    text = re.sub(r"https?://\S+|(?:/[^\s/]+)+/?", "…", str(text or "")).strip()
    return text if len(text) <= 160 else text[:159] + "…"


def year_of(s):
    m = YEAR.match(str(s or ""))
    return int(m.group()) if m else None


def read_state(path, now):
    try:
        p = pathlib.Path(path)
        if now - p.stat().st_mtime > STALE:
            return None
        data = json.loads(p.read_text())
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def older_profile(name, profiles):
    """The `· older versions allowed` twin of a profile; None when it is one already, Swiss German, or there is none."""
    if not name or OLDER in name or SWISS in name:
        return None
    names = [p.get("name") for p in profiles if isinstance(p, dict)]
    if name.startswith("Anime"):
        return next((n for n in names if n and n.startswith("Anime") and OLDER in n), None)
    want = DEFAULT_OLDER + (" - English original" if name.endswith(" - English original") else "")
    return want if want in names else None


def hard_rejection(reason):
    """A rejection only an admin may override: anything but "could not tell which title/episode", and always a sample."""
    reason = str(reason)
    return "sample" in reason.lower() or not UNMATCHED.search(reason)


def person(u):
    """A user's name for the page: Seerr's username / Jellyfin / Plex name, never the email Seerr falls back to."""
    u = u if isinstance(u, dict) else {}
    for k in ("username", "jellyfinUsername", "plexUsername"):
        v = u.get(k)
        if isinstance(v, str) and v.strip() and "@" not in v:
            return v.strip()[:60]
    return f"user {u.get('id')}"


def queue_kind(recs, year, kind):
    msgs = [str(m) for r in recs for sm in r.get("statusMessages") or [] for m in sm.get("messages") or []]
    if any(r.get("status") == "failed" or r.get("trackedDownloadState") in FAILED_STATES for r in recs):
        return "failed", {}
    if any(ID_MATCHED.search(m) for m in msgs):
        years = [int(y.group()) for y in YEAR.finditer(str(recs[0].get("title") or ""))]
        if kind == "movie" and year and years and not any(abs(year - y) <= YEAR_SLACK for y in years):
            return "wrong-film", {"releaseYear": years[-1], "year": year}
    return "import", {}


def plain(s):
    return re.sub(r"[\W_]+", " ", str(s).casefold()).strip()


def other_names(t):
    """The title's other names from Radarr/Sonarr: the original title first, then the alternate titles; deduplicated, without the title itself."""
    t = t if isinstance(t, dict) else {}
    seen, out = {plain(t.get("title") or "")}, []
    alts = [a.get("title") for a in t.get("alternateTitles") or [] if isinstance(a, dict)]
    for n in [t.get("originalTitle")] + alts:
        if not isinstance(n, str) or not n.strip():
            continue
        k = plain(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n.strip()[:100])
        if len(out) >= NAMES_MAX:
            break
    return out


def name_match(release, t, names):
    """The other name a release is called by (title part before its year, normalised), or None; a release named like the title itself needs no hint."""
    end = TITLE_END.search(release or "")
    part = plain(release[:end.start()] if end and end.start() else release or "")
    if not part or part == plain(YEAR.sub("", str((t or {}).get("title") or ""))):
        return None
    hit = next((n for n in names if plain(YEAR.sub("", n)) == part), None)
    return {"name": hit, "original": hit == (t or {}).get("originalTitle")} if hit else None


def pid_of(kind, key, extra=""):
    return hashlib.sha1(f"{kind}|{key}|{extra}".encode()).hexdigest()[:12]


def own_main_file(deps, arr, dl):
    """The download's biggest video from the arr's own file list for that download id (when its output path is a category folder or unknown)."""
    if dl in MAIN_FILE:
        return MAIN_FILE[dl]
    try:
        got = arr(f"/manualimport?downloadId={urllib.parse.quote(str(dl), safe='')}&filterExistingFiles=true")
    except Exception:
        return None
    best = None
    for x in got if isinstance(got, list) else []:
        if not isinstance(x, dict) or str(x.get("downloadId") or "").lower() != str(dl).lower():
            continue
        real = inside(deps, x.get("path"))
        if real and real.lower().endswith(VIDEO) and "sample" not in os.path.basename(real).lower() and os.path.isfile(real):
            size = os.path.getsize(real)
            if best is None or size > best[0]:
                best = (size, real)
    if len(MAIN_FILE) > 200:
        MAIN_FILE.clear()
    MAIN_FILE[dl] = best[1] if best else None
    return MAIN_FILE[dl]


def imported_by_hand(dn, arr, kind, recs, tid, since):
    """Radarr/Sonarr imported this download (history, after the problem began) and the title has its file(s), though the queue still says importBlocked."""
    dl = recs[0].get("downloadId")
    if not dl:
        return False
    try:
        got = arr(f"/history?page=1&pageSize=50&downloadId={urllib.parse.quote(str(dl), safe='')}")
        rows = got.get("records") if isinstance(got, dict) else got
        if not any(isinstance(h, dict) and h.get("eventType") in IMPORT_EVENTS and str(h.get("downloadId") or "").lower() == str(dl).lower()
                   and (epoch(h.get("date")) or 0) >= (since or 0) for h in rows or []):
            return False
        if kind == "movie":
            m = arr(f"/movie/{int(tid)}")
            return isinstance(m, dict) and bool(m.get("hasFile"))
        eps = sorted({r.get("episodeId") for r in recs if is_int(r.get("episodeId"))})
        got = arr("/episode?" + urllib.parse.urlencode([("episodeIds", e) for e in eps])) if eps else None
        have = {e.get("id") for e in got if isinstance(e, dict) and e.get("hasFile")} if isinstance(got, list) else set()
        return bool(eps) and set(eps) <= have
    except (dn.ApiError, TypeError, ValueError):
        return False


def scan(dn, deps, known, started=None, own=frozenset()):
    """Every open problem now: (problems by id, complete, files still to probe, problems imported by hand). Incomplete (a source unreadable) = nothing may be called gone.
    own: downloads of a release picked to replace a file; their held-back import is handled by the replacement, not shown as a problem."""
    now, complete, found, arrs, profiles, pending, imported = NOW(), True, {}, {}, {}, [], {}
    started = started or {}
    rs = read_state(deps.request_state_file, now) if deps.request_state_file else None
    if rs is None:
        complete, rs = False, {}
    try:
        reqs = (deps.seerr_admin("/request?take=1000&skip=0&filter=all&sort=added") or {}).get("results") if deps.seerr_admin else None
    except dn.ApiError:
        reqs = None
    if not isinstance(reqs, list):
        complete, reqs = False, []
    elif len(reqs) >= SEERR_TAKE:
        complete = False
    by_key, seerr_key = {}, {}
    for r in reqs:
        m = r.get("media") if isinstance(r, dict) else None
        if isinstance(m, dict) and m.get("tmdbId") and r.get("type") in dn.APP and r.get("status") != DECLINED and isinstance(r.get("requestedBy"), dict):
            key = f"{r['type']}/{m['tmdbId']}"
            by_key.setdefault(key, []).append(r)
            # Radarr/Sonarr may know the title under another TMDB id than Seerr: their own id (and the TVDB id) find the request too
            app = dn.APP[r["type"]]
            for ext in (m.get("externalServiceId"), m.get("externalServiceId4k")):
                if is_int(ext):
                    seerr_key.setdefault((app, "id", ext), key)
            if r["type"] == "tv" and is_int(m.get("tvdbId")):
                seerr_key.setdefault((app, "tvdb", m["tvdbId"]), key)

    def arr(app):
        if app not in arrs:
            arrs[app] = deps.arr(app)
        return arrs[app]

    def profile_list(app):
        if app not in profiles:
            got = arr(app)("/qualityprofile")
            profiles[app] = [p for p in got if isinstance(p, dict)] if isinstance(got, list) else []
        return profiles[app]

    def title_of(key):
        if known.get(key) and known[key][0]:
            return known[key]
        kind, _, tmdb = key.partition("/")
        try:
            t = deps.seerr_admin(f"/{kind}/{tmdb}") if deps.seerr_admin else None
        except dn.ApiError:
            t = None
        if not isinstance(t, dict):
            return key, None
        return (t.get("title") or t.get("name") or key), year_of(t.get("releaseDate") or t.get("firstAirDate"))

    def add(ptype, key, title, year, details, extra="", **internal):
        rq = by_key.get(key) or []
        names = list(dict.fromkeys(person(r["requestedBy"]) for r in rq))
        pid = pid_of(ptype, key, extra)
        internal.setdefault("requests", rq)
        ident = f"{ptype}|{internal['app']}|{internal['downloadId']}" if internal.get("downloadId") else f"{ptype}|{key}"
        found[pid] = {"id": pid, "type": ptype, "key": key, "ident": ident, "title": title, "year": year, "requesters": names, "details": details, "_": internal}

    def base(key):
        kind, _, tmdb = key.partition("/")
        rq = by_key.get(key) or []
        media = rq[0]["media"] if rq else {}
        return {"kind": kind, "app": dn.APP.get(kind), "tmdb": int(tmdb) if tmdb.isdigit() else None, "media": media,
                "seasons": sorted({s.get("seasonNumber") for r in rq for s in r.get("seasons") or [] if isinstance(s, dict) and isinstance(s.get("seasonNumber"), int)})}

    for key in rs.get("removed") or []:
        if isinstance(key, str) and key.partition("/")[0] in dn.APP:
            add("removed", key, *title_of(key), {"reason": "removed"}, **base(key))
    for key, q in (rs.get("queue") or {}).items():
        if isinstance(q, dict) and q.get("code") == "seerr" and key.partition("/")[0] in dn.APP:
            add("unmatched", key, *title_of(key), {"reason": "unmatched"}, **base(key))
    unreleased = rs.get("unreleased") or {}
    for key, last in (rs.get("noRelease") or {}).items():
        b = base(key) if key.partition("/")[0] in dn.APP else None
        rq = by_key.get(key) or []
        created = [epoch(r.get("createdAt")) for r in rq]
        if b is None or key in unreleased or not rq or None in created or now - min(created) < NO_RELEASE_DAYS * DAY:
            continue
        title, year = title_of(key)
        details = {"reason": "no-release", "lastSearch": last, "profile": None, "older": None, "old": False, "seasons": b["seasons"], "overrideFrom": ts(min(created) + OVERRIDE_DAYS * DAY)}
        try:
            t = dn.find_title(b["kind"], arr(b["app"]), {"mediaInfo": b["media"]}, b["tmdb"])
            if t:
                title, year = t.get("title") or title, t.get("year") or year
                name = next((p.get("name") for p in profile_list(b["app"]) if p.get("id") == t.get("qualityProfileId")), None)
                details.update(profile=name, older=older_profile(name, profile_list(b["app"])), names=other_names(t))
                b["arrId"] = t.get("id")
                b["runtime"] = t.get("runtime")
        except dn.ApiError:
            pass
        details["old"] = bool(year) and int(year) <= time.gmtime(now).tm_year - OLD_YEARS
        add("no-release", key, title, year, details, **b)
    for app, kind, extra in (("radarr", "movie", "&includeMovie=true"), ("sonarr", "tv", "&includeSeries=true&includeEpisode=true")):
        try:
            q = arr(app)(f"/queue?page=1&pageSize=1000{extra}")
        except dn.ApiError:
            complete = False
            continue
        if not isinstance(q, dict) or not isinstance(q.get("records"), list):
            complete = False
            continue
        CACHE[("queue", app)] = [r for r in q["records"] if isinstance(r, dict)]
        groups = {}
        for r in q["records"]:
            if not isinstance(r, dict) or not (r.get("trackedDownloadState") in PROBLEM_STATES or r.get("trackedDownloadStatus") == "error" or r.get("status") == "failed"):
                continue
            groups.setdefault(r.get("downloadId") or f"q{r.get('id')}", []).append(r)
        for dl, recs in groups.items():
            t = recs[0].get("movie" if kind == "movie" else "series") or {}
            tid = recs[0].get("movieId" if kind == "movie" else "seriesId")
            key = f"{kind}/{t['tmdbId']}" if t.get("tmdbId") else f"{app}:{tid}"
            if key not in by_key:
                key = seerr_key.get((app, "id", tid)) or seerr_key.get((app, "tvdb", t.get("tvdbId"))) or key
            ptype, more = queue_kind(recs, t.get("year"), kind)
            if ptype != "failed" and ((app, str(dl).lower()) in own or (app, tid, str(recs[0].get("title") or "").lower()) in own):
                continue
            pid = pid_of(ptype, key, dl)
            if imported_by_hand(dn, arr(app), kind, recs, tid, epoch(started.get(pid) or recs[0].get("added"))):
                for qid in [r.get("id") for r in recs if is_int(r.get("id"))]:
                    try:
                        arr(app)(f"/queue/{qid}?{STALE_QUEUE}", "DELETE")
                    except dn.ApiError as e:
                        print(f"manual: leftover queue entry {qid} not removed: {dn.redact(e)}", flush=True)
                print(f"manual: {key} ({t.get('title')}) imported by hand, leftover queue entry removed", flush=True)
                imported[pid] = ts(now)
                continue
            msg = next((m for r in recs for sm in r.get("statusMessages") or [] for m in sm.get("messages") or [] if m), None) or recs[0].get("errorMessage")
            msgs = [m for r in recs for sm in r.get("statusMessages") or [] for m in sm.get("messages") or [] if m] + [recs[0].get("errorMessage") or ""]
            code = ptype if ptype in ("wrong-film", "failed") else next((c for c in map(reason_code, msgs) if c != "unknown"), "unknown")
            details = {"reason": code, "release": str(recs[0].get("title") or "")[:200], "message": brief(msg), "names": other_names(t), **more}
            details["nameMatch"] = name_match(details["release"], t, details["names"])
            main_file = biggest_video(deps, recs[0].get("outputPath"))
            if not main_file and recs[0].get("downloadId") and recs[0].get("outputPath"):
                main_file = own_main_file(deps, arr(app), recs[0]["downloadId"])
            if main_file:
                details["virus"] = virus(deps, main_file)
                details["media"] = media_of(deps, main_file, run=False)
                if details["media"] is None:
                    pending.append(main_file)
                if details["media"]:
                    try:
                        plist = profile_list(app)
                    except dn.ApiError:
                        plist = []
                    quality = ((recs[0].get("quality") or {}).get("quality") or {}).get("id")
                    details["checks"] = checks(details["media"], check_context(t, plist, kind, quality, (recs[0].get("episode") or {}).get("runtime") if len(recs) == 1 else None))
            eps = sorted({r.get("episodeId") for r in recs if isinstance(r.get("episodeId"), int)})
            if kind == "tv":
                details["episodes"] = len(recs)
            add(ptype, key, t.get("title") or key, t.get("year"), details, dl, kind=kind, app=app, arrId=tid, downloadId=recs[0].get("downloadId"),
                queueIds=[r.get("id") for r in recs if isinstance(r.get("id"), int)], episodeIds=eps, tmdb=t.get("tmdbId"), media={}, seasons=[])
    return found, complete, pending, imported


def load(deps):
    """The stored problem list; a missing file is an empty list, an unreadable one raises (it must never be overwritten)."""
    if not deps.manual_file:
        return MEM.setdefault("problems", {})
    path = pathlib.Path(deps.manual_file)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        p = data["problems"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StoreUnreadable(f"{path.name}: {type(e).__name__}") from None
    if not isinstance(p, dict):
        raise StoreUnreadable(f"{path.name}: no problem list")
    return p


def save(deps, records):
    if not deps.manual_file:
        MEM["problems"] = records
        return
    path = pathlib.Path(deps.manual_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"problems": records}, ensure_ascii=False, separators=(",", ":")))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


GONE = {"import": "imported", "no-release": "found"}
BY_ITSELF = {"gone", "imported", "found"}


def same_problem(r, p):
    """An older record of this problem under another key (the key moved, e.g. to Seerr's TMDB id): same download, or before idents existed, same type and release name."""
    if r.get("id") == p["id"]:
        return False
    if r.get("ident"):
        return r["ident"] == p.get("ident")
    release = (r.get("details") or {}).get("release")
    return r.get("type") == p["type"] and bool(release) and release == (p.get("details") or {}).get("release")


def migrate(records, pid, p):
    """Fold older records of the same problem into pid: first seen and history kept; a resolution caused by the key change is dropped."""
    for old_id in [k for k, r in records.items() if isinstance(r, dict) and same_problem(r, p)]:
        old = records.pop(old_id)
        if old.get("resolved") and old["resolved"].get("how") in BY_ITSELF:
            old["resolved"] = None
        target = records.get(pid)
        if target is None:
            records[pid] = dict(old, id=pid)
            continue
        target["firstSeen"] = min(filter(None, (target.get("firstSeen"), old.get("firstSeen"))), default=None)
        target["actions"] = sorted((old.get("actions") or []) + (target.get("actions") or []), key=lambda a: a.get("at") or "")


def auto_blocks(deps, records, now):
    """Releases Movie-Downloads' language-block.py blocked on import (no German, English or original-language audio): one Done record per download, the latest file's outcome."""
    for e in scout_files(deps)["language-block.json"].get("entries") or []:
        if not isinstance(e, dict) or e.get("blocked") is not True or not isinstance(e.get("key"), str) or now - (epoch(e.get("at")) or 0) > KEEP_DAYS * DAY:
            continue
        ident = f"auto-block|{e.get('app')}|{e.get('downloadId')}"
        pid = pid_of("auto-block", e["key"], ident)
        details = {"reason": "no-language-audio", "release": str(e.get("release") or "")[:200], "audio": [str(x) for x in e.get("languages") or []][:12],
                   "original": e.get("original"), "file": e.get("file"), "searched": bool(e.get("searched"))}
        r = records.get(pid)
        if r is None:
            records[pid] = {"id": pid, "type": "auto-block", "key": e["key"], "ident": ident, "title": e.get("title") or e["key"], "year": e.get("year"), "requesters": [],
                            "details": details, "firstSeen": e.get("at"), "lastSeen": e.get("at"), "resolved": {"at": e.get("at"), "how": "auto-block"},
                            "actions": [{"at": e.get("at"), "user": None, "userId": None, "action": "auto-block", "result": e.get("file")}]}
        else:
            r["details"] = details


def merge(records, found, complete, now, imported=()):
    for pid in imported:
        r = records.get(pid)
        if isinstance(r, dict) and r.get("resolved") is None:
            r["resolved"] = {"at": ts(now), "how": "imported"}
    for pid, p in found.items():
        migrate(records, pid, p)
        pub = {k: v for k, v in p.items() if k != "_"}
        r = records.get(pid)
        if r is None:
            records[pid] = {**pub, "firstSeen": ts(now), "lastSeen": ts(now), "actions": [], "resolved": None}
            continue
        r.update(pub, lastSeen=ts(now))
        res = r.get("resolved")
        if res and (res.get("how") in BY_ITSELF or now - (epoch(res.get("at")) or 0) >= GRACE):
            r["resolved"] = None
            r.setdefault("actions", []).append({"at": ts(now), "user": None, "userId": None, "action": "reopened", "result": None})
    for pid, r in list(records.items()):
        if complete and r.get("resolved") is None and pid not in found and not replacing(r):
            r["resolved"] = {"at": ts(now), "how": GONE.get(r.get("type"), "gone")}
        res = r.get("resolved")
        if res and now - (epoch(res.get("at")) or now) > KEEP_DAYS * DAY and not open_override(r):
            del records[pid]


def open_override(r):
    o = r.get("override") if isinstance(r, dict) else None
    return isinstance(o, dict) and o.get("state") not in ("replaced", "blocked")


def replacing(r):
    o = r.get("replace") if isinstance(r, dict) else None
    return isinstance(o, dict) and o.get("state") in REPLACE_ACTIVE


def release_offers(r):
    """A Done entry whose current file came from a known release: it may be replaced by another release or blocked."""
    return (isinstance(r, dict) and bool(r.get("resolved")) and r["resolved"].get("how") != "delete" and isinstance(r.get("current"), dict)
            and not open_override(r) and not replacing(r))


def replacement_downloads(records):
    """The downloads (and, before their id is known, the release names) of every active pick."""
    own = set()
    for r in records.values():
        o = r.get("replace") if replacing(r) else None
        if o and o.get("mode") == "replace":
            own |= {(o.get("app"), d) for d in o.get("downloadIds") or []}
            own.add((o.get("app"), o.get("arrId"), str(o.get("release") or "").lower()))
    return frozenset(own)


def arr_title(dn, deps, r):
    """A record's Radarr/Sonarr title: (app, kind, title) or None; Seerr's own link first (its TMDB id may differ from the arr's)."""
    key = str(r.get("key") or "")
    kind, _, rest = key.partition("/")
    if kind in dn.APP and rest.isdigit():
        app, path = dn.APP[kind], "/movie" if kind == "movie" else "/series"
        arr = deps.arr(app)
        media = deps.seerr_admin(f"/{kind}/{rest}") if deps.seerr_admin else None
        media = media if isinstance(media, dict) else {}
        ext = (media.get("mediaInfo") or {}).get("externalServiceId")
        if is_int(ext) and ext > 0:
            t = arr(f"{path}/{ext}")
            if isinstance(t, dict) and is_int(t.get("id")):
                return app, kind, t
        t = dn.find_title(kind, arr, media, int(rest))
        return (app, kind, t) if isinstance(t, dict) and is_int(t.get("id")) else None
    app, _, aid = key.partition(":")
    if app in NAMES and aid.isdigit():
        kind = "movie" if app == "radarr" else "tv"
        t = deps.arr(app)(f"/{'movie' if kind == 'movie' else 'series'}/{int(aid)}")
        return (app, kind, t) if isinstance(t, dict) and is_int(t.get("id")) else None
    return None


def title_history(arr, kind, arr_id):
    got = arr(f"/history/movie?movieId={int(arr_id)}" if kind == "movie" else f"/history/series?seriesId={int(arr_id)}")
    got = got.get("records") if isinstance(got, dict) else got
    return sorted((h for h in got or [] if isinstance(h, dict)), key=lambda h: h.get("date") or "", reverse=True)


def current_release(dn, deps, r):
    """Where the title's current file came from: the grab whose download was imported as that very file (film: the movie file, series: every episode file of that download); None when unknown."""
    found = arr_title(dn, deps, r)
    if not found:
        return None
    app, kind, t = found
    arr = deps.arr(app)
    hist = title_history(arr, kind, t["id"])
    imports = [h for h in hist if h.get("eventType") in IMPORT_EVENTS and h.get("downloadId")]
    if kind == "movie":
        mf = t.get("movieFile") if t.get("hasFile") else None
        if not isinstance(mf, dict) or not is_int(mf.get("id")):
            return None
        imp = next((h for h in imports if str((h.get("data") or {}).get("fileId")) == str(mf["id"])), None)
        if imp is None:
            return None
        dl, episodes, files, season, quality = imp["downloadId"], [], [mf["id"]], None, ((mf.get("quality") or {}).get("quality") or {}).get("name")
    else:
        eps = arr(f"/episode?seriesId={int(t['id'])}")
        by_ep = {e["id"]: e for e in eps if isinstance(e, dict) and is_int(e.get("id"))} if isinstance(eps, list) else {}
        parts = str(r.get("ident") or "").split("|")
        dl = parts[2] if len(parts) == 3 and parts[1] == app else None
        if not dl:
            seasons = set((r.get("details") or {}).get("seasons") or [])
            dl = next((h["downloadId"] for h in imports if (by_ep.get(h.get("episodeId")) or {}).get("hasFile")
                       and (not seasons or by_ep[h["episodeId"]].get("seasonNumber") in seasons)), None)
        mine = {}
        for h in imports:
            if str(h["downloadId"]).lower() == str(dl).lower() and h.get("episodeId") not in mine:
                mine[h.get("episodeId")] = h
        if not mine or not all((by_ep.get(e) or {}).get("hasFile") and str(by_ep[e].get("episodeFileId")) == str((h.get("data") or {}).get("fileId")) for e, h in mine.items()):
            return None
        imp = next(iter(mine.values()))
        dl, episodes = imp["downloadId"], sorted(mine)
        files = sorted({by_ep[e]["episodeFileId"] for e in episodes})
        season = min(by_ep[e].get("seasonNumber") or 0 for e in episodes)
        quality = ((imp.get("quality") or {}).get("quality") or {}).get("name")
    grab = next((h for h in hist if h.get("eventType") == "grabbed" and str(h.get("downloadId") or "").lower() == str(dl).lower() and is_int(h.get("id"))), None)
    if grab is None:
        return None
    ref = hashlib.sha1(f"{app}|{t['id']}|{dl}|{files}".encode()).hexdigest()[:12]
    return {"ref": ref, "release": str(grab.get("sourceTitle") or "")[:200], "quality": quality, "at": imp.get("date"), "season": season,
            "app": app, "kind": kind, "arrId": t["id"], "downloadId": dl, "grabId": grab["id"], "fileIds": files, "episodeIds": episodes, "runtime": t.get("runtime")}


def releases_known(dn, deps, records, now):
    """Which Done entries' files came from a known release (offers: pick another, block it); rechecked at most hourly, CURRENT_MAX per scan."""
    n = 0
    for pid, r in records.items():
        if not isinstance(r, dict) or not r.get("resolved") or r["resolved"].get("how") == "delete" or open_override(r) or replacing(r):
            continue
        if now - (epoch(r.get("currentChecked")) or 0) < CURRENT_TTL:
            continue
        if n >= CURRENT_MAX:
            break
        n += 1
        try:
            r["current"] = current_release(dn, deps, r)
        except Exception as e:
            print(f"manual: current release of {r.get('key')} unknown: {type(e).__name__} {dn.redact(e)}", flush=True)
        r["currentChecked"] = ts(now)


def import_entry(x, kind, arr_id, dl, episode_ids=None):
    entry = {"path": x["path"], "quality": x.get("quality"), "languages": x.get("languages") or [], "releaseGroup": x.get("releaseGroup"),
             "downloadId": x.get("downloadId") or dl, "indexerFlags": x.get("indexerFlags") or 0}
    if kind == "movie":
        entry["movieId"] = arr_id
    else:
        entry.update(seriesId=arr_id, episodeIds=episode_ids, releaseType=x.get("releaseType") or "singleEpisode")
    return entry


def force_import(deps, arr, o, blocked, now):
    """A picked release held back only for not being better than the library's file: imported over it (the arr's own upgrade replaces the old file), only once the virus gates pass."""
    msgs = [str(m) for q in blocked for sm in q.get("statusMessages") or [] for m in sm.get("messages") or [] if m]
    if not msgs or not all(NOT_BETTER.search(m) for m in msgs):
        return "failed", "held back for another reason (" + brief(msgs[0] if msgs else "unknown") + "), see the Open tab; the current file stays"
    if now - (epoch(o.get("importAt")) or 0) < REIMPORT_GAP:
        return "importing", o.get("lastResult") or "import started"
    dl = str(blocked[0]["downloadId"])
    got = arr(f"/manualimport?downloadId={urllib.parse.quote(dl, safe='')}&filterExistingFiles=true")
    props = [x for x in got if isinstance(x, dict) and x.get("path") and str(x.get("downloadId") or "").lower() == dl.lower()] if isinstance(got, list) else []
    files = []
    for x in props:
        name = os.path.basename(str(x["path"])).lower()
        if not name.endswith(VIDEO) or "sample" in name:
            continue
        if not all(NOT_BETTER.search(str(rj.get("reason") if isinstance(rj, dict) else rj)) for rj in x.get("rejections") or []):
            return "failed", "a file was rejected for another reason, see the Open tab; the current file stays"
        eps = [e["id"] for e in x.get("episodes") or [] if isinstance(e, dict) and is_int(e.get("id"))]
        if o["kind"] == "tv" and not eps:
            return "failed", "Sonarr could not tell an episode, see the Open tab; the current file stays"
        files.append((x, eps))
    if o["kind"] == "movie" and files:
        files = [max(files, key=lambda f: f[0].get("size") or 0)]
    if not files:
        return "failed", "no video file to import; the current file stays"
    for x, eps in files:
        expected = (x.get("episodes") or [{}])[0].get("runtime") if eps else None
        scan = with_runtime(virus(deps, x["path"]), expected or o.get("runtime"))
        vt = (scan.get("vt") or {}).get("state")
        if scan["state"] == "infected" or vt == "malware":
            return "failed", "malware found, not imported; the current file stays"
        if vt == "flagged" or not import_safe(scan) and scan["state"] not in ("pending", "unknown"):
            return "failed", "the virus scan needs an admin, see the Open tab; the current file stays"
        if not import_safe(scan):
            return "importing", "waiting for the virus scan"
    arr("/command", "POST", {"name": "ManualImport", "files": [import_entry(x, o["kind"], o["arrId"], dl, eps) for x, eps in files], "importMode": "auto"})
    o["importAt"] = ts(now)
    return "importing", "import started, it replaces the current file"


def step_replacement(deps, arr, o, queue, now):
    """One look at a pick or a block: (state, result)."""
    since = epoch(o.get("at")) or 0
    hist = [h for h in title_history(arr, o["kind"], o["arrId"]) if (epoch(h.get("date")) or 0) > since]
    imports = [h for h in hist if h.get("eventType") in IMPORT_EVENTS]
    if o.get("mode") == "reject":
        old = str(o.get("downloadId") or "").lower()
        if any(str(h.get("downloadId") or "").lower() != old and (o["kind"] == "movie" or h.get("episodeId") in (o.get("episodeIds") or [])) for h in imports):
            return "done", "another release was imported"
        return o.get("state"), "searching"
    field = "movieId" if o["kind"] == "movie" else "seriesId"
    release = str(o.get("release") or "").lower()
    ids = set(o.get("downloadIds") or []) | {str(h["downloadId"]).lower() for h in hist if h.get("eventType") == "grabbed" and h.get("downloadId") and str(h.get("sourceTitle") or "").lower() == release}
    own = [q for q in queue if isinstance(q, dict) and q.get(field) == o["arrId"] and q.get("downloadId")
           and (str(q["downloadId"]).lower() in ids or str(q.get("title") or "").lower() == release)]
    ids |= {str(q["downloadId"]).lower() for q in own}
    o["downloadIds"] = sorted(ids)
    if ids and any(str(h.get("downloadId") or "").lower() in ids for h in imports):
        for q in own:
            if is_int(q.get("id")):
                try:
                    arr(f"/queue/{q['id']}?{STALE_QUEUE}", "DELETE")
                except Exception:
                    pass
        return "done", f"{o.get('release')} imported, it replaced {o.get('from')}"
    if any(q.get("status") == "failed" or q.get("trackedDownloadState") in FAILED_STATES for q in own):
        return "failed", "the download failed; the current file stays"
    blocked = [q for q in own if q.get("trackedDownloadState") in ("importBlocked", "importFailed")]
    if blocked:
        return force_import(deps, arr, o, blocked, now)
    if own:
        return "downloading", "downloading"
    if now - since > OVERRIDE_SETTLE:
        return "failed", "the download went away without an import; the current file stays"
    return o.get("state"), o.get("lastResult")


def track_replacements(dn, deps, records, now):
    """Every active pick or block: done when its (or, after a block, another) release was imported; a failed pick leaves the old file and returns to Done."""
    for pid, r in records.items():
        if not replacing(r):
            continue
        o = r["replace"]
        queue = CACHE.get(("queue", o.get("app")))
        if queue is None:
            continue
        try:
            state, result = step_replacement(deps, deps.arr(o["app"]), o, queue, now)
        except Exception as e:
            print(f"manual replace {pid}: {type(e).__name__} {dn.redact(e)}", flush=True)
            continue
        o.update(state=state, lastResult=result, lastCheck=ts(now))
        if state in ("done", "failed"):
            how = "replaced" if state == "done" else "replace-failed"
            r["resolved"] = {"at": ts(now), "how": how}
            r.setdefault("actions", []).append({"at": ts(now), "user": None, "userId": None, "action": how, "result": result})
            r.pop("current", None)
            r.pop("currentChecked", None)
            print(f"manual replace {pid} ({r.get('title')}): {result}", flush=True)


def blockable_override(r):
    return open_override(r) and r["override"].get("state") in ("grabbed", "downloading")


def override_queue(queue, o):
    """The queue items of an override download: by its learned download ids, before those are known by the release name."""
    ids = set(o.get("downloadIds") or [])
    return [q for q in queue if isinstance(q, dict) and q.get("movieId" if o.get("kind") == "movie" else "seriesId") == o.get("arrId") and q.get("downloadId")
            and (str(q["downloadId"]).lower() in ids or not ids and str(q.get("title") or "").lower() == str(o.get("release") or "").lower())]


def seeds_of(deps, ids):
    """Swarm seeds per torrent hash from qBittorrent (None when unreadable)."""
    if not ids or not getattr(deps, "qbit", None):
        return {}
    try:
        got = deps.qbit("/torrents/info?hashes=" + "|".join(ids))
    except Exception:
        return {}
    return {str(t.get("hash")).lower(): max(t.get("num_seeds") or 0, t.get("num_complete") or 0) for t in got if isinstance(t, dict)} if isinstance(got, list) else {}


def track(deps, records, now):
    """Where each override download stands: its download ids learned from the queue, downloading (with seeds), or finished."""
    for r in records.values():
        if not open_override(r):
            continue
        o = r["override"]
        queue = CACHE.get(("queue", o.get("app")))
        if queue is None:
            continue
        ids = set(o.get("downloadIds") or [])
        own = override_queue(queue, o)
        if own:
            o["downloadIds"] = sorted(ids | {str(q["downloadId"]).lower() for q in own})
            o["episodeIds"] = sorted(set(o.get("episodeIds") or []) | {q["episodeId"] for q in own if is_int(q.get("episodeId"))})
            if o.get("state") in ("grabbed", "downloading", "finished"):
                o["state"] = "downloading"
            if o.get("protocol") == "torrent":
                seeds = seeds_of(deps, o["downloadIds"])
                if seeds:
                    o["seeds"] = max(seeds.values())
        elif o.get("state") == "downloading" or o.get("state") == "grabbed" and now - (epoch(o.get("at")) or now) > OVERRIDE_SETTLE:
            o["state"] = "finished"


def refresh(dn, deps, force=False):
    """The current problems; force = a scan that started no earlier than this call. One scan at a time, callers waiting for it share its result."""
    start = NOW()

    def cached():
        c = CACHE.get("scan")
        return c[1] if c and (c[0] >= start or not force and NOW() - c[0] < SCAN_TTL) else None
    hit = cached()
    if hit is not None:
        return hit
    with STORE_LOCK:
        hit = cached()
        if hit is not None:
            return hit
        now = NOW()
        try:
            records, readable = load(deps), True
        except StoreUnreadable as e:
            print(f"manual: problem list unreadable, not saving until fixed: {e}", flush=True)
            records, readable = {}, False
        found, complete, pending, imported = scan(dn, deps, {r.get("key"): (r.get("title"), r.get("year")) for r in records.values() if isinstance(r, dict)},
                                                  {pid: r.get("firstSeen") for pid, r in records.items() if isinstance(r, dict)}, replacement_downloads(records))
        if readable:
            merge(records, found, complete, now, imported)
            auto_blocks(deps, records, now)
            track(deps, records, now)
            track_replacements(dn, deps, records, now)
            releases_known(dn, deps, records, now)
            save(deps, records)
        CACHE["scan"] = (now, found)
    # ffprobe outside the lock (it may take seconds per file); the next scan shows what it found
    probed, began = False, time.monotonic()
    for path in dict.fromkeys(pending):
        if time.monotonic() - began > PROBE_SCAN_BUDGET:
            break
        if media_of(deps, path) is not None:
            probed = True
    if probed:
        CACHE.pop("scan", None)
    return found


def record(deps, pid, me, action, result, resolve, override=None, replace=None):
    now = NOW()
    with STORE_LOCK:
        try:
            records = load(deps)
        except StoreUnreadable as e:
            print(f"manual: problem list unreadable, action not recorded: {e}", flush=True)
            return None
        r = records.get(pid)
        if r is None:
            return None
        r.setdefault("actions", []).append({"at": ts(now), "user": person(me), "userId": me.get("id"), "action": action, "result": result})
        if resolve:
            r["resolved"] = {"at": ts(now), "how": action}
        if override:
            r["override"] = override
        if replace:
            r.update(replace=replace, resolved=None)
            r.pop("current", None)
            r.pop("currentChecked", None)
        save(deps, records)
    return r


def throttle(key, gap, mark=True):
    with THROTTLE_LOCK:
        now = NOW()
        if now - THROTTLE.get(key, -gap - 1) < gap:
            return False
        if mark:
            THROTTLE[key] = now
        return True


def keep(pid, kind, me, data):
    """A list shown to one user, picked from by index: stored per user with a token the POST must send back."""
    token, now = secrets.token_hex(8), NOW()
    with OFFERS_LOCK:
        for k in [k for k, o in OFFERS.items() if now - o[0] > OFFER_TTL]:
            del OFFERS[k]
        OFFERS[(pid, kind, me.get("id"))] = (now, token, data)
    return token


def mine(pid, kind, me):
    """The caller's own still valid list (re-opening it is no new arr call): (token, data) or None."""
    with OFFERS_LOCK:
        o = OFFERS.get((pid, kind, me.get("id")))
    return (o[1], o[2]) if o and NOW() - o[0] <= OFFER_TTL else None


def offer(pid, kind, me, req):
    with OFFERS_LOCK:
        o = OFFERS.get((pid, kind, me.get("id")))
    if not o or NOW() - o[0] > OFFER_TTL or not isinstance(req.get("token"), str) or not secrets.compare_digest(o[1], req["token"]):
        return None
    return o[2]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def lookup(dn, deps, me, p, q):
    if p["type"] != "unmatched":
        raise Refused(400, "bad request")
    term = (q.get("term") or [p["title"] or ""])[0].strip()
    if not term or len(term) > 100:
        raise Refused(400, "bad request")
    if not throttle(("lookup", me.get("id"), p["id"]), LOOKUP_GAP):
        raise Refused(429, "slow down")
    i = p["_"]
    got = deps.arr(i["app"])(f"/{'movie' if i['kind'] == 'movie' else 'series'}/lookup?term={urllib.parse.quote_plus(term)}")
    items = [x for x in got if isinstance(x, dict)][:10] if isinstance(got, list) else []

    def score(x):
        s = difflib.SequenceMatcher(None, norm(x.get("title")), norm(p["title"])).ratio()
        y, py = x.get("year"), p.get("year")
        return s + (0.3 if y and py and y == py else 0.1 if y and py and abs(y - py) <= 1 else 0)
    scores = [score(x) for x in items]
    best = max(range(len(items)), key=lambda n: scores[n]) if items and max(scores) >= 0.6 else None
    token = keep(p["id"], "lookup", me, items)
    field = "tmdbId" if i["kind"] == "movie" else "tvdbId"
    return {"token": token, "results": [{"n": n, "title": str(x.get("title") or ""), "year": x.get("year"), "overview": str(x.get("overview") or "")[:200], field: x.get(field),
                         "inLibrary": bool(x.get("id")), "probable": n == best} for n, x in enumerate(items)]}


def episode_guess(name, by_number):
    for rx in EPISODE:
        m = rx.search(name)
        if m:
            s, first = int(m.group(1)), int(m.group(2))
            last = int(m.group(3)) if rx is EPISODE[0] and m.group(3) else first
            ids = [by_number.get((s, e)) for e in range(first, max(first, min(last, first + 30)) + 1)]
            return [x for x in ids if x]
    return []


def rescanned(deps, data):
    """A kept file list with today's virus scan state (it changes while the list is open)."""
    pub = dict(data["public"])
    expected = data.get("expected") or []
    pub["files"] = [dict(f, virus=with_runtime(virus(deps, data["props"][f["n"]]["path"]), expected[f["n"]] if f["n"] < len(expected) else None)) for f in pub["files"]]
    return pub


def files(dn, deps, me, p, q):
    if p["type"] != "import":
        raise Refused(400, "bad request")
    had = mine(p["id"], "files", me)
    if had and not q.get("refresh"):
        return dict(rescanned(deps, had[1]), token=had[0])
    recent = CACHE.get(("files", p["id"]))
    if recent and NOW() - recent[0] < FILES_GAP and not q.get("refresh"):
        return dict(rescanned(deps, recent[1]), token=keep(p["id"], "files", me, recent[1]))
    if not throttle(("files", p["id"]), FILES_GAP):
        raise Refused(429, "slow down")
    i = p["_"]
    arr = deps.arr(i["app"])
    dl = str(i.get("downloadId") or "")
    if not dl:
        raise Refused(400, "no download")
    # downloadId alone: with seriesId/movieId Sonarr lists the title's library folder instead
    got = arr(f"/manualimport?downloadId={urllib.parse.quote(dl, safe='')}&filterExistingFiles=true")
    props = [x for x in got if isinstance(x, dict) and x.get("path") and str(x.get("downloadId") or "").lower() == dl.lower()] if isinstance(got, list) else []
    episodes = []
    if i["kind"] == "tv":
        eps = arr(f"/episode?seriesId={i['arrId']}")
        episodes = sorted([e for e in eps if isinstance(e, dict) and is_int(e.get("id"))] if isinstance(eps, list) else [], key=lambda e: ((e.get("seasonNumber") or 0) == 0, e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))
    by_number = {(e.get("seasonNumber"), e.get("episodeNumber")): e["id"] for e in episodes}
    runtime = {e["id"]: e.get("runtime") for e in episodes}
    try:
        title = arr(f"/{'movie' if i['kind'] == 'movie' else 'series'}/{i['arrId']}")
        plist = arr("/qualityprofile")
    except Exception:
        title, plist = None, []
    out, probing, expected = [], time.monotonic(), []
    biggest = max(range(len(props)), key=lambda n: props[n].get("size") or 0) if props else None
    for n, x in enumerate(props):
        name = str(x.get("relativePath") or x.get("name") or x["path"]).replace("\\", "/").rsplit("/", 1)[-1]
        queued = i.get("episodeIds") or []
        guess = ([e.get("id") for e in x.get("episodes") or [] if isinstance(e, dict) and is_int(e.get("id"))] or episode_guess(name, by_number)
                 or (queued if len(queued) == 1 else [])) if i["kind"] == "tv" else []
        out.append({"n": n, "name": name, "size": x.get("size") or 0, "quality": ((x.get("quality") or {}).get("quality") or {}).get("name") or (x.get("quality") or {}).get("name"),
                    "languages": [str(lang.get("name")) for lang in x.get("languages") or [] if isinstance(lang, dict) and lang.get("name")],
                    "rejections": [brief(r.get("reason") if isinstance(r, dict) else r) for r in x.get("rejections") or []][:5],
                    "reasons": [reason_code(r.get("reason") if isinstance(r, dict) else r) for r in x.get("rejections") or []][:5],
                    "media": media_of(deps, x["path"]) if n < PROBE_MAX and time.monotonic() - probing < PROBE_BUDGET else None,
                    "episodeIds": guess, "probable": bool(guess) if i["kind"] == "tv" else n == biggest})
        qid = ((x.get("quality") or {}).get("quality") or {}).get("id")
        ctx = check_context(title, plist, i["kind"], qid, runtime.get(guess[0]) if len(guess) == 1 else None)
        out[-1]["checks"] = checks(out[-1]["media"], ctx)
        out[-1]["virus"] = with_runtime(virus(deps, x["path"]), ctx["expected"])
        expected.append(ctx["expected"])
    public = {"kind": i["kind"], "files": out, "episodes": [{"id": e["id"], "label": f"S{e.get('seasonNumber') or 0:02d}E{e.get('episodeNumber') or 0:02d}", "title": str(e.get("title") or "")[:80]} for e in episodes]}
    data = {"props": props, "episodes": {e["id"]: bool(e.get("hasFile")) for e in episodes}, "public": public, "expected": expected}
    CACHE[("files", p["id"])] = (NOW(), data)
    token = keep(p["id"], "files", me, data)
    return dict(public, token=token)


def may_override(p):
    return NOW() >= (epoch((p.get("details") or {}).get("overrideFrom")) or float("inf"))


def releases(dn, deps, me, p, q):
    out = release_list(dn, deps, me, p, q)
    out["override"] = may_override(p)
    return out


def release_list(dn, deps, me, p, q):
    if p["type"] not in ("no-release", "replace") or not p["_"].get("arrId"):
        raise Refused(400, "bad request")
    i = p["_"]
    season = None
    if i["kind"] == "tv" and i["seasons"]:
        raw = (q.get("season") or [str(i["seasons"][0])])[0]
        season = int(raw) if raw.isdigit() else None
        if season not in i["seasons"]:
            raise Refused(400, "bad request")
    had = mine(p["id"], "releases", me)
    if had and had[1]["season"] == season and not q.get("refresh"):
        return {"state": "done", "token": had[0], "releases": with_scouts(deps, had[1]), "dropped": had[1].get("dropped", 0), "season": season, "cached": True}
    cached = CACHE.get(("releases", p["id"]))
    if cached and cached["season"] == season and NOW() - cached["at"] < RELEASE_GAP:
        return {"state": "done", "token": keep(p["id"], "releases", me, cached), "releases": with_scouts(deps, cached), "dropped": cached.get("dropped", 0), "season": season, "cached": True}
    expire()
    with QUEUE_LOCK:
        job = SEARCHES.get(p["id"])
        if job and job["state"] in ("queued", "running"):
            if job["season"] != season:
                raise Refused(429, "slow down")
            return waiting(job)
        if job and job["state"] == "failed":
            del SEARCHES[p["id"]]
            raise Refused(502, "not reachable")
        waiting_jobs = [j for j in SEARCHES.values() if j["state"] in ("queued", "running")]
        if sum(1 for j in waiting_jobs if j["state"] == "queued") >= QUEUE_MAX:
            raise Refused(429, "search queue full")
        if sum(1 for j in waiting_jobs if j["user"] == me.get("id") and j["state"] == "queued") >= QUEUE_PER_USER:
            raise Refused(429, "too many searches")
        SEQ[0] += 1
        path = f"/release?movieId={i['arrId']}" if i["kind"] == "movie" else f"/release?seriesId={i['arrId']}" + (f"&seasonNumber={season}" if season is not None else "")
        job = SEARCHES[p["id"]] = {"pid": p["id"], "seq": SEQ[0], "state": "queued", "season": season, "path": path, "app": i["app"], "deps": deps,
                                   "kind": i["kind"], "arrId": i["arrId"], "runtime": i.get("runtime"), "current": i.get("current"),
                                   "user": me.get("id"), "title": p.get("title"), "queued": NOW()}
    KICK(deps)
    with QUEUE_LOCK:
        if job["state"] == "failed":
            SEARCHES.pop(p["id"], None)
            raise Refused(502, "not reachable")
        if job["state"] != "done":
            return waiting(job)
    cached = CACHE[("releases", p["id"])]
    return {"state": "done", "token": keep(p["id"], "releases", me, cached), "releases": with_scouts(deps, cached), "dropped": cached.get("dropped", 0), "season": season, "cached": False}


def waiting(job):
    """Where a search stands: running, or queued behind others (the first of them named)."""
    ahead = sorted((j for j in SEARCHES.values() if j["state"] in ("queued", "running") and j["seq"] < job["seq"]), key=lambda j: (j["state"] != "running", j["seq"]))
    return {"state": job["state"], "season": job["season"], "ahead": len(ahead), "waitingFor": ahead[0]["title"] if ahead and job["state"] == "queued" else None}


# a release's details page at the indexer (`infoUrl`): only public http(s) hosts, credential parameters cut, anything key-like left = no link
LINK_SECRETS = {"apikey", "api_key", "api", "key", "token", "passkey", "rsskey", "r", "i", "uid", "user", "username", "pass", "password", "auth", "authkey",
                "torrent_pass", "sid", "session", "secret", "sig", "signature"}
LINK_IDS = {"id", "guid"}
KEYLIKE = re.compile(r"[A-Za-z0-9_-]{24,}")
INTERNAL_HOST = re.compile(r"(^|\.)(homelab|local|lan|internal|localhost)$|prowlarr|gluetun", re.I)
PRIVATE_INDEXERS = ()


def safe_link(url):
    if not isinstance(url, str) or not url:
        return None
    try:
        sp = urllib.parse.urlsplit(url)
        host = sp.hostname or ""
        port = sp.port
    except ValueError:
        return None
    if sp.scheme not in ("http", "https") or not host or sp.username or sp.password or port not in (None, 80, 443):
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    if "." not in host or INTERNAL_HOST.search(host) or re.search(r"apikey|api_key|passkey|rsskey|token", sp.path, re.I):
        return None
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(sp.query, keep_blank_values=True) if k.lower() not in LINK_SECRETS]
    if any(k.lower() not in LINK_IDS and KEYLIKE.search(v) for k, v in keep):
        return None
    return urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path, urllib.parse.urlencode(keep), ""))


def private_indexer(x):
    """An account is needed to read the page: every usenet indexer here, plus listed private torrent trackers."""
    name = str(x.get("indexer") or "")
    return x.get("protocol") == "usenet" or any(p.lower() in name.lower() for p in PRIVATE_INDEXERS)


NFO_SOURCES = {"NZBgeek", "NzbPlanet", "Treasure Maps"}
DESCRIPTION_PAGE = re.compile(r"^https?://(?:[^/]*thepiratebay[^/]*|knaben\.[a-z]+/thepiratebay)/description\.php\?id=\d+$|^https?://(www\.)?1337x\.[a-z]+/torrent/\d+/")
READS_PER_USER = 5
READS_MAX = 30


def nfo_gid(guid):
    m = re.search(r"(?:guid=|/details/|/getnzb/|[?&]id=)([0-9a-fA-F]{16,64})\b", guid or "")
    return m.group(1).lower() if m else None


def btih_key(guid, info_url):
    m = re.search(r"btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})", guid or "")
    return "btih:" + m.group(1).upper() if m else "url:" + (info_url or guid or "")


def readable(x):
    """The NFO scout (usenet NZBgeek, NzbPlanet, Treasure Maps) or the description scout (TPB, Knaben→TPB, 1337x) can read this release."""
    if x.get("protocol") == "usenet":
        return str(x.get("indexer") or "").replace(" (Prowlarr)", "") in NFO_SOURCES and bool(nfo_gid(x.get("guid")))
    return bool(DESCRIPTION_PAGE.match(str(x.get("infoUrl") or "")))


def request_id(x):
    return hashlib.sha1(f"{x.get('indexer')}|{x.get('guid')}".encode()).hexdigest()[:16]


def scout_files(deps):
    """Movie-Downloads' state (read-only mount): the NFO scout's releases, the description scout's entries and the language block's log, re-read when they change."""
    d = getattr(deps, "scout_dir", None)
    out = {}
    for name in ("nfo-scout.json", "description-scout.json", "language-block.json"):
        path = os.path.join(d, name) if d else None
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        key = (st.st_mtime_ns, st.st_size) if st else None
        c = CACHE.get(("scout", name))
        if not c or c[0] != key:
            data = {}
            if st:
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    data = {}
            c = CACHE[("scout", name)] = (key, data if isinstance(data, dict) else {})
        out[name] = c[1]
    return out


def clean_lines(v):
    return [str(x)[:160] for x in v if isinstance(x, str)][:5] if isinstance(v, list) else []


def scout_of(deps, x, files):
    """What a scout read about this release, or where an on-demand read stands."""
    if x.get("protocol") == "usenet":
        e = (files["nfo-scout.json"].get("releases") or {}).get(f"{str(x.get('indexer') or '').replace(' (Prowlarr)', '')}|{nfo_gid(x.get('guid'))}")
        if isinstance(e, dict) and e.get("verdict") in ("langs", "none", "push", "pushed", "skip") and isinstance(e.get("langs"), list):
            return {"state": "read", "source": "nfo", "langs": [str(l)[:40] for l in e["langs"]][:12], "lines": clean_lines(e.get("lines"))}
    else:
        key = btih_key(x.get("guid"), x.get("infoUrl"))
        e = files["description-scout.json"].get("read|" + key)
        if isinstance(e, dict) and isinstance(e.get("langs"), list):
            return {"state": "read", "source": "description", "langs": [str(l)[:40] for l in e["langs"]][:12], "lines": clean_lines(e.get("lines"))}
        e = files["description-scout.json"].get(key)
        if isinstance(e, dict) and e.get("verdict") == "match":
            return {"state": "read", "source": "description", "langs": ["Swiss German"], "lines": []}
    d = getattr(deps, "scout_requests", None)
    if d:
        rid = request_id(x)
        if os.path.exists(os.path.join(d, rid + ".req")):
            return {"state": "pending"}
        try:
            with open(os.path.join(d, rid + ".result.json"), encoding="utf-8") as f:
                res = json.load(f)
        except (OSError, ValueError):
            res = None
        if isinstance(res, dict) and res.get("state") in ("refused", "failed"):
            return {"state": res["state"], "why": str(res.get("why") or "")[:120] or None}
    return None


def with_scouts(deps, data):
    files = scout_files(deps)
    items = data.get("items") or []
    out = []
    for pub in data.get("public") or []:
        x = items[pub["n"]] if pub["n"] < len(items) else {}
        out.append(dict(pub, scout=scout_of(deps, x, files), readable=readable(x) and bool(getattr(deps, "scout_requests", None))))
    return out


def do_describe(deps, me, p, req):
    """Ask the beefy-side runner (Movie-Downloads scripts/scout-requests.py) to read one release's NFO or description: a request file, no keys here."""
    o = offer(p["id"], "releases", me, req)
    n = req.get("release")
    d = getattr(deps, "scout_requests", None)
    if o is None or not is_int(n) or not 0 <= n < len(o["items"]) or not d:
        raise Refused(400, "bad request")
    x = o["items"][n]
    if not readable(x):
        raise Refused(400, "bad request")
    rid = request_id(x)
    path = os.path.join(d, rid + ".req")
    if os.path.exists(path):
        return "already asked"
    pending = [f for f in os.listdir(d) if f.endswith(".req")]
    mine_ = [f for f in pending if request_user(os.path.join(d, f)) == me.get("id")]
    if len(mine_) >= READS_PER_USER:
        raise Refused(429, "too many reads")
    if len(pending) >= READS_MAX:
        raise Refused(429, "too many reads")
    try:
        os.unlink(os.path.join(d, rid + ".result.json"))
    except OSError:
        pass
    body = {"id": rid, "indexer": x.get("indexer"), "protocol": x.get("protocol"), "guid": x.get("guid"), "infoUrl": x.get("infoUrl"), "title": x.get("title"),
            "user": me.get("id"), "at": int(NOW())}
    tmp = os.path.join(d, rid + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.replace(tmp, path)
    print(f"manual: user {me.get('id')} asks to read {x.get('indexer')} '{x.get('title')}'", flush=True)
    return "asked"


def request_user(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("user")
    except (OSError, ValueError, AttributeError):
        return None


def matched(x, job):
    """A release Radarr/Sonarr mapped to this very title (and season): indexers that ignore the query return their newest uploads, those are dropped."""
    if job.get("kind") == "movie":
        return x.get("mappedMovieId") == job.get("arrId")
    return x.get("mappedSeriesId") == job.get("arrId") and (job.get("season") is None or x.get("mappedSeasonNumber") == job.get("season"))


def release_data(got, job):
    season = job.get("season")
    every = [x for x in got if isinstance(x, dict) and x.get("guid") and is_int(x.get("indexerId"))] if isinstance(got, list) else []
    items = [x for x in every if matched(x, job)]
    dropped = len(every) - len(items)
    if dropped:
        print(f"manual release search {job.get('title')}: {len(items)} matched, {dropped} of other or unknown titles dropped", flush=True)
    current = job.get("current")
    for x in items:
        # replacing a file: the current release is never picked again, a rejection caused only by the current file does not count
        x["_current"] = bool(current) and str(x.get("title") or "") == current
        soft = bool(current) and all(SELF_CAUSED.search(str(r.get("reason") if isinstance(r, dict) else r)) for r in x.get("rejections") or [])
        x["_rejected"] = x["_current"] or bool(x.get("rejected")) and not soft
    items.sort(key=lambda x: (x["_rejected"], -(x.get("customFormatScore") or 0), -(x.get("seeders") or 0)))
    items = items[:100]
    public = [{"n": n, "title": str(x.get("title") or "")[:200], "size": x.get("size") or 0, "quality": ((x.get("quality") or {}).get("quality") or {}).get("name"),
               "languages": [str(lang.get("name")) for lang in x.get("languages") or [] if isinstance(lang, dict) and lang.get("name")],
               "seeders": x.get("seeders"), "leechers": x.get("leechers"), "score": x.get("customFormatScore"), "rejected": x["_rejected"], "current": x["_current"],
               "rejections": [brief(r.get("reason") if isinstance(r, dict) else r) for r in x.get("rejections") or []][:5],
               "reasons": [release_reason(r.get("reason") if isinstance(r, dict) else r) for r in x.get("rejections") or []][:5],
               "gbh": round((x.get("size") or 0) / 1e9 / (job["runtime"] / 60), 1) if job.get("kind") == "movie" and job.get("runtime") and x.get("size") else None,
               "indexer": str(x.get("indexer") or "")[:60], "protocol": x.get("protocol"), "age": x.get("age"),
               "link": safe_link(x.get("infoUrl")), "private": private_indexer(x)} for n, x in enumerate(items)]
    return {"season": season, "at": NOW(), "public": public, "dropped": dropped, "app": job.get("app"), "arrId": job.get("arrId"),
            "items": [{"guid": x["guid"], "indexerId": x["indexerId"], "rejected": x["_rejected"], "current": x["_current"], "title": str(x.get("title") or "")[:200],
                       "indexer": str(x.get("indexer") or "")[:60], "protocol": x.get("protocol"), "infoUrl": x.get("infoUrl"), "seeders": x.get("seeders")} for x in items]}


def expire():
    """Finished and failed searches are forgotten after OFFER_TTL: the next request starts afresh."""
    with QUEUE_LOCK:
        for k in [k for k, j in SEARCHES.items() if j["state"] in ("done", "failed") and NOW() - j["queued"] > OFFER_TTL]:
            del SEARCHES[k]


def drain(deps=None):
    """Run the queued release searches one at a time, oldest first; one drainer at a time, a job queued meanwhile is picked up too."""
    expire()
    while True:
        if not SEARCH_LOCK.acquire(blocking=False):
            return
        try:
            while True:
                with QUEUE_LOCK:
                    job = min((j for j in SEARCHES.values() if j["state"] == "queued"), key=lambda j: j["seq"], default=None)
                    if job is None:
                        break
                    job["state"] = "running"
                try:
                    data = release_data(job["deps"].arr(job["app"])(job["path"], timeout=RELEASE_TIMEOUT), job)
                    CACHE[("releases", job["pid"])] = data
                    state = "done"
                except Exception as e:
                    print(f"manual release search {job['title']}: {type(e).__name__} {str(e).split('?')[0][:120]}", flush=True)
                    state = "failed"
                with QUEUE_LOCK:
                    job["state"] = state
                    for k in [k for k, j in SEARCHES.items() if j["state"] == "done" and NOW() - j["queued"] > OFFER_TTL]:
                        del SEARCHES[k]
        finally:
            SEARCH_LOCK.release()
        with QUEUE_LOCK:
            if not any(j["state"] == "queued" for j in SEARCHES.values()):
                return


def start_drain(deps):
    threading.Thread(target=drain, args=(deps,), daemon=True).start()


KICK = start_drain


def flooding(me):
    """More than FLOOD_MAX list calls (lookup, files, releases) in FLOOD_WINDOW seconds by one user."""
    with THROTTLE_LOCK:
        now = NOW()
        hits = [t for t in FLOOD.get(me.get("id"), []) if now - t < FLOOD_WINDOW]
        hits.append(now)
        FLOOD[me.get("id")] = hits
        return len(hits) > FLOOD_MAX


def search(arr, i, episode_ids=None):
    if i["kind"] == "movie":
        arr("/command", "POST", {"name": "MoviesSearch", "movieIds": [i["arrId"]]})
    elif episode_ids:
        arr("/command", "POST", {"name": "EpisodeSearch", "episodeIds": episode_ids})
    else:
        arr("/command", "POST", {"name": "SeriesSearch", "seriesId": i["arrId"]})


def existing(arr, kind, obj):
    if kind == "movie":
        got = arr(f"/movie?tmdbId={int(obj['tmdbId'])}")
    else:
        got = arr(f"/series?tvdbId={int(obj['tvdbId'])}")
    return bool(got) if isinstance(got, list) else False


def add_title(deps, i, obj):
    app, kind = i["app"], i["kind"]
    arr = deps.arr(app)
    profiles = [p["id"] for p in arr("/qualityprofile") or [] if isinstance(p, dict)]
    roots = [r["path"] for r in arr("/rootfolder") or [] if isinstance(r, dict)]
    req = (i.get("requests") or [{}])[0]
    try:
        servers = deps.seerr_admin(f"/settings/{app}") if deps.seerr_admin else []
    except Exception:
        servers = []
    server = next((s for s in servers or [] if isinstance(s, dict) and s.get("isDefault") and not s.get("is4k")), {})
    anime = kind == "tv" and obj.get("seriesType") == "anime"
    want_profile = req.get("profileId") if req.get("profileId") in profiles else server.get("activeAnimeProfileId" if anime and server.get("activeAnimeProfileId") else "activeProfileId")
    want_root = req.get("rootFolder") if req.get("rootFolder") in roots else server.get("activeAnimeDirectory" if anime and server.get("activeAnimeDirectory") else "activeDirectory")
    body = {k: v for k, v in obj.items() if k != "id"}
    body.update(qualityProfileId=want_profile if want_profile in profiles else profiles[0], rootFolderPath=want_root if want_root in roots else roots[0], monitored=True, tags=[])
    if kind == "movie":
        body.update(minimumAvailability=obj.get("minimumAvailability") or "released", addOptions={"searchForMovie": True})
        arr("/movie", "POST", body)
    else:
        wanted = set(i.get("seasons") or [])
        body["seasons"] = [{**s, "monitored": (s.get("seasonNumber") in wanted) if wanted else (s.get("seasonNumber") or 0) > 0} for s in obj.get("seasons") or [] if isinstance(s, dict)]
        body.update(seasonFolder=True, addOptions={"searchForMissingEpisodes": True})
        arr("/series", "POST", body)


def do_link(deps, me, admin, p, req):
    items = offer(p["id"], "lookup", me, req)
    n = req.get("pick")
    if items is None or not is_int(n) or not 0 <= n < len(items):
        raise Refused(400, "bad request")
    i, obj = p["_"], items[n]
    field = "tmdbId" if i["kind"] == "movie" else "tvdbId"
    if not is_int(obj.get(field)):
        raise Refused(400, "bad request")
    media = i.get("media") or {}
    same = obj.get("tmdbId") == i["tmdb"] if i["kind"] == "movie" else (is_int(media.get("tvdbId")) and obj.get("tvdbId") == media["tvdbId"]) or (is_int(obj.get("tmdbId")) and obj.get("tmdbId") == i["tmdb"])
    if not same and not admin:
        raise Refused(403, "another title needs an admin")
    if not same:
        print(f"manual: admin {me.get('id')} links {p['key']} to {field} {obj[field]}", flush=True)
    arr = deps.arr(i["app"])
    if obj.get("id") or existing(arr, i["kind"], obj):
        result = f"already in {NAMES[i['app']]}"
    else:
        add_title(deps, i, obj)
        result = f"added to {NAMES[i['app']]} ({field} {obj[field]})"
    seerr_id = i["tmdb"] if i["kind"] == "movie" else (i.get("media") or {}).get("tvdbId")
    if obj[field] == seerr_id and deps.seerr_admin:
        for r in i.get("requests") or []:
            if r.get("status") == FAILED_REQUEST:
                deps.seerr_admin(f"/request/{r['id']}/retry", "POST")
                result += ", Seerr request retried"
    return result


def do_import(deps, me, admin, p, req):
    o = offer(p["id"], "files", me, req)
    want = req.get("files")
    i = p["_"]
    if o is None or not isinstance(want, list) or not 0 < len(want) <= 200:
        raise Refused(400, "bad request")
    seen, out = set(), []
    for f in want:
        n = f.get("n") if isinstance(f, dict) else None
        if not is_int(n) or not 0 <= n < len(o["props"]) or n in seen:
            raise Refused(400, "bad request")
        seen.add(n)
        x = o["props"][n]
        scan = with_runtime(virus(deps, x["path"]), (o.get("expected") or [])[n] if n < len(o.get("expected") or []) else None)
        if scan["state"] == "infected":
            print(f"manual: user {me.get('id')} import of {p['key']} refused, malware ({scan['signature']})", flush=True)
            raise Refused(403, "malware")
        vt = (scan.get("vt") or {}).get("state")
        if vt == "malware":
            print(f"manual: user {me.get('id')} import of {p['key']} refused, known malware on VirusTotal", flush=True)
            raise Refused(403, "malware")
        if vt == "flagged" and not admin:
            raise Refused(403, "flagged")
        if vt == "flagged":
            print(f"manual: admin {me.get('id')} imports {p['key']} flagged by {scan['vt'].get('malicious')} VirusTotal scanners", flush=True)
        safe = import_safe(scan)
        if not safe and not admin:
            raise Refused(403, "virus scan pending")
        if not safe:
            print(f"manual: admin {me.get('id')} imports {p['key']} with virus scan {scan['state']}", flush=True)
        hard = [str(rj.get("reason") if isinstance(rj, dict) else rj) for rj in x.get("rejections") or [] if hard_rejection(rj.get("reason") if isinstance(rj, dict) else rj)]
        eps = None
        if i["kind"] != "movie":
            eps = f.get("episodeIds")
            if not isinstance(eps, list) or not 0 < len(eps) <= 50 or not all(is_int(e) and e in o["episodes"] for e in eps):
                raise Refused(400, "bad request")
            if any(o["episodes"][e] for e in eps):
                hard.append("episode already has a file")
        entry = import_entry(x, i["kind"], i["arrId"], i["downloadId"], eps)
        if hard and not admin:
            raise Refused(403, "rejected file needs an admin")
        if hard:
            print(f"manual: admin {me.get('id')} overrides for {p['key']}: {'; '.join(brief(h) for h in hard)}", flush=True)
        out.append(entry)
    deps.arr(i["app"])("/command", "POST", {"name": "ManualImport", "files": out, "importMode": "auto"})
    return f"import of {len(out)} file(s) started"


def do_block(deps, me, admin, p, req):
    i = p["_"]
    if not i.get("queueIds"):
        raise Refused(400, "bad request")
    arr = deps.arr(i["app"])
    arr("/queue/bulk?removeFromClient=true&blocklist=true", "DELETE", {"ids": i["queueIds"]})
    search(arr, i, i.get("episodeIds"))
    return "release removed and blocked, searching again"


def do_profile(deps, me, admin, p, req):
    i, name = p["_"], p["details"].get("older")
    arr = deps.arr(i["app"])
    target = next((x.get("id") for x in arr("/qualityprofile") or [] if isinstance(x, dict) and x.get("name") == name), None) if name else None
    if target is None or not i.get("arrId"):
        raise Refused(400, "bad request")
    if i["kind"] == "movie":
        arr("/movie/editor", "PUT", {"movieIds": [i["arrId"]], "qualityProfileId": target})
    else:
        arr("/series/editor", "PUT", {"seriesIds": [i["arrId"]], "qualityProfileId": target})
    search(arr, i)
    return f"profile {name}, searching again"


def do_grab(deps, me, admin, p, req):
    o = offer(p["id"], "releases", me, req)
    n = req.get("release")
    if o is None or not is_int(n) or not 0 <= n < len(o["items"]) or o["items"][n]["rejected"]:
        raise Refused(400, "bad request")
    x = o["items"][n]
    deps.arr(p["_"]["app"])("/release", "POST", {"guid": x["guid"], "indexerId": x["indexerId"]})
    return f"grabbed {x['title']}"


def tag_id(arr):
    got = arr("/tag")
    tid = next((t.get("id") for t in got if isinstance(t, dict) and t.get("label") == OVERRIDE_TAG), None) if isinstance(got, list) else None
    return tid if tid is not None else arr("/tag", "POST", {"label": OVERRIDE_TAG})["id"]


def set_tag(arr, kind, arr_id, how):
    tid = tag_id(arr)
    if kind == "movie":
        arr("/movie/editor", "PUT", {"movieIds": [arr_id], "tags": [tid], "applyTags": how})
    else:
        arr("/series/editor", "PUT", {"seriesIds": [arr_id], "tags": [tid], "applyTags": how})


def do_override(deps, me, admin, p, req):
    """A release the profile rejected, downloaded anyway after OVERRIDE_DAYS of nothing found; the title gets the override tag (nightly search, no wake for a seedless torrent)."""
    o = offer(p["id"], "releases", me, req)
    n = req.get("release")
    if o is None or not is_int(n) or not 0 <= n < len(o["items"]) or not o["items"][n]["rejected"]:
        raise Refused(400, "bad request")
    if not may_override(p):
        raise Refused(403, "too early")
    i, x = p["_"], o["items"][n]
    arr = deps.arr(i["app"])
    set_tag(arr, i["kind"], i["arrId"], "add")
    arr("/release", "POST", {"guid": x["guid"], "indexerId": x["indexerId"]})
    i["override"] = {"at": ts(NOW()), "user": person(me), "userId": me.get("id"), "release": x["title"], "seeders": x.get("seeders"), "protocol": x.get("protocol"),
                     "indexer": x.get("indexer"), "app": i["app"], "kind": i["kind"], "arrId": i["arrId"], "season": o.get("season"), "state": "grabbed",
                     "downloadIds": [], "episodeIds": [], "seeds": None, "lastCheck": None, "lastResult": None}
    return f"downloaded anyway: {x['title']}"


def replaced(history, o):
    """Another download than the override was imported after it: for a series every episode the override brought."""
    ids, since = set(o.get("downloadIds") or []), epoch(o.get("at")) or 0
    others = [h for h in history if isinstance(h, dict) and h.get("eventType") == "downloadFolderImported" and (epoch(h.get("date")) or 0) > since
              and str(h.get("downloadId") or "").lower() not in ids]
    if o.get("kind") == "movie" or not o.get("episodeIds"):
        return others[-1] if others else None
    done = {h.get("episodeId") for h in others}
    return others[-1] if all(e in done for e in o["episodeIds"]) else None


def check_override(deps, o):
    """One nightly check: (new state, result). A waiting override makes way for a release the profile allows on its own; a finished one gets a normal search."""
    arr = deps.arr(o["app"])
    i = {"kind": o["kind"], "arrId": o["arrId"]}
    ids = set(o.get("downloadIds") or [])
    q = arr(f"/queue?page=1&pageSize=1000{'&includeMovie=true' if o['kind'] == 'movie' else '&includeSeries=true'}")
    queued = [r for r in (q.get("records") or [] if isinstance(q, dict) else []) if isinstance(r, dict) and str(r.get("downloadId") or "").lower() in ids]
    if queued:
        path = f"/release?movieId={o['arrId']}" if o["kind"] == "movie" else f"/release?seriesId={o['arrId']}" + (f"&seasonNumber={o['season']}" if o.get("season") is not None else "")
        got = arr(path, timeout=RELEASE_TIMEOUT)
        job = {"kind": o["kind"], "arrId": o["arrId"], "season": o.get("season")}
        better = [x for x in got if isinstance(x, dict) and matched(x, job) and all(SELF_CAUSED.search(str(r.get("reason") if isinstance(r, dict) else r)) for r in x.get("rejections") or [])] if isinstance(got, list) else []
        if not better:
            return o["state"], "no better version yet"
        arr("/queue/bulk?removeFromClient=true&blocklist=false", "DELETE", {"ids": [r["id"] for r in queued if is_int(r.get("id"))]})
        search(arr, i, o.get("episodeIds") or None)
        return "replacing", f"better version found ({str(better[0].get('title') or '')[:120]}), override download removed, searching"
    path = f"/history/movie?movieId={o['arrId']}" if o["kind"] == "movie" else f"/history/series?seriesId={o['arrId']}"
    got = arr(path)
    hit = replaced(got if isinstance(got, list) else [], o)
    if hit:
        set_tag(arr, o["kind"], o["arrId"], "remove")
        return "replaced", f"replaced by {str(hit.get('sourceTitle') or 'another download')[:120]}"
    search(arr, i, o.get("episodeIds") or None)
    return o["state"], "searched"


def nightly(dn, deps):
    """Inside the maintenance window, once a night: every override not yet replaced by a release the profile allows is checked (never given up)."""
    now = NOW()
    lt = LOCAL(now)
    if not NIGHT_CHECK[0] <= lt.tm_hour * 60 + lt.tm_min < NIGHT_CHECK[1]:
        return
    today = time.strftime("%Y-%m-%d", lt)
    with STORE_LOCK:
        try:
            todo = {pid: dict(r["override"]) for pid, r in load(deps).items() if open_override(r) and (r["override"].get("lastNight") or "") != today}
        except StoreUnreadable:
            return
    for pid, o in todo.items():
        try:
            state, result = check_override(deps, o)
        except Exception as e:
            state, result = o.get("state"), f"check failed: {type(e).__name__}"
        print(f"manual override {pid}: {result}", flush=True)
        with STORE_LOCK:
            try:
                records = load(deps)
            except StoreUnreadable:
                return
            r = records.get(pid)
            if not r or not isinstance(r.get("override"), dict):
                continue
            changed = state != r["override"].get("state")
            r["override"].update(state=state, lastCheck=ts(now), lastNight=today, lastResult=result)
            if changed:
                r.setdefault("actions", []).append({"at": ts(now), "user": None, "userId": None, "action": "override-" + state, "result": result})
            save(deps, records)


def do_readd(deps, me, admin, p, req):
    i = p["_"]
    arr = deps.arr(i["app"])
    if i["kind"] == "movie":
        obj = arr(f"/movie/lookup/tmdb?tmdbId={int(i['tmdb'])}")
    else:
        tvdb = (i.get("media") or {}).get("tvdbId")
        if not is_int(tvdb):
            raise Refused(400, "no tvdb id")
        got = arr(f"/series/lookup?term=tvdb:{tvdb}")
        obj = got[0] if isinstance(got, list) and got else None
    if not isinstance(obj, dict) or not is_int(obj.get("tmdbId" if i["kind"] == "movie" else "tvdbId")):
        raise Refused(502, "not found")
    if existing(arr, i["kind"], obj):
        return f"already in {NAMES[i['app']]}"
    add_title(deps, i, obj)
    return f"added to {NAMES[i['app']]} again, searching"


def do_delete(deps, me, admin, p, req):
    if req.get("confirm") != "delete":
        raise Refused(400, "confirm")
    ids = [r.get("id") for r in p["_"].get("requests") or [] if is_int(r.get("id"))]
    if not ids or not deps.seerr_admin:
        raise Refused(400, "bad request")
    done = []
    for rid in ids:
        try:
            deps.seerr_admin(f"/request/{rid}", "DELETE")
        except Exception:
            record(deps, p["id"], me, "delete", f"deleted Seerr request(s) {', '.join(map(str, done)) or 'none'}, request {rid} failed", False)
            raise
        done.append(rid)
    return f"{len(ids)} Seerr request(s) deleted, title data and files kept"


DO = {"link": do_link, "import": do_import, "block": do_block, "profile": do_profile, "grab": do_grab, "override": do_override, "readd": do_readd, "delete": do_delete}


def problem(dn, deps, raw, force=False):
    if not isinstance(raw, str) or not PID.match(raw):
        raise Refused(400, "bad request")
    p = refresh(dn, deps, force).get(raw)
    if p is None:
        raise Refused(404, "no such problem")
    return p


def block_override(deps, me, pid):
    """A download-anyway download that turned out wrong: blocked like a queue problem, the override ends (tag off, no nightly checks)."""
    with STORE_LOCK:
        try:
            r = load(deps).get(pid)
        except StoreUnreadable:
            r = None
    if not blockable_override(r):
        raise Refused(404, "no such problem")
    if not throttle(("act", pid), ACTION_GAP):
        raise Refused(429, "slow down")
    o = dict(r["override"])
    arr = deps.arr(o["app"])
    q = arr(f"/queue?page=1&pageSize=1000{'&includeMovie=true' if o['kind'] == 'movie' else '&includeSeries=true'}")
    own = override_queue(q.get("records") or [] if isinstance(q, dict) else [], o)
    ids = [x["id"] for x in own if is_int(x.get("id"))]
    if not ids:
        raise Refused(404, "no such problem")
    arr("/queue/bulk?removeFromClient=true&blocklist=true", "DELETE", {"ids": ids})
    set_tag(arr, o["kind"], o["arrId"], "remove")
    search(arr, {"kind": o["kind"], "arrId": o["arrId"]}, o.get("episodeIds") or None)
    result = "release removed and blocked, searching again"
    o["state"] = "blocked"
    print(f"manual: user {me.get('id')} block override {r.get('key')} ({r.get('title')}): {result}", flush=True)
    rec = record(deps, pid, me, "block", result, True, o)
    CACHE.pop("scan", None)
    return {"ok": True, "result": result, "problem": rec}


def override_from(r):
    """When Download anyway opens for a Done entry: the nothing-found clock of the record, else OVERRIDE_DAYS after it was first seen."""
    return (r.get("details") or {}).get("overrideFrom") or ts((epoch(r.get("firstSeen")) or NOW()) + OVERRIDE_DAYS * DAY)


def replace_problem(deps, pid):
    """A Done entry with a known current release, shaped as a problem for the release list."""
    with STORE_LOCK:
        try:
            r = load(deps).get(pid)
        except StoreUnreadable:
            r = None
    if not release_offers(r):
        raise Refused(404, "no such problem")
    c = r["current"]
    return {"id": pid, "type": "replace", "key": r.get("key"), "title": r.get("title"), "details": {"overrideFrom": override_from(r)},
            "_": {"kind": c["kind"], "app": c["app"], "arrId": c["arrId"], "seasons": [c["season"]] if c.get("season") is not None else [], "runtime": c.get("runtime"), "current": c["release"]}}


def kept_forever(deps, key):
    """Storage retention (README "Storage retention"): Keep forever titles, the test film, unknown titles and missing retention data never lose their file here."""
    if not getattr(deps, "retention_file", None) or not getattr(deps, "retention_keep", None):
        return True
    import retention_api
    return retention_api.is_kept(deps, key)


def release_action(dn, deps, me, req):
    """Pick another release for a Done entry (grabbed, imported over the current file) or block its release (markAsFailed of its grab: blocklisted, searched again)."""
    pid, action = req.get("id"), req.get("action")
    p = replace_problem(deps, pid)
    if action == "reject" and req.get("confirm") != "reject":
        raise Refused(400, "confirm")
    o, n = None, req.get("release")
    if action == "replace":
        o = offer(pid, "releases", me, req)
        if o is None or not is_int(n) or not 0 <= n < len(o["items"]) or o["items"][n].get("current"):
            raise Refused(400, "bad request")
    if not throttle(("act", pid), ACTION_GAP):
        raise Refused(429, "slow down")
    with STORE_LOCK:
        r = load(deps).get(pid)
    cur = current_release(dn, deps, r)
    if cur is None or cur["ref"] != r["current"]["ref"] or action == "reject" and req.get("ref") != cur["ref"]:
        raise Refused(409, "release changed")
    if action == "reject" and req.get("keep") is False and kept_forever(deps, r.get("key")):
        raise Refused(403, "kept forever")
    arr = deps.arr(cur["app"])
    base = {"at": ts(NOW()), "user": person(me), "userId": me.get("id"), "app": cur["app"], "kind": cur["kind"], "arrId": cur["arrId"], "from": cur["release"],
            "downloadId": cur["downloadId"], "episodeIds": cur["episodeIds"], "season": cur["season"], "runtime": cur.get("runtime"), "downloadIds": [], "lastResult": None}
    if action == "replace":
        if o.get("app") != cur["app"] or o.get("arrId") != cur["arrId"]:
            raise Refused(400, "bad request")
        x = o["items"][n]
        if x["rejected"] and not may_override(p):
            raise Refused(403, "too early")
        arr("/release", "POST", {"guid": x["guid"], "indexerId": x["indexerId"]})
        state = dict(base, mode="replace", release=x["title"], state="grabbed")
        result = f"grabbed {x['title']}, it replaces {cur['release']} once imported"
    else:
        keep = req.get("keep") is not False
        arr(f"/history/failed/{int(cur['grabId'])}", "POST")
        if not keep:
            for fid in cur["fileIds"]:
                arr(f"/{'moviefile' if cur['kind'] == 'movie' else 'episodefile'}/{int(fid)}", "DELETE")
        search(arr, {"kind": cur["kind"], "arrId": cur["arrId"]}, cur["episodeIds"] or None)
        state = dict(base, mode="reject", keep=keep, release=None, state="searching")
        result = f"{cur['release']} blocked{'' if keep else ', current file deleted'}, searching again"
    print(f"manual: user {me.get('id')} {action} {r.get('key')} ({r.get('title')}): {result}", flush=True)
    rec = record(deps, pid, me, action, result, False, replace=state)
    CACHE.pop("scan", None)
    return {"ok": True, "result": result, "problem": rec}


def act(dn, deps, me, req):
    action = req.get("action")
    try:
        p = problem(dn, deps, req.get("id"))
    except Refused as e:
        if e.status == 404 and action == "block":
            return block_override(deps, me, req["id"])
        if e.status == 404 and action in ("replace", "reject"):
            return release_action(dn, deps, me, req)
        raise
    admin = dn.allowed(me)
    if action == "describe" and p["type"] == "no-release":
        if flooding(me):
            raise Refused(429, "slow down")
        return {"ok": True, "result": do_describe(deps, me, p, req), "problem": None}
    if not isinstance(action, str) or action not in ACTIONS[p["type"]]:
        raise Refused(400, "bad request")
    if action == "delete" and not admin:
        raise Refused(403, "forbidden")
    if action == "delete" and req.get("confirm") != "delete":
        raise Refused(400, "confirm")
    if not throttle(("act", p["id"]), ACTION_GAP):
        raise Refused(429, "slow down")
    p = problem(dn, deps, p["id"], force=True)
    result = DO[action](deps, me, admin, p, req)
    print(f"manual: user {me.get('id')} {action} {p['type']} {p['key']} ({p['title']}): {result}", flush=True)
    rec = record(deps, p["id"], me, action, result, action in RESOLVING, p["_"].get("override") if action == "override" else None)
    CACHE.pop("scan", None)
    return {"ok": True, "result": result, "problem": rec}


def offered(r, admin):
    """What the page may offer on a record: its type's actions while open (delete for admins), a block on a running download-anyway download,
    on a Done entry with a known current release: pick another release or block it."""
    if r.get("resolved") is None:
        return [] if replacing(r) else sorted(a for a in ACTIONS.get(r.get("type"), ()) if admin or a != "delete")
    if blockable_override(r):
        return ["block"]
    return ["reject", "replace"] if release_offers(r) else []


def public_record(r, admin):
    out = dict(r, offers=offered(r, admin))
    out.pop("currentChecked", None)
    if isinstance(r.get("current"), dict):
        out["current"] = {k: r["current"].get(k) for k in PUBLIC_CURRENT}
    if isinstance(r.get("replace"), dict):
        out["replace"] = {k: r["replace"].get(k) for k in PUBLIC_REPLACE if k in r["replace"]}
    return out


def view(deps, admin):
    with STORE_LOCK:
        try:
            records = [public_record(r, admin) for r in load(deps).values() if isinstance(r, dict)]
        except StoreUnreadable:
            records = []
    open_ = sorted((r for r in records if r.get("resolved") is None), key=lambda r: r.get("firstSeen") or "")
    done = sorted((r for r in records if r.get("resolved")), key=lambda r: r["resolved"].get("at") or "", reverse=True)[:200]
    seen = set()
    for r in done:
        # several entries of one release (e.g. an import problem, then nothing found): only the latest offers its actions
        ref = (r.get("current") or {}).get("ref")
        if ref in seen:
            r["offers"] = [a for a in r["offers"] if a not in ("reject", "replace")]
        seen.add(ref)
    return {"open": open_, "done": done, "admin": admin, "updated": ts(CACHE.get("scan", (NOW(),))[0])}


def handle(dn, method, path, headers, body, deps):
    url = urllib.parse.urlsplit(path)
    sub = url.path[len("/_download-now/manual/"):]
    h = {k.lower(): v for k, v in headers.items()}
    req = {}
    if method == "POST" and sub == "action":
        if h.get("origin") not in deps.origins:
            return 403, {"error": "origin"}
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            return 415, {"error": "json only"}
        try:
            req = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            return 400, {"error": "bad request"}
        if not isinstance(req, dict):
            return 400, {"error": "bad request"}
    elif not (method == "GET" and sub in ("problems", "count", "history", "lookup", "releases", "files")):
        return 404, {"error": "not found"}
    auth = dn.auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except dn.ApiError:
        me = None
    if not isinstance(me, dict) or not is_int(me.get("id")):
        return 403, {"error": "forbidden"}
    if not SLOTS.acquire(blocking=False):
        return 429, {"error": "busy"}
    try:
        return work(dn, deps, me, method, sub, url, req)
    finally:
        SLOTS.release()


def work(dn, deps, me, method, sub, url, req):
    admin = dn.allowed(me)
    q = urllib.parse.parse_qs(url.query)
    try:
        if method == "POST":
            return 200, act(dn, deps, me, req)
        if sub == "problems":
            refresh(dn, deps)
            return 200, view(deps, admin)
        if sub == "count":
            refresh(dn, deps)
            return 200, {"open": len(view(deps, admin)["open"])}
        if sub == "history":
            if not admin:
                return 403, {"error": "forbidden"}
            refresh(dn, deps)
            with STORE_LOCK:
                return 200, {"problems": list(load(deps).values())}
        try:
            p = problem(dn, deps, (q.get("id") or [""])[0])
        except Refused as e:
            if e.status != 404 or sub != "releases":
                raise
            p = replace_problem(deps, q["id"][0])
        if flooding(me):
            raise Refused(429, "slow down")
        if sub == "lookup":
            return 200, lookup(dn, deps, me, p, q)
        if sub == "files":
            return 200, files(dn, deps, me, p, q)
        return 200, releases(dn, deps, me, p, q)
    except Refused as e:
        return e.status, {"error": e.error}
    except Exception as e:
        print(f"manual {sub}: {type(e).__name__} {dn.redact(e)}", flush=True)
        return 502, {"error": "not reachable"}


def loop(dn, deps, every=600):
    while True:
        try:
            refresh(dn, deps, force=True)
            nightly(dn, deps)
        except Exception as e:
            print(f"manual scan: {type(e).__name__} {dn.redact(e)}", flush=True)
        time.sleep(every)
