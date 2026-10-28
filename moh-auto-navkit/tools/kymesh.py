"""kymesh.py - structured model of the "KynogonRuntimeMesh" record inside pathdata.hpd sector blocks.

Part of BC2 NavKit; layout as documented in ..\\research\\KYNAPSE_SPEC.md section 4.4/4.5.
parse_mesh(bytes) -> Mesh, write_mesh(Mesh) -> bytes. All offsets in the record are relative to its
start; parse_mesh replaces them with references (object indices etc.) and write_mesh lays the record
out again and recomputes every offset.
"""
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

NULL = 0xFFFFFFFF
MARKER = b'KynogonRuntimeMesh\0\0'
Vec3 = Tuple[float, float, float]


@dataclass
class Portal:                 # B: portal to another area of the same sector
    p0: Vec3
    p1: Vec3
    target: int               # index of the target area (Mesh.areas)


@dataclass
class BorderPortal:           # C: portal on the sector border
    p0: Vec3
    p1: Vec3
    direction: Vec3           # unit vector out of the sector (or "up")
    runtime: Tuple[int, int] = (NULL, NULL)   # filled by the game at runtime; NULL in files


@dataclass
class Cell:                   # F: special pitch-sized cell (ledge / height break)
    direction: Vec3
    points: List[Vec3]


@dataclass
class Area:                   # one walkable area inside one tile
    sector_id: int
    tile_x: int
    tile_z: int
    layer: int
    radius: float
    pitch: float
    max_y: float
    min_y: float
    portals: List[Portal] = field(default_factory=list)
    border_portals: List[BorderPortal] = field(default_factory=list)
    d_lines: List[List[Vec3]] = field(default_factory=list)
    outline: List[List[Vec3]] = field(default_factory=list)      # E
    cells: List[Cell] = field(default_factory=list)
    end_row: Vec3 = (0.0, 0.0, 0.0)          # floats of the F table's terminating row
    null_offsets: dict = field(default_factory=dict)  # raw offset values stored with count 0


@dataclass
class TileItem:
    area: int                 # index into Mesh.areas
    a: float                  # stored per tile item (area min / max height in the samples)
    b: float


@dataclass
class LinkObject:
    header: Tuple[int, int, int]
    refs: List[Tuple[int, int]]   # (area index, byte offset inside that area object)


@dataclass
class NeighbourLink:
    sector_id: int
    objects: List[LinkObject]


@dataclass
class Mesh:
    version: int
    flags: int
    loaded: int
    sector_id: int
    resolution: float
    pitch: float
    rect: Tuple[float, float, float, float]   # minX, maxX, minZ, maxZ
    tiles_x: int
    tiles_z: int
    radius: float
    tiles: List[List[TileItem]]               # tiles_x * tiles_z
    areas: List[Area]
    links: List[NeighbourLink]
    end_slot: bytes                           # 4 bytes the loader never reads (uninitialised in DICE files)
    links_null_offset: int = NULL


def _u32(m, o): return struct.unpack_from('<I', m, o)[0]
def _f32(m, o): return struct.unpack_from('<f', m, o)[0]
def _v3(m, o): return struct.unpack_from('<3f', m, o)


def _points(m, a, b):
    return [_v3(m, q) for q in range(a, b, 12)]


