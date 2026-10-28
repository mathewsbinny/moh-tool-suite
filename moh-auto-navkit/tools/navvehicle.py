r"""NavKit: vehicle path data - the land-vehicle `pathdata.hpd` (pathfinding type "Vehicle", spec 2.1 / 2.2).

MOH ships two Kynapse data sets per level: `ai\levels\<level>\infantry\pathdata.hpd` and `...\vehicle\pathdata.hpd`.
The vehicle file has the infantry file's format exactly (header, sector table, route graph, runtime mesh - spec 4);
only the build settings differ (DICE's `AI/Pathfinding/Vehicles`): pitch 1 m, resolution 0.02, entity radius 2.5 m,
entity height 4 m, DistEdgeMax 50 m. This module builds it as its own file, reusing NavKit's infantry pipeline
(navbuild.stage: sectors, tiles, areas, portals, border portals, neighbour links, route graph, D / F detail) on a
vehicle walkability grid:

  1. the in-game samples (0.8 m, navkit.py sample) are measured for vehicles: the slope of each surface, the
     horizontal distance to the nearest obstacle a 4 m cylinder would touch (collision rising more than StepMax 0.4
     m and at most EntityHeight 4 m above the surface's local ground plane; objects with ExcludeFromPathfinding -
     fences, palm trees, sandbags - are crushed and do not count, DICE's `UseExclusions`), ForbidPathfinding
     surfaces (rocks, cliffs) are not ground;
  2. resampled onto the 1 m grid at whole metres (outlines at k + 0.5 m, as in DICE's vehicle files);
  3. walkable = slope <= SLOPE_MAX, no obstacle within CLEARANCE, not more than MAX_WATER_DEPTH under water;
  4. seeded from the level's land-vehicle spawn points (VehicleSpawnEntityData whose vehicle is not an aircraft,
     boat or fixed weapon) and flooded with the vehicle step rule (StepMax 0.4 m relative to the ground plane);
     only ground reached from a seed is kept (DICE's `VehicleSeeds`);
  5. optionally limited to a sector set (DICE builds vehicle data only in chosen sectors - spec 2.1; for parity the
     reference file's own sectors, `--sectors reference`);
  6. navbuild.stage with the vehicle settings (pitch 1, 50-sample tiles, radius 2.5, nodes 2 m over the ground,
     area height range ground .. ground + 3.2 m, route edges up to 50 m), written with navbuild.write_file.

Usage:
  navvehicle.py info <pathdata.hpd> ...                 vehicle-file summary (sectors, areas, nodes, settings)
  navvehicle.py build <level> [--out <file>] [--sectors reference|all|<hex,...>] [--opfor <folder>]
                                                       [--seeds land|land+static|all] [--no-detail]
  navvehicle.py compare <reference.hpd> <candidate.hpd>  parity: ground, extra ground, areas, bounds, graph, islands
  navvehicle.py parity [--levels a,b] [--opfor <folder>] [--save <name>]
                                                       build + compare on every level with DICE vehicle data and samples

Output goes to OPFOR's work\navvehicle\<level>\pathdata.hpd (never into the game folder).
Integration into navkit.py / navbuild (planned, see README "Vehicle path data"): navbuild.stage taking a ready
walkability grid (surfaces, keep, gradients) instead of loading samples and running navwalk.walkable; until then
`stage_on_grid` here substitutes those two calls for the duration of one build.
"""
import contextlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, deque

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import navsample  # noqa: E402
import navwalk  # noqa: E402

# ---------------------------------------------------------------------------------------------- settings
# DICE's AI/Pathfinding/Vehicles (PathfindingSettings + VehicleData; spec 2.1) and the rules measured on its files
PITCH = 1.0                  # Pitch 1 (no detailed zones); outlines at (k + 0.5) m - 99 % of vertex coordinates
RESOLUTION = 0.02            # mesh header +0x20 in every DICE vehicle sector
RADIUS = 2.5                 # EntityRadius / mesh header +0x40
ENTITY_HEIGHT = 4.0          # EntityHeight (cylinder)
STEP_MAX = 0.4               # StepMax / StepLength 0.4
SLOPE_MAX = 26.0             # degrees, by the surface normal. DICE's GroundSlopeMin / Max are 25 / 40; its vehicle
                             # ground stops at 25-28 deg (sp_04 open ground, share kept: 20-22.5 deg 73 %, 22.5-25
                             # 58 %, 25-27.5 29 %, 27.5-30 8 %, 30+ under 1 %) - measured, see spec 2.1
CLEARANCE = 2.5              # no obstacle within this horizontal distance (m) of the cell centre (EntityRadius)
CLEAR_LOW = STEP_MAX         # an obstacle rises more than this above the cell's local ground plane ...
CLEAR_HIGH = ENTITY_HEIGHT   # ... and at most this (higher geometry - a bridge deck, a roof - passes over)
TERRAIN_RISE = 0.8           # terrain is an obstacle only where it rises more than this above the plane (None: never)
BLOCKED_OBSTACLE = True      # a 0.8 m sample whose soldier capsule touched geometry (navsample BLOCKED) marks an
                             # obstacle 1 m over its ground in that column (thin walls fall between sample columns)
USE_EXCLUSIONS = True        # ExcludeFromPathfinding objects do not block vehicles (DICE: UseExclusions true for
                             # Vehicles, false for Infantry)
MAX_WATER_DEPTH = 1.6        # WaterLevelAdjustment -1.6 (infantry -3.5 setting, 1.5 m measured)
FORBID_TOL = 0.08            # a surface on ForbidPathfinding collision (navwalk's tolerance) is not ground
Y_OFFSET = 2.0               # route nodes (and outline heights) = ground + EntityHeight / 2: DICE's vehicle nodes sit
                             # a median 1.98-1.99 m over the terrain (sp_04, sp_006, sp_01, ONS_MP_002)
AREA_Y_MIN = 0.0             # area height range: lowest ground ...
AREA_Y_MAX = 3.2             # ... to highest ground + 0.8 x EntityHeight (DICE: max_y - (max outline - 2) median 3.20;
                             # infantry 1.52 = 0.8 x 1.9)
