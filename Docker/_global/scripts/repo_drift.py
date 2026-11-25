#!/usr/bin/env python3
"""Repo-vs-live drift: what runs or is installed on this host but differs from the repo (nightly, repo-drift.timer).
Kinds: compose (running containers vs `docker compose config` of the repo: image, env keys, mounts, labels), unit (systemd units and drop-ins), script (/usr/local), etc (/etc drop-ins, sysctl and journald effective values), worktree (uncommitted files in the repo).
Writes the textfile metrics drift_items{host,kind} and a JSON list of the items (read by arch_page.py). Never prints env values. Never fixes anything. Doc: README.md "repo-drift".
Usage: repo_drift.py --host fastpi --prom FILE --json FILE [--no-help]   Tests: python3 test_repo_drift.py"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
KINDS = ("compose", "unit", "script", "etc", "worktree")
HOME = Path(os.environ.get("HOME", "/home/pi"))
UNIT_DIRS = [Path("/etc/systemd/system"), HOME / ".config/systemd/user"]
SCRIPT_DIRS = [Path("/usr/local/sbin"), Path("/usr/local/bin"), Path("/usr/local/libexec/fastpi")]
ETC_DIRS = [Path(p) for p in ("/etc/sysctl.d", "/etc/modprobe.d", "/etc/tmpfiles.d", "/etc/ssh/sshd_config.d", "/etc/apt/apt.conf.d", "/etc/docker", "/etc/claude-here")]
SKIP_REPO = re.compile(r"(^|/)(_Deprecated|plugins-local|node_modules|__pycache__)/|\.bak|/test[_-]|\.pyc$")
TIMEOUT = 120


# ---------- compose ----------

def compose_diffs(svc, insp):
    """Reasons the running container differs from the repo's compose service. Env values are never reported."""
    out = []
    cfg = insp.get("Config") or {}
    if svc.get("image"):
        live = (cfg.get("Image") or "").split("@")[0]
        want = svc["image"].split("@")[0]
        if live != want:
            out.append(f"image: repo {want}, live {live}")
    env_live = {e.split("=", 1)[0] for e in cfg.get("Env") or []}
    out += [f"env key missing live: {k}" for k in sorted(svc.get("environment") or {}) if k not in env_live]
    dest = {m.get("Destination") for m in insp.get("Mounts") or []}
    out += [f"mount missing live: {v['target']}" for v in svc.get("volumes") or [] if v.get("type") != "tmpfs" and v.get("target") not in dest]
    live_labels = cfg.get("Labels") or {}
    for k, v in sorted((svc.get("labels") or {}).items()):
        if k.startswith("com.docker.compose."):
            continue
        if k not in live_labels:
            out.append(f"label missing live: {k}")
        elif str(live_labels[k]) != str(v):
            out.append(f"label changed: {k}")
    return out


def parse_hashes(out):
    return dict(l.split(None, 1) for l in out.splitlines() if len(l.split(None, 1)) == 2)


def sh(cmd, env=None, timeout=TIMEOUT):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=timeout, env=env)


def docker_env():
    e = dict(os.environ)
    if os.environ.get("DRIFT_DOCKER_HOST"):
        e["DOCKER_HOST"] = os.environ["DRIFT_DOCKER_HOST"]
    return e


