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
# Run as root:   sudo bash setup-storage.sh           (interactive confirm)
#                sudo bash setup-storage.sh --yes      (skip the prompt)
#
# >>> THIS WIPES THE THREE DATA DRIVES BELOW. The NVMe OS disk is never touched. <<<

set -euo pipefail

# ── Device assignment — BY SERIAL (never sdX, never SATA port) ───────────────
HOT_SSD=/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0WA00268B    # hot tier (with cold HDD)
AUDIO_SSD=/dev/disk/by-id/ata-Samsung_SSD_870_QVO_8TB_S5SSNF0W909892P  # audiobooks / music / cloud
COLD_HDD=/dev/disk/by-id/ata-ST30000NM004K-3RM133_K1S05Y9M            # 28TB cold tier

ASSUME_YES=0; [[ "${1:-}" == "--yes" ]] && ASSUME_YES=1

[[ $EUID -eq 0 ]] || { echo "Must run as root (sudo)."; exit 1; }

# ── Safety guards ────────────────────────────────────────────────────────────
OS_DISK=$(readlink -f "$(findmnt -no SOURCE / | sed 's/p\?[0-9]*$//')" 2>/dev/null || true)

check() {  # check <label> <by-id-path> <min-bytes> <max-bytes>
  local label=$1 id=$2 min=$3 max=$4 real sz
  [[ -e "$id" ]] || { echo "FATAL: $label not found: $id"; exit 1; }
  real=$(readlink -f "$id")
  [[ "$real" != "$OS_DISK" ]] || { echo "FATAL: $label resolves to the OS disk ($real)!"; exit 1; }
  ! findmnt -rno SOURCE | grep -q "^${real}" || { echo "FATAL: $label ($real) has a mounted partition. Aborting."; exit 1; }
  sz=$(blockdev --getsize64 "$real")
  (( sz >= min && sz <= max )) || { echo "FATAL: $label ($real) size $sz out of expected range."; exit 1; }
  echo "  OK  $label -> $real  ($(numfmt --to=iec "$sz"))  serial=$(basename "$id")"
}

echo "Verifying target drives by serial:"
check "HOT  SSD" "$HOT_SSD"   7000000000000  9000000000000
check "AUDIO SSD" "$AUDIO_SSD" 7000000000000  9000000000000
check "COLD HDD" "$COLD_HDD"  25000000000000 32000000000000

if findmnt -rno TARGET | grep -qx /srv/video; then
  echo "NOTE: /srv/video is already mounted — looks already set up. Aborting to avoid re-wipe."
  exit 1
fi

if [[ $ASSUME_YES -eq 0 ]]; then
  echo; echo "ALL DATA on the three drives above will be ERASED."
  read -r -p 'Type YES to proceed: ' ans
  [[ "$ans" == "YES" ]] || { echo "Aborted."; exit 1; }
fi

# ── Packages ─────────────────────────────────────────────────────────────────
echo "Installing packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq mergerfs hd-idle smartmontools xfsprogs gdisk parted util-linux

# ── Wipe ─────────────────────────────────────────────────────────────────────
vgchange -an 2>/dev/null || true   # best-effort: release any stale LVM
wipe_dev() {
  local d=$1 p
  for p in "$d"-part*; do [[ -e "$p" ]] && wipefs -a "$p" || true; done
  wipefs -a "$d" || true
  sgdisk --zap-all "$d" >/dev/null 2>&1 || true
  dd if=/dev/zero of="$d" bs=1M count=16 conv=fsync status=none   # clears bcache/LVM superblocks
}
echo "Wiping drives..."
wipe_dev "$HOT_SSD"; wipe_dev "$AUDIO_SSD"; wipe_dev "$COLD_HDD"

# ── Partition (single GPT partition each) ────────────────────────────────────
for d in "$HOT_SSD" "$AUDIO_SSD" "$COLD_HDD"; do
  sgdisk -n 1:0:0 -t 1:8300 "$d" >/dev/null
