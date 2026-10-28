"""NavKit: D lines and F cells of one area (KYNAPSE_SPEC.md 4.5, "D lines and F cells - the generator rule").

DICE's rule, measured on its files (sp_004_b, sp_01, ONS_MP_002; 2026-10-26):
  * a **broken side** is a side between two straight-neighbour cells of the same area whose ground heights differ by
    more than the link step (0.6 m at pitch 0.8; about 0.175 m at pitch 0.2) - the cells are in one area through
    other cells, but not linked across this side;
  * an **F cell** is a cell whose two sides along one axis are both broken (a cell stepped up into, and stepped up
    out of, along x or along z); the F cells of one axis form polygons (8-connected groups, 45-degree bridges as
    in the outline), each stored twice: direction -axis with every corner's height taken from the cells on the
    -axis side of the corner, and +axis with the +axis side's;
  * a **D line** is a broken side that is not an F cell's side along its axis, stored once per side (two lines
    facing each other), the owning cell on the right as in the outline, collinear runs merged, each vertex at the
    mean height of the owner-side cells touching it along the line.
These sides are not outline (E) in DICE's files: the area's outline goes round the area, D and F lie inside it.

detail_features(cells, pitch, ox, oz, step) -> (broken sides, D polylines, F cells). Pure: no level, no samples -
cells is the area's {(i, j): ground height} on its sector's sample grid (local indices, cell centre at
(ox + i * pitch, oz + j * pitch)); tests/test_detail_features.py runs it on synthetic areas.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kymesh  # noqa: E402

Y_OFFSET = 0.95                       # stored heights are ground + EntityHeight / 2 (as the outline)
SIDES = ((1, 0), (-1, 0), (0, 1), (0, -1))
CUT_CORNERS = True                    # a cell with D on exactly two perpendicular sides: its own line is the diagonal across
                                      # it (the corner cut), the neighbours' lines an L round the corner (DICE, sp_01: 409
                                      # of 453 such corners cut, 354 of them with the L in one line)
CHAIN_TURNS = True                    # D lines continue round a corner where the chain is unambiguous (DICE's 3-8 point
                                      # lines); False: straight runs only (the first version, 2026-10-26)
# F row directions as DICE stores them (zero components are -0.0)
F_DIRECTION = {('x', -1): (-1.0, -0.0, -0.0), ('x', 1): (1.0, -0.0, -0.0),
               ('z', -1): (-0.0, -0.0, -1.0), ('z', 1): (-0.0, -0.0, 1.0)}


def broken_sides(cells, step, eligible=None, half=None, breakable=None):
    """{((i, j), (di, dj))}: sides between two cells of the area whose heights differ by more than step.
    eligible(i, j, di, dj) -> False leaves a side out (a portal there, for example).
    half: {((i, j), (di, dj)): height} - the legs of 45-degree cut half cells: cell (i, j) is not in the area but half
    of it is (a bridge), its side toward the area cell (i + di, j + dj) lies inside the area; it breaks as any side,
    with the half cell's own height (DICE: 352 of 389 such legs with a step over 0.6 m have D or F, sp_004_b).
    breakable(a, b) -> False: the side between cells a and b never breaks (detailed zones: objects)."""
    out = set()
    for (i, j), y in cells.items():
        for di, dj in SIDES:
            n = cells.get((i + di, j + dj))
            if n is not None and abs(n - y) > step and (eligible is None or eligible(i, j, di, dj)) and                     (breakable is None or breakable((i, j), (i + di, j + dj))):
                out.add(((i, j), (di, dj)))
    for ((i, j), (di, dj)), y in (half or {}).items():
        n = cells.get((i + di, j + dj))
        if n is not None and abs(n - y) > step and (breakable is None or breakable((i, j), (i + di, j + dj))):
            out.add(((i, j), (di, dj)))
            out.add(((i + di, j + dj), (-di, -dj)))
    return out


def f_cell_sets(broken, cells=None):
    """(x-F cells, z-F cells): cells (of cells, when given - not half cells) whose two sides along x (z) are both
    broken."""
    fx = {c for c, s in broken if s == (1, 0) and (c, (-1, 0)) in broken and (cells is None or c in cells)}
    fz = {c for c, s in broken if s == (0, 1) and (c, (0, -1)) in broken and (cells is None or c in cells)}
    return fx, fz


def d_sides(broken, fx, fz):
    """Broken sides that are no F cell's side along its axis (on either side of the line)."""
    out = set()
    for (i, j), (di, dj) in broken:
        if di and ((i, j) in fx or (i + di, j) in fx):
            continue
        if dj and ((i, j) in fz or (i, j + dj) in fz):
            continue
        out.add(((i, j), (di, dj)))
    return out


