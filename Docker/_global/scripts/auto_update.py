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
The ref checked is the one the compose file declares (docker compose config, with .env), never the container's own.

Drift: a container whose image ref differs from the declared one (or, for a ref pinned by digest, runs another image id) is "drift", not an update: no registry check.
auto / edge-auto rows recreate it through update-project.sh recreate (checks, rollback; result "recreated"); notify / edge rows only report it with the recreate command.
A drift recreate that rolls back is not retried for that container image and declared ref.

The same file runs on fastpi and beefy; host differences come from the environment (systemd unit) and the script's own location.

Shared tags: after the updates, running containers whose tag another project already moved to a newer image are recreated through update-project.sh when their row applies updates (auto, edge-auto), else only logged; a recreate that rolls back holds that tag back. Every run (and --stale-only) writes container_image_stale.prom with the containers still stale.

Usage: auto_update.py [--dry-run] [--stale-only] [project ...]   (-h/--help: this text; an unknown option or project changes nothing, exit 2)
Env: COOLDOWN_HOURS (24), TEST_FORCE_FAIL=1 (first functional check fails: rollback drill),
     AUTO_UPDATE_HOST (host label, default the short hostname), AUTO_UPDATE_APPLY_BY (HH:MM local: no registry check or update starts later, the rest is "deferred"),
     AUTO_UPDATE_MAX_APPLY (projects updated per run, default no limit), AUTO_UPDATE_CONF / _STATE / _TEXTFILE (paths)
"""
import fcntl
import html
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


def norm_ref(ref: str) -> str:
    """docker.io/library/caddy -> caddy:latest: the spellings of one ref compare equal."""
    for p in ("docker.io/library/", "index.docker.io/library/", "docker.io/", "index.docker.io/"):
        if ref.startswith(p):
            ref = ref[len(p):]
            break
    name = ref.split("@", 1)[0]
    if ":" not in name.rsplit("/", 1)[-1] and not ref.startswith("sha256:"):
        ref = name + ":latest" + ref[len(name):]
    return ref


def check_drift(containers: list[dict], declared: dict[str, str], local_ids: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    """-> (declared ref -> running image id for the update check, drifted containers with want = declared ref and why).

    Drift: the container's ref differs from the one compose declares for its service, or a ref pinned by digest runs another image id.
    A tag ref on another image id is the shared-tag case (find_stale), not drift. Services compose does not name keep their own ref.
    """
    images, drift = {}, []
    for c in containers:
        want = declared.get(c["service"]) or c["ref"]
        why = ""
        if norm_ref(c["ref"]) != norm_ref(want):
            why = f"runs {c['ref']}, compose declares {want}"
        elif "@" in want and local_ids.get(want) and local_ids[want] != c["image"]:
            why = f"runs image {_short(c['image'])}, {want} is {_short(local_ids[want])}"
        if why:
            drift.append(dict(c, want=want, why=why))
        else:
            images[want] = c["image"]
    return images, drift


def drift_command(row: dict, services: list[str]) -> str:
    tail = "".join(f" {b}" for b in row["backups"])
    return f"SERVICES='{' '.join(sorted(services))}' {SCRIPTS}/update-project.sh {DOCKER / row['project']} recreate{tail}"


def update_command(row: dict) -> str:
    svc = f"SERVICES='{' '.join(row['services'])}' " if row["services"] else ""
    tail = "".join(f" {b}" for b in row["backups"])
    return f"{svc}{SCRIPTS}/update-project.sh {DOCKER / row['project']} pull{tail}"


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _short(d: str) -> str:
    return d.split(":", 1)[-1][:12]


QUIET = ("current", "local_build", "waiting")
PRIORITY = ["rollback_failed", "rolled_back", "error", "major_held", "held_back", "skipped", "drift", "updated", "recreated", "available", "deferred"]
SUMMARY_BUDGET = 3000


def _rank(r: dict) -> int:
    return PRIORITY.index(r["result"]) if r["result"] in PRIORITY else len(PRIORITY)


def render_prom(results: list[dict], now: float, host: str = HOST) -> str:
    """auto_update_result (one per image, result = current|waiting|updated|rolled_back|rollback_failed|available|drift|recreated|major_held|held_back|skipped|deferred|local_build|error; a rollback carries reason, e.g. the missing customizations), auto_update_omitted (noteworthy results left out to keep the Telegram summary under 4096 characters), auto_update_last_run_timestamp_seconds.

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
        if r.get("reason"):
            labels["reason"] = r["reason"]
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


