r"""NavKit: navigation mesh for boats on a level's water - a side file for OPFOR's bot layer (prototype, 2026-10-26).

  navwater_mesh.py <level> [--profile pbl|pwc] [--pitch 2] [--box minx,minz,maxx,maxz] [--infantry <pathdata.hpd>]
                   [--out <folder>] [--opfor <folder>] [--no-objects] [--no-samples]

Why a side file and not Kynapse data: BC2 has no water pathfinding. The AI's vehicle profiles
(mp_common / sp_common AI/Behaviours, AIVehicleBehaviourData) carry a Navigation type of NTNone, NTGround or
NTHelicopter - the only three the executable knows - and the patrol boat's driver profile "PBL" is NTNone with no
PathfindingType: the game never asks Kynapse for a boat route (the jet ski's driver uses the "Car" profile, NTGround
on the land vehicle data, which has no water). See research\CROSS_DOMAIN_ROUTES.md.

The mesh: a grid of cells (default 2 m) over the level's water surface (core/mohcore/water.py), within a box
(default: the in-game sampling box of the level, else the terrain's bounds). A cell is
  water        the water surface lies above the terrain there,
  blocked      the terrain, a sampled surface (OPFOR's in-game collision samples, when there are some) or a
               placed object's collision triangle lies in the hull band: from the boat's draft under the surface
               to its height above it (shallows, piers, docks, wrecks, ships, buoys, bridge piers); a bridge deck
               higher than the band does not block,
  navigable    water and not blocked,
  free         navigable and no blocked or dry cell within the boat's radius (the boat's centre may go there).
Free cells form regions (8-connected, a diagonal step needs a free side cell). Each region gets route nodes (in
each block of 8 x 8 cells the cell with the most clearance per region piece), edges between nodes up to two
blocks apart that see each other over free cells (cost: metres), and repair links where a region's graph would
fall apart. Shore links join the boat mesh to infantry ground (a path-data file's walkable cells): a free cell
within reach of infantry ground at the water line (beach: ground down to 1.5 m under the surface - DICE's wading
limit, navwater.py) or just above it (dock: object ground up to 3 m above), one per 20 m of shore, each with the
infantry route node and island at its land end. Sea-vehicle spawns of the level are attached to the mesh.

Output (only under the --out folder, default OPFOR's work\navwater\<level>\):
  <level>.<profile>.watermesh.json   the side file (format 'opfor-watermesh' 1, described in WATERMESH_FORMAT)
  <level>.<profile>.png              a picture of it
  <level>.<profile>.report.json      the validation figures
"""
import base64
import hashlib
import heapq
import json
import math
import os
import re
import sys
import time
import zlib
from collections import deque
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))

FORMAT, VERSION = 'opfor-watermesh', 1
WATERMESH_FORMAT = """JSON object:
  format 'opfor-watermesh', version 1, level, profile {name, radius, draft, height, speed}, pitch,
  origin [x, z] (centre of cell 0, 0), nx, nz (cells along x / z; cell (i, j) centre at origin + (i, j) * pitch),
  grids: base64 of zlib of little-endian arrays, row-major j then i:
    flags   u8  bit 0 water, 1 blocked, 2 navigable, 3 free (the boat's centre may be there), 4 dry (terrain above)
    surface f32 water surface height (NaN where there is no water)
    region  i32 region of a free cell (-1 elsewhere), regions ordered by size
  regions [{id, cells, area_m2, nodes}], nodes [[x, y, z, region, clearance_m]], edges [[a, b, cost_m]] (two-way),
  shore_links [{id, kind beach|dock, boat [x, y, z], boat_node, land [x, y, z], land_node, land_island,
                land_island_nodes, distance_m}],
  spawns [{name, vehicle, team, pos, cell_free, region, node, launch [x, y, z], launch_distance_m}],
  infantry {file, sha1} (the path data the shore links were made against), stats {...}"""


@dataclass
class Profile:
    name: str
    radius: float        # m, the hull's half length - padding from obstacles and shallows
    draft: float         # m under the surface the hull needs
    height: float        # m above the surface the hull needs (superstructure, gunner)
    speed: float         # m/s, the AI's MaxSpeed (mp_common AI/Vehicles MobilityData)
    reach: float = 2.0   # m beyond the radius a soldier can board from


PROFILES = {
    'pbl': Profile('pbl', radius=3.5, draft=1.0, height=3.0, speed=17.0),   # patrol boat: MobilityData MaxSpeed 17
    'pwc': Profile('pwc', radius=1.5, draft=0.5, height=1.5, speed=20.0),   # jet ski: MaxSpeed 20
}
BLOCK = 8                 # cells per node block side
EDGE_BLOCKS = 2           # edges to nodes up to this many blocks away (Chebyshev)
WADE_DEPTH = 1.5          # infantry ground under water down to this depth is walkable (DICE's data, navwater.py)
DOCK_RISE = 3.0           # object ground up to this far above the surface is a dock a soldier can board from
SHORE_SPACING = 20.0      # m, one shore link per this many metres of shore (per land island and boat region)
F_WATER, F_BLOCKED, F_NAV, F_FREE, F_DRY = 1, 2, 4, 8, 16
NODE_SNAP = 10.0          # m, a link's land end takes the nearest infantry route node within this
SPAWN_LAUNCH = 15.0       # m, a boat spawn off the free water is joined to the nearest free cell within this


# ---------------------------------------------------------------------------------------------- the grid

@dataclass
class Grid:
    pitch: float
    x0: float
    z0: float
    nx: int
    nz: int

    def centres(self):
        X = self.x0 + np.arange(self.nx) * self.pitch
        Z = self.z0 + np.arange(self.nz) * self.pitch
        return np.meshgrid(X, Z)                    # (nz, nx) each

    def cell_of(self, x, z):
        i = np.floor((np.asarray(x, float) - self.x0) / self.pitch + 0.5).astype(np.int64)
        j = np.floor((np.asarray(z, float) - self.z0) / self.pitch + 0.5).astype(np.int64)
        return i, j

    def inside(self, i, j):
        return (i >= 0) & (i < self.nx) & (j >= 0) & (j < self.nz)


