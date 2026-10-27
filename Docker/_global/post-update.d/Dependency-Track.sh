# Dependency-Track customizations (Docker/CUSTOMIZATIONS.md "Dependency-Track"). Sourced by post-update-check.sh.
if want dt-apiserver; then
  check dt.oidc-available '^true$' "$(curl -s -m 8 http://127.0.0.1:8090/api/v1/oidc/available)"
  check dt.team-mapping '^homelab-admins\|Administrators$' "$(docker exec dt-db sh -c 'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-dtrack}" -Atc "SELECT g.\"NAME\", t.\"NAME\" FROM \"MAPPEDOIDCGROUP\" m JOIN \"OIDCGROUP\" g ON g.\"ID\" = m.\"GROUP_ID\" JOIN \"TEAM\" t ON t.\"ID\" = m.\"TEAM_ID\""' 2>&1 | xargs)"
  check dt.sso-reach '^192\.168\.1\.2 200$' "$(docker exec dt-apiserver getent ahostsv4 sso.holy-grail.ch | awk 'NR==1{print $1}') $(docker exec dt-apiserver curl -sk -m 8 -o /dev/null -w '%{http_code}' https://sso.holy-grail.ch/application/o/dt-login/.well-known/openid-configuration 2>&1)"
fi
if want dt-frontend; then
  check dt.frontend-oidc '^https://sso\.holy-grail\.ch/application/o/dt-login/ [1-9][0-9]*$' "$(curl -s -m 8 http://127.0.0.1:8090/static/config.json | jq -r '"\(.OIDC_ISSUER) \(.OIDC_CLIENT_ID | length)"' 2>&1)"
fi
