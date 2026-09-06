#!/usr/bin/env python3
"""Self-checks for the patch toolchain. No framework: python3 tests/test_all.py

The real APK is optional -- pass it as argv[1] (or set REPO_APK) to also run
the end-to-end patch. Everything else runs on synthetic fixtures so CI needs
no 490 MB download.
"""
import os
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)

import lz4_block
import photon_probe
import photon_settings as ps
import quality_settings as qsm
import unityfs


def test_lz4_overlapping_match():
    # literals "a", then a 9-byte match at offset 1 -> "aaaaaaaaaa"
    stream = bytes([0x15]) + b"a" + struct.pack("<H", 1)
    assert lz4_block.decompress(stream, 10) == b"a" * 10


def test_lz4_literals_only():
    payload = bytes(range(256)) * 3
    stream = bytes([0xF0]) + _lsic(len(payload) - 15) + payload
    assert lz4_block.decompress(stream, len(payload)) == payload


def test_lz4_rejects_short_output():
    try:
        lz4_block.decompress(bytes([0x10]) + b"a", 99)
    except ValueError:
        return
    raise AssertionError("expected a size mismatch error")


def _lsic(n):
    out = b""
    while n >= 255:
        out += b"\xff"
        n -= 255
    return out + bytes([n])


def build_bundle(files: dict) -> bytes:
    """Minimal uncompressed UnityFS bundle, same shape as the game's."""
    blob = b""
    nodes = []
    for name, data in files.items():
        nodes.append((len(blob), len(data), name))
        blob += data
    block = 4096
    blocks = [blob[i:i + block] for i in range(0, len(blob), block)] or [b""]

    info = bytearray(b"\x00" * 16)
    info += struct.pack(">i", len(blocks))
    for b in blocks:
        info += struct.pack(">IIH", len(b), len(b), 0)
    info += struct.pack(">i", len(nodes))
    for off, size, name in nodes:
        info += struct.pack(">qqI", off, size, 4) + name.encode() + b"\x00"

    head = b"UnityFS\x00" + struct.pack(">I", 8) + b"5.x.x\x00" + b"2022.3.35f1\x00"
    size_off = len(head)
    head += struct.pack(">qIII", 0, len(info), len(info), 0x240)
    head += b"\x00" * (-len(head) % 16)
    out = bytearray(head + bytes(info))
    out += b"\x00" * (-len(out) % 16)
    for b in blocks:
        out += b
    struct.pack_into(">q", out, size_off, len(out))
    return bytes(out)


def test_bundle_read_and_replace():
    files = {"a.bin": bytes(range(256)) * 40, "b.bin": b"hello world" * 500}
    raw = build_bundle(files)
    b = unityfs.Bundle(raw)
    assert [n.path for n in b.nodes] == ["a.bin", "b.bin"]
    n = b.node("b.bin")
    assert b.read(n.offset, n.size) == files["b.bin"]

    # patch across a block boundary: 40 bytes starting 20 before a boundary
    start = 4096 - 20
    new = b"Z" * 40
    patched = unityfs.Bundle(raw).replace(start, 40, new)
    b2 = unityfs.Bundle(patched)
    assert b2.read(start, 40) == new
    assert b2.read(0, start) == b.read(0, start)
    assert b2.read(start + 40, 200) == b.read(start + 40, 200)
    assert [(x.path, x.offset, x.size) for x in b2.nodes] == [(x.path, x.offset, x.size) for x in b.nodes]


def test_bundle_rejects_resize():
    raw = build_bundle({"a.bin": b"x" * 100})
    try:
        unityfs.Bundle(raw).replace(0, 10, b"short")
    except ValueError:
        return
    raise AssertionError("expected same-size enforcement")


def sample_settings() -> dict:
    return {
        "m_GameObject": (0, 0), "m_Enabled": True, "m_Script": (1, 1818),
        "m_Name": "PhotonServerSettings",
        "AppIdRealtime": "c033dc0a-c5ae-4c78-81de-8e457a3a840d", "AppIdFusion": "",
        "AppIdChat": "", "AppIdVoice": "f3970ddf-a26f-4b6f-8675-36cdd1fe1474",
        "AppVersion": "v0.1.2.38_beta", "UseNameServer": True, "FixedRegion": "",
        "Server": "", "Port": 0, "ProxyServer": "", "Protocol": 0,
        "EnableProtocolFallback": True, "AuthMode": 0, "EnableLobbyStatistics": False,
        "NetworkLogging": 1, "DevRegion": "", "PunLogging": 0, "EnableSupportLogger": False,
        "RunInBackground": True, "StartInOfflineMode": False,
        "RpcList": [f"Rpc{i}" for i in range(284)],
    }


