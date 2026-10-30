r"""NavKit's water rule (navbuild.water_tops / deep_water) on a sample grid built here (no game data): ground more
than the limit (MOH: the soldier's WaterDepthLimit, 1.4 m) under the water is left out, shallower ground and ground outside the water are kept, stacked floors
are judged by their own height, overlapping water pieces take the higher surface, and no water changes nothing.

  python test_water_rule.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navbuild  # noqa: E402


def grid():
    """20 x 10 columns at 0.8 m from (0, 0). Columns with i < 10: ground at y 0. Columns with i >= 10: ground at
    y 2.0 (shallow), and in column j == 5 also a deck at y 5 (above the water)."""
    nx, nz, p = 20, 10, 0.8
    ys, start = [], [0]
    for j in range(nz):
        for i in range(nx):
            col = [0.0] if i < 10 else ([2.0, 5.0] if j == 5 else [2.0])
            ys += col
            start.append(start[-1] + len(col))
    return SimpleNamespace(nx=nx, nz=nz, pitch=p, min_x=0.0, min_z=0.0, start=np.array(start), y=np.array(ys, np.float32))


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    s = grid()
    # water at y 3.0 over x 0..12 (two triangles), the whole z range; columns beyond x = 12 are dry
    v = np.array([[-1, 3.0, -1], [12, 3.0, -1], [12, 3.0, 9], [-1, 3.0, 9]], np.float32)
    t = np.array([[0, 1, 2], [0, 2, 3]])
    top = navbuild.water_tops(s, v, t).reshape(s.nz, s.nx)
    xs = np.arange(s.nx) * s.pitch
    check(np.allclose(top[:, xs <= 12], 3.0) and np.isinf(top[:, xs > 12.01]).all(), 'water heights: 3.0 m under the water, none beyond it')
    deep = navbuild.deep_water(s, (v, t))
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
    i = col % s.nx
    x = i * s.pitch
    check(deep[(i < 10)].all(), 'ground at y 0 under water at 3.0 (3 m deep): left out')
    check(not deep[(i >= 10) & (s.y == 2.0) & (x <= 12)].any(), 'ground at y 2.0 (1 m deep): kept')
    check(not deep[s.y == 5.0].any(), 'a deck above the water: kept')
    check(not deep[x > 12.01].any(), 'ground outside the water: kept')
    check(navbuild.deep_water(s, (v, t), limit=3.5).sum() == 0, 'with a 3.5 m limit nothing is too deep')
    # an overlapping, higher water piece over x 0..4: the higher surface counts there
    v2 = np.concatenate([v, np.array([[-1, 4.0, -1], [4, 4.0, -1], [4, 4.0, 9]], np.float32)])
    t2 = np.concatenate([t, np.array([[4, 5, 6]])])
    top2 = navbuild.water_tops(s, v2, t2).reshape(s.nz, s.nx)
    check(top2.max() == 4.0 and np.isclose(top2[0, 0], 4.0) and np.isclose(top2[0, 8], 3.0), 'overlapping water: the higher surface')
    check(not navbuild.deep_water(s, None).any(), 'no water: nothing left out')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
