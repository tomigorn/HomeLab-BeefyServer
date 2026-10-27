#!/usr/bin/env bash
# Subtitle check (README "Subtitle check"): state dirs, Bazarr post-processing hook + the "common" subtitle mod, the nightly timer. Idempotent.
#   ~/Projects/Docker/Movie-Downloads/scripts/subtitle-check-setup.sh
# Needs the ./scripts:/scripts:ro mount in the bazarr service (docker-compose.yaml).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
STATE=/srv/appdata/bazarr/subtitle-check
B=${B:-http://127.0.0.1:6767/api}
BK=$(grep -oP '(?<=apikey: ).*' /srv/appdata/bazarr/config/config.yaml | head -1)
bz() { curl -sf -H "X-API-KEY: $BK" "$@"; }
py() { python3 -c "$@"; }

echo "== state dirs (owner = Bazarr's uid, group buntu for the host scan and the queue watchdog)"
sudo install -d -o 65104 -g buntu -m 2775 "$STATE" "$STATE/events" "$STATE/backup"

echo "== hook present in the container"
sudo docker exec movie-bazarr test -x /scripts/subtitle-check.py

echo "== Bazarr: post-processing + subtitle mod common (not remove_HI, not OCR_fixes)"
CMD='/scripts/subtitle-check.py "{{subtitles}}" "{{provider}}" "{{subtitle_id}}"'
ARGS=(--data-urlencode "settings-general-use_postprocessing=true"
      --data-urlencode "settings-general-postprocessing_cmd=$CMD"
      --data-urlencode "settings-general-use_postprocessing_threshold=false"
      --data-urlencode "settings-general-use_postprocessing_threshold_movie=false")
# Bazarr appends a mod on every "true", so post it only while it is missing.
if ! bz "$B/system/settings" | py 'import json,sys; sys.exit(0 if "common" in json.load(sys.stdin)["general"]["subzero_mods"] else 1)'; then
  ARGS+=(--data-urlencode "subzero-common=true")
fi
bz "$B/system/settings" -X POST "${ARGS[@]}" >/dev/null
bz "$B/system/settings" | CMD="$CMD" py 'import json,sys,os
g=json.load(sys.stdin)["general"]
print("   post-processing:", g["use_postprocessing"], "| cmd:", g["postprocessing_cmd"], "| thresholds:", g["use_postprocessing_threshold"], g["use_postprocessing_threshold_movie"], "| mods:", g["subzero_mods"])
mods = g["subzero_mods"]
ok = g["use_postprocessing"] is True and g["postprocessing_cmd"] == os.environ["CMD"] and "common" in mods and "remove_HI" not in mods and "OCR_fixes" not in mods
sys.exit(0 if ok else "   settings not as expected")'

echo "== nightly timer"
sudo install -m 0644 "$HERE/../systemd/subtitle-check.service" "$HERE/../systemd/subtitle-check.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now subtitle-check.timer
systemctl list-timers subtitle-check.timer --no-pager | sed -n 2p | sed 's/^/   /'