def _unit(side, cells):
    """A D side as a directed unit segment in half units with its two vertex heights: (start, end, ys, ye); the owner
    cell on the right; each vertex at the mean height of the owner-side cells touching it along the line."""
    (i, j), (di, dj) = side
    if di:
        line, a, b = 2 * i + di, 2 * j - 1, 2 * j + 1
        pts = [(line, a), (line, b)]
        rev = di == 1                           # owner on -x: runs -z
    else:
        line, a, b = 2 * j + dj, 2 * i - 1, 2 * i + 1
        pts = [(a, line), (b, line)]
        rev = dj == -1                          # owner on +z: runs -x

    def y_at(t2):
        own = [(i, t) if di else (t, j) for t in ((t2 - 1) // 2, (t2 + 1) // 2)]
        hs = [cells[c] for c in own if c in cells]
        return sum(hs) / len(hs) + Y_OFFSET
    ys = [y_at(a), y_at(b)]
    if rev:
        pts, ys = pts[::-1], ys[::-1]
    return pts[0], pts[1], ys[0], ys[1]


def corner_cuts(sides):
    """CUT_CORNERS: {cell: (s1, s2)} for cells owning D on exactly two sides, perpendicular to each other."""
    own = defaultdict(list)
    for c, sd in sides:
        own[c].append(sd)
    out = {}
    for c, sds in own.items():
        if len(sds) == 2 and (sds[0][0] == 0) != (sds[1][0] == 0):
            out[c] = tuple(sorted(sds))
    return out


def _cut(c, sds, cells):
    """The diagonal D segment of a cut corner, owner (the cell) on the right: between the cell's two corners next to
    the cut one (half units), each at the mean height of the cell and its neighbour along that side's line."""
    (i, j) = c
    (sx, _), (_, sz) = sorted(sds, key=lambda t: t[0] == 0)            # the x side, then the z side
    a = (2 * i + sx, 2 * j - sz)                                        # far end of the x side
    b = (2 * i - sx, 2 * j + sz)                                        # far end of the z side

    def y(other):
        hs = [cells[c]] + ([cells[other]] if other in cells else [])
        return sum(hs) / len(hs) + Y_OFFSET
    ya, yb = y((i, j - sz)), y((i - sx, j))
    # the cell's remaining half is on the far side from the cut corner (2i + sx, 2j + sz): it must be on the right
    dx, dz = b[0] - a[0], b[1] - a[1]
    right = (dz, -dx)
    if right[0] * (-sx) + right[1] * (-sz) < 0:
        a, b, ya, yb = b, a, yb, ya
    return a, b, ya, yb


def d_lines(sides, cells, pitch, ox, oz, cuts=None):
    """D polylines (world points) from owned broken sides, owner on the right: unit segments chained end to start
    where the chain is unambiguous (one way on, not back along the twin) - straight runs merge, and so do turns
    (DICE, sp_01: straight 2,785 of 2,785 joints joined, right turns 44 of 44, left turns 387 of 447); collinear
    vertices dropped. Heights: per vertex the mean over the owner's cells touching it (DICE's convention)."""
    cuts = cuts or {}
    segs = sorted(_unit(s, cells) for s in sides if not (s[0] in cuts and s[1] in cuts[s[0]]))
    diag = [_cut(c, sds, cells) for c, sds in sorted(cuts.items())]
    starts = defaultdict(list)
    for k, sg in enumerate(segs):
        starts[sg[0]].append(k)
    nxt, prv_count = {}, defaultdict(int)
    cand = {}
    for k, (a, b, _ya, _yb) in enumerate(segs):
        c = [t for t in starts.get(b, ()) if segs[t][1] != a]        # not straight back along the facing twin
        if not CHAIN_TURNS:
            c = [t for t in c if (segs[t][1][0] - b[0], segs[t][1][1] - b[1]) == (b[0] - a[0], b[1] - a[1])]
        cand[k] = c
        for t in c:
            prv_count[t] += 1
    for k, c in cand.items():
        if len(c) == 1 and prv_count[c[0]] == 1:
            nxt[k] = c[0]
    has_prev = set(nxt.values())
    out, used = [], set()

    def emit(chain):
        pts = [(segs[chain[0]][0], segs[chain[0]][2])]
        for q, k in enumerate(chain):
            a, b, ya, yb = segs[k]
            if q:
                pts[-1] = (pts[-1][0], (pts[-1][1] + ya) / 2.0)          # a joint: both segments' heights
            pts.append((b, yb))
        keep = [pts[0]]
        for q in range(1, len(pts) - 1):
            (p0, _), (p1, y1), (p2, _) = keep[-1], pts[q], pts[q + 1]
            d0 = (p1[0] - p0[0], p1[1] - p0[1])
            d1 = (p2[0] - p1[0], p2[1] - p1[1])
            if d0[0] * d1[1] - d0[1] * d1[0] != 0:
                keep.append((p1, y1))
        keep.append(pts[-1])
        out.append([(ox + p[0] * pitch / 2.0, y, oz + p[1] * pitch / 2.0) for p, y in keep])
    for k in range(len(segs)):
        if k in used or k in has_prev:
            continue
        chain = [k]
        while chain[-1] in nxt and nxt[chain[-1]] not in used and nxt[chain[-1]] not in chain:
            chain.append(nxt[chain[-1]])
        used.update(chain)
        emit(chain)
    for k in range(len(segs)):                                           # closed loops (around one cell)
        if k in used:
            continue
        chain = [k]
        while nxt.get(chain[-1]) is not None and nxt[chain[-1]] not in used and nxt[chain[-1]] not in chain:
            chain.append(nxt[chain[-1]])
        used.update(chain)
        emit(chain)
    for a, b, ya, yb in diag:                                            # corner cuts: lines of their own
        out.append([(ox + a[0] * pitch / 2.0, ya, oz + a[1] * pitch / 2.0), (ox + b[0] * pitch / 2.0, yb, oz + b[1] * pitch / 2.0)])
    return out


def _components(cellset):
    """8-connected groups of cells, each sorted; groups in order of their first cell (x, then z - DICE's order)."""
    seen, out = set(), []
    for c in sorted(cellset):
        if c in seen:
            continue
        comp, stack = [], [c]
        seen.add(c)
        while stack:
            i, j = stack.pop()
            comp.append((i, j))
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    n = (i + di, j + dj)
                    if n in cellset and n not in seen:
                        seen.add(n)
                        stack.append(n)
        out.append(sorted(comp))
    return out


def _quarters(comp):
    """Quarter triangles (cell, base side) covering a group: its cells, plus for each 45-degree bridge (two cells
    touching only at a corner) the two quarters of each empty cell beside that corner whose bases touch it."""
    cs = set(comp)
    q = set()
    for i, j in comp:
        for s in SIDES:
            q.add(((i, j), s))
    for i, j in comp:
        for di, dj in ((1, 1), (1, -1)):
            if (i + di, j + dj) in cs and (i + di, j) not in cs and (i, j + dj) not in cs:
                q.update({((i, j + dj), (0, -dj)), ((i, j + dj), (di, 0)),
                          ((i + di, j), (-di, 0)), ((i + di, j), (0, dj))})
    return q


def _loops(quarters):
    """Boundary of a union of quarter triangles: closed loops of half-unit vertices, counter-clockwise (x right,
    z up; inside on the left)."""
    edges = defaultdict(int)
    for (i, j), (sx, sz) in quarters:
        c = (2 * i, 2 * j)
        if sx:
            b0, b1 = (2 * i + sx, 2 * j - 1), (2 * i + sx, 2 * j + 1)
        else:
            b0, b1 = (2 * i - 1, 2 * j + sz), (2 * i + 1, 2 * j + sz)
        if (b0[0] - c[0]) * (b1[1] - c[1]) - (b0[1] - c[1]) * (b1[0] - c[0]) < 0:
            b0, b1 = b1, b0
        for p, r in ((c, b0), (b0, b1), (b1, c)):
            if edges[(r, p)]:
                edges[(r, p)] -= 1             # the same edge the other way round: interior
            else:
                edges[(p, r)] += 1
    nxt = defaultdict(list)
    for (p, r), n in edges.items():
        for _ in range(n):
            nxt[p].append(r)
    loops = []
    while any(nxt.values()):
        p0 = min(p for p, v in nxt.items() if v)
        loop, p, prev = [p0], p0, None
        while True:
            cand = nxt[p]
            if len(cand) > 1 and prev is not None:
                # a pinch: take the sharpest right turn (keeps the loops simple)
                din = (p[0] - prev[0], p[1] - prev[1])
                cand.sort(key=lambda r: _turn(din, (r[0] - p[0], r[1] - p[1])))
            r = cand.pop(0)
            prev, p = p, r
            if p == p0:
                break
            loop.append(p)
        loops.append(loop)
    return loops


def _turn(a, b):
    """Turn from direction a to b: negative = right; for sorting (right turns first)."""
    import math
    return math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])


