#!/usr/bin/env python3
"""Playback safety net (README "Playback check", owner spec 2026-11-18 part 3, fastpi reports/specs/2026-11-18-media-quality-check-design.md).
Detector on Jellyfin's activity log: the same item started 3 times or more within 15 minutes, each stopped within 60 s (any client, any user) → the file check only (Antivirus hook/quality_check.py, the library check's throw-away ffprobe container, idle I/O).
The check never replaces a healthy file: healthy → a note on the Seerr Manual page ("file OK, probably the client"); broken (timestamp jump back, no video, no audio) → the quality block's path (quality-block.py imported(): file removed or the previous one put back, release blocklisted, searched again).
A file the library check already verified (same size and mtime) is not read again; the same unchanged file is not checked twice within a day. The test film is never touched; PC_ACTION=log decides and records only.
State: ~/.local/state/movie-downloads/playback-check.json (entries shown on the Manual page, queue, seen hits); reads Jellyfin only through its API at the container's bridge IP (not counted by the idle watcher), one small request every 5 minutes while beefy is awake.

Usage: scripts/playback-check.py run                 (root: docker; normally playback-check.timer, every 5 min while beefy is awake)
       scripts/playback-check.py replay [--since 30d|all]   (read only: the hits the detector finds in the activity log, nothing checked, nothing written)
       scripts/playback-check.py status
       scripts/playback-check.py -h | --help"""
import calendar
import collections
import contextlib
import fcntl
import importlib.util
import ipaddress
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lc = _load("library_check", HERE / "library-check.py")
qb, qc, lb = lc.qb, lc.qc, lc.lb

STATE = pathlib.Path(os.environ.get("PC_STATE", "/home/buntu/.local/state/movie-downloads/playback-check.json"))
LIBSTATE = pathlib.Path(os.environ.get("PC_LIBRARY_STATE", str(lc.STATE)))
MEDIA_HOST = os.environ.get("PC_MEDIA_HOST", lc.MEDIA_HOST)
JELLYFIN = os.environ.get("PC_JELLYFIN", "")
KEY_FILE = pathlib.Path(os.environ.get("PC_JELLYFIN_KEY", str(HERE.parent.parent / "Jellyfin" / "secrets" / "api-key")))
ACTION = os.environ.get("PC_ACTION", "block")
LOCK = "/run/lock/beefy-media.lock"
CONTAINER = "playback-check"
INSIDE = lc.INSIDE
JELLYFIN_ROOT = "/media/"
MIN_STARTS = 3
WINDOW = 15 * 60
MAX_STOP = 60
LOOKBACK = 40 * 60
RECHECK = 24 * 3600
SEEN_KEEP = 2 * 86400
CHECKED_KEEP = 30 * 86400
ATTEMPTS = 3
LOCK_WAIT = 300
OK_NOTE = "file OK, probably the client"
PAGE = 1000

Deps = collections.namedtuple("Deps", "state libstate media_host activity item check apis imports clock lock action host tmdb")


class LockBusy(Exception):
    pass


# ---------- detector ----------

