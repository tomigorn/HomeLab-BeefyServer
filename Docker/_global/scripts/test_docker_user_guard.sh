#!/usr/bin/env bash
# Runs docker-user-guard.sh in throwaway network namespaces (sudo unshare -n): the live firewall is never touched. Usage: scripts/test_docker_user_guard.sh
HERE=$(cd "$(dirname "$0")" && pwd)
G="$HERE/docker-user-guard.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1 ${2:-}"; }
ns() { sudo -n unshare -n bash -c "$1" 2>&1; }
dump='iptables -S; ip6tables -S'

echo "applies both chains, final drops in place, hooks once"
out=$(ns "bash '$G' >/dev/null && $dump")
grep -qx -- "-A DOCKER-USER -d 192.168.1.0/24 -o enp6s0 -j DROP" <<<"$out" && ok "LAN drop present" || bad "LAN drop present" "$(head -3 <<<"$out")"
[[ $(grep -- "-A DOCKER-USER" <<<"$out" | tail -1) == "-A DOCKER-USER -j RETURN" ]] && ok "chain ends with RETURN" || bad "chain ends with RETURN"
grep -q -- '-A DOCKER-USER .*--log-prefix "MOVIE-INGRESS drop "' <<<"$out" && ok "log prefix with spaces survives the restore file" || bad "log prefix quoting"
[[ $(grep -c -- "-A FORWARD -j DOCKER-USER" <<<"$out") == 1 && $(grep -c -- "-A INPUT -i br-+ -j BEEFY-INPUT" <<<"$out") == 1 && $(grep -c -- "-A INPUT -i enp6s0 -j BEEFY-INPUT6" <<<"$out") == 1 ]] && ok "each hook exactly once" || bad "hooks once"
echo "$out" > "$T/once"

echo "a second run changes nothing"
out=$(ns "bash '$G' >/dev/null && bash '$G' >/dev/null && $dump")
[[ $out == "$(cat "$T/once")" ]] && ok "idempotent" || bad "idempotent" "$(diff <(echo "$out") "$T/once")"

echo "stale rules in the chains are replaced"
out=$(ns "iptables -N DOCKER-USER; iptables -A DOCKER-USER -s 10.9.9.9 -j ACCEPT; ip6tables -N BEEFY-INPUT6; ip6tables -A BEEFY-INPUT6 -j ACCEPT; bash '$G' >/dev/null && $dump")
[[ $out == "$(cat "$T/once")" ]] && ok "same result as a clean apply" || bad "stale rules replaced" "$(diff <(echo "$out") "$T/once")"

echo "a rule set iptables-restore refuses changes nothing"
mkdir -p "$T/bin"; printf '#!/usr/bin/env bash\n[[ " $* " == *" --test "* ]] && { echo "fake: line 7 failed" >&2; exit 2; }\nexec /usr/sbin/iptables-restore "$@"\n' > "$T/bin/iptables-restore"; chmod +x "$T/bin/iptables-restore"
out=$(ns "iptables -N DOCKER-USER; iptables -A DOCKER-USER -s 10.9.9.9 -j DROP; PATH='$T/bin':\$PATH bash '$G' >/dev/null; echo rc=\$?; $dump")
grep -q "^rc=1$" <<<"$out" && ok "exit 1" || bad "exit 1" "$(head -3 <<<"$out")"
grep -qx -- "-A DOCKER-USER -s 10.9.9.9/32 -j DROP" <<<"$out" && [[ $(grep -c -- "-A DOCKER-USER" <<<"$out") == 1 ]] && ok "old chain untouched" || bad "old chain untouched"
grep -q "nothing changed" <<<"$out" && ok "says nothing changed" || bad "says nothing changed"

echo "$PASS passed, $FAIL failed"; [[ $FAIL == 0 ]]
