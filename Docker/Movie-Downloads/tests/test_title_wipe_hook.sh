#!/usr/bin/env bash
# Runs scripts/title-wipe-hook.sh against a fake Radarr/Sonarr + qBittorrent + SABnzbd + Seerr API (python http.server).
# Usage: tests/test_title_wipe_hook.sh   (needs bash, curl, jq, python3, flock)
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK="$HERE/../scripts/title-wipe-hook.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

cat > "$T/fake.py" <<'PY'
import http.server, json, os, sys, urllib.parse
T = sys.argv[1]
CLIENTS = {"radarr": [{"name": "qBittorrent", "implementation": "QBittorrent", "fields": [{"name": "movieCategory", "value": "radarr"}]},
                      {"name": "SABnzbd", "implementation": "Sabnzbd", "fields": [{"name": "movieCategory", "value": "movies"}]}],
           "sonarr": [{"name": "qBittorrent", "implementation": "QBittorrent", "fields": [{"name": "tvCategory", "value": "sonarr"}]},
                      {"name": "SABnzbd", "implementation": "Sabnzbd", "fields": [{"name": "tvCategory", "value": "tv"}]}]}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        with open(os.path.join(T, "requests"), "a") as f:
            f.write(f"{self.command} {u.path} {json.dumps(q, sort_keys=True) if q else ''} {body}\n")
        state = json.load(open(os.path.join(T, "state.json")))
        if u.path.startswith("/qbit/api/v2/"):
            p = u.path.removeprefix("/qbit/api/v2")
            if self.command == "GET" and p == "/torrents/info":
                return self.reply(200, state.get("torrents", []))
            if self.command == "POST" and p == "/torrents/delete":
                return self.reply(200)
            return self.reply(404)
        if u.path == "/sab/api":
            if q.get("apikey") != "sabkey":
                return self.reply(403, {"error": "API Key Incorrect"})
            if q.get("name") == "delete":
                return self.reply(200, {"status": True})
            if q.get("mode") == "history":
                return self.reply(200, {"history": {"slots": state.get("sab_history", [])}})
            if q.get("mode") == "queue":
                return self.reply(200, {"queue": {"slots": state.get("sab_queue", [])}})
            return self.reply(404)
        if u.path.startswith("/seerr/api/v1/"):
            if self.headers.get("X-Api-Key") != "seerrkey":
                return self.reply(403)
            p = u.path.removeprefix("/seerr/api/v1")
            media = state.get("seerr", [])
            if self.command == "GET" and p == "/media":
                take, skip = int(q.get("take", 20)), int(q.get("skip", 0))
                return self.reply(200, {"pageInfo": {"results": len(media)}, "results": media[skip:skip + take]})
            if self.command == "DELETE" and p.startswith("/media/"):
                return self.reply(204)
            return self.reply(404)
        if self.headers.get("X-Api-Key") != "testkey":
            return self.reply(401)
        p = u.path.removeprefix("/base/api/v3")
        app = state.get("app", "radarr")
        if self.command == "GET" and p == "/downloadclient":
            return self.reply(200, CLIENTS[app])
        if self.command == "GET" and p in ("/movie", "/series"):
            return self.reply(200, state.get("library", []))
        if self.command == "GET" and p == "/history":
            page, size = int(q.get("page", 1)), int(q.get("pageSize", 10))
            recs = state.get("history", [])
            return self.reply(200, {"page": page, "pageSize": size, "totalRecords": len(recs), "records": recs[(page - 1) * size:page * size]})
        self.reply(404)
    do_GET = do_POST = do_DELETE = handle_any
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_address[1]))
s.serve_forever()
PY
python3 "$T/fake.py" "$T" & SRV=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
PORT=$(cat "$T/port")
printf '<Config>\n  <Port>%s</Port>\n  <UrlBase>/base</UrlBase>\n  <ApiKey>testkey</ApiKey>\n</Config>\n' "$PORT" > "$T/config.xml"
echo sabkey > "$T/sab.apikey"; echo seerrkey > "$T/seerr.apikey"

