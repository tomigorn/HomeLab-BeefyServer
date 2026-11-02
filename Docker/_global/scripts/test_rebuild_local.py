#!/usr/bin/env python3
"""Tests for rebuild_local.py's pure functions. Run: python3 test_rebuild_local.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rebuild_local import (  # noqa: E402
    DOCKER,
    alert_message,
    bump_pins,
    commit_message,
    due,
    fingerprint,
    from_refs,
    guard_reason,
    image_env,
    pinned_tags,
    parse_conf,
    project_dir,
    project_name,
    parse_env,
    render_prom,
    rollback_tag,
    update_result,
)


def test_parse_conf_reads_entries_with_defaults():
    rows = parse_conf('[[image]]\nproject = "Tower-Waker"\nday = "Mon"\n\n'
                      '[[image]]\nproject = "/srv/Stonks"\nday = "sat"\nservices = ["stonks"]\n'
                      'build = "docker build -t stonks:latest /x"\ncontext = "/x"\nunittest = "python -m unittest"\ncheck = "curl -sf http://x/"\n')
    assert rows[0] == {"project": "Tower-Waker", "day": "Mon", "services": [], "build": "", "context": "",
                       "test": "", "unittest": "", "check": "", "backups": [], "guard": ""}
    assert rows[1]["day"] == "Sat" and rows[1]["services"] == ["stonks"] and rows[1]["context"] == "/x"


def test_parse_conf_refuses_bad_day_and_unknown_keys():
    for bad in ('[[image]]\nproject = "A"\nday = "Monday"\n', '[[image]]\nproject = "A"\nday = "Mon"\ntypo = 1\n',
                '[[image]]\nday = "Mon"\n'):
        try:
            parse_conf(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_guard_reason_empty_when_ok_else_last_output_line():
    assert guard_reason("", Path("/")) == ""
    assert guard_reason("true", Path("/")) == ""
    assert guard_reason("echo checking; echo 1 playing now; exit 1", Path("/")) == "1 playing now"
    assert guard_reason("exit 3", Path("/")) == "guard failed (rc=3)"


def test_due_by_weekday_or_named():
    row = {"project": "Tower-Waker", "day": "Mon"}
    assert due(row, "Mon", set())
    assert not due(row, "Tue", set())
    assert due(row, "Tue", {"Tower-Waker"})
    assert not due(row, "Mon", {"Other"})


def test_project_dir_and_name():
    assert project_dir("Tower-Waker") == DOCKER / "Tower-Waker"
    assert project_dir("/home/pi/x/WG-Chores") == Path("/home/pi/x/WG-Chores")
    assert project_name("LanguageTool/Raspi") == "LanguageTool"
    assert project_name("/home/pi/x/WG-Chores") == "WG-Chores"


DOCKERFILE = """# syntax comment
ARG X=1
FROM python:3.13-alpine@sha256:aaaa AS thumbs
RUN true
FROM --platform=linux/arm64 python:3.13-alpine@sha256:aaaa
from alpine:3.24
FROM thumbs
FROM scratch
"""


def test_from_refs_skips_stages_and_scratch():
    assert from_refs(DOCKERFILE) == ["python:3.13-alpine@sha256:aaaa", "alpine:3.24"]


def test_pinned_tags_skips_digest_only_refs():
    text = DOCKERFILE + "FROM ghcr.io/linuxserver/mods@sha256:cccc AS opencl\nFROM registry:5000/x/y:1.2@sha256:dddd\n"
    assert pinned_tags(text) == {"python:3.13-alpine", "registry:5000/x/y:1.2"}


def test_bump_pins_rewrites_only_moved_digests():
    out, bumped = bump_pins(DOCKERFILE, {"python:3.13-alpine": "sha256:bbbb"})
    assert bumped == ["python:3.13-alpine"]
    assert out.count("python:3.13-alpine@sha256:bbbb") == 2 and "sha256:aaaa" not in out
    assert "from alpine:3.24" in out
    same, none = bump_pins(DOCKERFILE, {"python:3.13-alpine": "sha256:aaaa"})
    assert same == DOCKERFILE and none == []
    empty, none = bump_pins(DOCKERFILE, {"python:3.13-alpine": ""})
    assert empty == DOCKERFILE and none == []


def test_fingerprint_is_stable_and_sensitive():
    env = ["PATH=/usr/bin", "PYTHON_VERSION=3.13.8"]
    a = fingerprint(env, "P:musl\nV:1.2\n", "100644 abc 0\tapp.py\n")
    assert a == fingerprint(list(reversed(env)), "P:musl\nV:1.2\n", "100644 abc 0\tapp.py\n")
    assert a != fingerprint(["PATH=/usr/bin", "PYTHON_VERSION=3.13.9"], "P:musl\nV:1.2\n", "100644 abc 0\tapp.py\n")
    assert a != fingerprint(env, "P:musl\nV:1.3\n", "100644 abc 0\tapp.py\n")
    assert a != fingerprint(env, "P:musl\nV:1.2\n", "100644 abd 0\tapp.py\n")
    assert fingerprint(env, "", "x") == ""


def test_image_env_names_every_service_and_a_default():
    assert image_env({"clamav": "sha256:1", "port_updater": "sha256:2"}, "clamav") == {
        "IMAGE": "sha256:1", "IMAGE_CLAMAV": "sha256:1", "IMAGE_PORT_UPDATER": "sha256:2"}


def test_update_result_maps_exit_codes():
    assert [update_result(c) for c in (0, 1, 2, 3, 9)] == ["updated", "rolled_back", "error", "rollback_failed", "rollback_failed"]


def test_commit_message_is_one_short_line():
    m = commit_message("Tower-Waker", ["python:3.13-alpine"])
    assert m == "Tower-Waker: bump python:3.13-alpine base digest (weekly rebuild)"
    assert "\n" not in m and len(m.split()) <= 15


def test_alert_message_is_telegram_html_and_escaped():
    m = alert_message("fastpi", "Tower-Waker", "test_failed", "unittest <3 failures>")
    assert m.startswith("🔴 <b>Rebuild: Tower-Waker on fastpi</b>\n\n• ")
    assert "&lt;3 failures&gt;" in m and "test_failed" in m
    assert alert_message("fastpi", "X", "rolled_back", "").startswith("↩️ ")


def test_render_prom_lists_every_project_with_its_last_result():
    state = {"Tower-Waker": {"result": "updated", "time": 100, "ok_time": 100},
             "Stonks": {"result": "test_failed", "reason": 'a "b"', "time": 200, "ok_time": 50}}
    text = render_prom(state, 300, "fastpi")
    assert 'rebuild_local_result{host="fastpi",project="Stonks",result="test_failed",reason="a \\"b\\""} 1' in text
    assert 'rebuild_local_result{host="fastpi",project="Tower-Waker",result="updated",reason=""} 1' in text
    assert 'rebuild_local_last_ok_timestamp_seconds{host="fastpi",project="Stonks"} 50' in text
    assert 'rebuild_local_last_run_timestamp_seconds{host="fastpi"} 300' in text
    assert "# HELP" not in text


def test_rollback_tag_matches_update_project():
    assert rollback_tag("clamav-local:latest", "antivirus", "2026-11-02") == "rollback.invalid/clamav-local:2026-11-02-antivirus"
    assert rollback_tag("registry:5000/x/y:1.2@sha256:dd", "p", "2026-11-02") == "rollback.invalid/registry-5000/x/y:2026-11-02-p"
    assert rollback_tag("lscr.io/linuxserver/jellyfin", "jellyfin", "2026-11-07") == "rollback.invalid/lscr.io/linuxserver/jellyfin:2026-11-07-jellyfin"


def test_parse_env_tolerates_missing_image_output():
    assert parse_env('["PATH=/usr/bin"]\n') == ["PATH=/usr/bin"]
    assert parse_env("\n") is None
    assert parse_env("") is None
    assert parse_env("null") is None


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
