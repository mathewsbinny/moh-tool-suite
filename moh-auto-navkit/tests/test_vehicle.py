r"""NavKit's vehicle path data (tools/navvehicle.py) on a level built here (no game data): two 100 m sectors of flat
ground at 0.8 m with a 2.4 x 16 m block standing on it, sampled the way the in-game sampler records it.

Checks: the vehicle kinds of spawn blueprints; the resampling onto the 1 m grid (heights moved along the ground plane);
the obstacle distance (a rise of more than StepMax within the cylinder's reach, overhangs, ExcludeFromPathfinding
objects crushed); seeds along terrain splines; the per-sector flood (a sector without a seed stays empty, as in DICE's
files); and a whole build written as a vehicle pathdata.hpd - DICE's vehicle header values (pitch 1, resolution 0.02,
radius 2.5, 3 x 3 tiles of 50 samples, DistEdgeMax 50), nodes 2 m over the ground, area height ranges ground .. +3.2,
no ground within the vehicle radius of the block, hpdcheck's structural checks, and a byte-exact round trip
(hpdtool / kymesh). Output goes to a temporary folder.

  python test_vehicle.py
"""
import math
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import hpdcheck  # noqa: E402
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import navsample  # noqa: E402
import navvehicle  # noqa: E402
import navwalk  # noqa: E402

TERRAIN = navwalk.TERRAIN_SHAPE_VTABLE
OBJECT = 0x01483B38
GROUND = 10.0
BLOCK = (60.0, 62.4, 40.0, 56.0, 13.0)        # x0, x1, z0, z1, top (sample columns 75-78 x 50-70)


def level(nx=250, nz=125, slope_x=0.0):
    """Columns at 0.8 m from (0, 0): terrain at GROUND (+ slope_x * x); inside BLOCK the block's top (an object)
    and the terrain under it, marked blocked (the soldier capsule overlaps the block there)."""
    ys, nrm, flags, hit, start = [], [], [], [], [0]
    n = np.array([-slope_x, 1.0, 0.0])
    n = n / np.linalg.norm(n)
    for j in range(nz):
        for i in range(nx):
            x, z = i * 0.8, j * 0.8
            g = GROUND + slope_x * x
            if 75 <= i <= 78 and 50 <= j <= 70:
                col = [(BLOCK[4], (0, 1, 0), 0, OBJECT), (g, n, navsample.BLOCKED, TERRAIN)]
            else:
                col = [(g, n, 0, TERRAIN)]
            for y, nn, fl, h in col:
                ys.append(y)
                nrm.append(nn)
                flags.append(fl)
                hit.append((h, 0, 0))
            start.append(len(ys))
    return navsample.Samples(0.8, 0.0, 0.0, nx, nz, 100.0, 0.0, 0.4, 0.28, 1.9, 0.05, np.array(start, np.int64),
                             np.array(ys, np.float32), np.array(nrm, np.float32), np.array(flags, np.uint8),
                             np.array(hit, np.uint32))


