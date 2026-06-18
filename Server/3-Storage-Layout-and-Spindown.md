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

> **The "mover window" = nightly `04:00–06:00` (DECIDED).** This is the single batched period when
> the HDD is allowed to wake: demotion runs here, and **every other HDD-waking task** (Jellyfin /
> Bazarr / arr deep rescans, the §9 integrity hash scan, bulk renames) is scheduled into the *same*
> window so the drive wakes **once per night** instead of being trickle-woken all day. Every
> "schedule it into the mover window" reference elsewhere in this doc means this 04:00–06:00 slot.
> The mover/promoter are **not built yet** (§0); until they are, see §14.1 for the interim reality.

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

> **Cache nuance:** "served from cache" here means the **metadata** caches — mergerfs's
> attr/entry/readdir caches and the kernel dentry/inode cache — which persist and absorb *repeat*
> `stat`/`readdir` traffic so browsing rarely re-wakes the HDD. It does **not** mean cold file
> **data** stays resident: `dropcacheonclose=true` (§4.5) deliberately drops a file's page-cache
> data on close (so QLC writes/reads don't evict hot pages). So a repeated *browse* is cache-served
> and HDD-quiet; re-*reading the bytes* of a cold file after its handle closed can wake the HDD
> again. This is fine — it's exactly what promote-on-detail-view (§7) exists to pre-empt.

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

## 13. Per-component responsibilities — what each service must know about the storage

**This section is the authoritative reference for wiring up any single service** (a future
Claude setting up Sonarr, Jellyfin, qBittorrent, etc. should read *this* before touching that
app's config).

**The one-sentence rule:** the SSD/HDD split is hidden behind mergerfs, so **almost every
service needs to know nothing about it** — it uses `/srv/video` (mixed, unified) and
`/srv/audio` (pure SSD) and treats them as ordinary folders. The split is real only to two
**host-level** daemons — the **mover** (§5) and the **promoter** (§7) — which are the *only*
things allowed to touch the raw branches `/srv/.disks/ssd-hot` and `/srv/.disks/hdd-cold`.
Everything else that "just works" does so because of three mergerfs/mount facts already in
place, **not** because the app is tier-aware:

- new writes auto-land on the SSD branch (`category.create=ff`, §4.5);
- a file is read from whatever branch holds it (HDD touched only for cold-only files);
- `statfs=base` makes free-space checks report the SSD branch sanely (§4.5).

### 13.0 Universal rules (apply to EVERY container — do these regardless of app)

1. **Only ever mount `/srv/video` and/or `/srv/audio`.** **NEVER** bind-mount the raw branches
   (`/srv/.disks/ssd-hot`, `/srv/.disks/hdd-cold`) into any container. A container that writes
   to a raw branch defeats placement, breaks the unified namespace, and can corrupt mover/promoter
   assumptions.
2. **Bind-mount with `bind-propagation: rslave`** (§4.6) so a mergerfs remount on the host
   reaches the running container.
3. **Config / database / cache / transcode-temp live on the NVMe OS disk**, never on the pool
   (use a named volume or a bind under the OS disk). The pool is for *media payload only*.
4. **Mount the download tree and the library tree as ONE volume** (a single `/data`-style root),
   not two separate mounts. This is what makes arr imports **atomic hardlinks** instead of
   full copies — see §13.7. It is a *path-layout* rule, **not** tier knowledge.
5. **Never run on an empty mount.** Docker is ordered after the pool (§6) precisely so an arr
   never sees an empty `/srv/video` and mass-deletes the library. Don't override that ordering.
6. **Ownership / identity — DECIDED convention:** every media container runs as
   **`PUID=1000` / `PGID=1000`** (the `buntu` user/group), and `/srv/video` & `/srv/audio` (plus
   everything under them) is **`chown -R 1000:1000`** owned by `buntu`. One shared identity across
   the arr + download client + Jellyfin set is what lets hardlinks and cross-container reads work.
   Set **`UMASK=002`** in each container so new files are group-writable. (Ownership is still
   `root:root` today, §0 — apply the `chown` when wiring the first service.) See §14 for the full
   rationale and the one-time commands.

### 13.1 Quick-reference matrix

| Component | Storage it uses | Touches the **cold HDD**? | Needs to know the SSD/HDD split? | One-line responsibility |
|---|---|---|---|---|
| **qBittorrent** (torrents) | `/srv/video/torrents`, `/srv/audio/torrents` | Only if it seeds a demoted file — **don't** (mover pins seeders to SSD) | **No** | Save into the pool; bound seed ratio/time so finished torrents stop & become demotable |
| **SABnzbd / NZBGet** (usenet) | `/srv/video/usenet`, `/srv/audio/usenet` | No (no seeding) | **No** | Download+unpack into the pool; no seeding ⇒ imports demote freely once cold |
| **Prowlarr** (indexer manager) | *none* (config on NVMe) | No | **No — zero storage at all** | Talks to indexers + pushes configs to the arrs; never sees a media path |
| **Sonarr** (TV) / **Radarr** (movies) | `/srv/video` (downloads + `media/`) | Only during a full rescan of cold files (avoid) | **No** (but must follow the hardlink path rule, §13.7) | Single-mount `/srv/video`, hardlink imports, event-driven rescans only |
| **Lidarr** (music) / **Readarr** / **Audiobookshelf** | `/srv/audio` **only** | **Never** (audio is pure-SSD) | **No** | Keep *everything* (downloads + library) under `/srv/audio` so the HDD is never involved |
| **Bazarr** (subtitles) | `/srv/video/media` (video only — **not** `/srv/audio`) | Only on a full library sub-scan (limit it) | **No** | Writes subtitle **sidecars** (kept on SSD, never demoted); drive it by webhook, not periodic disk sweeps |
| **Jellyfin** (media server) | `/srv/video`, `/srv/audio` (read); NVMe for metadata/transcode | Yes on deep scans / image-extraction of cold files (mitigate) | **No** | Real-time monitoring instead of deep scans; metadata+transcode on NVMe; relies on the promoter for instant cold play |
| **mover** (§5, host daemon, *not yet built*) | raw branches `ssd-hot` + `hdd-cold` | **Yes — by design** | **YES — fully tier-aware** | Demotes cold/non-seeding files SSD→HDD in a batched window |
| **promoter** (§7, host daemon, *not yet built*) | raw branches | **Yes — wakes it to pre-copy** | **YES — fully tier-aware** | Copies a cold title to SSD on detail-view, before play |
| **Traefik / fastpi** (edge, the Pi) | *none* | No | **No — never, see §13.12** | Reverse-proxies HTTP only; never touches files |

### 13.2 Recommended on-pool directory layout (the basis for everything below)

A single TRaSH-style root per pool, so download and library are the **same filesystem** and the
arrs hardlink instead of copy:

```
/srv/video/                      (mergerfs union — SSD hot + HDD cold)
  torrents/{movies,tv}/          qBittorrent video save paths (seeded ⇒ pinned to SSD by the mover)
  usenet/
    incomplete/                  SAB/NZBGet scratch (SSD; heavy unpack writes — NVMe scratch is an option)
    complete/{movies,tv}/        SAB/NZBGet finished, pre-import
  media/{movies,tv}/             Jellyfin library (arr-managed; sidecars .nfo/posters/subs kept on SSD)

/srv/audio/                      (pure SSD — the 28 TB HDD is NEVER behind this)
  torrents/music/                audio torrents seed from SSD
  usenet/music/
  media/{music,audiobooks}/      Lidarr / Audiobookshelf libraries
```

Mount **`/srv/video`** (not its subfolders separately) into the video apps, and **`/srv/audio`**
into the audio apps. Same root = same filesystem = atomic hardlink imports.

### 13.3 qBittorrent (torrent download client)

- **Knows about the split: no.** Point category save paths inside the pool
  (`/srv/video/torrents/{movies,tv}`, music → `/srv/audio/torrents/music`). New data auto-lands
  on SSD; it never sees a branch.
- **The single thing you MUST get right — bound the live-seeding set.** Active torrents are
  **pinned to the SSD** by the mover (§8F): peers do random reads at random times, so seeding
  from the cold HDD would keep it awake permanently. The cold HDD therefore only ever holds
  *finished, non-seeding* content. So set **seed ratio / seed-time limits** so torrents stop
  seeding and the file becomes demotable — and make sure the *simultaneously-seeding* set fits
  on the SSD hot tier. This is a capacity bound on qBittorrent's behavior, **not** a path it
  configures.
- **Why a seeding torrent blocks SSD reclamation (mechanics the mover relies on):** after import
  the file has **two hardlinks** — `torrents/…` (held by qBittorrent for seeding) and `media/…`
  (the library). The mover demotes via copy→verify→**delete source** (§5); deleting only the
  library link leaves the data on SSD because the torrent link still references the inode
  (link-count > 0). SSD space is only reclaimable once qBittorrent removes its link (seeding
  stopped). Hence: **don't auto-delete on import, do cap seeding.**
- Free-space checks: `statfs=base` already makes these sane — no qBittorrent setting needed.
- **Do not** enable anything that walks the whole library; qBittorrent only ever touches its own
  save dirs.

### 13.4 SABnzbd / NZBGet (usenet download client)

- **Knows about the split: no.** Incomplete/unpack scratch + completed output under the pool
  (`/srv/video/usenet/...`, audio → `/srv/audio/usenet/music`).
- **No seeding** ⇒ none of the qBittorrent pin/reclaim complications. Once the arr imports and
  the usenet copy is cleaned up, only the single library link remains and the mover can demote it
  the moment it goes cold.
- Heavy unpack writes hit the QVO SLC cliff (§2). Keeping `incomplete/` on SSD (default via
  `category.create=ff`) is fine; an NVMe scratch dir is an optional optimization, **not**
  required, and would be the one place a usenet client uses a non-pool path (still not tier
  knowledge — just a faster scratch).

### 13.5 Prowlarr (the Servarr indexer manager)

- **Knows about the split: nothing. It has no media paths at all.** Prowlarr only manages
  indexer definitions and pushes them to Sonarr/Radarr/Lidarr. Its config/DB lives on NVMe.
- When a future Claude sets up Prowlarr: **do not** give it any `/srv` mount. If you're adding a
  storage volume to Prowlarr, you've made a mistake.

### 13.6 Sonarr (TV) / Radarr (movies) — the "search/PVR" clients

(You called Sonarr a "search client"; precisely it's the PVR/library manager — it searches via
Prowlarr's indexers, sends grabs to the download client, then **imports** the result into the
library. The import step is where storage matters.)

- **Knows about the split: no — but it is the component most sensitive to the *path layout*.**
- **Hardlink/atomic-move is the critical rule (§13.7).** Mount **one** volume `/srv/video` so the
  download dir and `media/` library are the **same filesystem**. Enable **"Use Hardlinks instead
  of Copy."** Then import is an instant hardlink/atomic rename on the SSD branch — no second copy,
  **no HDD wake**. If you instead mount downloads and library as two separate volumes, Sonarr
  thinks they're different filesystems and does a full copy (slow on QVO, and a cross-branch move
  returns `EXDEV` → full copy anyway — §5).
- **Remote path mapping must match the download client's paths** (or, simplest, mount the same
  `/srv/video` at the same path in both containers).
