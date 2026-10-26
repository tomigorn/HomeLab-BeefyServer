#!/usr/bin/env bash
# Runs scripts/language-policy-hook.sh against a fake Radarr/Sonarr + Bazarr API (python http.server).
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
import http.server, json, os, sys, urllib.parse
T = sys.argv[1]
PROFILES = [{"id": 3, "name": "Anime - Bluray + WEB 1080p"},
            {"id": 7, "name": "4K HDR - UHD Bluray + WEB (1080p fallback)"},
            {"id": 10, "name": "4K HDR - UHD Bluray + WEB (1080p fallback) - English original"},
            {"id": 11, "name": "Any quality - old & rare (upgrades to 4K)"},
            {"id": 12, "name": "Any quality - old & rare (upgrades to 4K) - English original"},
            {"id": 13, "name": "Swiss German (Mundart) only - any quality"},
            {"id": 17, "name": "4K HDR - WEB-2160p (1080p fallback)"},
            {"id": 20, "name": "4K HDR - WEB-2160p (1080p fallback) - English original"}]
TAGS = [{"id": 1, "label": "lang-es"}, {"id": 21, "label": "audio-english"}, {"id": 22, "label": "audio-german"},
        {"id": 23, "label": "audio-spanish"}, {"id": 24, "label": "audio-swiss-german"}, {"id": 31, "label": "subs-french"}, {"id": 32, "label": "subs-italian"},
        {"id": 40, "label": "bz-es-it"}, {"id": 60, "label": "old-rare"}]
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
        state = json.load(open(os.path.join(T, "state.json")))
        if self.path.startswith("/bazarr/api/"):
            if self.headers.get("X-API-KEY") != "bzkey":
                return self.reply(401)
            if self.command == "GET" and self.path == "/bazarr/api/system/languages/profiles":
                return self.reply(200, state.get("bazarr", []))
            if self.command == "POST" and self.path == "/bazarr/api/system/settings":
                form = urllib.parse.parse_qs(body)
                with open(os.path.join(T, "bazarr_profiles.json"), "w") as f:
                    f.write(form["languages-profiles"][0])
                return self.reply(204)
            return self.reply(404)
        if self.headers.get("X-Api-Key") != "testkey":
            return self.reply(401)
        p = self.path.removeprefix("/base/api/v3")
        if self.command == "GET" and p == "/qualityprofile":
            return self.reply(200, state.get("profiles", PROFILES))
        if self.command == "GET" and p == "/qualityprofile/13":
            if state.get("swiss_no_language"):
                return self.reply(200, {"id": 13, "name": PROFILES[5]["name"]})
            return self.reply(200, {"id": 13, "name": PROFILES[5]["name"], "language": state.get("swiss_language", {"id": -1, "name": "Any"})})
        if self.command == "PUT" and p == "/qualityprofile/13":
            return self.reply(202, json.loads(body))
        if self.command == "GET" and p == "/tag":
            return self.reply(200, state.get("tags", TAGS))
        if self.command == "POST" and p == "/tag":
            return self.reply(201, {"id": 99, "label": json.loads(body)["label"]})
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
echo bzkey > "$T/bazarr.apikey"

