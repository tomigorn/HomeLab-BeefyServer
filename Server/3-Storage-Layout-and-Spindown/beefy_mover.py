#!/usr/bin/env python3
"""beefy-mover - nightly SSD -> HDD demotion for the /srv/video pool (storage doc §5).

Policy: only when the hot SSD is at START_USED_PCT or more, move whole titles (a movie folder, a whole series folder) to the same relative path on the cold HDD until it is at TARGET_USED_PCT or less, oldest last use first.
Last use = max(last play by any Jellyfin user over the title's movie/episodes, newest Radarr movieFile / Sonarr episodeFile dateAdded). A title moves only when its last use is older than IDLE_DAYS; from FALLBACK_USED_PCT on, older than FALLBACK_DAYS is enough. atime and file dates are no use (scans, trickplay, original-audio read and touch the files). A title the arrs do not know counts as added today.
Fails closed: Jellyfin, Radarr or Sonarr unreadable, no Jellyfin user, or under 90 % of the SSD videos known to Jellyfin -> nothing moves. The data is read once at the start (auto-update may restart the apps mid-run).
Never moves a title with a hardlinked file (still seeding), an open file, a change in the last SETTLE_HOURS (import in progress) or a pinned path.
The bytes are moved by tier-move -> tier-helper (staging copy, verify read back from disk, source unchanged check, atomic rename, then delete; sidecars stay hot; shared media lock). Bounded per run: MAX_MOVE_GIB, DEADLINE (no title starts that cannot finish by then at 1.5 x size / RATE_MIB_S plus TIER_LOCK_WAIT (60 s) per branch for the media lock), HDD_RESERVE_PCT free kept on the HDD.
Writes PROM (fastpi alerts + morning digest): run result, duplicates, staging orphans, downloads that spilled onto the HDD, SMART counters. Safe to re-run: a title already on the HDD is no candidate, a killed copy leaves the source intact.
Usage: beefy-mover [--dry-run] [--plan] [--only REL] [--no-deadline]
"""
import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Zurich")
GIB = 1024 ** 3
MIB = 1024 ** 2
VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm", ".iso", ".mpg", ".mpeg"}
PARTIAL = (".partial~", ".partial", ".!qb", ".tmp")


@dataclasses.dataclass
class Conf:
    hots: list
    cold: pathlib.Path
    roots: list
    start: float
    target: float
    max_move: float
    settle_hours: float
    idle_days: float
    fallback: float
    fallback_days: float
    arr: dict
    smart: dict
    deadline: str
    rate: float
    hdd_reserve: float
    dry_run: bool
    pins: pathlib.Path
    tier_move: str
    keep_awake: pathlib.Path
    prom: pathlib.Path
    jf_url: str
    jf_key_file: str
    jf_prefix: str
    lock_wait: int

    @classmethod
    def from_env(cls, env):
        g = env.get
        return cls(hots=[pathlib.Path(h) for h in g("HOT", "/srv/.disks/ssd-hot /srv/.disks/ssd-hot2").split()], cold=pathlib.Path(g("COLD", "/srv/.disks/hdd-cold")),
                   roots=g("ROOTS", "media/movies media/tv").split(), start=float(g("START_USED_PCT", "70")) / 100,
                   target=float(g("TARGET_USED_PCT", "60")) / 100, max_move=float(g("MAX_MOVE_GIB", "600")) * GIB,
                   settle_hours=float(g("SETTLE_HOURS", "24")), idle_days=float(g("IDLE_DAYS", "30")),
                   fallback=float(g("FALLBACK_USED_PCT", "75")) / 100, fallback_days=float(g("FALLBACK_DAYS", "7")),
                   arr={"radarr": (g("RADARR_URL", "http://127.0.0.1:7878"), g("RADARR_CONFIG", "/srv/appdata/radarr/config.xml")),
                        "sonarr": (g("SONARR_URL", "http://127.0.0.1:8989"), g("SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"))},
                   smart={"hdd": g("SMART_HDD", "/dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M"),
                          "ssd": g("SMART_SSD", "/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0WA00268B"),
                          "ssd2": g("SMART_SSD2", "/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0W909892P")}, deadline=g("DEADLINE", "04:40"), rate=float(g("RATE_MIB_S", "200")) * MIB,
                   hdd_reserve=float(g("HDD_RESERVE_PCT", "10")) / 100, dry_run=g("DRY_RUN", "0") == "1",
                   pins=pathlib.Path(g("PINS", "/etc/beefy-mover-pins")), tier_move=g("TIER_MOVE", "/usr/local/sbin/tier-move"),
                   keep_awake=pathlib.Path(g("KEEP_AWAKE_FILE", "/run/beefy-keep-awake")),
                   prom=pathlib.Path(g("PROM", "/var/lib/beefy-mover/mover.prom")), jf_url=g("JF_URL", "http://127.0.0.1:8096"),
                   jf_key_file=g("JF_KEY_FILE", "/home/buntu/Projects/Docker/Jellyfin/secrets/api-key"), jf_prefix=g("JF_PREFIX", "/media/"),
                   lock_wait=int(g("TIER_LOCK_WAIT", "60")))


