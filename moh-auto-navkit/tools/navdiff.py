"""NavKit: content diff of two pathdata.hpd files of one level (reference = DICE's, candidate = NavKit's).

Usage: navdiff.py <reference.hpd> <candidate.hpd> [--sectors] [--json out.json] [--png out_dir] [--top N]

Compares what the AI gets, not bytes (ordering, ids and rounding are free). Five layers, each only meaningful
where the one before agrees:
  sectors   which sector ids exist in each file
  cells     walkable ground: every area is filled on the sample grid (samples at whole multiples of the pitch,
            outlines halfway between them); a cell is (i, j, height) - stacked floors stay apart by height
  areas     DICE areas reproduced as exactly the same set of cells (else split / merged / different)
  bounds    for areas that match: outline, portal and border-portal geometry, compared as grid-sized pieces
            (so runs merged differently still compare equal)
  graph     route nodes matched by position (0.05 m horizontally, 0.3 m vertically), then edges and costs

Parity = the share of the reference that the candidate reproduces exactly, per layer. 100 % on every layer
is the goal (navkit-dice-parity: same content, bytes may differ).
"""
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import mohsoldier  # noqa: E402

Y_OFFSET = mohsoldier.Y_OFFSET      # outline heights are ground + half the soldier's height (MOH 0.9; BFBC2 0.95)
GRID = 0.8           # cells are compared on this grid whatever an area's pitch (detailed zones use 0.2; 0.8 is a
                     # multiple, so these points are sample points of every area)
STACK_TOL = 4.0      # where floors are stacked, cells pair by nearest height up to this difference (storeys are 2.5 m+)
NODE_XZ, NODE_Y = 0.05, 0.3


# ------------------------------------------------------------------------------------------------ loading

class Sector:
    def __init__(self, s):
        self.id = s.id
        self.mesh = kymesh.parse_mesh(s.mesh)
        self.nodes = s.nodes
        self.edges = s.edges
        self._cells = None

    def cells(self):
        """Per area: dict (i, j) -> ground height estimate."""
        if self._cells is None:
            self._cells = [area_cells(a) for a in self.mesh.areas]
        return self._cells


def load(path):
    f = hpdtool.parse(open(path, 'rb').read())
    return {s.id: Sector(s) for s in f.sectors}


def segments(area):
    """All boundary segments of an area in (x, y, z): outline polylines, portals, border portals."""
    segs = []
    for line in area.outline:
        segs.extend(zip(line[:-1], line[1:]))
    segs.extend((p.p0, p.p1) for p in area.portals)
    segs.extend((b.p0, b.p1) for b in area.border_portals)
    return segs


def area_cells(area):
    """Samples inside the area's boundary (even-odd rule on the xz plane), with a ground height estimate from the
    nearest boundary point."""
    segs = segments(area)
    if not segs:
        return {}
    pitch = GRID
    a = np.array([s[0] for s in segs], dtype=np.float64)
    b = np.array([s[1] for s in segs], dtype=np.float64)
    xs = np.concatenate([a[:, 0], b[:, 0]])
    zs = np.concatenate([a[:, 2], b[:, 2]])
    i0, i1 = int(math.floor(xs.min() / pitch)) - 1, int(math.ceil(xs.max() / pitch)) + 1
    j0, j1 = int(math.floor(zs.min() / pitch)) - 1, int(math.ceil(zs.max() / pitch)) + 1
    ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
    ii, jj = ii.ravel(), jj.ravel()
    # a tiny offset keeps sample points off the 45-degree bridges that pass through cell corners
    px = ii * pitch + pitch * 1.37e-3
    pz = jj * pitch + pitch * 0.71e-3
    inside = np.zeros(len(ii), dtype=bool)
    ax, az, bx, bz = a[:, 0], a[:, 2], b[:, 0], b[:, 2]
    for k in range(0, len(ax), 256):
        sax, saz, sbx, sbz = ax[k:k + 256], az[k:k + 256], bx[k:k + 256], bz[k:k + 256]
        cond = (saz[None, :] > pz[:, None]) != (sbz[None, :] > pz[:, None])
        with np.errstate(divide='ignore', invalid='ignore'):
            xint = sax[None, :] + (pz[:, None] - saz[None, :]) * (sbx - sax)[None, :] / (sbz - saz)[None, :]
        crosses = cond & (px[:, None] < xint)
        inside ^= (np.count_nonzero(crosses, axis=1) % 2 == 1)
    if not inside.any():
        return {}
    pts = np.concatenate([a, b])
    sel_i, sel_j = ii[inside], jj[inside]
    sx, sz = sel_i * pitch, sel_j * pitch
    out = {}
    for k in range(0, len(sel_i), 2048):
        d = (sx[k:k + 2048, None] - pts[None, :, 0]) ** 2 + (sz[k:k + 2048, None] - pts[None, :, 2]) ** 2
        near = pts[np.argmin(d, axis=1), 1] - Y_OFFSET
        for n in range(len(near)):
            out[(int(sel_i[k + n]), int(sel_j[k + n]))] = float(near[n])
    return out


