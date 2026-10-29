#!/usr/bin/env bash
# Runs update-project.sh against a fake docker that keeps tags, the compose file and the container in files. Usage: scripts/test_update_project.sh
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1 ${2:-}"; }
U="$HERE/update-project.sh"
TODAY=$(date +%F)

mkdir -p "$T/bin" "$T/Demo" "$T/checks"
cat > "$T/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
D=$FAKE_DIR
lookup() { awk -v r="$1" '$1==r {print $2}' "$D/tags" | tail -1; }
settag() { grep -v "^$2 " "$D/tags" > "$D/tags.t"; echo "$2 $1" >> "$D/tags.t"; mv "$D/tags.t" "$D/tags"; }
resolve() {
  local f img=""
  for f in "$@"; do [[ -f $f ]] && { i=$(sed -n 's/^ *image: *//p' "$f" | head -1); [[ -n $i ]] && img=$i; }; done
  if [[ $img == '${'*'}' ]]; then v=${img#\$\{}; v=${v%\}}; img=$(sed -n "s/^$v=//p" .env); fi
  echo "$img"
}
case $1 in
  info) exit 0 ;;
  tag) id=$2; [[ $id == sha:* ]] || id=$(lookup "$2"); [[ -n $id ]] || exit 1; settag "$id" "$3" ;;
  image)
    case $2 in
      inspect) id=$(lookup "${@: -1}"); [[ -n $id ]] && echo "$id" || exit 1 ;;
      ls) awk -v r="${@: -1}" 'index($1, r":")==1 {print $1}' "$D/tags" ;;
      rm) for r in "${@:3}"; do grep -v "^$r " "$D/tags" > "$D/tags.t"; mv "$D/tags.t" "$D/tags"; done ;;
      *) echo "unexpected: $*" >&2; exit 9 ;;
    esac ;;
  inspect)
    read -r id ref gen < "$D/ctr"
    case "$*" in
      *"{{.Name}} {{.Config.Image}}"*) echo "/demo-app $ref cid$gen 0 app $D/Demo/compose.yaml" ;;
      "inspect -f {{.Image}} demo-app") echo "$id" ;;
      *"{{.State.Status}}|"*) echo "running|nohc|$id|cid$gen|0" ;;
      *"com.docker.compose.service"*) echo app ;;
      "inspect -f {{.State.Status}} {{.Image}} demo-app") echo "running $id" ;;
      *) echo "unexpected: $*" >&2; exit 9 ;;
    esac ;;
  compose)
    shift; files=(); while [[ $1 == -f || $1 == -p ]]; do [[ $1 == -f ]] && files+=("$2"); shift 2; done
    (( ${#files[@]} )) || files=(compose.yaml)
    ref=$(resolve "${files[@]}")
    case "$1 $2" in
      "config --format") echo "{\"name\":\"demo\",\"services\":{\"app\":{\"image\":\"$ref\"}}}" ;;
      "ps -q") echo cid1 ;;
      "ps --format") echo "demo-app Up" ;;
      pull*) new=$(awk -v r="$ref" '$1==r {print $2}' "$D/pullmap"); [[ -n $new ]] && settag "$new" "$ref"; : ;;
      up*) id=$(lookup "$ref"); [[ -n $id ]] || { echo "Error: no such image $ref"; exit 1; }
           read -r _ _ gen < "$D/ctr"; echo "$id $ref $((gen + 1))" > "$D/ctr"; echo " Container demo-app Recreated" ;;
      *) : ;;
    esac ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
STUB
chmod +x "$T/bin/docker"
export FAKE_LOG="$T/log" FAKE_DIR="$T" WAIT=0 ROLLBACK_WAIT=0 CHECK_TRIES=1 HOME="$T" POST_UPDATE_DIR="$T/checks" PATH="$T/bin:$PATH"
# CHECK passes only on the old image: every update below fails and rolls back.
OLDCHECK='grep -q "^sha:old " "$FAKE_DIR/ctr"'

setup() {  # setup <env ref> <container ref>; tags/pullmap come from the test
  printf 'services:\n  app:\n    image: ${APP_IMAGE}\n' > "$T/Demo/compose.yaml"
  echo "APP_IMAGE=$1" > "$T/Demo/.env"
  echo "sha:old ${2:-$1} 1" > "$T/ctr"
  : > "$T/pullmap"; : > "$T/log"; rm -rf "$T/backups"
}
run() { out=$(cd "$T" && "$U" "$T/Demo" "$@" 2>&1); rc=$?; }
ctr() { cat "$T/ctr"; }

echo "tag bump made after 'snapshot': rollback restores .env and the old image"
setup img:1; printf 'img:1 sha:old\nimg:2 sha:new\n' > "$T/tags"
run snapshot; [[ $rc == 0 ]] && ok "snapshot mode exits 0" || bad "snapshot mode exits 0" "rc=$rc $out"
echo "APP_IMAGE=img:2" > "$T/Demo/.env"
CHECK=$OLDCHECK run pull
[[ $rc == 1 && $out == *"ROLLED BACK demo"* ]] && ok "exit 1, rolled back" || bad "exit 1, rolled back" "rc=$rc $out"
[[ $(cat "$T/Demo/.env") == "APP_IMAGE=img:1" ]] && ok ".env restored from the snapshot" || bad ".env restored" "$(cat "$T/Demo/.env")"
[[ $(ctr) == "sha:old img:1 "* ]] && ok "container back on the old image and ref" || bad "container back on the old image" "$(ctr)"
[[ ! -e $T/backups/pre-update/pending/demo ]] && ok "pending snapshot consumed" || bad "pending snapshot consumed"

