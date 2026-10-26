#!/usr/bin/env bash
# Runs scripts/download-now-hook.sh against a fake Radarr/Sonarr API (python http.server).
# Usage: tests/test_download_now_hook.sh   (needs bash, curl, jq, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK="$HERE/../scripts/download-now-hook.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

cat > "$T/fake.py" <<'PY'
import http.server, json, os, sys
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
        state = json.load(open(os.path.join(T, "state.json")))
        if self.command != "GET":
            with open(os.path.join(T, "writes"), "a") as f:
                f.write(f"{self.command} {self.path} {body}\n")
        if self.headers.get("X-Api-Key") != "key":
            return self.reply(401)
        p = self.path.removeprefix("/api/v3")
        if p == "/tag":
            return self.reply(200, state["tags"])
        for kind in ("movie", "series"):
            if self.command == "GET" and p == f"/{kind}/8":
                return self.reply(200, {"id": 8, "title": "T", "tags": state["title_tags"]})
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
printf '<Config>\n  <Port>%s</Port>\n  <UrlBase></UrlBase>\n  <ApiKey>key</ApiKey>\n</Config>\n' "$(cat "$T/port")" > "$T/config.xml"
TAGS='[{"id": 13, "label": "audio-german"}, {"id": 70, "label": "download-now"}]'

run() { # run <title tag ids> <env...>
  echo "{\"tags\": $TAGS, \"title_tags\": [$1]}" > "$T/state.json"; shift
  : > "$T/writes"
  env -i PATH="$PATH" DN_CONFIG="$T/config.xml" "$@" bash "$HOOK" > "$T/out" 2>&1; RC=$?
}

run "13,70" radarr_eventtype=Download radarr_movie_id=8
assert_eq "film imported with download-now: tag removed" 'PUT /api/v3/movie/editor {"movieIds":[8],"tags":[70],"applyTags":"remove"}' "$(cat "$T/writes")"
assert_eq "  log line" "movie 8: download-now removed after the import, later downloads wait for the night" "$(cat "$T/out")"
assert_eq "  exit 0" 0 "$RC"
run "13" radarr_eventtype=Download radarr_movie_id=8
assert_eq "no download-now on the film: nothing written" "|0" "$(cat "$T/writes")|$RC"
run "70" sonarr_eventtype=Download sonarr_series_id=8
assert_eq "episode imported: tag removed from the series" 'PUT /api/v3/series/editor {"seriesIds":[8],"tags":[70],"applyTags":"remove"}' "$(cat "$T/writes")"
run "70" radarr_eventtype=Grab radarr_movie_id=8
assert_eq "grab: nothing (only an import ends the first download)" "|0" "$(cat "$T/writes")|$RC"
run "70" radarr_eventtype=Test
assert_eq "Connect test event: exit 0, nothing written" "|0" "$(cat "$T/writes")|$RC"
echo '{"tags": [{"id": 13, "label": "audio-german"}], "title_tags": [13]}' > "$T/state.json"; : > "$T/writes"
env -i PATH="$PATH" DN_CONFIG="$T/config.xml" radarr_eventtype=Download radarr_movie_id=8 bash "$HOOK" >/dev/null 2>&1; RC=$?
assert_eq "tag does not exist in the app: nothing written, exit 0" "|0" "$(cat "$T/writes")|$RC"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
