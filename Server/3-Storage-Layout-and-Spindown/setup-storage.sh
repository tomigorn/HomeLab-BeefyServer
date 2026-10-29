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
#   sudo bash setup-storage.sh --configure                   configure only: NO wipe (mounts/fstab/services)
#   sudo bash setup-storage.sh --provision <hot|hot2|cold>...  WIPE + format only the named drives, then run --configure
#
# Provision refuses a drive that is mounted, has partitions or carries any filesystem/label signature (a live branch
# that failed to mount under nofail looks exactly like that): clear a drive you really mean to reuse by hand first
# (wipefs -a). Each named drive asks for its serial typed back; one wrong answer and nothing is wiped. No --yes.
# The NVMe OS disk is never touched.

set -euo pipefail

# ── Device assignment — BY SERIAL (never sdX, never SATA port) ───────────────
HOT_SSD=${HOT_SSD:-/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0WA00268B}    # hot tier (with cold HDD)
COLD_HDD=${COLD_HDD:-/dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M}            # 28TB cold tier
HOT2_SSD=${HOT2_SSD:-/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0W909892P}   # second hot branch (ex-audio spare)

usage() { sed -n '15,23p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
MODE=""; DRIVES=()
for a in "$@"; do
  case "$a" in
    --configure) [[ -z $MODE ]] || usage; MODE=configure ;;
    --provision) [[ -z $MODE ]] || usage; MODE=provision ;;
    hot|hot2|cold) [[ $MODE == provision ]] || usage; [[ " ${DRIVES[*]} " == *" $a "* ]] || DRIVES+=("$a") ;;
    *) echo "unknown arg: $a"; usage ;;
  esac
done
[[ -n $MODE ]] || usage
[[ $MODE != provision || ${#DRIVES[@]} -gt 0 ]] || { echo "name the drives to wipe: --provision hot|hot2|cold ..."; exit 1; }
[[ $EUID -eq 0 || -n ${STORAGE_TEST_ROOT:-} ]] || { echo "Must run as root (sudo)."; exit 1; }

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

drive_id()    { case $1 in hot) echo "$HOT_SSD" ;; hot2) echo "$HOT2_SSD" ;; cold) echo "$COLD_HDD" ;; esac; }
drive_label() { case $1 in hot) echo ssd-hot ;; hot2) echo ssd-hot2 ;; cold) echo hdd-cold ;; esac; }
drive_serial() { local b; b=$(basename "$(drive_id "$1")"); echo "${b##*_}"; }

provision() {
  local d id real sig parts ans
  echo "Verifying the named drives by serial:"
  for d in "${DRIVES[@]}"; do
    id=$(drive_id "$d")
    case $d in
      cold) check "COLD HDD" "$id" 25000000000000 32000000000000 ;;
      *)    check "$(drive_label "$d")" "$id" 7000000000000 9000000000000 ;;
    esac
    real=$(readlink -f "$id")
    ! findmnt -rno SOURCE | grep -q "^${real}" || { echo "FATAL: $d ($real) has a mounted partition. Aborting, nothing wiped."; exit 1; }
    parts=$(lsblk -nro NAME "$real" 2>/dev/null | tail -n +2)
    [[ -z $parts ]] || { echo "FATAL: $d ($real) has a partition table ($(echo $parts)): it may be a live branch that did not mount. Aborting, nothing wiped."; exit 1; }
    sig=$(wipefs -n --noheadings "$real" 2>/dev/null)
    [[ -z $sig ]] || { echo "FATAL: $d ($real) carries a filesystem or label signature: $sig. Aborting, nothing wiped."; exit 1; }
  done

  echo; echo "ALL DATA on: ${DRIVES[*]} will be ERASED."
  for d in "${DRIVES[@]}"; do
    read -r -p "Type the serial of $d ($(drive_serial "$d")) to erase it: " ans || ans=""
    [[ $ans == "$(drive_serial "$d")" ]] || { echo "Aborted, nothing wiped."; exit 1; }
  done

  echo "Installing packages..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq mergerfs hd-idle smartmontools xfsprogs gdisk parted util-linux

  wipe_dev() {
    local d=$1
    wipefs -a "$d" || true
    sgdisk --zap-all "$d" >/dev/null 2>&1 || true
    dd if=/dev/zero of="$d" bs=1M count=16 conv=fsync status=none
  }
  echo "Wiping: ${DRIVES[*]}"
  for d in "${DRIVES[@]}"; do
    id=$(drive_id "$d"); wipe_dev "$id"
    sgdisk -n 1:0:0 -t 1:8300 "$id" >/dev/null
  done
  partprobe; udevadm settle
  for d in "${DRIVES[@]}"; do
    id=$(drive_id "$d")
    case $d in
      cold) mkfs.xfs -f -L hdd-cold "${id}-part1" ;;
      *)    mkfs.ext4 -F -m 0 -L "$(drive_label "$d")" "${id}-part1" ;;
    esac
  done
  udevadm settle
  echo "Provisioned: ${DRIVES[*]}. Next: sudo bash $0 --configure"
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

if [[ $MODE == provision ]]; then provision; else configure; fi
