# Cup-Agent customizations (Docker/CUSTOMIZATIONS.md "Cup-Agent"). Sourced by post-update-check.sh.
if want cup-agent socket-proxy-unix; then
  check cup.api '^[1-9][0-9]* images$' "$(curl -s -m 8 http://127.0.0.1:8000/api/v3/json | jq -r '"\(.metrics.monitored_images) images"' 2>&1)"
fi
if want socket-proxy-ro; then
  check cup.proxy-read-only '^BUILD=0 EXEC=0 NETWORKS=0 POST=0 VOLUMES=0$' "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' cup-socket-proxy-ro | grep -E '^(POST|EXEC|BUILD|VOLUMES|NETWORKS)=' | sort | xargs)"
fi
