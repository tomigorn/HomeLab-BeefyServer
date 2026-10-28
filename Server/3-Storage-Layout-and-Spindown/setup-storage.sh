#!/usr/bin/env bash
#
# beefy storage setup — implements Server/3-Storage-Layout-and-Spindown.md
#
#   NVMe              = OS (untouched)
#   HOT  SSD  (8TB)   = mergerfs hot tier, first branch                -> /srv/.disks/ssd-hot
#   HOT2 SSD  (8TB)   = mergerfs hot tier, second branch (ex-audio, added 2026-10-28) -> /srv/.disks/ssd-hot2
#   COLD HDD  (28TB)  = mergerfs cold tier                            -> /srv/.disks/hdd-cold
#   /srv/video        = mergerfs(ssd-hot, ssd-hot2, hdd-cold)   <- apps + Samba use this
#   Each SSD keeps 20 % (1479G) free for new files, the HDD 2800G (per-branch minfreespace).
#
# Everything is bound to DRIVE SERIAL (/dev/disk/by-id) and filesystem UUID.
# Nothing references /dev/sdX or a SATA port, so drives may be re-cabled to any slot.
#
# Usage (run as root):
#   sudo bash setup-storage.sh              provision (WIPES) + configure   [interactive YES]
#   sudo bash setup-storage.sh --yes        same, skip the confirm prompt
#   sudo bash setup-storage.sh --configure  configure only: NO wipe (mounts/fstab/services)
#
# >>> Provision WIPES THE THREE DATA DRIVES BELOW. The NVMe OS disk is never touched. <<<

set -euo pipefail

# ── Device assignment — BY SERIAL (never sdX, never SATA port) ───────────────
HOT_SSD=/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0WA00268B    # hot tier (with cold HDD)
COLD_HDD=/dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M            # 28TB cold tier
HOT2_SSD=/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0W909892P   # second hot branch (ex-audio spare)

MODE=full; ASSUME_YES=0
for a in "$@"; do
  case "$a" in
    --configure) MODE=configure ;;
    --yes) ASSUME_YES=1 ;;
    *) echo "unknown arg: $a"; exit 1 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "Must run as root (sudo)."; exit 1; }

OS_DISK=$(readlink -f "$(findmnt -no SOURCE / | sed 's/p\?[0-9]*$//')" 2>/dev/null || true)

check() {  # check <label> <by-id> <min-bytes> <max-bytes>
  local label=$1 id=$2 min=$3 max=$4 real sz
  [[ -e "$id" ]] || { echo "FATAL: $label not found: $id"; exit 1; }
  real=$(readlink -f "$id")
  [[ "$real" != "$OS_DISK" ]] || { echo "FATAL: $label resolves to the OS disk ($real)!"; exit 1; }
  sz=$(blockdev --getsize64 "$real")
  (( sz >= min && sz <= max )) || { echo "FATAL: $label ($real) size $sz out of expected range."; exit 1; }
  echo "  OK  $label -> $real  ($(numfmt --to=iec "$sz"))  serial=$(basename "$id")"
}

provision() {
  echo "Verifying target drives by serial:"
  for real in "$(readlink -f "$HOT_SSD")" "$(readlink -f "$HOT2_SSD")" "$(readlink -f "$COLD_HDD")"; do
    ! findmnt -rno SOURCE | grep -q "^${real}" || { echo "FATAL: $real has a mounted partition. Aborting."; exit 1; }
  done
  check "HOT  SSD" "$HOT_SSD"   7000000000000  9000000000000
  check "HOT2 SSD" "$HOT2_SSD"  7000000000000  9000000000000
  check "COLD HDD" "$COLD_HDD"  25000000000000 32000000000000

  # Re-wipe guard: refuse if drives already carry our labels (use --configure to finish).
  if blkid -o value -s LABEL "${COLD_HDD}-part1" 2>/dev/null | grep -qx hdd-cold; then
    echo "Drives already provisioned (label 'hdd-cold' present)."
    echo "Run with --configure to finish setup without wiping. Aborting."
    exit 1
  fi

  if [[ $ASSUME_YES -eq 0 ]]; then
    echo; echo "ALL DATA on the three drives above will be ERASED."
    read -r -p 'Type YES to proceed: ' ans
    [[ "$ans" == "YES" ]] || { echo "Aborted."; exit 1; }
  fi

  echo "Installing packages..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq mergerfs hd-idle smartmontools xfsprogs gdisk parted util-linux

  vgchange -an 2>/dev/null || true
  wipe_dev() {
    local d=$1 p
    for p in "$d"-part*; do [[ -e "$p" ]] && wipefs -a "$p" || true; done
    wipefs -a "$d" || true
    sgdisk --zap-all "$d" >/dev/null 2>&1 || true
    dd if=/dev/zero of="$d" bs=1M count=16 conv=fsync status=none
  }
  echo "Wiping drives..."
  wipe_dev "$HOT_SSD"; wipe_dev "$HOT2_SSD"; wipe_dev "$COLD_HDD"

  for d in "$HOT_SSD" "$HOT2_SSD" "$COLD_HDD"; do
    sgdisk -n 1:0:0 -t 1:8300 "$d" >/dev/null
  done
  partprobe; udevadm settle

  mkfs.ext4 -F -m 0 -L ssd-hot "${HOT_SSD}-part1"
  mkfs.ext4 -F -m 0 -L ssd-hot2 "${HOT2_SSD}-part1"
  mkfs.xfs  -f -L hdd-cold "${COLD_HDD}-part1"
  udevadm settle
}

