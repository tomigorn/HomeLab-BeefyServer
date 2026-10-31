#!/usr/bin/env python3
"""Translated subtitle fallback (README): when German or English has no human subtitle after the wait, translate one from the best human subtitle through Lingarr (LibreTranslate), timestamps unchanged; a human subtitle that turns up later replaces it.
Usage: scripts/subtitle-translate.py [--dry-run] [--max N] [--only <path substring>]
Env: ST_WAIT_HOURS (48), ST_STOP_AT (unset; the unit sets 05:50: no new title after it), ST_STATE (/srv/appdata/subtitle-translate), ST_DATA_HOST (/srv/video)."""
import argparse
import dataclasses
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ("de", "en")
LABEL = {"de": "German (translated)", "en": "English (translated)"}
MARK = re.compile(r"\.(German|English) \(translated\)\.(de|en)(\.hi)?\.srt$")
OTHER = {"de": "en", "en": "de"}
# LibreTranslate codes that differ from Bazarr's code2.
LT_CODE = {"zh": "zh-Hans", "zt": "zh-Hant", "pb": "pt"}
NAMES = {"english": "en", "german": "de", "french": "fr", "spanish": "es", "italian": "it", "portuguese": "pt", "russian": "ru",
         "swedish": "sv", "danish": "da", "norwegian": "nb", "dutch": "nl", "flemish": "nl", "polish": "pl", "finnish": "fi",
         "japanese": "ja", "korean": "ko", "chinese": "zh", "mandarin": "zh", "cantonese": "zt", "persian": "fa", "hindi": "hi",
         "tamil": "ta", "turkish": "tr", "czech": "cs", "hungarian": "hu", "greek": "el", "arabic": "ar", "hebrew": "he",
         "ukrainian": "uk"}
TIME = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}")
LETTER = re.compile(r"[^\W\d_]")
TEXT_CODECS = ("SubRip/SRT", "SubStationAlpha", "AdvancedSubStationAlpha")


@dataclasses.dataclass
class Config:
    data_host: str = "/srv/video"
    state_dir: str = "/srv/appdata/subtitle-translate"
    wait_hours: float = 48
    supported: set = dataclasses.field(default_factory=set)
    stop_at: dt.time | None = dt.time(5, 50)
    max_titles: int = 0
    dry_run: bool = False
    chunk: int = 150
    only: str | None = None


# ---------- decisions ----------

def is_translated(path):
    return bool(path) and MARK.search(path) is not None


def human(subs):
    """Bazarr's subtitles minus our own translations (Bazarr does not index them, this is the belt to those braces)."""
    return [s for s in subs if s.get("code2") and not is_translated(s.get("path"))]


def present(subs, lang):
    return any(s["code2"] == lang and not s.get("forced") for s in subs)


def rungs(missing, original):
    """Source order per the owner's rule; ('*', hi) = any other language."""
    orig = original if original not in (None, *TARGETS) else None
    if len(missing) == 1:
        o = OTHER[missing[0]]
        order = [(o, True), (o, False)]
    else:
        order = []
    if orig:
        order += [(orig, True), (orig, False)]
    return order + [("*", True), ("*", False)]


def candidates(subs, missing, original, supported):
    named = {lang for lang, _ in rungs(missing, original) if lang != "*"}
    out = []
    for lang, hi in rungs(missing, original):
        hits = [s for s in subs if not s.get("forced") and bool(s.get("hi")) == hi and s["code2"] in supported
                and s["code2"] not in missing and (s["code2"] == lang if lang != "*" else s["code2"] not in named)]
        # External first: already synced and checked by Bazarr, and no full read of the video.
        out += sorted(hits, key=lambda s: (s.get("path") is None, s["code2"]))
    return out


def plan(subs, original, supported):
    """{target: [source candidates, best first]} for each target with no human subtitle."""
    missing = [t for t in TARGETS if not present(subs, t)]
    if not missing:
        return {}
    cands = candidates(subs, missing, original, supported)
    return {t: cands for t in missing} if cands else {}


