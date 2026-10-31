"""A Kynapse path-data file (pathdata.hpd) as the suite's 3D tools draw it: walkable cells, area boundaries, the
route graph and its islands. Read with NavKit's byte-exact reader (moh-auto-navkit/tools: hpdtool, kymesh,
navdiff); the format is in moh-auto-navkit/research/KYNAPSE_SPEC.md.

    nav = load(path, heightfield=None, cache_dir=None)
    nav.areas[k]                 NavArea: sector, tile, layer, pitch, boundary pieces, island
    nav.cell_xz, nav.cell_y      every walkable cell on the 0.8 m grid (navdiff's cells), its ground height
    nav.cell_area                the area each cell belongs to
    nav.node_pos, nav.edges      route graph: node positions (ground + 0.95 m, as stored), edges as index pairs
    nav.edge_cost                each edge's cost in metres, as stored
    nav.node_island, nav.islands islands (connected parts of the graph), largest first

Cells come from navdiff.area_cells (samples inside an area's boundary, height from the nearest boundary point);
where a cell lies on terrain, the terrain height field's height is used instead. That step is slow for a
whole level (10-40 s), so it is cached per file (size and date) when cache_dir is given.
"""
import hashlib
import json
import os
import pickle
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import List

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'moh-auto-navkit', 'tools'))
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import navdiff  # noqa: E402

GRID = navdiff.GRID
Y_OFFSET = navdiff.Y_OFFSET                 # boundaries and nodes are stored 0.95 m above the ground
DETAILED = 0.5                              # areas with a smaller pitch are detailed zones (DICE: 0.2 m)
CACHE_VERSION = 3                           # 3: ladder links and ladder ground
LADDER_DY = 2.5                             # a ladder link: a route edge climbing more than this (m) ...
LADDER_SLOPE = 1.5                          # ... and more than this many metres per metre across - no walkable link is
                                            # that steep (DICE's files have none; NavKit's ladder links, ladders.py)


@dataclass
class NavArea:
    sector: int
    index: int                              # within its sector
    tile: tuple
    layer: int
    pitch: float
    min_y: float
    max_y: float
    outline: List[np.ndarray] = field(default_factory=list)      # polylines (ground + 0.95 m)
    portals: List[tuple] = field(default_factory=list)           # (p0, p1, target area index in the sector)
    borders: List[tuple] = field(default_factory=list)           # (p0, p1)
    island: int = -1                                             # index into NavData.islands
    cells: int = 0
    ladder: bool = False                                         # ground reached only through a ladder link

    @property
    def detailed(self):
        return self.pitch < DETAILED


@dataclass
class Island:
    group: int                              # graph group id (a node id of the group)
    nodes: int
    cells: int
    centre: np.ndarray


@dataclass
class NavData:
    path: str
    areas: List[NavArea]
    cell_xz: np.ndarray                     # (n, 2) cell centres
    cell_y: np.ndarray                      # (n,) ground height
    cell_area: np.ndarray                   # (n,) index into areas
    node_pos: np.ndarray                    # (m, 3)
    node_sector: np.ndarray                 # (m,)
    edges: np.ndarray                       # (k, 2) node indices, source -> destination
    edge_cost: np.ndarray                   # (k,) metres, as stored (the tag's floor(distance * 16) / 16)
    node_island: np.ndarray                 # (m,) index into islands
    islands: List[Island]
    ladder_edge: np.ndarray = None          # (k,) bool: the edge is a ladder link (LADDER_DY / LADDER_SLOPE)

    @property
    def sectors(self):
        return sorted({a.sector for a in self.areas})


def _terrain_id(heightfield):
    """A fingerprint of a height field's content (its bounds and samples), kept on the object."""
    if heightfield is None:
        return 'est'
    fp = getattr(heightfield, '_navdata_id', None)
    if fp is None:
        h = hashlib.sha1(np.asarray(heightfield.min, np.float64).tobytes() + np.asarray(heightfield.max, np.float64).tobytes())
        h.update(np.ascontiguousarray(heightfield.raw).tobytes())
        fp = 'hf-' + h.hexdigest()[:16]
        try:
            heightfield._navdata_id = fp
        except AttributeError:
            pass
    return fp


def _key(path, heightfield=None):
    st = os.stat(path)
    hf = _terrain_id(heightfield)
    return hashlib.sha1(f'{CACHE_VERSION}|{os.path.abspath(path).lower()}|{st.st_size}|{st.st_mtime_ns}|{hf}'.encode()).hexdigest()[:20]


def _ground(xz, est, heightfield):
    """Cell heights: the terrain height field's where the cell lies on terrain (within 1.5 m of navdiff's
    estimate), else the estimate."""
    if heightfield is None or not len(xz):
        return est
    t = heightfield.height(xz[:, 0], xz[:, 1])
    return np.where(np.abs(t - est) < 1.5, t, est)


