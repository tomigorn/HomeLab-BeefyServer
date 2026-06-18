# beefy poweroff + WOL — test log, method & verification record

> **What this is:** the working record (the "why" and the "how we test it") behind the
> poweroff + WOL setup — kept in the repo so both tomigorn and fastpi can read it, since
> the test itself powers beefy off and can wipe the on-box session/chat history. Action-
> oriented quick-start for fastpi is in `WOL-test-handoff-for-fastpi.md`; the full tutorials
> are `5-hibernation.md` and `6-WOL.md`. **This file is the methodology + resume point.**
>
> _Started 2026-06-18. Append-only log at the bottom — add a dated entry after each test run._

---

## 1. Decision & rationale

- **Chosen strategy: `poweroff` + WOL (S5).** beefy is a **stateless Docker host**, so a full
  power-off is the best sleep model: lowest power draw, no `resume_offset`/initramfs/swap
  machinery to break, no unencrypted RAM image on disk, and a cold boot comes up with a
  correct clock. Wake is a WOL magic packet sent from **fastpi** (always-on Pi on the same LAN).
- **Alternative (documented, not chosen):** suspend-then-hibernate (S4). The full S4 procedure
  stays in `5-hibernation.md` as the shared foundation, but S5 is the target here.
- Trade-off accepted: RAM state is **not** preserved, so every container must restart itself on
  boot (verified below) — that's fine for this host.

## 2. Box facts

| Thing | Value |
|-------|-------|
| beefy LAN IP | `192.168.1.102` |
| NIC | `enp6s0`, Realtek RTL8111/8168, `r8169` driver |
| **MAC (wake target)** | **`74:56:3c:96:79:a3`** |
| Motherboard / BIOS | Gigabyte H510M H V2, AMI UEFI **F3 (2023-12-20)** |
| Wake sender | `fastpi` (same LAN segment, `192.168.1.x`) |

## 3. State as of 2026-06-18

**Applied & verified on beefy (reversible parts — all done):**

- ✅ WOL enabled on `enp6s0` → `Wake-on: g`.
- ✅ Persistent systemd unit `/etc/systemd/system/wol@.service`, enabled as
  `wol@enp6s0.service`, re-arms `ethtool -s enp6s0 wol g` at every boot (bound to the NIC
  device unit, so it fires exactly when the interface appears).
- ✅ Docker `enabled` at boot; only running container is `portainer_agent` (restart policy
  `always`) → self-recovers after a cold boot.

**Pending (firmware / physical / fastpi — cannot be done over SSH):**

- ⏳ **BIOS:** Advanced Mode → `Settings → Platform Power`: **ErP = Disabled** (mandatory — else
  no S5 standby power → no WOL), **Wake on LAN = Enabled** (if present on F3; otherwise ErP-off
  + the OS `ethtool wol g` suffices), optional **AC BACK = Always On** for outage auto-recovery.
- ⏳ `fastpi` needs `wakeonlan` installed.
- ⏳ Electricity reading needs a physical smart plug / watt meter (not readable in software).

---

## 4. Verification method — and why "across reboots" matters

There are **three independent things to verify**, in order. Don't skip to the wake test:
the OS-side config is worthless if it doesn't *persist*, and a poweroff wake won't work if a
plain reboot already loses the arming.

### 4a. Persistence across a normal reboot (do this FIRST, it's safe)

The risk: `ethtool ... wol g` set by hand is **not** persistent — it's the `wol@enp6s0.service`
unit that re-arms it every boot. Confirm the unit actually does its job after a real reboot
*before* trusting it for poweroff.

```bash
# --- on beefy, BEFORE reboot: confirm armed now ---
sudo ethtool enp6s0 | egrep -i 'Supports Wake-on|Wake-on|Link detected'
#   want:  Supports Wake-on: pumbg / Wake-on: g / Link detected: yes
systemctl is-enabled wol@enp6s0.service        # -> enabled
systemctl is-enabled docker                    # -> enabled

sudo reboot

# --- on beefy, AFTER it comes back: confirm it re-armed itself ---
systemctl status wol@enp6s0.service --no-pager # -> active (exited), ExecStart status 0
sudo ethtool enp6s0 | grep -i 'Wake-on:'       # -> Wake-on: g   (proves persistence)
sudo docker ps                                 # -> portainer_agent Up
```

If `Wake-on: g` is **not** present after the reboot, the unit isn't doing its job — fix that
(`6-WOL.md` §systemd unit) before going further. **This step needs no fastpi and no BIOS
changes**, so it's the cheapest confidence check.

### 4b. Wake from suspend (cheap S-state check)

Confirms the magic-packet path (fastpi → LAN → NIC) works at all, without committing to S5.

```bash
# on beefy
sudo systemctl suspend            # becomes unresponsive

# on fastpi
ping -c4 192.168.1.102            # expect 100% loss (asleep)
wakeonlan 74:56:3c:96:79:a3
ping -c4 192.168.1.102            # retry until it answers, then `ssh buntu@beefy` works
```

### 4c. Wake from poweroff (the real S5 target)

```bash
# on beefy
sudo systemctl poweroff           # fully off; drops the session — THIS is the history-loss point

# on fastpi
wakeonlan 74:56:3c:96:79:a3
ping -c4 192.168.1.102            # retry until it answers
```

**Diagnostic logic — `r8169` caveat:** this NIC is known to wake from **suspend** but **not
from S5** unless firmware keeps it on standby power. So:

- suspend (4b) wakes **and** poweroff (4c) wakes → ✅ done.
- suspend wakes but poweroff does **not** → **ErP is still enabled** in BIOS (or F3 lacks S5
  WOL). Disable ErP and retry; if still dead, consider a BIOS update.
- neither wakes → check `Link detected: yes`, switch/cable, and that fastpi is on the same
  LAN segment (WOL is **layer-2 broadcast only**, it does not route across subnets).

### 4d. Measure the payoff

```bash
# on beefy, after a cold WOL boot — wake time:
systemd-analyze                   # firmware + loader + kernel + userspace total
systemd-analyze blame | head      # slowest userspace units
systemd-analyze critical-chain    # critical path to multi-user
# add a few s for the Docker stack to report healthy
```

- **Electricity (physical meter only):** record three readings — **idle awake** (baseline),
  **powered off** (S5 standby, expect ~0.5–2 W just for the NIC), optionally **hibernated** (S4).
  The S5-vs-idle delta over a typical day is the real saving. Wake time does **not** depend on
  how long it slept.

---

## 5. Resume checklist (when picking this back up after a wake)

- [ ] Did beefy wake from **poweroff** (4c), not just suspend? If only suspend → fix ErP in BIOS.
- [ ] `Wake-on: g` still present after a plain reboot (4a)? (persistence confirmed)
- [ ] `systemd-analyze` cold-boot time recorded in the log below.
- [ ] `sudo docker ps` shows `portainer_agent` running.
- [ ] Watt-meter readings collected (idle-awake vs off).
- [ ] **`sudo` on beefy needs a TTY** — buntu is intentionally **not** passwordless, so the
      privileged steps are run by tomigorn at the keyboard, not automated by Claude.

---

## 6. Test run log (append-only)

> Add one dated entry per attempt: what was run, what happened, measured numbers, next step.

- **2026-06-18** — Setup applied & verified (§3 ✅ items). Docs written and pushed. BIOS,
  fastpi `wakeonlan`, and watt meter still pending. No wake test run yet — _next: 4a (reboot
  persistence), then 4b (suspend), then 4c (poweroff)._
