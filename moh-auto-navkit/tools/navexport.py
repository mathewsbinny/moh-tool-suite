r"""NavKit: a Medal of Honor build exported to RSNV, the per-level file of OPFOR's navigation runtime (rsnav, shared
from Project OPFOR BF1943, MOH's copy in project-opfor-moh src/rsnav; format in core/mohcore/rsnv.py).

  navexport.py <level> [--out <file.rsnv>] [--check] [--compare] [--opfor <root>]

Reads the level's build (work\navkit\<level>\pathdata.hpd with its cells, pathdata.cells.npz), its settings
(navkit.json: sector offsets), its ladders (work\levels\<level>\ladders.json), its water and terrain, and writes
work\navkit\<level>\<level>.rsnv (little-endian, for the PC client). --check reads it back, validates it
(rsnv.World.validate) and compares its graph with the build's.

A port of BF1943's exporter (bf1943-tool-suite, bf1943-auto-navkit-big-endian/tools/navexport1943.py, version 1.4) -
the same layout and rules; what differs for MOH:
  - the soldier: tools/mohsoldier.py (radius 0.35, height 1.8, step 0.3, water 1.4); node and area heights are the
    build's ground (node height - mohsoldier.Y_OFFSET, 0.9);
  - water: MOH has no sea; a node's water depth is the level's water mesh over it (core water.py), which sets the
    shallow / wade flags (NAUX); the header's sea level (used by rsnav's flight code only) is 0;
  - no vehicle entries (OFFM): MOH MP has no vehicles a soldier boards;
  - no jump links yet: MOH's builds make none (BF1943's jumplinks1943 has no MOH counterpart); ladders are linked.

What it holds (as BF1943's): the route graph (nodes on the ground, edges with costs in 1/16 m rounded up to an
admissible heuristic, kinds walk / ladder up / ladder down, tight walk edges flagged); areas with bitmaps of their
walkable cells at their own pitch (0.2 m in detailed sectors, 0.8 m elsewhere; the build's own cells and heights
from pathdata.cells.npz, else rasterised from the outlines) and height grids; per sector 25 x 25 buckets of 4 m;
portals both ways; ladders (LADR); islands (ISLD, the main one flagged); provenance (META).
"""
import argparse
import collections
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE), os.path.join(os.path.dirname(os.path.dirname(HERE)), 'core')]
import mohsoldier  # noqa: E402
import navbuild  # noqa: E402
from mohcore import navdata, rsnv  # noqa: E402

EXPORTER_VERSION = ('moh 1.0 (2026-10-30: BF1943 exporter 1.4 for MOH - its soldier, water from the level\'s water '
                    'mesh, no vehicle entries, no jump links)')
HEIGHT_CELLS = {0.2: 5, 0.4: 2, 0.8: 1}   # cells per height-grid block, by pitch
COARSE_PITCH = 0.8        # m: the bitmap pitch of the build's 0.8 m areas
END_MATCH = 1.0           # m: a jump link's end and its route node lie at most this far apart
LADDER_MATCH = 3.0        # m: a ladder's foot / top and its route node
HEIGHT_TOL = 0.25         # m: a 0.2 m area whose 1 m height grid is further off than this at one of its own cells gets a
                          # finer grid (BF1943 2026-10-26: at steep area edges the blocks' means put the ground 0.3-0.7 m
                          # off and made walk edges there tight)
SHALLOW = 0.5             # m of water over a node: shallow (NAUX_SHALLOW); deeper, up to the soldier's limit: wading


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


