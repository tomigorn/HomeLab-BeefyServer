#!/usr/bin/env python3
"""Request, download and indexer figures for fastpi's Grafana "Homelab — morning view" (fastpi Docker/grafana/README.md).
Reads the files the night run and the indexer bar already write (request-state.json, night-waiting.json, usage.json, night-downloads.json, retention.json) plus Seerr /request/count and the Radarr/Sonarr imports of the last night window (22:00-06:00); counts and sizes only, no titles or users.
Library sizes per kind, quality (4K HDR/SDR, 1080p, other) and tier come from the Radarr/Sonarr file records (no disk reads), refreshed at most hourly (cache library.prom in the state dir).
Writes ~/.local/state/movie-downloads/media-metrics.prom; fastpi's beefy-idle-pull.sh copies it every 5 min (kept while beefy sleeps). A source that fails is left out and counted in beefy_media_metrics_errors.
Run by systemd/media-metrics.timer every 5 min while beefy is awake (never wakes it).
Usage: scripts/media-metrics.py"""
import datetime
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Zurich")
STATE_DIR = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")) / "movie-downloads"
BAR = pathlib.Path(os.environ.get("MM_BAR", "/srv/appdata/indexer-bar"))
OUT = pathlib.Path(os.environ.get("MM_OUT", STATE_DIR / "media-metrics.prom"))
SEERR_STATES = ("total", "pending", "approved", "declined", "processing", "available", "completed")
BADGES = {"can_wait": "canWait", "unreleased": "unreleased", "no_release": "noRelease", "searching": "searching", "pending": "pending", "imported": "imported"}
QUEUE_STATES = ("downloading", "paused", "importing", "problem")
QUALITIES = ("4k_hdr", "4k_sdr", "1080p", "other")
LIBRARY_EVERY = 3600


def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def night_window(now):
    start = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if now < start:
        start = (start - datetime.timedelta(days=1)).replace(hour=22)
    return start, start + datetime.timedelta(hours=8)


def request_state_lines(rs):
    out = [f'beefy_request_badges{{state="{k}"}} {len(rs.get(v) or [])}' for k, v in BADGES.items()]
    states = [q.get("state") for q in (rs.get("queue") or {}).values() if isinstance(q, dict)]
    out += [f'beefy_downloads_queue{{state="{s}"}} {states.count(s)}' for s in QUEUE_STATES]
    return "\n".join(out) + "\n"


def seerr_lines(count):
    return "\n".join(f'beefy_requests{{state="{s}"}} {int(count[s])}' for s in SEERR_STATES if s in count) + "\n"


def _when(rec):
    return datetime.datetime.fromisoformat(rec["date"].replace("Z", "+00:00"))


def night_lines(nd, waiting, history, start, end):
    out = []
    for app in sorted(history):
        recs = [r for r in history[app] if start <= _when(r) < end]
        out.append(f'beefy_downloads_night_imported{{app="{app}"}} {len(recs)}')
        out.append(f'beefy_downloads_night_imported_bytes{{app="{app}"}} {sum(int((r.get("data") or {}).get("size") or 0) for r in recs)}')
    if nd is not None:
        out.append(f"beefy_downloads_night_pending {int(nd.get('pending') or 0)}")
    if waiting is not None:
        out.append(f"beefy_downloads_night_waiting {len(waiting.get('waiting') or [])}")
    out.append(f"beefy_downloads_night_window_start_timestamp_seconds {int(start.timestamp())}")
    return "\n".join(out) + "\n"


def indexer_lines(usage, host="beefy"):
    h = f'host="{host}"'
    out = []
    for ix in usage.get("indexers", []):
        for kind in ("searches", "downloads"):
            q = ix.get(kind)
            if not isinstance(q, dict) or q.get("used") is None:
                continue
            lab = f'{h},indexer="{esc(ix["name"])}",kind="{kind}"'
            out.append(f"indexer_used{{{lab}}} {q['used']}")
            if q.get("limit") is not None:
                out.append(f"indexer_limit{{{lab}}} {q['limit']}")
    out.append(f"indexer_bar_ok{{{h}}} {1 if usage.get('ok') else 0}")
    if usage.get("generated"):
        ts = datetime.datetime.fromisoformat(usage["generated"].replace("Z", "+00:00")).timestamp()
        out.append(f"indexer_bar_generated_timestamp_seconds{{{h}}} {int(ts)}")
    return "\n".join(out) + "\n"


def retention_lines(doc):
    sm = doc.get("summary") if isinstance(doc, dict) else None
    if not isinstance(sm, dict):
        return ""
    out = [f'beefy_retention_titles{{state="keep"}} {sm.get("keep", 0)}\n', f'beefy_retention_titles{{state="temporary"}} {sm.get("temporary", 0)}\n',
           f'beefy_retention_leaving_7d {sm.get("leaving7d", 0)}\n', f'beefy_retention_delete_enabled {int(bool(doc.get("deleteEnabled")))}\n']
    if "keepBytes" in sm:
        out += [f'beefy_retention_bytes{{state="keep"}} {sm["keepBytes"]}\n', f'beefy_retention_bytes{{state="temporary"}} {sm.get("temporaryBytes", 0)}\n',
                f'beefy_retention_leaving_7d_bytes {sm.get("leaving7dBytes", 0)}\n']
    return "".join(out)