DIST_EDGE_MAX = 50.0         # VehicleData DistEdgeMax (the value on each sector's first node)
TILE = 50                    # samples per tile: DICE's vehicle tile lines at 48.5 / 98.5 m into the sector (sample
TILES = 3                    # -1 to 48, 49 to 98, 99 to 101), as infantry's 50 samples (38.8 / 78.8 m at 0.8)
LINK_STEP = 0.6              # area link rule (navbuild.AREA_LINK 'terrain'): raw step between terrain cells of one area
BORDER_NODE_SPACING = 10.0
SEED_KINDS = ('land',)       # which spawn kinds seed the vehicle ground (vehicle_kind)
SEED_ROADS = True            # the level's terrain splines (roads) seed it too (road_points; DICE's BuildRoads)
ROAD_STEP = 2.0              # ... a seed point every this many metres along each spline
FLOOD = 'sector'             # 'sector': each sector flooded inside its own window from its own seeds (flood_per_sector,
                             # as DICE's files show); 'level': one flood over the whole level
SEED_REACH = 3.0             # a seed takes the walkable surface nearest its point within this (m, horizontally)
SEED_DY = (-3.0, 3.0)        # ... from this far below to this far above the point
DETAIL = False               # D lines / F cells as navbuild builds them (navbuild.DETAIL_FEATURES, when present) - off:
                             # DICE's vehicle files do not follow the infantry rule (D lines mostly single, 27 % of their
                             # segments diagonal, facing pairs 163 of 7,949 lines; F directions include diagonals, no
                             # -axis / +axis twins) and their route edges cross D lines on 0.58 % (245 of 42,307), so
                             # route edges are not kept off D sides either (DETAIL_BLOCKS_EDGES off; 2026-10-26, spec 4.5)

# keywords of a vehicle blueprint path (VehicleSpawnEntityData.Vehicle) by kind; the first match wins, in this order
KINDS = (
    ('sea', ('pbl', 'boat', 'lcrs', 'pwc', 'rhib', 'jetski', 'ship', 'landingcraft')),
    ('static', ('hmg', 'gmg', 'kord', 'kornet', 'tow', 'bofors', 'zu23', 'zu-23', '30cal', 'uav_station', 'mortar',
                'stinger', 'igla', 'emplacement', 'm2_', 'mg42', 'm60_', 'pkm', 'aa_', 'flak', 'searchlight')),
    ('air', ('mi24', 'mi-24', 'mi28', 'mi-28', 'ah60', 'ah-60', 'ah64', 'ah-64', 'uh60', 'uh-60', 'a10', 'a-10',
             'f18', 'f-18', 'su39', 'su-39', 'su25', 'zero_fighter', 'corsair', 'uav', 'heli', 'ka52', 'ka-52',
             'huey', 'uh1', 'uh-1', 'mi8', 'mi-8', 'mi17', 'havoc', 'apache', 'blackhawk', 'littlebird', 'mh6',
             'ah6', 'b17', 'c130', 'plane', 'jet')),
)


def vehicle_kind(ref):
    """'land', 'air', 'sea' or 'static' (fixed weapon) from a vehicle blueprint path - by name keywords (KINDS)."""
    name = (ref or '').lower().replace('\\', '/')
    parts = name.split('/')
    if len(parts) > 2 and parts[0] == 'objects' and parts[1] == 'vehicles':
        name = '/'.join(parts[2:])
    for kind, words in KINDS:
        if any(w in name for w in words):
            return kind
    return 'land'


def vehicle_spawns(level_xml):
    """The level's VehicleSpawnEntityData: [{'pos': (x, y, z), 'vehicle': blueprint path, 'kind', 'name',
    'enabled'}] (spawns at the origin - unplaced templates - are left out)."""
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import xmlinst
    out = []
    for _typ, _guid, body in xmlinst.instances(level_xml, '.VehicleSpawnEntityData'):
        t = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        if not t:
            continue
        v = [float(a) for a in t.group(1).split('/')]
        if len(v) < 15 or not any(abs(c) > 1e-3 for c in v[12:15]):
            continue
        ref = xmlinst.field_ref(body, 'Vehicle') or ''
        nm = re.search(r'<field name="Name">([^<]*)<', body)
        en = re.search(r'<field name="Enabled">([^<]*)<', body)
        out.append({'pos': tuple(v[12:15]), 'vehicle': ref, 'kind': vehicle_kind(ref),
                    'name': nm.group(1) if nm else '', 'enabled': (en.group(1) if en else 'true').lower() != 'false'})
    return out


# ---------------------------------------------------------------------------------------------- geometry
def layered(s, cap=None):
    """Per column the surfaces as fixed layers: Y (nz, nx, L) float32 (nan where none) and K (nz, nx, L) surface
    index (-1 where none), in file order (top down)."""
    cnt = np.diff(s.start[:s.nx * s.nz + 1])
    L = int(cnt.max()) if len(cnt) else 1
    L = max(1, min(L, cap or L))
    Y = np.full((s.nz, s.nx, L), np.nan, np.float32)
    K = np.full((s.nz, s.nx, L), -1, np.int64)
    col = np.repeat(np.arange(s.nx * s.nz), cnt)
    rank = np.arange(len(col)) - s.start[col]
    ok = rank < L
    jj, ii = col // s.nx, col % s.nx
    Y[jj[ok], ii[ok], rank[ok]] = s.y[:len(col)][ok]
    K[jj[ok], ii[ok], rank[ok]] = np.nonzero(ok)[0]
    return Y, K


def gradients(s):
    ny = np.maximum(s.normal[:, 1], 0.05)
    return -s.normal[:, 0] / ny, -s.normal[:, 2] / ny


def tris_columns(s, tris, grow=0.0):
    """Pairs (triangle index, column index) for every sample column inside a triangle's xz bounding box (+ grow)."""
    if tris is None or not len(tris):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    t = np.asarray(tris, np.float64)
    i0 = np.ceil((t[:, :, 0].min(1) - grow - s.min_x) / s.pitch).astype(np.int64)
    i1 = np.floor((t[:, :, 0].max(1) + grow - s.min_x) / s.pitch).astype(np.int64)
    j0 = np.ceil((t[:, :, 2].min(1) - grow - s.min_z) / s.pitch).astype(np.int64)
    j1 = np.floor((t[:, :, 2].max(1) + grow - s.min_z) / s.pitch).astype(np.int64)
    i0, j0 = np.maximum(i0, 0), np.maximum(j0, 0)
    i1, j1 = np.minimum(i1, s.nx - 1), np.minimum(j1, s.nz - 1)
    w, h = i1 - i0 + 1, j1 - j0 + 1
    ok = (w > 0) & (h > 0)
    idx = np.nonzero(ok)[0]
    n = (w * h)[ok]
    tri = np.repeat(idx, n)
    k = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    ww = np.repeat(w[ok], n)
    ci = np.repeat(i0[ok], n) + k % ww
    cj = np.repeat(j0[ok], n) + k // ww
    return tri, cj * s.nx + ci


