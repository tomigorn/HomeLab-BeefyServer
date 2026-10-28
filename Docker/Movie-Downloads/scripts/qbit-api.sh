# Sourced. qBittorrent Web API with login: `qbit_api <base url> <path> [curl args]` prints the body; on 403 it logs in once (user QBIT_USER, default admin; password read from the file QBIT_PASS_FILE, never on argv) and retries. Sourcing creates the session cookie file (0600, also shared with the $(…) subshells); the caller removes it with qbit_logout.
QBIT_USER=${QBIT_USER:-admin}
QBIT_PASS_FILE=${QBIT_PASS_FILE:-}
[ -n "${QBIT_JAR:-}" ] || QBIT_JAR=$(umask 077 && mktemp)

qbit_login() { # qbit_login <base url>
  if [ -z "$QBIT_PASS_FILE" ] || [ ! -r "$QBIT_PASS_FILE" ]; then echo "qBittorrent: 403 and no readable password file (QBIT_PASS_FILE)" >&2; return 1; fi
  local r
  r=$(tr -d '\r\n' < "$QBIT_PASS_FILE" | curl -s -m 10 -c "$QBIT_JAR" -w '\n%{http_code}' --data-urlencode "username=$QBIT_USER" --data-urlencode "password@-" "$1/api/v2/auth/login") || { echo "qBittorrent: login not reachable" >&2; return 1; }
  # qBittorrent 5: 204 empty; 4.x: 200 "Ok."; refused: "Fails." or 401/403
  [[ ${r##*$'\n'} == 2?? && ${r%$'\n'*} != Fails. ]] || { echo "qBittorrent: login refused" >&2; return 1; }
}

qbit_api() { # qbit_api <base url> <path> [curl args]
  local base=$1 path=$2 out code try; shift 2
  for try in 1 2; do
    out=$(curl -s -m "${QBIT_TIMEOUT:-10}" -b "$QBIT_JAR" -w '\n%{http_code}' "$@" "$base/api/v2$path") || return 1
    code=${out##*$'\n'}; out=${out%$'\n'*}
    if [ "$code" = 403 ] && [ "$try" = 1 ]; then qbit_login "$base" || return 1; continue; fi
    [ "${code:0:1}" = 2 ] || return 1
    printf '%s' "$out"; return 0
  done
}

qbit_logout() { [ -n "$QBIT_JAR" ] && rm -f "$QBIT_JAR"; QBIT_JAR=; }
