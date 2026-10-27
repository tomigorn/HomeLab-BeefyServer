#!/usr/bin/env python3
"""Tests for auto_update.py's pure functions. Run: python3 test_auto_update.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import time  # noqa: E402

from auto_update import (  # noqa: E402
    SCRIPTS,
    DOCKER,
    build_refs,
    crosses_major,
    decide,
    failure_reason,
    parse_conf,
    past_deadline,
    pick_digest,
    render_prom,
    rollback_failed_message,
    update_command,
)

DAY = 86400


def test_parse_conf_skips_comments_and_reads_columns():
    text = (
        "# tier  project  services  backups  check\n"
        "\n"
        "auto    Claude-Mockups  -  -  curl -sf http://x/\n"
        "notify  Home-Assistant  homeassistant  ./config  -\n"
    )
    assert parse_conf(text) == [
        {"tier": "auto", "project": "Claude-Mockups", "services": [], "backups": [], "check": "curl -sf http://x/"},
        {"tier": "notify", "project": "Home-Assistant", "services": ["homeassistant"], "backups": ["./config"], "check": ""},
    ]


def test_parse_conf_splits_lists_on_commas():
    row = parse_conf("auto  mouse  sabnzbd,samba  vol:a,./b  -\n")[0]
    assert row["services"] == ["sabnzbd", "samba"] and row["backups"] == ["vol:a", "./b"]


def test_parse_conf_rejects_unknown_tier():
    try:
        parse_conf("yolo  X  -  -  -\n")
    except ValueError as e:
        assert "yolo" in str(e)
    else:
        raise AssertionError("unknown tier accepted")


def test_decide_current_when_remote_digest_is_running():
    assert decide("auto", "sha256:a", ["x@sha256:a"], {}, 100, 24) == ("current", {})


def test_decide_unknown_remote_is_error():
    assert decide("auto", "", ["x@sha256:a"], {}, 100, 24)[0] == "error"


def test_decide_new_digest_starts_cooldown():
    action, entry = decide("auto", "sha256:b", ["x@sha256:a"], {}, 1000, 24)
    assert action == "waiting" and entry == {"digest": "sha256:b", "first_seen": 1000}


def test_decide_waits_until_cooldown_has_passed():
    entry = {"digest": "sha256:b", "first_seen": 1000}
    assert decide("auto", "sha256:b", ["x@sha256:a"], entry, 1000 + 23 * 3600, 24)[0] == "waiting"
    assert decide("auto", "sha256:b", ["x@sha256:a"], entry, 1000 + 24 * 3600, 24)[0] == "apply"


def test_decide_tag_moved_again_restarts_cooldown():
    entry = {"digest": "sha256:b", "first_seen": 1000}
    action, new = decide("auto", "sha256:c", ["x@sha256:a"], entry, 1000 + 2 * DAY, 24)
    assert action == "waiting" and new == {"digest": "sha256:c", "first_seen": 1000 + 2 * DAY}


def test_decide_holds_back_a_digest_that_was_rolled_back():
    entry = {"digest": "sha256:b", "first_seen": 0, "bad": True}
    assert decide("auto", "sha256:b", ["x@sha256:a"], entry, 9 * DAY, 24)[0] == "held_back"


def test_decide_notify_and_edge_never_apply():
    for tier in ("notify", "edge"):
        assert decide(tier, "sha256:b", ["x@sha256:a"], {"digest": "sha256:b", "first_seen": 0}, 9 * DAY, 24)[0] == "available"


def test_decide_edge_auto_applies_like_auto():
    assert decide("edge-auto", "sha256:b", ["x@sha256:a"], {"digest": "sha256:b", "first_seen": 0}, 9 * DAY, 24)[0] == "apply"


def test_crosses_major():
    assert crosses_major("2.11.7", "3.0.0")
    assert crosses_major("v1.2", "v2.0")
    assert not crosses_major("2.11.7", "2.12.0")
    assert not crosses_major("", "3.0.0")
    assert not crosses_major("latest", "nightly")


def test_update_command_names_services_and_backups():
    row = {"project": "Home-Assistant", "services": ["homeassistant"], "backups": ["./config"]}
    assert update_command(row) == f"SERVICES='homeassistant' {SCRIPTS}/update-project.sh {DOCKER}/Home-Assistant pull ./config"
    assert update_command({"project": "DDNS", "services": [], "backups": []}) == f"{SCRIPTS}/update-project.sh {DOCKER}/DDNS pull"


def test_repo_is_found_from_the_script_location():
    assert SCRIPTS == Path(__file__).resolve().parent and DOCKER == SCRIPTS.parent.parent


def test_pick_digest_prefers_the_platform_manifest_the_running_image_was_pulled_by():
    m = {"digest": "sha256:index", "manifests": [
        {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
        {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}}]}
    assert pick_digest(m, ["x@sha256:amd"], "amd64") == "sha256:amd"
    assert pick_digest(m, ["x@sha256:amd"], "arm64") == "sha256:index"
    assert pick_digest(m, ["x@sha256:old"], "amd64") == "sha256:index"
    assert pick_digest({"digest": "sha256:single"}, ["x@sha256:old"], "amd64") == "sha256:single"


def test_past_deadline():
    at = lambda hh, mm: time.struct_time((2026, 10, 25, hh, mm, 0, 6, 298, -1))  # noqa: E731
    assert not past_deadline("", at(23, 59))
    assert not past_deadline("04:16", at(4, 15))
    assert past_deadline("04:16", at(4, 16))
    assert past_deadline("04:16", at(5, 0))
    assert not past_deadline("04:16", at(3, 59))


def test_render_prom_one_series_per_ref_and_valid_labels():
    out = render_prom([
        {"project": "DDNS", "tier": "auto", "ref": 'a"b', "result": "updated", "from": "sha256:aaaaaaaaaaaaaaaa", "to": "sha256:bbbbbbbbbbbbbbbb"},
    ], 1234, "fastpi")
    assert 'auto_update_result{host="fastpi",project="DDNS",tier="auto",ref="a\\"b",result="updated",from="aaaaaaaaaaaa",to="bbbbbbbbbbbb"} 1' in out
    assert 'auto_update_last_run_timestamp_seconds{host="fastpi"} 1234' in out
    assert out.endswith("\n")


def _rows(n, result="available"):
    return [{"project": f"P{i}", "tier": "notify", "ref": f"registry.example.com/org/image-{i}:latest", "result": result,
             "from": "a" * 12, "to": "b" * 12, "command": "x" * 150} for i in range(n)]


def test_render_prom_caps_noteworthy_rows_and_counts_omitted():
    out = render_prom(_rows(80), 1, "beefy")
    assert out.count("auto_update_result{") < 80
    kept = out.count("auto_update_result{")
    assert f'auto_update_omitted{{host="beefy"}} {80 - kept}\n' in out


def test_render_prom_keeps_failures_before_the_rest():
    rows = _rows(80) + [{"project": "Bad", "tier": "auto", "ref": "bad:1", "result": "rolled_back", "from": "a", "to": "b"},
                        {"project": "Worse", "tier": "auto", "ref": "worse:1", "result": "rollback_failed", "from": "a", "to": "b"}]
    out = render_prom(rows, 1, "fastpi")
    assert 'project="Bad"' in out and 'project="Worse"' in out


def test_render_prom_never_drops_quiet_rows_and_omits_nothing_when_small():
    out = render_prom(_rows(3) + _rows(100, "current"), 1, "fastpi")
    assert out.count('result="current"') == 100
    assert 'auto_update_omitted{host="fastpi"} 0\n' in out


def test_render_prom_every_series_carries_the_host():
    out = render_prom(_rows(2) + _rows(2, "deferred"), 1, "beefy")
    samples = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert samples and all(ln.startswith(("auto_update_result{host=\"beefy\",", "auto_update_omitted{host=\"beefy\"}", "auto_update_last_run_timestamp_seconds{host=\"beefy\"}")) for ln in samples)
    assert out.count('result="deferred"') == 2



def test_build_refs_are_the_images_of_services_with_a_build_section():
    cfg = {"services": {"app": {"image": "ghcr.io/example/app:v1"},
                        "app-helper": {"image": "app-helper:local", "build": {"context": "."}},
                        "nameless": {"build": {"context": "."}}}}
    assert build_refs(cfg) == {"app-helper:local"}
    assert build_refs({}) == set()


def test_render_prom_has_no_help_lines():
    assert "# HELP" not in render_prom(_rows(3), 1, "fastpi")


def test_rollback_failed_message_is_titled_bulleted_and_escaped():
    msg = rollback_failed_message("A<b>&", DOCKER / "A")
    assert msg.startswith("🔴 <b>Auto-update: A&lt;b&gt;&amp; is down</b>\n\n• ")
    assert "<blockquote expandable>" in msg and "<code>cd " in msg
    assert "• Update failed verification: customizations missing: a.b\n" in rollback_failed_message("A", DOCKER / "A", "customizations missing: a.b")

def test_failure_reason_names_the_missing_customizations():
    out = "  after : seerr running nohc\n  check : OK (x)\n  custom: FAIL seerr.oidc-login seerr.bar-js\n    FAIL seerr.oidc-login got: false\nROLLBACK seerr\n"
    assert failure_reason(out) == "customizations missing: seerr.oidc-login seerr.bar-js"
    assert failure_reason("  check : FAIL (curl x)\nROLLBACK a\n") == "check failed"
    assert failure_reason("  after : a exited -\nROLLBACK a\n") == "containers not running"
    assert failure_reason("OK a\n") == ""


def test_render_prom_carries_the_reason_of_a_rollback():
    out = render_prom([{"project": "Seerr", "tier": "auto", "ref": "s:1", "result": "rolled_back", "from": "a", "to": "b",
                        "reason": "customizations missing: seerr.oidc-login"}], 1, "beefy")
    assert ',reason="customizations missing: seerr.oidc-login"} 1' in out


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in fns:
        try:
            f()
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"FAIL {n}")
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)

