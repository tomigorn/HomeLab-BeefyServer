#!/usr/bin/env bash
# Runs scripts/seerr-sync.sh against a fake Jellyfin + Seerr (python http.server): debounce, Jellyfin scan request only for unknown files, import date for old file dates, Seerr full scan.
# Usage: tests/test_seerr_sync.sh   (needs bash, curl, jq, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../scripts/seerr-sync.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

cat > "$T/fake.py" <<'PY'
import http.server, json, os, sys
T = sys.argv[1]
def log(s):
    open(os.path.join(T, "calls"), "a").write(s + "\n")
def items():
    dates = json.load(open(os.path.join(T, "dates.json"))) if os.path.exists(os.path.join(T, "dates.json")) else {}
    tmdb = json.load(open(os.path.join(T, "tmdb.json"))) if os.path.exists(os.path.join(T, "tmdb.json")) else {}
    return {str(i): {"Id": str(i), "Type": "Episode" if "/tv/" in p else "Movie", "Path": p, "DateCreated": dates.get(p, "2999-01-01T00:00:00.0000000Z"), "ProviderIds": {"Tmdb": tmdb[p]} if p in tmdb else {}} for i, p in enumerate(json.load(open(os.path.join(T, "jf.json"))))}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/Users":
            return self.reply(200, [{"Id": "viewer", "Policy": {"IsAdministrator": False}}, {"Id": "adm", "Policy": {"IsAdministrator": True}}])
        if self.path.startswith("/Users/adm/Items/"):
            p = items().get(self.path.rsplit("/", 1)[1])
            return self.reply(200, {"Id": p["Id"], "Name": "x", "Path": p["Path"], "DateCreated": p["DateCreated"], "Trickplay": {"a": {"320": {"Width": 320}}}}) if p else self.reply(404)
        if self.path.startswith("/Items"):
            if "Token=\"jfkey\"" not in (self.headers.get("Authorization") or ""): return self.reply(401)
            if "userId=adm" not in self.path: return self.reply(200, {"Items": []})
            its = list(items().values())
            if "ProviderIds" not in self.path: its = [{k: v for k, v in i.items() if k != "ProviderIds"} for i in its]
            return self.reply(200, {"Items": its})
        if self.path.startswith("/System/Info/Public"):
            return self.reply(200, {})
        if self.path == "/api/v1/settings/jobs":
            if self.headers.get("X-Api-Key") != "seerrkey": return self.reply(401)
            n = int(open(os.path.join(T, "polls")).read() if os.path.exists(os.path.join(T, "polls")) else 0)
            started = os.path.exists(os.path.join(T, "started"))
            running = started and n < 2
            if started: open(os.path.join(T, "polls"), "w").write(str(n + 1))
            return self.reply(200, [{"id": "jellyfin-full-scan", "running": running}])
        self.reply(404)
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path == "/Library/Media/Updated":
            ups = json.loads(body)["Updates"]
            log("JF-UPDATE " + ",".join(u["Path"] for u in ups))
            if not os.path.exists(os.path.join(T, "jf-stuck")):
                have = json.load(open(os.path.join(T, "jf.json")))
                json.dump(have + [u["Path"] for u in ups], open(os.path.join(T, "jf.json"), "w"))
            return self.reply(204)
        if self.path.startswith("/Items/"):
            b = json.loads(body)
            if "Trickplay" in b: return self.reply(500)
            log("JF-DATE " + b["Path"] + " " + b["DateCreated"][:4])
            return self.reply(204)
        if self.path == "/api/v1/settings/jobs/jellyfin-full-scan/run":
            if self.headers.get("X-Api-Key") != "seerrkey": return self.reply(401)
            log("SEERR-SCAN")
            if os.path.exists(os.path.join(T, "seerr-down")): return self.reply(500)
            if os.path.exists(os.path.join(T, "import-during-scan")):
                os.remove(os.path.join(T, "import-during-scan"))
                open(os.path.join(T, "radarr", "seerr-sync.queue"), "a").write("/data/media/movies/D/D.mkv\n")
            open(os.path.join(T, "started"), "w").write("1"); open(os.path.join(T, "polls"), "w").write("0")
            return self.reply(200, {})
        self.reply(404)
http.server.HTTPServer(("127.0.0.1", 0), H).serve_forever()
PY
# the server prints nothing; find its port through a pre-bound probe
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
sed -i "s/HTTPServer((\"127.0.0.1\", 0)/HTTPServer((\"127.0.0.1\", $PORT)/" "$T/fake.py"
python3 "$T/fake.py" "$T" & SRV=$!
echo seerrkey > "$T/seerr.key"; echo jfkey > "$T/jf.key"
for _ in $(seq 50); do curl -s "http://127.0.0.1:$PORT/System/Info/Public" >/dev/null && break; sleep 0.1; done