def on_tris(s, tris, tol):
    """Surfaces lying on one of the triangles (within tol vertically at the column point) - bool per surface; the
    vectorised form of navwalk.on_objects."""
    out = np.zeros(len(s.y), bool)
    tri, col = tris_columns(s, tris)
    if not len(tri):
        return out
    t = np.asarray(tris, np.float64)
    for a in range(0, len(tri), 2_000_000):
        tr, cc = tri[a:a + 2_000_000], col[a:a + 2_000_000]
        A, B, C = t[tr, 0], t[tr, 1], t[tr, 2]
        px = s.min_x + (cc % s.nx) * s.pitch
        pz = s.min_z + (cc // s.nx) * s.pitch
        det = (B[:, 2] - C[:, 2]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 2] - C[:, 2])
        good = np.abs(det) > 1e-9
        det = np.where(good, det, 1.0)
        l1 = ((B[:, 2] - C[:, 2]) * (px - C[:, 0]) + (C[:, 0] - B[:, 0]) * (pz - C[:, 2])) / det
        l2 = ((C[:, 2] - A[:, 2]) * (px - C[:, 0]) + (A[:, 0] - C[:, 0]) * (pz - C[:, 2])) / det
        l3 = 1 - l1 - l2
        inside = good & (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        yt = l1 * A[:, 1] + l2 * B[:, 1] + l3 * C[:, 1]
        cc, yt = cc[inside], yt[inside]
        cnt = s.start[cc + 1] - s.start[cc]
        rep = np.repeat(np.arange(len(cc)), cnt)
        k = s.start[cc][rep] + (np.arange(len(rep)) - np.repeat(np.cumsum(cnt) - cnt, cnt))
        hit = np.abs(s.y[k].astype(np.float64) - yt[rep]) <= tol
        out[k[hit]] = True
    return out


def near_tris(s, tris, grow=0.5, below=2.2, above=0.3):
    """Surfaces within `grow` m (horizontally) of a triangle's bounding box, from `below` under to `above` over it -
    the vectorised form of navwalk.near_objects."""
    out = np.zeros(len(s.y), bool)
    tri, col = tris_columns(s, tris, grow)
    if not len(tri):
        return out
    t = np.asarray(tris, np.float32)
    y0 = t[:, :, 1].min(1) - below
    y1 = t[:, :, 1].max(1) + above
    for a in range(0, len(tri), 4_000_000):
        tr, cc = tri[a:a + 4_000_000], col[a:a + 4_000_000]
        cnt = s.start[cc + 1] - s.start[cc]
        rep = np.repeat(np.arange(len(cc)), cnt)
        k = s.start[cc][rep] + (np.arange(len(rep)) - np.repeat(np.cumsum(cnt) - cnt, cnt))
        yk = s.y[k]
        hit = (yk >= y0[tr][rep]) & (yk <= y1[tr][rep])
        out[k[hit]] = True
    return out


def obstacle_distance(s, cand, solid, extra=None, gx=None, gz=None, lo=CLEAR_LOW, hi=CLEAR_HIGH, radius=CLEARANCE + 1.0):
    """Per surface: the horizontal distance (m) to the nearest sample column holding an obstacle for a vehicle on it -
    a solid surface (or an `extra` obstacle height, per surface: nan for none) rising more than lo and at most hi above
    the surface's local ground plane at that column. Computed for `cand` surfaces only (99 elsewhere); capped at
    radius (distances beyond it are 99)."""
    if gx is None:
        gx, gz = gradients(s)
    out = np.full(len(s.y), 99.0, np.float32)
    p = s.pitch
    r = int(math.ceil(radius / p))
    offs = sorted((math.hypot(a, b) * p, a, b) for a in range(-r, r + 1) for b in range(-r, r + 1)
                  if 0 < math.hypot(a, b) * p <= radius)
    # the column itself: geometry above the surface within the band (an overhang) - distance 0
    offs = [(0.0, 0, 0)] + offs
    cnt = np.diff(s.start[:s.nx * s.nz + 1])
    band = 96
    for j0 in range(0, s.nz, band):
        # rows j0 .. j1 - 1 and their r-row margins, as fixed layers trimmed to this band's deepest column
        a0, a1 = max(0, j0 - r), min(s.nz, j0 + band + r)
        c0, c1 = a0 * s.nx, a1 * s.nx
        L = max(1, int(cnt[c0:c1].max()))
        nzb = a1 - a0
        col = np.repeat(np.arange(c0, c1), cnt[c0:c1])
        kk = np.arange(s.start[c0], s.start[c1])
        rank = kk - s.start[col]
        jj, ii = col // s.nx - a0, col % s.nx
        Y = np.full((nzb, s.nx, L), np.nan, np.float32)
        K = np.full((nzb, s.nx, L), -1, np.int64)
        Y[jj, ii, rank] = s.y[kk]
        K[jj, ii, rank] = kk
        valid = K >= 0
        ks = np.where(valid, K, 0)
        O = np.where(valid & solid[ks], Y, np.nan)
        if extra is not None:
            O = np.concatenate([O, np.where(valid, extra[ks], np.nan).astype(np.float32)], axis=2)
        used = ~np.all(np.isnan(O), axis=(0, 1))
        O = O[:, :, used] if used.any() else O[:, :, :1]
        # candidates: only the band's own rows
        own = np.zeros((nzb, 1, 1), bool)
        own[j0 - a0:min(j0 + band, s.nz) - a0] = True
        C = valid & cand[ks] & own
        if not C.any():
            continue
        GX = np.where(valid, gx[ks], 0).astype(np.float32)
        GZ = np.where(valid, gz[ks], 0).astype(np.float32)
        D = np.full(Y.shape, 99.0, np.float32)
        P = np.pad(O, ((r, r), (r, r), (0, 0)), constant_values=np.nan)
        rows = [l for l in range(L) if C[:, :, l].any()]
        for d, a, b in offs:
            sh = P[r + b:r + b + nzb, r + a:r + a + s.nx, :]
            for l in rows:
                todo = C[:, :, l] & (D[:, :, l] > d)
                if not todo.any():
                    continue
                base = Y[:, :, l] + GX[:, :, l] * (a * p) + GZ[:, :, l] * (b * p)
                with np.errstate(invalid='ignore'):
                    diff = sh - base[:, :, None]
                    hit = np.any((diff > lo) & (diff <= hi), axis=2) & todo
                D[:, :, l][hit] = d
        sel = C
        out[K[sel]] = D[sel]
    return out


def resample(s, pitch=PITCH):
    """The surfaces on a grid of the given pitch at whole multiples of it: each new column takes every surface of the
    nearest sample column, its height moved along its own ground plane by the offset (at most half the old pitch on
    each axis). Returns (Samples at `pitch`, source surface index per new surface)."""
    x0 = math.ceil(s.min_x / pitch - 1e-6) * pitch
    z0 = math.ceil(s.min_z / pitch - 1e-6) * pitch
    nx = int(math.floor((s.min_x + (s.nx - 1) * s.pitch - x0) / pitch + 1e-6)) + 1
    nz = int(math.floor((s.min_z + (s.nz - 1) * s.pitch - z0) / pitch + 1e-6)) + 1
    X = x0 + np.arange(nx) * pitch
    Z = z0 + np.arange(nz) * pitch
    si = np.clip(np.rint((X - s.min_x) / s.pitch).astype(np.int64), 0, s.nx - 1)
    sj = np.clip(np.rint((Z - s.min_z) / s.pitch).astype(np.int64), 0, s.nz - 1)
    dx = X - (s.min_x + si * s.pitch)
    dz = Z - (s.min_z + sj * s.pitch)
    src_col = (sj[:, None] * s.nx + si[None, :]).ravel()
    off_x = np.broadcast_to(dx[None, :], (nz, nx)).ravel()
    off_z = np.broadcast_to(dz[:, None], (nz, nx)).ravel()
    cnt = s.start[src_col + 1] - s.start[src_col]
    start = np.zeros(nx * nz + 1, np.int64)
    np.cumsum(cnt, out=start[1:])
    rep = np.repeat(np.arange(nx * nz), cnt)
    src = s.start[src_col][rep] + (np.arange(int(start[-1])) - np.repeat(start[:-1], cnt))
    gx, gz = gradients(s)
    y = (s.y[src] + gx[src] * off_x[rep] + gz[src] * off_z[rep]).astype(np.float32)
    hit = s.hit[src] if s.hit is not None and len(s.hit) else None
    out = navsample.Samples(pitch, float(x0), float(z0), nx, nz, s.y_top, s.y_bottom, s.radius, s.cap_bottom,
                            s.cap_top, s.ray_radius, start, y, s.normal[src], s.flags[src], hit)
    return out, src


def water_depth(s, water):
    """Per surface: depth under the level's water (0 where dry or no water)."""
    if water is None:
        return np.zeros(len(s.y), np.float32)
    import navbuild
    top = navbuild.water_tops(s, water[0], water[1])
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    d = top[col] - s.y
    return np.where(np.isfinite(d) & (d > 0), d, 0).astype(np.float32)


# ---------------------------------------------------------------------------------------------- walkability
def vehicle_grid(s8, forbid_tris=None, exclude_tris=None, dynamic_tris=None, water=None, log=print, stats=None):
    """The 1 m vehicle grid with each surface's measures: returns (s1, measures) with measures = dict of per-surface
    arrays on s1: slope (deg), clear (obstacle distance, m), forbid (bool), depth (m under water), terrain (bool)."""
    t0 = time.time()
    gx, gz = gradients(s8)
    ny = np.clip(s8.normal[:, 1], -1, 1)
    slope = np.degrees(np.arccos(ny)).astype(np.float32)
    terrain = (s8.hit[:, 0] == navwalk.TERRAIN_SHAPE_VTABLE) if s8.hit is not None and len(s8.hit) else np.ones(len(s8.y), bool)
    character = (s8.hit[:, 0] == navwalk.CHARACTER_SHAPE_VTABLE) if s8.hit is not None and len(s8.hit) else np.zeros(len(s8.y), bool)
    forbid = on_tris(s8, forbid_tris, FORBID_TOL) if forbid_tris is not None and len(forbid_tris) else np.zeros(len(s8.y), bool)
    absent = character.copy()                                    # soldiers stood in the level while it was sampled
    if dynamic_tris is not None and len(dynamic_tris):
        absent |= on_tris(s8, dynamic_tris, 0.2)                 # dynamic props are absent for path data (as infantry)
    crushed = np.zeros(len(s8.y), bool)
    if USE_EXCLUSIONS and exclude_tris is not None and len(exclude_tris):
        crushed = on_tris(s8, exclude_tris, 0.2)                 # ExcludeFromPathfinding: no obstacle for vehicles
    log(f'  objects: forbidden surfaces {int(forbid.sum())}, absent {int(absent.sum())}, crushed {int(crushed.sum())} '
        f'({time.time() - t0:.0f} s)')
    cand = (slope <= SLOPE_MAX + 2.0) & ~forbid & ~absent & ~crushed
    solid = ~absent & ~crushed
    extra = None
    if BLOCKED_OBSTACLE:
        blocked = (s8.flags & navsample.BLOCKED) > 0
        free = np.zeros(len(s8.y), bool)
        if dynamic_tris is not None and len(dynamic_tris):
            free |= near_tris(s8, dynamic_tris)
        if USE_EXCLUSIONS and exclude_tris is not None and len(exclude_tris):
            free |= near_tris(s8, exclude_tris)
        if character.any():
            free |= navwalk.near_characters(s8, character)
        extra = np.where(blocked & ~free, s8.y + 1.0, np.nan).astype(np.float32)
    # objects (and blocked samples) count from a rise of StepMax; terrain only from TERRAIN_RISE - the heightfield's
    # own curvature within the cylinder's reach is no obstacle to DICE's vehicles (banks of sunken roads, valley floors)
    clear = obstacle_distance(s8, cand, solid & ~terrain, extra, gx, gz)
    if TERRAIN_RISE is not None:
        clear = np.minimum(clear, obstacle_distance(s8, cand, solid & terrain, None, gx, gz, lo=TERRAIN_RISE))
    log(f'  obstacle distances ({time.time() - t0:.0f} s)')
    s1, src = resample(s8, PITCH)
    m = {'slope': slope[src], 'clear': clear[src], 'forbid': forbid[src] | absent[src] | crushed[src],
         'terrain': terrain[src], 'depth': water_depth(s1, water)}
    if stats is not None:
        stats['surfaces_08'] = int(len(s8.y))
        stats['surfaces_1m'] = int(len(s1.y))
    return s1, m


def candidates(m, slope_max=None, clearance=None, max_depth=None):
    slope_max = SLOPE_MAX if slope_max is None else slope_max
    clearance = CLEARANCE if clearance is None else clearance
    max_depth = MAX_WATER_DEPTH if max_depth is None else max_depth
    return (m['slope'] <= slope_max) & (m['clear'] > clearance) & ~m['forbid'] & (m['depth'] <= max_depth)


def seed_surfaces(s, cand, points, reach=SEED_REACH, dy=SEED_DY):
    """For each point: the candidate surface nearest it (horizontally) within reach and dy, or -1."""
    out = []
    r = int(math.ceil(reach / s.pitch))
    for p in points:
        gi, gj = int(round((p[0] - s.min_x) / s.pitch)), int(round((p[2] - s.min_z) / s.pitch))
        best, bd = -1, None
        for dj in range(-r, r + 1):
            for di in range(-r, r + 1):
                i, j = gi + di, gj + dj
                if not (0 <= i < s.nx and 0 <= j < s.nz):
                    continue
                d = math.hypot(di, dj) * s.pitch
                if d > reach:
                    continue
                c = j * s.nx + i
                for k in range(s.start[c], s.start[c + 1]):
                    if cand[k] and dy[0] <= s.y[k] - p[1] <= dy[1]:
                        key = (d, abs(float(s.y[k]) - p[1]))
                        if bd is None or key < bd:
                            best, bd = k, key
        out.append(best)
    return out


def road_points(level_xml, step=ROAD_STEP):
    """Seed points along the level's terrain splines (Terrain.TerrainSplineData: its Points, TerrainSplinePointData
    positions, in order), every `step` m along each polyline - DICE's `BuildRoads`: of the sectors with vehicle ground
    in DICE's files nearly all hold a spline control point or a vehicle spawn, the empty ones mostly neither (spec 2.2)."""
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import xmlinst
    pos = {}
    for _t, g, body in xmlinst.instances(level_xml, 'Terrain.TerrainSplinePointData'):
        p = re.search(r'<complex name="Position">([^<]*)</complex>', body)
        if p:
            pos[g.lower()] = [float(a) for a in p.group(1).split('/')[:3]]
    out = []
    for _t, _g, body in xmlinst.instances(level_xml, 'Terrain.TerrainSplineData'):
        if '<array name="Points">' not in body:
            continue
        arr = body.split('<array name="Points">', 1)[1].split('</array>', 1)[0]
        pts = [pos[r.lower()] for r in re.findall(r'<item ref="([^"]*)"', arr) if r.lower() in pos]
        for a, b in zip(pts[:-1], pts[1:]):
            n = max(1, int(math.hypot(b[0] - a[0], b[2] - a[2]) / step))
            for t in range(n + (1 if b is pts[-1] else 0)):
                f = t / n
                out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f))
        if len(pts) == 1:
            out.append(tuple(pts[0]))
    return out


