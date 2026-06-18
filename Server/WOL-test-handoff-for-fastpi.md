# Handoff: beefy poweroff + WOL (S5) test — for fastpi

> **Why this file exists:** the test below **powers beefy fully off**, which drops the
> SSH/Claude session on beefy and may wipe that conversation's history. This doc is the
> resume point. It is committed to `tomigorn/HomeLab-BeefyServer` so **fastpi can `git pull`
> and read it** — fastpi is the box that sends the wake packet, beefy is the box that sleeps.
>
> Full background and the complete procedure live in `5-hibernation.md` and `6-WOL.md`
> (those use placeholder MAC/IPs). **This file has the real values.**
>
> _Last updated: 2026-06-18._

## beefy facts you need

| Thing | Value |
|-------|-------|
| Hostname / user | `beefy` / `buntu` |
| LAN IP | `192.168.1.102` |
| NIC | `enp6s0` (Realtek RTL8111/8168, `r8169` driver) |
| **MAC (wake target)** | **`74:56:3c:96:79:a3`** |
| Chosen sleep strategy | **`poweroff` + WOL (S5)** — stateless Docker host, lowest power, cleanest recovery |
| Motherboard / BIOS | Gigabyte H510M H V2, AMI UEFI **F3 (2023-12-20)** |

## State as of last session (what's already done on beefy)

- ✅ WOL enabled on `enp6s0` → `Wake-on: g`.
- ✅ Persistent unit `/etc/systemd/system/wol@.service` enabled as `wol@enp6s0.service`
  (re-arms `ethtool -s enp6s0 wol g` at every boot, bound to the NIC device unit).
- ✅ Docker `enabled` at boot; only running container is `portainer_agent`
  (restart policy `always`) → self-recovers after a cold boot.
- ⏳ **Firmware not yet confirmed** (needs physical access to BIOS — see below).
- ⏳ **Electricity reading** needs a physical smart plug / watt meter.

## What fastpi needs (do this once, before the test)

```bash
# install a magic-packet sender on fastpi
sudo apt update
sudo apt install -y wakeonlan      # etherwake is an alternative
```

**To read these docs on fastpi**, fastpi needs a clone of `tomigorn/HomeLab-BeefyServer`:

```bash
# CONFIRM the path on fastpi — TBD; if no clone exists yet, create one:
git clone git@github.com:tomigorn/HomeLab-BeefyServer.git
# thereafter, before each session:  git -C <clone> pull
```

**Reaching beefy by name:** commands below use `ssh buntu@beefy`. If `beefy` doesn't resolve
on fastpi (no DNS/mDNS/`/etc/hosts` entry), use the IP directly: `ssh buntu@192.168.1.102`.
`wakeonlan` always uses the MAC, so it works regardless of name resolution.

## ⭐ The wake command (run on fastpi)

```bash
wakeonlan 74:56:3c:96:79:a3
```

> WOL is **layer-2 only** — the magic packet is a LAN broadcast and does **not** route
> across subnets. fastpi and beefy must be on the same LAN segment (they are: `192.168.1.x`).

## Test sequence

1. **Suspend-wake first (cheap sanity check).**
   - On beefy: `sudo systemctl suspend`
   - On fastpi: `ping -c4 192.168.1.102` (should fail), then `wakeonlan 74:56:3c:96:79:a3`, then ping again until it answers.
2. **Real target — poweroff + WOL (S5).**
   - On beefy: `sudo systemctl poweroff` (drops the session)
   - On fastpi: `wakeonlan 74:56:3c:96:79:a3`
3. **After beefy boots, verify on beefy:**
   ```bash
   systemd-analyze                 # total cold-boot time (firmware+loader+kernel+userspace)
   systemd-analyze blame | head    # slowest userspace units
   sudo docker ps                  # confirm portainer_agent is back up
   ```
4. **Electricity** — read the watt meter: idle-awake vs powered-off (S5 ≈ 0.5–2 W just for the NIC). Software can't read this.

### r8169 caveat (the key gotcha)

The Realtek `r8169` NIC is known to wake from **suspend** but **not from S5** unless the
firmware keeps it on standby power. So:

- **If suspend (step 1) wakes but poweroff (step 2) does NOT** → ErP is still enabled, or
  BIOS F3 lacks S5 WOL. Fix in firmware (below), or consider a BIOS update.

## Pending firmware steps (physical access to beefy's BIOS — nobody can do remotely)

Gigabyte H510M H V2, F3 → Advanced Mode → **Settings → Platform Power**:

- **ErP = Disabled** — **mandatory.** Without it there's no S5 standby power → no WOL.
- **Wake on LAN = Enabled** — if present on F3; otherwise ErP-off + the OS `ethtool wol g` suffices.
- **AC BACK = Always On** — optional, for auto-recovery after a mains outage.

## Resume checklist (when picking this back up after a wake)

- [ ] Did beefy wake from **poweroff** (not just suspend)? If only suspend worked, fix ErP in BIOS.
- [ ] `systemd-analyze` cold-boot time recorded.
- [ ] `sudo docker ps` shows `portainer_agent` running.
- [ ] Watt-meter readings collected (idle-awake vs off).
- [ ] Note: `sudo` on beefy needs a TTY (buntu is intentionally **not** passwordless) — privileged steps are run by tomigorn, not automated.
