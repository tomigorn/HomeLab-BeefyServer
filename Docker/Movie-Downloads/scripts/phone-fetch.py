#!/usr/bin/env python3
"""Phone versions, fetch and place (README "Phone versions", owner 2026-11-19, fastpi reports/specs/2026-11-19-phone-versions-design.md).
request: the title's release search through Radarr/Sonarr, the pick of scripts/phone-version.py (usenet only), sent to SABnzbd category `phone` at high priority, at once (never a night download).
run: SABnzbd progress → the clamav-queue worker's virus scan and quality check (quality-results.jsonl) → the file copied onto the HDD branch into the title's folder as `<folder> - Phone <res>p.mkv` (episodes `<series folder> - SxxEyy - Phone <res>p.mkv` in the season folder), so Jellyfin lists it as a second version of the same item → Jellyfin told about the new file → ready, a pop-up to every device that asked.
Requests come from Jellyfin's popular service as JSON files in <box>/requests (or by hand); the state per Jellyfin item is exported to <box>/state.json for it.
Usage: scripts/phone-fetch.py request --movie <radarr id> | --episode <sonarr episode id> | --item <jellyfin item id>
       scripts/phone-fetch.py run      (systemd/phone-fetch.path and .timer)
       scripts/phone-fetch.py status   | -h"""
import importlib.util
import json
import os
import pathlib
import posixpath
import re
import shutil
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
BOX = os.environ.get("PF_BOX", "/srv/appdata/phone-versions")
DL_HOST = os.environ.get("PF_DL_HOST", "/srv/dlpool")
HDD = os.environ.get("PF_HDD", "/srv/.disks/hdd-cold")
RESULTS = os.environ.get("PF_RESULTS", "/var/lib/clamav-scan/textfile/quality-results.jsonl")
SAB_URL = os.environ.get("PF_SAB_URL", "http://127.0.0.1:8081")
SAB_KEY = pathlib.Path(os.environ.get("PF_SAB_KEY", HERE.parent / "secrets" / "hook" / "sabnzbd.apikey"))
JELLYFIN = os.environ.get("PF_JELLYFIN", "http://127.0.0.1:8096")
JELLYFIN_KEY = pathlib.Path(os.environ.get("PF_JELLYFIN_KEY", HERE.parent.parent / "Jellyfin" / "secrets" / "api-key"))
CATEGORY = "phone"
PHONE_DIR = "/data/usenet/complete/phone/"
RETRY_HOURS = 24
CHECK_HOURS = 6
PLACE_HOURS = 2
KEEP_DAYS = 180
VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".webm"}
ACTIVE = ("downloading", "checking", "placing", "ready")
READY_HEADER = "✅ Phone version ready"
READY_TEXT = "{title}: the phone version is ready. Tap Download again (Original)."


class Refused(Exception):
    pass


class Deps:
    def __init__(self, arr, sab, jf, dl_host=DL_HOST, hdd=HDD, results=RESULTS, box=BOX, clock=time.time):
        self.arr, self.sab, self.jf, self.dl_host, self.hdd, self.results, self.box, self.clock = arr, sab, jf, dl_host, hdd, results, box, clock


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
    raw = [x for x in api("GET", "/release", query) or [] if x.get("protocol") == "usenet"]
    flat = [dict(pv.flatten(x), _i=i) for i, x in enumerate(raw)]
    p = pv.pick(flat, title, pv.bad_formats(api("GET", f"/qualityprofile/{profile_id}")))
    if not p["release"]:
        r.update(state="nothing", option=5, note=p["why"])
        return r
    rel = raw[p["release"]["_i"]]
    got = deps.sab({"mode": "addurl", "name": rel["downloadUrl"], "cat": CATEGORY, "priority": "1", "nzbname": f"phone {key} {rel['title']}"[:200]})
    ids = (got or {}).get("nzo_ids") or []
    if not ids:
        r.update(state="failed", note=f"SABnzbd refused the release: {got}")
        return r
    res = (rel.get("quality") or {}).get("quality", {}).get("resolution") or 0
    r.update(state="downloading", option=p["option"], release=rel["title"], size=rel.get("size"), nzo=ids[0], resolution=res)
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


def step(deps, r):
    now = deps.clock()
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
            r.update(state="failed", note=f"download failed: {h[0].get('fail_message') or 'unknown'}", updated=ts(now))
            return
        if h[0].get("status") != "Completed":
            return
        storage = h[0].get("storage") or ""
        if not storage.startswith(PHONE_DIR):
            r.update(state="failed", note=f"finished outside the phone folder: {storage}", updated=ts(now))
            return
        r.update(state="checking", storage=storage, percent=100, updated=ts(now))
    if r["state"] == "checking":
        local = host(deps, r["storage"], deps.dl_host)
        video = biggest_video(local)
        if not video:
            r.update(state="failed", note="no video file in the download", updated=ts(now))
            remove_job(deps, r)
            return
        v = quality_verdict(deps, PHONE_DIR + os.path.relpath(video, host(deps, PHONE_DIR, deps.dl_host)))
        if v is None:
            if since > CHECK_HOURS * 3600:
                r.update(state="failed", note=f"no virus/quality check result after {CHECK_HOURS} h", updated=ts(now))
            return
        if v.get("verdict") != "ok":
            r.update(state="failed", note="quality check: " + ("; ".join(v.get("reasons") or []) or v.get("verdict")), updated=ts(now))
            remove_job(deps, r)
            return
        if not os.path.exists(video):
            r.update(state="failed", note="the download vanished (virus quarantine?)", updated=ts(now))
            return
        dest, jf_path = place(deps, r, video)
        deps.jf("POST", "/Library/Media/Updated", {"Updates": [{"Path": jf_path, "UpdateType": "Created"}]})
        remove_job(deps, r)
        r.update(state="placing", target=dest, jfPath=jf_path, updated=ts(now))
        return
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
            items[r["jfItem"]] = {k: r.get(k) for k in ("state", "percent", "phoneItem", "option", "title", "updated")}
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
        if r["state"] in ("downloading", "checking", "placing"):
            try:
                step(deps, r)
            except (lb.ApiError, OSError, Refused) as e:
                print(f"phone-fetch: {r['key']}: {e}, retried next run", file=sys.stderr)
    now = deps.clock()
    for k, r in list(state["requests"].items()):
        if r["state"] not in ACTIVE and now - (lb.epoch(r.get("updated")) or now) > KEEP_DAYS * 86400:
            del state["requests"][k]
    export(deps, state)


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
    arr = {app: lb.client(os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in ("radarr", "sonarr")}
    return Deps(arr, sab_client(), jf_client())


def main(argv):
    if not argv or "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0 if argv else 2
    cmd = argv[0]
    try:
        state = load_state(STATE)
    except (OSError, ValueError) as e:
        print(f"phone-fetch: {STATE} unreadable: {e}", file=sys.stderr)
        return 1
    if cmd == "status" and len(argv) == 1:
        for r in sorted(state["requests"].values(), key=lambda r: r.get("at") or ""):
            print(f"{r['at']} {r['title']}: {r['state']}{' ' + str(r['percent']) + ' %' if r['state'] == 'downloading' else ''} · option {r.get('option')} · {r.get('release') or ''}"
                  f"{' · ' + r['note'] if r.get('note') else ''}")
        print("next: scripts/phone-fetch.py request --movie <radarr id>")
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
        print("usage: scripts/phone-fetch.py request --movie <id> | --episode <id> | --item <jellyfin id> | run | status | -h", file=sys.stderr)
        return 2
    save_state(STATE, state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
