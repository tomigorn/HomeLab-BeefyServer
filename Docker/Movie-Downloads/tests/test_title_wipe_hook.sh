#!/usr/bin/env bash
# Runs scripts/title-wipe-hook.sh against a fake Radarr/Sonarr + qBittorrent + SABnzbd + Jellyfin API (python http.server).
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
import email.utils, http.server, json, os, sys, time, urllib.parse
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
        sf = os.path.join(T, "state.json")
        state = json.load(open(sf))
        if u.path.startswith("/qbit/api/v2/"):
            p = u.path.removeprefix("/qbit/api/v2")
            if p == "/auth/login":
                ok = state.get("qbit_pw") and dict(urllib.parse.parse_qsl(body)) == {"username": "admin", "password": state["qbit_pw"]}
                data = b"" if ok else b"Fails."
                self.send_response(204 if ok else 401)
                if ok:
                    self.send_header("Set-Cookie", "QBT_SID_8080=sid1; HttpOnly; SameSite=Lax; expires=" + email.utils.formatdate(time.time() + 3600, usegmt=True) + "; path=/")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
                return
            if state.get("qbit_pw") and "QBT_SID_8080=sid1" not in (self.headers.get("Cookie") or ""):
                return self.reply(403)
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
        if u.path.startswith("/jf/"):
            if self.headers.get("Authorization") != 'MediaBrowser Token="jfkey"':
                return self.reply(401)
            if state.get("jf_down"):
                return self.reply(500)
            p = u.path.removeprefix("/jf")
            items = [i for i in state.get("jellyfin", []) if i["Type"] in q.get("IncludeItemTypes", "").split(",")]
            if self.command == "GET" and p == "/Items":
                return self.reply(200, {"Items": items, "TotalRecordCount": len(items)})
            if self.command == "DELETE" and p.startswith("/Items/"):
                return self.reply(204)
            return self.reply(404)
        if self.headers.get("X-Api-Key") != "testkey":
            return self.reply(401)
        p = u.path.removeprefix("/base/api/v3")
        app = state.get("app", "radarr")
        lib = state.get("library", [])
        if state.get("arr_down"):
            return self.reply(500)
        if self.command == "GET" and p == "/downloadclient":
            return self.reply(200, CLIENTS[app])
        if self.command == "GET" and p in ("/movie", "/series"):
            for f in ("tmdbId", "tvdbId"):
                if f in q:
                    lib = [t for t in lib if str(t.get(f)) == q[f]]
            return self.reply(200, lib)
        if self.command == "DELETE" and (p.startswith("/movie/") or p.startswith("/series/")):
            state["jellyfin"] = [i for i in state.get("jellyfin", []) if not i.get("auto")]
            json.dump(state, open(sf, "w"))
            return self.reply(200)
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
echo sabkey > "$T/sab.apikey"; echo jfkey > "$T/jf.apikey"; echo 'pw &=x' > "$T/qb.password"

# run <fake state json> <env...>: runs the hook (arguments from the array A) against $T/wipe.tsv; deletes land in $T/qdel (qBittorrent), $T/sdel (SABnzbd), $T/adel (Radarr/Sonarr), $T/jdel (Jellyfin), exit code in $RC
A=()
run() {
  echo "$1" > "$T/state.json"; shift
  : > "$T/requests"
  env -i PATH="$PATH" TW_CONFIG="$T/config.xml" TW_STATE="$T/wipe.tsv" TW_QBIT_URL="http://127.0.0.1:$PORT/qbit" \
    TW_SAB_URL="http://127.0.0.1:$PORT/sab" TW_SAB_KEY="${SABKEY-$T/sab.apikey}" TW_JF_URL="http://127.0.0.1:$PORT/jf" \
    TW_JF_KEY="${JFKEY-$T/jf.apikey}" TW_QBIT_PASS="${QBPASS-$T/qb.password}" TW_JF_WAIT=0 "$@" bash "$HOOK" "${A[@]}" > "$T/out" 2>&1; RC=$?
  grep '^POST /qbit/api/v2/torrents/delete' "$T/requests" | cut -d' ' -f4- > "$T/qdel"
  grep '"name": "delete"' "$T/requests" | sed 's/^GET \/sab\/api //; s/"apikey": "[^"]*", //' > "$T/sdel"
  grep '^DELETE /base/' "$T/requests" | cut -d' ' -f2- | sed 's/ $//' > "$T/adel"
  grep '^DELETE /jf/' "$T/requests" | cut -d' ' -f2 > "$T/jdel"
}
state() { cat "$T/wipe.tsv" 2>/dev/null; }
H1=aaaa000000000000000000000000000000000001; H2=bbbb000000000000000000000000000000000002; H3=cccc000000000000000000000000000000000003
H1U=$(tr a-f A-F <<<"$H1")
tor() { echo "{\"hash\": \"$1\", \"category\": \"$2\", \"name\": \"$3\"}"; } # tor <hash> <category> <name>
jf() { echo "{\"Id\": \"$1\", \"Type\": \"$2\", \"Name\": \"x\", \"ProviderIds\": {$3}, \"Path\": \"$4\"${5:+, \"auto\": true}}"; } # jf <id> <type> <provider ids> <path> [auto: gone after the Radarr/Sonarr delete]
NOTLD_LIB="{\"id\": 31, \"tmdbId\": 10331, \"title\": \"Night of the Living Dead\", \"year\": 1968}"
WIPE=(wipe radarr 10331 "Night of the Living Dead" 1968)
sdel() { echo "{\"del_files\": \"1\", \"mode\": \"$1\", \"name\": \"delete\", \"value\": \"$2\"} "; } # sdel <history|queue> <nzo id>
nonzero() { [ "$RC" -ne 0 ] && echo 1 || echo 0; }

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

