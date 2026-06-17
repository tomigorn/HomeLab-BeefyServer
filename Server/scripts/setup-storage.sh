#!/usr/bin/env bash
#
# beefy storage setup — implements Server/3-Storage-Layout-and-Spindown.md
#
#   NVMe              = OS (untouched)
#   HOT  SSD  (8TB)   = mergerfs hot tier, paired with the cold HDD  -> /srv/.disks/ssd-hot
#   AUDIO SSD (8TB)   = audiobooks / music / cloud                    -> /srv/audio
#   COLD HDD  (28TB)  = mergerfs cold tier                            -> /srv/.disks/hdd-cold
#   /srv/video        = mergerfs(ssd-hot, hdd-cold)   <- apps + Samba use this
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
AUDIO_SSD=/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0W909892P  # audiobooks / music / cloud
COLD_HDD=/dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M            # 28TB cold tier

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
  for real in "$(readlink -f "$HOT_SSD")" "$(readlink -f "$AUDIO_SSD")" "$(readlink -f "$COLD_HDD")"; do
    ! findmnt -rno SOURCE | grep -q "^${real}" || { echo "FATAL: $real has a mounted partition. Aborting."; exit 1; }
  done
  check "HOT  SSD" "$HOT_SSD"   7000000000000  9000000000000
  check "AUDIO SSD" "$AUDIO_SSD" 7000000000000  9000000000000
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
  wipe_dev "$HOT_SSD"; wipe_dev "$AUDIO_SSD"; wipe_dev "$COLD_HDD"

  for d in "$HOT_SSD" "$AUDIO_SSD" "$COLD_HDD"; do
    sgdisk -n 1:0:0 -t 1:8300 "$d" >/dev/null
  done
  partprobe; udevadm settle

  mkfs.ext4 -F -L ssd-hot "${HOT_SSD}-part1"
  mkfs.ext4 -F -L audio   "${AUDIO_SSD}-part1"
  mkfs.xfs  -f -L hdd-cold "${COLD_HDD}-part1"
  udevadm settle
}

configure() {
  # Clean any partial mounts (idempotent / safe to re-run).
  umount /srv/video 2>/dev/null || fusermount -u /srv/video 2>/dev/null || true
  umount /srv/.disks/hdd-cold /srv/.disks/ssd-hot /srv/audio 2>/dev/null || true

  # Sanity: partitions must already be formatted as expected.
  [[ "$(blkid -s TYPE -o value "${COLD_HDD}-part1")" == xfs  ]] || { echo "FATAL: cold partition is not xfs."; exit 1; }
  [[ "$(blkid -s TYPE -o value "${HOT_SSD}-part1")"  == ext4 ]] || { echo "FATAL: hot partition is not ext4."; exit 1; }

  local HOT_UUID AUDIO_UUID COLD_UUID
  HOT_UUID=$(blkid -s UUID -o value "${HOT_SSD}-part1")
  AUDIO_UUID=$(blkid -s UUID -o value "${AUDIO_SSD}-part1")
  COLD_UUID=$(blkid -s UUID -o value "${COLD_HDD}-part1")

  mkdir -p /srv/audio /srv/.disks/ssd-hot /srv/.disks/hdd-cold /srv/video

  local MERGER_OPTS="defaults,allow_other,use_ino,cache.files=partial,dropcacheonclose=true,category.create=ff,minfreespace=50G,moveonenospc=true,statfs=base,fsname=mergerfs,x-systemd.requires=/srv/.disks/ssd-hot,x-systemd.requires=/srv/.disks/hdd-cold"
  sed -i '/# >>> beefy-storage/,/# <<< beefy-storage/d' /etc/fstab
  cat >> /etc/fstab <<EOF
# >>> beefy-storage (managed by setup-storage.sh) >>>
UUID=$AUDIO_UUID  /srv/audio            ext4  noatime    0 2
UUID=$HOT_UUID    /srv/.disks/ssd-hot   ext4  relatime   0 2
UUID=$COLD_UUID   /srv/.disks/hdd-cold  xfs   noatime    0 2
/srv/.disks/ssd-hot:/srv/.disks/hdd-cold  /srv/video  fuse.mergerfs  $MERGER_OPTS  0 0
# <<< beefy-storage (managed by setup-storage.sh) <<<
EOF

  sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf
  mount --make-rshared / || true
  systemctl daemon-reload
  mount -a

  # Spin-down: hd-idle parks ONLY the cold HDD (by serial), 15 min idle.
  cat > /etc/default/hd-idle <<EOF
START_HD_IDLE=true
HD_IDLE_OPTS="-i 0 -a ata-ST30000NM004K-3RM133_K1S05Y9M -i 900 -l /var/log/hd-idle.log"
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
$AUDIO_SSD -a
EOF
  fi
  systemctl restart smartmontools 2>/dev/null || systemctl restart smartd 2>/dev/null || true

  systemctl enable --now fstrim.timer

  echo; echo "================ RESULT ================"
  lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT "$HOT_SSD" "$AUDIO_SSD" "$COLD_HDD"
  echo "---- findmnt /srv ----"; findmnt -R /srv || true
  echo "---- write/read test ----"
  echo ok > /srv/video/.write-test && cat /srv/video/.write-test && rm -f /srv/video/.write-test
  echo ok > /srv/audio/.write-test && cat /srv/audio/.write-test && rm -f /srv/audio/.write-test
  echo "---- hd-idle ----"; systemctl is-active hd-idle || true
  echo "Done. Storage is up. (Docker mount-ordering drop-in is added in the Docker phase.)"
}

[[ "$MODE" == full ]] && provision
configure
