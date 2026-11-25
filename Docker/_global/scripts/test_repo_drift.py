#!/usr/bin/env python3
"""Tests for repo_drift.py. Run: python3 test_repo_drift.py"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import repo_drift as rd

SVC = {"image": "traefik:v3.6", "environment": {"A": "1", "B": None}, "labels": {"traefik.enable": "true", "x": "y"},
       "volumes": [{"type": "bind", "source": "/h/a", "target": "/etc/a"}, {"type": "volume", "source": "v", "target": "/data"}]}


def insp(**kw):
    d = {"Config": {"Image": "traefik:v3.6", "Env": ["A=1", "B=2", "PATH=/bin"], "Labels": {"traefik.enable": "true", "x": "y", "com.docker.compose.service": "t"}},
         "Mounts": [{"Destination": "/etc/a"}, {"Destination": "/data"}]}
    for k, v in kw.items():
        d["Config"][k] = v
    return d


class Compose(unittest.TestCase):
    def test_equal_is_clean(self):
        self.assertEqual(rd.compose_diffs(SVC, insp()), [])

    def test_image_differs_and_digest_ignored(self):
        self.assertEqual(rd.compose_diffs(SVC, insp(Image="traefik:v3.6@sha256:abc")), [])
        self.assertEqual(rd.compose_diffs(SVC, insp(Image="traefik:v3.5")), ["image: repo traefik:v3.6, live traefik:v3.5"])

    def test_env_key_missing_live(self):
        self.assertEqual(rd.compose_diffs(SVC, insp(Env=["A=1"])), ["env key missing live: B"])

    def test_env_values_never_reported(self):
        out = rd.compose_diffs({"environment": {"S": "hunter2"}}, insp(Env=[]))
        self.assertEqual(out, ["env key missing live: S"])

    def test_mount_missing_live(self):
        i = insp()
        i["Mounts"] = [{"Destination": "/data"}]
        self.assertEqual(rd.compose_diffs(SVC, i), ["mount missing live: /etc/a"])

    def test_label_missing_or_changed(self):
        i = insp(Labels={"traefik.enable": "false", "com.docker.compose.service": "t"})
        self.assertEqual(rd.compose_diffs(SVC, i), ["label changed: traefik.enable", "label missing live: x"])

    def test_extra_live_things_are_not_drift(self):
        i = insp(Env=["A=1", "B=2", "EXTRA=1"])
        i["Mounts"].append({"Destination": "/more"})
        self.assertEqual(rd.compose_diffs(SVC, i), [])


class Hashes(unittest.TestCase):
    def test_parse_hashes(self):
        self.assertEqual(rd.parse_hashes("web abc123\ndb def456\n\n"), {"web": "abc123", "db": "def456"})


class Files(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def write(self, rel, text="x\n"):
        p = self.d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_norm(self):
        self.assertEqual(rd.norm("beefy_branch_order.py"), "beefy-branch-order")
        self.assertEqual(rd.norm("fastpi-backup"), "fastpi-backup")

    def test_check_file_same_differs_missing(self):
        live = self.write("live/a.service", "A")
        same = self.write("repo/x/a.service", "A")
        other = self.write("repo/y/a.service", "B")
        self.assertIsNone(rd.check_file(live, [same, other]))
        self.assertEqual(rd.check_file(live, [other]), "differs from repo")
        self.assertEqual(rd.check_file(live, []), "no repo source")

    def test_check_file_prefers_same_parent_dir(self):
        live = self.write("live/a.timer.d/night-window.conf", "A")
        wrong = self.write("repo/b.timer.d/night-window.conf", "B")
        right = self.write("repo/a.timer.d/night-window.conf", "A")
        self.assertIsNone(rd.check_file(live, [wrong, right]))

    def test_install_map_reads_install_lines(self):
        text = 'sudo install -m 0755 "$D/beefy_branch_order.py"      /usr/local/sbin/beefy-branch-order\ninstall -m 0644 "$HERE/x.service" /etc/systemd/system/\n'
        self.assertEqual(rd.install_map(text), {"/usr/local/sbin/beefy-branch-order": "beefy_branch_order.py", "/etc/systemd/system/x.service": "x.service"})

    def test_install_map_scripts_array(self):
        text = 'SCRIPTS=(\n  Docker/a/b/c_exp.py\n  Docker/_global/scripts/ctest.sh\n)\nDEST=${ROOT_SCRIPTS_DEST:-/usr/local/libexec/fastpi}\n'
        self.assertEqual(rd.install_map(text)["/usr/local/libexec/fastpi/ctest.sh"], "ctest.sh")

    def test_list_regular_skips_symlinks_binaries_and_pycache(self):
        self.write("u/a.service")
        (self.d / "u/b.service").symlink_to(self.d / "u/a.service")
        (self.d / "u/bin").write_bytes(b"\x7fELF\x02")
        self.write("u/__pycache__/x.pyc")
        self.write("u/sub/c.conf")
        self.write("u/x.conf.bak-2026")
        self.write("u/snap-core-1.mount")
        self.write("u/d.service", "# Auto-generated, DO NOT EDIT\n[Unit]\n")
        names = sorted(p.name for p in rd.list_regular(self.d / "u"))
        self.assertEqual(names, ["a.service", "c.conf"])


class Effective(unittest.TestCase):
    def test_last_assignment_wins(self):
        cat = "# /usr/lib/a.conf\n[Journal]\nStorage=persistent\n# /usr/lib/b.conf\n[Journal]\nStorage=volatile\nSystemMaxUse=1G\n"
        self.assertEqual(rd.effective_values(cat), {"Journal": {"Storage": "volatile", "SystemMaxUse": "1G"}})

    def test_setting_overridden_by_later_file(self):
        cat = "[Journal]\nStorage=persistent\n[Journal]\nStorage=volatile\n"
        self.assertEqual(rd.overridden(["Storage=persistent"], rd.effective_values(cat)["Journal"]), ["Storage: file persistent, effective volatile"])

    def test_time_spans_compare_by_value(self):
        self.assertEqual(rd.overridden(["A=2min", "B=14s"], {"A": "2m", "B": "1m"}), ["B: file 14s, effective 1m"])

    def test_sysctl_runtime_differs(self):
        self.assertEqual(rd.sysctl_diffs("vm.swappiness = 30\n# c\nkernel.x=1\n", {"vm.swappiness": "60", "kernel.x": "1"}), ["vm.swappiness: file 30, live 60"])


class Worktree(unittest.TestCase):
    def test_porcelain_lists_changed_and_untracked(self):
        out = " M Docker/a.yaml\n?? new.txt\nR  old -> new2\n"
        self.assertEqual(rd.parse_porcelain(out), [("modified", "Docker/a.yaml"), ("untracked", "new.txt"), ("renamed", "new2")])


class Output(unittest.TestCase):
    ITEMS = [{"kind": "compose", "path": "Traefik/traefik", "detail": "image: x"}, {"kind": "compose", "path": "grafana/prometheus", "detail": "y"},
             {"kind": "unit", "path": "/etc/systemd/system/a.service", "detail": "differs from repo"}]

    def test_counts_include_zero_kinds(self):
        self.assertEqual(rd.counts(self.ITEMS), {"compose": 2, "unit": 1, "script": 0, "etc": 0, "worktree": 0})

    def test_prom_has_series_per_kind_and_timestamp(self):
        text = rd.render_prom("fastpi", rd.counts(self.ITEMS), [], 1000.0, help_text=True)
        self.assertIn('drift_items{host="fastpi",kind="compose"} 2\n', text)
        self.assertIn('drift_items{host="fastpi",kind="worktree"} 0\n', text)
        self.assertIn('drift_last_run_timestamp_seconds{host="fastpi"} 1000\n', text)
        self.assertIn("# TYPE drift_items gauge", text)

    def test_prom_without_help_and_with_errors(self):
        text = rd.render_prom("beefy", rd.counts([]), ["compose"], 1000.0, help_text=False)
        self.assertNotIn("# HELP", text)
        self.assertIn('drift_collect_errors{host="beefy",kind="compose"} 1\n', text)
        self.assertIn('drift_collect_errors{host="beefy",kind="unit"} 0\n', text)

    def test_json_roundtrip_keeps_items(self):
        j = json.loads(rd.render_json("fastpi", self.ITEMS, 1000.0))
        self.assertEqual(j["host"], "fastpi")
        self.assertEqual(len(j["items"]), 3)

    def test_failed_kind_keeps_previous_items(self):
        prev = {"items": [{"kind": "compose", "path": "p", "detail": "old"}, {"kind": "unit", "path": "q", "detail": "old"}]}
        merged = rd.merge_previous(self.ITEMS[2:], ["compose"], prev)
        self.assertEqual(sorted(i["kind"] for i in merged), ["compose", "unit"])
        self.assertEqual([i for i in merged if i["kind"] == "compose"][0]["detail"], "old")


if __name__ == "__main__":
    unittest.main()