# ---------------------------------------------------------------- shared tags

STALE_FORMAT = ('{{.Name}}|{{.Config.Image}}|{{.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|'
                '{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{index .Config.Labels "com.docker.compose.service"}}')


def parse_container_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 6:
            continue
        name, ref, image, project, workdir, service = parts
        out.append({"name": name.lstrip("/"), "ref": ref, "image": image, "project": project, "workdir": workdir, "service": service})
    return out


def is_tag_ref(ref: str) -> bool:
    return bool(ref) and "@" not in ref and not ref.startswith("sha256:")


def find_stale(containers: list[dict], tag_ids: dict[str, str]) -> list[dict]:
    """Running containers made from a tag that now points at another local image (another project pulled it)."""
    return [dict(c, want=tag_ids[c["ref"]]) for c in containers
            if is_tag_ref(c["ref"]) and tag_ids.get(c["ref"]) and tag_ids[c["ref"]] != c["image"]]


def stale_plan(stale: list[dict], rows: list[dict], docker_dir: Path, bad: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    """-> (one recreate per conf row: {row, services, stale}, the rest with the reason they are only reported)."""
    plans: dict[int, dict] = {}
    report = []
    for c in stale:
        if c["want"] in bad.get(c["ref"], []) or c["image"] in bad.get(c["ref"], []):
            report.append(dict(c, why="held back (an earlier recreate rolled back)"))
            continue
        hits = [r for r in rows if c["workdir"] and Path(c["workdir"]) == docker_dir / r["project"]]
        if not hits:
            report.append(dict(c, why="not in auto-update.conf"))
            continue
        row = next((r for r in hits if not r["services"] or c["service"] in r["services"]), None)
        if row is None:
            report.append(dict(c, why="service not auto-updated"))
            continue
        if row["tier"] not in APPLYING:
            report.append(dict(c, why=f"tier {row['tier']}"))
            continue
        p = plans.setdefault(id(row), {"row": row, "services": [], "stale": []})
        if c["service"] not in p["services"]:
            p["services"].append(c["service"])
        p["stale"].append(c)
    return list(plans.values()), report


def render_stale_prom(stale: list[dict], now: float, host: str = HOST) -> str:
    """container_image_stale (one per running container whose tag moved to another image), container_image_stale_containers, container_image_stale_last_run_timestamp_seconds. No HELP lines (republished next to fastpi's)."""
    h = f'host="{_esc(host)}"'
    lines = ["# TYPE container_image_stale gauge"]
    for c in sorted(stale, key=lambda c: c["name"]):
        labels = {"host": host, "project": c["project"], "service": c["service"], "container": c["name"], "image": c["ref"],
                  "running": _short(c["image"]), "tag": _short(c["want"]), "dir": c["workdir"]}
        lines.append("container_image_stale{" + ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items()) + "} 1")
    lines += ["# TYPE container_image_stale_containers gauge", f"container_image_stale_containers{{{h}}} {len(stale)}",
              "# TYPE container_image_stale_last_run_timestamp_seconds gauge", f"container_image_stale_last_run_timestamp_seconds{{{h}}} {int(now)}"]
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


def compose_config(row: dict) -> dict:
    """The project's resolved compose config (compose files + .env), as update-project.sh reads it."""
    d = DOCKER / row["project"]
    r = sh(["docker", "compose", "--project-directory", str(d), "config", "--format", "json"], cwd=d)
    if r.returncode != 0:
        raise RuntimeError(f"docker compose config failed: {r.stderr.strip()[-200:]}")
    return json.loads(r.stdout)


def project_containers(row: dict) -> list[dict]:
    """The project's (selected) containers: name, ref, image id, service."""
    d = DOCKER / row["project"]
    ids = sh(["docker", "compose", "--project-directory", str(d), "ps", "-q", *row["services"]], cwd=d).stdout.split()
    out = []
    for cid in ids:
        line = sh(["docker", "inspect", "-f", '{{.Name}}|{{.Config.Image}}|{{.Image}}|{{index .Config.Labels "com.docker.compose.service"}}', cid]).stdout.strip()
        name, ref, img, svc = line.split("|")
        out.append({"name": name.lstrip("/"), "ref": ref, "image": img, "service": svc})
    return out


def declared_refs(config: dict) -> dict[str, str]:
    return {k: v["image"] for k, v in (config.get("services") or {}).items() if v.get("image")}


