r"""NavKit's rule that soldiers standing in the level while it is sampled are not in the path data
(navwalk.CHARACTERS_ABSENT, near_characters) on a sample grid built here (no game data): a soldier's hit is not
floor, the floor under it and in the next cells is not blocked by it, cells further away and floors far below keep
their own flags, and with the rule off the soldier blocks as before.

  python test_characters.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navwalk  # noqa: E402


def grid():
    """5 x 1 columns of terrain at y 0 (flags 1 = capsule blocked in columns 1..3), a soldier hit at y 1.5 in column 2,
    and in column 2 also a floor 5 m below (a basement, blocked by something else)."""
    ys, flags, hit, start = [], [], [], [0]
    for i in range(5):
        col = [(0.0, 1 if 1 <= i <= 3 else 0, navwalk.TERRAIN_SHAPE_VTABLE)]
        if i == 2:
            col += [(1.5, 0, navwalk.CHARACTER_SHAPE_VTABLE), (-5.0, 1, 0x1483B38)]
        for y, f, h in col:
            ys.append(y), flags.append(f), hit.append((h, 0, 0))
        start.append(len(ys))
    n = len(ys)
    return SimpleNamespace(nx=5, nz=1, pitch=0.8, min_x=0.0, min_z=0.0, start=np.array(start), y=np.array(ys, np.float32),
                           flags=np.array(flags), hit=np.array(hit, np.int64),
                           normal=np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1)))


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    s = grid()
    char = s.hit[:, 0] == navwalk.CHARACTER_SHAPE_VTABLE
    near = navwalk.near_characters(s, char)
    # surface order: col0 floor (0), col1 floor (1), col2 floor (2), soldier (3), basement (4), col3 floor (5), col4 floor (6)
    check(bool(near[2] and near[1] and near[5]), 'floor under the soldier and in the next cells counts as beside him')
    check(not near[0] and not near[6], 'cells two away do not')
    check(not near[4], 'a floor 5 m below is not blocked by him')
    keep = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=[(1.6, 0.0, 0.0)])
    check(bool(keep[1] and keep[2] and keep[5]), 'with the rule, the floor around the soldier is walkable')
    check(not keep[3], "the soldier's own surface is not floor")
    check(not keep[4], 'the basement keeps its own blocked flag')
    old = navwalk.CHARACTERS_ABSENT
    try:
        navwalk.CHARACTERS_ABSENT = False
        keep_off = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=[(1.6, 0.0, 0.0)])
    finally:
        navwalk.CHARACTERS_ABSENT = old
    check(not keep_off[2] and not keep_off[1], 'rule off: the soldier blocks the floor as before')
    # a soldier no ray hit: only his spawn point is known (column 3 floor, blocked)
    s2 = grid()
    s2.hit[3, 0] = 0x1483B38                                   # the hit is not a character now
    band = navwalk.HEADROOM_BAND                               # the object 1.5 m over the floor is in the headroom band
    navwalk.HEADROOM_BAND = None                               # (test_headroom.py); here only the spawn point is tested
    try:
        keep_pt = navwalk.walkable(s2, step=0.28, stairs=False, extra_seeds=[(1.6, 0.0, 0.0)], soldier_points=[(2.4, 0.0, 0.0)])
        keep_np = navwalk.walkable(s2, step=0.28, stairs=False, extra_seeds=[(1.6, 0.0, 0.0)])
    finally:
        navwalk.HEADROOM_BAND = band
    check(bool(keep_pt[5]) and not keep_np[5], 'a spawn point clears the floor at it (no character hit needed)')
    near = navwalk.near_points(s2, [(2.4, 0.0, 0.0)])
    check(bool(near[5] and near[2]) and not near[4] and not near[0], 'spawn point: the next cells at its height only')
    check(navwalk.CHARACTERS_ABSENT, 'default: soldiers absent')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
