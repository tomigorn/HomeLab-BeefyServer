# beefy host customizations around the containers (Docker/CUSTOMIZATIONS.md "Host"): only in `post-update-check.sh all`, no compose project.
check host.docker-user-lan '^blocked$' "$(docker exec movie-sonarr nc -zw2 192.168.1.2 22 >/dev/null 2>&1 && echo open || echo blocked)"
check host.beefy-input '^blocked$' "$(gw=$(docker network inspect -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' movie-downloads); docker exec movie-sonarr nc -zw2 "$gw" 22 >/dev/null 2>&1 && echo open || echo blocked)"
check host.movie-ingress '^script=(log|drop) live=\1 persisted=\1$' "$(m() { case $1 in *'MOVIE-INGRESS drop'*) echo drop ;; *'MOVIE-INGRESS seen'*) echo log ;; *) echo none ;; esac; }; echo "script=$(sed -n 's/^MOVIE_INGRESS=\([a-z]*\).*/\1/p' "$DIR/../scripts/docker-user-guard.sh") live=$(m "$(sudo -n iptables -S DOCKER-USER 2>&1)") persisted=$(m "$(sudo -n cat /etc/iptables/rules.v4 2>&1)")")"
check host.sso-egress '^200$' "$(docker exec jellyfin curl -s -m 8 -o /dev/null -w '%{http_code}' https://sso.holy-grail.ch/application/o/jellyfin/.well-known/openid-configuration 2>&1)"
check host.idle-watcher '^active fresh probe_error=0$' "$(echo "$(systemctl is-active beefy-idle-watcher.service) $(find /run/beefy-idle.prom -mmin -5 2>/dev/null | grep -q . && echo fresh || echo stale) probe_error=$(sed -n 's/^beefy_idle_busy{reason="probe_error"} //p' /run/beefy-idle.prom)")"
check host.timers '^enabled enabled enabled$' "$(systemctl is-enabled beefy-maintenance-window.timer auto-update.timer image-cleanup.timer | xargs)"
check host.idle-disks '^ok$' "$(for d in /srv/.disks/*; do p=$(findmnt -no SOURCE "$d" 2>/dev/null) || continue; k=$(lsblk -no PKNAME "$p" 2>/dev/null); grep -q "^DATA_DISKS=.*\b$k\b" /etc/beefy-idle.conf || echo "missing:$d"; done | xargs | sed 's/^$/ok/')"
check host.mergerfs-branches '^live=/srv/.disks/ssd-hot=RW,1479G:/srv/.disks/ssd-hot2=RW,1479G:/srv/.disks/hdd-cold=RW,2800G fstab=1$' "live=$(getfattr --only-values -n user.mergerfs.branches /srv/video/.mergerfs 2>/dev/null) fstab=$(grep -c '^/srv/.disks/ssd-hot=RW,1479G:/srv/.disks/ssd-hot2=RW,1479G:/srv/.disks/hdd-cold=RW,2800G ' /etc/fstab)"
check host.hd-idle '^-i 1200$' "$(ps -o args= -C hd-idle | grep -o 'K1S05Y9M -i [0-9]*' | cut -d' ' -f2-)"
check host.mover '^enabled 1$' "$(systemctl is-enabled beefy-mover.timer 2>&1) $(sed -n 's/^beefy_mover_last_run_success //p' /var/lib/beefy-mover/mover.prom 2>&1)"
check host.backup '^enabled success waited$' "$(systemctl is-enabled beefy-backup.timer 2>&1) $(systemctl show -p Result --value beefy-backup.service 2>&1) $(grep -q '^UNITS=.*beefy-backup.service' /usr/local/sbin/beefy-maintenance-window && echo waited || echo missing)"
# Root runs only root-owned copies (S-10/S-11): each copy equals its repo file, and no root unit of ours executes from /home.
root_copies() {
  local r m="" src dst; r=$(cd "$DIR/../../.." && pwd)
  while read -r src dst; do sudo -n cmp -s "$r/$src" "$dst" 2>/dev/null || m+=" ${dst##*/}"; done <<EOF
Server/3-Storage-Layout-and-Spindown/tier-move /usr/local/sbin/tier-move
Server/3-Storage-Layout-and-Spindown/tier_helper.py /usr/local/sbin/tier-helper
Server/3-Storage-Layout-and-Spindown/beefy_mover.py /usr/local/sbin/beefy-mover
Server/3-Storage-Layout-and-Spindown/tier-report /usr/local/sbin/tier-report
Server/3-Storage-Layout-and-Spindown/beefy_promoter.py /usr/local/sbin/beefy_promoter.py
Server/3-Storage-Layout-and-Spindown/hdd-spinstate.sh /usr/local/sbin/hdd-spinstate
Server/3-Storage-Layout-and-Spindown/smart_exporter.py /usr/local/lib/smart-exporter/smart_exporter.py
Server/3-Storage-Layout-and-Spindown/smart-exporter.service /etc/systemd/system/smart-exporter.service
Server/8-Idle-Watcher/beefy_idle_watcher.py /usr/local/sbin/beefy_idle_watcher.py
Server/9-Night-Schedule/beefy-maintenance-window /usr/local/sbin/beefy-maintenance-window
Server/10-Backup/beefy-backup.sh /usr/local/sbin/beefy-backup
Server/10-Backup/beefy-backup.service /etc/systemd/system/beefy-backup.service
Docker/_global/scripts/oob-alert.sh /usr/local/lib/beefy-oob/scripts/oob-alert.sh
Docker/Floppy/scripts/abs-push.py /usr/local/lib/floppy-abs-push/abs-push.py
Docker/Floppy/scripts/abs-push-apply.py /usr/local/lib/floppy-abs-push/abs-push-apply.py
EOF
  for f in /etc/systemd/system/*.service; do grep -q "^User=" "$f" && continue; grep -qE "^Exec[A-Za-z]*=[-+!@]*/[^ ]*/home/|^Exec[A-Za-z]*=.* -I /home/|^Exec[A-Za-z]*=.*/usr/bin/(python3|bash|sh) [^ ]*/home/" "$f" && m+=" home:${f##*/}"; done
  echo "${m:-ok}"
}
check host.root-copies "^ok$" "$(root_copies)"
check host.mem-limits '^none$' "$(docker ps --format '{{.Names}}' | while read -r c; do [ "$(docker inspect -f '{{.HostConfig.Memory}}' "$c")" = 0 ] && echo "$c"; done | xargs | sed 's/^$/none/')"
check host.os-updates '^installed false$' "$(cmp -s "$DIR/../../../Server/11-OS-Updates/52unattended-upgrades-local" /etc/apt/apt.conf.d/52unattended-upgrades-local && echo installed || echo differs) $(apt-config dump Unattended-Upgrade::Automatic-Reboot | sed -n 's/.*"\(.*\)";/\1/p')"
check host.sysctl-hardening '^installed ok$' "$(f="$DIR/../../../Server/12-Host-Hardening/90-hardening.conf"; cmp -s "$f" /etc/sysctl.d/90-hardening.conf && printf 'installed ' || printf 'differs '; grep -E '^[a-z]' "$f" | while IFS=' =' read -r k v; do [ "$(sysctl -n "$k" 2>&1)" = "$v" ] || echo "$k"; done | xargs | sed 's/^$/ok/')"
check host.headless '^multi-user.target disabled disabled$' "$(systemctl get-default) $(systemctl is-enabled gdm.service 2>&1) $(test -e /etc/cloud/cloud-init.disabled && echo disabled || echo enabled)"
