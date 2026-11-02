#!/usr/bin/env bash
# Rebuild of our own Seerr image (README "Own image"): newest stable release + OIDC PR #2715 + image/patches + image/dependency-overrides.json; upstream typecheck and tests, backup, deploy, roll back on failure, Telegram alert.
#   seerr-rebuild.sh [--force] [--no-deploy] [--deploy-release]   buntu with the docker group; timer seerr-rebuild, Tue 04:27
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SEERR=${SR_SEERR_DIR:-$(cd "$HERE/.." && pwd)}
IMG_DIR=${SR_IMAGE_DIR:-$SEERR/image}
DOCKER=${DOCKER:-docker}
GIT_URL=${SR_GIT:-https://github.com/seerr-team/seerr.git}
API=${SR_API:-https://api.github.com/repos/seerr-team/seerr}
STATE=${SR_STATE:-/var/lib/seerr-rebuild}
WORK=${SR_WORK:-$STATE/work}
BACKUPS=${SR_BACKUPS:-$HOME/backups/seerr}
APPDATA=${SR_APPDATA:-/srv/appdata/seerr}
ALERT=${SR_ALERT:-$SEERR/../_global/scripts/oob-alert.sh}
PRN=2715
REPO=seerr-local
FORCE=0; DEPLOY=1; RELEASE_OK=0
DEPLOY_CMD="sudo systemd-run --uid=buntu --gid=buntu -p SupplementaryGroups=docker -p StateDirectory=seerr-rebuild --wait --pipe --quiet -E HOME=/home/buntu /bin/bash /home/buntu/Projects/Docker/Seerr/scripts/seerr-rebuild.sh --deploy-release"
for a in "$@"; do
  case $a in
    --force) FORCE=1 ;;
    --no-deploy) DEPLOY=0 ;;
    --deploy-release) RELEASE_OK=1 ;;
    *) echo "usage: seerr-rebuild.sh [--force] [--no-deploy] [--deploy-release]" >&2; exit 64 ;;
  esac
done

log() { echo "seerr-rebuild: $*"; }
alert() { log "$1"; "$ALERT" "beefy: Seerr $1" >/dev/null 2>&1 || log "alert not sent"; }
api() { curl -sf -m 30 "$API/$1"; }
gs() { git -C "$src" "$@"; }
compose() { (cd "$SEERR" && $DOCKER compose "$@" >/dev/null 2>&1); }
once() { # once <key>: true the first time only
  [ -e "$STATE/told-$1" ] && return 1
  touch "$STATE/told-$1"
}

mkdir -p "$STATE" "$WORK"
exec 9>"$STATE/lock"
flock -n 9 || { log "already running"; exit 0; }

tag=$(api releases/latest | jq -r .tag_name)
pr=$(api "pulls/$PRN")
[[ $tag == v* ]] && [ -n "$pr" ] || { alert "rebuild: GitHub API unreadable, nothing changed"; exit 1; }

src=$WORK/seerr
[ -d "$src/.git" ] || git clone -q --filter=blob:none "$GIT_URL" "$src" 2>/dev/null
gs fetch -q --force origin "refs/tags/$tag:refs/tags/$tag" "+refs/pull/$PRN/head:refs/remotes/pr/$PRN" "+refs/heads/develop:refs/remotes/origin/develop"
gs checkout -q -f --detach "$tag"
gs clean -qfdx

sha=$(jq -r .head.sha <<<"$pr")
released=0
if [ "$(jq -r .merged <<<"$pr")" = true ]; then
  m=$(jq -r .merge_commit_sha <<<"$pr")
  gs cat-file -e "$m^{commit}" 2>/dev/null || gs fetch -q origin "$m" 2>/dev/null || true
  gs merge-base --is-ancestor "$m" "$tag" 2>/dev/null && released=1
elif [ "$(jq -r .state <<<"$pr")" = closed ] && once "closed-$sha"; then
  alert "OIDC PR #$PRN was closed without a merge; still building its last head ${sha:0:7}"
fi

if [ "$released" = 1 ]; then
  version=$tag; sha=
  once "released-$tag" && alert "OIDC (PR #$PRN) is in release $tag: we can return to the official image (Seerr README \"Back to stable\"). Until then this builds plain $tag."
