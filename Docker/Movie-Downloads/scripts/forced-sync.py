#!/usr/bin/env python3
"""Forced-subtitle sync (README "Subtitle tracks and sync"): Bazarr never syncs forced subtitles, so a forced file keeps the offset of its source.
Shifts each *.forced.srt by one constant offset (no speed change) so its cue edges line up with the full subtitle of the same title: same language, else English, else German (the full one is synced by Bazarr).
Method: cue starts and ends of the forced file, per third of its cues, against the reference edges (±0.25 s) at lags of ±30 s. Accepted only when every third has a clear peak and all thirds agree within 0.3 s; the median is applied. Otherwise the file is left as it is and reported once.
Skipped and reported: no reference, fewer than 5 cues, no clear peak, thirds disagree, offset over 30 s. An offset under 0.15 s counts as in sync.
Original to <state>/backup/, replaced through the merged path (temp file + rename). A file is looked at again only when it or its reference changed (<state>/forced-sync-state.json).
Events (digest): subtitle_forced_synced / subtitle_forced_unverified in <state>/events/. Log: <state>/forced-sync.log. Last run: <state>/forced-sync-status.json.
Usage: forced-sync.py [--dry-run] --scan <branch>[=<merged>]...     nightly (systemd/forced-sync.timer)
Env: FS_STATE (state dir)."""
import datetime
import importlib.util
import json
import os
import pathlib
import re
import statistics
import sys
import time
from dataclasses import dataclass

_spec = importlib.util.spec_from_file_location("sc", pathlib.Path(__file__).resolve().with_name("subtitle-check.py"))
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)
Config = sc.Config

