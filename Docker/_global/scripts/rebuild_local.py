#!/usr/bin/env python3
"""Weekly rebuild of the locally built images (Docker/_global/rebuild-local.conf), keeping our Dockerfiles and patches.

Per due entry, one at a time:
  1. digest-pinned FROM (A-30): set the pin to the tag's current digest (the documented update rule); other tags float within their pin
  2. build --pull --no-cache (fresh base and packages); a build failure changes nothing
  3. fingerprint (base ids + installed packages + build context): unchanged -> old image kept, nothing restarts
  4. the project's tests against the new image (unittest inside it, and/or a host command with $IMAGE)
  5. update-project.sh recreate: container checks, CHECK, post-update check, automatic rollback to the previous image (kept as rollback.invalid/<repo>:<date>-<project>)
  6. success with a moved pin: the Dockerfile change is committed (only that file, never pushed)
Any failure: old image back on its tag, the Dockerfile pin reverted, Telegram via oob-alert.sh.

The same file runs on fastpi and beefy (host differences from the environment and the script's own location).

Usage: rebuild_local.py [--dry-run] [project ...]   (named projects run whatever the weekday)
Env: REBUILD_CONF / REBUILD_STATE / REBUILD_TEXTFILE (paths), REBUILD_HOST (label, default short hostname),
     REBUILD_DEADLINE (HH:MM local: no new entry starts in the two hours from then), TEST_FORCE_FAIL=1 (update-project.sh rollback drill),
     REBUILD_FORCE=1 (recreate even when the fingerprint is unchanged)
Conf key guard: shell command run before the build and again before the recreate; non-zero = deferred to next week (no alert), e.g. someone is playing
"""
import fcntl
import hashlib
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCKER = REPO / "Docker"
SCRIPTS = DOCKER / "_global" / "scripts"
CONF = Path(os.environ.get("REBUILD_CONF", DOCKER / "_global" / "rebuild-local.conf"))
STATE = Path(os.environ.get("REBUILD_STATE", "/var/lib/rebuild-local"))
TEXTFILE = Path(os.environ.get("REBUILD_TEXTFILE", "/var/lib/node-exporter/textfile"))
HOST = os.environ.get("REBUILD_HOST") or socket.gethostname().split(".")[0]
DEADLINE = os.environ.get("REBUILD_DEADLINE", "")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
KEYS = {"project": "", "day": "", "services": [], "build": "", "context": "", "test": "", "unittest": "", "check": "", "backups": [], "guard": ""}
MIN_AVAILABLE_MB = int(os.environ.get("REBUILD_MIN_AVAILABLE_MB", "1200"))
MIN_DISK_GB = 15
FAILED = ("build_failed", "test_failed", "rolled_back", "rollback_failed", "error", "skipped")
# apk / dpkg package lists and Python distributions: what a rebuild can change besides the base and our files
PACKAGES_SH = ("(grep -E '^[PV]:' /lib/apk/db/installed 2>/dev/null; dpkg-query -W 2>/dev/null; "
               "find / -xdev \\( -name '*.dist-info' -o -name '*.egg-info' \\) 2>/dev/null | sort) | sort")


def parse_conf(text: str) -> list[dict]:
    rows = []
    for item in tomllib.loads(text).get("image", []):
        extra = set(item) - set(KEYS)
        if extra:
            raise ValueError(f"unknown keys {sorted(extra)} in {item.get('project')}")
        if not item.get("project"):
            raise ValueError("entry without project")
        row = {**KEYS, **item}
        row["day"] = row["day"][:1].upper() + row["day"][1:].lower()
        if row["day"] not in DAYS:
            raise ValueError(f"bad day {item.get('day')!r} for {row['project']}")
        rows.append(row)
    return rows


def guard_reason(cmd: str, cwd: Path) -> str:
    """'' when the guard passes (or there is none), else why it did not (its last output line)."""
    if not cmd:
        return ""
    r = sh(["bash", "-c", cmd], cwd=cwd, timeout=1800)
    if r.returncode == 0:
        return ""
    lines = (r.stdout + r.stderr).strip().splitlines()
    return lines[-1][-200:] if lines else f"guard failed (rc={r.returncode})"


def due(row: dict, today: str, only: set) -> bool:
    return row["project"] in only if only else row["day"] == today


