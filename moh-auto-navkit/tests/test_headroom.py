r"""NavKit's headroom band (navwalk.HEADROOM_BAND, headroom, low_headroom) on a sample grid built here (no game data):
ground with the next surface above it 0.5-2.0 m higher is not walkable; 0.4 m (a step) and 2.5 m (a ceiling) keep
their ground; a soldier overhead does not count; the deck itself (nothing above) stays; the rule off keeps it all.

  python test_headroom.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navwalk  # noqa: E402


def grid():
    """5 x 1 columns, floor at y 0 in each; above it: col 0 nothing, col 1 a surface at 0.4 (a step), col 2 a deck at
    1.8, col 3 a ceiling at 2.5, col 4 a soldier at 1.5."""
    over = {1: (0.4, 0x1483B38), 2: (1.8, 0x1483B38), 3: (2.5, 0x1483B38), 4: (1.5, navwalk.CHARACTER_SHAPE_VTABLE)}
    ys, hit, start = [], [], [0]
    for i in range(5):
        ys.append(0.0), hit.append((navwalk.TERRAIN_SHAPE_VTABLE, 0, 0))
        if i in over:
            ys.append(over[i][0]), hit.append((over[i][1], 0, 0))
        start.append(len(ys))
    n = len(ys)
    return SimpleNamespace(nx=5, nz=1, pitch=0.8, min_x=0.0, min_z=0.0, start=np.array(start), y=np.array(ys, np.float32),
                           flags=np.zeros(n, int), hit=np.array(hit, np.int64),
                           normal=np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1)))


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    s = grid()
    char = s.hit[:, 0] == navwalk.CHARACTER_SHAPE_VTABLE
    h = navwalk.headroom(s, char)
    # surfaces: col0 floor (0); col1 floor (1), step (2); col2 floor (3), deck (4); col3 floor (5), ceiling (6); col4 floor (7), soldier (8)
    check(np.isinf(h[0]) and abs(h[1] - 0.4) < 1e-6 and abs(h[3] - 1.8) < 1e-6 and abs(h[5] - 2.5) < 1e-6,
          'headroom: nothing / a step / a deck / a ceiling above the floor')
    check(np.isinf(h[7]), 'a soldier above does not count')
    low = navwalk.low_headroom(s, char, 0.5, 2.0)
    check(bool(low[3]) and not low[1] and not low[5] and not low[0] and not low[4], 'only the floor under the 1.8 m deck is in the band')
    keep = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=[(0.0, 0.0, 0.0), (1.6, 1.8, 0.0), (2.4, 0.0, 0.0)])     # col 3 is cut off from col 0 once col 2 goes
    check(not keep[3], 'the floor under the deck is not walkable')
    check(bool(keep[0] and keep[1] and keep[5]), 'open floor, the floor under a step-height surface and under a ceiling stay')
    old = navwalk.HEADROOM_BAND
    try:
        navwalk.HEADROOM_BAND = None
        keep_off = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=[(0.0, 0.0, 0.0), (1.6, 1.8, 0.0)])
    finally:
        navwalk.HEADROOM_BAND = old
    check(bool(keep_off[3]), 'rule off: the floor under the deck is kept as before')
    check(navwalk.HEADROOM_BAND == (0.5, 2.0), 'default band 0.5-2.0 m')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
