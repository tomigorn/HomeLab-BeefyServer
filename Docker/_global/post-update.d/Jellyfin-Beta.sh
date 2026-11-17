# Jellyfin-Beta customizations (Docker/CUSTOMIZATIONS.md "Jellyfin-Beta"). Sourced by post-update-check.sh.
JFB_DIR="$DIR/../../Jellyfin-Beta"
jfb_conf() { docker exec jellyfin-beta sh -c "$1" 2>&1; }
if want jellyfin-beta; then
  check jfb.image-follows-prod '^same$' "$([ "$(docker inspect -f '{{.Config.Image}}' jellyfin-beta 2>&1)" = "$(docker inspect -f '{{.Config.Image}}' jellyfin 2>&1)" ] && echo same || echo differs)"
  check jfb.limits '^4294967296 ro 172\.28\.20\.0/24$' "$(docker inspect -f '{{.HostConfig.Memory}}' jellyfin-beta) $(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/media"}}{{if .RW}}rw{{else}}ro{{end}}{{end}}{{end}}' jellyfin-beta) $(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' jellyfin-beta 2>&1)"
  check jfb.no-webhooks '^0 hooks$' "$(jfb_conf 'grep -c "<HookConfig>" /config/data/plugins/configurations/Jellyfin.Webhooks.xml || true') hooks"
  check jfb.no-library-writes '^0$' "$(jfb_conf 'cat /config/data/root/default/*/options.xml' | grep -cE '<(EnableRealtimeMonitor|EnableTrickplayImageExtraction|SaveLocalMetadata|EnableChapterImageExtraction)>true<')"
  check jfb.no-seerr '^false( false)*$' "$(jfb_conf 'sed -n "s/.*<SeerrEnabled>\(.*\)<\/SeerrEnabled>.*/\1/p" /config/data/plugins/configurations/Moonfin.Server.xml' | xargs)"
  check jfb.no-timed-tasks '^no timed triggers$' "$(cd "$JFB_DIR/scripts" && python3 beta_tasks.py --check 2>&1 | tail -1)"
  check jfb.own-server-id '^own$' "$(a=$(curl -s -m 5 http://127.0.0.1:8196/System/Info/Public | jq -r .Id); b=$(curl -s -m 5 http://127.0.0.1:8096/System/Info/Public | jq -r .Id); [ -n "$a" ] && [ "$a" != "$b" ] && echo own || echo "same or down")"
  check jfb.admin-and-key '^api key ok, beta-admin ok$' "$(cd "$JFB_DIR/scripts" && python3 beta_admin.py --check 2>&1 | tail -1)"
  check jfb.web-home '^unchanged$' "$(cd "$JFB_DIR/scripts" && python3 web_home.py --check 2>&1 | tail -1)"
  check jfb.rows-timer '^enabled$' "$(systemctl --user is-enabled jellyfin-beta-rows.timer 2>&1)"
  check jfb.band '^would change 0 placeholder images' "$(cd "$JFB_DIR/scripts" && python3 band_render.py --check 2>&1 | tail -1)"
fi
