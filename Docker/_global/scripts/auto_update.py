#!/usr/bin/env python3
"""Nightly image auto-update by tier, on top of update-project.sh (backup, recreate, verify, rollback).

Tiers (Docker/_global/auto-update.conf):
  auto       new digest of the tag in the code -> applied after COOLDOWN_HOURS, verified, rolled back on failure
  notify     stateful / DB-migrating: reported with the exact command, never applied
  edge       Traefik, Authentik, CrowdSec, AdGuard, cloudflared, WireGuard: like notify
  edge-auto  like auto, for the edge once the owner switched it on (see auto-updates.md)
A rollback that does not verify pages out of band (oob-alert.sh), past the edge it may have broken.

Never crosses a major version: when the image's org.opencontainers.image.version major changes, the old image is re-tagged and the update is reported instead.
Only the digest behind the tag written in the repo moves; pinned version bumps stay with Cup + the weekly digest.

The same file runs on fastpi and beefy; host differences come from the environment (systemd unit) and the script's own location.

Usage: auto_update.py [--dry-run] [project ...]
Env: COOLDOWN_HOURS (24), TEST_FORCE_FAIL=1 (first functional check fails: rollback drill),
     AUTO_UPDATE_HOST (host label, default the short hostname), AUTO_UPDATE_APPLY_BY (HH:MM local: no registry check or update starts later, the rest is "deferred"),
     AUTO_UPDATE_MAX_APPLY (projects updated per run, default no limit), AUTO_UPDATE_CONF / _STATE / _TEXTFILE (paths)
"""
import fcntl
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCKER = REPO / "Docker"
SCRIPTS = DOCKER / "_global" / "scripts"
CONF = Path(os.environ.get("AUTO_UPDATE_CONF", DOCKER / "_global" / "auto-update.conf"))
STATE = Path(os.environ.get("AUTO_UPDATE_STATE", "/var/lib/auto-update"))
TEXTFILE = Path(os.environ.get("AUTO_UPDATE_TEXTFILE", "/var/lib/node-exporter/textfile"))
HOST = os.environ.get("AUTO_UPDATE_HOST") or socket.gethostname().split(".")[0]
ARCH = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
APPLY_BY = os.environ.get("AUTO_UPDATE_APPLY_BY", "")
MAX_APPLY = int(os.environ.get("AUTO_UPDATE_MAX_APPLY", "0") or 0)
TIERS = ("auto", "notify", "edge", "edge-auto")
APPLYING = ("auto", "edge-auto")
MIN_AVAILABLE_MB = 1200
MIN_DISK_GB = 20
# Docker Hub answers 429 to bursts (and allows 100 manifest requests an hour per account/IP); one request every 3 s keeps a run of ~40 refs far below both.
PACE_S = float(os.environ.get("AUTO_UPDATE_PACE_S", "3"))


def parse_conf(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        tier, project, services, backups, check = (line.split(None, 4) + ["-"] * 5)[:5]
        if tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r} for {project}")
        lst = lambda s: [] if s == "-" else s.split(",")  # noqa: E731
        rows.append({"tier": tier, "project": project, "services": lst(services),
                     "backups": lst(backups), "check": "" if check.strip() == "-" else check.strip()})
    return rows


def decide(tier: str, remote: str, running: list[str], entry: dict, now: float, cooldown_h: float) -> tuple[str, dict]:
    """-> (current|waiting|apply|available|held_back|error, new state entry for this ref)."""
    if not remote:
        return "error", entry
    if any(r.endswith("@" + remote) for r in running):
        return "current", {}
    if entry.get("digest") != remote:
        entry = {"digest": remote, "first_seen": now}
    if entry.get("bad"):
        return "held_back", entry
    if tier not in APPLYING:
        return "available", entry
    return ("apply" if now - entry["first_seen"] >= cooldown_h * 3600 else "waiting"), entry


def _major(v: str) -> str:
    head = v.lstrip("vV").split(".", 1)[0]
    return head if head.isdigit() else ""


