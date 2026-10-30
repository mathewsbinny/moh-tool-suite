r"""NavKit's drop links (tools\navdrops.py) on samples built here (no game data): a ledge over larger ground gets links
down (at most DROP_PER_PAIR, DROP_SPACING apart), none up; no link from a ledge higher than DROP_MAX, across a wall,
onto a covered landing, or from a region smaller than DROP_MIN_AREA; the region labelling joins floors a link step
apart and keeps a ledge's top apart from the ground below.

  python test_navdrops.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import navdrops  # noqa: E402
import navsample  # noqa: E402

PITCH = 0.2
LINK_STEP = 0.3


def samples(columns, nx, nz):
    """columns: {(i, j): [y, ...]} (top first) -> navsample.Samples at PITCH, every surface flat and clear."""
    start, ys = [0], []
    for j in range(nz):
        for i in range(nx):
            ys += sorted(columns.get((i, j), []), reverse=True)
            start.append(len(ys))
    n = len(ys)
    normal = np.zeros((n, 3), np.float32)
    normal[:, 1] = 1.0
    return navsample.Samples(PITCH, 0.0, 0.0, nx, nz, 100.0, -100.0, 0.35, 0.3, 1.8, 0.05, np.array(start, np.int64),
                             np.array(ys, np.float32), normal, np.zeros(n, np.uint8))


def ledge(nx=40, nz=50, split=10, top=1.5, extra=None):
    """Ground at 0 m from column split on, a block's top at `top` before it (a ledge along x = split); extra:
    {(i, j): [y, ...]} added surfaces."""
    cols = {(i, j): [top if i < split else 0.0] for i in range(nx) for j in range(nz)}
    for k, v in (extra or {}).items():
        cols[k] = cols.get(k, []) + v
    return samples(cols, nx, nz)


def surface_xy(s, k):
    c = int(np.searchsorted(s.start, k, 'right') - 1)
    return c % s.nx, c // s.nx


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    # the regions: the ledge's top and the ground are two; a 0.2 m step joins two floors into one
    s = ledge()
    keep = np.ones(len(s.y), bool)
    lab = navdrops.components(s, keep, LINK_STEP)
    check(len(set(lab.tolist())) == 2, f'ledge and ground: two regions ({len(set(lab.tolist()))})')
    s2 = ledge(top=0.2)
    check(len(set(navdrops.components(s2, np.ones(len(s2.y), bool), LINK_STEP).tolist())) == 1,
          'a 0.2 m step: one region')

    # a 1.5 m ledge (2 m x 10 m) over larger ground: links down, from the ledge's edge to the ground beside it
    links = navdrops.find(s, keep, LINK_STEP, PITCH)
    up = [k for k, n in links]
    down = [n for k, n in links]
    check(1 <= len(links) <= navdrops.DROP_PER_PAIR, f'ledge: {len(links)} links (at most {navdrops.DROP_PER_PAIR})')
    check(all(abs(s.y[k] - 1.5) < 1e-6 for k in up) and all(abs(s.y[n]) < 1e-6 for n in down),
          'ledge: every link from the top down to the ground')
    xy = [surface_xy(s, k) for k in up]
    check(all(i == 9 for i, _ in xy), f'ledge: from the edge column {sorted(set(i for i, _ in xy))}')
    far = min((abs(a[1] - b[1]) * PITCH for a in xy for b in xy if a != b), default=99)
    check(far >= navdrops.DROP_SPACING - 1e-6, f'ledge: links {far:.1f} m apart (>= {navdrops.DROP_SPACING})')
    check(all(abs(surface_xy(s, n)[0] - surface_xy(s, k)[0]) * PITCH <= navdrops.DROP_REACH + 1e-6 for k, n in links),
          'ledge: the landing within DROP_REACH')

    # the ground is the larger region: no link up from it (a link is one way, from the smaller upper region)
    check(not any(s.y[k] < s.y[n] for k, n in links), 'no link up')

    # too high: a 4 m ledge gets none
    s3 = ledge(top=4.0)
    check(navdrops.find(s3, np.ones(len(s3.y), bool), LINK_STEP, PITCH) == [], f'{4.0} m ledge (> DROP_MAX): no link')

    # a wall along the ledge (a surface 1 m over the ledge's top in every ground column next to it): no link
    wall = {(i, j): [2.5] for i in (10, 11) for j in range(50)}
    s4 = ledge(extra=wall)
    k4 = np.ones(len(s4.y), bool)
    k4[np.isclose(s4.y, 2.5)] = False                  # the wall's top is not floor
    check(navdrops.find(s4, k4, LINK_STEP, PITCH) == [], 'wall at the ledge: no link')

    # a covered landing: a slab 1 m over the ground beside the ledge (under the soldier's height above the ledge top)
    roof = {(i, j): [1.0] for i in range(10, 21) for j in range(50)}
    s5 = ledge(extra=roof)
    k5 = np.ones(len(s5.y), bool)
    k5[np.isclose(s5.y, 1.0)] = False
    check(navdrops.find(s5, k5, LINK_STEP, PITCH) == [], 'covered landing: no link')

    # a small ledge (1 m x 1 m, under DROP_MIN_AREA) gets none
    s6 = samples({(i, j): [1.5 if (i < 5 and j < 5) else 0.0] for i in range(40) for j in range(40)}, 40, 40)
    check(navdrops.find(s6, np.ones(len(s6.y), bool), LINK_STEP, PITCH) == [], 'small ledge (1 m2): no link')

    print('all passed' if not failed else f'{failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
