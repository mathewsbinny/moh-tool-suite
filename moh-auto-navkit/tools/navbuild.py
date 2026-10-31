"""NavKit generator: in-game collision samples -> Kynapse-format pathdata.hpd (infantry, pitch 0.8).

Pipeline (research/KYNAPSE_SPEC.md sections 3 and 4):
  1. walkable surfaces: navwalk.walkable (slope, capsule, ForbidPathfinding objects, StepMax, perimeter seeds)
  2. sectors: 100 m grid from the level's SectorOffsets; each sector uses the samples -1 .. 126 of its
     window (two-sample overlap with each neighbour)
  3. tiles: 3 x 3 per sector, 50 samples each (edges half a sample outside the samples: -1.2, 38.8,
     78.8, 101.2 m); areas = connected regions of a tile (8-connected, AREA_LINK rule); the old bisection at
     the middle of their extent (over 25 samples) is off - DICE has no such cut (BISECT_AREAS)
  4. area boundary, traced on the half-sample lines and merged into straight runs:
       portal (B)   - to a connected surface in another area of the same sector
       border (C)   - to a connected surface outside the sector window (direction = outward axis)
       outline (E)  - everything else; where two cells of an area touch only diagonally, the two outline
                      corners between them are replaced by 45-degree segments (a bridge through the
                      half cells, as in DICE's outlines)
  5. neighbour links: per neighbouring sector, link objects {tile x, tile z, area index in tile} with the
     C records leading into that area
  6. graph: one node per area (sample nearest the area centre) and per portal run; border nodes on the
     overlap lines, shared by both sectors (same id and position); edges between nodes up to DistEdgeMax
     apart whose straight line stays on connected walkable samples; cost = 0x80000000 | floor(d * 16)
  7. write with kymesh.write_mesh / hpdtool.write
  (4b, between 4 and 5) detail features (DETAIL_FEATURES, navdetail.py): sides between two cells of one area whose
     heights differ by more than DETAIL_STEP become D lines, cells with both sides along an axis so broken become
     F cells (DICE's rule, KYNAPSE_SPEC.md 4.5) - and are taken out of the outline

Detailed zones (pitch 0.2) are a second pass at pitch 0.2 over the chosen sectors (build(..., detailed=...)).

Usage: navbuild.py <samples.bin> <forbid_tris.npy> <XOffset> <ZOffset> <out.hpd> [--step 0.3]
                   [--dynamic dynamic_tris.npy] [--high samples_high_capsule.bin] [--narrow samples_narrow.bin]
                   [--stairs stair_tris.npy] [--exclude exclude_tris.npy] [--exclude-foot exclude_footprint_tris.npy]
                   [--no-detail]   (no D lines / F cells: DETAIL_FEATURES off)
"""
import heapq
import math
import os
import sys
import time
from collections import defaultdict, deque

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import mohsoldier  # noqa: E402
import navdrops  # noqa: E402
import navislands  # noqa: E402
import navsample  # noqa: E402
import navwalk  # noqa: E402

PITCH = 0.8
SECTOR_SAMPLES = 125            # 100 m / 0.8 m
WINDOW = range(-1, 127)         # sample indices of a sector window (local)
TILE = 50                       # samples per tile
TILES = 3
TILE_20M_SECTORS = None         # experiment: sector ids (e.g. {0x901, 0x902}) tiled at 20 m (25 samples, 6 x 6 tiles)
                                # at the normal pitch - DICE's detailed zones have 20 m tiles (but pitch 0.2). For
                                # offline route checks only: how the runtime gets a sector's tile size is not known
                                # (its header has no tile size field), so such a build is not for the game (2026-10-19)
CLIP_TO_REFERENCE = True        # on a level with DICE path data, ground outside its extent (navregress.reference_bounds:
                                # outlines and portals at every pitch) is left out before seeding. A bound to the
                                # reference extent, NOT a found rule: DICE's edges are straight cuts (its last cell row
                                # fills 9-64 % of a side) from a generation rectangle that is not in the shipped level
                                # data. A new map (no DICE data) keeps the sampling box. (KYNAPSE_SPEC.md 4.5)
EDGE_SECTOR_RULE = 'grid_min'   # which sectors start their tiles at their own edge (EDGE_SECTOR_TILES): 'grid_min' = on
                                # the sector grid's minimum X (Z) - DICE 3,798 of 3,798 sector axes; 'no_neighbour' = no
                                # sector on the low side - 3,788 (misses holes inside the map)
SYMMETRIC_PORTALS = True        # every portal stands on both of its areas (DICE: 771 of 771 have a portal back)
ONE_WAY_PORTALS = 'drop'        # a portal piece whose target sees that side as its own interior (a second surface of
                                # the target in the source's column) cannot have a piece back (DICE meets no such case:
                                # its stacked floors are detailed zones): 'drop' = always outline; 'keep' = always a
                                # one-way portal; 'restore' = outline unless the two areas are joined through no other
                                # portal or shared surface level-wide (restore_one_way) - measured on sp_004_b it keeps
                                # 1,066 one-way records (80 % two-way) and AI pairwise falls 91.2 -> 89.1: most pairs a
                                # drop leaves without a portal are still joined by the route graph (walkable lines),
                                # which a portal-level test does not see. Open until the in-game test shows whether the
                                # runtime needs a portal to cross between areas (KYNAPSE_SPEC.md 4.5)
EDGE_SECTOR_TILES = True       # a sector with no sector next to it on the low x (or z) side starts its tiles at its
                                # own edge (sample 0, not -1): tile lines one cell later, 39.6 / 79.6 m into the sector
                                # (DICE's data: 3,788 of 3,798 sector axes on 17 levels; KYNAPSE_SPEC.md 4.5). Latent
                                # for now: NavKit's extra sectors outside DICE's bounds give most edge sectors a neighbour
MAX_AREA = 25                   # samples (20 m) before an area is bisected (only when BISECT_AREAS)
BISECT_AREAS = False            # DICE does not cut areas at the middle of their extent: its same-sector portals lie on
                                # the tile lines (38.8 / 78.8 m into the sector) and only sometimes on the fixed tile
                                # midlines (18.8 / 58.8 m; sp_005 17 %, sp_007 3 %, sp_004_b 2 % of portals), mostly in
                                # ground with long outlines; trigger not yet known (KYNAPSE_SPEC.md 4.5, 2026-10-16)
AREA_LINK = 'terrain'           # which neighbours one area joins: 'terrain' = DICE's rule below between terrain
                                # surfaces (the slope rule elsewhere), 'raw' = DICE's rule everywhere, 'slope' = the
                                # slope rule. Portals, borders and the route graph keep the slope rule ('slope'), so
                                # the steep pieces DICE cuts off stay reachable (NavKit lacks some of the steep cells
                                # that join them in DICE's data: the DICE rule there cost sp_004_b 9 connectivity points)
LINK_RULE = 'slope'             # the rule for everything else
LINK_STEP = mohsoldier.link_step(0.8)   # straight neighbours link when their raw height step is <= this (MOH: the
                                # 45 deg slope limit over one 0.8 m cell, 0.8 m); diagonal ones when a route through
                                # either side cell (walkable or not) has both steps <= it. (BFBC2: DICE's 0.6 m - 36.9 deg
                                # at 0.8 m; sp_004_b terrain: linked steps max 0.591; KYNAPSE_SPEC.md 4.5)
RADIUS = mohsoldier.RADIUS
RESOLUTION = 0.025
DIST_EDGE_MAX = 15.0
NODE_COVER = {'detailed': 4.0, 'normal': None}   # coverage nodes: in each area, a route node wherever walkable ground is
                                # farther than this (m, straight line, same floor) from every node, farthest first.
                                # DICE's detailed sectors: 90 % of outline points within 4.3 m of a node, 99 % within
                                # 8.3; NavKit's area-centre and portal nodes left 5.4 / 18.2 (ONS_MP_002, 2026-10-21).
                                # Normal sectors: off until checked against the campaign regression
GABRIEL_EDGES = 'detailed'      # prune the route graph's walkable pairs to the Gabriel graph (no other same-floor node
                                # inside the circle on the pair as diameter), keeping each sector's connectivity:
                                # 'detailed' = in pitch-0.2 sectors, 'all', or None. DICE's detailed-sector edges are 71 %
                                # Gabriel (RNG 50 %), 2.7 edges per node; NavKit's all-visible-pairs rule gave 15.9 per node
                                # there (174,024 edges against DICE's 24,710; ONS_MP_002, 2026-10-21)
DEDUP_GRAPH_NODES = True        # at most one route node per surface (build_graph; DICE: only border pairs share one)
SORT_NODES_BY_ID = True         # a sector's nodes in the file sorted by unsigned id, border nodes last (finalize_graph)
BORDER_OWNER = True             # node field c bit 16 only on the border copies the sector does not own (finalize_graph)
ORDER_NODES = True              # a route node at AI order positions far from any node (add_order_nodes; needs the level's
                                # order_points.json from levelobjects.py) - sp_004_b play test 2026-10-20: 0 failures. Was off until
                                # the sp_004_b play test confirms it (2026-10-20)
ORDER_NODE_MIN = 3.0            # ... when the nearest node on the same floor is farther than this (m)
ORDER_STATS = {}                # nodes added, and those removed by prune_order_nodes (for reports)
ORDER_IDS = set()               # ids of the order nodes of the current build
DROP_LINKS = True               # one-way route edges off ledges onto ground below, between regions no walk joins
                                # (navdrops.py; mp_02's spawn basin, 2026-10-30)
DROP_IDS = set()                # (upper node id, lower node id) of the drop links of the current build: finalize_graph
                                # writes only the edge down; the RSNV exporter reads a one-way edge as a drop
DROP_STATS = {}
ORDER_MIN_ISLAND = 25           # an order node in a route island with fewer nodes is dropped (prune_order_nodes): such
                                # pockets took the nearest-node matches next to the main networks (sp_009: connectivity
                                # 66.99 without order nodes, 62.58 with all, 67.45 with this rule; sp_004_b keeps its
                                # stairs-top nodes and gains 0.26 connectivity / 0.6 AI pairwise). A size, not "the main
                                # network": campaign levels have several large networks (sp_004_b: 2,577 and 1,255
                                # nodes). Calibrated on campaign levels; to check on MP geometry (2026-10-20)
DETOUR_REPAIR = False           # add waypoint nodes where the route graph detours (repair_detours) - off: at 1.3x it
                                # adds 65 % nodes / 4x edges on sp_004_b, far tighter than DICE's own graph (2026-10-20)
DETOUR_TRIGGER = 3.0            # repair node pairs within DIST_EDGE_MAX whose route exceeds this x straight - above DICE's
                                # p90 (3.12; median 1.34, sp_004_b 2026-10-20); the failing go-to routes ran 3.15-6.38x
DETOUR_MAX = 1.3                # ... to a walk of at most this x straight (DICE at the failing spot: 1.02-1.11)
DETOUR_LINK_ALL = False         # also link new waypoints to all walkable neighbours within DIST_EDGE_MAX (4x the edges)
DETOUR_PASSES = 2               # pass A, then pass B on the repaired graph
REPAIR_STATS = {}               # nodes added per pass (summed over sectors; for reports)
LADDER_STATS = {}               # ladder links made, and ladders whose ends lie in two sectors (not linked yet)
AREA_CELLS = {}                 # sector id -> per area (in the file's order): its cells' global indices i, j at its
                                # pitch (cell centre = index x pitch) and their ground heights - the kept surfaces the
                                # area was made of, exactly. Written beside the build as <name>.cells.npz (write_file)
                                # for the RSNV exporter (navexport.py): the outlines alone do not give a cell's height
                                # (BF1943's NavKit, 2026-10-25: estimated from the nearest outline point, 0.3 m or more
                                # off on 11 % of Coral Sea's 0.8 m cells)
BORDER_NODE_SPACING = 10.0
BRIDGE_UNION = True             # 45-degree bridges: outline from the exact union (bridge_union) - False: clip_bridges
MAX_WATER_DEPTH = mohsoldier.WATER_DEPTH   # ground deeper under water is not walkable: MOH's WaterDepthLimit 1.4 m
                                # (BFBC2: 1.5, DICE's data: 99.9 % of its ground under water within 1.45 m; navwater.py)
DETAIL_FEATURES = True          # D lines and F cells (navdetail.py): sides between two cells of one area whose heights
                                # differ by more than DETAIL_STEP are D lines (both sides) or, where a cell has both sides
                                # along an axis broken, F cells - and not outline (E), as in DICE's files. The runtime's
                                # straight-move test reads them. False: none (counts 0) and those sides stay outline -
                                # the output before 2026-10-26 (navkit.py build --no-detail, navbuild.py --no-detail)
DETAIL_HALF_CELLS = False       # the side cells of 45-degree bridges take part with their own height: a leg (their side
                                # toward a bridged area cell) breaks as any side (DICE's corner-cut legs with a step over
                                # 0.6 m to an area cell: 352 of 389 have D or F, sp_004_b). Off: NavKit's bridges are not
                                # DICE's corner cuts (it cuts more corners), and on NavKit's builds the legs cost D precision
                                # at pitch 0.8 (sp_01 44.6 -> 27.9 %, sp_004_b 59.3 -> 44.0 %) for 0-3 points of recall
DETAIL_IN_DETAILED = False      # D lines in detailed (pitch 0.2) sectors, by the terrain rule below. Off: NavKit's detailed
                                # areas join steep terrain DICE's do not, and the rule writes 10-20x DICE's lines there at
                                # 2.5-3 % precision (recall 41-69 %; sp_004_b, sp_01, ONS_MP_002, 2026-10-26) - lines that
                                # also keep route edges off them; the all-pitch D F1 fell on all three levels
DETAIL_F = {'normal': True, 'detailed': False}   # F cells per pitch; off: such cells' sides are D lines. In detailed zones
                                # NavKit's F matched none of DICE's (sp_004_b 4,898 records, precision 0.02 %; DICE has 20)
DETAIL_TERRAIN_ONLY = {'normal': False, 'detailed': True}   # sides break only between two terrain cells - in detailed
                                # zones DICE has D / F on 713 of 2,658,574 terrain sides and on 24 of 401,562 sides with an
                                # object cell (sp_004_b); at pitch 0.8 objects break too (sp_01: steps up to 1.2 m)