else
  # only a PR commit someone reviewed is built: an unmerged third-party branch must not reach production by itself
  reviewed=$(tr -d '[:space:]' < "$IMG_DIR/pr-reviewed" 2>/dev/null || true)
  [ -n "$reviewed" ] || { alert "image/pr-reviewed is missing: no reviewed OIDC PR commit, current image kept"; exit 2; }
  if [ "$sha" != "$reviewed" ]; then
    once "moved-$sha" && alert "OIDC PR #$PRN moved to ${sha:0:7} (reviewed: ${reviewed:0:7}). Please review the diff, then put the new sha into image/pr-reviewed (Seerr README \"Own image\"). Until then the reviewed commit is built."
    sha=$reviewed
    gs cat-file -e "$sha^{commit}" 2>/dev/null || gs fetch -q origin "$sha" 2>/dev/null \
      || { alert "reviewed OIDC PR commit ${sha:0:7} cannot be fetched any more, current image kept"; exit 2; }
  fi
  version="$tag-oidc${sha:0:7}"
  patch="$IMG_DIR/resolutions/$tag-${sha:0:12}.patch"
  if [ ! -f "$patch" ]; then
    patch=$STATE/pr.patch
    gs diff --binary "$(gs merge-base origin/develop "$sha")" "$sha" -- . ':(exclude)docs' > "$patch"
  fi
  if ! gs apply --3way --index "$patch" > "$STATE/apply.log" 2>&1; then
    files=$(gs diff --name-only --diff-filter=U | tr '\n' ' ')
    [ -n "$files" ] || files=$(sed -n 's/^error: patch failed: \([^:]*\):.*/\1/p' "$STATE/apply.log" | sort -u | tr '\n' ' ')
    alert "OIDC PR #$PRN (${sha:0:7}) does not apply onto $tag: conflicts in ${files:-?}- current image kept. Resolve by hand: Seerr README \"Own image\"."
    exit 2
  fi
fi

for p in "$IMG_DIR"/patches/*.patch; do
  [ -e "$p" ] || continue
  gs apply --index "$p" > "$STATE/apply.log" 2>&1 || { alert "own patch $(basename "$p") does not apply onto $version - current image kept"; exit 2; }
done

if [ -f "$IMG_DIR/dependency-overrides.json" ]; then
  jq --slurpfile o "$IMG_DIR/dependency-overrides.json" '.pnpm.overrides = ((.pnpm.overrides // {}) + ($o[0].overrides // {})) | .scripts += ($o[0].scripts // {})' "$src/package.json" > "$STATE/package.json"
  cat "$STATE/package.json" > "$src/package.json"
  node=$(sed -n '1s/^FROM \([^ ]*\).*/\1/p' "$src/Dockerfile")
  $DOCKER run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 -v "$src:/src" -w /src \
    "$node" corepack pnpm install --lockfile-only --ignore-scripts > "$STATE/lockfile.log" 2>&1 \
    || { alert "lockfile with the dependency overrides failed for $version (see $STATE/lockfile.log), current image kept"; exit 1; }
fi
gs add -A
tree=$(gs write-tree)

cur=$($DOCKER image inspect -f '{{index .Config.Labels "holy-grail.seerr.tree"}}' "$REPO:current" 2>/dev/null || true)
if [ "$FORCE" = 0 ] && [ "$cur" = "$tree" ]; then
  log "unchanged ($version)"
  exit 0
fi
# a new release may migrate the DB one way: built and tested, deployed only by the owner (--deploy-release)
running=$($DOCKER image inspect -f '{{index .Config.Labels "holy-grail.seerr.upstream"}}' "$REPO:current" 2>/dev/null || true)
hold=0
[ "$DEPLOY" = 1 ] && [ "$RELEASE_OK" = 0 ] && [ "$running" != "$tag" ] && hold=1
held() {
  once "held-$version" && alert "$version built and tested, not deployed: a new release (running ${running:-unknown}) may migrate the DB one way. Deploy: $DEPLOY_CMD"
  log "built $REPO:$version, new release held for the owner"
  exit 0
}

build_candidate() {
  if ! $DOCKER build --network host --target build --build-arg "COMMIT_TAG=$version" -t "$REPO:test" "$src" > "$STATE/build.log" 2>&1; then
    alert "build failed for $version (see $STATE/build.log), current image kept"
    exit 1
  fi
  if ! $DOCKER run --rm --network none "$REPO:test" sh -c 'pnpm typecheck && pnpm test' > "$STATE/test.log" 2>&1; then
    $DOCKER rmi "$REPO:test" >/dev/null 2>&1 || true
    alert "upstream tests failed for $version (see $STATE/test.log), current image kept"
    exit 1
  fi
  $DOCKER rmi "$REPO:test" >/dev/null 2>&1 || true
  if ! $DOCKER build --network host --build-arg "COMMIT_TAG=$version" --label "holy-grail.seerr.tree=$tree" --label "holy-grail.seerr.upstream=$tag" --label "holy-grail.seerr.pr=$sha" \
      -t "$REPO:$version" -t "$REPO:candidate" "$src" >> "$STATE/build.log" 2>&1; then
    alert "build failed for $version (see $STATE/build.log), current image kept"
    exit 1
  fi
}

cand=$($DOCKER image inspect -f '{{index .Config.Labels "holy-grail.seerr.tree"}}' "$REPO:candidate" 2>/dev/null || true)
if [ "$FORCE" = 0 ] && [ "$cand" = "$tree" ]; then
  log "candidate $version already built and tested"
