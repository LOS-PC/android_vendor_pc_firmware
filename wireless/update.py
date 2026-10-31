#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#
"""Copies the Wi-Fi and Bluetooth firmware pc_x86_64's kernel drivers use from a
linux-firmware checkout, and generates Android.bp.

    git clone --depth 1 https://gitlab.com/kernel-firmware/linux-firmware.git
    ./update.py linux-firmware

Files are picked from the WHENCE sections of the drivers in configs/kernel/pc.config
(DRIVERS), minus other chips' files (EXCLUDE), and for iwlwifi, only the newest firmware
API the kernel accepts. Link: entries become relative symlinks.
"""

import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# WHENCE "Driver:" lines of the drivers built (Wi-Fi) and of the Bluetooth parts of the
# same chips (btusb: Intel / Realtek / Broadcom, btmtk, btqca / qca, ath3k). rtl8192cu isn't
# built, but rtl8xxxu uses its firmware for the RTL8188CU / RTL8192CU.
DRIVERS = re.compile(r"^Driver: *("
                     r"ath9k_htc|ath10k|ath11k|ath12k|ath3k|.*AR3012|"
                     r"iwlwifi|"
                     r"brcmfmac|"
                     r"rtw88|rtw89|rtl8xxxu|rtl8192cu|rtl8188ee|rtl8192ee|rtl8723be|rtl8821ae|"
                     r"mt7601u|mt76x0|mt76x2u|mt7921|mt7922|mt7925|btmtk|btmtk_usb|"
                     r"rt2800pci|rt2800usb|"
                     r"btusb|btqca|qca)\b")

# Router / phone chips in the same sections, which PCs don't have, and brcmfmac's SDIO
# files (tablet boards; only its PCIe and USB support is built, and some of their names
# have spaces, which Android.bp file lists don't handle)
EXCLUDE = re.compile(r"(?i)(ipq|qcn9|wcn3990|qca4019|wcn3950|wcn3988|wcn399|wcn6750|wcn7750|-sdio)")

# iwlwifi firmware API range per chip family (the MAC part of the firmware name, e.g. "bz"
# in iwlwifi-bz-b0-fm-c0-93.ucode), from the kernel's drivers/net/wireless/intel/iwlwifi/
# cfg/*.c (IWL_*_UCODE_API_MIN / MAX). The driver builds the rest of the name at runtime
# from the chip's revision, tries MAX down to MIN, and uses the first file it finds, so
# only that one is kept, and none when upstream has none in range. Files named with a
# core version (-c101.ucode) are for newer kernels. Older families aren't listed: their
# newest file is kept.
IWL_API = {
    "Qu": (77, 77), "QuZ": (77, 77), "cc": (77, 77),
    "so": (77, 89), "ty": (77, 89), "ma": (77, 89),
    "bz": (90, 93), "gl": (90, 93),
    "sc": (90, 93), "sc2": (90, 93), "sc2f": (90, 93),
    "dr": (96, 96), "br": (96, 96),
}

# Firmware projects synced by repo whose Soong namespaces the product exports
# (external/linux-firmware: AOSP, external/linux-firmware-mainline: device/mainline/common).
# Soong defines an install rule for each of their modules whether the product ships it or not,
# and two modules can't install the same /vendor/firmware path, so files they have are left
# to their modules, which wireless.mk adds to the product instead.
OTHER_FIRMWARE = [os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "external", d))
                  for d in ("linux-firmware", "linux-firmware-mainline")]


def bp_string(body, prop):
    m = re.search(r"^\s*" + prop + r': *"([^"]+)"', body, re.M)
    return m.group(1) if m else None


