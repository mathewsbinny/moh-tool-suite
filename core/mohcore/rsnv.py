"""RSNV: our own per-level navigation file (operation-rising-sun research/NAVIGATION_RUNTIME_DESIGN.md, section 1.3),
the input of the navigation runtime (`rsnav`; MOH's copy: project-opfor-moh src/rsnav). Written by NavKit's exporter (navexport1943.py)
from a checked build; read here for tests, tools and the Python reference search.

Medal of Honor (2026-10-30): a copy of bf1943-tool-suite's core/bfbc2core/rsnv.py, unchanged below this paragraph except
for the MOH changes listed here - the format is shared with rsnav, and MOH keeps its own copies of both (this module and
project-opfor-moh's src/rsnav), so a fix goes into these two and BF1943's are left as they are. MOH's exporter is
moh-auto-navkit/tools/navexport.py; its files are little-endian (PC) and hold no vehicle entries (OFFM).
MOH changes:
- 2026-10-31: validate() refuses an edge kind past the known ones (EDGE_KINDS, 7), as MOH's rsn_load does: kinds 7-255
  loaded silently, and rsnav closed kinds over 31 while this module searched them (kinds 7-31 raised IndexError in
  edge_cost's kind_extra).

    w = rsnv.load(path_or_bytes)       # either byte order
    w.node_pos, w.edge_target, w.edge_kind, w.edge_cost, w.areas, ...
    w.locate(x, y, z)                  # (area, (i, j), distance) or None
    w.validate()                       # [] when consistent

Layout v1. All sections 16-byte aligned, fixed strides, indices and offsets only (no pointers), so a loader can swap
in place by the stride schema (SCHEMA) and then read native types.

  header (64 B): 'RSNV', u32 0x01020304 in the writer's order, u16 version, u16 flags (bit 0 big-endian), u32 file
    size, u32 section count, u32 level id (FNV-1a of the level name), f32 sea level, f32 agent radius, f32 height,
    f32 step, f32 max water depth, u32 first 4 bytes of the source .hpd's SHA-256, u32 at 48 the search heuristic's
    scale in 1/2^24 (H_SCALE_AT; 0 in files before 2026-10-26: 0.999), 12 reserved bytes
  directory: n x {u32 tag, u32 offset, u32 count, u32 stride} from 0x40
  SECT 32  u16 id 0xXXYY, u8 layer, u8 pitch code (0: 0.2 m, 1: 0.8 m), f32 minX, minZ, maxX, maxZ, u32 first GBKT
           bucket (25 x 25 buckets of 4 m from (minX, minZ), row-major in z), u16 first area, u16 area count, u32 0
  NODE 16  f32 x, y (ground), z, u32 first edge; nodes + 1 (the sentinel's first edge is the edge count)
  NAUX 8   u16 area (0xFFFF none), u16 sector, u16 island, u16 flags (NAUX_*)
  EDGE 8   u32 target (bits 0-23) | kind (bits 24-31, EDGE_*), u16 cost (1/16 m), u16 flags (EDGEF_*)
  AREA 64  f32 x0, z0 (first bitmap cell centre), x1, z1 (last), minY, maxY; u32 BMAP byte offset; u16 bitmap width,
           height; u32 first PORT, u16 portal count, u16 sector; u16 island, u16 flags; u32 first node (in AREA order,
           ANOD), u16 node count, u16 pitch in mm; u32 HGHT offset (entries), u16 height grid width, height; u32
           height grid step in mm (0: 4 m, files before 2026-10-25)
  PORT 24  f32 x0, z0, x1, z1, y, u16 from area, u16 to area (both directions stored)
  BMAP 1   per area, rows of ceil(width / 8) bytes, bit (i & 7) of byte i >> 3; 1 = walkable cell of the area
  HGHT 2   per area, u16 cm of ground height above the area's minY: the mean of the cells in each block of the
           height grid (blocks of a whole number of cells from the first cell); 0xFFFF a block with no cell
  GBKT 8   u32 first GLST entry, u32 count
  GLST 2   u16 area ids, each bucket's sorted by minY
  ANOD 4   u32 node ids, grouped by area
  LADR 40  f32 foot x, y, z, top x, y, z, yaw, u32 foot node, u32 top node, u32 aux = kind (bits 24-31: 0 ladder,
           1 jump) | source index
  OFFM 32  u8 kind (OFFM_*), u8 team mask, u16 vehicle class, u32 node, f32 x, y, z, yaw, radius, u32 aux
  ISLD 16  u32 nodes, u32 representative node, u16 flags (ISLD_*), u16 transfer count, u32 first transfer
  META 1   UTF-8 JSON: exporter version, sources and their hashes, NavKit settings
  OBJT 24  destructible objects: u32 key (the level DBX Enumeration), u32 blueprint hash, f32 x, y, z, u32 flags
  CEDG 16  conditional edges (EDGE flag EDGEF_COND), sorted by edge: u32 edge, u32 first OREF, u32 count, u32 0;
           open only when every object listed is destroyed
  OREF 4   u32 OBJT indices
Deviations from the design's table: AREA is 64 B (its 48 B does not hold the listed fields), GRID is GBKT + GLST,
ANOD lists nodes by area, LADR carries jump links too (kind), COND is CEDG + OREF. The exporter writes no OBJT /
CEDG / OREF yet: they need NavKit's blocker attribution (design section 3.1).
"""
import json
import math
import struct
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