def quality_class(f):
    q = ((f.get("quality") or {}).get("quality") or {}).get("resolution") or 0
    mi = f.get("mediaInfo") or {}
    if q >= 2160:
        return "4k_hdr" if mi.get("videoDynamicRange") == "HDR" or mi.get("videoDynamicRangeType") else "4k_sdr"
    return "1080p" if q == 1080 else "other"


def library_lines(movies, series, retention):
    """Bytes per kind/quality/tier, titles per kind and main quality, episode files per quality. Tier share = hddBytes/size of the title in retention.json (no disk reads); titles retention does not know are tier "unknown"."""
    by_tvdb = {e.get("tvdb"): e for k, e in retention.items() if k.startswith("tv/") and e.get("tvdb")}
    size = {(k, q, tr): 0 for k in ("movie", "series") for q in QUALITIES for tr in ("ssd", "hdd")}
    titles = {(k, q): 0 for k in ("movie", "series") for q in QUALITIES}
    files = {q: 0 for q in QUALITIES}

    def add(kind, entry, f):
        q, b = quality_class(f), int(f.get("size") or 0)
        if not entry or not entry.get("size"):
            size[(kind, q, "unknown")] = size.get((kind, q, "unknown"), 0) + b
            return q, b
        hdd = round(b * min(1.0, (entry.get("hddBytes") or 0) / entry["size"]))
        size[(kind, q, "hdd")] += hdd
        size[(kind, q, "ssd")] += b - hdd
        return q, b

    for m in movies:
        if m.get("movieFile"):
            q, _ = add("movie", retention.get(f"movie/{m.get('tmdbId')}"), m["movieFile"])
            titles[("movie", q)] += 1
    for s, eps in series:
        per = {}
        for f in eps:
            q, b = add("series", by_tvdb.get(s.get("tvdbId")), f)
            per[q] = per.get(q, 0) + b
            files[q] += 1
        if per:
            titles[("series", max(per, key=per.get))] += 1
    out = [f'beefy_library_bytes{{kind="{k}",quality="{q}",tier="{tr}"}} {v}' for (k, q, tr), v in size.items()]
    out += [f'beefy_library_titles{{kind="{k}",quality="{q}"}} {v}' for (k, q), v in titles.items()]
    out += [f'beefy_library_files{{kind="series",quality="{q}"}} {v}' for q, v in files.items()]
    return "\n".join(out) + "\n"


def cached(path, build, now=None):
    """(text, ok): the cached text while younger than LIBRARY_EVERY, else a fresh build; a failed build keeps the old text."""
    now = time.time() if now is None else now
    try:
        if now - path.stat().st_mtime < LIBRARY_EVERY:
            return path.read_text(), True
    except OSError:
        pass
    try:
        text = build()
    except Exception as e:
        print(f"media-metrics: library: {e}", file=sys.stderr)
        try:
            return path.read_text(), False
        except OSError:
            return "", False
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return text, True


def _json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as e:
        print(f"media-metrics: {path}: {e}", file=sys.stderr)
        return None


def main():
    spec = importlib.util.spec_from_file_location("nd", HERE / "night-downloads.py")
    nd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nd)
    now = datetime.datetime.now(TZ)
    start, end = night_window(now)
    parts, errors = [], 0
    rs = _json(BAR / "request-state.json")
    usage = _json(BAR / "usage.json")
    for data, fn in ((rs, request_state_lines), (usage, indexer_lines)):
        if data is None:
            errors += 1
        else:
            parts.append(fn(data))
    try:
        seerr = nd.seerr_client(os.environ.get("ND_SEERR_URL", "http://127.0.0.1:5055"), HERE.parent.parent / "Seerr" / "secrets" / "api-key")
        parts.append(seerr_lines(seerr("/request/count")))
    except (nd.ApiError, OSError, KeyError, ValueError) as e:
        print(f"media-metrics: seerr: {e}", file=sys.stderr)
        errors += 1
    history = {}
    since = start.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for app in nd.APPS:
        try:
            history[app] = nd.arr_client(app, f"/srv/appdata/{app}/config.xml")(f"/history/since?date={since}&eventType=3") or []
        except (nd.ApiError, OSError) as e:
            print(f"media-metrics: {app}: {e}", file=sys.stderr)
            errors += 1
    retention = _json(BAR / "retention.json")
    if retention is None:
        errors += 1
    else:
        parts.append(retention_lines(retention))
    def build_library():
        radarr = nd.arr_client("radarr", "/srv/appdata/radarr/config.xml")
        sonarr = nd.arr_client("sonarr", "/srv/appdata/sonarr/config.xml")
        series = [(s, sonarr(f"/episodefile?seriesId={s['id']}") or []) for s in sonarr("/series") or []]
        text = library_lines(radarr("/movie") or [], series, (retention or {}).get("titles") or {})
        return text + f"beefy_library_updated_timestamp_seconds {int(time.time())}\n"
    lib, ok = cached(STATE_DIR / "library.prom", build_library)
    errors += not ok
    parts.append(lib)
    parts.append(night_lines(_json(STATE_DIR / "night-downloads.json"), _json(BAR / "night-waiting.json"), history, start, end))
    parts.append(f"beefy_media_metrics_errors {errors}\nbeefy_media_metrics_updated_timestamp_seconds {int(now.timestamp())}\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUT.parent, prefix=".media-metrics-")
    with os.fdopen(fd, "w") as f:
        f.write("".join(parts))
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