def rasterise(area, pitch, y_offset, heightfield):
    """The area's walkable cells at its pitch from its boundary (a build without its cells file): (ii, jj) sample
    indices (centre = index x pitch) and ground heights - samples inside the boundary (even-odd on xz), the height from
    the nearest boundary point less y_offset, or the terrain's where the cell lies on the terrain (navdata._ground)."""
    segs = [(np.asarray(l[k], float), np.asarray(l[k + 1], float)) for l in area.outline for k in range(len(l) - 1)]
    segs += [(np.asarray(p0, float), np.asarray(p1, float)) for p0, p1, *_ in area.portals]
    segs += [(np.asarray(p0, float), np.asarray(p1, float)) for p0, p1 in area.borders]
    if not segs:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    a = np.array([s[0] for s in segs])
    b = np.array([s[1] for s in segs])
    xs, zs = np.concatenate([a[:, 0], b[:, 0]]), np.concatenate([a[:, 2], b[:, 2]])
    i0, i1 = int(math.floor(xs.min() / pitch)) - 1, int(math.ceil(xs.max() / pitch)) + 1
    j0, j1 = int(math.floor(zs.min() / pitch)) - 1, int(math.ceil(zs.max() / pitch)) + 1
    ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
    ii, jj = ii.ravel(), jj.ravel()
    px, pz = ii * pitch + pitch * 1.37e-3, jj * pitch + pitch * 0.71e-3      # off the 45-degree bridges' corners
    inside = np.zeros(len(ii), bool)
    for k in range(0, len(a), 256):
        ax, az, bx, bz = a[k:k + 256, 0], a[k:k + 256, 2], b[k:k + 256, 0], b[k:k + 256, 2]
        cond = (az[None, :] > pz[:, None]) != (bz[None, :] > pz[:, None])
        with np.errstate(divide='ignore', invalid='ignore'):
            xint = ax[None, :] + (pz[:, None] - az[None, :]) * (bx - ax)[None, :] / (bz - az)[None, :]
        inside ^= np.count_nonzero(cond & (px[:, None] < xint), axis=1) % 2 == 1
    ii, jj = ii[inside], jj[inside]
    if not len(ii):
        return ii, jj, np.zeros(0)
    pts = np.concatenate([a, b])
    est = np.empty(len(ii))
    for k in range(0, len(ii), 2048):
        sx, sz = ii[k:k + 2048] * pitch, jj[k:k + 2048] * pitch
        q = np.argmin((sx[:, None] - pts[None, :, 0]) ** 2 + (sz[:, None] - pts[None, :, 2]) ** 2, axis=1)
        est[k:k + 2048] = pts[q, 1] - y_offset
    xz = np.stack([ii * pitch, jj * pitch], 1)
    return ii, jj, navdata._ground(xz, est, heightfield)


def admissible_costs(pos, src, dst, cost):
    """The edges' costs for the runtime: the build's (floor(16 x length)), raised where that is under 16 x the length
    between the exported float32 node positions as the search measures it - to that, rounded up. Every path then costs
    at least 16 x its length, so the search's heuristic never overestimates (BF1943 exporter 1.4)."""
    p = np.asarray(pos, np.float32).astype(np.float64)
    d = p[np.asarray(dst)] - p[np.asarray(src)]
    ln = np.sqrt((d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1]) + d[:, 2] * d[:, 2])
    return np.maximum(np.asarray(cost, np.int64), np.ceil(16.0 * ln).astype(np.int64))


