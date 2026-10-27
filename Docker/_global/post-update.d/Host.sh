# beefy host customizations around the containers (Docker/CUSTOMIZATIONS.md "Host"): only in `post-update-check.sh all`, no compose project.
check host.docker-user-lan '^blocked$' "$(docker exec movie-sonarr nc -zw2 192.168.1.2 22 >/dev/null 2>&1 && echo open || echo blocked)"
check host.beefy-input '^blocked$' "$(gw=$(docker network inspect -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' movie-downloads); docker exec movie-sonarr nc -zw2 "$gw" 22 >/dev/null 2>&1 && echo open || echo blocked)"
check host.sso-egress '^200$' "$(docker exec jellyfin curl -s -m 8 -o /dev/null -w '%{http_code}' https://sso.holy-grail.ch/application/o/jellyfin/.well-known/openid-configuration 2>&1)"
check host.idle-watcher '^active fresh probe_error=0$' "$(echo "$(systemctl is-active beefy-idle-watcher.service) $(find /run/beefy-idle.prom -mmin -5 2>/dev/null | grep -q . && echo fresh || echo stale) probe_error=$(sed -n 's/^beefy_idle_busy{reason="probe_error"} //p' /run/beefy-idle.prom)")"
check host.timers '^enabled enabled enabled$' "$(systemctl is-enabled beefy-maintenance-window.timer auto-update.timer image-cleanup.timer | xargs)"