# ------------------------------------------------------------------------------------------------ comparing

def match_cells(ref_cells, cand_cells):
    """Pairs cells of two sectors at the same (i, j). Heights are estimates (from the boundary, so rough on large
    sloped areas): they only tell stacked floors apart. One floor on each side: the same ground. Several: paired
    by nearest height, at most STACK_TOL apart. Returns ref (area, ij) -> cand (area, ij) and the used cand cells."""
    by_ij_c, by_ij_r = defaultdict(list), defaultdict(list)
    for bj, cells in enumerate(cand_cells):
        for ij, y in cells.items():
            by_ij_c[ij].append((bj, y))
    for ai, cells in enumerate(ref_cells):
        for ij, y in cells.items():
            by_ij_r[ij].append((ai, y))
    ref_to_cand, cand_used = {}, set()
    for ij, refs in by_ij_r.items():
        cands = by_ij_c.get(ij)
        if not cands:
            continue
        if len(refs) == 1 and len(cands) == 1:
            ref_to_cand[(refs[0][0], ij)] = (cands[0][0], ij)
            cand_used.add((cands[0][0], ij))
            continue
        pairs = sorted((abs(yr - yc), ai, bj) for ai, yr in refs for bj, yc in cands)
        done_r, done_c = set(), set()
        for d, ai, bj in pairs:
            if d > STACK_TOL or ai in done_r or bj in done_c:
                continue
            done_r.add(ai)
            done_c.add(bj)
            ref_to_cand[(ai, ij)] = (bj, ij)
            cand_used.add((bj, ij))
    return ref_to_cand, cand_used


def pieces(segs, pitch):
    """Segments as a set of grid-sized pieces in half-pitch units (xz only), direction-free."""
    half = pitch / 2.0
    out = set()
    for p0, p1 in segs:
        x0, z0, x1, z1 = p0[0] / half, p0[2] / half, p1[0] / half, p1[2] / half
        steps = max(int(round(max(abs(x1 - x0), abs(z1 - z0)))), 1)
        for k in range(steps):
            a = (round(x0 + (x1 - x0) * k / steps), round(z0 + (z1 - z0) * k / steps))
            b = (round(x0 + (x1 - x0) * (k + 1) / steps), round(z0 + (z1 - z0) * (k + 1) / steps))
            out.add((a, b) if a <= b else (b, a))
    return out


