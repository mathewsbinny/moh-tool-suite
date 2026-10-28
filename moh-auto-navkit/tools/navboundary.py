r"""NavKit: cell-by-cell area boundary diagnostic against DICE's path data (normal pitch, single-layer columns).

  navboundary.py <level> [<candidate.hpd>] [--out <folder>] [--region x0,z0,x1,z1] [--opfor <folder>]

The candidate defaults to OPFOR's work\navkit\regress\<level>.hpd (navregress.py's build). Every straight
neighbour edge between two cells that both datasets have is classified:
  match      a boundary in both (the two cells are in different areas in DICE's data and in NavKit's)
  missing    a DICE boundary NavKit does not have (NavKit merges the two DICE areas there)
  extra      a NavKit boundary DICE does not have
  (interior) no boundary in either
Per cell: DICE and NavKit area, terrain height and slope, whether the DICE area is a single cell (DICE cuts steep
cells off on their own) and whether NavKit merges the DICE area with others. Columns with more than one floor in
either dataset are left out (counted in the summary).

Measures (summary.json): boundary precision / recall at 0, 1 and 2 cells' tolerance, exact area matches, DICE areas
merged / split, portal count and displacement (each portal's midpoint to the nearest portal midpoint of the other
dataset, both ways), ground in one dataset only.

Writes to --out (default OPFOR's work\navkit\boundary\<level>): cells.csv, edges.csv (non-interior edges),
portals.csv, summary.json, and maps: overview.png (whole level) and worst_sector.png (the 100 m square with the
most missing + extra edges), or region.png for --region.
"""
import csv
import json
import os
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))

TOLERANCES = (0, 1, 2)
OFF = 2 ** 20


def _keys(ij):
    return (ij[:, 0] + OFF) * 2 ** 21 + (ij[:, 1] + OFF)


def cells_of(nav):
    """Single-layer normal cells of a NavData: (ij (n, 2), area (n,), key (n,) sorted), and the multi-layer keys."""
    from mohcore import navdata
    normal = np.array([not a.detailed for a in nav.areas], bool)
    if not len(nav.cell_area):
        return np.zeros((0, 2), np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64)
    m = normal[nav.cell_area]
    ij = np.round(nav.cell_xz[m] / navdata.GRID).astype(np.int64)
    area = nav.cell_area[m].astype(np.int64)
    key = _keys(ij)
    u, cnt = np.unique(key, return_counts=True)
    one = np.isin(key, u[cnt == 1])
    order = np.argsort(key[one])
    return ij[one][order], area[one][order], key[one][order], u[cnt > 1]


def _index(keys, wanted):
    pos = np.minimum(np.searchsorted(keys, wanted), max(len(keys) - 1, 0))
    ok = (keys[pos] == wanted) if len(keys) else np.zeros(len(wanted), bool)
    return pos, ok


def _near(points, targets, r):
    """For each point (half-cell integer coordinates), whether a target lies within r half cells (Chebyshev)."""
    tset = {tuple(t) for t in targets.tolist()}
    offs = [(a, b) for a in range(-r, r + 1) for b in range(-r, r + 1)]
    return np.array([any((p[0] + a, p[1] + b) in tset for a, b in offs) for p in points.tolist()], bool)


def _portal_mids(nav):
    out = []
    for a in nav.areas:
        if a.detailed:
            continue
        for p0, p1, _ in a.portals:
            out.append(((p0[0] + p1[0]) / 2, (p0[2] + p1[2]) / 2, float(np.hypot(p1[0] - p0[0], p1[2] - p0[2]))))
    return np.array(out, float).reshape(-1, 3)