def when(text):
    """Epoch seconds (with the fraction) of a Jellyfin activity date, None when it is not one."""
    try:
        t = calendar.timegm(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    frac = str(text)[19:].rstrip("Z")
    try:
        return t + (float(frac) if frac.startswith(".") else 0.0)
    except ValueError:
        return t


def plays(entries):
    """One playback per start entry: {item, user, device, name, start, stop (None while no stop entry follows)}, oldest first. Paired per item, user and client."""
    ev = []
    for x in entries or []:
        if not isinstance(x, dict) or x.get("Type") not in ("VideoPlayback", "VideoPlaybackStopped") or not x.get("ItemId") or not x.get("UserId"):
            continue
        t = when(x.get("Date"))
        if t is not None:
            ev.append((t, x))
    ev.sort(key=lambda e: e[0])
    out, open_ = [], {}
    for t, x in ev:
        name = str(x.get("Name") or "")
        key = (x["ItemId"], x["UserId"], name.rsplit(" on ", 1)[-1] if " on " in name else "")
        if x["Type"] == "VideoPlayback":
            p = {"item": key[0], "user": key[1], "device": key[2], "name": name, "start": t, "stop": None}
            open_[key] = p
            out.append(p)
        elif key in open_:
            open_.pop(key)["stop"] = t
    return out


def hits(all_plays):
    """Per item every burst of MIN_STARTS or more playbacks that started within WINDOW and were each stopped within MAX_STOP s: {item, count, start, last, devices, users}."""
    by_item = collections.defaultdict(list)
    for p in all_plays:
        if p["stop"] is not None and p["stop"] - p["start"] <= MAX_STOP:
            by_item[p["item"]].append(p)
    out = []
    for item, shorts in by_item.items():
        shorts.sort(key=lambda p: p["start"])
        i = 0
        while i < len(shorts):
            j = i
            while j + 1 < len(shorts) and shorts[j + 1]["start"] - shorts[i]["start"] <= WINDOW:
                j += 1
            if j - i + 1 >= MIN_STARTS:
                run = shorts[i:j + 1]
                out.append({"item": item, "count": len(run), "start": run[0]["start"], "last": max(p["stop"] for p in run),
                            "devices": list(dict.fromkeys(p["device"] for p in run if p["device"])), "users": len({p["user"] for p in run})})
                i = j + 1
            else:
                i += 1
    return sorted(out, key=lambda h: h["start"])


def describe(h):
    return f"{h['count']} starts within {int((h['last'] - h['start']) // 60) + 1} min, each stopped within {MAX_STOP} s" + (f" ({', '.join(h['devices'])})" if h["devices"] else "")


# ---------- state ----------

def load(path):
    p = pathlib.Path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: not an object")
    return {"entries": list(data.get("entries") or []), "downloads": list(data.get("downloads") or []), "queue": list(data.get("queue") or []),
            "seenHits": dict(data.get("seenHits") or {}), "checked": dict(data.get("checked") or {}), "run": dict(data.get("run") or {})}


def prune(state, now):
    state["entries"] = [x for x in state["entries"] if lb.recent(x, now)]
    state["downloads"] = [x for x in state["downloads"] if lb.recent(x, now)]
    state["seenHits"] = {k: v for k, v in state["seenHits"].items() if isinstance(v, (int, float)) and v >= now - SEEN_KEEP}
    state["checked"] = {k: v for k, v in state["checked"].items() if isinstance(v, dict) and (v.get("at") or 0) >= now - CHECKED_KEEP}


# ---------- file and title ----------

def apps_path(jf_path):
    """Jellyfin sees the library at /media, Radarr/Sonarr (and the check) at /data/media."""
    p = str(jf_path or "")
    if p.startswith(JELLYFIN_ROOT):
        return INSIDE + "/" + p[len(JELLYFIN_ROOT):]
    return p if p.startswith(INSIDE + "/") else None


def host_file(media_host, path):
    return media_host.rstrip("/") + "/" + path[len(INSIDE) + 1:]


def library_result(libstate, path, size, mtime):
    """The library check's verdict for this file when it is unchanged since, else None."""
    try:
        e = (lc.load(libstate)["files"]).get(path)
    except (OSError, ValueError):
        return None
    if not isinstance(e, dict) or e.get("size") != size or e.get("mtime") != mtime or e.get("verdict") not in ("ok", "note", "broken"):
        return None
    return {"verdict": "broken" if e["verdict"] == "broken" else "ok", "reasons": list(e.get("reasons") or []), "notes": list(e.get("notes") or []),
            "duration": e.get("duration"), "cached": e.get("checkedAt") or True}


def find(apis, path, history=False):
    """(app, api, title, import records of this very file) of the library file, None when neither app has it. history=False skips the history lookups."""
    for app, api in apis.items():
        try:
            if app == "radarr":
                for m in api("GET", "/movie") or []:
                    if (m.get("movieFile") or {}).get("path") == path:
                        recs = imports_of(api("GET", "/history/movie", {"movieId": m["id"]}) or [], path) if history else []
                        return app, api, m, recs
            else:
                for s in api("GET", "/series") or []:
                    if not str(s.get("path") or "").rstrip("/") or not path.startswith(str(s["path"]).rstrip("/") + "/"):
                        continue
                    fid = next((f["id"] for f in api("GET", "/episodefile", {"seriesId": s["id"]}) or [] if f.get("path") == path), None)
                    if fid is None:
                        continue
                    recs = []
                    if history:
                        for e in api("GET", "/episode", {"seriesId": s["id"]}) or []:
                            if e.get("episodeFileId") == fid:
                                got = api("GET", "/history", {"episodeId": e["id"], "page": 1, "pageSize": 100, "eventType": 3})
                                recs += imports_of((got.get("records") if isinstance(got, dict) else got) or [], path)
                    return app, api, s, recs
        except (lb.ApiError, KeyError, TypeError) as e:
            print(f"playback-check: {app} not readable ({e}), going on without it", file=sys.stderr)
    return None


def imports_of(records, path):
    got = [r for r in records if r.get("eventType") in ("downloadFolderImported", 3) and (r.get("data") or {}).get("importedPath") == path]
    return sorted(got, key=lambda r: str(r.get("date") or ""), reverse=True)


def base_entry(target, info, path, result, now, verdict):
    x = {"verdict": verdict, "reasons": list(result.get("reasons") or []), "notes": list(result.get("notes") or []), "duration": result.get("duration"), "at": lb.ts(now)}
    name = os.path.basename(path)
    if target:
        app, _, title, _ = target
        e = qb.entry_for(app, title, x, now, name, None, None)
    else:
        e = {"at": lb.ts(now), "app": None, "key": None, "arrId": None, "title": str(info.get("name") or name)[:200], "year": None, "fileName": name, "downloadId": None,
             "verdict": verdict, "reasons": x["reasons"], "notes": x["notes"], "release": None, "file": "kept", "blocked": False, "searched": False, "note": "", "checkedAt": x["at"]}
    e["source"] = "playback"
    return e


# ---------- one queued hit ----------

def step(deps, state, q, now):
    """Advances one queued hit; True when it is finished (dropped or recorded), False to keep it for the next run."""
    info = q.get("info") or deps.item(q["item"])
    path = apps_path((info or {}).get("path"))
    if not path:
        print(f"playback-check: {q['item'][:8]}: not a library file in Jellyfin any more, dropped")
        return True
    q["info"] = {"path": info["path"], "name": info.get("name")}
    host = host_file(deps.media_host, path)
    try:
        st = os.stat(host)
    except OSError:
        print(f"playback-check: {os.path.basename(path)}: file gone, dropped")
        return True
    sig = {"size": st.st_size, "mtime": st.st_mtime_ns}
    done = state["checked"].get(q["item"])
    if "result" not in q and done and done.get("size") == sig["size"] and done.get("mtime") == sig["mtime"] and now - (done.get("at") or 0) < RECHECK:
        print(f"playback-check: {os.path.basename(path)}: unchanged and checked {int((now - done['at']) // 60)} min ago, not again")
        return True
    if "result" not in q:
        res = library_result(deps.libstate, path, sig["size"], sig["mtime"]) or deps.check(path)
        if res.get("verdict") not in ("ok", "broken"):
            q["attempts"] = q.get("attempts", 0) + 1
            why = res.get("why") or "no result"
            if q["attempts"] >= ATTEMPTS:
                print(f"playback-check: {os.path.basename(path)}: check failed {q['attempts']} times ({why}), gave up")
                return True
            print(f"playback-check: {os.path.basename(path)}: check failed ({why}), tried again on the next run")
            return False
        q["result"] = res
    res, h = q["result"], q["hit"]
    context = describe(h)
    try:
        target = find(deps.apis, path, history=res["verdict"] == "broken")
    except (OSError, ValueError) as e:
        print(f"playback-check: {os.path.basename(path)}: apps not readable ({e}), tried again on the next run")
        return False
    if res["verdict"] == "ok":
        notes = [OK_NOTE] + [n for n in res.get("notes") or [] if n]
        e = base_entry(target, info, path, dict(res, notes=notes, reasons=[]), now, "note")
        e["note"] = context
    else:
        try:
            with deps.lock():
                e = block(deps, state, target, info, path, res, now)
        except LockBusy:
            print(f"playback-check: {os.path.basename(path)}: broken, but the media lock is busy, acts on the next run")
            return False
        e["note"] = "; ".join(x for x in (e.get("note"), context) if x)
    state["entries"].append(e)
    state["checked"][q["item"]] = dict(sig, verdict=res["verdict"], at=now)
    what = "; ".join(e["reasons"] + e["notes"])
    print(f"{e.get('app') or '-'}: {e.get('title')}: {e['fileName']}: {e['verdict']}: {what}; file {e['file']}{', blocklisted' if e['blocked'] else ''}"
          f"{', searched again' if e['searched'] else ''}; {e['note']}")
    return True


def block(deps, state, target, info, path, res, now):
    x = {"verdict": "broken", "reasons": res["reasons"], "notes": res.get("notes") or [], "duration": res.get("duration"), "path": path, "at": lb.ts(now)}
    if target is None:
        e = base_entry(None, info, path, res, now, "broken")
        e["note"] = "neither Radarr nor Sonarr knows this file: not blocked"
        return e
    app, api, title, recs = target
    if not recs:
        e = base_entry(target, info, path, res, now, "broken")
        e["note"] = "no import record for this file: not blocked"
        return e
    tmdb = deps.tmdb() if deps.action != "log" else None
    e = qb.imported(app, api, x, recs, deps.imports.get(app) or [], state, now, False, deps.action, None, deps.host, tmdb)
    e["source"] = "playback"
    return e


# ---------- run ----------

def run(deps):
    now = deps.clock()
    if lc.in_night(now):
        print("playback-check: night window 03:50-04:50, nothing done")
        return {}
    state = load(deps.state)
    seen = state["seenHits"]
    for h in hits(plays(deps.activity(now - LOOKBACK))):
        if h["last"] <= seen.get(h["item"], 0):
            continue
        seen[h["item"]] = h["last"]
        state["queue"] = [q for q in state["queue"] if q["item"] != h["item"]] + [{"item": h["item"], "hit": h, "at": lb.ts(now), "attempts": 0}]
        print(f"playback-check: hit {h['item'][:8]}: {describe(h)}")
    queue, keep = state["queue"], []
    for i, q in enumerate(queue):
        if not step(deps, state, q, now):
            keep.append(q)
        state["queue"] = keep + queue[i + 1:]
        prune(state, now)
        lc.save(deps.state, state)
    state["queue"] = keep
    state["run"] = {"updated": lb.ts(now), "queued": len(keep)}
    prune(state, now)
    lc.save(deps.state, state)
    return state


# ---------- Jellyfin, lock, CLI ----------

def bridge_url():
    """Jellyfin at its container's bridge IP, never localhost: the idle watcher counts connections to beefy's own service ports (same as playback-watch)."""
    p = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", "jellyfin"], capture_output=True, text=True, timeout=20)
    ip = next(iter(p.stdout.split()), "") if p.returncode == 0 else ""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        raise OSError(f"no bridge IP for the jellyfin container ({p.stderr.strip()[:100] or ip!r})") from None
    if a.is_loopback or a.is_unspecified:
        raise OSError(f"refusing Jellyfin address {ip}")
    return f"http://{ip}:8096"


def jellyfin(base=None):
    key = KEY_FILE.read_text().strip()
    base = base or JELLYFIN or "http://127.0.0.1:8096"

    def get(path, query):
        url = base + path + "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers={"Authorization": f'MediaBrowser Token="{key}"'})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")

    def activity(since=None):
        out, i = [], 0
        while True:
            q = {"limit": PAGE, "startIndex": i}
            if since is not None:
                q["minDate"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))
            got = get("/System/ActivityLog/Entries", q) or {}
            out += got.get("Items") or []
            i += PAGE
            if i >= (got.get("TotalRecordCount") or 0) or not got.get("Items"):
                return out

    def item(item_id):
        got = (get("/Items", {"Ids": item_id, "Fields": "Path"}) or {}).get("Items") or []
        if not got:
            return None
        x = got[0]
        name = " - ".join(str(p) for p in (x.get("SeriesName"), x.get("Name")) if p)
        return {"path": x.get("Path"), "name": name}
    return activity, item


