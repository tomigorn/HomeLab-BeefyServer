#!/usr/bin/env python3
"""Language not found (README "Swiss German not found alert"): writes a node-exporter textfile with every Radarr movie / Sonarr series that carries a watched audio-<language> tag, is monitored, has no file (series: missing episodes), was searched and is not downloading.
Per title: since (first seen waiting; first run: the date it was added) and notify (since + LM_DAYS, or the time a grab of it failed, whichever is later; a new failure is a new `failed` label).
fastpi pulls the file (Server/4-Security-Monitoring/security_scan.py, group media) and alerts once per title and state (grafana/prometheus/rules/media-language.yml). Run by systemd/language-missing.timer.
Usage: scripts/language-missing.py"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
STATE_DIR = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")) / "movie-downloads"
APPS = {"radarr": ("/movie", "movieId"), "sonarr": ("/series", "seriesId")}
HELP = {
    "media_language_missing_since_timestamp_seconds": "Since when a title tagged audio-<language> waits for a release in that language (monitored, no file, searched, not downloading).",
    "media_language_missing_notify_timestamp_seconds": "From when that title is due for its one notification (since + wait, or the newest failed grab).",
    "media_language_missing_last_run_timestamp_seconds": "Unix time of the last run of language-missing.py on beefy.",
}


class ApiError(Exception):
    pass


def load_languages(tsv, codes):
    out = []
    for line in pathlib.Path(tsv).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        code, name, audio, _ = line.split("\t")
        if code in codes and audio != "-":
            out.append({"code": code, "name": name, "tag": "audio-" + name.lower().replace(" ", "-")})
    return out


def ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None


def paged(api, path, query=None):
    records, page = [], 1
    while True:
        r = api("GET", path, dict(query or {}, page=page, pageSize=1000)) or {}
        records += r.get("records") or []
        if page * 1000 >= (r.get("totalRecords") or 0):
            return records
        page += 1


def candidates(app, api, langs):
    """(key, labels, added, waiting, due) per tagged title; due = waiting, searched and not downloading."""
    path, idf = APPS[app]
    tags = {t["label"]: t["id"] for t in api("GET", "/tag")}
    titles = api("GET", path)
    queued = {r.get(idf) for r in paged(api, "/queue")}
    failed = {}
    for r in paged(api, "/blocklist"):
        failed[r.get(idf)] = failed.get(r.get(idf), 0) + 1
    missing = {}
    if app == "sonarr":
        for e in paged(api, "/wanted/missing", {"monitored": "true"}):
            missing.setdefault(e.get("seriesId"), []).append(e)
    out = []
    for lang in langs:
        tid = tags.get(lang["tag"])
        for t in titles:
            if tid is None or tid not in t.get("tags", []):
                continue
            if app == "radarr":
                waiting = t.get("monitored") and not t.get("hasFile")
                searched = bool(t.get("lastSearchTime"))
            else:
                eps = missing.get(t["id"], [])
                waiting = t.get("monitored") and bool(eps)
                searched = any(e.get("lastSearchTime") for e in eps)
            labels = {"app": app, "title": f"{t['title']} ({t.get('year') or '?'})", "language": lang["name"]}
            out.append((f"{app}:{t['id']}:{lang['code']}", labels, ts(t.get("added")), bool(waiting),
                        bool(waiting) and searched and t["id"] not in queued, failed.get(t["id"], 0)))
    return out


def escape(v):
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render(samples):
    lines, seen = [], set()
    for name, labels, value in samples:
        if name not in seen:
            seen.add(name)
            lines += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} gauge"]
        lab = ",".join(f'{k}="{escape(str(v))}"' for k, v in sorted(labels.items()))
        lines.append(f"{name}{{{lab}}} {int(value)}" if lab else f"{name} {int(value)}")
    return "\n".join(lines) + "\n"


class Watch:
    def __init__(self, apps, langs, state_path, prom_path, days=7):
        self.apps, self.langs, self.days = apps, langs, days
        self.state_path, self.prom_path = pathlib.Path(state_path), pathlib.Path(prom_path)

    def run(self, now=None):
        now = time.time() if now is None else now
        bootstrap = not self.state_path.exists()
        state = {} if bootstrap else json.loads(self.state_path.read_text())
        titles, emitted = state.get("titles", {}), state.get("emitted", {})
        rc = 0
        for app, api in self.apps.items():
            try:
                cands = candidates(app, api, self.langs)
            except ApiError as e:
                print(f"{app}: API error, last lines kept: {e}", file=sys.stderr)
                rc = 1
                continue
            keep, lines = {}, []
            for key, labels, added, waiting, due, failed in cands:
                if not waiting:
                    continue
                st = titles.get(key) or {"since": (added or now) if bootstrap else now, "failed": failed, "failed_at": None}
                if failed > st["failed"]:
                    st["failed_at"] = now
                st["failed"] = failed
                keep[key] = st
                if due:
                    notify = max(st["since"] + self.days * 86400, st["failed_at"] or 0)
                    lab = dict(labels, failed=str(failed))
                    lines += [("media_language_missing_since_timestamp_seconds", lab, st["since"]),
                              ("media_language_missing_notify_timestamp_seconds", lab, notify)]
            titles = {k: v for k, v in titles.items() if not k.startswith(app + ":")} | keep
            emitted[app] = lines
        samples = [tuple(s) for app in sorted(emitted) for s in emitted[app]]
        samples.sort(key=lambda s: s[0])
        samples.append(("media_language_missing_last_run_timestamp_seconds", {}, now))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        for path, text in ((self.state_path, json.dumps({"titles": titles, "emitted": emitted}, indent=1, sort_keys=True)), (self.prom_path, render(samples))):
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text)
            tmp.replace(path)
        return rc


def client(config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(method, path, query=None, body=None):
        url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        req = urllib.request.Request(url, method=method, headers={"X-Api-Key": get("ApiKey")})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ApiError(f"{method} {path}: {e}") from None
    return call


def main():
    apps = {app: client(os.environ.get(f"LM_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")) for app in os.environ.get("LM_APPS", "radarr,sonarr").split(",")}
    langs = load_languages(HERE / "languages.tsv", os.environ.get("LM_LANGUAGES", "gsw").split(","))
    watch = Watch(apps, langs, os.environ.get("LM_STATE", STATE_DIR / "language-missing.json"),
                  os.environ.get("LM_TEXTFILE", STATE_DIR / "media-language.prom"), days=int(os.environ.get("LM_DAYS", 7)))
    return watch.run()


if __name__ == "__main__":
    sys.exit(main())