done
partprobe; udevadm settle

# ── Format ───────────────────────────────────────────────────────────────────
mkfs.ext4 -F -L ssd-hot "${HOT_SSD}-part1"
mkfs.ext4 -F -L audio   "${AUDIO_SSD}-part1"
mkfs.xfs  -f -L hdd-cold "${COLD_HDD}-part1"
udevadm settle

HOT_UUID=$(blkid -s UUID -o value "${HOT_SSD}-part1")
AUDIO_UUID=$(blkid -s UUID -o value "${AUDIO_SSD}-part1")
COLD_UUID=$(blkid -s UUID -o value "${COLD_HDD}-part1")

# ── Mountpoints ──────────────────────────────────────────────────────────────
mkdir -p /srv/audio /srv/.disks/ssd-hot /srv/.disks/hdd-cold /srv/video

# ── fstab (managed block, by UUID) ───────────────────────────────────────────
MERGER_OPTS="defaults,allow_other,use_ino,cache.files=partial,dropcacheonclose=true,category.create=ff,minfreespace=50G,moveonenospc=true,statfs=base,fsname=mergerfs,x-systemd.requires=/srv/.disks/ssd-hot,x-systemd.requires=/srv/.disks/hdd-cold"
sed -i '/# >>> beefy-storage/,/# <<< beefy-storage/d' /etc/fstab
cat >> /etc/fstab <<EOF
# >>> beefy-storage (managed by setup-storage.sh) >>>
UUID=$AUDIO_UUID  /srv/audio            ext4  noatime            0 2
UUID=$HOT_UUID    /srv/.disks/ssd-hot   ext4  relatime           0 2
UUID=$COLD_UUID   /srv/.disks/hdd-cold  xfs   noatime,commit=60  0 2
/srv/.disks/ssd-hot:/srv/.disks/hdd-cold  /srv/video  fuse.mergerfs  $MERGER_OPTS  0 0
# <<< beefy-storage (managed by setup-storage.sh) <<<
EOF

# ── FUSE: allow containers (non-root) to read the mergerfs mount ─────────────
sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf
mount --make-rshared / || true

systemctl daemon-reload
mount -a

# ── Spin-down: hd-idle parks ONLY the cold HDD (by serial), 15 min idle ──────
cat > /etc/default/hd-idle <<EOF
START_HD_IDLE=true
HD_IDLE_OPTS="-i 0 -a ata-ST30000NM004K-3RM133_K1S05Y9M -i 900 -l /var/log/hd-idle.log"
EOF
systemctl enable --now hd-idle 2>/dev/null || systemctl restart hd-idle || true

# ── smartd: don't wake the HDD for polls ─────────────────────────────────────
if ! grep -q 'beefy-storage' /etc/smartd.conf 2>/dev/null; then
  sed -i 's/^DEVICESCAN/#DEVICESCAN  # beefy-storage: explicit lines below/' /etc/smartd.conf 2>/dev/null || true
  cat >> /etc/smartd.conf <<EOF
# beefy-storage
$COLD_HDD -a -n standby
$HOT_SSD -a
$AUDIO_SSD -a
EOF
fi
systemctl enable --now smartmontools 2>/dev/null || systemctl enable --now smartd 2>/dev/null || true

# ── TRIM weekly on the SSDs ──────────────────────────────────────────────────
systemctl enable --now fstrim.timer

# ── Verify ───────────────────────────────────────────────────────────────────
echo; echo "================ RESULT ================"
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT "$HOT_SSD" "$AUDIO_SSD" "$COLD_HDD"
echo "---- findmnt /srv ----"; findmnt -R /srv || true
echo "---- write/read test ----"
echo ok > /srv/video/.write-test && cat /srv/video/.write-test && rm -f /srv/video/.write-test
echo ok > /srv/audio/.write-test && cat /srv/audio/.write-test && rm -f /srv/audio/.write-test
echo "Done. Storage is up. (Docker mount-ordering drop-in is added in the Docker phase.)"
