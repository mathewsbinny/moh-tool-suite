r"""navboundary.py's classification and measures on path data built here (no game data): a 4 x 2 cell block that
DICE splits into two areas, against NavKit versions that merge it (missing boundaries, merged areas) and that
split it one cell too early plus at the right place (extra and matching boundaries, precision / recall with and
without tolerance); portal displacement; the CSV / JSON / map export.

  python test_navboundary.py
"""
import csv
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import navboundary  # noqa: E402
from mohcore import navdata  # noqa: E402

G = navdata.GRID


def nav(columns_area, portals=()):
    """Cells (i, j) for i in 0..3, j in 0..1; area of each cell by its column. portals: (area, x, z) midpoints."""
    n_areas = max(columns_area) + 1
    areas = []
    for a in range(n_areas):
        ps = [(np.array([x, 0.0, z - 0.4]), np.array([x, 0.0, z + 0.4]), 0) for aa, x, z in portals if aa == a]
        areas.append(SimpleNamespace(detailed=False, portals=ps, sector=0, index=a))
    xz, area = [], []
    for i in range(4):
        for j in range(2):
            xz.append((i * G, j * G))
            area.append(columns_area[i])
    return SimpleNamespace(areas=areas, cell_xz=np.array(xz), cell_area=np.array(area, np.int32))


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    dice = nav([0, 0, 1, 1], portals=[(0, 1.2, 0.4)])
    s, cells, edges, portals, only = navboundary.diagnose(dice, nav([0, 0, 0, 0]))
    check((s['match'], s['missing'], s['extra']) == (0, 2, 0), f'merged: 2 missing boundary edges ({s["missing"]})')
    check(s['dice_areas_merged_by_navkit'] == 2 and s['exact_areas'] == 0, 'merged: both DICE areas marked merged, none exact')
    check(s['boundary_recall_0'] == 0.0 and s['boundary_precision_0'] is None, 'merged: recall 0, no NavKit boundary')
    check(s['shared_cells'] == 8 and s['dice_only_cells'] == 0 and s['navkit_only_cells'] == 0, 'all 8 cells shared')

    s, cells, edges, portals, only = navboundary.diagnose(dice, nav([0, 1, 2, 2], portals=[(1, 1.2, 0.4), (0, 4.0, 4.0)]))
    check((s['match'], s['missing'], s['extra']) == (2, 0, 2), f'split early + right: 2 match, 2 extra ({s["match"]}, {s["extra"]})')
    check(s['boundary_precision_0'] == 50.0 and s['boundary_recall_0'] == 100.0, 'precision 50 / recall 100 exactly')
    check(s['boundary_precision_1'] == 100.0, 'within one cell the early boundary counts as matching')
    check(s['dice_areas_split_by_navkit'] == 1 and s['dice_areas_merged_by_navkit'] == 0, 'DICE area 0 split, none merged')
    check(s['portals_dice'] == 1 and s['portals_navkit'] == 2, 'portal counts')
    check(s['portal_displacement_dice_to_navkit_median'] == 0.0 and s['dice_portals_within_1m'] == 100.0,
          'DICE portal has a NavKit portal at the same place')
    check(abs(s['portal_displacement_navkit_to_dice_median'] - np.median([0.0, np.hypot(2.8, 3.6)])) < 0.01,
          'NavKit portal displacement median over both portals')

    s, cells, edges, portals, only = navboundary.diagnose(dice, nav([0, 0, 1, 1]))
    check(s['exact_areas'] == 2 and s['exact_areas_pct'] == 100.0 and s['missing'] == 0 and s['extra'] == 0, 'identical: all exact')

    out = tempfile.mkdtemp()
    s, cells, edges, portals, only = navboundary.diagnose(dice, nav([0, 1, 2, 2], portals=[(1, 1.2, 0.4)]))
    navboundary.export(out, cells, edges, portals)
    rows = list(csv.DictReader(open(os.path.join(out, 'edges.csv'))))
    check(sorted(r['kind'] for r in rows) == ['extra', 'extra', 'match', 'match'], 'edges.csv lists the non-interior edges')
    check(len(list(csv.DictReader(open(os.path.join(out, 'cells.csv'))))) == 8, 'cells.csv has every shared cell')
    navboundary.render(os.path.join(out, 'map.png'), cells, edges, portals, only, (-1, -1, 4, 2), 'test')
    check(os.path.getsize(os.path.join(out, 'map.png')) > 1000, 'map rendered')
    check(navboundary.worst_square(edges) == (0.0, 0.0, 100.0, 100.0), 'worst 100 m square found')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
