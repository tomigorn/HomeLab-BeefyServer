#!/usr/bin/env python3
"""One-off library check (README "Library check", owner 2026-11-18, fastpi reports/specs/2026-11-18-media-quality-check-design.md part 4).
Every library video on the SSDs (never the HDD, it stays parked) gets the download quality check once (Antivirus hook/quality_check.py: timestamp jump back > 1 s, no video, no audio = broken; container error, duration off the runtime = note).
Report only: results go to ~/.local/state/movie-downloads/library-check.json, shown on the Seerr Manual page (tab Library check); nothing is replaced, blocked or deleted.
ffprobe runs as Jellyfin's uid in a throw-away container of the Jellyfin image (no network, library read-only), nice 19 and idle I/O through /srv/dlpool.
Pauses while anyone plays in Jellyfin (or its sessions are unreadable) and 03:50-04:50 (night window); resumable: a file already checked is skipped unless its size or mtime changed, errors are retried.

Usage: scripts/library-check.py run [--limit N]   (root; normally: sudo systemctl start library-check)
       scripts/library-check.py status
       scripts/library-check.py -h | --help"""
import collections
import fcntl
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qc = _load("quality_check", HERE.parent.parent / "Antivirus" / "hook" / "quality_check.py")
qb = _load("quality_block", HERE / "quality-block.py")
lb = qb.lb

STATE = pathlib.Path(os.environ.get("LC_STATE", "/home/buntu/.local/state/movie-downloads/library-check.json"))
BRANCHES = os.environ.get("LC_BRANCHES", "/srv/.disks/ssd-hot:/srv/.disks/ssd-hot2").split(":")
MEDIA_HOST = os.environ.get("LC_MEDIA_HOST", "/srv/dlpool/media")
INSIDE = "/data/media"
CONTAINER = "library-check"
PROBE_USER = "65108:1100"
FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"
LOCK = "/run/lock/library-check.lock"
VIDEO_EXT = {"mkv", "mp4", "m4v", "avi", "mov", "wmv", "ts", "m2ts", "webm"}
POLL = 60
NIGHT = (3 * 60 + 50, 4 * 60 + 50)
JF_SCRIPTS = HERE.parent.parent / "Jellyfin" / "scripts"

Deps = collections.namedtuple("Deps", "branches state check sessions clock sleep apis")


class ProbeGone(Exception):
    pass


def walk(branches):
    """(path as the apps see it, (size, mtime_ns, host path)) of every video under <branch>/media, sorted; a path on both SSDs counts once."""
    out = {}
    for b in branches:
        root = os.path.join(b, "media")
        for d, dirs, names in os.walk(root):
            dirs.sort()
            for n in names:
                if n.rsplit(".", 1)[-1].lower() not in VIDEO_EXT:
                    continue
                host = os.path.join(d, n)
                p = INSIDE + "/" + os.path.relpath(host, root)
                if p in out:
                    continue
                try:
                    st = os.stat(host)
                except OSError:
                    continue
                out[p] = (st.st_size, st.st_mtime_ns, host)
    return sorted(out.items())


def folder_title(path):
    parts = path[len(INSIDE) + 1:].split("/")
    folder = parts[1] if len(parts) > 2 else os.path.splitext(parts[-1])[0]
    m = re.match(r"(.+?) \((\d{4})\)", folder)
    return {"app": None, "key": None, "title": m.group(1) if m else folder, "year": int(m.group(2)) if m else None, "minutes": None}


def titles(apis):
    """Library path → app, key (movie|tv/<tmdb>), title, year, expected minutes; GET only. An app that does not answer adds nothing."""
    out = {}
    for app, api in apis.items():
        try:
            if app == "radarr":
                for m in api("GET", "/movie") or []:
                    f = m.get("movieFile") or {}
                    if f.get("path"):
                        out[f["path"]] = {"app": app, "key": f"movie/{m['tmdbId']}" if m.get("tmdbId") else f"radarr:{m.get('id')}", "title": m.get("title"),
                                          "year": m.get("year"), "minutes": m.get("runtime") or None}
            else:
                for s in api("GET", "/series") or []:
                    minutes = collections.Counter()
                    for e in api("GET", "/episode", {"seriesId": s["id"]}) or []:
                        if e.get("episodeFileId"):
                            minutes[e["episodeFileId"]] += e.get("runtime") or 0
                    for f in api("GET", "/episodefile", {"seriesId": s["id"]}) or []:
                        if f.get("path"):
                            out[f["path"]] = {"app": app, "key": f"tv/{s['tmdbId']}" if s.get("tmdbId") else f"sonarr:{s.get('id')}", "title": s.get("title"),
                                              "year": s.get("year"), "minutes": minutes.get(f.get("id")) or None}
        except (lb.ApiError, OSError, ValueError, KeyError, TypeError) as e:
            print(f"library-check: {app} not readable ({e}): titles from folder names", file=sys.stderr)
    return out


