#!/usr/bin/env bash
# Runs scripts/grab-guard.sh against a fake Radarr API (python http.server).
# Usage: tests/test_grab_guard.sh   (needs bash, curl, jq, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
GUARD="$HERE/../scripts/grab-guard.sh"
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
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def handle_any(self):
        with open(os.path.join(T, "requests"), "a") as f:
            f.write(f"{self.command} {self.path}\n")
        if self.headers.get("X-Api-Key") != "testkey":
            return self.reply(401)
        state = json.load(open(os.path.join(T, "state.json")))
        u = urllib.parse.urlparse(self.path)
        p = u.path.removeprefix("/api/v3")
        q = dict(urllib.parse.parse_qsl(u.query))
        if self.command == "GET" and p == "/parse":
            return self.reply(200, {"title": q["title"], "parsedMovieInfo": {"year": state["parsed_year"]}})
        if self.command == "GET" and p == "/movie/5":
            return self.reply(200, {"id": 5, "title": "Night of the Living Dead", "year": 1968, "secondaryYear": state.get("secondary")})
        if self.command == "GET" and p == "/queue":
            return self.reply(200, {"records": state["queue"]})
        if self.command == "DELETE" and p.startswith("/queue/"):
            return self.reply(200, {})
        self.reply(404)
    do_GET = do_DELETE = handle_any
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_address[1]))
s.serve_forever()
PY
python3 "$T/fake.py" "$T" & SRV=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
PORT=$(cat "$T/port")
printf '<Config>\n  <Port>%s</Port>\n  <UrlBase></UrlBase>\n  <ApiKey>testkey</ApiKey>\n</Config>\n' "$PORT" > "$T/config.xml"

# run <state json> <release title> <env...>: leaves the DELETEs in $T/deletes and the exit code in $RC
run() {
  echo "$1" > "$T/state.json"; local title=$2; shift 2
  : > "$T/requests"
  env -i PATH="$PATH" LP_CONFIG="$T/config.xml" GG_WAIT=2 radarr_eventtype=Grab radarr_movie_id=5 radarr_movie_year=1968 \
    radarr_release_title="$title" radarr_download_id=abc "$@" bash "$GUARD" > "$T/out" 2>&1; RC=$?
  grep '^DELETE ' "$T/requests" | cut -d' ' -f2- > "$T/deletes"
}
Q='[{"id": 41, "downloadId": "other"}, {"id": 42, "downloadId": "abc"}]'

echo "grab-guard"
run "{\"parsed_year\": 1990, \"queue\": $Q}" "Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP"
assert_eq "release of another year (remake) is removed and blocklisted" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
assert_eq "  exit 0" 0 "$RC"
assert_eq "  log" "movie 5: grabbed 'Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP' is from 1990, not 1968: removed, blocklisted, searching again" "$(cat "$T/out")"
run "{\"parsed_year\": 1968, \"queue\": $Q}" "Night.of.the.Living.Dead.1968.1080p.BluRay.x264-GROUP"
assert_eq "same year is kept" "" "$(cat "$T/deletes")"
run "{\"parsed_year\": 1970, \"queue\": $Q}" "Night.of.the.Living.Dead.1970.1080p.BluRay.x264-GROUP"
assert_eq "two years off (premiere vs release year, Plan 9: TMDB 1957, names 1959) is kept" "" "$(cat "$T/deletes")"
run "{\"parsed_year\": 1971, \"queue\": $Q}" "Night.of.the.Living.Dead.1971.1080p.BluRay.x264-GROUP"
assert_eq "three years off is removed" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
run "{\"parsed_year\": 1975, \"secondary\": 1975, \"queue\": $Q}" "Night.of.the.Living.Dead.1975.1080p.BluRay.x264-GROUP"
assert_eq "the secondary year counts as right" "" "$(cat "$T/deletes")"
run "{\"parsed_year\": 0, \"queue\": $Q}" "Night.of.the.Living.Dead.1080p.BluRay.x264-GROUP"
assert_eq "no year in the name is kept" "" "$(cat "$T/deletes")"
run "{\"parsed_year\": 1990, \"queue\": []}" "Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP"
assert_eq "not in the queue after GG_WAIT: nothing deleted, exit non-zero" "|1" "$(cat "$T/deletes")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)"
run "{}" "x" radarr_eventtype=Test
assert_eq "Test event exits 0 and calls nothing" "0|" "$RC|$(cat "$T/requests")"
run "{}" "x" radarr_eventtype=Download
assert_eq "other events are ignored" "0|" "$RC|$(cat "$T/requests")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