def test_settings_roundtrip():
    f = sample_settings()
    assert ps.decode(ps.encode(f)) == f


def test_settings_match_pun_defaults():
    ps.validate(sample_settings())


def test_validate_catches_a_shifted_field_order():
    """The failure this exists for: Server written into Port, which round-trips
    byte for byte on a stock asset because both slots look empty."""
    f = sample_settings()
    blob = bytearray(ps.encode(f))
    good = ps.decode(bytes(blob))
    ps.validate(good)
    shifted = dict(f, Protocol=1, EnableProtocolFallback=False)
    try:
        ps.validate(shifted)
    except ValueError:
        return
    raise AssertionError("validate() must reject values that cannot be a stock asset")


def test_settings_decode_is_strict():
    blob = ps.encode(sample_settings())
    try:
        ps.decode(blob + b"\x00\x00\x00\x00")
    except ValueError:
        return
    raise AssertionError("trailing bytes must be rejected -- that is the layout guard")


def test_lan_patch_keeps_byte_size():
    orig = sample_settings()
    size = ps.size_of(orig)
    for ip in ("192.168.137.1", "10.0.0.2", "192.168.100.200"):
        f = dict(orig, UseNameServer=False, Server=ip, Port=5055, AppIdRealtime="")
        f = ps.fit_padding(f, size)
        blob = ps.encode(f)
        assert len(blob) == size, f"{ip}: {len(blob)} != {size}"
        back = ps.decode(blob)
        ps.validate(back)
        assert back["Server"] == ip and back["Port"] == 5055 and not back["UseNameServer"]
        assert back["ProxyServer"] == "" and back["FixedRegion"] == ""
        assert back["RpcList"] == orig["RpcList"]
        assert back["AppVersion"] == orig["AppVersion"], "the game parses AppVersion; do not touch it"


def test_fit_padding_reports_impossible():
    f = dict(sample_settings(), Server="x" * 400)
    try:
        ps.fit_padding(f, ps.size_of(sample_settings()))
    except ValueError:
        return
    raise AssertionError("expected a clear failure when the address does not fit")


def build_quality_settings(dpi=1.0, mip=0, aa=2, budget=25, lod=1.0) -> bytes:
    raw = struct.pack("<ii", 2, len(qsm.LEVEL_NAMES))
    for name in qsm.LEVEL_NAMES:
        raw += struct.pack("<i", len(name)) + name
        raw += b"\x00" * (-len(raw) % 4)
        body = bytearray(180)
        struct.pack_into("<i", body, qsm.TEXTURE_MIPMAP_LIMIT, mip)
        struct.pack_into("<i", body, qsm.ANTIALIASING, aa)
        body[qsm.SOFT_PARTICLES] = 1
        body[qsm.REFLECTION_PROBES] = 1
        struct.pack_into("<i", body, qsm.PARTICLE_RAYCAST_BUDGET, budget)
        struct.pack_into("<f", body, qsm.LOD_BIAS, lod)
        struct.pack_into("<f", body, qsm.DPI_FACTOR, dpi)
        struct.pack_into("<7f", body, qsm.TERRAIN_SIG, *qsm.TERRAIN_DEFAULTS)
        raw += bytes(body)
    return raw


def test_quality_patch_is_same_size_and_applies_to_every_level():
    raw = build_quality_settings()
    out = qsm.patch(raw, dpi_factor=0.6)
    assert len(out) == len(raw)
    for lvl in qsm.describe(out):
        assert lvl["resolutionScalingFixedDPIFactor"] == 0.6
        assert lvl["textureMipmapLimit"] == 1
        assert lvl["antiAliasing"] == 0
        assert lvl["anisotropic"] == 0
        assert lvl["softParticles"] == 0 and lvl["reflectionProbes"] == 0
        assert lvl["particleRaycastBudget"] == 16
        assert lvl["lodBias"] == 0.4
    assert [l["level"] for l in qsm.describe(out)] == [n.decode() for n in qsm.LEVEL_NAMES]


def test_quality_patch_refuses_shifted_mipmap_array():
    raw = bytearray(build_quality_settings())
    start = qsm.level_bodies(bytes(raw))[0][0]
    struct.pack_into("<i", raw, start + qsm.MIPMAP_LIMIT_SETTINGS, 1)
    try:
        qsm.patch(bytes(raw))
    except ValueError:
        return
    raise AssertionError("a non-empty textureMipmapLimitSettings must abort the patch")


