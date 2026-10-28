# Dependency-Track customizations (Docker/CUSTOMIZATIONS.md "Dependency-Track"). Sourced by post-update-check.sh.
# v5 image: busybox wget, no curl, no getent (extra_hosts lands in /etc/hosts).
if want dt5-apiserver; then
  check dt.oidc-available '^true$' "$(curl -s -m 8 http://127.0.0.1:8090/api/v1/oidc/available)"
  check dt.team-mapping '^homelab-admins\|Administrators$' "$(docker exec dt5-db sh -c 'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-dtrack}" -Atc "SELECT g.\"NAME\", t.\"NAME\" FROM \"MAPPEDOIDCGROUP\" m JOIN \"OIDCGROUP\" g ON g.\"ID\" = m.\"GROUP_ID\" JOIN \"TEAM\" t ON t.\"ID\" = m.\"TEAM_ID\""' 2>&1 | xargs)"
  check dt.sso-reach '^192\.168\.1\.2 200$' "$(docker exec dt5-apiserver awk '$2 == "sso.holy-grail.ch" {print $1; exit}' /etc/hosts) $(docker exec dt5-apiserver wget -q -S --no-check-certificate -T 8 -O /dev/null https://sso.holy-grail.ch/application/o/dt-login/.well-known/openid-configuration 2>&1 | awk '/HTTP\//{c=$2} END{print c}')"
  check dt.tmp-exec '^tmpfs rw,' "$(docker exec dt5-apiserver awk '$2 == "/tmp" {print $3, $4}' /proc/mounts | grep -v noexec)"
fi
if want dt5-frontend; then
  check dt.frontend-oidc '^https://sso\.holy-grail\.ch/application/o/dt-login/ [1-9][0-9]*$' "$(curl -s -m 8 http://127.0.0.1:8090/static/config.json | jq -r '"\(.OIDC_ISSUER) \(.OIDC_CLIENT_ID | length)"' 2>&1)"
fi