def playing(sessions):
    """True when anyone plays (a paused player too) or the sessions could not be read."""
    return sessions is None or any(s.get("NowPlayingItem") is not None for s in sessions)


def in_night(now):
    t = time.localtime(now)
    return NIGHT[0] <= t.tm_hour * 60 + t.tm_min < NIGHT[1]


def load(path):
    p = pathlib.Path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: not an object")
    return {"run": dict(data.get("run") or {}), "files": dict(data.get("files") or {})}


def save(path, state):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o644)
    if os.geteuid() == 0:
        st = os.stat(p.parent)
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, p)


def unchanged(e, size, mtime):
    return isinstance(e, dict) and e.get("size") == size and e.get("mtime") == mtime and e.get("verdict") in ("ok", "note", "broken")


def checked(state):
    return sum(1 for e in state["files"].values() if e.get("verdict") in ("ok", "note", "broken"))


def entry(res, size, mtime, title, at, seconds):
    notes = list(res.get("notes") or [])
    verdict = res.get("verdict") or "error"
    if verdict != "error":
        n = qb.duration_note(res.get("duration"), title.get("minutes"))
        if n:
            notes.append(n)
        if verdict == "ok" and notes:
            verdict = "note"
    e = {"size": size, "mtime": mtime, "verdict": verdict, "reasons": list(res.get("reasons") or []), "notes": notes, "duration": res.get("duration"),
         "checkedAt": at, "seconds": round(seconds, 1), "app": title.get("app"), "key": title.get("key"), "title": title.get("title"), "year": title.get("year")}
    if res.get("why"):
        e["why"] = str(res["why"])[:200]
    return e


def wait(deps, state):
    """Back only when nobody plays and it is outside the night window; the state shows why it waits."""
    while True:
        now = deps.clock()
        if in_night(now):
            why = "paused-night"
        elif playing(deps.sessions()):
            why = "paused-playback"
        else:
            return
        if state["run"].get("state") != why:
            state["run"].update(state=why, since=lb.ts(now), updated=lb.ts(now))
            save(deps.state, state)
        deps.sleep(POLL)


def run(deps, limit=None):
    state = load(deps.state)
    files = walk(deps.branches)
    here = {p for p, _ in files}
    state["files"] = {p: e for p, e in state["files"].items() if p in here}
    todo = [(p, v) for p, v in files if not unchanged(state["files"].get(p), v[0], v[1])]
    names = titles(deps.apis) if todo else {}
    r = state["run"]
    now = deps.clock()
    r.update(state="checking", since=lb.ts(now), updated=lb.ts(now), total=len(files), checked=checked(state), bytes=sum(v[0] for _, v in files))
    r.setdefault("started", lb.ts(now))
    r.pop("finished", None)
    save(deps.state, state)
    n = 0
    try:
        for p, (size, mtime, host) in todo:
            if limit is not None and n >= limit:
                r.update(state="stopped", since=lb.ts(deps.clock()))
                break
            wait(deps, state)
            try:
                st = os.stat(host)
            except OSError:
                state["files"].pop(p, None)
                continue
            now = deps.clock()
            if r.get("state") != "checking":
                r.update(state="checking", since=lb.ts(now))
            r.update(current=os.path.basename(p), updated=lb.ts(now))
            began = time.monotonic()
            res = deps.check(p)
            state["files"][p] = entry(res, st.st_size, st.st_mtime_ns, names.get(p) or folder_title(p), lb.ts(deps.clock()), time.monotonic() - began)
            r.update(checked=checked(state), updated=lb.ts(deps.clock()))
            save(deps.state, state)
            n += 1
            e = state["files"][p]
            if e["verdict"] != "ok":
                print(f"{e['verdict']}: {e.get('title')}: {os.path.basename(p)}: {'; '.join(e['reasons'] + e['notes']) or e.get('why', '')}", flush=True)
        else:
            r.update(state="done", since=lb.ts(deps.clock()), finished=lb.ts(deps.clock()))
    except ProbeGone as e:
        r.update(state="error", since=lb.ts(deps.clock()), why=str(e)[:200])
        raise
    except (KeyboardInterrupt, SystemExit):
        r.update(state="stopped", since=lb.ts(deps.clock()))
        raise
    finally:
        r.pop("current", None)
        r["updated"] = lb.ts(deps.clock())
        save(deps.state, state)
    return state


