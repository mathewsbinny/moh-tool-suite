r"""NavKit: does DICE's path data leave out ground under water? Water depth against path-data coverage.

  navwater.py [level ...] [--candidate <pattern with {lv}>] [--json out.json] [--opfor <folder>]

Per level, every walkable cell (0.8 m grid, navdiff's cells) of NavKit's file and of DICE's gets a water
depth: the level's water surface (core/mohcore/water.py) above the cell minus the ground height (the
terrain height field where the cell lies on terrain, else navdiff's height estimate). Cells are grouped in
depth bands; for each band: NavKit's cells, the share DICE also has, and DICE's own cells there. If DICE
leaves out ground under water, its share drops in the deeper bands. Also: how much of NavKit's extra ground
(cells DICE does not have) is under water.

Default candidate: OPFOR's work\navbuild\{lv}_W.hpd (baseline A's files); default levels: the campaign
levels with water and DICE path data.
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))
import navdiff  # noqa: E402
import navparity  # noqa: E402

BANDS = [(-1e9, -0.5, 'dry (water more than 0.5 m below)'), (-0.5, 0.0, 'at the shore (0-0.5 m below)'),
         (0.0, 0.5, 'under 0-0.5 m'), (0.5, 1.0, 'under 0.5-1 m'), (1.0, 1.5, 'under 1-1.5 m'), (1.5, 2.0, 'under 1.5-2 m'),
         (2.0, 3.0, 'under 2-3 m'), (3.0, 1e9, 'under more than 3 m')]
TILE = 16.0
NO_WATER = -1e9


def cells(path):
    """(i, j) -> list of ground height estimates, every area of every sector."""
    out = defaultdict(list)
    for s in navdiff.load(path).values():
        for area in s.cells():
            for ij, y in area.items():
                out[ij].append(y)
    return out


def water_heights(points, water):
    """Water surface height over each (x, z) point (the highest where pieces overlap), NO_WATER where none."""
    top = np.full(len(points), NO_WATER)
    if water is None or not len(water):
        return top
    tiles = defaultdict(list)
    keys = np.floor(points / TILE).astype(np.int64)
    for n, (a, b) in enumerate(keys):
        tiles[(a, b)].append(n)
    tiles = {k: np.array(v) for k, v in tiles.items()}
    tri = water.vertices[water.triangles].astype(np.float64)
    lo = np.floor(tri[:, :, [0, 2]].min(axis=1) / TILE).astype(np.int64)
    hi = np.floor(tri[:, :, [0, 2]].max(axis=1) / TILE).astype(np.int64)
    for t in range(len(tri)):
        idx = [tiles[(a, b)] for a in range(lo[t, 0], hi[t, 0] + 1) for b in range(lo[t, 1], hi[t, 1] + 1) if (a, b) in tiles]
        if not idx:
            continue
        idx = np.concatenate(idx)
        p = points[idx]
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri[t]
        d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(d) < 1e-12:
            continue
        l1 = ((bz - cz) * (p[:, 0] - cx) + (cx - bx) * (p[:, 1] - cz)) / d
        l2 = ((cz - az) * (p[:, 0] - cx) + (ax - cx) * (p[:, 1] - cz)) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if inside.any():
            y = l1[inside] * ay + l2[inside] * by + l3[inside] * cy
            k = idx[inside]
            top[k] = np.maximum(top[k], y)
    return top


def floors(cellmap):
    """Every floor of every cell: ((i, j), height estimate), stacked floors (a bridge over a river) apart."""
    out = []
    for ij, ys in cellmap.items():
        for y in sorted(set(round(v, 1) for v in ys)):
            if not out or out[-1][0] != ij or abs(out[-1][1] - y) > 1.0:
                out.append((ij, y))
    return out


def depths(entries, heightfield, water):
    """Per floor: water depth above its ground (NaN where there is no water above). The ground is the terrain
    height field's where the floor lies on terrain, else navdiff's estimate."""
    pts = np.array([(i * navdiff.GRID, j * navdiff.GRID) for (i, j), _ in entries], dtype=np.float64)
    est = np.array([y for _, y in entries])
    ground = est
    if heightfield is not None and len(pts):
        t = heightfield.height(pts[:, 0], pts[:, 1])
        ground = np.where(np.abs(t - est) < 1.5, t, est)
    w = water_heights(pts, water)
    return np.where(w > NO_WATER / 2, w - ground, np.nan)


def band_of(d):
    if np.isnan(d):
        return 0
    for n, (a, b, _) in enumerate(BANDS):
        if a <= d < b:
            return n
    return len(BANDS) - 1


def level_report(root, lv, candidate, log=print):
    from mohcore import level as levelmod, water as watermod
    ref = navparity.reference_of(root, lv)
    name = None
    for b in ('pkg', 'dist'):
        import glob
        for f in glob.glob(os.path.join(root, 'work', 'levels', lv, b, 'levels', '*.dbx')):
            if os.path.splitext(os.path.basename(f))[0].lower() == lv:
                from mohcore import dbx
                name = levelmod.terrain_name(dbx.read(f).instances)
                break
        if name:
            break
    hf = levelmod.heightfield_of(root, lv, log=lambda *a: None, name=name)
    w = watermod.read(os.path.join(root, 'work', 'levels', lv, 'terrain'), name or lv)
    if w is None or not len(w):
        log(f'{lv}: no water')
        return None
    D, N = cells(ref), cells(candidate)
    fn, fd = floors(N), floors(D)
    return compare(lv, fn, fd, depths(fn, hf, w), depths(fd, hf, w), len(w))


def compare(lv, fn, fd, dn, dd, water_triangles):
    """The report of level_report from NavKit's and DICE's floors (floors) and their water depths (depths)."""
    # the same ground, as navdiff pairs cells: one floor on each side is the same; stacked floors pair by
    # height, up to navdiff's STACK_TOL apart (the height estimates are rough on sloped areas)
    floors_d = defaultdict(list)
    for ij, y in fd:
        floors_d[ij].append(y)
    count_n = defaultdict(int)
    for ij, _ in fn:
        count_n[ij] += 1
    has = np.array([bool(floors_d.get(ij)) and ((count_n[ij] == 1 and len(floors_d[ij]) == 1)
                                                 or any(abs(y - yd) <= navdiff.STACK_TOL for yd in floors_d[ij]))
                    for ij, y in fn], dtype=bool)        # bool when empty too: ~ of an empty float array fails
    bn = np.array([band_of(x) for x in dn])
    bd = np.array([band_of(x) for x in dd])
    rows = []
    for n, (_, _, label) in enumerate(BANDS):
        sel = bn == n
        rows.append({'band': label, 'navkit': int(sel.sum()), 'dice': int((bd == n).sum()), 'navkit_in_dice': int(has[sel].sum()),
                     'share': round(100.0 * has[sel].sum() / sel.sum(), 1) if sel.any() else None})
    extra = ~has
    wet = extra & ~np.isnan(dn) & (dn >= 0)
    dice_wet = dd[~np.isnan(dd) & (dd >= 0)]
    limit = float(np.percentile(dice_wet, 99.9)) if len(dice_wet) else None      # the deepest DICE goes
    deep = ~np.isnan(dn) & (dn > (limit if limit is not None else 0.0))
    return {'level': lv, 'bands': rows, 'navkit_cells': len(fn), 'dice_cells': len(fd), 'extra_cells': int(extra.sum()),
            'extra_under_water': int(wet.sum()), 'extra_under_water_pct': round(100.0 * wet.sum() / max(extra.sum(), 1), 1),
            'dice_under_water': int(len(dice_wet)), 'dice_depth_limit': None if limit is None else round(limit, 2),
            'navkit_deeper_than_dice': int(deep.sum()),
            'navkit_deeper_pct_of_extra': round(100.0 * deep.sum() / max(extra.sum(), 1), 1),
            'water_triangles': water_triangles}


def main(argv):
    args = argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    root = navparity.opfor_root(opt('--opfor'))
    pattern = opt('--candidate', os.path.join(root, 'work', 'navbuild', '{lv}_W.hpd'))
    skip = {opt('--candidate'), opt('--json'), opt('--opfor')}
    levels = [a for a in args if not a.startswith('--') and a not in skip] or [lv for lv in navparity.LEVELS if lv not in ('sp_006', 'sp_009')]
    reports = []
    totals = defaultdict(lambda: [0, 0, 0])
    for lv in levels:
        r = level_report(root, lv, pattern.replace('{lv}', lv))
        if r is None:
            continue
        reports.append(r)
        lim = f'{r["dice_depth_limit"]:.2f} m' if r['dice_depth_limit'] is not None else 'n/a (no DICE ground under water)'
        print(f'\n{lv}: NavKit {r["navkit_cells"]:,} floors, DICE {r["dice_cells"]:,}; DICE under water {r["dice_under_water"]:,}, '
              f'99.9 % of it within {lim}; NavKit deeper than that: {r["navkit_deeper_than_dice"]:,} '
              f'({r["navkit_deeper_pct_of_extra"]} % of NavKit\'s extra ground {r["extra_cells"]:,})')
        for b in r['bands']:
            t = totals[b['band']]
            t[0] += b['navkit']; t[1] += b['navkit_in_dice']; t[2] += b['dice']
            if b['navkit'] or b['dice']:
                share = f'{b["share"]:5.1f} %' if b['share'] is not None else '   -   '
                print(f'  {b["band"]:36s} NavKit {b["navkit"]:9,}  DICE has {share}   DICE cells {b["dice"]:9,}')
    print(f'\nall {len(reports)} levels:')
    for _, _, label in BANDS:
        n, both, d = totals[label]
        if n or d:
            print(f'  {label:36s} NavKit {n:9,}  DICE has {100.0 * both / n if n else 0:5.1f} %   DICE cells {d:9,}')
    ex = sum(r['extra_cells'] for r in reports)
    wet = sum(r['extra_under_water'] for r in reports)
    deep = sum(r['navkit_deeper_than_dice'] for r in reports)
    print(f'  NavKit extra ground under water: {wet:,} of {ex:,} floors ({100.0 * wet / max(ex, 1):.1f} %); '
          f'deeper than DICE goes on the level: {deep:,} ({100.0 * deep / max(ex, 1):.1f} %)')
    lims = [r['dice_depth_limit'] for r in reports if r['dice_depth_limit'] is not None]
    if lims:
        print(f'  DICE\'s depth limit per level (99.9 % of its ground under water is within): '
              f'{min(lims):.2f} - {max(lims):.2f} m, median {float(np.median(lims)):.2f} m')
    if opt('--json'):
        json.dump(reports, open(opt('--json'), 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('navwater', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
