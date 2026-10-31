"""hpdtool.py - reader/writer for MOH Kynapse navigation data (pathdata.hpd).

Part of MOH NavKit (see ..\\README.md; written from ..\\research\\KYNAPSE_SPEC.md).

Usage:
    python hpdtool.py info <pathdata.hpd>                 file summary
    python hpdtool.py sector <pathdata.hpd> <index>       one sector: nodes, edges, mesh header
    python hpdtool.py roundtrip <file-or-folder>...       parse + write back, compare byte for byte
    python hpdtool.py coverage <file-or-folder>...        check every byte of every runtime mesh is explained
    python hpdtool.py meshtrip <file-or-folder>...        structured mesh model (kymesh.py) round trip

Sector.mesh holds the raw runtime-mesh record; kymesh.parse_mesh / write_mesh convert it to and
from the structured model (areas, portals, outlines, cells, neighbour links).
"""
import os
import struct
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAGIC = b'Kynogon HPD\0'
HEADER_WORDS = 36           # u32 words after the magic (0x0C..0x9B)
HEADER_SIZE = 12 + HEADER_WORDS * 4
TABLE_ENTRY = struct.Struct('<I6f5I')  # 48 bytes
NODE = struct.Struct('<I3fIf3I')       # 36 bytes
EDGE = struct.Struct('<3I')            # 12 bytes
MESH_MARKER = b'KynogonRuntimeMesh'


@dataclass
class Node:
    id: int                  # node id; high bit = border node shared with a neighbouring sector
                             # (same id and position appear in that sector too)
    pos: tuple[float, float, float]  # world position, Y up
    flags: int               # always 0x80000000
    value: float             # DistEdgeMax on the first node of a sector (infantry 15, vehicle 50), else 0
    a: int                   # index of this node's first outgoing edge + 1 (edges are sorted by source)
    b: int                   # low u16: entry of the node list sorted by x; high u16: sorted by y
    c: int                   # low u16: entry of the node list sorted by z; bit 16: border node


@dataclass
class Edge:
    tag: int                 # cost: 0x80000000 | floor(3-D distance between the nodes * 16)
    src: int
    dst: int


@dataclass
class Sector:
    id: int                  # grid coordinates, 0xXXYY
    bounds: tuple[float, ...]  # minX, maxX, minY, maxY, minZ, maxZ
    reserved: int            # always 0 so far
    date: int                # build date YYMMDD, zero-based month (100007 = 2010-01-07)
    time: int                # build time HHMMSScc
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    mesh: bytes = b''        # runtime mesh record (starts with "KynogonRuntimeMesh"), raw for now


@dataclass
class HpdFile:
    header: tuple[int, ...]  # the 36 header words (word 2 = sector count, rewritten on save)
    sectors: list[Sector]


def parse(data: bytes) -> HpdFile:
    if data[:12] != MAGIC:
        raise ValueError('not a Kynogon HPD file')
    header = struct.unpack_from(f'<{HEADER_WORDS}I', data, 12)
    count = header[2]
    blocks = HEADER_SIZE + count * TABLE_ENTRY.size
    sectors: list[Sector] = []
    for i in range(count):
        e = TABLE_ENTRY.unpack_from(data, HEADER_SIZE + i * TABLE_ENTRY.size)
        sid, bounds, reserved, size, offset, date, time = e[0], e[1:7], e[7], e[8], e[9], e[10], e[11]
        start = blocks + offset
        block = data[start:start + size]
        n_nodes, n_edges = struct.unpack_from('<2I', block, 0)
        pos = 8
        nodes: list[Node] = []
        for _ in range(n_nodes):
            v = NODE.unpack_from(block, pos)
            nodes.append(Node(v[0], (v[1], v[2], v[3]), v[4], v[5], v[6], v[7], v[8]))
            pos += NODE.size
        edges: list[Edge] = []
        for _ in range(n_edges):
            edges.append(Edge(*EDGE.unpack_from(block, pos)))
            pos += EDGE.size
        mesh = block[pos:]
        if not mesh.startswith(MESH_MARKER):
            raise ValueError(f'sector {i} ({sid:#x}): runtime mesh not where expected')
        sectors.append(Sector(sid, bounds, reserved, date, time, nodes, edges, mesh))
    if blocks + sum(len(_block(s)) for s in sectors) != len(data):
        raise ValueError('sector blocks do not cover the file')
    return HpdFile(header, sectors)


MESH_OBJECT_HEADER = 0x50


