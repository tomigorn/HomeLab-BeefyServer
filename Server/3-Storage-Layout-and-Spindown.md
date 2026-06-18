# Storage Layout & HDD Spindown

Full storage design for **beefy** and how the 28 TB HDD is kept asleep almost all
of the time.

> **Supersedes the earlier "SSD as cache for HDD" (bcache) approach.** For a
> spin-down-first media server, a block cache (bcache/dm-cache) is the *wrong* tool:
> it keeps the HDD structurally in the I/O path (cache misses, metadata eviction, and
> writeback flushes all wake it), and writeback couples the HDD's integrity to the
> cache SSD. We use **mergerfs tiering** instead — independent filesystems unioned into
> one mount — so the HDD is a self-contained archive the system only touches for an
> actual cold-content read.

> ⚠️ **This process wipes all data drives.** Only the NVMe OS disk is left untouched.

---

## 0. Status — as-built (2026-06-17)

**LIVE on beefy now** (built via `3-Storage-Layout-and-Spindown/setup-storage.sh`; see Appendix A for captured proof):

- ✅ Three data drives wiped (incl. old bcache/LVM signatures), GPT-partitioned, formatted:
  ext4 `ssd-hot`, ext4 `audio`, xfs `hdd-cold`.
- ✅ Mounted by **UUID** via the managed `/etc/fstab` block; mergerfs pool `/srv/video`
  reports **35 TB** (8 TB hot + 27 TB cold). `/srv/audio` = 7.3 TB.
- ✅ Spin-down: **`hd-idle` active + enabled**, parking only the cold HDD by serial after
  5 min (`-s 1 -i 0 -a /dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M -i 300`).
- ✅ `smartd` active (HDD `-n standby`), `fstrim.timer` enabled, `user_allow_other` set.
- ✅ **Spin-state logger active** — checks via `smartctl` every minute, logs to
  `/var/log/hdd-spinstate.log` **only on state change**, rotates at 15k lines (§11).

**Fixes applied during build (for the record):**
- First mount attempt failed because `commit=60` is an **ext4-only** option that XFS rejects
  → removed; the cold tier mounts with `noatime` only.
- `setup-storage.sh` gained a re-runnable **`--configure`** mode (mounts/services without
  re-wiping) used to recover from that failed mount.
- hd-idle initially never spun the drive down: `-a` was given the by-id **basename**, which
  hd-idle can't resolve to a device, so it silently applied its default (never spin down).
  Fixed to the **full `/dev/disk/by-id/...` path** + `-s 1` (runtime symlink resolution).
- `hdparm -C` returns `unknown` on this HAMR Exos → the spin-state logger uses `smartctl`.

**PLANNED — not yet implemented** (later migration phases): the tiering **mover** (§5), the
Docker mount-ordering drop-in (§6), **promote-on-detail-view** (§7), integrity/backup (§9),
Samba shares, and the services (Audiobookshelf, download/seed stack). Ownership of
`/srv/video` & `/srv/audio` is still `root:root` — set per service when wired.

---

## 1. Goals (in priority order)

1. **The 28 TB HDD sleeps almost always** — only a genuinely cold video read wakes it.
2. **No wait when starting playback** for the common case.
3. **One unified namespace** (`/srv/video`) for apps and the network share — they never
   see the SSD/HDD split.
4. Best-practice mounts: `/srv`, UUID-based, ordered before Docker.

---

## 2. Hardware & roles

| Device (role) | Size | Type | Purpose |
|---|---|---|---|
| NVMe (`nvme0n1`) | 1 TB | Samsung 970 EVO | OS root + Docker + **all container config/state and DBs** |
| Audio SSD (`sdX`) | 8 TB | Samsung 870 QVO (SATA) | `/srv/audio` — audiobooks / music / future cloud (pure SSD) |
| Hot SSD (`sdX`) | 8 TB | Samsung 870 QVO (SATA) | mergerfs **hot tier** of the video pool |
| Cold HDD (`sdX`) | ~28 TB | Seagate ST30000NM004K (HAMR Exos, SATA/AHCI) | mergerfs **cold tier** of the video pool |

Notes:
- The two 870 QVO SSDs share an **identical model string** — always identify by **UUID
  or serial**, never by `/dev/sdX` (which can reorder across reboots).
