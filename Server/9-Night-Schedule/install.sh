#!/usr/bin/env bash
# Install the beefy night schedule: maintenance window unit, timer drop-ins that move the catch-up jobs into 03:58-04:45, telemetry off. Run on beefy as buntu (uses sudo). Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUNTU_UID=$(id -u buntu)
user_ctl() { sudo -u buntu XDG_RUNTIME_DIR="/run/user/$BUNTU_UID" systemctl --user "$@"; }

echo "==> maintenance window"
sudo install -m 0755 "$HERE/beefy-maintenance-window" /usr/local/sbin/beefy-maintenance-window
sudo install -m 0644 "$HERE/beefy-maintenance-window.service" "$HERE/beefy-maintenance-window.timer" /etc/systemd/system/

echo "==> system timer drop-ins"
for d in "$HERE"/dropins/*.d; do
    u=$(basename "$d")
    sudo install -d -m 0755 "/etc/systemd/system/$u"
    sudo install -m 0644 "$d"/night-window.conf "/etc/systemd/system/$u/night-window.conf"
done

echo "==> user (buntu) timer drop-ins"
for d in "$HERE"/user-dropins/*.d; do
    u=$(basename "$d")
    sudo -u buntu install -d -m 0755 "/home/buntu/.config/systemd/user/$u"
    sudo -u buntu install -m 0644 "$d"/night-window.conf "/home/buntu/.config/systemd/user/$u/night-window.conf"
done

echo "==> timers tracked in other projects, now inside the window"
R="$HERE/../.."
sudo install -m 0644 "$R"/Docker/Movie-Downloads/systemd/{description-scout,nfo-scout,night-search,language-missing,original-audio,subtitle-check}.timer \
    "$R"/Docker/Movie-Downloads/systemd/original-audio-import.{path,service} \
    "$R"/Server/3-Storage-Layout-and-Spindown/beefy-mover.timer "$R"/Docker/_global/systemd/{auto-update,image-cleanup}.timer \
    "$R"/Docker/Antivirus/systemd/clamav-{scan,hostscan}.timer /etc/systemd/system/

echo "==> anacron: timer only (the boot start would run cron.daily at once)"
sudo systemctl disable anacron.service 2>&1 | tail -1 || true

echo "==> telemetry and ads off"
sudo systemctl mask --now motd-news.timer apport-autoreport.timer apport-autoreport.path ua-timer.timer
user_ctl mask --now ubuntu-insights-collect.timer ubuntu-insights-upload.timer snap.firmware-updater.firmware-notifier.timer
sudo pro config set apt_news=false 2>&1 | tail -1 || true

echo "==> snap refresh inside the window"
sudo snap set system refresh.timer=04:30-04:44

sudo systemctl daemon-reload
user_ctl daemon-reload
sudo systemctl enable --now beefy-maintenance-window.timer original-audio-import.path
# a running timer keeps its old schedule until restarted
sudo systemctl restart $(for d in "$HERE"/dropins/*.d; do u=$(basename "$d"); echo "${u%.d}"; done) description-scout.timer nfo-scout.timer night-search.timer language-missing.timer original-audio.timer subtitle-check.timer beefy-mover.timer
user_ctl restart $(for d in "$HERE"/user-dropins/*.d; do u=$(basename "$d"); echo "${u%.d}"; done)
systemctl list-timers --no-pager