echo "Remove from Radarr/Sonarr (delete events) wipes nothing"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968)]}" radarr_eventtype=MovieDelete radarr_movie_tmdbid=10331 radarr_movie_title="Night of the Living Dead" radarr_movie_year=1968 radarr_movie_deletedfiles=True
assert_eq "MovieDelete with files: no API call, state kept, exit 0" "|10331	qbit	$H1|0" "$(cat "$T/requests")|$(state)|$RC"
run "{\"app\": \"sonarr\"}" sonarr_eventtype=SeriesDelete sonarr_series_tvdbid=327417 sonarr_series_title="Money Heist" sonarr_series_deletedfiles=True
assert_eq "SeriesDelete with files: no API call, exit 0" "|0" "$(cat "$T/requests")|$RC"

echo "Wipe (Seerr Clear Data)"
A=("${WIPE[@]}")
printf '10331\tqbit\t%s\n10331\tsab\tSABnzbd_nzo_abc\n42\tqbit\t%s\n' "$H1" "$H2" > "$T/wipe.tsv"
FULL="{\"library\": [$NOTLD_LIB, {\"id\": 5, \"tmdbId\": 42, \"title\": \"Other Film\", \"year\": 2020}],
  \"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p), $(tor "$H2" radarr Other.Film.2020.1080p)],
  \"sab_history\": [{\"nzo_id\": \"SABnzbd_nzo_abc\", \"name\": \"Night.of.the.Living.Dead.1968\", \"category\": \"movies\"}, {\"nzo_id\": \"SABnzbd_nzo_zzz\", \"name\": \"Other\", \"category\": \"movies\"}],
  \"jellyfin\": [$(jf j1 Movie '"Tmdb": "10331"' "/media/movies/Night of the Living Dead (1968)/a.mkv"), $(jf j2 Movie '"Tmdb": "42"' /media/movies/Other/b.mkv), $(jf j3 Movie '"Tmdb": "10331"' /media/home-videos/c.mkv)]}"
