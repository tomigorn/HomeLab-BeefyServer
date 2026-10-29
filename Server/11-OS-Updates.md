# OS updates and headless state (beefy)

| What | Setting | Why |
|---|---|---|
| unattended-upgrades | `-security` + `-updates` (drop-in `11-OS-Updates/52unattended-upgrades-local` → `/etc/apt/apt.conf.d/`), `Automatic-Reboot "false"` | `-updates` are Ubuntu SRUs (staged, tested); security-only left kernel and fixes pending for weeks. Runs with `apt-daily-upgrade.timer` (04:34, maintenance window) |
| Reboot | never by a script | beefy powers off every night (idle-watcher) and fastpi wakes it: a new kernel is active after the next power-on. `/var/run/reboot-required` and Lynis `KRNL-5830` show a pending one |
| Docker CE | not in the allowed origins | an engine restart stops every container; update by hand |
| Desktop | `ubuntu-desktop-minimal` installed, but default target `multi-user.target`, `gdm` disabled | no graphical login; nothing listens for one |
| cloud-init | `/etc/cloud/cloud-init.disabled` (since 2026-06) | installer leftover; no unit runs at boot |

Install: `sudo install -m 0644 Server/11-OS-Updates/52unattended-upgrades-local /etc/apt/apt.conf.d/`
Verify: `apt-config dump | grep -- -updates`, `sudo unattended-upgrade --dry-run -d`; post-update checks `host.os-updates`, `host.headless`.
