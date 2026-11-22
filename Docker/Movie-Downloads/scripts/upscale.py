#!/usr/bin/env python3
"""Own conversions: a download phones and TVs cannot play directly (MPEG-2, VC-1, XviD/DivX, WMV, interlaced) gets a 2160p upscale next to it, a second Jellyfin version "2160p upscale" (README "Own conversions", owner 2026-11-19 and 2026-11-22).
detect (root, systemd/upscale-detect.path): every new import in the Radarr/Sonarr import logs (quality-block.imports, written by scripts/original-audio-hook.sh) is probed (ffprobe of the Jellyfin image: codec and field order); an incompatible one is queued. A file that already has its version is registered as done.
run (root, systemd/upscale.timer 22:10): night only, oldest first, one at a time, only when it ends before 06:00 and the disk stays under its floor (SSD 80 %, HDD 90 %): the proven The Ring Thing settings (libplacebo ewa_lanczossharp to 2160p, x264 crf 16 medium), every audio track copied plus an AAC stereo track of the original language as the default, subtitles copied (no burn-in); placed as `<folder> - 2160p upscale.mkv` (episodes `<name up to SxxEyy> - 2160p upscale.mkv`); Jellyfin told; Bazarr asked for missing subtitles, its text subtitles mirrored onto the version on every run.
Usage: scripts/upscale.py detect | run | status | add <library file> | -h"""
import datetime
import fcntl
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
STATE = os.environ.get("UP_STATE", "/var/lib/upscale/state.json")
POOL = os.environ.get("UP_POOL", "/srv/video")
MEDIA = os.path.join(POOL, "media")
LOGS = {"radarr": "/srv/appdata/radarr/quality-block.imports", "sonarr": "/srv/appdata/sonarr/quality-block.imports"}
LOCK = os.environ.get("UP_LOCK", "/run/lock/beefy-media.lock")
JELLYFIN = os.environ.get("UP_JELLYFIN", "http://127.0.0.1:8096")
JELLYFIN_KEY = HERE.parent.parent / "Jellyfin" / "secrets" / "api-key"
BAZARR = os.environ.get("UP_BAZARR", "http://127.0.0.1:6767")
BAZARR_KEY = HERE.parent / "secrets" / "hook" / "bazarr.apikey"
FFMPEG = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"
# Jellyfin's container user and the shared media group, as phone-fetch.py
CONVERT_USER = os.environ.get("UP_CONVERT_USER", "65108:1100")
CONTAINER = "upscale-convert"
LABEL = "2160p upscale"
WORK = "upscale-work"
PARTIAL = "upscale.partial.mkv"

CODECS = {"mpeg2video": "MPEG-2", "vc1": "VC-1", "mpeg4": "XviD/DivX", "msmpeg4v1": "XviD/DivX", "msmpeg4v2": "XviD/DivX", "msmpeg4v3": "XviD/DivX",
          "wmv1": "WMV", "wmv2": "WMV", "wmv3": "WMV"}
INTERLACED = {"tt", "bb", "tb", "bt"}
SUBS = {"subrip", "ass", "ssa", "hdmv_pgs_subtitle", "dvd_subtitle", "webvtt"}
TEXT_SIDECARS = {".srt", ".ass", ".ssa", ".vtt"}
OWN = re.compile(r" - (2160p upscale|Phone \d+p)\.[^./]+$")
EPISODE = re.compile(r"S\d{2,}E\d{2,}", re.I)

# The Ring Thing 2026-11-19: 4604 s of film, 20 105 977 434 bytes, 11:20 to 15:09 on beefy
BYTES_PER_SECOND = 20_105_977_434 / 4604
TIME_FACTOR = 3.0
FLOOR = {"ssd": 0.80, "hdd": 0.90}
NIGHT_START, NIGHT_END = 22, 6
# setfield=prog only marks the frames: x264 writes progressive anyway, unmarked ones carry the source's field order into the file (Jellyfin then calls it interlaced)
VF = "hwupload,libplacebo=w={w}:h={h}:upscaler=ewa_lanczossharp:format=yuv420p,hwdownload,format=yuv420p,setsar=1,setfield=prog"
X264 = ["-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-level", "5.1", "-pix_fmt", "yuv420p", "-crf", "16"]
AAC = ["aac", "192k", "2"]


