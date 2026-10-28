r"""NavKit's objective floor rule (navwalk.OBJECTIVE_FLOOR, objective_floor) on a sample grid built here (no game
data): terrain at y 0, a ForbidPathfinding box (an M-COM: top y 1.4) standing on it, its floor and a ring of floor
around it blocked by clearance (the capsule touches the box). An AI order position at the box keeps the floor under
and around it, joined to the ground; with the rule off, or the order position elsewhere, the hole stays; the box top
stays forbidden.

  python test_objective_floor.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navwalk  # noqa: E402

OBJECT = 0x1483B38
P = 0.2
X0 = 10.0
NX, NZ = 30, 9
BOX = {(i, j) for i in range(5, 9) for j in range(3, 6)}            # the box's footprint, top y 1.4
RING = {(i, j) for i in range(4, 10) for j in range(2, 7)}          # floor blocked by clearance (footprint included)
TOP = 1.4


def grid():
    ys, hit, flags, start = [], [], [], [0]
    for j in range(NZ):
        for i in range(NX):
            ys.append(0.0), hit.append(navwalk.TERRAIN_SHAPE_VTABLE), flags.append(1 if (i, j) in RING else 0)
            if (i, j) in BOX:
                ys.append(TOP), hit.append(OBJECT), flags.append(0)
            start.append(len(ys))
    n = len(ys)
    return SimpleNamespace(nx=NX, nz=NZ, pitch=P, min_x=X0, min_z=X0, start=np.array(start), y=np.array(ys, np.float32),
                           flags=np.array(flags, int), hit=np.array([(h, 0, 0) for h in hit], np.int64),
                           normal=np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1)))


SLAB = 0.9                                                          # slab_grid: the floor everything stands on


def slab_grid():
    """A slab (object surface) at y 0.9 over terrain at y 0 everywhere; the box stands on the slab (top 0.9 + 1.4).
    The terrain under the slab is blocked (no room); the slab floor in RING is blocked by the box."""
    ys, hit, flags, start = [], [], [], [0]
    for j in range(NZ):
        for i in range(NX):
            ys.append(0.0), hit.append(navwalk.TERRAIN_SHAPE_VTABLE), flags.append(1)
            ys.append(SLAB), hit.append(OBJECT), flags.append(1 if (i, j) in RING else 0)
            if (i, j) in BOX:
                ys.append(SLAB + TOP), hit.append(OBJECT), flags.append(0)
            start.append(len(ys))
    n = len(ys)
    return SimpleNamespace(nx=NX, nz=NZ, pitch=P, min_x=X0, min_z=X0, start=np.array(start), y=np.array(ys, np.float32),
                           flags=np.array(flags, int), hit=np.array([(h, 0, 0) for h in hit], np.int64),
                           normal=np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1)))


STRAY = {(5, 3), (8, 5)}                                            # stray_grid: box-edge cells with a side hit


def stray_grid():
    """grid() plus a hit on the box's side 0.4 m under its top in two edge cells (the sampler's rays graze the
    object): nearer an order position on the top than the floor is, but too few to be a layer."""
    s = grid()
    ys, hit, flags, start = [], [], [], [0]
    for c in range(NX * NZ):
        a, b = s.start[c], s.start[c + 1]
        ys += [float(v) for v in s.y[a:b]]
        hit += [int(h[0]) for h in s.hit[a:b]]
        flags += [int(f) for f in s.flags[a:b]]
        if (c % NX, c // NX) in STRAY:
            ys.append(TOP - 0.4), hit.append(OBJECT), flags.append(0)
        start.append(len(ys))
    n = len(ys)
    return SimpleNamespace(nx=NX, nz=NZ, pitch=P, min_x=X0, min_z=X0, start=np.array(start), y=np.array(ys, np.float32),
                           flags=np.array(flags, int), hit=np.array([(h, 0, 0) for h in hit], np.int64),
                           normal=np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1)))


def box_tris(bottom=False, base=0.0):
    """The box top as two triangles (x, y, z), a little larger than its footprint's sample points; with bottom, also
    its bottom face on the ground (an M-COM's collision: the ground under it then lies on a forbidden triangle)."""
    x0, x1 = X0 + 5 * P - 0.1, X0 + 8 * P + 0.1
    z0, z1 = X0 + 3 * P - 0.1, X0 + 5 * P + 0.1
    out = []
    for y in (base + TOP, base) if bottom else (base + TOP,):
        a, b, c, d = (x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)
        out += [[a, b, c], [a, c, d]]
    return np.array(out, np.float32)


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

    s, tris = grid(), box_tris()
    seeds = [pt(0, 0, 0.0)]
    centre = pt(6.5, 4, 0.0)
    keep0 = navwalk.walkable(s, tris, step=0.28, stairs=False, extra_seeds=seeds)
    check(not kept_at(s, keep0, 6, 4, 0.0), 'no order position: the floor under the box is a hole')
    keep = navwalk.walkable(s, tris, step=0.28, stairs=False, extra_seeds=seeds, objective_points=[centre])
    check(kept_at(s, keep, 6, 4, 0.0), 'an order position at the box keeps the floor under it')
    check(all(kept_at(s, keep, i, j, 0.0) for (i, j) in RING), '... and the blocked ring around it, joined to the ground')
    check(not kept_at(s, keep, 6, 4, TOP), 'the box top stays forbidden')
    keep_top = navwalk.walkable(s, tris, step=0.28, stairs=False, extra_seeds=seeds, objective_points=[pt(6.5, 4, TOP)])
    check(kept_at(s, keep_top, 6, 4, 0.0), 'an order position on the box top works too')
    keep_far = navwalk.walkable(s, tris, step=0.28, stairs=False, extra_seeds=seeds, objective_points=[pt(28, 4, 0.0)])
    check(not kept_at(s, keep_far, 6, 4, 0.0), 'an order position away from forbidden objects changes nothing')
    closed = box_tris(bottom=True)
    keep_c0 = navwalk.walkable(s, closed, step=0.28, stairs=False, extra_seeds=seeds)
    check(not kept_at(s, keep_c0, 6, 4, 0.0), 'a box with a bottom face: no order position, the hole stays')
    keep_c = navwalk.walkable(s, closed, step=0.28, stairs=False, extra_seeds=seeds, objective_points=[centre])
    check(kept_at(s, keep_c, 6, 4, 0.0) and all(kept_at(s, keep_c, i, j, 0.0) for (i, j) in RING),
          'a box with a bottom face (the ground under it on a forbidden triangle): the floor is kept all the same')
    check(not kept_at(s, keep_c, 6, 4, TOP), '... and its top stays forbidden')
    s2, slab_box = slab_grid(), box_tris(bottom=True, base=SLAB)
    keep_s = navwalk.walkable(s2, slab_box, step=0.28, stairs=False, extra_seeds=[pt(0, 0, SLAB)],
                              objective_points=[pt(6.5, 4, SLAB)])
    check(kept_at(s2, keep_s, 6, 4, SLAB) and all(kept_at(s2, keep_s, i, j, SLAB) for (i, j) in RING),
          'a box on a slab above the terrain: the slab floor under and around it is kept (not the terrain under the slab)')
    check(not kept_at(s2, keep_s, 6, 4, 0.0), '... and the terrain under the slab stays out')
    s3 = stray_grid()
    keep_st = navwalk.walkable(s3, tris, step=0.28, stairs=False, extra_seeds=seeds, objective_points=[pt(6.5, 4, TOP)])
    check(kept_at(s3, keep_st, 6, 4, 0.0) and all(kept_at(s3, keep_st, i, j, 0.0) for (i, j) in RING),
          'stray hits on the box side, nearer an order position on its top than the floor: the floor is kept all the same')
    old = navwalk.OBJECTIVE_FLOOR
    try:
        navwalk.OBJECTIVE_FLOOR = False
        keep_off = navwalk.walkable(s, tris, step=0.28, stairs=False, extra_seeds=seeds, objective_points=[centre])
        check(not kept_at(s, keep_off, 6, 4, 0.0), 'OBJECTIVE_FLOOR off: the hole stays')
    finally:
        navwalk.OBJECTIVE_FLOOR = old
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
