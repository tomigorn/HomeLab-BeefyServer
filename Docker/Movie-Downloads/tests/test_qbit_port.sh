#!/usr/bin/env bash
# Runs scripts/qbit-port.sh (gluetun's port-forward hook) against a fake qBittorrent API.
# Usage: tests/test_qbit_port.sh   (needs bash, wget, python3)
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK="$HERE/../scripts/qbit-port.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

cat > "$T/fake.py" <<'PY'
import http.server, os, sys, urllib.parse
T = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        with open(os.path.join(T, "requests"), "a") as f:
            f.write(f"{self.path} {urllib.parse.unquote_plus(body)}\n")
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"Ok")
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_port))
s.serve_forever()
PY
python3 "$T/fake.py" "$T" & SRV=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
export QBIT_URL="http://127.0.0.1:$(cat "$T/port")"

echo "qbit-port.sh"
out=$(sh "$HOOK" 45321 2>&1); rc=$?
assert_eq "exit 0 on success" 0 "$rc"
assert_eq "sets listen_port, then reannounces all" \
  "/api/v2/app/setPreferences json={\"listen_port\":45321}
/api/v2/torrents/reannounce hashes=all" "$(cat "$T/requests")"

: > "$T/requests"
sh "$HOOK" "12a" >/dev/null 2>&1; rc=$?
assert_eq "non-numeric port refused" 2 "$rc"
assert_eq "nothing sent for a refused port" "" "$(cat "$T/requests")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
