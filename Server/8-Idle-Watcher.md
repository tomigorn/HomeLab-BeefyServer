# Beefy idle-watcher — self-monitored sleep (design)

> **Status:** design / spec. Implements the "idle → sleep" half of wake-on-demand.
> The "request → wake" half is **done** on fastpi (`Docker/Beefy-Waker` +
> Traefik `beefy-wake` forwardAuth middleware). Together they close the loop:
> beefy powers itself off when idle, and any request to a beefy service wakes it.

This revises [§8 of `5-Sleep-and-WOL.md`](5-Sleep-and-WOL.md): the idle-detection
side is **beefy-driven** (beefy watches its own activity), not Traefik-driven.
Only the *wake* is driven from fastpi. Rationale: beefy has the ground truth
about what's actually happening on it (connections, logins, jobs, disk), so the
"am I idle?" decision lives where the evidence is.

## Goal

beefy runs only when it's doing something. When nothing has used it for **15
minutes**, it `systemctl poweroff`s (S5). WOL stays armed across poweroff
(netplan `wakeonlan: true`, see 5-Sleep-and-WOL.md §3.1), so the next request
re-wakes it via the fastpi Traefik path. Saves the ~20 W idle draw measured in
5-Sleep-and-WOL.md §4.

## What it is

A **host-level systemd service** on beefy — `beefy-idle-watcher` — a single
Python-stdlib daemon running as **root** (so it can read all connections,
`/proc/diskstats`, run `docker`, and call `systemctl poweroff`). Not a container:
powering off the host and seeing all host activity is the host's own job;
containerising it would need privileged + host net/pid + docker.sock for no gain.

Lives in this repo under `Server/8-Idle-Watcher/`:

| File | Purpose |
|------|---------|
| `beefy-idle-watcher.py` | The daemon. Stdlib only. |
| `beefy-idle-watcher.service` | systemd unit (root, restart on failure). |
| `beefy-idle.conf` | Tunables (thresholds, idle window, **dry-run flag**). |
| `install.sh` | Copies unit+config into place; prints the one sudo step. |

## The loop

Every `SAMPLE_INTERVAL` (default 60 s):

1. Evaluate the four probes below; each returns **busy** or **idle**.
2. If **any** probe is busy → reset `idle_since = now`.
3. If **all** probes have been idle continuously for ≥ `IDLE_MINUTES` (15) →
   log the verdict, and:
   - **dry-run on (default):** log `WOULD power off (idle 15m)` and keep running.
   - **dry-run off (armed):** `systemctl poweroff`.

Rate-based probes (CPU, disk, net) compare two samples across the interval, so
the loop's own cadence provides the deltas — no extra sampling threads.

## The four probes

Each is small and independent; a probe answers exactly "is *this* signal busy?"

1. **Inbound service connections** — established TCP whose *local* port is one of
   beefy's own listening service ports (discovered live from `ss -ltn`), excluding
   `22`. This is "someone is connected to a service" (a stream, a UI). Using the
   listening-port set means no per-app config and it ignores beefy's *outbound*
   connections (e.g. apt, telemetry to `:443`) and its automation SSH.

2. **Interactive presence** — busy if EITHER:
   - an interactive SSH login exists: an OpenSSH per-session title
     `sshd-session: <user>@pts/N` in `ps -eo args` (a real pty shell), OR
   - **VS Code Remote** is attached: a `.vscode-server/.../out/server-main.js` node
     process is running.

   (`who`/utmp is **empty** on this systemd box — utmp is deprecated — so the obvious
   `who` probe is blind; verified. `loginctl` was also rejected: it labels automation
   SSH and `manager` sessions the same as real logins, so it over-counts.)
   Non-interactive automation (`ssh host 'cmd'`, fastpi's poweroff key) is `@notty`
   with no VS Code server, so it is correctly ignored. **VS Code Remote counts as
   in-use by policy** (the primary way the box is operated); VS Code's own
   `--enable-remote-auto-shutdown` drops the connection when the user is away, which
   then lets beefy sleep. Logged as separate `ssh=` and `vscode=` fields.

3. **Background jobs** — CPU utilisation across the interval > `CPU_BUSY_PCT`, OR
   LAN throughput (`/proc/net/dev` delta on the primary NIC) > `NET_BUSY_KBPS`.
   Catches transcodes / library imports / byte-heavy work with no client attached.

4. **Active disk I/O** — sustained read+write across the data disks
   (`/proc/diskstats` delta) > `DISK_BUSY_KBPS`. Catches an in-progress download
   writing to disk. Aligns with HDD spindown: a parked disk does no I/O = idle.

**Manual inhibit:** if `INHIBIT_FILE` (default `/run/beefy-keep-awake`) exists,
the watcher treats beefy as busy unconditionally. `touch` it to pin beefy awake
for maintenance without holding an SSH session; delete it (or reboot — it's on
tmpfs) to release.

## Config (`/etc/beefy-idle.conf`)

Shell-style `KEY=value`, sourced by the daemon. Defaults:

```sh
DRY_RUN=1                 # 1 = log only (default until validated); 0 = actually poweroff
IDLE_MINUTES=15
SAMPLE_INTERVAL=60        # seconds
CPU_BUSY_PCT=15           # whole-host CPU% over the interval
NET_BUSY_KBPS=200         # primary-NIC throughput
DISK_BUSY_KBPS=2000       # data-disk read+write
PRIMARY_NIC=enp6s0
DATA_DISKS="sda sdb sdc"  # basenames in /proc/diskstats (tune to actual data disks)
EXCLUDE_PORTS="22"        # ports the inbound-conn probe ignores
INHIBIT_FILE=/run/beefy-keep-awake
```

Thresholds are first-guess and **will be tuned from real dry-run logs** before
arming. All defaults are deliberately conservative (err toward "busy").

## Observability

Every cycle logs a one-line verdict to journald (`journalctl -u
beefy-idle-watcher`), e.g.:

```
idle=12m  conns=0 ssh=0 cpu=3% net=4kbps disk=0kbps inhibit=0  -> idle
busy: disk=5300kbps                                            -> reset (download)
WOULD power off (idle 15m, dry-run)                            -> stay up
```

So every decision — and every *near* decision — is auditable. This is what makes
threshold tuning data-driven rather than guesswork.

## Safety / edge cases

- **Dry-run first (default).** Ships log-only. We watch a real day of verdicts,
  confirm it would only have slept at genuinely-idle times, tune thresholds, then
  flip `DRY_RUN=0`. This is why building+installing it carries no shutdown risk.
- **Mid-shutdown request** is lost, but the next request re-wakes beefy via
  Traefik. The 15-min window makes sleep/wake flapping after a boot impossible in
  practice.
- **Never sleeps** while an interactive SSH session exists or the inhibit file is
  present — you can't get powered off from under your own shell.
- **Coverage gap (explicit):** a task that is simultaneously low-CPU, low-disk,
  low-net, with no inbound client — e.g. idle-but-queued torrent *seeding* —
  would read as idle. Out of scope now: beefy isn't the seedbox (fastpi is, per
  the migration plan). If a public-torrent client later lands on beefy, add a
  per-app probe (query its API for active transfers) as a fifth probe.