MAGIC = b'RSNV'
VERSION = 1
HEADER = 64
H_SCALE_AT = 48         # header: u32 the search heuristic's scale in 1/2^24 (2026-10-26; 0 = files before: 0.999)
H_SCALE_ONE = 1 << 24
H_SCALE_LEGACY = 0.999
EDGE_WALK, EDGE_LADDER_UP, EDGE_LADDER_DOWN, EDGE_DROP, EDGE_JUMP, EDGE_BOARD, EDGE_ALIGHT = range(7)
EDGE_KINDS = 7         # rsnav's RSN_EDGE_KINDS: a file with an edge of another kind is refused (validate)
EDGE_NAMES = ['walk', 'ladder up', 'ladder down', 'drop', 'jump', 'board', 'alight']
NAUX_SHALLOW, NAUX_WADE, NAUX_BORDER, NAUX_LADDER_END, NAUX_JUMP_END = 1, 2, 4, 8, 16
EDGEF_COND = 1
EDGEF_TIGHT = 2        # a walk edge with no line of sight either way (exporter 1.2): the cells leave no room
OFFM_BOARD_VEHICLE, OFFM_MOUNT_STATIC = 1, 5
ISLD_MAIN, ISLD_CARRIER, ISLD_LADDER_GROUND = 1, 2, 4
PITCHES = {0: 0.2, 1: 0.8}
# line of sight (rsnav's rsnav_los.c): the soldier's step, the ground change allowed, coarse zones' allowances
LOS_STEP = 0.37
LOS_DY = 0.6
LOS_COARSE_DY = LOS_STEP + 0.8 * math.sqrt(2.0)   # the step plus a diagonal 0.8 m cell at 45 degrees (2026-10-26; was
                                                   # one straight cell, 1.17 m: NavKit's step rule allows the diagonal)
COARSE_GAP = 1.0
LOS_GRACE = 0.25
LOS_SEAM = 2.5          # m: line of sight looks for a sector's areas this far past its rectangle (2026-10-26): the
                        # build's sample window overlaps the neighbour sector (up to 2.4 m where MIXED_OVERLAP grows it
                        # at a mixed seam), and a line crossing a seam must find them
LOS_NEAR = 128          # areas at most per lookup (rsnav's MAX_NEAR)
LOS_FOLLOWED = True     # a change of area is tested against the ground the line has followed (the last sample's), not
                        # against the current area's height grid at the new sample (2026-10-29): past its own cells that
                        # grid is an extrapolation, and where an area holds two floors a cell or two apart it reads the
                        # other floor (Iwo Jima area 1189: 14.12 under a line on 14.93, the kept 0.2 m surface 14.86)
LOS_START_NEAREST = True  # line of sight starts on the area under the start whose ground is nearest its height
                        # (_los_start; 2026-10-29). Before, locate's nearest cell, the lowest area index on a tie: on
                        # stairs with areas stacked 0.2-0.9 m apart in one column it started a floor below the node
                        # and failed at the end (round 4: 11 of the 31 tight pairs on the four maps)
LOS_GRAZE = 0.25        # of a cell (2026-10-26): a sample this close to a cell's side leans on that side. In a 0.2 m
                        # area a sample on a blocked cell passes when it is within this of a walkable neighbour's square
                        # (the build's own lines step diagonally between cells: navbuild.line_walkable); in a 0.8 m
                        # area a sample needs the neighbour only on the sides it leans to
COARSE_MM = 400
BUCKET = 4.0
BUCKETS = 25

# per section: struct format of one record (without byte order); BMAP / META are bytes
SCHEMA = {
    'SECT': 'HBB4fIHHI',
    'NODE': '3fI',
    'NAUX': '4H',
    'EDGE': 'IHH',
    'AREA': '6fIHHIHHHHIHHIHHI',
    'PORT': '5fHH',
    'HGHT': 'H',
    'GBKT': 'II',
    'GLST': 'H',
    'ANOD': 'I',
    'LADR': '7f3I',
    'OFFM': 'BBHI5fI',
    'ISLD': 'IIHHI',
    'OBJT': 'II3fI',
    'CEDG': 'IIII',
    'OREF': 'I',
}
for _t, _f in SCHEMA.items():
    assert struct.calcsize('<' + _f) == {'SECT': 32, 'NODE': 16, 'NAUX': 8, 'EDGE': 8, 'AREA': 64, 'PORT': 24, 'HGHT': 2,
                                         'GBKT': 8, 'GLST': 2, 'ANOD': 4, 'LADR': 40, 'OFFM': 32, 'ISLD': 16, 'OBJT': 24,
                                         'CEDG': 16, 'OREF': 4}[_t], _t


def heuristic_scale(node_pos, src, dst, cost):
    """The heuristic's scale for a graph (u32, 1/2^24 units, for the header): the least cost / (16 x length) over the
    edges of nonzero length (lengths between the float32 node positions, as the search measures them), at most
    H_SCALE_LEGACY, rounded down; 1 (about 0) when such an edge costs 0. Every path then costs at least 16 x scale x its
    length, and so at least 16 x scale x the straight distance: the heuristic is admissible."""
    p = np.asarray(node_pos, np.float32).astype(np.float64)
    d = p[np.asarray(dst)] - p[np.asarray(src)]
    ln = np.sqrt((d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1]) + d[:, 2] * d[:, 2])
    c = np.asarray(cost, np.float64)
    m = ln > 0
    if not m.any():
        return int(H_SCALE_LEGACY * H_SCALE_ONE)
    if (c[m] == 0).any():
        return 1
    s = min(float((c[m] / (16.0 * ln[m])).min()), H_SCALE_LEGACY)
    return max(int(math.floor(s * H_SCALE_ONE)) - 1, 1)          # one unit down: safe against the division's rounding


