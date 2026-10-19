#!/usr/bin/env bash
# Runs scripts/language-policy-hook.sh against a fake Radarr/Sonarr API (python http.server).
# Usage: tests/test_language_policy_hook.sh   (needs bash, curl, jq, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK="$HERE/../scripts/language-policy-hook.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

cat > "$T/fake.py" <<'PY'
import http.server, json, os, sys
T = sys.argv[1]
PROFILES = [{"id": 3, "name": "Anime - Bluray + WEB 1080p"},
            {"id": 7, "name": "4K HDR - UHD Bluray + WEB (1080p fallback)"},
            {"id": 10, "name": "4K HDR - UHD Bluray + WEB (1080p fallback) - English original"},
            {"id": 11, "name": "Any quality - old & rare (upgrades to 4K)"},
            {"id": 17, "name": "4K HDR - WEB-2160p (1080p fallback)"},
            {"id": 20, "name": "4K HDR - WEB-2160p (1080p fallback) - English original"}]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        with open(os.path.join(T, "requests"), "a") as f:
            f.write(f"{self.command} {self.path} {body}\n")
        if self.headers.get("X-Api-Key") != "testkey":
            return self.reply(401)
        state = json.load(open(os.path.join(T, "state.json")))
        p = self.path.removeprefix("/base/api/v3")
        if self.command == "GET" and p == "/qualityprofile":
            return self.reply(200, PROFILES)
        for kind in ("movie", "series"):
            if self.command == "GET" and p.startswith(f"/{kind}/") and p.split("/")[2] in state:
                return self.reply(200, state[p.split("/")[2]])
            if self.command == "PUT" and p == f"/{kind}/editor":
                return self.reply(202, [])
        self.reply(404)
    do_GET = do_PUT = do_POST = handle_any
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_address[1]))
s.serve_forever()
PY
python3 "$T/fake.py" "$T" & SRV=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
PORT=$(cat "$T/port")
printf '<Config>\n  <Port>%s</Port>\n  <UrlBase>/base</UrlBase>\n  <ApiKey>%s</ApiKey>\n</Config>\n' "$PORT" testkey > "$T/config.xml"
printf '<Config>\n  <Port>%s</Port>\n  <UrlBase>/base</UrlBase>\n  <ApiKey>wrong</ApiKey>\n</Config>\n' "$PORT" > "$T/badconfig.xml"

# run <state json> <env...>: runs the hook, leaves the PUTs in $T/puts and the exit code in $RC
run() {
  echo "$1" > "$T/state.json"; shift
  : > "$T/requests"
  env -i PATH="$PATH" LP_CONFIG="${CONFIG:-$T/config.xml}" "$@" bash "$HOOK" > "$T/out" 2>&1; RC=$?
  grep '^PUT ' "$T/requests" | cut -d' ' -f2- > "$T/puts"
}
movie() { echo "{\"$1\": {\"id\": $1, \"title\": \"T\", \"qualityProfileId\": $2, \"originalLanguage\": $3}}"; }
EN='{"id": 1, "name": "English"}'; ES='{"id": 3, "name": "Spanish"}'

echo "Radarr"
run "$(movie 5 7 "$EN")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "English original on main moves to the twin" '/base/api/v3/movie/editor {"movieIds":[5],"qualityProfileId":10}' "$(cat "$T/puts")"
assert_eq "  exit 0" 0 "$RC"
run "$(movie 5 7 "$ES")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "Spanish original on main stays" "" "$(cat "$T/puts")"
assert_eq "  exit 0" 0 "$RC"
run "$(movie 5 10 "$ES")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "Spanish original on the twin moves back to main" '/base/api/v3/movie/editor {"movieIds":[5],"qualityProfileId":7}' "$(cat "$T/puts")"
run "$(movie 5 10 "$EN")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "English original on the twin stays" "" "$(cat "$T/puts")"
run "$(movie 5 3 "$EN")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "English original on another profile (anime) stays" "" "$(cat "$T/puts")"
run "$(movie 5 11 "$EN")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "English original on Any quality stays" "" "$(cat "$T/puts")"
run "$(movie 5 11 "$ES")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "Spanish original on Any quality stays" "" "$(cat "$T/puts")"
run "$(movie 5 7 null)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "no original language counts as not English" "" "$(cat "$T/puts")"
run "{}" radarr_eventtype=Test
assert_eq "Test event exits 0" 0 "$RC"
assert_eq "  and calls nothing" "" "$(cat "$T/requests")"
run "{}" radarr_eventtype=Grab radarr_movie_id=5
assert_eq "other events are ignored" "0|" "$RC|$(cat "$T/requests")"
CONFIG="$T/badconfig.xml" run "$(movie 5 7 "$EN")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "API error (wrong key) exits non-zero" 1 "$([ "$RC" -ne 0 ] && echo 1 || echo 0)"
assert_eq "  and changes nothing" "" "$(cat "$T/puts")"

echo "Sonarr"
run "$(movie 8 17 "$EN")" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "English original on main moves to the twin" '/base/api/v3/series/editor {"seriesIds":[8],"qualityProfileId":20}' "$(cat "$T/puts")"
run "$(movie 8 17 "$ES")" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "Spanish original on main stays" "" "$(cat "$T/puts")"
run "$(movie 8 20 "$ES")" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "Spanish original on the twin moves back to main" '/base/api/v3/series/editor {"seriesIds":[8],"qualityProfileId":17}' "$(cat "$T/puts")"
run "$(movie 8 3 "$EN")" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "English anime-profile series stays" "" "$(cat "$T/puts")"
run "$(movie 8 11 "$EN")" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "English series on Any quality stays" "" "$(cat "$T/puts")"
run "{}" sonarr_eventtype=Test
assert_eq "Test event exits 0 and calls nothing" "0|" "$RC|$(cat "$T/requests")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
