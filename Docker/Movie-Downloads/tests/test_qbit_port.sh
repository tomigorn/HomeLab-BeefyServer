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
    def reply(self, code, body=b"", cookie=None):
        self.send_response(code)
        if cookie: self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if self.path == "/api/v2/auth/login":
            form = urllib.parse.parse_qs(body)
            with open(os.path.join(T, "logins"), "a") as f: f.write(form.get("username", [""])[0] + "\n")
            if form.get("password", [""])[0] == open(os.path.join(T, "secret")).read().strip():
                return self.reply(204, cookie="QBT_SID_8080=good; HttpOnly; path=/")
            return self.reply(200, b"Fails.")
        if "QBT_SID_8080=good" not in (self.headers.get("Cookie") or ""):
            return self.reply(403, b"Forbidden")
        with open(os.path.join(T, "requests"), "a") as f:
            f.write(f"{self.path} {urllib.parse.unquote_plus(body)}\n")
        self.reply(200, b"Ok")
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_port))
s.serve_forever()
PY
printf '%s\n' 'p&ss w+rd=%x' > "$T/secret"
cp "$T/secret" "$T/pass"
python3 "$T/fake.py" "$T" & SRV=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
export QBIT_URL="http://127.0.0.1:$(cat "$T/port")" QBIT_PASS_FILE="$T/pass"

echo "qbit-port.sh"
out=$(sh "$HOOK" 45321 2>&1); rc=$?
assert_eq "exit 0 on success" 0 "$rc"
assert_eq "logs in as admin (no localhost bypass)" "admin" "$(cat "$T/logins")"
assert_eq "sets listen_port, then reannounces all" \
  "/api/v2/app/setPreferences json={\"listen_port\":45321}
/api/v2/torrents/reannounce hashes=all" "$(cat "$T/requests")"

: > "$T/requests"
sh "$HOOK" "12a" >/dev/null 2>&1; rc=$?
assert_eq "non-numeric port refused" 2 "$rc"
assert_eq "nothing sent for a refused port" "" "$(cat "$T/requests")"

: > "$T/requests"
echo wrong > "$T/pass"
start=$(date +%s)
sh "$HOOK" 45321 >/dev/null 2>&1; rc=$?
assert_eq "refused login fails at once" "1 yes" "$rc $([ $(( $(date +%s) - start )) -lt 5 ] && echo yes)"
assert_eq "nothing sent without a login" "" "$(cat "$T/requests")"

rm "$T/pass"
sh "$HOOK" 45321 >/dev/null 2>&1; rc=$?
assert_eq "missing password file fails" 1 "$rc"
assert_eq "no temp files left behind" "" "$(find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'qbit-port.*' -newer "$T/port" 2>/dev/null)"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
