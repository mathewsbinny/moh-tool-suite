r"""NavKit: route nodes of small route islands that shadow a larger island (SHADOW_ISLANDS).

The route graph's islands on ONS_MP_004 (2026-10-26, research/KYNAPSE_SPEC.md section "Route islands"): of NavKit's
108 islands besides the main one, 84 (657 nodes) stand on ground DICE's data does not have at all - object tops,
crates, M-COM cases, low roofs, rooms - seeded from the sector perimeter lines (objects are seeds, navwalk.py rule 4)
or from the normal build's ground in the pitch-0.2 pass; 15 are islands DICE has as well; only 7 (55 nodes) are
ground DICE joins to its main island (3 split by steep cells, 4 where the group joining missed a ground link), and
none at a sector seam. The harm is the nearest-node join: the runtime starts and ends a route at the nearest route
node (R = 15 m, the first candidate accepted; 0x0105ED80), so a node on a crate top or a roof 0.6-4 m above the
floor, nearer than the floor's own nodes, puts a point on the main ground into a pocket with no way out - two of
ONS_MP_004's M-COM targets and a third of its spawn -> target routes failed that way.

Rule: a route island (border ids join sectors) with fewer than SHADOW_MIN_NODES nodes, most of them on objects
(SHADOW_OBJECTS_ONLY, below), holding no anchor (a soldier
spawn or an AI order / objective position standing on one of its nodes' floors, ANCHOR_XZ / ANCHOR_DY), whose nodes
come within SHADOW_XZ horizontally and SHADOW_DY in height of a node of an island of SHADOW_MIN_NODES or more,
loses its edges; finalize_graph then drops its nodes (they have no edge). Its ground (areas, portals) stays: only
the route graph changes, so the DICE-parity ground measures do not move.

SHADOW_OBJECTS_ONLY: only islands whose nodes stand mostly on objects (not terrain) - DICE seeds its ground from
terrain alone (objects have AllowPathdataSeeds false; spec section 3.7), so a small terrain island is one DICE has as
well: on ONS_MP_004 all 12 small islands DICE also has are terrain, 64 of the 79 small NavKit-only ones objects, and
every pocket that captured a failed route is on objects. Without it the 12 go too (DICE nodes near NavKit nodes
61.15 -> 60.27 %, over-connection 8.32 -> 8.40). navbuild.stage records the terrain nodes (note_nodes); a node with
no record counts as an object node.
"""
import math
from collections import defaultdict

SHADOW_ISLANDS = True
SHADOW_MIN_NODES = 25           # islands under this many nodes are candidates (as navbuild.ORDER_MIN_ISLAND)
SHADOW_XZ = 8.0                 # ... when a node of a large island is this close across: twice the detailed zones'
                                # node cover (navbuild.NODE_COVER 4 m), so a pocket node can be the nearest node of
                                # ground the large island covers (ONS_MP_004: the same islands at 6 / 8 m, 3 more at 15)
SHADOW_DY = 4.0                 # ... and this close in height (the harness's join, navpath.JOIN_Y)
ANCHOR_XZ = 2.0                 # an anchor point keeps the island of a node this close across ...
ANCHOR_DY = 1.0                 # ... whose ground (node height - Y_OFFSET) is this close to the point's height
SHADOW_OBJECTS_ONLY = True
Y_OFFSET = 0.95
STATS = {}
TERRAIN_IDS = set()             # ids of route nodes standing on terrain (note_nodes; navbuild.build clears it)


def note_nodes(level, pts):
    """Record which of a sector's route nodes (navbuild.build_graph's pts: (id, pos, surface, i, j)) stand on terrain
    (level.terrain: the sampled hit is the terrain's shape)."""
    terrain = getattr(level, 'terrain', None)
    if terrain is None:
        return
    for p in pts:
        if p[2] is not None and terrain[p[2]]:
            TERRAIN_IDS.add(p[0])


