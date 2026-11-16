#!/usr/bin/env bash
# scripts/bar-assets.sh (README "Indexer bar", Caching) with a fake docker, and the versioned URLs in indexer-bar/nginx.conf.
# Usage: tests/test_bar_assets.sh
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../scripts/bar-assets.sh"
CONF="$HERE/../indexer-bar/nginx.conf"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

mkdir -p "$T/www" "$T/data" "$T/bin"
printf 'one\n' > "$T/www/a.js"
printf 'two\n' > "$T/www/b.css"
cat > "$T/bin/docker" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
[ "$1" = inspect ] && echo "${FAKE_RUNNING:-true}"
exit 0
FAKE
chmod +x "$T/bin/docker"
export FAKE_LOG="$T/docker.log" DOCKER="$T/bin/docker" BA_WWW="$T/www" BA_OUT="$T/data/asset-version.conf"
want() { (cd "$T/www" && printf '%s\n' * | LC_ALL=C sort | while read -r f; do printf '%s\n' "$f"; cat "$f"; done | sha256sum | cut -c1-12); }
reloads() { grep -c 'exec seerr-proxy nginx -s reload' "$FAKE_LOG" 2>/dev/null || true; }

echo "bar-assets.sh"
"$SCRIPT" >/dev/null
v1=$(want)
assert_eq "include file names the version of www/" "set \$hg_v \"$v1\";" "$(cat "$BA_OUT")"
assert_eq "seerr-proxy reloaded once" 1 "$(reloads)"
assert_eq "version prints it" "$v1" "$("$SCRIPT" version)"
"$SCRIPT" >/dev/null
assert_eq "unchanged www: no reload" 1 "$(reloads)"
printf 'one more\n' > "$T/www/a.js"
"$SCRIPT" >/dev/null
v2=$(want)
assert_eq "a changed file: new version" "set \$hg_v \"$v2\";" "$(cat "$BA_OUT")"
assert_eq "a changed file: reloaded" 2 "$(reloads)"
mv "$T/www/b.css" "$T/www/c.css"
"$SCRIPT" >/dev/null
assert_eq "a renamed file: new version" "$(want)" "$("$SCRIPT" version)"
printf 'x\n' > "$T/www/a.js"
FAKE_RUNNING=false "$SCRIPT" >/dev/null
assert_eq "seerr-proxy not running: file written, no reload" "3 set \$hg_v \"$(want)\";" "$(reloads) $(cat "$BA_OUT")"
assert_eq "--help" 0 "$("$SCRIPT" --help >/dev/null; echo $?)"
assert_eq "unknown argument: usage, exit 64" 64 "$("$SCRIPT" nope >/dev/null 2>&1; echo $?)"

echo "nginx.conf"
inject=$(grep -o "sub_filter \"</head>\" '.*';" "$CONF")
assets=$(grep -o '/_indexer-bar/[a-z-]*\.\(js\|css\)\(?[^"]*\)\?"' <<<"$inject" | sort -u)
assert_eq "every injected script and stylesheet carries ?v=\$hg_v" "" "$(grep -v '?v=\$hg_v"$' <<<"$assets")"
assert_eq "16 injected files" 16 "$(wc -l <<<"$assets" | tr -d ' ')"
assert_eq "version include read in the server block" 1 "$(grep -c 'include /srv/indexer-bar/data/asset-version\*\.conf;' "$CONF")"
assert_eq "/_indexer-bar/ cached by version" 1 "$(sed -n '/location \/_indexer-bar\/ {/,/}/p' "$CONF" | grep -c 'add_header Cache-Control \$hg_asset_cache always;')"
assert_eq "immutable only for the current version, private (no shared cache on the edge)" 1 "$(grep -c '"~^(\[0-9a-f\]{12}):\\1\$" "private, max-age=31536000, immutable";' "$CONF")"
assert_eq "data files stay uncached" 3 "$(grep -c 'add_header Cache-Control "no-store" always;' <(sed -n '/location = \/_indexer-bar\/[a-z-]*\.json {/,/}/p' "$CONF"))"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