run "$FULL"
assert_eq "title still in Radarr: deleted there with its files" "/base/api/v3/movie/31 {\"addImportExclusion\": \"false\", \"deleteFiles\": \"true\"}" "$(cat "$T/adel")"
assert_eq "  only the title's torrent removed, with its files" "hashes=$H1&deleteFiles=true" "$(cat "$T/qdel")"
assert_eq "  its SABnzbd history entry removed (with files)" "$(sdel history SABnzbd_nzo_abc)" "$(cat "$T/sdel")"
assert_eq "  its Jellyfin item removed (exact tmdb id, movie library only)" "/jf/Items/j1" "$(cat "$T/jdel")"
assert_eq "  its state lines dropped, others kept" "42	qbit	$H2" "$(state)"
assert_eq "  exit 0" 0 "$RC"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"library\": [$NOTLD_LIB], \"jellyfin\": [$(jf j1 Movie '"Tmdb": "10331"' /media/movies/N/a.mkv auto)]}"
assert_eq "Jellyfin item gone after the Radarr delete (its own notification): no Jellyfin delete" "" "$(cat "$T/jdel")"
printf '10331\tqbit\t%s\n10331\tsab\tSABnzbd_nzo_abc\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p)], \"sab_history\": [{\"nzo_id\": \"SABnzbd_nzo_abc\", \"name\": \"N\", \"category\": \"movies\"}], \"jellyfin\": [$(jf j1 Movie '"Tmdb": "10331"' /media/movies/N/a.mkv)]}"
assert_eq "already removed from Radarr earlier: no Radarr delete, leftovers still wiped" "|hashes=$H1&deleteFiles=true|$(sdel history SABnzbd_nzo_abc)|/jf/Items/j1|0" "$(cat "$T/adel")|$(cat "$T/qdel")|$(cat "$T/sdel")|$(cat "$T/jdel")|$RC"
printf '10331\tsab\tSABnzbd_nzo_q\n' > "$T/wipe.tsv"
run "{\"sab_queue\": [{\"nzo_id\": \"SABnzbd_nzo_q\", \"filename\": \"Night.of.the.Living.Dead.1968\", \"cat\": \"movies\"}]}"
assert_eq "a job still in SABnzbd's queue is removed from the queue" "$(sdel queue SABnzbd_nzo_q)" "$(cat "$T/sdel")"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" audiobooks Night.of.the.Living.Dead.1968)]}"
assert_eq "recorded hash now in another category: torrent kept" "" "$(cat "$T/qdel")"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H2" radarr Night.of.the.Living.Dead.1968.REPACK)]}"
assert_eq "recorded hash already gone from qBittorrent: no name guessing, nothing removed" "" "$(cat "$T/qdel")"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"library\": [$NOTLD_LIB], \"torrents\": [$(tor "$H1" radarr N)], \"arr_down\": true}"
assert_eq "Radarr not reachable: nothing removed anywhere, state kept, exit non-zero" "||1|10331	qbit	$H1" "$(cat "$T/qdel")|$(cat "$T/jdel")|$(nonzero)|$(state)"

echo "Titles grabbed before the state file (name match)"
rm -f "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p.BluRay.x264-GRP), $(tor "$H2" radarr Night.of.the.Living.Dead.1990.1080p), $(tor "$H3" audiobooks Night.of.the.Living.Dead.1968)]}"
assert_eq "one torrent matches title + year in the radarr category: removed" "hashes=$H1&deleteFiles=true" "$(cat "$T/qdel")"
A=(wipe radarr 10331)
run "{\"library\": [$NOTLD_LIB], \"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p)]}"
assert_eq "no title given: taken from Radarr (title still there), name match works" "hashes=$H1&deleteFiles=true" "$(cat "$T/qdel")"
A=("${WIPE[@]}")
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p), $(tor "$H2" radarr Night.Of.The.Living.Dead.1968.2160p)]}"
assert_eq "two torrents match: ambiguous, nothing removed" "" "$(cat "$T/qdel")"
grep -q ambiguous "$T/out" && ok "  logged as ambiguous" || bad "  logged as ambiguous" "$(cat "$T/out")"
run "{\"torrents\": [$(tor "$H3" audiobooks Night.of.the.Living.Dead.1968)]}"
assert_eq "a match outside the app's category (fastpi-style audiobooks): kept" "" "$(cat "$T/qdel")"
run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968.1080p)], \"library\": [{\"id\": 9, \"tmdbId\": 999, \"title\": \"Night of the Living Dead\", \"year\": 1968}]}"
assert_eq "another library title with the same name and year: nothing removed" "" "$(cat "$T/qdel")"
run "{\"sab_history\": [{\"nzo_id\": \"SABnzbd_nzo_old\", \"name\": \"Night.of.the.Living.Dead.1968.DVDRip\", \"category\": \"movies\"}, {\"nzo_id\": \"SABnzbd_nzo_x\", \"name\": \"Night.of.the.Living.Dead.1968.DVDRip\", \"category\": \"tv\"}]}"
assert_eq "SABnzbd history by name, only in the app's category" "$(sdel history SABnzbd_nzo_old)" "$(cat "$T/sdel")"
A=(wipe radarr 777)
run "{\"torrents\": [$(tor "$H2" radarr Other.Film.2020.1080p)], \"jellyfin\": [$(jf j2 Movie '"Tmdb": "42"' /media/movies/O/b.mkv)]}"
assert_eq "unknown title, no name: nothing removed anywhere" "||||0" "$(cat "$T/adel")|$(cat "$T/qdel")|$(cat "$T/sdel")|$(cat "$T/jdel")|$RC"

