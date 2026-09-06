#!/usr/bin/env python3
"""Second opinion on a patched bundle, using UnityPy instead of our own reader.

  pip install UnityPy && python3 tools/crosscheck_unitypy.py original.apk patched.apk

Reads both asset bundles with an independent implementation and compares the
structure (sub-files, object counts) plus the QualitySettings type tree, which
is the authoritative field layout. Skips cleanly if UnityPy is not installed.
"""
import sys
import zipfile

BUNDLE = "assets/bin/Data/data.unity3d"
WATCH = ("globalTextureMipmapLimit", "anisotropicTextures", "antiAliasing", "lodBias",
         "particleRaycastBudget", "resolutionScalingFixedDPIFactor", "softParticles",
         "realtimeReflectionProbes", "customRenderPipeline", "terrainPixelError")


def load(path):
    import UnityPy
    return UnityPy.load(zipfile.ZipFile(path).read(BUNDLE))


def structure(env):
    out = {}
    for f in env.files.values():
        for name, sub in f.files.items():
            out[name] = len(getattr(sub, "objects", {}) or {})
    return out


def quality(env):
    for f in env.files.values():
        ggm = f.files.get("globalgamemanagers")
        if not ggm:
            continue
        for o in ggm.objects.values():
            if o.type.value == 47:
                return o.read_typetree()
    raise LookupError("QualitySettings not found")


def main(argv=None):
    argv = argv or sys.argv[1:]
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("UnityPy not installed -- skipping cross-check")
        return 0

    a, b = load(argv[0]), load(argv[1])
    sa, sb = structure(a), structure(b)
    print(f"sub-files: {len(sa)} -> {len(sb)}, objects: {sum(sa.values()):,} -> {sum(sb.values()):,}")
    fail = []
    if sa != sb:
        fail.append(f"structure changed: {set(sa.items()) ^ set(sb.items())}")

    qa, qb = quality(a), quality(b)
    if len(qa["m_QualitySettings"]) != len(qb["m_QualitySettings"]):
        fail.append("quality level count changed")
    for la, lb in zip(qa["m_QualitySettings"], qb["m_QualitySettings"]):
        changed = {k: (la[k], lb[k]) for k in WATCH if la[k] != lb[k]}
        print(f"  {lb['name']}: " + ", ".join(f"{k} {v[0]}->{v[1]}" for k, v in changed.items()))
        if lb["customRenderPipeline"] != {"m_FileID": 0, "m_PathID": 0}:
            fail.append(f"{lb['name']}: customRenderPipeline got clobbered")
        if round(lb["terrainPixelError"], 3) != 1.0:
            fail.append(f"{lb['name']}: terrain block got clobbered")
        if lb["textureMipmapLimitSettings"]:
            fail.append(f"{lb['name']}: textureMipmapLimitSettings is not empty")
    for m in fail:
        print("FAIL:", m)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
