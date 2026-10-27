#!/usr/bin/env python3
"""Subtitle check (README "Subtitle check"): finds broken text encoding in subtitle files and repairs it only when the repair is unambiguous.
Finds: not UTF-8, U+FFFD, C1 control characters, mojibake = UTF-8 once read as cp1252/latin-1/cp1251 and saved again (в™Є or В™Є for ♪, Ã¤ for ä, â€™ for ’, also twice over).
Repair: every mojibake sequence is turned back into bytes and decoded as UTF-8, and must give an expected character (Latin-1 letter/symbol, typographic punctuation, ♪, emoji); the file is written only when nothing suspicious is left. The original goes to <state>/backup/ (flat: the container uid and the host user both write there), outside the library. Anything else is only reported.
Every repair and finding: a line in <state>/hook.log or scan.log and an event file in <state>/events/ for scripts/queue-watchdog.py (morning digest).
Usage: subtitle-check.py [--dry-run] <subtitle> [provider] [subtitle id]     Bazarr post-processing (in the container, state /config/subtitle-check)
       subtitle-check.py [--dry-run] --scan <branch>[=<merged>]...           nightly: files changed since the last scan (systemd/subtitle-check.timer)
Env: SC_STATE (state dir)."""
import collections
import datetime
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass

SUBS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
UTF8_BOM = b"\xef\xbb\xbf"
CYRILLIC = {"ru", "uk", "bg", "sr", "mk", "be", "kk", "ky", "mn", "tg"}
PUNCT = {chr(c) for c in [*range(0x2010, 0x2028), *range(0x2030, 0x203B), 0x20AC, 0x2122, 0x266A, 0x266B]}
RESIDUE = re.compile("(?:â|в)(?:€|Ђ|™|„)")
RESIDUE_CYR = re.compile("â(?:€|™|„)")
C1 = re.compile("[\u0080-\u009f]")
LANG = re.compile(r"\.([a-z]{2,3}(?:-[a-z]{2,4})?)((?:\.(?:hi|cc|sdh|forced))*)\.(?:srt|ass|ssa|vtt|sub)$", re.I)
IDS = re.compile(r"\s*[\[{](?:tmdb|tvdb|imdb)id-[^\]}]*[\]}]")
EPISODE = re.compile(r"\bS\d{1,3}E\d{1,4}\b", re.I)
SEASON = re.compile(r"^(?:Season \d+|Specials)$", re.I)
PASSES = 3


@dataclass
class Config:
    state: pathlib.Path
    dry_run: bool = False
    log_name: str = "hook.log"


@dataclass
class Result:
    status: str
    fixed: str = None
    output: bytes = None
    reasons: list = None
    counts: dict = None


def to_bytes(s, enc):
    """One byte per character in that code page; its undefined bytes come back as the C1 character of the same number."""
    out = bytearray()
    for c in s:
        try:
            b = c.encode(enc)
        except UnicodeEncodeError:
            if 0x80 <= ord(c) <= 0x9F:
                b = bytes([ord(c)])
            else:
                return None
        out += b
    return bytes(out)


def plausible(ch, n, lead, enc, lowered, cyrillic, seg):
    cp = ord(ch)
    if lowered and (n == 2 or ch.lower() != ch.upper()):
        return False
    if n == 2:
        if cyrillic and enc == "cp1251":
            return False
        if lead in (0xC2, 0xC3):
            return 0xA0 <= cp <= 0xFF
        return cp >= 0xA0 and any(0x80 <= ord(c) <= 0x9F for c in seg[1:])
    if n == 3:
        return lead == 0xE2 and ch in PUNCT
    return cp >= 0x1F000


LEADS = {c for enc in ("cp1252", "cp1251") for c in bytes(range(0xC2, 0xF5)).decode(enc, errors="ignore")}
LEADS |= {c.upper() for c in LEADS}


