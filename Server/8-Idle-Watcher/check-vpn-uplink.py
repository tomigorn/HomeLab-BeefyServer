#!/usr/bin/env python3
"""Prints "<VPN_UPLINK> ok" when the idle watcher resolves the VPN container uplink to a real NIC (post-update check host.idle-vpn-uplink), else "<VPN_UPLINK> BAD:<nic>"."""
import sys
sys.path.insert(0, "/usr/local/sbin")
import beefy_idle_watcher as w

env = {}
for line in open("/etc/beefy-idle.conf"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
cfg = w.load_config(env)
pid = w._container_pid(cfg["VPN_CONTAINER"])
route = open(f"/proc/{pid}/net/route").read() if pid else None
dev = open(f"/proc/{pid}/net/dev").read() if pid else ""
up = w.vpn_uplink(cfg["VPN_UPLINK"], route)
nics = {line.split(":", 1)[0].strip() for line in dev.splitlines() if ":" in line}
print(cfg["VPN_UPLINK"], "ok" if up and up != cfg["VPN_TUNNEL"] and up in nics else f"BAD:{up}")
