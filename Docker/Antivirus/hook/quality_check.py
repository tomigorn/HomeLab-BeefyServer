"""Quality check of one downloaded video (README "Quality check"): ffprobe demuxes, never decodes. Blocks: a video timestamp jump backwards over JUMP seconds, no video stream, no audio stream. A container read error is a note only."""
import json
import os
import re
import subprocess
import tempfile
import threading

JUMP = 1.0
TIMEOUT_MIN = 600
BYTES_PER_SECOND = 100 * 1024 * 1024


def jumps(pts):
    """(highest timestamp so far, the earlier one that followed) for every backward jump over JUMP seconds; the timeline restarts at each jump."""
    out, top = [], None
    for x in pts:
        try:
            t = float(x)
        except ValueError:
            continue
        if top is not None and t < top - JUMP:
            out.append((top, t))
            top = t
        top = t if top is None else max(top, t)
    return out


def video_index(streams):
    for s in streams:
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            return s.get("index")
    return None


def first_error(err):
    line = next((x.strip() for x in err.splitlines() if x.strip()), "")
    return re.sub(r" @ 0x[0-9a-f]+\]", "]", line)[:200]


def verdict(has_video, has_audio, found, err):
    """(ok|broken, reasons that block, notes that do not)."""
    reasons = []
    if not has_video:
        reasons.append("no video stream")
    elif not has_audio:
        reasons.append("no audio stream")
    if found:
        top, back = found[0]
        reasons.append("timestamp jump %.1f s -> %.1f s%s" % (top, back, " (%d jumps)" % len(found) if len(found) > 1 else ""))
    notes = ["container error: " + first_error(err)] if err.strip() else []
    return ("broken" if reasons else "ok"), reasons, notes


def timeout_for(path):
    try:
        return max(TIMEOUT_MIN, os.path.getsize(path) // BYTES_PER_SECOND)
    except OSError:
        return TIMEOUT_MIN


def low_priority():
    try:
        os.nice(19)
    except OSError:
        pass


def check(path, ffprobe="ffprobe", tmp=None):
    """{"verdict": ok|broken|error, "reasons", "notes", "duration" (s or None), "why" on error}."""
    result = {"verdict": "error", "reasons": [], "notes": [], "duration": None}
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=index,codec_type:stream_disposition=attached_pic:format=duration", "-of", "json", path],
                           capture_output=True, text=True, timeout=TIMEOUT_MIN, preexec_fn=low_priority)
    except (OSError, subprocess.TimeoutExpired) as e:
        return dict(result, why="ffprobe: %s" % type(e).__name__)
    if p.returncode != 0 and "Invalid data found" not in p.stderr:
        return dict(result, why="ffprobe exit %d: %s" % (p.returncode, first_error(p.stderr)))
    try:
        info = json.loads(p.stdout or "{}")
    except ValueError:
        info = {}
    streams = info.get("streams") or []
    try:
        result["duration"] = float((info.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        pass
    vi = video_index(streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if vi is None or not has_audio:
        result["verdict"], result["reasons"], result["notes"] = verdict(vi is not None, has_audio, [], "")
        return result
    with tempfile.TemporaryFile("w+", dir=tmp) as err:
        try:
            q = subprocess.Popen([ffprobe, "-v", "error", "-select_streams", str(vi), "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
                                 stdout=subprocess.PIPE, stderr=err, text=True, preexec_fn=low_priority)
        except OSError as e:
            return dict(result, why="ffprobe: %s" % type(e).__name__)
        timer = threading.Timer(timeout_for(path), q.kill)
        timer.start()
        try:
            with q:
                found = jumps(x.strip().rstrip(",") for x in q.stdout)
            rc = q.returncode
        finally:
            timer.cancel()
        if rc < 0:
            return dict(result, why="ffprobe: killed after %d s" % timeout_for(path))
        err.seek(0)
        text = err.read()
    result["verdict"], result["reasons"], result["notes"] = verdict(True, True, found, text)
    return result
