#!/usr/bin/env python3
"""Phone versions, fetch and place (README "Phone versions", owner 2026-11-19, fastpi reports/specs/2026-11-19-phone-versions-design.md).
request: the title's release search through Radarr/Sonarr, the pick of scripts/phone-version.py (usenet only), sent to SABnzbd category `phone` at high priority, at once (never a night download).
A failed download or check takes the next fitting release at once, up to 3; none fits or all failed → a conversion job for the server copy.
convert (root, systemd/phone-convert.service): one job at a time, Quick Sync HEVC 5 Mbit/s 1080p HDR10, lossy audio only, in a throw-away container of the Jellyfin image; run places the result like a download.
run: SABnzbd progress → the clamav-queue worker's virus scan and quality check (quality-results.jsonl) → the file copied onto the HDD branch into the title's folder as `<folder> - Phone <res>p.mkv` (episodes `<series folder> - SxxEyy - Phone <res>p.mkv` in the season folder), so Jellyfin lists it as a second version of the same item → Jellyfin told about the new file → ready, a pop-up to every device that asked.
Requests come from Jellyfin's popular service as JSON files in <box>/requests (or by hand); the state per Jellyfin item is exported to <box>/state.json for it.
Usage: scripts/phone-fetch.py request --movie <radarr id> | --episode <sonarr episode id> | --item <jellyfin item id>
       scripts/phone-fetch.py run      (systemd/phone-fetch.path and .timer)
       scripts/phone-fetch.py convert  (systemd/phone-convert.path, root)
       scripts/phone-fetch.py status
       scripts/phone-fetch.py forget --item <jellyfin item id>   (drops the title's record; a placed "- Phone" file stays; a later request searches afresh)
       scripts/phone-fetch.py -h"""
import fcntl
import importlib.util
import json
import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("phone_version", HERE / "phone-version.py")
pv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pv)
lb = pv.lb

STATE = pathlib.Path(os.environ.get("PF_STATE", lb.STATE_DIR / "phone-fetch.json"))
LOCK = os.environ.get("PF_LOCK", "/run/lock/beefy-media.lock")
BOX = os.environ.get("PF_BOX", "/srv/appdata/phone-versions")
DL_HOST = os.environ.get("PF_DL_HOST", "/srv/dlpool")
HDD = os.environ.get("PF_HDD", "/srv/.disks/hdd-cold")
MEDIA_HOST = os.environ.get("PF_MEDIA_HOST", "/srv/video/media")
RESULTS = os.environ.get("PF_RESULTS", "/var/lib/clamav-scan/textfile/quality-results.jsonl")
SAB_URL = os.environ.get("PF_SAB_URL", "http://127.0.0.1:8081")
SAB_KEY = pathlib.Path(os.environ.get("PF_SAB_KEY", HERE.parent / "secrets" / "hook" / "sabnzbd.apikey"))
JELLYFIN = os.environ.get("PF_JELLYFIN", "http://127.0.0.1:8096")
JELLYFIN_KEY = pathlib.Path(os.environ.get("PF_JELLYFIN_KEY", HERE.parent.parent / "Jellyfin" / "secrets" / "api-key"))
# a release search asks every indexer through the arr and can take minutes
RELEASE_TIMEOUT = 600
CATEGORY = "phone"
PHONE_DIR = "/data/usenet/complete/phone/"
CONVERT_DIR = PHONE_DIR + "convert/"
MAX_RELEASES = 3
RETRY_HOURS = 24
CHECK_HOURS = 6
PLACE_HOURS = 2
KEEP_DAYS = 180
GUARD_HOURS = 1
VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".webm"}
ACTIVE = ("searching", "downloading", "checking", "placing", "ready", "queued", "converting")
STEPPED = ("searching", "downloading", "checking", "placing", "queued", "converting")
# Jellyfin's container user and the shared media group: the output is readable and removable by buntu
CONVERT_USER = os.environ.get("PF_CONVERT_USER", "65108:1100")
FFMPEG = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"
LOSSY = {"eac3", "ac3", "aac"}
TEXT_OR_IMAGE_SUBS = {"subrip", "ass", "ssa", "hdmv_pgs_subtitle", "dvd_subtitle", "webvtt"}
COLOUR = re.compile(r"^[a-z0-9-]{1,20}$")
JOB_KEY = re.compile(r"^(radarr|sonarr)-[0-9]+$")
READY_HEADER = "✅ Phone version ready"
READY_TEXT = "{title}: the phone version is ready. Tap Download again (Original)."


class Refused(Exception):
    pass


