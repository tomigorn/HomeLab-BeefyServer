#!/usr/bin/env bash
# Wires the completion checks (README "Download integrity", "Completion scan") into SABnzbd and qBittorrent through their APIs. Idempotent.
#   ~/Projects/Docker/Movie-Downloads/scripts/clamav-hooks.sh
set -euo pipefail

SK=$(sudo grep -oP '^api_key = \K.*' /srv/appdata/sabnzbd/sabnzbd.ini)
sab() { curl -sf -G --data-urlencode apikey@<(printf %s "$SK") "http://127.0.0.1:8081/api?output=json&$1" >/dev/null; }

# SABnzbd: script folder, the hook on the default category (movies, tv, ... inherit it), a non-zero exit fails the job.
# download-integrity.py checks the data on disk, then runs clamav-hook.py. It needs the par2 index kept, and no direct write.
sab "mode=set_config&section=misc&keyword=script_dir&value=/clamav-hook"
sab "mode=set_config&section=misc&keyword=script_can_fail&value=1"
sab "mode=set_config&section=misc&keyword=direct_write&value=0"
sab "mode=set_config&section=misc&keyword=enable_par_cleanup&value=0"
sab "mode=set_config&section=categories&name=*&script=download-integrity.py"
sudo grep -E '^(script_dir|script_can_fail|direct_write|enable_par_cleanup) ' /srv/appdata/sabnzbd/sabnzbd.ini
sudo grep -A4 -F '[[*]]' /srv/appdata/sabnzbd/sabnzbd.ini | grep 'script ='

# qBittorrent: recheck the pieces on disk when a torrent finishes, then run the hook. Logs in with the WebUI password.
HERE=$(cd "$(dirname "$0")" && pwd)
QBIT_PASS_FILE=$HERE/../secrets/qbit-webui.password
. "$HERE/qbit-api.sh"
trap qbit_logout EXIT
qb() { qbit_api http://127.0.0.1:8080 "$@"; }
PREFS=$(python3 -c 'import json; print(json.dumps({"recheck_completed_torrents": True, "autorun_enabled": True, "autorun_program": "/clamav-hook/download-integrity.py --qbit \"%I\" \"%F\""}))')
qb /app/setPreferences --data-urlencode "json=$PREFS" >/dev/null
# The background scan worker (clamav-queue, 172.28.10.204) stops and tags a torrent it finds infected: add it to the no-password whitelist.
WL=$(qb /app/preferences | python3 -c 'import json,sys; w=json.load(sys.stdin)["bypass_auth_subnet_whitelist"].split("\n"); n="172.28.10.204/32"; print(json.dumps({"bypass_auth_subnet_whitelist": "\n".join(w if n in w else w + [n])}))')
qb /app/setPreferences --data-urlencode "json=$WL" >/dev/null
qb /app/preferences | python3 -c 'import json,sys; p=json.load(sys.stdin); print("qbit recheck on completion:", p["recheck_completed_torrents"]); print("qbit autorun:", p["autorun_enabled"], p["autorun_program"]); print("qbit whitelist:", p["bypass_auth_subnet_whitelist"].replace("\n", ", "))'