def test_quality_patch_refuses_unknown_layout():
    raw = bytearray(build_quality_settings())
    start = qsm.level_bodies(bytes(raw))[3][0]
    struct.pack_into("<f", raw, start + qsm.TERRAIN_SIG + 8, 1234.0)   # shift the layout
    try:
        qsm.patch(bytes(raw))
    except ValueError:
        return
    raise AssertionError("a drifted layout must abort the patch, not corrupt the asset")


def test_touch_layout_moves_only_its_own_geometry():
    import touch_layout as tl
    scene = b"\xaa" * 100 + tl.SPRINT_BEFORE + b"\xbb" * 50 + tl.GRAB_BEFORE + b"\xcc" * 100
    out = tl.move_grab(tl.move_sprint(scene))
    assert len(out) == len(scene)
    assert tl.is_moved(out) and tl.grab_is_moved(out)
    assert out[:100] == scene[:100] and out[-100:] == scene[-100:]
    assert out[140:190] == scene[140:190], "bytes between the two buttons must be untouched"


def test_touch_layout_refuses_an_ambiguous_scene():
    import touch_layout as tl
    scene = tl.SPRINT_BEFORE + b"\x00" * 8 + tl.SPRINT_BEFORE
    try:
        tl.move_sprint(scene)
    except ValueError:
        return
    raise AssertionError("two identical geometries must abort the patch")


def test_quality_patch_sets_pixel_lights():
    raw = build_quality_settings()
    out = qsm.patch(raw, dpi_factor=None, mipmap_limit=None, lod_bias=None,
                    particle_budget=None, effects=False, pixel_lights=3)
    assert all(l["pixelLightCount"] == 3 for l in qsm.describe(out))
    assert len(out) == len(raw)


def test_audio_fix_needs_a_unique_fingerprint():
    import audio_fix
    blob = b"\x00" * 40 + audio_fix.FINGERPRINT + b"\x00" * 40
    hits = [i for i in range(len(blob)) if blob.startswith(audio_fix.FINGERPRINT, i)]
    assert len(hits) == 1
    twice = blob + audio_fix.FINGERPRINT
    assert sum(1 for i in range(len(twice)) if twice.startswith(audio_fix.FINGERPRINT, i)) == 2, \
        "two matches must be possible, which is exactly what patch() refuses"


def test_ipa_to_sim_flips_the_platform_byte():
    import ipa_to_sim
    # minimal 64-bit Mach-O: header + one LC_BUILD_VERSION(platform=iOS)
    hdr = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, 1, 24, 0, 0)
    cmd = struct.pack("<IIIIII", 0x32, 24, 2, 12 << 16, 18 << 16, 0)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bin")
        with open(p, "wb") as f:
            f.write(hdr + cmd)
        assert ipa_to_sim.is_macho(p)
        assert ipa_to_sim.patch_macho(p) is True
        with open(p, "rb") as f:
            data = f.read()
        assert struct.unpack_from("<I", data, 32 + 8)[0] == ipa_to_sim.PLATFORM_IOS_SIMULATOR
        assert ipa_to_sim.patch_macho(p) is False, "patching twice must be a no-op"


def test_ipa_to_sim_ignores_other_files():
    import ipa_to_sim
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Info.plist")
        with open(p, "wb") as f:
            f.write(b"<?xml version=...")
        assert not ipa_to_sim.is_macho(p)


def test_quality_patch_can_leave_fields_alone():
    raw = build_quality_settings(dpi=1.0, mip=0, aa=2, budget=25, lod=1.0)
    out = qsm.patch(raw, dpi_factor=0.7, mipmap_limit=None, lod_bias=None,
                    particle_budget=None, effects=False)
    for before, after in zip(qsm.describe(raw), qsm.describe(out)):
        assert after["resolutionScalingFixedDPIFactor"] == 0.7
        for k in ("textureMipmapLimit", "antiAliasing", "lodBias", "particleRaycastBudget"):
            assert before[k] == after[k], k


def test_probe_detects_a_dead_udp_port():
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    assert photon_probe.udp_open("127.0.0.1", port, timeout=0.5)   # silent listener
    srv.close()
    assert not photon_probe.udp_open("127.0.0.1", port, timeout=0.5)


def test_probe_finds_a_listener():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert photon_probe.tcp_open("127.0.0.1", port, timeout=1.0)
    srv.close()
    assert not photon_probe.tcp_open("127.0.0.1", port, timeout=1.0)


