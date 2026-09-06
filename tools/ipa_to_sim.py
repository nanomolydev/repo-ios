#!/usr/bin/env python3
"""Turn a device .ipa into an .app the iOS Simulator will run.

  python3 tools/ipa_to_sim.py game.ipa -o build-sim

A device build is arm64 Mach-O with LC_BUILD_VERSION platform=IOS(2). An
Apple-silicon simulator executes the same instruction set, so the only thing in
the way is that platform byte, the Info.plist platform strings, and the old
signature. Flip those, ad-hoc re-sign on macOS, and simctl installs it.

Nothing about the game's own code or data is touched.
"""
import argparse
import os
import plistlib
import shutil
import struct
import sys
import zipfile

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_IPHONEOS = 0x25
PLATFORM_IOS = 2
PLATFORM_IOS_SIMULATOR = 7


def patch_macho(path: str) -> bool:
    """Rewrite LC_BUILD_VERSION.platform in place. Returns True if changed."""
    with open(path, "r+b") as f:
        data = bytearray(f.read())
        if len(data) < 32:
            return False
        (magic,) = struct.unpack_from("<I", data, 0)
        if magic == FAT_MAGIC or struct.unpack_from(">I", data, 0)[0] == FAT_MAGIC:
            raise NotImplementedError(f"{path}: fat binaries are not handled")
        if magic != MH_MAGIC_64:
            return False
        ncmds = struct.unpack_from("<I", data, 16)[0]
        p = 32
        changed = False
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", data, p)
            if cmd == LC_BUILD_VERSION:
                platform = struct.unpack_from("<I", data, p + 8)[0]
                if platform == PLATFORM_IOS:
                    struct.pack_into("<I", data, p + 8, PLATFORM_IOS_SIMULATOR)
                    changed = True
            elif cmd == LC_VERSION_MIN_IPHONEOS:
                # Old-style load command the simulator refuses; neuter it into
                # LC_VERSION_MIN_IPHONEOSSIMULATOR (0x28).
                struct.pack_into("<I", data, p, 0x28)
                changed = True
            p += size
        if changed:
            f.seek(0)
            f.write(data)
            f.truncate()
    return changed


def is_macho(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return struct.unpack("<I", f.read(4))[0] in (MH_MAGIC_64, FAT_MAGIC)
    except (OSError, struct.error):
        return False


def fix_plist(path: str) -> None:
    with open(path, "rb") as f:
        pl = plistlib.load(f)
    pl["CFBundleSupportedPlatforms"] = ["iPhoneSimulator"]
    for key, value in (("DTPlatformName", "iphonesimulator"),
                       ("DTSDKName", "iphonesimulator18.2")):
        if key in pl:
            pl[key] = value
    pl.pop("UIRequiresFullScreen", None)
    with open(path, "wb") as f:
        plistlib.dump(pl, f)


def convert(ipa: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(ipa) as z:
        z.extractall(out_dir)
    payload = os.path.join(out_dir, "Payload")
    app = next(os.path.join(payload, d) for d in os.listdir(payload) if d.endswith(".app"))

    for root, dirs, files in os.walk(app):
        for d in list(dirs):
            if d == "_CodeSignature":
                shutil.rmtree(os.path.join(root, d))
                dirs.remove(d)
        for name in files:
            p = os.path.join(root, name)
            if name == "embedded.mobileprovision":
                os.remove(p)
            elif name == "Info.plist" and root == app:
                fix_plist(p)
            elif is_macho(p):
                if patch_macho(p):
                    print(f"  patched {os.path.relpath(p, app)}")
                os.chmod(p, 0o755)
    return app


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa")
    ap.add_argument("-o", "--out", default="build-sim")
    a = ap.parse_args(argv)
    app = convert(a.ipa, a.out)
    print(f"simulator app: {app}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
