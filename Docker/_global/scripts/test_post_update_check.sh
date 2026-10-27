#!/usr/bin/env bash
# Runs post-update-check.sh against fake check files, and update-project.sh against a fake docker. Usage: scripts/test_post_update_check.sh
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1 ${2:-}"; }
S="$HERE/post-update-check.sh"
mkdir -p "$T/checks"
cat > "$T/checks/Good.sh" <<'EOF'
check good.one '^yes$' "$(echo yes)"
if want web; then check good.web '^200$' "$(echo 200)"; fi
EOF
cat > "$T/checks/Bad.sh" <<'EOF'
check bad.fine '^ok$' "$(echo ok)"
check bad.gone '^present$' "$(echo missing)"
if want db; then check bad.db '^up$' "$(echo down)"; fi
if want web; then check bad.web '^x$' "$(echo x)"; fi
EOF
export POST_UPDATE_DIR="$T/checks"

out=$("$S" Good 2>&1); rc=$?
[[ $rc == 0 ]] && ok "all checks pass -> exit 0" || bad "all checks pass -> exit 0" "rc=$rc $out"
[[ $out == *"PASS good.one"* && $out == *"PASS good.web"* ]] && ok "lists each passing check" || bad "lists each passing check" "$out"

out=$("$S" Bad 2>&1); rc=$?
[[ $rc == 1 ]] && ok "a missing customization -> exit 1" || bad "a missing customization -> exit 1" "rc=$rc"
[[ $out == *"FAIL bad.gone"*"got: missing"* ]] && ok "names the failed check and what it got" || bad "names the failed check and what it got" "$out"
[[ $(tail -1 <<<"$out") == "FAILED: bad.gone bad.db" ]] && ok "last line lists the failed ids" || bad "last line lists the failed ids" "$(tail -1 <<<"$out")"

out=$(SERVICES="web" "$S" Bad 2>&1); rc=$?
[[ $rc == 1 && $out != *bad.db* && $out == *"PASS bad.web"* && $out == *"FAIL bad.gone"* ]] && ok "SERVICES limits want-blocks, unscoped checks still run" || bad "SERVICES limits want-blocks" "rc=$rc $out"

out=$("$S" good 2>&1); rc=$?
[[ $rc == 0 && $out == *good.one* ]] && ok "project name is case-insensitive" || bad "project name is case-insensitive" "rc=$rc $out"

out=$("$S" Nope 2>&1); rc=$?
[[ $rc == 2 ]] && ok "unknown project -> exit 2" || bad "unknown project -> exit 2" "rc=$rc $out"

out=$("$S" all 2>&1); rc=$?
[[ $rc == 1 && $out == *"== Good"* && $out == *"== Bad"* ]] && ok "all runs every project, fails if one fails" || bad "all runs every project" "rc=$rc $out"

out=$("$S" --list 2>&1)
[[ $out == *"Bad"*"Good"* ]] && ok "--list names the projects" || bad "--list names the projects" "$out"

cat > "$T/checks/Long.sh" <<'EOF'
check long.out '^short$' "$(printf 'x%.0s' {1..300})"
EOF
out=$("$S" Long 2>&1)
(( $(grep 'FAIL long.out' <<<"$out" | wc -c) < 140 )) && ok "a failing value is cut short" || bad "a failing value is cut short" "$out"

# update-project.sh: a failing customization check rolls back; a passing one keeps the update.
mkdir -p "$T/bin" "$T/Demo"
cat > "$T/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
  info) exit 0 ;;
  "compose config --format json") echo '{"name":"demo","services":{"app":{"image":"img:1"}}}' ;;
  "compose ps -q") echo cid1 ;;
  "inspect -f {{.Name}} {{.Config.Image}} {{.Id}} {{.RestartCount}} cid1") echo "/demo-app img:1 cid1 0" ;;
  "inspect -f {{.Image}} demo-app") echo sha:old ;;
  "tag "*) touch "$FAKE_DIR/rolled" ;;
  inspect\ -f\ \{\{.State.Status\}\}\|*) [[ -e $FAKE_DIR/rolled ]] && echo "running|nohc|sha:old|cid3|0" || echo "running|nohc|sha:new|cid2|0" ;;
  *"com.docker.compose.service"*) echo app ;;
  "inspect -f {{.State.Status}} {{.Image}} demo-app") echo "running sha:old" ;;
  compose*) : ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
STUB
chmod +x "$T/bin/docker"
export FAKE_LOG="$T/log" FAKE_DIR="$T" WAIT=0 ROLLBACK_WAIT=0 CHECK_TRIES=1 HOME="$T"
U="$HERE/update-project.sh"

