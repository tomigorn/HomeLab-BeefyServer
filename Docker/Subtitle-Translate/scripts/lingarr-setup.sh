#!/usr/bin/env bash
# lingarr-setup.sh: idempotent first-run setup of Lingarr through its API: onboarding, one admin user, API key, then auth on (README "Lingarr").
# Writes secrets/lingarr.password and secrets/lingarr.apikey (600); never prints them.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
L=http://127.0.0.1:$LINGARR_PORT
umask 077; mkdir -p secrets
for i in $(seq 60); do curl -sf -o /dev/null "$L/" && break; sleep 2; done
if [[ $(curl -s -o /dev/null -w '%{http_code}' -H "X-Api-Key: $(cat secrets/lingarr.apikey 2>/dev/null)" "$L/api/translate/languages") == 200 ]]; then
  echo "lingarr: already set up, API key works"; exit 0
fi
[[ -s secrets/lingarr.password ]] || head -c 32 /dev/urandom | base64 | tr -d '/+=' > secrets/lingarr.password
jar=$(mktemp); trap 'rm -f "$jar"' EXIT
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$L/api/auth/onboarding" -H 'Content-Type: application/json' -d '{"enableUserAuth":"false"}')
[[ $code == 200 || $code == 409 ]] || { echo "lingarr: onboarding failed ($code)"; exit 1; }
if [[ $(curl -s "$L/api/auth/users/any") != true ]]; then
  jq -n --rawfile p secrets/lingarr.password '{username: "admin", password: ($p | rtrimstr("\n"))}' |
    curl -sf -c "$jar" -o /dev/null -X POST "$L/api/auth/signup" -H 'Content-Type: application/json' -d @- || { echo "lingarr: signup failed"; exit 1; }
else
  jq -n --rawfile p secrets/lingarr.password '{username: "admin", password: ($p | rtrimstr("\n"))}' |
    curl -sf -c "$jar" -o /dev/null -X POST "$L/api/auth/login" -H 'Content-Type: application/json' -d @- || { echo "lingarr: login failed (user exists, password unknown)"; exit 1; }
fi
curl -sf -b "$jar" -X POST "$L/api/auth/apikey/generate" | jq -er '.apiKey' > secrets/lingarr.apikey.new
mv secrets/lingarr.apikey.new secrets/lingarr.apikey
# AUTH_ENABLED=true in compose turns the login back on at start.
sudo docker compose restart lingarr >/dev/null
for i in $(seq 60); do curl -sf -o /dev/null "$L/" && break; sleep 2; done
anon=$(curl -s -o /dev/null -w '%{http_code}' "$L/api/translate/languages")
keyed=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Api-Key: $(cat secrets/lingarr.apikey)" "$L/api/translate/languages")
echo "lingarr: anonymous $anon (want 401), API key $keyed (want 200)"
[[ $anon == 401 && $keyed == 200 ]]
