#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Import, On Upgrade): queues the imported file for scripts/original-audio.py --queue, so its original-language audio track becomes the default without a boot-time or daily sweep (README "Languages"), and for scripts/language-block.py --queue (README "Language block").
# Writes "<movie/series id><TAB><container path>" per file to /config/original-audio.queue and "<id><TAB><path><TAB><file id><TAB><download id><TAB><isupgrade><TAB><deleted paths><TAB><recycle bin paths>" to /config/language-block.queue (host: /srv/appdata/<app>/, picked up by systemd/original-audio-import.path and language-block-import.path). Runs inside the app container; registered by scripts/language-policy.sh.
set -euo pipefail

QUEUE=${OAH_QUEUE:-/config/original-audio.queue}
LB_QUEUE=${OAH_LB_QUEUE:-/config/language-block.queue}
if [ -n "${radarr_eventtype:-}" ]; then
  event=$radarr_eventtype; id=${radarr_movie_id:-}; paths=${radarr_moviefile_path:-}; ids=${radarr_moviefile_id:-}
  dl=${radarr_download_id:-}; upgrade=${radarr_isupgrade:-}; deleted=${radarr_deletedpaths:-}; recycled=${radarr_deletedrecyclebinpaths:-}
elif [ -n "${sonarr_eventtype:-}" ]; then
  event=$sonarr_eventtype; id=${sonarr_series_id:-}; paths=${sonarr_episodefile_paths:-${sonarr_episodefile_path:-}}; ids=${sonarr_episodefile_ids:-${sonarr_episodefile_id:-}}
  dl=${sonarr_download_id:-}; upgrade=${sonarr_isupgrade:-}; deleted=${sonarr_deletedpaths:-}; recycled=${sonarr_deletedrecyclebinpaths:-}
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
[ "$event" = Download ] || exit 0
[ -n "$id" ] && [ -n "$paths" ] || exit 0

IFS='|' read -ra list <<<"$paths"
IFS='|' read -ra fids <<<"$ids"
for i in "${!list[@]}"; do
  p=${list[$i]}
  printf '%s\t%s\n' "$id" "$p" >> "$QUEUE"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$id" "$p" "${fids[$i]:-}" "$dl" "$upgrade" "$deleted" "$recycled" >> "$LB_QUEUE"
done