def project_images(row: dict, config: dict) -> tuple[dict[str, str], list[dict]]:
    """-> (declared ref -> running image id, drifted containers); see check_drift."""
    declared = declared_refs(config)
    local = {}
    for ref in {r for r in declared.values() if "@" in r}:
        q = sh(["docker", "image", "inspect", "-f", "{{.Id}}", ref])
        if q.returncode == 0 and q.stdout.strip():
            local[ref] = q.stdout.strip()
    return check_drift(project_containers(row), declared, local)


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


def update_result(rc: int) -> str:
    """update-project.sh exit code -> result: 2 = nothing changed (a backup failed or no project), 3 = the rollback did not verify."""
    return {0: "updated", 1: "rolled_back", 2: "error"}.get(rc, "rollback_failed")


def failure_reason(out: str) -> str:
    """Why update-project.sh rolled back or aborted, from its output: a failed backup, the missing customizations (post-update-check.sh), the functional check, or the containers."""
    for line in out.splitlines():
        if line.startswith("  backup: FAIL"):
            return "backup failed: " + line.split("FAIL", 1)[1].strip()
        if line.startswith("  custom: FAIL"):
            return "customizations missing: " + line.split("FAIL", 1)[1].strip()
    if any(line.startswith("  check : FAIL") for line in out.splitlines()):
        return "check failed"
    return "containers not running" if "ROLLBACK" in out else ""


def rollback_failed_message(project: str, directory, reason: str = "") -> str:
    return (f"🔴 <b>Auto-update: {html.escape(project)} is down</b>\n\n"
            f"• Update failed verification{': ' + html.escape(reason) if reason else ''}\n"
            "• Rollback did not verify either\n"
            f"<blockquote expandable>💡 Check: <code>cd {html.escape(str(directory))} && docker compose ps</code></blockquote>")


def oob(msg: str) -> None:
    script = SCRIPTS / "oob-alert.sh"
    if not script.exists():
        log("  no oob-alert.sh on this host: the AutoUpdateRollbackFailed alert is the page")
        return
    sh([str(script), msg], timeout=60)


def apply(row: dict, refs: list[str], images: dict[str, str]) -> tuple[dict[str, str], str]:
    """Pull, refuse a major jump, then update-project.sh recreate with the functional and customization checks. -> (ref -> result, rollback reason)."""
    d = DOCKER / row["project"]
    services = row["services"]
    r = sh(["docker", "compose", "pull", "-q", *services], cwd=d, timeout=1800)
    if r.returncode != 0:
        log(f"  pull failed: {r.stderr.strip()[-300:]}")
        return {ref: "error" for ref in refs}, ""
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
        return out, ""
    env = dict(os.environ, SERVICES=" ".join(services), CHECK=row["check"])
    r = subprocess.run([str(SCRIPTS / "update-project.sh"), str(d), "recreate", *row["backups"]],
                       env=env, text=True, capture_output=True, timeout=1800)
    for line in r.stdout.splitlines():
        log("  | " + line)
    res = update_result(r.returncode)
    reason = failure_reason(r.stdout) if res != "updated" else ""
    if res == "rollback_failed":
        log(f"  {row['project']} failed verification AND its rollback did not verify. Check now: cd {d} && docker compose ps")
        oob(rollback_failed_message(row["project"], d, reason))
    return {ref: (res if v == "pending" else v) for ref, v in out.items()}, reason


def stale_containers() -> list[dict]:
    ids = sh(["docker", "ps", "-q"]).stdout.split()
    if not ids:
        return []
    r = sh(["docker", "inspect", "-f", STALE_FORMAT, *ids])
    cs = parse_container_lines(r.stdout)
    if not cs:
        raise RuntimeError(f"docker inspect gave no containers: {r.stderr.strip()[-200:]}")
    tags = {}
    for ref in sorted({c["ref"] for c in cs if is_tag_ref(c["ref"])}):
        q = sh(["docker", "image", "inspect", "-f", "{{.Id}}", ref])
        if q.returncode == 0 and q.stdout.strip():
            tags[ref] = q.stdout.strip()
    return find_stale(cs, tags)


def stale_command(c: dict) -> str:
    return f"SERVICES={c['service']} {SCRIPTS}/update-project.sh {c['workdir']} recreate"


