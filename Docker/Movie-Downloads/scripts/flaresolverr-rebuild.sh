#!/usr/bin/env bash
# Weekly rebuild of the local FlareSolverr image (README "FlareSolverr"): newest upstream release + all Debian upgrades; recreate only when the packages or the app changed, test through the VPN, roll back on failure, Telegram alert.
#   flaresolverr-rebuild.sh [--force]   buntu with the docker group (like auto-update); timer flaresolverr-rebuild, Thu 04:27
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
MD=$(cd "$HERE/.." && pwd)
DOCKER=${DOCKER:-docker}
UPSTREAM=${FS_UPSTREAM:-ghcr.io/flaresolverr/flaresolverr}
IMAGE=flaresolverr-local:current
CANDIDATE=flaresolverr-local:candidate
ROLLBACK=flaresolverr-local:rollback
DRIVER_DIR=${FS_DRIVER_DIR:-/srv/appdata/flaresolverr}
STATE=${FS_STATE:-/var/lib/flaresolverr-rebuild}
ALERT=${FS_ALERT:-$MD/../_global/scripts/oob-alert.sh}
FORCE=0; [ "${1:-}" = --force ] && FORCE=1

log() { echo "flaresolverr-rebuild: $*"; }
alert() { log "$1"; "$ALERT" "beefy: FlareSolverr $1" >/dev/null 2>&1 || log "alert not sent"; }

fingerprint() { # packages, app version and code of an image
  $DOCKER run --rm --entrypoint sh "$1" -c 'dpkg-query -W -f "\${Package}=\${Version}\n"; cat /app/package.json 2>/dev/null; find /app -name "*.py" -exec sha256sum {} + | sort' | sha256sum | cut -c1-16
}

# undetected_chromedriver patches /app/chromedriver in place, so compose mounts a copy owned by the container user; root inside a throwaway container writes it
driver() { # driver new|rollback
  local cmd='cp -p /d/chromedriver /d/chromedriver.rollback && install -o 65119 -g 65119 -m 0755 /app/chromedriver /d/chromedriver'
  [ "$1" = rollback ] && cmd='install -o 65119 -g 65119 -m 0755 /d/chromedriver.rollback /d/chromedriver'
  $DOCKER run --rm --user 0 --network none --entrypoint sh -v "$DRIVER_DIR:/d" "$IMAGE" -c "$cmd"
}

apply() { (cd "$MD" && $DOCKER compose up -d --no-deps flaresolverr >/dev/null 2>&1); }

verify() {
  if [ -n "${FS_VERIFY:-}" ]; then eval "$FS_VERIFY"; return; fi
  local i s k port proxy id
  for i in $(seq 1 36); do
    s=$($DOCKER inspect -f '{{.State.Health.Status}}' movie-flaresolverr 2>/dev/null || true)
    [ "$s" = healthy ] && break
    sleep 5
  done
  [ "$s" = healthy ] || { log "not healthy"; return 1; }
  $DOCKER exec movie-flaresolverr curl -sf -m 90 -X POST http://127.0.0.1:8191/v1 -H 'Content-Type: application/json' -d '{"cmd":"request.get","url":"https://example.com/","maxTimeout":60000}' | grep -q '"status": "ok"' || { log "request failed"; return 1; }
  k=$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' /srv/appdata/prowlarr/config.xml)
  port=$(sed -n 's/^PROWLARR_PORT=//p' "$MD/.env"); port=${port:-9696}
  pr() { curl -sf -m 150 -H "X-Api-Key: $k" -H 'Content-Type: application/json' "$@"; }
  proxy=$(pr "http://127.0.0.1:$port/api/v1/indexerproxy" | jq -c 'first(.[] | select(.implementation == "FlareSolverr"))')
  pr -X POST -d "$proxy" "http://127.0.0.1:$port/api/v1/indexerproxy/test" >/dev/null || { log "Prowlarr proxy test failed"; return 1; }
  for id in $(pr "http://127.0.0.1:$port/api/v1/indexer" | jq -r '.[] | select(.enable and (.name | test("^(1337x|EZTV)$"))) | .id'); do
    pr "http://127.0.0.1:$port/api/v1/indexer/$id" | pr -X POST -d @- "http://127.0.0.1:$port/api/v1/indexer/test" >/dev/null || { log "Prowlarr indexer $id test failed"; return 1; }
  done
  SERVICES=flaresolverr bash "$MD/../_global/scripts/post-update-check.sh" Movie-Downloads >/dev/null || { log "post-update check failed"; return 1; }
}

mkdir -p "$STATE"
digest=$($DOCKER buildx imagetools inspect "$UPSTREAM:latest" --format '{{json .Manifest.Digest}}' | tr -d '"')
[[ $digest == sha256:* ]] || { alert "rebuild: no upstream digest"; exit 1; }
if ! $DOCKER build --network host --pull --no-cache --build-arg "FLARESOLVERR_BASE=$UPSTREAM@$digest" -t "$CANDIDATE" "$MD/flaresolverr" > "$STATE/build.log" 2>&1; then
  alert "build failed (see $STATE/build.log), nothing changed"
  exit 1
fi
if [ "$FORCE" = 0 ] && [ "$(fingerprint "$CANDIDATE")" = "$(fingerprint "$IMAGE" 2>/dev/null || true)" ]; then
  log "unchanged ($UPSTREAM@${digest:7:12})"
  $DOCKER rmi "$CANDIDATE" >/dev/null 2>&1 || true
  exit 0
fi

prev=$($DOCKER image inspect -f '{{.Id}}' "$ROLLBACK" 2>/dev/null || true)
$DOCKER tag "$IMAGE" "$ROLLBACK"
$DOCKER tag "$CANDIDATE" "$IMAGE"
driver new
apply
if verify; then
  $DOCKER rmi "$CANDIDATE" >/dev/null 2>&1 || true
  # one rollback image is enough: the one before it is untagged now (fails harmlessly while still in use)
  [ -z "$prev" ] || $DOCKER rmi "$prev" >/dev/null 2>&1 || true
  log "updated to $UPSTREAM@${digest:7:12} + Debian upgrades"
  exit 0
fi
$DOCKER tag "$ROLLBACK" "$IMAGE"
driver rollback
apply
$DOCKER rmi "$CANDIDATE" >/dev/null 2>&1 || true
if verify; then alert "rebuild failed its tests, rolled back to the previous image"; else alert "rebuild failed, rolled back, but the old image does not verify either"; fi
exit 1