def parse_mesh(m: bytes) -> Mesh:
    if not m.startswith(MARKER):
        raise ValueError('not a KynogonRuntimeMesh record')
    version, flags = struct.unpack_from('<HH', m, 0x14)
    loaded, sector_id = _u32(m, 0x18), _u32(m, 0x1C)
    resolution, pitch = _f32(m, 0x20), _f32(m, 0x24)
    rect = struct.unpack_from('<4f', m, 0x28)
    tx, tz = _u32(m, 0x38), _u32(m, 0x3C)
    radius = _f32(m, 0x40)
    tile_tab, n_links, links_off = _u32(m, 0x44), _u32(m, 0x48), _u32(m, 0x4C)
    ntiles = tx * tz
    tile_offs = [_u32(m, tile_tab + i * 4) for i in range(ntiles + 1)]

    area_index = {}           # object offset -> index
    area_offsets = []
    tiles_raw = []
    for t in range(ntiles):
        to = tile_offs[t]
        n = _u32(m, to)
        items = [struct.unpack_from('<Iff', m, to + 4 + k * 12) for k in range(n)]
        for ptr, _, _ in items:
            if ptr not in area_index:
                area_index[ptr] = len(area_offsets)
                area_offsets.append(ptr)
        tiles_raw.append(items)
    tiles = [[TileItem(area_index[p], a, b) for p, a, b in items] for items in tiles_raw]

    areas = []
    for o in area_offsets:
        hdr = struct.unpack_from('<4I4f', m, o)
        cnt = {k: _u32(m, o + k) for k in (0x20, 0x28, 0x30, 0x38, 0x40, 0x48)}
        offs = {k: _u32(m, o + k + 4) for k in cnt}
        null_offsets = {k: offs[k] for k in cnt if cnt[k] == 0}
        area = Area(hdr[0], hdr[1], hdr[2], hdr[3], hdr[4], hdr[5], hdr[6], hdr[7], null_offsets=null_offsets)
        if cnt[0x20]:
            raise ValueError('A records present (never seen before)')
        pos = o + offs[0x28] if cnt[0x28] else None
        for k in range(cnt[0x28]):
            r = pos + k * 0x20
            area.portals.append(Portal(_v3(m, r), _v3(m, r + 12), _u32(m, r + 0x18)))   # target resolved below
        pos = o + offs[0x30] if cnt[0x30] else None
        for k in range(cnt[0x30]):
            r = pos + k * 0x2C
            area.border_portals.append(BorderPortal(_v3(m, r), _v3(m, r + 12), _v3(m, r + 0x20),
                                                    (_u32(m, r + 0x18), _u32(m, r + 0x1C))))
        for key, target in ((0x38, area.d_lines), (0x40, area.outline)):
            if cnt[key]:
                tab = o + offs[key]
                idx = [_u32(m, tab + j * 4) for j in range(cnt[key] + 1)]
                for j in range(cnt[key]):
                    target.append(_points(m, o + idx[j], o + idx[j + 1]))
        if cnt[0x48]:
            tab = o + offs[0x48]
            rows = [struct.unpack_from('<I3f', m, tab + j * 16) for j in range(cnt[0x48] + 1)]
            for j in range(cnt[0x48]):
                area.cells.append(Cell(rows[j][1:], _points(m, o + rows[j][0], o + rows[j + 1][0])))
            area.end_row = rows[-1][1:]
        areas.append(area)
    for area in areas:
        for p in area.portals:
            p.target = area_index[p.target]

    links = []
    for k in range(n_links):
        sid, cnt, lst = struct.unpack_from('<3I', m, links_off + k * 12)
        objs = []
        for j in range(cnt):
            lo = _u32(m, lst + j * 4)
            h = struct.unpack_from('<3I', m, lo)
            c = _u32(m, lo + 0xC)
            refs = []
            for q in range(c):
                ptr = _u32(m, lo + 0x10 + q * 4)
                owner = max(a for a in area_offsets if a <= ptr)
                refs.append((area_index[owner], ptr - owner))
            objs.append(LinkObject(h, refs))
        links.append(NeighbourLink(sid, objs))
    end_slot = m[tile_offs[ntiles]:tile_offs[ntiles] + 4]
    return Mesh(version, flags, loaded, sector_id, resolution, pitch, rect, tx, tz, radius, tiles, areas, links,
                end_slot, links_off if n_links == 0 else NULL)