def collect_compose(runner=sh):
    env = docker_env()
    ids = runner(["docker", "ps", "-aq"], env).stdout.split()
    if not ids:
        raise OSError("docker ps returned no containers")
    insp = json.loads(runner(["docker", "inspect", *ids], env).stdout)
    by_dir = {}
    for d in insp:
        lab = (d.get("Config") or {}).get("Labels") or {}
        wd = lab.get("com.docker.compose.project.working_dir", "")
        if wd.startswith(str(REPO) + "/") and lab.get("com.docker.compose.oneoff") != "True":
            by_dir.setdefault((wd, lab.get("com.docker.compose.project.config_files", "")), []).append(d)
    items = []
    for (wd, files), conts in sorted(by_dir.items()):
        proj = wd[len(str(REPO / "Docker")) + 1:] if wd.startswith(str(REPO / "Docker") + "/") else wd
        cmd = ["docker", "compose", "--project-directory", wd, *[a for f in files.split(",") if f for a in ("-f", f)], "--profile", "*", "config", "--format", "json"]
        p = runner(cmd, env)
        if p.returncode != 0:
            items.append({"kind": "compose", "path": proj, "detail": "repo compose does not evaluate: " + (p.stderr.strip().splitlines() or ["?"])[-1][:160]})
            continue
        services = json.loads(p.stdout).get("services") or {}
        hp = runner(cmd[:-3] + ["config", "--hash", "*"], env)
        hashes = parse_hashes(hp.stdout) if hp.returncode == 0 else {}
        for d in conts:
            lab = d["Config"].get("Labels") or {}
            name = d.get("Name", "").lstrip("/")
            svc = services.get(lab.get("com.docker.compose.service", ""))
            if svc is None:
                items.append({"kind": "compose", "path": f"{proj}/{name}", "detail": "container is not a service of the repo compose"})
                continue
            items += [{"kind": "compose", "path": f"{proj}/{name}", "detail": r} for r in compose_diffs(svc, d)]
            want = hashes.get(lab.get("com.docker.compose.service", ""))
            if want and want != lab.get("com.docker.compose.config-hash"):
                items.append({"kind": "compose", "path": f"{proj}/{name}", "detail": "config-hash: repo compose changed since the container was created"})
    return items


# ---------- files: units, scripts, /etc ----------

def norm(name):
    return re.sub(r"\.(sh|py)$", "", name).replace("_", "-")


def install_map(text):
    """dest path -> source basename from `install ... "$X/src" /dest` lines and the SCRIPTS=( ) list of install-root-scripts.sh."""
    out = {}
    for m in re.finditer(r'\binstall\b[^\n]*?"?\$\{?\w+\}?/([\w./-]+)"?\s+(/[\w./-]+)', text):
        src, dest = m.groups()
        out[dest + src.rsplit("/", 1)[-1] if dest.endswith("/") else dest] = src.rsplit("/", 1)[-1]
    arr = re.search(r"^SCRIPTS=\(\n(.*?)^\)", text, re.M | re.S)
    dm = re.search(r"DEST=\$\{\w+:-([^}]+)\}", text)
    if arr and dm:
        for line in arr.group(1).split():
            out[f"{dm.group(1)}/{line.rsplit('/', 1)[-1]}"] = line.rsplit("/", 1)[-1]
    return out


def is_generated(path, head=""):
    """snapd, cloud-init and similar write their own units; they have no repo source by design."""
    return bool(re.match(r"snap[-.]", path.name)) or bool(re.search(r"auto-?generated|generated by|DO NOT EDIT", head, re.I))


def list_regular(root):
    """Regular text files under root (symlinks, ELF binaries, caches, backups and generated files are not drift candidates)."""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            p = Path(dirpath) / f
            if p.is_symlink() or not p.is_file() or ".bak" in f:
                continue
            try:
                with open(p, "rb") as fh:
                    head = fh.read(400)
            except OSError:
                continue
            if head[:4] == b"\x7fELF" or is_generated(p, head.decode(errors="replace")):
                continue
            out.append(p)
    return out


def check_file(live, candidates):
    """None when the live file equals a repo candidate; the reason otherwise."""
    if not candidates:
        return "no repo source"
    try:
        data = live.read_bytes()
    except OSError as e:
        raise OSError(f"cannot read {live}: {e}")
    cands = sorted(candidates, key=lambda c: c.parent.name != live.parent.name)
    for c in cands:
        if c.read_bytes() == data:
            return None
    return "differs from repo"