def main_video(probe):
    return next((s for s in probe.get("streams") or [] if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)


def verdict(probe):
    """Why the source needs its own version (README "Own conversions"), or None."""
    v = main_video(probe)
    if not v:
        return None
    return CODECS.get(v.get("codec_name")) or ("interlaced" if v.get("field_order") in INTERLACED else None)


def target_size(v):
    """2160p at the source's display aspect, never wider than 3840."""
    try:
        n, d = (int(x) for x in str(v.get("sample_aspect_ratio") or "1:1").split(":"))
        sar = n / d if n and d else 1.0
    except ValueError:
        sar = 1.0
    dar = v["width"] * sar / v["height"]
    if dar >= 3840 / 2160:
        return 3840, round(3840 / dar / 2) * 2
    return round(2160 * dar / 2) * 2, 2160


def original_audio(tracks):
    named = [t for t in tracks if "(Original)" in ((t.get("tags") or {}).get("title") or "")]
    flagged = [t for t in tracks if (t.get("disposition") or {}).get("default")]
    return (named or flagged or tracks)[0]


def plan(probe):
    """ffmpeg arguments after the input: the proven settings, all audio copied plus AAC stereo of the original as the only default, subtitles copied."""
    v = main_video(probe)
    streams = probe.get("streams") or []
    tracks = [s for s in streams if s.get("codec_type") == "audio"]
    w, h = target_size(v)
    args = ["-map", f"0:{v['index']}"]
    for t in tracks:
        args += ["-map", f"0:{t['index']}"]
    if tracks:
        o = original_audio(tracks)
        args += ["-map", f"0:{o['index']}"]
    for s in streams:
        if (s.get("codec_type") == "subtitle" and s.get("codec_name") in SUBS) or s.get("codec_type") == "attachment":
            args += ["-map", f"0:{s['index']}"]
    args += ["-map_chapters", "0", "-map_metadata", "0", "-vf", VF.format(w=w, h=h), *X264]
    if tracks:
        n = len(tracks)
        tags = o.get("tags") or {}
        name = tags.get("title") or tags.get("language") or "Original"
        args += ["-c:a", "copy", f"-c:a:{n}", AAC[0], f"-b:a:{n}", AAC[1], f"-ac:a:{n}", AAC[2],
                 "-disposition:a", "0", f"-disposition:a:{n}", "default",
                 f"-metadata:s:a:{n}", f"language={tags.get('language') or 'und'}", f"-metadata:s:a:{n}", f"title={name} · AAC stereo"]
    args += ["-c:s", "copy", "-c:t", "copy"]
    return args


def own_version(path):
    return bool(OWN.search(os.path.basename(path)))


def version_path(src):
    d, name = os.path.split(src)
    m = EPISODE.search(name)
    prefix = name[:m.end()] if m else os.path.basename(d)
    return os.path.join(d, f"{prefix} - {LABEL}.mkv")


def estimate_bytes(duration):
    return duration * BYTES_PER_SECOND


def fits_night(now, duration):
    """Starts in 22:00-06:00 and ends before 06:00 (TIME_FACTOR × the film's length)."""
    if NIGHT_END <= now.hour < NIGHT_START:
        return False
    end = now.replace(hour=NIGHT_END, minute=0, second=0, microsecond=0)
    if now.hour >= NIGHT_START:
        end += datetime.timedelta(days=1)
    return now + datetime.timedelta(seconds=duration * TIME_FACTOR) <= end


def disk_ok(branch, total, used, extra):
    kind = "hdd" if "hdd" in os.path.basename(branch.rstrip("/")) else "ssd"
    return (used + extra) / total < FLOOR[kind]


def load_state(path):
    try:
        s = json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        s = {}
    s.setdefault("titles", {})
    s.setdefault("seen", {})
    return s


def count(state):
    state["pending"] = sum(1 for r in state.get("titles", {}).values() if r.get("state") == "queued")


def save_state(path, state):
    count(state)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def stamp(deps):
    return deps.clock().isoformat(timespec="seconds")


def register(deps, state, src, app, ident):
    """Probe one library file; queue it when incompatible, done when its version already exists."""
    titles = state["titles"]
    if own_version(src) or (src in titles and titles[src].get("state") in ("queued", "done")):
        return titles.get(src)
    probe = deps.probe(src)
    why = verdict(probe)
    if not why:
        return None
    r = {"src": src, "app": app, "id": ident, "reason": why, "duration": float((probe.get("format") or {}).get("duration") or 0), "at": stamp(deps)}
    if os.path.exists(version_path(src)):
        r.update(state="done", note="version already there", done=stamp(deps))
    else:
        r.update(state="queued")
    titles[src] = r
    print(f"upscale: {os.path.basename(src)}: {why} → {r['state']}", flush=True)
    return r


def detect(deps, state, logs=None, media=MEDIA):
    """New lines of the import logs (`<epoch> <id> <container path> …`)."""
    state.setdefault("titles", {})
    seen = state.setdefault("seen", {})
    probed = 0
    for app, log in (logs or LOGS).items():
        try:
            lines = pathlib.Path(log).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            f = line.split("\t")
            if len(f) < 3 or not f[2].startswith("/data/media/"):
                continue
            key = f"{app}\t{f[0]}\t{f[2]}"
            if key in seen:
                continue
            seen[key] = f[0]
            src = os.path.join(media, f[2][len("/data/media/"):])
            if not os.path.exists(src):
                continue
            probed += 1
            try:
                register(deps, state, src, app, int(f[1]))
            except Exception as e:  # noqa: BLE001
                print(f"upscale: {src}: probe failed: {e}", file=sys.stderr)
    # the logs keep 14 days
    cut = time.time() - 30 * 86400
    for k in [k for k, v in seen.items() if v.isdigit() and int(v) < cut]:
        del seen[k]
    count(state)
    print(f"upscale: {probed} new import(s) probed, {state['pending']} waiting for the night", flush=True)


def on_branch(deps, branch, path):
    return branch + path[len(deps.pool):]


def check(src_probe, out_probe, size):
    want = float((src_probe.get("format") or {}).get("duration") or 0)
    got = float((out_probe.get("format") or {}).get("duration") or 0)
    vids = [s for s in out_probe.get("streams") or [] if s.get("codec_type") == "video"]
    n_src = sum(1 for s in src_probe.get("streams") or [] if s.get("codec_type") == "audio")
    auds = [s for s in out_probe.get("streams") or [] if s.get("codec_type") == "audio"]
    if len(vids) != 1 or (vids[0].get("width"), vids[0].get("height")) != size or vids[0].get("codec_name") != "h264":
        return "video stream not 2160p H.264"
    if n_src and (len(auds) != n_src + 1 or auds[-1].get("codec_name") != "aac"):
        return f"{len(auds)} audio tracks instead of {n_src} + AAC"
    if not want or abs(got - want) > want * 0.01:
        return f"duration {got:.0f} s instead of {want:.0f} s"
    return None


def mirror_subs(src):
    """Bazarr's text subtitles of the original, copied onto the version's name (Jellyfin pairs sidecars by file name)."""
    dest = version_path(src)
    if not os.path.exists(dest):
        return 0
    d, stem = os.path.dirname(src), os.path.splitext(os.path.basename(src))[0]
    vstem = os.path.splitext(os.path.basename(dest))[0]
    n = 0
    for name in os.listdir(d):
        if not name.startswith(stem + ".") or os.path.splitext(name)[1].lower() not in TEXT_SIDECARS:
            continue
        a, b = os.path.join(d, name), os.path.join(d, vstem + name[len(stem):])
        if os.path.exists(b) and os.path.getmtime(b) >= os.path.getmtime(a):
            continue
        shutil.copy2(a, b)
        st = os.stat(a)
        try:
            os.chown(b, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        n += 1
    return n


def convert(deps, r):
    src = r["src"]
    probe = deps.probe(src)
    v = main_video(probe)
    branch = deps.branch(src)
    total, used = deps.usage(branch)
    if not disk_ok(branch, total, used, estimate_bytes(r["duration"])):
        r["note"] = f"skipped {stamp(deps)}: the disk would cross its floor ({used / total:.0%} used of {branch})"
        return
    work = os.path.join(branch, WORK, re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(src))[:120])
    os.makedirs(work, exist_ok=True)
    uid, gid = (int(x) for x in CONVERT_USER.split(":"))
    try:
        os.chown(work, uid, gid)
        os.chmod(work, 0o2775)
    except PermissionError:
        pass
    out = os.path.join(work, PARTIAL)
    r.update(state="converting", started=stamp(deps), work=work)
    rc, err = deps.encode(src, work, plan(probe))
    why = f"ffmpeg exit {rc}: {err.strip()[-300:]}" if rc else check(probe, deps.probe(out), target_size(v))
    if why:
        r.update(state="failed", note=why)
        if os.path.exists(out):
            os.unlink(out)
        os.rmdir(work)
        print(f"upscale: {os.path.basename(src)}: failed: {why}", flush=True)
        return
    dest = version_path(src)
    # the media lock of the mover, original-audio and phone-fetch: never move a file into a folder they are moving
    # read-only: root may not open another user's file in sticky /run/lock for writing (fs.protected_regular)
    with open(deps.lock, "r" if os.path.exists(deps.lock) else "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        target = on_branch(deps, deps.branch(src), dest)
        try:
            os.rename(out, target)
        except OSError:
            shutil.move(out, target)
    st = os.stat(src)
    try:
        os.chown(dest, st.st_uid, st.st_gid)
    except PermissionError:
        pass
    os.chmod(dest, 0o664)
    os.rmdir(work)
    mirror_subs(src)
    r.update(state="done", done=stamp(deps), note="")
    print(f"upscale: {os.path.basename(src)}: done → {os.path.basename(dest)}", flush=True)
    for step, call in (("Jellyfin", lambda: deps.jellyfin(os.path.dirname(dest))), ("Bazarr", lambda: deps.search_subs(r["app"], r["id"]))):
        try:
            call()
        except Exception as e:  # noqa: BLE001
            print(f"upscale: {step} not told: {e}", file=sys.stderr)


def cleanup(work):
    if not work or not os.path.isdir(work):
        return
    for name in (PARTIAL, "progress"):
        try:
            os.unlink(os.path.join(work, name))
        except FileNotFoundError:
            pass
    try:
        os.rmdir(work)
    except OSError as e:
        print(f"upscale: work folder {work} left: {e}", file=sys.stderr)


def run(deps, state, save=None):
    for r in state["titles"].values():
        if r.get("state") == "converting":
            r.update(state="queued", note="interrupted, again")
    for r in state["titles"].values():
        if r.get("state") == "done":
            try:
                mirror_subs(r["src"])
            except OSError as e:
                print(f"upscale: subtitles of {r['src']}: {e}", file=sys.stderr)
    for r in sorted((r for r in state["titles"].values() if r.get("state") == "queued"), key=lambda r: r.get("at") or ""):
        now = deps.clock()
        if not fits_night(now, r["duration"]):
            r["note"] = f"waits {now:%Y-%m-%d %H:%M}: would not end before 06:00"
            print(f"upscale: {os.path.basename(r['src'])}: {r['note']}", flush=True)
            continue
        if not os.path.exists(r["src"]):
            r.update(state="gone", note="the file left the library")
            continue
        if os.path.exists(version_path(r["src"])):
            r.update(state="done", note="version already there", done=stamp(deps))
            continue
        try:
            convert(deps, r)
        except Exception as e:  # noqa: BLE001
            r.update(state="failed", note=str(e)[-300:])
            cleanup(r.get("work"))
        if save:
            save(state)
    count(state)


class Deps:
    """The real world: docker (root), the mergerfs pool, Jellyfin and Bazarr."""
    pool = POOL
    lock = LOCK

    def __init__(self):
        self._image = None

    def image(self):
        if not self._image:
            self._image = subprocess.run(["docker", "inspect", "jellyfin", "--format", "{{.Config.Image}}"], capture_output=True, text=True, check=True).stdout.strip()
        return self._image

    def container(self, src_dir, work, entry, *args, name=None):
        return ["docker", "run", "--rm", *(["--name", name] if name else []), "--network", "none", "--user", CONVERT_USER, "--group-add", str(render_gid()),
                "--device", "/dev/dri", "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
                "-v", f"{src_dir}:/in:ro", *(["-v", f"{work}:/out"] if work else []), "--entrypoint", entry, self.image(), *args]

    def probe(self, path):
        d, name = os.path.split(path)
        p = subprocess.run(self.container(d, None, FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", f"/in/{name}"), capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(f"ffprobe: {p.stderr.strip()[-300:]}")
        return json.loads(p.stdout)

    def encode(self, src, work, args):
        d, name = os.path.split(src)
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        p = subprocess.run(self.container(d, work, FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-init_hw_device", "vulkan", "-i", f"/in/{name}",
                                          *args, "-progress", "/out/progress", "-f", "matroska", f"/out/{PARTIAL}", name=CONTAINER), capture_output=True, text=True)
        try:
            os.unlink(os.path.join(work, "progress"))
        except FileNotFoundError:
            pass
        return p.returncode, p.stderr

    def branch(self, path):
        full = os.getxattr(path, "user.mergerfs.fullpath").decode()
        return full[:len(full) - len(path) + len(self.pool)]

    def usage(self, branch):
        s = os.statvfs(branch)
        return s.f_blocks * s.f_frsize, (s.f_blocks - s.f_bfree) * s.f_frsize

    def clock(self):
        # UP_NOW: a fixed local time for the scratch test of the real runner by day (README "Own conversions")
        return datetime.datetime.fromisoformat(os.environ["UP_NOW"]) if os.environ.get("UP_NOW") else datetime.datetime.now()

    def jellyfin(self, folder):
        key = JELLYFIN_KEY.read_text().strip()
        body = json.dumps({"Updates": [{"Path": "/media/" + os.path.relpath(folder, MEDIA), "UpdateType": "Created"}]}).encode()
        req = urllib.request.Request(JELLYFIN + "/Library/Media/Updated", data=body, method="POST",
                                     headers={"Authorization": f'MediaBrowser Token="{key}"', "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).close()

    def search_subs(self, app, ident):
        if ident is None:
            return
        key = BAZARR_KEY.read_text().strip()
        what = f"movies?radarrid={ident}" if app == "radarr" else f"series?seriesid={ident}"
        req = urllib.request.Request(f"{BAZARR}/api/{what}&action=search-missing", method="PATCH", headers={"X-API-KEY": key})
        urllib.request.urlopen(req, timeout=120).close()


def render_gid():
    import grp
    try:
        return grp.getgrnam("render").gr_gid
    except KeyError:
        return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    cmd = argv[0]
    state = load_state(STATE)
    if cmd == "status" and len(argv) == 1:
        for r in sorted(state["titles"].values(), key=lambda r: r.get("at") or ""):
            print(f"{r.get('at')} {r['state']:<10} {r['reason']:<10} {os.path.basename(r['src'])}{' · ' + r['note'] if r.get('note') else ''}")
        print(f"{state.get('pending', 0)} waiting · next: sudo systemctl start upscale.service (night only), journalctl -u upscale -n 20")
        return 0
    if os.geteuid() != 0:
        print("upscale: needs root (docker); run it through its unit: sudo systemctl start upscale-detect.service | upscale.service", file=sys.stderr)
        return 1
    deps = Deps()
    if cmd == "detect" and len(argv) == 1:
        detect(deps, state)
    elif cmd == "add" and len(argv) == 2:
        src = os.path.realpath(argv[1])
        if not src.startswith(MEDIA + "/") or not os.path.isfile(src):
            print(f"upscale: not a library file under {MEDIA}: {argv[1]}", file=sys.stderr)
            return 1
        app = "radarr" if src.startswith(MEDIA + "/movies/") else "sonarr"
        r = register(deps, state, src, app, None)
        print(f"{os.path.basename(src)}: {r['state'] + ' (' + r['reason'] + ')' if r else 'plays directly, no own version'}")
    elif cmd == "run" and len(argv) == 1:
        run(deps, state, save=lambda s: save_state(STATE, s))
    else:
        print("usage: scripts/upscale.py detect | run | status | add <library file> | -h", file=sys.stderr)
        return 2
    save_state(STATE, state)
    print("next: scripts/upscale.py status")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