def fix_stale(rows: list[dict], state: dict, results: list[dict], applied: int, dry: bool, skip: set[str] = frozenset()) -> int:
    """Containers left on the old image of a tag another project pulled: recreated through update-project.sh (verify, rollback) when their row applies updates, else reported. Containers already handled as drift (skip) are left out. -> projects applied so far."""
    stale = [c for c in stale_containers() if c["name"] not in skip]
    bad = state.setdefault("stale_bad", {})
    plans, report = stale_plan(stale, rows, DOCKER, bad)
    for c in report:
        log(f"  stale: {c['name']} ({c['project']}/{c['service']}) runs {_short(c['image'])}, {c['ref']} is on {_short(c['want'])}: {c['why']}; to recreate: {stale_command(c)}")
    for p in plans:
        row, first = p["row"], p["stale"][0]
        log(f"== {row['project']} ({row['tier']}): stale on a shared tag: {', '.join(c['name'] for c in p['stale'])}")
        base = {"project": row["project"], "tier": row["tier"], "ref": first["ref"], "from": first["image"], "to": first["want"], "reason": "shared tag moved by another project"}
        if dry:
            log(f"  would recreate {' '.join(p['services'])}")
            continue
        if any(crosses_major(image_version(c["image"]), image_version(c["want"])) for c in p["stale"]):
            log("  major jump, not recreated")
            results.append(dict(base, result="major_held", command=stale_command(first)))
            continue
        if past_deadline(APPLY_BY, time.localtime()) or (MAX_APPLY and applied >= MAX_APPLY):
            results.append(dict(base, result="deferred"))
            continue
        floor = resources_ok()
        if floor:
            log(f"  skipped: {floor}")
            results.append(dict(base, result="skipped"))
            continue
        applied += 1
        env = dict(os.environ, SERVICES=" ".join(p["services"]), CHECK=row["check"])
        r = subprocess.run([str(SCRIPTS / "update-project.sh"), str(DOCKER / row["project"]), "recreate", *row["backups"]],
                           env=env, text=True, capture_output=True, timeout=1800)
        for line in r.stdout.splitlines():
            log("  | " + line)
        res = update_result(r.returncode)
        out = dict(base, result=res)
        if res != "updated":
            out["reason"] += ": " + (failure_reason(r.stdout) or "update-project.sh failed")
            for c in p["stale"]:
                bad[c["ref"]] = sorted({c["image"], c["want"]})
        if res == "rollback_failed":
            oob(rollback_failed_message(row["project"], DOCKER / row["project"], out["reason"]))
        log(f"  {first['ref']}: {res}")
        results.append(out)
    return applied


def write_stale_prom() -> None:
    try:
        stale = stale_containers()
    except Exception as e:  # noqa: BLE001
        log(f"stale scan failed, container_image_stale.prom left as it was: {e}")
        return
    for c in stale:
        log(f"  still stale: {c['name']} ({c['project']}/{c['service']}) on {_short(c['image'])}, {c['ref']} is on {_short(c['want'])}")
    TEXTFILE.mkdir(parents=True, exist_ok=True)
    tmp = TEXTFILE / ".container_image_stale.prom.tmp"
    tmp.write_text(render_stale_prom(stale, time.time()))
    os.chmod(tmp, 0o644)
    os.replace(tmp, TEXTFILE / "container_image_stale.prom")


def usage_error(argv: list[str], known: set[str]) -> tuple[int, str] | None:
    """--help -> (0, usage); an unknown option or project -> (2, why + usage); else None."""
    usage = "Usage: " + __doc__.split("Usage: ", 1)[1].split("\n", 1)[0]
    if "-h" in argv or "--help" in argv:
        return 0, usage
    bad = [a for a in argv if (a.startswith("-") and a not in ("--dry-run", "--stale-only")) or (not a.startswith("-") and a not in known)]
    return (2, f"auto_update.py: unknown argument {' '.join(bad)}\n{usage}") if bad else None


def drift_result(row: dict, c: dict, result: str, **extra) -> dict:
    return {"project": row["project"], "tier": row["tier"], "ref": c["want"], "result": result, "from": c["image"], "to": "",
            "reason": f"drift: {c['name']} {c['why']}", **extra}


