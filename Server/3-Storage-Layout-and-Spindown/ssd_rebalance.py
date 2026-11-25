#!/usr/bin/env python3
"""ssd-rebalance - move whole titles from the fuller hot SSD to the emptier one until both are about equally full.

  ssd-rebalance [--dry-run] [--tol-pct 1] [--max-pct 75]

Units: media/movies/<title>, media/tv/<series>/<season>, media/tv/<series> (series-level files). Each file moves with every hardlink of its inode on the branch (torrents/, usenet/), at the same relative paths, so the links stay links and the pool paths do not change.
Per inode: rsync into <dst>/.rebalance-staging, verify read back from disk, link into place at every path, re-check the source is unchanged (ctime, link count), then delete the source links. A journal (/var/lib/ssd-rebalance/journal.json) makes a kill or power cut recoverable on the next start: before the delete the source wins, after it the target.
Skips: units with a link outside the branch walk, a file changed in the last 24 h, an open file, or a file already at the target path. Pauses: a Jellyfin session plays something, the night window 03:50-04:50 (no file starts that cannot finish before it), the nightly mover runs. Takes the shared media lock per unit. Stops when the two SSDs are within 2 x --tol-pct, or the target would pass --max-pct.
Idempotent: re-run it to continue. Progress: /var/log/ssd-rebalance.log.
"""
import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("tier_helper", HERE / "tier_helper.py")
th = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(th)

TZ = ZoneInfo("Europe/Zurich")
STAGING = ".rebalance-staging"
SKIP_DIRS = {"lost+found", ".tier-staging", ".tier-recovered", STAGING}
PAUSE_FROM, PAUSE_TO = dt.time(3, 50), dt.time(4, 50)


def unit_of(rel):
    p = rel.split("/")
    if len(p) < 4 or p[0] != "media" or p[1] not in ("movies", "tv"):
        return None
    if p[1] == "movies" or len(p) == 4:
        return "/".join(p[:3])
    return "/".join(p[:4])


