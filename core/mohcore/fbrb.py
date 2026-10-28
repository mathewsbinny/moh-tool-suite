"""Read-only access to .fbrb bundles without unpacking them (the layout FbrbTool reads, moh-fbrb-tool/FbrbTool.cs).

    entries(path)                    [Entry]: name, type, flags, offset, size, size2 - reads only the header (fast)
    names(path)                      {lower-case entry name: Entry}, *deleted* markers left out
    read(path, [name, ...])          {name: bytes} of the entries asked for (the payload is decompressed once, up to
                                     the last one asked for; a missing name raises KeyError)

Layout: "FbRB", u32 big-endian header length, the header (gzip): u32, u32 string-table length, the strings, u32 entry
count, per entry six u32 (name offset, flags, payload offset, size, size2, type offset), one byte (payload zipped),
u32 payload length; then the payload (gzip when zipped). Changing a bundle stays with FbrbTool (unpack / pack).
"""
import gzip
import struct
import zlib
from dataclasses import dataclass

DELETED = '*deleted*'


@dataclass
class Entry:
    name: str
    type: str
    flags: int
    offset: int
    size: int
    size2: int

    @property
    def deleted(self):
        return self.type == DELETED and self.size == 0


def _cstring(b, o):
    e = b.index(b'\0', o)
    return b[o:e].decode('ascii', errors='replace')


def _header(fh):
    head = fh.read(8)
    if len(head) < 8 or head[:4] != b'FbRB':
        raise ValueError(f'{getattr(fh, "name", "file")} is not an FbRB bundle')
    cut = struct.unpack('>I', head[4:8])[0]
    h = gzip.decompress(fh.read(cut))
    strlen = struct.unpack_from('>I', h, 4)[0]
    base = 8
    count = struct.unpack_from('>I', h, base + strlen)[0]
    out = []
    for i in range(count):
        no, flags, off, size, size2, to = struct.unpack_from('>6I', h, base + strlen + 4 + 24 * i)
        out.append(Entry(_cstring(h, base + no), _cstring(h, base + to), flags, off, size, size2))
    zipped = h[base + strlen + 4 + 24 * count] != 0
    return out, zipped, 8 + cut


def entries(path):
    with open(path, 'rb') as fh:
        return _header(fh)[0]


def names(path):
    return {e.name.lower(): e for e in entries(path) if not e.deleted}


def read(path, wanted):
    """{name: bytes} for the entry names given (matched without regard to case; keys as given)."""
    with open(path, 'rb') as fh:
        ents, zipped, start = _header(fh)
        by = {e.name.lower(): e for e in ents if not e.deleted}
        want = {}
        for n in wanted:
            if n.lower() not in by:
                raise KeyError(f'{n} is not an entry of {path}')
            want[n] = by[n.lower()]
        if not want:
            return {}
        end = max(e.offset + e.size for e in want.values())
        out = {n: bytearray() for n in want}
        fh.seek(start)
        d = zlib.decompressobj(16 + zlib.MAX_WBITS) if zipped else None
        pos = 0                                       # payload offset of the next byte to come
        while pos < end:                              # streamed: only the wanted entries' bytes are kept
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            data = d.decompress(chunk) if d else chunk
            for n, e in want.items():
                lo, hi = max(e.offset, pos), min(e.offset + e.size, pos + len(data))
                if lo < hi:
                    out[n] += data[lo - pos:hi - pos]
            pos += len(data)
        if any(len(out[n]) != e.size for n, e in want.items()):
            raise ValueError(f'{path}: the payload ends before entry data (damaged bundle)')
        return {n: bytes(b) for n, b in out.items()}


def build(items, zipped=True):
    """A bundle from [(name, type, data)] (flags 0x00010000, size2 = size) - for tests; FbrbTool packs real ones."""
    strings, offs = bytearray(), {}

    def s(t):
        if t not in offs:
            offs[t] = len(strings)
            strings.extend(t.encode('ascii') + b'\0')
        return offs[t]
    table, payload = bytearray(), bytearray()
    for name, typ, data in items:
        table += struct.pack('>6I', s(name), 0x00010000, len(payload), len(data), len(data), s(typ))
        payload += data
    h = struct.pack('>II', 2, len(strings)) + bytes(strings) + struct.pack('>I', len(items)) + bytes(table) + bytes([1 if zipped else 0]) + struct.pack('>I', len(payload))
    hz = gzip.compress(h)
    return b'FbRB' + struct.pack('>I', len(hz)) + hz + (gzip.compress(bytes(payload)) if zipped else bytes(payload))
