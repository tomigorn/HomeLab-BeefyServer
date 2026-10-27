#!/usr/bin/env bash
# docker-user-guard.sh — beefy's container firewall (IPv4 DOCKER-USER + BEEFY-INPUT, the IPv6 INPUT guard).
#
# Inbound: published container ports answer only fastpi (Traefik, Prometheus, Portainer, Cup). Allowlist, so a newly published port is closed by default.
# Outbound: containers may not open connections into the LAN, except Jellyfin/Seerr and Dependency-Track → fastpi :443 (sso via extra_hosts).
# Floppy: its users enter integration URLs that the server fetches (SSRF by design), so its subnet reaches the internet only, on tcp 80/443 (DNS goes through dockerd on the host): no LAN, no fastpi, no 10/8, 172.16/12 (other Docker networks, also beefy's published ports after DNAT), 192.168/16, 100.64/10, 169.254/16. The host itself is BEEFY-INPUT.
# Floppy inbound: its port answers fastpi and the jellyfin subnet only (Jellyfin and Seerr webhooks via 192.168.1.102:8012, hairpin DNAT); other containers are dropped (FLOPPY-INGRESS). Floppy itself still cannot open anything into the LAN or Docker.
# Host: containers reach the beefy host itself only with replies and ping (BEEFY-INPUT on INPUT from docker0/br-+). The apps talk to each other by container name; observed 2026-10-25: no container opens connections to the host. LAN access to the host (enp6s0) is untouched.
# IPv6: no container has IPv6, but docker-proxy listens on [::] for every published port, so only sshd is reachable over IPv6.
#
# Idempotent: rebuilds both chains on every run. `--persist` writes /etc/iptables/rules.v4 / rules.v6 with ONLY these two chains. Never `netfilter-persistent save`: it freezes Docker's own per-bridge rules, which come back at boot for bridges that no longer exist and silently drop traffic to reused container IPs (seen on fastpi 2026-10-24).
set -euo pipefail

IFACE=enp6s0
LAN=192.168.1.0/24
FASTPI=192.168.1.2/32
JELLYFIN_NET=172.28.11.0/24   # jellyfin, seerr (the only containers that reach Floppy)
DT_NET=172.28.14.0/24         # dependency-track (apiserver: OIDC JWKS from sso)
FLOPPY_NET=172.28.16.0/24     # floppy (pinned in Floppy compose; its redis net is internal)

iptables -N DOCKER-USER 2>/dev/null || true
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD -j DOCKER-USER

iptables -F DOCKER-USER
iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A DOCKER-USER -i "$IFACE" ! -s "$FASTPI" -j DROP
iptables -A DOCKER-USER -s "$JELLYFIN_NET" -d "$FLOPPY_NET" -p tcp --dport 8000 -j RETURN
iptables -A DOCKER-USER ! -i "$IFACE" ! -s "$FLOPPY_NET" -d "$FLOPPY_NET" -m limit --limit 6/min -j LOG --log-prefix "FLOPPY-INGRESS drop " --log-level 6
iptables -A DOCKER-USER ! -i "$IFACE" ! -s "$FLOPPY_NET" -d "$FLOPPY_NET" -j DROP
iptables -A DOCKER-USER -s "$FLOPPY_NET" -d "$FLOPPY_NET" -j RETURN
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
  iptables -A DOCKER-USER -s "$FLOPPY_NET" -d "$net" -j DROP
done
iptables -A DOCKER-USER -s "$FLOPPY_NET" -p tcp -m multiport --dports 80,443 -j RETURN
iptables -A DOCKER-USER -s "$FLOPPY_NET" -m limit --limit 6/min -j LOG --log-prefix "FLOPPY-EGRESS drop " --log-level 6
iptables -A DOCKER-USER -s "$FLOPPY_NET" -j DROP
iptables -A DOCKER-USER -s "$JELLYFIN_NET" -o "$IFACE" -d "$FASTPI" -p tcp --dport 443 -j RETURN
iptables -A DOCKER-USER -s "$DT_NET" -o "$IFACE" -d "$FASTPI" -p tcp --dport 443 -j RETURN
iptables -A DOCKER-USER -o "$IFACE" -d "$LAN" -j DROP
iptables -A DOCKER-USER -j RETURN

iptables -N BEEFY-INPUT 2>/dev/null || true
iptables -F BEEFY-INPUT
iptables -A BEEFY-INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A BEEFY-INPUT -p icmp -j RETURN
iptables -A BEEFY-INPUT -m limit --limit 6/min -j LOG --log-prefix "BEEFY-INPUT drop " --log-level 6
iptables -A BEEFY-INPUT -j DROP
for i in docker0 "br-+"; do
  while iptables -C INPUT -i "$i" -j BEEFY-INPUT 2>/dev/null; do iptables -D INPUT -i "$i" -j BEEFY-INPUT; done
  iptables -I INPUT -i "$i" -j BEEFY-INPUT
done

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
  persist() {  # file, save-cmd, restore-cmd, then pairs of chain and its hook line(s)
    local f=$1 save=$2 restore=$3 tmp i; shift 3; local a=("$@"); tmp=$(mktemp)
    { echo "# Written by docker-user-guard.sh --persist: only our own chains, Docker builds its own."
      echo "*filter"
      for ((i = 0; i < ${#a[@]}; i += 2)); do echo ":${a[i]} - [0:0]"; done
      for ((i = 0; i < ${#a[@]}; i += 2)); do echo "${a[i+1]}"; "$save" -S "${a[i]}" | grep '^-A '; done
      echo "COMMIT"; } > "$tmp"
    "$restore" --test < "$tmp"
    [[ -e $f ]] && cp -a "$f" "$f.bak-$(date +%Y%m%d-%H%M%S)"
    install -m 640 "$tmp" "$f"; rm -f "$tmp"
  }
  persist "$d/rules.v4" iptables iptables-restore DOCKER-USER "-A FORWARD -j DOCKER-USER" \
    BEEFY-INPUT "$(printf '%s\n' '-A INPUT -i docker0 -j BEEFY-INPUT' '-A INPUT -i br-+ -j BEEFY-INPUT')"
  persist "$d/rules.v6" ip6tables ip6tables-restore BEEFY-INPUT6 "-A INPUT -i $IFACE -j BEEFY-INPUT6"
  echo "persisted to $d/rules.v4 + rules.v6"
fi

iptables -L DOCKER-USER -n -v --line-numbers
iptables -L BEEFY-INPUT -n -v --line-numbers
ip6tables -L BEEFY-INPUT6 -n -v --line-numbers