def need(src_used, dst_used):
    return max(0, (src_used - dst_used) // 2)


def pick(sizes, need, tol, room):
    if need <= tol:
        return None
    fits = [(s, k) for k, s in sizes.items() if 0 < s <= need + tol and s <= room]
    return max(fits)[1] if fits else None


def in_pause_window(now):
    return PAUSE_FROM <= now.astimezone(TZ).time() < PAUSE_TO


def may_start(now, size, rate):
    local = now.astimezone(TZ)
    start = local.replace(hour=PAUSE_FROM.hour, minute=PAUSE_FROM.minute, second=0, microsecond=0)
    if local >= start:
        return True
    return local + dt.timedelta(seconds=size / rate) < start


def streaming(sessions):
    return any(s.get("NowPlayingItem") for s in sessions)


@dataclasses.dataclass
class Unit:
    rel: str
    groups: list = dataclasses.field(default_factory=list)
    size: int = 0
    complete: bool = True
    changed: float = 0.0


def scan(branch):
    branch = pathlib.Path(branch)
    links, files = {}, []
    for root, dirs, names in os.walk(branch):
        if pathlib.Path(root) == branch:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            path = pathlib.Path(root) / n
            rel = str(path.relative_to(branch))
            st = os.lstat(path)
            files.append((rel, st))
            if st.st_nlink > 1 and not path.is_symlink():
                links.setdefault(st.st_ino, []).append(rel)
    units, seen = {}, set()
    for rel, st in files:
        key = unit_of(rel)
        if key is None:
            continue
        u = units.setdefault(key, Unit(key))
        if not os.path.isfile(branch / rel) or os.path.islink(branch / rel):
            u.complete = False
            continue
        u.changed = max(u.changed, st.st_mtime)
        if st.st_ino in seen:
            continue
        seen.add(st.st_ino)
        group = sorted(links.get(st.st_ino, [rel]))
        if len(group) != st.st_nlink:
            u.complete = False
        u.groups.append(group)
        u.size += st.st_size
    return units


def fuser_open(path):
    return subprocess.run(["fuser", "-s", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


class Mover:
    def __init__(self, src, dst, journal, log=print, is_open=fuser_open, settle_hours=24, now=time.time):
        self.src, self.dst, self.journal = pathlib.Path(src), pathlib.Path(dst), pathlib.Path(journal)
        self.log, self.is_open, self.settle, self.now = log, is_open, settle_hours * 3600, now
        self.before_place = lambda: None

    def _write_journal(self, phase, rels):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.journal.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"phase": phase, "src": str(self.src), "dst": str(self.dst), "rels": rels}, f)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, self.journal)
        th.fsync_dir(self.journal.parent)

    def _clear_journal(self):
        self.journal.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            th.fsync_dir(self.journal.parent)

    def _prune(self, rels):
        for rel in rels:
            d = pathlib.PurePosixPath(rel).parent
            while len(d.parts) > 2:
                try:
                    (self.src / d).rmdir()
                except OSError:
                    break
                d = d.parent

    def _prune_staging(self):
        root = self.dst / STAGING
        for d, _, _ in sorted(os.walk(root), key=lambda x: len(x[0]), reverse=True):
            with contextlib.suppress(OSError):
                os.rmdir(d)

    def _unplace(self, rels):
        for rel in rels:
            d, s = self.dst / rel, self.src / rel
            if d.exists() and s.exists() and d.stat().st_size == s.stat().st_size:
                d.unlink()
        shutil_rmtree(self.dst / STAGING)

    def recover(self):
        if self.journal.exists():
            j = json.loads(self.journal.read_text())
            rels = j["rels"]
            if j["phase"] == "delete":
                if all((self.dst / r).exists() for r in rels):
                    pairs = [r for r in rels if (self.src / r).exists()]
                    if pairs and not th.same_on_disk(self.src / pairs[0], self.dst / pairs[0]):
                        raise RuntimeError(f"journal says delete, but {pairs[0]} differs between the branches - check by hand")
                    for r in pairs:
                        (self.src / r).unlink()
                    self.log(f"recovered: finished deleting the source of {rels[0]}")
                    self._prune(rels)
                else:
                    raise RuntimeError(f"journal says delete, but {rels} is not complete on {self.dst} - check by hand")
            else:
                self._unplace(rels)
                self.log(f"recovered: rolled back the half-done copy of {rels[0]} (phase {j['phase']}), the source is untouched")
            self._clear_journal()
        shutil_rmtree(self.dst / STAGING)

    def _same(self, rels, before):
        try:
            st = os.stat(self.src / rels[0])
        except FileNotFoundError:
            return False
        return (st.st_ino, st.st_size, st.st_ctime_ns) == before and st.st_nlink == len(rels)

    def move_group(self, rels):
        src0 = self.src / rels[0]
        before = th.sig(src0)
        stage = self.dst / STAGING / rels[0]
        self._write_journal("copy", rels)
        stage.parent.mkdir(parents=True, exist_ok=True)
        if subprocess.run(["rsync", "-aAX", "--", str(src0), str(stage)], stdout=subprocess.DEVNULL).returncode != 0:
            self._unplace(rels)
            self._clear_journal()
            self.log(f"copy FAILED: {rels[0]} - source untouched")
            return "failed"
        if not self._same(rels, before):
            self._unplace(rels)
            self._clear_journal()
            return "changed"
        if not th.same_on_disk(src0, stage):
            self._unplace(rels)
            self._clear_journal()
            self.log(f"VERIFY FAILED: {rels[0]} - copy discarded, source untouched")
            return "failed"
        self.before_place()
        self._write_journal("place", rels)
        try:
            for rel in rels:
                parent = str(pathlib.PurePosixPath(rel).parent)
                th.clone_dirs(self.src, self.dst, parent)
                os.link(stage, self.dst / rel)
        except FileExistsError as e:
            self._unplace(rels)
            self._clear_journal()
            self.log(f"skip: {e.filename} appeared on the target - rolled back")
            return "skip"
        stage.unlink()
        self._prune_staging()
        for rel in rels:
            th.fsync_up(self.dst, str(pathlib.PurePosixPath(rel).parent))
        if not self._same(rels, before):
            self._unplace(rels)
            self._clear_journal()
            self.log(f"SOURCE CHANGED during the move: {rels[0]} - rolled back, source untouched")
            return "changed"
        self._write_journal("delete", rels)
        for rel in rels:
            (self.src / rel).unlink()
        for rel in rels:
            th.fsync_dir((self.src / rel).parent)
        self._clear_journal()
        return "moved"

    def move_unit(self, u):
        if not u.complete:
            self.log(f"skip {u.rel}: a hardlink lives outside the branch walk, or a non-regular file")
            return "skip"
        if self.now() - u.changed < self.settle:
            self.log(f"skip {u.rel}: changed in the last {self.settle // 3600:.0f} h")
            return "skip"
        for g in u.groups:
            try:
                sts = [os.lstat(self.src / r) for r in g]
            except FileNotFoundError:
                self.log(f"skip {u.rel}: {g[0]} is gone (stale scan)")
                return "skip"
            if len({s.st_ino for s in sts}) != 1 or sts[0].st_nlink != len(g):
                self.log(f"skip {u.rel}: links of {g[0]} changed since the scan")
                return "skip"
            hit = next((r for r in g if (self.dst / r).exists()), None)
            if hit:
                self.log(f"skip {u.rel}: {hit} already exists on {self.dst}")
                return "skip"
            if self.is_open(self.src / g[0]):
                self.log(f"skip {u.rel}: {g[0]} is open (playing or seeding)")
                return "skip"
        for g in sorted(u.groups):
            rc = self.move_group(g)
            if rc != "moved":
                return rc
        self._prune([r for g in u.groups for r in g])
        return "moved"


