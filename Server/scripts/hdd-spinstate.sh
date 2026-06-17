#!/usr/bin/env bash
#
# Append the cold HDD's power state to a log WITHOUT waking it.
#
#   - This HAMR Exos returns "unknown" to `hdparm -C`, so we use `smartctl` instead.
#   - `smartctl -n standby` checks the power mode first (ATA CHECK POWER MODE, a non-data
#     command) and ABORTS without spinning the drive up if it's parked — i.e. non-waking.
#   - /proc/diskstats is in-memory kernel counters; reading it never touches the disk.
#   - Neither path increments the sectors counters, so hd-idle's idle timer is unaffected.
#
# Reported state:  STANDBY = motor off (parked/asleep, the goal)
#                  IDLE_A/IDLE_B/IDLE/ACTIVE = platters spinning
#
# Drive is referenced BY SERIAL (re-cable to any slot freely).

DISK_ID="${1:-/dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M}"
LOG="${HDD_SPINSTATE_LOG:-/var/log/hdd-spinstate.log}"

dev="$(readlink -f "$DISK_ID")"
kname="$(basename "$dev")"

out="$(smartctl -n standby -i "$DISK_ID" 2>&1)"
if printf '%s' "$out" | grep -qiE 'STANDBY mode|SLEEP mode'; then
  state="STANDBY"                       # parked — smartctl refused to wake it
else
  state="$(printf '%s' "$out" | sed -n 's/^Power mode was:[[:space:]]*//p' | head -1)"
  [ -n "$state" ] || state="unknown"
fi

# sectors_read = field 6, sectors_written = field 10 of /proc/diskstats
sr="$(awk -v k="$kname" '$3==k{print $6}' /proc/diskstats)"
sw="$(awk -v k="$kname" '$3==k{print $10}' /proc/diskstats)"

printf '%s  %-10s read=%s written=%s (cumulative sectors)\n' \
  "$(date -Is)" "$state" "${sr:-?}" "${sw:-?}" >> "$LOG"