- **Library/import rescans wake the HDD** because they `stat` cold files (§4.7, §12). Mitigate:
  prefer **import-on-completion + folder monitoring**; turn **off** aggressive periodic
  "Rescan Series/Movie" full sweeps, or schedule them into the nightly **mover window** so the
  one wake is shared. Do **not** point the arr at the cold branch for any scan.
- **Sidecars** the arr writes (`.nfo`, posters) are **never demoted** (§5) — they stay on the SSD
  branch so the directory skeleton is SSD-represented and browsing avoids the HDD. No config
  needed; just don't relocate metadata off the library tree.
- **Boot-order / mass-delete safety (§6):** an arr on an empty mount can mark the whole library
  "missing" and delete DB entries. The Docker-after-pool ordering prevents it — keep it, and do
  **not** set the merged mount to `nofail`-and-proceed.

### 13.7 The hardlink rule, stated once (applies to all arrs)

mergerfs can only hardlink **within one branch**. The chain that keeps imports instant and the
HDD asleep:

1. Download lands on the **SSD** branch (`category.create=ff`).
2. arr imports with **hardlink** → the library link is created **on the same SSD branch** →
   instant, no copy, HDD untouched.
3. The mover later demotes the file SSD→HDD **only after** all other links are gone (usenet:
   immediately; torrents: after seeding stops). Demotion is copy+delete (`rename` across branches
   is `EXDEV`), so a demoted file naturally drops its hardlink relationship — which is fine
   because by then nothing else links it.

