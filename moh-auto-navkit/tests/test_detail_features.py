r"""NavKit's D lines and F cells (navdetail.py, navbuild.detail_pass) on synthetic areas built here (no game data):
a flat area has none; a raised cell joined round a corner gives D lines in facing pairs (owner on the right, its
heights) and no F cell; a cell stepped into and out of along x gives an F cell pair (-x then +x, -0.0 components,
DICE's start corners, heights from each side); diagonal F cells are one polygon with 45-degree bridges; a side with
a portal gets none; random areas: D sides pair up, F polygons are closed and cover exactly the F cells; the records
survive kymesh's writer; navbuild takes such sides out of the outline only in pairs (the boundary stays closed), and
its route lines (line_walkable) do not step across a D side.

  python test_detail_features.py
"""
import math
import os
import random
import sys
from collections import Counter
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import kymesh  # noqa: E402
import navbuild  # noqa: E402
import navdetail  # noqa: E402

P = 0.8
Y = navdetail.Y_OFFSET


def in_poly(x, z, pts):
    inside = False
    for k in range(len(pts)):
        (ax, _, az), (bx, _, bz) = pts[k], pts[(k + 1) % len(pts)]
        if (az > z) != (bz > z) and x < ax + (z - az) * (bx - ax) / (bz - az):
            inside = not inside
    return inside


def area2(pts):
    return abs(sum(a[0] * b[2] - b[0] * a[2] for a, b in zip(pts, pts[1:]))) / 2.0