def compare_sector(ref, cand):
    r = {'cells_ref': 0, 'cells_cand': 0, 'cells_same': 0, 'areas_ref': len(ref.mesh.areas), 'areas_cand': len(cand.mesh.areas),
         'areas_same': 0, 'areas_split': 0, 'areas_other': 0, 'outline_same': 0, 'portals_same': 0, 'borders_same': 0,
         'nodes_ref': len(ref.nodes), 'nodes_cand': len(cand.nodes), 'nodes_same': 0, 'edges_ref': len(ref.edges),
         'edges_same': 0, 'costs_same': 0, 'only_ref': [], 'only_cand': [],
         'detailed_ref': sum(1 for a in ref.mesh.areas if a.pitch < 0.5), 'detailed_cand': sum(1 for a in cand.mesh.areas if a.pitch < 0.5)}
    rc, cc = ref.cells(), cand.cells()
    r['cells_ref'] = sum(len(c) for c in rc)
    r['cells_cand'] = sum(len(c) for c in cc)
    ref_to_cand, cand_used = match_cells(rc, cc)
    r['cells_same'] = len(ref_to_cand)
    for ai, cells in enumerate(rc):
        for ij in cells:
            if (ai, ij) not in ref_to_cand:
                r['only_ref'].append(ij)
    for bi, cells in enumerate(cc):
        for ij in cells:
            if (bi, ij) not in cand_used:
                r['only_cand'].append(ij)
    # areas: a reference area is reproduced by a candidate area with exactly the same boundary (outline, portals,
    # border portals as grid pieces at its own pitch) at the same height; unmatched ones are "split" when their
    # ground is spread over several candidate areas, else "other"
    def shape(a):
        segs = [s for line in a.outline for s in zip(line[:-1], line[1:])] + [(p.p0, p.p1) for p in a.portals] + [(b.p0, b.p1) for b in a.border_portals]
        return (round(a.pitch, 2), frozenset(pieces(segs, a.pitch)), round(a.min_y * 2), round(a.max_y * 2))
    by_shape = defaultdict(list)
    for bj, a in enumerate(cand.mesh.areas):
        by_shape[shape(a)].append(bj)
    area_map = {}
    for ai, a in enumerate(ref.mesh.areas):
        hits = by_shape.get(shape(a))
        if hits:
            area_map[ai] = hits.pop(0)
            r['areas_same'] += 1
            continue
        targets = {ref_to_cand[(ai, ij)][0] for ij in rc[ai] if (ai, ij) in ref_to_cand}
        if len(targets) > 1:
            r['areas_split'] += 1
        else:
            r['areas_other'] += 1
    # boundaries of matched areas
    for ai, bj in area_map.items():
        ra, ca = ref.mesh.areas[ai], cand.mesh.areas[bj]
        pitch = ra.pitch
        out_r = pieces([s for line in ra.outline for s in zip(line[:-1], line[1:])], pitch)
        out_c = pieces([s for line in ca.outline for s in zip(line[:-1], line[1:])], pitch)
        r['outline_same'] += out_r == out_c
        pr = pieces([(p.p0, p.p1) for p in ra.portals], pitch)
        pc = pieces([(p.p0, p.p1) for p in ca.portals], pitch)
        targets_same = sorted(area_map.get(p.target, -1) for p in ra.portals) == sorted(p.target for p in ca.portals) if pr == pc else False
        r['portals_same'] += pr == pc and targets_same
        br = {(k, tuple(round(v, 3) for v in b.direction)) for b in ra.border_portals for k in pieces([(b.p0, b.p1)], pitch)}
        bc = {(k, tuple(round(v, 3) for v in b.direction)) for b in ca.border_portals for k in pieces([(b.p0, b.p1)], pitch)}
        r['borders_same'] += br == bc
    # graph
    cand_nodes = defaultdict(list)
    for idx, n in enumerate(cand.nodes):
        cand_nodes[(round(n.pos[0] / 0.5), round(n.pos[2] / 0.5))].append(idx)
    # one to one: each candidate node pairs with at most one reference node (the closest still free)
    node_map, used = {}, set()
    for idx, n in enumerate(ref.nodes):
        key = (round(n.pos[0] / 0.5), round(n.pos[2] / 0.5))
        best, best_d = None, None
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for c in cand_nodes.get((key[0] + dx, key[1] + dz), ()):
                    m = cand.nodes[c]
                    if c in used or abs(m.pos[0] - n.pos[0]) > NODE_XZ or abs(m.pos[2] - n.pos[2]) > NODE_XZ or abs(m.pos[1] - n.pos[1]) > NODE_Y:
                        continue
                    d = (m.pos[0] - n.pos[0]) ** 2 + (m.pos[1] - n.pos[1]) ** 2 + (m.pos[2] - n.pos[2]) ** 2
                    if best is None or d < best_d or (d == best_d and c == idx):
                        best, best_d = c, d
        if best is not None:
            node_map[idx] = best
            used.add(best)
    r['nodes_same'] = len(node_map)
    cand_edges = defaultdict(list)
    for e in cand.edges:
        cand_edges[(e.src, e.dst)].append(e.tag)
    for e in ref.edges:
        s, d = node_map.get(e.src), node_map.get(e.dst)
        if s is not None and d is not None and cand_edges.get((s, d)):
            tags = cand_edges[(s, d)]
            r['edges_same'] += 1
            r['costs_same'] += e.tag in tags
            tags.remove(e.tag if e.tag in tags else tags[0])
    return r