def height_grid(ii, jj, y, cpb, miny):
    """The area's height grid (blocks of cpb x cpb cells from its first cell, each its cells' mean, cm above miny;
    0xFFFF for a block with no cell): (grid rows x columns, width, height)."""
    hw = max(1, -(-(int(ii.max()) + 1 if len(ii) else 1) // cpb))
    hh = max(1, -(-(int(jj.max()) + 1 if len(jj) else 1) // cpb))
    grid = np.full((hh, hw), 0xFFFF, np.int64)
    if len(ii):
        gi, gj = ii // cpb, jj // cpb
        key = gj * hw + gi
        sums = np.bincount(key, weights=y, minlength=hw * hh)
        cnt = np.bincount(key, minlength=hw * hh)
        filled = cnt > 0
        flat = grid.reshape(-1)
        flat[filled] = np.minimum(np.round((sums[filled] / cnt[filled] - miny) * 100).astype(np.int64), 0xFFFE)
    return grid, hw, hh


def grid_error(ii, jj, y, grid, hw, hh, cpb, pitch, miny):
    """The largest difference between the grid's ground (rsnv.World.ground, the runtime's reading) and the cells'
    own heights, at the cells."""
    a = rsnv.Area(0, 0.0, 0.0, 0.0, 0.0, miny, miny, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, pitch, 0, hw, hh,
                  int(round(cpb * pitch * 1000)))
    stub = type('G', (), {})()
    stub.hght = grid.reshape(-1)
    return max((abs(rsnv.World.ground(stub, a, float(u) * pitch, float(v) * pitch) - float(h))
                for u, v, h in zip(ii, jj, y)), default=0.0)


def water_over(points, water):
    """The water surface height over each point ((n, 3) x, y, z; -inf where there is none). water: (vertices,
    triangles) of the level's water mesh, or None."""
    top = np.full(len(points), -np.inf)
    if water is None or not len(points):
        return top
    P = np.asarray(points, float)
    tri = np.asarray(water[0], float)[np.asarray(water[1])]
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tri:
        sel = np.nonzero((P[:, 0] >= min(ax, bx, cx)) & (P[:, 0] <= max(ax, bx, cx)) &
                         (P[:, 2] >= min(az, bz, cz)) & (P[:, 2] <= max(az, bz, cz)))[0]
        if not len(sel):
            continue
        d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(d) < 1e-12:
            continue
        X, Z = P[sel, 0], P[sel, 2]
        l1 = ((bz - cz) * (X - cx) + (cx - bx) * (Z - cz)) / d
        l2 = ((cz - az) * (X - cx) + (ax - cx) * (Z - cz)) / d
        l3 = 1 - l1 - l2
        ok = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        y = l1 * ay + l2 * by + l3 * cy
        top[sel[ok]] = np.maximum(top[sel[ok]], y[ok])
    return top


def export(root, lv, out=None, log=print, compare=False):
    """compare: also rasterise the outlines where the build's cells are read, and report how they differ."""
    import navkit
    pr = navkit.Project(root)
    hpd, xoff, zoff, hf, nav, exact = load_build(pr, root, lv, log)
    yoff = mohsoldier.Y_OFFSET
    sec_ids, sec_index, first_area = sector_order(nav)
    bmap, hght, area_rows, area_cells = export_areas(nav, exact, hf, yoff, sec_index, lv, log, compare)

    # nodes: ground heights, their areas
    gpos = nav.node_pos.copy()
    gpos[:, 1] -= yoff
    cellkey = cell_index(area_cells)
    node_area = node_areas(gpos, cellkey)

    E, cost, raised, kind = export_edges(nav, gpos)
    lp = os.path.join(pr.level_work(lv), 'ladders.json')
    ladders = json.load(open(lp, encoding='utf-8')) if os.path.isfile(lp) else []
    first_edge = np.searchsorted(E[:, 0], np.arange(len(gpos) + 1))
    log(f'{lv}: edges by kind ' + str({rsnv.EDGE_NAMES[k]: int(v) for k, v in zip(*np.unique(kind, return_counts=True))}))

    isl, island_rows = export_islands(nav)
    nflags = node_flags(root, lv, gpos, kind, E)
    anod = area_nodes(nav, node_area, area_rows, isl)
    port = export_portals(nav, area_rows, first_area, cellkey, yoff)
    sect, gbkt, glst = sector_buckets(nav, sec_ids, area_rows, xoff, zoff)
    ladr, unlinked = ladder_records(ladders, gpos)
    if unlinked:
        log(f'{lv}: {len(unlinked)} ladder(s) with no route node at an end left out of LADR: ' + ', '.join(unlinked[:6]))

    node_rows = [(float(x), float(y), float(z), int(first_edge[n])) for n, (x, y, z) in enumerate(gpos)]
    node_rows.append((0.0, 0.0, 0.0, int(first_edge[-1])))
    naux = [(int(node_area[n]), sec_index.get(int(nav.node_sector[n]), 0xFFFF), int(isl[n]), int(nflags[n]))
            for n in range(len(gpos))]
    edge_rows = [(int(b_) | (int(kind[k]) << 24), min(int(cost[k]), 0xFFFF), 0) for k, (a_, b_) in enumerate(E)]
    inputs = {'hpd': hpd, 'ladders.json': lp, 'navkit.json': pr.settings_path(lv)}
    if exact is not None:
        inputs['cells.npz'] = navbuild.cells_path(hpd)
    meta = export_meta(lv, exact, inputs, gpos, E, nav, port, ladders, ladr, unlinked, island_rows, nflags)
    sections = {'SECT': sect, 'NODE': node_rows, 'NAUX': naux, 'EDGE': edge_rows,
                'AREA': [tuple(r) for r in area_rows], 'PORT': port, 'BMAP': bytes(bmap), 'HGHT': [(v,) for v in hght],
                'GBKT': gbkt, 'GLST': [(v,) for v in glst], 'ANOD': [(v,) for v in anod], 'LADR': ladr, 'OFFM': [],
                'ISLD': island_rows, 'META': json.dumps(meta, indent=1).encode('utf-8')}
    out = out or os.path.join(pr.out_dir(lv), f'{lv}.rsnv')
    h_q = rsnv.heuristic_scale(gpos, E[:, 0], E[:, 1], np.minimum(cost, 0xFFFF))
    meta['heuristic scale'] = h_q / rsnv.H_SCALE_ONE
    meta['counts']['edge costs rounded up'] = raised
    header = {'level': lv, 'sea_level': 0.0, 'radius': mohsoldier.RADIUS, 'height': mohsoldier.HEIGHT,
              'step': mohsoldier.STEP, 'water_depth': mohsoldier.WATER_DEPTH,
              'hpd_sha256': meta['sources']['hpd']['sha256'], 'h_scale_q': h_q}
    size, tight = write_with_tight_edges(out, sections, header, meta, E, kind, edge_rows, first_edge)
    walk = sum(1 for k in range(len(E)) if int(kind[k]) == rsnv.EDGE_WALK)
    log(f'{lv}: heuristic scale {h_q / rsnv.H_SCALE_ONE:.6f}; wrote {out} ({size / 1e6:.2f} MB; {len(port)} portals, '
        f'{len(ladr)} ladders, {len(island_rows)} islands; {len(tight)} of {walk} walk edges tight)')
    return out, nav


def load_build(pr, root, lv, log):
    """The level's build: (hpd path, xoff, zoff, terrain height field, navdata, the build's cells or None)."""
    from mohcore import level as levelmod
    hpd = os.path.join(pr.out_dir(lv), 'pathdata.hpd')
    if not os.path.isfile(hpd):
        raise FileNotFoundError(f'{hpd}: build the level first (navkit.py build {lv})')
    st = pr.settings(lv)
    xoff, zoff = st['xoff'], st['zoff']
    hf = levelmod.heightfield_of(root, lv, log=lambda *a: None)
    if hf is None:
        raise FileNotFoundError(f'{lv}: no terrain height field')
    navdata.Y_OFFSET = mohsoldier.Y_OFFSET
    nav = navdata.load(hpd, hf)
    log(f'{lv}: {len(nav.areas)} areas, {len(nav.node_pos)} nodes, {len(nav.edges)} edges')
    exact = navbuild.read_cells(hpd)
    if exact is not None and (len(exact) != len(nav.areas) or any(e[0] != a.sector for e, a in zip(exact, nav.areas))):
        log(f'{lv}: {os.path.basename(navbuild.cells_path(hpd))} does not match the areas of the build: not used')
        exact = None
    if exact is None:
        log(f'{lv}: no cells file for this build: cells rasterised from the outlines, heights estimated (rebuild to '
            f'get {os.path.basename(navbuild.cells_path(hpd))})')
    return hpd, xoff, zoff, hf, nav, exact


def sector_order(nav):
    """Sectors and their areas, in the build's order: (sector ids, index by sector, first area by sector)."""
    sec_ids = []
    for a in nav.areas:
        if not sec_ids or sec_ids[-1] != a.sector:
            sec_ids.append(a.sector)
    sec_index = {s: k for k, s in enumerate(sec_ids)}
    first_area = {}
    for k, a in enumerate(nav.areas):
        first_area.setdefault(a.sector, k)
    return sec_ids, sec_index, first_area


def export_areas(nav, exact, hf, yoff, sec_index, lv, log, compare):
    """Areas: bitmaps at their pitch, height grids. Returns (bmap, hght, area rows, per area (ii, jj, y, pitch))."""
    stats = collections.Counter()
    bmap, hght, area_rows, area_cells = bytearray(), [], [], []
    for k, a in enumerate(nav.areas):
        pitch = 0.2 if a.pitch < 0.5 else COARSE_PITCH
        ii, jj, y = area_cells_of(k, a, pitch, exact, hf, yoff, compare, stats, lv)
        ci0, cj0, w_, h_, miny, maxy, packed = area_bitmap(a, ii, jj, y)
        x0, z0 = ci0 * pitch, cj0 * pitch
        li, lj = (ii - ci0, jj - cj0) if len(ii) else (ii, jj)
        grid, hw, hh, cpb = area_height_grid(li, lj, y, pitch, miny, stats)
        pmm = int(round(pitch * 1000))
        area_rows.append([x0, z0, x0 + (w_ - 1) * pitch, z0 + (h_ - 1) * pitch, miny, maxy, len(bmap), w_, h_,
                          0, 0, sec_index[a.sector], 0, 0, 0, 0, pmm, len(hght), hw, hh, cpb * pmm])
        bmap += packed.tobytes()
        hght += grid.ravel().tolist()
        area_cells.append((ii, jj, y, pitch))
    log(f'{lv}: bitmaps {len(bmap) / 1e6:.2f} MB, {sum(len(c[0]) for c in area_cells):,} cells'
        + (' (those of the build' + (f'; against the outlines: {dict(stats)})' if compare else ')') if exact is not None else '')
        + '; finer height grids: ' + str({k: v for k, v in stats.items() if 'height blocks' in k}))
    return bmap, hght, area_rows, area_cells


def area_cells_of(k, a, pitch, exact, hf, yoff, compare, stats, lv):
    """Area k's cells (ii, jj, ground y): the build's, else rasterised from its outline; compare counts the differences."""
    if exact is None or compare:
        ii, jj, y = rasterise(a, pitch, yoff, hf)
    if exact is None:
        return ii, jj, y
    ei, ej, ey = (np.asarray(v) for v in exact[k][2:])
    if abs(exact[k][1] - pitch) > 1e-3:
        raise ValueError(f'{lv}: area {k}: cells file pitch {exact[k][1]} against {pitch}')
    if compare:
        have = dict(zip(zip(ii.tolist(), jj.tolist()), y.tolist()))
        mine = set(zip(ei.tolist(), ej.tolist()))
        stats['cells'] += len(mine)
        stats['only in the outline'] += len(set(have) - mine)
        stats['only in the cells file'] += len(mine - set(have))
        dy = [abs(have[c] - float(v)) for c, v in zip(zip(ei.tolist(), ej.tolist()), ey.tolist()) if c in have]
        stats['estimate off by over 0.3 m'] += sum(d > 0.3 for d in dy)
    return ei.astype(np.int64), ej.astype(np.int64), ey.astype(np.float64)


def area_bitmap(a, ii, jj, y):
    """The area's cell bitmap: (first column, first row, width, height, min y, max y, packed rows of whole bytes)."""
    if len(ii):
        ci0, cj0 = int(ii.min()), int(jj.min())
        w_, h_ = int(ii.max()) - ci0 + 1, int(jj.max()) - cj0 + 1
        miny, maxy = float(y.min()), float(y.max())
    else:
        ci0 = cj0 = 0
        w_ = h_ = 0
        miny, maxy = a.min_y, a.max_y
    row = (w_ + 7) >> 3
    bits = np.zeros((h_, row * 8), np.uint8)
    if len(ii):
        bits[jj - cj0, ii - ci0] = 1
    packed = np.packbits(bits.reshape(h_, row, 8)[:, :, ::-1], axis=-1).reshape(-1) if h_ else np.zeros(0, np.uint8)
    return ci0, cj0, w_, h_, miny, maxy, packed


def area_height_grid(li, lj, y, pitch, miny, stats):
    """The area's height grid, made finer while it is off by over HEIGHT_TOL at a cell: (grid, width, height, cpb)."""
    cpb = HEIGHT_CELLS[pitch]
    grid, hw, hh = height_grid(li, lj, y, cpb, miny)
    for finer in (2, 1):
        if cpb <= finer or not len(li) or grid_error(li, lj, y, grid, hw, hh, cpb, pitch, miny) <= HEIGHT_TOL:
            break
        cpb = finer
        grid, hw, hh = height_grid(li, lj, y, cpb, miny)
    if cpb != HEIGHT_CELLS[pitch]:
        stats[f'areas with {cpb * pitch:g} m height blocks'] += 1
    return grid, hw, hh, cpb


def cell_index(area_cells):
    """{(pitch, i, j): [(area, ground y)]} over every area's cells."""
    cellkey = {}
    for k, (ii, jj, y, pitch) in enumerate(area_cells):
        for i, j, v in zip(ii.tolist(), jj.tolist(), y.tolist()):
            cellkey.setdefault((round(pitch, 1), i, j), []).append((k, v))
    return cellkey


def node_areas(gpos, cellkey):
    """Each node's area: the one with the nearest cell within 2 cells and 1 m of height (0xFFFF for none)."""
    node_area = np.full(len(gpos), 0xFFFF, np.int64)
    for n, (x, y, z) in enumerate(gpos):
        best = None
        for pitch in sorted({0.2, COARSE_PITCH}):
            ci, cj = int(round(x / pitch)), int(round(z / pitch))
            for di in (0, 1, -1, 2, -2):
                for dj in (0, 1, -1, 2, -2):
                    for k, v in cellkey.get((pitch, ci + di, cj + dj), ()):
                        if abs(v - y) <= 1.0:
                            d = math.hypot(di, dj) * pitch + abs(v - y)
                            if best is None or d < best[0]:
                                best = (d, k)
        if best is not None:
            node_area[n] = best[1]
    return node_area


def export_edges(nav, gpos):
    """Edges: CSR by source, kinds. Returns (edges sorted, admissible costs, how many were raised, kinds)."""
    E = nav.edges
    order = np.lexsort((E[:, 1], E[:, 0]))
    E = E[order]
    build_cost = np.round(nav.edge_cost[order] * 16).astype(np.int64)
    cost = admissible_costs(gpos, E[:, 0], E[:, 1], build_cost)
    raised = int((cost != build_cost).sum())
    kind = np.zeros(len(E), np.int64)
    if nav.ladder_edge is not None:
        lad = nav.ladder_edge[order]
        up = gpos[E[:, 1], 1] > gpos[E[:, 0], 1]
        kind[lad & up] = rsnv.EDGE_LADDER_UP
        kind[lad & ~up] = rsnv.EDGE_LADDER_DOWN
    # drop links (navbuild.DROP_LINKS): one-way walk edges down off a ledge, no edge back up. Only those: a ladder keeps
    # its kind, and a one-way edge on the level or up (a side file's route edge, navlinks) stays a walk
    both = set(zip(E[:, 0].tolist(), E[:, 1].tolist()))
    one_way = np.array([(b_, a_) not in both for a_, b_ in zip(E[:, 0].tolist(), E[:, 1].tolist())], bool)
    down = gpos[E[:, 1], 1] < gpos[E[:, 0], 1] - mohsoldier.STEP
    kind[one_way & down & (kind == rsnv.EDGE_WALK)] = rsnv.EDGE_DROP
    return E, cost, raised, kind


def export_islands(nav):
    """Islands: (island per node, ISLD rows - member count, first member, the main one flagged)."""
    isl = nav.node_island.astype(np.int64)
    island_rows = []
    for k_i, I in enumerate(nav.islands):
        members = np.nonzero(isl == k_i)[0]
        island_rows.append((len(members), int(members[0]) if len(members) else 0, rsnv.ISLD_MAIN if k_i == 0 else 0, 0, 0))
    return isl, island_rows


def node_flags(root, lv, gpos, kind, E):
    """Node flags: water over the node (the level's water mesh), ladder ends."""
    import navregress
    water = navregress.level_water(root, lv)
    depth = water_over(gpos, water) - gpos[:, 1]
    nflags = np.zeros(len(gpos), np.int64)
    nflags[(depth > 0) & (depth <= SHALLOW)] |= rsnv.NAUX_SHALLOW
    nflags[(depth > SHALLOW) & (depth <= mohsoldier.WATER_DEPTH + 0.05)] |= rsnv.NAUX_WADE
    for k_e in np.nonzero((kind == rsnv.EDGE_LADDER_UP) | (kind == rsnv.EDGE_LADDER_DOWN))[0]:
        nflags[E[k_e, 0]] |= rsnv.NAUX_LADDER_END
    return nflags


def area_nodes(nav, node_area, area_rows, isl):
    """Areas: island (most of their nodes, else the reader's), nodes by area. Fills the rows; returns ANOD."""
    anod = []
    by_area = collections.defaultdict(list)
    for n, a_ in enumerate(node_area):
        if a_ != 0xFFFF:
            by_area[int(a_)].append(n)
    for k, row in enumerate(area_rows):
        ns = by_area.get(k, [])
        row[12] = collections.Counter(isl[ns].tolist()).most_common(1)[0][0] if ns else max(nav.areas[k].island, 0)
        row[14], row[15] = len(anod), len(ns)
        anod += ns
    return anod


def export_portals(nav, area_rows, first_area, cellkey, yoff):
    """Portals: inside sectors by target index; border portals to the neighbour sector's area across. Fills the rows."""
    port = []
    per_area = collections.defaultdict(list)
    for k, a in enumerate(nav.areas):
        base = first_area[a.sector]
        for p0, p1, target in a.portals:
            per_area[k].append((p0, p1, base + int(target)))
        for p0, p1 in a.borders:
            hit = border_target(nav, a, p0, p1, cellkey, yoff)
            if hit is not None:
                per_area[k].append((p0, p1, hit))
    for k in range(len(nav.areas)):
        area_rows[k][9] = len(port)
        area_rows[k][10] = len(per_area[k])
        for p0, p1, target in per_area[k]:
            port.append((float(p0[0]), float(p0[2]), float(p1[0]), float(p1[2]), float((p0[1] + p1[1]) / 2 - yoff),
                         k, int(target)))
    return port


def border_target(nav, a, p0, p1, cellkey, yoff):
    """The area of another sector 0.6 m across the border p0-p1 at its height (one side, then the other), or None."""
    m = (np.asarray(p0) + np.asarray(p1)) / 2
    d = np.asarray(p1) - np.asarray(p0)
    nrm = np.array([-d[2], 0.0, d[0]])
    nrm /= max(np.linalg.norm(nrm), 1e-9)
    for sgn in (1, -1):
        q = m + sgn * 0.6 * nrm
        hit = None
        for pitch in sorted({0.2, COARSE_PITCH}):
            for kk, v in cellkey.get((pitch, int(round(q[0] / pitch)), int(round(q[2] / pitch))), ()):
                if nav.areas[kk].sector != a.sector and abs(v - (m[1] - yoff)) <= 1.0:
                    hit = kk
                    break
            if hit is not None:
                break
        if hit is not None:
            return hit
    return None


def sector_buckets(nav, sec_ids, area_rows, xoff, zoff):
    """Sectors and their 4 m buckets: (SECT, GBKT, GLST rows)."""
    sect, gbkt, glst = [], [], []
    for s in sec_ids:
        X, Z = (s >> 8) & 0xFF, s & 0xFF
        sx0, sz0 = (X + xoff) * 100.0, (Z + zoff) * 100.0
        ks = [k for k, a in enumerate(nav.areas) if a.sector == s]
        pitch_code = 0 if nav.areas[ks[0]].pitch < 0.5 else 1
        first_bucket = len(gbkt)
        buckets = [[] for _ in range(rsnv.BUCKETS * rsnv.BUCKETS)]
        for k in ks:
            r = area_rows[k]
            if r[7] == 0:
                continue
            m = r[16] / 1000.0                  # a whole cell of margin (BF1943 2026-10-26)
            b0 = max(int((r[0] - m - sx0) // rsnv.BUCKET), 0)
            b1 = min(int((r[2] + m - sx0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            c0 = max(int((r[1] - m - sz0) // rsnv.BUCKET), 0)
            c1 = min(int((r[3] + m - sz0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            for bj in range(c0, c1 + 1):
                for bi in range(b0, b1 + 1):
                    buckets[bj * rsnv.BUCKETS + bi].append(k)
        for lst in buckets:
            lst.sort(key=lambda k: area_rows[k][4])
            gbkt.append((len(glst), len(lst)))
            glst += lst
        sect.append((s, 0, pitch_code, sx0, sz0, sx0 + 100.0, sz0 + 100.0, first_bucket, ks[0], len(ks), 0))
    return sect, gbkt, glst


def nearest_node(gpos, p, maxd=END_MATCH):
    """(the node nearest p, its distance), the node None when it is further than maxd."""
    d = np.linalg.norm(gpos - np.asarray(p, float), axis=1)
    k = int(np.argmin(d))
    return (k, float(d[k])) if d[k] <= maxd else (None, float(d[k]))


def ladder_records(ladders, gpos):
    """LADR rows and the ladders left out: only records whose two ends are nodes of the build."""
    # ladders: only records whose two ends are nodes of the build (rsnav's loader refuses a LADR end that is no node)
    ladr, unlinked = [], []
    for k, L in enumerate(ladders):
        lo, hi = L['lo'], L['hi']
        foot = [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]
        top = [(lo[0] + hi[0]) / 2, hi[1], (lo[2] + hi[2]) / 2]
        fn, _ = nearest_node(gpos, foot, LADDER_MATCH)
        tn, _ = nearest_node(gpos, top, LADDER_MATCH)
        if fn is None or tn is None:
            unlinked.append(f"ladder {k} ({L.get('bp', '?').rsplit('/', 1)[-1]})")
            continue
        ladr.append((*foot, *top, 0.0, fn, tn, k))
    return ladr, unlinked


def export_meta(lv, exact, inputs, gpos, E, nav, port, ladders, ladr, unlinked, island_rows, nflags):
    """The META section's provenance: exporter, level, sources with their hashes, the soldier, counts."""
    return {'exporter': EXPORTER_VERSION, 'game': 'Medal of Honor (2010) MP', 'level': lv,
            'cells': 'build' if exact is not None else 'rasterised outlines',
            'sources': {k: {'path': v, 'sha256': sha256(v)} for k, v in inputs.items() if os.path.isfile(v)},
            'soldier': {'radius': mohsoldier.RADIUS, 'step': mohsoldier.STEP, 'height': mohsoldier.HEIGHT,
                        'slope_max': mohsoldier.SLOPE, 'water_depth': mohsoldier.WATER_DEPTH, 'jump': mohsoldier.JUMP},
            'counts': {'nodes': len(gpos), 'edges': len(E), 'areas': len(nav.areas), 'portals': len(port),
                       'ladders': len(ladders), 'LADR records': len(ladr), 'ladders not linked': len(unlinked),
                       'islands': len(island_rows), 'shallow nodes': int(((nflags & rsnv.NAUX_SHALLOW) > 0).sum()),
                       'wading nodes': int(((nflags & rsnv.NAUX_WADE) > 0).sum())}}


def write_with_tight_edges(out, sections, header, meta, E, kind, edge_rows, first_edge):
    """Writes the file, flags the tight walk edges over its cells, writes it again. Returns (size, tight edges)."""
    rsnv.write(out, sections, header, False)
    # tight edges: walk edges with no line of sight either way over the cells just written (the runtime's rule)
    w = rsnv.load(out)
    tight = set()
    for k, (a_, b_) in enumerate(E):
        if int(kind[k]) != rsnv.EDGE_WALK or k in tight:
            continue
        pa, pb = tuple(float(v) for v in w.node_pos[a_]), tuple(float(v) for v in w.node_pos[b_])
        if not w.los(pa, pb) and not w.los(pb, pa):
            tight.add(k)
            tight.update(j for j in range(int(first_edge[b_]), int(first_edge[b_ + 1])) if int(E[j][1]) == int(a_))
    sections['EDGE'] = [(t, c, rsnv.EDGEF_TIGHT if k in tight else 0) for k, (t, c, _f) in enumerate(edge_rows)]
    meta['counts']['tight walk edges'] = len(tight)
    sections['META'] = json.dumps(meta, indent=1).encode('utf-8')
    size = rsnv.write(out, sections, header, False)
    return size, tight


def check(path, nav, log=print):
    """Reads the file back: validate(), and its graph against the build's (navdata)."""
    w = rsnv.load(path)
    problems = list(w.validate())
    n = len(nav.node_pos)
    if len(w.node_pos) != n:
        problems.append(f'{len(w.node_pos)} nodes, the build has {n}')
    else:
        d = np.abs(w.node_pos[:, [0, 2]] - nav.node_pos[:, [0, 2]]).max()
        if d > 1e-3:
            problems.append(f'node positions differ by up to {d:.4f} m')
        src = np.repeat(np.arange(n), np.diff(w.node_first_edge))
        got = sorted(zip(src.tolist(), w.edge_target.tolist(), w.edge_cost.tolist()))
        gp = nav.node_pos.copy()
        gp[:, 1] -= mohsoldier.Y_OFFSET
        want = sorted(zip(nav.edges[:, 0].tolist(), nav.edges[:, 1].tolist(),
                          np.minimum(admissible_costs(gp, nav.edges[:, 0], nav.edges[:, 1],
                                                      np.round(nav.edge_cost * 16).astype(np.int64)), 0xFFFF).tolist()))
        if got != want:
            problems.append('edges or costs differ from the build')
    unplaced = int((w.node_area == 0xFFFF).sum())
    log(f'check: {len(problems)} problem(s); nodes without an area {unplaced} of {len(w.node_pos)}')
    for p in problems:
        log('  ' + p)
    return problems, w


def main(argv):
    ap = argparse.ArgumentParser(prog='navexport.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level')
    ap.add_argument('--out')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--compare', action='store_true', help='also compare the cells of the build with the rasterised outlines')
    ap.add_argument('--opfor', help='Project OPFOR folder (default: OPFOR_ROOT, else found next to the suite)')
    a = ap.parse_args(argv[1:])
    import navkit
    root = navkit.find_opfor(a.opfor)
    lv = navkit.Project(root).level(a.level)
    path, nav = export(root, lv, a.out, compare=a.compare)
    if a.check:
        problems, _ = check(path, nav)
        return 1 if problems else 0
    return 0


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('navexport', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
