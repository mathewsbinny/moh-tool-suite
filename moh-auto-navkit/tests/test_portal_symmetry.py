r"""NavKit's portal symmetry (navbuild.trace, SYMMETRIC_PORTALS / ONE_WAY_PORTALS) on sample grids built here (no game
data): two areas side by side get a portal each way and no edge carries two portal pieces; in a stacked column (a
surface of area A under one of area T, T going on next door) A's would-be one-way portal into T becomes outline
('drop'), or stays one-way ('keep').

  python test_portal_symmetry.py
"""
import os
import sys
from collections import Counter
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navbuild  # noqa: E402


def level(columns, nx, nz):
    """columns: per column (j * nx + i) a list of surface heights. Flat ground (normal up), terrain."""
    ys, start = [], [0]
    for c in range(nx * nz):
        ys += columns.get(c, [])
        start.append(len(ys))
    n = len(ys)
    normal = np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1))
    s = SimpleNamespace(nx=nx, nz=nz, pitch=0.8, min_x=0.0, min_z=0.0, start=np.array(start), y=np.array(ys, np.float32),
                        normal=normal, hit=None)
    return navbuild.Level(s, np.ones(n, bool), 0.28)


def unit_portals(result):
    """Per area: Counter of (target, unit edge midpoint) for every 0.8 m piece of its portals."""
    out = []
    for portals, _borders, _outline in result:
        c = Counter()
        for p in portals:
            (x0, _, z0), (x1, _, z1) = p.p0, p.p1
            n = max(1, int(round(max(abs(x1 - x0), abs(z1 - z0)) / 0.8)))
            for q in range(n):
                f = (q + 0.5) / n
                c[(p.target, round(x0 + (x1 - x0) * f, 2), round(z0 + (z1 - z0) * f, 2))] += 1
        out.append(c)
    return out


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    # two areas side by side on flat ground: columns i 0..3, j 0..1; A = i 0..1, B = i 2..3
    lv = level({j * 4 + i: [0.0] for i in range(4) for j in range(2)}, 4, 2)
    k = lambda i, j: j * 4 + i
    areas = [{'cells': [(i, j, k(i, j)) for i in (0, 1) for j in (0, 1)]}, {'cells': [(i, j, k(i, j)) for i in (2, 3) for j in (0, 1)]}]
    area_of = {c[2]: a for a, ar in enumerate(areas) for c in ar['cells']}
    res = navbuild.trace(lv, (0, 0), areas, area_of, 0, 0, 0.0, 0.0)
    up = unit_portals(res)
    to_b = {(x, z) for (t, x, z) in up[0] if t == 1}
    to_a = {(x, z) for (t, x, z) in up[1] if t == 0}
    check(len(to_b) == 2 and to_b == to_a, f'side by side: 2 portal pieces each way on the same edge ({len(to_b)}, {len(to_a)})')
    check(all(v == 1 for c in up for v in c.values()), 'no edge carries two portal pieces')

    # stacked: column 0 holds A's surface (y 0) and T's (y 0.2); column 1 holds T's (y 0.1)
    lv2 = level({0: [0.0, 0.2], 1: [0.1]}, 2, 1)
    areas2 = [{'cells': [(0, 0, 0)]}, {'cells': [(0, 0, 1), (1, 0, 2)]}]
    area_of2 = {0: 0, 1: 1, 2: 1}
    old = navbuild.ONE_WAY_PORTALS
    try:
        navbuild.ONE_WAY_PORTALS = 'keep'
        up_keep = unit_portals(navbuild.trace(lv2, (0, 0), areas2, area_of2, 0, 0, 0.0, 0.0))
        navbuild.ONE_WAY_PORTALS = 'drop'
        res_drop = navbuild.trace(lv2, (0, 0), areas2, area_of2, 0, 0, 0.0, 0.0)
        up_drop = unit_portals(res_drop)
    finally:
        navbuild.ONE_WAY_PORTALS = old
    check(any(t == 1 for t, _, _ in up_keep[0]) and not any(t == 0 for t, _, _ in up_keep[1]),
          "stacked, 'keep': A has a portal into T, T none back (the one-way case)")
    check(not up_drop[0] and not up_drop[1], "stacked, 'drop': no portal either way")
    outline_a = res_drop[0][2]
    check(any(abs(p0[0] - 0.4) < 1e-6 and abs(p1[0] - 0.4) < 1e-6 for p0, p1 in outline_a),
          "stacked, 'drop': A's side at x 0.4 is outline instead")
    # 'restore' (level-wide, build()): a one-way candidate stays only when its two areas are joined no other way
    def restored(lvx, areasx, area_ofx, shared=()):
        per_area, one_way = navbuild.trace_edges(lvx, areasx, area_ofx, 0, 0)
        keep = navbuild.restore_one_way({(0, 0): (per_area, one_way)}, shared)
        navbuild.drop_one_way(per_area, one_way, keep.get((0, 0), set()))
        return one_way, keep.get((0, 0), set()), unit_portals(navbuild.trace_emit(lvx, areasx, per_area, 0, 0, 0.0, 0.0))

    one_way, kept, up = restored(lv2, areas2, area_of2)
    check(len(one_way) == 1 and kept == {0} and any(t == 1 for t, _, _ in up[0]),
          'restore: the stacked pair joined no other way keeps its one-way portal (not severed as under drop)')
    # the same stacked column, but A and T also meet on flat ground next door: columns (0,1) A, (1,1) T at y 0
    lv3 = level({0: [0.0, 0.2], 1: [0.1], 2: [0.0], 3: [0.0]}, 2, 2)
    areas3 = [{'cells': [(0, 0, 0), (0, 1, 3)]}, {'cells': [(0, 0, 1), (1, 0, 2), (1, 1, 4)]}]
    area_of3 = {0: 0, 3: 0, 1: 1, 2: 1, 4: 1}
    one_way3, kept3, up3 = restored(lv3, areas3, area_of3)
    two_way = {(x, z) for t, x, z in up3[0] if t == 1} & {(x, z) for t, x, z in up3[1] if t == 0}
    check(len(one_way3) >= 1 and not kept3 and two_way,
          f'restore: a pair also joined by a two-way portal drops its one-way pieces ({len(one_way3)} candidates, kept {len(kept3)})')
    one_way_rest = sum(1 for t, x, z in up3[0] if t == 1 and (x, z) not in two_way) + \
        sum(1 for t, x, z in up3[1] if t == 0 and (x, z) not in two_way)
    check(one_way_rest == 0, 'restore: no one-way piece left where the areas are joined anyway')
    _, kept_sh, _ = restored(lv2, areas2, area_of2, shared=[(((0, 0), 0), ((0, 0), 1))])
    check(not kept_sh, 'restore: areas joined through a shared surface across sectors drop the one-way piece')
    # invariant: after the pass every candidate pair is joined (through kept pieces, portals or shared surfaces)
    per_area, ow = navbuild.trace_edges(lv2, areas2, area_of2, 0, 0)
    keep = navbuild.restore_one_way({(0, 0): (per_area, ow)}, ())
    check(all(n in keep[(0, 0)] for n in range(len(ow))), 'invariant: a pair with no other join keeps every candidate piece')
    check(navbuild.SYMMETRIC_PORTALS and navbuild.ONE_WAY_PORTALS == 'drop', 'defaults: symmetric portals, one-way pieces dropped (restore open)')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