If a future Claude sees imports doing full copies or the HDD waking on import, the cause is
almost always a **split mount** (downloads and library on different volumes/filesystems) — fix
the mount layout, not the app's copy setting.

#### 13.7.1 Two names, one file — original torrent name *and* the Servarr naming convention

This is the property you're relying on, and it's worth stating precisely because it's the whole
reason hardlinks (not "atomic move") are used here.

**What a hardlink is:** two (or more) **directory entries — i.e. two names/paths — pointing at
the same inode** (the same physical data blocks on disk). It is **not** a copy and **not** a
shortcut/symlink: there is exactly **one** copy of the data, and each name is an equal, first-class
reference to it. The data is freed only when the **last** name is removed (link count → 0). On the
union, `use_ino` (set in §4.5) makes the inode numbers consistent so apps correctly see the two
paths as the same file.

**How that gives you both names at once:**

- The **download** keeps its **original release name and folder structure** exactly as the tracker
  expects, e.g.
  `…/torrents/tv/Some.Show.S01E01.1080p.WEB.h264-GROUP/Some.Show.S01E01.…h264-GROUP.mkv`.
  qBittorrent keeps seeding **this** path untouched — the bytes, the filename, and the folder all
  match the `.torrent`, so piece verification passes and seeding continues.
- The arr creates a **second name** for the **same inode** under the library, applying its
  **Servarr naming convention**, e.g.
  `…/media/tv/Some Show/Season 01/Some Show - S01E01 - Episode Title [WEBDL-1080p][h264].mkv`.
  Jellyfin reads **this** organized name.