def flood_per_sector(s, cand, seeds, xoff, zoff, step=None, gx=None, gz=None, mode='sector'):
    """FLOOD 'sector': each sector's ground is flooded only inside its own window (samples -1 .. 101, the sector plus
    its overlap with each neighbour) from the seeds in that window - DICE's vehicle ground does not continue into a
    neighbouring sector unless that sector has a seed of its own (sp_04: sector 0x0a09 holds no seed and no ground,
    while the ground of its neighbour reaches the shared line over smooth terrain; spec 2.2)."""
    step = STEP_MAX if step is None else step
    if gx is None:
        gx, gz = gradients(s)
    seeds = np.array([k for k in seeds if k >= 0], np.int64)
    keep = np.zeros(len(s.y), bool)
    if not len(seeds):
        return keep
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x = s.min_x + (col % s.nx) * s.pitch
    z = s.min_z + (col // s.nx) * s.pitch
    n = int(round(100 / s.pitch))
    gi = np.rint(x / s.pitch).astype(np.int64)            # world sample index
    gj = np.rint(z / s.pitch).astype(np.int64)
    sec = {(int(a), int(b)) for a, b in zip(np.floor(x[seeds] / 100.0).astype(np.int64),
                                            np.floor(z[seeds] / 100.0).astype(np.int64))}
    # a seed in another sector's overlap seeds that sector too
    for k in seeds:
        for dX in (-1, 0, 1):
            for dY in (-1, 0, 1):
                X, Y = int(math.floor(x[k] / 100.0)) + dX, int(math.floor(z[k] / 100.0)) + dY
                if X * n - 1 <= gi[k] <= X * n + n + 1 and Y * n - 1 <= gj[k] <= Y * n + n + 1:
                    sec.add((X, Y))
    if mode == 'seeded':
        # one flood over the union of the seeded sectors' windows: ground crosses between seeded sectors, never into
        # a sector without a seed of its own
        allowed = np.zeros(len(s.y), bool)
        for X, Y in sec:
            allowed |= (gi >= X * n - 1) & (gi <= X * n + n + 1) & (gj >= Y * n - 1) & (gj <= Y * n + n + 1)
        return flood(s, cand & allowed, list(seeds), step, gx, gz)
    for X, Y in sorted(sec):
        w = (gi >= X * n - 1) & (gi <= X * n + n + 1) & (gj >= Y * n - 1) & (gj <= Y * n + n + 1)
        sd = [int(k) for k in seeds if w[k]]
        if sd:
            keep |= flood(s, cand & w, sd, step, gx, gz)
    return keep


def perimeter_seeds(s, cand, terrain, xoff, zoff, sectors=None, inset=2):
    """Auto-seeds as infantry's (spec 3.5): candidate terrain surfaces on each sector's perimeter lines `inset`
    samples inside its 100 m square (2 m at pitch 1, as 1.6 m = 2 samples at 0.8). sectors: limit to these {(X, Y)}."""
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x = s.min_x + (col % s.nx) * s.pitch
    z = s.min_z + (col // s.nx) * s.pitch
    lx = np.mod(np.rint(x / s.pitch).astype(np.int64), int(round(100 / s.pitch)))
    lz = np.mod(np.rint(z / s.pitch).astype(np.int64), int(round(100 / s.pitch)))
    n = int(round(100 / s.pitch))
    on = (lx == inset) | (lx == n - inset) | (lz == inset) | (lz == n - inset)
    m = on & cand & terrain
    if sectors is not None:
        m &= in_sectors(s, sectors, xoff, zoff)
    return list(np.nonzero(m)[0])


def flood(s, cand, seeds, step=None, gx=None, gz=None):
    """Candidate surfaces reached from the seed surfaces through 8-connected neighbours by the step rule
    (navwalk.step_fits: the height change within `step` of the rise the two ground planes predict)."""
    step = STEP_MAX if step is None else step
    if gx is None:
        gx, gz = gradients(s)
    y = s.y.astype(np.float64)
    keep = np.zeros(len(y), bool)
    col_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    q = deque()
    for k in seeds:
        if k >= 0 and cand[k] and not keep[k]:
            keep[k] = True
            q.append(k)
    nbr = [(di, dj) for dj in (-1, 0, 1) for di in (-1, 0, 1) if di or dj]
    while q:
        k = q.popleft()
        c = col_of[k]
        i, j = c % s.nx, c // s.nx
        for di, dj in nbr:
            a, b = i + di, j + dj
            if not (0 <= a < s.nx and 0 <= b < s.nz):
                continue
            cc = b * s.nx + a
            for n in range(s.start[cc], s.start[cc + 1]):
                if keep[n] or not cand[n]:
                    continue
                if navwalk.step_fits(y[n] - y[k], (gx[k], gz[k]), (gx[n], gz[n]), False, False, di, dj, s.pitch, step):
                    keep[n] = True
                    q.append(n)
    return keep


def in_sectors(s, sectors, xoff, zoff):
    """bool per surface: its column lies in one of the sectors {(X, Y)} (grid ids with the level's offsets)."""
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x = s.min_x + (col % s.nx) * s.pitch
    z = s.min_z + (col // s.nx) * s.pitch
    X = np.floor(x / 100.0).astype(np.int64) - xoff
    Y = np.floor(z / 100.0).astype(np.int64) - zoff
    code = X * 4096 + Y
    allowed = np.array(sorted(a * 4096 + b for a, b in sectors), np.int64)
    return np.isin(code, allowed)


# ---------------------------------------------------------------------------------------------- the mesh / graph build
@contextlib.contextmanager
def vehicle_settings(detail=None):
    """navbuild's module settings for a vehicle build (restored afterwards)."""
    import navbuild
    names = ['PITCH', 'SECTOR_SAMPLES', 'WINDOW', 'TILE', 'TILES', 'RESOLUTION', 'LINK_STEP', 'RADIUS',
             'DIST_EDGE_MAX', 'Y_OFFSET', 'AREA_Y_MIN', 'AREA_Y_MAX', 'BORDER_NODE_SPACING', 'NODE_COVER',
             'ORDER_NODES', 'CLIP_TO_REFERENCE', 'DETAIL_FEATURES', 'DETAIL_STEP', 'DETAIL_BLOCKS_EDGES',
             'TILE_20M_SECTORS']
    saved = {n: getattr(navbuild, n) for n in names if hasattr(navbuild, n)}
    try:
        navbuild.set_pitch(PITCH)
        navbuild.TILE, navbuild.TILES = TILE, TILES
        navbuild.RESOLUTION, navbuild.LINK_STEP, navbuild.RADIUS = RESOLUTION, LINK_STEP, RADIUS
        navbuild.DIST_EDGE_MAX, navbuild.Y_OFFSET = DIST_EDGE_MAX, Y_OFFSET
        navbuild.AREA_Y_MIN, navbuild.AREA_Y_MAX = AREA_Y_MIN, AREA_Y_MAX
        navbuild.BORDER_NODE_SPACING = BORDER_NODE_SPACING
        navbuild.NODE_COVER = {'detailed': None, 'normal': None}
        navbuild.ORDER_NODES = False
        navbuild.CLIP_TO_REFERENCE = False
        navbuild.TILE_20M_SECTORS = None
        if 'DETAIL_FEATURES' in saved:
            navbuild.DETAIL_FEATURES = DETAIL if detail is None else detail
        if 'DETAIL_STEP' in saved:
            navbuild.DETAIL_STEP = dict(saved['DETAIL_STEP'], normal=LINK_STEP)
        if 'DETAIL_BLOCKS_EDGES' in saved:
            navbuild.DETAIL_BLOCKS_EDGES = False   # DICE's vehicle edges cross D lines (0.58 %; infantry 0.004 %)
        yield navbuild
    finally:
        for n, v in saved.items():
            setattr(navbuild, n, v)


class _Shim:
    """A stand-in for a module inside navbuild for one call: the given attributes, the rest from the real module."""
    def __init__(self, real, **over):
        self._real, self._over = real, over

    def __getattr__(self, name):
        return self._over[name] if name in self._over else getattr(self._real, name)


def stage_on_grid(s, keep, xoff, zoff, log=print, detail=None):
    """navbuild.stage on a ready walkability grid: the surfaces `s` (Samples at PITCH) and the kept ones. The stage's
    sample loading and infantry walkability (navsample.load, navwalk.walkable) are replaced for this call only.
    Returns navbuild.stage's result ('graphs' ready for navbuild.write_file)."""
    gx, gz = gradients(s)
    with vehicle_settings(detail) as nb:
        real_ns, real_nw = nb.navsample, nb.navwalk
        nb.navsample = _Shim(real_ns, load=lambda _p: (s, s.nx * s.nz))
        nb.navwalk = _Shim(real_nw, walkable=lambda *_a, **_k: (keep, gx, gz, np.zeros(len(s.y), bool)),
                           ROOF_PRUNE=False, OBJECTIVE_FLOOR=False)
        try:
            nb.ORDER_IDS.clear()
            nb.LADDER_STATS.clear()
            return nb.stage('<vehicle grid>', None, xoff, zoff, step=STEP_MAX, log=log)
        finally:
            nb.navsample, nb.navwalk = real_ns, real_nw


def write(result, out_path, log=print, t0=None):
    with vehicle_settings() as nb:
        nb.ORDER_IDS.clear()
        nb.write_file(result['graphs'], out_path, log, t0 or time.time())


# ---------------------------------------------------------------------------------------------- level inputs
def opfor_root(given=None):
    import navparity
    return navparity.opfor_root(given)


def reference_of(root, lv):
    """DICE's vehicle file of a level in the game copy (the original where something else is installed), or None."""
    d = os.path.join(root, 'game', 'Output', 'win32', 'ai', 'levels', lv, 'vehicle')
    if not os.path.isdir(d):
        return None
    fs = os.listdir(d)
    orig = [f for f in fs if f.lower().startswith('pathdata.hpd.')]
    if orig:
        return os.path.join(d, orig[0])
    return os.path.join(d, 'pathdata.hpd') if 'pathdata.hpd' in [f.lower() for f in fs] else None


def file_offsets(path):
    """The sector offsets a path-data file was built with, from its own sector ids (spec 3.9): per sector
    floor(centre / 100) - grid id, the most common value."""
    f = hpdtool.parse(open(path, 'rb').read())
    c = Counter()
    for s in f.sectors:
        X, Y = s.id >> 8, s.id & 0xFF
        b = s.bounds
        c[(math.floor((b[0] + b[1]) / 200.0) - X, math.floor((b[4] + b[5]) / 200.0) - Y)] += 1
    return c.most_common(1)[0][0] if c else None


def level_offsets(root, lv):
    """The level's vehicle SectorOffsets (Precomputed/Vehicle), else the infantry ones (navkit.json / Precomputed
    Infantry), else None."""
    import glob
    L = os.path.join(root, 'work', 'levels', lv, 'dist', 'levels')
    for kind in ('Vehicle', 'Infantry'):
        so = glob.glob(os.path.join(L, '*', 'Precomputed', kind, 'SectorOffsets.xml')) + \
            glob.glob(os.path.join(L, lv, 'Precomputed', kind, 'SectorOffsets.xml'))
        for p in so:
            if os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(p)))).lower() != lv.lower():
                continue
            t = open(p, encoding='utf-8').read()
            mx, mz = re.search(r'name="XOffset">(-?\d+)<', t), re.search(r'name="ZOffset">(-?\d+)<', t)
            if mx and mz:
                return int(mx.group(1)), int(mz.group(1))
    s = os.path.join(root, 'work', 'navkit', lv, 'navkit.json')
    if os.path.isfile(s):
        j = json.load(open(s))
        return j['xoff'], j['zoff']
    return None