def due(added, now, wait_hours):
    if not added:
        return False
    t = dt.datetime.fromisoformat(added.replace("Z", "+00:00"))
    return now - t >= dt.timedelta(hours=wait_hours)


def out_path(video, lang, hi):
    base = os.path.splitext(video)[0]
    return f"{base}.{LABEL[lang]}.{lang}{'.hi' if hi else ''}.srt"


def describe(s):
    return f"{'external' if s.get('path') else 'embedded'} {s['code2']}{' hi' if s.get('hi') else ''}"


# ---------- subtitle text ----------

def parse_srt(text):
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        k = next((i for i, x in enumerate(lines) if TIME.match(x)), None)
        if k is None:
            continue
        cues.append({"index": lines[k - 1].strip() if k else str(len(cues) + 1), "time": lines[k].strip(), "text": "\n".join(lines[k + 1:]).strip("\n")})
    return cues


def render_srt(cues, translated):
    return "".join(f"{c['index']}\n{c['time']}\n{translated.get(i) or c['text']}\n\n" for i, c in enumerate(cues))


def translatable(cues):
    """(position, text) of the cues with words; sound-only cues (♪, -) stay as they are."""
    return [(i, c["text"]) for i, c in enumerate(cues) if LETTER.search(c["text"])]


def _ass_time(t):
    h, m, s = t.strip().split(":")
    sec, cs = (s.split(".") + ["0"])[:2]
    return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},{int(cs.ljust(2, '0')[:2]) * 10:03d}"


def parse_ass(text):
    fields, cues = None, []
    for line in text.lstrip("﻿").splitlines():
        if line.startswith("Format:") and fields is None:
            fields = [f.strip().lower() for f in line[7:].split(",")]
        elif line.startswith("Dialogue:") and fields:
            parts = line[9:].split(",", len(fields) - 1)
            row = dict(zip(fields, parts))
            body = re.sub(r"\{[^}]*\}", "", row.get("text", "")).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ").strip()
            if body:
                cues.append({"index": str(len(cues) + 1), "time": f"{_ass_time(row['start'])} --> {_ass_time(row['end'])}", "text": body})
    return cues


def read_cues(path):
    raw = pathlib.Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return parse_ass(text) if path.lower().endswith((".ass", ".ssa")) or "[Events]" in text[:5000] else parse_srt(text)


# ---------- run ----------

def load_state(cfg):
    p = pathlib.Path(cfg.state_dir) / "state.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"translations": {}, "failed": {}, "no_text": {}}


def save_state(cfg, state):
    p = pathlib.Path(cfg.state_dir) / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, p)


def host(cfg, container_path):
    return cfg.data_host + container_path[len("/data"):] if container_path.startswith("/data/") else container_path


def past_deadline(cfg, now):
    return cfg.stop_at is not None and cfg.stop_at <= now.time() < dt.time(22, 0)


def no_text_key(video, s):
    try:
        size = os.path.getsize(video)
    except OSError:
        size = 0
    return f"{video}|{s.get('embedded_track_id')}|{size}"


def source_cues(cfg, apis, video, s, work, state):
    if s.get("path"):
        return read_cues(host(cfg, s["path"]))
    key = no_text_key(video, s)
    if key in state.setdefault("no_text", {}):
        return None
    dest = os.path.join(work, f"track{s['embedded_track_id']}.sub")
    if not apis.extract(video, s, dest):
        # Bitmap or missing track: remembered per file size, so the video is not read again every night.
        state["no_text"][key] = True
        return None
    return read_cues(dest)


def is_caps(text):
    letters = LETTER.findall(text)
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def sentence_case(text):
    return "\n".join(re.sub(r"[^\W\d_]", lambda m: m.group(0).upper(), line.lower(), count=1) for line in text.split("\n"))


def upper_text(text):
    return "".join(part if part[:1] in "<{" else part.upper() for part in re.split(r"(<[^>]*>|\{[^}]*\})", text))