- Both names exist simultaneously, on the same SSD branch, costing **one** copy of the data.
  qBittorrent sees its original; Jellyfin/arr see the renamed one; neither disturbs the other.

**Where do the two names physically live — SSD or HDD?** This is the most common point of
confusion, so state it flatly:

- **`/srv/video` is not a disk** — it's the mergerfs *union view*, with no storage of its own.
  Every path under it physically resides on exactly one branch: `/srv/.disks/ssd-hot/…` (SSD) or
  `/srv/.disks/hdd-cold/…` (HDD). `/srv/video/X` is just the logical name for whichever branch
  actually holds `X`.
- **A hardlink can never span two filesystems** (kernel rule, not a mergerfs quirk). So the two
  names **must** sit on the **same branch**, sharing one inode — you can *never* have one leg on
  SSD and the other on HDD.
- **In practice both legs are on the SSD.** The download lands on SSD (`category.create=ff`), and
  the arr's hardlink import creates the second name on that **same SSD branch** (mergerfs issues
  the `link()` on `ssd-hot`). So `…/torrents/…` and `…/media/…` both resolve to
  `/srv/.disks/ssd-hot/…`, link count 2, one copy of the data, on the SSD.
- **The HDD never holds a hardlink.** Demotion is **copy→verify→delete** (not move) and runs only
  once all-but-one name is gone (torrent removed / seeding stopped, §5); the surviving name is
  copied to the HDD as a **fresh, lone file** (link count 1, no longer linked to anything). So:
  *hot/seeding → two hardlinked names on SSD; cold → one ordinary file on HDD; never a link
  straddling both.*

**Do we have to manage this? Almost never — it's automatic, with one exception (the mover):**

- **Containers (qBittorrent / SAB / the arrs):** nothing to manage. One `/srv/video` mount +
  "Use Hardlinks" ON, and placement (`category.create=ff`) guarantees both legs land on the SSD.
- **The mover (§5) is the *only* component that must be link-aware:** it must **never demote a
  file whose inode still has another link on the SSD** (e.g. a still-seeding torrent). It checks
  link count / seeding status before copy+delete. If it ignored this, demoting a still-linked file
  would break the hardlink and store the data **twice** (seeding copy on SSD + demoted copy on
  HDD) — which is precisely what the seeding-pin rule (§8F) prevents. Correct hardlink handling
  therefore lives in the **mover's design**, nowhere else.

**Why hardlink, not "atomic/instant move":** Sonarr/Radarr have *both* behaviors when source and
destination are the same filesystem. An **atomic move** is a `rename` — it **relocates the single
name** into the library and the original path **disappears**, which **breaks seeding** (the torrent
can no longer find its files). A **hardlink** **adds** the library name while **keeping** the
original, so seeding survives. For a seeding torrent client you therefore want **"Use Hardlinks
instead of Copy" = ON** (the default, and what TRaSH recommends) — that is what produces the
dual-name behavior. Usenet has no seeding, so move-vs-hardlink is immaterial there (the original is
cleaned up after import anyway).

#### 13.7.2 Impact on naming / library organization (the practical consequences)

- **The Servarr rename applies only to the library link.** You can use any Sonarr/Radarr naming
  format you like for `/srv/video/media/...` — the torrent's original name is never modified, so
  **renaming never risks your seeding**. Rename freely; only the library entry changes.
- **No double disk usage and no HDD wake to rename.** Because rename = "create a second directory
  entry," there's no data copy. A 50 GB episode imported + renamed still consumes ~50 GB, on the
  SSD branch, instantly — the QVO write cliff and the cold HDD are both irrelevant to the import.
- **Sidecars follow the *library* names, and stay on SSD.** `.nfo`, posters, and Bazarr subtitles
  are written next to the **renamed** library file (matching its basename) and are **never demoted**
  (§5) — so the browsable, correctly-named skeleton lives on the SSD branch and Jellyfin browsing
  avoids the HDD.
