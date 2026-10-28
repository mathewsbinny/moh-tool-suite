"""NavKit: which sectors to build as detailed zones (pitch 0.2), from the level alone - no DICE path data.

A sector is detailed when
  - a placed object whose blueprint sets NeedsDetailedPathfinding has its collision box within MARGIN m of the
    sector's square (the level's own trigger - the field DICE's generator reads), or
  - at least STACKED_MIN columns of NavKit's 0.8 m samples in the sector hold two standable surfaces at least 2 m apart
    (stacked floors: what the 0.8 m grid cannot separate well).
Scored against DICE's choice on the 14 levels that have both (2026-10-21): it finds 445 of DICE's 481 detailed
sectors (93 %; the flag alone 84 %) and adds 243 of about 2,500 that DICE builds at 0.8 m. The misses have neither a
flagged object nor stacked floors and stay at 0.8 m; the additions cost sampling and build time, not quality.

Usage: detailed.py <level> [--samples <0.8 m samples.bin>] [--offsets X,Z] [--score]   (--score: against DICE's file)
"""
import json
import math
import os
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import levelobjects  # noqa: E402
import navsample  # noqa: E402

MARGIN = 5.0           # m around a flagged object's collision box
STACKED_MIN = 100      # stacked-floor columns (0.8 m samples) that make a sector detailed
STACK_GAP = 2.0        # m between the lowest and highest standable surface of a column
SLOPE_MAX = 40.0


def flagged_sectors(level_dir, common, xoff, zoff, margin=MARGIN):
    """Sectors (X, Z) within margin of a NeedsDetailedPathfinding object's collision box (objects.json)."""
    bases = [b for b in [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + list(common) if os.path.isdir(b)]
    hv = levelobjects.havok_index(bases)
    out, cache = set(), {}
    for o in json.load(open(os.path.join(level_dir, 'objects.json'))):
        if not (o.get('flags') and o['flags'][0]):
            continue
        bp = o['bp'].lower()
        if bp not in cache:
            cache[bp] = levelobjects.triangles(hv[bp]) if bp in hv else None
        t = np.array(o['m'][12:15])
        if cache[bp] is None:
            x0, z0, x1, z1 = t[0], t[2], t[0], t[2]
        else:
            V = cache[bp].reshape(-1, 3) @ np.array([o['m'][0:3], o['m'][4:7], o['m'][8:11]]) + t
            x0, z0, x1, z1 = V[:, 0].min(), V[:, 2].min(), V[:, 0].max(), V[:, 2].max()
        for X in range(int(math.floor((x0 - margin) / 100)) - xoff, int(math.floor((x1 + margin) / 100)) - xoff + 1):
            for Z in range(int(math.floor((z0 - margin) / 100)) - zoff, int(math.floor((z1 + margin) / 100)) - zoff + 1):
                out.add((X, Z))
    return out


def stacked_columns(samples_path, xoff, zoff):
    """{sector (X, Z): columns holding standable surfaces at least STACK_GAP apart} from 0.8 m samples."""
    s, _ = navsample.load(samples_path)
    ok = ((s.flags & 3) == 0) & (s.normal[:, 1] >= math.cos(math.radians(SLOPE_MAX)) - 0.01)
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
    y = np.where(ok, s.y.astype(float), np.nan)
    lo = np.full(s.nx * s.nz, np.inf)
    hi = np.full(s.nx * s.nz, -np.inf)
    sel = ok
    np.minimum.at(lo, col[sel], y[sel])
    np.maximum.at(hi, col[sel], y[sel])
    stacked = np.nonzero(hi - lo >= STACK_GAP)[0]
    x = s.min_x + (stacked % s.nx) * s.pitch
    z = s.min_z + (stacked // s.nx) * s.pitch
    return Counter(zip((np.floor(x / 100).astype(int) - xoff).tolist(), (np.floor(z / 100).astype(int) - zoff).tolist()))


def choose(level_dir, common, samples_path, xoff, zoff, margin=MARGIN, stacked_min=STACKED_MIN):
    """(sorted [(X, Z)], {'flag': n, 'stacked': n}) - the detailed sectors of a level."""
    flag = flagged_sectors(level_dir, common, xoff, zoff, margin)
    stacked = {k for k, n in stacked_columns(samples_path, xoff, zoff).items() if n >= stacked_min} if samples_path else set()
    return sorted(flag | stacked), {'flag': len(flag), 'stacked': len(stacked), 'stacked only': len(stacked - flag)}


def score(chosen, dice_pathdata):
    """Against DICE's choice (evaluation only): (found, DICE detailed, chosen sectors DICE builds at 0.8)."""
    import hpdtool
    import kymesh
    det, norm = set(), set()
    for s in hpdtool.parse(open(dice_pathdata, 'rb').read()).sectors:
        (det if abs(kymesh.parse_mesh(s.mesh).pitch - 0.2) < 1e-3 else norm).add((s.id >> 8, s.id & 0xff))
    c = set(chosen)
    return len(det & c), len(det), len(norm & c)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    lv = argv[1].lower()
    opts = dict(zip(argv[2:], argv[3:]))
    root = os.environ.get('OPFOR_ROOT') or r'C:\Games\Project OPFOR'
    L = os.path.join(root, 'work', 'levels', lv)
    common = [os.path.join(root, 'work', 'navkit', 'common', c) for c in ('mp_common', 'sp_common')]
    xoff, zoff = (int(v) for v in opts['--offsets'].split(','))
    chosen, why = choose(L, common, opts.get('--samples'), xoff, zoff)
    print(f'{lv}: {len(chosen)} detailed sectors ({why})')
    if '--score' in argv:
        p = os.path.join(root, 'game', 'Output', 'win32', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')
        if os.path.isfile(p + '.opfor-orig'):
            p += '.opfor-orig'
        f, d, x = score(chosen, p)
        print(f'  against DICE: {f} of its {d} detailed sectors, {x} more')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
