#!/usr/bin/env bash
# Runs scripts/clear-data-wipe.sh against a fake Seerr API (python http.server) and a stub title-wipe hook.
# Usage: tests/test_clear_data_wipe.sh   (needs bash, curl, jq, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../scripts/clear-data-wipe.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

cat > "$T/fake.py" <<'PY'
import http.server, json, os, sys, urllib.parse
T = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, body=None):
        data = (body if isinstance(body, str) else json.dumps(body)).encode() if body is not None else b""
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_DELETE(self):
        with open(os.path.join(T, "deletes"), "a") as f:
            f.write(self.path + "\n")
        self.reply(204)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        state = json.load(open(os.path.join(T, "state.json")))
        if self.headers.get("X-Api-Key") != "seerrkey":
            return self.reply(401)
        with open(os.path.join(T, "calls"), "a") as f:
            f.write(self.path + "\n")
        n = sum(1 for _ in open(os.path.join(T, "calls")))
        if u.path == "/api/v1/media":
            if state.get("error"):
                return self.reply(500)
            if state.get("garbage"):
                return self.reply(200, "<html>")
            media = state.get("second", state["media"]) if n > 1 and "second" in state else state["media"]
            total = state.get("total", len(media))
            take, skip = int(q.get("take", 20)), int(q.get("skip", 0))
            return self.reply(200, {"pageInfo": {"results": total}, "results": media[skip:skip + take]})
        if u.path.startswith("/api/v1/movie/") or u.path.startswith("/api/v1/tv/"):
            t = state.get("tmdb", {}).get(u.path.rsplit("/", 1)[1])
            return self.reply(200, t) if t else self.reply(404)
        self.reply(404)
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_address[1]))
s.serve_forever()
PY
python3 "$T/fake.py" "$T" & SRV=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
PORT=$(cat "$T/port")
echo seerrkey > "$T/seerr.key"
cat > "$T/hook.sh" <<'SH'
d=$(dirname "$0")
printf '%s|%s\n' "$*" "$(basename "$TW_CONFIG")" >> "$d/hooks"
echo "stub wiped $*"
exit "$(cat "$d/hook.rc" 2>/dev/null || echo 0)"
SH

# run <fake Seerr state json> [env...]: snapshot, pending list and log persist until reset; hook calls in $T/hooks
run() {
  echo "$1" > "$T/state.json"; shift
  : > "$T/calls"; : > "$T/hooks"; : > "$T/deletes"
  env -i PATH="$PATH" HOME="$T" CW_SEERR_URL="http://127.0.0.1:$PORT" CW_SEERR_KEY="$T/seerr.key" CW_HOOK="$T/hook.sh" \
    CW_SNAPSHOT="$T/snap" CW_LOG="$T/log" CW_RADARR_CONFIG="$T/radarr.xml" CW_SONARR_CONFIG="$T/sonarr.xml" "$@" \
    bash "$SCRIPT" > "$T/out" 2>&1; RC=$?
}
reset() { rm -f "$T/snap" "$T/snap.pending" "$T/log" "$T/hook.rc"; }
m() { echo "{\"id\": $1, \"mediaType\": \"$2\", \"tmdbId\": $3, \"tvdbId\": ${4:-null}, \"status\": ${5:-5}}"; } # m <id> <movie|tv> <tmdbId> [tvdbId] [status]
NOTLD=$(m 13 movie 10331); BAD=$(m 4 movie 1198654); HEIST=$(m 10 tv 71446 327417); BLOCK=$(m 20 movie 555 null 6); NOTVDB=$(m 21 tv 999)
TMDB='"tmdb": {"10331": {"title": "Night of the Living Dead", "releaseDate": "1968-10-01"}, "71446": {"name": "Money Heist", "firstAirDate": "2017-05-02"}}'
SYNCED='"tmdb": {"10331": {"title": "Night of the Living Dead", "releaseDate": "1968-10-01", "mediaInfo": {"id": 30, "requests": []}}}'
REQUESTED='"tmdb": {"10331": {"title": "Night of the Living Dead", "releaseDate": "1968-10-01", "mediaInfo": {"id": 30, "requests": [{"id": 9}]}}}'
nonzero() { [ "$RC" -ne 0 ] && echo 1 || echo 0; }

echo "First run"
reset
run "{\"media\": [$NOTLD, $BAD]}"
assert_eq "no snapshot: snapshot written, nothing wiped, exit 0" "|2|0" "$(cat "$T/hooks")|$(wc -l < "$T/snap")|$RC"
grep -q 'first run' "$T/log" && ok "  logged" || bad "  logged" "$(cat "$T/log")"

