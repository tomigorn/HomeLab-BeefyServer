import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import record

METHODS = ("direct", "remux", "audio", "video")
RANK = {m: i for i, m in enumerate(METHODS)}
GAP = 300.0
KEEP_SECONDS = 90 * 86400
DAY = 86400
ENCODING_XML = Path("/srv/appdata/jellyfin/encoding.xml")
TONEMAP_KEYS = ("EnableTonemapping", "EnableVppTonemapping", "EnableVideoToolboxTonemapping")


def _video_stream(item):
    for stream in item.get("MediaStreams") or []:
        if stream.get("Type") == "Video":
            return stream
    return {}


def _reasons(info):
    r = (info or {}).get("TranscodeReasons")
    if isinstance(r, str):
        r = [x.strip() for x in r.split(",")]
    return [str(x) for x in r or [] if x]


def classify(session):
    pm = (session.get("PlayState") or {}).get("PlayMethod")
    if pm == "DirectStream":
        return "remux"
    if pm != "Transcode":
        return "direct"
    info = session.get("TranscodingInfo")
    if not info:
        return "remux"
    if info.get("IsVideoDirect") is False:
        return "video"
    return "remux" if info.get("IsAudioDirect") else "audio"


def tonemapping_enabled(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False
    return any((root.findtext(k) or "").strip().lower() == "true" for k in TONEMAP_KEYS)


def hdr_to_sdr(session, tonemapping=False):
    """yes: picture converted, source range not SDR, and the output is SDR: h264, range named as a reason, or server tone mapping is on."""
    if classify(session) != "video":
        return "no"
    src = _video_stream(session.get("NowPlayingItem") or {}).get("VideoRangeType")
    if not src or src.upper() in ("SDR", "UNKNOWN"):
        return "no"
    info = session.get("TranscodingInfo") or {}
    if "VideoRangeTypeNotSupported" in _reasons(info):
        return "yes"
    codec = str(info.get("VideoCodec") or "").lower()
    return "yes" if codec == "h264" or (codec == "hevc" and tonemapping) else "no"


def title(item):
    name = item.get("Name") or ""
    series = item.get("SeriesName")
    if series:
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        if s is not None and e is not None:
            return f"{series} S{int(s):02d}E{int(e):02d}"
        return f"{series} {name}".strip()
    year = item.get("ProductionYear")
    return f"{name} ({year})" if year else name


def _write_rows(path, rows):
    record._atomic_write(path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), 0o600)


def _load_open(path):
    try:
        data = json.loads(Path(path).read_text())
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def read_plays(state_dir, since, now=None):
    """Finished plus still-open playbacks started at or after `since`, read-only."""
    state_dir = Path(state_dir)
    rows = [r for r in record._read_rows(state_dir / "plays.jsonl") if "start" in r]
    for r in _load_open(state_dir / "plays-open.json").values():
        rows.append({k: v for k, v in r.items() if k != "last_seen"} | {"end": r.get("last_seen")})
    return [r for r in rows if r.get("start", 0) >= since]


class PlayLog:
    def __init__(self, state_dir, now=None, encoding_path=ENCODING_XML):
        self.dir = Path(state_dir)
        self.path = self.dir / "plays.jsonl"
        self.open_path = self.dir / "plays-open.json"
        now = time.time() if now is None else now
        rows = [r for r in record._read_rows(self.path) if isinstance(r.get("start"), (int, float))]
        self.rows = [r for r in rows if r["start"] >= now - KEEP_SECONDS]
        if len(self.rows) != len(rows):
            _write_rows(self.path, self.rows)
            for i in range(1, record.KEEP + 1):
                self.path.with_name(f"{self.path.name}.{i}").unlink(missing_ok=True)
        self.open = _load_open(self.open_path)
        self.encoding_path, self._enc_mtime, self._tonemap = Path(encoding_path), None, False

    def _tonemapping(self):
        try:
            mtime = self.encoding_path.stat().st_mtime
        except OSError:
            self._enc_mtime, self._tonemap = None, False
            return False
        if mtime != self._enc_mtime:
            self._enc_mtime, self._tonemap = mtime, tonemapping_enabled(self.encoding_path)
        return self._tonemap

    def update(self, sessions, now):
        dirty = False
        tonemap = self._tonemapping()
        seen = set()
        for s in sessions:
            item = s.get("NowPlayingItem")
            if not item:
                continue
            key = f"{s.get('Id')}|{item.get('Id')}"
            seen.add(key)
            pb = self.open.get(key)
            if pb is None:
                vs = _video_stream(item)
                pb = self.open[key] = {
                    "start": now, "user": s.get("UserName"), "client": s.get("Client"), "device": s.get("DeviceName"),
                    "title": title(item), "item_id": item.get("Id"), "method": "direct", "reasons": [],
                    "hdr_to_sdr": "no", "source_codec": vs.get("Codec"), "source_range": vs.get("VideoRangeType"),
                    "output_codec": None, "bitrate": None,
                }
                dirty = True
            pb["last_seen"] = now
            method = classify(s)
            info = s.get("TranscodingInfo") or {}
            before = (pb["method"], list(pb["reasons"]), pb["hdr_to_sdr"], pb["output_codec"])
            if RANK[method] > RANK[pb["method"]]:
                pb["method"] = method
            if method != "direct":
                for r in _reasons(info):
                    if r not in pb["reasons"]:
                        pb["reasons"].append(r)
                if info.get("VideoCodec"):
                    pb["output_codec"] = info.get("VideoCodec")
                if info.get("Bitrate") is not None:
                    pb["bitrate"] = info.get("Bitrate")
            if hdr_to_sdr(s, tonemap) == "yes":
                pb["hdr_to_sdr"] = "yes"
            if before != (pb["method"], pb["reasons"], pb["hdr_to_sdr"], pb["output_codec"]):
                dirty = True
        for key in [k for k, pb in self.open.items() if k not in seen and now - pb["last_seen"] >= GAP]:
            pb = self.open.pop(key)
            row = {k: v for k, v in pb.items() if k != "last_seen"} | {"end": pb["last_seen"]}
            record._append(self.path, row)
            self.rows.append(row)
            dirty = True
        if dirty:
            self.save_open()

    def save_open(self):
        record._atomic_write(self.open_path, json.dumps(self.open, sort_keys=True), 0o600)

    def recent(self, now, window=DAY):
        cutoff = now - window
        live = [{k: v for k, v in pb.items() if k != "last_seen"} | {"end": pb["last_seen"]} for pb in self.open.values()]
        return [r for r in self.rows + live if r["start"] >= cutoff]


def report(rows):
    rows = sorted(rows, key=lambda r: (-RANK.get(r.get("method"), 0), -r.get("start", 0)))
    header = ["started", "user", "client", "device", "title", "method", "hdr>sdr", "minutes", "reasons"]
    table = [header]
    for r in rows:
        minutes = max(0.0, (r.get("end") or r["start"]) - r["start"]) / 60
        table.append([
            datetime.fromtimestamp(r["start"]).strftime("%Y-%m-%d %H:%M"), str(r.get("user") or "-"),
            str(r.get("client") or "-"), str(r.get("device") or "-"), str(r.get("title") or "-"),
            str(r.get("method") or "-"), str(r.get("hdr_to_sdr") or "-"), f"{minutes:.0f}",
            ",".join(r.get("reasons") or []) or "-",
        ])
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    out = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in table]
    counts = {m: sum(1 for r in rows if r.get("method") == m) for m in METHODS}
    out.append("")
    out.append(f"{len(rows)} playbacks: " + ", ".join(f"{m} {n}" for m, n in counts.items()))
    return "\n".join(out)