def bp_list(body, prop):
    m = re.search(r"^\s*" + prop + r": *\[([^\]]*)\]", body, re.M)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def other_installed():
    """Returns {/vendor/firmware path: module} for OTHER_FIRMWARE's modules."""
    paths = {}
    # install_symlink module: the prebuilt_firmware module that requires it. The symlink
    # module installs only the link, so its path goes to the module with the file.
    owners = {}
    symlinks = set()
    for tree in OTHER_FIRMWARE:
        if not os.path.isdir(tree):
            print("warning: " + tree + " not found", file=sys.stderr)
            continue
        for root, _, names in os.walk(tree):
            if "Android.bp" not in names:
                continue
            text = open(os.path.join(root, "Android.bp"), encoding="utf-8").read()
            for kind, body in re.findall(r"^(prebuilt_firmware|install_symlink) *\{\n(.*?)^\}",
                                         text, re.M | re.S):
                name = bp_string(body, "name")
                if kind == "install_symlink":
                    symlinks.add(name)
                    loc = bp_string(body, "installed_location") or ""
                    if loc.startswith("firmware/"):
                        paths[loc[len("firmware/"):]] = name
                    continue
                for req in bp_list(body, "required"):
                    owners[req] = name
                subdir = bp_string(body, "relative_install_path") or bp_string(body, "sub_dir")
                dsts = bp_list(body, "dsts")
                src = bp_string(body, "src")
                if not dsts and src:
                    dsts = [bp_string(body, "filename") or os.path.basename(src)]
                for dst in dsts:
                    paths[os.path.join(subdir, dst) if subdir else dst] = name
    return {path: owners.get(name, name) if name in symlinks else name
            for path, name in paths.items()}


IWL_UCODE = re.compile(r"^(iwlwifi-([^-]+)-.+)-(c?)(\d+)\.ucode$")


def parse_whence(path):
    """Returns the sections of the selected drivers: (header, files, links, licences, text)."""
    sections = []
    header = None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("Driver:"):
            header = line if DRIVERS.match(line) else None
            if header:
                sections.append((header, [], [], set(), []))
                sections[-1][4].append(line)
            continue
        if line.startswith("-----"):
            header = None
        if not header:
            continue
        _, files, links, licences, text = sections[-1]
        text.append(line)
        # Paths may be quoted (and then contain spaces)
        m = re.match(r'^(?:Raw)?File: *(?:"([^"]+)"|(\S+))', line)
        if m:
            files.append(m.group(1) or m.group(2))
        m = re.match(r'^Link: *(?:"([^"]+)"|(\S+)) *-> *(?:"([^"]+)"|(\S+))', line)
        if m:
            links.append((m.group(1) or m.group(2), m.group(3) or m.group(4)))
        for lic in re.findall(r"(LICEN[CS]E[\w.\-]*)", line):
            licences.add(lic.rstrip("."))
    return sections


def pick_iwlwifi(files):
    """Keeps, per iwlwifi firmware name, the newest version the kernel accepts."""
    best = {}
    keep = []
    for f in files:
        m = IWL_UCODE.match(os.path.basename(f))
        if not m:
            keep.append(f)  # .pnvm and other files
            continue
        prefix, family, core, api = m.group(1), m.group(2), m.group(3), int(m.group(4))
        low, high = IWL_API.get(family, (0, 10**6))
        if core or not low <= api <= high:
            continue
        if prefix not in best or api > best[prefix][0]:
            best[prefix] = (api, f)
    return keep + [f for _, f in best.values()]