def project_dir(project: str) -> Path:
    return Path(project) if project.startswith("/") else DOCKER / project


def project_name(project: str) -> str:
    return Path(project).name if project.startswith("/") else project.split("/", 1)[0]


def from_refs(dockerfile: str) -> list[str]:
    """Base images of a Dockerfile, in order, without stage names, scratch and duplicates."""
    refs, stages = [], set()
    for line in dockerfile.splitlines():
        words = line.split()
        if not words or words[0].upper() != "FROM":
            continue
        words = [w for w in words[1:] if not w.startswith("--")]
        if not words:
            continue
        ref = words[0]
        if len(words) >= 3 and words[1].upper() == "AS":
            stages.add(words[2].lower())
        if ref.lower() in stages or ref == "scratch" or ref in refs:
            continue
        refs.append(ref)
    return refs


def pinned_tags(dockerfile: str) -> set[str]:
    """name:tag of every digest-pinned base; a digest-only ref has no tag to follow."""
    names = {r.split("@", 1)[0] for r in from_refs(dockerfile) if "@sha256:" in r}
    return {n for n in names if ":" in n.rsplit("/", 1)[-1]}


def bump_pins(dockerfile: str, current: dict[str, str]) -> tuple[str, list[str]]:
    """Replace name:tag@sha256:old by the tag's current digest. current: name:tag -> digest ('' = unknown, left alone)."""
    bumped = []

    def fix(m):
        tag, old = m.group(1), m.group(2)
        new = current.get(tag, "")
        if not new or new == old:
            return m.group(0)
        if tag not in bumped:
            bumped.append(tag)
        return f"{tag}@{new}"

    out = re.sub(r"(?<=\s)([^\s@]+:[^\s@]+)@(sha256:[0-9a-f]+)", fix, dockerfile)
    return out, bumped


def fingerprint(env: list[str], packages: str, context: str) -> str:
    """Image Env (carries the base's PYTHON_VERSION, JAVA_VERSION, ...), installed packages, build context. '' when the package list could not be read: then the image counts as changed."""
    if not packages.strip():
        return ""
    return hashlib.sha256("\n--\n".join(["\n".join(sorted(env)), packages, context]).encode()).hexdigest()[:16]


def image_env(images: dict[str, str], first: str) -> dict[str, str]:
    env = {"IMAGE": images.get(first, "")}
    for svc, img in images.items():
        env["IMAGE_" + re.sub(r"[^A-Za-z0-9]", "_", svc).upper()] = img
    return env


def update_result(rc: int) -> str:
    return {0: "updated", 1: "rolled_back", 2: "error"}.get(rc, "rollback_failed")


def commit_message(name: str, bumped: list[str]) -> str:
    return f"{name}: bump {', '.join(bumped)} base digest (weekly rebuild)"


def alert_message(host: str, project: str, result: str, reason: str) -> str:
    icon = "↩️" if result == "rolled_back" else "🔴"
    what = {"build_failed": "build failed, nothing changed", "test_failed": "tests failed on the new image, nothing changed",
            "rolled_back": "new image failed its checks, rolled back to the previous one",
            "rollback_failed": "new image failed and the rollback did not verify either: check now",
            "error": "error, nothing changed", "skipped": "skipped"}.get(result, result)
    return (f"{icon} <b>Rebuild: {html.escape(project)} on {html.escape(host)}</b>\n\n"
            f"• {html.escape(what)} ({result})"
            + (f"\n• {html.escape(reason)}" if reason else "")
            + f"\n<blockquote expandable>💡 Log: <code>journalctl -u rebuild-local -n 80</code></blockquote>")


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_prom(state: dict, now: float, host: str = HOST) -> str:
    """rebuild_local_result (last result per project), rebuild_local_last_ok_timestamp_seconds (last updated/unchanged), rebuild_local_last_run_timestamp_seconds. No HELP lines (fastpi republishes beefy's file)."""
    h = f'host="{_esc(host)}"'
    lines = ["# TYPE rebuild_local_result gauge"]
    for p, s in sorted(state.items()):
        lines.append(f'rebuild_local_result{{{h},project="{_esc(p)}",result="{_esc(s.get("result", ""))}",reason="{_esc(s.get("reason", ""))}"}} 1')
    lines.append("# TYPE rebuild_local_last_ok_timestamp_seconds gauge")
    for p, s in sorted(state.items()):
        lines.append(f'rebuild_local_last_ok_timestamp_seconds{{{h},project="{_esc(p)}"}} {int(s.get("ok_time", 0))}')
    lines += ["# TYPE rebuild_local_last_run_timestamp_seconds gauge", f"rebuild_local_last_run_timestamp_seconds{{{h}}} {int(now)}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- I/O

_docker: list[str] = []


def sh(cmd, timeout: int = 600, **kw) -> subprocess.CompletedProcess:
    if isinstance(cmd, list) and cmd[0] == "docker":
        if not _docker:
            ok = subprocess.run(["docker", "info"], capture_output=True).returncode == 0
            _docker.extend(["docker"] if ok else ["sudo", "-n", "docker"])
        cmd = _docker + cmd[1:]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or "", f"timeout after {timeout}s")


