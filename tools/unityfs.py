"""Minimal UnityFS bundle reader/writer (Unity 2019.4+ layout, LZ4 blocks).

Only what the patcher needs: list nodes, read a byte range lazily, replace a
byte range and write the bundle back. Untouched blocks are copied verbatim in
their original compressed form, so a 400 MB bundle is rewritten in seconds.
"""
import struct
from dataclasses import dataclass

from lz4_block import compress, decompress


@dataclass
class Block:
    usize: int
    csize: int
    flags: int
    offset: int        # offset of compressed data in the file
    uoffset: int       # offset of uncompressed data in the virtual stream


@dataclass
class Node:
    offset: int
    size: int
    flags: int
    path: str


class Bundle:
    def __init__(self, data: bytes):
        self.raw = data
        p = data.index(b"\x00", 0) + 1                     # "UnityFS\0"
        self.version = struct.unpack_from(">I", data, p)[0]
        p += 4
        self.unity_version, p = _cstr(data, p)
        self.unity_revision, p = _cstr(data, p)
        self.size_off = p
        (self.size, self.ci_size, self.ui_size, self.flags) = struct.unpack_from(">qIII", data, p)
        p += 20
        if self.version >= 7:
            p = _align16(p)
        self.header_end = p

        if self.flags & 0x80:                              # blocks info at end
            ci_off = len(data) - self.ci_size
        else:
            ci_off = p
        self.ci_off = ci_off
        info = _decomp(data[ci_off:ci_off + self.ci_size], self.ui_size, self.flags & 0x3F)

        q = 16                                             # skip hash
        (count,) = struct.unpack_from(">i", info, q)
        q += 4
        self.blocks = []
        off = p if (self.flags & 0x80) else ci_off + self.ci_size
        if self.flags & 0x200:                             # padding before blocks
            off = _align16(off)
        uoff = 0
        for _ in range(count):
            usize, csize, bflags = struct.unpack_from(">IIH", info, q)
            q += 10
            self.blocks.append(Block(usize, csize, bflags, off, uoff))
            off += csize
            uoff += usize
        self.data_start = self.blocks[0].offset if self.blocks else off
        self.data_end = off

        (ncount,) = struct.unpack_from(">i", info, q)
        q += 4
        self.nodes = []
        for _ in range(ncount):
            noff, nsize, nflags = struct.unpack_from(">qqI", info, q)
            q += 20
            path, q = _cstr(info, q)
            self.nodes.append(Node(noff, nsize, nflags, path))
        self.total_usize = uoff

    def node(self, name: str) -> Node:
        for n in self.nodes:
            if n.path == name:
                return n
        raise KeyError(name)

    def read(self, start: int, length: int) -> bytes:
        """Read from the virtual (decompressed) stream."""
        out = bytearray()
        end = start + length
        for b in self.blocks:
            if b.uoffset >= end or b.uoffset + b.usize <= start:
                continue
            chunk = self._block_data(b)
            lo = max(0, start - b.uoffset)
            hi = min(b.usize, end - b.uoffset)
            out += chunk[lo:hi]
        return bytes(out)

    def _block_data(self, b: Block) -> bytes:
        raw = self.raw[b.offset:b.offset + b.csize]
        return _decomp(raw, b.usize, b.flags & 0x3F)

    def replace(self, start: int, old_len: int, new: bytes) -> bytes:
        if old_len != len(new):
            raise ValueError("replace() is same-size only")
        return self.replace_many([(start, new)])

    def replace_many(self, edits: list) -> bytes:
        """[(offset, new_bytes)] in the virtual stream -> rebuilt bundle.

        Same-size only: every node offset and every untouched block then stays
        exactly where it was, so a 400 MB bundle is rebuilt by copying.
        """
        changed = {}
        for start, new in edits:
            end = start + len(new)
            hit = False
            for i, b in enumerate(self.blocks):
                if b.uoffset >= end or b.uoffset + b.usize <= start:
                    continue
                data = bytearray(changed.get(i) or self._block_data(b))
                lo = max(0, start - b.uoffset)
                hi = min(b.usize, end - b.uoffset)
                data[lo:hi] = new[b.uoffset + lo - start:b.uoffset + hi - start]
                changed[i] = bytes(data)
                hit = True
            if not hit:
                raise ValueError(f"offset {start} outside bundle")
        return self._rebuild(changed)

    def _rebuild(self, changed: dict) -> bytes:
        """changed: block index -> new uncompressed block bytes.

        Rewritten blocks and the blocks-info table are re-compressed with the
        same codec the bundle already uses, so the container stays exactly the
        shape Unity shipped. Untouched blocks are copied byte for byte.
        """
        info = bytearray(_decomp(self.raw[self.ci_off:self.ci_off + self.ci_size],
                                self.ui_size, self.flags & 0x3F))
        payloads = []
        q = 16 + 4
        for i, b in enumerate(self.blocks):
            if i in changed:
                if len(changed[i]) != b.usize:
                    raise ValueError("block size changed")
                payload = _comp(changed[i], b.flags & 0x3F)
                bflags = b.flags
            else:
                payload = self.raw[b.offset:b.offset + b.csize]
                bflags = b.flags
            payloads.append(payload)
            struct.pack_into(">IIH", info, q, b.usize, len(payload), bflags)
            q += 10

        cinfo = _comp(bytes(info), self.flags & 0x3F)
        out = bytearray(self.raw[:self.ci_off])
        out += cinfo
        if self.flags & 0x200:
            out += b"\x00" * (_align16(len(out)) - len(out))
        for p in payloads:
            out += p
        out += self.raw[self.data_end:]                    # trailing padding

        struct.pack_into(">qIII", out, self.size_off, len(out), len(cinfo), len(info),
                         self.flags)
        return bytes(out)

def _align16(p: int) -> int:
    return (p + 15) & ~15


def _cstr(data: bytes, p: int):
    e = data.index(b"\x00", p)
    return data[p:e].decode("utf8"), e + 1


def _comp(data: bytes, ctype: int) -> bytes:
    if ctype == 0:
        return data
    if ctype in (2, 3):                                    # LZ4 / LZ4HC: same format
        return compress(data)
    raise NotImplementedError(f"compression type {ctype}")


def _decomp(raw: bytes, usize: int, ctype: int) -> bytes:
    if ctype == 0:
        return raw
    if ctype in (2, 3):
        return decompress(raw, usize)
    raise NotImplementedError(f"compression type {ctype}")
