#!/usr/bin/env python3
"""Container rules as a check over every compose project under Docker/ (scripts/README.md, policy-check). Same file on fastpi and beefy.
Env: POLICY_DOCKER (docker command; default docker, else sudo -n docker)."""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib

RULES = ("ipv4_address", "user", "user_unique", "cap_drop", "no_new_privileges", "healthcheck", "image_pinned", "env_secret")
HERE = os.path.dirname(os.path.abspath(__file__))
COMPOSE_FILES = ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml")
SECRET_NAME = re.compile(r"PASSWORD|PASSWD|SECRET|TOKEN|(^|_)API_?KEY($|_)|(^|_)KEY($|_)|APIKEY")
NOT_SECRET_SUFFIX = ("_FILE", "_PATH", "_DIR", "_URL", "_URI", "EXPIRATION", "EXPIRY", "EXPIRES")
NOT_SECRET_VALUE = re.compile(r"(?i)^(true|false|yes|no|on|off|\d+)$|^/|^https?://")


def docker_cmd():
    if os.environ.get("POLICY_DOCKER"):
        return shlex.split(os.environ["POLICY_DOCKER"])
    if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        return ["docker"]
    return ["sudo", "-n", "docker"]


def find_projects(root):
    """{label: (folder, [compose files])}; docker-compose.<variant>.yaml is its own stack <folder>:<variant>."""
    out = {}
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if name.startswith(("_", ".")) or not os.path.isdir(d):
            continue
        for f in COMPOSE_FILES:
            if os.path.isfile(os.path.join(d, f)):
                override = os.path.join(d, f.replace(".y", ".override.y"))
                out[name] = (name, [os.path.join(d, f)] + ([override] if os.path.isfile(override) else []))
                break
        for f in sorted(os.listdir(d)):
            m = re.fullmatch(r"(?:docker-)?compose\.([\w-]+)\.ya?ml", f)
            if m and m.group(1) != "override":
                out[f"{name}:{m.group(1)}"] = (name, [os.path.join(d, f)])
    return out


