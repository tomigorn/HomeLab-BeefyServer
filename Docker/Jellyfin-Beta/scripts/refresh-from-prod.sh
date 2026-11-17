#!/usr/bin/env bash
# Re-copies prod Jellyfin into the beta (prod is only read: SQLite .backup, rsync without --delete on prod) and re-applies everything that makes it the beta.
# Usage: refresh-from-prod.sh [--keep-plugins] [-h]
#   --keep-plugins  keep the beta's own plugins and their configs (try a plugin across refreshes)
# Reset by a refresh: beta watch history, beta user settings, beta-only collections (rows.py rebuilds the rows), beta Moonfin layouts (rows.py), beta-only plugins (unless --keep-plugins).
# Kept: secrets/ (API key re-created if dead, beta-admin password, oidc.json), band originals (re-taken from prod's images).
set -euo pipefail
cd "$(dirname "$0")/.."
B=/srv/appdata/jellyfin-beta
P=/srv/appdata/jellyfin
keep=0
case "${1:-}" in
  "") ;;
  --keep-plugins) keep=1 ;;
  -h|--help) sed -n 2,6p "$0"; exit 0 ;;
  *) echo "usage: $0 [--keep-plugins]" >&2; exit 2 ;;
esac
h=$(date +%H%M)
if [ "$h" -ge 0355 ] && [ "$h" -le 0445 ]; then echo "night window 03:55-04:45: run it later" >&2; exit 1; fi
prod_img=$(sudo docker inspect -f '{{.Config.Image}}' jellyfin)
beta_img=$(sed -n 's/^ *image: *\([^ ]*\).*/\1/p' docker-compose.yaml | head -1)
[ "$prod_img" = "$beta_img" ] || echo "note: prod runs $prod_img, the beta $beta_img: update the image line in docker-compose.yaml to follow prod"
excl=(--exclude=/cache/ --exclude=/log/ --exclude=/data/data/trickplay/ --exclude=/data/transcodes/ --exclude="/data/data/jellyfin.db*" --exclude=/data/data/SQLiteBackups/ --exclude="*.bak*" --exclude=/.cache/ --exclude=/band/)
[ "$keep" = 1 ] && excl+=(--exclude=/data/plugins/)
sudo docker compose stop jellyfin-beta
sudo ionice -c3 nice -n19 rsync -a --delete "${excl[@]}" "$P/" "$B/"
sudo ionice -c3 nice -n19 sqlite3 "file:$P/data/data/jellyfin.db?mode=ro" ".backup $B/data/data/jellyfin.db.new"
sudo rm -f "$B/data/data/jellyfin.db-wal" "$B/data/data/jellyfin.db-shm"
sudo mv "$B/data/data/jellyfin.db.new" "$B/data/data/jellyfin.db"
sudo python3 scripts/sanitize.py
sudo chown -R 65132:buntu "$B"
sudo docker compose up -d jellyfin-beta
for _ in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8196/health && break; sleep 3; done
python3 scripts/beta_admin.py
python3 scripts/beta_tasks.py
python3 scripts/web_home.py
python3 scripts/rows.py
sudo python3 scripts/band_render.py
echo "done. check: ../_global/scripts/post-update-check.sh Jellyfin-Beta"
