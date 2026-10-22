# beefy — Hardware Specifications

Home-lab "beefy" server (data/compute tier: downloads, Samba, media library; Audiobookshelf stays on fastpi) — woken on demand from fastpi via Wake-on-LAN, sleeps to S5 when idle.

**Hostname:** `beefy` · **LAN IP:** `192.168.1.102/24` (`enp6s0`) · **MAC:** `74:56:3c:96:79:a3` (Wake-on-LAN)  
**OS:** Ubuntu 26.04 LTS (Resolute Raccoon), x86_64 (64-bit) · **Kernel:** `7.0.0-27-generic`  
**Gathered:** 2026-07-03 via `lscpu`, `lsblk`, `lspci`, `ip`, DMI sysfs, DDR4 SPD, `dmidecode -t 17`; prices from purchase records.  
Serial numbers intentionally omitted.

---

## Case

| Case | Jonsbo Z20 (Black) |
|------|--------------------|
| Type | Portable Micro-ATX / Mini-ITX chassis, ~20 L |
| PSU support | SFX / ATX |
| Front I/O | USB Type-C Gen2 |
| Purchased | 2024-08-23 (AliExpress) |

> DMI chassis vendor reads "Default string", type 3 / Desktop — the board firmware has no
> chassis identity; case info above is from the purchase record.

## Power Supply

| Power Supply | Cooler Master MWE Gold V2 750 W |
|--------------|---------------------------------|
| Manufacturer part no. | MPE-7501-AFAAG-EU |
| Wattage | 750 W |
| Efficiency | 80 PLUS Gold (~90%) |
| Form factor | ATX |
| Modularity | Fully modular |
| Purchased | 2024-09-07 (Galaxus, Zürich branch) |

> Not software-detectable (a PSU exposes no digital/USB monitoring interface) — details from
> the purchase record.

## Motherboard

| Motherboard | Gigabyte H510M H V2 |
|-------------|---------------------|
| Vendor | Gigabyte Technology Co., Ltd. |
| Board revision | rev 1.0 (silk-screened on PCB; firmware reports placeholder `x.x`), product suffix `-CF` |
| Chipset | Intel H510 (PCH silicon reports as "Comet Lake") |
| Socket | LGA1200 |
| Form factor | Micro-ATX |

## BIOS / Firmware

| BIOS | American Megatrends Inc. (AMI) |
|------|--------------------------------|
| Version | F3 |
| Date | 2023-12-20 (release 5.17) |

---

## CPU

| CPU | Intel Core i5-11400 |
|-----|---------------------|
| Generation | 11th Gen (Rocket Lake-S) |
| Socket | LGA1200 |
| Cores / Threads | 6 C / 12 T |
| Base clock | 2.60 GHz |
| Max turbo | 4.40 GHz (min 800 MHz) |
| TDP | 65 W (spec) |
| L1 cache | 288 KiB data + 192 KiB instr (6× 48 KiB / 6× 32 KiB) |
| L2 cache | 3 MiB (6× 512 KiB) |
| L3 cache | 12 MiB (shared) |
| Virtualization | VT-x (VMX), VT-d capable |
| Notable ISA | AVX-512, AVX2, VAES, SHA-NI |

## CPU Cooler

| CPU Cooler | Thermalright Peerless Assassin 120 SE |
|------------|---------------------------------------|
| Type | Dual-tower air cooler |
| Height | 155 mm |
| Fans | 2× 120 mm |

## Integrated GPU

| Integrated GPU | Intel UHD Graphics 730 |
|----------------|------------------------|
| Silicon | Rocket Lake-S GT1, Xe (Gen 12.1 / LP), 24 EUs |
| PCI ID | `8086:4c8b` (rev 04), bus `00:02.0` |
| Max clock | ~1.30 GHz |
| Render node | `/dev/dri/renderD128` (+ `card0`) — VA-API transcoding |

**Quick Sync Video — Version 8** (Gen 12; shared with Tiger Lake / Alder Lake / Raptor Lake).
On Rocket Lake specifically:

