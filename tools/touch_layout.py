"""Move the port's on-screen buttons around inside a Unity scene file.

The mobile controls live in level0 as plain RectTransforms, so a button is
moved by rewriting five Vector2s -- anchors, anchored position, size, pivot --
all floats, so the file size never changes.

A RectTransform is located by the byte fingerprint of its current geometry
rather than by an offset: if the layout ever differs from what we expect, the
fingerprint stops matching and the patch refuses instead of writing into some
other object.

The canvas is ConstantPixelSize, so all numbers below are screen pixels.
"""
import struct

# name -> (anchorMin, anchorMax, anchoredPos, sizeDelta, pivot)
Geometry = tuple


def pack(anchor_min, anchor_max, pos, size, pivot) -> bytes:
    return struct.pack("<10f", *anchor_min, *anchor_max, *pos, *size, *pivot)


SPRINT_BEFORE = pack((0, 0.5), (0, 0.5), (150, 0), (120, 120), (0.5, 0.5))
# Right-hand cluster: directly above Crouch (which sits at -320,151 from the
# bottom-right) and clear of the two Jump buttons at y 418..788.
SPRINT_AFTER = pack((1, 0), (1, 0), (-320, 330), (120, 120), (0.5, 0.5))


# Grab sits 54 px inside the look panel (the panel spans the screen minus 530 px
# per side; Grab's left half crosses that edge), so a press that slides a little
# can be taken over by the look/drag handler instead of the button. Nudge it out.
GRAB_BEFORE = pack((1, 0.5), (1, 0.5), (-551, 0), (150, 150), (0.5, 0.5))
GRAB_AFTER = pack((1, 0.5), (1, 0.5), (-455, 0), (150, 150), (0.5, 0.5))


def find_one(data: bytes, needle: bytes) -> int:
    hits = []
    pos = data.find(needle)
    while pos != -1:
        hits.append(pos)
        pos = data.find(needle, pos + 1)
    if len(hits) != 1:
        raise ValueError(f"geometry fingerprint matched {len(hits)} times; refusing to guess")
    return hits[0]


def is_moved(data: bytes) -> bool:
    return SPRINT_AFTER in data and SPRINT_BEFORE not in data


def move_sprint(data: bytes) -> bytes:
    off = find_one(data, SPRINT_BEFORE)
    out = bytearray(data)
    out[off:off + len(SPRINT_AFTER)] = SPRINT_AFTER
    assert len(out) == len(data)
    return bytes(out)


def grab_is_moved(data: bytes) -> bool:
    return GRAB_AFTER in data and GRAB_BEFORE not in data


def move_grab(data: bytes) -> bytes:
    off = find_one(data, GRAB_BEFORE)
    out = bytearray(data)
    out[off:off + len(GRAB_AFTER)] = GRAB_AFTER
    assert len(out) == len(data)
    return bytes(out)


def describe(data: bytes) -> dict:
    """What the Sprint button's geometry currently is, for reporting."""
    for tag, blob in (("original", SPRINT_BEFORE), ("moved", SPRINT_AFTER)):
        if blob in data:
            v = struct.unpack("<10f", blob)
            return {"state": tag, "anchor": (v[0], v[1]), "pos": (v[4], v[5]), "size": (v[6], v[7])}
    return {"state": "unknown"}