def reference_sectors(path, xoff=None, zoff=None):
    """{(X, Y)} sector grid ids of a path-data file, re-based to (xoff, zoff) when given (its own offsets otherwise)."""
    f = hpdtool.parse(open(path, 'rb').read())
    own = file_offsets(path)
    out = set()
    for s in f.sectors:
        X, Y = s.id >> 8, s.id & 0xFF
        if xoff is not None:
            X, Y = X + own[0] - xoff, Y + own[1] - zoff
        out.add((X, Y))
    return out


def samples_of(root, lv):
    for name in (f'{lv}_navkit', f'{lv}_full'):
        p = os.path.join(root, 'work', 'navsample', name + '.bin')
        if os.path.isfile(p):
            return p
    return None


def build_level(root, lv, out_path=None, sectors='reference', seeds_kind=SEED_KINDS, log=print, detail=None,
                stats=None, grid_cache=None):
    """Builds a level's vehicle file from its in-game samples and level data. sectors: 'reference' (DICE's vehicle
    file's sector set - parity), 'all', or a set {(X, Y)}. Returns (out_path, stats)."""
    import navregress
    t0 = time.time()
    stats = {} if stats is None else stats
    sp = samples_of(root, lv)
    if sp is None:
        raise RuntimeError(f'{lv}: no in-game samples (work\\navsample\\{lv}_navkit.bin or _full.bin)')
    ref = reference_of(root, lv)
    offs = file_offsets(ref) if ref else level_offsets(root, lv)
    if offs is None:
        raise RuntimeError(f'{lv}: no sector offsets (no DICE vehicle file, SectorOffsets or navkit.json)')
    xoff, zoff = offs
    L = os.path.join(root, 'work', 'levels', lv)

    def tris(name):
        p = os.path.join(L, name)
        return np.load(p) if os.path.isfile(p) else None
    if grid_cache is not None and lv in grid_cache:
        s1, m = grid_cache[lv]
    else:
        s8, _ = navsample.load(sp)
        log(f'{lv}: {len(s8.y)} surfaces at {s8.pitch:g} m ({os.path.basename(sp)}); offsets {xoff},{zoff}')
        water = navregress.level_water(root, lv)
        s1, m = vehicle_grid(s8, tris('forbid_tris.npy'), tris('exclude_tris.npy'), tris('dynamic_tris.npy'), water,
                             log, stats)
        if grid_cache is not None:
            grid_cache[lv] = (s1, m)
    cand = candidates(m)
    if sectors == 'reference':
        if not ref:
            raise RuntimeError(f'{lv}: no DICE vehicle file for --sectors reference')
        sectors = reference_sectors(ref, xoff, zoff)
    if sectors not in (None, 'all'):
        cand &= in_sectors(s1, sectors, xoff, zoff)
    xml = None
    try:
        import navparity
        xml = open(navparity.level_xml(root, lv), encoding='utf-8', errors='replace').read()
    except Exception as e:                                   # noqa: BLE001 - reported, the build needs seeds
        raise RuntimeError(f'{lv}: level XML not found ({e}); run navkit.py prepare {lv}')
    spawns = vehicle_spawns(xml)
    pts = [sp_['pos'] for sp_ in spawns if sp_['kind'] in seeds_kind or 'all' in seeds_kind]
    roads = road_points(xml) if SEED_ROADS else []
    seeds = seed_surfaces(s1, cand, pts + roads)
    if FLOOD in ('sector', 'seeded'):
        keep = flood_per_sector(s1, cand, seeds, xoff, zoff, mode=FLOOD)
    else:
        keep = flood(s1, cand, seeds)
    stats.update({'spawns': len(spawns), 'seed_points': len(pts), 'road_points': len(roads),
                  'seeded': sum(1 for k in seeds if k >= 0), 'candidates': int(cand.sum()), 'kept': int(keep.sum())})
    log(f'{lv}: vehicle spawns {len(spawns)} ({dict(Counter(s_["kind"] for s_ in spawns))}), road points {len(roads)}; '
        f'seeds {stats["seeded"]} of {len(pts) + len(roads)}; candidate surfaces {stats["candidates"]}, kept '
        f'{stats["kept"]} (flood: {FLOOD})')
    if not keep.any():
        raise RuntimeError(f'{lv}: no vehicle ground reached from the seeds')
    res = stage_on_grid(s1, keep, xoff, zoff, log=lambda *a: log('  ' + ' '.join(str(x) for x in a)), detail=detail)
    if out_path is None:
        out_path = os.path.join(root, 'work', 'navvehicle', lv, 'pathdata.hpd')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if os.path.abspath(out_path).lower().startswith(os.path.abspath(os.path.join(root, 'game')).lower() + os.sep):
        raise RuntimeError('refusing to write into the game folder')
    write(res, out_path, log=lambda *a: log('  ' + ' '.join(str(x) for x in a)), t0=t0)
    stats['seconds'] = round(time.time() - t0, 1)
    return out_path, stats


