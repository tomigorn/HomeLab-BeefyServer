#!/usr/bin/env bash
# Runs scripts/request-tags.sh against a fake Seerr + Radarr/Sonarr API (python http.server) and a stub hook.
# Usage: tests/test_request_tags.sh   (needs bash, curl, jq, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../scripts/request-tags.sh"
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
        if self.path.startswith("/seerr/"):
            if self.headers.get("X-Api-Key") != "seerrkey":
                return self.reply(401)
            if self.path.startswith("/seerr/api/v1/request?"):
                return self.reply(200, {"results": state["requests"]})
            return self.reply(404)
        app, _, p = self.path.lstrip("/").partition("/api/v3")
        if self.headers.get("X-Api-Key") != app + "key":
            return self.reply(401)
        if self.command == "GET" and p == "/qualityprofile":
            return self.reply(200, state.get(app + "_profiles", []))
        if self.command == "PUT" and p.startswith("/qualityprofile/"):
            return self.reply(202, json.loads(body))
        for kind in ("movie", "series"):
            if self.command == "GET" and p.startswith(f"/{kind}/"):
                t = state.get(app, {}).get(p.split("/")[2])
                return self.reply(200, t) if t else self.reply(404)
            if self.command == "PUT" and p == f"/{kind}/editor":
                return self.reply(202, [])
        if self.command == "POST" and p == "/command":
            return self.reply(201, {"id": 1})
        self.reply(404)
    do_GET = do_PUT = do_POST = handle_any
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
echo seerrkey > "$T/seerr.key"
cat > "$T/hook.sh" <<'SH'
echo "${radarr_eventtype:-}${sonarr_eventtype:-} ${radarr_movie_id:-}${sonarr_series_id:-} LP_TAGS_ONLY=${LP_TAGS_ONLY:-} LP_CONFIG=$(basename "${LP_CONFIG:-}")" >> "$(dirname "$0")/hooks"
SH

# run <state json> [env...]: runs the script with a fresh hook log; the state file of handled ids persists until reset
run() {
  echo "$1" > "$T/state.json"; shift
  : > "$T/writes"; : > "$T/hooks"
  env -i PATH="$PATH" HOME="$T" RT_SEERR_URL="http://127.0.0.1:$PORT/seerr" RT_SEERR_KEY="$T/seerr.key" \
    RT_RADARR_CONFIG="$T/radarr.xml" RT_SONARR_CONFIG="$T/sonarr.xml" RT_HOOK="$T/hook.sh" RT_STATE="$T/done" "$@" \
    bash "$SCRIPT" > "$T/out" 2>&1; RC=$?
}
reset() { rm -f "$T/done"; }
req() { echo "{\"id\": $1, \"status\": $2, \"type\": \"$3\", \"tags\": [$4], \"media\": {\"externalServiceId\": $5}}"; }