def fnv1a(text):
    h = 0x811C9DC5
    for b in text.encode('utf-8'):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def write(path, sections: Dict[str, object], header: dict, big_endian=False):
    """Writes the file. sections: tag -> list of tuples (by SCHEMA) or bytes (BMAP, META). header: level, sea_level,
    radius, height, step, water_depth, hpd_sha256 (hex)."""
    e = '>' if big_endian else '<'
    order = [t for t in ('SECT', 'NODE', 'NAUX', 'EDGE', 'AREA', 'PORT', 'BMAP', 'HGHT', 'GBKT', 'GLST', 'ANOD', 'LADR',
                         'OFFM', 'ISLD', 'OBJT', 'CEDG', 'OREF', 'META') if t in sections]
    blobs = []
    for t in order:
        v = sections[t]
        if isinstance(v, (bytes, bytearray)):
            blobs.append((t, bytes(v), len(v), 1))
        else:
            st = struct.Struct(e + SCHEMA[t])
            blobs.append((t, b''.join(st.pack(*r) for r in v), len(v), st.size))
    off = HEADER + 16 * len(order)
    off = (off + 15) & ~15
    directory, body = [], bytearray()
    for t, b, count, stride in blobs:
        directory.append((t, off + len(body), count, stride))
        body += b
        body += bytes((-len(body)) % 16)
    size = off + len(body)
    out = bytearray()
    out += MAGIC + struct.pack(e + 'IHHIII', 0x01020304, VERSION, 1 if big_endian else 0, size, len(order),
                               fnv1a(header['level']))
    out += struct.pack(e + '5f', header['sea_level'], header['radius'], header['height'], header['step'],
                       header['water_depth'])
    out += bytes.fromhex(header['hpd_sha256'][:8]) + struct.pack(e + 'I', int(header.get('h_scale_q', 0))) + bytes(12)
    assert len(out) == HEADER
    for t, o, count, stride in directory:
        out += t.encode('ascii') + struct.pack(e + 'III', o, count, stride)
    out += bytes(off - len(out))
    out += body
    open(path, 'wb').write(bytes(out))
    return size


@dataclass
class Area:
    index: int
    x0: float
    z0: float
    x1: float
    z1: float
    min_y: float
    max_y: float
    bmap: int
    width: int
    height: int
    first_portal: int
    portals: int
    sector: int
    island: int
    flags: int
    first_node: int
    nodes: int
    pitch: float
    hght: int
    hw: int
    hh: int
    hstep_mm: int = 0

    @property
    def pitch_mm_(self):
        return int(round(self.pitch * 1000))

    def cell(self, x, z):
        """(i, j) of the bitmap cell at (x, z), or None outside the bitmap."""
        i = int(round((x - self.x0) / self.pitch))
        j = int(round((z - self.z0) / self.pitch))
        return (i, j) if 0 <= i < self.width and 0 <= j < self.height else None