# run <fake state json> <env...>: runs the hook against $T/wipe.tsv; qBittorrent deletes in $T/qdel, SABnzbd deletes in $T/sdel, Seerr deletes in $T/mdel, exit code in $RC
run() {
  echo "$1" > "$T/state.json"; shift
  : > "$T/requests"
  env -i PATH="$PATH" TW_CONFIG="$T/config.xml" TW_STATE="$T/wipe.tsv" TW_QBIT_URL="http://127.0.0.1:$PORT/qbit" \
    TW_SAB_URL="http://127.0.0.1:$PORT/sab" TW_SAB_KEY="${SABKEY-$T/sab.apikey}" TW_SEERR_URL="http://127.0.0.1:$PORT/seerr" \
    TW_SEERR_KEY="${SEERRKEY-$T/seerr.apikey}" TW_SEERR_WAIT=0 TW_FOREGROUND=1 "$@" bash "$HOOK" ${ARGS:-} > "$T/out" 2>&1; RC=$?
  grep '^POST /qbit/api/v2/torrents/delete' "$T/requests" | cut -d' ' -f4- > "$T/qdel"
  grep '"name": "delete"' "$T/requests" | sed 's/^GET \/sab\/api //; s/"apikey": "[^"]*", //' > "$T/sdel"
  grep '^DELETE /seerr/' "$T/requests" | cut -d' ' -f2 > "$T/mdel"
}
state() { cat "$T/wipe.tsv" 2>/dev/null; }
H1=aaaa000000000000000000000000000000000001; H2=bbbb000000000000000000000000000000000002; H3=cccc000000000000000000000000000000000003
H1U=$(tr a-f A-F <<<"$H1")
tor() { echo "{\"hash\": \"$1\", \"category\": \"$2\", \"name\": \"$3\"}"; } # tor <hash> <category> <name>
NOTLD="{\"id\": 13, \"mediaType\": \"movie\", \"tmdbId\": 10331, \"tvdbId\": null, \"status\": 7}"
OTHER="{\"id\": 4, \"mediaType\": \"movie\", \"tmdbId\": 1198654, \"tvdbId\": null, \"status\": 5}"
HEIST="{\"id\": 10, \"mediaType\": \"tv\", \"tmdbId\": 71446, \"tvdbId\": 327417, \"status\": 7}"
DEL=(radarr_eventtype=MovieDelete radarr_movie_tmdbid=10331 radarr_movie_title="Night of the Living Dead" radarr_movie_year=1968 radarr_movie_deletedfiles=True)
sdel() { echo "{\"del_files\": \"1\", \"mode\": \"$1\", \"name\": \"delete\", \"value\": \"$2\"} "; } # sdel <history|queue> <nzo id>

echo "Recording downloads"
rm -f "$T/wipe.tsv"
run '{}' radarr_eventtype=Grab radarr_movie_tmdbid=10331 radarr_download_client=qBittorrent radarr_download_client_type=qBittorrent radarr_download_id="$H1U"
assert_eq "Grab via qBittorrent: tmdb id + hash (lower case) recorded" "10331	qbit	$H1" "$(state)"
assert_eq "  calls no API" "0|" "$RC|$(cat "$T/requests")"
run '{}' radarr_eventtype=Grab radarr_movie_tmdbid=10331 radarr_download_client=qBittorrent radarr_download_client_type=qBittorrent radarr_download_id="$H1U"
assert_eq "the same grab twice: one line" "10331	qbit	$H1" "$(state)"
run '{}' radarr_eventtype=Download radarr_movie_tmdbid=10331 radarr_download_client=SABnzbd radarr_download_client_type=Sabnzbd radarr_download_id=SABnzbd_nzo_abc
assert_eq "Import via SABnzbd: nzo id recorded" "10331	qbit	$H1
10331	sab	SABnzbd_nzo_abc" "$(state)"
run '{}' radarr_eventtype=Download radarr_movie_tmdbid=10331
assert_eq "Import without a download client (manual import): nothing recorded" "2|0" "$(state | wc -l)|$RC"
run '{}' sonarr_eventtype=Grab sonarr_series_tvdbid=327417 sonarr_download_client_type=qBittorrent sonarr_download_id="$H3"
assert_eq "Sonarr Grab: tvdb id recorded" "327417	qbit	$H3" "$(state | tail -1)"
run '{}' radarr_eventtype=Test
assert_eq "Test event exits 0 and calls nothing" "0|" "$RC|$(cat "$T/requests")"