echo "Movies"
reset
run "{\"requests\": [$(req 9 5 movie 13 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"The Ring Thing\", \"tags\": []}}}"
assert_eq "existing film without the requested tag: tag added" 'PUT /radarr/api/v3/movie/editor {"movieIds":[8],"tags":[13],"applyTags":"add"}
POST /radarr/api/v3/command {"name":"MoviesSearch","movieIds":[8]}' "$(cat "$T/writes")"
assert_eq "  hook run for that film, tags only" "MovieAdded 8 LP_TAGS_ONLY=1 LP_CONFIG=radarr.xml" "$(cat "$T/hooks")"
assert_eq "  log line" "radarr: request 9, The Ring Thing: tags 13 added, searched" "$(cat "$T/out")"
assert_eq "  exit 0" 0 "$RC"
run "{\"requests\": [$(req 9 5 movie 13 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"The Ring Thing\", \"tags\": []}}}"
assert_eq "a handled request is not applied again (tag removed by hand stays removed)" "|" "$(cat "$T/writes")|$(cat "$T/hooks")"
reset
run "{\"requests\": [$(req 9 2 movie "13,16" 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"T\", \"tags\": [13, 2]}}}"
assert_eq "only the missing tags are added" 'PUT /radarr/api/v3/movie/editor {"movieIds":[8],"tags":[16],"applyTags":"add"}
POST /radarr/api/v3/command {"name":"MoviesSearch","movieIds":[8]}' "$(cat "$T/writes")"
reset
run "{\"requests\": [$(req 10 5 movie 13 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"T\", \"tags\": [13]}}}"
assert_eq "new film (tags came with the add): nothing to do" "||" "$(cat "$T/writes")|$(cat "$T/hooks")|$(cat "$T/out")"
assert_eq "  but marked handled" "10" "$(cat "$T/done")"
reset
run "{\"requests\": [$(req 11 1 movie 13 8), $(req 12 3 movie 13 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"T\", \"tags\": []}}}"
assert_eq "pending and declined requests are skipped" "|" "$(cat "$T/writes")|$(cat "$T/hooks")"
assert_eq "  and stay unhandled" "" "$(cat "$T/done" 2>/dev/null)"
run "{\"requests\": [$(req 13 2 movie "" 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"T\", \"tags\": []}}}"
assert_eq "request without tags: nothing to do" "" "$(cat "$T/writes")"
run "{\"requests\": [$(req 14 2 movie 13 null)], \"radarr\": {}}"
assert_eq "not in Radarr yet: skipped, retried next run" "|" "$(cat "$T/writes")|$(grep -x 14 "$T/done")"
reset
run "{\"requests\": [$(req 9 5 movie 13 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"T\", \"tags\": []}}}" RT_SEARCH=0
assert_eq "RT_SEARCH=0: tags and hook, no search" 'PUT /radarr/api/v3/movie/editor {"movieIds":[8],"tags":[13],"applyTags":"add"}|MovieAdded 8 LP_TAGS_ONLY=1 LP_CONFIG=radarr.xml' "$(cat "$T/writes")|$(cat "$T/hooks")"
assert_eq "  log line" "radarr: request 9, T: tags 13 added, not searched (RT_SEARCH=0)" "$(cat "$T/out")"

echo "Series"
reset
run "{\"requests\": [$(req 20 2 tv 31 5)], \"sonarr\": {\"5\": {\"id\": 5, \"title\": \"S\", \"tags\": []}}}"
assert_eq "existing series: tag added, series searched" 'PUT /sonarr/api/v3/series/editor {"seriesIds":[5],"tags":[31],"applyTags":"add"}
POST /sonarr/api/v3/command {"name":"SeriesSearch","seriesId":5}' "$(cat "$T/writes")"
assert_eq "  hook run for that series" "SeriesAdd 5 LP_TAGS_ONLY=1 LP_CONFIG=sonarr.xml" "$(cat "$T/hooks")"

echo "Swiss German profile language"
SWISS='{"id": 13, "name": "Swiss German (Mundart) only - any quality", "language": {"id": -2, "name": "Original"}}'
reset
run "{\"requests\": [], \"radarr_profiles\": [{\"id\": 7, \"name\": \"4K\", \"language\": {\"id\": -2, \"name\": \"Original\"}}, $SWISS]}"
assert_eq "language put back to Original (Recyclarr update): set to Any again" 'PUT /radarr/api/v3/qualityprofile/13 {"id":13,"name":"Swiss German (Mundart) only - any quality","language":{"id":-1,"name":"Any"}}' "$(cat "$T/writes")"
assert_eq "  log line" "radarr: profile 13 language Original -> Any" "$(cat "$T/out")"
run "{\"requests\": [], \"radarr_profiles\": [${SWISS/Original/Any}]}"
assert_eq "language Any: nothing written" "" "$(cat "$T/writes")"

echo "Errors"
reset
echo wrong > "$T/seerr.key"
run "{\"requests\": [$(req 9 5 movie 13 8)], \"radarr\": {\"8\": {\"id\": 8, \"title\": \"T\", \"tags\": []}}}"
assert_eq "Seerr unreachable or wrong key: exit non-zero, nothing written" "1|" "$([ "$RC" -ne 0 ] && echo 1 || echo 0)|$(cat "$T/writes")"
echo seerrkey > "$T/seerr.key"
run "{\"requests\": [$(req 9 5 movie 13 8), $(req 21 2 tv 31 5)], \"radarr\": {}, \"sonarr\": {\"5\": {\"id\": 5, \"title\": \"S\", \"tags\": []}}}"
assert_eq "film deleted from Radarr: marked handled, the next request still runs" "9|SeriesAdd 5 LP_TAGS_ONLY=1 LP_CONFIG=sonarr.xml" "$(grep -x 9 "$T/done")|$(cat "$T/hooks")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
