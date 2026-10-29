#!/usr/bin/env bash
# The API-driven units (review S-18) carry subtitle-check's sandbox minus the network block: read-only system and home, writes only to listed dirs, no capabilities.
# Usage: tests/test_unit_hardening.sh [unit dir]   (default: systemd/)
U=${1:-$(cd "$(dirname "$0")/.." && pwd)/systemd}
PASS=0; FAIL=0
NEED="NoNewPrivileges=true PrivateTmp=true ProtectSystem=strict ProtectHome=read-only PrivateDevices=yes ProtectKernelTunables=yes ProtectKernelModules=yes ProtectKernelLogs=yes ProtectControlGroups=yes ProtectClock=yes ProtectHostname=yes ProtectProc=invisible ProcSubset=pid RestrictNamespaces=yes RestrictRealtime=yes RestrictSUIDSGID=yes LockPersonality=yes MemoryDenyWriteExecute=yes CapabilityBoundingSet= SystemCallArchitectures=native SystemCallFilter=@system-service"
for u in clear-data-wipe clear-data-wipe-added seerr-sync request-state night-downloads media-metrics; do
  missing=""
  for l in $NEED; do grep -qxF "$l" "$U/$u.service" 2>/dev/null || missing+=" $l"; done
  grep -qx 'ReadWritePaths=/home/buntu/.local/state/movie-downloads.*' "$U/$u.service" 2>/dev/null || missing+=" ReadWritePaths(state dir)"
  if [ -z "$missing" ]; then PASS=$((PASS+1)); echo "  ok   $u sandboxed"; else FAIL=$((FAIL+1)); echo "  FAIL $u missing:$missing"; fi
done
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