- **Decode:** MPEG-2, VC-1, H.264/AVC, HEVC 8/10/12-bit (4:2:0/4:2:2/4:4:4), VP9 8/10/12-bit, JPEG
- **Encode:** H.264/AVC, HEVC 8/10-bit, VP9 8-bit, MPEG-2, JPEG
- **HDR10 tone-mapping:** yes (hardware, added in QSV v7) — HDR→SDR transcodes are accelerated
- **No VP8 decode** and **no AV1** (both are Tiger-Lake-only within v8; Rocket Lake has neither)

Good fit for Plex/Jellyfin HW transcoding up to 4K HEVC/H.264 10-bit incl. HDR tone-map.

---

## Memory (RAM)

- **Total:** 64 GB DDR4 (2× 32 GB), dual-channel — **board maxed** (2 slots, both filled).
- **Modules:** Corsair Vengeance LPX — kit `CMK64GX4M2A2666C16` (rated **DDR4-2666 CL16**, dual-rank, 1.2 V, XMP 2.0, 288-pin DIMM, unbuffered / non-ECC).
- **Running speed:** **2133 MT/s** (JEDEC default) — H510 does not support XMP / memory OC, so the modules run below their rated 2666.

| Slot (locator) | Populated | Size | Rank | Rated | Running | Module |
|---|---|---|---|---|---|---|
| ChannelA-DIMM0 | ✅ | 32 GB | dual | 2666 CL16 | 2133 MT/s | Corsair Vengeance LPX `CMK64GX4M2A2666C16` |
| ChannelB-DIMM0 | ✅ | 32 GB | dual | 2666 CL16 | 2133 MT/s | Corsair Vengeance LPX `CMK64GX4M2A2666C16` |
| ChannelA-DIMM1 | — | — | — | — | — | No Module Installed |
| ChannelB-DIMM1 | — | — | — | — | — | No Module Installed |

> Per-DIMM values from `dmidecode -t 17` and the DDR4 SPD EEPROMs (`ee1004` driver at i2c
> `0-0050` / `0-0052`). SMBIOS advertises 4 logical devices but only the two DIMM0 sockets
> physically exist and are filled.

---

## Storage

| Dev | Model | Type / Bus | Capacity | FW | FS | Mount / Role |
|---|---|---|---|---|---|---|
| `nvme0n1` | Samsung SSD 970 EVO 1TB | NVMe M.2 (PCIe) | 1 TB | 2B2QEXE7 | vfat + ext4 | `/boot/efi` (1 GB) + `/` (930 GB) — **OS / boot** |
| `sda` | Samsung SSD 870 QVO 8TB | SATA III 2.5" SSD (QLC) | 8 TB | — | ext4 | `/srv/.disks/ssd-hot` — **SSD hot tier** |
| `sdb` | Samsung SSD 870 QVO 8TB | SATA III 2.5" SSD (QLC) | 8 TB | — | ext4 (label `audio`) | **Unused spare** — former `/srv/audio` tier, retired 2026-07-03. Not mounted, not in fstab, not in the mergerfs pool; SMART-monitored, data left intact |
| `sdc` | Seagate Exos M ST30000NM004K | SATA III 3.5" HDD, 7200 RPM | 30 TB | — | xfs | `/srv/.disks/hdd-cold` — **cold tier** |

- **mergerfs pool:** `/srv/video` (`fuse.mergerfs`) — unifies the hot-SSD + cold-HDD tiers for the video library.
- **SATA controller:** Intel Comet Lake SATA AHCI (`00:17.0`, `8086:06d2`).
- **NVMe controller:** Samsung SM981/PM981/PM983-class (`05:00.0`, `144d:a808`).
- **NVMe cooling:** be quiet! MC1 M.2 SSD heatsink mounted on the `nvme0n1` 970 EVO.
- **Total raw storage:** 1 TB NVMe + 2× 8 TB SATA SSD + 30 TB HDD = **47 TB** (~46 TB usable across data mounts).
- *(SATA drive firmware/serials require root SMART — not captured; serials intentionally omitted.)*

