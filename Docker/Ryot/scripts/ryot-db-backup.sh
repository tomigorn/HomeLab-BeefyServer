#!/usr/bin/env bash
# Nightly Ryot DB dump to ~/backups/ryot/ryot-YYYY-MM-DD.dump (pg_dump custom format), keeps 14 days.
set -euo pipefail
umask 077
dir="$HOME/backups/ryot"
mkdir -p "$dir"
out="$dir/ryot-$(date +%F).dump"
sudo docker exec -u postgres ryot-db pg_dump -U ryot -d ryot -Fc --no-acl --no-owner > "$out.tmp"
mv "$out.tmp" "$out"
find "$dir" -name 'ryot-*.dump' -mtime +14 -delete
size=$(stat -c %s "$out")
if (( size < 1024 )); then echo "dump too small: $size bytes" >&2; exit 1; fi
echo "ok $out $size bytes"
