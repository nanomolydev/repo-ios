"""LZ4 block codec, pure Python.

ponytail: exists only because this box strips native .so files, so the lz4
wheel can't load. On a normal machine `pip install lz4` is faster; the API
here matches lz4.block.decompress/compress closely enough for UnityFS.
"""


def decompress(src: bytes, uncompressed_size: int) -> bytes:
    out = bytearray(uncompressed_size)
    i = 0
    o = 0
    n = len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if lit:
            out[o:o + lit] = src[i:i + lit]
            i += lit
            o += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise ValueError("lz4: zero match offset")
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        s = o - offset
        if s < 0:
            raise ValueError("lz4: match before start")
        for _ in range(ml):          # byte-wise: matches may overlap
            out[o] = out[s]
            o += 1
            s += 1
    if o != uncompressed_size:
        raise ValueError(f"lz4: got {o} bytes, expected {uncompressed_size}")
    return bytes(out)


def compress(src: bytes) -> bytes:
    """LZ4 block compressor, greedy single-candidate matching.

    Not as tight as LZ4HC (the format Unity shipped), but the same format --
    Unity decompresses both with the same routine. Only the handful of blocks
    the patcher rewrites go through it, so speed and ratio barely matter; what
    matters is that the rebuilt bundle keeps a compressed container instead of
    switching to stored blocks.
    """
    n = len(src)
    out = bytearray()
    table = {}
    anchor = 0
    i = 0
    while i < n - MF_LIMIT:
        seq = src[i:i + MIN_MATCH]
        cand = table.get(seq)
        table[seq] = i
        if cand is None or i - cand > 65535:
            i += 1
            continue
        m = MIN_MATCH
        limit = n - LAST_LITERALS
        while i + m < limit and src[cand + m] == src[i + m]:
            m += 1
        _emit_sequence(out, src, anchor, i, i - cand, m)
        i += m
        anchor = i
    _emit_last_literals(out, src, anchor)
    return bytes(out)


MIN_MATCH = 4
LAST_LITERALS = 5
MF_LIMIT = 12


def _emit_sequence(out, src, anchor, pos, offset, match_len):
    lit = pos - anchor
    extra = match_len - MIN_MATCH
    out.append((min(lit, 15) << 4) | min(extra, 15))
    if lit >= 15:
        _lsic(out, lit - 15)
    out += src[anchor:pos]
    out += offset.to_bytes(2, "little")
    if extra >= 15:
        _lsic(out, extra - 15)


def _emit_last_literals(out, src, anchor):
    lit = len(src) - anchor
    out.append(min(lit, 15) << 4)
    if lit >= 15:
        _lsic(out, lit - 15)
    out += src[anchor:]


def _lsic(out, n):
    while n >= 255:
        out.append(255)
        n -= 255
    out.append(n)
