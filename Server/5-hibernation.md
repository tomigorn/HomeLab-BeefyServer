# Hibernation of Ubuntu Server (deep sleep / suspend-to-disk, S4)

The goal is that the server can go to **deep sleep hibernation** (S4): RAM is
written to swap, the machine powers down to near-zero draw, and on wake the kernel
restores the previous session from the image.

> ⚠️ **Security note — accepted risk.** Hibernation writes the *entire contents of
> RAM* to `/swap.img` on the unencrypted OS disk. Anything live in memory at
> hibernate time — secrets, tokens, keys, decrypted data — is persisted to disk in
> the clear and remains there until that swap region is overwritten. The proper fix
> is an encrypted swap device (LUKS), which complicates resume. **We have decided
> *not* to solve this and accept the risk** on this homelab box. Documented here so
> the trade-off is explicit.

---

## Sleep strategy — which power state? (read this first)

beefy is a **stateless Docker host**: services come back on their own via container
`restart:` policies, and there is no precious in-RAM state to preserve across a sleep.
So the choice is about **reliability and power**, not state preservation.

| Strategy | Power while asleep¹ | Time until fully awake² | Docker behaviour on wake (esp. after a long sleep) | Survives power loss asleep | Keeps RAM state |
|---|---|---|---|---|---|
| **S3 — suspend-to-RAM** | ~5–10 W | **~1–3 s** | Thaws & continues, but a wall-clock *time-jump* → in-container cron may all fire at once, healthchecks flap, dropped DB/MQTT/NFS sockets must reconnect | ❌ power blip = RAM lost (unclean, like a crash) | ✅ |
| **S4 — hibernate** *(this doc)* | **≈ off, ~1–3 W** | ~20–60 s (POST + read image; scales with RAM *in use*, **not** with sleep length) | Same time-jump / reconnect issues as S3 once the image restores | ✅ image is on disk; resumes on next power-on | ✅ |
| **suspend-then-hibernate** | ~5–10 W → ≈ off after delay | fast (S3) if woken soon, else ~20–60 s | S3-like early, S4-like later | ✅ once it has hibernated | ✅ |
| ⭐ **poweroff + WOL (S5)** | **lowest, ~0.5–2 W** | ~20–45 s cold boot + a few s for the stack | **cleanest** — fresh boot, correct clock, `restart: unless-stopped` brings containers up with no time-warp or stale connections | ✅ irrelevant — it's already off | ❌ (not needed here) |

¹ Approximate — measure with a plug meter. ² Wake time does **not** grow with how long it slept.

**Does a long sleep hurt?** Wake time is independent of sleep duration — hibernated for
5 minutes or 5 days, it wakes equally fast (image size depends only on RAM *in use* at
hibernate time). What *does* grow with duration is **clock skew and dead connections**:
on resume from S3/S4 the wall clock jumps from hibernate-time to "now" in one step, so
time-sensitive containers (databases, lease/cluster services, anything checking TLS or
token expiry) may log errors, fire scheduled jobs all at once, or want a restart, and
long-lived sockets that timed out must reconnect. The Docker daemon itself survives. A
**poweroff + WOL** cold boot sidesteps all of it (correct clock, fresh container start),
which is why it is the cleanest option for a Docker host. **No** option here has an
"extremely long" wake — worst case is roughly under a minute.

> ⭐ **Recommendation: `poweroff` + WOL (S5).** Lowest power, most reliable (no swap /
> `resume_offset` / initramfs fragility to break on kernel upgrades), best Docker
> behaviour, and no unencrypted RAM image on disk. Use **suspend-then-hibernate** instead
> only if you want instant "came-right-back" wake during the day. The S4 procedure below
> is documented in full because it is the **foundation for both** hibernate and
> suspend-then-hibernate — set it up here, then choose your trigger. Wake in every case
> is via **WOL from `fastpi`** (see `6-WOL.md`).

---

## 0. Pre-flight: is hibernation even possible on this box?

```bash
# "disk" must appear -> kernel supports suspend-to-disk
$ cat /sys/power/state
freeze mem disk
```

**Secure Boot / kernel lockdown check.** Under kernel *lockdown* (which Secure Boot
turns on) the kernel **refuses to hibernate** — `systemctl hibernate` fails. Confirm
it's clear before relying on hibernation:

```bash
$ mokutil --sb-state
SecureBoot disabled

$ cat /sys/kernel/security/lockdown
[none] integrity confidentiality
```

`SecureBoot disabled` and lockdown `[none]` → good. If Secure Boot is **enabled** and
lockdown is `integrity`/`confidentiality`, either disable Secure Boot in firmware or
hibernation will not work.

## 1. Try the easy way first

```bash
$ swapon --show
NAME      TYPE SIZE USED PRIO
/swap.img file   8G   0B   -2

$ systemctl hibernate
Call to Hibernate failed: Not enough suitable swap space for hibernation available on compatible block devices and file systems
```

The default 8 GB swap is far smaller than RAM, so the image won't fit → configure a
bigger swapfile by hand.

## 2. Delete the small swap and create a bigger one

The swapfile must be **≥ system RAM** to hold the hibernation image.

```bash
# show RAM. here it's 62Gi
$ free -h
               total        used        free      shared  buff/cache   available
Mem:            62Gi       2.6Gi        59Gi       1.8Mi       1.3Gi        59Gi
Swap:          8.0Gi          0B       8.0Gi

# disable current swap
$ sudo swapoff /swap.img

# delete the old swap and create a new one 64G (adjust size >= your RAM)
$ sudo rm /swap.img
$ sudo fallocate -l 64G /swap.img

# secure and enable
$ sudo chmod 600 /swap.img
$ sudo mkswap /swap.img
Setting up swapspace version 1, size = 64 GiB (68719472640 bytes)
no label, UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
$ sudo swapon /swap.img

# verify
$ swapon --show
NAME      TYPE SIZE USED PRIO
/swap.img file  64G   0B   -2
$ free -h
               total        used        free      shared  buff/cache   available
Mem:            62Gi       5.0Gi        56Gi       1.8Mi       1.3Gi        57Gi
Swap:           63Gi          0B        63Gi
```