DETAIL_STEP = {'normal': 0.6, 'detailed': 0.175}   # the height difference (m) that breaks a side. Pitch 0.8: DICE's
                                # area link step - its in-area sides with a step from 0.61 m all have D / F (sp_004_b,
                                # sp_01, ONS_MP_002; spec 4.5). Pitch 0.2: DICE's detailed D sides start at 0.175 m, on
                                # terrain only (DETAIL_TERRAIN_ONLY): on DICE's own detailed areas, terrain sides over 0.173 m
                                # find 86 % of its D / F sides at 25 % precision (all surfaces: 15 %); was 0.3 m (NavKit's
                                # link step there) until the terrain rule - 0.175 on all surfaces gave 17x DICE's lines
DETAIL_BLOCKS_EDGES = True      # route edges do not cross a D line: line_walkable does not step straight across a D side
                                # of the sector being built. DICE's route edges cross D lines almost never (sp_004_b 1 of
                                # 24,514, sp_01 11 of 82,602) - its graph builder uses the same straight-move test; F cell
                                # sides they do cross (0.4-0.6 %), as NavKit's do. NavKit's edges crossed D lines on 1.7 %
                                # (sp_004_b) without it: the slope rule links some sides the 0.6 m step breaks (2026-10-26)
DETAIL_STATS = {}               # D lines, F cells and outline sides moved to D / F (for reports)


def water_tops(s, vertices, triangles):
    """The water surface height over every sample column (-inf where there is no water). The columns are a regular
    grid, so each water triangle only visits the columns its bounding box covers."""
    nx, nz, p = s.nx, s.nz, s.pitch
    top = np.full((nz, nx), -np.inf)
    if vertices is None or not len(triangles):
        return top.ravel()
    tri = np.asarray(vertices, dtype=np.float64)[np.asarray(triangles)]
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tri:
        i0 = max(0, int(math.floor((min(ax, bx, cx) - s.min_x) / p)))
        i1 = min(nx - 1, int(math.ceil((max(ax, bx, cx) - s.min_x) / p)))
        j0 = max(0, int(math.floor((min(az, bz, cz) - s.min_z) / p)))
        j1 = min(nz - 1, int(math.ceil((max(az, bz, cz) - s.min_z) / p)))
        if i0 > i1 or j0 > j1:
            continue
        d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(d) < 1e-12:
            continue
        X = s.min_x + np.arange(i0, i1 + 1) * p
        Z = s.min_z + np.arange(j0, j1 + 1) * p
        X, Z = np.meshgrid(X, Z)
        l1 = ((bz - cz) * (X - cx) + (cx - bx) * (Z - cz)) / d
        l2 = ((cz - az) * (X - cx) + (ax - cx) * (Z - cz)) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if inside.any():
            h = np.where(inside, l1 * ay + l2 * by + l3 * cy, -np.inf)
            sub = top[j0:j1 + 1, i0:i1 + 1]
            np.maximum(sub, h, out=sub)
    return top.ravel()


def outside_bounds(s, bounds):
    """Surfaces whose column lies outside bounds (minX, minZ, maxX, maxZ) - bool per surface (CLIP_TO_REFERENCE)."""
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x, z = s.min_x + (col % s.nx) * s.pitch, s.min_z + (col // s.nx) * s.pitch
    e = 1e-3                                             # edges inclusive (float noise of the sample grid)
    return (x < bounds[0] - e) | (x > bounds[2] + e) | (z < bounds[1] - e) | (z > bounds[3] + e)


def deep_water(s, water, limit=MAX_WATER_DEPTH):
    """Surfaces more than limit metres under the level's water (bool per surface). water: (vertices, triangles)."""
    if water is None:
        return np.zeros(len(s.y), dtype=bool)
    top = water_tops(s, water[0], water[1])
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    return (top[col] - s.y.astype(np.float64)) > limit
AREA_Y_MIN = 0.0                # area height range relative to its ground: DICE's convention (see build())
AREA_Y_MAX = mohsoldier.AREA_Y_MAX        # 0.8 x the soldier's height (BFBC2 1.52)
Y_OFFSET = mohsoldier.Y_OFFSET            # outline / node height above the ground: half the soldier's height (BFBC2 0.95)
HEADER = (2, 2, 0, 1, 0, 0, 0, 0, 2, 0, 8, 0) + (0,) * 20 + (36, 12, 1, 0)
DETAILED_PITCH = 0.2            # DICE's detailed zones: whole sectors at pitch 0.2, resolution 0.05, 6 x 6 tiles of 20 m
                                # (header rect -0.2 .. 100.22; KYNAPSE_SPEC.md 4.5). Built in a second pass (build(...,
                                # detailed=...)) from a pitch-0.2 sampling run, merged with the normal build.
CARRY_RADIUS = 1.0              # a shared node carried across a mixed-pitch seam is attached to ground within this (m)
DETAILED_LINK_STEP = mohsoldier.link_step(DETAILED_PITCH)   # LINK_STEP in detailed zones (MOH: the 0.3 m step; BFBC2
                                # 0.3 from DICE's one-cell height steps inside its pitch-0.2 areas
                                # (ONS_MP_002, 10,353 outline segments) p99 0.31, p99.9 0.42, max 0.49 m - about half its
                                # pitch-0.8 values (p99 0.96, max 1.03), not a quarter: the same slope (0.15 m) cut sloped
                                # ground into 3x DICE's areas (8,400 against 2,640; 2026-10-21)


def set_pitch(p):
    """Switch the grid settings for a build at pitch p: 0.8 (normal sectors) or 0.2 (detailed zones)."""
    global PITCH, SECTOR_SAMPLES, WINDOW, TILE, TILES, RESOLUTION, LINK_STEP
    PITCH = p
    SECTOR_SAMPLES = int(round(100 / p))                  # 125 / 500
    WINDOW = range(-1, SECTOR_SAMPLES + 2)                # two samples of overlap with each neighbour
    detailed = abs(p - DETAILED_PITCH) < 1e-6
    TILES = 6 if detailed else 3
    TILE = int(round((20 if detailed else 40) / p))       # 100 / 50 samples
    RESOLUTION = 0.05 if detailed else 0.025
    LINK_STEP = DETAILED_LINK_STEP if detailed else mohsoldier.link_step(0.8)


class Level:
    """Kept walkable surfaces on the global sample grid, with the step-rule connectivity."""

    def __init__(self, s, keep, step, gx=None, gz=None, stair=None):
        self.s, self.keep, self.step = s, keep, step
        self.stair = stair if stair is not None else np.zeros(len(s.y), bool)
        self.y = s.y.astype(float)
        ny = np.maximum(s.normal[:, 1], 0.05)
        self.gx = gx if gx is not None else -s.normal[:, 0] / ny
        self.gz = gz if gz is not None else -s.normal[:, 2] / ny
        self.col_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
        self.terrain = (s.hit[:, 0] == navwalk.TERRAIN_SHAPE_VTABLE) if s.hit is not None else np.ones(len(s.y), bool)
        self.blocked = set()       # (surface, surface) pairs line_walkable does not step across (D sides; DETAIL_BLOCKS_EDGES)

    def surfaces(self, gi, gj):
        if not (0 <= gi < self.s.nx and 0 <= gj < self.s.nz):
            return range(0)
        c = gj * self.s.nx + gi
        return [k for k in range(self.s.start[c], self.s.start[c + 1]) if self.keep[k]]

    def connected(self, k, n, di, dj, rule=None):
        """Surface n (direction di, dj from k) links to k by rule ('slope', 'raw', 'terrain'; LINK_RULE if None)."""
        rule = rule or LINK_RULE
        if rule == 'slope' or (rule == 'terrain' and not (self.terrain[k] and self.terrain[n])):
            return navwalk.step_fits(self.y[n] - self.y[k], (self.gx[k], self.gz[k]), (self.gx[n], self.gz[n]),
                                     self.stair[k], self.stair[n], di, dj, PITCH, self.step)
        y = self.y
        if not (di and dj):
            return abs(y[n] - y[k]) <= LINK_STEP
        # diagonal: through either side column, over any surface there (walkable or not)
        c = self.col_of[k]
        gi, gj = c % self.s.nx, c // self.s.nx
        for a, b in ((gi + di, gj), (gi, gj + dj)):
            if 0 <= a < self.s.nx and 0 <= b < self.s.nz:
                m0 = b * self.s.nx + a
                for m in range(self.s.start[m0], self.s.start[m0 + 1]):
                    if abs(y[m] - y[k]) <= LINK_STEP and abs(y[n] - y[m]) <= LINK_STEP:
                        return True
        return False

    def neighbour(self, k, gi, gj, di, dj, prefer=None, rule=None):
        """The kept surface in column (gi+di, gj+dj) connected to surface k, or None. With prefer (a set of
        surfaces, e.g. those of k's own area), a connected surface in that set is returned first."""
        best, bd = None, 9e9
        for n in self.surfaces(gi + di, gj + dj):
            if self.connected(k, n, di, dj, rule):
                if prefer is not None and n in prefer:
                    return n
                d = abs(self.y[n] - self.y[k])
                if d < bd:
                    best, bd = n, d
        return best


def sector_origin_index(level, X, Y, xoff, zoff):
    """Global sample index of the sector's local sample (0, 0)."""
    s = level.s
    ox, oz = (X + xoff) * 100.0, (Y + zoff) * 100.0
    return int(round((ox - s.min_x) / PITCH)), int(round((oz - s.min_z) / PITCH)), ox, oz


def tiling(sid):
    """(samples per tile, tiles per axis) of a sector."""
    return (25, 6) if TILE_20M_SECTORS and sid in TILE_20M_SECTORS else (TILE, TILES)


def build_areas(level, gi0, gj0, start=(-1, -1), tile=TILE, tiles=TILES):
    """Areas of one sector: list of dicts {tile: (tx, tz), cells: [(i, j, k)]} (local i, j). start: the local sample
    index where the tiles begin on x and z (-1, or 0 on a side with no neighbouring sector - EDGE_SECTOR_TILES)."""
    cells_by_tile = defaultdict(list)
    for j in WINDOW:
        for i in WINDOW:
            for k in level.surfaces(gi0 + i, gj0 + j):
                tx = min(max(i - start[0], 0) // tile, tiles - 1)
                tz = min(max(j - start[1], 0) // tile, tiles - 1)
                cells_by_tile[(tx, tz)].append((i, j, k))
    areas = []
    for tile in sorted(cells_by_tile):
        members = {k: (i, j) for i, j, k in cells_by_tile[tile]}
        for comp in components(level, members, gi0, gj0):
            for piece in (bisect(level, comp, gi0, gj0) if BISECT_AREAS else [comp]):
                areas.append({'tile': tile, 'cells': piece})
    return areas


def components(level, members, gi0, gj0):
    """8-connected components (AREA_LINK rule) of the given surfaces {k: (i, j)}. An area is single-layer in plan: a
    surface whose column the component already holds (a second floor reached round a corner) starts its own."""
    seen, out = set(), []
    for k0 in members:
        if k0 in seen:
            continue
        comp, q = [], deque([k0])
        seen.add(k0)
        cols = {members[k0]}
        while q:
            k = q.popleft()
            i, j = members[k]
            comp.append((i, j, k))
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj, rule=AREA_LINK)
                if n is not None and n in members and n not in seen and members[n] == (i + di, j + dj) \
                        and members[n] not in cols:
                    seen.add(n)
                    cols.add(members[n])
                    q.append(n)
        out.append(comp)
    return out


def bisect(level, comp, gi0, gj0):
    i0, i1 = min(c[0] for c in comp), max(c[0] for c in comp)
    j0, j1 = min(c[1] for c in comp), max(c[1] for c in comp)
    if i1 - i0 + 1 <= MAX_AREA and j1 - j0 + 1 <= MAX_AREA:
        return [comp]
    if i1 - i0 >= j1 - j0:
        mid = (i0 + i1 + 1) // 2
        halves = ([c for c in comp if c[0] < mid], [c for c in comp if c[0] >= mid])
    else:
        mid = (j0 + j1 + 1) // 2
        halves = ([c for c in comp if c[1] < mid], [c for c in comp if c[1] >= mid])
    out = []
    for h in halves:
        for sub in components(level, {k: (i, j) for i, j, k in h}, gi0, gj0):
            out.extend(bisect(level, sub, gi0, gj0))
    return out


def clip_bridges(bsegs, bridges):
    """Outline of overlapping 45-degree bridges is the boundary of their union: where edges of two bridges
    cross (one cell with diagonal-only neighbours on both sides of an empty cell), split them at the crossing
    and drop the pieces lying inside the other bridge's strip (half-width 1/sqrt 2 sample about the diagonal)."""
    def inside(px, pz, b):
        i, j, di, dj = bridges[b]
        rx, rz = px - i, pz - j
        along = (rx * di + rz * dj) / 2.0                   # 0..1 between the two cell centres
        perp = abs(rx * dj - rz * di) / math.sqrt(2.0)
        return 0.0 < along < 1.0 and perp < 1.0 / math.sqrt(2.0) - 1e-6

    out = list(bsegs)
    changed = True
    while changed:
        changed = False
        for u in range(len(out)):
            for v in range(u + 1, len(out)):
                (bu, p0, p1), (bv, q0, q1) = out[u], out[v]
                if bu == bv:
                    continue
                d = (p1[0] - p0[0]) * (q1[1] - q0[1]) - (p1[1] - p0[1]) * (q1[0] - q0[0])
                if abs(d) < 1e-9:
                    continue
                t = ((q0[0] - p0[0]) * (q1[1] - q0[1]) - (q0[1] - p0[1]) * (q1[0] - q0[0])) / d
                s = ((q0[0] - p0[0]) * (p1[1] - p0[1]) - (q0[1] - p0[1]) * (p1[0] - p0[0])) / d
                if not (1e-6 < t < 1 - 1e-6 and 1e-6 < s < 1 - 1e-6):
                    continue
                pieces = []
                for (b, a0, a1, f), other in (((bu, p0, p1, t), bv), ((bv, q0, q1, s), bu)):
                    m = (a0[0] + (a1[0] - a0[0]) * f, a0[1] + (a1[1] - a0[1]) * f, a0[2] + (a1[2] - a0[2]) * f)
                    for c0, c1 in ((a0, m), (m, a1)):
                        if not inside((c0[0] + c1[0]) / 2, (c0[1] + c1[1]) / 2, other):
                            pieces.append((b, c0, c1))
                out = [x for w, x in enumerate(out) if w not in (u, v)] + pieces
                changed = True
                break
            if changed:
                break
    return out


def bridge_union(bridges, cell_k, level):
    """BRIDGE_UNION: the outline of an area's 45-degree bridges as the boundary of their union, exactly. A bridge
    between diagonal-only cells A (i, j) and B (i + di, j + dj) covers half of each of the two empty cells beside the
    shared corner - per empty cell, the two quarter triangles (base: a cell side, apex: the centre) whose bases touch
    that corner. Over the set of covered quarters of the empty cells, a half-diagonal (cell corner to centre) shared
    by two quarters is interior; the others are the bridges' outline - halves of one diagonal joined. A side of an
    area cell is covered (no outline there) where the empty cell's quarter on it is. Returns (covered sides in
    trace_edges' keys, [((ax, az, ya), (bx, bz, yb))] in local sample units). With non-overlapping bridges this is
    exactly the two diagonals per bridge; overlapping ones (chains, a cell shared by two corners) close where
    clip_bridges left open ends (1.7-3.1 % of areas on sp_004_b / sp_01, 2026-10-23)."""
    quarters = {}                                   # (cell i, j, base side (sx, sz)) -> (bridge index, ya, yb)
    order = []
    for bi, (i, j, di, dj) in enumerate(bridges):
        ya = level.y[cell_k[(i, j)]] + Y_OFFSET
        yb = level.y[cell_k[(i + di, j + dj)]] + Y_OFFSET
        # empty cell (i, j + dj): its sides towards A (0, -dj) and towards B (di, 0); empty cell (i + di, j): towards
        # A (-di, 0) and towards B (0, dj) - the order clip_bridges emitted them in
        for (ei, ej), toward_a, toward_b in (((i, j + dj), (0, -dj), (di, 0)), ((i + di, j), (-di, 0), (0, dj))):
            for side in (toward_a, toward_b):
                q = (ei, ej, side)
                if q not in quarters:
                    quarters[q] = (bi, ya, yb, toward_a, toward_b)
                    order.append(q)
    def corners(ei, ej, side):                      # the two corners of a quarter's base, in half units
        sx, sz = side
        if sx:
            return ((2 * ei + sx, 2 * ej - 1), (2 * ei + sx, 2 * ej + 1))
        return ((2 * ei - 1, 2 * ej + sz), (2 * ei + 1, 2 * ej + sz))
    count = defaultdict(int)
    for (ei, ej, side) in quarters:
        for c in corners(ei, ej, side):
            count[(c, ei, ej)] += 1                 # half-diagonal from corner c to the centre of (ei, ej)
    drop = set()
    for (ei, ej, side) in quarters:
        sx, sz = side
        drop.add(('x', ei + sx * 0.5, ej) if sx else ('z', ej + sz * 0.5, ei))
    segs, done, emitted = [], set(), set()
    for (ei, ej, side) in order:
        bi, ya, yb, toward_a, toward_b = quarters[(ei, ej, side)]
        if (ei, ej, bi) in done:
            continue
        done.add((ei, ej, bi))
        # this bridge's diagonal through the cell: from A's outer corner to B's outer corner, through the centre
        ca = [c for c in corners(ei, ej, toward_a) if count[(c, ei, ej)] == 1 and (ei, ej, toward_a) in quarters
              and (c, ei, ej) not in emitted]
        cb = [c for c in corners(ei, ej, toward_b) if count[(c, ei, ej)] == 1 and (ei, ej, toward_b) in quarters
              and (c, ei, ej) not in emitted]
        emitted.update((c, ei, ej) for c in ca + cb)
        ctr = (float(ei), float(ej), (ya + yb) / 2.0)
        pa = [(c[0] / 2.0, c[1] / 2.0, ya) for c in ca]
        pb = [(c[0] / 2.0, c[1] / 2.0, yb) for c in cb]
        if len(pa) == 1 and len(pb) == 1:
            segs.append((pa[0], pb[0]))
        else:
            segs.extend((p, ctr) for p in pa)
            segs.extend((ctr, p) for p in pb)
    return drop, segs


def trace(level, sector, areas, area_of, gi0, gj0, ox, oz):
    """Boundary edges of every area -> merged runs. Returns per area: portals, borders, outlines. One sector on its
    own: one-way portal pieces are kept ('keep') or become outline (otherwise) - build() decides them level-wide
    ('restore', restore_one_way)."""
    per_area, one_way = trace_edges(level, areas, area_of, gi0, gj0)
    drop_one_way(per_area, one_way, keep=set(range(len(one_way))) if ONE_WAY_PORTALS == 'keep' else set())
    return trace_emit(level, areas, per_area, gi0, gj0, ox, oz)


def trace_edges(level, areas, area_of, gi0, gj0):
    """Boundary edges of every area: (per area {(kind, target, axis, line, dir): [(along, y)]}, one-way candidates).
    Portals are symmetric (SYMMETRIC_PORTALS): every portal edge A -> B also stands as B -> A, replacing an outline
    edge B had there - neighbour() picks the nearest surface in height, so with two surfaces in a column the two
    sides could disagree and leave a one-way portal (22 % of them on sp_004_b before). Where the target's side is
    its own interior (a second surface of the target in the source's column) the piece cannot be mirrored: it is a
    one-way candidate (source area, target area, key, along) for drop_one_way."""
    per_area = []
    one_way = []
    mirror = defaultdict(dict)       # area -> {(source area, axis, line, dir, along): y of its own cell there}
    side_kind = []                   # per area: {(axis, line, dir, along): (kind 'I' interior / 'B' / 'C' / 'E', target)}
    for ai, area in enumerate(areas):
        edges = defaultdict(list)    # (kind, target, axis, line, dir) -> [(along, y0, y1)]
        sides = {}
        own = {c[2] for c in area['cells']}
        for i, j, k in area['cells']:
            yk = level.y[k] + Y_OFFSET
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                side = ('x', i + di * 0.5, di, j) if di else ('z', j + dj * 0.5, dj, i)
                # a side is interior when any connected surface across it is in this area (two connected
                # surfaces in one column - stair edge, split-off layer - must not leave a stray portal inside)
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj, prefer=own)
                ni, nj = i + di, j + dj
                if n is not None and ni in WINDOW and nj in WINDOW:
                    t = area_of.get(n)
                    if t == ai:
                        sides[side] = ('I', None)
                        continue
                    kind, target = ('B', t) if t is not None else ('E', None)
                elif n is not None:
                    kind, target = 'C', (di, dj)
                else:
                    kind, target = 'E', None
                if di:   # vertical line x = const
                    key = (kind, target, 'x', i + di * 0.5, di)
                    edges[key].append((j, yk))
                else:
                    key = (kind, target, 'z', j + dj * 0.5, dj)
                    edges[key].append((i, yk))
                sides[side] = (kind, target)
                if kind == 'B':
                    axis, line, along = ('x', i + di * 0.5, j) if di else ('z', j + dj * 0.5, i)
                    mirror[target][(ai, axis, line, -(di or dj), along)] = level.y[n] + Y_OFFSET
        per_area.append(edges)
        side_kind.append(sides)
    if SYMMETRIC_PORTALS:
        # only where the target's side is plain outline: an interior side (the column holds a surface of the target
        # too) or a side holding another portal stays as it is - a portal there would break the area's outline
        for t, need in mirror.items():
            edges = per_area[t]
            for (src, axis, line, d, along), y in need.items():
                there = side_kind[t].get((axis, line, d, along), ('E', None))
                if there == ('B', src):                  # a portal back already
                    continue
                if there[0] != 'E':
                    key = ('B', t, axis, line, -d)
                    if any(r[0] == along for r in per_area[src].get(key, [])):
                        one_way.append((src, t, key, along))
                    continue
                for key in [k for k in edges if k[0] == 'E' and k[2] == axis and k[3] == line and k[4] == d]:
                    edges[key] = [r for r in edges[key] if r[0] != along]
                edges[('B', src, axis, line, d)].append((along, y))
                side_kind[t][(axis, line, d, along)] = ('B', src)
    return per_area, one_way


def drop_one_way(per_area, one_way, keep):
    """One-way candidates (trace_edges) not in keep (indices) become outline on their source area."""
    for n, (src, t, key, along) in enumerate(one_way):
        if n in keep:
            continue
        se = per_area[src]
        ys = [r for r in se.get(key, []) if r[0] == along]
        se[key] = [r for r in se.get(key, []) if r[0] != along]
        se[('E', None, key[2], key[3], key[4])].extend(ys)


def restore_one_way(sectors, shared):
    """ONE_WAY_PORTALS 'restore': which one-way candidates to keep. sectors: {sector: (per_area, one_way)} from
    trace_edges; shared: pairs ((sector, area), (sector, area)) joined across sectors (one surface in both - the
    sectors' two-sample overlap). Areas are joined through every portal piece that is not a candidate and through
    shared; then, pair by pair, a pair's candidates are kept only when its two areas are not joined yet (keeping them
    joins them). Returns {sector: set of kept candidate indices}."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for sec, (per_area, one_way) in sectors.items():
        cand = {(src, key, along) for src, _t, key, along in one_way}
        for ai, edges in enumerate(per_area):
            find((sec, ai))
            for key, rows in edges.items():
                if key[0] == 'B' and any((ai, key, r[0]) not in cand for r in rows):
                    union((sec, ai), (sec, key[1]))
    for a, b in shared:
        union(a, b)
    keep = defaultdict(set)
    by_pair = defaultdict(list)
    for sec, (_per_area, one_way) in sectors.items():
        for n, (src, t, key, along) in enumerate(one_way):
            by_pair[(sec, src, t)].append(n)
    for (sec, src, t), ns in sorted(by_pair.items()):
        if find((sec, src)) != find((sec, t)):
            keep[sec].update(ns)
            union((sec, src), (sec, t))
    return keep


def half_cells(level, area, gi0, gj0):
    """The 45-degree bridges' side cells of an area (navdetail's half cells): {((i, j), (di, dj)): height} for each
    leg - the side of a side cell toward the bridged area cell - and {(i, j): surface}. The side cell's surface is the
    sampled one (kept or not) nearest the two bridged cells' mean height, within 1 m."""
    cell_k = {(c[0], c[1]): c[2] for c in area['cells']}
    half, ks = {}, {}
    s = level.s
    for (i, j), k in cell_k.items():
        for di, dj in ((1, 1), (1, -1)):
            kb = cell_k.get((i + di, j + dj))
            if kb is None or (i + di, j) in cell_k or (i, j + dj) in cell_k:
                continue
            y0 = (level.y[k] + level.y[kb]) / 2.0
            for (xi, xj), legs in (((i, j + dj), ((0, -dj), (di, 0))), ((i + di, j), ((-di, 0), (0, dj)))):
                gi, gj = gi0 + xi, gj0 + xj
                if not (0 <= gi < s.nx and 0 <= gj < s.nz):
                    continue
                c = gj * s.nx + gi
                col = range(s.start[c], s.start[c + 1])
                if not len(col):
                    continue
                kx = min(col, key=lambda q: abs(level.y[q] - y0))
                if abs(level.y[kx] - y0) > 1.0:
                    continue
                ks[(xi, xj)] = kx
                for leg in legs:
                    half[((xi, xj), leg)] = float(level.y[kx])
    return half, ks