def match_at(text, i, cyrillic):
    lead_ch = text[i]
    if lead_ch not in LEADS:
        return None
    variants = [(lead_ch, False)] + ([(lead_ch.lower(), True)] if lead_ch.lower() != lead_ch else [])
    for enc in ("cp1252", "cp1251"):
        for v, lowered in variants:
            b = to_bytes(v, enc)
            if not b or len(b) != 1:
                continue
            lead = b[0]
            n = 2 if 0xC2 <= lead <= 0xDF else 3 if 0xE0 <= lead <= 0xEF else 4 if 0xF0 <= lead <= 0xF4 else 0
            if not n or i + n > len(text):
                continue
            seg = v + text[i + 1:i + n]
            bb = to_bytes(seg, enc)
            if bb is None or len(bb) != n or any(not 0x80 <= x <= 0xBF for x in bb[1:]):
                continue
            try:
                ch = bb.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if plausible(ch, n, lead, enc, lowered, cyrillic, seg):
                return n, ch
    return None


def repair_pass(text, cyrillic, counts):
    out, i, changed = [], 0, False
    while i < len(text):
        m = match_at(text, i, cyrillic)
        if m:
            n, ch = m
            counts[f"{text[i:i + n]} → {ch}"] += 1
            out.append(ch)
            i += n
            changed = True
        else:
            out.append(text[i])
            i += 1
    return "".join(out), changed


def problems(text, cyrillic):
    reasons = []
    if "�" in text:
        reasons.append(f"U+FFFD replacement character ×{text.count(chr(0xFFFD))}")
    c1 = C1.findall(text)
    if c1:
        reasons.append(f"C1 control characters ×{len(c1)}")
    res = (RESIDUE_CYR if cyrillic else RESIDUE).findall(text)
    if res:
        reasons.append(f"mojibake residue ×{len(res)} ({', '.join(sorted(set(res)))})")
    return reasons


def analyse(data, lang="und"):
    """ok, binary, repaired (fixed text + output bytes) or suspect (reasons)."""
    cyrillic = lang.split("-")[0] in CYRILLIC
    utf16 = data.startswith((b"\xff\xfe", b"\xfe\xff"))
    if not utf16 and b"\x00" in data[:4096]:
        return Result("binary", reasons=[])
    bom = UTF8_BOM if data.startswith(UTF8_BOM) else b""
    try:
        text = data.decode("utf-16") if utf16 else data[len(bom):].decode("utf-8")
    except UnicodeDecodeError:
        return Result("suspect", reasons=["not UTF-8 (Bazarr's UTF-8 conversion did not run or failed)"], counts={})
    counts = collections.Counter()
    fixed = text
    for _ in range(PASSES):
        fixed, changed = repair_pass(fixed, cyrillic, counts)
        if not changed:
            break
    reasons = problems(fixed, cyrillic)
    if repair_pass(fixed, cyrillic, collections.Counter())[1]:
        reasons.append(f"mojibake left after {PASSES} passes")
    if reasons:
        return Result("suspect", reasons=reasons + ([f"{sum(counts.values())} sequences were repairable, file left as it is"] if counts else []), counts=dict(counts))
    if not counts:
        return Result("ok", reasons=[], counts={})
    if utf16:
        return Result("suspect", reasons=["mojibake in a UTF-16 file, not repaired"], counts=dict(counts))
    return Result("repaired", fixed=fixed, output=bom + fixed.encode("utf-8"), reasons=[], counts=dict(counts))


def language(name):
    m = LANG.search(name)
    return m.group(1).lower() if m else "und"


def title(path):
    p = pathlib.PurePosixPath(path)
    parent = p.parent
    ep = ""
    if SEASON.match(parent.name):
        m = EPISODE.search(p.name)
        ep = " " + m.group(0).upper() if m else ""
        parent = parent.parent
    return f"{IDS.sub('', parent.name).strip()}{ep} · {language(p.name)}"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def log(cfg, line):
    cfg.state.mkdir(parents=True, exist_ok=True)
    with open(cfg.state / cfg.log_name, "a", encoding="utf-8") as f:
        f.write(f"{now_iso()} {line}\n")


def say(line):
    print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


_seq = 0


def event(cfg, kind, path, r, source):
    global _seq
    _seq += 1
    d = cfg.state / "events"
    d.mkdir(parents=True, exist_ok=True)
    ev = {"app": "bazarr", "kind": kind, "title": title(path), "lang": language(pathlib.Path(path).name), "path": str(path),
          "reasons": r.reasons or [], "counts": r.counts or {}, "source": source, "ts": int(time.time())}
    f = d / f"{int(time.time() * 1000)}-{os.getpid()}-{_seq}.json"
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(ev, indent=1), encoding="utf-8")
    tmp.replace(f)