def mesh_layout(m: bytes) -> tuple[list[tuple[int, int, str]], list[tuple[int, list[tuple], int | None]],
                                   dict[int, int], list[tuple[int, int, int]], list[int]]:
    """Walk a runtime mesh record (offsets relative to its start) and return a list of
    (start, end, label) regions plus the parsed structure. Raises ValueError on inconsistencies."""
    u32 = lambda o: struct.unpack_from('<I', m, o)[0]
    regions = [(0, 0x50, 'header')]
    nx, nz = u32(0x38), u32(0x3C)
    tiles_off, links_n, links_off = u32(0x44), u32(0x48), u32(0x4C)
    if tiles_off != 0x50:
        raise ValueError(f'tile table at {tiles_off:#x}')
    tile_count = nx * nz
    regions.append((0x50, 0x50 + (tile_count + 1) * 4, 'tile table'))
    tile_offs = [u32(0x50 + i * 4) for i in range(tile_count + 1)]
    objects: dict[int, int] = {}
    tiles: list[tuple[int, list[tuple], int | None]] = []
    for t in range(tile_count):
        to = tile_offs[t]
        n = u32(to)
        items = [struct.unpack_from('<Iff', m, to + 4 + k * 12) for k in range(n)]
        # An empty tile is just its count; otherwise items are followed by an end offset.
        end_ptr = u32(to + 4 + n * 12) if n else None
        regions.append((to, to + (8 + n * 12 if n else 4), f'tile {t}'))
        for ptr, _, _ in items:
            if ptr not in objects:
                objects[ptr] = _object_end(m, ptr)
                regions.append((ptr, objects[ptr], f'object @{ptr:#x}'))
        tiles.append((to, items, end_ptr))
    # The extra (last) tile-table entry points at a 4-byte slot the loader never reads; DICE's tool
    # left uninitialised memory there (preserved for exact round trips, zero when generating).
    regions.append((tile_offs[tile_count], tile_offs[tile_count] + 4, 'tile table end slot'))
    # Trailer: the last 8 bytes hold their own offset and 0.
    if u32(len(m) - 8) != len(m) - 8 or u32(len(m) - 4) != 0:
        raise ValueError('mesh trailer not found')
    regions.append((len(m) - 8, len(m), 'trailer'))
    links: list[tuple[int, int, int]] = []
    if links_n:
        regions.append((links_off, links_off + links_n * 12, 'link table'))
        for k in range(links_n):
            sid, cnt, lst = struct.unpack_from('<3I', m, links_off + k * 12)
            regions.append((lst, lst + cnt * 4, f'link list {sid:#x}'))
            for j in range(cnt):
                lo = u32(lst + j * 4)
                c = u32(lo + 0xC)
                regions.append((lo, lo + 0x10 + c * 4, f'link {sid:#x}/{j}'))
            links.append((sid, cnt, lst))
    return regions, tiles, objects, links, tile_offs


def _object_end(m: bytes, o: int) -> int:
    u32 = lambda x: struct.unpack_from('<I', m, x)[0]
    a, b, c = u32(o + 0x20), u32(o + 0x28), u32(o + 0x30)
    end = o + MESH_OBJECT_HEADER + a * 0x20 + b * 0x20 + c * 0x2C
    for cnt_off, tab_off, stride in ((0x38, 0x3C, 4), (0x40, 0x44, 4), (0x48, 0x4C, 16)):
        cnt = u32(o + cnt_off)
        if not cnt:
            continue
        tab = o + u32(o + tab_off)
        if tab != end:
            raise ValueError(f'object {o:#x}: table {cnt_off:#x} at {tab:#x}, expected {end:#x}')
        first = u32(tab)
        last = u32(tab + cnt * stride)
        end = tab + (cnt + 1) * stride + ((last - first) & ~3)
    return end


def mesh_coverage(m: bytes) -> tuple[list[tuple[int, int]], list[tuple[int, int, str]]]:
    regions, *_ = mesh_layout(m)
    regions.sort()
    gaps: list[tuple[int, int]] = []
    overlaps: list[tuple[int, int, str]] = []
    pos = 0
    for s, e, label in regions:
        if s > pos:
            gaps.append((pos, s))
        elif s < pos:
            overlaps.append((s, pos, label))
        pos = max(pos, e)
    if pos < len(m):
        gaps.append((pos, len(m)))
    return gaps, overlaps


def _block(s: Sector) -> bytes:
    out = bytearray(struct.pack('<2I', len(s.nodes), len(s.edges)))
    for n in s.nodes:
        out += NODE.pack(n.id, *n.pos, n.flags, n.value, n.a, n.b, n.c)
    for e in s.edges:
        out += EDGE.pack(e.tag, e.src, e.dst)
    out += s.mesh
    return bytes(out)


def write(f: HpdFile) -> bytes:
    header = list(f.header)
    header[2] = len(f.sectors)
    out = bytearray(MAGIC + struct.pack(f'<{HEADER_WORDS}I', *header))
    blocks = [_block(s) for s in f.sectors]
    offset = 0
    for s, b in zip(f.sectors, blocks):
        out += TABLE_ENTRY.pack(s.id, *s.bounds, s.reserved, len(b), offset, s.date, s.time)
        offset += len(b)
    for b in blocks:
        out += b
    return bytes(out)