def status(path):
    try:
        state = load(path)
    except (OSError, ValueError) as e:
        print(f"library-check: {path} unreadable: {e}", file=sys.stderr)
        return 1
    r, files = state["run"], state["files"]
    if not r:
        print("library check: not started")
        print("next: sudo systemctl start library-check")
        return 0
    count = collections.Counter(e.get("verdict") for e in files.values())
    print(f"library check: {r.get('state')} since {r.get('since')} · {r.get('checked', 0)} of {r.get('total', 0)} checked · {count['broken']} broken · {count['note']} notes · "
          f"{count['error']} could not be read · updated {r.get('updated')}")
    done = [e for e in files.values() if e.get("seconds")]
    rate = sum(e["size"] for e in done) / max(sum(e["seconds"] for e in done), 1)
    left = max((r.get("bytes") or 0) - sum(e.get("size") or 0 for e in files.values() if e.get("verdict") in ("ok", "note", "broken")), 0)
    if r.get("state") not in ("done",) and rate > 0 and left:
        print(f"left: {left / 1e12:.2f} TB of video (disk reads) · about {left / rate / 3600:.1f} h of reading at {rate / 1e9:.2f} GB/s, plus pauses")
    for v in ("broken", "note", "error"):
        for p, e in sorted(files.items(), key=lambda x: (str(x[1].get("title")), x[0])):
            if e.get("verdict") == v:
                print(f"{v}: {e.get('title')} ({e.get('year')}): {os.path.basename(p)}: {'; '.join(e.get('reasons', []) + e.get('notes', [])) or e.get('why', '')}")
    if r.get("state") == "done":
        print("next: Seerr → Manual interaction → tab Library check (https://requests.holy-grail.ch/manual)")
    else:
        print("next: journalctl -u library-check -f   (watch) · sudo systemctl start library-check   (resume) · sudo systemctl stop library-check   (stop)")
    return 0


class Prober:
    """ffprobe in a throw-away container of the Jellyfin image: Jellyfin's uid, no network, no capabilities, the library read-only at the apps' path."""

    def __init__(self, media_host=MEDIA_HOST, name=CONTAINER, image=None):
        self.media_host, self.name, self.image = media_host, name, image
        self.dir, self.ffprobe = None, None

    def start(self):
        image = self.image or jellyfin_image()
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        subprocess.run(["docker", "run", "-d", "--rm", "--name", self.name, "--network", "none", "--user", PROBE_USER, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                        "--read-only", "--cpu-shares", "2", "--pids-limit", "64", "--memory", "512m", "-v", f"{self.media_host}:{INSIDE}:ro", "--entrypoint", "sleep", image, "infinity"],
                       check=True, capture_output=True, text=True)
        self.dir = tempfile.mkdtemp(prefix="library-check.")
        self.ffprobe = os.path.join(self.dir, "ffprobe")
        with open(self.ffprobe, "w") as f:
            f.write(f'#!/bin/sh\nexec docker exec {self.name} nice -n 19 ionice -c 3 {FFPROBE} "$@"\n')
        os.chmod(self.ffprobe, 0o700)

    def alive(self):
        p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", self.name], capture_output=True, text=True)
        return p.stdout.strip() == "true"

    def check(self, path):
        res = qc.check(path, ffprobe=self.ffprobe)
        if res.get("verdict") == "error" and not self.alive():
            raise ProbeGone(f"probe container {self.name} is gone ({res.get('why')})")
        return res

    def stop(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)


def jellyfin_image():
    return subprocess.run(["docker", "inspect", "jellyfin", "--format", "{{.Config.Image}}"], capture_output=True, text=True, check=True).stdout.strip()


def jellyfin_sessions():
    try:
        sys.path.insert(0, str(JF_SCRIPTS))
        from jellyfin_api import api
        return api("GET", "/Sessions?activeWithinSeconds=960")
    except Exception as e:  # noqa: BLE001
        print(f"library-check: Jellyfin sessions unreadable ({type(e).__name__}): waiting", file=sys.stderr)
        return None


def real_apis():
    out = {}
    for app in ("radarr", "sonarr"):
        try:
            out[app] = lb.client(os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml"))
        except OSError as e:
            print(f"library-check: {app}: {e}", file=sys.stderr)
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    if argv == ["status"]:
        return status(STATE)
    if argv[0] != "run" or len(argv) not in (1, 3) or len(argv) == 3 and (argv[1] != "--limit" or not argv[2].isdigit()):
        print("usage: scripts/library-check.py run [--limit N] | status | -h", file=sys.stderr)
        return 2
    limit = int(argv[2]) if len(argv) == 3 else None
    if os.geteuid() != 0:
        print("library-check: run needs root (docker); next: sudo systemctl start library-check", file=sys.stderr)
        return 1
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("library-check: already running; next: scripts/library-check.py status", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    probe = Prober()
    try:
        probe.start()
        state = run(Deps(BRANCHES, STATE, probe.check, jellyfin_sessions, time.time, time.sleep, real_apis()), limit)
    except ProbeGone as e:
        print(f"library-check: {e}; next: sudo systemctl start library-check", file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f"library-check: {type(e).__name__}: {e}; next: scripts/library-check.py status", file=sys.stderr)
        return 1
    finally:
        probe.stop()
    r = state["run"]
    print(f"library check {r['state']}: {r['checked']} of {r['total']} checked")
    print("next: scripts/library-check.py status" + ("   · resume: sudo systemctl start library-check" if r["state"] != "done" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
