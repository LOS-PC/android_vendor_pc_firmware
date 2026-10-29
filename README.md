# Firmware for pc_x86_64

Firmware from upstream [linux-firmware](https://gitlab.com/kernel-firmware/linux-firmware)
that `external/linux-firmware-mainline` doesn't have, installed to `/vendor/firmware`.
ueventd serves it to kernel modules (`firmware_directories` in `ueventd.rc`).

| Directory | Upstream | Commit | Used by |
|---|---|---|---|
| `amdgpu` | `amdgpu/` | `d0039fd797d7` (2026-10-27) | amdgpu (AMD GPUs) |
| `intel_gpu` | `i915/`, `xe/` (their `WHENCE` sections) | `d0039fd797d7` (2026-10-27) | i915, xe (Intel GPUs) |
| `nvidia` | `nvidia/` (the `nouveau` section of `WHENCE`) | `d0039fd797d7` (2026-10-27) | nouveau (NVIDIA GPUs, Pascal to Ada) |
| `wireless` | Wi-Fi / Bluetooth driver sections of `WHENCE` | `d0039fd797d7` (2026-10-27) | Wi-Fi drivers in `configs/kernel/pc.config`, btusb, btmtk, btqca |

Each directory keeps its upstream licence (`LICENSE`) and its `WHENCE` entries.

## Updating

### wireless

`wireless/update.py` picks the files from a linux-firmware checkout and regenerates
`wireless/Android.bp`: the `WHENCE` sections of the drivers the kernel builds, without
other chips' files (routers, phones, SDIO boards) and, for iwlwifi, only the newest firmware
API the kernel accepts (its ranges come from the kernel's `iwlwifi/cfg/*.c`; update them
with the kernel). `Link:` entries become symlinks. Paths that a module of
`external/linux-firmware` or `external/linux-firmware-mainline` (both in the manifest)
installs are left to that module: Soong defines install rules for every module in their
namespaces, shipped or not, and two modules can't install the same file. `update.py` reads
them from their `Android.bp` files and writes `wireless/wireless.mk`, which adds
`pc_firmware_wireless` and those modules to the product.

```bash
git clone --depth 1 https://gitlab.com/kernel-firmware/linux-firmware.git
wireless/update.py linux-firmware
```

### intel_gpu

`intel_gpu/update.py` copies the files of the `i915` and `xe` sections of `WHENCE`,
except those `external/linux-firmware` already has modules for (Tiger Lake, Alder Lake,
Meteor Lake, Panther Lake and the newest display firmware), and regenerates
`intel_gpu/Android.bp` and `intel_gpu/intel_gpu.mk`, which adds those modules too. It
reuses `wireless/update.py`'s code for finding them. `Link:` entries are older names that
the kernel no longer requests, so they're skipped.

```bash
intel_gpu/update.py linux-firmware
```

### nvidia

`nvidia/update.py` copies the files of the `nouveau` section of `WHENCE` for the chips in
its `CHIPS` (Pascal, Turing, Ampere and Ada) and regenerates `nvidia/Android.bp` and
`nvidia/nvidia.mk`. Only the GSP-RM version the kernel loads (`GSP_VERSION`, 535.113.01 for
6.12) is kept. Most chips share files with another one through `Link:` entries, including
whole directories (`ga106/gsp` -> `ga102/gsp`, `ad104` -> `ad102`); those are installed as
symlinks (`install_symlink`), so each GSP image (24 MB for Turing, 38 MB for Ampere / Ada)
is stored once.

```bash
nvidia/update.py linux-firmware
```

### amdgpu

```bash
curl -fsSL -o amdgpu.tar.gz "https://gitlab.com/kernel-firmware/linux-firmware/-/archive/main/linux-firmware-main.tar.gz?path=amdgpu"
```

Replace the `.bin` files with the archive's, update `LICENSE` (`LICENSES/LICENSE.amdgpu`) and
`WHENCE` (the amdgpu section), and the commit above. Upstream also defines `Link:` aliases in
`WHENCE`; the amdgpu section has none as of this commit.
