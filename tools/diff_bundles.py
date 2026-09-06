#!/usr/bin/env python3
"""Prove the patch touched only what it claims.

  python3 tools/diff_bundles.py original.apk patched.apk

Decompresses both asset bundles block by block and reports every differing
byte range. Anything outside the QualitySettings and PhotonServerSettings
objects is a bug.
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photon_settings as ps
import quality_settings as qs
import unityfs

BUNDLE = "assets/bin/Data/data.unity3d"


def diffs(a: unityfs.Bundle, b: unityfs.Bundle):
    if a.total_usize != b.total_usize:
        yield ("size", a.total_usize, b.total_usize)
        return
    for i, (x, y) in enumerate(zip(a.blocks, b.blocks)):
        if a.raw[x.offset:x.offset + x.csize] == b.raw[y.offset:y.offset + y.csize]:
            continue                                   # identical compressed block
        da, db = a._block_data(x), b._block_data(y)
        start = None
        for k in range(len(da)):
            if da[k] != db[k]:
                if start is None:
                    start = k
                last = k
            elif start is not None and k - last > 64:
                yield ("range", x.uoffset + start, x.uoffset + last + 1)
                start = None
        if start is not None:
            yield ("range", x.uoffset + start, x.uoffset + last + 1)


def main(argv=None):
    argv = argv or sys.argv[1:]
    with zipfile.ZipFile(argv[0]) as z:
        a = unityfs.Bundle(z.read(BUNDLE))
    with zipfile.ZipFile(argv[1]) as z:
        b = unityfs.Bundle(z.read(BUNDLE))

    allowed = [qs.locate(b), ps.locate(b)]
    print("expected patch windows:", [(o, o + s) for o, s in allowed])

    bad = 0
    for kind, x, y in diffs(a, b):
        inside = any(o <= x and y <= o + s for o, s in allowed)
        print(f"  {kind} {x}..{y} {'(expected)' if inside else 'UNEXPECTED'}")
        bad += 0 if inside else 1
    print("clean" if not bad else f"{bad} unexpected difference(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