class Deps:
    def __init__(self, arr, sab, jf, dl_host=DL_HOST, hdd=HDD, results=RESULTS, box=BOX, clock=time.time, media_host=MEDIA_HOST, docker=None, image=None):
        self.arr, self.sab, self.jf, self.dl_host, self.hdd, self.results, self.box, self.clock = arr, sab, jf, dl_host, hdd, results, box, clock
        self.media_host, self.docker, self.image = media_host, docker, image


def ts(now):
    return lb.ts(now)


def load_state(path):
    p = pathlib.Path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: not an object")
    return {"requests": dict(data.get("requests") or {})}


def save_state(path, state):
    lb.save_state(path, state)


def forget(state, item):
    """Drop the record of one Jellyfin item; returns the removed record or None. Files stay untouched."""
    for key, r in list(state["requests"].items()):
        if str(r.get("jfItem")) == str(item):
            return state["requests"].pop(key)
    return None


def forget_locked(path, item, lock=None, wait=120):
    """forget under the media lock (the one `run` holds), state re-read inside it; returns (record or None, state)."""
    with open(lock or LOCK, "a") as fh:
        end = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise TimeoutError("a run holds the media lock")
                time.sleep(1)
        state = load_state(path)
        r = forget(state, item)
        if r:
            save_state(path, state)
        return r, state


def resolve(deps, target):
    """{"app", "id"} or {"item": <Jellyfin id>} → (app, arr id, Jellyfin item id or None)."""
    if "item" not in target:
        return target["app"], int(target["id"]), target.get("jfItem")
    iid = str(target["item"])
    item = deps.jf("GET", f"/Items/{urllib.parse.quote(iid, safe='')}?Fields=ProviderIds,MediaSources")
    if not item:
        raise Refused(f"Jellyfin item {iid} unknown")
    if item.get("Type") == "Movie":
        tmdb = int((item.get("ProviderIds") or {}).get("Tmdb") or 0)
        found = deps.arr["radarr"]("GET", "/movie", {"tmdbId": tmdb}) if tmdb else []
        if not found:
            raise Refused(f"film {iid} not in Radarr")
        return "radarr", found[0]["id"], iid
    if item.get("Type") == "Episode":
        series = deps.jf("GET", f"/Items/{urllib.parse.quote(str(item.get('SeriesId')), safe='')}?Fields=ProviderIds") or {}
        tvdb = int((series.get("ProviderIds") or {}).get("Tvdb") or 0)
        found = deps.arr["sonarr"]("GET", "/series", {"tvdbId": tvdb}) if tvdb else []
        if not found:
            raise Refused(f"series of episode {iid} not in Sonarr")
        eps = deps.arr["sonarr"]("GET", "/episode", {"seriesId": found[0]["id"]}) or []
        ep = next((e for e in eps if e.get("seasonNumber") == item.get("ParentIndexNumber") and e.get("episodeNumber") == item.get("IndexNumber")), None)
        if not ep:
            raise Refused(f"episode {iid} not in Sonarr")
        return "sonarr", ep["id"], iid
    raise Refused(f"item {iid} is no film or episode")


def request(deps, state, target, who):
    """Start (or join) the phone version of one title; returns its state record."""
    now = deps.clock()
    app, ident, jf_item = resolve(deps, target)
    key = f"{app}-{ident}"
    r = state["requests"].get(key)
    asker = {k: who[k] for k in ("user", "device") if who.get(k)}
    if r and (r["state"] in ACTIVE or now - (lb.epoch(r.get("at")) or 0) < RETRY_HOURS * 3600):
        if asker and asker not in [{k: x[k] for k in ("user", "device") if x.get(k)} for x in r["requestedBy"]]:
            r["requestedBy"].append(dict(asker, at=ts(now)))
        if jf_item and not r.get("jfItem"):
            r["jfItem"] = jf_item
        return r
    try:
        return start(deps, state, app, ident, jf_item, key, asker, now)
    except BaseException:
        if state["requests"].get(key, {}).get("state") == "searching":
            del state["requests"][key]
        raise


def start(deps, state, app, ident, jf_item, key, asker, now):
    api = deps.arr[app]
    title, profile_id, query = pv.server_title(app, api, ident)
    r = state["requests"][key] = {"key": key, "app": app, "arrId": ident, "jfItem": jf_item, "title": title["name"], "at": ts(now), "updated": ts(now),
                                  "state": "searching", "option": None, "release": None, "size": None, "nzo": None, "percent": 0, "storage": None,
                                  "target": None, "phoneItem": None, "note": "", "requestedBy": [dict(asker, at=ts(now))] if asker else [], "notified": []}
    r["folder"] = target_folder(app, api, ident)
    if title["resolution"] != 2160:
        r.update(state="not-needed", option=5, note="the server copy is not 4K")
        return r
    return fetch_next(deps, r)