echo "Clear Data"
run "{\"media\": [$NOTLD, $BAD]}"
assert_eq "nothing changed: no wipe" "" "$(cat "$T/hooks")"
run "{\"media\": [$(m 13 movie 10331 null 7), $BAD]}"
assert_eq "Remove from Radarr only (row still there, status deleted): no wipe" "" "$(cat "$T/hooks")"
run "{\"media\": [$BAD], $TMDB}"
assert_eq "row gone: full wipe of that movie with title + year from Seerr" "wipe radarr 10331 Night of the Living Dead 1968|radarr.xml" "$(cat "$T/hooks")"
assert_eq "  exit 0" 0 "$RC"
grep -q 'stub wiped wipe radarr 10331' "$T/log" && ok "  hook output in the log" || bad "  hook output in the log" "$(cat "$T/log")"
run "{\"media\": [$BAD], $TMDB}"
assert_eq "next run: not wiped twice" "" "$(cat "$T/hooks")"
reset; run "{\"media\": [$HEIST, $BAD]}"
run "{\"media\": [$BAD], $TMDB}"
assert_eq "series row gone: Sonarr wipe by tvdb id" "wipe sonarr 327417 Money Heist 2017|sonarr.xml" "$(cat "$T/hooks")"
reset; run "{\"media\": [$NOTLD, $BAD]}"
run "{\"media\": [$BAD]}"
assert_eq "Seerr has no TMDB details: wiped by id only" "wipe radarr 10331  0|radarr.xml" "$(cat "$T/hooks")"
reset; run "{\"media\": [$NOTLD, $BAD]}"
run "{\"media\": [$(m 30 movie 10331), $BAD], $REQUESTED}"
assert_eq "cleared and requested again (new row with a request): no wipe" "|" "$(cat "$T/hooks")|$(cat "$T/deletes")"
reset; run "{\"media\": [$NOTLD, $BAD]}"
run "{\"media\": [$(m 30 movie 10331), $BAD], $SYNCED}"
assert_eq "cleared, then re-created by Seerr's own sync before this run (new id, no request): wiped" "wipe radarr 10331 Night of the Living Dead 1968|radarr.xml" "$(cat "$T/hooks")"
assert_eq "  the re-created row is removed after the wipe" "/api/v1/media/30" "$(cat "$T/deletes")"
run "{\"media\": [$BAD], $SYNCED}"
assert_eq "  next run: nothing more" "|" "$(cat "$T/hooks")|$(cat "$T/deletes")"
reset; run "{\"media\": [$NOTLD, $BAD]}"
run "{\"media\": [$BAD], \"second\": [$BAD], $SYNCED}"
assert_eq "re-created by Seerr's sync during the wipe: removed afterwards too" "/api/v1/media/30" "$(cat "$T/deletes")"
reset; printf 'movie:10331\t-\t5\nmovie:1198654\t-\t5\n' > "$T/snap"
run "{\"media\": [$BAD], $TMDB}"
assert_eq "snapshot from before row ids were kept: a gone row is still wiped" "wipe radarr 10331 Night of the Living Dead 1968|radarr.xml" "$(cat "$T/hooks")"

echo "Never wipe on bad data"
reset; run "{\"media\": [$NOTLD, $BAD, $HEIST]}"
run '{"error": true}'
assert_eq "Seerr API error: nothing wiped, exit non-zero" "|1" "$(cat "$T/hooks")|$(nonzero)"
run '{"garbage": true}'
assert_eq "Seerr answers garbage: nothing wiped" "|1" "$(cat "$T/hooks")|$(nonzero)"
run '{"media": []}'
assert_eq "empty list while the snapshot had rows: nothing wiped" "|1" "$(cat "$T/hooks")|$(nonzero)"
run "{\"media\": [$BAD], \"total\": 3}"
assert_eq "partial list (fewer rows than Seerr's total): nothing wiped" "|1" "$(cat "$T/hooks")|$(nonzero)"
assert_eq "  snapshot untouched by the failed runs" 3 "$(wc -l < "$T/snap")"
run "{\"media\": [$BAD], \"second\": [$NOTLD, $BAD, $HEIST]}"
assert_eq "rows back in the confirming second listing: nothing wiped" "" "$(cat "$T/hooks")"
reset; run "{\"media\": [$(m 1 movie 1) , $(m 2 movie 2), $(m 3 movie 3), $(m 4 movie 4), $(m 5 movie 5), $(m 6 movie 6), $(m 7 movie 7)]}"
run "{\"media\": [$(m 7 movie 7)]}"
assert_eq "more rows gone than CW_MAX (5): nothing wiped, exit non-zero" "|1" "$(cat "$T/hooks")|$(nonzero)"
grep -q 'CW_MAX' "$T/log" && ok "  logged with the override" || bad "  logged with the override" "$(cat "$T/log")"
run "{\"media\": [$(m 7 movie 7)]}" CW_MAX=10
assert_eq "  CW_MAX=10 by hand: all six wiped" 6 "$(wc -l < "$T/hooks")"

echo "Skipped rows"
reset; run "{\"media\": [$BLOCK, $NOTVDB, $BAD]}"
run "{\"media\": [$BAD]}"
assert_eq "blocklisted row (status 6) or series without tvdb id gone: no wipe" "" "$(cat "$T/hooks")"
grep -q 'no tvdb id' "$T/log" && ok "  missing tvdb id logged" || bad "  missing tvdb id logged" "$(cat "$T/log")"

echo "Retries"
reset; run "{\"media\": [$NOTLD, $BAD]}"
echo 1 > "$T/hook.rc"
run "{\"media\": [$BAD]}"
assert_eq "wipe failed: exit non-zero" 1 "$(nonzero)"
run "{\"media\": [$BAD]}"
assert_eq "  retried on the next run" "wipe radarr 10331  0|radarr.xml" "$(cat "$T/hooks")"
echo 0 > "$T/hook.rc"
run "{\"media\": [$BAD]}"
run "{\"media\": [$BAD]}"
assert_eq "  succeeded: not retried any more" "" "$(cat "$T/hooks")"
echo 1 > "$T/hook.rc"
reset; echo 1 > "$T/hook.rc"; run "{\"media\": [$NOTLD, $BAD]}"
for _ in 1 2 3 4 5 6; do run "{\"media\": [$BAD]}"; done
run "{\"media\": [$BAD]}"
assert_eq "  gives up after 5 failed tries" "" "$(cat "$T/hooks")"
grep -q 'gave up' "$T/log" && ok "  logged" || bad "  logged" "$(cat "$T/log")"
reset; echo 1 > "$T/hook.rc"; run "{\"media\": [$NOTLD, $BAD]}"
run "{\"media\": [$BAD]}"
run "{\"media\": [$(m 30 movie 10331), $BAD], $REQUESTED}"
assert_eq "  requested again before the retry: retry dropped" "" "$(cat "$T/hooks")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