@dataclass
class World:
    level_id: int
    big_endian: bool
    sea_level: float
    radius: float
    agent_height: float
    step: float
    water_depth: float
    sections: Dict[str, tuple] = field(default_factory=dict)       # tag -> (offset, count, stride)
    h_scale_q: int = 0              # the heuristic's scale in 1/2^24 (header); 0: H_SCALE_LEGACY

    @property
    def h_scale(self):
        """The search heuristic's scale: h = floor(16 x distance x h_scale). The exporter writes the least cost per
        1/16 m of length over the file's edges (heuristic_scale), so h never exceeds the cost of any path: admissible
        (with 0.999, many short edges each rounded down could sum below h, and a costlier path came back)."""
        return self.h_scale_q / H_SCALE_ONE if self.h_scale_q else H_SCALE_LEGACY
    raw: bytes = b''
    sectors: list = field(default_factory=list)
    areas: List[Area] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    # -- bitmaps and heights
    def walkable(self, a: Area, i, j):
        if not (0 <= i < a.width and 0 <= j < a.height):
            return False
        row = (a.width + 7) >> 3
        return bool(self.bmap[a.bmap + j * row + (i >> 3)] >> (i & 7) & 1)

    def ground(self, a: Area, x, z):
        """The area's ground height at (x, z) from its height grid: bilinear between the four block centres around
        the point; up to half a block past the outermost centres the slope continues (linear extrapolation, so an
        area's edge on a slope is not flattened). Where one of the four has no cells, the others' weights (within
        the grid) are renormalised; the nearest filled block of the 3 x 3 around it only when none carries weight.
        rsnav's rsn_area_ground computes the same, in the same order of operations."""
        if a.hw == 0 or a.hh == 0:
            return a.min_y
        pmm = int(round(a.pitch * 1000))
        hmm = a.hstep_mm or 4000
        cpb = (hmm + pmm // 2) // pmm                   # cells per block
        u = (x - a.x0) / a.pitch
        v = (z - a.z0) / a.pitch
        gu = min(max((u - (cpb - 1) / 2.0) / cpb, -0.5), a.hw - 0.5)
        gv = min(max((v - (cpb - 1) / 2.0) / cpb, -0.5), a.hh - 0.5)
        i0 = min(max(math.floor(gu), 0), max(a.hw - 2, 0))
        j0 = min(max(math.floor(gv), 0), max(a.hh - 2, 0))
        i1, j1 = min(i0 + 1, a.hw - 1), min(j0 + 1, a.hh - 1)
        fu = gu - i0 if i1 > i0 else 0.0
        fv = gv - j0 if j1 > j0 else 0.0
        h = [int(self.hght[a.hght + jj * a.hw + ii]) for jj, ii in ((j0, i0), (j0, i1), (j1, i0), (j1, i1))]
        if 0xFFFF not in h:
            top = (1.0 - fu) * h[0] + fu * h[1]
            bottom = (1.0 - fu) * h[2] + fu * h[3]
            return a.min_y + ((1.0 - fv) * top + fv * bottom) / 100.0
        fu = min(max(fu, 0.0), 1.0)
        fv = min(max(fv, 0.0), 1.0)
        wts = ((1.0 - fu) * (1.0 - fv), fu * (1.0 - fv), (1.0 - fu) * fv, fu * fv)
        s = ws = 0.0
        for wt, hv in zip(wts, h):
            if hv != 0xFFFF:
                s += wt * hv
                ws += wt
        if ws > 1e-6:
            return a.min_y + (s / ws) / 100.0
        gi = min(max(math.floor(u / cpb), 0), a.hw - 1)
        gj = min(max(math.floor(v / cpb), 0), a.hh - 1)
        best = None
        for dj in range(-1, 2):
            for di in range(-1, 2):
                ii, jj = gi + di, gj + dj
                if 0 <= ii < a.hw and 0 <= jj < a.hh:
                    v = int(self.hght[a.hght + jj * a.hw + ii])
                    if v != 0xFFFF and (best is None or abs(di) + abs(dj) < best[0]):
                        best = (abs(di) + abs(dj), a.min_y + v / 100.0)
        return best[1] if best else a.min_y

    def areas_near(self, x, z):
        """Area ids of the 4 m bucket holding (x, z) in every sector that contains it, each bucket's lowest first."""
        out = []
        for s in self.sectors:
            _id, _layer, _pc, x0, z0, x1, z1, first_bucket, _fa, _na, _r = s
            if x0 <= x < x1 and z0 <= z < z1:
                bi = min(math.floor((x - x0) / BUCKET), BUCKETS - 1)
                bj = min(math.floor((z - z0) / BUCKET), BUCKETS - 1)
                first, count = self.gbkt[first_bucket + bj * BUCKETS + bi]
                out += [int(a) for a in self.glst[first:first + count]]
        return out

    def locate(self, x, y, z, max_dist=3.0, dy=None):
        """(area index, (i, j), horizontal distance) of the walkable cell nearest (x, z) whose area's ground is within
        dy of y (default: the step plus half a metre), searching outward up to max_dist; None when there is none.
        Areas in index order, cells by rows then columns, the first nearest kept; all in double precision with a fixed
        order of operations, so the C runtime (rsnav) gives the same answer."""
        dy = self.step + 0.5 if dy is None else dy
        best = None
        cand = set()
        r = math.ceil(max_dist / BUCKET)
        for bj in range(-r, r + 1):
            for bi in range(-r, r + 1):
                cand.update(self.areas_near(x + bi * BUCKET, z + bj * BUCKET))
        for k in sorted(cand):
            a = self.areas[k]
            if not (a.min_y - dy <= y <= a.max_y + dy):
                continue
            if abs(self.ground(a, x, z) - y) > dy:
                continue
            n = math.ceil(max_dist / a.pitch)
            ci, cj = math.floor((x - a.x0) / a.pitch + 0.5), math.floor((z - a.z0) / a.pitch + 0.5)
            for j in range(max(cj - n, 0), min(cj + n, a.height - 1) + 1):
                for i in range(max(ci - n, 0), min(ci + n, a.width - 1) + 1):
                    if self.walkable(a, i, j):
                        dx, dz = a.x0 + i * a.pitch - x, a.z0 + j * a.pitch - z
                        d = math.sqrt(dx * dx + dz * dz)
                        if d <= max_dist and (best is None or d < best[2]):
                            best = (k, (i, j), d)
        return best

    def nearest_node(self, k, x, z):
        """The node of area k nearest (x, z) horizontally (the first in ANOD order on a tie), or None."""
        a, best = self.areas[k], None
        for t in range(a.first_node, a.first_node + a.nodes):
            nd = int(self.anod[t])
            dx, dz = self.node_pos[nd][0] - x, self.node_pos[nd][2] - z
            d = dx * dx + dz * dz
            if best is None or d < best[1]:
                best = (nd, d)
        return best[0] if best else None

    # -- line of sight (rsnav's rsn_los, the same rule and order of operations)
    def _cell_of(self, a, x, z):
        i, j = math.floor((x - a.x0) / a.pitch + 0.5), math.floor((z - a.z0) / a.pitch + 0.5)
        return i, j, 0 <= i < a.width and 0 <= j < a.height

    @staticmethod
    def _lean(f):
        """-1, 0 or 1: the side a sample at fraction f (-0.5 .. 0.5) from its cell's centre leans to (LOS_GRAZE)."""
        return 0 if abs(f) < 0.5 - LOS_GRAZE else (1 if f > 0 else -1)

    def on_cell(self, a, x, z):
        """Whether (x, z) lies on area a's walkable ground: its nearest cell is walkable or it lies within LOS_GRAZE
        of the square of a walkable cell beside it (rsnav's raw_ok; in 0.8 m areas too from 2026-10-26)."""
        u, v = (x - a.x0) / a.pitch, (z - a.z0) / a.pitch
        i, j = math.floor(u + 0.5), math.floor(v + 0.5)
        if self.walkable(a, i, j):
            return True
        si, sj = self._lean(u - i), self._lean(v - j)
        return bool((si and self.walkable(a, i + si, j)) or (sj and self.walkable(a, i, j + sj))
                    or (si and sj and self.walkable(a, i + si, j + sj)))

    def _raw_ok(self, a, x, z):
        return self.on_cell(a, x, z)

    def _near_los(self, x, z):
        """Line of sight's area lookup (rsnav_los.c near_areas): the 4 m bucket holding (x, z) in every sector whose
        rectangle grown by LOS_SEAM holds it, bucket indices clamped to the grid, at most LOS_NEAR areas."""
        out = []
        for s in self.sectors:
            _id, _layer, _pc, x0, z0, x1, z1, first_bucket, _fa, _na, _r = s
            if x0 - LOS_SEAM <= x < x1 + LOS_SEAM and z0 - LOS_SEAM <= z < z1 + LOS_SEAM:
                bi = min(max(math.floor((x - x0) / BUCKET), 0), BUCKETS - 1)
                bj = min(max(math.floor((z - z0) / BUCKET), 0), BUCKETS - 1)
                first, count = self.gbkt[first_bucket + bj * BUCKETS + bi]
                for a in self.glst[first:first + count]:
                    if len(out) >= LOS_NEAR:
                        return out
                    out.append(int(a))
        return out

    def _allowed(self, k1, k2):
        return LOS_COARSE_DY if self.areas[k1].pitch_mm_ >= COARSE_MM or self.areas[k2].pitch_mm_ >= COARSE_MM \
            else LOS_DY

    def _other_ok(self, skip, x, z, y):
        for k in self._near_los(x, z):
            b = self.areas[k]
            if k == skip or not self._raw_ok(b, x, z):
                continue
            if abs(self.ground(b, x, z) - y) <= self._allowed(skip, k):
                return True
        return False

    def _cell_ok(self, k, x, z):
        a = self.areas[k]
        if a.pitch_mm_ < COARSE_MM:
            return self.on_cell(a, x, z)
        u, v = (x - a.x0) / a.pitch, (z - a.z0) / a.pitch
        i, j = math.floor(u + 0.5), math.floor(v + 0.5)
        if not self.walkable(a, i, j):
            return self.on_cell(a, x, z)       # a sample leaning on a cell of the area beside its own (LOS_GRAZE)
        y = self.ground(a, x, z)
        si, sj = self._lean(u - i), self._lean(v - j)
        for di, dj in ((si, 0), (0, sj)):
            if not (di or dj):
                continue
            ii, jj = i + di, j + dj
            if ii < 0 or jj < 0 or ii >= a.width or jj >= a.height or self.walkable(a, ii, jj):
                continue
            if not self._other_ok(k, a.x0 + ii * a.pitch, a.z0 + jj * a.pitch, y):
                return False
        return True

    def _compatible(self, f, t, x, z, gy=None):
        """Whether the line may go on in area t at (x, z): t's ground there within the allowance of the followed
        ground gy (LOS_FOLLOWED; of area f's ground at (x, z) without it), and t's cell test passes."""
        ref = self.ground(self.areas[f], x, z) if gy is None or not LOS_FOLLOWED else gy
        d = self.ground(self.areas[t], x, z) - ref
        return abs(d) <= self._allowed(f, t) and self._cell_ok(t, x, z)

    def _neighbour(self, cur, x, z, gy=None):
        a = self.areas[cur]
        for p in self.ports[a.first_portal:a.first_portal + a.portals]:
            if p[6] != cur and self._compatible(cur, p[6], x, z, gy):
                return p[6]
        for k in self._near_los(x, z):
            if k != cur and self._compatible(cur, k, x, z, gy):
                return k
        return None

    def _los_start(self, a):
        """The area line of sight starts on (LOS_START_NEAREST): of the areas with ground under a (on_cell) within the
        step plus half a metre of its height, the one whose ground is nearest a's height (the first in _near_los order
        on a tie); None when there is none (then locate's nearest cell within 1 m)."""
        best = None
        lim = self.step + 0.5
        for k in self._near_los(a[0], a[2]):
            ar = self.areas[k]
            if not self.on_cell(ar, a[0], a[2]):
                continue
            d = abs(self.ground(ar, a[0], a[2]) - a[1])
            if d <= lim and (best is None or d < best[1]):
                best = (k, d)
        return best[0] if best else None

    def los(self, a, b, trace=None):
        """Whether a soldier walks straight from a to b (x, y, z; pass float32 values for parity with rsnav): every
        sample, a quarter of the finest pitch apart, on a walkable cell (on_cell: in fine areas within LOS_GRAZE of
        one); between areas through portals or the buckets, to ground within LOS_DY (LOS_COARSE_DY where either area
        is coarse); the ground changing by at most that between samples; coarse cells needing their neighbours on the
        sides a sample leans to (LOS_GRAZE); gaps up to COARSE_GAP crossed in coarse zones; the first LOS_GRACE not
        tested. See rsnav's rsnav_los.c for the reasons.
        trace: a dict that, on a refusal, gets why (diagnostics only, rsnvtool's tight: the rule is unchanged) -
        rule 'start' (no cell within 1 m of a), 'long', 'cell' (a sample on no compatible cell; with the coarse gap
        search failed in a coarse area), 'climb' (the ground changing by more than lim between samples) or 'end'
        (b's height off its ground), with the sample (k of n, x, z), the area it was on and the amounts."""
        cur = self._los_start(a) if LOS_START_NEAREST else None
        if cur is None:
            loc = self.locate(a[0], a[1], a[2], max_dist=1.0)
            if loc is None:
                if trace is not None:
                    trace.update(rule='start')
                return False
            cur = loc[0]
        gy = self.ground(self.areas[cur], a[0], a[2])
        dx, dz = b[0] - a[0], b[2] - a[2]
        ln = math.sqrt(dx * dx + dz * dz)
        step = self.min_pitch / 4.0
        n = math.ceil(ln / step)
        if n > 200000:
            if trace is not None:
                trace.update(rule='long')
            return False
        k = 0
        while k < n:
            k += 1
            t = k / n
            if ln * t < LOS_GRACE:
                continue
            x, z = a[0] + dx * t, a[2] + dz * t
            prev = cur
            if not self._cell_ok(cur, x, z):
                nb = self._neighbour(cur, x, z, gy)
                if nb is None and self.areas[cur].pitch_mm_ >= COARSE_MM:
                    k2 = k + 1
                    while k2 <= n and ln * ((k2 - k) / n) <= COARSE_GAP:
                        t2 = k2 / n
                        x2, z2 = a[0] + dx * t2, a[2] + dz * t2
                        gap = ln * (t2 - t) + step
                        for c in self._near_los(x2, z2):
                            d2 = self.ground(self.areas[c], x2, z2) - gy
                            if self._raw_ok(self.areas[c], x2, z2) and abs(d2) <= LOS_STEP + gap:
                                nb = c
                                break
                        if nb is not None:
                            k, t, x, z = k2, t2, x2, z2
                            break
                        k2 += 1
                    if nb is not None:
                        cur = nb
                        gy = self.ground(self.areas[cur], x, z)
                        continue
                if nb is None:
                    if trace is not None:
                        trace.update(rule='cell', k=k, n=n, x=x, z=z, along=ln * t, area=cur, gy=gy)
                    return False
                cur = nb
            ny = self.ground(self.areas[cur], x, z)
            lim = LOS_DY if cur == prev else self._allowed(prev, cur)
            if ln * t >= LOS_GRACE + step and abs(ny - gy) > lim:
                if trace is not None:
                    trace.update(rule='climb', k=k, n=n, x=x, z=z, along=ln * t, area=cur, prev_area=prev, gy=gy,
                                 ny=ny, lim=lim, excess=abs(ny - gy) - lim)
                return False
            gy = ny
        d = self.ground(self.areas[cur], b[0], b[2]) - b[1]
        lim = LOS_COARSE_DY if self.areas[cur].pitch_mm_ >= COARSE_MM else LOS_DY
        if trace is not None and abs(d) > lim:
            trace.update(rule='end', area=cur, d=d, lim=lim, excess=abs(d) - lim, x=b[0], z=b[2], along=ln)
        return abs(d) <= lim

    def validate(self):
        """Problems with the file's contents ([] when none): every index and range the readers follow, the checks of
        rsnav's rsn_load (rsnav_load.c) and with its messages, so a file this accepts the runtime accepts and the other
        way round (2026-10-26: an ANOD node id past the nodes passed and crashed nearest_node)."""
        out = []
        n = len(self.node_pos)
        n_edge, n_area, n_sect = len(self.edge_target), len(self.areas), len(self.sectors)
        coord = lambda v: -1e6 < v < 1e6                    # rsnav's coordinate(): finite, inside +-1e6 m
        if n >= 1 << 24:
            out.append('more nodes than an edge can address')
        if len(self.node_area) != n:
            out.append('NAUX count is not the node count')
        if np.any(np.diff(self.node_first_edge) < 0):
            out.append('NODE first edges not ascending')
        if not all(coord(float(v)) for v in self.node_pos.ravel()):
            out.append('NODE position not a number or past 1e6 m')
        if self.node_first_edge[-1] != n_edge or (n and self.node_first_edge[0] != 0):
            out.append('NODE edge ranges do not cover EDGE')
        if n_edge and int(self.edge_target.max()) >= n:
            out.append('EDGE target out of range')
        if n_edge and int(self.edge_kind.max()) >= EDGE_KINDS:
            out.append('EDGE kind unknown')
        if len(self.node_area) == n and n and (np.any((self.node_area != 0xFFFF) & (self.node_area >= n_area))
                                               or np.any(self.node_sector >= n_sect)):
            out.append('NAUX area or sector out of range')
        for sc in self.sectors:
            _id, _l, _pc, x0, z0, x1, z1, first_bucket, first_area, areas, _r = sc
            if first_bucket + BUCKETS * BUCKETS > len(self.gbkt):
                out.append('SECT buckets past GBKT')
            if first_area + areas > n_area:
                out.append('SECT areas past AREA')
            if not (x0 <= x1 and z0 <= z1 and coord(x0) and coord(z0) and coord(x1) and coord(z1)):
                out.append('SECT bounds inverted, not numbers or past 1e6 m')
        if len(self.gbkt) and int((self.gbkt[:, 0] + self.gbkt[:, 1]).max()) > len(self.glst):
            out.append('GBKT list past GLST')
        if len(self.glst) and int(self.glst.max()) >= n_area:
            out.append('GLST area out of range')
        if len(self.anod) and int(self.anod.max()) >= n:
            out.append('ANOD node out of range')
        for a in self.areas:
            row = (a.width + 7) >> 3
            if a.bmap + row * a.height > len(self.bmap):
                out.append('AREA bitmap past BMAP')
            if a.hght + a.hw * a.hh > len(self.hght) or a.hw == 0 or a.hh == 0:
                out.append('AREA height grid past HGHT or empty (every area has at least one block)')
            if a.first_node + a.nodes > len(self.anod):
                out.append('AREA nodes past ANOD')
            if a.first_portal + a.portals > len(self.ports):
                out.append('AREA portals past PORT')
            if a.pitch_mm_ < 50 or a.sector >= n_sect:
                out.append('AREA pitch or sector invalid')
            if a.hstep_mm != 0 and (a.hstep_mm < a.pitch_mm_ or a.hstep_mm > 100000):
                out.append('AREA height grid step under its pitch or over 100 m')
            if not (a.min_y <= a.max_y and coord(a.x0) and coord(a.z0) and coord(a.min_y) and coord(a.max_y)):
                out.append('AREA corner or heights inverted, not numbers or past 1e6 m')
        if any(p[5] >= n_area or p[6] >= n_area for p in self.ports):
            out.append('PORT area out of range')
        if any(L[7] >= n or L[8] >= n for L in self.ladders):
            out.append('LADR node out of range')
        if any(o[3] != 0xFFFFFFFF and o[3] >= n for o in self.offmesh):
            out.append('OFFM node out of range')
        if any(i[1] >= n for i in self.islands):
            out.append('ISLD representative out of range')
        oref = getattr(self, 'oref', [o for objs in self.cond.values() for o in objs])
        if any(o >= len(self.objects) for o in oref):
            out.append('OREF object out of range')
        rows = getattr(self, 'cedg_rows', [(k, 0, len(v), 0) for k, v in sorted(self.cond.items())])
        c = 0
        for e_ in range(n_edge):                           # rsn_load's walk: flagged edges listed, in order
            flagged = bool(int(self.edge_flags[e_]) & EDGEF_COND)
            listed = c < len(rows) and rows[c][0] == e_
            if flagged != listed:
                out.append('CEDG does not list exactly the conditional edges, in order')
                break
            if listed:
                if rows[c][1] + rows[c][2] > len(oref) or rows[c][2] == 0:
                    out.append('CEDG objects past OREF or none')
                    break
                c += 1
        else:
            if c != len(rows):
                out.append('CEDG lists an edge out of range or out of order')
        return list(dict.fromkeys(out))


def _array(raw, e, off, count, fmt):
    st = struct.Struct(e + fmt)
    return [st.unpack_from(raw, off + k * st.size) for k in range(count)]


def load(src):
    raw = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
    if len(raw) < HEADER:
        raise ValueError('RSNV: shorter than its header')
    if raw[:4] != MAGIC:
        raise ValueError('not an RSNV file')
    e = '<' if struct.unpack_from('<I', raw, 4)[0] == 0x01020304 else '>'
    if struct.unpack_from(e + 'I', raw, 4)[0] != 0x01020304:
        raise ValueError('RSNV: bad byte-order marker')
    version, flags, size, nsec, level_id = struct.unpack_from(e + 'HHIII', raw, 8)
    if version != VERSION:
        raise ValueError(f'RSNV version {version}, this reader reads {VERSION}')
    if size != len(raw):
        raise ValueError(f'RSNV: header says {size} bytes, the file has {len(raw)}')
    sea, radius, height, step, water = struct.unpack_from(e + '5f', raw, 0x18)
    w = World(level_id, e == '>', sea, radius, height, step, water, raw=bytes(raw))
    w.h_scale_q = struct.unpack_from(e + 'I', raw, H_SCALE_AT)[0]
    if nsec > 4 * len(SCHEMA) + 8 or HEADER + 16 * nsec > size:
        raise ValueError('RSNV: section directory outside the file')
    for k in range(nsec):
        tag = raw[HEADER + 16 * k:HEADER + 16 * k + 4].decode('latin-1')     # an unknown tag is skipped, as rsn_load skips it
        off, count, stride = struct.unpack_from(e + 'III', raw, HEADER + 16 * k + 4)
        if off + count * stride > len(raw) or off % 16:
            raise ValueError(f'RSNV: section {tag} outside the file or misaligned')
        if (tag in SCHEMA and stride != struct.calcsize('<' + SCHEMA[tag])) or (tag in ('BMAP', 'META') and stride != 1):
            raise ValueError(f'RSNV: section {tag} stride {stride}')
        if tag in w.sections and (tag in SCHEMA or tag in ('BMAP', 'META')):
            raise ValueError(f'RSNV: section {tag} appears twice')
        w.sections[tag] = (off, count, stride)
    for need in ('SECT', 'NODE', 'NAUX', 'EDGE', 'AREA', 'BMAP', 'HGHT', 'GBKT', 'GLST', 'ANOD'):
        if need not in w.sections:
            raise ValueError(f'RSNV: no {need} section')
    sec = lambda t: w.sections.get(t, (0, 0, 0))
    if sec('NODE')[1] < 1:
        raise ValueError('RSNV: NODE has no sentinel')
    o, c, _ = sec('NODE')
    nodes = np.frombuffer(raw, np.dtype([('p', e + 'f4', 3), ('e', e + 'u4')]), c, o)
    w.node_pos = nodes['p'][:-1].astype(np.float64)
    w.node_first_edge = nodes['e'].astype(np.int64)
    o, c, _ = sec('NAUX')
    naux = np.frombuffer(raw, np.dtype(e + 'u2'), 4 * c, o).reshape(c, 4).astype(np.int64)
    w.node_area, w.node_sector, w.node_island, w.node_flags = naux.T
    o, c, _ = sec('EDGE')
    edges = np.frombuffer(raw, np.dtype([('t', e + 'u4'), ('c', e + 'u2'), ('f', e + 'u2')]), c, o)
    w.edge_target = (edges['t'] & 0xFFFFFF).astype(np.int64)
    w.edge_kind = (edges['t'] >> 24).astype(np.int64)
    w.edge_cost = edges['c'].astype(np.int64)
    w.edge_flags = edges['f'].astype(np.int64)
    w.sectors = _array(raw, e, *sec('SECT')[:2], SCHEMA['SECT'])
    for k, r in enumerate(_array(raw, e, *sec('AREA')[:2], SCHEMA['AREA'])):
        (x0, z0, x1, z1, mny, mxy, bmap, bw, bh, fp, npo, sector, island, flags, fnode, nnode, pmm, hg, hw, hh, _z) = r
        w.areas.append(Area(k, x0, z0, x1, z1, mny, mxy, bmap, bw, bh, fp, npo, sector, island, flags, fnode, nnode,
                            pmm / 1000.0, hg, hw, hh, _z))
    o, c, _ = sec('BMAP')
    w.bmap = raw[o:o + c]
    o, c, _ = sec('HGHT')
    w.hght = np.frombuffer(raw, np.dtype(e + 'u2'), c, o)
    o, c, _ = sec('GBKT')
    w.gbkt = np.frombuffer(raw, np.dtype(e + 'u4'), 2 * c, o).reshape(c, 2).astype(np.int64)
    o, c, _ = sec('GLST')
    w.glst = np.frombuffer(raw, np.dtype(e + 'u2'), c, o)
    o, c, _ = sec('ANOD')
    w.anod = np.frombuffer(raw, np.dtype(e + 'u4'), c, o)
    w.ladders = _array(raw, e, *sec('LADR')[:2], SCHEMA['LADR']) if 'LADR' in w.sections else []
    w.offmesh = _array(raw, e, *sec('OFFM')[:2], SCHEMA['OFFM']) if 'OFFM' in w.sections else []
    w.islands = _array(raw, e, *sec('ISLD')[:2], SCHEMA['ISLD']) if 'ISLD' in w.sections else []
    w.ports = _array(raw, e, *sec('PORT')[:2], SCHEMA['PORT']) if 'PORT' in w.sections else []
    pm = min((a.pitch_mm_ for a in w.areas), default=200)
    w.min_pitch = float(np.float32(np.float32(pm) / np.float32(1000.0)))      # as rsnav computes it, in float
    w.objects = _array(raw, e, *sec('OBJT')[:2], SCHEMA['OBJT']) if 'OBJT' in w.sections else []
    w.cond = {}                                     # edge -> [object indices]
    w.cedg_rows = _array(raw, e, *sec('CEDG')[:2], SCHEMA['CEDG']) if 'CEDG' in w.sections else []
    o, c, _ = sec('OREF')
    w.oref = [int(x) for x in np.frombuffer(raw, np.dtype(e + 'u4'), c, o)] if c else []
    for edge, first, count, _r in w.cedg_rows:
        w.cond[int(edge)] = w.oref[first:first + count]
    if 'META' in w.sections:
        o, c, _ = sec('META')
        try:
            w.meta = json.loads(raw[o:o + c].decode('utf-8'))
        except ValueError:                          # provenance only: the runtime does not read META
            w.meta = {'unreadable META': True}
    problems = w.validate()
    if problems:
        raise ValueError('RSNV: ' + problems[0])
    return w


DONE, PARTIAL = 'done', 'partial'


def edge_cost(w, n, k, water_scale=(256, 256, 256), kind_extra=None, penalty=None, destroyed=None, tight_extra=0):
    """The filter's cost of edge k from node n (rsnav's rsn_edge_cost), or None when it is closed: floor(cost x
    water_scale[class] / 256) + kind_extra[kind] + tight_extra (a tight edge) + penalty, class the deeper end's (0 dry,
    1 shallow, 2 wading); a
    conditional edge is closed unless all its objects are in `destroyed`."""
    if int(w.edge_flags[k]) & EDGEF_COND and not (destroyed is not None and all(o in destroyed for o in w.cond.get(k, [None]))):
        return None

    def cls(m):
        f = int(w.node_flags[m])
        return 2 if f & NAUX_WADE else 1 if f & NAUX_SHALLOW else 0
    c = int(w.edge_cost[k]) * water_scale[max(cls(n), cls(int(w.edge_target[k])))] >> 8
    if kind_extra:
        c += kind_extra[int(w.edge_kind[k])]
    if tight_extra and int(w.edge_flags[k]) & EDGEF_TIGHT:
        c += tight_extra
    if penalty:
        c += penalty.get(k, 0)
    return c


def search(w, start, goal, max_expansions=1 << 30, kind_mask=None, water_scale=(256, 256, 256), kind_extra=None,
           penalty=None, destroyed=None, tight_extra=0):
    """The reference search (design sections 2.6 and 6.3), which the C runtime (rsnav) must match exactly: integer
    costs in 1/16 m; heuristic floor(16 x Euclidean x w.h_scale) in double precision (admissible: exporter 1.4 rounds
    each cost up to 16 x its length, and heuristic_scale bounds the scale by the costs), summed x, y, then z; the open list
    ordered by (f, node); a closed node is never reopened; an equal-cost parent is replaced by a lower-indexed one.
    The goal reached when popped is DONE; otherwise, once max_expansions nodes are expanded or the open list runs out,
    PARTIAL: the path to the expanded node with the least heuristic (then cost, then index), or [start] if none.
    Edge costs are edge_cost()'s (water_scale, kind_extra, penalty: {edge: extra}, destroyed: object indices).
    Returns (status, cost, [nodes], expansions)."""
    import heapq
    pos = w.node_pos
    fe, tgt, cost, kind = w.node_first_edge, w.edge_target, w.edge_cost, w.edge_kind
    gx, gy, gz = (float(v) for v in pos[goal])

    scale = getattr(w, 'h_scale', H_SCALE_LEGACY)

    def h(n):
        dx, dy, dz = float(pos[n][0]) - gx, float(pos[n][1]) - gy, float(pos[n][2]) - gz
        return math.floor(16.0 * math.sqrt((dx * dx + dy * dy) + dz * dz) * scale)
    g = {start: 0}
    hv = {start: h(start)}
    parent = {start: -1}
    heap = [(hv[start], start)]
    closed = set()
    exp = 0
    best = None

    def path_to(n):
        p = [n]
        while parent[p[-1]] != -1:
            p.append(parent[p[-1]])
        return p[::-1]
    while heap:
        f, n = heap[0]
        if n in closed:
            heapq.heappop(heap)
            continue
        if n == goal:
            return DONE, g[n], path_to(n), exp
        if exp >= max_expansions:
            break
        heapq.heappop(heap)
        closed.add(n)
        exp += 1
        if best is None or (hv[n], g[n], n) < best:
            best = (hv[n], g[n], n)
        for k in range(fe[n], fe[n + 1]):
            if kind_mask is not None and not (kind_mask >> int(kind[k]) & 1):
                continue
            m = int(tgt[k])
            if m in closed:
                continue
            c = edge_cost(w, n, k, water_scale, kind_extra, penalty, destroyed, tight_extra)
            if c is None:
                continue
            ng = g[n] + c
            if m not in g or ng < g[m] or (ng == g[m] and n < parent[m]):
                if m not in hv:
                    hv[m] = h(m)
                if m not in g or ng < g[m]:
                    heapq.heappush(heap, (ng + hv[m], m))
                g[m] = ng
                parent[m] = n
    end = best[2] if best else start
    return PARTIAL, g[end], path_to(end), exp


def astar(w, start, goal, max_expansions=1 << 30, kind_mask=None):
    """search() for a complete path: (cost, [nodes], expansions), or (None, [], expansions) when the goal is not
    reached."""
    status, c, path, exp = search(w, start, goal, max_expansions, kind_mask)
    return (c, path, exp) if status == DONE else (None, [], exp)
