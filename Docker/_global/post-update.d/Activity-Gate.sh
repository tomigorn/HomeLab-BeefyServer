# Activity-Gate customizations (Docker/CUSTOMIZATIONS.md "Activity-Gate"). Sourced by post-update-check.sh.
GATE_DIR="$DIR/../../Activity-Gate"
gate_codes() { local m=$1; shift; for u in "$@"; do docker exec activity-gate curl -s -m 8 -o /dev/null -w '%{http_code} ' -X "$m" "http://127.0.0.1:8080$u" 2>/dev/null || echo -n 'err '; done | xargs; }
if want gate; then
  check gate.image-pin '^pinned, running = pin$' "$(ref=$(docker compose --project-directory "$GATE_DIR" config --format json 2>/dev/null | jq -r '.services.gate.image'); [[ $ref == *@sha256:* ]] && echo -n pinned || echo -n "not pinned: $ref"; id=$(docker image inspect -f '{{.Id}}' "$ref" 2>/dev/null); [[ -n $id && $id == "$(docker inspect -f '{{.Image}}' activity-gate 2>/dev/null)" ]] && echo ", running = pin" || echo ", running differs")"
  check gate.config '^ok$' "$(docker exec activity-gate nginx -t -c /gate/nginx.conf 2>&1 | grep -q 'test is successful' && echo ok || echo broken)"
  # every path of the contract (fastpi rust/src/beefy.rs PATH_*) answers
  check gate.allowlist '^200 200 200 200 200 200$' "$(gate_codes GET '/jellyfin/Sessions?ActiveWithinSeconds=60' '/radarr/api/v3/queue?pageSize=200&includeMovie=true' '/sonarr/api/v3/queue?pageSize=500&includeSeries=true&includeEpisode=true' /sonarr/api/v3/series /seerr/request-state.json /seerr/night-waiting.json)"
  check gate.refusals '^404 404 404 404 404 404 405$' "$(echo "$(gate_codes GET /jellyfin/Sessions '/jellyfin/Sessions?ActiveWithinSeconds=61' /jellyfin/Users '/radarr/api/v3/queue?pageSize=200&includeMovie=true&x=1' /seerr/usage.json /) $(gate_codes POST /sonarr/api/v3/series)")"
  # any client but fastpi: the host reaches it through docker-proxy, so the gate sees a bridge address
  check gate.other-client '^403$' "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$(sed -n 's/^GATE_PORT=//p' "$GATE_DIR/.env")/health" 2>&1)"
  check gate.idle-exclude '^excluded$' "$(p=$(sed -n 's/^GATE_PORT=//p' "$GATE_DIR/.env"); grep -qE "^EXCLUDE_PORTS=\"?([0-9 ]* )?$p( [0-9 ]*)?\"?$" /etc/beefy-idle.conf && echo excluded || echo "not excluded: $p")"
  check gate.secrets '^440 65131 440 65131 440 65131$' "$(stat -c '%a %g' "$GATE_DIR"/secrets/{jellyfin,radarr,sonarr}.apikey 2>&1 | xargs)"
fi
