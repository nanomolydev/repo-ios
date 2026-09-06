"""Just enough SerializedFile (Unity 2020+, version 21/22) parsing to find an
object and its type tree. Read-only; patching is done as same-size byte edits.
"""
import struct
from dataclasses import dataclass


@dataclass
class SType:
    class_id: int
    script_index: int
    script_id: bytes
    old_hash: bytes


@dataclass
class Obj:
    path_id: int
    byte_start: int          # relative to data_offset
    byte_size: int
    type_index: int


class SerializedFile:
    def __init__(self, data: bytes):
        self.data = data
        (_, _, self.version, _) = struct.unpack_from(">IIII", data, 0)
        assert self.version >= 22, f"unsupported serialized file version {self.version}"
        self.endianness = data[16]
        p = 20
        (self.metadata_size, self.file_size, self.data_offset, _) = struct.unpack_from(">Iqqq", data, p)
        p += 28
        self.unity_version, p = _cstr(data, p)
        (self.target_platform, self.enable_type_tree) = struct.unpack_from("<iB", data, p)
        p += 5
        (type_count,) = struct.unpack_from("<i", data, p)
        p += 4
        self.types = []
        for _ in range(type_count):
            t, p = self._read_type(p)
            self.types.append(t)
        (obj_count,) = struct.unpack_from("<i", data, p)
        p += 4
        self.objects = []
        for _ in range(obj_count):
            p = _align(p, 4)
            path_id, start, size, tindex = struct.unpack_from("<qqii", data, p)
            p += 24
            self.objects.append(Obj(path_id, start, size, tindex))

    def _read_type(self, p, is_ref=False):
        d = self.data
        (class_id,) = struct.unpack_from("<i", d, p)
        p += 4
        p += 1                                            # is_stripped
        (script_index,) = struct.unpack_from("<h", d, p)
        p += 2
        t = SType(class_id, script_index, b"", b"")
        if class_id == 114:                               # MonoBehaviour
            t.script_id = d[p:p + 16]
            p += 16
        t.old_hash = d[p:p + 16]
        p += 16
        if self.enable_type_tree:                         # stripped in player builds
            (node_count, string_size) = struct.unpack_from("<ii", d, p)
            p += 8 + node_count * 32 + string_size
            (deps,) = struct.unpack_from("<i", d, p)
            p += 4 + deps * 4
        return t, p

    def obj_bytes(self, o: Obj) -> bytes:
        s = self.data_offset + o.byte_start
        return self.data[s:s + o.byte_size]


def _align(p, n):
    return (p + n - 1) & ~(n - 1)


def _cstr(d, p):
    e = d.index(b"\x00", p)
    return d[p:e].decode("utf8"), e + 1