# run <state json> <env...>: runs the hook, leaves the PUTs in $T/puts and the exit code in $RC
run() {
  echo "$1" > "$T/state.json"; shift
  : > "$T/requests"
  rm -f "$T/bazarr_profiles.json"
  env -i PATH="$PATH" LP_CONFIG="${CONFIG:-$T/config.xml}" LP_YEAR=2026 LP_LANGUAGES="$HERE/../scripts/languages.tsv" \
    LP_BAZARR_URL="http://127.0.0.1:$PORT/bazarr" LP_BAZARR_KEY="${BZKEY-$T/bazarr.apikey}" "$@" bash "$HOOK" > "$T/out" 2>&1; RC=$?
  grep '^PUT ' "$T/requests" | cut -d' ' -f2- > "$T/puts"
  grep '^POST /base/api/v3/tag ' "$T/requests" | cut -d' ' -f3- > "$T/newtags"
}
movie() { echo "{\"$1\": {\"id\": $1, \"title\": \"T\", \"qualityProfileId\": $2, \"originalLanguage\": $3}}"; }
# film <id> <profile> <language> <year> <tmdb votes>
film() { echo "{\"$1\": {\"id\": $1, \"title\": \"T\", \"qualityProfileId\": $2, \"originalLanguage\": $3, \"year\": $4, \"ratings\": {\"imdb\": {\"votes\": 900000}, \"tmdb\": {\"votes\": $5}}}}"; }
# show <id> <profile> <language> <year> <votes>
show() { echo "{\"$1\": {\"id\": $1, \"title\": \"T\", \"qualityProfileId\": $2, \"originalLanguage\": $3, \"year\": $4, \"ratings\": {\"votes\": $5}}}"; }
put() { echo "/base/api/v3/$1/editor {\"$2\":[$3],\"qualityProfileId\":$4}"; }
# tagged <id> <profile> <language> <year> <tag ids>
tagged() { echo "{\"$1\": {\"id\": $1, \"title\": \"T\", \"qualityProfileId\": $2, \"originalLanguage\": $3, \"year\": $4, \"ratings\": {\"votes\": 5000, \"tmdb\": {\"votes\": 5000}}, \"tags\": [$5]}}"; }
tagput() { echo "/base/api/v3/$1/editor {\"$2\":[$3],\"tags\":[$4],\"applyTags\":\"$5\"}"; }
# moved <kind> <ids field> <id> <profile>: profile move plus the old-rare tag (60) every Any quality title gets
moved() { put "$@"; tagput "$1" "$2" "$3" 60 add; }
bzprofile() { jq -c --arg t "$1" '.[] | select(.tag == $t) | [.name, ([.items[] | .language + (if .forced == "True" then "+f" else "" end)] | join(","))]' "$T/bazarr_profiles.json" 2>/dev/null; }
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
assert_eq "English original on Any quality moves to its twin" "$(moved movie movieIds 5 12)" "$(cat "$T/puts")"
run "$(movie 5 11 "$ES")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "Spanish original on Any quality stays, gets the old-rare tag" "$(tagput movie movieIds 5 60 add)" "$(cat "$T/puts")"
run "$(movie 5 12 "$ES")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "Spanish original on the Any twin moves back to Any" "$(moved movie movieIds 5 11)" "$(cat "$T/puts")"