def make_grid(box, pitch):
    x0 = math.floor(box[0] / pitch) * pitch
    z0 = math.floor(box[1] / pitch) * pitch
    nx = int(math.floor((box[2] - x0) / pitch)) + 1
    nz = int(math.floor((box[3] - z0) / pitch)) + 1
    return Grid(pitch, x0, z0, nx, nz)


def surface_heights(grid, vertices, triangles):
    """The water surface over each cell centre ((nz, nx), NaN where none); the highest where pieces overlap.
    navbuild.water_tops on a column grid with the cells' layout."""
    import navbuild
    s = SimpleNamespace(nx=grid.nx, nz=grid.nz, pitch=grid.pitch, min_x=grid.x0, min_z=grid.z0)
    top = navbuild.water_tops(s, vertices, triangles).reshape(grid.nz, grid.nx)
    return np.where(np.isfinite(top), top, np.nan)


def terrain_heights(grid, heightfield):
    """The highest terrain height at the cell centre and the four points half a cell away (conservative bed)."""
    if heightfield is None:
        return np.full((grid.nz, grid.nx), -np.inf)
    X, Z = grid.centres()
    h = grid.pitch / 2
    out = heightfield.height(X, Z)
    for dx, dz in ((-h, 0), (h, 0), (0, -h), (0, h)):
        out = np.maximum(out, heightfield.height(X + dx, Z + dz))
    lo, hi = heightfield.min, heightfield.max
    outside = (X < lo[0]) | (X > hi[0]) | (Z < lo[2]) | (Z > hi[2])
    return np.where(outside, -np.inf, out)


def mark_points(grid, surface, blocked, pts, profile):
    """Points (n, 3) of collision: a point in the hull band of its cell's water blocks the cell. Returns the count."""
    if not len(pts):
        return 0
    i, j = grid.cell_of(pts[:, 0], pts[:, 2])
    ok = grid.inside(i, j)
    i, j, y = i[ok], j[ok], pts[ok, 1]
    s = surface[j, i]
    hit = np.isfinite(s) & (y > s - profile.draft) & (y < s + profile.height)
    blocked[j[hit], i[hit]] = True
    return int(hit.sum())


def triangle_points(tris, step):
    """Points on each triangle (m, 3, 3), no two more than about step apart (corners, edges and inside)."""
    out = [tris.reshape(-1, 3)]
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    longest = np.maximum.reduce([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)])
    n = np.maximum(1, np.ceil(longest / step)).astype(int)
    for k in np.unique(n):
        sel = n == k
        u, v = np.meshgrid(np.arange(k + 1), np.arange(k + 1))
        keep = (u + v) <= k
        u, v = u[keep] / k, v[keep] / k                       # barycentric lattice
        A, B, C = a[sel], b[sel], c[sel]
        p = A[:, None, :] + (B - A)[:, None, :] * u[None, :, None] + (C - A)[:, None, :] * v[None, :, None]
        out.append(p.reshape(-1, 3))
    return np.concatenate(out)


def object_triangles(root, lv, log=print, cache=None):
    """World-space collision triangles (m, 3, 3) of every placed object and group instance of the level
    (objects.json / group_instances.json from levelobjects.py; each blueprint's <name>_Havok.res). Cached in cache."""
    if cache and os.path.isfile(cache):
        return np.load(cache)
    import levelobjects
    L = os.path.join(root, 'work', 'levels', lv)
    objs = json.load(open(os.path.join(L, 'objects.json'))) if os.path.isfile(os.path.join(L, 'objects.json')) else []
    groups = json.load(open(os.path.join(L, 'group_instances.json'))) if os.path.isfile(os.path.join(L, 'group_instances.json')) else []
    bases = [os.path.join(L, 'pkg'), os.path.join(L, 'dist')] + \
        [os.path.join(root, 'work', 'navkit', 'common', c) for c in ('sp_common', 'mp_common')]
    havok = levelobjects.havok_index([b for b in bases if os.path.isdir(b)])
    local, world, missing = {}, [], set()
    for o in objs + groups:
        bp = o['bp']
        if bp not in local:
            local[bp] = levelobjects.triangles(havok[bp]) if bp in havok else None
        t = local[bp]
        if t is None:
            missing.add(bp)
            continue
        m = np.array(o['m']).reshape(4, 4)
        world.append((t @ m[:3, :3] + m[3, :3]).astype(np.float32))
    tris = np.concatenate(world) if world else np.zeros((0, 3, 3), np.float32)
    log(f'objects: {len(objs)} placed, {len(groups)} group instances, {len(tris):,} collision triangles; '
        f'{len(missing)} blueprints without collision')
    if cache:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        np.save(cache, tris)
    return tris


def mark_triangles(grid, surface, blocked, tris, profile):
    """Collision triangles crossing the hull band over water block their cells."""
    if not len(tris):
        return 0
    lo = np.nanmin(surface) - profile.draft if np.isfinite(surface).any() else 0
    hi = np.nanmax(surface) + profile.height if np.isfinite(surface).any() else 0
    ymin, ymax = tris[:, :, 1].min(1), tris[:, :, 1].max(1)
    x0, x1 = grid.x0 - grid.pitch, grid.x0 + grid.nx * grid.pitch
    z0, z1 = grid.z0 - grid.pitch, grid.z0 + grid.nz * grid.pitch
    sel = (ymax > lo) & (ymin < hi) & (tris[:, :, 0].max(1) > x0) & (tris[:, :, 0].min(1) < x1) & \
          (tris[:, :, 2].max(1) > z0) & (tris[:, :, 2].min(1) < z1)
    count = 0
    t = tris[sel].astype(np.float64)
    for k in range(0, len(t), 4000):
        count += mark_points(grid, surface, blocked, triangle_points(t[k:k + 4000], grid.pitch / 3), profile)
    return count


