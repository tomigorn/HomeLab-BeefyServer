#!/usr/bin/env bash
# Tests for beefy-keep-awake and for the units that use it. Run: bash test_beefy_keep_awake.sh
set -u
D=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$D/../.." && pwd)
T=$(mktemp -d); trap 'rm -r "$T"' EXIT
export BEEFY_KEEP_AWAKE_RUN=$T
H=$D/beefy-keep-awake
pass=0; fail=0
ok() { if eval "$2"; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL $1"; fi; }

"$H" on >/dev/null
ok "on without holder makes the shared file" '[ -e "$T/beefy-keep-awake" ]'
"$H" on upscale >/dev/null
"$H" on maintenance-window >/dev/null
ok "on <holder> makes its own file" '[ -e "$T/beefy-keep-awake.d/upscale" ] && [ -e "$T/beefy-keep-awake.d/maintenance-window" ]'
"$H" off upscale >/dev/null
ok "off <holder> keeps the other holders" '[ -e "$T/beefy-keep-awake.d/maintenance-window" ] && [ -e "$T/beefy-keep-awake" ]'
ok "off <holder> removes its own file" '[ ! -e "$T/beefy-keep-awake.d/upscale" ]'
"$H" off maintenance-window >/dev/null
ok "off <holder> leaves the shared file alone" '[ -e "$T/beefy-keep-awake" ]'
ok "status lists the shared hold" '"$H" status | grep -q "^held"'
"$H" off >/dev/null
ok "off without holder removes only the shared file" '[ ! -e "$T/beefy-keep-awake" ]'
ok "status released when nothing holds" '[ "$("$H" status)" = released ]'
"$H" on night >/dev/null
ok "status names a holder" '"$H" status | grep -q "night"'
"$H" off night >/dev/null
for bad in ../x a/b .hidden "" "A B"; do
  ok "holder '$bad' refused" '! "$H" on "$bad" 2>/dev/null && [ -z "$(ls -A "$T/beefy-keep-awake.d")" ] && [ ! -e "$T/x" ]'
done
ok "unknown command exits 2" '"$H" bogus 2>/dev/null; [ $? -eq 2 ]'

units=$(grep -rl "beefy-keep-awake" "$REPO/Docker" "$REPO/Server" --include='*.service')
ok "units found" '[ -n "$units" ]'
for u in $units; do
  n=$(basename "$u" .service)
  ok "$n takes its own hold" 'grep -qE "^ExecStartPre=\+?/usr/local/sbin/beefy-keep-awake on [a-z0-9-]+$" "$u"'
  h=$(sed -nE 's#^ExecStartPre=\+?/usr/local/sbin/beefy-keep-awake on ([a-z0-9-]+)$#\1#p' "$u")
  ok "$n releases the same hold" 'grep -qE "^ExecStopPost=\+?/usr/local/sbin/beefy-keep-awake off $h$" "$u"'
  ok "$n never removes the shared file" '! grep -qE "keep-awake off *$|rm -f /run/beefy-keep-awake( |$)" "$u"'
done
ok "apt hook uses its own holder file" 'grep -q "beefy-keep-awake.d/apt" "$D/apt-99-beefy-keep-awake.conf" && ! grep -q "rm -f /run/beefy-keep-awake " "$D/apt-99-beefy-keep-awake.conf"'

echo "$pass passed, $fail failed"
(( fail == 0 ))
