#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#
"""Copies the NVIDIA GPU firmware nouveau needs for the supported generations from a
linux-firmware checkout, and generates Android.bp and nvidia.mk.

    git clone --depth 1 https://gitlab.com/kernel-firmware/linux-firmware.git
    ./update.py linux-firmware

Files come from the nouveau section of WHENCE, for the chips in CHIPS. Most chips share
their files with another one through Link: entries, including whole directories (ga106/gsp
-> ga102/gsp, ad104 -> ad102); those become symlinks on the device (install_symlink), so
the large GSP images are stored once. Only the GSP version the kernel loads (GSP_VERSION) is
kept. Files that a module of external/linux-firmware(-mainline) installs are left to that
module (see ../wireless/update.py, whose code this reuses).
"""

import importlib.util
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location(
    "wireless_update", os.path.join(HERE, "..", "wireless", "update.py"))
wireless = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wireless)

# Pascal, Turing, Ampere and Ada GPUs with graphics (not GP10B / GA10B, which are Tegra, or
# GA100, which is compute only)
CHIPS = [
    "gp100", "gp102", "gp104", "gp106", "gp107", "gp108",
    "tu102", "tu104", "tu106", "tu116", "tu117",
    "ga102", "ga103", "ga104", "ga106", "ga107",
    "ad102", "ad103", "ad104", "ad106", "ad107",
]

# GSP-RM version nouveau loads on Turing and newer (kernel 6.12: r535.c); update with the kernel
GSP_VERSION = "535.113.01"
GSP_FILE = re.compile(r"/gsp/[^/]*-(\d+\.\d+(?:\.\d+)?)\.bin$")

LICENCE = "LICENCE.nvidia"


def parse_whence(path):
    """Returns (files, links, section text) of the nouveau section."""
    files, links, text = set(), {}, []
    inside = False
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("Driver:"):
            inside = bool(re.match(r"^Driver: *nouveau\b", line))
        elif line.startswith("-----"):
            inside = False
        if not inside:
            continue
        text.append(line)
        m = re.match(r"^(?:Raw)?File: *(\S+)", line)
        if m:
            files.add(m.group(1))
        m = re.match(r"^Link: *(\S+) *-> *(\S+)", line)
        if m:
            links[m.group(1)] = m.group(2)
    return files, links, text


def link_target(path, links):
    return os.path.normpath(os.path.join(os.path.dirname(path), links[path]))


def resolve(path, files, links):
    """Follows file and directory links to the file that holds the data."""
    for _ in range(32):
        if path in files:
            return path
        if path in links:
            path = link_target(path, links)
            continue
        parts = path.split("/")
        for i in range(len(parts) - 1, 0, -1):
            prefix = "/".join(parts[:i])
            if prefix in links:
                path = link_target(prefix, links) + "/" + "/".join(parts[i:])
                break
        else:
            return None
    return None


def wanted_paths(files, links):
    """Every path under CHIPS' directories, with directory links expanded."""
    paths = set(files) | set(links)
    dir_links = {l: link_target(l, links) for l in links
                 if any(p.startswith(link_target(l, links) + "/") for p in files | set(links))}
    changed = True
    while changed:
        changed = False
        for link, target in dir_links.items():
            for p in list(paths):
                if p.startswith(target + "/") and link + p[len(target):] not in paths:
                    paths.add(link + p[len(target):])
                    changed = True
    wanted = set()
    for p in paths:
        if p in dir_links or not any(p.startswith("nvidia/%s/" % c) for c in CHIPS):
            continue
        m = GSP_FILE.search(p)
        if "/gsp/" in p and (not m or m.group(1) != GSP_VERSION):
            continue
        wanted.add(p)
    return sorted(wanted)


