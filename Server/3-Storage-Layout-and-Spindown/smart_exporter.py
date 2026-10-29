#!/usr/bin/env python3
"""SMART health of every disk -> Prometheus textfile, without waking a spun-down disk.

smartctl -n standby checks the power mode first (ATA CHECK POWER MODE, a non-data command) and skips a parked disk; its last values come from the state file, marked smart_standby=1, so the series do not vanish while a disk sleeps.
Disks are named by their mountpoint ("/" = system, /srv/.disks/ssd-hot = ssd-hot), so no serial numbers end up in metrics.
Single source: HomeLab-FastPi Docker/grafana/smart-exporter/; beefy runs a verbatim copy (HomeLab-BeefyServer Server/3-Storage-Layout-and-Spindown/smart_exporter.py).
Usage: smart_exporter.py --host fastpi --out /var/lib/node-exporter/textfile/smart_fastpi.prom [--state /var/lib/smart-exporter/state.json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

GAUGES = ("healthy", "temperature_celsius", "power_on_hours", "power_cycles", "start_stop_count", "reallocated_sectors",
          "pending_sectors", "offline_uncorrectable", "media_errors", "crc_errors", "wear_ratio", "nvme_critical_warning")
ATA_RAW = {4: "start_stop_count", 5: "reallocated_sectors", 197: "pending_sectors", 198: "offline_uncorrectable",
           187: "media_errors", 199: "crc_errors"}
# Normalized wear indicators (100 = new): Samsung Wear_Leveling_Count, SSD_Life_Left, Media_Wearout_Indicator.
ATA_WEAR = (177, 231, 233)


def discover(lsblk: dict) -> list[dict]:
    out = []
    for d in lsblk.get("blockdevices", []):
        if d.get("type") != "disk" or d["name"].startswith(("zram", "loop", "ram")):
            continue
        mounts = sorted(m for c in [d] + d.get("children", []) for m in (c.get("mountpoints") or []) if m and m.startswith("/"))
        name = "system" if "/" in mounts else (os.path.basename(mounts[0]) if mounts else d["name"])
        out.append({"dev": "/dev/" + d["name"], "disk": name, "rota": bool(d.get("rota")), "tran": d.get("tran") or "", "model": (d.get("model") or "").strip()})
    return out


def smartctl_cmd(dev: str) -> list[str]:
    return ["smartctl", "-n", "standby", "-j", "-a", dev]


def run_smartctl(dev: str) -> dict:
    r = subprocess.run(smartctl_cmd(dev), capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"smartctl": {"exit_status": r.returncode}}


def asleep(j: dict) -> bool:
    return any("STANDBY mode" in m.get("string", "") or "SLEEP mode" in m.get("string", "")
               for m in j.get("smartctl", {}).get("messages", []))


def parse(j: dict, rota: bool) -> dict | None:
    """Values of one disk; None when it is parked (nothing was read). ValueError when SMART could not be read."""
    if asleep(j):
        return None
    if "smart_status" not in j:
        raise ValueError(f"no SMART data (smartctl exit {j.get('smartctl', {}).get('exit_status')})")
    nvme = j.get("nvme_smart_health_information_log")
    v = {"type": "nvme" if nvme is not None else ("hdd" if rota or j.get("rotation_rate", 0) > 0 else "ssd"),
         "model": j.get("model_name", ""), "healthy": 1 if j["smart_status"].get("passed") else 0}
    if "current" in j.get("temperature", {}):
        v["temperature_celsius"] = j["temperature"]["current"]
    if "hours" in j.get("power_on_time", {}):
        v["power_on_hours"] = j["power_on_time"]["hours"]
    if "power_cycle_count" in j:
        v["power_cycles"] = j["power_cycle_count"]
    if nvme is not None:
        v["media_errors"] = nvme.get("media_errors", 0)
        v["nvme_critical_warning"] = nvme.get("critical_warning", 0)
        if "percentage_used" in nvme:
            v["wear_ratio"] = nvme["percentage_used"] / 100
    for a in j.get("ata_smart_attributes", {}).get("table", []):
        if a["id"] in ATA_RAW:
            v[ATA_RAW[a["id"]]] = a["raw"]["value"]
        if a["id"] in ATA_WEAR and "wear_ratio" not in v and v["type"] == "ssd":
            v["wear_ratio"] = round(1 - a["value"] / 100, 4)
    return v


def esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def collect(host: str, disks: list[dict], read, state_path: str, now: float) -> str:
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    lines, errors = [], 0
    h = f'host="{esc(host)}"'
    for d in disks:
        lab = f'{h},disk="{esc(d["disk"])}"'
        standby = 0
        try:
            v = parse(read(d["dev"]), d["rota"])
        except (ValueError, OSError, subprocess.TimeoutExpired) as e:
            print(f"smart-exporter: {d['disk']} ({d['dev']}): {e}", file=sys.stderr)
            errors += 1
            v = None
        else:
            if v is None:
                standby = 1
            else:
                state[d["disk"]] = {"ts": now, "values": v}
        lines.append(f"smart_standby{{{lab}}} {standby}")
        cached = state.get(d["disk"])
        if not cached:
            continue
        vals = cached["values"]
        lines.append(f'smart_device_info{{{lab},model="{esc(vals.get("model") or d["model"])}",type="{vals["type"]}",transport="{esc(d["tran"])}"}} 1')
        lines.append(f"smart_last_read_timestamp_seconds{{{lab}}} {int(cached['ts'])}")
        lines += [f"smart_{k}{{{lab}}} {vals[k]:g}" for k in GAUGES if k in vals]
    lines += [f"smart_exporter_errors{{{h}}} {errors}", f"smart_exporter_last_run_timestamp_seconds{{{h}}} {int(now)}"]
    write_atomic(state_path, json.dumps(state, indent=1, sort_keys=True))
    return "\n".join(lines) + "\n"


def write_atomic(path: str, text: str, mode: int = 0o644) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--state", default=os.path.join(os.environ.get("STATE_DIRECTORY", "/var/lib/smart-exporter"), "state.json"))
    a = p.parse_args()
    lsblk = json.loads(subprocess.run(["lsblk", "-J", "-o", "NAME,TYPE,TRAN,ROTA,MOUNTPOINTS,MODEL"], capture_output=True, text=True, check=True).stdout)
    write_atomic(a.out, collect(a.host, discover(lsblk), run_smartctl, a.state, time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