def crosses_major(old: str, new: str) -> bool:
    a, b = _major(old), _major(new)
    return bool(a and b and a != b)


def past_deadline(hhmm: str, now: time.struct_time) -> bool:
    """True for two hours from HH:MM, so a run by hand at another time of day is not cut short."""
    if not hhmm:
        return False
    h, m = (int(x) for x in hhmm.split(":"))
    return (h, m) <= (now.tm_hour, now.tm_min) < (h + 2, 0)


def build_refs(config: dict) -> set[str]:
    """Images the project builds itself: never looked up in a registry (with containerd's image store they have RepoDigests too)."""
    return {s["image"] for s in (config.get("services") or {}).values() if "build" in s and s.get("image")}


def pick_digest(manifest: dict, running: list[str], arch: str) -> str:
    plat = [x["digest"] for x in manifest.get("manifests") or []
            if (x.get("platform") or {}).get("os") == "linux" and (x.get("platform") or {}).get("architecture") == arch]
    hit = [d for d in plat if any(x.endswith("@" + d) for x in running)]
    return hit[0] if hit else manifest.get("digest", "")


def update_command(row: dict) -> str:
    svc = f"SERVICES='{' '.join(row['services'])}' " if row["services"] else ""
    tail = "".join(f" {b}" for b in row["backups"])
    return f"{svc}{SCRIPTS}/update-project.sh {DOCKER / row['project']} pull{tail}"


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _short(d: str) -> str:
    return d.split(":", 1)[-1][:12]


QUIET = ("current", "local_build", "waiting")
PRIORITY = ["rollback_failed", "rolled_back", "error", "major_held", "held_back", "skipped", "updated", "available", "deferred"]
SUMMARY_BUDGET = 3000


def _rank(r: dict) -> int:
    return PRIORITY.index(r["result"]) if r["result"] in PRIORITY else len(PRIORITY)