class RepoIndex:
    def __init__(self, repo=REPO, runner=sh):
        names = runner(["git", "-C", str(repo), "ls-files"]).stdout.split("\n")
        self.repo = repo
        self.by_name, self.by_norm = {}, {}
        for n in names:
            if n and not SKIP_REPO.search(n):
                p = repo / n
                self.by_name.setdefault(p.name, []).append(p)
                self.by_norm.setdefault(norm(p.name), []).append(p)
        self.install = {}
        for n in names:
            if n.endswith(".sh") and not SKIP_REPO.search(n) and (repo / n).is_file():
                try:
                    self.install.update(install_map((repo / n).read_text(errors="replace")))
                except OSError:
                    pass

    def candidates(self, live):
        c = list(self.by_name.get(live.name, []))
        src = self.install.get(str(live))
        if src:
            c += self.by_name.get(src, [])
        if not c and live.suffix in ("", ".sh", ".py"):
            c += self.by_norm.get(norm(live.name), [])
        return list(dict.fromkeys(c))

    def mentions(self, path, runner=sh):
        return runner(["git", "-C", str(self.repo), "grep", "-qF", "--", str(path)]).returncode == 0


def dpkg_owned(paths, runner=sh):
    if not paths:
        return set()
    out = runner(["dpkg", "-S", *map(str, paths)]).stdout
    owned = {m.group(1) for line in out.splitlines() if (m := re.match(r"^[^:]+(?::\w+)?: (/.+)$", line))}
    conf = runner(["dpkg-query", "-W", "-f", "${Conffiles}\\n"]).stdout
    return owned | {l.split()[0] for l in conf.splitlines() if l.strip()}


def collect_files(roots, kind, index, mention_ok, runner=sh):
    files = [p for r in roots if r.is_dir() for p in list_regular(r)]
    owned = dpkg_owned(files, runner)
    items = []
    for p in files:
        if str(p) in owned:
            continue
        cands = index.candidates(p)
        why = check_file(p, cands)
        if why == "no repo source" and mention_ok and index.mentions(p, runner):
            why = None
        if why:
            items.append({"kind": kind, "path": str(p), "detail": why})
    return items


# ---------- /etc effective values ----------

def effective_values(cat_config):
    out = {}
    sec = None
    for line in cat_config.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            sec = line.strip("[]")
        elif "=" in line and sec:
            k, v = line.split("=", 1)
            out.setdefault(sec, {})[k.strip()] = v.strip()
    return out


def span(v):
    """Seconds of a systemd time span (2min, 1m 30s), else None."""
    units = {"us": 1e-6, "ms": 1e-3, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400}
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]*)", v)
    if not parts or re.sub(r"(\d+(?:\.\d+)?)\s*([a-z]*)\s*", "", v) or any(u not in units and u != "" for _, u in parts):
        return None
    return sum(float(n) * units.get(u, 1) for n, u in parts)


def same_value(a, b):
    return a == b or (span(a) is not None and span(a) == span(b))


def overridden(file_lines, eff):
    out = []
    for line in file_lines:
        k, _, v = line.partition("=")
        if k.strip() in eff and not same_value(eff[k.strip()], v.strip()):
            out.append(f"{k.strip()}: file {v.strip()}, effective {eff[k.strip()]}")
    return out


def sysctl_diffs(text, live):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;" or "=" not in line:
            continue
        k, v = (s.strip() for s in line.split("=", 1))
        if k in live and " ".join(live[k].split()) != " ".join(v.split()):
            out.append(f"{k}: file {v}, live {live[k]}")
    return out


def collect_effective(runner=sh):
    items = []
    for d in sorted(Path("/etc/systemd").glob("*.conf.d")):
        base = "systemd/" + d.name[:-2]
        cat = runner(["systemd-analyze", "cat-config", base])
        if cat.returncode != 0:
            continue
        eff = effective_values(cat.stdout)
        for f in list_regular(d):
            sec = None
            for line in f.read_text(errors="replace").splitlines():
                line = line.strip()
                if line.startswith("["):
                    sec = line.strip("[]")
                elif "=" in line and not line.startswith("#") and sec in eff:
                    items += [{"kind": "etc", "path": str(f), "detail": "overridden by a later file: " + r} for r in overridden([line], eff[sec])]
    live = {}
    keys = []
    files = list_regular(Path("/etc/sysctl.d")) if Path("/etc/sysctl.d").is_dir() else []
    owned = dpkg_owned(files, runner)
    for f in files:
        if str(f) in owned:
            continue
        for line in f.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and line[0] not in "#;" and "=" in line:
                keys.append(line.split("=", 1)[0].strip())
    for k in dict.fromkeys(keys):
        try:
            live[k] = Path("/proc/sys", *k.split(".")).read_text().strip()
        except OSError:
            pass
    for f in files:
        if str(f) not in owned:
            items += [{"kind": "etc", "path": str(f), "detail": "sysctl " + r} for r in sysctl_diffs(f.read_text(errors="replace"), live)]
    return items