- 870 QVO is **QLC**: ~2,880 TBW endurance per 8 TB. Sustained writes drop to ~80 MB/s
  once the SLC cache fills (a *speed* limit, not a longevity problem). The **audio SSD is
  read-dominant**; the **hot SSD is not** (it absorbs downloads + prefetch copies + mover
  rewrites) — still fine for ~a decade.
- The cold HDD is a **new HAMR platform**: spin-up/ready is ~15–30 s, not the ~8 s of
  classic drives. Controller is native **Intel AHCI** (not USB), so software standby works.

**Concrete assignment (bound to drive serial — re-cable to any SATA slot freely):**

| Role | Drive serial (`/dev/disk/by-id/...`) |
|---|---|
| Hot SSD (with cold HDD) | `ata-Samsung_SSD_870_QVO_8TB_S5SSNF0WA00268B` |
| Audio SSD | `ata-Samsung_SSD_870_QVO_8TB_S5SSNF0W909892P` |
| Cold HDD | `ata-ST30000NM004K-3RM133_K1S05Y9M` |

Nothing references `/dev/sdX` or a SATA port: drives are wiped/partitioned by `by-id`
serial, mounted by filesystem `UUID`, and hd-idle/smartd target the HDD by serial. See
`3-Storage-Layout-and-Spindown/setup-storage.sh`.

---

## 3. Final mount layout

```
/                       nvme0n1p2   ext4               OS, Docker, container config/state + DBs
/srv/audio              <audio-ssd> ext4  noatime      audiobooks / music / cloud (pure SSD)
/srv/.disks/ssd-hot     <hot-ssd>   ext4  relatime     mergerfs hot branch
/srv/.disks/hdd-cold    <cold-hdd>  xfs   noatime             mergerfs cold branch
/srv/video              mergerfs    union(ssd-hot, hdd-cold)   <-- apps + Samba use THIS
```

- **`noatime` is the load-bearing spin-down option** on the cold tier — it stops reads
  from writing an access-time back to the disk. Note: `commit=N` is an **ext4-only**
  option; XFS rejects it (it broke the first mount attempt). XFS has no equivalent flag,
  and on an idle read-only cold disk there's nothing to flush anyway, so `noatime`
  suffices (optional `logbsize=256k` would batch log writes if ever needed).
- `relatime` on the **hot SSD** so the mover can tell what was recently watched.
- Cold tier is **xfs** (handles very large volumes / large files / fsck time better, and
  delayed-logging coalesces metadata writes).
- Apps and Samba **only ever use `/srv/video`** and `/srv/audio` — never the raw branches.

---

## 4. Build steps

> **These steps are implemented and automated by `3-Storage-Layout-and-Spindown/setup-storage.sh`** (which is how
> the live system was built). Run it as root: `sudo bash 3-Storage-Layout-and-Spindown/setup-storage.sh` for a fresh
> wipe+build, or `sudo bash 3-Storage-Layout-and-Spindown/setup-storage.sh --configure` to (re)apply mounts/services
> without wiping. The drive serial→role assignment and all options are baked into that script.
> The manual steps below explain what it does and why.

### 4.1 Identify drives by stable ID

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL
ls -l /dev/disk/by-id      # stable, serial-based names — use these in scripts
sudo blkid                 # UUIDs (for fstab)
```

Record the **serial → role** mapping before doing anything destructive (both SSDs have
the same model string, so serial is the only safe discriminator).

### 4.2 Wipe old signatures (bcache / LVM)

The drives currently hold old signatures (`LVM2_member`, `bcache`). A plain `mkfs` does
**not** remove a bcache superblock — it can be re-claimed on reboot. Wipe explicitly:

```bash
# For each data drive and partition (use by-id paths!):
sudo wipefs -a /dev/disk/by-id/<...>          # clears FS/RAID/bcache/LVM signatures
# belt-and-suspenders for bcache:
sudo dd if=/dev/zero of=/dev/disk/by-id/<...> bs=1M count=16 conv=fsync
```

### 4.3 Partition & format

```bash
# One GPT partition per data drive (align to 1 MiB; tools default to this):
sudo parted -s /dev/disk/by-id/<audio-ssd> mklabel gpt mkpart primary 0% 100%
sudo parted -s /dev/disk/by-id/<hot-ssd>   mklabel gpt mkpart primary 0% 100%
sudo parted -s /dev/disk/by-id/<cold-hdd>  mklabel gpt mkpart primary 0% 100%