@dataclasses.dataclass
class Video:
    rel: str
    branch: pathlib.Path
    size: int
    mtime: float
    ctime: float
    nlink: int


@dataclasses.dataclass
class Unit:
    rel: str
    videos: list
    partial: bool = False

    @property
    def branches(self):
        return list(dict.fromkeys(v.branch for v in self.videos))

    @property
    def size(self):
        return sum(v.size for v in self.videos)

    @property
    def added(self):
        return max(v.mtime for v in self.videos)

    @property
    def changed(self):
        return max(v.ctime for v in self.videos)


def unit_of(root, rel):
    """media/movies/<title> or media/tv/<series>: the folder right under the root; None for a file in the root itself."""
    parts = rel.split("/")
    depth = len(root.split("/"))
    if len(parts) <= depth + 1:
        return None
    return "/".join(parts[:depth + 1])


def scan(hots, roots):
    """Titles on the hot branch(es); a title split over two SSDs is one unit whose videos remember their branch."""
    hots = [pathlib.Path(h) for h in ([hots] if isinstance(hots, (str, pathlib.Path)) else hots)]
    units = {}
    for hot in hots:
        for root in roots:
            base = hot / root
            if not base.is_dir():
                continue
            dev = base.stat().st_dev
            for dirpath, dirnames, files in os.walk(base):
                dirnames[:] = [d for d in dirnames if os.lstat(os.path.join(dirpath, d)).st_dev == dev]
                for name in files:
                    p = os.path.join(dirpath, name)
                    rel = os.path.relpath(p, hot)
                    key = unit_of(root, rel)
                    if key is None:
                        continue
                    u = units.setdefault(key, Unit(key, []))
                    if name.endswith(PARTIAL):
                        u.partial = True
                        continue
                    if os.path.splitext(name)[1].lower() not in VIDEO:
                        continue
                    st = os.lstat(p)
                    u.videos.append(Video(rel, hot, st.st_size, st.st_mtime, st.st_ctime, st.st_nlink))
    return sorted((u for u in units.values() if u.videos), key=lambda u: u.rel)


def read_pins(path):
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line.startswith("/srv/video/"):
            line = line[len("/srv/video/"):]
        line = line.strip("/")
        if line:
            out.append(line)
    return out