def detail_pass(level, areas, per_area, ox, oz, gi0=None, gj0=None):
    """DETAIL_FEATURES: per area (D polylines, F cells) from navdetail, and the broken sides taken out of the area's
    outline edges (per_area, from trace_edges; before trace_emit). A side holding a portal or border portal stays one.
    gi0, gj0: the sector's sample origin - with them, the bridges' half cells take part (DETAIL_HALF_CELLS)."""
    import navdetail
    kind = 'detailed' if abs(PITCH - DETAILED_PITCH) < 1e-6 else 'normal'
    if kind == 'detailed' and not DETAIL_IN_DETAILED:
        return [([], [])] * len(areas)
    step = DETAIL_STEP[kind]
    terrain = getattr(level, 'terrain', None)
    out = []
    for ai, area in enumerate(areas):
        edges = per_area[ai]
        cells = {(i, j): float(level.y[k]) for i, j, k in area['cells']}
        ks = {(i, j): k for i, j, k in area['cells']}
        half = None
        if DETAIL_HALF_CELLS and gi0 is not None and hasattr(level, 's'):
            half, hks = half_cells(level, area, gi0, gj0)
            for c, k in hks.items():
                ks.setdefault(c, k)
        breakable = None
        if DETAIL_TERRAIN_ONLY[kind] and terrain is not None:
            def breakable(a, b, ks=ks):         # detailed zones: only between terrain cells (DICE: objects never)
                return a in ks and b in ks and bool(terrain[ks[a]]) and bool(terrain[ks[b]])
        linked = {(key[2], key[3], key[4], r[0]) for key, rows in edges.items() if key[0] in ('B', 'C') for r in rows}

        def eligible(i, j, di, dj):     # neither of the two cells has a portal or border portal on the side
            return (('x', i + di * 0.5, di, j) if di else ('z', j + dj * 0.5, dj, i)) not in linked and \
                (('x', i + di * 0.5, -di, j) if di else ('z', j + dj * 0.5, -dj, i)) not in linked
        broken, lines, fcells = navdetail.detail_features(cells, PITCH, ox, oz, step, eligible, half, breakable,
                                                          with_f=DETAIL_F[kind])
        if broken and DETAIL_BLOCKS_EDGES and hasattr(level, 'blocked'):
            fsets = navdetail.f_cell_sets(broken, cells) if DETAIL_F[kind] else (set(), set())
            for (i, j), (di, dj) in navdetail.d_sides(broken, *fsets):
                if (i, j) in ks and (i + di, j + dj) in ks:
                    level.blocked.add((ks[(i, j)], ks[(i + di, j + dj)]))
        broken = {b_ for b_ in broken if b_[0] in cells and (b_[0][0] + b_[1][0], b_[0][1] + b_[1][1]) in cells}
        if broken:
            # a broken side is outline on both of its cells or on neither (the slope rule linked it); taken out of the
            # outline only as a pair, so the area's boundary stays closed
            e_at = {(key[2], key[3], key[4], r[0]) for key, rows in edges.items() if key[0] == 'E' for r in rows}
            gone = defaultdict(set)
            for (i, j), (di, dj) in broken:
                axis, line, d, along = ('x', i + di * 0.5, di, j) if di else ('z', j + dj * 0.5, dj, i)
                if (axis, line, d, along) in e_at and (axis, line, -d, along) in e_at:
                    gone[('E', None, axis, line, d)].add(along)
                elif (axis, line, d, along) in e_at:
                    DETAIL_STATS['outline_one_sided'] = DETAIL_STATS.get('outline_one_sided', 0) + 1
            moved = 0
            for key, alongs in gone.items():
                before = len(edges[key])
                edges[key] = [r for r in edges[key] if r[0] not in alongs]
                moved += before - len(edges[key])
            DETAIL_STATS['outline_moved'] = DETAIL_STATS.get('outline_moved', 0) + moved
        DETAIL_STATS['d'] = DETAIL_STATS.get('d', 0) + len(lines)
        DETAIL_STATS['f'] = DETAIL_STATS.get('f', 0) + len(fcells)
        out.append((lines, fcells))
    return out