def compose_config(docker, files):
    args = ["compose", "--profile", "*", "--project-directory", os.path.dirname(files[0])]
    for f in files:
        args += ["-f", f]
    p = subprocess.run(docker + args + ["config", "--format", "json"], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return json.loads(p.stdout)


class Images:
    def __init__(self, docker):
        self.docker, self.cache = docker, {}

    def healthcheck(self, image):
        """True/False for the image's HEALTHCHECK, None when the image is not local."""
        if image not in self.cache:
            p = subprocess.run(self.docker + ["image", "inspect", "--format", "{{json .Config.Healthcheck}}", image],
                               capture_output=True, text=True)
            if p.returncode != 0:
                self.cache[image] = None
            else:
                hc = json.loads(p.stdout.strip() or "null") or {}
                test = hc.get("Test") or []
                self.cache[image] = bool(test) and test[0] != "NONE"
        return self.cache[image]


def split_user(user):
    uid, _, gid = str(user).partition(":")
    return uid.strip(), gid.strip()


def check_service(s, images, default_image=""):
    """Violations of one service as (rule, detail) pairs, except user_unique."""
    v = []
    mode = s.get("network_mode") or ""
    if not (mode in ("host", "none") or mode.startswith(("container:", "service:"))):
        for net, cfg in (s.get("networks") or {"default": None}).items():
            if not (cfg or {}).get("ipv4_address"):
                v.append(("ipv4_address", net))

    user = s.get("user")
    if user in (None, ""):
        v.append(("user", "not set"))
    else:
        uid, gid = split_user(user)
        if uid in ("0", "root"):
            v.append(("user", "root"))
        elif "1000" in (uid, gid):
            v.append(("user", "1000"))
        elif gid == "100":
            v.append(("user", "gid 100"))

    if "ALL" not in [c.upper() for c in s.get("cap_drop") or []]:
        v.append(("cap_drop", ""))

    opts = [o.replace("=", ":").replace(" ", "") for o in s.get("security_opt") or []]
    if not any(o in ("no-new-privileges", "no-new-privileges:true") for o in opts):
        v.append(("no_new_privileges", ""))

    hc, image = s.get("healthcheck"), s.get("image") or ""
    probe = image or (default_image if s.get("build") else "")
    if hc and hc.get("disable"):
        v.append(("healthcheck", "disabled"))
    elif not (hc and hc.get("test")):
        has = images.healthcheck(probe) if probe else None
        if has is None:
            v.append(("healthcheck", "image not local"))
        elif not has:
            v.append(("healthcheck", ""))

    last = image.rsplit("/", 1)[-1]
    local = last.split(":")[0].endswith("-local") or last.endswith(":local")
    if not s.get("build") and not local:
        if "@sha256:" not in image:
            if ":" not in last:
                v.append(("image_pinned", "no tag"))
            elif last.rsplit(":", 1)[1] == "latest":
                v.append(("image_pinned", "latest"))

    env = s.get("environment") or {}
    if isinstance(env, list):
        env = dict(e.partition("=")[::2] for e in env)
    for k in sorted(env):
        val = env[k]
        if (SECRET_NAME.search(k.upper()) and not k.upper().endswith(NOT_SECRET_SUFFIX)
                and val not in (None, "") and not NOT_SECRET_VALUE.search(str(val))):
            v.append(("env_secret", k))
    return v


def load_exceptions(path):
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    out = []
    for i, e in enumerate(data.get("exception", []), 1):
        for k in ("project", "service", "rule", "reason"):
            if not str(e.get(k, "")).strip():
                raise ValueError(f"{path}: exception {i}: {k} missing or empty")
        if e["rule"] not in RULES:
            raise ValueError(f"{path}: exception {i}: unknown rule {e['rule']!r} (rules: {' '.join(RULES)})")
        out.append(e)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Container policy check: one line per violation, exceptions from policy-exceptions.toml.",
        epilog="Rules: " + " ".join(RULES) + ". Exit 0 clean, 1 violations/stale exceptions, 2 error. "
               "Add an exception: [[exception]] project/service/rule/reason in Docker/_global/policy-exceptions.toml.")
    ap.add_argument("projects", nargs="*", metavar="PROJECT", help="project folder(s) under Docker/ (default: all)")
    ap.add_argument("--root", default=os.path.normpath(os.path.join(HERE, "..", "..")), help="Docker/ folder")
    ap.add_argument("--exceptions", default=os.path.join(HERE, "..", "policy-exceptions.toml"), help="exceptions file")
    a = ap.parse_args(argv)

    try:
        exceptions = load_exceptions(a.exceptions)
    except (ValueError, tomllib.TOMLDecodeError) as e:
        print(f"policy: {e}", file=sys.stderr)
        return 2

    projects = find_projects(a.root)
    folders = sorted({f for f, _ in projects.values()})
    selected = []
    for want in a.projects:
        hit = [l for l, (f, _) in projects.items() if want.lower() in (f.lower(), l.lower())]
        if not hit:
            print(f"policy: unknown project {want} (have: {' '.join(folders)})", file=sys.stderr)
            return 2
        selected += hit
    selected = selected or list(projects)

    docker = docker_cmd()
    images = Images(docker)
    configs, names, failed = {}, {}, []
    for p, (_, files) in projects.items():
        cfg = compose_config(docker, files)
        if cfg is None:
            failed.append(p)
        else:
            configs[p] = cfg.get("services") or {}
            names[p] = cfg.get("name") or p.lower()

    uids = {}
    for p, services in configs.items():
        for name, s in services.items():
            uid = split_user(s.get("user") or "")[0]
            if uid.isdigit() and uid != "0":
                uids.setdefault(uid, set()).add(projects[p][0])

    violations = []
    for p in selected:
        for name, s in sorted(configs.get(p, {}).items()):
            v = check_service(s, images, f"{names[p]}-{name}")
            uid = split_user(s.get("user") or "")[0]
            others = sorted(uids.get(uid, set()) - {projects[p][0]})
            if others:
                v.append(("user_unique", f"{uid} also in {', '.join(others)}"))
            violations += [(p, name, r, d) for r, d in v]

    excepted, stale, lines = 0, [], []
    keys = {(e["project"], e["service"], e["rule"]) for e in exceptions}
    for p, name, rule, detail in violations:
        if (p, name, rule) in keys:
            excepted += 1
        else:
            lines.append(f"{p}/{name}: {rule}" + (f" ({detail})" if detail else ""))
    hit = {(p, n, r) for p, n, r, _ in violations}
    for e in exceptions:
        p, n, r = e["project"], e["service"], e["rule"]
        if a.projects and p not in selected:
            continue
        if p not in projects:
            why = "project gone"
        elif p in failed:
            continue
        elif n not in configs[p]:
            why = "service gone"
        elif (p, n, r) not in hit:
            why = "not violated"
        else:
            continue
        stale.append(f"stale exception: {p}/{n}: {r} ({why})")

    for p in failed:
        if p in selected:
            lines.append(f"{p}: compose config failed (run: docker compose -f {projects[p][1][0]} config)")
    for l in sorted(lines) + sorted(stale):
        print(l)
    bad = len(lines) + len(stale)
    print(f"policy: {len(lines)} violations, {excepted} excepted, {len(stale)} stale exceptions")
    if bad:
        print(f"next: fix the service, or add a reviewed [[exception]] to {os.path.normpath(a.exceptions)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
