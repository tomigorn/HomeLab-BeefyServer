#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Import, On Upgrade, On Rename): appends the file paths to /config/seerr-sync.queue (host: /srv/appdata/<app>/seerr-sync.queue). systemd/seerr-sync.path starts scripts/seerr-sync.sh, which debounces and refreshes Seerr's availability (README "Seerr availability after an import"). No network from here.
# Runs inside the app container; registered by scripts/language-policy.sh.
set -euo pipefail

QUEUE=${SSH_QUEUE:-/config/seerr-sync.queue}
if [ -n "${radarr_eventtype:-}" ]; then
  event=$radarr_eventtype; paths=${radarr_moviefile_path:-${radarr_moviefile_paths:-}}
elif [ -n "${sonarr_eventtype:-}" ]; then
  event=$sonarr_eventtype; paths=${sonarr_episodefile_paths:-${sonarr_episodefile_path:-}}
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
case $event in Download|Rename) ;; *) exit 0;; esac
[ -n "$paths" ] || exit 0

IFS='|' read -ra list <<<"$paths"
for p in "${list[@]}"; do
  printf '%s\n' "$p" >> "$QUEUE"
done