def log(msg: str) -> None:
    print(time.strftime("%F %T"), msg, flush=True)


def resources_ok() -> str:
    avail = int(sh(["free", "-m"]).stdout.splitlines()[1].split()[6])
    disk = shutil.disk_usage("/").free // 2**30
    if avail < MIN_AVAILABLE_MB or disk < MIN_DISK_GB:
        return f"resource floor: {avail} MB available, {disk} GB disk free"
    return ""


def past_deadline(hhmm: str, now: time.struct_time) -> bool:
    if not hhmm:
        return False
    h, m = (int(x) for x in hhmm.split(":"))
    start = h * 60 + m
    cur = now.tm_hour * 60 + now.tm_min
    return 0 <= (cur - start) % 1440 < 120


def compose_config(d: Path) -> dict:
    r = sh(["docker", "compose", "config", "--format", "json"], cwd=d)
    return json.loads(r.stdout) if r.returncode == 0 else {}


def running(d: Path, services: list[str]) -> dict[str, tuple[str, str]]:
    """service -> (image ref, image id) of the running containers."""
    out = {}
    for svc in services:
        cid = sh(["docker", "compose", "ps", "-q", svc], cwd=d).stdout.split()
        if cid:
            ref, img = sh(["docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}", cid[0]]).stdout.split()
            out[svc] = (ref, img)
    return out


def dockerfiles(row: dict, config: dict, services: list[str]) -> list[Path]:
    if row["context"]:
        return [Path(row["context"]) / "Dockerfile"]
    files = []
    for svc in services:
        b = (config.get("services", {}).get(svc) or {}).get("build") or {}
        if b:
            f = Path(b.get("context", ".")) / b.get("dockerfile", "Dockerfile")
            if f not in files:
                files.append(f)
    return files


def tag_digest(tag: str) -> str:
    time.sleep(2)
    r = sh(["docker", "buildx", "imagetools", "inspect", tag, "--format", "{{json .Manifest}}"], timeout=120)
    if r.returncode != 0:
        log(f"  {tag}: registry: {r.stderr.strip()[-160:]}")
        return ""
    return json.loads(r.stdout).get("digest", "")


def git(path: Path, *args) -> subprocess.CompletedProcess:
    return sh(["git", "-C", str(path.parent), *args])


def context_listing(files: list[Path]) -> str:
    """Tracked files of each build context plus their uncommitted changes."""
    out = []
    for f in files:
        out.append(git(f, "ls-files", "-s", ".").stdout)
        out.append(git(f, "diff", "HEAD", "--", ".").stdout)
    return "\n".join(out)


def parse_env(out: str) -> list | None:
    try:
        env = json.loads(out)
    except ValueError:
        return None
    return env if isinstance(env, list) else None


def rollback_tag(ref: str, proj: str, date: str) -> str:
    """Same name update-project.sh gives the image it replaces."""
    repo = ref.split("@", 1)[0]
    if ":" in repo.rsplit("/", 1)[-1]:
        repo = repo.rsplit(":", 1)[0]
    return f"rollback.invalid/{repo.replace(':', '-')}:{date}-{proj}"


def image_fingerprint(image: str, context: str) -> str:
    env = parse_env(sh(["docker", "image", "inspect", "-f", "{{json .Config.Env}}", image]).stdout)
    if env is None:
        return ""
    r = sh(["docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh", image, "-c", PACKAGES_SH], timeout=180)
    return fingerprint(env, r.stdout if r.returncode == 0 else "", context)