echo "Radarr: old and niche films"
run "$(film 5 7 "$ES" 2014 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "film from 2014 (now - 12) on main moves to Any quality" "$(moved movie movieIds 5 11)" "$(cat "$T/puts")"
run "$(film 5 7 "$EN" 1968 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "old English film on main moves to the Any twin" "$(moved movie movieIds 5 12)" "$(cat "$T/puts")"
run "$(film 5 10 "$EN" 1968 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "old English film on the twin moves to the Any twin" "$(moved movie movieIds 5 12)" "$(cat "$T/puts")"
run "$(film 5 7 "$ES" 2015 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "popular film from 2015 stays on main" "" "$(cat "$T/puts")"
run "$(film 5 7 "$EN" 2020 99)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "niche film (99 TMDB votes) moves to the Any twin" "$(moved movie movieIds 5 12)" "$(cat "$T/puts")"
run "$(film 5 7 "$ES" 2020 100)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "100 TMDB votes is not niche" "" "$(cat "$T/puts")"
run "$(film 5 7 "$ES" 2025 10)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "few votes on a film from last year is not niche" "" "$(cat "$T/puts")"
run "$(film 5 7 "$ES" 2024 10)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "few votes on a film 2 years old is niche" "$(moved movie movieIds 5 11)" "$(cat "$T/puts")"
run "$(film 5 7 "$ES" 0 0)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "unknown year (0) stays on main" "" "$(cat "$T/puts")"
run "{\"5\": {\"id\": 5, \"qualityProfileId\": 7, \"originalLanguage\": $ES, \"year\": 2020, \"ratings\": {}}}" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "no TMDB rating on an older film counts as niche" "$(moved movie movieIds 5 11)" "$(cat "$T/puts")"
run "$(film 5 3 "$ES" 1968 5)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "old niche film on another profile (anime) stays" "" "$(cat "$T/puts")"
run "$(film 5 11 "$ES" 2025 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "new popular film put on Any quality by the requester stays, gets the old-rare tag" "$(tagput movie movieIds 5 60 add)" "$(cat "$T/puts")"
run "$(film 5 7 "$EN" 1968 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "  the log line names the reason" "movie 5: profile 7 -> 12 (old: 1968, English original)
movie 5: tag old-rare added (Any quality)" "$(cat "$T/out")"
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
assert_eq "English series on Any quality moves to its twin" "$(moved series seriesIds 8 12)" "$(cat "$T/puts")"
run "$(show 8 17 "$ES" 2014 500000)" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "series from 2014 on main moves to Any quality" "$(moved series seriesIds 8 11)" "$(cat "$T/puts")"
run "$(show 8 17 "$EN" 2015 500000)" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "popular English series from 2015 moves to the twin only" "$(put series seriesIds 8 20)" "$(cat "$T/puts")"
run "$(show 8 20 "$EN" 2018 999)" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "niche English series (999 votes) moves to the Any twin" "$(moved series seriesIds 8 12)" "$(cat "$T/puts")"
run "$(show 8 17 "$ES" 2018 1000)" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "1000 votes is not niche" "" "$(cat "$T/puts")"
run "$(show 8 3 "$ES" 1990 5)" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "old niche anime-profile series stays" "" "$(cat "$T/puts")"
run "{}" sonarr_eventtype=Test
assert_eq "Test event exits 0 and calls nothing" "0|" "$RC|$(cat "$T/requests")"