# ---------------------------------------------------------------------------------------------- reading / comparing
def info(path):
    """Summary of a (vehicle) path-data file."""
    f = hpdtool.parse(open(path, 'rb').read())
    c = Counter()
    pitch, res_, rad, tiles = Counter(), Counter(), Counter(), Counter()
    for s in f.sectors:
        m = kymesh.parse_mesh(s.mesh)
        pitch[m.pitch] += 1
        res_[round(m.resolution, 4)] += 1
        rad[m.radius] += 1
        tiles[(m.tiles_x, m.tiles_z)] += 1
        c['areas'] += len(m.areas)
        for a in m.areas:
            c['portals'] += len(a.portals)
            c['border_portals'] += len(a.border_portals)
            c['d_lines'] += len(a.d_lines)
            c['f_cells'] += len(a.cells)
        c['nodes'] += len(s.nodes)
        c['edges'] += len(s.edges)
        c['border_node_copies'] += sum(1 for n in s.nodes if n.id & 0x80000000)
    first = [n.value for s in f.sectors for n in s.nodes[:1]]
    return {'path': path, 'sectors': len(f.sectors), 'offsets': file_offsets(path), 'pitch': dict(pitch),
            'resolution': dict(res_), 'radius': dict(rad), 'tiles': {f'{a}x{b}': n for (a, b), n in tiles.items()},
            'dist_edge_max': sorted(set(first)), **dict(c)}