def trace_emit(level, areas, per_area, gi0, gj0, ox, oz):
    """Per area (portals, borders, outline) from its boundary edges: runs merged, 45-degree bridges."""
    result = []
    for ai, area in enumerate(areas):
        edges = per_area[ai]
        # 45-degree bridges between diagonal-only neighbours of this area
        cellset = {(c[0], c[1]) for c in area['cells']}
        bridges = []
        cell_k = {(c[0], c[1]): c[2] for c in area['cells']}
        for i, j, k in area['cells']:
            for di, dj in ((1, 1), (1, -1)):
                if (i + di, j + dj) in cellset and (i + di, j) not in cellset and (i, j + dj) not in cellset:
                    # only across an open corner: neither cell has a connected surface (another area) beside it
                    # there - otherwise those sides are portals and the strip would overlap the other area
                    kd = cell_k[(i + di, j + dj)]
                    if any(level.neighbour(kk, gi0 + a, gj0 + b, x, z) is not None
                           for kk, a, b, x, z in ((k, i, j, di, 0), (k, i, j, 0, dj),
                                                  (kd, i + di, j + dj, -di, 0), (kd, i + di, j + dj, 0, -dj))):
                        continue
                    bridges.append((i, j, di, dj))
        portals, borders, outline = [], [], []
        if BRIDGE_UNION:
            drop, dsegs = bridge_union(bridges, cell_k, level)
            for (ax, az, ya), (bx, bz, yb) in dsegs:
                outline.append([(ox + ax * PITCH, ya, oz + az * PITCH), (ox + bx * PITCH, yb, oz + bz * PITCH)])
        else:
            drop = set()
            for i, j, di, dj in bridges:
                # unit outline edges around the corner point (i + di/2, j + dj/2)
                drop.update({('x', i + di * 0.5, j), ('z', j + dj * 0.5, i), ('x', i + di * 0.5, j + dj), ('z', j + dj * 0.5, i + di)})
            bsegs = []                                      # (bridge, (ax, az, ya), (bx, bz, yb)) local units
            for bi, (i, j, di, dj) in enumerate(bridges):
                cx, cz = i + di * 0.5, j + dj * 0.5         # shared corner (local sample units)
                ya = level.y[cell_k[(i, j)]] + Y_OFFSET
                yb = level.y[cell_k[(i + di, j + dj)]] + Y_OFFSET
                for (ax, az), (bx, bz) in (((cx - di, cz), (cx, cz + dj)), ((cx, cz - dj), (cx + di, cz))):
                    bsegs.append((bi, (ax, az, ya), (bx, bz, yb)))
            for _bi, (ax, az, ya), (bx, bz, yb) in clip_bridges(bsegs, bridges):
                outline.append([(ox + ax * PITCH, ya, oz + az * PITCH), (ox + bx * PITCH, yb, oz + bz * PITCH)])
        for key in list(edges):
            if key[0] == 'E':
                axis, line = key[2], key[3]
                edges[key] = [r for r in edges[key] if (axis, line, r[0]) not in drop]
        for (kind, target, axis, line, d), runs in edges.items():
            runs.sort()
            start = 0
            for r in range(1, len(runs) + 1):
                if r == len(runs) or runs[r][0] != runs[r - 1][0] + 1:
                    a, b = runs[start][0] - 0.5, runs[r - 1][0] + 0.5
                    ya, yb = runs[start][1], runs[r - 1][1]
                    if axis == 'x':
                        p0 = (ox + line * PITCH, ya, oz + a * PITCH)
                        p1 = (ox + line * PITCH, yb, oz + b * PITCH)
                    else:
                        p0 = (ox + a * PITCH, ya, oz + line * PITCH)
                        p1 = (ox + b * PITCH, yb, oz + line * PITCH)
                    if kind == 'B':
                        portals.append(kymesh.Portal(p0, p1, target))
                    elif kind == 'C':
                        borders.append((kymesh.BorderPortal(p0, p1, (float(target[0]), 0.0, float(target[1]))),
                                        runs[start][0], runs[r - 1][0], line))
                    else:
                        outline.append([p0, p1])
                    start = r
        result.append((portals, borders, outline))
    return result


def build(samples_path, tris_path, xoff, zoff, out_path, step=mohsoldier.STEP, log=print, dynamic_path=None, high_path=None,
          narrow_path=None, stairs_path=None, exclude_path=None, exclude_foot_path=None, seeds=None, edits=None,
          water=None, max_water_depth=MAX_WATER_DEPTH, bounds=None, order_points=None, detailed=None, ladders=None,
          area_orders=None):
    """edits: the operations of a navigation side file (navedits.py applies the walkability ones).
    water: the level's water mesh (vertices, triangles; core/mohcore/water.py): ground more than max_water_depth
    under it is not walkable, as in DICE's data (hand edits forcing ground walkable still win).
    bounds: (minX, minZ, maxX, maxZ) of the reference path data (CLIP_TO_REFERENCE), or None.
    detailed: {'samples': a pitch-0.2 sampling run, 'high': its high-capsule run (or None), 'sectors': {(X, Y)}} -
    the second pass: those sectors are built at pitch 0.2 (DICE's detailed zones) and merged with the normal build
    (merge_detailed). Without it the build is the normal one, unchanged."""
    t0 = time.time()
    common = dict(tris_path=tris_path, step=step, log=log, dynamic_path=dynamic_path, narrow_path=narrow_path,
                  stairs_path=stairs_path, exclude_path=exclude_path, exclude_foot_path=exclude_foot_path,
                  edits=edits, water=water, max_water_depth=max_water_depth, bounds=bounds,
                  order_points=order_points, t0=t0, ladders=ladders, area_orders=area_orders)
    set_pitch(0.8)
    ORDER_IDS.clear()
    DROP_IDS.clear()
    DROP_STATS.clear()
    navislands.TERRAIN_IDS.clear()
    LADDER_STATS.clear()
    AREA_CELLS.clear()
    DETAIL_STATS.clear()
    a = stage(samples_path, high_path, xoff, zoff, seeds=seeds, **common)
    if detailed:
        graphs = merge_detailed(a, detailed, xoff, zoff, seeds, common, log)
        set_pitch(0.8)
    else:
        graphs = a['graphs']
    link_counts(graphs)
    if DROP_LINKS:
        log(f'drop links made {DROP_STATS["linked"]}, ends in two sectors {DROP_STATS["split"] // 2} (not linked)')
    if ladders:
        log(f'ladders: {len(ladders)}; links made {LADDER_STATS["linked"]}, ends in two sectors '
            f'{LADDER_STATS["split"]}')
    if DETAIL_FEATURES:
        log(f'detail features: {DETAIL_STATS.get("d", 0)} D lines, {DETAIL_STATS.get("f", 0)} F cells; '
            f'{DETAIL_STATS.get("outline_moved", 0)} outline sides moved to them, '
            f'{DETAIL_STATS.get("outline_one_sided", 0)} kept (outline on one cell only)')
    else:
        log('detail features: off (no D lines or F cells)')
    # route islands that shadow a larger one lose their nodes (navislands.py: SHADOW_ISLANDS)
    graphs = navislands.prune(graphs, list(seeds or []) + list(order_points or []) + list(area_orders or []), log)
    write_file(graphs, out_path, log, t0)


def link_counts(graphs):
    """DROP_STATS / LADDER_STATS 'linked' and 'split' of the sectors written (graphs, after merge_detailed): the drop
    links are the one-way pairs (DROP_IDS) in their graphs; the rest is the stages' per-sector entries. Summed over
    both stages, stage a's links in the sectors stage b replaces were counted too (2026-10-31)."""
    sids = [g[0] for g in graphs]
    DROP_STATS['linked'] = sum(1 for _sid, _b, _m, pts, pairs, _o in graphs for a_, b_, _d in pairs
                               if (pts[a_][0], pts[b_][0]) in DROP_IDS)
    per = DROP_STATS.get('sectors', {})
    DROP_STATS['split'] = sum(per.get(sid, 0) for sid in sids)
    per = LADDER_STATS.get('sectors', {})
    LADDER_STATS['linked'] = sum(per.get(sid, (0, 0))[0] for sid in sids)
    LADDER_STATS['split'] = sum(per.get(sid, (0, 0))[1] for sid in sids)


def on_grid(path, s, what):
    """A second sampling run of the grid s (the high- or narrow-capsule run), coarsened as s was when it is a fine
    run (navwalk.coarse, as stage does): walkable() matches its surfaces to s's by column and height
    (navwalk.matched_flags). A run on another grid is refused - read at its own pitch beside a coarsened s it was
    indexed with the wrong columns (2026-10-31)."""
    f, _ = navsample.load(path)
    if abs(f.pitch - PITCH) > 1e-3:
        f = navwalk.coarse(f)
    if (abs(f.pitch - s.pitch) > 1e-3 or f.nx != s.nx or f.nz != s.nz or abs(f.min_x - s.min_x) > 1e-3
            or abs(f.min_z - s.min_z) > 1e-3):
        raise ValueError(f'{what} run {path} is not on the grid of the samples (pitch {f.pitch:g}, {f.nx} x {f.nz} '
                         f'from ({f.min_x:g}, {f.min_z:g}); the samples: pitch {s.pitch:g}, {s.nx} x {s.nz} from '
                         f'({s.min_x:g}, {s.min_z:g}))')
    return f


def stage(samples_path, high_path, xoff, zoff, tris_path=None, step=mohsoldier.STEP, log=print, dynamic_path=None,
          narrow_path=None, stairs_path=None, exclude_path=None, exclude_foot_path=None, seeds=None, edits=None,
          water=None, max_water_depth=MAX_WATER_DEPTH, bounds=None, order_points=None, area_orders=None, t0=None,
          sector_filter=None, grid_min=None, id_start=1, carried=None, seed_points=None, ladders=None):
    """One grid's build (the module's pitch settings, set_pitch): walkable surfaces, sectors, areas, border nodes,
    traced boundaries, meshes and route graphs - not yet written. sector_filter: build only these (X, Y) sectors;
    grid_min: the level's minimum sector X / Y (EDGE_SECTOR_RULE) when the stage holds only part of it; id_start:
    the first node id; carried: {sector: [(id, pos)]} shared nodes from another stage on this stage's sectors (the
    seams with sectors built at another pitch) - attached to this grid's ground where it has some; seed_points:
    more walkable-seed points (x, ground y, z) - not soldier spawns (those are seeds).
    Returns {'level', 'info', 'graphs', 'border_nodes', 'next_id', 'dropped', 'pitch', 'meshes'}."""
    t0 = t0 or time.time()
    f, _ = navsample.load(samples_path)
    s = navwalk.coarse(f) if abs(f.pitch - PITCH) > 1e-3 else f
    soldiers = list(seeds or [])                 # the level's soldier spawn points (navwalk.CHARACTERS_ABSENT)
    hand, seeds = stage_hand_rules(s, seeds, edits, water, max_water_depth, bounds, log)
    level, keep = stage_walkable(s, step, hand, seeds, soldiers, seed_points, order_points, area_orders, ladders,
                                 tris_path, dynamic_path, high_path, narrow_path, stairs_path, exclude_path,
                                 exclude_foot_path, log, t0)
    ladder_pairs, drop_pairs = stage_link_pairs(s, keep, ladders, log)
    sectors = kept_sectors(s, level, keep, xoff, zoff, sector_filter, log)

    # Pass 1: areas per sector, and which area each surface belongs to.
    info = sector_areas(level, sectors, xoff, zoff, grid_min, samples_path, log, t0)

    # Graph: border nodes shared between sector pairs (global ids), then inner nodes.
    next_id = [id_start]

    def new_id():
        next_id[0] += 1
        return next_id[0]

    border_nodes = shared_border_nodes(level, info, new_id)
    dropped = attach_carried(level, info, carried, border_nodes)
    edge_sets = traced_edge_sets(level, info, log)
    graphs, meshes = [], {}
    for (X, Y), inf in sorted(info.items()):
        mesh, graph = sector_output(level, info, X, Y, inf, edge_sets[(X, Y)], border_nodes[(X, Y)], new_id,
                                    order_points, ladder_pairs, drop_pairs)
        meshes[(X, Y)] = mesh
        graphs.append(graph)
    return {'level': level, 'info': info, 'graphs': graphs, 'border_nodes': border_nodes, 'next_id': next_id[0],
            'dropped': dropped, 'pitch': PITCH, 'meshes': meshes, 'sectors': set(info)}


def stage_hand_rules(s, seeds, edits, water, max_water_depth, bounds, log):
    """The surfaces forced walkable / unwalkable before the walk (hand edits, the water rule, the reference extent),
    as walkable()'s keyword arguments, and the seeds with the edits' own added. Returns (hand, seeds)."""
    hand = {}
    if edits:
        import navedits
        e = navedits.edits_for(s, edits)
        hand = {'force_walkable': e['force_walkable'] if e['surfaces_walkable'] else None,
                'force_unwalkable': e['force_unwalkable'] if e['surfaces_unwalkable'] else None,
                'unseeds': e['unseeds']}
        seeds = list(seeds or []) + e['seeds']
        log(f'hand edits: {e["counts"]}; surfaces forced walkable {e["surfaces_walkable"]}, unwalkable {e["surfaces_unwalkable"]}')
    if water is not None and max_water_depth is not None:
        deep = deep_water(s, water, max_water_depth)
        if deep.any():
            fw, fu = hand.get('force_walkable'), hand.get('force_unwalkable')
            if fw is not None:
                deep &= ~fw                      # a hand edit forcing ground walkable wins over the water rule
            hand['force_unwalkable'] = deep if fu is None else (fu | deep)
        log(f'water: {int(deep.sum())} surfaces more than {max_water_depth} m under water left out')
    if bounds is not None and CLIP_TO_REFERENCE:
        outside = outside_bounds(s, bounds)
        fw, fu = hand.get('force_walkable'), hand.get('force_unwalkable')
        if fw is not None:
            outside &= ~fw                       # a hand edit forcing ground walkable wins here too
        hand['force_unwalkable'] = outside if fu is None else (fu | outside)
        log(f'reference extent x {bounds[0]:.1f}..{bounds[2]:.1f}, z {bounds[1]:.1f}..{bounds[3]:.1f}: '
            f'{int(outside.sum())} surfaces outside left out')
    return hand, seeds


