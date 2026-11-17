#!/usr/bin/env bash
# Install/refresh /srv/dlpool: a second mergerfs view of the pool for the download containers, at idle I/O class (Movie-Downloads README "Disk priority"). Run with sudo on beefy.
# --print-line: only print the fstab line it would write (FSTAB, LIVE_CREATE override the sources; test-install-dlpool)
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
MP=/srv/dlpool
FSTAB=${FSTAB:-/etc/fstab}
case ${1:-} in -h|--help) echo "usage: sudo $0 [--print-line]   install /srv/dlpool (fstab line from the pool, idle-class unit, docker drop-in)"; exit 0 ;; esac

main=$(awk '$2 == "/srv/video" && $3 == "fuse.mergerfs"' "$FSTAB")
[ "$(printf '%s\n' "$main" | grep -c .)" = 1 ] || { echo "no single /srv/video mergerfs line in $FSTAB" >&2; exit 1; }
live=${LIVE_CREATE:-$(getfattr --only-values -n user.mergerfs.category.create /srv/video/.mergerfs 2>/dev/null)}
grep -q "category.create=$live[,[:space:]]" <<<"$main" || { echo "fstab category.create differs from the live pool ($live): persist the pool first" >&2; echo "  check: getfattr -n user.mergerfs.category.create /srv/video/.mergerfs; grep /srv/video /etc/fstab" >&2; exit 1; }

# Same branches and options as the pool, own mount point and fsname
line=$(awk -v mp="$MP" '{ $2 = mp; sub(/fsname=mergerfs/, "fsname=mergerfs-dl", $4); print }' <<<"$main")
[ "${1:-}" = --print-line ] && { printf '%s\n' "$line"; exit 0; }
tmp=$(mktemp)
awk -v mp="$MP" '$2 != mp' /etc/fstab > "$tmp"
printf '%s\n' "$line" >> "$tmp"
cmp -s "$tmp" /etc/fstab || { cp /etc/fstab "/etc/fstab.bak-dlpool-$(date +%Y%m%d%H%M%S)"; install -m 0644 "$tmp" /etc/fstab; }
rm -f "$tmp"

install -d -m 0755 "$MP"
install -m 0644 "$D/dlpool-ioprio.service" /etc/systemd/system/dlpool-ioprio.service
install -d -m 0755 /etc/systemd/system/docker.service.d
install -m 0644 "$D/11-require-dlpool.conf" /etc/systemd/system/docker.service.d/11-require-dlpool.conf
systemctl daemon-reload
systemctl enable dlpool-ioprio.service
systemctl start srv-dlpool.mount dlpool-ioprio.service

echo "installed: $(findmnt -no SOURCE,FSTYPE "$MP") class=$(cat /sys/fs/cgroup/system.slice/srv-dlpool.mount/io.prio.class)"
echo "  next:   the download containers on it: Movie-Downloads README \"Disk priority\""
echo "  check:  Docker/_global/scripts/post-update-check.sh Host"