@contextlib.contextmanager
def vehicle_cells():
    """navdiff's cell grid and outline offset for vehicle files (1 m grid, outlines / nodes 2 m over the ground)."""
    import navdiff
    saved = navdiff.GRID, navdiff.Y_OFFSET
    navdiff.GRID, navdiff.Y_OFFSET = PITCH, Y_OFFSET
    try:
        yield navdiff
    finally:
        navdiff.GRID, navdiff.Y_OFFSET = saved


def compare(reference, candidate, log=print, clip=None):
    """Parity of a vehicle file against DICE's: navdiff's layers on the 1 m grid, plus navparity's route islands
    (connectivity, over-connection). Returns a flat dict of measures."""
    import navparity
    with vehicle_cells() as nd:
        res, _rows, _R, _C = nd.diff(reference, candidate, log=lambda *a: None, clip=clip)
    isl = navparity.islands(reference, candidate)
    c = res['cells']
    out = {
        'sectors_reference': res['sectors']['reference'], 'sectors_candidate': res['sectors']['candidate'],
        'ground': c['parity'], 'extra_ground': round(100.0 * c['only_candidate'] / max(c['reference'], 1), 2),
        'cells_reference': c['reference'], 'cells_candidate': c['candidate'],
        'areas_reference': res['areas']['reference'], 'areas_candidate': res['areas']['candidate'],
        'areas_same': res['areas']['parity'], 'outline': res['bounds_of_same_areas']['outline'],
        'portals': res['bounds_of_same_areas']['portals'], 'borders': res['bounds_of_same_areas']['borders'],
        'nodes_reference': res['graph']['nodes_reference'], 'nodes_candidate': res['graph']['nodes_candidate'],
        'nodes_same': res['graph']['node_parity'], 'nodes_near': isl['nodes_near'],
        'connectivity': isl['connectivity'], 'over_connection': isl['over_connection'],
        'islands_reference': isl['reference'], 'islands_candidate': isl['candidate'],
        'large_islands_reference': isl['large_reference'], 'large_islands_candidate': isl['large_candidate'],
    }
    log(f"  sectors {out['sectors_candidate']} (DICE {out['sectors_reference']}); ground {out['ground']} %, extra "
        f"{out['extra_ground']} % (cells {out['cells_candidate']} / DICE {out['cells_reference']}); areas same "
        f"{out['areas_same']} % ({out['areas_candidate']} / {out['areas_reference']}); connectivity "
        f"{out['connectivity']}, over-connection {out['over_connection']}; nodes {out['nodes_candidate']} / "
        f"{out['nodes_reference']}, near {out['nodes_near']} %; islands {out['islands_candidate']} / "
        f"{out['islands_reference']} (25+ nodes {out['large_islands_candidate']} / {out['large_islands_reference']})")
    return out


