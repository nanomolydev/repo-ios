#!/usr/bin/env python3
"""Build a LAN / slim variant of the R.E.P.O. Android port.

  python3 tools/patch_apk.py game.apk -o game-lan.apk --server 192.168.137.1

What it changes, and nothing else:
  * PhotonServerSettings -> talk to a self-hosted Photon Server on the LAN
    instead of Photon Cloud (UseNameServer=false, Server=<ip>, Port=5055).
  * drops non-arm64 native libs (--keep-abi all to disable).
The patched object keeps its exact byte size, so the 400 MB asset bundle is
rebuilt by copying blocks, not by re-serializing the game.

Graphics are NOT touched: the game applies its own settings from the in-game
Graphics menu at runtime, so anything baked into QualitySettings here would be
overwritten a second later. See README.
"""
import argparse
import os
import struct
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photon_settings as ps
import quality_settings as qs
import unityfs

BUNDLE = "assets/bin/Data/data.unity3d"
SIG = ("META-INF/MANIFEST.MF", "META-INF/CERT.SF", "META-INF/CERT.RSA")


def patch_bundle(data: bytes, server, port: int, graphics: bool, dpi: float) -> bytes:
    bundle = unityfs.Bundle(data)
    edits = []
    if graphics:
        qoff, qsize = qs.locate(bundle)
        qraw = bundle.read(qoff, qsize)
        edits.append((qoff, qs.patch(qraw, dpi_factor=dpi)))
        print(f"  QualitySettings at {qoff} ({qsize} B): mipmap limit 1, AA off, "
              f"aniso off, lodBias 0.4, particles 8, render scale {dpi}")
    if server is None:
        return bundle.replace_many(edits) if edits else data

    off, size = ps.locate(bundle)
    fields = ps.decode(bundle.read(off, size))
    ps.validate(fields)
    print(f"  found {fields['m_Name']} at {off} ({size} B), "
          f"server={fields['Server']!r} nameserver={fields['UseNameServer']}")

    fields["UseNameServer"] = False
    fields["Server"] = server
    fields["Port"] = port
    # Safe only because Server is set: PUN's ConnectUsingSettings guard is
    # "no AppId AND not IsMasterServerAddress", and IsMasterServerAddress is
    # just "Server is not empty". A self-hosted server does not check app ids.
    fields["AppIdRealtime"] = ""
    fields = ps.fit_padding(fields, size)
    new = ps.encode(fields)
    ps.validate(ps.decode(new))
    assert len(new) == size
    print(f"  -> server={server}:{port}, nameserver off, appid cleared")
    edits.append((off, new))
    return bundle.replace_many(edits)


def keep_entry(name: str, keep_abi: str) -> bool:
    if name in SIG:
        return False
    if keep_abi != "all" and name.startswith("lib/") and not name.startswith(f"lib/{keep_abi}/"):
        return False
    return True


def repack(src: str, dst: str, new_bundle: bytes, keep_abi: str) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", allowZip64=True) as zout:
        for info in zin.infolist():
            if not keep_entry(info.filename, keep_abi):
                print(f"  drop {info.filename}")
                continue
            data = new_bundle if info.filename == BUNDLE else zin.read(info)
            out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            out.compress_type = info.compress_type
            out.external_attr = info.external_attr
            out.create_system = info.create_system
            _align(zout, out)
            zout.writestr(out, data)


def _align(zf: zipfile.ZipFile, info: zipfile.ZipInfo, boundary: int = 4) -> None:
    """zipalign, inline: stored entries must start on a 4-byte boundary or
    Android's mmap of the asset fails."""
    if info.compress_type != zipfile.ZIP_STORED:
        return
    header = 30 + len(info.filename.encode("utf8"))
    pad = -(zf.fp.tell() + header) % boundary
    if pad:
        if pad < 4:
            pad += boundary
        info.extra = struct.pack("<HH", 0xD935, pad - 4) + b"\x00" * (pad - 4)


def sign(apk: str, keystore: str) -> bool:
    """apksigner if the Android SDK is around, otherwise uber-apk-signer.jar."""
    if not _which("apksigner"):
        jar = os.environ.get("UBER_APK_SIGNER", os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uber-apk-signer.jar"))
        if _which("java") and os.path.exists(jar):
            out = os.path.dirname(os.path.abspath(apk))
            subprocess.run(["java", "-jar", jar, "-a", apk, "--allowResign",
                            "--skipZipAlign", "-o", out], check=True)
            signed = os.path.join(out, os.path.basename(apk).replace(".apk", "-debugSigned.apk"))
            os.replace(signed, apk)
            for junk in (signed + ".idsig",):
                if os.path.exists(junk):
                    os.remove(junk)
            print(f"signed {apk} (debug key, v1+v2+v3)")
            return True
        print("no apksigner and no uber-apk-signer.jar -- output is UNSIGNED")
        return False
    if not os.path.exists(keystore):
        subprocess.run(["keytool", "-genkeypair", "-keystore", keystore, "-storepass", "android",
                        "-keypass", "android", "-alias", "repo", "-keyalg", "RSA", "-keysize", "2048",
                        "-validity", "10000", "-dname", "CN=repo-lan"], check=True)
    subprocess.run(["apksigner", "sign", "--ks", keystore, "--ks-pass", "pass:android",
                    "--key-pass", "pass:android", apk], check=True)
    subprocess.run(["apksigner", "verify", apk], check=True)
    return True


def _which(cmd):
    from shutil import which
    return which(cmd)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apk")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--server", default="192.168.137.1",
                    help="Photon Server address baked into the build "
                         "(default: the Windows Mobile Hotspot gateway)")
    ap.add_argument("--port", type=int, default=5055)
    ap.add_argument("--keep-abi", default="arm64-v8a", help="arm64-v8a | armeabi-v7a | all")
    ap.add_argument("--cloud", action="store_true", help="keep Photon Cloud (skip the LAN patch)")
    ap.add_argument("--no-graphics", action="store_true", help="skip the QualitySettings patch")
    ap.add_argument("--render-scale", type=float, default=0.6,
                    help="resolutionScalingFixedDPIFactor baked into every quality level")
    ap.add_argument("--keystore", default="repo-lan.keystore")
    ap.add_argument("--no-sign", action="store_true")
    a = ap.parse_args(argv)

    with zipfile.ZipFile(a.apk) as z:
        bundle = z.read(BUNDLE)
    print(f"{a.apk}: bundle {len(bundle) / 1e6:.0f} MB")
    new_bundle = patch_bundle(bundle, None if a.cloud else a.server, a.port,
                              not a.no_graphics, a.render_scale)
    repack(a.apk, a.out, new_bundle, a.keep_abi)
    print(f"wrote {a.out} ({os.path.getsize(a.out) / 1e6:.0f} MB)")
    if not a.no_sign:
        sign(a.out, a.keystore)
    return 0


if __name__ == "__main__":
    sys.exit(main())