def translate_cues(cfg, apis, cues, src, tgt, title):
    # The models mangle ALL-CAPS sound cues ("DRUMROLLPLAYING"): sent in sentence case, returned in caps.
    todo = [(p, sentence_case(t) if is_caps(t) else t) for p, t in translatable(cues)]
    caps = {p for p, t in translatable(cues) if is_caps(t)}
    done = {}
    for k in range(0, len(todo), cfg.chunk):
        part = todo[k:k + cfg.chunk]
        got = apis.translate(LT_CODE.get(src, src), tgt, part, f"{title} [{k // cfg.chunk + 1}]")
        done.update({p: (upper_text(t) if p in caps else t) for p, t in got.items() if t and t.strip()})
    if len(done) < len(todo) * 0.95:
        raise RuntimeError(f"only {len(done)} of {len(todo)} lines came back")
    return done


def write_atomic(path, text):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".translating-", suffix=".tmp", dir=d)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o664)
    os.replace(tmp, path)


def cleanup(cfg, apis, items, state, report):
    """Remove a translation once a human subtitle (not forced) exists in its language, or when its video is gone."""
    by_video = {host(cfg, i["path"]): human(i.get("subtitles") or []) for i in items}
    touched = set()
    for out, rec in list(state["translations"].items()):
        video = rec["video"]
        subs = by_video.get(video)
        if subs is None:
            if not os.path.exists(video):
                state["translations"].pop(out)
            continue
        if present(subs, rec["lang"]):
            if not cfg.dry_run:
                try:
                    os.remove(out)
                except FileNotFoundError:
                    pass
                state["translations"].pop(out)
                touched.add(video)
            report["replaced"].append(out)
    return touched


def run(cfg, apis, now):
    report = {"translated": [], "replaced": [], "failed": [], "would_translate": [], "waiting": 0, "left_for_next_night": 0, "seconds": []}
    state = load_state(cfg)
    items = apis.bazarr_items()
    if cfg.only:
        items = [i for i in items if cfg.only in i["path"]]
    refresh = cleanup(cfg, apis, items, state, report)
    jobs = []
    for item in items:
        video = host(cfg, item["path"])
        subs = human(item.get("subtitles") or [])
        p = plan(subs, None, cfg.supported)
        if not p:
            continue
        original, added = apis.arr_info(item)
        p = plan(subs, NAMES.get((original or "").lower()), cfg.supported)
        p = {t: c for t, c in p.items() if not any(r["video"] == video and r["lang"] == t and os.path.exists(o) for o, r in state["translations"].items())}
        if not p:
            continue
        if not due(added, now, cfg.wait_hours):
            report["waiting"] += 1
            continue
        jobs.append((added or "", item, video, p))
    jobs.sort(key=lambda j: j[0], reverse=True)
    started = 0
    for _, item, video, p in jobs:
        if past_deadline(cfg, now if cfg.dry_run else apis_now(apis, now)) or (cfg.max_titles and started >= cfg.max_titles):
            report["left_for_next_night"] += 1
            continue
        started += 1
        if cfg.dry_run:
            for t, c in p.items():
                report["would_translate"].append({"video": os.path.basename(video), "target": t, "source": describe(c[0])})
            continue
        t0 = time.monotonic()
        key_fail = video
        with tempfile.TemporaryDirectory(prefix="subtitle-translate-") as work:
            for s in next(iter(p.values())):
                try:
                    cues = source_cues(cfg, apis, video, s, work, state)
                    if not cues:
                        continue
                    wrote = []
                    for tgt in p:
                        if s["code2"] == tgt:
                            continue
                        done = translate_cues(cfg, apis, cues, s["code2"], tgt, f"{os.path.basename(video)} {describe(s)} -> {tgt}")
                        text = render_srt(cues, done)
                        if [c["time"] for c in parse_srt(text)] != [c["time"] for c in cues]:
                            raise RuntimeError("timestamps changed")
                        out = out_path(video, tgt, bool(s.get("hi")))
                        write_atomic(out, text)
                        state["translations"][out] = {"video": video, "lang": tgt, "source": describe(s), "source_path": s.get("path"),
                                                      "track": s.get("embedded_track_id"), "created": now.isoformat(), "lines": len(cues)}
                        wrote.append(out)
                        report["translated"].append({"video": os.path.basename(video), "target": tgt, "source": describe(s), "out": out, "lines": len(cues)})
                    if wrote:
                        refresh.add(video)
                        state["failed"].pop(key_fail, None)
                        break
                except (OSError, RuntimeError, ValueError) as e:
                    report["failed"].append({"video": os.path.basename(video), "source": describe(s), "error": str(e)[:200]})
                    state["failed"][key_fail] = {"error": str(e)[:200], "at": now.isoformat()}
        report["seconds"].append({"video": os.path.basename(video), "seconds": round(time.monotonic() - t0, 1)})
        save_state(cfg, state)
    if refresh and not cfg.dry_run:
        try:
            apis.refresh(sorted(refresh))
        except Exception as e:
            report["failed"].append({"video": "-", "source": "jellyfin refresh", "error": str(e)[:200]})
    if not cfg.dry_run:
        save_state(cfg, state)
    return report


