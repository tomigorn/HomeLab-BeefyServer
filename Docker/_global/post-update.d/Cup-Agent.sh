# Cup-Agent customizations (Docker/CUSTOMIZATIONS.md "Cup-Agent"). Sourced by post-update-check.sh.
if want cup-agent socket-proxy-unix; then
  check cup.api '^[1-9][0-9]* images$' "$(curl -s -m 8 http://127.0.0.1:8000/api/v3/json | jq -r '"\(.metrics.monitored_images) images"' 2>&1)"
fi
if want socket-proxy-ro; then
  check cup.proxy-read-only '^BUILD=0 EXEC=0 NETWORKS=0 POST=0 VOLUMES=0$' "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' cup-socket-proxy-ro | grep -E '^(POST|EXEC|BUILD|VOLUMES|NETWORKS)=' | sort | xargs)"
fi
# Rollback tags (rollback.invalid/..., UPDATE-RUNBOOK.md "Rollback tags") crash-loop Cup unless excluded, and hide an in-use image if they sort first.
if want cup-agent; then
  check cup.exclude-rollback '^rollback.invalid/ ignore=True$' "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(*[e for e in d["images"]["exclude"] if "rollback" in e], "ignore=%s" % d.get("registries", {}).get("rollback.invalid", {}).get("ignore"))' "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../Cup-Agent" && pwd)/cup.json" 2>&1)"
  check cup.rollback-tags-last '^0 images$' "$(docker image ls -q | sort -u | xargs -r docker image inspect -f '{{join .RepoTags " "}}' 2>/dev/null | awk '$1 ~ /^rollback\.invalid\// && $NF !~ /^rollback\.invalid\// {n++} END {print n+0, "images"}')"
  a=$(docker inspect -f '{{.Name}} {{.State.Status}} {{.RestartCount}}' cup-agent 2>&1); sleep 12; b=$(docker inspect -f '{{.Name}} {{.State.Status}} {{.RestartCount}}' cup-agent 2>&1)
  up=$(( $(date +%s) - $(date -d "$(docker inspect -f '{{.State.StartedAt}}' cup-agent 2>/dev/null)" +%s 2>/dev/null || date +%s) ))
  check cup.no-crash-loop '^stable, up [0-9]{2,} s' "$([[ $a == "$b" && $b == *running* ]] && echo "stable, up $up s: $b" || echo "restarting: $a -> $b")"
fi
