#!/usr/bin/env python3
"""Language block (README "Languages", owner 2026-11-16): a file Radarr/Sonarr just imported whose audio has neither German, English nor the title's original language is blocked automatically: its release is marked failed (blocklisted), the new file removed, or after an upgrade the previous file put back from the recycle bin, and the title searched again.
Unknown audio (und tags without a language in the track name), a title marked "download anyway" (tag override), the test film and silent films are never blocked.
Usage: scripts/language-block.py --queue [--dry-run] | --library | -h
--queue: the imports queued by scripts/original-audio-hook.sh (<appdata>/<app>/language-block.queue), run by systemd/language-block-import.path; --dry-run decides only and leaves the queue in place.
--library: decision only over every file, from the arr's own media info (no disk reads, the HDDs stay asleep); never acts."""
import calendar
import importlib.util
import json
import os
import pathlib
import posixpath
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("original_audio", HERE / "original-audio.py")
oa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oa)

STATE = pathlib.Path(os.environ.get("LB_STATE", pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")) / "movie-downloads" / "language-block.json"))
KEEP_DAYS = 180
SILENT_BEFORE = 1930
TEST_FILM = 653
OVERRIDE_TAG = "override"
GERMAN = {"ger", "deu", "gsw"}
ENGLISH = {"eng"}
COMMENTARY = re.compile(r"commentary|kommentar|commentaire", re.I)


class ApiError(Exception):
    pass


def load_tsv(path=HERE / "languages.tsv"):
    """(code, language name, release-name regex) per row of languages.tsv: the name heuristics the release profiles use."""
    rows = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        code, name, rx, _ = line.split("\t")
        rows.append((code, name, re.compile(rx, re.I) if rx != "-" else None))
    return rows


TSV = load_tsv()
CODE_NAME = {}
for _name, _codes in oa.LANGUAGES.items():
    for _c in _codes.split():
        CODE_NAME.setdefault(_c, _name.title())
CODE_NAME["gsw"] = "Swiss German"


def name_codes(name):
    """ISO 639-2 codes of a Radarr/Sonarr language name ("Portuguese (Brazil)" counts as Portuguese), None when unknown."""
    base = re.sub(r"\s*\(.*\)\s*$", "", str(name or "")).strip()
    if base.lower() == "swiss german":
        return {"gsw"}
    return oa.language_codes(base)


def first_code(name):
    codes = name_codes(name)
    return sorted(codes)[0] if codes else None


def track_language(t):
    """The track's language code; for und/missing/unknown tags the BCP 47 tag, then the track name (languages.tsv patterns, language names); None when nothing says."""
    lang = str(t.get("lang") or "").lower()
    if lang in CODE_NAME:
        return lang
    primary = str(t.get("ietf") or "").split("-")[0].lower()
    if primary in CODE_NAME:
        return primary
    tsv_name = next((n for c, n, _ in TSV if c == primary), None)
    if tsv_name:
        return first_code(tsv_name)
    name = str(t.get("name") or "")
    if name:
        for _, n, rx in TSV:
            if rx and rx.search(name):
                return first_code(n)
        for n in oa.LANGUAGES:
            if re.search(rf"\b{re.escape(n)}\b", name, re.I):
                return first_code(n)
    return None


def is_commentary(t):
    return bool(t.get("commentary")) or bool(COMMENTARY.search(str(t.get("name") or "")))


def decide(tracks, original, tags=(), year=None, test_film=False):
    """ok (German, English or the original language in the audio), unknown (a track says no language), block (none of the three), skip (never checked)."""
    tags = set(tags or ())
    if OVERRIDE_TAG in tags:
        return {"verdict": "skip", "reason": "downloaded anyway by a person (tag override)", "languages": []}
    if test_film:
        return {"verdict": "skip", "reason": "permanent test film", "languages": []}
    if year and int(year) < SILENT_BEFORE:
        return {"verdict": "skip", "reason": "silent film", "languages": []}
    speech = [t for t in tracks or [] if not is_commentary(t)]
    if not speech:
        return {"verdict": "unknown", "reason": "only commentary tracks" if tracks else "no audio track", "languages": []}
    orig = name_codes(original)
    wanted = GERMAN | ENGLISH | (orig or set())
    codes = [track_language(t) for t in speech]
    found = list(dict.fromkeys(CODE_NAME[c] for c in codes if c))
    if any(c in wanted for c in codes if c):
        return {"verdict": "ok", "reason": "audio: " + ", ".join(found), "languages": found}
    if None in codes:
        return {"verdict": "unknown", "reason": "an audio track has no language tag (und)" + (f", others: {', '.join(found)}" if found else ""), "languages": found}
    base = re.sub(r"\s*\(.*\)$", "", str(original or ""))
    need = "German or English (original language unknown)" if not orig else "German or English" if orig <= GERMAN | ENGLISH else f"German, English or {base}"
    return {"verdict": "block", "reason": f"no {need} audio, found: {', '.join(found)}", "languages": found}


def parse_mkvmerge(info):
    out = []
    for tr in (info or {}).get("tracks") or []:
        if tr.get("type") != "audio":
            continue
        p = tr.get("properties") or {}
        out.append({"lang": p.get("language") or "und", "ietf": p.get("language_ietf") or "", "name": p.get("track_name") or "", "commentary": bool(p.get("flag_commentary"))})
    return out


def parse_arr_languages(s):
    """Radarr/Sonarr mediaInfo.audioLanguages ("eng/ger", the arr's own ffprobe at import) as tracks."""
    if not str(s or "").strip():
        return []
    return [{"lang": x.strip() or "und", "ietf": "", "name": "", "commentary": False} for x in str(s).split("/")]


def parse_queue_line(line):
    """<id> <path> <file id> <download id> <isupgrade> <deleted paths|…> <recycle bin paths|…> (TAB-separated, written by original-audio-hook.sh)."""
    f = line.rstrip("\r\n").split("\t")
    if len(f) < 2 or not f[0].strip() or not f[1].strip():
        return None
    f += [""] * (7 - len(f))
    return {"id": f[0].strip(), "path": f[1].strip(), "fileId": f[2].strip(), "downloadId": f[3].strip(), "upgrade": f[4].strip().lower() == "true",
            "deleted": [p for p in f[5].split("|") if p], "recycled": f[6].split("|") if f[6] else []}


def recycle_path(recycle_bin, roots, deleted):
    """Where the arr's recycle bin keeps a deleted library file: the path below its root folder, under the bin."""
    if not recycle_bin:
        return None
    for root in roots:
        r = root.rstrip("/") + "/"
        if deleted.startswith(r):
            rel = posixpath.normpath(deleted[len(r):])
            if rel.startswith(".."):
                return None
            return recycle_bin.rstrip("/") + "/" + rel
    return None


def ts(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def epoch(s):
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def load_state(path):
    p = pathlib.Path(path)
    if not p.exists():
        return {"entries": [], "downloads": []}
    data = json.loads(p.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: not an object")
    return {"entries": list(data.get("entries") or []), "downloads": list(data.get("downloads") or [])}


def save_state(path, state):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


def recent(x, now):
    at = epoch(x.get("at"))
    return at is None or at >= now - KEEP_DAYS * 86400


def prune(state, now):
    for k in ("entries", "downloads"):
        state[k] = [x for x in state[k] if recent(x, now)]


def find_grab(api, download_id):
    got = api("GET", "/history", {"downloadId": download_id, "page": 1, "pageSize": 50})
    recs = got.get("records") if isinstance(got, dict) else got
    return next((h for h in recs or [] if h.get("eventType") == "grabbed" and str(h.get("downloadId") or "").lower() == download_id.lower()), None)


def sidecars(video):
    """Subtitles and other files the arr kept next to the video under the same name (<stem>.en.srt)."""
    d, stem = os.path.dirname(video), pathlib.Path(video).stem
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return [os.path.join(d, n) for n in sorted(names) if n.startswith(stem + ".") and os.path.join(d, n) != video]


def undo(moved):
    for src, dst in reversed(moved):
        try:
            os.rename(dst, src)
        except OSError as e:
            print(f"language-block: could not move {dst} back to {src}: {e}", file=sys.stderr)


def restore_previous(app, api, item, title, probe, host, file_api):
    """Puts the previous file(s) back from the recycle bin, then removes the new one; (moved pairs, None) or (None, why not). Undone when the new file cannot be removed."""
    bins = {}

    def recycle(dst):
        if "bin" not in bins:
            bins["bin"] = (api("GET", "/config/mediamanagement") or {}).get("recycleBin")
            bins["roots"] = [r.get("path") for r in api("GET", "/rootfolder") or [] if r.get("path")]
        return recycle_path(bins["bin"], bins["roots"], dst)
    pairs = []
    try:
        for i, dst in enumerate(item["deleted"]):
            src = item["recycled"][i] if i < len(item["recycled"]) and item["recycled"][i] else recycle(dst)
            if not src:
                return None, "previous file not in the recycle bin"
            hs, hd = host(src), host(dst)
            if not os.path.isfile(hs):
                return None, "previous file gone from the recycle bin"
            if os.path.exists(hd):
                return None, "previous file's place is taken"
            try:
                old = decide(probe(hs), (title.get("originalLanguage") or {}).get("name"), year=title.get("year"))
            except (oa.ToolError, OSError, ValueError):
                old = {"verdict": "unknown"}
            if old["verdict"] == "block":
                return None, "previous file lacks that audio too"
            pairs.append((hs, hd))
            pairs += [(s, os.path.join(os.path.dirname(hd), os.path.basename(s))) for s in sidecars(hs)
                      if not os.path.exists(os.path.join(os.path.dirname(hd), os.path.basename(s)))]
    except (ApiError, ValueError) as e:
        return None, f"previous file not found: {e}"
    moved = []
    try:
        for s, d in pairs:
            os.rename(s, d)
            moved.append((s, d))
    except OSError as e:
        undo(moved)
        return None, f"previous file not put back: {e}"
    try:
        api("DELETE", file_api + item["fileId"])
    except ApiError as e:
        undo(moved)
        return None, f"new file not removed: {e}"
    return moved, None


def act(app, api, item, title, probe, state, host=None, dry=False, now=None):
    """Decide one imported file and, for a block, act on it. Returns the log entry (also kept in state for a block, unless dry)."""
    host = host or oa.host_path
    now = time.time() if now is None else now
    kind = "movie" if app == "radarr" else "tv"
    original = (title.get("originalLanguage") or {}).get("name")
    try:
        tracks = probe(host(item["path"]))
        d = decide(tracks, original, title.get("tags"), title.get("year"), test_film=app == "radarr" and title.get("tmdbId") == TEST_FILM)
    except (oa.ToolError, OSError, ValueError) as e:
        d = {"verdict": "unknown", "reason": f"tracks not readable: {e}", "languages": []}
    key = f"{kind}/{title['tmdbId']}" if title.get("tmdbId") else f"{app}:{title.get('id')}"
    entry = {"at": ts(now), "app": app, "key": key, "arrId": title.get("id"), "title": title.get("title"), "year": title.get("year"), "original": original,
             "fileName": os.path.basename(item["path"]), "downloadId": item.get("downloadId") or None, "verdict": d["verdict"], "reason": d["reason"],
             "languages": d["languages"], "release": None, "file": "kept", "blocked": False, "searched": False, "note": ""}
    if d["verdict"] != "block":
        return entry
    notes = []

    def finish():
        entry["note"] = "; ".join(notes)
        if not dry:
            prune(state, now)
            state["entries"].append(entry)
        return entry
    if not item.get("downloadId"):
        notes.append("imported by hand (no download id): not blocked")
        return finish()
    try:
        grab = find_grab(api, item["downloadId"])
    except ApiError as e:
        notes.append(f"history not readable: {e}")
        return finish()
    if grab is None:
        notes.append("no grab event for this download: not blocked")
        return finish()
    entry["release"] = str(grab.get("sourceTitle") or "")[:200] or None
    if dry:
        entry["file"] = "dry run"
        return entry
    file_api = "/moviefile/" if app == "radarr" else "/episodefile/"
    episodes = []
    if app == "sonarr":
        try:
            episodes = [e["id"] for e in api("GET", "/episode", {"episodeFileId": item["fileId"]}) or [] if isinstance(e.get("id"), int)]
        except ApiError as e:
            notes.append(f"episodes not readable: {e}")
    if not item.get("fileId"):
        notes.append("no file id from the app: file kept")
    elif not item["upgrade"] and not item["deleted"]:
        try:
            api("DELETE", file_api + item["fileId"])
            entry["file"] = "removed"
        except ApiError as e:
            notes.append(f"file not removed: {e}")
    elif item["deleted"]:
        moved, why = restore_previous(app, api, item, title, probe, host, file_api)
        if moved is None:
            notes.append(why + ": new file kept")
        else:
            entry["file"] = "previous restored"
            try:
                api("POST", "/command", body={"name": "RescanMovie", "movieId": title["id"]} if app == "radarr" else {"name": "RescanSeries", "seriesId": title["id"]})
            except ApiError as e:
                notes.append(f"rescan failed, the next scan finds the file: {e}")
    else:
        notes.append("upgrade without a previous file to put back: new file kept")
    done = {(x.get("app"), str(x.get("downloadId")).lower()) for x in state["downloads"]}
    if (app, item["downloadId"].lower()) in done:
        entry["blocked"] = True
    else:
        try:
            api("POST", f"/history/failed/{grab['id']}")
            entry["blocked"] = True
            state["downloads"].append({"app": app, "downloadId": item["downloadId"], "at": ts(now)})
        except ApiError as e:
            notes.append(f"not blocklisted: {e}")
    try:
        api("POST", "/command", body={"name": "MoviesSearch", "movieIds": [title["id"]]} if app == "radarr" else {"name": "EpisodeSearch", "episodeIds": episodes})
        entry["searched"] = app == "radarr" or bool(episodes)
    except ApiError as e:
        notes.append(f"search failed: {e}")
    return finish()


def client(config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(method, path, query=None, body=None):
        url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"X-Api-Key": get("ApiKey")}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ApiError(f"{method} {path}: {e}") from None
    return call


def mkvmerge_tracks(path):
    return parse_mkvmerge(oa.mkvmerge_info(path))


def with_labels(api, title):
    labels = {t["id"]: t["label"] for t in api("GET", "/tag") or []}
    return dict(title, tags=[labels.get(t, t) for t in title.get("tags") or []])


def library_rows(app, api):
    """Decision per library file from the arr's media info (no disk reads)."""
    labels = {t.get("id"): t.get("label") for t in api("GET", "/tag") or [] if isinstance(t, dict)}

    def row(t, f):
        tags = [labels.get(x, x) for x in t.get("tags") or []]
        d = decide(parse_arr_languages((f.get("mediaInfo") or {}).get("audioLanguages")), (t.get("originalLanguage") or {}).get("name"), tags, t.get("year"),
                   test_film=app == "radarr" and t.get("tmdbId") == TEST_FILM)
        return {"app": app, "title": t.get("title"), "year": t.get("year"), "file": os.path.basename(f.get("path") or ""), **d}
    if app == "radarr":
        for m in api("GET", "/movie") or []:
            if m.get("movieFile"):
                yield row(m, m["movieFile"])
    else:
        for s in api("GET", "/series") or []:
            for f in api("GET", "/episodefile", {"seriesId": s["id"]}) or []:
                yield row(s, f)


def claim(path, dry):
    """Queue items in order (renamed first, so the app can keep appending; left in place on a dry run)."""
    p, work = pathlib.Path(path), pathlib.Path(str(path) + ".work")
    if dry:
        text = (work.read_text() if work.exists() else "") + (p.read_text() if p.exists() else "")
    else:
        if p.exists():
            with open(work, "a") as w:
                w.write(p.read_text())
            p.unlink()
        text = work.read_text() if work.exists() else ""
    items, seen = [], set()
    for line in text.splitlines():
        it = parse_queue_line(line)
        if it and (it["id"], it["path"], it["fileId"]) not in seen:
            seen.add((it["id"], it["path"], it["fileId"]))
            items.append(it)
    return items, work


def run_queue(dry):
    rc = 0
    try:
        state = load_state(STATE)
    except (OSError, ValueError) as e:
        print(f"language-block: {STATE} unreadable, nothing done: {e}", file=sys.stderr)
        return 1
    for app in ("radarr", "sonarr"):
        config = os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")
        try:
            api = client(config)
            items, work = claim(pathlib.Path(config).parent / "language-block.queue", dry)
        except OSError as e:
            print(f"{app}: {e}", file=sys.stderr)
            rc = 1
            continue
        for it in items:
            try:
                title = with_labels(api, api("GET", ("/movie/" if app == "radarr" else "/series/") + it["id"]))
            except ApiError as e:
                print(f"{app}: queued {it['path']}: {e}", file=sys.stderr)
                rc = 1
                continue
            e = act(app, api, it, title, mkvmerge_tracks, state, dry=dry)
            extra = f"; file {e['file']}" if e["verdict"] == "block" else ""
            note = f"; {e['note']}" if e["note"] else ""
            print(f"{app}: {e['title']} ({e['year']}): {e['fileName']}: {e['verdict']}: {e['reason']}{extra}{note}{' (dry run)' if dry else ''}")
        if not dry:
            save_state(STATE, state)
            work.unlink(missing_ok=True)
    return rc


def run_library():
    counts, rc = {}, 0
    for app in ("radarr", "sonarr"):
        try:
            api = client(os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml"))
            for r in library_rows(app, api):
                counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
                if r["verdict"] in ("block", "unknown"):
                    print(f"{app}: {r['title']} ({r['year']}): {r['file']}: {r['verdict']}: {r['reason']}")
        except (ApiError, OSError) as e:
            print(f"{app}: API error: {e}", file=sys.stderr)
            rc = 1
    print("library (dry run, arr media info): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("next: journalctl -u language-block-import -n 20   (what real imports did)")
    return rc


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    if "--library" in argv:
        return run_library()
    if "--queue" in argv:
        return run_queue("--dry-run" in argv)
    print("usage: scripts/language-block.py --queue [--dry-run] | --library | -h", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