echo "MovieDelete"
printf '10331\tqbit\t%s\n10331\tsab\tSABnzbd_nzo_abc\n42\tqbit\t%s\n' "$H1" "$H2" > "$T/wipe.tsv"
FULL="{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p), $(tor "$H2" radarr Other.Film.2020.1080p)], \"sab_history\": [{\"nzo_id\": \"SABnzbd_nzo_abc\", \"name\": \"Night.of.the.Living.Dead.1968\", \"category\": \"movies\"}, {\"nzo_id\": \"SABnzbd_nzo_zzz\", \"name\": \"Other\", \"category\": \"movies\"}], \"seerr\": [$OTHER, $NOTLD]}"
run "$FULL" "${DEL[@]:0:4}" radarr_movie_deletedfiles=False
assert_eq "deleted without files (unmonitor/remove only): nothing removed anywhere" "|||0" "$(cat "$T/qdel")|$(cat "$T/sdel")|$(cat "$T/mdel")|$RC"
assert_eq "  state kept" 3 "$(state | wc -l)"
run "$FULL" "${DEL[@]}"
assert_eq "deleted with files: only the title's torrent removed, with its files" "hashes=$H1&deleteFiles=true" "$(cat "$T/qdel")"
assert_eq "  its SABnzbd history entry removed (with files)" "$(sdel history SABnzbd_nzo_abc)" "$(cat "$T/sdel")"
assert_eq "  its Seerr media record removed" "/seerr/api/v1/media/13" "$(cat "$T/mdel")"
assert_eq "  its state lines dropped, others kept" "42	qbit	$H2" "$(state)"
assert_eq "  exit 0" 0 "$RC"
printf '10331\tsab\tSABnzbd_nzo_q\n' > "$T/wipe.tsv"
run "{\"sab_queue\": [{\"nzo_id\": \"SABnzbd_nzo_q\", \"filename\": \"Night.of.the.Living.Dead.1968\", \"cat\": \"movies\"}]}" "${DEL[@]}"
assert_eq "a job still in SABnzbd's queue is removed from the queue" "$(sdel queue SABnzbd_nzo_q)" "$(cat "$T/sdel")"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" audiobooks Night.of.the.Living.Dead.1968)], \"seerr\": [$NOTLD]}" "${DEL[@]}"
assert_eq "recorded hash now in another category: torrent kept" "" "$(cat "$T/qdel")"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H2" radarr Night.of.the.Living.Dead.1968.REPACK)], \"seerr\": [$NOTLD]}" "${DEL[@]}"
assert_eq "recorded hash already gone from qBittorrent: no name guessing, nothing removed" "" "$(cat "$T/qdel")"

echo "Titles grabbed before the state file (name match)"
rm -f "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p.BluRay.x264-GRP), $(tor "$H2" radarr Night.of.the.Living.Dead.1990.1080p), $(tor "$H3" audiobooks Night.of.the.Living.Dead.1968)], \"seerr\": [$NOTLD]}" "${DEL[@]}"
assert_eq "one torrent matches title + year in the radarr category: removed" "hashes=$H1&deleteFiles=true" "$(cat "$T/qdel")"
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p), $(tor "$H2" radarr Night.Of.The.Living.Dead.1968.2160p)]}" "${DEL[@]}"
assert_eq "two torrents match: ambiguous, nothing removed" "" "$(cat "$T/qdel")"
grep -q ambiguous "$T/out" && ok "  logged as ambiguous" || bad "  logged as ambiguous" "$(cat "$T/out")"
run "{\"torrents\": [$(tor "$H3" audiobooks Night.of.the.Living.Dead.1968)]}" "${DEL[@]}"
assert_eq "a match outside the app's category (fastpi-style audiobooks): kept" "" "$(cat "$T/qdel")"
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p)], \"library\": [{\"id\": 9, \"title\": \"Night of the Living Dead\", \"year\": 1968}]}" "${DEL[@]}"
assert_eq "another library title with the same name and year: nothing removed" "" "$(cat "$T/qdel")"
run "{\"sab_history\": [{\"nzo_id\": \"SABnzbd_nzo_old\", \"name\": \"Night.of.the.Living.Dead.1968.DVDRip\", \"category\": \"movies\"}, {\"nzo_id\": \"SABnzbd_nzo_x\", \"name\": \"Night.of.the.Living.Dead.1968.DVDRip\", \"category\": \"tv\"}]}" "${DEL[@]}"
assert_eq "SABnzbd history by name, only in the app's category" "$(sdel history SABnzbd_nzo_old)" "$(cat "$T/sdel")"
run "{\"torrents\": [$(tor "$H2" radarr Other.Film.2020.1080p)], \"seerr\": [$OTHER]}" radarr_eventtype=MovieDelete radarr_movie_tmdbid=777 radarr_movie_title="Unknown Film" radarr_movie_year=2001 radarr_movie_deletedfiles=True
assert_eq "unknown title: nothing removed anywhere" "|||0" "$(cat "$T/qdel")|$(cat "$T/sdel")|$(cat "$T/mdel")|$RC"

