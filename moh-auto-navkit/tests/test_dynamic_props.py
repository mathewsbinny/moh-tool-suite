r"""NavKit's dynamic props (navwalk.DYNAMIC_FLOOR, DYNAMIC_ABSENT_HEADROOM and the grazing hits on a door leaf) on
sample rows built here (no game data). A closed door on the middle column: (a) its bottom rail rests on the floor -
the rays record the door's top, its leaf's face at 1 m and the rail, never the floor; the rail stands for the floor.
(b) it hangs over a static sill - the sill is ordinary floor, and the leaf's face 1 m above it is no ceiling. Both
rows are crossed; with the matching rule off the door cuts the row.

  python test_dynamic_props.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navwalk  # noqa: E402

OBJECT = 0x1483B38
PITCH = 0.2
X0 = 10.0


def box(x0, x1, y0, y1, z0, z1):
    """The 12 triangles of an axis-aligned box."""
    c = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], np.float32)
    f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4),
         (1, 5, 7), (1, 7, 3)]
    return c[np.array(f)]


def door_row(rail_on_door=True):
    """7 columns; floor at y 0; column 3 (x = X0 + 0.6) holds the door: top 2.2, a leaf face hit at 1.0 (steep),
    the bottom rail at 0.06. The floors beside the door have no room (the capsule touches the leaf)."""
    surf = {3: [(2.2, (0.0, 1.0, 0.0)), (1.0, (0.995, 0.1, 0.0)), (0.06, (0.0, 1.0, 0.0))]}
    ys, nrm, flags, start = [], [], [], [0]
    for i in range(7):
        for y, n in surf.get(i, [(0.0, (0.0, 1.0, 0.0))]):
            ys.append(y), nrm.append(n), flags.append(1 if i in (2, 4) or (i == 3 and y < 0.5) else 0)
        start.append(len(ys))
    n = len(ys)
    s = SimpleNamespace(nx=7, nz=1, pitch=PITCH, min_x=X0, min_z=X0, start=np.array(start), y=np.array(ys, np.float32),
                        flags=np.array(flags), hit=np.tile(np.array([OBJECT, 0, 0], np.int64), (n, 1)),
                        normal=np.array(nrm, np.float32))
    door = box(X0 + 0.58, X0 + 0.62, 0.05 if rail_on_door else 0.3, 2.2, X0 - 0.5, X0 + 0.5)
    return s, door


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    s, door = door_row()
    seed = [(X0, 0.0, X0)]
    keep = navwalk.walkable(s, step=0.28, stairs=False, dynamic_tris=door, extra_seeds=seed)
    last = len(s.y) - 1
    rail = s.start[3] + 2
    check(bool(keep[rail]), 'the bottom rail stands for the floor under the door')
    check(bool(keep[last]), 'the row is crossed through the closed door')
    check(not keep[s.start[3]] and not keep[s.start[3] + 1], 'the door top and the leaf face are not floor')
    s2, door2 = door_row(rail_on_door=False)
    keep2 = navwalk.walkable(s2, step=0.28, stairs=False, dynamic_tris=door2, extra_seeds=seed)
    check(bool(keep2[last]), 'a static sill under a hanging door: the leaf 1 m above is no ceiling, the row is crossed')
    old = navwalk.DYNAMIC_FLOOR, navwalk.DYNAMIC_ABSENT_HEADROOM
    try:
        navwalk.DYNAMIC_FLOOR = None
        check(not navwalk.walkable(s, step=0.28, stairs=False, dynamic_tris=door, extra_seeds=seed)[last],
              'DYNAMIC_FLOOR off: the rail is no floor, the door column is a hole, the row is cut')
        navwalk.DYNAMIC_FLOOR, navwalk.DYNAMIC_ABSENT_HEADROOM = old[0], False
        check(not navwalk.walkable(s2, step=0.28, stairs=False, dynamic_tris=door2, extra_seeds=seed)[last],
              'DYNAMIC_ABSENT_HEADROOM off: the leaf counts as a low ceiling over the sill, the row is cut')
    finally:
        navwalk.DYNAMIC_FLOOR, navwalk.DYNAMIC_ABSENT_HEADROOM = old
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