- **Both names must stay on the same branch for the link to hold.** `category.create=ff` puts the
  download on SSD and the hardlink import lands the library name on that same SSD branch — good. If
  a future layout ever caused the two to land on different branches, mergerfs can't hardlink across
  branches → you'd silently get a full **copy** (2× space) instead. Keep download + library under
  the **one** `/srv/video` mount (§13.2).
- **Renaming/editing after demotion.** Once a file is on the cold HDD (seeding stopped, torrent
  link gone, only the library name left), a Servarr **rename or file edit** rewrites/moves that file
  on the **HDD** and **will wake it**. So bulk "Rename Files" / format-migration sweeps across the
  whole library are a cold-HDD wake event — run them in the **mover window** or accept the spin-up,
  exactly like a deep rescan (§13.6, §12). New imports (still on SSD) are unaffected.
- **Don't let the download client "move on completion" out of the pool.** If qBittorrent/SAB is
  set to move finished data to a path that isn't on `/srv/video`, you break same-filesystem and the
  arr falls back to copying. Keep completed downloads inside the pool tree (§13.2).

### 13.8 Lidarr / Readarr / Audiobookshelf (audio stack)

- **Knows about the split: no — and uniquely, never has to care.** `/srv/audio` is the **second
  SSD** — a **pure-SSD** filesystem (model `…909892P`, §2/Appendix A) with **no HDD behind it**
  (§8C). Keep **downloads and library both under `/srv/audio`** so (a) hardlink imports work within
  that one filesystem and (b) the 28 TB HDD is **never** involved in audio at all — no spin-up, ever.
- **The entire spin-down machinery does NOT apply to audio.** `/srv/audio` is **not** a mergerfs
  union, has **no hot/cold tiers**, and the **mover (§5), promoter (§7), hd-idle, and the spin-state
  logger all target only the cold *video* HDD** (§0). So for any audio app: **no demotion, no
  promote-on-detail-view, no "schedule scans into the mover window," no hardlink-across-tiers worry,
  no `moveonenospc` spill.** An SSD doesn't spin down — the audio SSD is simply always-on. (The one
  rule that *still* applies is the universal boot-order safeguard, §13.0(5)/§14.2-F: Lidarr/Readarr
  on an empty mount could still mass-delete — so `/srv/audio` is gated by the same Docker ordering.)
- The audio SSD is read-dominant QLC — fine for ~a decade (§2). Config/DB/cache on NVMe.
- The **only** audio data needing real protection is the off-box checksummed backup of
  `/srv/audio` (§9) — that's an audio-stack concern, not a tiering one.
- **Audiobookshelf specifically** is the **audio analog of Jellyfin** — a *media server*, not a
  downloader. Content reaches `/srv/audio/media/audiobooks` (and `…/music`) via the download clients
  + Lidarr/Readarr organizing into the library, via ABS's **built-in podcast downloader** (another
  writer to `/srv/audio`), or by manual upload. Wire it like Jellyfin **except** the HDD caveats
  vanish: put its **config / metadata / cache / transcode-temp on NVMe** (§13.0(3)), but you can let
  ABS run **full library scans, cover/metadata fetching, and audio probing freely** — they only ever
  touch the always-on SSD, so there is **nothing to spin up and nothing to schedule**. PUID/PGID/UMASK
  per §14.0, same as the rest of the stack.
- **Scope note:** **Lidarr** = music, **Audiobookshelf** = audiobook/podcast *server*. **Readarr**
  manages **e-books *and* audiobooks** — but e-books are **not** audio: they'd need their own
  library path (e.g. `/srv/audio/media/ebooks` is fine since it's all pure-SSD, or skip Readarr
  entirely — it is effectively unmaintained upstream). Decide whether you actually want e-books
  before wiring Readarr; if not, drop it and use Audiobookshelf for audiobooks. See §14 (open
  decisions).

### 13.9 Bazarr (subtitles)

- **Knows about the split: no.** Bazarr handles **video subtitles only** (it pairs with
  Sonarr/Radarr, not the audio stack) — so it mounts **`/srv/video` only, never `/srv/audio`**. It
  writes subtitle files as **sidecars** next to the media in `/srv/video/media`. Sidecars are
  **never demoted** (§5), so they live on the SSD branch — Bazarr's writes stay on SSD
  automatically.
- **The behavior to watch:** Bazarr's *full library disk scans* `stat` cold files and wake the
  HDD. Drive Bazarr from **arr webhooks / event-driven sync** for new items; keep any scheduled
  full "scan disk for missing subtitles" sweeps rare or inside the mover window.