def fetch_next(deps, r):
    """The next fitting release not tried yet into SABnzbd, or the conversion job once none is left."""
    now = deps.clock()
    api = deps.arr[r["app"]]
    r.update(state="searching", updated=ts(now))
    tried = r.setdefault("tried", [])
    while len(tried) < MAX_RELEASES:
        title, profile_id, query = pv.server_title(r["app"], api, r["arrId"])
        raw = [x for x in api("GET", "/release", query) or [] if x.get("protocol") == "usenet"]
        flat = [dict(pv.flatten(x), _i=i) for i, x in enumerate(raw)]
        p = pv.pick(flat, title, pv.bad_formats(api("GET", f"/qualityprofile/{profile_id}")), tried)
        if not p["release"]:
            break
        rel = raw[p["release"]["_i"]]
        tried.append(rel["title"])
        got = deps.sab({"mode": "addurl", "name": rel["downloadUrl"], "cat": CATEGORY, "priority": "1", "nzbname": f"phone {r['key']} {rel['title']}"[:200]})
        ids = (got or {}).get("nzo_ids") or []
        if not ids:
            r.setdefault("failures", []).append(f"SABnzbd refused the release: {got}")
            continue
        res = (rel.get("quality") or {}).get("quality", {}).get("resolution") or 0
        r.update(state="downloading", option=p["option"], release=rel["title"], size=rel.get("size"), nzo=ids[0], resolution=res, percent=0, storage=None, updated=ts(now))
        return r
    return queue_convert(deps, r)


def fail(deps, r, note):
    r.setdefault("failures", []).append(note)
    r["note"] = note
    fetch_next(deps, r)


def server_file(app, api, ident):
    if app == "radarr":
        return ((api("GET", f"/movie/{ident}") or {}).get("movieFile") or {}).get("path")
    e = api("GET", f"/episode/{ident}") or {}
    return ((api("GET", f"/episodefile/{e['episodeFileId']}") if e.get("episodeFileId") else {}) or {}).get("path")


def job_dir(deps, key):
    return host(deps, CONVERT_DIR + key, deps.dl_host)


def queue_convert(deps, r):
    now = deps.clock()
    src = server_file(r["app"], deps.arr[r["app"]], r["arrId"])
    if not src:
        r.update(state="nothing", option=3, note="no server copy to convert", updated=ts(now))
        return r
    d = job_dir(deps, r["key"])
    shutil.rmtree(d, ignore_errors=True)
    made = []
    p = d
    while not os.path.isdir(p):
        made.append(p)
        p = os.path.dirname(p)
    for p in reversed(made):
        os.mkdir(p)
        os.chmod(p, 0o2775)
    with open(os.path.join(d, ".job.json"), "w") as f:
        json.dump({"key": r["key"], "src": src, "at": ts(now)}, f)
    os.replace(os.path.join(d, ".job.json"), os.path.join(d, "job.json"))
    r.update(state="queued", option=3, percent=0, release=None, nzo=None, resolution=1080, updated=ts(now))
    return r


def target_folder(app, api, ident):
    """(library folder as the arrs see it, file name prefix) of the title."""
    if app == "radarr":
        path = api("GET", f"/movie/{ident}")["path"]
        return {"dir": path, "prefix": posixpath.basename(path.rstrip("/"))}
    e = api("GET", f"/episode/{ident}")
    s = api("GET", f"/series/{e['seriesId']}")
    base = s["path"].rstrip("/")
    code = f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}"
    # named like the episode's own file up to SxxEyy, so Jellyfin labels the version "SxxEyy - Phone 1080p" like the other one
    own = posixpath.basename((api("GET", f"/episodefile/{e['episodeFileId']}") or {}).get("path") or "") if e.get("episodeFileId") else ""
    i = own.find(code)
    prefix = own[:i + len(code)] if i > 0 else f"{re.sub(r' \[[a-z]+id-[^]]*\]', '', posixpath.basename(base))} - {code}"
    return {"dir": f"{base}/Season {e['seasonNumber']:02d}", "prefix": prefix}


def host(deps, container_path, root):
    """/data/... (SABnzbd and the arrs) → the same path under a host root (the download view or a pool branch)."""
    rel = posixpath.normpath(container_path)[len("/data/"):]
    if rel.startswith("..") or posixpath.isabs(rel):
        raise Refused(f"path outside /data: {container_path}")
    return os.path.join(root, rel)


def quality_verdict(deps, path):
    out = None
    for p in (deps.results + ".1", deps.results):
        try:
            text = pathlib.Path(p).read_text()
        except OSError:
            continue
        for raw in text.splitlines():
            try:
                x = json.loads(raw)
            except ValueError:
                continue
            if isinstance(x, dict) and x.get("path") == path:
                out = x
    return out


