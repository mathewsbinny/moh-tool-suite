r"""NavKit's boat mesh (tools\navwater_mesh.py) on water built here (no game data): deep water free, a shallow bar
splitting two basins, a pier crossing the hull band blocking, a bridge deck above it not blocking, padding by the hull
radius, regions, a connected route graph per region with every edge in sight, shore links on a beach and a dock (and
none where the ground is too high or too far), the side file read back unchanged.
With --game: the generator on ONS_MP_004 (Isla Inocentes; output under OPFOR's work\navwater\_test) - structure,
read-back, every sea-vehicle spawn attached to one region, shore links on the infantry path data's cells.

  python test_navwater_mesh.py [--game]
"""
import json
import math
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import navwater_mesh as nm  # noqa: E402


class Flat:
    """A terrain height field stand-in: height(x, z) from a function; bounds for terrain_heights."""
    def __init__(self, f, lo=(-1000, 0, -1000), hi=(1000, 0, 1000)):
        self.f, self.min, self.max = f, np.array(lo, float), np.array(hi, float)

    def height(self, x, z):
        return self.f(np.asarray(x, float), np.asarray(z, float))


def bed(x, z):
    """Water at y 10 everywhere. Bed: 4 m deep over x 0..200; a shallow bar (y 9.6, 0.4 m deep) across x 96..104
    splitting the lake into two basins; land (y 12) below x 0 and beyond x 204; a beach strip (y 9, 1 m under
    water) at x 200..204."""
    y = np.full(np.broadcast(x, z).shape, 6.0)
    y = np.where((x >= 96) & (x <= 104), 9.6, y)                 # the bar: 0.4 m deep, under the 1 m draft
    y = np.where((x < 0) | (x > 200), 12.0, y)                   # dry land around
    y = np.where((x >= 200) & (x < 204), 9.0, y)                 # a beach strip 1 m under water at x 200..204
    return y


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    prof = nm.PROFILES['pbl']
    grid = nm.make_grid((-20, -20, 220, 120), 2.0)
    v = np.array([[-50, 10, -50], [250, 10, -50], [250, 10, 150], [-50, 10, 150]], np.float32)
    t = np.array([[0, 1, 2], [0, 2, 3]])
    surface = nm.surface_heights(grid, v, t)
    hf = Flat(bed)
    terrain = nm.terrain_heights(grid, hf)
    extra = np.zeros(surface.shape, bool)
    # a pier: points from y 9 to 12 (crossing the band 9..13) along x 40, z 0..30
    pier = np.array([[40.0, y, z] for z in np.arange(0, 30, 0.5) for y in (9.0, 10.5, 12.0)])
    n_pier = nm.mark_points(grid, surface, extra, pier, prof)
    # a bridge deck at y 15 (above the band, surface 10 + height 3) along z 60, x 140..160: no block
    deck = np.array([[x, 15.0, 60.0] for x in np.arange(140, 160, 0.5)])
    n_deck = nm.mark_points(grid, surface, extra, deck, prof)
    # a wall as triangles: a vertical quad x 160, z 10..20, y 5..14 crosses the band
    wall = np.array([[[160, 5, 10], [160, 14, 10], [160, 14, 20]], [[160, 5, 10], [160, 14, 20], [160, 5, 20]]], float)
    n_wall = nm.mark_triangles(grid, surface, extra, wall, prof)
    roof = np.array([[[150, 16, 40], [170, 16, 40], [170, 16, 50]]], float)
    n_roof = nm.mark_triangles(grid, surface, extra, roof, prof)
    check(n_pier > 0 and n_deck == 0, f'pier points in the hull band block ({n_pier}), a bridge deck above it does not')
    check(n_wall > 0 and n_roof == 0, f'a wall crossing the band blocks ({n_wall} points), a roof above it does not')
    i_wall, j_wall = grid.cell_of(160, 15)
    check(bool(extra[j_wall, i_wall]), 'the wall cell is blocked')
    wm = nm.build(surface, terrain, grid, prof, extra, level='synthetic')
    free = (wm.flags & nm.F_FREE) != 0
    nav = (wm.flags & nm.F_NAV) != 0

    def at(x, z, m):
        i, j = grid.cell_of(x, z)
        return bool(m[j, i])
    check(at(50, 50, free) and at(150, 80, free), 'deep open water is free')
    check(not at(100, 50, nav) and (wm.flags[grid.cell_of(100, 50)[1], grid.cell_of(100, 50)[0]] & nm.F_BLOCKED), 'the shallow bar is blocked')
    check(not at(40, 10, nav), 'the pier is blocked')
    check(at(150, 60, free), 'under the bridge deck is free')
    check(not at(94, 50, free) and at(92, 50, free), 'padding (radius 3.5 m): free 4 m from the bar, not 2 m from it')
    check(not at(-10, 50, (wm.flags & nm.F_WATER) != 0) and at(-10, 50, (wm.flags & nm.F_DRY) != 0), 'land beside the lake is dry, not water')
    r1, r2 = wm.region[grid.cell_of(50, 50)[::-1]], wm.region[grid.cell_of(150, 50)[::-1]]
    check(r1 >= 0 and r2 >= 0 and r1 != r2 and wm.stats['regions'] == 2, f'the bar splits the lake into two regions ({wm.stats["regions"]})')
    ok, fig = nm.validate(wm)
    check(ok and fig['free_cells_seeing_a_node_pct'] == 100.0, f'graph: nodes on free cells, edges in sight, each region connected ({fig})')
    # route edges keep clear of the pier (x 40, z 0..30): every point of every edge at least radius - one cell away
    P = np.array([wm.node_pos(k) for k in range(len(wm.nodes))])
    near = math.inf
    for a, b in wm.edges:
        s = np.linspace(0, 1, 50)[:, None]
        q = P[a][[0, 2]] * (1 - s) + P[b][[0, 2]] * s
        dz = np.clip(q[:, 1], 0, 30) - q[:, 1]
        near = min(near, float(np.hypot(q[:, 0] - 40, dz).min()))
    check(near >= prof.radius - grid.pitch, f'route edges keep {near:.1f} m from the pier (hull radius {prof.radius} m)')
    # a narrow channel (8 m wide) through a dam: two lakes joined, regions merge; a 4 m channel stays closed
    for width, joined in ((8.0, True), (4.0, False)):
        dam = np.zeros(surface.shape, bool)
        X, Z = grid.centres()
        dam |= (np.abs(X - 100) < 6) & ~((np.abs(Z - 50) < width / 2))
        wm2 = nm.build(surface, np.where((X >= 96) & (X <= 104), 6.0, terrain), grid, prof, dam)
        a, b = wm2.region[grid.cell_of(50, 50)[::-1]], wm2.region[grid.cell_of(150, 50)[::-1]]
        check((a == b) == joined, f'a {width:.0f} m channel {"joins" if joined else "does not join"} the basins (hull 7 m wide)')
    # shore links: infantry cells on the beach (x 200..204, ground 9.0, 1 m under), a dock (object ground 11.5 at
    # z 90 over water near x 60 - terrain there is 6), ground too high (y 14), and ground far from the water
    cells_xz = np.array([[202.4, 30.4], [202.4, 70.4], [60.0, 101.6], [100.0, 130.0], [-15.2, 50.4], [205.6, 50.4]])
    cells_y = np.array([9.0, 9.0, 11.5, 12.0, 14.0, 12.0])
    island = np.zeros(len(cells_xz), np.int64)
    links = nm.shore_links(grid, surface, wm.flags, wm.region, wm.clear, (cells_xz, cells_y, island, np.array([100])), hf, prof)
    kinds = sorted(l['kind'] for l in links)
    lands = {tuple(l['land'][::2]) for l in links}
    check('beach' in kinds and (202.4, 30.4) in lands and (202.4, 70.4) in lands, f'beach cells 1 m under water get links ({kinds})')
    check(all(l['distance_m'] <= prof.radius + prof.reach for l in links), 'every link within the boarding reach')
    check((-15.2, 50.4) not in lands and (100.0, 130.0) not in lands, 'no link from ground too high or too far')
    hf_dock = Flat(lambda x, z: np.where(np.asarray(z) > 100, 6.0, bed(x, z)))
    sur2 = surface.copy()
    links2 = nm.shore_links(grid, sur2, wm.flags, wm.region, wm.clear, (cells_xz[2:3], cells_y[2:3], island[:1], np.array([100])), hf_dock, prof)
    check(len(links2) == 1 and links2[0]['kind'] == 'dock', f'object ground 1.5 m over the water is a dock link ({[l["kind"] for l in links2]})')
    nm.attach(wm, links, [('boat', 'pbl', 'Team1', (50.0, 10.0, 50.0)), ('dockboat', 'pbl', 'Team1', (93.0, 10.0, 50.0)),
                          ('lost', 'pbl', 'Team1', (-15.0, 12.0, 50.0))])
    sp = {s['name']: s for s in wm.spawns}
    check(sp['boat']['cell_free'] and sp['boat']['node'] is not None, 'a spawn on open water is on a free cell with a node')
    check(not sp['dockboat']['cell_free'] and sp['dockboat']['launch_distance_m'] and sp['dockboat']['node'] is not None,
          f'a spawn by the bar is launched {sp["dockboat"]["launch_distance_m"]} m out')
    check(sp['lost']['node'] is None, 'a spawn far on land is not attached')
    routes = nm.route_check(wm)
    check(routes[0]['reached'] >= 1, f'the open-water spawn reaches shore links over the graph ({routes[0]["reached"]})')
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'x.watermesh.json')
        json.dump(nm.to_json(wm), open(p, 'w'), default=nm._plain)
        back = nm.from_json(json.load(open(p)))
        check(np.array_equal(back.flags, wm.flags) and np.array_equal(back.region, wm.region) and back.nodes == wm.nodes
              and back.edges == wm.edges and np.allclose(back.surface[np.isfinite(back.surface)], wm.surface[np.isfinite(wm.surface)]),
              'the side file reads back unchanged')
        nm.picture(wm, os.path.join(d, 'x.png'))
        check(os.path.getsize(os.path.join(d, 'x.png')) > 1000, 'the picture is written')
    # no water: nothing free
    empty = nm.build(np.full(surface.shape, np.nan), terrain, grid, prof, None)
    check(empty.stats['free_cells'] == 0 and empty.nodes == [], 'no water: no cells, no nodes')

    if '--game' in argv:
        import navparity
        root = navparity.opfor_root(None)
        out = os.path.join(root, 'work', 'navwater', '_test')
        g = nm.level_run(root, 'ons_mp_004', prof, out_dir=out, log=lambda *a: None)
        s = g.stats
        check(s['structure_ok'] and s['read_back_identical'], f'ONS_MP_004: structure {s["structure"]}, read back')
        check(s['free_pct_of_water'] > 60 and s['largest_region_pct_of_free'] > 95,
              f'ONS_MP_004: free {s["free_pct_of_water"]} % of the water, largest region {s["largest_region_pct_of_free"]} %')
        check(s['spawns']['attached'] == s['spawns']['sea_spawns'] and len(s['spawns']['regions']) == 1,
              f'ONS_MP_004: every sea-vehicle spawn attached, one region ({s["spawns"]})')
        sh = s.get('shore', {})
        check(sh.get('links', 0) > 20 and sh.get('land_on_infantry_cell_pct') == 100.0,
              f'ONS_MP_004: shore links on the infantry cells ({sh})')
        check(all(r['reached'] > 0 for r in s['routes']), 'ONS_MP_004: every attached spawn reaches shore links')
        import navdomains as nd
        W = nd.level_world(root, 'ons_mp_004', watermesh=os.path.join(out, 'ons_mp_004.pbl.watermesh.json'), log=lambda *a: None)
        tables = W.tables()
        inf = W.domains['infantry']
        rng = np.random.default_rng(3)
        bad = 0
        for _ in range(40):
            a, b = rng.integers(0, inf.n, 2)
            c, _legs = W.plan(('infantry', int(a)), ('infantry', int(b)), tables)
            f = W.plan_flat(('infantry', int(a)), ('infantry', int(b)))
            bad += not ((math.isinf(c) and math.isinf(f)) or abs(c - f) < 1e-6)
        check(bad == 0, f'ONS_MP_004: hierarchical plans cost the same as the joined graph (40 random pairs, {bad} differ)')

    print(f'\n{"all passed" if not failed else f"{failed} failed"}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