echo "SeriesDelete"
SDEL=(sonarr_eventtype=SeriesDelete sonarr_series_tvdbid=327417 sonarr_series_tmdbid=71446 sonarr_series_title="Money Heist" sonarr_series_year=2017 sonarr_series_deletedfiles=True)
printf '327417\tqbit\t%s\n' "$H3" > "$T/wipe.tsv"
run "{\"app\": \"sonarr\", \"torrents\": [$(tor "$H3" sonarr Money.Heist.S02.1080p), $(tor "$H1" sonarr Money.Heist.S01.1080p)], \"seerr\": [$NOTLD, $HEIST]}" "${SDEL[@]}"
assert_eq "recorded season torrent removed, the unrecorded one kept" "hashes=$H3&deleteFiles=true" "$(cat "$T/qdel")"
assert_eq "  Seerr tv record found by tvdb id" "/seerr/api/v1/media/10" "$(cat "$T/mdel")"
rm -f "$T/wipe.tsv"
run "{\"app\": \"sonarr\", \"torrents\": [$(tor "$H1" sonarr Money.Heist.S01.1080p), $(tor "$H2" sonarr Money.Heist.S02E03.720p), $(tor "$H3" sonarr Money.Heist.Korea.Joint.Economic.Area.S01)]}" "${SDEL[@]}"
assert_eq "no state: every season torrent of the series by name, not a spin-off" "hashes=$H1|$H2&deleteFiles=true" "$(cat "$T/qdel")"
run "{\"app\": \"sonarr\", \"torrents\": [$(tor "$H1" sonarr Money.Heist.S01.1080p)]}" "${SDEL[@]:0:5}" sonarr_series_deletedfiles=False
assert_eq "deleted without files: nothing removed" "" "$(cat "$T/qdel")$(cat "$T/mdel")"

echo "Seerr and failures"
rm -f "$T/wipe.tsv"
run "{\"seerr\": [$OTHER]}" "${DEL[@]}"
assert_eq "title not in Seerr: no Seerr delete, exit 0" "|0" "$(cat "$T/mdel")|$RC"
SEERRKEY="$T/none" run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968)], \"seerr\": [$NOTLD]}" "${DEL[@]}"
assert_eq "no Seerr key: torrent still removed, exit non-zero" "hashes=$H1&deleteFiles=true|1" "$(cat "$T/qdel")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)"
SABKEY="$T/none" run "{\"seerr\": [$NOTLD]}" "${DEL[@]}"
assert_eq "no SABnzbd key: Seerr still cleared, exit non-zero" "/seerr/api/v1/media/13|1" "$(cat "$T/mdel")|$([ "$RC" -ne 0 ] && echo 1 || echo 0)"

echo "Seed from history (titles grabbed before the hook)"
rm -f "$T/wipe.tsv"
HIST='{"history": [{"downloadId": "AAAA000000000000000000000000000000000001", "movie": {"tmdbId": 10331}, "data": {"downloadClient": "qBittorrent"}}, {"downloadId": "SABnzbd_nzo_abc", "movie": {"tmdbId": 10248}, "data": {"downloadClient": "SABnzbd"}}, {"movie": {"tmdbId": 5}, "data": {}}]}'
ARGS="seed radarr" run "$HIST"
assert_eq "seed: grabbed history -> state" "10331	qbit	$H1
10248	sab	SABnzbd_nzo_abc" "$(state)"
ARGS="seed radarr" run "$HIST"
assert_eq "  re-run adds nothing" 2 "$(state | wc -l)"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