def _simplify(loop, keep):
    """Drop collinear vertices except keep; rotate the loop to start at keep when it is a vertex."""
    n = len(loop)
    if keep in loop:
        k = loop.index(keep)
        loop = loop[k:] + loop[:k]
    out = []
    for k, p in enumerate(loop):
        a, b = loop[k - 1], loop[(k + 1) % n]
        d0 = (p[0] - a[0], p[1] - a[1])
        d1 = (b[0] - p[0], b[1] - p[1])
        if k == 0 or d0[0] * d1[1] - d0[1] * d1[0] != 0 or d0[0] * d1[0] + d0[1] * d1[1] < 0:
            out.append(p)
    return out


def f_cells(fset, axis, cells, pitch, ox, oz):
    """F records of one axis: per group, the -axis record then ... - all -axis records first, then all +axis ones
    (DICE's order). Each is a closed polygon (first point repeated) with every corner's height from the area cells on
    that side of it."""
    groups = []
    for comp in _components(fset):
        loops = _loops(_quarters(comp))
        i0, j0 = comp[0]
        groups.append((i0, j0, loops))
    out = []
    for sign in (-1, 1):
        for i0, j0, loops in groups:
            # DICE starts at the first cell's top-left corner going -z for -x records, else at its bottom-left corner
            start = (2 * i0 - 1, 2 * j0 + 1) if (axis, sign) == ('x', -1) else (2 * i0 - 1, 2 * j0 - 1)
            for loop in loops:
                pts = _simplify(loop, start)
                pts = pts + [pts[0]]
                out.append(kymesh.Cell(F_DIRECTION[(axis, sign)],
                                       [_f_point(p, axis, sign, cells, fset, pitch, ox, oz) for p in pts]))
    return out