def xz_of(s):
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
    return s.min_x + (col % s.nx) * s.pitch, s.min_z + (col // s.nx) * s.pitch


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    # vehicle kinds
    kinds = {'objects/vehicles/ru/t90/t90/guid': 'land', 'objects/vehicles/us/quadbike/quadbike/g': 'land',
             'objects/vehicles/us/ah60/ah60/g': 'air', 'objects/vehicles/ru/mi24_adv/mi24/g': 'air',
             'objects/vehicles/us/pbl/pbl/g': 'sea', 'objects/vehicles/ru/ru_hmg_kord/kord/g': 'static',
             'objects/vehicles/ru/ru_at_kornet/x/g': 'static', 'objects/vehicles/us/humvee/humvee/g': 'land'}
    check(all(navvehicle.vehicle_kind(r) == k for r, k in kinds.items()), 'vehicle kinds: tanks / cars / quads land, '
          'helicopters air, boats sea, fixed weapons static')

    # resampling a sloped plane: heights follow the plane at the new points
    sl = level(nx=40, nz=20, slope_x=0.2)
    s1, src = navvehicle.resample(sl, 1.0)
    x1, _ = xz_of(s1)
    check(s1.pitch == 1.0 and s1.min_x == 0.0 and np.allclose(s1.y, GROUND + 0.2 * x1, atol=1e-4),
          'resampling onto the 1 m grid: heights moved along the ground plane')

    # obstacle distance on the flat level
    s8 = level()
    x8, z8 = xz_of(s8)
    gx, gz = navvehicle.gradients(s8)
    ground = s8.y == GROUND
    solid = np.ones(len(s8.y), bool)
    d = navvehicle.obstacle_distance(s8, ground, solid, None, gx, gz)
    dx = np.maximum(np.maximum(BLOCK[0] - x8, x8 - BLOCK[1]), 0)
    dz = np.maximum(np.maximum(BLOCK[2] - z8, z8 - BLOCK[3]), 0)
    true = np.hypot(dx, dz)
    open_ = ground & (true > 0.01)
    near = open_ & (true <= 3.0)
    check(np.allclose(d[near], true[near], atol=0.85), 'obstacle distance: within a sample of the block\'s true distance')
    check((d[ground & (true > 4.0)] == 99).all(), 'obstacle distance: none beyond the reach')
    under = ground & (true <= 0.01)
    check((d[under] == 0).all(), 'the ground under the block: the block is an overhang over it (distance 0)')
    crushed = navvehicle.obstacle_distance(s8, ground, ~(s8.y == BLOCK[4]), None, gx, gz)
    check((crushed[open_] == 99).all(), 'an object that is not solid (ExcludeFromPathfinding, crushed) blocks nothing')
    extra = np.where((s8.flags & navsample.BLOCKED) > 0, s8.y + 1.0, np.nan).astype(np.float32)
    db = navvehicle.obstacle_distance(s8, ground, np.zeros(len(s8.y), bool), extra, gx, gz)
    check(np.allclose(db[near], true[near], atol=0.85), 'blocked soldier samples act as obstacles (thin walls)')
    high = np.where(s8.y == BLOCK[4], GROUND + 6.0, s8.y).astype(np.float32)
    s8h = navsample.Samples(**{**s8.__dict__, 'y': high})
    dh = navvehicle.obstacle_distance(s8h, ground, solid, None, gx, gz)
    check((dh[open_] == 99).all(), 'geometry more than EntityHeight (4 m) above the ground passes over')

    # vehicle grid, seeds, per-sector flood
    s1, m = navvehicle.vehicle_grid(s8, log=lambda *a: None)
    cand = navvehicle.candidates(m)
    x1, z1 = xz_of(s1)
    check(cand[(s1.y == GROUND) & (x1 > 70) & (x1 < 90)].all(), 'open flat ground is a candidate')
    t1 = np.hypot(np.maximum(np.maximum(BLOCK[0] - x1, x1 - BLOCK[1]), 0), np.maximum(np.maximum(BLOCK[2] - z1, z1 - BLOCK[3]), 0))
    check(not cand[(s1.y == GROUND) & (t1 <= navvehicle.CLEARANCE - 0.6)].any(),
          'no candidate ground within the vehicle radius of the block')
    seeds = navvehicle.seed_surfaces(s1, cand, [(20.0, GROUND, 20.0)])
    one = navvehicle.flood_per_sector(s1, cand, seeds, 0, 0)
    check(one.any() and x1[one].max() <= 101.0 + 1e-6 and x1[one].min() == 0.0,
          'per-sector flood: the ground stays in the seeded sector\'s window (to 101 m)')
    both = navvehicle.flood_per_sector(s1, cand, navvehicle.seed_surfaces(s1, cand, [(20.0, GROUND, 20.0), (150.0, GROUND, 50.0)]), 0, 0)
    check(x1[both].max() >= 199.0, 'a seed in the second sector floods it too')
    check(not both[s1.y == BLOCK[4]].any(), 'the top of the block (no step up to it) is not reached')
    whole = navvehicle.flood(s1, cand, seeds)
    check(x1[whole].max() >= 199.0, 'the level-wide flood crosses sector lines')
    xml = ('<instance guid="P1" type="Terrain.TerrainSplinePointData"><complex name="Position">0/10/5/0</complex>'
           '</instance><instance guid="P2" type="Terrain.TerrainSplinePointData"><complex name="Position">10/10/5/0'
           '</complex></instance><instance guid="S1" type="Terrain.TerrainSplineData"><array name="Points">'
           '<item ref="p1" /><item ref="p2" /></array></instance>')
    rp = navvehicle.road_points(xml, 2.0)
    check(len(rp) == 6 and rp[0] == (0.0, 10.0, 5.0) and rp[-1] == (10.0, 10.0, 5.0), 'road seed points every 2 m along a spline')

    # a whole build, written and read back
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'pathdata.hpd')
        res = navvehicle.stage_on_grid(s1, both, 0, 0, log=lambda *a: None)
        navvehicle.write(res, out, log=lambda *a: None)
        data = open(out, 'rb').read()
        f = hpdtool.parse(data)
        check(hpdtool.write(f) == data, 'byte-exact round trip (hpdtool)')
        meshes = [kymesh.parse_mesh(s.mesh) for s in f.sectors]
        check(all(kymesh.write_mesh(mm) == s.mesh for mm, s in zip(meshes, f.sectors)), 'byte-exact mesh round trip (kymesh)')
        check(len(f.sectors) == 2, f'two sectors ({len(f.sectors)})')
        check(all(mm.pitch == 1.0 and abs(mm.resolution - 0.02) < 1e-6 and mm.radius == 2.5 and (mm.tiles_x, mm.tiles_z) == (3, 3)
                  for mm in meshes), 'DICE\'s vehicle header: pitch 1, resolution 0.02, radius 2.5, 3 x 3 tiles')
        check(all(a.radius == 2.5 and a.pitch == 1.0 for mm in meshes for a in mm.areas), 'areas: radius 2.5, pitch 1')
        check(all(s.nodes and s.nodes[0].value == 50.0 for s in f.sectors), 'DistEdgeMax 50 on each sector\'s first node')
        ny = [n.pos[1] for s in f.sectors for n in s.nodes]
        check(ny and all(abs(y - (GROUND + 2.0)) < 1e-4 for y in ny), 'route nodes 2 m over the ground (EntityHeight / 2)')
        rng = {(round(a.min_y, 3), round(a.max_y, 3)) for mm in meshes for a in mm.areas}
        check(rng == {(GROUND, GROUND + 3.2)}, f'area height range ground .. ground + 3.2 ({sorted(rng)[:3]})')
        tl = set()
        for mm, s in zip(meshes, f.sectors):
            ox = (s.id >> 8) * 100.0
            for a in mm.areas:
                for b in a.portals:
                    if abs(b.p0[0] - b.p1[0]) < 1e-6:
                        tl.add(round(b.p0[0] - ox, 2))
        check({48.5, 98.5} <= tl, f'tile lines 48.5 / 98.5 m into the sector (50-sample tiles; portals at {sorted(tl)[:6]})')
        cells = []
        import navdiff
        with navvehicle.vehicle_cells():
            for mm in meshes:
                for a in mm.areas:
                    cells += list(navdiff.area_cells(a).keys())
        cx = np.array([c[0] for c in cells], float)
        cz = np.array([c[1] for c in cells], float)
        dist = np.hypot(np.maximum(np.maximum(BLOCK[0] - cx, cx - BLOCK[1]), 0), np.maximum(np.maximum(BLOCK[2] - cz, cz - BLOCK[3]), 0))
        check(len(cells) > 15000 and dist.min() >= 1.9, f'no area ground within the vehicle radius of the block (nearest {dist.min():.2f} m)')
        problems = hpdcheck.check(out)
        bad = {k: v for k, v in problems.items() if v}
        check(not bad, 'hpdcheck: closed boundaries, edges, node order, no zero-length edges, detail features'
              + (f' - {list(bad.items())[0]}' if bad else ''))
        inf = navvehicle.info(out)
        check(inf['pitch'] == {1.0: 2} and inf['dist_edge_max'] == [50.0], 'info() reads the vehicle settings back')
        cmp_ = navvehicle.compare(out, out, log=lambda *a: None)
        check(cmp_['ground'] == 100.0 and cmp_['extra_ground'] == 0 and cmp_['connectivity'] == 100.0,
              'compare(): a file against itself is 100 % ground, 0 extra, 100 connectivity')
    print('all passed' if not failed else f'{failed} FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