def main(tree):
    files, links, text = parse_whence(os.path.join(tree, "WHENCE"))
    installed = wireless.other_installed()

    real = set()      # files this directory ships
    symlinks = {}     # device path: target relative to its directory
    others = set()
    for path in wanted_paths(files, links):
        target = resolve(path, files, links)
        if target is None:
            print("warning: " + path + " doesn't resolve to a file", file=sys.stderr)
            continue
        if path in installed:
            others.add(installed[path])
            continue
        if target != path:
            symlinks[path] = os.path.relpath(target, os.path.dirname(path))
        if target in installed:
            others.add(installed[target])
        else:
            real.add(target)

    shutil.rmtree(os.path.join(HERE, "nvidia"), ignore_errors=True)
    for path in sorted(real):
        os.makedirs(os.path.join(HERE, os.path.dirname(path)), exist_ok=True)
        shutil.copy2(os.path.join(tree, path), os.path.join(HERE, path))
    shutil.copy2(os.path.join(tree, "LICENSES", LICENCE), os.path.join(HERE, LICENCE))

    with open(os.path.join(HERE, "WHENCE"), "w", encoding="utf-8") as out:
        out.write("The nouveau section of upstream linux-firmware's WHENCE\n\n")
        out.write("\n".join(text).rstrip("\n-") + "\n")

    # prebuilt_firmware copies each file to its intermediates directory under the file name
    # alone, so files with the same name (every chip has an acr/bl.bin) go to separate
    # modules: pc_firmware_nvidia, _2, _3...
    groups = []
    for path in sorted(real):
        for names, paths in groups:
            if os.path.basename(path) not in names:
                break
        else:
            groups.append((set(), []))
            names, paths = groups[-1]
        names.add(os.path.basename(path))
        paths.append(path)
    modules = ["pc_firmware_nvidia" + ("_%d" % (i + 1) if i else "") for i in range(len(groups))]
    link_modules = ["pc_firmware_nvidia_link_%d" % (i + 1) for i in range(len(symlinks))]

    def bp_items(items):
        return "".join('        "%s",\n' % i for i in items)

    with open(os.path.join(HERE, "Android.bp"), "w", encoding="utf-8") as bp:
        bp.write("""//
// SPDX-FileCopyrightText: The LineageOS Project
// SPDX-License-Identifier: Apache-2.0
//

// Generated by update.py: NVIDIA GPU firmware from upstream linux-firmware, installed to
// /vendor/firmware/nvidia, where ueventd serves it to nouveau

license_kind {
    name: "LicenseRef-%(lic)s",
    conditions: ["by_exception_only"],
    url: "https://gitlab.com/kernel-firmware/linux-firmware/-/blob/main/LICENSES/%(lic)s",
}

license {
    name: "pc_firmware_nvidia_license",
    visibility: ["//visibility:private"],
    license_kinds: ["LicenseRef-%(lic)s"],
    license_text: ["%(lic)s"],
}
""" % {"lic": LICENCE})
        for i, (name, (_, paths)) in enumerate(zip(modules, groups)):
            required = modules[1:] + link_modules if i == 0 else []
            bp.write("""
prebuilt_firmware {
    name: "%s",
    licenses: ["pc_firmware_nvidia_license"],
    srcs: [
%s    ],
    dsts: [
%s    ],
""" % (name, bp_items(paths), bp_items(paths)))
            if required:
                bp.write("    required: [\n%s    ],\n" % bp_items(required))
            bp.write("    vendor: true,\n}\n")
        for name, (path, target) in zip(link_modules, sorted(symlinks.items())):
            bp.write("""
install_symlink {
    name: "%s",
    symlink_target: "%s",
    installed_location: "firmware/%s",
    vendor: true,
}
""" % (name, target, path))

    with open(os.path.join(HERE, "nvidia.mk"), "w", encoding="utf-8") as mk:
        mk.write("""#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

# Generated by update.py: pc_firmware_nvidia (which requires its other modules and symlinks),
# plus the external/linux-firmware(-mainline) modules that install the rest of the files

PRODUCT_PACKAGES += \\
""")
        mk.write(" \\\n".join("    " + n for n in ["pc_firmware_nvidia"] + sorted(others)) + "\n")

    size = sum(os.path.getsize(os.path.join(HERE, p)) for p in real)
    print("%d files (%.1f MB) in %d modules, %d symlinks" %
          (len(real), size / 1e6, len(modules), len(symlinks)))
    print("other modules: " + (", ".join(sorted(others)) or "none"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