RES = 20
MAXLAG = 30.0
TOL = 5
MIN_CUES = 5
MIN_FRAC = 0.5
MIN_RATIO = 1.25
AGREE = 0.3
IN_SYNC = 0.15
NAME = re.compile(r"^(?P<base>.+?)\.(?P<lang>[a-z]{2,3}(?:-[a-z]{2,4})?)(?P<flags>(?:\.(?:hi|cc|sdh|forced))*)\.srt$", re.I)
TIME = re.compile(rb"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})(\s*-->\s*)(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass
class Measure:
    status: str
    offset: float = 0.0
    reason: str = ""


def ms(h, m, s, f):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(f)


def parse_srt(data):
    return [(ms(*m.group(1, 2, 3, 4)) / 1000, ms(*m.group(6, 7, 8, 9)) / 1000, "") for m in TIME.finditer(data)]


def fmt(t):
    return b"%02d:%02d:%02d,%03d" % (t // 3600000, t // 60000 % 60, t // 1000 % 60, t % 1000)


def shift_srt(data, offset):
    d = round(offset * 1000)

    def sub(m):
        a = max(0, ms(*m.group(1, 2, 3, 4)) + d)
        b = max(a, ms(*m.group(6, 7, 8, 9)) + d)
        return fmt(a) + m.group(5) + fmt(b)

    return TIME.sub(sub, data)


def bits(points, size):
    buf = bytearray(size // 8 + 1)
    for p in points:
        if 0 <= p < size:
            buf[p >> 3] |= 1 << (p & 7)
    return int.from_bytes(buf, "little")


def edges(cues):
    return {round(t * RES) for s, e, _ in cues for t in (s, e)}


def fit(group, dilated, size):
    """Best lag (seconds) of one group of forced cues, its confidence and the runner-up outside ±1 s."""
    pts = edges(group)
    f = bits(pts, size)
    n = f.bit_count()
    reach = int(MAXLAG * RES)
    padded = dilated << reach
    scores = [((padded >> (reach + lag)) & f).bit_count() for lag in range(-reach, reach + 1)]
    best = max(scores)
    k = scores.index(best)
    near = [i for i, v in enumerate(scores) if v == best and abs(i - k) <= RES]
    lag = (statistics.median(near) - reach) / RES
    second = max([v for i, v in enumerate(scores) if abs(i - k) > RES] or [0])
    return lag, best / n if n else 0, best, second


def measure(forced, ref):
    if len(forced) < MIN_CUES:
        return Measure("unverified", reason=f"fewer than {MIN_CUES} cues")
    if len(ref) < MIN_CUES:
        return Measure("unverified", reason="reference has too few cues")
    size = int((max(e for _, e, _ in forced + ref) + MAXLAG + 5) * RES)
    dil = set()
    for p in edges(ref):
        dil.update(range(p - TOL, p + TOL + 1))
    dilated = bits(dil, size)
    forced = sorted(forced)
    g = min(3, len(forced) // 3)
    lags = []
    for i in range(g):
        grp = forced[i * len(forced) // g:(i + 1) * len(forced) // g]
        lag, frac, best, second = fit(grp, dilated, size)
        if frac < MIN_FRAC or best < MIN_RATIO * second:
            return Measure("unverified", reason=f"no clear peak in part {i + 1} of {g} (match {frac:.0%})")
        lags.append(lag)
    if max(lags) - min(lags) > AGREE:
        return Measure("unverified", reason="parts disagree: " + ", ".join(f"{x:+.2f} s" for x in lags))
    off = round(statistics.median(lags), 2)
    if abs(off) >= MAXLAG - 0.5:
        return Measure("unverified", reason=f"offset {off:+.2f} s is over {MAXLAG:.0f} s")
    return Measure("in-sync" if abs(off) < IN_SYNC else "offset", off)


def reference(path):
    """Full subtitle beside a forced one: same title, same language, else English, else German."""
    m = NAME.match(path.name)
    if not m:
        return None
    base, lang = m.group("base"), m.group("lang").lower()
    tiers = {lang: 0, "en": 1, "de": 2}
    found = []
    for p in path.parent.iterdir():
        n = NAME.match(p.name)
        if not n or n.group("base") != base or "forced" in n.group("flags").lower() or p.is_symlink():
            continue
        t = tiers.get(n.group("lang").lower())
        if t is not None:
            found.append((t, bool(n.group("flags")), p.name, p))
    return min(found)[3] if found else None


def sig_of(forced, ref):
    st = forced.stat()
    rs = ref.stat() if ref else None
    return [st.st_mtime_ns, st.st_size, ref.name if ref else None, rs.st_mtime_ns if rs else None, rs.st_size if rs else None]


def sync_file(p, target, state, cfg):
    """Returns synced, in_sync or unverified; records the outcome in state."""
    key = str(target)
    ref = reference(p)
    sig = sig_of(p, ref)
    if state.get(key, {}).get("sig") == sig:
        return None
    data = p.read_bytes()
    if ref is None:
        m = Measure("unverified", reason="no full subtitle of the same title beside it")
    else:
        m = measure(parse_srt(data), parse_srt(ref.read_bytes()))
    lang = sc.language(p.name)
    r = sc.Result(m.status, reasons=[m.reason] if m.reason else [f"shifted {m.offset:+.2f} s against {ref.name}"] if m.status == "offset" else [])
    if m.status == "offset":
        if cfg.dry_run:
            sc.say(f"would shift {target} by {m.offset:+.2f} s against {ref.name}")
            return "synced"
        stamp = datetime.datetime.now()
        backup = cfg.state / "backup" / f"{stamp.strftime('%Y-%m-%d_%H%M%S')} {target.parent.name} -- {target.name}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(data)
        sc.write_file(target, shift_srt(data, m.offset))
        sc.log(cfg, f"synced {target}: {m.offset:+.2f} s against {ref.name}; original in {backup}")
        sc.event(cfg, "subtitle_forced_synced", target, r, "forced sync")
        sc.say(f"synced {target}: {m.offset:+.2f} s against {ref.name}")
        state[key] = {"sig": sig_of(p, ref), "result": f"synced {m.offset:+.2f}"}
        return "synced"
    if m.status == "in-sync":
        if not cfg.dry_run:
            sc.log(cfg, f"in sync {target} ({m.offset:+.2f} s against {ref.name})")
            state[key] = {"sig": sig, "result": "in-sync"}
        return "in_sync"
    if cfg.dry_run:
        sc.say(f"unverified {target}: {m.reason}")
        return "unverified"
    sc.log(cfg, f"unverified {target}: {m.reason}")
    sc.event(cfg, "subtitle_forced_unverified", target, r, "forced sync")
    sc.say(f"unverified, left as it is, reported {target}: {m.reason}")
    state[key] = {"sig": sig, "result": "unverified"}
    return "unverified"


def scan(pairs, cfg, now=None):
    now = time.time() if now is None else now
    state_file = cfg.state / "forced-sync-state.json"
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        state = {}
    res = {"checked": 0, "synced": 0, "in_sync": 0, "unverified": 0, "errors": 0}
    for branch, merged in pairs:
        branch = pathlib.Path(branch)
        for dp, _, files in os.walk(branch):
            for f in sorted(files):
                n = NAME.match(f)
                if not n or "forced" not in n.group("flags").lower():
                    continue
                p = pathlib.Path(dp) / f
                if p.is_symlink():
                    continue
                target = pathlib.Path(merged) / p.relative_to(branch) if merged else p
                try:
                    out = sync_file(p, target, state, cfg)
                except (OSError, ValueError) as e:
                    res["errors"] += 1
                    if not cfg.dry_run:
                        sc.log(cfg, f"error {target}: {e}")
                    continue
                if out:
                    res["checked"] += 1
                    res[out] += 1
    if not cfg.dry_run:
        sc.log(cfg, f"scan: {res['checked']} checked, {res['synced']} synced, {res['in_sync']} in sync, {res['unverified']} unverified, {res['errors']} errors")
        state_file.write_text(json.dumps(state, indent=0))
        (cfg.state / "forced-sync-status.json").write_text(json.dumps({"last_run": now, **res}))
    return res


def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if argv[:1] != ["--scan"] or len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    pairs = [tuple(a.split("=", 1)) if "=" in a else (a, None) for a in argv[1:]]
    res = scan(pairs, Config(pathlib.Path(env.get("FS_STATE", "/srv/appdata/bazarr/subtitle-check")), dry, "forced-sync.log"))
    sc.say(f"{res['checked']} checked, {res['synced']} synced, {res['in_sync']} in sync, {res['unverified']} unverified, {res['errors']} errors")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