def main(tree):
    sections = parse_whence(os.path.join(tree, "WHENCE"))

    files = []
    links = []
    licences = set()
    for header, sec_files, sec_links, sec_licences, _ in sections:
        # Paths with spaces are board-specific files (tablets), and Android.bp lists can't
        # hold them
        sec_files = [f for f in sec_files if not EXCLUDE.search(f) and " " not in f]
        if header.startswith("Driver: iwlwifi"):
            sec_files = pick_iwlwifi(sec_files)
        files += sec_files
        links += sec_links
        licences |= sec_licences
    files = sorted(set(f for f in files if os.path.basename(f) not in ("README", "LICENSE")))
    missing = [f for f in files if not os.path.isfile(os.path.join(tree, f))]
    for f in missing:
        print("warning: " + f + " not found", file=sys.stderr)
    files = [f for f in files if f not in missing]

    # Links whose (final) target is kept
    kept = set(files)
    link_targets = {}
    for name, target in links:
        link_targets[name] = os.path.normpath(os.path.join(os.path.dirname(name), target))
    def resolve(name, depth=0):
        target = link_targets.get(name, name)
        return resolve(target, depth + 1) if target in link_targets and depth < 10 else target
    links = sorted(n for n in link_targets
                   if resolve(n) in kept and not EXCLUDE.search(n) and " " not in n)

    # 6.12's iwlwifi asks for iwlwifi-*.ucode at the top level, which upstream links to
    # intel/iwlwifi/; those are installed only under the link names
    other = other_installed()
    wanted = [f for f in files if not f.startswith("intel/iwlwifi/")] + links
    install = [p for p in wanted if p not in other]
    other_modules = sorted(set(other[p] for p in wanted if p in other))
    print("%d paths left to %d modules of %s" % (len(wanted) - len(install), len(other_modules),
                                                 " and ".join(map(os.path.basename,
                                                                  OTHER_FIRMWARE))))

    for d in os.listdir(HERE):
        if d not in ("update.py",) and not d.startswith("."):
            p = os.path.join(HERE, d)
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)

    for f in files:
        dst = os.path.join(HERE, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(tree, f), dst)
    for name in links:
        dst = os.path.join(HERE, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.symlink(os.path.relpath(os.path.join(HERE, link_targets[name]),
                                   os.path.dirname(dst)), dst)

    license_files = []
    os.makedirs(os.path.join(HERE, "LICENSES"), exist_ok=True)
    for lic in sorted(licences):
        for src in (os.path.join(tree, "LICENSES", lic), os.path.join(tree, lic)):
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(HERE, "LICENSES", lic))
                license_files.append("LICENSES/" + lic)
                break
        else:
            print("warning: licence " + lic + " not found", file=sys.stderr)

    # The upstream WHENCE sections these files come from (EXCLUDE / iwlwifi versions not
    # applied), for their versions and licences
    with open(os.path.join(HERE, "WHENCE"), "w", encoding="utf-8") as out:
        for section in sections:
            out.write("\n".join(section[4]).rstrip() + "\n\n" + "-" * 74 + "\n\n")

    def bp_items(items):
        return "".join('        "%s",\n' % i for i in items)

    # prebuilt_firmware copies each file to its intermediates directory under the file name
    # alone, so a module can't hold two files with the same name (ath10k / ath11k board-2.bin
    # etc.): those go to pc_firmware_wireless_2, _3...
    groups = []
    for path in install:
        for names, paths in groups:
            if os.path.basename(path) not in names:
                break
        else:
            groups.append((set(), []))
            names, paths = groups[-1]
        names.add(os.path.basename(path))
        paths.append(path)
    modules = ["pc_firmware_wireless" + ("_%d" % (i + 1) if i else "")
               for i in range(len(groups))]

    def bp_module(name, paths):
        return """
prebuilt_firmware {
    name: "%s",
    licenses: ["pc_firmware_wireless_license"],
    srcs: [
""" % name + bp_items(paths) + """    ],
    dsts: [
""" + bp_items(paths) + """    ],
    vendor: true,
}
"""

    with open(os.path.join(HERE, "Android.bp"), "w", encoding="utf-8") as bp:
        bp.write("""//
// SPDX-FileCopyrightText: The LineageOS Project
// SPDX-License-Identifier: Apache-2.0
//

// Generated by update.py: Wi-Fi and Bluetooth firmware from upstream linux-firmware,
// installed to /vendor/firmware, where ueventd serves it to the kernel modules

license_kind {
    name: "pc_firmware_wireless_redistributable",
    conditions: ["by_exception_only"],
}

license {
    name: "pc_firmware_wireless_license",
    visibility: ["//visibility:private"],
    license_kinds: ["pc_firmware_wireless_redistributable"],
    license_text: [
""" + bp_items(license_files) + """    ],
}
""" + "".join(bp_module(m, paths) for m, (_, paths) in zip(modules, groups)))

    with open(os.path.join(HERE, "wireless.mk"), "w", encoding="utf-8") as mk:
        mk.write("""#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

# Generated by update.py: the pc_firmware_wireless modules, plus the
# external/linux-firmware(-mainline) modules that install the rest of the files

PRODUCT_PACKAGES += \\""" + " \\".join("\n    " + m for m in modules + other_modules) + "\n")

    size = sum(os.path.getsize(os.path.join(tree, f)) for f in files
               if not f.startswith("intel/iwlwifi/")) + sum(
        os.path.getsize(os.path.join(tree, resolve(n))) for n in links)
    print("%d files, %d links, %d licences, %.0f MB installed" %
          (len(files), len(links), len(license_files), size / 1e6))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