> **Why `fallocate`?** On **ext4** (our OS filesystem) `fallocate` is the fast, correct
> way to size a swapfile — `mkswap`/`swapon` accept the result and `filefrag` (§3)
> reports a usable physical offset. If you ever move the swapfile onto a filesystem
> where `swapon` rejects a `fallocate`d file (older kernels / different FS), fall back
> to `sudo dd if=/dev/zero of=/swap.img bs=1M count=$((64*1024))`. The swapfile **must
> live directly on the root filesystem** for the §3 offset to be valid — not on Btrfs,
> not behind an extra device-mapper layer.

Confirm it is mounted at boot in `/etc/fstab` (a swapfile uses its path, not a UUID):

```bash
$ sudo nano /etc/fstab
# swap file is mounted on boot
/swap.img       none    swap    sw      0       0
```

## 3. Compute `resume_offset` and find the block-device UUID

`filefrag` ships in `e2fsprogs`:

```bash
$ sudo apt update
$ sudo apt install e2fsprogs

# physical start offset of the swapfile (this is resume_offset)
$ OFFSET=$(sudo filefrag -v /swap.img | awk '/^ *0:/ {print $4}' | cut -d'.' -f1)
$ echo "resume_offset=$OFFSET"
resume_offset=4831232

# the block device that holds /swap.img, and its UUID
$ DEVICE=$(df --output=source /swap.img | tail -n1)
$ UUID=$(sudo blkid -s UUID -o value "$DEVICE")
$ echo "device=$DEVICE"
device=/dev/disk/...           # whatever holds the root fs on this install
$ echo "uuid=$UUID"
uuid=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Add the resume args to the kernel command line. **Mind the spelling — it is
`resume_offset`, not `resume_offeset`; a misspelled kernel parameter is silently
ignored, the kernel falls back to offset 0, and resume reads the wrong location:**

```bash
$ sudo nano /etc/default/grub
# add resume=UUID=<uuid> and resume_offset=<offset>
GRUB_CMDLINE_LINUX_DEFAULT="resume=UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx resume_offset=4831232"
```

**Ubuntu-idiomatic alignment.** The initramfs `resume` hook reads the resume *device*
from `/etc/initramfs-tools/conf.d/resume`. Declare it there too so both places agree
(the swapfile *offset* stays on the kernel cmdline via `resume_offset=`):

```bash
$ echo "RESUME=UUID=$UUID" | sudo tee /etc/initramfs-tools/conf.d/resume
RESUME=UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Regenerate boot config + initramfs and reboot:

```bash
$ sudo update-grub
Sourcing file `/etc/default/grub'
Generating grub configuration file ...
...
done

$ sudo update-initramfs -u -k all
update-initramfs: Generating /boot/initrd.img-<kernel>

$ sudo reboot
```

## 4. Tune swappiness (hibernation-only swap)

A 64 GB swapfile on a 62 GiB-RAM box should exist for hibernation, **not** for routine
paging. Bias the kernel away from swapping during normal operation:

```bash
$ echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
$ sudo sysctl --system
$ cat /proc/sys/vm/swappiness
10
```

## 5. Verify the resume parameters took effect

After the reboot:

```bash
# kernel command line contains the resume args we added (correct spelling!)
$ cat /proc/cmdline
BOOT_IMAGE=... resume=UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx resume_offset=4831232

# the kernel actually registered a resume device + offset (both must be NON-zero)
$ cat /sys/power/resume
253:0
$ cat /sys/power/resume_offset
4831232

# swap is active and large enough
$ swapon --show
NAME      TYPE SIZE USED PRIO
/swap.img file  64G   0B   -2

# suspend-to-disk supported
$ cat /sys/power/state
freeze mem disk
```

If `/sys/power/resume` is `0:0` or `/sys/power/resume_offset` is `0`, the params did
**not** apply (re-check the spelling and re-run `update-grub` / `update-initramfs`).

## 6. Prove that resume actually restores state

Powering off and coming back is **not** proof — a broken `resume_offset` makes the box
silently *cold-boot* instead of restoring. The reliable signal is the **boot
timestamp**: a true resume keeps the *same* boot, a cold boot starts a new one.

```bash
# --- BEFORE hibernating ---
$ uptime -s                         # note this exact timestamp
2026-06-18 09:00:00
$ journalctl --list-boots | tail -1 # note the current boot index
 0 abcd... Thu 2026-06-18 09:00:00 CEST—...
$ sleep 100000 &                    # a marker process that must survive
[1] 12345
$ date > /tmp/pre-hibernate.txt

# over SSH this drops the connection; wake via power button or WOL (see 6-WOL.md)
$ sudo systemctl hibernate

# --- AFTER waking ---
$ uptime -s                         # MUST be the SAME timestamp as before => resumed
2026-06-18 09:00:00
$ journalctl --list-boots | tail -1 # boot index did NOT increment => not a cold boot
 0 abcd... Thu 2026-06-18 09:00:00 CEST—...
$ jobs ; ls -l /tmp/pre-hibernate.txt   # marker job + file still present
[1]+  Running                 sleep 100000 &
$ journalctl -b 0 | grep -iE 'hibernation (entry|exit)|Resume from|resuming from'
```

If `uptime -s` changed or the boot index incremented, it **cold-booted** — resume is
broken; do not trust hibernation until this test passes cleanly.
