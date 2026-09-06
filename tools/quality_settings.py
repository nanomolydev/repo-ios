"""Patch QualitySettings inside globalgamemanagers.

Only fields the game's own Graphics menu never writes are touched, so they
survive GraphicsManager re-applying the player's saved settings at startup.
Offsets are verified against the terrain-defaults signature that ends every
quality level -- if a future build shifts the layout, patching aborts instead
of writing garbage into the middle of the asset.
"""
import struct
# offset inside a level body -> (kind, description)
# Offsets inside a quality level body, from the Unity 2022.3 type tree.
# Valid only while textureMipmapLimitSettings is empty (checked below) -- it is
# a variable-length array and would shift every field after it.
PIXEL_LIGHT_COUNT = 0          # how many lights get per-pixel shading
TEXTURE_MIPMAP_LIMIT = 52
MIPMAP_LIMIT_SETTINGS = 56     # array count, must be 0
ANISOTROPIC = 60
ANTIALIASING = 64
SOFT_PARTICLES = 68            # 4 packed bools: softParticles, softVegetation,
REFLECTION_PROBES = 70         #   realtimeReflectionProbes, billboardsFaceCamera
LOD_BIAS = 84
PARTICLE_RAYCAST_BUDGET = 112
DPI_FACTOR = 128
TERRAIN_SIG = 148              # terrainPixelError .. terrainFadeLength
TERRAIN_DEFAULTS = (1.0, 1.0, 1000.0, 80.0, 5000.0, 50.0, 5.0)

LEVEL_NAMES = [b"Very Low", b"Low", b"Medium", b"High", b"Very High", b"Ultra"]


def locate(bundle):
    """(virtual offset, size) of the QualitySettings object (class id 47)."""
    import serializedfile as sf

    n = bundle.node("globalgamemanagers")
    head = bundle.read(n.offset, 64)
    data_offset = struct.unpack_from(">Iqqq", head, 20)[2]
    f = sf.SerializedFile(bundle.read(n.offset, data_offset))
    obj = next(o for o in f.objects if f.types[o.type_index].class_id == 47)
    return n.offset + f.data_offset + obj.byte_start, obj.byte_size


def level_bodies(raw: bytes) -> list:
    """[(start, end)] of each quality level body, located by its name."""
    spans = []
    starts = []
    for name in LEVEL_NAMES:
        i = raw.find(struct.pack("<i", len(name)) + name)
        if i < 0:
            raise ValueError(f"quality level {name!r} not found")
        starts.append((i, len(name)))
    for k, (i, ln) in enumerate(starts):
        body = (i + 4 + ln + 3) & ~3
        end = starts[k + 1][0] if k + 1 < len(starts) else len(raw)
        spans.append((body, end))
    return spans


def check_layout(raw: bytes) -> None:
    for start, end in level_bodies(raw):
        got = struct.unpack_from("<7f", raw, start + TERRAIN_SIG)
        if tuple(round(v, 3) for v in got) != TERRAIN_DEFAULTS:
            raise ValueError(f"unexpected quality level layout at {start}: {got}")
        if struct.unpack_from("<i", raw, start + MIPMAP_LIMIT_SETTINGS)[0] != 0:
            raise ValueError("textureMipmapLimitSettings is not empty; offsets would shift")
        if end - start < TERRAIN_SIG + 32:
            raise ValueError(f"quality level body too short: {end - start}")


def describe(raw: bytes) -> list:
    out = []
    for (start, end), name in zip(level_bodies(raw), LEVEL_NAMES):
        out.append({
            "level": name.decode(),
            "pixelLightCount": struct.unpack_from("<i", raw, start + PIXEL_LIGHT_COUNT)[0],
            "textureMipmapLimit": struct.unpack_from("<i", raw, start + TEXTURE_MIPMAP_LIMIT)[0],
            "anisotropic": struct.unpack_from("<i", raw, start + ANISOTROPIC)[0],
            "antiAliasing": struct.unpack_from("<i", raw, start + ANTIALIASING)[0],
            "softParticles": raw[start + SOFT_PARTICLES],
            "reflectionProbes": raw[start + REFLECTION_PROBES],
            "particleRaycastBudget": struct.unpack_from("<i", raw, start + PARTICLE_RAYCAST_BUDGET)[0],
            "lodBias": round(struct.unpack_from("<f", raw, start + LOD_BIAS)[0], 3),
            "resolutionScalingFixedDPIFactor": round(struct.unpack_from("<f", raw, start + DPI_FACTOR)[0], 3),
        })
    return out


def patch(raw: bytes, dpi_factor=0.6, mipmap_limit=1, lod_bias=0.4,
          particle_budget=16, effects=True, pixel_lights=None) -> bytes:
    """Every argument accepts None, meaning "leave that field as it is".

    The iOS build wants a different mix from Android: cutting texture
    resolution there makes the thing people complain about (textures) worse.
    """
    check_layout(raw)
    out = bytearray(raw)
    for start, _end in level_bodies(raw):
        if pixel_lights is not None:
            struct.pack_into("<i", out, start + PIXEL_LIGHT_COUNT, pixel_lights)
        if mipmap_limit is not None:
            struct.pack_into("<i", out, start + TEXTURE_MIPMAP_LIMIT, mipmap_limit)
        if effects:
            struct.pack_into("<i", out, start + ANISOTROPIC, 0)
            struct.pack_into("<i", out, start + ANTIALIASING, 0)
            out[start + SOFT_PARTICLES] = 0
            out[start + REFLECTION_PROBES] = 0
        if particle_budget is not None:
            budget = min(struct.unpack_from("<i", raw, start + PARTICLE_RAYCAST_BUDGET)[0],
                         particle_budget)
            struct.pack_into("<i", out, start + PARTICLE_RAYCAST_BUDGET, budget)
        if lod_bias is not None:
            struct.pack_into("<f", out, start + LOD_BIAS, lod_bias)
        if dpi_factor is not None:
            struct.pack_into("<f", out, start + DPI_FACTOR, dpi_factor)
    assert len(out) == len(raw)
    return bytes(out)