echo "Tags: audio-xx and subs-xx"
JA='{"id": 8, "name": "Japanese"}'; DE='{"id": 4, "name": "German"}'; HI='{"id": 26, "name": "Hindi"}'
BZ_EXISTING='[{"profileId": 1, "name": "English + German", "tag": null, "items": []}, {"profileId": 2, "name": "English + German + Spanish", "tag": "lang-es", "items": []}]'
run "$(tagged 5 7 "$ES" 2020 "1,22")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "audio-german on a Spanish film: nothing changes (tags present)" "0||" "$RC|$(cat "$T/puts")|$(cat "$T/newtags")"
run "$(tagged 5 7 "$EN" 2020 "21")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "audio-english on an English film is dropped (original audio is required anyway)" "$(put movie movieIds 5 10)
$(tagput movie movieIds 5 21 remove)" "$(cat "$T/puts")"
run "$(tagged 5 7 "$ES" 2020 "1,23")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "audio-spanish on a Spanish film is dropped" "$(tagput movie movieIds 5 23 remove)" "$(cat "$T/puts")"
run "$(tagged 5 7 "$EN" 2020 "31")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "subs-french on an English film creates tag bz-fr" '{"label":"bz-fr"}' "$(cat "$T/newtags")"
assert_eq "  and adds it to the film" "$(put movie movieIds 5 10)
$(tagput movie movieIds 5 99 add)" "$(cat "$T/puts")"
assert_eq "  and creates the Bazarr profile for it" '["English + German + French (requested)","en,de,fr,en+f,de+f"]' "$(bzprofile bz-fr)"
run "{\"tags\": [{\"id\": 31, \"label\": \"subs-french\"}, {\"id\": 50, \"label\": \"bz-es-fr\"}], \"bazarr\": $BZ_EXISTING, $(tagged 5 7 "$ES" 2020 "1,31" | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "subs-french on a Spanish film: existing tag bz-es-fr is added" "$(tagput movie movieIds 5 50 add)" "$(cat "$T/puts")"
assert_eq "  no new tag" "" "$(cat "$T/newtags")"
assert_eq "  Bazarr profile: original + requested" '["English + German + Spanish + French (requested)","en,de,es,fr,en+f,de+f"]' "$(bzprofile bz-es-fr)"
assert_eq "  existing Bazarr profiles are kept" '["English + German + Spanish",""]' "$(bzprofile lang-es)"
run "{\"bazarr\": [{\"profileId\": 7, \"name\": \"English + German + Spanish + Italian\", \"tag\": \"bz-es-it\", \"items\": []}], $(tagged 5 7 "$ES" 2020 "1,32" | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "Bazarr profile already there: not posted again" "missing" "$([ -e "$T/bazarr_profiles.json" ] && echo posted || echo missing)"
assert_eq "  tag bz-es-it added" "$(tagput movie movieIds 5 40 add)" "$(cat "$T/puts")"
run "$(tagged 5 7 "$JA" 2020 "32,31")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "two subs tags on a Japanese film: bz-ja-fr-it" '{"label":"bz-ja-fr-it"}' "$(cat "$T/newtags")"
assert_eq "  profile" '["English + German + Japanese + French + Italian (requested)","en,de,ja,fr,it,en+f,de+f"]' "$(bzprofile bz-ja-fr-it)"
run "$(tagged 5 7 "$DE" 2020 "31")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "German film: German is always there, bz-fr" '{"label":"bz-fr"}' "$(cat "$T/newtags")"
run "$(tagged 5 7 "$HI" 2020 "31")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "original without subtitle support (Hindi): bz-fr" '{"label":"bz-fr"}' "$(cat "$T/newtags")"
run "$(tagged 5 7 "$ES" 1968 "1,31")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "old film with subs-french: profile, old-rare and bz tag all change" "$(moved movie movieIds 5 11)
$(tagput movie movieIds 5 99 add)" "$(cat "$T/puts")"
run "$(tagged 5 7 "$ES" 1968 "1,31")" radarr_eventtype=MovieAdded radarr_movie_id=5 LP_TAGS_ONLY=1
assert_eq "LP_TAGS_ONLY=1: only the tag changes" "$(tagput movie movieIds 5 99 add)" "$(cat "$T/puts")"
BZKEY="$T/nokey" run "$(tagged 5 7 "$ES" 1968 "1,31")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "no Bazarr key: profile and old-rare still change, exit non-zero" "$(moved movie movieIds 5 11)|1" "$(cat "$T/puts")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)"
run "{\"tags\": [{\"id\": 33, \"label\": \"subs-spanish\"}], $(tagged 5 7 "$ES" 2020 "33" | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "subs-spanish on a Spanish film keeps the Spanish profile: bz-es" '{"label":"bz-es"}' "$(cat "$T/newtags")"
run "{\"tags\": [{\"id\": 31, \"label\": \"subs-french\"}, {\"id\": 51, \"label\": \"bz-fr\"}], \"bazarr\": [{\"profileId\": 3, \"name\": \"x\", \"tag\": \"bz-fr\", \"items\": []}], $(tagged 5 10 "$EN" 2020 "31,51" | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5 LP_TAGS_ONLY=1
assert_eq "already tagged bz-fr: nothing to do" "" "$(cat "$T/puts")$(cat "$T/newtags")"
run "{\"tags\": [{\"id\": 32, \"label\": \"subs-italian\"}, {\"id\": 51, \"label\": \"bz-fr\"}, {\"id\": 52, \"label\": \"bz-it\"}], \"bazarr\": [{\"profileId\": 3, \"name\": \"x\", \"tag\": \"bz-it\", \"items\": []}], $(tagged 5 10 "$EN" 2020 "32,51" | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5 LP_TAGS_ONLY=1
assert_eq "subs changed from fr to it: stale bz-fr removed, bz-it added" "$(tagput movie movieIds 5 51 remove)
$(tagput movie movieIds 5 52 add)" "$(cat "$T/puts")"
run "$(tagged 8 17 "$ES" 2020 "1,31")" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "Sonarr: subs-french on a Spanish series" "$(tagput series seriesIds 8 99 add)" "$(cat "$T/puts")"
assert_eq "  profile" '["English + German + Spanish + French (requested)","en,de,es,fr,en+f,de+f"]' "$(bzprofile bz-es-fr)"