# ---------- worktree ----------

def parse_porcelain(out):
    items = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if code == "??":
            items.append(("untracked", path))
        elif "R" in code:
            items.append(("renamed", path.split(" -> ")[-1]))
        elif "D" in code:
            items.append(("deleted", path))
        else:
            items.append(("modified", path))
    return items


def collect_worktree(runner=sh):
    p = runner(["git", "--no-optional-locks", "-C", str(REPO), "status", "--porcelain=v1", "--untracked-files=normal"])
    if p.returncode != 0:
        raise OSError("git status failed")
    return [{"kind": "worktree", "path": path, "detail": what} for what, path in parse_porcelain(p.stdout)]


# ---------- output ----------

def counts(items):
    c = dict.fromkeys(KINDS, 0)
    for i in items:
        c[i["kind"]] += 1
    return c


def render_prom(host, cnt, errors, now, help_text=True):
    h = (lambda n, t, d: f"# HELP {n} {d}\n# TYPE {n} {t}\n") if help_text else (lambda n, t, d: "")
    out = h("drift_items", "gauge", "Things that differ between the repo and this host, by kind (compose, unit, script, etc, worktree).")
    out += "".join(f'drift_items{{host="{host}",kind="{k}"}} {cnt.get(k, 0)}\n' for k in KINDS)
    out += h("drift_collect_errors", "gauge", "1 if the last collection of that kind failed (its items are the previous ones).")
    out += "".join(f'drift_collect_errors{{host="{host}",kind="{k}"}} {1 if k in errors else 0}\n' for k in KINDS)
    out += h("drift_last_run_timestamp_seconds", "gauge", "Last run of the drift check.")
    out += f'drift_last_run_timestamp_seconds{{host="{host}"}} {now:.0f}\n'
    return out


def render_json(host, items, now):
    return json.dumps({"host": host, "updated": int(now), "items": items}, indent=1) + "\n"


def merge_previous(items, failed, prev):
    return items + [i for i in (prev or {}).get("items", []) if i["kind"] in failed]


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--prom", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--no-help", action="store_true", help="no HELP/TYPE lines: both hosts write the same families and node-exporter rejects the merge when only one file has them")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args(argv)
    try:
        prev = json.loads(Path(a.json).read_text())
    except (OSError, ValueError):
        prev = None
    index = RepoIndex()
    jobs = {
        "compose": collect_compose,
        "unit": lambda: collect_files(UNIT_DIRS, "unit", index, False),
        "script": lambda: collect_files([*SCRIPT_DIRS, *[p for p in Path("/usr/local/lib").glob("*") if p.is_dir()]], "script", index, False),
        "etc": lambda: collect_files(ETC_DIRS + [p for p in Path("/etc/systemd").glob("*.conf.d")], "etc", index, True) + collect_effective(),
        "worktree": collect_worktree,
    }
    items, failed = [], []
    for kind, fn in jobs.items():
        try:
            items += fn()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
            print(f"ERROR {kind}: {e}", file=sys.stderr)
            failed.append(kind)
    items = merge_previous(items, failed, prev)
    now = time.time()
    c = counts(items)
    if a.print:
        print(render_prom(a.host, c, failed, now, not a.no_help), end="")
    else:
        atomic_write(a.json, render_json(a.host, items, now))
        atomic_write(a.prom, render_prom(a.host, c, failed, now, not a.no_help))
    for i in items:
        print(f"{a.host} {i['kind']} {i['path']}: {i['detail']}")
    print(f"drift: " + ", ".join(f"{k} {c[k]}" for k in KINDS) + (f"; errors: {' '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
