#!/usr/bin/env bash
# Wires the completion scan (README "Completion scan") into SABnzbd and qBittorrent through their APIs. Idempotent.
#   ~/Projects/Docker/Movie-Downloads/scripts/clamav-hooks.sh
set -euo pipefail

SK=$(grep -oP '^api_key = \K.*' /srv/appdata/sabnzbd/sabnzbd.ini)
sab() { curl -sf "http://127.0.0.1:8081/api?apikey=$SK&output=json&$1" >/dev/null; }

# SABnzbd: script folder, the hook on the default category (movies, tv, ... inherit it), a non-zero exit fails the job.
sab "mode=set_config&section=misc&keyword=script_dir&value=/clamav-hook"
sab "mode=set_config&section=misc&keyword=script_can_fail&value=1"
sab "mode=set_config&section=categories&name=*&script=clamav-hook.py"
grep -E '^(script_dir|script_can_fail) ' /srv/appdata/sabnzbd/sabnzbd.ini
grep -A4 -F '[[*]]' /srv/appdata/sabnzbd/sabnzbd.ini | grep 'script ='

# qBittorrent: run the hook when a torrent finishes. Called from gluetun's namespace, where localhost needs no login.
qbit() { sudo docker exec movie-gluetun wget -qO- "$@"; }
PREFS=$(python3 -c 'import json; print(json.dumps({"autorun_enabled": True, "autorun_program": "/clamav-hook/clamav-hook.py --qbit \"%I\" \"%F\""}))')
qbit --post-data "json=$PREFS" http://127.0.0.1:8080/api/v2/app/setPreferences
qbit http://127.0.0.1:8080/api/v2/app/preferences | python3 -c 'import json,sys; p=json.load(sys.stdin); print("qbit autorun:", p["autorun_enabled"], p["autorun_program"])'