echo "Swiss German: audio-swiss-german moves to its own profile"
run "$(tagged 5 7 "$DE" 2020 "24")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "German film on main moves to the Swiss German profile" "$(put movie movieIds 5 13)" "$(cat "$T/puts")"
assert_eq "  the log line names the tag" "movie 5: profile 7 -> 13 (audio-swiss-german)" "$(cat "$T/out")"
run "$(tagged 5 7 "$EN" 1968 "24")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "old English film (Swiss German dub): Swiss German profile, not the Any twin" "$(put movie movieIds 5 13)" "$(cat "$T/puts")"
run "$(tagged 5 3 "$JA" 2020 "24")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "a profile picked in Seerr (anime) is overruled" "$(put movie movieIds 5 13)" "$(cat "$T/puts")"
run "$(tagged 5 13 "$DE" 2020 "24")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "already on the Swiss German profile: nothing changes" "|movie 5: profile 13 kept" "$(cat "$T/puts")|$(cat "$T/out")"
run "$(tagged 5 11 "$DE" 2004 "24")" radarr_eventtype=MovieAdded radarr_movie_id=5 LP_TAGS_ONLY=1
assert_eq "LP_TAGS_ONLY=1 (tag added later): moves too" "$(put movie movieIds 5 13)" "$(cat "$T/puts")"
run "$(tagged 5 7 "$DE" 2020 "22")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "audio-german is not Swiss German: stays on main (the tag is dropped, German is the original)" "$(tagput movie movieIds 5 22 remove)" "$(cat "$T/puts")"
run "$(tagged 5 7 "$EN" 2020 "24,31")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "audio-swiss-german + subs-french: profile and bz-fr" "$(put movie movieIds 5 13)
$(tagput movie movieIds 5 99 add)" "$(cat "$T/puts")"
run "{\"swiss_language\": {\"id\": -2, \"name\": \"Original\"}, $(tagged 5 7 "$EN" 2020 "24" | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "Swiss German profile language reset to Original (by a Recyclarr update): back to Any before the search" '/base/api/v3/movie/editor {"movieIds":[5],"qualityProfileId":13}
/base/api/v3/qualityprofile/13 {"id":13,"name":"Swiss German (Mundart) only - any quality","language":{"id":-1,"name":"Any"}}' "$(cat "$T/puts")"
assert_eq "  log line" "movie 5: profile 7 -> 13 (audio-swiss-german)
profile 13: language Original -> Any" "$(cat "$T/out")"
run "$(film 5 13 "$DE" 2020 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "added on the Swiss German profile without the tag: tag added (it is the hard filter)" "$(tagput movie movieIds 5 24 add)" "$(cat "$T/puts")"
assert_eq "  log line" "movie 5: tag audio-swiss-german added (Swiss German profile)" "$(cat "$T/out")"
run "$(film 5 13 "$DE" 2020 5000)" radarr_eventtype=MovieAdded radarr_movie_id=5 LP_TAGS_ONLY=1
assert_eq "  also with LP_TAGS_ONLY=1" "$(tagput movie movieIds 5 24 add)" "$(cat "$T/puts")"
# Sonarr profiles have no language field (Sonarr 4), so nothing to put back to Any there.
NOLANG='"swiss_no_language": true'
run "{$NOLANG, $(tagged 8 17 "$EN" 2020 "24" | sed 's/^{//')" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "Sonarr: English series on main moves to the Swiss German profile, no profile language to fix" "$(put series seriesIds 8 13)" "$(cat "$T/puts")"
assert_eq "  log line" "series 8: profile 17 -> 13 (audio-swiss-german)" "$(cat "$T/out")"
run "{$NOLANG, $(tagged 8 3 "$JA" 1990 "24,31" | sed 's/^{//')" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "Sonarr: anime profile overruled, subs-french still handled" "$(put series seriesIds 8 13)
$(tagput series seriesIds 8 99 add)" "$(cat "$T/puts")"
run "{$NOLANG, $(show 8 13 "$DE" 2020 5000 | sed 's/^{//')" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "Sonarr: added on the Swiss German profile without the tag: tag added" "$(tagput series seriesIds 8 24 add)" "$(cat "$T/puts")"
run "{$NOLANG, $(tagged 8 11 "$DE" 2004 "24" | sed 's/^{//')" sonarr_eventtype=SeriesAdd sonarr_series_id=8 LP_TAGS_ONLY=1
assert_eq "Sonarr: LP_TAGS_ONLY=1 (tag added later) moves too" "$(put series seriesIds 8 13)" "$(cat "$T/puts")"
run "{\"profiles\": [{\"id\": 17, \"name\": \"4K HDR - WEB-2160p (1080p fallback)\"}, {\"id\": 20, \"name\": \"4K HDR - WEB-2160p (1080p fallback) - English original\"}], $(tagged 8 17 "$EN" 2020 "24" | sed 's/^{//')" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "no Swiss German profile (Recyclarr not synced yet): the usual twin move" "$(put series seriesIds 8 20)" "$(cat "$T/puts")"