def biggest_video(d):
    best = None
    for root, _, names in os.walk(d):
        for n in names:
            p = os.path.join(root, n)
            if os.path.splitext(n)[1].lower() in VIDEO and (best is None or os.path.getsize(p) > os.path.getsize(best)):
                best = p
    return best


def remove_job(deps, r):
    """The finished job's folder (only ever under the phone category folder) and its SABnzbd history entry."""
    if r.get("storage") and r["storage"].startswith(PHONE_DIR):
        shutil.rmtree(host(deps, r["storage"], deps.dl_host), ignore_errors=True)
    try:
        deps.sab({"mode": "history", "name": "delete", "value": r["nzo"]})
    except Exception as e:  # noqa: BLE001
        print(f"phone-fetch: SABnzbd history entry {r['nzo']} not removed: {e}", file=sys.stderr)


def place(deps, r, src):
    folder = r["folder"]
    ext = os.path.splitext(src)[1].lower() or ".mkv"
    name = f"{folder['prefix']} - Phone {r.get('resolution') or 1080}p{ext}"
    dest_dir = host(deps, folder["dir"], deps.hdd)
    made = []
    d = dest_dir
    while not os.path.isdir(d):
        made.append(d)
        d = os.path.dirname(d)
    for d in reversed(made):
        os.mkdir(d)
        os.chmod(d, 0o2775)
    dest = os.path.join(dest_dir, name)
    tmp = os.path.join(dest_dir, f".{name}.partial")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o664)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    return dest, posixpath.join("/" + folder["dir"][len("/data/"):], name)


def notify(deps, r, header, text):
    for x in r["requestedBy"]:
        if not x.get("device") or x["device"] in r["notified"]:
            continue
        try:
            for s in deps.jf("GET", "/Sessions?deviceId=" + urllib.parse.quote(x["device"], safe="")) or []:
                if s.get("DeviceId") == x["device"] and (not x.get("user") or s.get("UserId") == x["user"]):
                    deps.jf("POST", f"/Sessions/{urllib.parse.quote(str(s['Id']), safe='')}/Message", {"Header": header, "Text": text, "TimeoutMs": 15000})
                    r["notified"].append(x["device"])
                    break
        except Exception as e:  # noqa: BLE001
            print(f"phone-fetch: pop-up for {r['key']} not sent: {type(e).__name__}", file=sys.stderr)


def follow_convert(deps, r):
    now = deps.clock()
    d = job_dir(deps, r["key"])
    if not os.path.isdir(d):
        r.update(state="failed", note="the conversion job vanished", updated=ts(now))
        return
    try:
        res = json.loads(pathlib.Path(d, "result.json").read_text())
    except (OSError, ValueError):
        res = None
    if res is None:
        try:
            status = json.loads(pathlib.Path(d, "status.json").read_text())
            lines = pathlib.Path(d, "progress").read_text().splitlines()
        except (OSError, ValueError):
            return
        # out_time lags far behind the video when a file has many subtitle tracks: frames when the frame count is known
        frames = [int(x[6:]) for x in lines if x.startswith("frame=") and x[6:].isdigit()]
        us = [int(x[12:]) for x in lines if x.startswith("out_time_us=") and x[12:].isdigit()]
        total, duration = status.get("frames") or 0, status.get("duration") or 0
        share = frames[-1] / total if frames and total else us[-1] / 1e6 / duration if us and duration else None
        if share is not None:
            r.update(state="converting", percent=max(0, min(99, int(share * 100))))
        return
    if not res.get("ok"):
        shutil.rmtree(d, ignore_errors=True)
        if res.get("skip"):
            r.update(state="nothing", note=res.get("why") or "not converted", updated=ts(now))
        else:
            r.update(state="failed", note=f"conversion failed: {res.get('why') or 'unknown'}", updated=ts(now))
        return
    dest, jf_path = place(deps, r, os.path.join(d, os.path.basename(res.get("file") or "phone.mkv")))
    deps.jf("POST", "/Library/Media/Updated", {"Updates": [{"Path": jf_path, "UpdateType": "Created"}]})
    shutil.rmtree(d, ignore_errors=True)
    r.update(state="placing", percent=100, target=dest, jfPath=jf_path, updated=ts(now))