else
  build_candidate
fi
[ "$hold" = 1 ] && held
if [ "$DEPLOY" = 0 ]; then
  log "built $REPO:$version (not deployed)"
  exit 0
fi

verify() {
  if [ -n "${SR_VERIFY:-}" ]; then eval "$SR_VERIFY"; return; fi
  local i s v want=$1
  for i in $(seq 1 60); do
    s=$($DOCKER inspect -f '{{.State.Health.Status}}' seerr 2>/dev/null || true)
    [ "$s" = healthy ] && break
    sleep 5
  done
  [ "$s" = healthy ] || { log "not healthy"; return 1; }
  v=$(curl -s -m 8 http://127.0.0.1:5055/api/v1/status | jq -r .commitTag)
  [ "$v" = "$want" ] || { log "status commitTag $v, expected $want"; return 1; }
  curl -s -m 15 http://127.0.0.1:5055/api/v1/auth/oidc/login/holy-grail | jq -r .redirectUrl | grep -q '^https://sso\.holy-grail\.ch/application/o/authorize/' || { log "OIDC login start failed"; return 1; }
  SERVICES=seerr bash "$SEERR/../_global/scripts/post-update-check.sh" Seerr > "$STATE/check.log" 2>&1 || { log "post-update check failed (see $STATE/check.log)"; return 1; }
}

dbcheck() { # dbcheck <backup>: the copy's database passes SQLite's integrity check
  if [ -n "${SR_DBCHECK:-}" ]; then eval "$SR_DBCHECK"; return; fi
  local t r
  t=$(mktemp -d "$STATE/dbcheck.XXXXXX")
  tar -xzf "$1" -C "$t" ./db/db.sqlite3 && r=$(sqlite3 "$t/db/db.sqlite3" 'PRAGMA integrity_check;')
  rm -rf "$t"
  [ "${r:-}" = ok ]
}

old=$($DOCKER inspect -f '{{index .Config.Labels "holy-grail.seerr.upstream"}}' seerr 2>/dev/null || true)
name="seerr-$(date +%Y%m%d-%H%M%S)-before-$version.tgz"
mkdir -p "$BACKUPS"
compose stop seerr
if ! $DOCKER run --rm --user 0 --network none --entrypoint sh -v "$APPDATA:/d:ro" -v "$BACKUPS:/b" "$REPO:current" \
      -c "tar -C /d -czf /b/$name . && chown $(id -u):$(id -g) /b/$name && chmod 600 /b/$name" \
   || ! $DOCKER run --rm --network none --entrypoint sh -v "$BACKUPS:/b:ro" "$REPO:current" -c "tar -tzf /b/$name" > "$STATE/backup.list" \
   || ! grep -qx './db/db.sqlite3' "$STATE/backup.list" \
   || ! dbcheck "$BACKUPS/$name"; then
  compose up -d --no-deps seerr || log "compose up failed"
  alert "backup failed before $version, nothing changed"
  exit 1
fi
ls -1t "$BACKUPS"/seerr-*.tgz 2>/dev/null | tail -n +7 | xargs -r rm -f || true

$DOCKER tag "$REPO:current" "$REPO:rollback"
$DOCKER tag "$REPO:candidate" "$REPO:current"
compose up -d --no-deps seerr || log "compose up failed"
if verify "$version"; then
  $DOCKER rmi "$REPO:candidate" >/dev/null 2>&1 || true
  keep="$($DOCKER image inspect -f '{{.Id}}' "$REPO:current") $($DOCKER image inspect -f '{{.Id}}' "$REPO:rollback")"
  $DOCKER images --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}' "$REPO" | while read -r ref id; do
    case " $keep " in *" $id "*) ;; *) $DOCKER rmi "$ref" >/dev/null 2>&1 || true ;; esac
  done
  log "updated ${old:-previous} -> $version (backup $BACKUPS/$name)"
  exit 0
fi

compose stop seerr || true
$DOCKER run --rm --user 0 --network none --entrypoint sh -v "$APPDATA:/d" -v "$BACKUPS:/b:ro" "$REPO:rollback" \
  -c "find /d -mindepth 1 -delete && tar -C /d -xzpf /b/$name" || log "restore failed"
$DOCKER tag "$REPO:rollback" "$REPO:current"
compose up -d --no-deps seerr || log "compose up failed"
$DOCKER rmi "$REPO:candidate" "$REPO:$version" >/dev/null 2>&1 || true
want=$($DOCKER image inspect -f '{{.Config.Env}}' "$REPO:current" 2>/dev/null | sed -n 's/.*COMMIT_TAG=\([^] ]*\).*/\1/p')
if verify "$want"; then
  alert "$version failed its checks, rolled back to the previous image and the config backup $name"
else
  alert "$version failed its checks, rolled back, but the previous image does not verify either: look now (backup $BACKUPS/$name)"
fi
exit 1