def shutil_rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def usage(path):
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    return total, total - st.f_bavail * st.f_frsize


def jf_sessions(url, key_file):
    key = pathlib.Path(key_file).read_text().strip()
    req = urllib.request.Request(url + "/Sessions?activeWithinSeconds=300", headers={"Authorization": f"MediaBrowser Token={key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def lock_busy(path):
    try:
        with open(path) as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except FileNotFoundError:
        return False
    except BlockingIOError:
        return True


@contextlib.contextmanager
def media_lock(path, wait):
    with contextlib.suppress(OSError):
        if not os.path.exists(path):
            pathlib.Path(path).touch()
    f = open(path)
    try:
        end = time.time() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > end:
                    yield False
                    return
                time.sleep(5)
        yield True
    finally:
        f.close()


@contextlib.contextmanager
def keep_awake(path):
    path = pathlib.Path(path)
    mine = False
    with contextlib.suppress(OSError):
        if not path.exists():
            path.parent.mkdir(exist_ok=True)
            path.touch()
            mine = True
    try:
        yield
    finally:
        if mine:
            with contextlib.suppress(OSError):
                path.unlink()


def idle_io():
    with contextlib.suppress(OSError):
        cg = pathlib.Path("/proc/self/cgroup").read_text().strip().split("::")[-1]
        (pathlib.Path("/sys/fs/cgroup") / cg.lstrip("/") / "io.prio.class").write_text("idle")


def pct(used, total):
    return 100 * used / total


class Stop(Exception):
    pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rebalance whole titles (with their hardlinks) from the fuller hot SSD to the emptier one")
    ap.add_argument("--dry-run", action="store_true", help="list what would move, change nothing")
    ap.add_argument("--hot", default="/srv/.disks/ssd-hot /srv/.disks/ssd-hot2", help="the two hot branches")
    ap.add_argument("--tol-pct", type=float, default=1.0, help="stop when the fill levels are within 2 x this")
    ap.add_argument("--max-pct", type=float, default=75.0, help="never fill the target above this")
    ap.add_argument("--settle-hours", type=float, default=24)
    ap.add_argument("--rate-mb-s", type=float, default=100, help="assumed copy rate, for the night-window start check")
    ap.add_argument("--log", default="/var/log/ssd-rebalance.log")
    ap.add_argument("--journal", default="/var/lib/ssd-rebalance/journal.json")
    ap.add_argument("--keep-awake", default="/run/beefy-keep-awake.d/ssd-rebalance")
    ap.add_argument("--jf-url", default="http://127.0.0.1:8096")
    ap.add_argument("--jf-key", default="/home/buntu/Projects/Docker/Jellyfin/secrets/api-key")
    a = ap.parse_args(argv)

    def log(msg):
        line = f"{dt.datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}"
        print(line, flush=True)
        if not a.dry_run:
            with contextlib.suppress(OSError), open(a.log, "a") as f:
                f.write(line + "\n")

    hots = a.hot.split()
    (t0, u0), (t1, u1) = usage(hots[0]), usage(hots[1])
    src, dst = (hots[0], hots[1]) if u0 >= u1 else (hots[1], hots[0])
    total = max(t0, t1)
    tol = int(total * a.tol_pct / 100)
    rate = a.rate_mb_s * 10 ** 6

    def state():
        (ts, us), (td, ud) = usage(src), usage(dst)
        return us, ud, ts, td

    if a.dry_run:
        units = scan(src)
        us, ud, ts, td = state()
        n, room = need(us, ud), td * a.max_pct / 100 - ud
        sizes = {k: u.size for k, u in units.items() if u.complete}
        moved = 0
        while (k := pick(sizes, n, tol, room)) is not None:
            s = sizes.pop(k)
            print(f"{s / 1e9:8.1f} GB  {k}")
            n, room, moved = n - s, room - s, moved + s
        print(f"would move {moved / 1e12:.2f} TB from {src} ({pct(us, ts):.1f} %) to {dst} ({pct(ud, td):.1f} %); "
              f"after: {pct(us - moved, ts):.1f} % / {pct(ud + moved, td):.1f} %")
        return 0

    def on_term(*_):
        raise Stop()
    signal.signal(signal.SIGTERM, on_term)
    idle_io()
    mv = Mover(src, dst, a.journal, log=log, settle_hours=a.settle_hours)
    titles = moved_bytes = 0
    with keep_awake(a.keep_awake):
        try:
            with media_lock("/run/lock/beefy-media.lock", 600) as got:
                if got:
                    mv.recover()
            us, ud, ts, td = state()
            log(f"start: {src} {pct(us, ts):.1f} %, {dst} {pct(ud, td):.1f} %, to move about {need(us, ud) / 1e12:.2f} TB")
            units, scanned, done, paused = scan(src), time.time(), set(), None
            log(f"scanned {len(units)} units on {src}")
            while True:
                now = dt.datetime.now(TZ)
                reason = None
                if in_pause_window(now):
                    reason = "night window 03:50-04:50"
                elif lock_busy("/run/lock/beefy-mover.lock"):
                    reason = "the nightly mover runs"
                else:
                    try:
                        if streaming(jf_sessions(a.jf_url, a.jf_key)):
                            reason = "someone streams"
                    except Exception as e:
                        reason = f"Jellyfin unreadable ({type(e).__name__})"
                if reason:
                    if paused != reason:
                        log(f"paused: {reason}")
                        paused = reason
                    time.sleep(60)
                    continue
                if paused:
                    log("resumed")
                    paused = None
                if time.time() - scanned > 3600:
                    units, scanned = scan(src), time.time()
                us, ud, ts, td = state()
                n, room = need(us, ud), td * a.max_pct / 100 - ud
                k = pick({k: u.size for k, u in units.items() if k not in done and u.complete}, n, tol, room)
                if k is None:
                    log(f"done: {src} {pct(us, ts):.1f} %, {dst} {pct(ud, td):.1f} %")
                    break
                u = units[k]
                if not may_start(now, u.size, rate):
                    if paused != "window ahead":
                        log(f"paused: {k} ({u.size / 1e9:.1f} GB) cannot finish before 03:50")
                        paused = "window ahead"
                    time.sleep(60)
                    continue
                with media_lock("/run/lock/beefy-media.lock", 600) as got:
                    if not got:
                        log("media lock busy for 10 min - retrying")
                        continue
                    t = time.time()
                    rc = mv.move_unit(u)
                    dt_s = time.time() - t
                done.add(k)
                if rc == "moved":
                    titles += 1
                    moved_bytes += u.size
                    us, ud, ts, td = state()
                    log(f"moved {k}: {u.size / 1e9:.1f} GB in {dt_s:.0f} s ({u.size / 1e6 / max(dt_s, 1):.0f} MB/s) | "
                        f"total {titles} units, {moved_bytes / 1e12:.3f} TB | {src} {pct(us, ts):.1f} %, {dst} {pct(ud, td):.1f} %")
                elif rc != "skip":
                    log(f"{rc}: {k}")
        except Stop:
            log("stopping on SIGTERM")
            mv.recover()
        except Exception as e:
            log(f"FAILED: {type(e).__name__}: {e}")
            return 1
        finally:
            log(f"exit: moved {titles} units, {moved_bytes / 1e12:.3f} TB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