echo 'check demo.ok "^ok$" "$(echo ok)"' > "$T/checks/Demo.sh"
out=$(PATH="$T/bin:$PATH" "$U" "$T/Demo" pull 2>&1); rc=$?
[[ $rc == 0 && $out == *"custom: OK"* ]] && ok "update-project runs the project's customization check" || bad "update-project runs the customization check" "rc=$rc $out"

echo 'check demo.lost "^here$" "$([[ -e $FAKE_DIR/rolled ]] && echo here || echo gone)"' > "$T/checks/Demo.sh"
rm -f "$T/rolled"; out=$(PATH="$T/bin:$PATH" "$U" "$T/Demo" pull 2>&1); rc=$?
[[ $rc == 1 && $out == *"custom: FAIL demo.lost"* && $out == *"ROLLED BACK demo"* ]] && ok "a dropped customization rolls the update back" || bad "a dropped customization rolls back" "rc=$rc $out"

echo 'check demo.lost "^here$" "$(echo gone)"' > "$T/checks/Demo.sh"
rm -f "$T/rolled"; out=$(PATH="$T/bin:$PATH" "$U" "$T/Demo" pull 2>&1); rc=$?
[[ $rc == 3 && $out == *"rollback: customizations still missing"* ]] && ok "still missing after the rollback -> exit 3" || bad "still missing after the rollback -> exit 3" "rc=$rc $out"
echo 'check demo.lost "^here$" "$([[ -e $FAKE_DIR/rolled ]] && echo here || echo gone)"' > "$T/checks/Demo.sh"

rm -f "$T/rolled"; out=$(POST_CHECK=0 PATH="$T/bin:$PATH" "$U" "$T/Demo" pull 2>&1); rc=$?
[[ $rc == 0 && $out != *custom:* ]] && ok "POST_CHECK=0 skips it" || bad "POST_CHECK=0 skips it" "rc=$rc $out"

rm -f "$T/rolled"; out=$(CHECK='true' PATH="$T/bin:$PATH" "$U" "$T/Demo" pull 2>&1); rc=$?
[[ $rc == 1 && $out == *"check : OK (true)"* && $out == *"custom: FAIL"* ]] && ok "an explicit CHECK runs as well" || bad "an explicit CHECK runs as well" "rc=$rc $out"

rm -f "$T/checks/Demo.sh" "$T/rolled"; out=$(PATH="$T/bin:$PATH" "$U" "$T/Demo" pull 2>&1); rc=$?
[[ $rc == 0 && $out != *custom:* ]] && ok "no check file -> container checks only" || bad "no check file -> container checks only" "rc=$rc $out"

# The repo's registry and its checks name the same ids (Docker/CUSTOMIZATIONS.md "Verify" column vs post-update.d).
REPO_D=$(cd "$HERE/.." && pwd)/post-update.d; REG=$(cd "$HERE/../.." && pwd)/CUSTOMIZATIONS.md
if [[ -d $REPO_D && -f $REG ]]; then
  diffs=$(python3 - "$REPO_D" "$REG" <<'PY'
import pathlib, re, sys
d, reg = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]).read_text()
checks = {m for f in d.glob("*.sh") for m in re.findall(r"^\s*check ([a-z0-9][a-z0-9.-]*) ", f.read_text(), re.M)}
listed, col = set(), None
for line in reg.splitlines():
    cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else None
    if not cells:
        col = None
    elif any(c.startswith("Verify") for c in cells):
        col = next(i for i, c in enumerate(cells) if c.startswith("Verify"))
    elif col is not None and len(cells) > col:
        listed |= set(re.findall(r"`([a-z0-9][a-z0-9-]*\.[a-z0-9.-]+)`", cells[col]))
print(" ".join(sorted(f"+{x}" for x in checks - listed) + sorted(f"-{x}" for x in listed - checks)))
PY
)
  [[ -z $diffs ]] && ok "registry and checks name the same ids" || bad "registry and checks name the same ids" "(+ check without registry row, - row without check): $diffs"
  dup=$(grep -hoE '^\s*check [a-z0-9.-]+ ' "$REPO_D"/*.sh | awk '{print $2}' | sort | uniq -d | xargs)
  [[ -z $dup ]] && ok "check ids are unique" || bad "check ids are unique" "$dup"
  for f in "$REPO_D"/*.sh; do bash -n "$f" || bad "syntax $(basename "$f")"; done
fi

echo "$PASS passed, $FAIL failed"
[[ $FAIL == 0 ]]