def step(deps, r):
    now = deps.clock()
    if r["state"] == "searching":
        return fetch_next(deps, r)
    since = now - (lb.epoch(r.get("updated")) or now)
    if r["state"] == "downloading":
        q = (deps.sab({"mode": "queue", "nzo_ids": r["nzo"]}) or {}).get("queue", {}).get("slots") or []
        if q:
            r["percent"] = int(float(q[0].get("percentage") or 0))
            return
        h = (deps.sab({"mode": "history", "nzo_ids": r["nzo"]}) or {}).get("history", {}).get("slots") or []
        if not h:
            return
        if h[0].get("status") == "Failed":
            remove_job(deps, r)
            return fail(deps, r, f"download failed: {h[0].get('fail_message') or 'unknown'}")
        if h[0].get("status") != "Completed":
            return
        storage = h[0].get("storage") or ""
        if not storage.startswith(PHONE_DIR) or storage.startswith(CONVERT_DIR):
            return fail(deps, r, f"finished outside the phone folder: {storage}")
        r.update(state="checking", storage=storage, percent=100, updated=ts(now))
    if r["state"] == "checking":
        local = host(deps, r["storage"], deps.dl_host)
        video = biggest_video(local)
        if not video:
            # SABnzbd can report the job done while its post-processing still renames or moves the file
            if since > CHECK_HOURS * 3600:
                remove_job(deps, r)
                fail(deps, r, "no video file in the download")
            return
        v = quality_verdict(deps, PHONE_DIR + os.path.relpath(video, host(deps, PHONE_DIR, deps.dl_host)))
        if v is None:
            if since > CHECK_HOURS * 3600:
                remove_job(deps, r)
                fail(deps, r, f"no virus/quality check result after {CHECK_HOURS} h")
            return
        if v.get("verdict") != "ok":
            remove_job(deps, r)
            return fail(deps, r, "quality check: " + ("; ".join(v.get("reasons") or []) or v.get("verdict")))
        if not os.path.exists(video):
            return fail(deps, r, "the download vanished (virus quarantine?)")
        dest, jf_path = place(deps, r, video)
        deps.jf("POST", "/Library/Media/Updated", {"Updates": [{"Path": jf_path, "UpdateType": "Created"}]})
        remove_job(deps, r)
        r.update(state="placing", target=dest, jfPath=jf_path, updated=ts(now))
        return
    if r["state"] in ("queued", "converting"):
        return follow_convert(deps, r)
    if r["state"] == "placing":
        if r.get("jfItem"):
            item = deps.jf("GET", f"/Items/{urllib.parse.quote(r['jfItem'], safe='')}?Fields=MediaSources") or {}
            src = next((s for s in item.get("MediaSources") or [] if s.get("Path") == r.get("jfPath")), None)
            if src:
                r.update(state="ready", phoneItem=src.get("Id"), updated=ts(now))
                notify(deps, r, READY_HEADER, READY_TEXT.format(title=r["title"]))
            elif since > PLACE_HOURS * 3600:
                r["note"] = f"Jellyfin has not listed the phone version after {PLACE_HOURS} h"
        else:
            r.update(state="ready", updated=ts(now))


def guard(deps, r):
    """Hourly per ready phone version: Radarr/Sonarr import a stray video on a disk scan once their own file is gone; if one now tracks the phone file, say so."""
    now = deps.clock()
    if now - (lb.epoch(r.get("guarded")) or 0) < GUARD_HOURS * 3600:
        return
    r["guarded"] = ts(now)
    api = deps.arr[r["app"]]
    if r["app"] == "radarr":
        tracked = ((api("GET", f"/movie/{r['arrId']}") or {}).get("movieFile") or {}).get("path") or ""
    else:
        e = api("GET", f"/episode/{r['arrId']}") or {}
        tracked = ((api("GET", f"/episodefile/{e['episodeFileId']}") if e.get("episodeFileId") else {}) or {}).get("path") or ""
    if " - Phone " in posixpath.basename(tracked):
        r.update(guard=True, note=f"{r['app'].capitalize()} now tracks the phone version as its own file (server copy gone?): check the title")
        print(f"phone-fetch: {r['key']}: {r['note']}", file=sys.stderr)


def take_inbox(deps, state):
    d = pathlib.Path(deps.box) / "requests"
    try:
        files = sorted(d.glob("*.json")) + sorted(d.glob("*.json.retry"))
    except OSError:
        return
    for f in files:
        try:
            x = json.loads(f.read_text())
            request(deps, state, {"item": x["item"]}, {"user": x.get("user"), "device": x.get("device")})
        except Refused as e:
            print(f"phone-fetch: {f.name}: {e}", file=sys.stderr)
        except (ValueError, KeyError, OSError) as e:
            print(f"phone-fetch: {f.name} unreadable: {e}", file=sys.stderr)
        except lb.ApiError as e:
            # renamed out of the path unit's *.json glob: the 2-min timer retries it, no tight loop
            print(f"phone-fetch: {f.name}: {e}, retried by the timer", file=sys.stderr)
            if f.suffix == ".json":
                f.rename(f.with_name(f.name + ".retry"))
            continue
        f.unlink(missing_ok=True)


