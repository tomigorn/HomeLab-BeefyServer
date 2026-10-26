#!/usr/bin/env bash
# audiobookshelf-link.sh link <floppy-user>     asks for the ABS API key (hidden)
# audiobookshelf-link.sh unlink <floppy-user>
set -euo pipefail
action=${1:-}; user=${2:-}
[[ $action =~ ^(link|unlink)$ && -n $user ]] || { sed -n '2,3p' "$0"; exit 2; }
cd "$(dirname "$0")"
key=""
if [[ $action == link ]]; then
  read -r -s -p "ABS API key for $user: " key; echo
fi
printf '%s' "$key" | sudo docker exec -i -u abc -w /floppy -e FLOPPY_USER="$user" -e ABS_ACTION="$action" -e ABS_ALLOW_ADMIN="${ABS_ALLOW_ADMIN:-0}" floppy \
  python manage.py shell -c "$(cat audiobookshelf-link.py)" 2>&1 | grep -E '^RESULT|Error|error' | sed 's/^RESULT //'