def _write_atomic(path, write):
    """write(file object) into path + '.tmp', then renamed over path: an interrupted write leaves no half file."""
    tmp = path + '.tmp'
    try:
        with open(tmp, 'wb') as fh:
            write(fh)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _cells(f, heightfield, cache_dir, path):
    if cache_dir:
        cp = os.path.join(cache_dir, _key(path) + '.npz')
        if os.path.isfile(cp):
            # 2026-10-31: a truncated or damaged entry (an interrupted write) made every load raise BadZipFile; it
            # is rebuilt now, and entries are written to a temporary name first
            try:
                with np.load(cp) as z:
                    xz, est, area = z['xz'], z['est'], z['area']
                return xz, _ground(xz, est, heightfield), area, est
            except Exception:
                pass
    xz, ys, area_of = [], [], []
    k = 0
    for s in f.sectors:
        for a in kymesh.parse_mesh(s.mesh).areas:
            c = navdiff.area_cells(a)
            if c:
                ij = np.array(list(c.keys()), dtype=np.float64)
                xz.append(ij * GRID)
                ys.append(np.array(list(c.values()), dtype=np.float64))
                area_of.append(np.full(len(c), k, dtype=np.int32))
            k += 1
    xz = np.concatenate(xz) if xz else np.zeros((0, 2))
    est = np.concatenate(ys) if ys else np.zeros(0)
    area = np.concatenate(area_of) if area_of else np.zeros(0, np.int32)
    y = _ground(xz, est, heightfield)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        _write_atomic(os.path.join(cache_dir, _key(path) + '.npz'), lambda fh: np.savez(fh, xz=xz, y=y, area=area, est=est))
    return xz, y, area, est


def load(path, heightfield=None, cache_dir=None):
    """The file as NavData. With cache_dir, the whole result is kept there per file (and per height source), so
    opening a level again takes about a second."""
    if cache_dir:
        pk = os.path.join(cache_dir, _key(path, heightfield) + '.navdata.pkl')
        # the cache is keyed by the absolute path, so a hit may come from a caller in another folder that passed
        # a relative path: nav.path is always this caller's file, absolute (2026-10-31: a relative path stored by
        # the first caller no longer resolved, and the studio failed at start in NavTune.new)
        if os.path.isfile(pk):
            try:
                with open(pk, 'rb') as fh:
                    nav = pickle.load(fh)
                nav.path = os.path.abspath(path)
                return nav
            except Exception:
                pass                                         # an unreadable cache entry is rebuilt
        nav = _load(os.path.abspath(path), heightfield, cache_dir)
        os.makedirs(cache_dir, exist_ok=True)
        _write_atomic(pk, lambda fh: pickle.dump(nav, fh, protocol=pickle.HIGHEST_PROTOCOL))
        return nav
    return _load(path, heightfield, None)


def _load(path, heightfield, cache_dir):
    f = hpdtool.parse(open(path, 'rb').read())
    areas = []
    for s in f.sectors:
        for n, a in enumerate(kymesh.parse_mesh(s.mesh).areas):
            areas.append(NavArea(s.id, n, (a.tile_x, a.tile_z), a.layer, a.pitch, a.min_y, a.max_y,
                                 [np.asarray(l, dtype=np.float32) for l in a.outline if len(l) >= 2],
                                 [(np.asarray(p.p0), np.asarray(p.p1), p.target) for p in a.portals],
                                 [(np.asarray(b.p0), np.asarray(b.p1)) for b in a.border_portals]))
    xz, y, cell_area, _ = _cells(f, heightfield, cache_dir, path)
    counts = np.bincount(cell_area, minlength=len(areas)) if len(cell_area) else np.zeros(len(areas), int)
    for a, n in zip(areas, counts):
        a.cells = int(n)

    # route graph: border nodes repeat in both sectors with the same id - one node per id
    index, pos, sec, edges, costs = {}, [], [], [], []
    for s in f.sectors:
        local = []
        for nd in s.nodes:
            if nd.id not in index:
                index[nd.id] = len(pos)
                pos.append(nd.pos)
                sec.append(s.id)
            local.append(index[nd.id])
        for e in s.edges:
            edges.append((local[e.src], local[e.dst]))
            costs.append((e.tag & 0x7FFFFFFF) / 16.0)
    pos = np.array(pos, dtype=np.float64).reshape(-1, 3)
    edges = np.array(edges, dtype=np.int64).reshape(-1, 2)
    parent = np.arange(len(pos))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(k) for k in range(len(pos))], dtype=np.int64)
    # ladder links, and the walking parts of each island (its components without them): ground on any walking
    # part but the island's largest is reached through a ladder - ladder ground
    if len(edges):
        d = pos[edges[:, 1]] - pos[edges[:, 0]]
        dy, h = np.abs(d[:, 1]), np.hypot(d[:, 0], d[:, 2])
        ladder_edge = (dy > LADDER_DY) & (dy > LADDER_SLOPE * h)
    else:
        ladder_edge = np.zeros(0, bool)
    walk = np.arange(len(pos))

    def wfind(a):
        while walk[a] != a:
            walk[a] = walk[walk[a]]
            a = walk[a]
        return a
    for (a, b), lad in zip(edges, ladder_edge):
        if not lad:
            ra, rb = wfind(a), wfind(b)
            if ra != rb:
                walk[ra] = rb
    walk_root = np.array([wfind(k) for k in range(len(pos))], dtype=np.int64)
    base = {}
    if ladder_edge.any():
        wsize = Counter(walk_root.tolist())
        for k in range(len(pos)):
            r, w = roots[k], walk_root[k]
            if r not in base or wsize[w] > wsize[base[r]]:
                base[r] = w
    sizes = Counter(roots.tolist())
    order = [r for r, _ in sizes.most_common()]
    rank = {r: k for k, r in enumerate(order)}
    node_island = np.array([rank[r] for r in roots], dtype=np.int32)

    # each area's island: the graph node of its sector nearest to the area's cells (an area has its own node,
    # near its centre; tiny areas without one take their neighbour's)
    sec_nodes = {}
    for k, s in enumerate(sec):
        sec_nodes.setdefault(s, []).append(k)
    sec_nodes = {s: np.array(v) for s, v in sec_nodes.items()}
    centre = np.zeros((len(areas), 3))
    if len(cell_area):
        for d in range(2):
            centre[:, [0, 2][d]] = np.bincount(cell_area, weights=xz[:, d], minlength=len(areas)) / np.maximum(counts, 1)
        centre[:, 1] = np.bincount(cell_area, weights=y, minlength=len(areas)) / np.maximum(counts, 1)
    for k, a in enumerate(areas):
        cand = sec_nodes.get(a.sector)
        if cand is None or not len(cand):
            continue
        p = pos[cand]
        d = np.hypot(p[:, 0] - centre[k, 0], p[:, 2] - centre[k, 2]) + 3.0 * np.abs(p[:, 1] - Y_OFFSET - centre[k, 1])
        kn = cand[int(np.argmin(d))]
        a.island = int(node_island[kn])
        a.ladder = bool(base) and walk_root[kn] != base[roots[kn]]
    island_cells = Counter()
    for a in areas:
        island_cells[a.island] += a.cells
    islands = []
    for k, r in enumerate(order):
        sel = node_island == k
        islands.append(Island(int(r), int(sel.sum()), int(island_cells.get(k, 0)), pos[sel].mean(axis=0)))
    return NavData(path, areas, xz, y, cell_area, pos, np.array(sec), edges, np.array(costs, dtype=np.float64),
                   node_island, islands, ladder_edge)


