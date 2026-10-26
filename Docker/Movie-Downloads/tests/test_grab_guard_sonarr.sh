#!/usr/bin/env bash
# Runs scripts/grab-guard.sh against a fake Sonarr API (python http.server), plus
# one Radarr-path check that the shared guard still removes a wrong-year movie.
# Usage: tests/test_grab_guard_sonarr.sh   (needs bash, curl, jq, python3)
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
            return self.reply(200, {"title": q["title"], "parsedMovieInfo": {"year": state["parsed_year"]}, "parsedEpisodeInfo": {"seriesTitleInfo": {"year": state["parsed_year"]}}})
        if self.command == "GET" and p == "/series/4":
            return self.reply(200, {"id": 4, "title": "Money Heist", "year": state["series_year"], "tvdbId": 327417})
        if self.command == "GET" and p == "/movie/7":
            return self.reply(200, {"id": 7, "title": "Night of the Living Dead", "year": 1968, "secondaryYear": None})
        if self.command == "GET" and p == "/queue":
            recs = state["queue"]
            if "seriesIds" in q:
                recs = [r for r in recs if str(r.get("seriesId")) == q["seriesIds"]]
            if "true" not in (q.get("includeUnknownSeriesItems"), q.get("includeUnknownMovieItems")):
                recs = [r for r in recs if r.get("seriesId") is not None]
            return self.reply(200, {"records": recs})
        if self.command == "DELETE" and p.startswith("/queue/"):
            return self.reply(200, {})
        if self.command == "GET" and p == "/history/series":
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
  env -i PATH="$PATH" LP_CONFIG="$T/config.xml" GG_WAIT=2 GG_EVENTS="$T/events" GG_DUBONLY="$T/dub-only.json" sonarr_eventtype=Grab sonarr_series_id=4 \
    sonarr_release_title="$title" sonarr_download_id=abc "$@" bash "$GUARD" > "$T/out" 2>&1; RC=$?
  grep '^DELETE ' "$T/requests" | cut -d' ' -f2- > "$T/deletes"
  grep '^POST ' "$T/requests" | cut -d' ' -f2- > "$T/posts"
  cat "$T"/events/*.json 2>/dev/null | jq -r '[.app, .action, .title, .year, .release, .releaseYear] | @tsv' > "$T/event"
  cat "$T"/events/*.json 2>/dev/null | jq -r '[.reason // "", (.languages // [] | join(",")), .original // ""] | @tsv' > "$T/event2"
}
dub() { jq -n --arg t "$1" --argjson at "$(( $(date +%s) - ${2:-1} * 86400 ))" --argjson id "${3:-327417}" \
  '[{guid: "https://x/details/abc", title: $t, indexer: "NzbPlanet", langs: ["French", "German"], original: "Spanish", titleId: $id, at: $at}]' > "$T/dub-only.json"; }
Q='[{"id": 41, "downloadId": "other"}, {"id": 42, "downloadId": "abc"}]'

echo "grab-guard (Sonarr)"
run "{\"series_year\": 2017, \"parsed_year\": 1995, \"queue\": $Q}" "Money.Heist.1995.S01E01.1080p.WEB.x264-GROUP"
assert_eq "release of a same-named older series is removed and blocklisted" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
assert_eq "  exit 0" 0 "$RC"
assert_eq "  log" "series 4: grabbed 'Money.Heist.1995.S01E01.1080p.WEB.x264-GROUP' is from 1995, not 2017: removed, blocklisted, searching again" "$(cat "$T/out")"

run "{\"series_year\": 2017, \"parsed_year\": 1995, \"queue\": [{\"id\": 43, \"downloadId\": \"abc\", \"seriesId\": null}]}" "Money.Heist.1995.S01E01.1080p.WEB.x264-GROUP"
assert_eq "queue item Sonarr could not map to a series (seriesId null) is found and removed" "/api/v3/queue/43?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"

run "{\"series_year\": 2017, \"parsed_year\": 2017, \"queue\": $Q}" "Money.Heist.2017.S01E01.1080p.WEB.x264-GROUP"
assert_eq "same series year is kept" "" "$(cat "$T/deletes")"

run "{\"series_year\": 2005, \"parsed_year\": 2005, \"queue\": $Q}" "Doctor.Who.2005.S14E01.1080p.WEB.x264-GROUP"
assert_eq "old-but-current series: late season, title year == firstAired year, kept" "" "$(cat "$T/deletes")"

run "{\"series_year\": 2017, \"parsed_year\": 2019, \"queue\": $Q}" "Money.Heist.2019.S01E01.1080p.WEB.x264-GROUP"
assert_eq "two years off is kept" "" "$(cat "$T/deletes")"

run "{\"series_year\": 2017, \"parsed_year\": 2020, \"queue\": $Q}" "Money.Heist.2020.S01E01.1080p.WEB.x264-GROUP"
assert_eq "three years off is removed" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"

run "{\"series_year\": 2017, \"parsed_year\": 0, \"queue\": $Q}" "Money.Heist.S01E01.1080p.WEB.x264-GROUP"
assert_eq "no year in the release name is kept" "" "$(cat "$T/deletes")"

run "{\"series_year\": 0, \"parsed_year\": 1995, \"queue\": $Q}" "Money.Heist.1995.S01E01.1080p.WEB.x264-GROUP"
assert_eq "unknown series year is kept" "" "$(cat "$T/deletes")"

run "{\"series_year\": 2017, \"parsed_year\": 1995, \"queue\": []}" "Money.Heist.1995.S01E01.1080p.WEB.x264-GROUP"
assert_eq "not in the queue after GG_WAIT: nothing deleted, exit non-zero" "|1" "$(cat "$T/deletes")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)"

H='[{"id": 80, "eventType": "grabbed", "downloadId": "abc"}, {"id": 81, "eventType": "grabbed", "downloadId": "abc"}]'
REL=Money.Heist.1995.S01E01.1080p.WEB.x264-GROUP
run "{\"series_year\": 2017, \"parsed_year\": 1995, \"queue\": $Q}" "$REL"
assert_eq "event for a queue removal" "sonarr	removed	Money Heist	2017	$REL	1995" "$(cat "$T/event")"
run "{\"series_year\": 2017, \"parsed_year\": 1995, \"queue\": [], \"history\": $H}" "$REL"
assert_eq "not in the queue: grabbed history record marked failed once" "/api/v3/history/failed/80" "$(cat "$T/posts")"
assert_eq "  exit 0, event" "0|sonarr	marked-failed	Money Heist	2017	$REL	1995" "$RC|$(cat "$T/event")"
run "{\"series_year\": 2017, \"parsed_year\": 1995, \"queue\": [], \"history\": []}" "$REL"
assert_eq "no history record: exit non-zero, check by hand" "1|sonarr	manual	Money Heist	2017	$REL	1995" "$([ "$RC" -ne 0 ] && echo 1 || echo 0)|$(cat "$T/event")"

DUBREL=Money.Heist.S01.MULTi.1080p.NF.WEB-DL.DDP5.1.x264-DUB
run "{\"series_year\": 2017, \"parsed_year\": 2017, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: no list file, kept" "" "$(cat "$T/deletes")"
dub "$DUBREL"
run "{\"series_year\": 2017, \"parsed_year\": 0, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: listed season pack is removed and blocklisted" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
assert_eq "  log" "series 4: grabbed '$DUBREL' lists only dubbed audio (French, German), not the original Spanish: removed, blocklisted, searching again" "$(cat "$T/out")"
assert_eq "  event" "sonarr	removed	Money Heist	2017	$DUBREL	0|dub-only	French,German	Spanish" "$(cat "$T/event")|$(cat "$T/event2")"
dub "$DUBREL" 1 111
run "{\"series_year\": 2017, \"parsed_year\": 0, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: another tvdb id is kept" "" "$(cat "$T/deletes")"
dub "$DUBREL" 40
run "{\"series_year\": 2017, \"parsed_year\": 0, \"queue\": $Q}" "$DUBREL"
assert_eq "dub-only: expired entry is ignored" "" "$(cat "$T/deletes")"
rm -f "$T/dub-only.json"

run "{}" "x" sonarr_eventtype=Test
assert_eq "Test event exits 0 and calls nothing" "0|" "$RC|$(cat "$T/requests")"

run "{}" "x" sonarr_eventtype=Download
assert_eq "other events are ignored" "0|" "$RC|$(cat "$T/requests")"

# The shared guard still removes a wrong-year Radarr grab (Radarr path unchanged).
echo "$T/state.json" >/dev/null
echo "{\"series_year\": 2017, \"parsed_year\": 1990, \"queue\": $Q}" > "$T/state.json"
: > "$T/requests"
env -i PATH="$PATH" LP_CONFIG="$T/config.xml" GG_WAIT=2 GG_EVENTS="$T/events" radarr_eventtype=Grab radarr_movie_id=7 \
  radarr_release_title="Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP" radarr_download_id=abc bash "$GUARD" > "$T/out" 2>&1; RC=$?
grep '^DELETE ' "$T/requests" | cut -d' ' -f2- > "$T/deletes"
assert_eq "Radarr grab still routed and removed by the shared guard" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
assert_eq "  log names the movie" "movie 7: grabbed 'Night.of.the.Living.Dead.1990.2160p.UHD.BluRay.x265-GROUP' is from 1990, not 1968: removed, blocklisted, searching again" "$(cat "$T/out")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