def stage_walkable(s, step, hand, seeds, soldiers, seed_points, order_points, area_orders, ladders, tris_path,
                   dynamic_path, high_path, narrow_path, stairs_path, exclude_path, exclude_foot_path, log, t0):
    """The walkable surfaces of the grid s (navwalk.walkable with the stage's side inputs) and the Level over them.
    Returns (level, keep)."""
    tris = np.load(tris_path) if tris_path else None
    dyn = np.load(dynamic_path) if dynamic_path else None
    hi = on_grid(high_path, s, 'high-capsule') if high_path else None
    narrow = on_grid(narrow_path, s, 'narrow-capsule') if narrow_path else None
    st = np.load(stairs_path) if stairs_path else None
    ex = np.load(exclude_path) if exclude_path else None
    exf = np.load(exclude_foot_path) if exclude_foot_path else None
    keep, gx, gz, stair = navwalk.walkable(s, tris, step=step, dynamic_tris=dyn, high_capsule=hi, return_gradients=True,
                                    return_stairs=True,
                                    narrow_capsule=narrow, stair_tris=st, exclude_tris=ex, exclude_footprint_tris=exf,
                                    extra_seeds=list(seeds or []) + list(seed_points or []), soldier_points=soldiers,
                                    ladders=ladders, **hand,
                                    # roof pruning anchors: soldier spawns, AI order positions, and (stage b) the ground
                                    # the normal build kept - stage b drops only roofs that build did not have
                                    anchors=list(seeds or []) + list(order_points or []) + list(seed_points or []),
                                    objective_points=area_orders)
    level = Level(s, keep, step, gx, gz, stair)
    log(f'{len(s.y)} surfaces, {int(keep.sum())} walkable kept ({time.time() - t0:.0f} s)'
        + (f'; roof islands dropped {navwalk.ROOF_STATS.get("dropped", 0)} ({navwalk.ROOF_STATS.get("surfaces", 0)} surfaces)'
           if navwalk.ROOF_PRUNE else '')
        + (f'; objective floors {navwalk.OBJECTIVE_STATS.get("points", 0)} ({navwalk.OBJECTIVE_STATS.get("surfaces", 0)} surfaces)'
           if navwalk.OBJECTIVE_FLOOR else ''))
    navwalk.ROOF_STATS.clear()
    navwalk.OBJECTIVE_STATS.clear()
    return level, keep


def stage_link_pairs(s, keep, ladders, log):
    """The (from, to) kept-surface pairs of the ladder links and the drop links. Returns (ladder_pairs, drop_pairs)."""
    # ladder links: the kept surfaces at each ladder's foot and top (navwalk.ladder_ends)
    ladder_pairs = []
    for lad in (ladders or ()):
        f_, t_ = navwalk.ladder_ends(s, keep, keep, lad)
        if f_ is not None and t_ is not None:
            ladder_pairs.append((int(f_), int(t_)))
    drop_pairs = navdrops.find(s, keep, LINK_STEP, PITCH) if DROP_LINKS else []
    if DROP_LINKS:
        log(f'drop links: {len(drop_pairs)} ({navdrops.STATS.get("regions", 0)} regions, '
            f'{navdrops.STATS.get("candidates", 0)} candidate ledges)')
    return ladder_pairs, drop_pairs


