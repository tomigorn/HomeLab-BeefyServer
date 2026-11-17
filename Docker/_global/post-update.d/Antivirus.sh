# Antivirus customizations (Docker/CUSTOMIZATIONS.md "Antivirus"). Sourced by post-update-check.sh.
if want clamav; then
  check av.clamd-tcp '^PONG$' "$(docker exec clamav sh -c 'printf "zPING\0" | nc -w3 127.0.0.1 3310' 2>&1 | tr -d '\0')"
  check av.scan-branches '^/scan/ssd-hot /scan/ssd-hot2 ok ok$' "$(docker exec clamav sh -c 'echo $SCAN_ROOTS; for b in ssd-hot ssd-hot2; do test -w /scan/$b/quarantine && echo ok; done' 2>&1 | xargs)"
  check av.new-clamd '^1 6 1$' "$(docker exec clamav sh -c 'echo "$NEW_CLAMD $(sed -n "s/^MaxThreads //p" /etc/clamav/clamd.conf) $(grep -c "^clamd_new()" /scan.sh)"' 2>&1)"
  check av.clamd-isolated '^blocked$' "$(docker exec movie-sonarr sh -c 'nc -zw2 clamav 3310 && echo open || echo blocked' 2>&1 | tail -1)"
  check av.local-build '^clamav-local:' "$(docker inspect -f '{{.Config.Image}}' clamav 2>&1)"
fi
if want clamav-queue; then
  check av.queue-worker '^alive$' "$(docker exec clamav-queue sh -c 'find /tmp/clamav-queue-alive -mmin -15 | grep -q . && echo alive' 2>&1)"
  check av.parts-scan '^ffprobe version .* /tmp 192(\.0)?M$' "$(echo "$(docker exec clamav-queue ffprobe -version 2>&1 | head -1 | cut -c1-24) $(docker exec clamav-queue df -h /tmp 2>&1 | awk 'NR==2{print $6, $2}')")"
  check av.vt-key '^65112 400 ok$' "$(echo "$(stat -c '%u %a' "$DIR/../../Antivirus/secrets/vt_api_key" 2>&1) $(docker exec clamav-queue sh -c 'test -s /run/secrets/vt_api_key && echo ok' 2>&1)")"
  check av.vt-thread '^on$' "$(docker logs clamav-queue 2>&1 | grep -q 'VirusTotal fingerprint check on' && echo on)"
  check av.queue-perms '^65112:1100 2770 [0-9]+:1100 2770 65112:1100 2770 [0-9]+:1100 2770$' "$(stat -c '%u:%g %a' /srv/.disks/ssd-hot/.clamav-queue /srv/.disks/ssd-hot/quarantine /srv/.disks/ssd-hot2/.clamav-queue /srv/.disks/ssd-hot2/quarantine 2>&1 | xargs)"
fi
if want clamav clamav-hostscan; then
  # the nightly scan reads the SSD branches directly; its unit gives the clamav container the idle I/O class before each run (a recreate resets it)
  check av.scan-io-idle '^1$' "$(grep -c '^ExecStartPre=.*io\.prio\.class' /etc/systemd/system/clamav-scan.service 2>&1)"
  check av.timers '^active active$' "$(systemctl is-active clamav-scan.timer clamav-hostscan.timer | xargs)"
  check av.unit-sandbox '^installed installed ProtectSystem=strict ProtectSystem=strict$' "$(for u in clamav-scan clamav-hostscan; do cmp -s "$DIR/../../Antivirus/systemd/$u.service" /etc/systemd/system/$u.service && echo installed || echo differs; done | xargs) $(systemctl show -p ProtectSystem clamav-scan.service clamav-hostscan.service | xargs)"
fi
