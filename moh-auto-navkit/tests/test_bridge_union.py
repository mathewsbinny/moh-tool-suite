r"""NavKit's 45-degree bridge outlines (navbuild.bridge_union) on random cell patterns built here (no game data): an
area's boundary - its cells' open sides, minus the sides its bridges cover, plus the bridges' diagonals - is closed
(every end point is met an even number of times) for every pattern, as in DICE's files; non-overlapping bridges give
exactly two diagonals each; the old clipping (clip_bridges) leaves open ends on some patterns (the bug this replaced).

  python test_bridge_union.py
"""
import os
import random
import sys
from collections import Counter
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navbuild  # noqa: E402


def bridges_of(cells):
    """trace_emit's bridges: diagonal-only neighbours (both side cells outside the area)."""
    out = []
    for i, j in sorted(cells):
        for di, dj in ((1, 1), (1, -1)):
            if (i + di, j + dj) in cells and (i + di, j) not in cells and (i, j + dj) not in cells:
                out.append((i, j, di, dj))
    return out


def open_ends(cells, drop, diag):
    """End points (half-sample units) of the boundary met an odd number of times."""
    deg = Counter()
    for i, j in cells:
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (i + di, j + dj) in cells:
                continue
            key = ('x', i + di * 0.5, j) if di else ('z', j + dj * 0.5, i)
            if key in drop:
                continue
            if di:
                a, b = (2 * i + di, 2 * j - 1), (2 * i + di, 2 * j + 1)
            else:
                a, b = (2 * i - 1, 2 * j + dj), (2 * i + 1, 2 * j + dj)
            deg[a] += 1
            deg[b] += 1
    for p, q in diag:
        deg[(round(p[0] * 2), round(p[1] * 2))] += 1
        deg[(round(q[0] * 2), round(q[1] * 2))] += 1
    return [k for k, v in deg.items() if v % 2]


def old_method(cells, bridges, level, cell_k):
    """trace_emit's previous code: four sides dropped per bridge, the two diagonals, clip_bridges."""
    drop, bsegs = set(), []
    for bi, (i, j, di, dj) in enumerate(bridges):
        drop.update({('x', i + di * 0.5, j), ('z', j + dj * 0.5, i), ('x', i + di * 0.5, j + dj), ('z', j + dj * 0.5, i + di)})
        cx, cz = i + di * 0.5, j + dj * 0.5
        for (ax, az), (bx, bz) in (((cx - di, cz), (cx, cz + dj)), ((cx, cz - dj), (cx + di, cz))):
            bsegs.append((bi, (ax, az, 0.0), (bx, bz, 0.0)))
    return drop, [((a[0], a[1]), (b[0], b[1])) for _bi, a, b in navbuild.clip_bridges(bsegs, bridges)]


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    rng = random.Random(1)
    bad_new = bad_old = with_bridges = overlapping = 0
    patterns = 600
    for _ in range(patterns):
        n = rng.choice((4, 6, 8))
        cells = {(i, j) for i in range(n) for j in range(n) if rng.random() < rng.choice((0.35, 0.5, 0.65))}
        bridges = bridges_of(cells)
        if not bridges:
            continue
        with_bridges += 1
        cell_k = {c: k for k, c in enumerate(sorted(cells))}
        level = SimpleNamespace(y=[0.0] * len(cell_k))
        drop, segs = navbuild.bridge_union(bridges, cell_k, level)
        diag = [((a[0], a[1]), (b[0], b[1])) for a, b in segs]
        if open_ends(cells, drop, diag):
            bad_new += 1
        corners = Counter()
        for i, j, di, dj in bridges:
            corners[(i + di, j)] += 1
            corners[(i, j + dj)] += 1
        overlapping += any(v > 1 for v in corners.values())
        odrop, odiag = old_method(cells, bridges, level, cell_k)
        if open_ends(cells, odrop, odiag):
            bad_old += 1
    check(with_bridges > 300, f'{with_bridges} of {patterns} random patterns have bridges ({overlapping} with overlapping ones)')
    check(bad_new == 0, f'bridge_union: every boundary closed ({bad_new} with open ends)')
    check(bad_old > 0, f'the old clipping leaves open ends on {bad_old} patterns (the case this replaced)')

    # one bridge alone: exactly its two diagonals, from A's corners to B's corners
    cells = {(0, 0), (1, 1)}
    cell_k = {(0, 0): 0, (1, 1): 1}
    drop, segs = navbuild.bridge_union(bridges_of(cells), cell_k, SimpleNamespace(y=[1.0, 2.0]))
    ends = sorted(((a[0], a[1], a[2]), (b[0], b[1], b[2])) for a, b in segs)
    check(ends == [((-0.5, 0.5, 1.0 + navbuild.Y_OFFSET), (0.5, 1.5, 2.0 + navbuild.Y_OFFSET)),
                   ((0.5, -0.5, 1.0 + navbuild.Y_OFFSET), (1.5, 0.5, 2.0 + navbuild.Y_OFFSET))],
          'a lone bridge: two diagonals, A end at A\'s height, B end at B\'s')
    check(drop == {('x', 0.5, 0), ('z', 0.5, 0), ('x', 0.5, 1), ('z', 0.5, 1)}, 'a lone bridge covers the four sides at its corner')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