def _f_point(p, axis, sign, cells, fset, pitch, ox, oz):
    """World point of a polygon vertex (half units); height = mean of the area cells on the sign side of it along
    axis that touch it (corner: the two cells beside it; a cell centre: the next cell)."""
    vx, vz = p
    if axis == 'x':
        ci = [(vx + sign) // 2] if vx % 2 else [vx // 2 + sign]
        cj = [(vz - 1) // 2, (vz + 1) // 2] if vz % 2 else [vz // 2]
    else:
        cj = [(vz + sign) // 2] if vz % 2 else [vz // 2 + sign]
        ci = [(vx - 1) // 2, (vx + 1) // 2] if vx % 2 else [vx // 2]
    hs = [cells[(a, b)] for a in ci for b in cj if (a, b) in cells]
    if not hs:                                 # nothing on that side: the F cells touching the vertex
        hs = [cells[c] for c in fset if abs(2 * c[0] - vx) <= 1 and abs(2 * c[1] - vz) <= 1 and c in cells] or \
             [cells[min(fset, key=lambda c: abs(2 * c[0] - vx) + abs(2 * c[1] - vz))]]
    return (ox + vx * pitch / 2.0, sum(hs) / len(hs) + Y_OFFSET, oz + vz * pitch / 2.0)


def detail_features(cells, pitch, ox, oz, step, eligible=None, half=None, breakable=None, with_f=True):
    """(broken sides, D polylines, F cells) of one area. cells: {(i, j): ground height}; step: the link step (m);
    half, breakable: see broken_sides; with_f False: no F cells (their sides are D lines)."""
    broken = broken_sides(cells, step, eligible, half, breakable)
    if not broken:
        return broken, [], []
    fx, fz = f_cell_sets(broken, cells) if with_f else (set(), set())
    hcells = dict(cells)
    for ((i, j), _s), y in (half or {}).items():
        hcells.setdefault((i, j), y)
    ds = d_sides(broken, fx, fz)
    lines = d_lines(ds, hcells, pitch, ox, oz, corner_cuts(ds) if CUT_CORNERS else None)
    fs = f_cells(fx, 'x', cells, pitch, ox, oz) + f_cells(fz, 'z', cells, pitch, ox, oz)
    return broken, lines, fs