echo "tag bump made before the run without a snapshot: old image pinned, compose drift reported"
setup img:2 img:1; printf 'img:1 sha:old\nimg:2 sha:new\n' > "$T/tags"
CHECK=$OLDCHECK run recreate
[[ $rc == 1 && $out == *"ROLLED BACK demo"* ]] && ok "exit 1, rolled back (was ROLLBACK FAILED)" || bad "exit 1, rolled back" "rc=$rc $out"
[[ $(ctr) == "sha:old "* ]] && ok "container on the old image" || bad "container on the old image" "$(ctr)"
[[ $out == *"img:2"*"img:1"* && $out == *WARN* ]] && ok "warns that compose still names the new ref" || bad "warns about compose drift" "$out"
grep -q -- "-f .*rollback-override" "$T/log" && ok "pinned with an override file" || bad "pinned with an override file" "$(grep 'compose.*up' "$T/log")"

echo "same tag, new digest (auto-update): rollback re-tags the old image"
setup img:1; printf 'img:1 sha:old\n' > "$T/tags"; echo "img:1 sha:new" > "$T/pullmap"
CHECK=$OLDCHECK run pull
[[ $rc == 1 && $(ctr) == "sha:old img:1 "* ]] && ok "back on the old image" || bad "back on the old image" "rc=$rc $(ctr) $out"
[[ $(awk '$1=="img:1"{print $2}' "$T/tags") == sha:old ]] && ok "tag points at the old image again" || bad "tag points at the old image" "$(cat "$T/tags")"
! grep -q "rollback-override" "$T/log" && ok "no pin needed when compose names the old ref" || bad "no pin needed"

echo "digest-pinned ref bumped during the run: compose restored, old digest recreated"
setup "img@sha256:d1"; printf 'img@sha256:d1 sha:old\nimg@sha256:d2 sha:new\n' > "$T/tags"
run snapshot; echo "APP_IMAGE=img@sha256:d2" > "$T/Demo/.env"
CHECK=$OLDCHECK run recreate
[[ $rc == 1 && $(ctr) == "sha:old img@sha256:d1 "* ]] && ok "back on the old digest" || bad "back on the old digest" "rc=$rc $(ctr) $out"
[[ $(cat "$T/Demo/.env") == "APP_IMAGE=img@sha256:d1" ]] && ok ".env restored" || bad ".env restored" "$(cat "$T/Demo/.env")"
! grep -q "^tag sha:old img@" "$T/log" && ok "never tags a digest ref" || bad "never tags a digest ref"

echo "rollback tags: one per run, last ROLLBACK_KEEP per project kept"
setup img:1; printf 'img:1 sha:old\nimg:rollback-2026-10-01-demo sha:a\nimg:rollback-2026-10-02-demo sha:b\nimg:rollback-2026-10-03-demo sha:c\nimg:rollback-2026-10-01-other sha:d\n' > "$T/tags"
echo "img:1 sha:new" > "$T/pullmap"
CHECK=true ROLLBACK_KEEP=3 run pull
[[ $rc == 0 ]] && ok "update kept" || bad "update kept" "rc=$rc $out"
grep -q "^img:rollback-$TODAY-demo sha:old$" "$T/tags" && ok "old image tagged <repo>:rollback-<date>-<project>" || bad "rollback tag" "$(cat "$T/tags")"
[[ $(grep -c ':rollback-.*-demo ' "$T/tags") == 3 ]] && ! grep -q 'rollback-2026-10-01-demo' "$T/tags" && ok "oldest beyond ROLLBACK_KEEP untagged" || bad "keep last N" "$(cat "$T/tags")"
grep -q 'rollback-2026-10-01-other' "$T/tags" && ok "other projects' rollback tags untouched" || bad "other project untouched"
grep -n "^tag sha:old img:rollback" "$T/log" | head -1 | grep -q . && [[ $(grep -n "^tag sha:old img:rollback" "$T/log" | cut -d: -f1) -lt $(grep -n "^compose pull" "$T/log" | cut -d: -f1) ]] && ok "tagged before the pull" || bad "tagged before the pull" "$(cat "$T/log")"

echo "customization check names the project"
setup img:1; printf 'img:1 sha:old\n' > "$T/tags"
echo 'check demo.ok "^ok$" "$(echo ok)"' > "$T/checks/Demo.sh"
run recreate
[[ $rc == 0 && $out == *"custom: OK (Demo)"* ]] && ok "custom: OK (Demo)" || bad "custom: OK (Demo)" "rc=$rc $out"
rm -f "$T/checks/Demo.sh"

echo "a stale pending snapshot is not restored"
setup img:1; printf 'img:1 sha:old\nimg:2 sha:new\n' > "$T/tags"
run snapshot; touch -d '-2 days' "$T/backups/pre-update/pending/demo/taken"
echo "APP_IMAGE=img:2" > "$T/Demo/.env"
CHECK=$OLDCHECK run recreate
[[ $out == *"stale"* && $(cat "$T/Demo/.env") == "APP_IMAGE=img:2" && $(ctr) == "sha:old "* ]] && ok "stale snapshot ignored, old image pinned" || bad "stale snapshot ignored" "rc=$rc $(cat "$T/Demo/.env") $out"

echo "$PASS passed, $FAIL failed"; [[ $FAIL == 0 ]]