echo "Wipe a series"
A=(wipe sonarr 327417 "Money Heist" 2017)
printf '327417\tqbit\t%s\n' "$H3" > "$T/wipe.tsv"
run "{\"app\": \"sonarr\", \"library\": [{\"id\": 8, \"tvdbId\": 327417, \"title\": \"Money Heist\", \"year\": 2017}],
  \"torrents\": [$(tor "$H3" sonarr Money.Heist.S02.1080p), $(tor "$H1" sonarr Money.Heist.S01.1080p)],
  \"jellyfin\": [$(jf s1 Series '"Tvdb": "327417", "Tmdb": "71446"' "/media/tv/Money Heist"), $(jf m1 Movie '"Tvdb": "327417"' /media/movies/M)]}"
assert_eq "series deleted in Sonarr with its files" "/base/api/v3/series/8 {\"addImportListExclusion\": \"false\", \"deleteFiles\": \"true\"}" "$(cat "$T/adel")"
assert_eq "  recorded season torrent removed, the unrecorded one kept" "hashes=$H3&deleteFiles=true" "$(cat "$T/qdel")"
assert_eq "  Jellyfin series removed by tvdb id, nothing else" "/jf/Items/s1" "$(cat "$T/jdel")"
rm -f "$T/wipe.tsv"
run "{\"app\": \"sonarr\", \"torrents\": [$(tor "$H1" sonarr Money.Heist.S01.1080p), $(tor "$H2" sonarr Money.Heist.S02E03.720p), $(tor "$H3" sonarr Money.Heist.Korea.Joint.Economic.Area.S01)]}"
assert_eq "no state: every season torrent of the series by name, not a spin-off" "hashes=$H1|$H2&deleteFiles=true" "$(cat "$T/qdel")"

echo "Failures"
A=("${WIPE[@]}")
rm -f "$T/wipe.tsv"
SABKEY="$T/none" run "{\"torrents\": [$(tor "$H1" radarr Night.of.the.Living.Dead.1968)]}"
assert_eq "no SABnzbd key: torrent still removed, exit non-zero" "hashes=$H1&deleteFiles=true|1" "$(cat "$T/qdel")|$(nonzero)"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" radarr N)], \"jf_down\": true}"
assert_eq "Jellyfin not reachable: torrent still removed, state kept for the retry, exit non-zero" "hashes=$H1&deleteFiles=true|10331	qbit	$H1|1" "$(cat "$T/qdel")|$(state)|$(nonzero)"
A=(wipe radarr)
run '{}'
assert_eq "wipe without an id: refused, nothing called" "|1" "$(cat "$T/requests")|$(nonzero)"

echo "qBittorrent login (no whitelist)"
A=("${WIPE[@]}")
QB_AUTH='"qbit_pw": "pw &=x", "torrents": ['"$(tor "$H1" radarr N)"']'
logins() { grep -c '^POST /qbit/api/v2/auth/login' "$T/requests"; }
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{$QB_AUTH}"
assert_eq "403: logs in once with the password file, torrent removed, exit 0" "1|hashes=$H1&deleteFiles=true|0" "$(logins)|$(cat "$T/qdel")|$RC"
assert_eq "  password never in a URL" "0" "$(cut -d' ' -f1-3 "$T/requests" | grep -c 'pw')"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{$QB_AUTH, \"qbit_pw\": \"other\"}"
assert_eq "wrong password: one login, nothing removed in qBittorrent, exit non-zero" "1||1" "$(logins)|$(cat "$T/qdel")|$(nonzero)"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
QBPASS="$T/none" run "{$QB_AUTH}"
assert_eq "no password file: no login, nothing removed in qBittorrent, exit non-zero" "0||1" "$(logins)|$(cat "$T/qdel")|$(nonzero)"
printf '10331\tqbit\t%s\n' "$H1" > "$T/wipe.tsv"
run "{\"torrents\": [$(tor "$H1" radarr N)]}"
assert_eq "whitelisted (no 403): no login" "0|hashes=$H1&deleteFiles=true" "$(logins)|$(cat "$T/qdel")"

echo "Seed from history (titles grabbed before the hook)"
rm -f "$T/wipe.tsv"
HIST='{"history": [{"downloadId": "AAAA000000000000000000000000000000000001", "movie": {"tmdbId": 10331}, "data": {"downloadClient": "qBittorrent"}}, {"downloadId": "SABnzbd_nzo_abc", "movie": {"tmdbId": 10248}, "data": {"downloadClient": "SABnzbd"}}, {"movie": {"tmdbId": 5}, "data": {}}]}'
A=(seed radarr)
run "$HIST"
assert_eq "seed: grabbed history -> state" "10331	qbit	$H1
10248	sab	SABnzbd_nzo_abc" "$(state)"
run "$HIST"
assert_eq "  re-run adds nothing" 2 "$(state | wc -l)"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