def handle_drift(row: dict, drift: list[dict], state: dict, results: list[dict], applied: int, dry: bool) -> tuple[int, bool]:
    """Drifted containers: recreated through update-project.sh in auto / edge-auto rows, else reported. -> (projects applied so far, recreated)."""
    services = sorted({c["service"] for c in drift})
    cmd = drift_command(row, services)
    bad = state.setdefault("drift_bad", {})
    for c in drift:
        log(f"  {c['name']}: drift, {c['why']}; to recreate: {cmd}")
    held = [c for c in drift if bad.get(f"{row['project']}|{c['want']}") == c["image"]]
    if row["tier"] not in APPLYING or dry or held:
        if held:
            log("  an earlier drift recreate rolled back: reported, not retried")
        elif dry and row["tier"] in APPLYING:
            log(f"  would recreate {' '.join(services)}")
        results.extend(drift_result(row, c, "drift", command=cmd) for c in drift)
        return applied, False
    if past_deadline(APPLY_BY, time.localtime()) or (MAX_APPLY and applied >= MAX_APPLY):
        results.extend(drift_result(row, c, "deferred", command=cmd) for c in drift)
        return applied, False
    floor = resources_ok()
    if floor:
        log(f"  skipped: {floor}")
        results.extend(drift_result(row, c, "skipped", command=cmd) for c in drift)
        return applied, False
    env = dict(os.environ, SERVICES=" ".join(services), CHECK=row["check"])
    r = subprocess.run([str(SCRIPTS / "update-project.sh"), str(DOCKER / row["project"]), "recreate", *row["backups"]],
                       env=env, text=True, capture_output=True, timeout=1800)
    for line in r.stdout.splitlines():
        log("  | " + line)
    res = update_result(r.returncode)
    res = "recreated" if res == "updated" else res
    why = failure_reason(r.stdout) if res != "recreated" else ""
    for c in drift:
        out = drift_result(row, c, res)
        if why:
            out["reason"] += f"; {why}"
        if res not in ("recreated", "error"):
            bad[f"{row['project']}|{c['want']}"] = c["image"]
            out["command"] = cmd
        results.append(out)
    if res == "rollback_failed":
        oob(rollback_failed_message(row["project"], DOCKER / row["project"], "drift recreate" + (": " + why if why else "")))
    log(f"  drift recreate: {res}")
    return applied + 1, res == "recreated"


def main(argv: list[str]) -> int:
    if not ({"-h", "--help"} & set(argv)) and any(not a.startswith("-") for a in argv):
        known = {r["project"] for r in parse_conf(CONF.read_text())}
    else:
        known = set()
    err = usage_error(argv, known)
    if err:
        print(err[1], file=sys.stdout if err[0] == 0 else sys.stderr)
        return err[0]
    dry = "--dry-run" in argv
    only = {a for a in argv if not a.startswith("--")}
    if "--stale-only" in argv:
        write_stale_prom()
        return 0
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
    results, applied, drifted = [], 0, set()
    for row in rows:
        log(f"== {row['project']} ({row['tier']})")
        if past_deadline(APPLY_BY, time.localtime()):
            log(f"  deferred: past {APPLY_BY}")
            results.append({"project": row["project"], "tier": row["tier"], "ref": "-", "result": "deferred"})
            continue
        try:
            config = compose_config(row)
            images, drift = project_images(row, config)
            if drift:
                applied, fixed = handle_drift(row, drift, state, results, applied, dry)
                if fixed:
                    images, drift = project_images(row, config)
                    results.extend(drift_result(row, c, "drift", command=drift_command(row, [c["service"]])) for c in drift)
                drifted |= {c["name"] for c in drift}
        except Exception as e:  # noqa: BLE001
            log(f"  cannot list containers: {e}")
            results.append({"project": row["project"], "tier": row["tier"], "ref": "-", "result": "error", "reason": str(e)[:200]})
            continue
        todo, built = [], build_refs(config)
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
        outcome, reason = apply(row, todo, images)
        for r in results:
            if r["project"] == row["project"] and r["ref"] in outcome:
                r["result"] = outcome[r["ref"]]
                if reason and r["result"] in ("rolled_back", "rollback_failed", "error"):
                    r["reason"] = reason
                key = f"{row['project']}|{r['ref']}"
                if r["result"] == "updated":
                    state[key] = {}
                elif r["result"] in ("rolled_back", "rollback_failed"):
                    state[key]["bad"] = True
                elif r["result"] == "major_held":
                    r["command"] = update_command(row)
                    state[key]["bad"] = True
                log(f"  {r['ref']}: {r['result']}")
    try:
        applied = fix_stale(rows, state, results, applied, dry, drifted)
    except Exception as e:  # noqa: BLE001
        log(f"stale scan failed: {e}")
    if dry:
        return 0
    write_stale_prom()
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