### 13.10 Jellyfin (media server)

- **Knows about the split: no** — it reads `/srv/video` and `/srv/audio` like any folder. But its
  *background behaviors* are the biggest non-playback source of HDD wakes, so it needs the most
  care:
  - **Metadata, images, and the transcode/temp dir → NVMe**, never the pool. (Jellyfin config is
    on NVMe per §2 already; make sure transcoding temp isn't pointed at `/srv`.)
  - **Disable deep periodic library scans; use Real-Time Monitoring (inotify).** A scheduled
    "scan all libraries" stats every cold file → wakes the HDD (§4.7, §12). New files arriving on
    SSD are picked up by monitoring without a full sweep. If a periodic scan is unavoidable,
    schedule it into the nightly **mover window** so it shares the one wake.
  - **Turn off (or window) cold-content image work:** chapter-image extraction, trickplay/BIF
    generation, and "extract on scan" all read full files and will wake the HDD across the cold
    library. Generate these at import time (file still on SSD) or in the mover window.
- **Instant cold play depends on the promoter (§7), not Jellyfin.** Jellyfin does **not** copy or
  cache files itself — the host-level promoter pre-copies a title to SSD when you open its
  **detail page**, so by the time you press play mergerfs serves it from SSD and the HDD stays
  parked. Jellyfin's only involvement is providing the trigger (a `fanotify` read-burst on the
  artwork folder, or a Jellyfin webhook/plugin on the item-page `PlaybackInfo` request). There is
  **no "stream-from-HDD-while-caching"** mode — that doesn't spin the drive down mid-film (§7), so
  don't configure one.