def export(deps, state):
    items = {}
    for r in state["requests"].values():
        if r.get("jfItem"):
            items[r["jfItem"]] = {k: r.get(k) for k in ("state", "percent", "phoneItem", "option", "title", "updated", "at", "note")}
            items[r["jfItem"]]["requestedBy"] = [{"user": x["user"], "at": x.get("at")} for x in r.get("requestedBy") or [] if x.get("user")]
    out = pathlib.Path(deps.box) / "state.json"
    try:
        tmp = out.with_name(".state.json.tmp")
        tmp.write_text(json.dumps({"items": items}, ensure_ascii=False))
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except OSError as e:
        print(f"phone-fetch: {out} not written: {e}", file=sys.stderr)


def run(deps, state):
    take_inbox(deps, state)
    for r in list(state["requests"].values()):
        if r["state"] in STEPPED + ("ready",):
            try:
                step(deps, r) if r["state"] != "ready" else guard(deps, r)
            except (lb.ApiError, OSError, Refused) as e:
                print(f"phone-fetch: {r['key']}: {e}, retried next run", file=sys.stderr)
    now = deps.clock()
    for k, r in list(state["requests"].items()):
        if r["state"] not in ACTIVE and now - (lb.epoch(r.get("updated")) or now) > KEEP_DAYS * 86400:
            del state["requests"][k]
    export(deps, state)


HW_INPUT = ["-init_hw_device", "vaapi=va:,vendor_id=0x8086,driver=iHD", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs", "-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]