def parity_levels(root):
    """Levels with a DICE vehicle file and in-game samples."""
    d = os.path.join(root, 'game', 'Output', 'win32', 'ai', 'levels')
    out = []
    for lv in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if reference_of(root, lv) and samples_of(root, lv) and \
                os.path.isdir(os.path.join(root, 'work', 'levels', lv, 'dist')):
            out.append(lv)
    return out


# ---------------------------------------------------------------------------------------------- command line
def _arg(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def main(argv):
    if len(argv) < 2 or argv[1] in ('-h', '--help'):
        print(__doc__)
        return 0 if len(argv) >= 2 else 1
    cmd = argv[1]
    if cmd == 'info':
        for p in argv[2:]:
            print(json.dumps(info(p), indent=1))
        return 0
    if cmd == 'compare':
        if len(argv) < 4:
            print(__doc__)
            return 1
        print(json.dumps(compare(argv[2], argv[3]), indent=1))
        return 0
    root = opfor_root(_arg(argv, '--opfor'))
    seeds = tuple(_arg(argv, '--seeds', '+'.join(SEED_KINDS)).split('+'))
    detail = False if '--no-detail' in argv else None
    sec = _arg(argv, '--sectors', 'reference')
    if sec not in ('reference', 'all'):
        sec = {((int(h, 16) >> 8) & 0xFF, int(h, 16) & 0xFF) for h in sec.split(',') if h}
    if cmd == 'build':
        if len(argv) < 3:
            print(__doc__)
            return 1
        lv = argv[2].lower()
        if sec == 'reference' and not reference_of(root, lv):
            sec = 'all'
        out, st = build_level(root, lv, _arg(argv, '--out'), sec, seeds, detail=detail)
        ref = reference_of(root, lv)
        if ref:
            compare(ref, out)
        print(json.dumps(st))
        return 0
    if cmd == 'parity':
        levels = (_arg(argv, '--levels') or '').split(',') if _arg(argv, '--levels') else parity_levels(root)
        run = {}
        for lv in levels:
            try:
                out, st = build_level(root, lv, None, sec, seeds, detail=detail)
                run[lv] = dict(compare(reference_of(root, lv), out), **st)
            except Exception as e:                       # noqa: BLE001 - one level's failure is reported, the rest run
                print(f'{lv}: FAILED - {e}')
                run[lv] = {'error': str(e)}
        keys = ['ground', 'extra_ground', 'areas_same', 'connectivity', 'over_connection', 'nodes_near']
        print('level        ' + ' '.join(f'{k[:12]:>12}' for k in keys))
        for lv, r in run.items():
            print(f'{lv:12s} ' + ' '.join(f'{r.get(k, float("nan")):12.2f}' for k in keys))
        ok = [r for r in run.values() if 'error' not in r]
        if ok:
            print('mean         ' + ' '.join(f'{sum(r[k] for r in ok) / len(ok):12.2f}' for k in keys))
        name = _arg(argv, '--save')
        if name:
            p = os.path.join(root, 'work', 'navvehicle', 'parity', name + '.json')
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(run, open(p, 'w'), indent=1)
            print('saved', p)
        return 0 if len(ok) == len(run) else 1
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import errors
    errors.setup('navvehicle', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
