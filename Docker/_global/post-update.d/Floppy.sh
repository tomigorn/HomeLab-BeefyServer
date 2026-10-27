# Floppy customizations (Docker/CUSTOMIZATIONS.md "Floppy"). Sourced by post-update-check.sh.
fl_page() { curl -s -m 8 -H 'Host: tracker.holy-grail.ch' -H 'X-Forwarded-Proto: https' "http://127.0.0.1:8012$1"; }
fl_ip() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' floppy | tr ' ' '\n' | grep '^172\.28\.16\.' | head -1; }
if want floppy; then
  check floppy.image-pin '@sha256:c4959b1f2544' "$(docker inspect -f '{{.Config.Image}}' floppy 2>&1)"
  check floppy.security-env '^ADMIN_ENABLED=False COMICVINE_API= CSRF_COOKIE_SECURE=True DEBUG=False DEMO_ACCOUNT_ENABLED=False SESSION_COOKIE_SECURE=True SOCIALACCOUNT_ONLY=True USE_X_FORWARDED_HOST=False$' "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' floppy | grep -E '^(SOCIALACCOUNT_ONLY|ADMIN_ENABLED|DEBUG|DEMO_ACCOUNT_ENABLED|COMICVINE_API|SESSION_COOKIE_SECURE|CSRF_COOKIE_SECURE|USE_X_FORWARDED_HOST)=' | sort | xargs)"
  check floppy.sso-only-login '^sso 0 password$' "$(p=$(fl_page /accounts/login/); echo "$(grep -q 'Holy Grail' <<<"$p" && echo sso || echo no-sso) $(grep -c 'name="password"' <<<"$p") password")"
  check floppy.seerr-webhook-secret '^401$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' -X POST -H 'Host: 192.168.1.102' -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8012/webhook/seerr/global/)"
  check floppy.subnet '^172\.28\.16\.0/24 172\.28\.16\.[0-9]+ internal-redis=true$' "$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' floppy_floppy 2>/dev/null || docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' floppy 2>&1) $(fl_ip) internal-redis=$(docker network inspect -f '{{.Internal}}' floppy_floppy-redis 2>/dev/null || docker network inspect -f '{{.Internal}}' floppy-redis 2>&1)"
  check floppy.egress-lan-blocked '^blocked$' "$(docker exec floppy nc -zw2 192.168.1.2 443 >/dev/null 2>&1 && echo open || echo blocked)"
  check floppy.egress-internet '^open$' "$(docker exec floppy nc -zw4 api.themoviedb.org 443 >/dev/null 2>&1 && echo open || echo blocked)"
  check floppy.ingress-narrowed '^jellyfin=open seerr=open sonarr=blocked$' "$(for c in jellyfin seerr movie-sonarr; do printf '%s=%s ' "${c#movie-}" "$(docker exec "$c" nc -zw2 192.168.1.102 8012 >/dev/null 2>&1 && echo open || echo blocked)"; done | xargs)"
fi