sudo mkfs.ext4 -L audio   /dev/disk/by-id/<audio-ssd>-part1
sudo mkfs.ext4 -L ssd-hot /dev/disk/by-id/<hot-ssd>-part1
sudo mkfs.xfs  -L hdd-cold /dev/disk/by-id/<cold-hdd>-part1
```

### 4.4 Mount via systemd (ordering is safety-critical)

Mount by **UUID**. Use systemd `.mount` units (or `x-systemd.*` in `/etc/fstab`) so that
Docker **refuses to start** until the pool is mounted — see §6 for why this matters.

`/etc/fstab` example:

```fstab
UUID=<audio-uuid>   /srv/audio            ext4  noatime               0 2
UUID=<hot-uuid>     /srv/.disks/ssd-hot   ext4  relatime              0 2
UUID=<cold-uuid>    /srv/.disks/hdd-cold  xfs   noatime               0 2
```

```bash
sudo mkdir -p /srv/audio /srv/.disks/ssd-hot /srv/.disks/hdd-cold /srv/video
sudo systemctl daemon-reload
sudo mount -a
```

### 4.5 Install & configure mergerfs

```bash
sudo apt install -y mergerfs
```

`/etc/fstab` mergerfs line (branches: hot first so reads/creates prefer SSD):

```fstab
/srv/.disks/ssd-hot:/srv/.disks/hdd-cold  /srv/video  fuse.mergerfs  \
  defaults,allow_other,use_ino,cache.files=partial,dropcacheonclose=true,\
category.create=ff,minfreespace=50G,moveonenospc=true,statfs=base,\
fsname=mergerfs,x-systemd.requires=/srv/.disks/ssd-hot,\
x-systemd.requires=/srv/.disks/hdd-cold  0 0
```

Key options and why:
- **`category.create=ff`** with the SSD branch listed first → all new writes (downloads)
  land on the **SSD**; `moveonenospc=true` spills to HDD only if the SSD genuinely fills.
- **Read** is first-found → a file present on SSD is read from SSD; the HDD is touched
  only for files that exist *only* on the cold branch.
- **`statfs=base`** so qBittorrent/SABnzbd free-space checks aren't confused by aggregated
  free space (otherwise downloads can refuse/misreport).
- Large attr/entry/readdir caches (defaults are fine; tune up given 61 GB RAM) reduce
  *repeat* HDD wakes from browsing.

### 4.6 FUSE prerequisites for containers

```bash
# Allow non-root containers to read the FUSE mount:
sudo sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf

