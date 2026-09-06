#!/usr/bin/env python3
"""Read a (patched) APK back and report what the game will actually do.

  python3 tools/verify_apk.py game-lan.apk [--expect-server 192.168.137.1]

Exit code is non-zero if a check fails, so CI can gate on it.
"""
import argparse
import os
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photon_settings as ps
import quality_settings as qs
import unityfs

BUNDLE = "assets/bin/Data/data.unity3d"


def check_alignment(path: str, z: zipfile.ZipFile) -> list:
    """Stored entries must start on a 4-byte boundary or Android cannot mmap
    them. Read the local headers, not the central directory -- the two can
    carry different extra fields after re-signing."""
    bad = []
    with open(path, "rb") as f:
        for i in z.infolist():
            if i.compress_type != zipfile.ZIP_STORED:
                continue
            f.seek(i.header_offset)
            name_len, extra_len = struct.unpack("<HH", f.read(30)[26:30])
            data_off = i.header_offset + 30 + name_len + extra_len
            if data_off % 4:
                bad.append((i.filename, data_off % 4))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("apk")
    ap.add_argument("--expect-server")
    ap.add_argument("--expect-abi", default=None, help="e.g. arm64-v8a")
    ap.add_argument("--expect-render-scale", type=float, default=None)
    a = ap.parse_args(argv)

    fail = []
    with zipfile.ZipFile(a.apk) as z:
        abis = sorted({n.split("/")[1] for n in z.namelist() if n.startswith("lib/")})
        print(f"abis: {', '.join(abis)}")
        print(f"size: {os.path.getsize(a.apk) / 1e6:.0f} MB")
        signed = any(n.startswith("META-INF/") and n.endswith((".RSA", ".EC", ".DSA")) for n in z.namelist())
        print(f"v1 signature block: {'yes' if signed else 'no (v2/v3 only or unsigned)'}")

        bad = check_alignment(a.apk, z)
        if bad:
            fail.append(f"unaligned stored entries: {bad[:5]}")
        bundle_data = z.read(BUNDLE)

    b = unityfs.Bundle(bundle_data)
    off, size = ps.locate(b)
    f = ps.decode(b.read(off, size))
    try:
        ps.validate(f)
    except ValueError as e:
        fail.append(str(e))
    print("photon:")
    for k in ("UseNameServer", "Server", "Port", "AppIdRealtime", "AppVersion", "FixedRegion",
              "ProxyServer", "Protocol", "DevRegion", "StartInOfflineMode", "RunInBackground"):
        print(f"  {k} = {f[k]!r}")
    print(f"  RpcList: {len(f['RpcList'])} entries")

    if len(f["RpcList"]) != 284:
        fail.append(f"RpcList has {len(f['RpcList'])} entries, expected 284 -- object layout drifted")
    if a.expect_server:
        if f["AppVersion"] != "v0.1.2.38_beta":
            fail.append(f"AppVersion was modified: {f['AppVersion']!r}")
        if f["Server"] != a.expect_server:
            fail.append(f"Server is {f['Server']!r}, expected {a.expect_server!r}")
        if f["UseNameServer"]:
            fail.append("UseNameServer is still on; the game would go to Photon Cloud")
    qoff, qsize = qs.locate(b)
    levels = qs.describe(b.read(qoff, qsize))
    print("quality settings (all levels):")
    for lvl in levels:
        print("  " + ", ".join(f"{k}={v}" for k, v in lvl.items()))
    if a.expect_render_scale is not None:
        wrong = [l["level"] for l in levels if l["resolutionScalingFixedDPIFactor"] != a.expect_render_scale]
        if wrong:
            fail.append(f"render scale not applied to: {wrong}")
        if any(l["antiAliasing"] or l["textureMipmapLimit"] != 1 for l in levels):
            fail.append("MSAA / texture mipmap limit not applied to every level")

    if a.expect_abi and abis != [a.expect_abi]:
        fail.append(f"abis are {abis}, expected only {a.expect_abi}")

    for msg in fail:
        print(f"FAIL: {msg}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