def apis_now(apis, now):
    return apis.now() if hasattr(apis, "now") else now


# ---------- real APIs ----------

class Http:
    def __init__(self, base, headers, timeout=60):
        self.base, self.headers, self.timeout = base, headers, timeout

    def __call__(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={**self.headers, **({"Content-Type": "application/json"} if data else {})})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None


def arr_key(app):
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", pathlib.Path(f"/srv/appdata/{app}/config.xml").read_text()).group(1)


class RealApis:
    def __init__(self, cfg):
        md = HERE.parent / "Movie-Downloads"
        self.bazarr = Http("http://127.0.0.1:6767/api", {"X-API-KEY": (md / "secrets/hook/bazarr.apikey").read_text().strip()})
        self.radarr = Http("http://127.0.0.1:7878/api/v3", {"X-Api-Key": arr_key("radarr")})
        self.sonarr = Http("http://127.0.0.1:8989/api/v3", {"X-Api-Key": arr_key("sonarr")})
        port = env_file().get("LINGARR_PORT", "9876")
        self.lingarr = Http(f"http://127.0.0.1:{port}/api", {"X-Api-Key": (HERE / "secrets/lingarr.apikey").read_text().strip()}, timeout=1800)
        self.jf_key = HERE.parent / "Jellyfin" / "secrets" / "api-key"
        self._movies = self._series = None
        self._files = {}

    def now(self):
        return dt.datetime.now().astimezone()

    def bazarr_items(self):
        out = [dict(m, kind="movie") for m in self.bazarr("GET", "/movies?start=0&length=-1")["data"]]
        for s in self.bazarr("GET", "/series?start=0&length=-1")["data"]:
            if s.get("episodeFileCount"):
                out += [dict(e, kind="episode") for e in self.bazarr("GET", f"/episodes?seriesid[]={s['sonarrSeriesId']}")["data"]]
        return [i for i in out if i.get("path")]

    def arr_info(self, item):
        if item["kind"] == "movie":
            if self._movies is None:
                self._movies = {m["id"]: m for m in self.radarr("GET", "/movie")}
            m = self._movies.get(item["radarrId"]) or {}
            return (m.get("originalLanguage") or {}).get("name"), (m.get("movieFile") or {}).get("dateAdded")
        if self._series is None:
            self._series = {s["id"]: s for s in self.sonarr("GET", "/series")}
        sid = item["sonarrSeriesId"]
        if sid not in self._files:
            self._files[sid] = {f["path"]: f.get("dateAdded") for f in self.sonarr("GET", f"/episodefile?seriesId={sid}")}
        return (self._series.get(sid, {}).get("originalLanguage") or {}).get("name"), self._files[sid].get(item["path"])

    def translate(self, src, tgt, lines, title):
        body = {"arrMediaId": 0, "title": title, "sourceLanguage": src, "targetLanguage": tgt, "mediaType": "Movie",
                "lines": [{"position": p, "line": t} for p, t in lines]}
        got = self.lingarr("POST", "/translate/content", body)
        return {x["position"]: x["line"] for x in got or []}

    def extract(self, video, s, dest):
        info = json.loads(subprocess.run(["mkvmerge", "-J", video], capture_output=True, text=True, check=True).stdout)
        tracks = [t for t in info.get("tracks", []) if t.get("type") == "subtitles"]
        t = next((t for t in tracks if t["id"] == s["embedded_track_id"]), None)
        if t is None or t.get("codec") not in TEXT_CODECS:
            return False
        r = subprocess.run(["mkvextract", video, "tracks", f"{t['id']}:{dest}"], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dest):
            raise RuntimeError(f"mkvextract exit {r.returncode}: {r.stdout.strip()[-150:]}")
        return True

    def refresh(self, videos):
        key = self.jf_key.read_text().strip()
        jf = Http("http://127.0.0.1:8096", {"Authorization": f'MediaBrowser Token="{key}"'})
        items = jf("GET", "/Items?Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path")["Items"]
        ids = {i["Path"]: i["Id"] for i in items if i.get("Path")}
        for v in videos:
            jid = ids.get("/media" + v.split("/media", 1)[1]) if "/media" in v else None
            if not jid:
                continue
            base = os.path.basename(os.path.splitext(v)[0])
            want = sorted(n for n in os.listdir(os.path.dirname(v)) if n.startswith(base) and is_translated(n))
            # A refresh is sometimes skipped (seen 2026-10-31): ask again until Jellyfin lists exactly our files.
            for _ in range(3):
                jf("POST", f"/Items/{jid}/Refresh?MetadataRefreshMode=Default&ImageRefreshMode=None&ReplaceAllMetadata=false")
                time.sleep(20)
                streams = jf("GET", f"/Items/{jid}/PlaybackInfo")["MediaSources"][0]["MediaStreams"]
                if sorted(os.path.basename(x.get("Path") or "") for x in streams if is_translated(x.get("Path") or "")) == want:
                    break