def compare_cells(cand, ref):
    """Per cell of each file: True where the other file has the same ground (navdiff's rule: the same 0.8 m
    cell; one floor on each side is the same ground, stacked floors pair by height within 4 m)."""
    def keyed(nav):
        ij = np.round(nav.cell_xz / GRID).astype(np.int64)
        return (ij[:, 0] + 50_000) * 100_000 + (ij[:, 1] + 50_000)

    kc, kr = keyed(cand), keyed(ref)
    yc, yr = np.asarray(cand.cell_y, np.float64), np.asarray(ref.cell_y, np.float64)

    def same(keys, y, okeys, oy):
        """For each (key, y): does the other file have the key, and (one floor each) or a floor within STACK_TOL?"""
        uk, counts = np.unique(keys, return_counts=True)
        mine_n = counts[np.searchsorted(uk, keys)]
        order = np.lexsort((oy, okeys))
        sk, sy = okeys[order], oy[order]
        lo, hi = np.searchsorted(sk, keys, 'left'), np.searchsorted(sk, keys, 'right')
        other_n = hi - lo
        # the other file's nearest floor at the same key: composite (key, height) values, sorted - the
        # neighbours of (key, y) in that order are the nearest floors below and above
        comp_o = sk.astype(np.float64) * 1e5 + (sy + 2e4)
        comp_m = keys.astype(np.float64) * 1e5 + (y + 2e4)
        p = np.searchsorted(comp_o, comp_m)
        best = np.full(len(keys), np.inf)
        for q in (p - 1, p):
            ok = (q >= 0) & (q < len(sk))
            qq = np.clip(q, 0, max(len(sk) - 1, 0))
            hit = ok & (sk[qq] == keys) if len(sk) else np.zeros(len(keys), bool)
            best = np.where(hit, np.minimum(best, np.abs(sy[qq] - y) if len(sk) else best), best)
        return (other_n > 0) & (((other_n == 1) & (mine_n == 1)) | (best <= navdiff.STACK_TOL))
    return same(kc, yc, kr, yr), same(kr, yr, kc, yc)


def summary(nav):
    return {'areas': len(nav.areas), 'detailed areas': sum(a.detailed for a in nav.areas), 'cells': int(len(nav.cell_xz)),
            'nodes': int(len(nav.node_pos)), 'edges': int(len(nav.edges)), 'islands': len(nav.islands),
            'large islands (25+ nodes)': sum(i.nodes >= 25 for i in nav.islands), 'sectors': len(nav.sectors),
            'ladder links': int(nav.ladder_edge.sum() // 2) if nav.ladder_edge is not None else 0,
            'ladder ground cells': sum(a.cells for a in nav.areas if a.ladder)}


if __name__ == '__main__':
    nav = load(sys.argv[1])
    print(json.dumps(summary(nav), indent=1))