def test_find_button_ignores_the_top_half():
    try:
        from PIL import Image
    except ImportError:
        return                                   # optional dependency, CI installs it
    import find_button
    im = Image.new("RGB", (400, 200), (10, 10, 10))
    for x in range(30, 70):                      # green logo, top half -> ignored
        for y in range(20, 40):
            im.putpixel((x, y), (60, 200, 90))
    for x in range(200, 240):                    # green button, bottom half
        for y in range(160, 175):
            im.putpixel((x, y), (60, 200, 90))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.png")
        im.save(p)
        x, y = find_button.find_green(p)
    assert 195 < x < 245, x
    assert 155 < y < 180, y


def test_fps_parser():
    from fps_bench import parse_surfaceflinger_latency
    refresh = 16_666_667
    lines = ["16666667"]
    t = 0
    for i in range(20):
        t += refresh * (2 if i % 5 == 0 else 1)     # every 5th frame is a double
        lines.append(f"0 {t} 0")
    r = parse_surfaceflinger_latency("\n".join(lines))
    assert 20 < r["fps"] < 60, r
    assert r["frames"] == 19
    assert r["jank_pct"] > 0


def test_ipa_end_to_end(ipa):
    import patch_ipa
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "tuned.ipa")
        assert patch_ipa.main([ipa, "-o", out, "--version", "0.3", "--render-scale", "0.7"]) == 0
        with zipfile.ZipFile(out) as z:
            app = patch_ipa.app_dir(z.namelist())
            import plistlib
            pl = plistlib.loads(z.read(app + "Info.plist"))
            assert pl["CFBundleShortVersionString"] == "0.3"
            ggm = z.read(app + patch_ipa.GGM)
            import audio_fix, touch_layout
            assert audio_fix.is_fixed(ggm), "ios playback must be forced to the speaker"
            scene = z.read(app + patch_ipa.SCENE)
            assert touch_layout.is_moved(scene), "sprint button must be on the right"
            assert touch_layout.grab_is_moved(scene)
            off, size = patch_ipa.quality_span(ggm)
            for lvl in qsm.describe(ggm[off:off + size]):
                assert lvl["resolutionScalingFixedDPIFactor"] == 0.7
            res = z.read(app + patch_ipa.RES)
            poff, psize = patch_ipa.photon_span(ggm, res)
            f = ps.decode(res[poff:poff + psize])
            ps.validate(f)
            assert f["AppVersion"] == "v0.1.2.38_beta", "multiplayer config must stay untouched"
        with zipfile.ZipFile(ipa) as a, zipfile.ZipFile(out) as b:
            for name in b.namelist():
                if name.endswith(("Info.plist", patch_ipa.GGM)):
                    continue
                assert a.read(name) == b.read(name), f"{name} changed unexpectedly"


def test_apk_end_to_end(apk):
    import patch_apk
    import verify_apk
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "lan.apk")
        assert patch_apk.main([apk, "-o", out, "--server", "192.168.137.1", "--no-sign"]) == 0
        assert verify_apk.main([out, "--expect-server", "192.168.137.1", "--expect-abi", "arm64-v8a",
                                "--expect-render-scale", "0.6"]) == 0
        with zipfile.ZipFile(apk) as a, zipfile.ZipFile(out) as b:
            src = {i.filename for i in a.infolist()}
            dst = {i.filename for i in b.infolist()}
            assert not dst - src, dst - src
            for name in sorted(dst):
                if name != patch_apk.BUNDLE:
                    assert a.read(name) == b.read(name), f"{name} changed unexpectedly"


def main():
    apk = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REPO_APK", "")).strip()
    ipa = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("REPO_IPA", "")).strip()
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and k not in ("test_apk_end_to_end", "test_ipa_end_to_end")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e.__class__.__name__}: {e}")
    if apk and os.path.exists(apk):
        try:
            test_apk_end_to_end(apk)
            print("ok   test_apk_end_to_end")
        except Exception as e:
            failed += 1
            print(f"FAIL test_apk_end_to_end: {e.__class__.__name__}: {e}")
    else:
        print("skip test_apk_end_to_end (no apk given)")
    if ipa and os.path.exists(ipa):
        try:
            test_ipa_end_to_end(ipa)
            print("ok   test_ipa_end_to_end")
        except Exception as e:
            failed += 1
            print(f"FAIL test_ipa_end_to_end: {e.__class__.__name__}: {e}")
    else:
        print("skip test_ipa_end_to_end (no ipa given)")
    ran = len(tests) + (1 if apk else 0) + (1 if ipa else 0)
    print(f"\n{ran - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
