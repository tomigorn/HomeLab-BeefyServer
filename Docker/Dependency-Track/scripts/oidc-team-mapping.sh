#!/usr/bin/env bash
# Idempotent: maps the Holy Grail group homelab-admins (OIDC groups claim) to DT team Administrators. Run on beefy: bash scripts/oidc-team-mapping.sh
set -euo pipefail
A=http://127.0.0.1:8090/api
pw=$(cat ~/Projects/Docker/Dependency-Track/secrets/dt_admin_password)
jwt=$(curl -sf -X POST --data-urlencode username=admin --data-urlencode "password=$pw" $A/v1/user/login); unset pw
H=(-H "Authorization: Bearer $jwt" -H "Content-Type: application/json")
admins=$(curl -sf "${H[@]}" $A/v1/team | python3 -c 'import json,sys; print([t["uuid"] for t in json.load(sys.stdin) if t["name"]=="Administrators"][0])')
g=$(curl -sf "${H[@]}" $A/v1/oidc/group | python3 -c 'import json,sys; print(next((x["uuid"] for x in json.load(sys.stdin) if x["name"]=="homelab-admins"), ""))')
[[ -n $g ]] || g=$(curl -sf -X PUT "${H[@]}" -d '{"name":"homelab-admins"}' $A/v1/oidc/group | python3 -c 'import json,sys; print(json.load(sys.stdin)["uuid"])')
mapped=$(curl -sf "${H[@]}" $A/v1/oidc/group/$g/team | python3 -c "import json,sys; print(any(t['uuid']=='$admins' for t in json.load(sys.stdin)))")
[[ $mapped == True ]] || curl -sf -o /dev/null -X PUT "${H[@]}" -d "{\"team\":\"$admins\",\"group\":\"$g\"}" $A/v1/oidc/mapping
curl -sf "${H[@]}" $A/v1/oidc/group/$g/team | python3 -c 'import json,sys; print("homelab-admins ->", [t["name"] for t in json.load(sys.stdin)])'
