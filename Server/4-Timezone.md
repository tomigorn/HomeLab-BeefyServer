# Timezone & Time Sync

Set **beefy**'s local timezone to `Europe/Zurich` and confirm the clock is
NTP-synchronised. Everything here is driven by `timedatectl` (systemd) — no files
are edited by hand.

> **Why local time on a server?** The system clock and logs stay correct internally
> regardless of zone (systemd stores time in UTC), but a local zone makes journal
> timestamps, cron schedules, and `ls`/file mtimes read in **our** wall-clock time,
> which is what we want when reading logs at 2am.

---

## 0. Status — as-built (2026-06-18)

**LIVE on beefy now** (verified via `timedatectl`):

- ✅ Time zone = **`Europe/Zurich`**, currently reporting **CEST (+0200)** (DST applied automatically).
- ✅ **System clock synchronized: yes**; **NTP service: active** (`systemd-timesyncd`).
- ✅ **RTC in local TZ: no** — hardware clock kept in UTC (recommended default).

---

## 1. Check the starting state

```bash
$ timedatectl
               Local time: Mon 2025-09-15 03:08:31 UTC
           Universal time: Mon 2025-09-15 03:08:31 UTC
                 RTC time: Mon 2025-09-15 03:08:31
                Time zone: Etc/UTC (UTC, +0000)
System clock synchronized: yes
              NTP service: active
          RTC in local TZ: no
```

Read this as:

- **System clock synchronized: yes** — the clock is correct (NTP has set it).
- **NTP service: active** — time sync is running. On Ubuntu this is
  `systemd-timesyncd` (see §4); no separate `ntpd`/`chrony` is needed.
- **RTC in local TZ: no** — the hardware clock stays in **UTC**. Leave it this way
  (the recommended default); local-TZ RTC only exists for dual-boot Windows and
  invites DST/ambiguity bugs.

## 2. Find the zone name

```bash
$ timedatectl list-timezones | grep Europe
Europe/Sofia
Europe/Stockholm
...
Europe/Vaduz
Europe/Vatican
Europe/Vienna
...
Europe/Zagreb
Europe/Zaporozhye
Europe/Zurich
```

`set-timezone` also tab-completes the zone name, so the `grep` is just to confirm
the exact spelling.

## 3. Set the zone and verify

```bash
$ sudo timedatectl set-timezone Europe/Zurich

$ timedatectl
               Local time: Mon 2025-09-15 05:09:23 CEST
           Universal time: Mon 2025-09-15 03:09:23 UTC
                 RTC time: Mon 2025-09-15 03:09:23
                Time zone: Europe/Zurich (CEST, +0200)
System clock synchronized: yes
              NTP service: active
          RTC in local TZ: no
```

Local time now shows **CEST (+0200)** while Universal time is unchanged — exactly
what we want. The RTC correctly stays in UTC.

> **DST is automatic.** Because we set a *named* zone (`Europe/Zurich`), the kernel
> switches between CET (+0100) and CEST (+0200) on the correct dates by itself.
> Never set a fixed-offset zone like `Etc/GMT-1` for this — it would freeze the
> offset and break twice a year.

## 4. Ensure NTP is enabled (idempotent)

If `NTP service` ever shows `inactive`, turn it on:

```bash
$ sudo timedatectl set-ntp true
```

Inspect the sync source and drift:

```bash
$ timedatectl show-timesync --all       # server, poll interval, offset
$ systemctl status systemd-timesyncd
```

---

## Quick reference

| Task | Command |
|------|---------|
| Show status | `timedatectl` |
| List zones | `timedatectl list-timezones` |
| Set zone | `sudo timedatectl set-timezone Europe/Zurich` |
| Enable NTP | `sudo timedatectl set-ntp true` |
| Sync details | `timedatectl show-timesync --all` |