# ------------------------------------------------------------------------------------------------ report

def pct(a, b):
    return 100.0 * a / b if b else 100.0


def diff(reference, candidate, per_sector=False, log=print, clip=None):
    """clip: (minX, minZ, maxX, maxZ) - reference cells outside it are left out (ground beyond the level's terrain)."""
    R, C = load(reference), load(candidate)
    if clip:
        for s in R.values():
            s._cells = [{ij: h for ij, h in c.items() if clip[0] <= ij[0] * GRID <= clip[2] and clip[1] <= ij[1] * GRID <= clip[3]}
                        for c in s.cells()]
    only_r, only_c = sorted(set(R) - set(C)), sorted(set(C) - set(R))
    tot = defaultdict(int)
    rows = []
    for sid in sorted(set(R) & set(C)):
        r = compare_sector(R[sid], C[sid])
        r['id'] = sid
        rows.append(r)
        for k, v in r.items():
            if isinstance(v, int) and k != 'id':
                tot[k] += v
    # sectors only in the reference count fully as missing
    for sid in only_r:
        s = R[sid]
        tot['cells_ref'] += sum(len(c) for c in s.cells())
        tot['areas_ref'] += len(s.mesh.areas)
        tot['nodes_ref'] += len(s.nodes)
        tot['edges_ref'] += len(s.edges)
    for sid in only_c:
        tot['cells_cand'] += sum(len(c) for c in C[sid].cells())
    res = {
        'reference': reference, 'candidate': candidate,
        'sectors': {'reference': len(R), 'candidate': len(C), 'only_reference': [hex(s) for s in only_r], 'only_candidate': [hex(s) for s in only_c]},
        'cells': {'reference': tot['cells_ref'], 'candidate': tot['cells_cand'], 'same': tot['cells_same'],
                  'parity': round(pct(tot['cells_same'], tot['cells_ref']), 2),
                  'only_reference': tot['cells_ref'] - tot['cells_same'], 'only_candidate': tot['cells_cand'] - tot['cells_same']},
        'areas': {'reference': tot['areas_ref'], 'candidate': tot['areas_cand'], 'same': tot['areas_same'],
                  'split': tot['areas_split'], 'other': tot['areas_other'], 'parity': round(pct(tot['areas_same'], tot['areas_ref']), 2),
                  'detailed_reference': tot['detailed_ref'], 'detailed_candidate': tot['detailed_cand']},
        'bounds_of_same_areas': {'outline': round(pct(tot['outline_same'], tot['areas_same']), 2),
                                 'portals': round(pct(tot['portals_same'], tot['areas_same']), 2),
                                 'borders': round(pct(tot['borders_same'], tot['areas_same']), 2)},
        'graph': {'nodes_reference': tot['nodes_ref'], 'nodes_candidate': tot['nodes_cand'], 'nodes_same': tot['nodes_same'],
                  'node_parity': round(pct(tot['nodes_same'], tot['nodes_ref']), 2),
                  'edges_reference': tot['edges_ref'], 'edges_same': tot['edges_same'], 'edge_parity': round(pct(tot['edges_same'], tot['edges_ref']), 2),
                  'costs_same_of_same_edges': round(pct(tot['costs_same'], tot['edges_same']), 2)},
        'worst_sectors': [{'id': hex(r['id']), 'cells_only_reference': len(r['only_ref']), 'cells_only_candidate': len(r['only_cand']),
                           'areas_same': f"{r['areas_same']}/{r['areas_ref']}"}
                          for r in sorted(rows, key=lambda r: -(len(r['only_ref']) + len(r['only_cand'])))[:15]],
    }
    s, c, a, b, g = res['sectors'], res['cells'], res['areas'], res['bounds_of_same_areas'], res['graph']
    log(f"sectors   reference {s['reference']}, candidate {s['candidate']}; only in reference {len(s['only_reference'])}, only in candidate {len(s['only_candidate'])}")
    log(f"cells     {c['same']}/{c['reference']} reproduced ({c['parity']} %); {c['only_reference']} only in reference, {c['only_candidate']} only in candidate")
    log(f"areas     {a['same']}/{a['reference']} exactly the same ({a['parity']} %); {a['split']} split differently, {a['other']} other; candidate has {a['candidate']}"
        f" (detailed 0.2 m areas: reference {a['detailed_reference']}, candidate {a['detailed_candidate']})")
    log(f"bounds    of the same areas: outline {b['outline']} %, portals {b['portals']} %, border portals {b['borders']} %")
    log(f"graph     nodes {g['nodes_same']}/{g['nodes_reference']} ({g['node_parity']} %), edges {g['edges_same']}/{g['edges_reference']} ({g['edge_parity']} %), "
        f"costs of matched edges {g['costs_same_of_same_edges']} %; candidate has {g['nodes_candidate']} nodes")
    if per_sector:
        log('worst sectors (cells only in reference / only in candidate, areas the same):')
        for w in res['worst_sectors']:
            log(f"  {w['id']:>8}  {w['cells_only_reference']:6d} / {w['cells_only_candidate']:6d}   areas {w['areas_same']}")
    return res, rows, R, C