def r2(p):
    return tuple(round(v, 3) for v in p)


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    # flat ground
    flat = {(i, j): 5.0 for i in range(4) for j in range(4)}
    b, d, f = navdetail.detail_features(flat, P, 0, 0, 0.6)
    check(not b and not d and not f, 'flat area: no broken side, no D line, no F cell')

    # a ledge: two cells 0.7 m up beside two low ones (joined elsewhere): one broken side per column -> a D pair
    cells = {(0, 0): 0.0, (1, 0): 0.0, (0, 1): 0.7, (1, 1): 0.7}
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6)
    d = [line for line in d if all(p[2] > 0.3 and p[2] < 0.5 for p in line)]    # the z-line between the rows
    check(len(d) == 2 and not f, f'a ledge: a pair of D lines along it, no F ({len(d)}, {len(f)})')
    keys = Counter(frozenset((r2((p[0], 0, p[2])) for p in line)) for line in d)
    check(all(v == 2 for v in keys.values()), 'D lines come in pairs on the same place (the run of two cells merged)')
    ok = True
    for line in d:
        (x0, y0, z0), (x1, y1, z1) = line[0], line[-1]
        dx, dz = x1 - x0, z1 - z0
        L = abs(dx) + abs(dz)
        mx, mz = (x0 + x1) / 2 + dz / L * P / 2, (z0 + z1) / 2 - dx / L * P / 2      # half a pitch to the right
        owner = (int(round(mx / P)), int(round(mz / P)))
        ok &= owner in cells and abs(y0 - (cells[owner] + Y)) < 0.36 and abs(y1 - (cells[owner] + Y)) < 0.36
    check(ok, 'each D line: its owner cells on the right, heights near the owner\'s ground + 0.95')
    hi = [line for line in d if min(p[1] for p in line) > 1.0][0]
    check([r2(p)[1] for p in hi] == [round(0.7 + Y, 3), round(0.7 + Y, 3)] and r2(hi[0])[0] == 1.2,
          'vertex heights: the mean of the owner-side cells touching it along the line; runs -x (owner on +z)')

    # a cell 0.7 m up, joined to the rest round the corner: its two broken sides make a corner cut - its own line is
    # the diagonal across it, the low cells' lines an L round its corner (DICE's 3-point lines)
    cells = {(0, 0): 0.0, (1, 0): 0.0, (0, 1): 0.0, (1, 1): 0.7}
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6)
    ls = [line for line in d if len(line) == 3]
    dg = [line for line in d if len(line) == 2]
    check(len(b) == 4 and len(ls) == 1 and len(dg) == 1 and not f,
          f'raised corner cell: an L of 3 points and one diagonal line ({len(ls)}, {len(dg)})')
    check(ls and (r2(ls[0][1])[0], r2(ls[0][1])[2]) == (0.4, 0.4) and all(abs(p[1] - Y) < 1e-6 for p in ls[0]),
          'the L turns at the raised cell corner, at the low cells height')
    if dg:
        (x0, y0, z0), (x1, y1, z1) = dg[0]
        mx, mz = (x0 + x1) / 2, (z0 + z1) / 2
        check(abs(abs(x1 - x0) - P) < 1e-9 and abs(abs(z1 - z0) - P) < 1e-9 and (round(mx, 3), round(mz, 3)) == (0.8, 0.8) and
              (z1 - z0) * (1.2 - mx) - (x1 - x0) * (1.2 - mz) > 0 and y0 > 1.0,
              'the diagonal crosses the raised cell through its centre, its far half on the right, at its height')
    navdetail.CUT_CORNERS = False
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6)
    navdetail.CUT_CORNERS = True
    check(len(d) == 2 and all(len(line) == 3 for line in d), 'CUT_CORNERS off: two L lines facing each other')

    # a cell stepped into and out of along x: x-F pair, no D on its x sides
    cells = {(i, j): 0.7 * i for i in range(3) for j in range(2)}
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6)
    check(len(f) == 2 and not d, f'stairs of 0.7 m along x: one F pair, no D line ({len(f)}, {len(d)})')
    check([c.direction for c in f] == [(-1.0, -0.0, -0.0), (1.0, -0.0, -0.0)] and
          math.copysign(1, f[0].direction[1]) < 0 and math.copysign(1, f[1].direction[2]) < 0,
          'F order -x then +x; zero components stored as -0.0 (DICE)')
    fm, fp = f
    check([r2((p[0], 0, p[2])) for p in fm.points] == [(0.4, 0, 0.4), (0.4, 0, -0.4), (1.2, 0, -0.4), (1.2, 0, 1.2), (0.4, 0, 1.2), (0.4, 0, 0.4)],
          '-x record: starts at the first cell\'s top-left corner going -z (that vertex kept), closed')
    check([r2((p[0], 0, p[2])) for p in fp.points] == [(0.4, 0, -0.4), (1.2, 0, -0.4), (1.2, 0, 1.2), (0.4, 0, 1.2), (0.4, 0, -0.4)],
          '+x record: starts at the first cell\'s bottom-left corner going +x, closed')
    check(all(abs(p[1] - (0.0 if p[0] < 1 else 0.7) - Y) < 1e-6 for p in fm.points) and
          all(abs(p[1] - (0.7 if p[0] < 1 else 1.4) - Y) < 1e-6 for p in fp.points),
          'corner heights: -x record from the cells on the -x side, +x record from the +x side')

    # the same along z
    cells = {(i, j): 0.7 * j for i in range(2) for j in range(3)}
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6)
    check([c.direction for c in f] == [(-0.0, -0.0, -1.0), (-0.0, -0.0, 1.0)] and not d, 'stairs along z: a -z / +z F pair')
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6, with_f=False)
    check(not f and len(d) == 4, f'with_f off (detailed zones): no F, the stepped sides are D lines ({len(d)})')

    # two F cells touching only at a corner: one polygon, two 45-degree segments, area of 3 cells (two halves added)
    fset = {(1, 0), (2, 1)}
    cells = {(i, j): 0.0 for i in range(4) for j in range(2)}
    recs = navdetail.f_cells(fset, 'x', cells, P, 0, 0)
    pts = recs[0].points
    diag = sum(1 for a, c in zip(pts, pts[1:]) if abs(a[0] - c[0]) > 1e-6 and abs(a[2] - c[2]) > 1e-6)
    check(len(recs) == 2 and diag == 2 and abs(area2(pts) - 3 * P * P) < 1e-6,
          f'diagonal F cells: one polygon per direction with two 45-degree bridge segments, area 3 cells ({diag}, {area2(pts) / P / P:.2f})')

    # a bridge's side cell (half in the area) 0.7 m up: its two legs break with its own height; a 0.3 m one does not
    cells = {(0, 0): 0.0, (1, 1): 0.0}
    half = {((1, 0), (-1, 0)): 0.7, ((1, 0), (0, 1)): 0.7}
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6, half=half)
    check(len(b) == 4 and len(d) >= 2 and not f, f'half cell 0.7 m up: both legs broken on both sides ({len(b)}, {len(d)})')
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6, half={k: 0.3 for k in half})
    check(not b and not d, 'half cell 0.3 m up: no D')
    # breakable: detailed zones break only between terrain cells
    cells = {(0, 0): 0.0, (0, 1): 0.7}
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6, breakable=lambda a, c: False)
    check(not b and not d, 'a side breakable() refuses (an object cell in a detailed zone) holds no D')

    # a side holding a portal is no D side
    cells = {(0, 0): 0.0, (1, 0): 0.0, (0, 1): 0.0, (1, 1): 0.7}
    b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6, eligible=lambda i, j, di, dj: dj == 0)
    check(len(d) == 2 and all(len(line) == 2 for line in d), f'a side excluded (portal): its pair is gone, the other pair stays ({len(d)})')

    # random areas
    rnd = random.Random(7)
    bad_pair = bad_cover = bad_closed = 0
    n_f = n_d = 0
    navdetail.CUT_CORNERS = False                               # facing twins: without the corner cuts
    for _ in range(300):
        cells = {(i, j): rnd.choice((0.0, 0.3, 0.7, 1.4)) for i in range(6) for j in range(6) if rnd.random() < 0.85}
        b, d, f = navdetail.detail_features(cells, P, 0, 0, 0.6)
        fx, fz = navdetail.f_cell_sets(b)
        n_f += len(f)
        n_d += len(d)
        units = Counter()
        for line in d:
            (x0, _, z0), (x1, _, z1) = line[0], line[-1]
            n = int(round((abs(x1 - x0) + abs(z1 - z0)) / P))
            for k in range(n):
                a = (round(x0 + (x1 - x0) * k / n, 3), round(z0 + (z1 - z0) * k / n, 3))
                c = (round(x0 + (x1 - x0) * (k + 1) / n, 3), round(z0 + (z1 - z0) * (k + 1) / n, 3))
                units[(a, c)] += 1
        bad_pair += sum(1 for (a, c) in units if units[(c, a)] != units[(a, c)])
        for rec in f:
            bad_closed += rec.points[0] != rec.points[-1]
        for axis, fs in (('x', fx), ('z', fz)):
            recs = [r for r in f if (abs(r.direction[0]) > 0.5) == (axis == 'x')]
            half = recs[:len(recs) // 2]                        # the -axis half covers every group once
            # cells wholly inside an odd number of loops (holes are loops of their own; a bridge covers half of a side
            # cell, which does not count); bridges add half cells, so the
            # union's area is a quarter of a cell per quarter triangle
            covered = {c for c in cells if all(sum(in_poly(c[0] * P + dx, c[1] * P + dz, r.points) for r in half) % 2
                                               for dx, dz in ((0.2, 0.01), (-0.2, 0.01), (0.01, 0.2), (0.01, -0.2)))}
            want = sum(len(navdetail._quarters(g)) for g in navdetail._components(fs)) / 4.0 * P * P
            signed = sum(sum(a[0] * b[2] - b[0] * a[2] for a, b in zip(r.points, r.points[1:])) / 2.0 for r in half)
            bad_cover += covered != fs or abs(signed - want) > 1e-6
    navdetail.CUT_CORNERS = True
    check(n_f > 0 and n_d > 0, f'random areas: {n_d} D lines, {n_f} F records')
    check(bad_pair == 0, f'random areas: every D side has its facing twin ({bad_pair} without)')
    check(bad_closed == 0 and bad_cover == 0, f'random areas: F polygons closed and cover exactly the F cells ({bad_closed}, {bad_cover})')

    # kymesh writes them and reads them back
    cells = {(i, j): 0.7 * i for i in range(3) for j in range(2)}
    cells[(0, 2)] = 0.0
    cells[(1, 2)] = 0.9
    b, d, f = navdetail.detail_features(cells, P, 100.0, 200.0, 0.6)
    area = kymesh.Area(0x101, 0, 0, 0, 0.4, P, 2.0, 0.0, [], [], d, [[(100.0, 1.0, 200.0), (100.8, 1.0, 200.0)]], f)
    mesh = kymesh.Mesh(2, 0, 0, 0x101, 0.025, P, (0, 100, 0, 100), 1, 1, 0.4, [[kymesh.TileItem(0, 0.0, 2.0)]], [area], [], b'\0' * 4)
    data = kymesh.write_mesh(mesh)
    back = kymesh.parse_mesh(data).areas[0]
    check(back.d_lines == [[tuple(map(lambda v: struct_f(v), p)) for p in line] for line in d] and
          [c.direction for c in back.cells] == [c.direction for c in f] and len(back.cells) == len(f) and
          kymesh.write_mesh(kymesh.parse_mesh(data)) == data, 'kymesh: D lines and F cells written, read back, rewritten byte for byte')

    # navbuild.detail_pass: broken sides leave the outline in pairs only
    navbuild.set_pitch(0.8)
    ys = [0.0, 0.0, 0.0, 0.7]                                 # k: (0,0) (1,0) (0,1) (1,1)
    level = SimpleNamespace(y=ys)
    area = {'cells': [(0, 0, 0), (1, 0, 1), (0, 1, 2), (1, 1, 3)]}
    E = lambda axis, line, d_: ('E', None, axis, line, d_)
    per_area = [{E('x', 0.5, 1): [(1, 0.95)], E('x', 0.5, -1): [(1, 1.65)],       # (0,1)|(1,1): outline on both cells
                 E('z', 0.5, 1): [(1, 0.95)],                                     # (1,0)|(1,1): on one cell only
                 E('x', -0.5, -1): [(0, 0.95), (1, 0.95)]}]                       # the area's own west edge
    navbuild.DETAIL_STATS.clear()
    out = navbuild.detail_pass(level, [area], per_area, 0.0, 0.0)
    e = per_area[0]
    check(e[E('x', 0.5, 1)] == [] and e[E('x', 0.5, -1)] == [] and e[E('z', 0.5, 1)] == [(1, 0.95)] and
          e[E('x', -0.5, -1)] == [(0, 0.95), (1, 0.95)], 'detail_pass: a pair of outline sides moved to D, a one-sided one kept, the rest untouched')
    check(len(out[0][0]) == 2 and navbuild.DETAIL_STATS.get('outline_one_sided') == 1, 'detail_pass: the D lines of the area (two L-shaped), the one-sided side counted')
    # route edges and D sides (DETAIL_BLOCKS_EDGES): a flat 3 x 3 grid, surface k = j * 3 + i
    class Grid:
        y = [0.0] * 9
        blocked = set()

        def neighbour(self, k, gi, gj, di, dj, prefer=None, rule=None):
            i, j = gi + di, gj + dj
            return j * 3 + i if 0 <= i < 3 and 0 <= j < 3 else None
    g = Grid()
    free = navbuild.line_walkable(g, 0, (0, 0), (2, 1), 0, 0, 5)
    g.blocked = {(1, 4), (4, 1)}                      # D side between (1, 0) and (1, 1): on the line
    on_line = navbuild.line_walkable(g, 0, (0, 0), (2, 1), 0, 0, 5)
    g.blocked = {(2, 5), (5, 2)}                      # between (2, 0) and (2, 1): the line passes (1, 1), not (2, 0)
    off_line = navbuild.line_walkable(g, 0, (0, 0), (2, 1), 0, 0, 5)
    g.blocked = {(0, 1), (1, 0)}                      # a straight step across a D side
    straight = navbuild.line_walkable(g, 0, (0, 0), (2, 0), 0, 0, 2)
    check(free and not on_line and off_line and not straight,
          'line_walkable: no step across a D side - straight, or diagonal through the side cell the line passes; a D side '
          f'beside the line does not block ({free}, {on_line}, {off_line}, {straight})')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


def struct_f(v):
    import struct
    return struct.unpack('<f', struct.pack('<f', v))[0]


if __name__ == '__main__':
    sys.exit(main(sys.argv))
