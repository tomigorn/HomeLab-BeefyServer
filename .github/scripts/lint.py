#!/usr/bin/env python3
"""shellcheck / hadolint over the tracked files, minus the reviewed baseline in .github/lint-baseline.toml. Usage: python3 .github/scripts/lint.py shellcheck|hadolint
Exit 0 clean, 1 new finding or stale baseline entry, 2 error."""
import collections
import json
import os
import re
import subprocess
import sys
import tomllib

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BASELINE = os.path.join(ROOT, ".github", "lint-baseline.toml")
SHELLCHECK_SEVERITY = "warning"
VENDORED = "/plugins-local/"


def parse_shellcheck(out):
    return [{"file": c["file"], "line": c["line"], "code": f"SC{c['code']}", "message": c["message"]}
            for c in json.loads(out)["comments"]]


def parse_hadolint(out):
    return [{"file": c["file"], "line": c["line"], "code": c["code"], "message": c["message"]}
            for c in json.loads(out) if c["level"] in ("error", "warning")]


def check_entries(entries):
    for e in entries:
        for k in ("file", "code", "count", "reason"):
            if not str(e.get(k, "")).strip():
                raise ValueError(f"baseline entry {e}: {k} missing or empty")
    return entries


def evaluate(found, entries):
    """(failures, notes): findings beyond the baseline, stale entries; entries that allow more than found."""
    allowed = {(e["file"], e["code"]): e["count"] for e in entries}
    by = collections.defaultdict(list)
    for x in found:
        by[(x["file"], x["code"])].append(x)
    bad, notes = [], []
    for key, items in sorted(by.items()):
        n = allowed.get(key, 0)
        if len(items) > n:
            for x in items:
                bad.append(f"{x['file']}:{x['line']}: {x['code']} {x['message']} (baseline allows {n})")
        elif len(items) < n:
            notes.append(f"{key[0]}: {key[1]} found {len(items)}, baseline allows {n}: lower the count")
    for key, n in sorted(allowed.items()):
        if key not in by:
            bad.append(f"stale baseline entry: {key[0]} {key[1]} (no longer found): remove it")
    return bad, notes


def tracked():
    return subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()


def shell_files():
    out = []
    for f in tracked():
        p = os.path.join(ROOT, f)
        if VENDORED in f or os.path.islink(p) or not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            first = fh.readline(200)
        if re.match(rb"#!.*[/ ](ba|da|k)?sh( |\s*$)", first):
            out.append(f)
    return out


def dockerfiles():
    return [f for f in tracked() if re.search(r"(^|/)Dockerfile[^/]*$", f) and VENDORED not in f]


def run(tool):
    if tool == "shellcheck":
        files = shell_files()
        p = subprocess.run(["shellcheck", "-S", SHELLCHECK_SEVERITY, "-f", "json1", *files], cwd=ROOT, capture_output=True, text=True)
        return files, parse_shellcheck(p.stdout)
    files = dockerfiles()
    p = subprocess.run(["hadolint", "--no-fail", "-f", "json", *files], cwd=ROOT, capture_output=True, text=True)
    return files, parse_hadolint(p.stdout)


def main(argv):
    if len(argv) != 2 or argv[1] not in ("shellcheck", "hadolint"):
        print(__doc__, file=sys.stderr)
        return 2
    tool = argv[1]
    try:
        with open(BASELINE, "rb") as f:
            entries = check_entries(tomllib.load(f).get(tool, []))
        files, found = run(tool)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError, subprocess.CalledProcessError) as e:
        print(f"lint: {e}", file=sys.stderr)
        return 2
    bad, notes = evaluate(found, entries)
    for line in bad + notes:
        print(line)
    baselined = sum(e["count"] for e in entries)
    print(f"{tool}: {len(files)} files, {len(found)} findings, {baselined} baselined, {len(bad)} to fix")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
