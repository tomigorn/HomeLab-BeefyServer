#!/usr/bin/env bash
# Version of the injected /_indexer-bar/ files (README "Indexer bar", Caching): sha256 over indexer-bar/www, written as the nginx include that puts ?v=<version> into every injected URL; seerr-proxy reloaded when it changed.
#   bar-assets.sh            write the include, reload seerr-proxy on a change (systemd/seerr-bar-assets.path runs it on every change in www/)
#   bar-assets.sh version    print the current version
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WWW=${BA_WWW:-$HERE/../indexer-bar/www}
OUT=${BA_OUT:-/srv/appdata/indexer-bar/asset-version.conf}
DOCKER=${DOCKER:-docker}

usage() { sed -n '2,4s/^# \{0,1\}//p' "$0"; }
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  ""|version) ;;
  *) usage >&2; echo "next: bar-assets.sh --help" >&2; exit 64 ;;
esac

v=$(cd "$WWW" && printf '%s\n' * | LC_ALL=C sort | while read -r f; do [ -f "$f" ] || continue; printf '%s\n' "$f"; cat "$f"; done | sha256sum | cut -c1-12)
if [ "${1:-}" = version ]; then echo "$v"; exit 0; fi

line="set \$hg_v \"$v\";"
if [ "$(cat "$OUT" 2>/dev/null)" = "$line" ]; then
  echo "bar-assets: version $v unchanged"
  exit 0
fi
printf '%s\n' "$line" > "$OUT.tmp" && chmod 0644 "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
if [ "$($DOCKER inspect -f '{{.State.Running}}' seerr-proxy 2>/dev/null)" = true ]; then
  $DOCKER exec seerr-proxy nginx -s reload
  echo "bar-assets: version $v, seerr-proxy reloaded"
else
  echo "bar-assets: version $v written, seerr-proxy not running (next: sudo docker compose up -d seerr-proxy)"
fi
