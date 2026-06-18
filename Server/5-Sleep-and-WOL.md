# Beefy sleep & wake — poweroff (S5) + Wake-on-LAN

How **beefy** saves power: instead of running 24/7 it **fully powers off (S5)** when idle and is
**woken on demand by a Wake-on-LAN magic packet from `fastpi`**. Both directions are driven from
fastpi — fastpi can power beefy off *and* wake it. beefy is a **stateless Docker host** (every
container restarts itself from disk on boot), so a full power-off loses nothing.

> **Status: built & verified 2026-06-18.** Power-off + WOL works end-to-end; fastpi can also trigger
> the power-off with no password. Remaining work is the *automation* layer (idle → sleep,
> request → wake) — see [§8](#8-future--traefik-driven-idle-automation).

---

## 1. Operating it from fastpi (quick reference)

```bash
ssh beefy-poweroff            # power beefy OFF  (full S5 power-off, no password prompt)
wakeonlan 74:56:3c:96:79:a3   # wake it back ON  (~51 s cold boot to usable SSH)
```

> This is a **power-off**, not suspend/sleep — waking is a full ~51 s cold boot, not an instant
> resume (by design; see §2). WOL is **layer-2 only**: fastpi and beefy must share the LAN segment
> (they do — `192.168.1.x`). The magic packet uses the MAC, so it works regardless of name resolution.

### Box facts

| Thing | Value |
|-------|-------|
| Host / user | `beefy` / `buntu` |
| LAN IP | `192.168.1.102` |
| NIC | `enp6s0` (Realtek RTL8111/8168, `r8169` driver) |
| **MAC (wake target)** | **`74:56:3c:96:79:a3`** |
| Motherboard / BIOS | Gigabyte H510M H V2, AMI UEFI **F3** |
| OS | Ubuntu 26.04 |
| Wake sender | `fastpi` (always-on Pi, same LAN, `192.168.1.2`) |

---

## 2. Why poweroff + WOL (S5) — strategy decision

beefy has no precious in-RAM state, so the choice is about **reliability and power**, not state
preservation. Comparison of the options that were considered:

| Strategy | Power asleep¹ | Time to fully awake² | Docker behaviour on wake | Survives power loss |
|---|---|---|---|---|
| S3 — suspend-to-RAM | ~5–10 W | ~1–3 s | thaws, but a wall-clock *time-jump* → cron storms, healthcheck flaps, dropped sockets | ❌ blip = RAM lost |
| S4 — hibernate | ≈ off, ~1–3 W | ~20–60 s | same time-jump/reconnect issues after restore | ✅ image on disk |
| suspend-then-hibernate | ~5–10 W → ≈ off | fast early, ~20–60 s later | S3-like early, S4-like later | ✅ once hibernated |
| ⭐ **poweroff + WOL (S5)** | **lowest (measured ~0 W, see §4)** | ~51 s cold boot | **cleanest** — fresh boot, correct clock, `restart:` policies bring containers up with no time-warp | ✅ already off |

¹ Wake time does **not** grow with how long it slept. ² Measured numbers in §4.

**Chosen: ⭐ poweroff + WOL (S5).** Lowest power, most reliable (no swap / `resume_offset` /
initramfs fragility to break on kernel upgrades), cleanest Docker behaviour (cold boot = correct
clock + fresh container start, no time-warp or stale connections), and no unencrypted RAM image on
disk.

**Why S4 hibernate was rejected.** It writes the *entire contents of RAM* to `/swap.img` on the
unencrypted OS disk (secrets/keys persisted in the clear), and adds swap-resize / `resume_offset` /
initramfs machinery that breaks on kernel upgrades — all to preserve RAM state this host doesn't
need. The full S4 setup procedure was removed from this doc; if ever needed it is in git history
(`5-hibernation.md`, before the 2026-06-18 consolidation).

---

## 3. As-built configuration (the working setup)

### 3.1 Wake-on-LAN armed via netplan (NOT a systemd unit — see the gotcha)

WOL is armed by **netplan**, which makes NetworkManager keep `Wake-on: g` on every boot:

```yaml
# /etc/netplan/00-installer-config.yaml
network:
  version: 2
  ethernets:
    enp6s0:
      dhcp4: true
      dhcp6: true
      wakeonlan: true                       # <-- arms WOL
      match: { macaddress: 74:56:3c:96:79:a3 }
      set-name: enp6s0
```

> ⚠️ **The gotcha that cost us a debugging session.** The original approach was a systemd template
> unit (`wol@enp6s0.service`) running `ethtool -s enp6s0 wol g`. It **silently failed**: the unit
> reported success, but `ethtool` showed `Wake-on: d` (disabled). Cause — `enp6s0` is managed by
> **NetworkManager**, which **resets the NIC's Wake-on flag back to `d`** when it activates the
> connection, *after* the unit had set `g`. Provable on the fly:
> `sudo ethtool -s enp6s0 wol g` → `g`, then `sudo nmcli connection up netplan-enp6s0` → `d`.
> **Fix:** let NetworkManager own WOL via the netplan `wakeonlan: true` above (NM then maintains
> `Wake-on: g` itself), and retire the systemd unit:
> `sudo systemctl disable --now wol@enp6s0.service`. One source of truth, survives reboots.

Verify (needs sudo for the `ethtool` read):
```bash
sudo ethtool enp6s0 | egrep -i 'Supports Wake-on|Wake-on|Link detected'
#   want:  Supports Wake-on: pumbg  /  Wake-on: g  /  Link detected: yes
```

### 3.2 BIOS — ErP Disabled (mandatory for S5 wake)

The OS-side `wol g` only reliably covers wake-from-**suspend**. To wake from **poweroff (S5)** the
firmware must keep the NIC on standby power: Gigabyte H510M H V2 (F3) → Advanced Mode →
**Settings → Platform Power → ErP = Disabled** (and **Wake on LAN = Enabled** if present; optional
**AC BACK = Always On** for auto power-on after a mains outage). Without ErP off, suspend-wake works
but poweroff-wake does not — this is the classic `r8169` S5 caveat. Set at the physical BIOS only.

### 3.3 Boot hygiene — `multi-user.target`

```bash
sudo systemctl set-default multi-user.target
```
Headless Docker host → no graphical target. Besides trimming boot, it removes a hang where
`plymouth-quit-wait.service` blocked boot from ever reaching "finished" (so `systemd-analyze` never
printed a total) even though SSH/Docker were already up.

### 3.4 Containers restart on boot

Docker is `enabled` at boot and every container has a restart policy (the only running one,
`portainer_agent`, is `always`), so the stack self-recovers after a cold boot. Verify:
```bash
systemctl is-enabled docker        # enabled
docker ps --format '{{.Names}}: {{.RestartPolicy}}'   # all unless-stopped/always
```

### 3.5 Remote poweroff from fastpi (no password, locked-down key)

So fastpi can *trigger* the power-off (the wake was always fastpi's job). Least-privilege design —
a key that can do **exactly one thing**:

1. **Dedicated keypair on fastpi:** `~/.ssh/beefy-poweroff` (ed25519, no passphrase; private key
   never leaves fastpi). Convenience alias `Host beefy-poweroff` in fastpi's `~/.ssh/config`.
2. **Forced command on beefy** (`~buntu/.ssh/authorized_keys`) — the key is pinned to one command
   and stripped of pty/forwarding, so whatever the client sends is ignored:
   ```
   restrict,command="sudo /usr/bin/systemctl poweroff" ssh-ed25519 AAAA…q fastpi-beefy-poweroff
   ```
3. **Narrow sudoers on beefy** (`/etc/sudoers.d/fastpi-poweroff`, mode 440):
   ```
   buntu ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
   ```
   buntu may run *only* `systemctl poweroff` without a password — nothing else is NOPASSWD.

**Threat model:** if the fastpi key leaked, the only thing it grants is "power beefy off" — a
recoverable nuisance (just WOL it back), never a shell or other sudo. The admin key (`~/.ssh/beefy`)
is separate and unrestricted. This is the exact primitive the future Traefik automation (§8) calls.

### 3.6 Post-wake gotcha — exclude `/srv/**` from VS Code server

After a wake, VS Code server re-indexes the storage pools and pins every core (ripgrep/node). Fix —
`~/.vscode-server/data/Machine/settings.json`:
```json
{ "files.watcherExclude": { "/srv/**": true },
  "search.exclude":       { "/srv/**": true },
  "search.followSymlinks": false }
```

---

## 4. Power & timing measurements (inline meter, 2026-06-18)

**Power draw** (whole machine at the wall; cold HDD spindown active — see §6):

| State | Draw |
|---|---|
| Freshly booted (peak) | **41 W**, settling quickly to **~28.1 W** |
| Idle ~10 min after boot | **~22.3–22.5 W** |
| Idle ≥10 min, SSH disconnected | **~22.2–22.4 W** (barely lower than connected-idle) |
| **Powered off (S5) via `ssh beefy-poweroff`** | **~0 W reported** (meter may not read below ~2 W; true S5 draw is likely ~0.5–2 W for NIC standby) |

So running-but-idle costs **~22 W continuously**; powered off it's **≈ 0–2 W**. The ~20 W delta is
the saving for every hour beefy is asleep instead of idling (~0.5 kWh/day if off ~24 h).

**Cold-boot time** (`systemd-analyze`, with `multi-user.target` default):
```
31.854 s firmware + 2.435 s loader + 3.915 s kernel + 1.633 s initrd + 10.974 s userspace = 50.8 s
```
Firmware POST dominates (~32 s, ~63%) and is independent of how long the box was off.

**Wake-from-packet latency** (fastpi sends WOL → beefy reachable):
- from **suspend (S3)**: ping/SSH back **~8 s**.
- from **poweroff (S5)**: ping/SSH back **~51 s** (the cold boot above).

---

## 5. How it was verified

All driven from fastpi (fastpi sends the packet; sudo/BIOS steps run at beefy's keyboard).

- **Persistence across a reboot** — `Wake-on: g` survives a plain reboot with the `wol@` unit
  *disabled*, proving netplan/NM alone arms it. (This test is what exposed the NM-clobber bug in §3.1.)
- **Wake from suspend (S3)** — `sudo systemctl suspend` on beefy → fastpi `wakeonlan` → back in ~8 s.
- **Wake from poweroff (S5)** — `sudo systemctl poweroff` → fastpi `wakeonlan` → cold boot back in
  ~51 s, `uptime` reset (a true cold boot, not a resume). ErP=Disabled was the enabling change.
- **Full fastpi-driven cycle** — `ssh beefy-poweroff` powered beefy off (no password), then
  `wakeonlan` brought it back. Both directions confirmed from fastpi.

---

## 6. HDD spindown (the other half of the power story)

While the *machine* sleeps via poweroff, the **cold 28 TB HDD also parks itself** (`hd-idle`,
`STANDBY`) whenever idle, so even while beefy is awake the drive isn't burning power or wearing.
The power figures in §4 were taken with spindown active. Details, plus the non-waking spin-state
logger and the live `hdd-spinwatch` viewer, are in
**[`3-Storage-Layout-and-Spindown.md`](3-Storage-Layout-and-Spindown.md) §11**.

---

## 7. Wiring it up from scratch (rebuild commands)

```bash
# --- on beefy: arm WOL via netplan ---
sudoedit /etc/netplan/00-installer-config.yaml        # add `wakeonlan: true` (see §3.1)
sudo netplan generate
sudo grep -i wake-on-lan /run/NetworkManager/system-connections/netplan-enp6s0.nmconnection  # want: wake-on-lan=1
sudo netplan apply
sudo systemctl disable --now wol@enp6s0.service       # retire the old unit if present
sudo systemctl set-default multi-user.target          # headless boot hygiene

# --- BIOS (physical): Settings → Platform Power → ErP = Disabled ---

# --- on fastpi: dedicated poweroff key + alias ---
ssh-keygen -t ed25519 -f ~/.ssh/beefy-poweroff -N "" -C "fastpi-beefy-poweroff"
cat >> ~/.ssh/config <<'EOF'

Host beefy-poweroff
    HostName beefy
    User buntu
    IdentityFile ~/.ssh/beefy-poweroff
    IdentitiesOnly yes
    PreferredAuthentications publickey
    RequestTTY no
EOF

# --- on beefy: install the forced-command key + narrow sudoers ---
printf '\n%s\n' 'restrict,command="sudo /usr/bin/systemctl poweroff" '"$(ssh fastpi cat ~/.ssh/beefy-poweroff.pub)" \
  >> ~/.ssh/authorized_keys                            # or paste the pubkey line manually
echo 'buntu ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff' | sudo tee /etc/sudoers.d/fastpi-poweroff
sudo chmod 440 /etc/sudoers.d/fastpi-poweroff
sudo visudo -cf /etc/sudoers.d/fastpi-poweroff         # must say: parsed OK

# --- test from fastpi ---
ssh beefy-poweroff ; sleep 30 ; wakeonlan 74:56:3c:96:79:a3
```

---

## 8. Future — Traefik-driven idle automation

The intended end-state (not built yet) is **idle-based auto power management** with Traefik on
fastpi fronting beefy's services:

- **Sleep:** no traffic routed to beefy for N minutes → fastpi calls `ssh beefy-poweroff`.
- **Wake:** a request arrives for a beefy service while it's off → fastpi sends the WOL packet, holds
  the request behind a "warming up" page (~51 s, the §4 cold-boot time) until healthchecks pass,
  then proxies through.

This **mechanism** is fully in place (both off and on are driven from fastpi). What remains is the
*glue*: it depends on Pi→beefy Traefik routing existing first, plus idle-detection and the
wake-on-request hold (Traefik can't do WOL-on-demand natively — typically a small helper such as a
Sablier-style on-demand middleware, or a custom forward-auth/proxy that fires the WOL and waits for
readiness).