# Mount propagation so a mergerfs remount reaches running containers:
sudo mount --make-rshared /
```

In Docker, bind-mount `/srv/video` into containers with **`bind-propagation: rslave`**.

### 4.7 Spin-down stack

```bash
sudo apt install -y hd-idle smartmontools
```

- **`hd-idle`** parks the cold HDD after ~5 min of *actual* zero I/O (preferred over
  `hdparm -S`, whose firmware APM timer some Exos ignore; hd-idle watches
  `/proc/diskstats`). Target **only the cold HDD**, and pass the **full
  `/dev/disk/by-id/...` path** to `-a` (a bare basename won't resolve → hd-idle silently
  applies its default of *never spin down*); add `-s 1` for runtime symlink resolution.
- The drive's **write-back cache is on**, so `fsync`/journal FLUSH commands wake a parked
  drive — this is why writes to the cold tier are *batched* by the mover (§5).
- **`smartd -n standby`** so SMART polls don't wake it; schedule self-tests off-standby.
- **`fstrim.timer`** weekly on all three SSDs (do **not** use the `discard` mount option):
  ```bash
  sudo systemctl enable --now fstrim.timer
  ```
- **Exclude the cold branch from everything that walks files:** `updatedb`/`mlocate`
  PRUNEPATHS, scheduled Plex/Jellyfin deep scans (prefer inotify or schedule into the
  mover window), Portainer/Glances/Scrutiny disk-usage walks, and backup nightly scans.

---

## 5. Tiering mover (nightly systemd timer)

Keeps the SSD hot tier as the *active working set* and demotes cold content to the HDD.

- **Demotes** large video files SSD → HDD when old / not-recently-accessed and the SSD is
  filling. Paths in `/srv/video` **never change** when a file moves between tiers.
- **Crash-safe (no UPS):** copy → `fsync` → verify → **then** delete the source. A power
  cut mid-move leaves the original intact.
- **Cross-tier moves are copy+verify+delete**, never `rename` (mergerfs `rename` across
  branches returns `EXDEV`).
- **Skips open files** (in playback or actively seeding) and runs demotions in a **tight
  batched window** (then `sync` + idle) so journal/FLUSH activity doesn't trickle-wake the
  HDD all night.
- **Pin list** (keep-on-SSD) for favorites.
- **Seeding torrents are pinned to SSD** — sustained seeding reads fundamentally conflict
  with spin-down; demote only after seeding stops (ratio/time cap reached).
- **Never demotes sidecars** (`.nfo`, posters, subtitles) — they stay on the SSD branch so
  the directory skeleton is SSD-represented and browsing mostly avoids the HDD.
- **arr download dir and library live on the same branch/tree** so hardlink-based atomic
  imports work (cross-branch falls back to full copies).

---

## 6. Boot-order safety (the most dangerous failure mode)

If Docker starts **before** mergerfs is mounted, a container bind-mounts an **empty**
`/srv/video` — and an *arr app can then mark the entire library "missing" and delete
entries. Prevent it:

- systemd `.mount` units + a `docker.service` drop-in with
  `Requires=srv-video.mount` and `After=srv-video.mount`, so **Docker refuses to start**
  if the pool isn't up.
- Do **not** use `nofail`-and-proceed on the merged mount — failing closed is correct here.

---

## 7. Promote-on-detail-view (instant cold play, HDD stays asleep)

The naive "stream from HDD while copying to SSD" does **not** spin the drive down mid-film:
the player's open file handle stays bound to the HDD branch for the whole session. The fix
is **pre-promotion** — copy the title to SSD *before* the user presses play:

- **Trigger on the media server's detail page** (when you open a movie and read its
  synopsis). Because the copy finishes before play, the player opens the file and mergerfs
  serves it **from SSD from the first byte** — the HDD spun up during browsing, then parks,
  and **playback never touches the HDD.**
- **Distinguish detail-view from grid-scroll** (both read artwork): a `fanotify` watcher
  fires only on a *burst* of reads from one folder (poster + backdrop + logo + media-info
  within ~2 s = detail view; a single small thumbnail = grid tile). Debounced, cold-only.
  Alternative: a Jellyfin webhook/plugin on the item-page `PlaybackInfo` request.
- **Atomic copy:** write to a temp name on the SSD branch, `fsync`, then `rename` into
  place — the player never opens a half-copied file.
- **Fallback:** if the user plays before the copy finishes, that session stays HDD-bound
  (rare, acceptable); the promotion still completes so the *next* play is hot.
- Also pre-promote predictable next watches (next unwatched episodes of an in-progress
  series, watchlist).

Realistic copy throughput: the QVO write cliff caps sustained SSD writes at ~80 MB/s, so a
50 GB title is ~7–10 min (still far faster than ~7 MB/s playback).

---

## 8. Access scenarios (documented behavior)

**A. Hot movie (already on SSD):** media server reads from SSD; **HDD never touched,
stays asleep; instant start.**

**B. Cold movie (promote-on-detail-view, §7):** browsing the detail page copies it to SSD;
by the time you press play it's hot → instant, HDD parks. Without pre-promotion, a truly
cold play keeps the HDD awake for that film.

**C. Audiobook / music:** files live on `/srv/audio` (pure SSD) → instant, **the 28 TB HDD
is never involved.**

**D. New download:** downloader writes into `/srv/video`; mergerfs places it on the **SSD
hot branch** → fast writes, **HDD stays asleep during downloading**; the mover demotes it
later once cold (and not seeding).

**E. `ls -la /srv/video` (root):** root entries are top-level dirs whose skeleton lives on
the SSD branch → served from SSD + cache, **HDD not spun up**. (A deep `ls -la` into a cold
file's folder can `stat` that file and wake the HDD once, then served from cache.)

**F. Seeding (uploading to peers):** active torrents always seed **from SSD, never the cold
HDD** — peers issue random reads at unpredictable times, so seeded data on the HDD would
keep it awake constantly. Audiobook/music torrents seed from `/srv/audio` (pure SSD, no HDD
behind it). Movie/series torrents seed from the **SSD hot tier** of `/srv/video`; the mover
**pins actively-seeding files to the SSD** and demotes to the cold HDD only *after* seeding
stops (ratio/time cap). The cold HDD therefore holds only finished, non-seeding content.
**Caveat:** the live-seeding set must fit on the SSD — bound it with seed-ratio/time limits
so finished torrents stop seeding and demote. (Exact download-dir / category save-path /
import-hardlink layout is defined in the download-stack phase.)

---

## 9. Integrity & backup

Neither ext4 nor xfs checksums file *data*, so silent bitrot is undetectable, and these are
single drives (no redundancy). Full data-checksumming (ZFS/btrfs) conflicts with this
spin-down/mergerfs design, and SnapRAID parity needs a spare ≥28 TB drive we don't have.
Therefore:

- **Movies → detection-only:** a periodic hash scan flags anything that rotted; re-download
  it. No parity drive needed.
- **Audio (`/srv/audio`) → checksummed off-box backup** (restic/borg) — covers both bitrot
  and drive death for the irreplaceable data. This is the real protection.

---

## 10. No UPS

A UPS isn't available, so we engineer around power loss:

- Journaled xfs/ext4 recover to a **consistent** state (you lose at most very recent
  writes, never the library).
- The **mover is crash-safe** (copy → fsync → verify → delete source).
- Downloads are **resumable** (qBittorrent/SABnzbd recheck and continue after a reboot).
- Drive write-cache stays **on** (disabling it would hurt spin-down/perf for marginal
  safety). Residual risk: lose an in-flight download (auto-resumed) or seconds of recent
  writes.

---

## 11. Verifying spin-down (non-waking power-state log)

`3-Storage-Layout-and-Spindown/hdd-spinstate.sh` + `3-Storage-Layout-and-Spindown/install-hdd-spinlog.sh` install a systemd timer that
checks the cold HDD's power state **every minute** but only appends to
`/var/log/hdd-spinstate.log` **when the state changes** (so every line is a real
transition) — and **without waking it**. The log is rotated to the most recent 15,000 lines.

> **Note:** this HAMR Exos returns `unknown` to `hdparm -C`, so the logger uses **`smartctl`**
> instead. `smartctl -n standby` checks the power mode (ATA *CHECK POWER MODE*, non-data) and
> aborts without spinning a parked drive up — i.e. non-waking. `/proc/diskstats` is read from
> memory. Neither resets hd-idle's timer.

The log shows a plain-language state with the raw ATA mode in brackets:

| Log label | Meaning | Raw ATA |
|---|---|---|
| **`SPUN-DOWN`** | motor off — parked / asleep **(the goal)** | `STANDBY` |
| `SLEEP` | deepest state, interface off | `SLEEP` |
| `IDLE-LOWRPM` | spinning at reduced RPM | `IDLE_C` |
| `IDLE-SPINNING` | idle, platters at full RPM | `IDLE_A`/`IDLE_B` |
| `ACTIVE` | spinning and in use | `ACTIVE` |
| `UNKNOWN` | could not be determined | — |

Review over a day or two to confirm the drive reads `SPUN-DOWN` whenever idle and isn't being
woken by background activity. hd-idle's own spindown/spinup *transition* events are in
`/var/log/hd-idle.log` (made world-readable by the installer).

### 11.1 How to check the log (no sudo needed — both logs are world-readable)

The spin-state log only appends **on a state change**, so the **last line is the current state**.

**Watch it live with a spinner — `hdd-spinwatch`** (the nice way). Shows the current state plus an
old-school icon ( `| / - \` ) that **spins continuously** (~7 fps) so you can see the monitor is
live, while the **`checks seen` counter increments and the `last … ago / next ~…` countdown resets
each time the minute-checker actually runs** — that pair is the proof the checks are happening.

Sample line:

```
 -  SPUN-DOWN     since 2026-06-18T11:18:45+02:00  |  checks seen: 12  last 7s ago  next ~53s