def kept_sectors(s, level, keep, xoff, zoff, sector_filter, log):
    """The sorted (X, Y) sectors that contain kept surfaces (only those of sector_filter when given)."""
    sectors = set()
    for k in np.nonzero(keep)[0]:
        c = level.col_of[k]
        x, z = s.min_x + (c % s.nx) * PITCH, s.min_z + (c // s.nx) * PITCH
        sectors.add((int(math.floor(x / 100.0)) - xoff, int(math.floor(z / 100.0)) - zoff))
    if sector_filter is not None:
        sectors &= set(sector_filter)
    sectors = sorted(sectors)
    log(f'{len(sectors)} sectors (pitch {PITCH:g})')
    return sectors


def sector_areas(level, sectors, xoff, zoff, grid_min, samples_path, log, t0):
    """Pass 1: per sector its areas (tiled, layered), the area of each surface, its origin and tile start.
    Returns {(X, Y): {'areas', 'area_of', 'origin', 'start'}}."""
    info = {}
    have = set(sectors)
    if not sectors and grid_min is None:
        # the level's own stage (not a detailed pass, whose caller keeps sectors without ground at the normal pitch)
        raise ValueError(f'{samples_path}: no walkable ground kept ({len(level.s.y)} surfaces) - nothing to build')
    min_x_sector, min_z_sector = grid_min or (min(X for X, _ in sectors), min(Y for _, Y in sectors))
    for X, Y in sectors:
        gi0, gj0, ox, oz = sector_origin_index(level, X, Y, xoff, zoff)
        if EDGE_SECTOR_RULE == 'grid_min':
            edge = (X == min_x_sector, Y == min_z_sector)
        else:
            edge = ((X - 1, Y) not in have, (X, Y - 1) not in have)
        start = tuple(0 if EDGE_SECTOR_TILES and e else -1 for e in edge)
        areas = build_areas(level, gi0, gj0, start, *tiling(((X & 0xFF) << 8) | (Y & 0xFF)))
        # order: tiles column-major, then layer = index within tile
        areas.sort(key=lambda a: (a['tile'][0], a['tile'][1], min(level.y[c[2]] for c in a['cells'])))
        per_tile = defaultdict(int)
        for a in areas:
            a['layer'] = per_tile[a['tile']]
            per_tile[a['tile']] += 1
        area_of = {c[2]: ai for ai, a in enumerate(areas) for c in a['cells']}
        info[(X, Y)] = {'areas': areas, 'area_of': area_of, 'origin': (gi0, gj0, ox, oz), 'start': start}
    log(f'{sum(len(v["areas"]) for v in info.values())} areas ({time.time() - t0:.0f} s)')
    return info


def border_line_runs(level, gi0, gj0, line, dX):
    """Every kept surface on a sector's border line (all layers: floors under roofs too), grouped into connected runs
    along it, one run per layer: [[(t, k)]]."""
    runs, active = [], []
    for t in WINDOW:
        i, j = (line, t) if dX else (t, line)
        nxt = []
        for k in level.surfaces(gi0 + i, gj0 + j):
            for r in active:
                if r[-1][0] == t - 1 and level.connected(r[-1][1], k, 0 if dX else 1, 1 if dX else 0):
                    r.append((t, k))
                    nxt.append(r)
                    active.remove(r)
                    break
            else:
                r = [(t, k)]
                runs.append(r)
                nxt.append(r)
        active = nxt
    return runs


def shared_border_nodes(level, info, new_id):
    """The border nodes shared between neighbouring sectors, spaced along each run of a border line (global ids).
    Returns {sector: [(id, pos, k)]}."""
    border_nodes = defaultdict(list)     # sector -> [(id, pos, k)]
    border_at = {}                       # surface -> (id, pos) of the border node on it
    for (X, Y), inf in info.items():
        gi0, gj0, ox, oz = inf['origin']
        for (dX, dY), line in (((1, 0), SECTOR_SAMPLES + 1), ((0, 1), SECTOR_SAMPLES + 1)):
            other = (X + dX, Y + dY)
            if other not in info:
                continue
            runs = border_line_runs(level, gi0, gj0, line, dX)
            for run in runs:
                n = max(1, int(math.ceil(len(run) * PITCH / BORDER_NODE_SPACING)))
                for q in range(n):
                    t, k = run[int((q + 0.5) * len(run) / n)]
                    i, j = (line, t) if dX else (t, line)
                    pos = (ox + i * PITCH, float(level.y[k]) + Y_OFFSET, oz + j * PITCH)
                    if DEDUP_GRAPH_NODES and k in border_at:
                        # a surface already holding a border node (a sector corner: on both lines): the same
                        # node, shared with this neighbour too - DICE has border ids in 3 and 4 sectors; two ids
                        # on one spot were joined by zero-length edges (11 of 13 campaign levels, 2026-10-20)
                        nid, pos = border_at[k]
                        for sec in ((X, Y), other):
                            if not any(b[0] == nid for b in border_nodes[sec]):
                                border_nodes[sec].append((nid, pos, k))
                        continue
                    nid = 0x80000000 | new_id()
                    border_at[k] = (nid, pos)
                    border_nodes[(X, Y)].append((nid, pos, k))
                    border_nodes[other].append((nid, pos, k))
    return border_nodes


def attach_carried(level, info, carried, border_nodes):
    """Shared nodes from another stage (seams with sectors at another pitch) added to border_nodes on this grid's
    ground at the node. Returns the ids of those with no ground (dropped)."""
    s = level.s
    # shared nodes from another stage (seams with sectors at another pitch): on this grid's ground at the node, the
    # nearest kept surface of the sector within 0.6 m of its height; nodes with no such ground are reported dropped
    dropped = set()
    for sec, nodes in (carried or {}).items():
        if sec not in info:
            dropped.update(nid for nid, _pos in nodes)
            continue
        own = info[sec]['area_of']
        for nid, pos in nodes:
            # within CARRY_RADIUS of the node, on its floor; ground of a larger area first (not a stray speck), then
            # the nearest - a 3 x 3 search (0.2 m at pitch 0.2) left 74 ONS_MP_002 seam nodes without ground and put
            # others on specks, and the detailed core lost its links to the normal sectors (connectivity 91.6 -> 60.9)
            gi, gj = int(round((pos[0] - s.min_x) / PITCH)), int(round((pos[2] - s.min_z) / PITCH))
            r = int(math.ceil(CARRY_RADIUS / PITCH))
            cand = []
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    dxz = math.hypot(di, dj) * PITCH
                    if dxz > CARRY_RADIUS:
                        continue
                    for k in level.surfaces(gi + di, gj + dj):
                        if k in own and abs(level.y[k] + Y_OFFSET - pos[1]) <= 0.6:
                            size = len(info[sec]['areas'][own[k]]['cells'])
                            cand.append((size < 25, dxz, k))
            if not cand:
                dropped.add(nid)
                continue
            k = min(cand)[2]
            border_nodes[sec].append((nid, pos, k))
    return dropped


def traced_edge_sets(level, info, log):
    """Boundary edges of every sector, then the one-way portal pieces decided level-wide (ONE_WAY_PORTALS) and the
    others dropped. Returns {sector: (per_area, one_way)}."""
    edge_sets = {}
    for (X, Y), inf in sorted(info.items()):
        gi0, gj0, ox, oz = inf['origin']
        edge_sets[(X, Y)] = trace_edges(level, inf['areas'], inf['area_of'], gi0, gj0)
    n_cand = sum(len(v[1]) for v in edge_sets.values())
    if ONE_WAY_PORTALS == 'restore':
        owners = defaultdict(list)
        for sec, inf in info.items():
            for k, ai in inf['area_of'].items():
                owners[k].append((sec, ai))
        shared = [(o[0], x) for o in owners.values() if len(o) > 1 for x in o[1:]]
        keep = restore_one_way(edge_sets, shared)
    else:
        keep = {sec: set(range(len(v[1]))) if ONE_WAY_PORTALS == 'keep' else set() for sec, v in edge_sets.items()}
    for sec, (per_area, one_way) in edge_sets.items():
        drop_one_way(per_area, one_way, keep.get(sec, set()))
    log(f'one-way portal pieces: {n_cand}, kept one-way {sum(len(v) for v in keep.values())} ({ONE_WAY_PORTALS})')
    return edge_sets


def sector_mesh(level, info, X, Y, inf, edges, sid):
    """A sector's mesh: its areas (portals, borders, D lines, outline, F cells, height range) in their tiles, the
    neighbour links and the rectangle. Returns (mesh, traced, rect)."""
    gi0, gj0, ox, oz = inf['origin']
    areas = inf['areas']
    level.blocked = set()            # D sides of this sector only (another sector may hold the cells in two areas)
    details = (detail_pass(level, areas, edges[0], ox, oz, gi0, gj0) if DETAIL_FEATURES
               else [([], [])] * len(areas))
    traced = trace_emit(level, areas, edges[0], gi0, gj0, ox, oz)
    mesh_areas = []
    ntiles = tiling(sid)[1]
    tiles = [[] for _ in range(ntiles * ntiles)]
    for ai, (a, (portals, borders, outline), (d_lines, f_cells)) in enumerate(zip(areas, traced, details)):
        ys = [level.y[c[2]] for c in a['cells']]
        # DICE's area height range: from its lowest ground to its highest ground + 1.52 m (all normal-pitch
        # areas of sp_004_b: median +0.000 / +1.520; NavKit had -0.95 / +1.55, a metre of extra room below
        # each area, so a lower floor's range reached the deck above it - 2026-10-19)
        lo, hi = min(ys) + AREA_Y_MIN, max(ys) + AREA_Y_MAX
        mesh_areas.append(kymesh.Area(sid, a['tile'][0], a['tile'][1], a['layer'], RADIUS, PITCH, hi, lo,
                                      portals, [b[0] for b in borders], d_lines, outline, f_cells))
        a['borders'] = borders
        tiles[a['tile'][0] * ntiles + a['tile'][1]].append(kymesh.TileItem(ai, lo, hi))
    mesh_links = sector_links(X, Y, areas, mesh_areas, same_grid_resolver(level, info, X, Y))
    # the runtime's tile grid starts at the rectangle's minimum (DICE: tile lines 39.6 m past it on 3,748 sector
    # axes) - so an edge sector, whose tiles start at its own edge, starts its rectangle there too (DICE 0x101:
    # -800.0 instead of -800.8). The maximum side keeps the 1.1-pitch margin (DICE drops it at the grid's maximum).
    sx, sz = inf['start']
    rect = (ox + sx * PITCH, ox + 100 + 1.1 * PITCH, oz + sz * PITCH, oz + 100 + 1.1 * PITCH)
    mesh = kymesh.Mesh(2, 0, 0, sid, RESOLUTION, PITCH, rect, ntiles, ntiles, RADIUS, tiles, mesh_areas,
                       mesh_links, b'\0' * 4)
    return mesh, traced, rect


def sector_output(level, info, X, Y, inf, edges, borders, new_id, order_points, ladder_pairs, drop_pairs):
    """A sector's mesh and route graph, its link counts (DROP_STATS / LADDER_STATS) and its areas' cells
    (AREA_CELLS). Returns (mesh, (sid, bounds, mesh, pts, pairs, (ox, oz)))."""
    gi0, gj0, ox, oz = inf['origin']
    areas, area_of = inf['areas'], inf['area_of']
    sid = ((X & 0xFF) << 8) | (Y & 0xFF)
    mesh, traced, rect = sector_mesh(level, info, X, Y, inf, edges, sid)
    orders = [p for p in (order_points or ()) if ox <= p[0] < ox + 100 and oz <= p[2] < oz + 100]
    lads = [(f_, t_) for f_, t_ in ladder_pairs if f_ in area_of and t_ in area_of]
    drops = [(f_, t_) for f_, t_ in drop_pairs if f_ in area_of and t_ in area_of]
    # per sector, a later stage's entry replacing an earlier one's (stage b over a's detailed sectors): build()
    # counts the sectors written. A drop whose ledge and landing lie in two sectors is not linked (as for
    # ladders: the graph is per sector)
    DROP_STATS.setdefault('sectors', {})[sid] = sum(1 for f_, t_ in drop_pairs if (f_ in area_of) != (t_ in area_of))
    LADDER_STATS.setdefault('sectors', {})[sid] = (
        len(lads), sum(1 for f_, t_ in ladder_pairs if (f_ in area_of) != (t_ in area_of)))
    pts, pairs = build_graph(level, areas, area_of, traced, borders, gi0, gj0, ox, oz, new_id, orders,
                             lads, drops)
    navislands.note_nodes(level, pts)        # which nodes stand on terrain (SHADOW_OBJECTS_ONLY)
    ys = [a.min_y for a in mesh.areas] + [a.max_y for a in mesh.areas]
    bounds_ = (rect[0], rect[1], min(ys), max(ys), rect[2], rect[3])
    # the areas' own cells for the sidecar file (write_file): global cell indices at this pitch, ground heights
    base_i, base_j = int(round(ox / PITCH)), int(round(oz / PITCH))
    AREA_CELLS[sid] = [(np.array([base_i + c[0] for c in a['cells']], np.int32),
                        np.array([base_j + c[1] for c in a['cells']], np.int32),
                        np.array([level.y[c[2]] for c in a['cells']], np.float32), PITCH) for a in areas]
    return mesh, (sid, bounds_, mesh, pts, pairs, (ox, oz))


def sector_links(X, Y, areas, mesh_areas, resolve):
    """The neighbour links of a sector: for every border portal (C), the area across it - resolve(other, area,
    border) gives its (tile x, tile z, layer) in the other sector, or None."""
    links = defaultdict(lambda: defaultdict(list))
    for ai, a in enumerate(areas):
        for ci, b in enumerate(a['borders']):
            bp = b[0]
            di, dj = int(bp.direction[0]), int(bp.direction[2])
            other = (X + di, Y + dj)
            hit = resolve(other, a, b)
            if hit is None:
                continue
            rel = 0x50 + len(mesh_areas[ai].portals) * 0x20 + ci * 0x2C
            links[other][hit].append((ai, rel))
    mesh_links = []
    for other in sorted(links):
        oid = ((other[0] & 0xFF) << 8) | (other[1] & 0xFF)
        mesh_links.append(kymesh.NeighbourLink(oid, [kymesh.LinkObject(h, refs) for h, refs in sorted(links[other].items())]))
    return mesh_links


def border_cell(a, b):
    """(i, j, k) of the area's cell just inside a border portal (the middle of its run), and the step (di, dj)."""
    bp, t0_, t1_, line = b
    di, dj = int(bp.direction[0]), int(bp.direction[2])
    tmid = (t0_ + t1_) // 2
    i, j = (int(line + 0.5 * di), tmid) if di else (tmid, int(line + 0.5 * dj))
    k = next((c[2] for c in a['cells'] if (c[0], c[1]) == (i - di, j - dj)), None)
    return i - di, j - dj, k, di, dj


def same_grid_resolver(level, info, X, Y):
    """resolve for sector_links within one stage: the connected surface across, in the other sector's areas."""
    gi0, gj0, _ox, _oz = info[(X, Y)]['origin']

    def resolve(other, a, b):
        if other not in info:
            return None
        i, j, k_here, di, dj = border_cell(a, b)
        if k_here is None:
            return None
        n = level.neighbour(k_here, gi0 + i, gj0 + j, di, dj)
        oth = info[other]
        if n is None or n not in oth['area_of']:
            return None
        ta = oth['areas'][oth['area_of'][n]]
        return (ta['tile'][0], ta['tile'][1], ta['layer'])
    return resolve


def cross_grid_resolver(here, there, X, Y):
    """resolve for sector_links across two stages (a detailed sector next to a normal one): the cell just across
    the border, in world coordinates, looked up on the other stage's grid - its kept surface nearest in height
    (within 0.8 m) that belongs to the other sector's areas."""
    lv, inf = here['level'], here['info'][(X, Y)]
    _gi0, _gj0, ox, oz = inf['origin']
    p_here, olv, p_there = here['pitch'], there['level'], there['pitch']
    os_ = olv.s

    def resolve(other, a, b):
        if other not in there['info']:
            return None
        i, j, k_here, di, dj = border_cell(a, b)
        if k_here is None:
            return None
        x = ox + i * p_here + di * max(p_here, p_there)
        z = oz + j * p_here + dj * max(p_here, p_there)
        y = lv.y[k_here]
        oth = there['info'][other]
        gi, gj = int(round((x - os_.min_x) / p_there)), int(round((z - os_.min_z) / p_there))
        cand = [k for k in olv.surfaces(gi, gj) if k in oth['area_of'] and abs(olv.y[k] - y) <= 0.8]
        if not cand:
            return None
        n = min(cand, key=lambda k: abs(olv.y[k] - y))
        ta = oth['areas'][oth['area_of'][n]]
        return (ta['tile'][0], ta['tile'][1], ta['layer'])
    return resolve


def merge_detailed(a, detailed, xoff, zoff, seeds, common, log):
    """The second pass: DICE's detailed-zone sectors built at pitch 0.2 from their own sampling run (stage b),
    merged with the normal build a. Stage b is seeded from a's walkable ground in those sectors (ground joined to the
    rest of the level only through normal sectors would otherwise be lost) and continues a's node ids. Shared nodes
    on the seams between a detailed and a normal sector come from a and are attached to b's ground (dropped from
    both sides where b has none there); neighbour links across those seams are found by position on the other grid.
    Returns the merged graphs (normal sectors from a, detailed from b)."""
    dset = set(detailed['sectors']) & a['sectors']
    alv = a['level']
    sa = alv.s
    # seeds: a's kept ground in the detailed sectors
    pts = []
    for k in np.nonzero(alv.keep)[0]:
        c = alv.col_of[k]
        x, z = sa.min_x + (c % sa.nx) * sa.pitch, sa.min_z + (c // sa.nx) * sa.pitch
        if (int(math.floor(x / 100.0)) - xoff, int(math.floor(z / 100.0)) - zoff) in dset:
            pts.append((x, float(alv.y[k]), z))
    # a's shared nodes on the seams between a detailed and a normal sector, per detailed sector
    owners = defaultdict(set)
    for sec, nodes in a['border_nodes'].items():
        for nid, _pos, _k in nodes:
            owners[nid].add(sec)
    carried = defaultdict(list)
    for sec in dset:
        for nid, pos, _k in a['border_nodes'].get(sec, ()):
            if owners[nid] - dset:
                carried[sec].append((nid, pos))
    log(f'detailed pass: {len(dset)} sectors at pitch {DETAILED_PITCH}, {len(pts)} seed points from the normal build, '
        f'{sum(len(v) for v in carried.values())} shared nodes on mixed seams')
    set_pitch(DETAILED_PITCH)
    grid_min = (min(X for X, _ in a['sectors']), min(Y for _, Y in a['sectors']))
    b = stage(detailed['samples'], detailed.get('high'), xoff, zoff, seeds=seeds, **common,
              sector_filter=dset, grid_min=grid_min, id_start=a['next_id'], carried=carried, seed_points=pts)
    missing = dset - b['sectors']
    if missing:
        log(f'detailed pass: {len(missing)} sectors without ground at pitch {DETAILED_PITCH} kept at the normal pitch: '
            + ', '.join(f'{(X & 0xFF) << 8 | (Y & 0xFF):04x}' for X, Y in sorted(missing)))
    dset = b['sectors']
    drop = b['dropped']
    log(f'detailed pass: {len(drop)} mixed-seam shared nodes without ground at pitch {DETAILED_PITCH} dropped')
    # links across mixed seams: b's sectors to a's normal neighbours, a's normal sectors to b's detailed ones
    a_norm = {sec: g for sec, g in zip(sorted(a['sectors']), a['graphs']) if sec not in dset}
    b_det = {sec: g for sec, g in zip(sorted(b['sectors']), b['graphs'])}
    a_view = dict(a, info={s_: i for s_, i in a['info'].items() if s_ not in dset})
    for here, there, mine in ((b, a_view, b_det), (a, b, a_norm)):
        for (X, Y), g in mine.items():
            areas, mesh = here['info'][(X, Y)]['areas'], g[2]
            same = same_grid_resolver(here['level'], {s_: i for s_, i in here['info'].items()
                                                      if s_ in mine}, X, Y)
            cross = cross_grid_resolver(here, there, X, Y)
            mesh.links = sector_links(X, Y, areas, mesh.areas,
                                      lambda other, ar, bd, s=same, c=cross: s(other, ar, bd) if other in mine else c(other, ar, bd))
    merged = []
    for sec in sorted(set(a_norm) | set(b_det)):
        sid, bnd, mesh, pts_, pairs, origin = a_norm[sec] if sec in a_norm else b_det[sec]
        if drop and sec in a_norm:
            # a's normal sector: drop the mixed-seam shared nodes stage b had no ground for
            gone = {q for q, p in enumerate(pts_) if p[0] in drop}
            pairs = [p for p in pairs if p[0] not in gone and p[1] not in gone]
        merged.append((sid, bnd, mesh, pts_, pairs, origin))
    log(f'merged: {len(a_norm)} normal + {len(b_det)} detailed sectors')
    return merged


def write_file(graphs, out_path, log, t0):
    """Order-node pruning, final node / edge tables, and the file."""
    hpd_sectors = []
    stamp = time.localtime()
    date = (stamp.tm_year % 100) * 10000 + (stamp.tm_mon - 1) * 100 + stamp.tm_mday
    clock = stamp.tm_hour * 1000000 + stamp.tm_min * 10000 + stamp.tm_sec * 100
    if ORDER_IDS and ORDER_MIN_ISLAND:
        graphs = prune_order_nodes(graphs)
    linked = {pts[i][0] for _sid, _b, _m, pts, pairs, _o in graphs for a_, b_, _d in pairs for i in (a_, b_)}
    for sid, bounds, mesh, pts, pairs, origin in graphs:
        nodes, edges = finalize_graph(pts, pairs, linked, origin)
        hpd_sectors.append(hpdtool.Sector(sid, bounds, 0, date, clock, nodes, edges, kymesh.write_mesh(mesh)))
    out = hpdtool.HpdFile(HEADER, hpd_sectors)
    data = hpdtool.write(out)
    open(out_path, 'wb').write(data)
    log(f'wrote {out_path}: {len(hpd_sectors)} sectors, {sum(len(x.nodes) for x in hpd_sectors)} nodes, '
        f'{sum(len(x.edges) for x in hpd_sectors)} edges, {len(data)} bytes ({time.time() - t0:.0f} s)')
    try:
        write_cells(graphs, out_path, data, log)
    except (OSError, ValueError) as e:     # the sidecar is optional: the exporter rasterises outlines without it
        # (OSError: the .npz not writable - locked, disk full; ValueError: numpy refusing the recorded arrays)
        log(f'cells: not written ({type(e).__name__}: {e})')


def cells_path(hpd_path):
    """The sidecar file of a build: <name>.cells.npz beside <name>.hpd."""
    return os.path.splitext(hpd_path)[0] + '.cells.npz'


def write_cells(graphs, out_path, data, log):
    """The sidecar of the build just written (AREA_CELLS, in the file's sector and area order): per area its sector id,
    pitch and cell count; the cells' global indices and ground heights; the build's SHA-256, so a reader can tell it
    belongs to this file. Nothing is written when a sector's areas were not recorded."""
    import hashlib
    sids, pitch, count, ii, jj, yy = [], [], [], [], [], []
    for sid, _b, mesh, _p, _q, _o in graphs:
        rec = AREA_CELLS.get(sid)
        if rec is None or len(rec) != len(mesh.areas):
            log(f'cells: sector {sid:04x} not recorded; no {os.path.basename(cells_path(out_path))} written')
            return
        for i_, j_, y_, p_ in rec:
            sids.append(sid)
            pitch.append(p_)
            count.append(len(i_))
            ii.append(i_)
            jj.append(j_)
            yy.append(y_)
    cat = lambda v, t: np.concatenate(v).astype(t) if v else np.zeros(0, t)
    np.savez_compressed(cells_path(out_path), sector=np.array(sids, np.int32), pitch=np.array(pitch, np.float32),
                        count=np.array(count, np.int64), i=cat(ii, np.int32), j=cat(jj, np.int32), y=cat(yy, np.float32),
                        hpd_sha256=np.array(hashlib.sha256(data).hexdigest()))
    log(f'cells: {len(count)} areas, {sum(count)} cells -> {cells_path(out_path)}')


def read_cells(hpd_path):
    """The sidecar of a build, or None when it is missing or belongs to another file: [(sector id, pitch, i, j, y)] per
    area in the file's order."""
    import hashlib
    p = cells_path(hpd_path)
    if not os.path.isfile(p):
        return None
    z = np.load(p)
    h = hashlib.sha256(open(hpd_path, 'rb').read()).hexdigest()
    if str(z['hpd_sha256']) != h:
        return None
    count = z['count']
    ends = np.cumsum(count)
    starts = ends - count
    ci, cj, cy = z['i'], z['j'], z['y']                 # read once (each z[...] reads the array from the file again)
    return [(int(s_), float(p_), ci[a:b], cj[a:b], cy[a:b])
            for s_, p_, a, b in zip(z['sector'], z['pitch'], starts, ends)]


def _crosses_d(level, blocked, k, nk, gi, gj, di, dj, xfirst=None):
    """A step from surface k (column gi, gj) to nk crosses a D side: a straight step across one; a diagonal step when
    the straight line passes through the side column it leaves first (xfirst: True = (di, 0), False = (0, dj), None =
    exactly through the corner - then either way) and one of that way's two sides is a D side."""
    if not (di and dj):
        return (k, nk) in blocked
    ways = [(di, 0)] if xfirst else [(0, dj)] if xfirst is False else [(di, 0), (0, dj)]
    for a, b in ways:
        s_ = level.neighbour(k, gi, gj, a, b)
        if s_ is not None and ((k, s_) in blocked or (s_, nk) in blocked):
            return True
    return False


def line_walkable(level, k0, a, b, gi0, gj0, k1):
    """Straight line from local sample a to b (i, j) over connected kept surfaces, starting on k0, ending on k1."""
    (i0, j0), (i1, j1) = a, b
    n = max(abs(i1 - i0), abs(j1 - j0))
    k, ci, cj = k0, i0, j0
    for t in range(1, n + 1):
        ti = i0 + round((i1 - i0) * t / n)
        tj = j0 + round((j1 - j0) * t / n)
        di, dj = ti - ci, tj - cj
        if di or dj:
            nk = level.neighbour(k, gi0 + ci, gj0 + cj, di, dj)
        else:
            continue
        if nk is None:
            return False
        blocked = getattr(level, 'blocked', ())
        xfirst = None
        if blocked and di and dj:
            # where the straight line crosses the column line between ci and ci + di: still in row cj -> x first
            zc = j0 + (j1 - j0) * (ci + di * 0.5 - i0) / (i1 - i0)
            off = abs(zc - cj)
            xfirst = None if abs(off - 0.5) < 1e-9 else off < 0.5
        if blocked and _crosses_d(level, blocked, k, nk, gi0 + ci, gj0 + cj, di, dj, xfirst):
            return False                 # a D line between them (DETAIL_BLOCKS_EDGES)
        k, ci, cj = nk, ti, tj
    return k == k1 or abs(level.y[k] - level.y[k1]) <= 0.6


def build_graph(level, areas, area_of, traced, borders, gi0, gj0, ox, oz, new_id, orders=(), ladders=(), drops=()):
    """A sector's route graph: nodes (area centres, portals, border nodes, cover), walkable edges, ladder and drop
    links, the groups joined, detours repaired and order nodes added. Returns (pts, pairs)."""
    pts, cell_of = place_nodes(level, areas, traced, borders, ox, oz, new_id)
    pairs = walkable_pairs(level, pts, gi0, gj0)
    for kf, kt in ladders:
        # a ladder link: a node on the ground at its foot and at its top, joined directly (the climb is no walkable
        # line); the runtime's mover walks the bot into the ladder, which takes any soldier who walks into it
        ends = [link_end_node(level, pts, cell_of, k, ox, oz, new_id) for k in (kf, kt)]
        pairs.append((ends[0], ends[1], math.dist(pts[ends[0]][1], pts[ends[1]][1])))
    drop_ends = []
    for ku, kl in drops:
        # a drop link: a node on the ledge and one on the ground below, joined one way (DROP_IDS). The nodes now, so the
        # group joining below walks them into their own floors; the link after it, so it does not count the two
        # floors joined (it would leave out the walk back up where there is one)
        ends = [link_end_node(level, pts, cell_of, k, ox, oz, new_id) for k in (ku, kl)]
        if ends[0] != ends[1]:
            drop_ends.append(tuple(ends))
    node_at, cell_ij = join_groups(level, areas, pts, pairs, gi0, gj0, ox, oz, new_id)
    if DETOUR_REPAIR:
        for p in range(DETOUR_PASSES):
            added = repair_detours(level, pts, pairs, node_at, cell_ij, gi0, gj0, ox, oz, new_id)
            REPAIR_STATS[p] = REPAIR_STATS.get(p, 0) + added
    if ORDER_NODES and orders:
        ORDER_STATS['added'] = ORDER_STATS.get('added', 0) + add_order_nodes(level, pts, pairs, node_at, area_of, orders,
                                                                             gi0, gj0, ox, oz, new_id)
    joined = {frozenset((a_, b_)) for a_, b_, _d in pairs}
    for u, l_ in drop_ends:
        if frozenset((u, l_)) in joined:
            continue                    # a walk pair joins the two nodes already: no drop (finalize_graph would take
                                        # the walk's way back up)
        DROP_IDS.add((pts[u][0], pts[l_][0]))
        pairs.append((u, l_, math.dist(pts[u][1], pts[l_][1])))
    return pts, pairs


def place_nodes(level, areas, traced, borders, ox, oz, new_id):
    """The sector's nodes before any edge: area centres, portal midpoints, the border nodes and the NODE_COVER ones,
    one per surface. Returns (pts [(id, pos, k, local i, local j)], cell_of {surface: (i, j)})."""
    pts = []    # (id, pos, k, local i, local j)
    # one node per surface: an area-centre or portal node whose surface already holds a node (another area's or
    # portal's, or a border node) is not added - DICE's route graphs have no two nodes at one position except a
    # border node in both of its sectors (sp_004_b: 1,515 shared positions, all border pairs with one id); NavKit
    # had 377 duplicate pairs joined by zero-length edges (2026-10-20)
    taken = {k for _nid, _pos, k in borders} if DEDUP_GRAPH_NODES else set()
    # ... and none at a border node's position: a shared node carried from the other pitch's build is attached to
    # ground up to CARRY_RADIUS away, so its surface can differ from the one under its own position - an area node
    # on that one stood on the border node, joined by a zero-length edge (sp_007 sector 0e0b, 2026-10-23)
    taken_pos = {tuple(round(v, 3) for v in pos) for _nid, pos, _k in borders} if DEDUP_GRAPH_NODES else set()

    def add(i, j, k):
        pos = (ox + i * PITCH, float(level.y[k]) + Y_OFFSET, oz + j * PITCH)
        if DEDUP_GRAPH_NODES:
            key = tuple(round(v, 3) for v in pos)
            if k in taken or key in taken_pos:
                return
            taken.add(k)
            taken_pos.add(key)
        pts.append((new_id(), pos, k, i, j))
    for a in areas:
        if len(a['cells']) < 4:
            continue
        ci = sum(c[0] for c in a['cells']) / len(a['cells'])
        cj = sum(c[1] for c in a['cells']) / len(a['cells'])
        i, j, k = min(a['cells'], key=lambda c: (c[0] - ci) ** 2 + (c[1] - cj) ** 2)
        add(i, j, k)
    cell_of = {c[2]: (c[0], c[1]) for a in areas for c in a['cells']}
    for ai, (portals, _b, _o) in enumerate(traced):
        for p in portals:
            if p.target < ai:
                continue          # one node per portal pair
            mx, mz = (p.p0[0] + p.p1[0]) / 2, (p.p0[2] + p.p1[2]) / 2
            best = min(areas[ai]['cells'], key=lambda c: (ox + c[0] * PITCH - mx) ** 2 + (oz + c[1] * PITCH - mz) ** 2)
            i, j, k = best
            add(i, j, k)
    for nid, pos, k in borders:
        # the node's own surface cell (a node carried from another pitch's build sits up to CARRY_RADIUS from it;
        # walkable lines must start on that cell, not on the cell under the position)
        i, j = cell_of.get(k) or (int(round((pos[0] - ox) / PITCH)), int(round((pos[2] - oz) / PITCH)))
        pts.append((nid, pos, k, i, j))
    cover = NODE_COVER.get('detailed' if abs(PITCH - DETAILED_PITCH) < 1e-6 else 'normal')
    if cover:
        add_cover_nodes(level, areas, pts, add, ox, oz, cover)
    return pts, cell_of


def walkable_pairs(level, pts, gi0, gj0):
    """The edges: node pairs within DIST_EDGE_MAX whose straight line is walkable (Gabriel-pruned by GABRIEL_EDGES).
    Returns [(a, b, distance)]."""
    # edges: pairs within DIST_EDGE_MAX whose straight line is walkable
    cell = DIST_EDGE_MAX
    grid = defaultdict(list)
    for idx, p in enumerate(pts):
        grid[(int(p[1][0] // cell), int(p[1][2] // cell))].append(idx)
    pairs = []
    for idx, p in enumerate(pts):
        gx, gz = int(p[1][0] // cell), int(p[1][2] // cell)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for q in grid.get((gx + dx, gz + dz), ()):
                    if q <= idx:
                        continue
                    d = math.dist(p[1], pts[q][1])
                    if d <= DIST_EDGE_MAX and line_walkable(level, p[2], (p[3], p[4]), (pts[q][3], pts[q][4]), gi0, gj0, pts[q][2]):
                        pairs.append((idx, q, d))
    if GABRIEL_EDGES == 'all' or (GABRIEL_EDGES == 'detailed' and abs(PITCH - DETAILED_PITCH) < 1e-6):
        pairs = gabriel_prune(pts, pairs)
    return pairs


def link_end_node(level, pts, cell_of, k, ox, oz, new_id):
    """The index of the node on surface k (a ladder or drop link's end), added there when it has none."""
    idx = next((q for q, p in enumerate(pts) if p[2] == k), None)
    if idx is None:
        i, j = cell_of[k]
        pts.append((new_id(), (ox + i * PITCH, float(level.y[k]) + Y_OFFSET, oz + j * PITCH), k, i, j))
        idx = len(pts) - 1
    return idx


def join_groups(level, areas, pts, pairs, gi0, gj0, ox, oz, new_id):
    """Connect remaining groups: grid search (8-connected, step rule) from one group to the nearest node of another,
    then string-pull the path into straight walkable legs with waypoint nodes. Returns (node_at, cell_ij)."""
    parent = list(range(len(pts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a_, b_, _ in pairs:
        parent[find(a_)] = find(b_)
    node_at = {}
    for idx, p in enumerate(pts):
        node_at.setdefault(p[2], idx)
    cell_ij = {c[2]: (c[0], c[1]) for a in areas for c in a['cells']}
    for p in pts:
        cell_ij.setdefault(p[2], (p[3], p[4]))
    changed = True
    while changed:
        changed = False
        groups = defaultdict(list)
        for idx in range(len(pts)):
            groups[find(idx)].append(idx)
        if len(groups) < 2:
            break
        for root, members in sorted(groups.items(), key=lambda g: len(g[1])):
            if find(members[0]) != root:
                continue
            path = path_to_other_group(level, pts, members, root, find, node_at, cell_ij, gi0, gj0)
            if path is None:
                continue
            way = string_pull(level, path, cell_ij, gi0, gj0)
            chain = []
            for k in way:
                if k in node_at:
                    chain.append(node_at[k])
                else:
                    i, j = cell_ij[k]
                    pts.append((new_id(), (ox + i * PITCH, float(level.y[k]) + Y_OFFSET, oz + j * PITCH), k, i, j))
                    parent.append(len(pts) - 1)
                    node_at[k] = len(pts) - 1
                    chain.append(len(pts) - 1)
            for u, v in zip(chain, chain[1:]):
                pairs.append((u, v, math.dist(pts[u][1], pts[v][1])))
                parent[find(u)] = find(v)
            changed = True
    return node_at, cell_ij


def path_to_other_group(level, pts, members, root, find, node_at, cell_ij, gi0, gj0):
    """Breadth-first grid search (8-connected, step rule) from a group's surfaces to the nearest surface holding a node
    of another group: the surfaces from a member's to the target's, or None."""
    prev = {pts[m][2]: None for m in members}
    q = deque(pts[m][2] for m in members)
    target = None
    while q and target is None:
        k = q.popleft()
        i, j = cell_ij[k]
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if not (di or dj) or (i + di) not in WINDOW or (j + dj) not in WINDOW:
                    continue
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj)
                if n is None or n in prev:
                    continue
                prev[n] = k
                cell_ij.setdefault(n, (i + di, j + dj))
                if n in node_at and find(node_at[n]) != root:
                    target = n
                    break
                q.append(n)
            if target is not None:
                break
    if target is None:
        return None
    path = [target]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])
    path.reverse()                       # from a member's surface to the target's surface
    return path


def string_pull(level, path, cell_ij, gi0, gj0):
    """A surface path pulled into straight walkable legs of at most DIST_EDGE_MAX: the surfaces at the legs' ends."""
    # string pulling
    way = [path[0]]
    a = 0
    while a < len(path) - 1:
        b = len(path) - 1
        while b > a + 1 and not (math.dist(cell_ij[path[a]], cell_ij[path[b]]) * PITCH <= DIST_EDGE_MAX and
                                 line_walkable(level, path[a], cell_ij[path[a]], cell_ij[path[b]], gi0, gj0, path[b])):
            b -= 1
        way.append(path[b])
        a = b
    return way


def add_cover_nodes(level, areas, pts, add, ox, oz, cover):
    """NODE_COVER: in every area of 4+ cells, add route nodes (through add, so one per surface) at the cell farthest from
    all nodes on the same floor (|dy| < 2.5 m) until no cell is farther than cover metres."""
    for a in areas:
        if len(a['cells']) < 4:
            continue
        C = np.array([(ox + i * PITCH, float(level.y[k]) + Y_OFFSET, oz + j * PITCH) for i, j, k in a['cells']])
        x0, x1, z0, z1 = C[:, 0].min() - cover, C[:, 0].max() + cover, C[:, 2].min() - cover, C[:, 2].max() + cover
        P = np.array([p[1] for p in pts if x0 <= p[1][0] <= x1 and z0 <= p[1][2] <= z1], float).reshape(-1, 3)
        d = np.full(len(C), np.inf)
        for q in P:
            same = np.abs(C[:, 1] - q[1]) < 2.5
            d = np.where(same, np.minimum(d, np.hypot(C[:, 0] - q[0], C[:, 2] - q[2])), d)
        while True:
            w = int(np.argmax(d))
            if d[w] <= cover:
                break
            i, j, k = a['cells'][w]
            n = len(pts)
            add(i, j, k)
            q = C[w]
            d[w] = 0.0                               # placed (or the surface already held a node)
            if len(pts) > n:
                same = np.abs(C[:, 1] - q[1]) < 2.5
                d = np.where(same, np.minimum(d, np.hypot(C[:, 0] - q[0], C[:, 2] - q[2])), d)


def gabriel_prune(pts, pairs):
    """The pairs that are Gabriel edges of the sector's nodes (no other node within 2.5 m of height inside the circle
    with the pair as diameter), plus the shortest dropped pairs needed to keep every group of nodes the full pair set
    joins joined (so pruning never splits a route)."""
    P = np.array([p[1] for p in pts], float)
    keep, rest = [], []
    for a_, b_, d in pairs:
        m = (P[a_] + P[b_]) / 2
        r2 = (d / 2) ** 2 - 1e-9
        near = (np.abs(P[:, 1] - m[1]) < 2.5) & (((P[:, 0] - m[0]) ** 2 + (P[:, 2] - m[2]) ** 2) < r2)
        near[[a_, b_]] = False
        (rest if near.any() else keep).append((a_, b_, d))
    parent = list(range(len(pts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a_, b_, _d in keep:
        parent[find(a_)] = find(b_)
    for a_, b_, d in sorted(rest, key=lambda e: e[2]):
        if find(a_) != find(b_):
            parent[find(a_)] = find(b_)
            keep.append((a_, b_, d))
    return keep


def prune_order_nodes(graphs):
    """Order nodes in a route island under ORDER_MIN_ISLAND nodes lose their edges (finalize_graph then
    drops them as unlinked); other nodes are untouched. An order node that joins a small pocket becomes the
    nearest node for points next to the main network, and the runtime takes the nearest node (sp_009: the order
    nodes cut connectivity 4.4 points through such pockets; 2026-10-20). Components over all sectors (border ids
    join them). Records the removed ones in ORDER_STATS['removed'] as (id, position, component size)."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for _sid, _b, _m, pts, pairs, _o in graphs:
        for p in pts:
            find(p[0])
        for a_, b_, _d in pairs:
            ra, rb = find(pts[a_][0]), find(pts[b_][0])
            if ra != rb:
                parent[ra] = rb
    size = defaultdict(int)
    for x in list(parent):
        size[find(x)] += 1
    if not size:
        return graphs
    drop = {i for i in ORDER_IDS if size[find(i)] < ORDER_MIN_ISLAND}
    seen = set()
    for _sid, _b, _m, pts, _pairs, _o in graphs:
        for p in pts:
            if p[0] in drop and p[0] not in seen:
                seen.add(p[0])
                ORDER_STATS.setdefault('removed', []).append((p[0], tuple(round(v, 2) for v in p[1]), size[find(p[0])]))
    return [(sid, b, m, pts, [q for q in pairs if pts[q[0]][0] not in drop and pts[q[1]][0] not in drop], o)
            for sid, b, m, pts, pairs, o in graphs]


def add_order_nodes(level, pts, pairs, node_at, area_of, orders, gi0, gj0, ox, oz, new_id):
    """A route node at each AI order position (AreaGeometryEntityData) on this sector's walkable ground whose nearest
    route node on the same floor is farther than ORDER_NODE_MIN, linked to every node within DIST_EDGE_MAX it reaches
    in a straight walkable line. The runtime resolves a go-to point to the nearest gathered node (R = 15 m, the first
    candidate accepted; 0x0105ED80) - at sp_004_b's stairs top NavKit's nearest node was 3.6-6.3 m from the order
    points, DICE's 0.2 m, and those requests failed (2026-10-20). Returns the number of nodes added."""
    s = level.s
    added = 0
    for x, y, z in orders:
        gi, gj = int(round((x - s.min_x) / PITCH)), int(round((z - s.min_z) / PITCH))
        cands = [k for k in level.surfaces(gi, gj) if k in area_of and abs(level.y[k] - y) <= 1.0]
        if not cands:
            continue
        k = min(cands, key=lambda q: abs(level.y[q] - y))
        if k in node_at:
            continue
        pos = (ox + (gi - gi0) * PITCH, float(level.y[k]) + Y_OFFSET, oz + (gj - gj0) * PITCH)
        # a node within ORDER_NODE_MIN counts only when the position walks to it in a straight line: one on the other
        # side of a wall, or on a ledge 0.6 m up, served nothing (mp_10's spawn at (24.0, 113.3, 6.4), in a walled
        # pocket: a node 1.8 m away beyond its wall stopped the order node, the pocket's own nodes were 6.8 m off and
        # rsnav found no start node in sight - 2026-10-31)
        if any(math.hypot(p[1][0] - pos[0], p[1][2] - pos[2]) <= ORDER_NODE_MIN and abs(p[1][1] - pos[1]) < 1.5 and
               line_walkable(level, k, (gi - gi0, gj - gj0), (p[3], p[4]), gi0, gj0, p[2]) for p in pts):
            continue
        u = len(pts)
        pts.append((new_id(), pos, k, gi - gi0, gj - gj0))
        ORDER_IDS.add(pts[u][0])
        node_at[k] = u
        added += 1
        for q in range(u):
            d = math.dist(pos, pts[q][1])
            if d <= DIST_EDGE_MAX and line_walkable(level, k, (gi - gi0, gj - gj0), (pts[q][3], pts[q][4]), gi0, gj0, pts[q][2]):
                pairs.append((u, q, d))
    return added


def repair_detours(level, pts, pairs, node_at, cell_ij, gi0, gj0, ox, oz, new_id):
    """Detour repair: for node pairs of the sector within DIST_EDGE_MAX whose graph route is longer than
    DETOUR_TRIGGER x their straight distance, while a walk over kept surfaces of at most DETOUR_MAX x the straight
    distance exists, add waypoint nodes along the string-pulled walk, linked along the chain (and to the nodes around
    them by the edge rule only with DETOUR_LINK_ALL).
    DICE's routes run 1.02-1.11x the straight distance where NavKit's failed go-to requests ran 3.2-6.3x
    (sp_004_b stairs top, 2026-10-20). Returns the number of nodes added."""
    adj = defaultdict(dict)
    for a_, b_, d in pairs:
        adj[a_][b_] = adj[b_][a_] = min(d, adj[a_].get(b_, 1e18))
    added = 0
    n0 = len(pts)
    for a in range(n0):
        best = graph_routes(adj, a, DETOUR_TRIGGER * DIST_EDGE_MAX)
        for b in range(a + 1, n0):
            straight = math.dist(pts[a][1], pts[b][1])
            if straight > DIST_EDGE_MAX or straight < 1e-3:
                continue
            route = best.get(b, 1e18)
            if route <= DETOUR_TRIGGER * straight:
                continue
            # the walk itself within DETOUR_MAX x straight
            path = surface_walk(level, cell_ij, gi0, gj0, pts[a][2], pts[b][2], DETOUR_MAX * straight)
            if path is None:
                continue
            way = string_pull(level, path, cell_ij, gi0, gj0)
            chain = []
            for k in way:
                if k in node_at:
                    chain.append(node_at[k])
                else:
                    i, j = cell_ij[k]
                    pts.append((new_id(), (ox + i * PITCH, float(level.y[k]) + Y_OFFSET, oz + j * PITCH), k, i, j))
                    node_at[k] = len(pts) - 1
                    chain.append(len(pts) - 1)
                    added += 1
            for u, v in zip(chain, chain[1:]):
                if v not in adj[u]:
                    d = math.dist(pts[u][1], pts[v][1])
                    pairs.append((u, v, d))
                    adj[u][v] = adj[v][u] = d
            if DETOUR_LINK_ALL:
                for u in chain:
                    if u >= n0:
                        link_by_edge_rule(level, pts, pairs, adj, u, gi0, gj0)
            best = graph_routes(adj, a, DETOUR_TRIGGER * DIST_EDGE_MAX)
    return added


def graph_routes(adj, src, limit):
    """Shortest route lengths over the graph adj from src (Dijkstra, not expanded past limit): {node: metres}."""
    best = {src: 0.0}
    q = [(0.0, src)]
    while q:
        d, u = heapq.heappop(q)
        if d > best[u] or d > limit:
            continue
        for v, w in adj[u].items():
            if d + w < best.get(v, 1e18):
                best[v] = d + w
                heapq.heappush(q, (d + w, v))
    return best


def surface_walk(level, cell_ij, gi0, gj0, k0, k1, limit):
    """Shortest 8-connected walk over kept surfaces (step rule) from k0 to k1, at most limit metres; list of
    surfaces or None."""
    best = {k0: 0.0}
    prev = {k0: None}
    q = [(0.0, k0)]
    while q:
        d, k = heapq.heappop(q)
        if k == k1:
            path = [k]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        if d > best[k]:
            continue
        i, j = cell_ij[k]
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if not (di or dj) or (i + di) not in WINDOW or (j + dj) not in WINDOW:
                    continue
                n = level.neighbour(k, gi0 + i, gj0 + j, di, dj)
                if n is None:
                    continue
                nd = d + (1.4142 if di and dj else 1.0) * PITCH
                if nd <= limit and nd < best.get(n, 1e18):
                    best[n], prev[n] = nd, k
                    cell_ij.setdefault(n, (i + di, j + dj))
                    heapq.heappush(q, (nd, n))
    return None


def link_by_edge_rule(level, pts, pairs, adj, u, gi0, gj0):
    """Node u linked (in pairs and adj) to every node within DIST_EDGE_MAX it reaches in a straight walkable line."""
    for q_ in range(len(pts)):
        if q_ == u or q_ in adj[u]:
            continue
        d = math.dist(pts[u][1], pts[q_][1])
        if d <= DIST_EDGE_MAX and line_walkable(level, pts[u][2], (pts[u][3], pts[u][4]), (pts[q_][3], pts[q_][4]),
                                                gi0, gj0, pts[q_][2]):
            pairs.append((u, q_, d))
            adj[u][q_] = adj[q_][u] = d


def finalize_graph(pts, pairs, linked_ids, origin=None):
    """Nodes and edges of a sector. Nodes without an edge in any sector (lone nodes on slivers and tiny islands
    the group joining cannot reach; border ids count edges of both sectors) are dropped - DICE's data has none."""
    keep = [idx for idx, p in enumerate(pts) if p[0] in linked_ids]
    if SORT_NODES_BY_ID:
        # DICE writes a sector's nodes sorted by id as an unsigned 32-bit value - ordinary nodes, then border nodes
        # (flag 80000000h) - in 40 of 40 sp_004_b sectors (masked to 31 bits: 0 of 40); the runtime binary-searches
        # these arrays for a path's start / destination ids (0x0108D5E0 / 0x0108D550). NavKit interleaved border
        # nodes (0 of 40 sorted) - 2026-10-20
        keep.sort(key=lambda idx: pts[idx][0] & 0xFFFFFFFF)
    new = {old: q for q, old in enumerate(keep)}
    out_edges = defaultdict(list)
    for a_, b_, d in pairs:
        if a_ in new and b_ in new:
            # the cost from the positions as stored (32-bit floats), as every DICE edge is: from the unrounded
            # positions, 138 of sp_004_b's 60,142 costs came out one unit off
            d = math.dist(*(tuple(float(np.float32(v)) for v in pts[i][1]) for i in (a_, b_)))
            cost = 0x80000000 | int(math.floor(d * 16))
            ia, ib = pts[a_][0], pts[b_][0]
            if (ib, ia) not in DROP_IDS:          # a drop link has no edge back up
                out_edges[new[a_]].append((cost, new[b_]))
            if (ia, ib) not in DROP_IDS:
                out_edges[new[b_]].append((cost, new[a_]))
    nodes, edges = [], []
    for q, idx in enumerate(keep):
        nid, pos = pts[idx][0], pts[idx][1]
        nodes.append(hpdtool.Node(nid, pos, 0x80000000, DIST_EDGE_MAX if q == 0 else 0.0, 0, 0, 0))
    for idx in range(len(nodes)):
        first = len(edges)
        for cost, dst in sorted(out_edges[idx], key=lambda e: e[1]):
            edges.append(hpdtool.Edge(cost, idx, dst))
        nodes[idx].a = first + 1 if out_edges[idx] else 0     # DICE: 0 = no outgoing edges
    def foreign(nd):
        # bit 16 of c: a border node's copy in a sector that does not own it. DICE: one copy per border id is the
        # owner (bit clear) - the sector whose 100 m square holds the node (1,379 of 1,445 sp_004_b ids), the
        # others set; ordinary nodes never. NavKit set the bit on every border copy, so no border node had an
        # owner (2026-10-20)
        if not nd.id & 0x80000000:
            return False
        if not BORDER_OWNER or origin is None:
            return True
        ox, oz = origin
        return not (ox <= nd.pos[0] < ox + 100 and oz <= nd.pos[2] < oz + 100)
    n = len(nodes)
    by_x = sorted(range(n), key=lambda q: nodes[q].pos[0])
    by_y = sorted(range(n), key=lambda q: nodes[q].pos[1])
    by_z = sorted(range(n), key=lambda q: nodes[q].pos[2])
    for q in range(n):
        nodes[q].b = by_x[q] | (by_y[q] << 16)
        nodes[q].c = by_z[q] | (0x10000 if foreign(nodes[q]) else 0)
    return nodes, edges


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    opts = dict(zip(argv[1:], argv[2:]))
    if len(args) < 5:
        print(__doc__)
        return 1
    if '--no-detail' in argv:
        global DETAIL_FEATURES
        DETAIL_FEATURES = False
    build(args[0], args[1] if args[1] != '-' else None, int(args[2]), int(args[3]), args[4],
          float(opts.get('--step', mohsoldier.STEP)), dynamic_path=opts.get('--dynamic'), high_path=opts.get('--high'),
          narrow_path=opts.get('--narrow'), stairs_path=opts.get('--stairs'), exclude_path=opts.get('--exclude'),
          exclude_foot_path=opts.get('--exclude-foot'))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('navbuild', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