def render_prom(results: list[dict], now: float, host: str = HOST) -> str:
    """auto_update_result (one per image, result = current|waiting|updated|rolled_back|rollback_failed|available|major_held|held_back|skipped|deferred|local_build|error), auto_update_omitted (noteworthy results left out to keep the Telegram summary under 4096 characters), auto_update_last_run_timestamp_seconds.

    No HELP lines: fastpi republishes beefy's file next to its own, and node-exporter drops a family whose files disagree on HELP (one with, one without, or two versions).
    """
    h = f'host="{_esc(host)}"'
    lines = ["# TYPE auto_update_result gauge"]
    used, omitted, keep = 0, 0, []
    for r in sorted(results, key=_rank):
        if r["result"] in QUIET or r["result"] == "rollback_failed":
            keep.append(r)
            continue
        cost = len(r["project"]) + len(r["ref"]) + len(r["result"]) + len(r.get("command", "")) + 60
        if used + cost > SUMMARY_BUDGET:
            omitted += 1
            continue
        used += cost
        keep.append(r)
    keep_ids = {id(r) for r in keep}
    for r in (r for r in results if id(r) in keep_ids):
        labels = {"host": host, "project": r["project"], "tier": r["tier"], "ref": r["ref"], "result": r["result"],
                  "from": _short(r.get("from", "")), "to": _short(r.get("to", ""))}
        if r.get("command"):
            labels["command"] = r["command"]
        lines.append("auto_update_result{" + ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items()) + "} 1")
    lines += [
        "# TYPE auto_update_omitted gauge",
        f"auto_update_omitted{{{h}}} {omitted}",
    ]
    lines += [
        "# TYPE auto_update_last_run_timestamp_seconds gauge",
        f"auto_update_last_run_timestamp_seconds{{{h}}} {int(now)}",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- I/O

_docker: list[str] = []


def docker_cmd() -> list[str]:
    """docker, or sudo -n docker for a user outside the docker group (beefy's buntu by hand; its service gets the group)."""
    if not _docker:
        ok = subprocess.run(["docker", "info"], capture_output=True).returncode == 0
        _docker.extend(["docker"] if ok else ["sudo", "-n", "docker"])
    return _docker


def sh(cmd: list[str], timeout: int = 600, **kw) -> subprocess.CompletedProcess:
    if cmd[0] == "docker":
        cmd = docker_cmd() + cmd[1:]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def log(msg: str) -> None:
    print(time.strftime("%F %T"), msg, flush=True)


def resources_ok() -> str:
    avail = int(sh(["free", "-m"]).stdout.splitlines()[1].split()[6])
    disk = shutil.disk_usage("/").free // 2**30
    if avail < MIN_AVAILABLE_MB or disk < MIN_DISK_GB:
        return f"resource floor: {avail} MB available, {disk} GB disk free"
    return ""


def project_images(row: dict) -> dict[str, str]:
    """ref -> running image id for the project's (selected) containers."""
    d = DOCKER / row["project"]
    ids = sh(["docker", "compose", "--project-directory", str(d), "ps", "-q", *row["services"]], cwd=d).stdout.split()
    out = {}
    for cid in ids:
        ref, img = sh(["docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}", cid]).stdout.split()
        out[ref] = img
    return out


def local_builds(row: dict) -> set[str]:
    d = DOCKER / row["project"]
    r = sh(["docker", "compose", "--project-directory", str(d), "config", "--format", "json"], cwd=d)
    return build_refs(json.loads(r.stdout)) if r.returncode == 0 else set()


def repo_digests(image_id: str) -> list[str]:
    r = sh(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", image_id])
    return json.loads(r.stdout or "[]") or []


def remote_digest(ref: str, running: list[str]) -> str:
    """Digest the tag points at now: the index, or this host's platform manifest when that is what the running image was pulled by."""
    for attempt in range(2):
        time.sleep(PACE_S)
        r = sh(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"], timeout=120)
        if r.returncode == 0 or "429" not in r.stderr or attempt:
            break
        time.sleep(90)
    if r.returncode != 0:
        log(f"  {ref}: registry: {r.stderr.strip()[-160:]}")
        return ""
    return pick_digest(json.loads(r.stdout), running, ARCH)


def image_version(image: str) -> str:
    r = sh(["docker", "image", "inspect", "-f", '{{index .Config.Labels "org.opencontainers.image.version"}}', image])
    v = r.stdout.strip()
    return "" if v == "<no value>" else v


def oob(msg: str) -> None:
    script = SCRIPTS / "oob-alert.sh"
    if not script.exists():
        log("  no oob-alert.sh on this host: the AutoUpdateRollbackFailed alert is the page")
        return
    sh([str(script), msg], timeout=60)


def apply(row: dict, refs: list[str], images: dict[str, str]) -> dict[str, str]:
    """Pull, refuse a major jump, then update-project.sh recreate with the functional check. -> ref -> result."""
    d = DOCKER / row["project"]
    services = row["services"]
    r = sh(["docker", "compose", "pull", "-q", *services], cwd=d, timeout=1800)
    if r.returncode != 0:
        log(f"  pull failed: {r.stderr.strip()[-300:]}")
        return {ref: "error" for ref in refs}
    out, changed = {}, False
    for ref in refs:
        old_id = images[ref]
        new_id = sh(["docker", "image", "inspect", "-f", "{{.Id}}", ref]).stdout.strip()
        if new_id == old_id:
            out[ref] = "current"
            continue
        ov, nv = image_version(old_id), image_version(new_id)
        if crosses_major(ov, nv):
            sh(["docker", "tag", old_id, ref])
            log(f"  {ref}: {ov} -> {nv} is a major jump; old image re-tagged, not applied")
            out[ref] = "major_held"
            continue
        out[ref] = "pending"
        changed = True
    if not changed:
        return out
    env = dict(os.environ, SERVICES=" ".join(services), CHECK=row["check"])
    r = subprocess.run([str(SCRIPTS / "update-project.sh"), str(d), "recreate", *row["backups"]],
                       env=env, text=True, capture_output=True, timeout=1800)
    for line in r.stdout.splitlines():
        log("  | " + line)
    res = {0: "updated", 1: "rolled_back"}.get(r.returncode, "rollback_failed")
    if res == "rollback_failed":
        msg = f"auto-update: {row['project']} failed verification AND its rollback did not verify. Check now: cd {d} && docker compose ps"
        log("  " + msg)
        oob(msg)
    return {ref: (res if v == "pending" else v) for ref, v in out.items()}


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    only = {a for a in argv if not a.startswith("--")}
    cooldown = float(os.environ.get("COOLDOWN_HOURS", "24"))
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another run is active")
        return 0
    state_path = STATE / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    rows = [r for r in parse_conf(CONF.read_text()) if not only or r["project"] in only]
    results, applied = [], 0
    for row in rows:
        log(f"== {row['project']} ({row['tier']})")
        if past_deadline(APPLY_BY, time.localtime()):
            log(f"  deferred: past {APPLY_BY}")
            results.append({"project": row["project"], "tier": row["tier"], "ref": "-", "result": "deferred"})
            continue
        try:
            images = project_images(row)
        except Exception as e:  # noqa: BLE001
            log(f"  cannot list containers: {e}")
            results.append({**row, "ref": "-", "result": "error"})
            continue
        todo, built = [], local_builds(row)
        for ref, img in sorted(images.items()):
            key = f"{row['project']}|{ref}"
            running = [] if ref in built else repo_digests(img)
            if not running:
                log(f"  {ref}: local build, skipped (its base images are in the CVE scan)")
                results.append({"project": row["project"], "tier": row["tier"], "ref": ref, "result": "local_build"})
                continue
            remote = remote_digest(ref, running)
            action, entry = decide(row["tier"], remote, running, state.get(key, {}), time.time(), cooldown)
            state[key] = entry
            cur = next((x.split("@", 1)[1] for x in running), "")
            res = {"project": row["project"], "tier": row["tier"], "ref": ref, "result": action, "from": cur, "to": remote}
            if action in ("available", "held_back"):
                res["command"] = update_command(row)
            log(f"  {ref}: {action} ({_short(cur)} -> {_short(remote)})")
            results.append(res)
            if action == "apply":
                todo.append(ref)
        if not todo or dry:
            continue
        late = past_deadline(APPLY_BY, time.localtime()) or (MAX_APPLY and applied >= MAX_APPLY)
        if late:
            log(f"  deferred to the next run (deadline {APPLY_BY or '-'}, {applied}/{MAX_APPLY or '-'} applied)")
            for r in results:
                if r["project"] == row["project"] and r["ref"] in todo:
                    r["result"] = "deferred"
            continue
        floor = resources_ok()
        if floor:
            log(f"  skipped: {floor}")
            for r in results:
                if r["project"] == row["project"] and r["ref"] in todo:
                    r["result"] = "skipped"
            continue
        applied += 1
        outcome = apply(row, todo, images)
        for r in results:
            if r["project"] == row["project"] and r["ref"] in outcome:
                r["result"] = outcome[r["ref"]]
                key = f"{row['project']}|{r['ref']}"
                if r["result"] == "updated":
                    state[key] = {}
                elif r["result"] in ("rolled_back", "rollback_failed"):
                    state[key]["bad"] = True
                elif r["result"] == "major_held":
                    r["command"] = update_command(row)
                    state[key]["bad"] = True
                log(f"  {r['ref']}: {r['result']}")
    if dry:
        return 0
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, state_path)
    TEXTFILE.mkdir(parents=True, exist_ok=True)
    prom = TEXTFILE / "auto_update.prom"
    tmp = TEXTFILE / ".auto_update.prom.tmp"
    tmp.write_text(render_prom(results, time.time()))
    os.chmod(tmp, 0o644)
    os.replace(tmp, prom)
    return 2 if any(r["result"] == "rollback_failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