def fuser_open(path):
    return subprocess.run(["fuser", "-s", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


class SourceError(Exception):
    pass


@dataclasses.dataclass
class Snapshot:
    played: dict
    added: dict
    jf_paths: set


def jf_time(s):
    if not s:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)?$", s)
    if not m:
        return None
    t = dt.datetime.fromisoformat(m.group(1) + (m.group(2) or "Z").replace("Z", "+00:00"))
    return t.timestamp() if t.year > 1 else None


def pool_rel(path, prefix):
    return path[len(prefix):].strip("/") if path and path.startswith(prefix) else None


def unit_for(rel, roots):
    for root in roots:
        if rel.startswith(root + "/"):
            return unit_of(root, rel)
    return None


def snapshot(jf, radarr, sonarr, roots, jf_prefix="/media/", jf_pool="media/", arr_prefix="/data/"):
    """Read play and add dates once. jf/radarr/sonarr: path -> parsed JSON, raising on any failure. Raises SourceError."""
    try:
        users = jf("/Users")
        if not users:
            raise SourceError("Jellyfin lists no user")
        played, jf_paths = {}, set()
        for user in users:
            items = jf(f"/Items?userId={user['Id']}&Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path&EnableImages=false&EnableUserData=true")
            for it in items["Items"]:
                tail = pool_rel(it.get("Path"), jf_prefix)
                if tail is None:
                    continue
                rel = jf_pool + tail
                jf_paths.add(rel)
                last = jf_time((it.get("UserData") or {}).get("LastPlayedDate"))
                unit = unit_for(rel, roots)
                if unit and last:
                    played[unit] = max(played.get(unit, 0), last)
        added = {}
        for m in radarr("/api/v3/movie"):
            rel, f = pool_rel(m.get("path"), arr_prefix), m.get("movieFile")
            if rel and f and jf_time(f.get("dateAdded")):
                added[rel] = max(added.get(rel, 0), jf_time(f["dateAdded"]))
        for series in sonarr("/api/v3/series"):
            rel = pool_rel(series.get("path"), arr_prefix)
            if not rel or not (series.get("statistics") or {}).get("episodeFileCount", 1):
                continue
            dates = [jf_time(f.get("dateAdded")) for f in sonarr(f"/api/v3/episodefile?seriesId={series['id']}")]
            dates = [d for d in dates if d]
            if dates:
                added[rel] = max(added.get(rel, 0), max(dates))
        return Snapshot(played, added, jf_paths)
    except SourceError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise SourceError(f"{type(e).__name__}: {e}") from None


def json_api(base, headers):
    def get(path):
        req = urllib.request.Request(base + path, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    return get


def arr_key(config_xml):
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", pathlib.Path(config_xml).read_text())
    if not m:
        raise SourceError(f"no API key in {config_xml}")
    return m.group(1)


def read_snapshot(conf):
    try:
        jf = json_api(conf.jf_url, {"Authorization": f"MediaBrowser Token={pathlib.Path(conf.jf_key_file).read_text().strip()}"})
        radarr = json_api(conf.arr["radarr"][0], {"X-Api-Key": arr_key(conf.arr["radarr"][1])})
        sonarr = json_api(conf.arr["sonarr"][0], {"X-Api-Key": arr_key(conf.arr["sonarr"][1])})
    except OSError as e:
        raise SourceError(f"{type(e).__name__}: {e}") from None
    return snapshot(jf, radarr, sonarr, conf.roots, conf.jf_prefix)


def last_use(unit, snap, now):
    """(played or None, added) for the unit; unknown to the arrs = added now."""
    return snap.played.get(unit.rel), snap.added.get(unit.rel, now.timestamp())


def age_days(unit, snap, now):
    p, a = last_use(unit, snap, now)
    return (now.timestamp() - max(p or 0, a)) / 86400


def order(units, snap, now):
    return sorted(units, key=lambda u: (-age_days(u, snap, now), u.rel))


def recency(unit, snap, now, days):
    p, a = last_use(unit, snap, now)
    limit = now.timestamp() - days * 86400
    if p and p > limit:
        return f"played in the last {days:g} days"
    if a > limit:
        return f"added in the last {days:g} days"
    return None


def skip_reason(unit, conf, now, pins, is_open):
    if any(unit.rel == p or unit.rel.startswith(p + "/") or p.startswith(unit.rel + "/") for p in pins):
        return "pinned"
    if unit.partial:
        return "import in progress"
    if any(v.nlink > 1 for v in unit.videos):
        return "seeding (hardlinked)"
    if conf.settle_hours and unit.changed > now.timestamp() - conf.settle_hours * 3600:
        return f"changed in the last {conf.settle_hours:g} h"
    if any(is_open(v.branch / v.rel) for v in unit.videos):
        return "in use"
    return None


def coverage(units, snap, now):
    """Share of the SSD videos older than a day that Jellyfin knows (None: nothing to check)."""
    old = [v.rel for u in units for v in u.videos if v.ctime < now.timestamp() - 86400]
    return sum(r in snap.jf_paths for r in old) / len(old) if old else None


def usage(statvfs, paths):
    """(total, used) summed over one branch or a list of them; used = total - f_bavail, the measure mergerfs minfreespace uses."""
    total = used = 0
    for path in ([paths] if isinstance(paths, (str, pathlib.Path)) else paths):
        st = statvfs(path)
        t = st.f_blocks * st.f_frsize
        total, used = total + t, used + t - st.f_bavail * st.f_frsize
    return total, used


def human(n):
    for unit, size in (("TiB", 1024 ** 4), ("GiB", GIB), ("MiB", MIB), ("KiB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{int(n)} B"


def deadline_at(conf, now):
    if not conf.deadline:
        return None
    h, m = map(int, conf.deadline.split(":"))
    return now.astimezone(TZ).replace(hour=h, minute=m, second=0, microsecond=0)


@contextlib.contextmanager
def keep_awake(path):
    path = pathlib.Path(path)
    mine = False
    try:
        if not path.exists():
            path.touch()
            mine = True
    except OSError:
        pass
    try:
        yield
    finally:
        if mine:
            with contextlib.suppress(OSError):
                path.unlink()


def count_files(path):
    n = 0
    for _, _, files in os.walk(path):
        n += len(files)
    return n


def hdd_checks(conf, units):
    """Only while the mover works (the HDD is awake then): duplicates, staging leftovers (incl. .tier-recovered, kept for a look by hand), downloads that spilled onto the HDD."""
    dups = sum((conf.cold / v.rel).exists() for u in units for v in u.videos)
    return {"duplicates": dups, "staging_orphans": count_files(conf.cold / ".tier-staging") + count_files(conf.cold / ".tier-recovered"),
            "hdd_overflow_files": count_files(conf.cold / "torrents") + count_files(conf.cold / "usenet")}


def tier_env(conf, branch):
    """tier-move environment for one branch; the media lock wait is short (TIER_LOCK_WAIT), so a busy promoter or original-audio costs at most that per branch, counted in the deadline."""
    return dict(os.environ, HOT=str(branch), COLD=str(conf.cold), TIER_LOCK_WAIT=str(conf.lock_wait))


def classify(rc, out):
    """tier-move result -> (kind, line): moved, skip (lock busy, source changed, guard refused) or failed."""
    first = next((l.strip() for l in out.splitlines() if l.strip()), "no output")
    if rc == 0:
        return "moved", first
    if rc in (3, 4) or "REFUSING" in out or "in use" in out:
        return "skip", first
    return "failed", first


def run(conf, now, statvfs, tier_move, is_open, snap, log, only=None, use_deadline=True):
    t0 = time.monotonic()
    ssd_total, ssd_used = usage(statvfs, conf.hots)
    res = {"started": int(now.timestamp()), "success": True, "dry_run": conf.dry_run, "moved_titles": 0, "moved_bytes": 0,
           "skipped_titles": 0, "failed_titles": 0, "verify_failures": 0, "ssd_before": ssd_used / ssd_total, "ssd_after": ssd_used / ssd_total}
    log(f"beefy-mover: SSD {100 * ssd_used / ssd_total:.1f} % used (start at {100 * conf.start:g} %, target {100 * conf.target:g} %){' DRY RUN' if conf.dry_run else ''}")
    if only is None and ssd_used / ssd_total < conf.start:
        log("beefy-mover: below the start threshold - nothing to do, HDD not touched")
        return res
    if snap is None and only is None:
        log("beefy-mover: play/add data unreadable - nothing moves (fails closed)")
        res["success"] = False
        return res
    units = scan(conf.hots, conf.roots)
    if only is not None:
        units = [u for u in units if u.rel == only]
        if not units:
            log(f"beefy-mover: {only} is no title on the SSD")
            res["success"] = False
            return res
    else:
        cov = coverage(units, snap, now)
        if cov is not None and cov < 0.9:
            log(f"beefy-mover: Jellyfin knows only {100 * cov:.0f} % of the SSD videos - nothing moves (fails closed)")
            res["success"] = False
            return res
    res.update(hdd_checks(conf, units))
    if res["staging_orphans"] or res["duplicates"]:
        log(f"beefy-mover: {res['staging_orphans']} staging leftover(s), {res['duplicates']} video(s) on both branches (resolved when their title moves)")
    if res["hdd_overflow_files"]:
        log(f"beefy-mover: {res['hdd_overflow_files']} download file(s) landed on the HDD (SSD was at its mergerfs limit)")
    pins = read_pins(conf.pins)
    end = deadline_at(conf, now) if use_deadline and only is None else None
    hdd_total, hdd_used = usage(statvfs, conf.cold)
    for u in (order(units, snap, now) if snap else units):
        ratio = ssd_used / ssd_total
        if only is None and ratio <= conf.target:
            log(f"beefy-mover: reached {100 * ratio:.1f} % - target met")
            break
        if only is None and res["moved_bytes"] + u.size > conf.max_move:
            log(f"beefy-mover: per-run data cap of {human(conf.max_move)} reached - stopping")
            break
        if end and now + dt.timedelta(seconds=time.monotonic() - t0 + conf.lock_wait * len(u.branches) + 1.5 * u.size / conf.rate) > end:
            log(f"beefy-mover: time limit {conf.deadline}: {u.rel} ({human(u.size)}) would not finish - stopping, the rest goes next night")
            break
        if only is None:
            days = conf.idle_days if ratio < conf.fallback else conf.fallback_days
            late = recency(u, snap, now, days)
            if late:
                log(f"beefy-mover: next title {late} ({u.rel}) - nothing older left to move")
                break
        reason = skip_reason(u, conf, now, pins, is_open)
        if reason:
            log(f"  skip ({reason}): {u.rel}")
            res["skipped_titles"] += 1
            continue
        if hdd_total - hdd_used - u.size < conf.hdd_reserve * hdd_total:
            log(f"beefy-mover: HDD would drop under its {100 * conf.hdd_reserve:g} % reserve - stopping")
            break
        tag = f" ({describe(u, snap, now)})" if snap else ""
        if conf.dry_run:
            log(f"  WOULD move ({human(u.size)}){tag}: {u.rel}")
            ssd_used -= u.size
            hdd_used += u.size
        else:
            kind, line, out, done = "moved", "", "", 0
            for branch in u.branches:
                rc, out = tier_move(u.rel, branch)
                kind, line = classify(rc, out)
                if kind != "moved":
                    break
                done += 1
            part = f" (partially moved: {done} of {len(u.branches)} branches, still served by mergerfs, finished next night)" if done and kind != "moved" else ""
            if kind == "failed":
                res["failed_titles"] += 1
                res["verify_failures"] += "VERIFY FAILED" in out
                res["success"] = False
                log(f"  FAILED: {u.rel}: {line}{part}")
                continue
            if kind == "skip":
                res["skipped_titles"] += 1
                log(f"  skip ({line}): {u.rel}{part}")
                continue
            log(f"  moved ({human(u.size)}){tag}: {u.rel}")
            ssd_total, ssd_used = usage(statvfs, conf.hots)
            hdd_total, hdd_used = usage(statvfs, conf.cold)
        res["moved_titles"] += 1
        res["moved_bytes"] += u.size
    res["ssd_after"] = ssd_used / ssd_total
    log(f"beefy-mover: done - {res['moved_titles']} moved ({human(res['moved_bytes'])}), {res['skipped_titles']} skipped, "
        f"{res['failed_titles']} failed, SSD {100 * res['ssd_after']:.1f} %")
    return res


def day(t):
    return dt.datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d") if t else "never"


def describe(u, snap, now):
    p, a = last_use(u, snap, now)
    return f"last played {day(p)}, added {day(a) if u.rel in snap.added else 'unknown (= today)'}"


def plan(conf, now, statvfs, is_open, snap):
    total, used = usage(statvfs, conf.hots)
    lines = [f"SSD {100 * used / total:.1f} % used; start at {100 * conf.start:g} %, target {100 * conf.target:g} %; "
             f"idle {conf.idle_days:g} days ({conf.fallback_days:g} from {100 * conf.fallback:g} %). Titles in move order:"]
    if snap is None:
        return lines + ["  play/add data unreadable: a real run moves nothing"]
    pins = read_pins(conf.pins)
    days = conf.idle_days if used / total < conf.fallback else conf.fallback_days
    for u in order(scan(conf.hots, conf.roots), snap, now):
        reason = recency(u, snap, now, days) or skip_reason(u, conf, now, pins, is_open)
        p, a = last_use(u, snap, now)
        added = day(a) if u.rel in snap.added else "unknown"
        lines.append(f"  {'candidate' if not reason else 'skip':9}  {human(u.size):>10}  played {day(p):10}  added {added:10}  {u.rel}{'  (' + reason + ')' if reason else ''}")
    return lines


def smart(dev, run_cmd=subprocess.run):
    """Raw SMART counters without waking a parked disk (-n standby): {attribute name: raw value}; {} when asleep or unreadable."""
    try:
        r = run_cmd(["smartctl", "-n", "standby", "-A", "-j", dev], capture_output=True, text=True, timeout=30)
        table = json.loads(r.stdout)["ata_smart_attributes"]["table"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return {}
    keep = {"Start_Stop_Count", "Load_Cycle_Count", "Power_Cycle_Count", "Wear_Leveling_Count", "Total_LBAs_Written"}
    return {a["name"]: a["raw"]["value"] for a in table if a.get("name") in keep}


def smart_line(now, values):
    """One line per run for the spin-up-per-boot comparison (SMART_LOG)."""
    if not values:
        return None
    parts = [d + " " + " ".join(f"{k}={v}" for k, v in sorted(a.items())) for d, a in sorted(values.items())]
    return now.isoformat() + " " + " | ".join(parts) + "\n"


def prom(res, smart_values=None):
    lines = [f"beefy_mover_last_run_timestamp_seconds {res['started']}",
             f"beefy_mover_last_run_success {int(res['success'])}",
             f"beefy_mover_dry_run {int(res['dry_run'])}"]
    for k in ("moved_titles", "moved_bytes", "skipped_titles", "failed_titles", "verify_failures", "duplicates", "staging_orphans", "hdd_overflow_files"):
        if k in res:
            lines.append(f"beefy_mover_{k} {res[k]}")
    lines.append(f'beefy_mover_ssd_used_ratio{{when="before"}} {round(res["ssd_before"], 4)}')
    lines.append(f'beefy_mover_ssd_used_ratio{{when="after"}} {round(res["ssd_after"], 4)}')
    for disk, attrs in sorted((smart_values or {}).items()):
        for name, value in sorted(attrs.items()):
            lines.append(f'beefy_disk_smart_raw{{disk="{disk}",attribute="{name}"}} {value}')
    return "\n".join(lines) + "\n"


def write_prom(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mover-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Nightly SSD -> HDD mover for /srv/video")
    ap.add_argument("--dry-run", action="store_true", help="log what would move, change nothing")
    ap.add_argument("--plan", action="store_true", help="list every title on the SSD in move order with size, last played, added and skip reason, whatever the fill level")
    ap.add_argument("--only", metavar="REL", help="move just this title (pool-relative), ignoring thresholds and play/add dates; all file guards stay")
    ap.add_argument("--no-deadline", action="store_true", help="ignore DEADLINE (manual daytime runs)")
    a = ap.parse_args(argv)
    conf = Conf.from_env(os.environ)
    if a.dry_run:
        conf.dry_run = True
    now = dt.datetime.now(TZ).replace(microsecond=0)
    snap = None
    if a.only is None:
        try:
            snap = read_snapshot(conf)
        except SourceError as e:
            print(f"beefy-mover: cannot read play/add data: {e}", flush=True)
    if a.plan:
        print("\n".join(plan(conf, now, os.statvfs, fuser_open, snap)))
        return 0
    lock = open("/run/lock/beefy-mover.lock" if os.access("/run/lock", os.W_OK) else os.devnull, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("beefy-mover: another run is busy - nothing done")
        return 0

    def tier_move(rel, branch):
        r = subprocess.run([conf.tier_move, "demote", rel, "--apply", "--any-time"], capture_output=True, text=True, env=tier_env(conf, branch))
        out = r.stdout + r.stderr
        keys = ("REFUS", "FAIL", "in use", "could not", "SOURCE", "media lock", "stopped at")
        return r.returncode, "\n".join(l for l in out.splitlines() if any(k in l for k in keys)) or out
    with keep_awake(conf.keep_awake):
        try:
            res = run(conf, now, os.statvfs, tier_move, fuser_open, snap, lambda l: print(l, flush=True), only=a.only, use_deadline=not a.no_deadline)
        except Exception as e:
            print(f"beefy-mover: run failed: {type(e).__name__}: {e}", flush=True)
            res = {"started": int(now.timestamp()), "success": False, "dry_run": conf.dry_run, "moved_titles": 0, "moved_bytes": 0,
                   "skipped_titles": 0, "failed_titles": 0, "ssd_before": 0, "ssd_after": 0}
        if a.only is None:
            values = {k: v for k, v in ((d, smart(dev)) for d, dev in conf.smart.items()) if v}
            with contextlib.suppress(OSError):
                write_prom(conf.prom, prom(res, values))
            line = smart_line(now, values)
            if line:
                with contextlib.suppress(OSError), open(conf.prom.parent / "smart-cycles.log", "a") as f:
                    f.write(line)
    os.sync()
    return 0 if res["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
