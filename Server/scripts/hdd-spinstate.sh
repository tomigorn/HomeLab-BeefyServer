#!/usr/bin/env bash
#
# Append the cold HDD's power state to a log WITHOUT waking it.
#
#   - `hdparm -C` issues ATA CHECK POWER MODE (a non-data command); it reports
#     active/idle | standby | sleeping and does NOT spin up a parked drive.
#   - /proc/diskstats is in-memory kernel counters; reading it never touches the disk.
#   - Neither increments the sectors counters, so hd-idle's idle timer is unaffected.
#
# Drive is referenced BY SERIAL (re-cable to any slot freely).

DISK_ID="${1:-/dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M}"
LOG="${HDD_SPINSTATE_LOG:-/var/log/hdd-spinstate.log}"

dev="$(readlink -f "$DISK_ID")"
kname="$(basename "$dev")"

state="$(hdparm -C "$DISK_ID" 2>/dev/null | awk '/drive state is/{print $NF}')"
[ -n "$state" ] || state="unknown"

# sectors_read = field 6, sectors_written = field 10 of /proc/diskstats
sr="$(awk -v k="$kname" '$3==k{print $6}' /proc/diskstats)"
sw="$(awk -v k="$kname" '$3==k{print $10}' /proc/diskstats)"

printf '%s  %-12s read=%s written=%s (cumulative sectors)\n' \
  "$(date -Is)" "$state" "${sr:-?}" "${sw:-?}" >> "$LOG"
