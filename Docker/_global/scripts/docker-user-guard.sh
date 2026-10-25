#!/usr/bin/env bash
# docker-user-guard.sh — beefy's container firewall (IPv4 DOCKER-USER + the IPv6 INPUT guard).
#
# Inbound: published container ports answer only fastpi (Traefik, Prometheus, Portainer, Cup). Allowlist, so a newly published port is closed by default.
# Outbound: containers may not open connections into the LAN, except Jellyfin/Seerr, Ryot and Dependency-Track → fastpi :443 (sso + tracker via extra_hosts).
# IPv6: no container has IPv6, but docker-proxy listens on [::] for every published port, so only sshd is reachable over IPv6.
#
# Idempotent: rebuilds both chains on every run. `--persist` writes /etc/iptables/rules.v4 / rules.v6 with ONLY these two chains. Never `netfilter-persistent save`: it freezes Docker's own per-bridge rules, which come back at boot for bridges that no longer exist and silently drop traffic to reused container IPs (seen on fastpi 2026-10-24).
set -euo pipefail

IFACE=enp6s0
LAN=192.168.1.0/24
FASTPI=192.168.1.2/32
JELLYFIN_NET=172.28.11.0/24   # jellyfin, seerr
RYOT_NET=172.28.15.0/24       # ryot, ryot-go, ryot-db (pinned in Ryot compose)
DT_NET=172.28.14.0/24         # dependency-track (apiserver: OIDC JWKS from sso)

iptables -N DOCKER-USER 2>/dev/null || true
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD -j DOCKER-USER

iptables -F DOCKER-USER
iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A DOCKER-USER -i "$IFACE" ! -s "$FASTPI" -j DROP
iptables -A DOCKER-USER -s "$JELLYFIN_NET" -o "$IFACE" -d "$FASTPI" -p tcp --dport 443 -j RETURN
iptables -A DOCKER-USER -s "$RYOT_NET" -o "$IFACE" -d "$FASTPI" -p tcp --dport 443 -j RETURN
iptables -A DOCKER-USER -s "$DT_NET" -o "$IFACE" -d "$FASTPI" -p tcp --dport 443 -j RETURN
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
  d=${RULES_DIR:-/etc/iptables}
  persist() {  # file, save-cmd, restore-cmd, chain, hook
    local tmp; tmp=$(mktemp)
    { echo "# Written by docker-user-guard.sh --persist: only $4, Docker builds its own chains."
      echo "*filter"; echo ":$4 - [0:0]"; echo "$5"
      "$2" -S "$4" | grep '^-A '
      echo "COMMIT"; } > "$tmp"
    "$3" --test < "$tmp"
    [[ -e $1 ]] && cp -a "$1" "$1.bak-$(date +%Y%m%d-%H%M%S)"
    install -m 640 "$tmp" "$1"; rm -f "$tmp"
  }
  persist "$d/rules.v4" iptables iptables-restore DOCKER-USER "-A FORWARD -j DOCKER-USER"
  persist "$d/rules.v6" ip6tables ip6tables-restore BEEFY-INPUT6 "-A INPUT -i $IFACE -j BEEFY-INPUT6"
  echo "persisted to $d/rules.v4 + rules.v6"
fi

iptables -L DOCKER-USER -n -v --line-numbers
ip6tables -L BEEFY-INPUT6 -n -v --line-numbers