```

**Run it** (unprivileged — only reads the world-readable log + queries systemctl; Ctrl-C to stop):

```bash
hdd-spinwatch                       # on beefy (if installed to /usr/local/bin)
# or run straight from the repo clone, no install needed:
bash ~/Projects/Server/3-Storage-Layout-and-Spindown/hdd-spinwatch
# from fastpi (the -t is required for the live redraw):
ssh -t buntu@beefy hdd-spinwatch
```

**Install / update it** (idempotent — also re-run after a `git pull` to update to a new version):

```bash
git -C ~/Projects/Server pull
sudo bash ~/Projects/Server/3-Storage-Layout-and-Spindown/install-hdd-spinlog.sh
```

It leaves the log **untouched** — `tail -n 1` still shows the true last state — because the
"a check just ran" heartbeat is read from systemd's own record of the timer firing
(`ExecMainStartTimestamp`), not written into the log. (Why not make plain `tail -f` animate a
spinner? It can't: `tail -f` only reacts to *appended* bytes and `tail -n 1` reads the file's end,
so you can't have a per-minute in-place spinner there without either growing the file every minute
or breaking `tail -n 1`. Hence the dedicated viewer.)

Or read the raw log directly:

```bash
# --- current state (the most recent transition) ---
tail -n 1 /var/log/hdd-spinstate.log

