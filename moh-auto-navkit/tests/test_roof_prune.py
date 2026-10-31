r"""NavKit's roof pruning (navwalk.ROOF_PRUNE, roof_islands) on a sample grid built here (no game data): terrain at
y 0 everywhere, a roof (object, y 5) nothing reaches, an anchored deck (y 5.3) 1.6 m from a second roof, and a
ground-level patch cut off from the rest. The lone roof is dropped; the roof beside the anchored deck, a roof with
an anchor on it, the ground-level patch and the largest island stay; with the rule off everything stays. Each
column's lowest surface (what a roof's rise is measured from) with empty columns at the grid's end.

  python test_roof_prune.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navwalk  # noqa: E402

OBJECT = 0x1483B38
P = 0.8
X0 = 10.0
NX, NZ = 16, 4
LONE = {(i, j) for i in (2, 3) for j in (1, 2)}          # roof, y 5
NEAR = {(i, j) for i in (7, 8) for j in (1, 2)}          # roof, y 5 - 1.6 m from the deck
DECK = {(i, j) for i in (10, 11) for j in (1, 2)}        # deck, y 5.3 - anchored
GAP = {(13, j) for j in range(NZ)}                       # no surface: cuts off columns 14-15 (ground-level patch)


def grid():
    ys, hit, start = [], [], [0]
    for j in range(NZ):
        for i in range(NX):
            if (i, j) not in GAP:
                ys.append(0.0), hit.append(navwalk.TERRAIN_SHAPE_VTABLE)
            for cells, y in ((LONE, 5.0), (NEAR, 5.0), (DECK, 5.3)):
                if (i, j) in cells:
                    ys.append(y), hit.append(OBJECT)
            start.append(len(ys))
    n = len(ys)
    return SimpleNamespace(nx=NX, nz=NZ, pitch=P, min_x=X0, min_z=X0, start=np.array(start), y=np.array(ys, np.float32),
                           flags=np.zeros(n, int), hit=np.array([(h, 0, 0) for h in hit], np.int64),
                           normal=np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1)))


def pt(i, j, y):
    return (X0 + i * P, y, X0 + j * P)


def kept_at(s, keep, i, j, y):
    c = j * NX + i
    return any(keep[k] for k in range(s.start[c], s.start[c + 1]) if abs(s.y[k] - y) < 0.1)


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    s = grid()
    seeds = [pt(0, 0, 0.0), pt(2, 1, 5.0), pt(7, 1, 5.0), pt(10, 1, 5.3), pt(14, 0, 0.0)]
    anchors = [pt(0, 0, 0.0), pt(10, 1, 5.3)]
    keep = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=seeds, anchors=anchors)
    check(not kept_at(s, keep, 2, 1, 5.0), 'a roof nothing reaches is dropped')
    check(kept_at(s, keep, 7, 1, 5.0), 'a roof 1.6 m from an anchored deck at its height stays')
    check(kept_at(s, keep, 10, 1, 5.3), 'the anchored deck stays')
    check(kept_at(s, keep, 14, 0, 0.0), 'a ground-level patch nothing reaches stays (DICE keeps those)')
    check(kept_at(s, keep, 0, 0, 0.0) and kept_at(s, keep, 5, 3, 0.0), 'the main ground stays')
    keep2 = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=seeds, anchors=anchors + [pt(3, 2, 5.0)])
    check(kept_at(s, keep2, 2, 1, 5.0), 'an anchor (spawn / order / ladder end) on the roof keeps it')
    old = navwalk.ROOF_PRUNE
    try:
        navwalk.ROOF_PRUNE = False
        keep3 = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=seeds, anchors=anchors)
        check(kept_at(s, keep3, 2, 1, 5.0), 'ROOF_PRUNE off: the lone roof stays')
    finally:
        navwalk.ROOF_PRUNE = old
    # each column's lowest surface, with empty columns at the grid's end: the last column holding surfaces keeps its
    # lowest (reduceat over clipped starts gave its roof, 3.0, 2026-10-31)
    cols = [[0.0], [0.0], [0.0], [3.0, 0.0], [], []]
    t = SimpleNamespace(nx=3, nz=2, start=np.cumsum([0] + [len(c) for c in cols]),
                        y=np.array([v for c in cols for v in c], np.float32))
    low = navwalk.column_lowest(t)
    check(low.tolist() == [0.0, 0.0, 0.0, 0.0, 0.0], f'lowest surface per column, empty columns at the end: {low.tolist()}')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