def env_file():
    out = {}
    try:
        for line in (HERE / ".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def supported_languages():
    codes = set(env_file().get("LT_LANGUAGES", "en,de").split(","))
    back = {v: k for k, v in LT_CODE.items()}
    return {back.get(c, c) for c in codes} | ({"pb"} if "pt" in codes else set())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--only")
    a = ap.parse_args(argv)
    stop = os.environ.get("ST_STOP_AT", "")
    cfg = Config(data_host=os.environ.get("ST_DATA_HOST", "/srv/video"), state_dir=os.environ.get("ST_STATE", "/srv/appdata/subtitle-translate"),
                 wait_hours=float(os.environ.get("ST_WAIT_HOURS", "48")), supported=supported_languages(),
                 stop_at=dt.time.fromisoformat(stop) if stop else None, max_titles=a.max, dry_run=a.dry_run, only=a.only)
    apis = RealApis(cfg)
    t0 = time.time()
    try:
        report = run(cfg, apis, apis.now())
    except (urllib.error.URLError, OSError) as e:
        print(f"subtitle-translate: error: {e}", file=sys.stderr)
        return 1
    report["at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    report["run_seconds"] = round(time.time() - t0, 1)
    if not cfg.dry_run:
        status = pathlib.Path(cfg.state_dir) / "status.json"
        status.write_text(json.dumps(report, indent=1))
    for r in report["translated"]:
        print(f"translated: {r['video']} -> {r['target']} from {r['source']} ({r['lines']} cues)")
    for r in report["would_translate"]:
        print(f"would translate: {r['video']} -> {r['target']} from {r['source']}")
    for r in report["replaced"]:
        print(f"replaced by a human subtitle: {os.path.basename(r)}")
    for r in report["failed"]:
        print(f"failed: {r['video']} ({r['source']}): {r['error']}")
    print(f"summary: {len(report['translated'])} translated, {len(report['replaced'])} replaced, {len(report['failed'])} failed, "
          f"{report['waiting']} waiting, {report['left_for_next_night']} left for the next night, {report['run_seconds']} s")
    return 1 if report["failed"] and not report["translated"] else 0


if __name__ == "__main__":
    sys.exit(main())