mkdir -p "$T/radarr" "$T/sonarr"
reset() { rm -f "$T/calls" "$T/started" "$T/polls" "$T/jf-stuck" "$T/seerr-down" "$T/import-during-scan" "$T/log" "$T/last" "$T/dates.json" "$T/tmdb.json" "$T/radarr/"* "$T/sonarr/"*; echo '[]' > "$T/jf.json"; : > "$T/calls"; }
mkdir -p "$T/bin"; REAL_CURL=$(command -v curl)
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s/argv"\nexec %s "$@"\n' "$T" "$REAL_CURL" > "$T/bin/curl"; chmod +x "$T/bin/curl"
run() { # run [env...]: runs the script with test settings, prints its exit code
  env PATH="$T/bin:$PATH" XDG_STATE_HOME="$T/state" SS_QUEUES="$T/radarr/seerr-sync.queue $T/sonarr/seerr-sync.queue" SS_LASTRUN="$T/last" SS_LOG="$T/log" \
    SS_SEERR_URL="http://127.0.0.1:$PORT" SS_SEERR_KEY="$T/seerr.key" SS_JF_URL="http://127.0.0.1:$PORT" SS_JF_KEY="$T/jf.key" \
    SS_QUIET=1 SS_CAP=4 SS_MIN_GAP=0 SS_JF_WAIT=2 SS_SCAN_WAIT=10 SS_POLL=1 "$@" bash "$SCRIPT" >"$T/out" 2>&1; echo $?
}
calls() { paste -sd' ' "$T/calls"; }

reset
assert_eq "empty queue exits 0" "0" "$(run)"
assert_eq "empty queue calls nothing" "" "$(calls)"

reset; echo '["/media/movies/A/A.mkv"]' > "$T/jf.json"; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"
assert_eq "file Jellyfin already has exits 0" "0" "$(run)"
assert_eq "known file: only the Seerr scan" "SEERR-SCAN" "$(calls)"
[ ! -e "$T/radarr/seerr-sync.queue" ] && ok "queue claimed" || bad "queue claimed" "still there"

reset; echo /data/media/movies/B/B.mkv > "$T/radarr/seerr-sync.queue"; echo /data/media/tv/S/1.mkv > "$T/sonarr/seerr-sync.queue"
assert_eq "unknown files exit 0" "0" "$(run)"
assert_eq "unknown files: Jellyfin asked (mapped paths), then Seerr" "JF-UPDATE /media/movies/B/B.mkv,/media/tv/S/1.mkv SEERR-SCAN" "$(calls)"

reset; echo /data/media/movies/C/C.mkv > "$T/radarr/seerr-sync.queue"; touch "$T/jf-stuck"
assert_eq "Jellyfin never picks the file up: still exits 0" "0" "$(run)"
assert_eq "stuck Jellyfin: Seerr scan runs anyway" "JF-UPDATE /media/movies/C/C.mkv SEERR-SCAN" "$(calls)"
grep -q 'scanning Seerr anyway' "$T/log" && ok "stuck Jellyfin is logged" || bad "stuck Jellyfin is logged" "$(cat "$T/log")"

reset; echo '["/media/movies/A/A.mkv"]' > "$T/jf.json"; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"
start=$(date +%s); run >/dev/null; el=$(( $(date +%s) - start ))
[ "$el" -ge 1 ] && ok "waits the quiet period (${el}s)" || bad "waits the quiet period" "${el}s"

reset; echo '["/media/movies/A/A.mkv"]' > "$T/jf.json"; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"
( sleep 2; echo /data/media/movies/A/A.mkv >> "$T/radarr/seerr-sync.queue" ) &
start=$(date +%s); run SS_QUIET=3 SS_CAP=20 >/dev/null
el=$(( $(date -d "$(grep -m1 ' start: ' "$T/log" | cut -c1-19)" +%s) - start ))
[ "$el" -ge 4 ] && ok "a later import extends the wait (${el}s)" || bad "a later import extends the wait" "${el}s"
assert_eq "both imports, one Seerr scan" "SEERR-SCAN" "$(calls)"

reset; echo '["/media/movies/A/A.mkv"]' > "$T/jf.json"; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"
( for _ in 1 2 3 4 5 6 7 8; do sleep 1; echo /data/media/movies/A/A.mkv >> "$T/radarr/seerr-sync.queue"; done ) & FEED=$!
start=$(date +%s); run SS_QUIET=3 >/dev/null
el=$(( $(date -d "$(grep -m1 ' start: ' "$T/log" | cut -c1-19)" +%s) - start ))
[ "$el" -le 6 ] && ok "a steady stream still runs at the cap (${el}s)" || bad "cap" "${el}s"
wait $FEED