def _files(args: Iterable[str]) -> Iterator[str]:
    for a in args:
        if os.path.isdir(a):
            for dp, _, fs in os.walk(a):
                for fn in fs:
                    if fn.lower() == 'pathdata.hpd':
                        yield os.path.join(dp, fn)
        else:
            yield a


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == 'info':
        f = parse(open(argv[2], 'rb').read())
        nodes = sum(len(s.nodes) for s in f.sectors)
        edges = sum(len(s.edges) for s in f.sectors)
        mesh = sum(len(s.mesh) for s in f.sectors)
        dates = sorted({s.date for s in f.sectors})
        print(f'{len(f.sectors)} sectors, {nodes} graph nodes, {edges} edges, {mesh} bytes of runtime mesh; '
              f'build dates {dates[0]}..{dates[-1]}')
        return 0
    if cmd == 'sector':
        f = parse(open(argv[2], 'rb').read())
        s = f.sectors[int(argv[3])]
        print(f'sector {s.id:#06x} bounds {[round(b, 2) for b in s.bounds]} built {s.date} {s.time:08d}')
        for n in s.nodes:
            print(f'  node {n.id:#010x} pos ({n.pos[0]:.2f}, {n.pos[1]:.2f}, {n.pos[2]:.2f}) flags {n.flags:#x} '
                  f'value {n.value:g} a {n.a} b {n.b & 0xFFFF},{n.b >> 16} c {n.c}')
        for e in s.edges:
            print(f'  edge {e.tag:#010x} {e.src} -> {e.dst}')
        print(f'  mesh {len(s.mesh)} bytes: {s.mesh[:48].hex(" ")}')
        return 0
    if cmd == 'coverage':
        total = clean = 0
        gap_kinds: dict[tuple, int] = {}
        for p in _files(argv[2:]):
            f = parse(open(p, 'rb').read())
            for s in f.sectors:
                total += 1
                try:
                    gaps, overlaps = mesh_coverage(s.mesh)
                except (ValueError, struct.error) as ex:
                    print(f'ERROR {p} sector {s.id:#x}: {ex}')
                    continue
                if not gaps and not overlaps:
                    clean += 1
                else:
                    key = (tuple((e - b) for b, e in gaps[:3]), len(overlaps))
                    gap_kinds[key] = gap_kinds.get(key, 0) + 1
                    if gap_kinds[key] <= 2:
                        desc = [f'{b:#x}:{s.mesh[b:e].hex(" ")}' for b, e in gaps[:4]]
                        print(f'{os.path.basename(os.path.dirname(os.path.dirname(p)))}/{os.path.basename(os.path.dirname(p))} '
                              f'sector {s.id:#x} len {len(s.mesh):#x} last-tile-entry '
                              f'{struct.unpack_from("<I", s.mesh, 0x50 + struct.unpack_from("<I", s.mesh, 0x38)[0] * struct.unpack_from("<I", s.mesh, 0x3C)[0] * 4)[0]:#x}: '
                              f'gaps {desc} overlaps {overlaps[:2]}')
        print(f'coverage: {clean}/{total} meshes fully explained; gap patterns {len(gap_kinds)}')
        return 0
    if cmd == 'meshtrip':
        import kymesh
        total = same = 0
        reasons: dict[str, int] = {}
        for p in _files(argv[2:]):
            for s in parse(open(p, 'rb').read()).sectors:
                total += 1
                try:
                    out = kymesh.write_mesh(kymesh.parse_mesh(s.mesh))
                except Exception as ex:     # noqa: BLE001 - a survey: every failure is tallied by its type
                    reasons[type(ex).__name__ + ': ' + str(ex)[:60]] = reasons.get(type(ex).__name__ + ': ' + str(ex)[:60], 0) + 1
                    continue
                if out == s.mesh:
                    same += 1
                else:
                    n = min(len(out), len(s.mesh))
                    first = next((i for i in range(n) if out[i] != s.mesh[i]), n)
                    key = f'len {len(out) - len(s.mesh):+d}, first diff at word {first // 4 * 4:#x}'
                    if not reasons:
                        print(f'first mismatch: {p} sector {s.id:#x}: {key}; '
                              f'orig {s.mesh[first // 4 * 4:first // 4 * 4 + 16].hex(" ")} new {out[first // 4 * 4:first // 4 * 4 + 16].hex(" ")}')
                    reasons[key] = reasons.get(key, 0) + 1
        print(f'mesh round trip: {same}/{total} identical')
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:8]:
            print(f'  {v:6} x {k}')
        return 0 if same == total else 2
    if cmd == 'roundtrip':
        ok = bad = 0
        for p in _files(argv[2:]):
            data = open(p, 'rb').read()
            try:
                same = write(parse(data)) == data
            except ValueError as ex:
                same = False
                print(f'ERROR {p}: {ex}')
            ok += same
            bad += not same
            if not same:
                print(f'MISMATCH {p}')
        print(f'round trip: {ok} identical, {bad} different')
        return 0 if bad == 0 else 2
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('hpdtool', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
