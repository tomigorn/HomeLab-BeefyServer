# Floppy customizations (Docker/CUSTOMIZATIONS.md "Floppy"). Sourced by post-update-check.sh.
FLOPPY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../Floppy" && pwd)
fl_page() { curl -s -m 8 -H 'Host: tracker.holy-grail.ch' -H 'X-Forwarded-Proto: https' "http://127.0.0.1:8012$1"; }
fl_ip() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' floppy | tr ' ' '\n' | grep '^172\.28\.16\.' | head -1; }
if want floppy; then
  check floppy.image-pin '@sha256:5e88307d4af6' "$(docker inspect -f '{{.Config.Image}}' floppy 2>&1)"
  check floppy.security-env '^ADMIN_ENABLED=False COMICVINE_API= CSRF_COOKIE_SECURE=True DEBUG=False DEMO_ACCOUNT_ENABLED=False SESSION_COOKIE_SECURE=True SOCIALACCOUNT_ONLY=True USE_X_FORWARDED_HOST=False$' "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' floppy | grep -E '^(SOCIALACCOUNT_ONLY|ADMIN_ENABLED|DEBUG|DEMO_ACCOUNT_ENABLED|COMICVINE_API|SESSION_COOKIE_SECURE|CSRF_COOKIE_SECURE|USE_X_FORWARDED_HOST)=' | sort | xargs)"
  check floppy.sso-only-login '^sso 0 password$' "$(p=$(fl_page /accounts/login/); echo "$(grep -q 'Holy Grail' <<<"$p" && echo sso || echo no-sso) $(grep -c 'name="password"' <<<"$p") password")"
  check floppy.seerr-webhook-secret '^401$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' -X POST -H 'Host: 192.168.1.102' -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8012/webhook/seerr/global/)"
  check floppy.subnet '^172\.28\.16\.0/24 172\.28\.16\.[0-9]+ internal-redis=true$' "$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' floppy_floppy 2>/dev/null || docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' floppy 2>&1) $(fl_ip) internal-redis=$(docker network inspect -f '{{.Internal}}' floppy_floppy-redis 2>/dev/null || docker network inspect -f '{{.Internal}}' floppy-redis 2>&1)"
  check floppy.egress-lan-blocked '^blocked$' "$(docker exec floppy nc -zw2 192.168.1.2 443 >/dev/null 2>&1 && echo open || echo blocked)"
  check floppy.egress-internet '^open$' "$(docker exec floppy nc -zw4 api.themoviedb.org 443 >/dev/null 2>&1 && echo open || echo blocked)"
  check floppy.ingress-narrowed '^jellyfin=open seerr=open sonarr=blocked$' "$(for c in jellyfin seerr movie-sonarr; do printf '%s=%s ' "${c#movie-}" "$(docker exec "$c" nc -zw2 192.168.1.102 8012 >/dev/null 2>&1 && echo open || echo blocked)"; done | xargs)"
  check floppy.abs-pull-off '^0 tasks 0 keys$' "$(docker exec -u abc -w /floppy floppy python manage.py shell -c "from django_celery_beat.models import PeriodicTask; from integrations.models import AudiobookshelfAccount; print(PeriodicTask.objects.filter(task__icontains='audiobookshelf').count(), 'tasks', AudiobookshelfAccount.objects.exclude(api_token='pushed-by-abs-push').count(), 'keys')" 2>&1 | tail -1)"
  check floppy.abs-push-selftest '^ok dry-run$' "$(s=; [ -w /var/run/docker.sock ] || s="sudo -n"; $s python3 -I /usr/local/lib/floppy-abs-push/abs-push.py selftest 2>&1 | tail -1)"
  check floppy.abs-push-timer '^enabled success$' "$(systemctl is-enabled floppy-abs-push.timer 2>&1) $(systemctl show -p Result --value floppy-abs-push.service 2>&1)"
  check floppy.abs-push-key '^root:root 600$' "$(stat -c '%U:%G %a' "$FLOPPY_DIR/secrets/floppy-sync-abs-key" 2>&1)"
  check floppy.abs-push-root-copy '^root:root 644 root:root 644 same$' "$(stat -c '%U:%G %a' /usr/local/lib/floppy-abs-push/abs-push.py /usr/local/lib/floppy-abs-push/abs-push-apply.py 2>&1 | xargs) $(cmp -s "$FLOPPY_DIR/scripts/abs-push.py" /usr/local/lib/floppy-abs-push/abs-push.py && cmp -s "$FLOPPY_DIR/scripts/abs-push-apply.py" /usr/local/lib/floppy-abs-push/abs-push-apply.py && echo same || echo differs)"
fi