---

## Networking

| Interface | Device | Speed |
|---|---|---|
| `enp6s0` (onboard) | Realtek RTL8111/8168 PCIe Gigabit Ethernet (`06:00.0`, `10ec:8168`, rev 15) | 1000 Mb/s |

- **LAN IP:** `192.168.1.102/24`  ·  **WoL MAC:** `74:56:3c:96:79:a3`
- *(docker0 / br-* / veth* are Docker virtual bridges.)*

## USB / Audio / Sensors

- **USB:** Intel Comet Lake USB 3.1 xHCI host controller (`00:14.0`, `8086:06ed`) — USB 2.0 + 3.0 root hubs.
- **Audio:** Intel Rocket Lake PCH HD Audio (`00:1f.3`, `8086:f1c8`).
- **hwmon sensors:** `coretemp`, `acpitz`, `nvme`, `pch_cometlake`, `gigabyte_wmi`.

---

## Build cost

Prices as paid (CHF). Galaxus items from purchase records; the case from AliExpress (total incl. shipping & import).

| Component | Item | Price (CHF) | Source |
|-----------|------|------------:|--------|
| Case | Jonsbo Z20 | 128.91 | AliExpress (total, incl. shipping) |
| CPU | Intel Core i5-11400 (Tray) | 89.10 | Galaxus |
| CPU cooler | Thermalright Peerless Assassin 120 SE | 53.30 | Galaxus |
| Motherboard | Gigabyte H510M H V2 | 63.10 | Galaxus |
| RAM | Corsair Vengeance LPX 64 GB | 128.00 | Galaxus |
| PSU | Cooler Master MWE Gold V2 750 W | 79.90 | Galaxus (pickup 2024-09-07) |
| Boot SSD | Samsung 970 EVO 1TB | 215.00 | Galaxus |
| NVMe cooler | be quiet! MC1 (M.2 heatsink) | 11.20 | Galaxus (CHF 11.20/pc) |
| Data SSD | Samsung 870 QVO 8TB | 449.00 | Galaxus |
| Data SSD | Samsung 870 QVO 8TB | 480.00 | Galaxus |
| Data HDD | Seagate Exos M 30TB | 516.00 | Galaxus |
| **Total** | | **2213.51** | |

> Storage is the bulk of the build: SSD/HDD subtotal CHF 1660.00 (215 + 449 + 480 + 516).

---

## Summary

| Component | Spec |
|---|---|
| Case | Jonsbo Z20 (Black, ~20 L MATX) |
| PSU | Cooler Master MWE Gold V2, 750 W, 80+ Gold |
| Board | Gigabyte H510M H V2, H510, LGA1200, mATX |
| CPU | Intel Core i5-11400 — 6C/12T, up to 4.4 GHz, 65 W |
| Cooler | Thermalright Peerless Assassin 120 SE (dual-tower air) |
| iGPU | Intel UHD Graphics 730 (QSV v8, HDR tone-map, no AV1/VP8) |
| RAM | 64 GB DDR4 (2× 32 GB Corsair Vengeance LPX @ 2133 MT/s), 2/2 slots full |
| Boot | Samsung 970 EVO 1 TB NVMe |
| Data | 2× Samsung 870 QVO 8 TB SATA SSD + Seagate Exos 30 TB HDD (47 TB raw) |
| Net | 1× Gigabit Ethernet (Realtek) |
| OS | Ubuntu 26.04 LTS, kernel 7.0.0-27 |
| Build cost | CHF 2213.51 |

---

*Last updated: 2026-07-03. Regenerate software-reported fields with `lscpu`, `lspci -nn`, `lsblk -f`, `free -h`, `/sys/devices/virtual/dmi/id/`, and `sudo dmidecode -t 17`. RAM per-DIMM also readable from DDR4 SPD EEPROMs (`/sys/bus/i2c/devices/*/eeprom`, driver `ee1004`). Physical-only fields (case, PSU) and prices are manual entry.*
