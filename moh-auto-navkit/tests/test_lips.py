r"""NavKit's narrow lips and door sills (navwalk.LIP_WIDTH, LIP_OBJECT_RISE, LIP_OBJECT_FLAT) on sample rows built here
(no game data): at pitch 0.2 a band of up to two terrain samples steeper than 40 deg between walkable ground is walkable,
a band of three is not; a 5 cm object sill steeper than 55 deg between flat floors is walkable, between sloped floors
or 0.5 m high it is not; at pitch 0.8 nothing changes; the rules off keep the row cut.

  python test_lips.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navwalk  # noqa: E402

OBJECT = 0x1483B38


def normal(deg):
    a = math.radians(deg)
    return (math.sin(a), math.cos(a), 0.0)


def row(cols, pitch=0.2):
    """One row of columns, one surface each: cols = [(y, slope deg, terrain?)]. x starts at 10 (no sector-edge seed)."""
    n = len(cols)
    return SimpleNamespace(nx=n, nz=1, pitch=pitch, min_x=10.0, min_z=10.0, start=np.arange(n + 1),
                           y=np.array([c[0] for c in cols], np.float32), flags=np.zeros(n, int),
                           hit=np.array([(navwalk.TERRAIN_SHAPE_VTABLE if c[2] else OBJECT, 0, 0) for c in cols], np.int64),
                           normal=np.array([normal(c[1]) for c in cols], np.float32))


def reaches_end(s):
    keep = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=[(s.min_x, float(s.y[0]), s.min_z)])
    return bool(keep[-1]), keep


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    flat = (0.0, 0.0, True)
    steep = (0.0, 48.0, True)
    check(reaches_end(row([flat] * 4 + [steep] * 2 + [flat] * 4))[0], 'terrain: a two-sample band at 48 deg is crossed at 0.2 m')
    ok3, keep3 = reaches_end(row([flat] * 4 + [steep] * 3 + [flat] * 4))
    check(not ok3, 'terrain: a three-sample band is not crossed')
    check(not reaches_end(row([flat] * 4 + [(0.0, 60.0, True)] * 2 + [flat] * 4))[0], 'terrain: steeper than 55 deg is no lip')
    check(not reaches_end(row([flat] * 4 + [steep] * 2 + [flat] * 4, pitch=0.8))[0], 'pitch 0.8: no lips (the step rule decides there)')

    floor = (0.0, 0.0, False)
    sill = (0.05, 75.0, False)
    check(reaches_end(row([floor] * 4 + [sill] + [floor] * 4))[0], 'object: a 5 cm sill at 75 deg between flat floors is crossed')
    sloped = (0.0, 30.0, False)
    check(not reaches_end(row([sloped] * 4 + [sill] + [sloped] * 4))[0], 'object: the same sill between 30 deg floors is not')
    check(not reaches_end(row([floor] * 4 + [(0.5, 75.0, False)] + [floor] * 4))[0], 'object: a strip 0.5 m above the floor is not')
    mild = (0.05, 50.0, False)
    check(reaches_end(row([sloped] * 4 + [mild] + [sloped] * 4))[0], 'object: a 50 deg strip needs no flat floor (the old cap)')

    old = navwalk.LIP_WIDTH, navwalk.LIP_OBJECT_RISE
    try:
        navwalk.LIP_WIDTH = None
        check(not reaches_end(row([flat] * 4 + [steep] * 2 + [flat] * 4))[0], 'LIP_WIDTH off: the terrain band cuts the row')
        navwalk.LIP_WIDTH, navwalk.LIP_OBJECT_RISE = old[0], None
        check(not reaches_end(row([floor] * 4 + [sill] + [floor] * 4))[0], 'LIP_OBJECT_RISE off: the sill cuts the row')
    finally:
        navwalk.LIP_WIDTH, navwalk.LIP_OBJECT_RISE = old
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