def pngs(rows, R, C, out_dir, top):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    os.makedirs(out_dir, exist_ok=True)
    for r in sorted(rows, key=lambda r: -(len(r['only_ref']) + len(r['only_cand'])))[:top]:
        sid = r['id']
        pitch = R[sid].mesh.pitch
        fig, ax = plt.subplots(figsize=(8, 8))
        same = [ij for cells in R[sid].cells() for ij in cells]
        if same:
            ax.scatter([i * pitch for i, _ in same], [j * pitch for _, j in same], s=1, c='#c8c8c8', label='walkable in reference')
        if r['only_ref']:
            ax.scatter([i * pitch for i, _ in r['only_ref']], [j * pitch for _, j in r['only_ref']], s=3, c='#d62728', label='only reference')
        if r['only_cand']:
            ax.scatter([i * pitch for i, _ in r['only_cand']], [j * pitch for _, j in r['only_cand']], s=3, c='#1f77b4', label='only candidate')
        ax.set_aspect('equal')
        ax.set_title(f'sector {sid:#06x}: {len(r["only_ref"])} only in reference, {len(r["only_cand"])} only in candidate')
        ax.legend(loc='upper right', markerscale=4, fontsize=8)
        fig.savefig(os.path.join(out_dir, f'sector_{sid:04x}.png'), dpi=110, bbox_inches='tight')
        plt.close(fig)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    opts = dict(zip(argv[1:], argv[2:]))
    if len(args) < 2:
        print(__doc__)
        return 1
    res, rows, R, C = diff(args[0], args[1], per_sector='--sectors' in argv)
    if '--json' in opts:
        json.dump(res, open(opts['--json'], 'w'), indent=1)
    if '--png' in opts:
        pngs(rows, R, C, opts['--png'], int(opts.get('--top', 6)))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('navdiff', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