configure() {
  # Clean any partial mounts (idempotent / safe to re-run).
  umount /srv/video 2>/dev/null || fusermount -u /srv/video 2>/dev/null || true
  umount /srv/.disks/hdd-cold /srv/.disks/ssd-hot /srv/.disks/ssd-hot2 2>/dev/null || true

  # Sanity: partitions must already be formatted as expected.
  [[ "$(blkid -s TYPE -o value "${COLD_HDD}-part1")" == xfs  ]] || { echo "FATAL: cold partition is not xfs."; exit 1; }
  [[ "$(blkid -s TYPE -o value "${HOT_SSD}-part1")"  == ext4 ]] || { echo "FATAL: hot partition is not ext4."; exit 1; }
  [[ "$(blkid -s TYPE -o value "${HOT2_SSD}-part1")" == ext4 ]] || { echo "FATAL: hot2 partition is not ext4."; exit 1; }

  local HOT_UUID HOT2_UUID COLD_UUID
  HOT_UUID=$(blkid -s UUID -o value "${HOT_SSD}-part1")
  HOT2_UUID=$(blkid -s UUID -o value "${HOT2_SSD}-part1")
  COLD_UUID=$(blkid -s UUID -o value "${COLD_HDD}-part1")

  mkdir -p /srv/.disks/ssd-hot /srv/.disks/ssd-hot2 /srv/.disks/hdd-cold /srv/video

  local MERGER_OPTS="defaults,nofail,allow_other,cache.files=partial,dropcacheonclose=true,category.create=ff,minfreespace=50G,moveonenospc=true,statfs=base,fsname=mergerfs,x-systemd.requires=/srv/.disks/ssd-hot,x-systemd.requires=/srv/.disks/ssd-hot2,x-systemd.requires=/srv/.disks/hdd-cold"
  sed -i '/# >>> beefy-storage/,/# <<< beefy-storage/d' /etc/fstab
  cat >> /etc/fstab <<EOF
# >>> beefy-storage (managed by setup-storage.sh) >>>
UUID=$HOT_UUID    /srv/.disks/ssd-hot   ext4  relatime,nofail,x-systemd.device-timeout=30s   0 2
UUID=$HOT2_UUID   /srv/.disks/ssd-hot2  ext4  relatime,nofail,x-systemd.device-timeout=30s   0 2
UUID=$COLD_UUID   /srv/.disks/hdd-cold  xfs   noatime,nofail,x-systemd.device-timeout=30s    0 2
/srv/.disks/ssd-hot=RW,1479G:/srv/.disks/ssd-hot2=RW,1479G:/srv/.disks/hdd-cold=RW,2800G  /srv/video  fuse.mergerfs  $MERGER_OPTS  0 0
# <<< beefy-storage (managed by setup-storage.sh) <<<
EOF

  sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf
  mount --make-rshared / || true
  systemctl daemon-reload
  mount -a

  # Spin-down: hd-idle parks ONLY the cold HDD (by serial), 20 min idle.
  cat > /etc/default/hd-idle <<EOF
START_HD_IDLE=true
# NOTE: -a must be the FULL by-id PATH (a bare basename won't resolve -> hd-idle
# silently applies its default of "never spin down"). -s 1 re-resolves at runtime.
HD_IDLE_OPTS="-s 1 -i 0 -a /dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M -i 1200 -l /var/log/hd-idle.log"
EOF
  systemctl enable hd-idle 2>/dev/null || true
  systemctl restart hd-idle 2>/dev/null || true

  # smartd: don't wake the HDD for polls.
  if ! grep -q 'beefy-storage' /etc/smartd.conf 2>/dev/null; then
    sed -i 's/^DEVICESCAN/#DEVICESCAN  # beefy-storage: explicit lines below/' /etc/smartd.conf 2>/dev/null || true
    cat >> /etc/smartd.conf <<EOF
# beefy-storage
$COLD_HDD -a -n standby
$HOT_SSD -a
$HOT2_SSD -a
EOF
  fi
  systemctl restart smartmontools 2>/dev/null || systemctl restart smartd 2>/dev/null || true

  systemctl enable --now fstrim.timer

  echo; echo "================ RESULT ================"
  lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT "$HOT_SSD" "$COLD_HDD" "$HOT2_SSD"
  echo "---- findmnt /srv ----"; findmnt -R /srv || true
  echo "---- write/read test ----"
  echo ok > /srv/video/.write-test && cat /srv/video/.write-test && rm -f /srv/video/.write-test
  echo "---- hd-idle ----"; systemctl is-active hd-idle || true
  echo "Done. Storage is up. (nofail is safe only with the docker.service drop-in, added in the Docker phase: storage doc 14.2-F.)"
}

[[ "$MODE" == full ]] && provision
configure