- **Demotion does NOT reset watch progress, and Jellyfin does NOT re-add the title as new.** The
  mover's copy→verify→delete moves bytes between branches *underneath* the union — **the
  `/srv/video/...` path Jellyfin sees never changes.** This is safe because:
  - Jellyfin keys each library item (and its per-user **played/unplayed state, resume position, and
    series season/episode tracking**) on the **path**, stored in its DB on the NVMe. Stable path →
    stable item ID → progress preserved. Moving bytes between SSD and HDD doesn't touch the DB.
  - The file **never disappears from the union** during a demotion (copy *then* delete → it's on
    both branches mid-move, the HDD after). An item only loses its watch state if its file is
    *gone* during a scan and the item gets removed — that window does not exist here. So even a
    scan running mid-demotion sees the file present and changes nothing.
  - **Requirement this imposes on the mover (§5/§13.11):** it must copy to the **identical relative
    path** on the cold branch (`…/ssd-hot/media/X` → `…/hdd-cold/media/X`) so the union path is
    unchanged, and **preserve mtime** (`cp -p`/`rsync -t`). Preserving mtime isn't needed to keep
    *progress* (that's path-based and always safe) — it just stops Jellyfin from noticing a
    "changed" file and needlessly re-reading media info / regenerating images. Worst case if mtime
    *isn't* preserved: a one-file in-place metadata re-scan; **your watch state still survives.**

### 13.11 The mover and the promoter (host daemons — the ONLY tier-aware components)

Listed for completeness; both are **PLANNED, not yet built** (§0, §5, §7). Unlike everything
above, these run on the **host** (not in containers) and **are fully aware of the SSD/HDD split**
— they read/write the raw branches `/srv/.disks/ssd-hot` and `/srv/.disks/hdd-cold` directly and
own all promotion/demotion policy (batched windows, seeding pins, sidecar exclusion, crash-safe
copy→fsync→verify→delete). **No containerized service should ever replicate their job.** When a
future Claude builds them, this is where tier logic lives — and nowhere else.

### 13.12 Traefik on fastpi (and fastpi in general) — never needs to know

**Traefik / the Pi never needs to know anything about the HDD/SSD split — not now, not ever.**
Traefik is an HTTP(S) reverse proxy: it terminates Cloudflare-tunnelled traffic and routes by
hostname/path to a **service port** on beefy. It never opens, reads, or writes a media file, so
the storage architecture is completely invisible and irrelevant to it.

- The **only** thing fastpi needs about beefy is **network reachability to beefy's published
  service ports** (e.g. Jellyfin's port) — and even that routing is **not set up yet** (per the
  homelab notes, the Pi→beefy path is still to be built). That is a *networking* concern, not a
  storage one.
- Concretely: when wiring a service through the Pi, give Traefik the hostname → `beefy:<port>`
  mapping (plus any auth/headers/rate-limit middleware). Do **not** add storage paths, mounts, or
  tier hints to any Traefik or fastpi config — there is nothing there for it to use.

---

## 14. Operational scenarios, current limitations & open decisions

§13 explains *what each service must know*. This section closes the gaps a fresh review found:
**(14.0)** the decided values, **(14.1)** what actually happens *today* (mover/promoter not built),
**(14.2)** scenarios §13 didn't spell out, and **(14.3)** the remaining open decisions. Read this
before deploying any service.

### 14.0 Decided values (use these verbatim)

| Decision | Value | Notes |
|---|---|---|
| **Container identity** | `PUID=1000` / `PGID=1000` (the `buntu` user) | One shared identity for **all** media containers (arrs, download clients, Jellyfin, Bazarr). |
| **Pool ownership** | `chown -R 1000:1000 /srv/video /srv/audio` | Apply when wiring the first service (still `root:root` today, §0). |
| **Umask** | `UMASK=002` in every media container | New files group-writable → cross-container hardlinks/edits work. |
| **Mover window** | nightly **`04:00–06:00`** | The one batched HDD-wake slot; *all* HDD-waking scans schedule into it (§5). |
| **Seeding bound** | ratio **2.0** *or* **30 days** then stop, **+** hot-seed cap (see 14.2-A) | Conservative defaults so the live-seeding set fits the ~7.3 TB usable hot SSD. |

One-time ownership/identity setup (run when the first media service is wired):

```bash
# buntu is uid/gid 1000:1000 already; just take ownership of the pool trees:
sudo chown -R 1000:1000 /srv/video /srv/audio
# (each container then sets PUID=1000, PGID=1000, UMASK=002 in its .env / compose environment)
```

### 14.1 Interim reality — the mover and promoter are NOT built yet

Until §5 (mover) and §7 (promoter) exist, the "it just works" guarantees in §13 are only **partly**
true. What is actually live today (§0):

- **Placement works:** new writes land on the SSD (`category.create=ff`); reads come from whichever
  branch holds the file; hd-idle parks the cold HDD. So downloads, hot playback, audio, and
  browsing already stay off the HDD.
- **Nothing demotes:** the SSD hot tier only ever *fills*. There is no automatic SSD→HDD migration,
  no seeding pin enforcement, and **no promote-on-detail-view** (so a cold-content play wakes the
  HDD for the whole session — expected, §8B/§12).
- **The danger to watch in the interim — SSD fill → `moveonenospc` spill:** when the SSD hits
  `minfreespace=50G`, mergerfs `moveonenospc=true` starts placing **new writes directly on the cold
  HDD** (waking it), and nothing moves them back. So in the interim you must **manually** keep the
  hot SSD below its limit (move finished/cold titles off, or hand-place them on the cold branch
  during the mover window) — or accept that a full SSD degrades into HDD writes. **Don't run the
  download stack unattended at scale before the mover exists.**
- **The Docker mount-ordering safeguard (§6) must be built before the first arr runs** — see 14.2-F;
  it is independent of the mover and is a hard prerequisite, not optional.

### 14.2 Scenarios §13 didn't fully cover

**A. SSD seeding budget (the number behind "bound the seeding set", §13.3).** Usable hot SSD is
**~7.3 TB**. Reserve `minfreespace`=50 GB + download/unpack scratch headroom (say ~0.5–1 TB) + the
hot *library* working set; whatever remains is the **simultaneous-seeding cap**. Enforce it in
qBittorrent with **ratio 2.0 OR 30-day** seed limits (whichever first → then stop) so finished
torrents stop seeding and become demotable, *plus* keep an eye on total active-seeding size against
that cap. If the seeding set would exceed the cap, tighten the limits — don't let seeding push the
SSD into spill (14.1).

**B. `minfreespace` / download-bigger-than-free-SSD spill.** A large season pack (and its temporary
unpack copy) can blow past the 50 GB headroom and spill to HDD. Mitigations: set **per-category disk
space limits** in qBittorrent/SABnzbd so a job won't start without room; keep the SAB **unpack/incomplete
dir** small/rotated (an **NVMe scratch dir** for usenet `incomplete/` is the clean fix — §13.4). Once
the mover exists, it relieves pressure nightly; until then this is manual (14.1).

**C. Upgrades / replacements while seeding.** When an arr grabs a better release, it imports the new
file and removes the old library link. If the **old** file is still seeding (its torrent link
persists), removing the library link does **not** free the SSD until the old torrent is removed
(link-count, §13.3) — and qBittorrent may keep seeding the now-superseded release. Decision baked in:
let seed limits (14.0) retire the old torrent naturally; don't force-delete a seeding torrent's data
on upgrade. Set the arr's behavior to import-and-keep (don't hard-delete the download).

