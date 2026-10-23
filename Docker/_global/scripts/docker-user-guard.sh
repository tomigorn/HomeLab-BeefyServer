#!/usr/bin/env bash
# docker-user-guard.sh — beefy's container firewall (IPv4 DOCKER-USER + the IPv6 INPUT guard).
#
# Inbound: published container ports answer only fastpi (Traefik, Prometheus, Portainer, Cup). Allowlist, so a newly published port is closed by default.
# Outbound: containers may not open connections into the LAN, except Jellyfin/Seerr and Ryot → fastpi :443 (sso + tracker via extra_hosts).
# IPv6: no container has IPv6, but docker-proxy listens on [::] for every published port, so only sshd is reachable over IPv6.
#
# Idempotent: rebuilds both chains on every run. `--persist` also writes them into /etc/iptables/rules.v4 / rules.v6 (only those lines; Docker's own chains there are left alone).
set -euo pipefail

IFACE=enp6s0
LAN=192.168.1.0/24
FASTPI=192.168.1.2/32
JELLYFIN_NET=172.28.11.0/24   # jellyfin, seerr
RYOT_NET=172.20.0.0/16        # ryot, ryot-go, ryot-db

iptables -N DOCKER-USER 2>/dev/null || true
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD -j DOCKER-USER

iptables -F DOCKER-USER
iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A DOCKER-USER -i "$IFACE" ! -s "$FASTPI" -j DROP
iptables -A DOCKER-USER -s "$JELLYFIN_NET" -o "$IFACE" -d "$FASTPI" -p tcp --dport 443 -j RETURN
iptables -A DOCKER-USER -s "$RYOT_NET" -o "$IFACE" -d "$FASTPI" -p tcp --dport 443 -j RETURN
iptables -A DOCKER-USER -o "$IFACE" -d "$LAN" -j DROP
iptables -A DOCKER-USER -j RETURN

ip6tables -N BEEFY-INPUT6 2>/dev/null || true
ip6tables -F BEEFY-INPUT6
ip6tables -A BEEFY-INPUT6 -p tcp --dport 22 -j RETURN
ip6tables -A BEEFY-INPUT6 -p tcp -m conntrack --ctstate NEW -j DROP
ip6tables -A BEEFY-INPUT6 -j RETURN
while ip6tables -C INPUT -i "$IFACE" -j BEEFY-INPUT6 2>/dev/null; do ip6tables -D INPUT -i "$IFACE" -j BEEFY-INPUT6; done
ip6tables -S INPUT | { grep -E '^-A INPUT .*--dports .* -j DROP$' || true; } | sed 's/^-A /-D /' | while read -r r; do eval "ip6tables $r"; done
ip6tables -I INPUT -i "$IFACE" -j BEEFY-INPUT6

if [[ ${1:-} == --persist ]]; then
  RULES_DIR=${RULES_DIR:-/etc/iptables} python3 - <<'PY'
import os, subprocess
d = os.environ['RULES_DIR']
def put(path, cmd, chain, hook):
    lines = open(path).read().split('\n')
    start = lines.index('*filter'); end = lines.index('COMMIT', start)
    body = [l for l in lines[start + 1:end] if not l.startswith((f'-A {chain} ', f':{chain} ', '-A INPUT -i enp6s0 -p tcp -m multiport --dports'))]
    last_decl = max(i for i, l in enumerate(body) if l.startswith(':'))
    body.insert(last_decl + 1, f':{chain} - [0:0]')
    if hook not in body:
        body.insert(next(i for i, l in enumerate(body) if l.startswith('-A ')), hook)
    rules = [l for l in subprocess.check_output([cmd, '-S', chain], text=True).splitlines() if l.startswith(f'-A {chain} ')]
    open(path, 'w').write('\n'.join(lines[:start + 1] + body + rules + lines[end:]))
put(f'{d}/rules.v4', 'iptables', 'DOCKER-USER', '-A FORWARD -j DOCKER-USER')
put(f'{d}/rules.v6', 'ip6tables', 'BEEFY-INPUT6', '-A INPUT -i enp6s0 -j BEEFY-INPUT6')
PY
  iptables-restore --test "${RULES_DIR:-/etc/iptables}/rules.v4" && ip6tables-restore --test "${RULES_DIR:-/etc/iptables}/rules.v6"
  echo "persisted to ${RULES_DIR:-/etc/iptables}/rules.v4 + rules.v6"
fi

iptables -L DOCKER-USER -n -v --line-numbers
ip6tables -L BEEFY-INPUT6 -n -v --line-numbers