reset; echo '["/media/movies/A/A.mkv"]' > "$T/jf.json"; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"; date +%s > "$T/last"
start=$(date +%s); run SS_MIN_GAP=3 >/dev/null; el=$(( $(date +%s) - start ))
[ "$el" -ge 2 ] && ok "min gap since the previous run is kept (${el}s)" || bad "min gap" "${el}s"

reset; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"
assert_eq "no Seerr key exits 1" "1" "$(run SS_SEERR_KEY=/nonexistent SS_JF_WAIT=0)"
assert_eq "  the claimed file is back in the queue" "/data/media/movies/A/A.mkv" "$(cat "$T/radarr/seerr-sync.queue" 2>/dev/null)"

reset; echo '["/media/movies/A/A.mkv"]' > "$T/jf.json"; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"; touch "$T/seerr-down"
assert_eq "Seerr scan request fails: exits 1" "1" "$(run)"
assert_eq "  the claimed file is back in the queue" "/data/media/movies/A/A.mkv" "$(cat "$T/radarr/seerr-sync.queue" 2>/dev/null)"
assert_eq "  no claimed leftovers" "" "$(ls "$T/radarr" | grep claimed)"

reset; echo '["/media/movies/A/A.mkv", "/media/movies/D/D.mkv"]' > "$T/jf.json"; echo /data/media/movies/A/A.mkv > "$T/radarr/seerr-sync.queue"; touch "$T/import-during-scan"
assert_eq "an import lands during the Seerr scan: exits 0" "0" "$(run)"
assert_eq "  a second round scans Seerr again" "SEERR-SCAN SEERR-SCAN" "$(calls)"
assert_eq "  queue empty afterwards" "" "$(cat "$T/radarr/seerr-sync.queue" 2>/dev/null; ls "$T/radarr" | grep claimed)"

reset; echo /data/media/movies/E/E.mkv > "$T/radarr/seerr-sync.queue.claimed.99999"; echo '["/media/movies/E/E.mkv"]' > "$T/jf.json"; touch "$T/radarr/seerr-sync.queue"
echo /data/media/movies/A/A.mkv > "$T/sonarr/seerr-sync.queue"
assert_eq "a claimed file left by a killed run is taken too" "0" "$(run)"
assert_eq "  and removed after success" "" "$(ls "$T/radarr" | grep claimed)"

reset; echo '["/media/movies/F/F.mkv", "/media/movies/G/G.mkv", "/media/movies/H/H.mkv"]' > "$T/jf.json"
echo '{"/media/movies/F/F.mkv": "2013-05-08T20:06:21.0000000Z", "/media/movies/H/H.mkv": "2013-05-08T20:06:21.0000000Z"}' > "$T/dates.json"
printf '/data/media/movies/F/F.mkv\n/data/media/movies/G/G.mkv\n' > "$T/radarr/seerr-sync.queue"
assert_eq "old file date: exits 0" "0" "$(run)"
assert_eq "  only the imported item with an old date is dated to the import, before the Seerr scan" "JF-DATE /media/movies/F/F.mkv $(date -u +%Y) SEERR-SCAN" "$(calls)"

reset; echo '["/media/movies/Nosferatu (1922) [tmdbid-653]/N.mkv", "/media/movies/Other/N2.mkv", "/media/movies/F/F.mkv", "/media/tv/S/E.mkv"]' > "$T/jf.json"
echo '{"/media/movies/Nosferatu (1922) [tmdbid-653]/N.mkv": "2013-05-08T20:06:21.0000000Z", "/media/movies/Other/N2.mkv": "2013-05-08T20:06:21.0000000Z", "/media/movies/F/F.mkv": "2013-05-08T20:06:21.0000000Z", "/media/tv/S/E.mkv": "2013-05-08T20:06:21.0000000Z"}' > "$T/dates.json"
echo '{"/media/movies/Other/N2.mkv": "653", "/media/tv/S/E.mkv": "653"}' > "$T/tmdb.json"
printf '/data/media/movies/Nosferatu (1922) [tmdbid-653]/N.mkv\n/data/media/movies/Other/N2.mkv\n/data/media/movies/F/F.mkv\n/data/media/tv/S/E.mkv\n' > "$T/radarr/seerr-sync.queue"
assert_eq "test film Nosferatu (TMDB 653): exits 0" "0" "$(run)"
assert_eq "  never dated to the import, by folder id or Jellyfin TMDB id (an episode with that TMDB id still is)" "JF-DATE /media/movies/F/F.mkv $(date -u +%Y) JF-DATE /media/tv/S/E.mkv $(date -u +%Y) SEERR-SCAN" "$(calls)"

grep -qE 'seerrkey|jfkey' "$T/argv" && bad "Seerr and Jellyfin keys never on curl's argv" "$(grep -m1 -E 'seerrkey|jfkey' "$T/argv")" || ok "Seerr and Jellyfin keys never on curl's argv ($(wc -l < "$T/argv") curl calls)"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
