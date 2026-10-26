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
            return self.reply(200, {"id": 5, "title": "Night of the Living Dead", "year": 1968, "tmdbId": 1234, "secondaryYear": state.get("secondary")})
        if self.command == "GET" and p == "/queue":
            recs = state["queue"]
            if "movieIds" in q:
                recs = [r for r in recs if str(r.get("movieId")) == q["movieIds"]]
            if q.get("includeUnknownMovieItems") != "true":
                recs = [r for r in recs if r.get("movieId") is not None]
            return self.reply(200, {"records": recs})
        if self.command == "DELETE" and p.startswith("/queue/"):
            return self.reply(200, {})
        if self.command == "GET" and p == "/history/movie":
            return self.reply(200, state.get("history", []))
        if self.command == "POST" and p.startswith("/history/failed/"):
            return self.reply(500 if state.get("fail_history") else 200, {})
        self.reply(404)
    do_GET = do_DELETE = do_POST = handle_any
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
  : > "$T/requests"; rm -rf "$T/events"
  env -i PATH="$PATH" LP_CONFIG="$T/config.xml" GG_WAIT=2 GG_EVENTS="$T/events" GG_DUBONLY="$T/dub-only.json" radarr_eventtype=Grab radarr_movie_id=5 radarr_movie_year=1968 \
    radarr_release_title="$title" radarr_download_id=abc "$@" bash "$GUARD" > "$T/out" 2>&1; RC=$?
  grep '^DELETE ' "$T/requests" | cut -d' ' -f2- > "$T/deletes"
  grep '^POST ' "$T/requests" | cut -d' ' -f2- > "$T/posts"
  cat "$T"/events/*.json 2>/dev/null | jq -r '[.app, .action, .title, .year, .release, .releaseYear] | @tsv' > "$T/event"
  cat "$T"/events/*.json 2>/dev/null | jq -r '[.reason // "", (.languages // [] | join(",")), .original // ""] | @tsv' > "$T/event2"
}
# dub <title> [age days] [titleId] [langs json]: a dub-only list with one entry
dub() { jq -n --arg t "$1" --argjson at "$(( $(date +%s) - ${2:-1} * 86400 ))" --argjson id "${3:-1234}" --argjson l "${4:-[\"German\", \"French\"]}" \
  '[{guid: "https://x/details/abc", title: $t, indexer: "NzbPlanet", langs: $l, original: "Persian", titleId: $id, at: $at}]' > "$T/dub-only.json"; }
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
QU='[{"id": 41, "downloadId": "other", "movieId": 9}, {"id": 43, "downloadId": "abc", "movieId": null}]'
run "{\"parsed_year\": 1990, \"queue\": $QU}" "Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP"
assert_eq "queue item Radarr could not map to a movie (movieId null, the wrong-year case) is found and removed" "/api/v3/queue/43?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
run "{\"parsed_year\": 1975, \"secondary\": 1975, \"queue\": $Q}" "Night.of.the.Living.Dead.1975.1080p.BluRay.x264-GROUP"
assert_eq "the secondary year counts as right" "" "$(cat "$T/deletes")"
run "{\"parsed_year\": 0, \"queue\": $Q}" "Night.of.the.Living.Dead.1080p.BluRay.x264-GROUP"
assert_eq "no year in the name is kept" "" "$(cat "$T/deletes")"
run "{\"parsed_year\": 1990, \"queue\": []}" "Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP"
assert_eq "not in the queue after GG_WAIT: nothing deleted, exit non-zero" "|1" "$(cat "$T/deletes")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)"
H='[{"id": 70, "eventType": "grabbed", "downloadId": "other"}, {"id": 71, "eventType": "downloadFolderImported", "downloadId": "abc"}, {"id": 72, "eventType": "grabbed", "downloadId": "abc"}]'
REL=Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP
run "{\"parsed_year\": 1990, \"queue\": $Q}" "$REL"
assert_eq "event for a queue removal" "radarr	removed	Night of the Living Dead	1968	$REL	1990" "$(cat "$T/event")"
run "{\"parsed_year\": 1990, \"queue\": [], \"history\": $H}" "$REL"
assert_eq "not in the queue: the grabbed history record is marked failed" "/api/v3/history/failed/72" "$(cat "$T/posts")"
assert_eq "  nothing deleted, exit 0" "|0" "$(cat "$T/deletes")|$RC"
assert_eq "  log" "movie 5: grabbed '$REL' is from 1990, not 1968: not in the queue, marked failed in history, blocklisted, searching again" "$(cat "$T/out")"
assert_eq "  event" "radarr	marked-failed	Night of the Living Dead	1968	$REL	1990" "$(cat "$T/event")"
run "{\"parsed_year\": 1990, \"queue\": [], \"history\": []}" "$REL"
assert_eq "no history record either: nothing posted, exit non-zero, event says check by hand" "|1|radarr	manual	Night of the Living Dead	1968	$REL	1990" "$(cat "$T/posts")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)|$(cat "$T/event")"
assert_eq "  stderr keeps the remove-by-hand error" "movie 5: grabbed '$REL' is from 1990, not 1968, but it is not in the queue or in history: remove it by hand" "$(cat "$T/out")"
run "{\"parsed_year\": 1990, \"queue\": [], \"history\": $H, \"fail_history\": true}" "$REL"
assert_eq "marking failed errors: exit non-zero, event says check by hand" "1|radarr	manual	Night of the Living Dead	1968	$REL	1990" "$([ "$RC" -ne 0 ] && echo 1 || echo 0)|$(cat "$T/event")"
run "{\"parsed_year\": 1970, \"queue\": $Q}" "$REL"
assert_eq "kept grab writes no event and posts nothing" "||" "$(cat "$T/event")|$(cat "$T/posts")|$(ls "$T/events" 2>/dev/null)"
DUBREL=Night.of.the.Living.Dead.1968.MULTi.1080p.BluRay.x264-DUB
rm -f "$T/dub-only.json"
run "{\"parsed_year\": 1968, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: no list file, kept" "" "$(cat "$T/deletes")"
dub "$DUBREL"
run "{\"parsed_year\": 1968, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: listed release (right year) is removed and blocklisted" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
assert_eq "  exit 0, log" "0|movie 5: grabbed '$DUBREL' lists only dubbed audio (German, French), not the original Persian: removed, blocklisted, searching again" "$RC|$(cat "$T/out")"
assert_eq "  event" "radarr	removed	Night of the Living Dead	1968	$DUBREL	1968" "$(cat "$T/event")"
assert_eq "  event carries reason, languages, original" "dub-only	German,French	Persian" "$(cat "$T/event2")"
dub "$DUBREL" 1 1234 '["German", "French"]'
run "{\"parsed_year\": 0, \"queue\": $QU}" "$DUBREL"
assert_eq "dub-only: also without a year in the release, unknown queue item found" "/api/v3/queue/43?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
dub "$DUBREL" 1 1234
run "{\"parsed_year\": 1968, \"queue\": $Q}" "Other.Release.1968.MULTi.1080p-X"
assert_eq "dub-only: another title is kept" "" "$(cat "$T/deletes")"
dub "$DUBREL" 31
run "{\"parsed_year\": 1968, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: entry older than 30 days is ignored" "" "$(cat "$T/deletes")"
dub "$DUBREL" 1 999
run "{\"parsed_year\": 1968, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: same release name for another title id is kept" "" "$(cat "$T/deletes")"
dub "$DUBREL" 1 1234 '["German"]'
run "{\"parsed_year\": 1968, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: an entry with fewer than 2 languages is never acted on" "" "$(cat "$T/deletes")"
echo '{nope' > "$T/dub-only.json"
run "{\"parsed_year\": 1968, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: unreadable list is ignored, exit 0" "|0" "$(cat "$T/deletes")|$RC"
dub "$DUBREL"
run "{\"parsed_year\": 1968, \"queue\": [], \"history\": $H}" "$DUBREL"
assert_eq "dub-only: not in the queue, history record marked failed" "/api/v3/history/failed/72" "$(cat "$T/posts")"
assert_eq "  event" "radarr	marked-failed	Night of the Living Dead	1968	$DUBREL	1968|dub-only	German,French	Persian" "$(cat "$T/event")|$(cat "$T/event2")"
run "{\"parsed_year\": 1968, \"queue\": [], \"history\": []}" "$DUBREL"
assert_eq "dub-only: nowhere to be found: exit non-zero, check by hand" "1|radarr	manual	Night of the Living Dead	1968	$DUBREL	1968" "$([ "$RC" -ne 0 ] && echo 1 || echo 0)|$(cat "$T/event")"
assert_eq "  stderr" "movie 5: grabbed '$DUBREL' lists only dubbed audio (German, French), not the original Persian, but it is not in the queue or in history: remove it by hand" "$(cat "$T/out")"
dub "$DUBREL"
run "{\"parsed_year\": 1990, \"queue\": $Q}" "$DUBREL"
assert_eq "listed and wrong year: removed once, reported as dub-only" "radarr	removed	Night of the Living Dead	1968	$DUBREL	1990|dub-only	German,French	Persian" "$(cat "$T/event")|$(cat "$T/event2")"
rm -f "$T/dub-only.json"

run "{}" "x" radarr_eventtype=Test
assert_eq "Test event exits 0 and calls nothing" "0|" "$RC|$(cat "$T/requests")"
run "{}" "x" radarr_eventtype=Download
assert_eq "other events are ignored" "0|" "$RC|$(cat "$T/requests")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