def write_mesh(mesh: Mesh) -> bytes:
    # Pass 1: serialise areas without B target fields to learn their sizes; pass 2: lay out and patch.
    bodies = [_area_body(a) for a in mesh.areas]
    out = bytearray(MARKER)
    out += struct.pack('<HHII', mesh.version, mesh.flags, mesh.loaded, mesh.sector_id)
    out += struct.pack('<2f4f2If', mesh.resolution, mesh.pitch, *mesh.rect, mesh.tiles_x, mesh.tiles_z, mesh.radius)
    out += b'\0' * 12                      # tile table offset, link count, link offset (patched below)
    ntiles = mesh.tiles_x * mesh.tiles_z
    tab = len(out)
    out += b'\0' * ((ntiles + 1) * 4)
    tile_offs = []
    area_off = {}
    for items in mesh.tiles:
        to = len(out)
        tile_offs.append(to)
        out += struct.pack('<I', len(items))
        item_pos = len(out)
        out += b'\0' * (len(items) * 12)
        if items:
            out += b'\0' * 4               # end offset, patched below
        for it in items:
            if it.area not in area_off:
                area_off[it.area] = len(out)
                out += bodies[it.area][0]
        for k, it in enumerate(items):
            struct.pack_into('<Iff', out, item_pos + k * 12, area_off[it.area], it.a, it.b)
        if items:
            struct.pack_into('<I', out, item_pos + len(items) * 12, len(out))
    end_slot_off = len(out)
    out += mesh.end_slot
    tile_offs.append(end_slot_off)
    for i, to in enumerate(tile_offs):
        struct.pack_into('<I', out, tab + i * 4, to)
    # Patch B portal targets (offset and size of the target area).
    sizes = [len(b[0]) for b in bodies]
    for i, (body, portal_pos) in enumerate(bodies):
        base = area_off[i]
        for k, p in enumerate(mesh.areas[i].portals):
            struct.pack_into('<II', out, base + portal_pos + k * 0x20 + 0x18, area_off[p.target], sizes[p.target])
    # Neighbour links.
    links_off = len(out) if mesh.links else mesh.links_null_offset
    if mesh.links:
        table = len(out)
        out += b'\0' * (len(mesh.links) * 12)
        for k, link in enumerate(mesh.links):
            lst = len(out)
            out += b'\0' * (len(link.objects) * 4)
            struct.pack_into('<3I', out, table + k * 12, link.sector_id, len(link.objects), lst)
            for j, lo in enumerate(link.objects):
                struct.pack_into('<I', out, lst + j * 4, len(out))
                out += struct.pack('<4I', *lo.header, len(lo.refs))
                for ai, rel in lo.refs:
                    out += struct.pack('<I', area_off[ai] + rel)
    struct.pack_into('<3I', out, 0x44, tab, len(mesh.links), links_off)
    out += struct.pack('<II', len(out), 0)   # trailer
    return bytes(out)


def _area_body(a: Area):
    """Bytes of one area object with B target fields zeroed; returns (bytes, offset of the B records)."""
    hdr_size = 0x50
    blob = bytearray()
    off = {}
    pos = hdr_size
    portal_pos = pos
    off[0x28] = pos if a.portals else a.null_offsets.get(0x28, NULL)
    for p in a.portals:
        blob += struct.pack('<6fII', *p.p0, *p.p1, 0, 0)
    pos = hdr_size + len(blob)
    off[0x30] = pos if a.border_portals else a.null_offsets.get(0x30, NULL)
    for c in a.border_portals:
        blob += struct.pack('<6f2I3f', *c.p0, *c.p1, *c.runtime, *c.direction)
    for key, lists in ((0x38, a.d_lines), (0x40, a.outline)):
        if not lists:
            off[key] = a.null_offsets.get(key, NULL)
            continue
        tab = hdr_size + len(blob)
        off[key] = tab
        data_start = tab + (len(lists) + 1) * 4
        idx, q = [], data_start
        for pts in lists:
            idx.append(q)
            q += len(pts) * 12
        idx.append(q)
        blob += struct.pack(f'<{len(idx)}I', *idx)
        for pts in lists:
            for pt in pts:
                blob += struct.pack('<3f', *pt)
    if a.cells:
        tab = hdr_size + len(blob)
        off[0x48] = tab
        q = tab + (len(a.cells) + 1) * 16
        for c in a.cells:
            blob += struct.pack('<I3f', q, *c.direction)
            q += len(c.points) * 12
        blob += struct.pack('<I3f', q, *a.end_row)
        for c in a.cells:
            for pt in c.points:
                blob += struct.pack('<3f', *pt)
    else:
        off[0x48] = a.null_offsets.get(0x48, NULL)
    off[0x20] = a.null_offsets.get(0x20, NULL)
    header = struct.pack('<4I4f', a.sector_id, a.tile_x, a.tile_z, a.layer, a.radius, a.pitch, a.max_y, a.min_y)
    counts = {0x20: 0, 0x28: len(a.portals), 0x30: len(a.border_portals), 0x38: len(a.d_lines),
              0x40: len(a.outline), 0x48: len(a.cells)}
    for k in (0x20, 0x28, 0x30, 0x38, 0x40, 0x48):
        header += struct.pack('<II', counts[k], off[k])
    return bytes(header + blob), portal_pos