@contextlib.contextmanager
def media_lock(path=LOCK, wait=LOCK_WAIT, sleep=time.sleep):
    """The shared media lock (the SSD->HDD mover, original-audio, language-block and quality-block hold it too)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(path) from None
                sleep(5)
        yield
    finally:
        os.close(fd)


def parse_since(text):
    if text == "all":
        return None
    units = {"h": 3600, "d": 86400}
    if text[-1:] in units and text[:-1].isdigit():
        return time.time() - int(text[:-1]) * units[text[-1]]
    raise ValueError(f"bad --since {text!r} (use 12h, 30d or all)")


def replay(entries, item):
    """Prints every burst the detector finds in these activity entries; touches nothing. Returns the count."""
    found = hits(plays(entries))
    for h in found:
        info = item(h["item"]) or {}
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(h['start']))}  {info.get('name') or h['item'][:8]}  {describe(h)}")
    return len(found)


def status(path):
    try:
        state = load(path)
    except (OSError, ValueError) as e:
        print(f"playback-check: {path} unreadable: {e}", file=sys.stderr)
        return 1
    r = state["run"]
    print(f"playback check: last run {r.get('updated', 'never')} · {len(state['queue'])} queued · {len(state['entries'])} recorded")
    for e in state["entries"][-10:]:
        print(f"{e['at']}  {e['verdict']}: {e.get('title')}: {e['fileName']}: {'; '.join(e['reasons'] + e['notes'])}; file {e['file']}")
    print("next: journalctl -u playback-check -n 50 · scripts/playback-check.py replay --since 30d")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    if argv == ["status"]:
        return status(STATE)
    if argv[0] == "replay" and (len(argv) == 1 or len(argv) == 3 and argv[1] == "--since"):
        try:
            since = parse_since(argv[2]) if len(argv) == 3 else None
            activity, item = jellyfin()
            entries = activity(since)
        except (ValueError, OSError) as e:
            print(f"playback-check: {e}; next: scripts/playback-check.py replay --since 30d", file=sys.stderr)
            return 1
        n = replay(entries, item)
        print(f"{n} hit(s) in {len(entries)} activity entries; nothing was checked or changed")
        return 0
    if argv != ["run"]:
        print("playback-check: unknown arguments; next: scripts/playback-check.py -h", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("playback-check: run needs root (docker); next: sudo systemctl start playback-check", file=sys.stderr)
        return 1
    probe = lc.Prober(name=CONTAINER)
    started = []

    def check(path):
        try:
            if not started:
                probe.start()
                started.append(1)
            return probe.check(path)
        except lc.ProbeGone as e:
            started.clear()
            return {"verdict": "error", "reasons": [], "notes": [], "why": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"verdict": "error", "reasons": [], "notes": [], "why": type(e).__name__}
    try:
        activity, item = jellyfin(JELLYFIN or bridge_url())
        imports = {}
        apis = lc.real_apis()
        for app in apis:
            config = os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")
            imports[app] = qb.read_imports(pathlib.Path(config).parent / "quality-block.imports", time.time())
        deps = Deps(str(STATE), str(LIBSTATE), MEDIA_HOST, activity, item, check, apis, imports, time.time, media_lock, ACTION, None, lb.tmdb_languages)
        run(deps)
    except urllib.error.URLError as e:
        print(f"playback-check: Jellyfin not reachable ({e.reason}), tried again on the next run")
        return 0
    except (OSError, ValueError) as e:
        print(f"playback-check: {type(e).__name__}: {e}; next: scripts/playback-check.py status", file=sys.stderr)
        return 1
    finally:
        if started:
            probe.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