echo "old-rare tag: only titles on Any quality (and its twin) carry it"
run "$(tagged 5 11 "$ES" 2004 "60")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "already tagged on Any quality: nothing changes" "|movie 5: profile 11 kept" "$(cat "$T/puts")|$(cat "$T/out")"
run "$(tagged 5 7 "$ES" 2020 "60")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "tag on a title that stays on main is removed" "$(tagput movie movieIds 5 60 remove)" "$(cat "$T/puts")"
assert_eq "  log line" "movie 5: profile 7 kept
movie 5: tag old-rare removed (not on Any quality)" "$(cat "$T/out")"
run "$(tagged 5 11 "$DE" 2004 "24,60")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "moved off Any quality to Swiss German: tag removed" "$(put movie movieIds 5 13)
$(tagput movie movieIds 5 60 remove)" "$(cat "$T/puts")"
run "$(tagged 5 3 "$JA" 1990 "60")" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "tag on another profile (anime) is removed" "$(tagput movie movieIds 5 60 remove)" "$(cat "$T/puts")"
run "$(tagged 5 12 "$EN" 2004 "")" radarr_eventtype=MovieAdded radarr_movie_id=5 LP_TAGS_ONLY=1
assert_eq "LP_TAGS_ONLY=1 (existing title) on the Any twin: tag added" "$(tagput movie movieIds 5 60 add)" "$(cat "$T/puts")"
run "$(tagged 5 7 "$ES" 1968 "60")" radarr_eventtype=MovieAdded radarr_movie_id=5 LP_TAGS_ONLY=1
assert_eq "LP_TAGS_ONLY=1 on main (not moved): tag removed" "$(tagput movie movieIds 5 60 remove)" "$(cat "$T/puts")"
run "{\"tags\": [{\"id\": 24, \"label\": \"audio-swiss-german\"}], $(film 5 7 "$ES" 2004 5000 | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "no old-rare tag in the app yet: created" '{"label":"old-rare"}' "$(cat "$T/newtags")"
assert_eq "  and added" "$(put movie movieIds 5 11)
$(tagput movie movieIds 5 99 add)" "$(cat "$T/puts")"
run "{\"tags\": [{\"id\": 24, \"label\": \"audio-swiss-german\"}], $(film 5 7 "$ES" 2020 5000 | sed 's/^{//')" radarr_eventtype=MovieAdded radarr_movie_id=5
assert_eq "no old-rare tag in the app and not on Any: nothing created" "|" "$(cat "$T/puts")|$(cat "$T/newtags")"
run "$(tagged 8 17 "$ES" 2018 "60")" sonarr_eventtype=SeriesAdd sonarr_series_id=8
assert_eq "Sonarr: tag on a series that stays on main is removed" "$(tagput series seriesIds 8 60 remove)" "$(cat "$T/puts")"
run "$(tagged 8 11 "$ES" 1990 "")" sonarr_eventtype=SeriesAdd sonarr_series_id=8 LP_TAGS_ONLY=1
assert_eq "Sonarr: LP_TAGS_ONLY=1 on Any quality: tag added" "$(tagput series seriesIds 8 60 add)" "$(cat "$T/puts")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