## Install (the usual split — I stage, you run the one sudo step)

```sh
# files are already in ~/Projects/Server/8-Idle-Watcher/ (committed)
cd ~/Projects/Server/8-Idle-Watcher
sudo ./install.sh        # cp unit+conf into place, daemon-reload, enable --now
# observe (still dry-run):
journalctl -u beefy-idle-watcher -f
```

`install.sh` copies `beefy-idle-watcher.py` to `/usr/local/sbin/`, the unit to
`/etc/systemd/system/`, and `beefy-idle.conf` to `/etc/` (only if absent, so it
never clobbers tuned values), then `systemctl daemon-reload && enable --now`.

## Rollout

1. Install in **dry-run** (default). 
2. Watch `journalctl` across a representative day; confirm "would sleep" only at
   real idle and never during use/downloads. Tune thresholds in
   `/etc/beefy-idle.conf` (then `systemctl restart beefy-idle-watcher`).
3. Once confident, set `DRY_RUN=0`, restart, verify a real idle → poweroff, and
   that a request wakes it back (closes the loop with `Beefy-Waker`).
4. Update 5-Sleep-and-WOL.md §8 to "done" (and the project's working notes).

## Operations & known limitations

**Current state:** ARMED — `DRY_RUN=0` in `/etc/beefy-idle.conf`, `IDLE_MINUTES=15`, daemon
**v1.1.0** (monotonic idle clock). beefy powers off (S5) after 15 min fully idle; WoL wakes it.

**Controls**
- **Logs:** `journalctl -u beefy-idle-watcher -f` (live) or `-b | grep start:` (version banners).
- **Keep awake** (unattended job): `sudo touch /run/beefy-keep-awake`; release: `sudo rm` it (clears on reboot).
- **Disarm:** `DRY_RUN=1` in `/etc/beefy-idle.conf` + `sudo systemctl restart beefy-idle-watcher`.
- **Tune:** change `IDLE_MINUTES` / thresholds in the same file + restart.

**Known limitations** (full cross-system review on fastpi:
`HomeLab-FastPi` -> `Docker/Beefy-Waker/docs/2026-06-26-power-management-review.md`)
- **Sleeps mid-job** if a detached job is below all thresholds (CPU<15% / net<200kB/s /
  disk<2000kB/s) with no SSH and no inbound conn — including a paused `apt`/`dpkg` (can corrupt
  the package DB). Mitigation: `touch /run/beefy-keep-awake`; **installed** via an apt `DPkg::Pre-Invoke`/`Post-Invoke` hook
  (`/etc/apt/apt.conf.d/99-beefy-keep-awake`; copy in `8-Idle-Watcher/apt-99-beefy-keep-awake.conf`).
- **Never sleeps** if a persistent connection to a service port lingers (keepalive monitor,
  left-open browser tab, idle WebSocket).
- **`DATA_DISKS=sda,sdb,sdc`** are hardcoded basenames — verify they match beefy's real data
  disks (`lsblk`); a mismatch silently blinds the disk probe.
- **Probe regexes** (`@pts/N`, `server-main.js`) can break silently across OpenSSH / VS Code
  upgrades — re-verify after major updates.
- **No second confirmation** before poweroff (the 15-min *continuous*-idle requirement is the
  safety). A confirmation re-check is a possible future hardening.