def _nearest(a, b):
    """Distance from each 2-D point of a to the nearest of b (grid buckets of 10 m, then exact)."""
    if not len(a):
        return np.zeros(0)
    if not len(b):
        return np.full(len(a), np.inf)
    cell = 10.0
    buckets = {}
    for k, (x, z) in enumerate(b.tolist()):
        buckets.setdefault((int(x // cell), int(z // cell)), []).append(k)
    out = np.full(len(a), np.inf)
    for n, (x, z) in enumerate(a.tolist()):
        bx, bz = int(x // cell), int(z // cell)
        for r in range(0, 30):
            cand = [k for i in range(bx - r, bx + r + 1) for j in range(bz - r, bz + r + 1)
                    if max(abs(i - bx), abs(j - bz)) == r for k in buckets.get((i, j), [])]
            if cand:
                d = np.hypot(b[cand, 0] - x, b[cand, 1] - z).min()
                out[n] = min(out[n], d)
                if out[n] <= r * cell:
                    break
    return out


def diagnose(ref, cand, heightfield=None):
    """The cell and edge classification and the measures. ref / cand: NavData (DICE / NavKit)."""
    from mohcore import navdata
    G = navdata.GRID
    rij, rarea, rkey, rmulti = cells_of(ref)
    cij, carea, ckey, cmulti = cells_of(cand)
    multi = np.union1d(rmulti, cmulti)
    keep_r = ~np.isin(rkey, multi)
    keep_c = ~np.isin(ckey, multi)
    rij, rarea, rkey = rij[keep_r], rarea[keep_r], rkey[keep_r]
    cij, carea, ckey = cij[keep_c], carea[keep_c], ckey[keep_c]
    pos, both = _index(ckey, rkey)
    ij = rij[both]
    a_r = rarea[both]
    a_c = carea[pos[both]]
    key = rkey[both]
    n = len(key)
    size_r = Counter(rarea.tolist())
    # DICE areas NavKit merges: the NavKit area holding most of a DICE area's shared cells also holds other DICE areas
    pairs = Counter(zip(a_r.tolist(), a_c.tolist()))
    best = {}
    for (r, c), k in pairs.items():
        if k > best.get(r, (None, -1))[1]:
            best[r] = (c, k)
    c_to_r = {}
    for (r, c) in pairs:
        c_to_r.setdefault(c, set()).add(r)
    merged_r = {r for r, (c, _) in best.items() if len(c_to_r[c]) > 1}
    r_to_c = {}
    for (r, c) in pairs:
        r_to_c.setdefault(r, set()).add(c)
    exact = sum(1 for r, cs in r_to_c.items() if len(cs) == 1 and c_to_r[next(iter(cs))] == {r}
                and size_r[r] == sum(k for (rr, _), k in pairs.items() if rr == r))
    split_r = sum(1 for r, cs in r_to_c.items() if len(cs) > 1)

    h = slope = None
    if heightfield is not None:
        x, z = ij[:, 0] * G, ij[:, 1] * G
        h = heightfield.height(x, z)
        e = 0.05
        gx = (heightfield.height(x + e, z) - heightfield.height(x - e, z)) / (2 * e)
        gz = (heightfield.height(x, z + e) - heightfield.height(x, z - e)) / (2 * e)
        slope = np.degrees(np.arctan(np.hypot(gx, gz)))

    # straight neighbour edges between shared cells
    edges = []
    for di, dj in ((1, 0), (0, 1)):
        q, ok = _index(key, _keys(ij + np.array([di, dj])))
        p = np.nonzero(ok)[0]
        q = q[ok]
        bd = a_r[p] != a_r[q]
        bc = a_c[p] != a_c[q]
        mid = 2 * ij[p] + np.array([di, dj])                 # half-cell coordinates of the edge midpoint
        edges.append((p, q, mid, bd, bc))
    P = np.concatenate([e[0] for e in edges]); Q = np.concatenate([e[1] for e in edges])
    M = np.concatenate([e[2] for e in edges]).reshape(-1, 2)
    BD = np.concatenate([e[3] for e in edges]); BC = np.concatenate([e[4] for e in edges])
    kind = np.full(len(P), '', dtype=object)
    kind[BD & BC] = 'match'
    kind[BD & ~BC] = 'missing'
    kind[~BD & BC] = 'extra'
    summary = {'shared_cells': int(n), 'dice_only_cells': int((~both).sum()), 'navkit_only_cells': int(len(ckey) - both.sum()),
               'multi_layer_columns_left_out': int(len(multi)),
               'edges': int(len(P)), 'match': int((BD & BC).sum()), 'missing': int((BD & ~BC).sum()),
               'extra': int((~BD & BC).sum()),
               'dice_areas': len(size_r), 'dice_single_cell_areas': sum(1 for v in size_r.values() if v == 1),
               'dice_areas_merged_by_navkit': len(merged_r), 'dice_areas_split_by_navkit': split_r,
               'exact_areas': exact, 'exact_areas_pct': round(100.0 * exact / max(len(size_r), 1), 2)}
    for t in TOLERANCES:
        pb, db = M[BC], M[BD]
        summary[f'boundary_precision_{t}'] = round(100.0 * _near(pb, db, 2 * t).mean(), 2) if len(pb) else None
        summary[f'boundary_recall_{t}'] = round(100.0 * _near(db, pb, 2 * t).mean(), 2) if len(db) else None
    pr, pc = _portal_mids(ref), _portal_mids(cand)
    d_rc, d_cr = _nearest(pr[:, :2], pc[:, :2]), _nearest(pc[:, :2], pr[:, :2])
    summary.update({'portals_dice': int(len(pr)), 'portals_navkit': int(len(pc)),
                    'portal_displacement_dice_to_navkit_median': round(float(np.median(d_rc)), 2) if len(d_rc) else None,
                    'portal_displacement_navkit_to_dice_median': round(float(np.median(d_cr)), 2) if len(d_cr) else None,
                    'dice_portals_within_1m': round(100.0 * float(np.mean(d_rc <= 1.0)), 2) if len(d_rc) else None,
                    'navkit_portals_within_1m': round(100.0 * float(np.mean(d_cr <= 1.0)), 2) if len(d_cr) else None})
    cells = {'ij': ij, 'dice_area': a_r, 'navkit_area': a_c, 'height': h, 'slope': slope,
             'dice_single': np.array([size_r[r] == 1 for r in a_r.tolist()], bool),
             'dice_merged': np.array([r in merged_r for r in a_r.tolist()], bool)}
    only = {'dice': rij[~both], 'navkit': cij[~np.isin(ckey, rkey)]}
    return summary, cells, {'p': P, 'q': Q, 'mid': M, 'kind': kind}, {'dice': pr, 'navkit': pc}, only


def render(path, cells, edges, portals, only, region, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from mohcore import navdata
    G = navdata.GRID
    x0, z0, x1, z1 = region
    span = max(x1 - x0, z1 - z0)
    fig, ax = plt.subplots(figsize=(12, 12), dpi=110 if span > 400 else 150)
    ij = cells['ij']
    X, Z = ij[:, 0] * G, ij[:, 1] * G
    inr = (X >= x0) & (X <= x1) & (Z >= z0) & (Z <= z1)
    ms = max(0.2, (72 * 12 / (span / G)) ** 2 * 0.9)
    if cells['slope'] is not None:
        sc = ax.scatter(X[inr], Z[inr], c=cells['slope'][inr], cmap='Greys', vmin=0, vmax=50, s=ms, marker='s', linewidths=0)
        fig.colorbar(sc, ax=ax, fraction=0.03, label='terrain slope (deg)')
    else:
        ax.scatter(X[inr], Z[inr], c='0.85', s=ms, marker='s', linewidths=0)
    for name, colour in (('dice', '#d6a400'), ('navkit', '#7a3db8')):
        o = only[name]
        if len(o):
            ox, oz = o[:, 0] * G, o[:, 1] * G
            m = (ox >= x0) & (ox <= x1) & (oz >= z0) & (oz <= z1)
            ax.scatter(ox[m], oz[m], c=colour, s=ms, marker='s', linewidths=0,
                       label=f'ground in {"DICE" if name == "dice" else "NavKit"} only')
    single = inr & cells['dice_single']
    ax.scatter(X[single], Z[single], facecolors='none', edgecolors='#e0007a', s=ms * 2.5, marker='o', linewidths=0.6,
               label='DICE single-cell area')
    mid = edges['mid']
    for kind, colour, label in (('match', '#1a9850', 'boundary in both'), ('missing', '#d73027', 'DICE boundary NavKit lacks'),
                                ('extra', '#4575b4', 'NavKit boundary DICE lacks')):
        sel = edges['kind'] == kind
        if not sel.any():
            continue
        m = mid[sel].astype(float) / 2 * G                  # edge midpoint in metres
        vertical = (mid[sel, 0] % 2) == 1                   # x odd: an edge between (i, j) and (i+1, j) - a line along z
        segs = np.where(vertical[:, None, None],
                        np.stack([np.stack([m[:, 0], m[:, 1] - G / 2], 1), np.stack([m[:, 0], m[:, 1] + G / 2], 1)], 1),
                        np.stack([np.stack([m[:, 0] - G / 2, m[:, 1]], 1), np.stack([m[:, 0] + G / 2, m[:, 1]], 1)], 1))
        keep = (m[:, 0] >= x0) & (m[:, 0] <= x1) & (m[:, 1] >= z0) & (m[:, 1] <= z1)
        ax.add_collection(LineCollection(segs[keep], colors=colour, linewidths=1.6 if span < 400 else 0.8, label=label))
    for name, colour, marker in (('dice', 'black', 'x'), ('navkit', '#ff7f00', '+')):
        p = portals[name]
        if len(p):
            m = (p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= z0) & (p[:, 1] <= z1)
            ax.scatter(p[m, 0], p[m, 1], c=colour, marker=marker, s=18, linewidths=0.8,
                       label=f'{"DICE" if name == "dice" else "NavKit"} portal (midpoint)')
    ax.set_xlim(x0, x1)
    ax.set_ylim(z0, z1)
    ax.set_aspect('equal')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('z (m)')
    ax.set_title(title)
    ax.legend(loc='upper right', fontsize=7, markerscale=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def export(out, cells, edges, portals):
    from mohcore import navdata
    G = navdata.GRID
    with open(os.path.join(out, 'cells.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['x', 'z', 'dice_area', 'navkit_area', 'height', 'slope_deg', 'dice_single_cell', 'dice_area_merged'])
        h, s = cells['height'], cells['slope']
        for k, (i, j) in enumerate(cells['ij'].tolist()):
            w.writerow([round(i * G, 2), round(j * G, 2), int(cells['dice_area'][k]), int(cells['navkit_area'][k]),
                        '' if h is None else round(float(h[k]), 3), '' if s is None else round(float(s[k]), 1),
                        int(cells['dice_single'][k]), int(cells['dice_merged'][k])])
    with open(os.path.join(out, 'edges.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['x', 'z', 'along', 'kind', 'dice_area_a', 'dice_area_b', 'navkit_area_a', 'navkit_area_b', 'height_step'])
        h = cells['height']
        for k in np.nonzero(edges['kind'] != '')[0].tolist():
            p, q, m = edges['p'][k], edges['q'][k], edges['mid'][k]
            w.writerow([round(m[0] / 2 * G, 2), round(m[1] / 2 * G, 2), 'z' if m[0] % 2 else 'x', edges['kind'][k],
                        int(cells['dice_area'][p]), int(cells['dice_area'][q]), int(cells['navkit_area'][p]),
                        int(cells['navkit_area'][q]), '' if h is None else round(float(abs(h[q] - h[p])), 3)])
    with open(os.path.join(out, 'portals.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['source', 'x', 'z', 'length'])
        for name in ('dice', 'navkit'):
            for x, z, ln in portals[name].tolist():
                w.writerow([name, round(x, 2), round(z, 2), round(ln, 2)])


def worst_square(edges, size=100.0):
    from mohcore import navdata
    G = navdata.GRID
    bad = edges['mid'][(edges['kind'] == 'missing') | (edges['kind'] == 'extra')].astype(float) / 2 * G
    if not len(bad):
        return None
    sq = Counter(map(tuple, np.floor(bad / size).astype(int).tolist()))
    (i, j), _ = sq.most_common(1)[0]
    return (i * size, j * size, (i + 1) * size, (j + 1) * size)


def main(argv):
    args = argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    pos = [a for k, a in enumerate(args) if not a.startswith('--') and (k == 0 or not args[k - 1].startswith('--'))]
    if not pos:
        print(__doc__)
        return 2
    import navparity
    from mohcore import navdata, level as levelmod
    lv = pos[0].lower()
    root = navparity.opfor_root(opt('--opfor'))
    cand_path = pos[1] if len(pos) > 1 else os.path.join(root, 'work', 'navkit', 'regress', f'{lv}.hpd')
    if not os.path.isfile(cand_path):
        raise FileNotFoundError(f'no NavKit file {cand_path} (build it with navregress.py --levels {lv})')
    out = opt('--out') or os.path.join(root, 'work', 'navkit', 'boundary', lv)
    os.makedirs(out, exist_ok=True)
    t = time.time()
    ref = navdata.load(navparity.reference_of(root, lv))
    cand = navdata.load(cand_path)
    try:
        hf = levelmod.heightfield_of(root, lv, log=lambda *a: None)
    except Exception as e:
        print(f'no terrain heights ({e}); maps without slope')
        hf = None
    summary, cells, edges, portals, only = diagnose(ref, cand, hf)
    summary.update({'level': lv, 'candidate': cand_path, 'date': time.strftime('%Y-%m-%d %H:%M')})
    export(out, cells, edges, portals)
    G = navdata.GRID
    X, Z = cells['ij'][:, 0] * G, cells['ij'][:, 1] * G
    if opt('--region'):
        region = tuple(float(v) for v in opt('--region').split(','))
        render(os.path.join(out, 'region.png'), cells, edges, portals, only, region, f'{lv}: area boundaries, region')
    else:
        whole = (X.min() - 5, Z.min() - 5, X.max() + 5, Z.max() + 5) if len(X) else (0, 0, 100, 100)
        render(os.path.join(out, 'overview.png'), cells, edges, portals, only, whole, f'{lv}: area boundaries')
        w = worst_square(edges)
        if w:
            summary['worst_square'] = w
            render(os.path.join(out, 'worst_sector.png'), cells, edges, portals, only, w,
                   f'{lv}: the 100 m square with the most mismatched boundary edges')
    json.dump(summary, open(os.path.join(out, 'summary.json'), 'w'), indent=1)
    for k in ('shared_cells', 'match', 'missing', 'extra', 'boundary_precision_0', 'boundary_recall_0',
              'boundary_precision_1', 'boundary_recall_1', 'boundary_precision_2', 'boundary_recall_2',
              'exact_areas_pct', 'dice_areas_merged_by_navkit', 'dice_areas_split_by_navkit',
              'portals_dice', 'portals_navkit', 'portal_displacement_dice_to_navkit_median', 'dice_portals_within_1m'):
        print(f'  {k:42s} {summary.get(k)}')
    print(f'wrote {out} ({time.time() - t:.0f} s)')
    return 0


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('navboundary', gui=False)      # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