def mark_samples(grid, surface, blocked, samples, profile):
    """OPFOR's in-game collision samples (navsample.py): every recorded surface in the hull band blocks its cell
    (soldiers standing in the level while it was sampled do not count)."""
    import navwalk
    s = samples
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x = s.min_x + (col % s.nx) * s.pitch
    z = s.min_z + (col // s.nx) * s.pitch
    keep = np.ones(len(s.y), bool)
    if s.hit is not None:
        keep &= s.hit[:, 0] != navwalk.CHARACTER_SHAPE_VTABLE
    pts = np.stack([x[keep], s.y[keep].astype(np.float64), z[keep]], 1)
    return mark_points(grid, surface, blocked, pts, profile)


def disk(radius_cells):
    r = int(math.ceil(radius_cells))
    return [(di, dj) for dj in range(-r, r + 1) for di in range(-r, r + 1) if di * di + dj * dj < radius_cells * radius_cells]


def dilate(mask, offsets):
    out = mask.copy()
    nz, nx = mask.shape
    for di, dj in offsets:
        if di == 0 and dj == 0:
            continue
        if abs(dj) >= nz or abs(di) >= nx:
            continue
        ys = slice(max(0, dj), nz + min(0, dj))
        xs = slice(max(0, di), nx + min(0, di))
        ys_src = slice(max(0, -dj), nz + min(0, -dj))
        xs_src = slice(max(0, -di), nx + min(0, -di))
        out[ys, xs] |= mask[ys_src, xs_src]
    return out


def clearance(free, pitch):
    """Approximate distance (m) from each free cell to the nearest cell that is not free (chamfer 3-4)."""
    nz, nx = free.shape
    INF = 10 ** 9
    d = np.where(free, INF, 0).astype(np.int64)
    pad = np.zeros((nz + 2, nx + 2), np.int64)
    pad[1:-1, 1:-1] = d
    for j in range(1, nz + 1):                           # forward pass
        row, up = pad[j], pad[j - 1]
        cand = np.minimum.reduce([row[1:-1], up[:-2] + 4, up[1:-1] + 3, up[2:] + 4])
        row[1:-1] = cand
        for i in range(1, nx + 1):
            if row[i] > row[i - 1] + 3:
                row[i] = row[i - 1] + 3
    for j in range(nz, 0, -1):                           # backward pass
        row, dn = pad[j], pad[j + 1]
        cand = np.minimum.reduce([row[1:-1], dn[:-2] + 4, dn[1:-1] + 3, dn[2:] + 4])
        row[1:-1] = cand
        for i in range(nx, 0, -1):
            if row[i] > row[i + 1] + 3:
                row[i] = row[i + 1] + 3
    return pad[1:-1, 1:-1] / 3.0 * pitch


NEIGHBOURS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def step_ok(free, i, j, di, dj):
    if not free[j + dj, i + di]:
        return False
    return not (di and dj) or free[j, i + di] or free[j + dj, i]


def regions_of(free):
    """Region id per cell (-1 where not free), regions ordered by size (largest 0)."""
    nz, nx = free.shape
    lab = np.full(free.shape, -1, np.int64)
    sizes = []
    for j0, i0 in zip(*np.nonzero(free)):
        if lab[j0, i0] >= 0:
            continue
        r = len(sizes)
        lab[j0, i0] = r
        q, n = deque([(i0, j0)]), 0
        while q:
            i, j = q.popleft()
            n += 1
            for di, dj in NEIGHBOURS:
                a, b = i + di, j + dj
                if 0 <= a < nx and 0 <= b < nz and lab[b, a] < 0 and step_ok(free, i, j, di, dj):
                    lab[b, a] = r
                    q.append((a, b))
        sizes.append(n)
    order = np.argsort(-np.array(sizes, dtype=np.int64), kind='stable') if sizes else np.zeros(0, np.int64)
    remap = np.full(len(sizes) + 1, -1, np.int64)
    remap[order] = np.arange(len(order))
    return np.where(lab >= 0, remap[lab], -1), [sizes[k] for k in order]


def line_free(free, a, b):
    """Every cell along the segment between cell centres a = (i, j) and b is free (sampled every third cell)."""
    (i0, j0), (i1, j1) = a, b
    n = int(max(abs(i1 - i0), abs(j1 - j0)) * 3) + 1
    t = np.linspace(0, 1, n + 1)
    ii = np.floor(i0 + (i1 - i0) * t + 0.5).astype(int)
    jj = np.floor(j0 + (j1 - j0) * t + 0.5).astype(int)
    if not free[jj, ii].all():
        return False
    # diagonal moves between successive cells need a free side cell (no corner squeezing)
    di, dj = np.diff(ii), np.diff(jj)
    diag = (di != 0) & (dj != 0)
    if diag.any():
        k = np.nonzero(diag)[0]
        side = free[jj[k], ii[k] + di[k]] | free[jj[k] + dj[k], ii[k]]
        return bool(side.all())
    return True


class UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb
            return True
        return False

    def grow(self):
        self.p.append(len(self.p))
        return len(self.p) - 1


def build_graph(free, region, clear, grid, surface, block=BLOCK, edge_blocks=EDGE_BLOCKS):
    """Route nodes and edges per region. Returns (nodes [(i, j)], node_region, edges [(a, b)], repairs)."""
    nz, nx = free.shape
    nodes, node_region, node_block = [], [], []
    by_block = {}
    for bj in range(0, nz, block):
        for bi in range(0, nx, block):
            sub = region[bj:bj + block, bi:bi + block]
            if not (sub >= 0).any():
                continue
            cl = clear[bj:bj + block, bi:bi + block]
            # one node per connected piece of each region inside the block
            piece = regions_of(sub >= 0)[0]
            for pc in np.unique(piece[piece >= 0]):
                m = piece == pc
                score = np.where(m, cl, -1.0)
                cj, ci = np.unravel_index(int(np.argmax(score)), score.shape)
                k = len(nodes)
                nodes.append((bi + ci, bj + cj))
                node_region.append(int(sub[cj, ci]))
                node_block.append((bi // block, bj // block))
                by_block.setdefault((bi // block, bj // block), []).append(k)
    edges = set()
    for k, (b0, b1) in enumerate(node_block):
        for db in range(-edge_blocks, edge_blocks + 1):
            for dc in range(-edge_blocks, edge_blocks + 1):
                for m in by_block.get((b0 + db, b1 + dc), ()):
                    if m <= k or node_region[m] != node_region[k]:
                        continue
                    if line_free(free, nodes[k], nodes[m]):
                        edges.add((k, m))
    # repair: a region's nodes must form one graph; join the pieces along grid paths, string-pulled
    uf = UnionFind(len(nodes))
    for a, b in edges:
        uf.union(a, b)
    repairs = 0
    node_at = {n: k for k, n in enumerate(nodes)}
    for r in sorted(set(node_region)):
        members = [k for k in range(len(nodes)) if node_region[k] == r]
        while len({uf.find(k) for k in members}) > 1:
            root = uf.find(members[0])
            src = [nodes[k] for k in members if uf.find(k) == root]
            path = grid_path(free, src, {nodes[k] for k in members if uf.find(k) != root})
            if path is None:                                        # cannot happen inside one region
                break
            pts = string_pull(free, path)
            prev = node_at[pts[0]]
            for p in pts[1:]:
                if p not in node_at:
                    node_at[p] = len(nodes)
                    nodes.append(p)
                    node_region.append(r)
                    uf.grow()
                    members.append(node_at[p])
                cur = node_at[p]
                edges.add((min(prev, cur), max(prev, cur)))
                uf.union(prev, cur)
                prev = cur
            repairs += 1
    return nodes, node_region, sorted(edges), repairs


def grid_path(free, sources, targets):
    """Breadth-first path over free cells from any source cell to the first target cell reached."""
    nz, nx = free.shape
    prev = {s: None for s in sources}
    q = deque(sources)
    while q:
        c = q.popleft()
        if c in targets:
            out = []
            while c is not None:
                out.append(c)
                c = prev[c]
            return out[::-1]
        i, j = c
        for di, dj in NEIGHBOURS:
            a, b = i + di, j + dj
            if 0 <= a < nx and 0 <= b < nz and (a, b) not in prev and step_ok(free, i, j, di, dj):
                prev[(a, b)] = c
                q.append((a, b))
    return None


def string_pull(free, path):
    """Waypoints of a cell path: from each kept point, the farthest later cell in straight sight."""
    out, k = [path[0]], 0
    while k < len(path) - 1:
        m = len(path) - 1
        while m > k + 1 and not line_free(free, path[k], path[m]):
            m -= 1
        out.append(path[m])
        k = m
    return out


# ------------------------------------------------------------------------------------- shore links, spawns

def infantry_cells(nav):
    """(xz (n, 2), y (n,), island (n,), island node counts) of a NavData's walkable cells."""
    isl = np.array([nav.areas[a].island for a in nav.cell_area], dtype=np.int64) if len(nav.cell_area) else np.zeros(0, np.int64)
    sizes = np.array([i.nodes for i in nav.islands], dtype=np.int64)
    return nav.cell_xz, nav.cell_y, isl, sizes


def shore_links(grid, surface, flags, region, clear, cells, heightfield, profile, nav_nodes=None, node_island=None,
                spacing=SHORE_SPACING):
    """Links between free water cells and infantry ground at the water line. cells: infantry_cells(...).
    Returns a list of dicts (see WATERMESH_FORMAT; boat_node / ids filled in later)."""
    xz, y, isl, sizes = cells
    if not len(xz):
        return []
    i, j = grid.cell_of(xz[:, 0], xz[:, 1])
    ok = grid.inside(i, j)
    i_c, j_c = np.clip(i, 0, grid.nx - 1), np.clip(j, 0, grid.nz - 1)
    reach = profile.radius + profile.reach
    win = int(math.ceil(reach / grid.pitch))
    # the water surface near each cell: its own cell's, else the nearest water cell's within the window
    surf = np.where(ok, surface[j_c, i_c], np.nan)
    for r in range(1, win + 1):
        miss = ok & np.isnan(surf)
        if not miss.any():
            break
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                if max(abs(di), abs(dj)) != r:
                    continue
                a, b = i_c + di, j_c + dj
                inside = miss & grid.inside(a, b)
                s = np.full(len(xz), np.nan)
                s[inside] = surface[b[inside], a[inside]]
                surf = np.where(np.isnan(surf) & ~np.isnan(s), s, surf)
    dy = y - surf
    terrain = heightfield.height(xz[:, 0], xz[:, 1]) if heightfield is not None else y
    on_object = np.abs(y - terrain) > 0.5
    beach = ~on_object & (dy >= -WADE_DEPTH) & (dy <= 0.5)
    dock = on_object & (dy > 0.3) & (dy <= DOCK_RISE)
    cand = np.nonzero(ok & ~np.isnan(surf) & (beach | dock))[0]
    free = (flags & F_FREE) != 0
    best_d = np.full(len(cand), np.inf)
    best_c = np.full((len(cand), 2), -1, np.int64)
    for di in range(-win, win + 1):
        for dj in range(-win, win + 1):
            a, b = i_c[cand] + di, j_c[cand] + dj
            inside = grid.inside(a, b)
            hit = np.zeros(len(cand), bool)
            hit[inside] = free[b[inside], a[inside]]
            px = grid.x0 + a * grid.pitch
            pz = grid.z0 + b * grid.pitch
            d = np.hypot(px - xz[cand, 0], pz - xz[cand, 1])
            better = hit & (d <= reach) & (d < best_d)
            best_d[better] = d[better]
            best_c[better] = np.stack([a[better], b[better]], 1)
    found = np.isfinite(best_d)
    cand, best_d, best_c = cand[found], best_d[found], best_c[found]
    # one link per spacing tile of shore, per land island and boat region: the shortest
    keep = {}
    for n, c in enumerate(cand):
        reg = int(region[best_c[n, 1], best_c[n, 0]])
        key = (int(math.floor(xz[c, 0] / spacing)), int(math.floor(xz[c, 1] / spacing)), int(isl[c]), reg)
        if key not in keep or best_d[n] < best_d[keep[key]]:
            keep[key] = n
    out = []
    for key, n in sorted(keep.items(), key=lambda kv: kv[0]):
        c = cand[n]
        bi, bj = best_c[n]
        bx, bz = grid.x0 + bi * grid.pitch, grid.z0 + bj * grid.pitch
        link = {'kind': 'dock' if on_object[c] else 'beach',
                'boat': [round(float(bx), 2), round(float(surface[bj, bi]), 2), round(float(bz), 2)],
                'boat_cell': [int(bi), int(bj)], 'region': key[3],
                'land': [round(float(xz[c, 0]), 2), round(float(y[c]), 2), round(float(xz[c, 1]), 2)],
                'land_island': int(isl[c]), 'land_island_nodes': int(sizes[isl[c]]) if 0 <= isl[c] < len(sizes) else 0,
                'distance_m': round(float(best_d[n]), 2), 'land_node': None}
        if nav_nodes is not None and len(nav_nodes):
            p = nav_nodes
            d = np.hypot(p[:, 0] - xz[c, 0], p[:, 2] - xz[c, 1]) + 2.0 * np.abs(p[:, 1] - 0.95 - y[c])
            if node_island is not None:
                d = np.where(node_island == isl[c], d, np.inf)
            k = int(np.argmin(d))
            if d[k] <= NODE_SNAP:
                link['land_node'] = k
        out.append(link)
    return out


def sea_spawns(level_xml_text):
    """[(name, vehicle blueprint, team, (x, y, z))] of the level's VehicleSpawnEntityData whose vehicle is a sea
    vehicle (blueprint path through vehicles/sea, or a known boat name)."""
    from mohcore import xmlinst
    out = []
    for _typ, _guid, body in xmlinst.instances(level_xml_text, '.VehicleSpawnEntityData'):
        ref = xmlinst.field_ref(body, 'Vehicle') or ''
        if not re.search(r'vehicles/sea/|/pbl|/pwc|zodiac|lcrs|rhib', ref, re.I):
            continue
        t = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        name = re.search(r'<field name="Name">([^<]*)</field>', body)
        team = re.search(r'<field name="Team">([^<]*)</field>', body)
        if t:
            v = [float(a) for a in t.group(1).split('/')]
            out.append((name.group(1) if name else '', ref.rsplit('/', 1)[0].split('/')[-1], team.group(1) if team else '',
                        tuple(v[12:15])))
    return out


# --------------------------------------------------------------------------------------------- the build

@dataclass
class WaterMesh:
    level: str
    profile: Profile
    grid: Grid
    flags: np.ndarray
    surface: np.ndarray
    region: np.ndarray
    region_sizes: list
    clear: np.ndarray
    nodes: list                          # (i, j)
    node_region: list
    edges: list                          # (a, b)
    repairs: int = 0
    shore: list = field(default_factory=list)
    spawns: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    infantry: dict = None

    def node_pos(self, k):
        i, j = self.nodes[k]
        return (self.grid.x0 + i * self.grid.pitch, float(self.surface[j, i]), self.grid.z0 + j * self.grid.pitch)

    def edge_cost(self, a, b):
        pa, pb = self.node_pos(a), self.node_pos(b)
        return math.dist(pa, pb)


def build(surface, bed, grid, profile, extra_blocked=None, level='', log=print):
    """The mesh from the water surface and terrain bed per cell ((nz, nx) arrays) and further blocked cells
    (collision in the hull band). The shore links and spawns are added by the caller."""
    water = np.isfinite(surface) & (bed < surface)
    dry = np.isfinite(surface) & ~water
    blocked = water & (bed > surface - profile.draft)
    if extra_blocked is not None:
        blocked |= water & extra_blocked
    nav = water & ~blocked
    stop = ~nav                                                       # dry land, no water, blocked: all stop a hull
    free = nav & ~dilate(stop, disk(profile.radius / grid.pitch))
    # the box edge counts as open water, not as an obstacle: cells near it stay free when the water goes on
    region, sizes = regions_of(free)
    clear = clearance(free, grid.pitch)
    nodes, node_region, edges, repairs = build_graph(free, region, clear, grid, surface)
    flags = (water * F_WATER | blocked * F_BLOCKED | nav * F_NAV | free * F_FREE | dry * F_DRY).astype(np.uint8)
    wm = WaterMesh(level, profile, grid, flags, surface.astype(np.float32), region, sizes, clear, nodes, node_region,
                   edges, repairs)
    a = grid.pitch ** 2
    wm.stats = {'cells': int(grid.nx * grid.nz), 'water_cells': int(water.sum()), 'water_m2': round(float(water.sum() * a)),
                'shallow_cells': int((water & (bed > surface - profile.draft)).sum()),
                'collision_cells': int((water & extra_blocked & ~(bed > surface - profile.draft)).sum()) if extra_blocked is not None else 0,
                'navigable_cells': int(nav.sum()), 'free_cells': int(free.sum()),
                'free_pct_of_water': round(100.0 * free.sum() / max(water.sum(), 1), 1),
                'navigable_pct_of_water': round(100.0 * nav.sum() / max(water.sum(), 1), 1),
                'regions': len(sizes), 'largest_region_pct_of_free': round(100.0 * sizes[0] / max(free.sum(), 1), 1) if sizes else 0.0,
                'nodes': len(nodes), 'edges': len(edges), 'repair_paths': repairs}
    return wm


def validate(wm):
    """Structural checks of a mesh: every node on a free cell of its region, every edge in sight over free cells and
    inside one region, each region's graph connected, every free cell within sight of a node (sampled). Returns
    (ok, figures)."""
    free = (wm.flags & F_FREE) != 0
    bad_nodes = sum(1 for (i, j), r in zip(wm.nodes, wm.node_region) if not free[j, i] or wm.region[j, i] != r)
    bad_edges = sum(1 for a, b in wm.edges if wm.node_region[a] != wm.node_region[b] or not line_free(free, wm.nodes[a], wm.nodes[b]))
    uf = UnionFind(len(wm.nodes))
    for a, b in wm.edges:
        uf.union(a, b)
    comps = {}
    for k, r in enumerate(wm.node_region):
        comps.setdefault(r, set()).add(uf.find(k))
    split = sum(1 for r in comps if len(comps[r]) > 1)
    no_node = sum(1 for r in range(len(wm.region_sizes)) if r not in comps)
    # sight coverage on a sample of free cells: a node of the same region in straight sight within 3 blocks
    js, is_ = np.nonzero(free)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(js), size=min(2000, len(js)), replace=False) if len(js) else []
    by_cell = np.array(wm.nodes) if wm.nodes else np.zeros((0, 2), int)
    seen = 0
    for k in pick:
        c = (int(is_[k]), int(js[k]))
        r = wm.region[c[1], c[0]]
        d = np.abs(by_cell - np.array(c)).max(1) if len(by_cell) else np.zeros(0)
        near = [m for m in np.argsort(d)[:24] if d[m] <= 3 * BLOCK and wm.node_region[m] == r]
        if any(line_free(free, c, wm.nodes[m]) for m in near):
            seen += 1
    figures = {'nodes_off_free': bad_nodes, 'edges_blocked_or_cross_region': bad_edges, 'regions_with_split_graph': split,
               'regions_without_node': no_node, 'free_cells_sampled': len(pick),
               'free_cells_seeing_a_node_pct': round(100.0 * seen / max(len(pick), 1), 1)}
    ok = bad_nodes == 0 and bad_edges == 0 and split == 0 and no_node == 0
    return ok, figures


def nearest_node(wm, i, j, region=None, sight=True):
    free = (wm.flags & F_FREE) != 0
    best, bd = None, float('inf')
    for k, (a, b) in enumerate(wm.nodes):
        if region is not None and wm.node_region[k] != region:
            continue
        d = math.hypot(a - i, b - j)
        if d < bd and (not sight or d > 3 * BLOCK or line_free(free, (i, j), (a, b))):
            best, bd = k, d
    if best is None and sight:
        return nearest_node(wm, i, j, region, sight=False)
    return best


def attach(wm, links, spawns):
    """Boat nodes for the shore links, cells / regions / nodes for the spawns; ids."""
    free = (wm.flags & F_FREE) != 0
    for n, l in enumerate(links):
        l['id'] = n
        i, j = l.pop('boat_cell')
        l['boat_node'] = nearest_node(wm, i, j, l['region'])
    wm.shore = links
    g = wm.grid
    out = []
    for name, veh, team, (x, y, z) in spawns:
        i, j = g.cell_of(x, z)
        rec = {'name': name, 'vehicle': veh, 'team': team, 'pos': [round(x, 2), round(y, 2), round(z, 2)],
               'cell_free': False, 'region': None, 'node': None, 'launch': None, 'launch_distance_m': None}
        if g.inside(i, j):
            i, j = int(i), int(j)
            rec['cell_free'] = bool(free[j, i])
            if not free[j, i]:
                r = int(math.ceil(SPAWN_LAUNCH / g.pitch))
                best = None
                for b in range(max(0, j - r), min(g.nz, j + r + 1)):
                    for a in range(max(0, i - r), min(g.nx, i + r + 1)):
                        if free[b, a]:
                            d = math.hypot(a - i, b - j) * g.pitch
                            if d <= SPAWN_LAUNCH and (best is None or d < best[0]):
                                best = (d, a, b)
                if best:
                    _, i, j = best
                    rec['launch_distance_m'] = round(best[0], 2)
            if free[j, i]:
                rec['region'] = int(wm.region[j, i])
                rec['node'] = nearest_node(wm, i, j, rec['region'])
                rec['launch'] = [round(g.x0 + i * g.pitch, 2), round(float(wm.surface[j, i]), 2), round(g.z0 + j * g.pitch, 2)]
                rec['launch_distance_m'] = rec['launch_distance_m'] or 0.0
        out.append(rec)
    wm.spawns = out


def dijkstra(n, adj, src):
    dist = [math.inf] * n
    dist[src] = 0.0
    h = [(0.0, src)]
    while h:
        d, a = heapq.heappop(h)
        if d > dist[a]:
            continue
        for b, c in adj[a]:
            if d + c < dist[b]:
                dist[b] = d + c
                heapq.heappush(h, (d + c, b))
    return dist


def adjacency(wm):
    adj = [[] for _ in wm.nodes]
    for a, b in wm.edges:
        c = wm.edge_cost(a, b)
        adj[a].append((b, c))
        adj[b].append((a, c))
    return adj


def route_check(wm):
    """From each attached spawn: how many shore links its boat reaches over the graph, and the route length to
    the nearest and farthest one (m)."""
    adj = adjacency(wm)
    out = []
    for s in wm.spawns:
        if s['node'] is None:
            out.append({'spawn': s['name'], 'reached': 0, 'links': len(wm.shore)})
            continue
        d = dijkstra(len(wm.nodes), adj, s['node'])
        ds = [d[l['boat_node']] for l in wm.shore if l['boat_node'] is not None]
        fin = [x for x in ds if math.isfinite(x)]
        out.append({'spawn': s['name'], 'pos': s['pos'], 'region': s['region'], 'reached': len(fin), 'links': len(wm.shore),
                    'nearest_m': round(min(fin), 1) if fin else None, 'farthest_m': round(max(fin), 1) if fin else None})
    return out


# ------------------------------------------------------------------------------------------- the side file

def _plain(o):
    """numpy scalars as plain numbers in JSON."""
    if hasattr(o, 'item'):
        return o.item()
    raise TypeError(f'{type(o).__name__} is not JSON serializable')


def _b64(a):
    return base64.b64encode(zlib.compress(np.ascontiguousarray(a).tobytes(), 9)).decode('ascii')


def _unb64(s, dtype, shape):
    return np.frombuffer(zlib.decompress(base64.b64decode(s)), dtype=dtype).reshape(shape)


def to_json(wm):
    g, p = wm.grid, wm.profile
    nodes = []
    for k, (i, j) in enumerate(wm.nodes):
        x, y, z = wm.node_pos(k)
        nodes.append([round(x, 2), round(y, 2), round(z, 2), wm.node_region[k], round(float(wm.clear[j, i]), 2)])
    counts = {}
    for r in wm.node_region:
        counts[r] = counts.get(r, 0) + 1
    return {'format': FORMAT, 'version': VERSION, 'level': wm.level,
            'profile': {'name': p.name, 'radius': p.radius, 'draft': p.draft, 'height': p.height, 'speed': p.speed},
            'pitch': g.pitch, 'origin': [g.x0, g.z0], 'nx': g.nx, 'nz': g.nz,
            'grids': {'flags': _b64(wm.flags.astype('<u1')), 'surface': _b64(wm.surface.astype('<f4')),
                      'region': _b64(wm.region.astype('<i4'))},
            'regions': [{'id': r, 'cells': n, 'area_m2': round(n * g.pitch ** 2), 'nodes': counts.get(r, 0)}
                        for r, n in enumerate(wm.region_sizes)],
            'nodes': nodes, 'edges': [[a, b, round(wm.edge_cost(a, b), 2)] for a, b in wm.edges],
            'shore_links': wm.shore, 'spawns': wm.spawns, 'infantry': wm.infantry, 'stats': wm.stats}


def from_json(d):
    """A side file back as a WaterMesh (clearance is not stored: zeros)."""
    if d.get('format') != FORMAT:
        raise ValueError('not an opfor-watermesh file')
    g = Grid(d['pitch'], d['origin'][0], d['origin'][1], d['nx'], d['nz'])
    shape = (g.nz, g.nx)
    p = d['profile']
    prof = Profile(p['name'], p['radius'], p['draft'], p['height'], p['speed'])
    nodes = [tuple(int(v) for v in g.cell_of(n[0], n[2])) for n in d['nodes']]
    wm = WaterMesh(d['level'], prof, g, _unb64(d['grids']['flags'], '<u1', shape).copy(),
                   _unb64(d['grids']['surface'], '<f4', shape).copy(), _unb64(d['grids']['region'], '<i4', shape).astype(np.int64),
                   [r['cells'] for r in d['regions']], np.zeros(shape), nodes, [int(n[3]) for n in d['nodes']],
                   [(int(a), int(b)) for a, b, _ in d['edges']], 0, d['shore_links'], d['spawns'], d['stats'], d.get('infantry'))
    return wm


def picture(wm, path, title=''):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    g = wm.grid
    f = wm.flags
    img = np.zeros(f.shape + (3,))
    img[:] = (0.93, 0.93, 0.90)
    img[(f & F_DRY) != 0] = (0.80, 0.76, 0.66)
    img[(f & F_WATER) != 0] = (0.62, 0.74, 0.86)
    img[(f & F_BLOCKED) != 0] = (0.85, 0.45, 0.35)
    img[(f & F_FREE) != 0] = (0.30, 0.52, 0.80)
    ext = (g.x0 - g.pitch / 2, g.x0 + (g.nx - 0.5) * g.pitch, g.z0 - g.pitch / 2, g.z0 + (g.nz - 0.5) * g.pitch)
    fig, ax = plt.subplots(figsize=(8, 8 * g.nz / max(g.nx, 1) if g.nx else 8), dpi=110)
    ax.imshow(img, origin='lower', extent=ext, interpolation='nearest')
    P = np.array([wm.node_pos(k) for k in range(len(wm.nodes))]) if wm.nodes else np.zeros((0, 3))
    for a, b in wm.edges:
        ax.plot([P[a, 0], P[b, 0]], [P[a, 2], P[b, 2]], color=(0.1, 0.2, 0.45), lw=0.4)
    if len(P):
        ax.plot(P[:, 0], P[:, 2], '.', ms=1.5, color=(0.05, 0.1, 0.3))
    for l in wm.shore:
        ax.plot([l['boat'][0], l['land'][0]], [l['boat'][2], l['land'][2]], '-', color='#1b7f3a' if l['kind'] == 'beach' else '#a0522d', lw=1.2)
        ax.plot(l['land'][0], l['land'][2], 'o', ms=2.5, color='#1b7f3a' if l['kind'] == 'beach' else '#a0522d')
    for s in wm.spawns:
        ax.plot(s['pos'][0], s['pos'][2], '*', ms=9, color='#d4a017', mec='k', mew=0.5)
    ax.set_title(title or f'{wm.level} boat mesh ({wm.profile.name})', fontsize=9)
    ax.set_xlabel('x (m)')
    ax.set_ylabel('z (m)')
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------------------------- the level

def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def level_run(root, lv, profile, pitch=2.0, box=None, infantry=None, out_dir=None, use_objects=True,
              use_samples=True, log=print):
    from mohcore import level as levelmod, navdata
    import navparity
    import navsample
    t0 = time.time()
    out_dir = out_dir or os.path.join(root, 'work', 'navwater', lv)
    os.makedirs(out_dir, exist_ok=True)
    w = levelmod.water_of(root, lv, log=log)
    if w is None or not len(w):
        log(f'{lv}: no water')
        return None
    hf = levelmod.heightfield_of(root, lv, log=log)
    samples = None
    for name in (f'{lv}_navkit.bin', f'{lv}_full.bin'):
        p = os.path.join(root, 'work', 'navsample', name)
        if use_samples and os.path.isfile(p):
            samples, _ = navsample.load(p)
            break
    if box is None:
        if samples is not None:
            box = (samples.min_x, samples.min_z, samples.min_x + (samples.nx - 1) * samples.pitch,
                   samples.min_z + (samples.nz - 1) * samples.pitch)
        elif hf is not None:
            box = (hf.min[0], hf.min[2], hf.max[0], hf.max[2])
        else:
            v = w.vertices
            box = (v[:, 0].min(), v[:, 2].min(), v[:, 0].max(), v[:, 2].max())
    grid = make_grid(box, pitch)
    log(f'{lv}: box x {box[0]:.0f}..{box[2]:.0f}, z {box[1]:.0f}..{box[3]:.0f}, {grid.nx} x {grid.nz} cells of {pitch} m')
    surface = surface_heights(grid, w.vertices, w.triangles)
    bed = terrain_heights(grid, hf)
    extra = np.zeros(surface.shape, bool)
    sources = ['terrain height field']
    if samples is not None:
        n = mark_samples(grid, surface, extra, samples, profile)
        sources.append(f'in-game samples ({n:,} surfaces in the hull band)')
    if use_objects:
        tris = object_triangles(root, lv, log, cache=os.path.join(out_dir, 'object_tris.npy'))
        n = mark_triangles(grid, surface, extra, tris, profile)
        sources.append(f'object collision ({n:,} points in the hull band)')
    wm = build(surface, bed, grid, profile, extra, level=lv, log=log)
    wm.stats['obstacle_sources'] = sources
    wm.stats['box'] = [round(float(b), 1) for b in box]
    ok, figures = validate(wm)
    wm.stats['structure'] = figures
    # shore links against the infantry path data (DICE's where there is some)
    inf = infantry or navparity.reference_of(root, lv)
    links = []
    if inf and os.path.isfile(inf):
        nav = navdata.load(inf, heightfield=hf, cache_dir=os.path.join(root, 'work', 'navwater', 'cache'))
        cells = infantry_cells(nav)
        links = shore_links(grid, surface, wm.flags, wm.region, wm.clear, cells, hf, profile, nav.node_pos, nav.node_island)
        wm.infantry = {'file': os.path.relpath(inf, root), 'sha1': sha1(inf)}
        # recheck: every land end on a walkable cell of that file (navdiff's 0.8 m cells, same height)
        key = {(int(round(x / navdata.GRID)), int(round(z / navdata.GRID))): [] for x, z in cells[0]}
        for (x, z), yy in zip(cells[0], cells[1]):
            key[(int(round(x / navdata.GRID)), int(round(z / navdata.GRID)))].append(yy)
        on = sum(1 for l in links if any(abs(yy - l['land'][1]) < 0.05 for yy in
                                         key.get((int(round(l['land'][0] / navdata.GRID)), int(round(l['land'][2] / navdata.GRID))), [])))
        wm.stats['shore'] = {'links': len(links), 'beach': sum(l['kind'] == 'beach' for l in links),
                             'dock': sum(l['kind'] == 'dock' for l in links),
                             'land_on_infantry_cell_pct': round(100.0 * on / max(len(links), 1), 1),
                             'land_on_island_25plus_pct': round(100.0 * sum(l['land_island_nodes'] >= 25 for l in links) / max(len(links), 1), 1),
                             'land_with_route_node_pct': round(100.0 * sum(l['land_node'] is not None for l in links) / max(len(links), 1), 1),
                             'land_main_island_pct': round(100.0 * sum(l['land_island'] == 0 for l in links) / max(len(links), 1), 1),
                             'boat_regions_with_link': len({l['region'] for l in links})}
    else:
        log(f'{lv}: no infantry path data - no shore links')
    xml = None
    try:
        xml = open(navparity.level_xml(root, lv), encoding='utf-8', errors='replace').read()
    except (FileNotFoundError, OSError):
        pass
    attach(wm, links, sea_spawns(xml) if xml else [])
    wm.stats['spawns'] = {'sea_spawns': len(wm.spawns), 'in_box': int(sum(bool(wm.grid.inside(*wm.grid.cell_of(s['pos'][0], s['pos'][2]))) for s in wm.spawns)),
                          'attached': sum(s['node'] is not None for s in wm.spawns),
                          'regions': sorted({s['region'] for s in wm.spawns if s['region'] is not None})}
    wm.stats['routes'] = route_check(wm)
    wm.stats['structure_ok'] = ok
    wm.stats['seconds'] = round(time.time() - t0, 1)
    base = os.path.join(out_dir, f'{lv}.{profile.name}')
    json.dump(to_json(wm), open(base + '.watermesh.json', 'w'), separators=(',', ':'), default=_plain)
    json.dump({'level': lv, 'profile': profile.name, 'stats': wm.stats}, open(base + '.report.json', 'w'), indent=1, default=_plain)
    # the written file reads back the same
    back = from_json(json.load(open(base + '.watermesh.json')))
    same = np.array_equal(back.flags, wm.flags) and np.array_equal(back.region, wm.region) and back.nodes == wm.nodes \
        and back.edges == wm.edges
    wm.stats['read_back_identical'] = bool(same)
    json.dump({'level': lv, 'profile': profile.name, 'stats': wm.stats}, open(base + '.report.json', 'w'), indent=1, default=_plain)
    picture(wm, base + '.png')
    return wm


def summary_lines(wm):
    s = wm.stats
    out = [f"{wm.level} ({wm.profile.name}): water {s['water_cells']:,} cells ({s['water_m2']:,} m2) in the box; navigable "
           f"{s['navigable_pct_of_water']} %, free for the hull {s['free_pct_of_water']} % (shallow {s['shallow_cells']:,}, "
           f"collision {s['collision_cells']:,} cells); {s['regions']} regions, largest {s['largest_region_pct_of_free']} % "
           f"of the free water; {s['nodes']:,} nodes, {s['edges']:,} edges, {s['repair_paths']} repair paths",
           f"  structure {'OK' if s['structure_ok'] else 'FAILED'}: {s['structure']}; read back identical: {s.get('read_back_identical')}",
           f"  obstacles from: {', '.join(s['obstacle_sources'])}"]
    if 'shore' in s:
        out.append(f"  shore links: {s['shore']}")
    out.append(f"  sea-vehicle spawns: {s['spawns']}")
    for r in s['routes']:
        out.append(f"    {r}")
    out.append(f"  {s['seconds']} s")
    return out


def main(argv):
    args = argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if not args or args[0].startswith('--'):
        print(__doc__)
        return 1
    import navparity
    root = navparity.opfor_root(opt('--opfor'))
    lv = args[0].lower()
    profile = PROFILES[opt('--profile', 'pbl')]
    box = tuple(float(v) for v in opt('--box').split(',')) if opt('--box') else None
    wm = level_run(root, lv, profile, pitch=float(opt('--pitch', 2.0)), box=box, infantry=opt('--infantry'),
                   out_dir=opt('--out'), use_objects='--no-objects' not in args, use_samples='--no-samples' not in args)
    if wm is None:
        return 1
    for line in summary_lines(wm):
        print(line)
    return 0 if wm.stats['structure_ok'] and wm.stats.get('read_back_identical') else 1


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('navwater_mesh', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