**D. arr Recycle Bin.** Point each arr's **Recycle Bin to an SSD-resident path inside the pool**
(e.g. `/srv/video/.recyclebin`, which `category.create=ff` keeps on the SSD branch) and set a short
auto-cleanup (e.g. 7 days). Rationale: deletes/upgrades then **don't wake the HDD**, and you keep a
brief undo window. Never point it at a cold-branch path. Sidecars/empty-dir cleanup runs on SSD.

**E. Manual imports / importing an existing library.** Any source **outside `/srv/video`** crosses
filesystems → full copy (and possibly an HDD wake if the destination resolves to the cold branch).
For manual imports, stage the files **inside** `/srv/video` first (so it's same-filesystem → hardlink/
instant) and run large bulk imports during the **mover window**. Importing a pre-existing library that
already lives on the pool is fine (same filesystem).

**F. Docker boot-order drop-in (the §6 safeguard) — concrete spec.** `/srv/video` mounted via
`/etc/fstab` gives systemd the auto-generated unit name **`srv-video.mount`**. Gate Docker on it so a
container can never bind an empty `/srv/video` and mass-delete a library:

```ini
# /etc/systemd/system/docker.service.d/10-require-srv-video.conf
[Unit]
RequiresMountsFor=/srv/video /srv/audio
After=srv-video.mount srv-audio.mount
Requires=srv-video.mount
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart docker
```

Do **not** add `nofail` to the merged mount — failing closed is correct (§6). Build this **before**
the first arr container.

**G. Season packs / multi-file torrents.** A pack seeds as a unit: the arr hardlinks each episode
into the library individually, but **all** episode links share the pack's seeding lifetime, so the
mover can't demote *any* episode in the pack until the **whole pack** stops seeding (link-count is
per-file, but the seeding *torrent* holds a link on every file). Budget the seeding cap (14.2-A) in
whole-pack units.

**H. Integrity hash scan (§9) wakes the entire cold library.** Reading every byte of every cold file
is the single biggest HDD-wake event. **Run it inside the mover window, infrequently** (e.g. monthly),
as one deliberate spin-up — never on a daily/continuous schedule. It's detection-only; a slow monthly
pass is fine.

**I. Jellyfin whole-file background tasks.** Beyond deep scans (§13.10): **chapter-image / trickplay
(BIF) generation**, **embedded-subtitle extraction**, and **Intro-Skipper / credit detection** all read
whole media files → wake the cold HDD across the library. Generate/run them on **import (file still on
SSD)** or **inside the mover window**, and disable "extract on play/scan" for cold content. Playlists/
collections are DB-only (safe). Bazarr likewise: prefer downloaded sidecars over forcing embedded-sub
extraction from cold files.

**J. `relatime` recency resolution (for the future mover).** The hot SSD uses `relatime` (§3), which
only updates atime once per ~24 h (or when atime < mtime/ctime). So the mover's "recently watched"
signal is **day-granular, not true LRU** — it can tell "watched in the last day" but not "an hour ago
vs this morning." Design demotion policy around day-level recency (+ the open-file/seeding checks),
not fine-grained access order. (Note: Jellyfin reads do bump atime since hot isn't `noatime`, but
transcode/seek patterns make raw atime a coarse proxy for "watched.")

### 14.3 Remaining open decisions (still TODO — not blockers for the *paths*, but needed to finish)

- **Download-client categories & save-paths (download-stack phase, §8).** Proposed concrete defaults
  to confirm: qBittorrent categories `radarr→/srv/video/torrents/movies`, `sonarr→/srv/video/torrents/tv`,
  `lidarr→/srv/audio/torrents/music`; SAB categories `movies/tv→/srv/video/usenet/...`,
  `music→/srv/audio/usenet/music`. **Ports** for every service are unassigned — pick a scheme when the
  compose stack is authored.
- **Samba shares (§0/§1).** No share definitions, users, or permission model yet. When built: shares
  expose `/srv/video` & `/srv/audio` (never raw branches); SMB writes land on SSD via
  `category.create=ff` and carry the same `minfreespace` spill caveat as 14.2-B (a huge SMB copy can
  spill to HDD).
- **E-books / Readarr (§13.8).** Decide whether e-books are in scope. If yes, give them a path (e.g.
  `/srv/audio/media/ebooks`) and accept Readarr's unmaintained status; if no, drop Readarr.
- **Mover & promoter implementation (§5/§7).** The window (04:00–06:00) and policy are decided; the
  daemons themselves still need building. This is the largest remaining piece and the one that makes
  the spin-down design fully hold.

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