def plan(probe):
    """The ffmpeg arguments after the input for one source (owner 2026-11-20 15:00 and 2026-11-21), or {"skip": True, "why"}."""
    streams = probe.get("streams") or []
    video = next((x for x in streams if x.get("codec_type") == "video" and not (x.get("disposition") or {}).get("attached_pic")), None)
    if not video:
        return {"skip": True, "why": "no video stream"}
    dovi = next((d for d in video.get("side_data_list") or [] if "dv_profile" in d), None)
    if dovi and not dovi.get("dv_bl_signal_compatibility_id"):
        return {"skip": True, "why": "Dolby Vision without an HDR10 layer"}
    audio = [x for x in streams if x.get("codec_type") == "audio"]
    langs = {}
    for x in audio:
        langs.setdefault((x.get("tags") or {}).get("language") or "und", []).append(x)
    keep, convert = set(), set()
    for tracks in langs.values():
        lossy = [x["index"] for x in tracks if x.get("codec_name") in LOSSY]
        if lossy:
            keep.update(lossy)
        else:
            convert.add(max(tracks, key=lambda x: (x.get("channels") or 0, -x["index"]))["index"])
    args = ["-map", f"0:{video['index']}"]
    codecs = []
    for x in audio:
        if x["index"] in keep or x["index"] in convert:
            args += ["-map", f"0:{x['index']}"]
            n = sum(c.startswith("-c:a:") for c in codecs)
            codecs += [f"-c:a:{n}", "copy"] if x["index"] in keep else [f"-c:a:{n}", "eac3", f"-b:a:{n}", "640k", f"-ac:a:{n}", str(min(6, x.get("channels") or 6))]
    for x in streams:
        if (x.get("codec_type") == "subtitle" and x.get("codec_name") in TEXT_OR_IMAGE_SUBS) or x.get("codec_type") == "attachment":
            args += ["-map", f"0:{x['index']}"]
    colour = []
    for flag, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_transfer"), ("-colorspace", "color_space")):
        if COLOUR.match(str(video.get(key) or "")):
            colour += [flag, video[key]]
    args += ["-map_chapters", "0", "-map_metadata", "0",
             "-vf", "scale_vaapi=w=1920:h=-2:format=p010,hwmap=derive_device=qsv,format=qsv",
             "-c:v", "hevc_qsv", "-low_power", "1", "-preset", "slow", "-b:v", "5M", "-maxrate", "7500k", "-bufsize", "15M", "-fps_mode", "passthrough", *colour,
             *codecs, "-c:s", "copy", "-c:t", "copy"]
    duration = float((probe.get("format") or {}).get("duration") or 0)
    num, _, den = str(video.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0
    return {"input": HW_INPUT, "args": args, "duration": duration, "frames": int(duration * fps)}


def check(src, out):
    """Why the converted file is not fit, or None."""
    want = float((src.get("format") or {}).get("duration") or 0)
    got = float((out.get("format") or {}).get("duration") or 0)
    kinds = [x.get("codec_type") for x in out.get("streams") or []]
    if kinds.count("video") != 1:
        return f"{kinds.count('video')} video streams"
    if "audio" not in kinds:
        return "no audio stream"
    if not want or abs(got - want) > want * 0.01:
        return f"duration {got:.0f} s instead of {want:.0f} s"
    return None


def container(deps, d, entry, *args, name=None):
    return ["run", "--rm", *(["--name", name] if name else []), "--network", "none", "--user", CONVERT_USER, "--group-add", str(render_gid()), "--device", "/dev/dri", "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL", "-v", f"{deps.media_host}:/media:ro", "-v", f"{d}:/out", "--entrypoint", entry, deps.image(), *args]


def render_gid():
    import grp
    try:
        return grp.getgrnam("render").gr_gid
    except KeyError:
        return 0


def ffprobe(deps, d, path):
    rc, out, err = deps.docker(container(deps, d, FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", path))
    if rc:
        raise Refused(f"ffprobe: {err.strip()[-300:]}")
    return json.loads(out)


def convert_one(deps, d, job):
    src = posixpath.normpath(job.get("src") or "")
    if not src.startswith("/data/media/") or not JOB_KEY.match(job.get("key") or ""):
        return {"ok": False, "why": f"refused job {job.get('key')}: {src}"}
    inside = "/media/" + src[len("/data/media/"):]
    try:
        probe = ffprobe(deps, d, inside)
        p = plan(probe)
        if p.get("skip"):
            return {"ok": False, "skip": True, "why": p["why"]}
        with open(os.path.join(d, "status.json"), "w") as f:
            json.dump({"duration": p["duration"], "frames": p["frames"]}, f)
        rc, _, err = deps.docker(container(deps, d, FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats", "-y", *p["input"], "-i", inside, *p["args"],
                                           "-progress", "/out/progress", "-f", "matroska", "/out/phone.partial.mkv", name="phone-convert"))
        if rc:
            return {"ok": False, "why": f"ffmpeg exit {rc}: {err.strip()[-300:]}"}
        why = check(probe, ffprobe(deps, d, "/out/phone.partial.mkv"))
        if why:
            return {"ok": False, "why": why}
    except (Refused, ValueError, OSError) as e:
        return {"ok": False, "why": str(e)}
    os.replace(os.path.join(d, "phone.partial.mkv"), os.path.join(d, "phone.mkv"))
    return {"ok": True, "file": "phone.mkv"}


def write_result(d, res):
    if not res["ok"]:
        pathlib.Path(d, "phone.partial.mkv").unlink(missing_ok=True)
    tmp = os.path.join(d, ".result.json")
    with open(tmp, "w") as f:
        json.dump(res, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(d, "result.json"))


def convert(deps):
    """Every waiting conversion job, oldest first, one at a time (root: docker)."""
    root = host(deps, CONVERT_DIR, deps.dl_host)
    if not os.path.isdir(root):
        return
    # the folder name becomes part of a docker mount: only real job keys
    for name in sorted(filter(JOB_KEY.match, os.listdir(root))):
        d = os.path.join(root, name)
        if os.path.exists(os.path.join(d, "running.json")) and not os.path.exists(os.path.join(d, "result.json")):
            os.unlink(os.path.join(d, "running.json"))
            write_result(d, {"ok": False, "why": "interrupted"})
    while True:
        jobs = []
        for name in filter(JOB_KEY.match, os.listdir(root)):
            try:
                jobs.append((json.loads(pathlib.Path(root, name, "job.json").read_text()), os.path.join(root, name)))
            except (OSError, ValueError):
                continue
        if not jobs:
            return
        job, d = min(jobs, key=lambda x: str(x[0].get("at")))
        os.replace(os.path.join(d, "job.json"), os.path.join(d, "running.json"))
        print(f"phone-fetch: converting {job.get('key')}", flush=True)
        res = convert_one(deps, d, job)
        write_result(d, res)
        os.unlink(os.path.join(d, "running.json"))
        # phone-fetch.path watches this file: it places the result at once
        pathlib.Path(root, ".done").write_text(job.get("key") or "")
        print(f"phone-fetch: {job.get('key')}: {'ok' if res['ok'] else res['why']}", flush=True)


def docker_client(argv):
    p = subprocess.run(["docker", *argv], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def jellyfin_image():
    return subprocess.run(["docker", "inspect", "jellyfin", "--format", "{{.Config.Image}}"], capture_output=True, text=True, check=True).stdout.strip()


def sab_client():
    key = SAB_KEY.read_text().strip()

    def call(params):
        url = f"{SAB_URL}/api?" + urllib.parse.urlencode({**params, "output": "json", "apikey": key})
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except (OSError, ValueError) as e:
            raise lb.ApiError(f"SABnzbd {params.get('mode')}: {type(e).__name__}") from None
    return call


def jf_client():
    key = JELLYFIN_KEY.read_text().strip()
    admin = []

    def item(path):
        # Jellyfin answers GET /Items/<id> with an API key only for a user: asked as the first administrator
        if not admin:
            admin.append(next(u["Id"] for u in raw("GET", "/Users") if (u.get("Policy") or {}).get("IsAdministrator")))
        iid, _, query = path[len("/Items/"):].partition("?")
        got = raw("GET", f"/Items?ids={iid}&userId={admin[0]}&{query}") or {}
        return (got.get("Items") or [None])[0]

    def call(method, path, body=None):
        if method == "GET" and path.startswith("/Items/") and "/" not in path[len("/Items/"):].split("?")[0]:
            return item(path)
        return raw(method, path, body)

    def raw(method, path, body=None):
        headers = {"Authorization": f'MediaBrowser Token="{key}"'}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(JELLYFIN + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None
            raise lb.ApiError(f"Jellyfin {method} {path.split('?')[0]}: {e.code}") from None
        except (OSError, ValueError) as e:
            raise lb.ApiError(f"Jellyfin {method} {path.split('?')[0]}: {type(e).__name__}") from None
    return call


def real_deps():
    arr = {app: lb.client(os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml"), timeout=RELEASE_TIMEOUT) for app in ("radarr", "sonarr")}
    return Deps(arr, sab_client(), jf_client())


def main(argv):
    if not argv or "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0 if argv else 2
    cmd = argv[0]
    if cmd == "forget":
        if len(argv) != 3 or argv[1] != "--item":
            print("usage: scripts/phone-fetch.py forget --item <jellyfin item id>", file=sys.stderr)
            return 2
        try:
            r, state = forget_locked(STATE, argv[2])
        except (OSError, ValueError) as e:
            print(f"phone-fetch: forget failed: {e}", file=sys.stderr)
            print("next: scripts/phone-fetch.py forget --item " + argv[2] + "   (try again in a minute)", file=sys.stderr)
            return 1
        if not r:
            print(f"phone-fetch: no phone-fetch record for item {argv[2]}", file=sys.stderr)
            print("next: scripts/phone-fetch.py status   (lists the requests), then forget --item <jellyfin item id>", file=sys.stderr)
            return 1
        export(Deps(None, None, None, box=BOX), state)
        print(f"{r.get('title')}: record forgotten (was {r.get('state')}); a placed \"- Phone\" file stays")
        print(f"next: scripts/phone-fetch.py request --item {argv[2]}   (searches afresh), scripts/phone-fetch.py status")
        return 0
    try:
        state = load_state(STATE)
    except (OSError, ValueError) as e:
        print(f"phone-fetch: {STATE} unreadable: {e}", file=sys.stderr)
        return 1
    if cmd == "status" and len(argv) == 1:
        for r in sorted(state["requests"].values(), key=lambda r: r.get("at") or ""):
            print(f"{r['at']} {r['title']}: {r['state']}{' ' + str(r['percent']) + ' %' if r['state'] in ('downloading', 'converting') else ''} · option {r.get('option')} · {r.get('release') or ''}"
                  f"{' · ' + r['note'] if r.get('note') else ''}")
        print("next: scripts/phone-fetch.py request --movie <radarr id>")
        return 0
    if cmd == "convert" and len(argv) == 1:
        convert(Deps(None, None, None, docker=docker_client, image=jellyfin_image))
        print("next: journalctl -u phone-fetch -n 5   (placing), scripts/phone-fetch.py status")
        return 0
    deps = real_deps()
    if cmd == "run" and len(argv) == 1:
        run(deps, state)
    elif cmd == "request" and len(argv) == 3 and argv[1] in ("--movie", "--episode", "--item"):
        target = {"item": argv[2]} if argv[1] == "--item" else {"app": "radarr" if argv[1] == "--movie" else "sonarr", "id": argv[2]}
        try:
            r = request(deps, state, target, {})
        except (Refused, lb.ApiError, ValueError) as e:
            print(f"phone-fetch: {e}", file=sys.stderr)
            return 1
        print(f"{r['title']}: {r['state']} · option {r.get('option')} · {r.get('release') or r.get('note')}")
        print("next: journalctl -u phone-fetch -f   (progress), scripts/phone-fetch.py status")
    else:
        print("usage: scripts/phone-fetch.py request --movie <id> | --episode <id> | --item <jellyfin id> | run | convert | status | forget --item <jellyfin id> | -h", file=sys.stderr)
        return 2
    save_state(STATE, state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
