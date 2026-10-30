r"""NavKit's offline sampler (tools\navsample_offline.py) on geometry built here (no game data): the cast loop
(layers top to bottom, the 0.1 m gap, the step through thin solids, nothing under the terrain, convex pieces solid,
the floor under a convex piece taller than the 10 m solid limit, mesh triangles hit from both sides with the normal
facing the ray, the sphere touching an edge), the capsule test (walls, ceilings, steep terrain; the exact
capsule-triangle interval against brute force), the file written in
navsample.cpp's version-3 format and read back by navsample.py, the grid alignment of navsample.cpp, and the
destroyed-state parts of a blueprint.

  python test_navsample_offline.py
"""
import math
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import navsample  # noqa: E402
import navsample_offline as no  # noqa: E402
from mohcore import terrain  # noqa: E402


def heightfield(fn, lo=-20.0, hi=20.0, size=64):
    """A terrain.Heightfield over [lo, hi]^2 with heights fn(x, z)."""
    cell = (hi - lo) / size
    xs = lo + np.arange(size) * cell
    X, Z = np.meshgrid(xs, xs)
    h = fn(X, Z).astype(np.float32)
    ymin, ymax = float(h.min()) - 1, float(h.max()) + 1
    raw = np.round((h - ymin) / (ymax - ymin) * 65535).astype(np.uint16)
    heights = (ymin + raw * ((ymax - ymin) / 65535.0)).astype(np.float32)
    return terrain.Heightfield(np.array([lo, ymin, lo]), np.array([hi, ymax, hi]), size, raw, heights)


