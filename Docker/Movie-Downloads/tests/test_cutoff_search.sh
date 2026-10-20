#!/usr/bin/env bash
# Runs scripts/cutoff-search.sh against a fake Radarr + Sonarr API (python http.server).
# Usage: tests/test_cutoff_search.sh   (needs bash, curl, jq, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../scripts/cutoff-search.sh"
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
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        with open(os.path.join(T, "requests"), "a") as f:
            f.write(f"{self.command} {self.path} {body}\n")
        app = self.path.split("/")[1]
        if self.headers.get("X-Api-Key") != app + "key":
            return self.reply(401)
        state = json.load(open(os.path.join(T, "state.json")))
        u = urllib.parse.urlparse(self.path)
        p = u.path.removeprefix(f"/{app}/api/v3")
        if self.command == "GET" and p == "/wanted/cutoff":
            q = dict(urllib.parse.parse_qsl(u.query))
            recs = state[app][: int(q["pageSize"])]
            return self.reply(200, {"page": 1, "pageSize": int(q["pageSize"]), "totalRecords": len(state[app]), "records": recs})
        if self.command == "POST" and p == "/command":
            return self.reply(201, {"id": 1, "name": json.loads(body)["name"]})
        self.reply(404)
    do_GET = do_POST = handle_any
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_address[1]))
s.serve_forever()
PY
python3 "$T/fake.py" "$T" & SRV=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
PORT=$(cat "$T/port")
for app in radarr sonarr; do
  printf '<Config>\n  <Port>%s</Port>\n  <UrlBase>/%s</UrlBase>\n  <ApiKey>%skey</ApiKey>\n</Config>\n' "$PORT" "$app" "$app" > "$T/$app.xml"
done

# run <state json> <env...>: leaves the commands in $T/commands, the cutoff queries in $T/queries, the exit code in $RC
run() {
  echo "$1" > "$T/state.json"; shift
  : > "$T/requests"
  env -i PATH="$PATH" CS_RADARR_CONFIG="$T/radarr.xml" CS_SONARR_CONFIG="${SONARR_XML:-$T/sonarr.xml}" "$@" bash "$SCRIPT" > "$T/out" 2>&1; RC=$?
  grep '^POST ' "$T/requests" | cut -d' ' -f2- > "$T/commands"
  grep '^GET .*/wanted/cutoff' "$T/requests" | cut -d' ' -f2 > "$T/queries"
}
movies() { python3 -c 'import json,sys; print(json.dumps([{"id": int(i), "title": "M"+i} for i in sys.argv[1:]]))' "$@"; }
episodes() { python3 -c 'import json,sys; print(json.dumps([{"id": int(i), "seriesId": 4, "title": "E"+i} for i in sys.argv[1:]]))' "$@"; }

echo "cutoff-search"
run "{\"radarr\": $(movies 4 8 9), \"sonarr\": $(episodes 101 102)}"
assert_eq "searches the cutoff-unmet movies and episodes" "/radarr/api/v3/command {\"name\":\"MoviesSearch\",\"movieIds\":[4,8,9]}
/sonarr/api/v3/command {\"name\":\"EpisodeSearch\",\"episodeIds\":[101,102]}" "$(cat "$T/commands")"
assert_eq "  exit 0" 0 "$RC"
assert_eq "  monitored only, least recently searched first, 10 each by default" \
  "/radarr/api/v3/wanted/cutoff?page=1&pageSize=10&monitored=true&sortKey=movies.lastSearchTime&sortDirection=ascending
/sonarr/api/v3/wanted/cutoff?page=1&pageSize=10&monitored=true&sortKey=episodes.lastSearchTime&sortDirection=ascending&includeSeries=true" "$(cat "$T/queries")"
run "{\"radarr\": $(movies 1 2 3 4 5), \"sonarr\": $(episodes 1 2 3 4 5)}" CS_MOVIES=2 CS_EPISODES=3
assert_eq "caps per run (CS_MOVIES, CS_EPISODES)" "/radarr/api/v3/command {\"name\":\"MoviesSearch\",\"movieIds\":[1,2]}
/sonarr/api/v3/command {\"name\":\"EpisodeSearch\",\"episodeIds\":[1,2,3]}" "$(cat "$T/commands")"
assert_eq "  log names the titles" "radarr: 2 of 5 cutoff-unmet searched: M1, M2
sonarr: 3 of 5 cutoff-unmet searched: E1, E2, E3" "$(cat "$T/out")"
run '{"radarr": [], "sonarr": []}'
assert_eq "nothing unmet: no search" "" "$(cat "$T/commands")"
assert_eq "  exit 0" 0 "$RC"
run "{\"radarr\": $(movies 4), \"sonarr\": $(episodes 7)}" CS_MOVIES=0
assert_eq "CS_MOVIES=0 skips Radarr" "/sonarr/api/v3/command {\"name\":\"EpisodeSearch\",\"episodeIds\":[7]}" "$(cat "$T/commands")"
printf '<Config>\n  <Port>%s</Port>\n  <UrlBase>/sonarr</UrlBase>\n  <ApiKey>wrong</ApiKey>\n</Config>\n' "$PORT" > "$T/bad.xml"
SONARR_XML="$T/bad.xml" run "{\"radarr\": $(movies 4), \"sonarr\": $(episodes 7)}"
assert_eq "Sonarr API error: Radarr still searched, exit non-zero" "/radarr/api/v3/command {\"name\":\"MoviesSearch\",\"movieIds\":[4]}|1" "$(cat "$T/commands")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
