#!/usr/bin/env bash
# Runs migrate-rollback-tags.sh against a fake docker that keeps tags in a file. Usage: scripts/test_migrate_rollback_tags.sh
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1 ${2:-}"; }
cat > "$T/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/log"
D=$FAKE_DIR
case "$1 $2" in
  "info "*) exit 0 ;;
  "image ls") awk '{print $1}' "$D/tags" ;;
  "image inspect") awk -v r="${@: -1}" '$1==r {print $2, $3}' "$D/tags" | grep . ;;
  "tag "*) t=$(awk -v r="$2" '$1==r {print $3}' "$D/tags" | head -1); echo "$3 $2 ${t:-2026-10-29}" >> "$D/tags" ;;
  "image rm") [[ $3 == -f ]] && exit 9; n=$(awk -v r="$3" '$1==r {print $2}' "$D/tags"); [[ $(grep -c " $n " "$D/tags") -gt 1 ]] || exit 7; grep -v "^$3 " "$D/tags" > "$D/t"; mv "$D/t" "$D/tags" ;;
  "ps -a") cat "$D/ps" ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
STUB
chmod +x "$T/docker"
export DOCKER="$T/docker" FAKE_DIR="$T"
cat > "$T/tags" <<'EOF'
willfarrell/autoheal:rollback-2026-10-29 sha:a 2026-10-29
lscr.io/linuxserver/prowlarr:rollback-2026-10-29-mouse sha:b 2026-10-29
stonks:rollback-20261023-023558 sha:c 2026-10-23
cloudflare/cloudflared:2026.10.4 sha:d 2026-10-20
cloudflare/cloudflared:rollback-2026.10.4 sha:d 2026-10-28
reg.example:5000/x:rollback-2026-10-01 sha:e 2026-10-01
avatar-picker:v1.13.0-rollback sha:f 2026-10-20
beefy-waker:rollback sha:g 2026-10-20
inuse:rollback-2026-10-29 sha:h 2026-10-29
rollback.invalid/done:2026-10-29-x sha:i 2026-10-29
EOF
echo "inuse:rollback-2026-10-29" > "$T/ps"

echo "dry run changes nothing"
bash "$HERE/migrate-rollback-tags.sh" --dry-run > "$T/out" 2>&1
! grep -qE '^(tag|image rm) ' "$T/log" && ok "no tag or rm" || bad "dry run" "$(cat "$T/log")"
grep -q 'would move willfarrell/autoheal:rollback-2026-10-29 -> rollback.invalid/willfarrell/autoheal:2026-10-29' "$T/out" && ok "reports the move" || bad "reports" "$(cat "$T/out")"

echo "migration"
: > "$T/log"; bash "$HERE/migrate-rollback-tags.sh" > "$T/out" 2>&1; rc=$?
[[ $rc == 0 ]] && ok "exit 0" || bad "exit 0" "rc=$rc $(cat "$T/out")"
has() { grep -q "^$1 $2 " "$T/tags" && ok "$1" || bad "$1" "$(cat "$T/tags")"; }
hasnt() { ! grep -q "^$1 " "$T/tags" && ok "gone: $1" || bad "gone: $1" "$(cat "$T/tags")"; }
has rollback.invalid/willfarrell/autoheal:2026-10-29 sha:a; hasnt willfarrell/autoheal:rollback-2026-10-29
has rollback.invalid/lscr.io/linuxserver/prowlarr:2026-10-29-mouse sha:b; hasnt lscr.io/linuxserver/prowlarr:rollback-2026-10-29-mouse
has rollback.invalid/stonks:20261023-023558 sha:c
has rollback.invalid/cloudflare/cloudflared:2026-10-28-2026.10.4 sha:d; has cloudflare/cloudflared:2026.10.4 sha:d; hasnt cloudflare/cloudflared:rollback-2026.10.4
has rollback.invalid/reg.example-5000/x:2026-10-01 sha:e
has avatar-picker:v1.13.0-rollback sha:f; has beefy-waker:rollback sha:g
has inuse:rollback-2026-10-29 sha:h; ! grep -q '^rollback.invalid/inuse' "$T/tags" && ok "tag a container runs by name left alone" || bad "in-use tag left alone"
! grep -q 'image rm -f\|rmi' "$T/log" && ok "never forces a removal" || bad "never forces"
echo "second run is a no-op"
: > "$T/log"; bash "$HERE/migrate-rollback-tags.sh" > "$T/out" 2>&1
! grep -qE '^(tag|image rm) ' "$T/log" && ok "idempotent" || bad "idempotent" "$(cat "$T/log")"

echo "$PASS passed, $FAIL failed"; [[ $FAIL == 0 ]]