# --- recent history (last N transitions) ---
tail -n 20 /var/log/hdd-spinstate.log

# --- watch live: new transitions appear as they happen (Ctrl-C to stop) ---
tail -f /var/log/hdd-spinstate.log

# --- whole log, paged (q to quit) ---
less /var/log/hdd-spinstate.log

# --- only the spin-downs (did it actually park, and when?) ---
grep SPUN-DOWN /var/log/hdd-spinstate.log | tail

# --- just today's transitions ---
grep "$(date +%F)" /var/log/hdd-spinstate.log

# --- hd-idle's own spindown/spinup events ---
tail -n 20 /var/log/hd-idle.log
tail -f     /var/log/hd-idle.log
```

Is the logger even running, and when did it last/next fire?

```bash
systemctl status hdd-spinstate.timer --no-pager
systemctl list-timers hdd-spinstate.timer --no-pager   # last + next trigger times
```

Force a fresh sample **right now** instead of waiting for the 1-min timer (still non-waking; only
writes a line if the state actually changed):

```bash
sudo systemctl start hdd-spinstate.service
tail -n 1 /var/log/hdd-spinstate.log
```

**From fastpi (remote, over SSH)** — the logs are world-readable, so no sudo:

```bash
ssh buntu@beefy 'tail -n 20 /var/log/hdd-spinstate.log'
ssh buntu@beefy 'tail -f  /var/log/hdd-spinstate.log'   # live follow over SSH
```

Install:  `sudo bash ~/Projects/Server/3-Storage-Layout-and-Spindown/install-hdd-spinlog.sh`

## 12. Honest spin-down expectation

Not "untouched for weeks." Realistically: **asleep most of the time between cold-content
accesses**, with a wake (a) per cold-movie play — for the whole film unless pre-promoted —
and (b) per deep library scan (mitigated, not eliminated). The OS, all configs/DBs,
audiobooks, music, browsing of hot content, and every download live on SSD/NVMe and never
touch the HDD.

---

## Appendix A — As-built verification (captured 2026-06-17)

Live state on beefy after running `setup-storage.sh` (+ `--configure` to fix the XFS mount).

### Drives, partitions, UUIDs (bound by serial)

| Role | Drive serial (`/dev/disk/by-id/...`) | Part | FS | Label | UUID |
|---|---|---|---|---|---|
| Hot SSD (with cold HDD) | `ata-Samsung_SSD_870_QVO_8TB_S5SSNF0WA00268B` | sda1 | ext4 | ssd-hot | `5e19e1fd-ce5c-4c1d-80ba-d87983494e46` |
| Audio SSD | `ata-Samsung_SSD_870_QVO_8TB_S5SSNF0W909892P` | sdb1 | ext4 | audio | `9a2d3432-cfc8-4844-b4b1-e0dddfb5ef4b` |
| Cold HDD | `ata-ST30000NM004K-3RM133_K1S05Y9M` | sdc1 | xfs | hdd-cold | `b805bc03-6217-41ea-9161-2b55281e0313` |

(`/dev/sdX` shown for reference only — nothing depends on it.)

### `/proc/mounts`

```
/dev/sdb1 /srv/audio ext4 rw,noatime 0 0
/dev/sda1 /srv/.disks/ssd-hot ext4 rw,relatime 0 0
/dev/sdc1 /srv/.disks/hdd-cold xfs rw,noatime,inode64,logbufs=8,logbsize=32k,noquota 0 0
mergerfs /srv/video fuse.mergerfs rw,relatime,user_id=0,group_id=0,default_permissions,allow_other 0 0
```

### Capacity

```
mergerfs       fuse.mergerfs   35T  535G   34T   2%  /srv/video
/dev/sdb1      ext4           7.3T  2.1M  6.9T   1%  /srv/audio
```

### Live `/etc/fstab` managed block

```
# >>> beefy-storage (managed by setup-storage.sh) >>>
UUID=9a2d3432-cfc8-4844-b4b1-e0dddfb5ef4b  /srv/audio            ext4  noatime    0 2
UUID=5e19e1fd-ce5c-4c1d-80ba-d87983494e46  /srv/.disks/ssd-hot   ext4  relatime   0 2
UUID=b805bc03-6217-41ea-9161-2b55281e0313  /srv/.disks/hdd-cold  xfs   noatime    0 2
/srv/.disks/ssd-hot:/srv/.disks/hdd-cold  /srv/video  fuse.mergerfs  defaults,allow_other,use_ino,cache.files=partial,dropcacheonclose=true,category.create=ff,minfreespace=50G,moveonenospc=true,statfs=base,fsname=mergerfs,x-systemd.requires=/srv/.disks/ssd-hot,x-systemd.requires=/srv/.disks/hdd-cold  0 0
# <<< beefy-storage (managed by setup-storage.sh) <<<
```

### Services

| Item | State |
|---|---|
| `hd-idle` | **active + enabled** — `-s 1 -i 0 -a /dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M -i 300 -l /var/log/hd-idle.log` (idle=300 s / 5 min; **full by-id path** required) |
| `smartmontools` (smartd) | active (`-n standby` on the HDD) |
| `fstrim.timer` | enabled (weekly TRIM, all SSDs) |
| `user_allow_other` in `/etc/fuse.conf` | set |
| `hdd-spinstate.timer` | **active + enabled** (installed 2026-06-18) — 1-min non-waking power-state logger → `/var/log/hdd-spinstate.log`; see §11.1 for how to read it |

### Scripts (in `~/Projects/Server/3-Storage-Layout-and-Spindown/`)

- `setup-storage.sh` — wipe/partition/format/mount + mergerfs + hd-idle/smartd/fstrim (`--configure` = no-wipe).
- `hdd-spinstate.sh` — one non-waking power-state sample (`hdparm -C` + `/proc/diskstats`).
- `install-hdd-spinlog.sh` — installs the 1-min spin-state logger timer + `hdd-spinwatch`.
- `hdd-spinwatch` — live view: a constantly-spinning icon + a `checks seen` counter that ticks each minute a check runs (§11.1). Unprivileged.

### Validation

- ✅ **Reboot persistence** — confirmed: after `sudo reboot` the pool auto-mounted via
  systemd (mergerfs `/srv/video` 35 TB), and hd-idle / hdd-spinstate / smartd / fstrim all
  came back active+enabled.
- ✅ **Physical spin-down** — confirmed (after the by-id-path fix): the cold HDD reaches
  `STANDBY` when idle, and the spin-state log records `SPUN-DOWN [STANDBY]` with the
  read/written counters unchanged (proving `smartctl` reads it non-wakingly). The SCSI
  spindown command works on this drive (no `-c ata` needed). `hdparm -C` reports `unknown`
  here, which is why the logger uses `smartctl`.