def components(graphs):
    """{node id: root id}, {root id: node count}, {node id: position} over all sectors' (pts, pairs)."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    pos = {}
    for g in graphs:
        pts, pairs = g[3], g[4]
        for p in pts:
            find(p[0])
            pos.setdefault(p[0], p[1])
        for a, b, _d in pairs:
            ra, rb = find(pts[a][0]), find(pts[b][0])
            if ra != rb:
                parent[ra] = rb
    linked = set()                                 # nodes without an edge are dropped by finalize_graph anyway
    for g in graphs:
        pts = g[3]
        for a, b, _d in g[4]:
            linked.update((pts[a][0], pts[b][0]))
    root = {i: find(i) for i in pos if i in linked}
    size = defaultdict(int)
    for r in root.values():
        size[r] += 1
    return root, size, pos


def shadowing(graphs, anchors=(), min_nodes=None, xz=None, dy=None):
    """The roots of the islands SHADOW_ISLANDS drops, and the components ({id: root}, {root: size}, {id: pos})."""
    min_nodes = SHADOW_MIN_NODES if min_nodes is None else min_nodes
    xz = SHADOW_XZ if xz is None else xz
    dy = SHADOW_DY if dy is None else dy
    root, size, pos = components(graphs)
    small = {r for r, n in size.items() if n < min_nodes}
    if SHADOW_OBJECTS_ONLY and small:
        on_terrain = defaultdict(int)
        for i, r in root.items():
            if r in small and i in TERRAIN_IDS:
                on_terrain[r] += 1
        small = {r for r in small if 2 * on_terrain[r] < size[r]}
    if not small or len(size) < 2:
        return set(), root, size, pos
    grid = defaultdict(list)                       # large islands' nodes in xz buckets
    for i, r in root.items():
        if size[r] >= min_nodes:
            p = pos[i]
            grid[(math.floor(p[0] / xz), math.floor(p[2] / xz))].append(p)
    abucket = defaultdict(list)                    # anchors in buckets
    for a in anchors or ():
        abucket[(math.floor(a[0] / ANCHOR_XZ), math.floor(a[2] / ANCHOR_XZ))].append(a)
    anchored, near = set(), set()
    for i, r in root.items():
        if r not in small:
            continue
        p = pos[i]
        if r not in anchored:
            bx, bz = math.floor(p[0] / ANCHOR_XZ), math.floor(p[2] / ANCHOR_XZ)
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for a in abucket.get((bx + dx, bz + dz), ()):
                        if math.hypot(a[0] - p[0], a[2] - p[2]) <= ANCHOR_XZ and abs(p[1] - Y_OFFSET - a[1]) <= ANCHOR_DY:
                            anchored.add(r)
        if r in near:
            continue
        bx, bz = math.floor(p[0] / xz), math.floor(p[2] / xz)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for q in grid.get((bx + dx, bz + dz), ()):
                    if abs(q[1] - p[1]) <= dy and math.hypot(q[0] - p[0], q[2] - p[2]) <= xz:
                        near.add(r)
                        break
                if r in near:
                    break
            if r in near:
                break
    return near - anchored, root, size, pos


def prune(graphs, anchors=(), log=None):
    """graphs [(sid, bounds, mesh, pts, pairs, origin)] with the shadowing islands' edges removed (SHADOW_ISLANDS).
    anchors: (x, y, z) points (soldier spawns, AI order and objective positions) whose islands stay."""
    STATS.clear()
    if not SHADOW_ISLANDS:
        return graphs
    drop, root, size, _pos = shadowing(graphs, anchors)
    ids = {i for i, r in root.items() if r in drop}
    STATS.update(islands=len(drop), nodes=len(ids), before=len(size), after=len(size) - len(drop))
    if log:
        log(f'shadow islands: {len(drop)} of {len(size)} route islands ({len(ids)} nodes) under {SHADOW_MIN_NODES} nodes '
            f'within {SHADOW_XZ:g} m of a larger one lose their nodes')
    if not ids:
        return graphs
    return [(sid, b, m, pts, [q for q in pairs if pts[q[0]][0] not in ids and pts[q[1]][0] not in ids], o)
            for sid, b, m, pts, pairs, o in graphs]
