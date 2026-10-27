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
            body = {"id": 4, "title": "Money Heist", "year": state["series_year"], "tvdbId": 327417}
            for k in ("firstAired", "lastAired", "status"):
                if k in state: body[k] = state[k]
            return self.reply(200, body)
        if self.command == "GET" and p == "/episode":
            return self.reply(200, [e for e in state.get("episodes", []) if str(e["seasonNumber"]) == q.get("seasonNumber")])
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
            return self.reply(500 if state.get("fail_delete") else 200, {})
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
  env -i PATH="$PATH" LP_CONFIG="$T/config.xml" GG_WAIT=2 GG_DETACH=0 GG_EVENTS="$T/events" GG_DUBONLY="$T/dub-only.json" sonarr_eventtype=Grab sonarr_series_id=4 \
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

# A season-pack release carries the season's air year (The Boys 2019: "The.Boys.2024.S04"), not the series' start year.
BOYS='"series_year": 2019, "firstAired": "2019-07-26T00:00:00Z", "lastAired": "2026-05-20T00:00:00Z", "status": "ended"'
BREL=The.Boys.2024.S04.2160p.AMZN.WEB-DL.H.265.HDR.DDP5.1-PTerWEB
run "{$BOYS, \"parsed_year\": 2024, \"queue\": $Q}" "$BREL" sonarr_release_seasonnumber=4 sonarr_release_episodeairdates=2024-06-13,2024-06-20,2024-07-18
assert_eq "season air year from the grab env (near the episode air dates) is kept" "" "$(cat "$T/deletes")"
assert_eq "  exit 0" 0 "$RC"
run "{$BOYS, \"parsed_year\": 2024, \"queue\": $Q}" "$BREL" sonarr_release_seasonnumber=4
assert_eq "no air dates in env: air year of the season from the episode API is kept" "" "$(cat "$T/deletes")"
EPS='[{"seasonNumber": 4, "airDate": "2024-06-13"}, {"seasonNumber": 4, "airDate": "2024-07-18"}, {"seasonNumber": 3, "airDate": "2022-06-03"}]'
run "{\"series_year\": 2005, \"firstAired\": \"2005-03-26T00:00:00Z\", \"lastAired\": \"2008-06-21T00:00:00Z\", \"status\": \"ended\", \"parsed_year\": 2013, \"episodes\": $EPS, \"queue\": $Q}" "Doctor.Who.2013.S04.1080p.WEB.x264-GROUP" sonarr_release_seasonnumber=4
assert_eq "episode API: 2013 is not near the 2024 season and outside the 2005-2008 run: removed" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
run "{$BOYS, \"parsed_year\": 2022, \"episodes\": $EPS, \"queue\": $Q}" "The.Boys.2022.S04.1080p.WEB.x264-GROUP" sonarr_release_seasonnumber=4
assert_eq "a year inside the series' run (2019-2026) is kept" "" "$(cat "$T/deletes")"
run "{$BOYS, \"parsed_year\": 1985, \"queue\": $Q}" "The.Boys.1985.S01.1080p.WEB.x264-GROUP" sonarr_release_seasonnumber=1 sonarr_release_episodeairdates=2019-07-26
assert_eq "same-named older series (1985, outside the run) is still removed" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
run "{\"series_year\": 2017, \"firstAired\": \"2017-05-02T00:00:00Z\", \"lastAired\": \"2018-12-03T00:00:00Z\", \"status\": \"ended\", \"parsed_year\": 2023, \"queue\": $Q}" "Money.Heist.2023.S01E01.1080p.WEB.x264-GROUP" sonarr_release_seasonnumber=1 sonarr_release_episodeairdates=2017-05-02
assert_eq "same-named newer series after an ended run is removed" "/api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(cat "$T/deletes")"
run "{\"series_year\": 2017, \"firstAired\": \"2017-05-02T00:00:00Z\", \"lastAired\": \"2018-12-03T00:00:00Z\", \"parsed_year\": 2019, \"queue\": $Q}" "Money.Heist.2019.S01E01.1080p.WEB.x264-GROUP" sonarr_release_seasonnumber=1 sonarr_release_episodeairdates=2025-05-02
assert_eq "air year +-1 of the episodes: a release one year off the air date is kept" "" "$(cat "$T/deletes")"

# A failing queue DELETE falls back to the history instead of aborting the script.
run "{\"series_year\": 2017, \"parsed_year\": 1995, \"fail_delete\": true, \"queue\": $Q, \"history\": $H}" "$REL"
assert_eq "queue DELETE fails: grabbed history record marked failed instead" "/api/v3/history/failed/80" "$(cat "$T/posts")"
assert_eq "  exit 0, event" "0|sonarr	marked-failed	Money Heist	2017	$REL	1995" "$RC|$(cat "$T/event")"
run "{\"series_year\": 2017, \"parsed_year\": 1995, \"fail_delete\": true, \"queue\": $Q, \"history\": []}" "$REL"
assert_eq "queue DELETE and history both fail: exit non-zero, check-by-hand event" "1|sonarr	manual	Money Heist	2017	$REL	1995" "$([ "$RC" -ne 0 ] && echo 1 || echo 0)|$(cat "$T/event")"

# Sonarr writes the grab history and shows the queue item only after the Connect script returns: the removal runs detached.
echo "{\"series_year\": 2017, \"parsed_year\": 1995, \"queue\": $Q}" > "$T/state.json"; : > "$T/requests"; rm -rf "$T/events"
env -i PATH="$PATH" LP_CONFIG="$T/config.xml" GG_WAIT=2 GG_LOG="$T/guard.log" GG_EVENTS="$T/events" sonarr_eventtype=Grab sonarr_series_id=4 \
  sonarr_release_title="$REL" sonarr_download_id=abc bash "$GUARD" > "$T/out" 2>&1; RC=$?
assert_eq "detached: script returns at once, nothing deleted yet" "0|" "$RC|$(grep '^DELETE ' "$T/requests")"
for _ in $(seq 50); do grep -q '^DELETE ' "$T/requests" && break; sleep 0.2; done
assert_eq "  background removal deletes the queue item" "DELETE /api/v3/queue/42?removeFromClient=true&blocklist=true&skipRedownload=false" "$(grep '^DELETE ' "$T/requests")"
sleep 0.5
assert_eq "  background log" "series 4: grabbed '$REL' is from 1995, not 2017: removed, blocklisted, searching again" "$(tail -1 "$T/guard.log")"

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
