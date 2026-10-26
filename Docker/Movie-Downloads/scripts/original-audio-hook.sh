#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Import, On Upgrade): queues the imported file for scripts/original-audio.py --queue, so its original-language audio track becomes the default without a boot-time or daily sweep (README "Languages").
# Writes "<movie/series id><TAB><container path>" per file to /config/original-audio.queue (host: /srv/appdata/<app>/original-audio.queue, picked up by systemd/original-audio-import.path). Runs inside the app container; registered by scripts/language-policy.sh.
set -euo pipefail

QUEUE=${OAH_QUEUE:-/config/original-audio.queue}
if [ -n "${radarr_eventtype:-}" ]; then
  event=$radarr_eventtype; id=${radarr_movie_id:-}; paths=${radarr_moviefile_path:-}
elif [ -n "${sonarr_eventtype:-}" ]; then
  event=$sonarr_eventtype; id=${sonarr_series_id:-}; paths=${sonarr_episodefile_paths:-${sonarr_episodefile_path:-}}
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
[ "$event" = Download ] || exit 0
[ -n "$id" ] && [ -n "$paths" ] || exit 0

IFS='|' read -ra list <<<"$paths"
for p in "${list[@]}"; do
  printf '%s\t%s\n' "$id" "$p" >> "$QUEUE"
done