def oob(msg: str) -> None:
    script = SCRIPTS / "oob-alert.sh"
    if script.exists():
        r = sh([str(script), msg], timeout=60)
        if r.returncode != 0:
            log(f"  alert not sent: {r.stderr.strip()[-160:]}")
    else:
        log("  no oob-alert.sh on this host")


def retag(old: dict[str, tuple[str, str]]) -> None:
    for ref, img in old.values():
        if "@" not in ref and not ref.startswith("sha256:"):
            sh(["docker", "tag", img, ref])


def rebuild(row: dict, entry: dict, dry: bool) -> tuple[str, str]:
    """-> (result, reason). result: updated | unchanged | deferred | build_failed | test_failed | rolled_back | rollback_failed | error | skipped."""
    d = project_dir(row["project"])
    config = compose_config(d)
    if not config:
        return "error", "docker compose config failed"
    services = row["services"] or [s for s, v in config.get("services", {}).items() if v.get("build")]
    old = running(d, services)
    if not old:
        return "error", "no running container"
    files = dockerfiles(row, config, services)
    pins: dict[Path, tuple[str, list[str]]] = {}
    for f in files:
        text = f.read_text()
        tags = pinned_tags(text)
        if not tags:
            continue
        dirty = git(f, "status", "--porcelain", "--", f.name).stdout.strip()
        if dirty:
            log(f"  {f}: uncommitted changes, pin left as it is")
            continue
        new, bumped = bump_pins(text, {t: tag_digest(t) for t in sorted(tags)})
        if bumped:
            pins[f] = (text, bumped)
            log(f"  {f.name}: pin moves for {', '.join(bumped)}")
            if not dry:
                f.write_text(new)
    log("  before: " + ", ".join(f"{s}={r} {i[7:19]}" for s, (r, i) in old.items()))
    if (busy := guard_reason(row["guard"], d)):
        for f, (text, _) in pins.items():
            if not dry:
                f.write_text(text)
        return "deferred", busy
    if dry:
        return "dry_run", ""

    def revert_pins():
        for f, (text, _) in pins.items():
            f.write_text(text)

    if (floor := resources_ok()):
        revert_pins()
        return "skipped", floor
    gone = [s for s, (_, img) in old.items() if sh(["docker", "image", "inspect", img]).returncode != 0]
    if gone:
        revert_pins()
        return "error", f"running image of {', '.join(gone)} is no longer in the image store, no rollback possible: recreate by hand"
    # containerd image store: the old image vanishes once the build moves its tag, so pin it first
    date = time.strftime("%Y-%m-%d")
    for ref, img in old.values():
        if not ref.startswith("sha256:"):
            sh(["docker", "tag", img, rollback_tag(ref, config.get("name", d.name.lower()), date)])
    cmd = row["build"] or "docker compose build --pull --no-cache " + " ".join(services)
    t0 = time.time()
    r = sh(["bash", "-c", cmd], cwd=d, timeout=5400, env=dict(os.environ, DOCKER_BUILDKIT="1"))
    log(f"  build: rc={r.returncode} in {int(time.time() - t0)} s")
    if r.returncode != 0:
        for line in (r.stdout + r.stderr).strip().splitlines()[-8:]:
            log("  | " + line)
        retag(old)
        revert_pins()
        return "build_failed", (r.stderr.strip().splitlines() or ["?"])[-1][-200:]
    new = {s: sh(["docker", "image", "inspect", "-f", "{{.Id}}", ref]).stdout.strip() for s, (ref, _) in old.items()}
    ctx = context_listing(files)
    prints = entry.setdefault("fingerprints", {})
    changed = []
    for s, (ref, img) in old.items():
        fp_new = image_fingerprint(new[s], ctx)
        # first run: the running image is taken as built from the committed context
        fp_old = prints.get(img) or image_fingerprint(img, ctx)
        log(f"  {s}: fingerprint {fp_old or '?'} -> {fp_new or '?'}")
        if new[s] != img and (not fp_new or fp_new != fp_old):
            changed.append(s)
        prints[new[s]] = fp_new
    if not changed and not pins and os.environ.get("REBUILD_FORCE") != "1":
        retag(old)
        for s in old:
            if new[s] != old[s][1]:
                sh(["docker", "image", "rm", new[s]])
                prints.pop(new[s], None)
        return "unchanged", ""
    env = dict(os.environ, **image_env(new, next(iter(old))))
    if row["unittest"]:
        r = sh(["docker", "run", "--rm", "--network", "none", "--user", "65534:65534", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint", "sh", "-v", f"{d}:/src:ro", "-w", "/src", env["IMAGE"], "-c", row["unittest"]], timeout=900)
        log(f"  unittest: rc={r.returncode} {(r.stdout + r.stderr).strip().splitlines()[-1:] or ''}")
        if r.returncode != 0:
            retag(old)
            revert_pins()
            return "test_failed", (r.stdout + r.stderr).strip().splitlines()[-1][-200:] if (r.stdout + r.stderr).strip() else "unittest failed"
    if row["test"]:
        r = sh(["bash", "-c", row["test"]], cwd=d, env=env, timeout=900)
        log(f"  test: rc={r.returncode}")
        if r.returncode != 0:
            retag(old)
            revert_pins()
            return "test_failed", ((r.stdout + r.stderr).strip().splitlines() or ["test failed"])[-1][-200:]
    if (floor := resources_ok()):
        retag(old)
        revert_pins()
        return "skipped", floor
    if (busy := guard_reason(row["guard"], d)):
        retag(old)
        revert_pins()
        return "deferred", busy
    uenv = dict(os.environ, SERVICES=" ".join(old), CHECK=row["check"])
    r = subprocess.run([str(SCRIPTS / "update-project.sh"), str(d), "recreate", *row["backups"]],
                       env=uenv, text=True, capture_output=True, timeout=2700)
    for line in r.stdout.splitlines():
        log("  | " + line)
    res = update_result(r.returncode)
    if res != "updated":
        revert_pins()
        why = next((ln.strip() for ln in r.stdout.splitlines() if ": FAIL" in ln), "containers not running" if res != "error" else "")
        return res, why
    for f, (_, bumped) in pins.items():
        name = project_name(row["project"])
        c = git(f, "commit", "-q", "-m", commit_message(name, bumped), "--", f.name)
        log(f"  commit {f.name}: {'ok' if c.returncode == 0 else 'FAILED ' + c.stderr.strip()[-200:]}")
        if c.returncode != 0:
            return "updated", "pin bump not committed: " + c.stderr.strip()[-120:]
    return "updated", ""


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    only = {a for a in argv if not a.startswith("--")}
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another run is active")
        return 0
    state_path = STATE / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    rows = parse_conf(CONF.read_text())
    today = DAYS[time.localtime().tm_wday]
    worst = 0
    for row in rows:
        if not due(row, today, only):
            continue
        log(f"== {row['project']} ({row['day']})")
        if past_deadline(DEADLINE, time.localtime()):
            log(f"  deferred: past {DEADLINE}, next week")
            continue
        entry = state.setdefault(row["project"], {})
        try:
            res, reason = rebuild(row, entry, dry)
        except Exception as e:  # noqa: BLE001
            res, reason = "error", f"{type(e).__name__}: {e}"[:200]
        log(f"  result: {res}{' (' + reason + ')' if reason else ''}")
        if dry:
            continue
        entry.update(result=res, reason=reason, time=time.time())
        if res in ("updated", "unchanged"):
            entry["ok_time"] = time.time()
            # keep the fingerprints of images that still exist
            entry["fingerprints"] = {k: v for k, v in entry.get("fingerprints", {}).items()
                                     if sh(["docker", "image", "inspect", k]).returncode == 0}
        if res in FAILED or reason.startswith("pin bump not committed"):
            oob(("🧪 drill (TEST_FORCE_FAIL)\n" if os.environ.get("TEST_FORCE_FAIL") == "1" else "") + alert_message(HOST, row["project"], res, reason))
            worst = max(worst, 2 if res == "rollback_failed" else 1)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        os.replace(tmp, state_path)
    if dry:
        return 0
    TEXTFILE.mkdir(parents=True, exist_ok=True)
    tmp = TEXTFILE / ".rebuild_local.prom.tmp"
    known = {r["project"] for r in rows}
    tmp.write_text(render_prom({p: s for p, s in state.items() if p in known}, time.time()))
    os.chmod(tmp, 0o644)
    os.replace(tmp, TEXTFILE / "rebuild_local.prom")
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
