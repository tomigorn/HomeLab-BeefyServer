#!/usr/bin/env python3
"""Scout requests (README "Scout requests"): Seerr's Manual interaction asks to read one release's NFO or description ("Read description").
Seerr's download-now backend drops <id>.req (JSON) into the request folder; this reads that one release with the NFO scout (usenet, t=getnfo / t=details) or the description scout
(TPB / Knaben→TPB / 1337x through FlareSolverr) code, within their limits plus a daily cap for reads on demand, writes the languages and lines into their state files and <id>.result.json.
Waits while nfo-scout or description-scout run (both write the same state). Started by systemd/scout-requests.path when a request appears.
Usage: scripts/scout-requests.py   (env SR_DIR, SR_NFO_DAY, SR_DESC_DAY; FlareSolverr URL from the unit's resolve step)"""
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ns = _load("nfo_scout", "nfo-scout.py")
ds = ns.ds
DAY = 86400
REQUEST = re.compile(r"^[0-9a-f]{16}\.req$")
LIMIT = "daily limit reached, try tomorrow"
KEEP_RESULTS_DAYS = 7
LANGS = ds.load_languages(HERE / "languages.tsv", os.environ.get("DS_LANGUAGES", "gsw").split(","))


def _read(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _write(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def _recent(stamps, now):
    return [t for t in stamps or [] if isinstance(t, (int, float)) and t > now - DAY]


def read_nfo(req, fetch, state_path, usage, now, log, day_cap, call_delay=1):
    idx = str(req.get("indexer") or "").replace(" (Prowlarr)", "")
    gid = ns.guid_id(str(req.get("guid") or ""))
    if idx not in ns.SOURCES or not gid:
        return {"state": "failed", "why": "this indexer's NFO cannot be read"}
    key = f"{idx}|{gid}"
    st = (_read(state_path).get("releases") or {}).get(key) or {}
    if st.get("verdict") in ("langs", "none") and "lines" in st:
        return {"state": "done", "source": "nfo", "langs": st.get("langs") or []}
    state = _read(state_path)
    if len(_recent(state.get("ondemand"), now)) >= day_cap:
        return {"state": "refused", "why": LIMIT}
    scout = ns.Scout(None, None, None, fetch, state_path, log, usage=usage, now=now, max_fetch=1, call_delay=call_delay)
    if idx in scout.refused or idx in scout.unsupported:
        return {"state": "refused", "why": "the indexer refuses NFO calls today, try tomorrow"}
    if not scout.budget.allowed(idx):
        return {"state": "refused", "why": LIMIT}
    scout.languages("on demand", {"indexer": idx, "title": str(req.get("title") or "")[:300]}, gid, key)
    scout.state["ondemand"] = _recent(scout.state.get("ondemand"), now) + [now]
    scout.save()
    st = scout.releases.get(key) or {}
    if st.get("verdict") in ("langs", "none"):
        return {"state": "done", "source": "nfo", "langs": st.get("langs") or []}
    return {"state": "failed", "why": "the indexer did not answer, try later"}


def read_description(req, fetch, state_path, now, log, day_cap):
    info_url = str(req.get("infoUrl") or "")
    src = ds.description_source({"infoUrl": info_url})
    if req.get("protocol") != "torrent" or not src:
        return {"state": "failed", "why": "this site's description cannot be read"}
    key = "read|" + ds.release_key({"guid": str(req.get("guid") or ""), "infoUrl": info_url})
    state = _read(state_path)
    if isinstance(state.get(key), dict) and "langs" in state[key]:
        return {"state": "done", "source": "description", "langs": state[key]["langs"]}
    if len(_recent(state.get("ondemand"), now)) >= day_cap:
        return {"state": "refused", "why": LIMIT}
    state["ondemand"] = _recent(state.get("ondemand"), now) + [now]
    _write(state_path, state)
    try:
        info = ds.tpb_from_apibay(fetch(f"https://apibay.org/t.php?id={src[1]}")) if src[0] == "tpb" else ds.x1337_from_page(fetch(src[1]))
    except (ds.ApiError, ValueError, KeyError) as e:
        log(f"on demand: '{req.get('title')}': description fetch failed: {e}")
        return {"state": "failed", "why": "the site did not answer, try later"}
    text = ds.unescape(info.get("descr") or "")
    lines = []
    langs = ns.audio_languages(text, lines)
    for lang in LANGS:
        if lang["name"] not in langs and ds.description_matches(text, lang["regex"]):
            langs.append(lang["name"])
            hit = next((l.strip()[:ns.LINE_LEN] for l in text.splitlines() if lang["regex"].search(l)), None)
            if hit and hit not in lines and len(lines) < ns.LINES_MAX:
                lines.append(hit)
    state = _read(state_path)
    state[key] = {"title": str(req.get("title") or "")[:300], "langs": langs, "lines": lines, "source": "description",
                  "at": time.strftime("%Y-%m-%d", time.localtime(now))}
    _write(state_path, state)
    log(f"on demand: '{req.get('title')}' ({req.get('indexer')}): description names {', '.join(langs) or 'no language'}")
    return {"state": "done", "source": "description", "langs": langs}


def process(req, nfo_fetch, desc_fetch, nfo_state, ds_state, usage, now, log, nfo_day=30, desc_day=30):
    if not isinstance(req, dict) or not req.get("indexer") or not req.get("guid"):
        return {"state": "failed", "why": "not a release"}
    if req.get("protocol") == "usenet":
        return read_nfo(req, nfo_fetch, nfo_state, usage, now, log, nfo_day)
    return read_description(req, desc_fetch, ds_state, now, log, desc_day)


def run(req_dir, handle, busy, now, max_wait=1800, pause=30):
    req_dir = pathlib.Path(req_dir)
    for f in req_dir.glob("*.result.json"):
        try:
            if f.stat().st_mtime < now - KEEP_RESULTS_DAYS * DAY:
                f.unlink()
        except OSError:
            pass
    waited = 0
    while busy():
        if waited >= max_wait:
            return 0
        time.sleep(pause)
        waited += pause
    for f in sorted(req_dir.iterdir()):
        if not REQUEST.match(f.name) or not f.is_file() or f.is_symlink():
            continue
        try:
            req = json.loads(f.read_text())
            result = handle(req) if isinstance(req, dict) else {"state": "failed", "why": "not a release"}
        except ValueError:
            result = {"state": "failed", "why": "not a release"}
        result["at"] = int(now)
        out = f.with_name(f.name[:-4] + ".result.json")
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        tmp.chmod(0o664)
        tmp.replace(out)
        f.unlink()
    return 0


def scouts_running():
    r = subprocess.run(["systemctl", "is-active", "nfo-scout.service", "description-scout.service"], capture_output=True, text=True, check=False)
    return any(s in ("active", "activating", "reloading") for s in r.stdout.split())


def main():
    env = os.environ.get
    state_dir = ds.STATE_DIR
    logfile = pathlib.Path(env("SR_LOG", state_dir / "scout-requests.log"))
    logfile.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        msg = re.sub(r"(?i)(apikey|api_key|r)=[^&\s'\"]+", r"\1=***", str(msg).replace("\n", " "))
        print(msg, flush=True)
        with logfile.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    req_dir = pathlib.Path(env("SR_DIR", "/srv/appdata/seerr-scout-requests"))
    if not req_dir.is_dir() or not any(REQUEST.match(f.name) for f in req_dir.iterdir()):
        return 0
    fs_file = env("SR_FLARESOLVERR_FILE", "/run/scout-requests/flaresolverr")
    fs_url = env("DS_FLARESOLVERR") or (pathlib.Path(fs_file).read_text().strip() if pathlib.Path(fs_file).exists() else None)
    nfo_fetch = ns.indexer_fetch(env("NS_PROWLARR_DB", "/srv/appdata/prowlarr/prowlarr.db"))
    desc_fetch = ds.flaresolverr(fs_url) if fs_url else (lambda url: (_ for _ in ()).throw(ds.ApiError("FlareSolverr not resolved")))
    now = time.time()
    usage = ns.read_usage(env("NS_USAGE", "/srv/appdata/indexer-bar/usage.json"))

    def handle(req):
        return process(req, nfo_fetch, desc_fetch, env("NS_STATE", state_dir / "nfo-scout.json"), env("DS_STATE", state_dir / "description-scout.json"),
                       usage, now, log, nfo_day=int(env("SR_NFO_DAY", 30)), desc_day=int(env("SR_DESC_DAY", 30)))
    return run(req_dir, handle, scouts_running, now)


if __name__ == "__main__":
    sys.exit(main())
