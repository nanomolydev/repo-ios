#!/usr/bin/env python3
"""Patch the iOS port the same way patch_apk.py patches the Android one.

  python3 tools/patch_ipa.py game.ipa -o game-tuned.ipa --version 0.3

The iOS build ships its Unity data as loose serialized files (no data.unity3d
bundle), so QualitySettings is edited straight inside Data/globalgamemanagers --
same offsets, same layout guard, same same-size rule.

The output is unsigned: sideloaders (AltStore, Sideloadly, esign) re-sign it
anyway, and an ad-hoc signature would be thrown away by them.
"""
import argparse
import os
import plistlib
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_fix
import photon_settings as ps
import touch_layout
import quality_settings as qs
import serializedfile as sf

GGM = "Data/globalgamemanagers"
RES = "Data/resources.assets"
SCENE = "Data/level0"          # holds MOBILE CONTROLS


def app_dir(names) -> str:
    for n in names:
        if n.startswith("Payload/") and ".app/" in n:
            return n[:n.index(".app/") + 5]
    raise LookupError("no Payload/*.app in the ipa")


def quality_span(ggm: bytes):
    """(offset, size) of the QualitySettings object inside globalgamemanagers."""
    data_offset = struct.unpack_from(">Iqqq", ggm, 20)[2]
    f = sf.SerializedFile(ggm[:data_offset])
    obj = next(o for o in f.objects if f.types[o.type_index].class_id == 47)
    return f.data_offset + obj.byte_start, obj.byte_size


def photon_span(ggm: bytes, res: bytes):
    """(offset, size) of PhotonServerSettings inside resources.assets."""
    data_offset = struct.unpack_from(">Iqqq", ggm, 20)[2]
    gf = sf.SerializedFile(ggm[:data_offset])
    rm = next(o for o in gf.objects if gf.types[o.type_index].class_id == 147)
    raw = ggm[gf.data_offset + rm.byte_start:][:rm.byte_size]
    (count,) = struct.unpack_from("<i", raw, 0)
    p = 4
    path_id = None
    for _ in range(count):
        name, p = ps._read(raw, p, "str")
        _fid, pid = struct.unpack_from("<iq", raw, p)
        p += 12
        if name.lower() == "photonserversettings":
            path_id = pid
            break
    if path_id is None:
        raise LookupError("photonserversettings not in the ResourceManager")
    rdo = struct.unpack_from(">Iqqq", res, 20)[2]
    rf = sf.SerializedFile(res[:rdo])
    obj = next(o for o in rf.objects if o.path_id == path_id)
    return rf.data_offset + obj.byte_start, obj.byte_size


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--version", help="CFBundleShortVersionString to write")
    ap.add_argument("--render-scale", type=float, default=None,
                    help="resolutionScalingFixedDPIFactor, e.g. 0.7 (default: leave alone)")
    ap.add_argument("--mipmap-limit", type=int, default=None,
                    help="globalTextureMipmapLimit: 1 halves texture resolution "
                         "(default: leave the build's own value alone)")
    ap.add_argument("--pixel-lights", type=int, default=None,
                    help="pixelLightCount per quality level; the build ships 1, "
                         "which makes extra lights pop in and out")
    ap.add_argument("--no-touch-fix", action="store_true",
                    help="leave the on-screen buttons where the port put them")
    ap.add_argument("--no-audio-fix", action="store_true",
                    help="leave the iOS speaker routing alone (it is fixed by default)")
    ap.add_argument("--effects-off", action="store_true",
                    help="also turn off MSAA, anisotropy, soft particles, reflection probes")
    a = ap.parse_args(argv)

    with zipfile.ZipFile(a.ipa) as z:
        names = z.namelist()
        app = app_dir(names)
        ggm = z.read(app + GGM)
        res = z.read(app + RES)
        scene = z.read(app + SCENE)

        qoff, qsize = quality_span(ggm)
        before = qs.describe(ggm[qoff:qoff + qsize])
        print(f"QualitySettings at {qoff} ({qsize} B), active level "
              f"{struct.unpack_from('<i', ggm, qoff)[0]}")
        new_ggm = ggm
        if (a.render_scale is not None or a.mipmap_limit is not None
                or a.effects_off or a.pixel_lights is not None):
            patched = qs.patch(ggm[qoff:qoff + qsize],
                               dpi_factor=a.render_scale,
                               mipmap_limit=a.mipmap_limit,
                               lod_bias=None, particle_budget=None,
                               effects=a.effects_off,
                               pixel_lights=a.pixel_lights)
            new_ggm = ggm[:qoff] + patched + ggm[qoff + qsize:]
            assert len(new_ggm) == len(ggm)
            for b, aft in zip(before, qs.describe(new_ggm[qoff:qoff + qsize])):
                print(f"  {b['level']}: pixelLights {b['pixelLightCount']}->{aft['pixelLightCount']}, "
                      f"mipmapLimit {b['textureMipmapLimit']}->{aft['textureMipmapLimit']}, "
                      f"renderScale {b['resolutionScalingFixedDPIFactor']}->{aft['resolutionScalingFixedDPIFactor']}")

        if not a.no_audio_fix:
            if audio_fix.is_fixed(new_ggm):
                print("audio: already routed to the speaker")
            else:
                off = audio_fix.speaker_flag_offset(new_ggm)
                new_ggm = audio_fix.patch(new_ggm)
                print(f"audio: forceIOSSpeakersWhenRecording 0 -> 1 at {off} "
                      f"(was routing playback to the earpiece)")

        new_scene = scene
        if not a.no_touch_fix:
            if touch_layout.is_moved(new_scene):
                print("touch: sprint button already on the right")
            else:
                new_scene = touch_layout.move_sprint(new_scene)
                print(f"touch: sprint {touch_layout.describe(scene)['pos']} (left edge) "
                      f"-> {touch_layout.describe(new_scene)['pos']} anchored bottom-right")
            if touch_layout.grab_is_moved(new_scene):
                print("touch: grab already clear of the look panel")
            else:
                new_scene = touch_layout.move_grab(new_scene)
                print("touch: grab moved 96 px right, out of the look/mouse panel")
            assert len(new_scene) == len(scene)

        poff, psize = photon_span(ggm, res)
        fields = ps.decode(res[poff:poff + psize])
        ps.validate(fields)
        print(f"Photon: appId={fields['AppIdRealtime'][:8]}..., version={fields['AppVersion']!r}, "
              f"{len(fields['RpcList'])} rpcs -- left untouched")

        with zipfile.ZipFile(a.out, "w", allowZip64=True) as out:
            for info in z.infolist():
                data = z.read(info)
                if info.filename == app + GGM:
                    data = new_ggm
                elif info.filename == app + SCENE:
                    data = new_scene
                elif info.filename == app + "Info.plist" and a.version:
                    pl = plistlib.loads(data)
                    print(f"version {pl.get('CFBundleShortVersionString')} -> {a.version}")
                    pl["CFBundleShortVersionString"] = a.version
                    data = plistlib.dumps(pl)
                elif info.filename.startswith(app + "_CodeSignature/"):
                    continue                      # stale signature, sideloaders re-sign
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                out.writestr(zi, data)
    print(f"wrote {a.out} ({os.path.getsize(a.out) / 1e6:.0f} MB, unsigned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
