# Antivirus customizations (Docker/CUSTOMIZATIONS.md "Antivirus"). Sourced by post-update-check.sh.
if want clamav; then
  check av.clamd-tcp '^PONG$' "$(docker exec clamav sh -c 'printf "zPING\0" | nc -w3 127.0.0.1 3310' 2>&1 | tr -d '\0')"
  check av.scan-branches '^/scan/ssd-hot /scan/ssd-hot2 ok ok$' "$(docker exec clamav sh -c 'echo $SCAN_ROOTS; for b in ssd-hot ssd-hot2; do test -w /scan/$b/quarantine && echo ok; done' 2>&1 | xargs)"
  check av.local-build '^clamav-local:' "$(docker inspect -f '{{.Config.Image}}' clamav 2>&1)"
fi
if want clamav-queue; then
  check av.queue-worker '^alive$' "$(docker exec clamav-queue sh -c 'find /tmp/clamav-queue-alive -mmin -15 | grep -q . && echo alive' 2>&1)"
  check av.queue-perms '^65112:1100 2770 [0-9]+:1100 2770 65112:1100 2770 [0-9]+:1100 2770$' "$(stat -c '%u:%g %a' /srv/.disks/ssd-hot/.clamav-queue /srv/.disks/ssd-hot/quarantine /srv/.disks/ssd-hot2/.clamav-queue /srv/.disks/ssd-hot2/quarantine 2>&1 | xargs)"
fi
if want clamav clamav-hostscan; then
  check av.timers '^active active$' "$(systemctl is-active clamav-scan.timer clamav-hostscan.timer | xargs)"
fi