def counts_text(counts):
    return ", ".join(f"{v}× {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def write_file(target, data):
    tmp = target.with_name(f".{target.name}.sc-tmp")
    try:
        tmp.write_bytes(data)
        os.chmod(tmp, target.stat().st_mode & 0o777)
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        with open(target, "r+b") as f:
            f.write(data)
            f.truncate()


def check_file(path, cfg, source="", write_path=None, log_ok=True):
    """ok, binary, skipped, repaired or suspect. Reads path, writes the repair to write_path (default path)."""
    p = pathlib.Path(path)
    if p.is_symlink() or p.suffix.lower() not in SUBS:
        return "skipped"
    data = p.read_bytes()
    target = pathlib.Path(write_path) if write_path else p
    r = analyse(data, language(p.name))
    src = f" ({source})" if source else ""
    if r.status in ("ok", "binary"):
        if log_ok and not cfg.dry_run:
            log(cfg, f"{r.status} {target}{src}")
        return r.status
    if r.status == "repaired":
        if cfg.dry_run:
            say(f"would repair {target}: {counts_text(r.counts)}")
            return r.status
        stamp = datetime.datetime.now()
        backup = cfg.state / "backup" / f"{stamp.strftime('%Y-%m-%d_%H%M%S')} {target.parent.name} -- {target.name}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(data)
        write_file(target, r.output)
        log(cfg, f"repaired {target}{src}: {counts_text(r.counts)}; original in {backup}")
        event(cfg, "subtitle_repaired", target, r, source)
        say(f"repaired {target}: {counts_text(r.counts)}")
        return r.status
    detail = "; ".join(r.reasons) + (f" [{counts_text(r.counts)}]" if r.counts else "")
    if cfg.dry_run:
        say(f"suspect {target}: {detail}")
        return r.status
    log(cfg, f"suspect {target}{src}: {detail}")
    event(cfg, "subtitle_suspect", target, r, source)
    say(f"suspect, left as it is, reported {target}: {detail}")
    return r.status


def scan(pairs, cfg, now=None):
    """Files on the branch changed (mtime or ctime) since the last scan; the first scan checks all. Repairs go through the merged path."""
    now = time.time() if now is None else now
    state_file = cfg.state / "scan-state.json"
    try:
        last = json.loads(state_file.read_text())["last_run"]
    except (OSError, ValueError, KeyError):
        last = None
    res = {"checked": 0, "repaired": 0, "suspect": 0}
    for branch, merged in pairs:
        branch = pathlib.Path(branch)
        for dp, _, files in os.walk(branch):
            for f in sorted(files):
                p = pathlib.Path(dp) / f
                if p.suffix.lower() not in SUBS:
                    continue
                try:
                    st = p.lstat()
                except OSError:
                    continue
                if last is not None and max(st.st_mtime, st.st_ctime) < last:
                    continue
                res["checked"] += 1
                status = check_file(p, cfg, "nightly scan", pathlib.Path(merged) / p.relative_to(branch) if merged else None, log_ok=False)
                if status in res:
                    res[status] += 1
    if not cfg.dry_run:
        log(cfg, f"scan {'first run' if last is None else 'since ' + datetime.datetime.fromtimestamp(last).astimezone().isoformat(timespec='seconds')}: "
                 f"{res['checked']} checked, {res['repaired']} repaired, {res['suspect']} suspect")
        state_file.write_text(json.dumps({"last_run": now}))
    return res


def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    state = pathlib.Path(env.get("SC_STATE", "/config/subtitle-check"))
    if argv[:1] == ["--scan"]:
        pairs = [tuple(a.split("=", 1)) if "=" in a else (a, None) for a in argv[1:]]
        if not pairs:
            print(__doc__, file=sys.stderr)
            return 2
        res = scan(pairs, Config(state, dry, "scan.log"))
        say(f"{res['checked']} checked, {res['repaired']} repaired, {res['suspect']} suspect")
        return 0
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    path = pathlib.Path(argv[0])
    if not path.is_file():
        print(f"subtitle-check: no such file: {path}", file=sys.stderr)
        return 1
    try:
        check_file(path, Config(state, dry), " ".join(argv[1:3]))
    except OSError as e:
        print(f"subtitle-check: {path}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
