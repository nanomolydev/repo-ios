"""Decode/encode the PhotonServerSettings MonoBehaviour body.

Type trees are stripped in the player build, so the field order comes from the
IL2CPP metadata (declaration order of Photon.Realtime.AppSettings and
Photon.Pun.ServerSettings). decode() checks itself: it must consume the object
exactly, or the layout is wrong and we refuse to patch.
"""
import struct

SCHEMA = [
    ("m_GameObject", "pptr"), ("m_Enabled", "bool"), ("m_Script", "pptr"), ("m_Name", "str"),
    # Photon.Realtime.AppSettings -- order taken from the IL2CPP field table, not
    # guessed. BestRegionSummaryFromStorage sits between FixedRegion and Server in
    # the source but is [NonSerialized], so it is absent here; getting that wrong
    # shifts Server into Port and Unity dies with "Position out of bounds".
    ("AppIdRealtime", "str"), ("AppIdFusion", "str"), ("AppIdChat", "str"), ("AppIdVoice", "str"),
    ("AppVersion", "str"), ("UseNameServer", "bool"), ("FixedRegion", "str"), ("Server", "str"),
    ("Port", "int"), ("ProxyServer", "str"), ("Protocol", "int"),
    ("EnableProtocolFallback", "bool"), ("AuthMode", "int"), ("EnableLobbyStatistics", "bool"),
    ("NetworkLogging", "int"),
    # Photon.Pun.ServerSettings
    ("DevRegion", "str"), ("PunLogging", "int"), ("EnableSupportLogger", "bool"),
    ("RunInBackground", "bool"), ("StartInOfflineMode", "bool"), ("RpcList", "strlist"),
]

# Values PUN ships in a stock ServerSettings asset. A field-order mistake among
# the empty strings round-trips byte for byte, so the only way to catch it is to
# check that the fields we did not touch still read like their defaults.
INVARIANTS = {
    "m_Name": "PhotonServerSettings",
    "Protocol": 0,                 # ConnectionProtocol.Udp
    "EnableProtocolFallback": True,
    "AuthMode": 0,                 # AuthModeOption.Auth
    "NetworkLogging": 1,           # DebugLevel.ERROR
    "RunInBackground": True,
}


def validate(fields: dict) -> None:
    for key, want in INVARIANTS.items():
        if fields[key] != want:
            raise ValueError(f"{key} reads {fields[key]!r}, expected {want!r} -- field order is wrong")
    rpcs = fields["RpcList"]
    if not 50 < len(rpcs) < 2000:
        raise ValueError(f"RpcList has {len(rpcs)} entries; the tail of the object did not parse")
    bad = [r for r in rpcs if not r or not r.replace("_", "").isalnum()]
    if bad:
        raise ValueError(f"RpcList holds non-identifier entries: {bad[:3]}")


def decode(raw: bytes) -> dict:
    out, p = {}, 0
    for name, kind in SCHEMA:
        v, p = _read(raw, p, kind)
        out[name] = v
    if p != len(raw):
        raise ValueError(f"layout mismatch: consumed {p} of {len(raw)} bytes")
    return out


def encode(fields: dict) -> bytes:
    out = bytearray()
    for name, kind in SCHEMA:
        _write(out, fields[name], kind)
    return bytes(out)


def size_of(fields: dict) -> int:
    return len(encode(fields))


def fit_padding(fields: dict, target: int, pad_field: str = "DevRegion") -> dict:
    """Grow/shrink pad_field so the object keeps its original byte size.

    Patching in place is what keeps every other object in the 400 MB bundle
    where it is. BestRegionSummaryFromStorage is the safe filler: it is a
    development-only region override that PUN reads inside
    `if (Application.isEditor)`, so a player build never looks at it.
    AppVersion is deliberately left untouched -- the game parses it
    (DataDirector.PhotonSetVersion).
    """
    fields = dict(fields)
    for extra in range(0, 4096):
        fields[pad_field] = "x" * extra
        if size_of(fields) == target:
            return fields
    raise ValueError(f"cannot pad to {target} bytes (need {size_of(fields) - target} fewer)")


def _read(raw, p, kind):
    if kind == "pptr":
        return struct.unpack_from("<iq", raw, p), p + 12
    if kind == "bool":
        return raw[p] != 0, p + 4
    if kind == "int":
        return struct.unpack_from("<i", raw, p)[0], p + 4
    if kind == "str":
        (n,) = struct.unpack_from("<i", raw, p)
        if n < 0 or p + 4 + n > len(raw):
            raise ValueError(f"bad string length {n} at {p}")
        return raw[p + 4:p + 4 + n].decode("utf8"), _align4(p + 4 + n)
    if kind == "strlist":
        (n,) = struct.unpack_from("<i", raw, p)
        p += 4
        items = []
        for _ in range(n):
            s, p = _read(raw, p, "str")
            items.append(s)
        return items, p
    raise AssertionError(kind)


def _write(out, v, kind):
    if kind == "pptr":
        out += struct.pack("<iq", *v)
    elif kind == "bool":
        out += struct.pack("<i", 1 if v else 0)
    elif kind == "int":
        out += struct.pack("<i", v)
    elif kind == "str":
        b = v.encode("utf8")
        out += struct.pack("<i", len(b)) + b
        out += b"\x00" * (_align4(len(out)) - len(out))
    elif kind == "strlist":
        out += struct.pack("<i", len(v))
        for s in v:
            _write(out, s, "str")
    else:
        raise AssertionError(kind)


def _align4(p):
    return (p + 3) & ~3


def locate(bundle):
    """Find the PhotonServerSettings object inside a data.unity3d bundle.

    Returns (virtual_offset, byte_size). Looked up through the ResourceManager
    container ("photonserversettings" -> PPtr), so it survives asset shuffles
    between game versions instead of depending on a hardcoded offset.
    """
    import serializedfile as sf

    ggm = bundle.node("globalgamemanagers")
    gf = sf.SerializedFile(bundle.read(ggm.offset, _data_offset(bundle, ggm)))
    rm = next(o for o in gf.objects if gf.types[o.type_index].class_id == 147)
    raw = bundle.read(ggm.offset + gf.data_offset + rm.byte_start, rm.byte_size)

    (count,) = struct.unpack_from("<i", raw, 0)
    p = 4
    path_id = None
    for _ in range(count):
        name, p = _read(raw, p, "str")
        _fid, pid = struct.unpack_from("<iq", raw, p)
        p += 12
        if name.lower() == "photonserversettings":
            path_id = pid
            break
    if path_id is None:
        raise KeyError("photonserversettings not in ResourceManager")

    res = bundle.node("resources.assets")
    rf = sf.SerializedFile(bundle.read(res.offset, _data_offset(bundle, res)))
    obj = next(o for o in rf.objects if o.path_id == path_id)
    return res.offset + rf.data_offset + obj.byte_start, obj.byte_size


def _data_offset(bundle, node):
    head = bundle.read(node.offset, 64)
    return struct.unpack_from(">Iqqq", head, 20)[2]
