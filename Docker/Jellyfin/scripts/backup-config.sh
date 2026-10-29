#!/usr/bin/env bash
# Usage: backup-config.sh <label>  — prints the backup dir; exit 0 only when verified
set -euo pipefail
umask 077

label="${1:?usage: backup-config.sh <label>}"
[[ "$label" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "bad label" >&2; exit 2; }

src=/srv/appdata/jellyfin
db=$src/data/data/jellyfin.db
dir="$HOME/backups/jellyfin/$(date +%Y%m%d-%H%M%S)-$label"

mkdir -p "$HOME/backups/jellyfin"
chmod 700 "$HOME/backups/jellyfin"
mkdir "$dir"
chmod 700 "$dir"

sudo sh -c "cd $src && tar -cf - *.xml data/plugins/configurations data/root data/data/collections" > "$dir/config-xml.tar"
sudo sqlite3 "$db" ".backup '$dir/jellyfin.db'"
sudo cat /srv/appdata/seerr/settings.json > "$dir/seerr-settings.json"

sudo chown -R "$(id -u):$(id -g)" "$dir"
chmod 700 "$dir"
chmod 600 "$dir"/*

[[ "$(sqlite3 "$dir/jellyfin.db" 'PRAGMA integrity_check')" == ok ]] || { echo "integrity_check failed" >&2; exit 1; }
live=$(sudo sqlite3 "file:$db?mode=ro" 'select count(*) from BaseItems')
copy=$(sqlite3 "$dir/jellyfin.db" 'select count(*) from BaseItems')
[[ "$live" == "$copy" ]] || { echo "BaseItems count differs: live $live copy $copy" >&2; exit 1; }
[[ "$(tar -tf "$dir/config-xml.tar" | grep -c .)" -gt 0 ]] || { echo "empty tar" >&2; exit 1; }
for f in "$dir"/*; do
  [[ "$(stat -c %a "$f")" == 600 ]] || { echo "bad mode on $f" >&2; exit 1; }
done

echo "backup ok $dir"
