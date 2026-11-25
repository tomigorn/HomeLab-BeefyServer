#!/usr/bin/env python3
"""Fixture compose projects through policy_check.py (real `docker compose config`, fake `docker image inspect`). Usage: python3 test_policy_check.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "policy_check.py")
REAL_DOCKER = shutil.which("docker") or "docker"
if not subprocess.run([REAL_DOCKER, "compose", "version"], capture_output=True).returncode == 0:
    REAL_DOCKER = "sudo -n docker"

GOOD = """
    image: nginx:1.27.0
    user: "{uid}:{uid}"
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    healthcheck: {{test: ["CMD", "true"]}}
    networks:
      net: {{ipv4_address: 172.30.0.{ip}}}
"""
NETS = """
networks:
  net:
    ipam: {config: [{subnet: 172.30.0.0/24}]}
  net2:
    ipam: {config: [{subnet: 172.31.0.0/24}]}
"""


def svc(name, uid=65001, ip=2, drop=(), extra=""):
    """A service that passes every rule, minus the keys in drop, plus extra lines."""
    lines = [l for l in GOOD.format(uid=uid, ip=ip).strip("\n").splitlines()]
    out, skip = [], False
    for l in lines:
        key = l.strip().split(":")[0]
        if l.startswith("    ") and not l.startswith("     "):
            skip = key in drop
        if not skip:
            out.append(l)
    body = "\n".join(out)
    if extra:
        body += "\n" + textwrap.indent(textwrap.dedent(extra).strip("\n"), "    ")
    return f"  {name}:\n{body}\n"


class PolicyCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="policy-test-")
        self.root = os.path.join(self.tmp, "Docker")
        os.makedirs(self.root)
        self.exc = os.path.join(self.tmp, "exceptions.toml")
        self.images = {"nginx:1.27.0": None, "withhc:1.0": {"Test": ["CMD", "true"]}, "nohc:1.0": None}
        fake = os.path.join(self.tmp, "fake-docker")
        with open(fake, "w") as f:
            f.write(textwrap.dedent(f"""\
                #!/usr/bin/env bash
                if [[ $1 == compose ]]; then exec {REAL_DOCKER} "$@"; fi
                if [[ $1 == image && $2 == inspect ]]; then
                  exec python3 -I -c 'import json,sys; m=json.load(open(sys.argv[1])); i=sys.argv[2]
                if i not in m: print("Error: No such image: "+i, file=sys.stderr); sys.exit(1)
                print(json.dumps(m[i]))' "{self.tmp}/images.json" "${{@: -1}}"
                fi
                exit 3
                """))
        os.chmod(fake, 0o755)
        self.docker = fake

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def project(self, name, services, env=""):
        d = os.path.join(self.root, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "docker-compose.yaml"), "w") as f:
            f.write("services:\n" + "".join(services) + NETS)
        if env:
            with open(os.path.join(d, ".env"), "w") as f:
                f.write(env)

    def exceptions(self, *entries):
        with open(self.exc, "w") as f:
            for e in entries:
                f.write("[[exception]]\n" + "".join(f'{k} = "{v}"\n' for k, v in e.items()) + "\n")

    def run_check(self, *args):
        with open(os.path.join(self.tmp, "images.json"), "w") as f:
            json.dump(self.images, f)
        env = dict(os.environ, POLICY_DOCKER=self.docker)
        p = subprocess.run([sys.executable, SCRIPT, "--root", self.root, "--exceptions", self.exc, *args],
                           capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr

    def lines(self, out):
        return sorted(l for l in out.splitlines() if ": " in l and not l.startswith(("policy:", "next:")))

    # --- passing baseline
    def test_good_service_passes(self):
        self.project("P", [svc("app")])
        rc, out, err = self.run_check()
        self.assertEqual((rc, self.lines(out)), (0, []), out + err)
        self.assertIn("policy: 0 violations", out)

    # --- ipv4_address
    def test_network_without_ip(self):
        self.project("P", [svc("app", drop=("networks",), extra="networks:\n  net: {ipv4_address: 172.30.0.2}\n  net2: {}")])
        rc, out, _ = self.run_check()
        self.assertEqual(rc, 1)
        self.assertEqual(self.lines(out), ["P/app: ipv4_address (net2)"])

    def test_default_network_is_dynamic(self):
        self.project("P", [svc("app", drop=("networks",))])
        self.assertIn("P/app: ipv4_address (default)", self.lines(self.run_check()[1]))

    def test_network_mode_skipped(self):
        self.project("P", [svc("app", drop=("networks",), extra="network_mode: host"),
                           svc("side", uid=65001, drop=("networks",), extra="network_mode: service:app")])
        rc, out, _ = self.run_check()
        self.assertEqual((rc, self.lines(out)), (0, []), out)

    # --- user
    def test_user_rules(self):
        self.project("P", [svc("nouser", drop=("user",)),
                           svc("zero", extra='user: "0"', drop=("user",)),
                           svc("root", extra="user: root:root", drop=("user",)),
                           svc("pi", extra='user: "1000:1000"', drop=("user",)),
                           svc("gid1000", extra='user: "65002:1000"', drop=("user",)),
                           svc("users", extra='user: "65003:100"', drop=("user",))])
        out = self.lines(self.run_check()[1])
        self.assertEqual(out, ["P/gid1000: user (1000)", "P/nouser: user (not set)", "P/pi: user (1000)",
                               "P/root: user (root)", "P/users: user (gid 100)", "P/zero: user (root)"])

    def test_uid_shared_across_projects(self):
        self.project("A", [svc("one", uid=65010), svc("two", uid=65010, ip=3)])
        self.project("B", [svc("three", uid=65010)])
        out = self.lines(self.run_check()[1])
        self.assertEqual(out, ["A/one: user_unique (65010 also in B)", "A/two: user_unique (65010 also in B)",
                               "B/three: user_unique (65010 also in A)"])

    # --- cap_drop / no_new_privileges
    def test_caps(self):
        self.project("P", [svc("nodrop", drop=("cap_drop",)),
                           svc("partial", drop=("cap_drop",), extra="cap_drop: [NET_RAW]"),
                           svc("added", extra="cap_add: [NET_BIND_SERVICE]")])
        self.assertEqual(self.lines(self.run_check()[1]), ["P/nodrop: cap_drop", "P/partial: cap_drop"])

    def test_no_new_privileges(self):
        self.project("P", [svc("none", drop=("security_opt",)),
                           svc("off", drop=("security_opt",), extra="security_opt: [\"no-new-privileges:false\"]"),
                           svc("short", drop=("security_opt",), extra="security_opt: [no-new-privileges]")])
        self.assertEqual(self.lines(self.run_check()[1]), ["P/none: no_new_privileges", "P/off: no_new_privileges"])

    # --- healthcheck
    def test_healthcheck(self):
        self.project("P", [svc("missing", drop=("healthcheck", "image"), extra="image: nohc:1.0"),
                           svc("fromimage", drop=("healthcheck", "image"), extra="image: withhc:1.0"),
                           svc("disabled", drop=("healthcheck", "image"), extra="image: withhc:1.0\nhealthcheck: {disable: true}"),
                           svc("notlocal", drop=("healthcheck", "image"), extra="image: gone:2.0")])
        self.assertEqual(self.lines(self.run_check()[1]), [
            "P/disabled: healthcheck (disabled)", "P/missing: healthcheck", "P/notlocal: healthcheck (image not local)"])

    # --- image_pinned
    def test_image_pinned(self):
        dig = "sha256:" + "a" * 64
        self.project("P", [svc("latest", drop=("image",), extra="image: nginx:latest"),
                           svc("bare", drop=("image",), extra="image: nginx"),
                           svc("port", drop=("image",), extra="image: reg.lan:5000/team/app"),
                           svc("digest", drop=("image",), extra=f"image: nginx@{dig}"),
                           svc("tagdigest", drop=("image",), extra=f"image: nginx:latest@{dig}"),
                           svc("portok", drop=("image",), extra="image: reg.lan:5000/app:1.2")])
        self.images.update({"nginx:latest": None, "nginx": None, "reg.lan:5000/team/app": None,
                            f"nginx@{dig}": None, f"nginx:latest@{dig}": None, "reg.lan:5000/app:1.2": None})
        for k in self.images:
            self.images[k] = {"Test": ["CMD", "true"]}
        self.assertEqual(self.lines(self.run_check()[1]), [
            "P/bare: image_pinned (no tag)", "P/latest: image_pinned (latest)", "P/port: image_pinned (no tag)"])

    def test_built_image_not_pinned_checked(self):
        os.makedirs(os.path.join(self.root, "P", "ctx"))
        self.project("P", [svc("built", drop=("image",), extra="build: ./ctx\nimage: p-local:latest")])
        self.images["p-local:latest"] = None
        self.assertEqual(self.lines(self.run_check()[1]), [])

    def test_local_image_convention_not_pinned_checked(self):
        self.project("P", [svc("a", drop=("image",), extra="image: clamav-local:latest"),
                           svc("b", drop=("image",), extra="image: thing:local")])
        self.images.update({"clamav-local:latest": None, "thing:local": None})
        self.assertEqual(self.lines(self.run_check()[1]), [])

    def test_built_without_image_uses_compose_name(self):
        os.makedirs(os.path.join(self.root, "P", "ctx"))
        self.project("P", [svc("built", drop=("image", "healthcheck"), extra="build: ./ctx"),
                           svc("bare", drop=("image", "healthcheck"), extra="build: ./ctx")])
        self.images.update({"p-built": {"Test": ["CMD", "true"]}, "p-bare": None})
        self.assertEqual(self.lines(self.run_check()[1]), ["P/bare: healthcheck"])

    # --- env_secret
    def test_env_secrets(self):
        self.project("P", [svc("app", extra=textwrap.dedent("""\
            environment:
              DB_PASSWORD: hunter2plain
              API_KEY: ${FROM_ENV}
              APIKEY: x1
              PASSWORD_FILE: /run/secrets/db
              CLIENT_SECRET: /run/secrets/client
              KEYBOARD_LAYOUT: de
              MONKEY: banana
              AUTH_TOKEN: ""
              SECRET_KEY_BASE: abc
              SECRETS: "0"
              DISABLE_PASSWORD_AUTH: "true"
              USE_REFRESH_TOKEN: "false"
              OAUTH_TOKEN_URL: https://sso.example/token
              AzureAd__ClientSecretExpiration: 2027-01-01""")) ], env="FROM_ENV=s3cr3tfromenv\n")
        rc, out, err = self.run_check()
        self.assertEqual(self.lines(out), ["P/app: env_secret (APIKEY)", "P/app: env_secret (API_KEY)",
                                           "P/app: env_secret (DB_PASSWORD)", "P/app: env_secret (SECRET_KEY_BASE)"])
        for value in ("hunter2plain", "s3cr3tfromenv", "abc", "x1"):
            self.assertNotIn(value, out + err)

    # --- exceptions
    def test_exception_covers_violation(self):
        self.project("P", [svc("app", drop=("cap_drop",))])
        self.exceptions({"project": "P", "service": "app", "rule": "cap_drop", "reason": "needs caps, tested"})
        rc, out, _ = self.run_check()
        self.assertEqual((rc, self.lines(out)), (0, []), out)
        self.assertIn("1 excepted", out)

    def test_stale_exceptions(self):
        self.project("P", [svc("app")])
        self.exceptions({"project": "P", "service": "app", "rule": "cap_drop", "reason": "was needed"},
                        {"project": "P", "service": "gone", "rule": "user", "reason": "old"},
                        {"project": "Gone", "service": "x", "rule": "user", "reason": "old"})
        rc, out, _ = self.run_check()
        self.assertEqual(rc, 1)
        self.assertEqual(self.lines(out), ["stale exception: Gone/x: user (project gone)",
                                           "stale exception: P/app: cap_drop (not violated)",
                                           "stale exception: P/gone: user (service gone)"])

    def test_exception_needs_reason(self):
        self.project("P", [svc("app")])
        self.exceptions({"project": "P", "service": "app", "rule": "cap_drop", "reason": " "})
        rc, _, err = self.run_check()
        self.assertEqual(rc, 2)
        self.assertIn("reason", err)

    def test_exception_unknown_rule(self):
        self.project("P", [svc("app")])
        self.exceptions({"project": "P", "service": "app", "rule": "caps", "reason": "x"})
        rc, _, err = self.run_check()
        self.assertEqual(rc, 2)
        self.assertIn("unknown rule", err)

    def test_missing_exceptions_file_is_empty(self):
        self.project("P", [svc("app")])
        self.assertEqual(self.run_check()[0], 0)

    # --- project selection
    def test_only_named_projects(self):
        self.project("A", [svc("app", drop=("cap_drop",))])
        self.project("B", [svc("app", uid=65002)])
        self.exceptions({"project": "A", "service": "app", "rule": "user", "reason": "stale but not checked"})
        rc, out, _ = self.run_check("b")
        self.assertEqual((rc, self.lines(out)), (0, []), out)

    def test_unknown_project(self):
        self.project("A", [svc("app")])
        rc, _, err = self.run_check("Nope")
        self.assertEqual(rc, 2)
        self.assertIn("A", err)

    def test_skips_underscore_dirs_and_root_template(self):
        self.project("_global", [svc("app", drop=("cap_drop",))])
        self.project("_Deprecated", [svc("app", drop=("cap_drop",))])
        with open(os.path.join(self.root, "docker-compose.yaml"), "w") as f:
            f.write("services:\n" + svc("tmpl", drop=("cap_drop",)) + NETS)
        self.project("P", [svc("app")])
        self.assertEqual(self.run_check()[0], 0)

    def test_compose_variant_is_own_stack(self):
        self.project("P", [svc("app")])
        with open(os.path.join(self.root, "P", "docker-compose.v5.yaml"), "w") as f:
            f.write("services:\n" + svc("app", drop=("cap_drop",)) + NETS)
        with open(os.path.join(self.root, "P", "docker-compose.override.yaml"), "w") as f:
            f.write("services:\n  app:\n    user: \"0\"\n")
        rc, out, _ = self.run_check("P")
        self.assertEqual(self.lines(out), ["P/app: user (root)", "P:v5/app: cap_drop"])

    def test_variant_shares_uid_with_its_folder(self):
        self.project("P", [svc("app")])
        with open(os.path.join(self.root, "P", "docker-compose.v5.yaml"), "w") as f:
            f.write("services:\n" + svc("app") + NETS)
        self.assertEqual(self.lines(self.run_check()[1]), [])

    def test_compose_config_failure(self):
        self.project("P", [svc("app")])
        with open(os.path.join(self.root, "P", "docker-compose.yaml"), "a") as f:
            f.write("bogus: [\n")
        rc, out, _ = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("P: compose config failed", out)

    def test_help(self):
        p = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("policy-exceptions.toml", p.stdout)


if __name__ == "__main__":
    unittest.main()