def slab(x0, z0, x1, z1, y0, y1):
    """A closed box as 12 mesh triangles (outward winding)."""
    c = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], float)
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
             (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    t = c[np.array(faces)]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    flip = (n * (t.mean(1) - c.mean(0))).sum(1) < 0
    t[flip] = t[flip][:, [0, 2, 1]]
    return t


def geometry(pieces):
    """pieces: [(triangles, kind)] -> navsample_offline.Geometry (one solid id per convex piece)."""
    T, K, S = [], [], []
    for k, (t, kind) in enumerate(pieces):
        T.append(t)
        K.append(np.full(len(t), kind, np.uint8))
        S.append(np.full(len(t), k if kind == no.KIND_CONVEX else -1, np.int32))
    if not T:
        return no.Geometry(np.zeros((0, 3, 3)), np.zeros(0, np.uint8), np.zeros(0, np.int32), np.zeros(0, np.uint8),
                           np.zeros(0, np.int32), [])
    T, K, S = np.concatenate(T), np.concatenate(K), np.concatenate(S)
    return no.Geometry(T, K, S, np.full(len(T), ord('h'), np.uint8), np.zeros(len(T), np.int32), ['test'])


def column(s, x, z):
    i = int(round((x - s.min_x) / s.pitch))
    j = int(round((z - s.min_z) / s.pitch))
    r = s.column(i, j)
    return [(round(float(s.y[k]), 3), round(float(s.normal[k, 1]), 2), int(s.flags[k]), int(s.hit[k, 0])) for k in r]


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    B = no.CAP_BOTTOMS['']                           # the normal run's capsule bottom (MOH 0.3)
    flat = heightfield(lambda x, z: np.zeros_like(x))
    grid = no.Grid(0.8, -4.0, -4.0, 11, 11, -10.0, 50.0)

    # 1. a deck 0.3 m thick at 3 m over flat terrain: its top, its underside (hit from below the top, normal flipped
    #    up) and the terrain; beside it only the terrain
    g = geometry([(slab(-2.1, -2.1, 2.1, 2.1, 2.7, 3.0), no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[B]
    col = column(s, 0.0, 0.0)
    check([c[0] for c in col] == [3.0, 2.7, 0.0] and all(c[1] > 0.99 for c in col),
          f'deck: top, underside (normal facing the ray), terrain: {col}')
    check(col[0][3] == no.MESH_SHAPE_VTABLE and col[2][3] == no.TERRAIN_SHAPE_VTABLE, 'deck: hit kinds (mesh, terrain)')
    # the underside 'surface' (inside the 0.3 m deck) is blocked: MOH's capsule starts 0.3 m over it (BFBC2's fitted
    # in-game capsule started 0.55 m over it, clear of the deck's top); the terrain under the deck is clear
    check(col[1][2] == 1 and col[2][2] == 0, 'deck: the capsule (up to the soldier height, 1.8 m) fits under a 2.7 m underside')
    check(column(s, 3.2, 3.2) == [(0.0, 1.0, 0, no.TERRAIN_SHAPE_VTABLE)], 'beside the deck: terrain only, clear')

    # 2. a thin slab (0.07 m): its underside is within 0.17 m of its top - the cast starting 0.1 under the top hits it
    #    at once and steps down 0.5 m, so it is not recorded
    g = geometry([(slab(-2.1, -2.1, 2.1, 2.1, 2.93, 3.0), no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[B]
    check([c[0] for c in column(s, 0.0, 0.0)] == [3.0, 0.0], f'thin slab: underside skipped {column(s, 0.0, 0.0)}')

    # 3. nothing under the terrain is recorded (a box buried 2 m deep)
    g = geometry([(slab(-2.1, -2.1, 2.1, 2.1, -3.0, -2.0), no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[B]
    check([c[0] for c in column(s, 0.0, 0.0)] == [0.0], 'buried box: not recorded')

    # 4. a convex block standing on the terrain is solid: its top (grown by nothing here), then the cast steps
    #    through it and stops in the terrain - the terrain under it is not recorded
    g = geometry([(slab(-2.1, -2.1, 2.1, 2.1, 0.0, 3.0), no.KIND_CONVEX)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[B]
    col = column(s, 0.0, 0.0)
    check([c[0] for c in col] == [3.0] and col[0][3] == no.CONVEX_SHAPE_VTABLE, f'convex block: top only {col}')
    # the same block as a mesh: hollow, its floor (the bottom face from inside) is recorded at the terrain height
    g = geometry([(slab(-2.1, -2.1, 2.1, 2.1, 0.0, 3.0), no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[B]
    check([c[0] for c in column(s, 0.0, 0.0)] == [3.0, 0.0], f'mesh block: top and bottom {column(s, 0.0, 0.0)}')
    # a convex piece taller than MAX_SOLID (12 m, from 15 m up) over a floor at 4 m: past the limit the cast goes on
    # from the piece's bottom - the floor and the terrain under it are recorded (mp_09_overrun's invisible wall)
    g = geometry([(slab(-2.1, -2.1, 2.1, 2.1, 15.0, 27.0), no.KIND_CONVEX),
                  (slab(-2.1, -2.1, 2.1, 2.1, 3.9, 4.0), no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[B]
    check([c[0] for c in column(s, 0.0, 0.0)] == [27.0, 4.0, 0.0], f'tall convex piece: floor below kept {column(s, 0.0, 0.0)}')

    # 5. the sphere touching an edge: a thin vertical sheet (no thickness) 0.03 m beside a column gives a hit at its
    #    top edge with an upward normal; 0.2 m away it does not
    sheet = np.array([[[0.03, 0.0, -2.0], [0.03, 1.0, -2.0], [0.03, 1.0, 2.0]],
                      [[0.03, 0.0, -2.0], [0.03, 1.0, 2.0], [0.03, 0.0, 2.0]]])
    g = geometry([(sheet, no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[B]
    col = column(s, 0.0, 0.0)
    check(col[0][0] == 1.0 and col[0][1] > 0.5, f'sheet 0.03 m away: its top edge hit, normal up-ish {col}')
    check([c[0] for c in column(s, 0.8, 0.0)] == [0.0], 'sheet 0.77 m away: terrain only')

    # 6. the capsule: a wall 0.3 m from a column blocks it, 0.7 m away not; a ceiling 1.7 m up blocks, 2.6 m not
    wall = slab(0.3, -4.0, 0.5, 4.0, -1.0, 5.0)
    g = geometry([(wall, no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)
    check(column(s[B], 0.0, 0.0)[0][2] == 1, 'wall 0.3 m away: blocked')
    check(column(s[B], -0.8, 0.0)[0][2] == 0, 'wall 1.1 m away: clear')
    for top, want in ((1.7, 1), (2.6, 0)):
        g = geometry([(slab(-4.0, -4.0, 4.0, 4.0, top, top + 0.3), no.KIND_MESH)])
        s = no.sample_grid(grid, g, flat, log=lambda *a: None)
        check(column(s[B], 0.0, 0.0)[-1][2] == want, f'ceiling (underside) {top} m over the floor: blocked {want}')

    # 7. steep terrain: blocked from about 59 degrees in the normal run with MOH's capsule (BFBC2's fitted one: ~64), 30 and 55 clear
    for deg, want in ((30, 0), (55, 0), (72, 1)):
        hf = heightfield(lambda x, z, d=deg: x * math.tan(math.radians(d)), size=128)
        s = no.sample_grid(no.Grid(0.8, -1.6, -1.6, 5, 5, -100.0, 100.0), geometry([]), hf, log=lambda *a: None)[B]
        col = column(s, 0.0, 0.0)
        check(len(col) == 1 and col[0][2] == want and abs(col[0][1] - math.cos(math.radians(deg))) < 0.02,
              f'terrain at {deg} deg: blocked {want}, normal {col}')
    # the reported terrain height is lifted by RAY_RADIUS * (1 - ny) on a slope, as the in-game samples show
    hf = heightfield(lambda x, z: x * math.tan(math.radians(45)), size=128)
    s = no.sample_grid(no.Grid(0.8, -1.6, -1.6, 5, 5, -100.0, 100.0), geometry([]), hf, log=lambda *a: None)[B]
    y = column(s, 0.0, 0.0)[0][0]
    check(abs(y - (float(hf.height(0.0, 0.0)) + no.RAY_RADIUS * (1 - math.cos(math.radians(45))))) < 0.002,
          f'terrain lift on a 45 deg slope: {y}')

    # 8. the capsule-triangle interval against brute force (random triangles, random vertical lines)
    rng = np.random.default_rng(3)
    T = rng.uniform(-1, 1, (300, 3, 3))
    T[:100, :, 1] = 0.2                      # some flat
    T[100:150, :, 0] = T[100:150, :1, 0]     # some vertical (one x)
    px, pz = rng.uniform(-1.2, 1.2, 300), rng.uniform(-1.2, 1.2, 300)
    lo, hi = no._line_tri_interval(T, px, pz, 0.4)
    hs = np.linspace(-2, 2, 2001)
    bad = 0
    for k in range(300):
        P = np.stack([np.full_like(hs, px[k]), hs, np.full_like(hs, pz[k])], 1)
        d = np.array([point_tri_dist(p, T[k]) for p in P[::10]])
        inside = hs[::10][d <= 0.4]
        if len(inside):
            bad += not (lo[k] <= inside.min() + 0.021 and hi[k] >= inside.max() - 0.021 and
                        lo[k] >= inside.min() - 0.021 and hi[k] <= inside.max() + 0.021)
        else:
            bad += lo[k] <= hi[k] and (hi[k] - lo[k]) > 0.03
    check(bad == 0, f'capsule interval against brute force on 300 triangles: {bad} wrong')

    # 9. the file: navsample.cpp's version-3 layout, read back by navsample.py
    g = geometry([(slab(-2.1, -2.1, 2.1, 2.1, 2.7, 3.0), no.KIND_MESH)])
    s = no.sample_grid(grid, g, flat, log=lambda *a: None)[no.CAP_BOTTOMS['_hi']]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'x_navkit_hi.bin')
        no.write(p, s)
        b, cols = navsample.load(p)
        check(cols == grid.ncols and np.array_equal(b.start, s.start) and np.allclose(b.y, s.y) and
              np.array_equal(b.flags, s.flags) and np.array_equal(b.hit, s.hit) and abs(b.cap_bottom - no.CAP_BOTTOMS['_hi']) < 1e-6,
              'file written and read back (version 3)')
        r = no.compare(b, s, log=lambda *a: None)
        check(r['layers_equal'] == 100 and r['surf_a_found'] == 100 and r['blocked_agree_matched'] == 100,
              'compare: a file against itself agrees everywhere')

    # 10. the grid as navsample.cpp's Begin() aligns it; detailed-zone rectangles mask the columns
    gr = no.Grid.from_box(0.8, [-770.0, -910.0, 135.0, -555.0], [290, 680])
    check(abs(gr.min_x - (-770.4)) < 1e-3 and abs(gr.min_z - (-910.4)) < 1e-3 and gr.nx == 1133 and gr.nz == 446,
          f'grid of sp_004_b\'s 0.8 m run: ({gr.min_x:.2f}, {gr.min_z:.2f}) {gr.nx} x {gr.nz}')
    gr = no.Grid.from_box(0.2, [-1, -1, 201, 101], [0, 10], rects=[[-1, -1, 101, 101]])
    check(gr.mask[:, :10].all() and not gr.mask[:, -10:].any(), 'rectangles mask the columns outside them')

    # 11. destroyed-state parts: the parts named only by health states below a component's full health
    xml = '''<partition>
	<instance guid="A" type="GameSharedResources.PartComponentData">
		<field name="Name">ruin</field>
		<array name="HealthStates">
			<item ref="b" />
			<item ref="c" />
		</array>
	</instance>
	<instance guid="B" type="GameSharedResources.HealthStateData">
		<field name="MeshName">ruin_downed</field>
		<field name="Health">0</field>
	</instance>
	<instance guid="C" type="GameSharedResources.HealthStateData">
		<field name="MeshName">ruin</field>
		<field name="Health">100000</field>
	</instance>
	<instance guid="D" type="GameSharedResources.PartComponentData">
		<array name="HealthStates">
			<item ref="e" />
			<item ref="f" />
		</array>
	</instance>
	<instance guid="E" type="GameSharedResources.HealthStateData">
		<field name="MeshName" />
		<field name="Health">0</field>
	</instance>
	<instance guid="F" type="GameSharedResources.HealthStateData">
		<field name="MeshName">Part_01</field>
		<field name="Health">100</field>
	</instance>
</partition>
'''
    hav = '''<partition><instance type="Physics.HavokAsset"><array name="PartInfoDatas">
			<complex>
				<field name="Name">base</field>
			</complex>
			<complex>
				<field name="Name">Part_01</field>
			</complex>
			<complex>
				<field name="Name">ruin</field>
			</complex>
			<complex>
				<field name="Name">ruin_downed</field>
			</complex>
		</array></instance></partition>
'''
    with tempfile.TemporaryDirectory() as d:
        bp, hx = os.path.join(d, 'House.xml'), os.path.join(d, 'House_Havok.xml')
        open(bp, 'w').write(xml)
        open(hx, 'w').write(hav)
        check(no.destroyed_parts(bp) == {'ruin_downed'}, f'destroyed parts: {no.destroyed_parts(bp)}')
        check(no.part_names(hx[:-4] + '.res') == ['base', 'part_01', 'ruin', 'ruin_downed'], 'part names in order')
        check(no.destroyed_parts(os.path.join(d, 'missing.xml')) == set(), 'no blueprint: no destroyed parts')

    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


def point_tri_dist(p, t):
    """Distance from p to triangle t (brute force: the plane inside, else the nearest edge)."""
    a, b, c = t
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n)
    best = np.inf
    if ln > 1e-12:
        n = n / ln
        q = p - np.dot(p - a, n) * n
        ok = all(np.dot(np.cross(v - u, q - u), n) >= -1e-12 for u, v in ((a, b), (b, c), (c, a)))
        if ok:
            best = abs(np.dot(p - a, n))
    for u, v in ((a, b), (b, c), (c, a)):
        e = v - u
        s = np.clip(np.dot(p - u, e) / max(np.dot(e, e), 1e-12), 0, 1)
        best = min(best, np.linalg.norm(p - (u + s * e)))
    return best


if __name__ == '__main__':
    sys.exit(main(sys.argv))
