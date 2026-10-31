r"""navdata.py and navpath.py on NavKit's build of a MOH level (work\navkit\<level>\pathdata.hpd, the current export;
mp_07_koth by default - MOH ships no path data of its own): the islands agree with navparity's count (ordercheck's
route groups) for the file and with the count recorded for the export, a file compared with itself is the same
everywhere, a route across the main network (between two nodes that reach each other) is found both ways, its
cost is the sum of its edges' stored costs and each step is an edge of the file, a drop link (a one-way edge) is
not walked back up, routes are refused between islands (with the gap) and for points far from the graph, and
the cache gives the same data as a fresh read.

Without game data (run first; --quick runs only these): a cache entry made from a relative path, read from another
folder, gives this caller's absolute path; a truncated .npz cells entry is rebuilt, and no temporary file is left.
And path data written here (hpdtool, kymesh: three sectors, a portal, a border node shared by two sectors, a
detailed zone, a roof reached by a ladder link, an area apart, a sector without route nodes): the areas and their
boundary pieces, the cells and their ground (a height field's on terrain), the route graph (one node per id, costs
as stored), ladder links and ladder ground, islands and each area's island, the summary; compare_cells (one floor
each side pairs, stacked floors within 4 m, missing areas); the cache per file and height field content, a damaged
entry rebuilt, a changed file read again; python -m mohcore.navdata.

  python test_navdata.py [level] [--quick]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'moh-auto-navkit'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'moh-auto-navkit', 'tools'))
from mohcore import navdata, navpath  # noqa: E402
from mohcore.terrain import Heightfield  # noqa: E402
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import navkit  # noqa: E402
import ordercheck  # noqa: E402

# NavKit's exports as of 2026-10-30 (all 26 MP levels built offline, drop links within a run's reach). A new export of
# one of these levels can change its count: check the islands in the Navigation Studio, then record the new one.
ISLANDS = {'mp_07_koth': 8, 'mp_03_elimination': 14}


def reach(nav, k, back=False):
    """The nodes reachable from node k along the edges' directions (back: the nodes that reach k)."""
    adj = [[] for _ in range(len(nav.node_pos))]
    for s, d in nav.edges.tolist():
        adj[d if back else s].append(s if back else d)
    seen, stack = {k}, [k]
    while stack:
        for v in adj[stack.pop()]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def cache_checks(check):
    """The cache without game data (2026-10-31): _load stands in for the file's reader."""
    tmp = tempfile.mkdtemp(prefix='navdata_cache_test_')
    cwd, real_load = os.getcwd(), navdata._load
    try:
        a, b = os.path.join(tmp, 'a'), os.path.join(tmp, 'b')
        os.makedirs(os.path.join(a, 'lv'))
        os.makedirs(b)
        hpd = os.path.join(a, 'lv', 'pathdata.hpd')
        open(hpd, 'wb').write(b'stand-in')
        cache = os.path.join(tmp, 'cache')
        e = np.zeros(0)
        navdata._load = lambda path, hf, cd: navdata.NavData(path, [], np.zeros((0, 2)), e, e, np.zeros((0, 3)), e,
                                                             np.zeros((0, 2), np.int64), e, e, [])
        os.chdir(a)
        first = navdata.load(os.path.join('lv', 'pathdata.hpd'), cache_dir=cache)
        os.chdir(b)
        second = navdata.load(os.path.join('..', 'a', 'lv', 'pathdata.hpd'), cache_dir=cache)
        check(os.path.isabs(first.path) and os.path.samefile(second.path, hpd) and os.path.isabs(second.path),
              'a cache entry made from a relative path, read from another folder: the path is absolute and resolves')
        navdata._load = real_load

        class NoSectors:
            sectors = []
        cp = os.path.join(cache, navdata._key(hpd) + '.npz')
        navdata._cells(NoSectors(), None, cache, hpd)
        good = open(cp, 'rb').read()
        open(cp, 'wb').write(good[:len(good) // 2])                      # an interrupted write of an old version
        try:
            xz, y, area, est = navdata._cells(NoSectors(), None, cache, hpd)
            ok = len(xz) == 0 and open(cp, 'rb').read() == good
        except Exception as ex:  # noqa: BLE001 - test harness: any failure is recorded in the check
            ok = f'{type(ex).__name__}: {ex}'
        check(ok is True, f'a truncated cells cache entry is rebuilt, not an error ({ok})')
        check(not [f for f in os.listdir(cache) if f.endswith('.tmp')], 'cache entries leave no temporary file')
    finally:
        navdata._load = real_load
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


Y = navdata.Y_OFFSET


def square(x0, z0, x1, z1, y, portal=None):
    """An area's boundary on the ground at y: (outline polylines, B portals, C border portals). portal: a target
    area index (a B portal on the east side) or 'border' (a C portal there); None: the outline is a ring."""
    a, b, c, d = (x1, y + Y, z0), (x0, y + Y, z0), (x0, y + Y, z1), (x1, y + Y, z1)
    if portal is None:
        return [[a, b, c, d, a]], [], []
    if portal == 'border':
        return [[a, b, c, d]], [], [kymesh.BorderPortal(d, a, (1.0, 0.0, 0.0))]
    return [[a, b, c, d]], [kymesh.Portal(d, a, portal)], []


def sector(sid, areas, nodes, edges):
    """A path-data sector: areas [(square(...), layer, pitch)], nodes [(id, (x, y, z))], edges [(src, dst)] (indices
    into nodes; cost: the 3-D distance as the build stores it)."""
    ka = []
    for (outline, portals, borders), layer, pitch in areas:
        ys = [p[1] - Y for line in outline for p in line]
        ka.append(kymesh.Area(sid, sid >> 8, sid & 0xFF, layer, 0.4, pitch, max(ys), min(ys), portals, borders, [],
                              outline, []))
    mesh = kymesh.Mesh(2, 0, 0, sid, 0.025, 0.8, (0.0, 100.0, 0.0, 100.0), 1, 1, 0.4,
                       [[kymesh.TileItem(k, a.min_y, a.max_y) for k, a in enumerate(ka)]], ka, [], b'\0' * 4)
    hn = [hpdtool.Node(i, p, 0x80000000, 0.0, 0, 0, 0) for i, p in nodes]
    he = [hpdtool.Edge(0x80000000 | int(np.linalg.norm(np.subtract(nodes[d][1], nodes[s][1])) * 16), s, d)
          for s, d in edges]
    return hpdtool.Sector(sid, (0.0, 100.0, -10.0, 50.0, 0.0, 100.0), 0, 100007, 0, hn, he, kymesh.write_mesh(mesh))


def made_up(roof=10.0, east=True, side_y=0.0):
    """Three sectors on flat ground. Sector 0x0000: areas 0 (0..8 m) and 1 (8..16 m, a 0.2 m detailed zone) joined by
    a portal, area 2 a roof (0..4 m, <roof> m up) reached by a ladder link from area 0's node. Sector 0x0100: area 3
    (16..24 m, <side_y> m up) behind the sector border - its border node is sector 0's node 1 again (one id) - and
    area 4 apart (40..44 m), its node joined to nothing. Sector 0x0200: area 5, without route nodes. east=False:
    without area 1."""
    border = 0x80000001
    s0_areas = [(square(0, 0, 8, 8, 0.0, portal=1 if east else None), 0, 0.8)]
    if east:
        s0_areas.append((square(8, 0, 16, 8, 0.0, portal='border'), 0, 0.2))
    s0_areas.append((square(0, 0, 4, 4, roof), 1, 0.8))
    s0 = sector(0x0000, s0_areas, [(0, (4.0, Y, 4.0)), (border, (12.0, Y, 4.0)), (2, (2.0, roof + Y, 2.0))],
                [(0, 1), (1, 0), (0, 2), (2, 0)])
    s1 = sector(0x0100, [(square(16, 0, 24, 8, side_y), 0, 0.8), (square(40, 40, 44, 44, 0.0), 0, 0.8)],
                [(border, (12.0, Y, 4.0)), (3, (20.0, side_y + Y, 4.0)), (4, (42.0, Y, 42.0))], [(0, 1), (1, 0)])
    s2 = sector(0x0200, [(square(60, 60, 62, 62, 0.0), 0, 0.8)], [], [])
    return hpdtool.write(hpdtool.HpdFile(tuple([0] * hpdtool.HEADER_WORDS), [s0, s1, s2]))


def flat_heightfield(h):
    """A terrain.Heightfield at height h over -64..64 m."""
    return Heightfield(np.array([-64.0, h - 1, -64.0]), np.array([64.0, h + 1, 64.0]), 8,
                       np.full((8, 8), 32768, np.uint16), np.full((8, 8), h, np.float32))


def built_checks(check):
    """navdata.load, summary and compare_cells on path data written here (hpdtool, kymesh), and the cache."""
    tmp = tempfile.mkdtemp(prefix='navdata_built_test_')
    try:
        path = os.path.join(tmp, 'pathdata.hpd')
        open(path, 'wb').write(made_up())
        nav = navdata.load(path)
        a = nav.areas
        check([(x.sector, x.index, x.tile, x.layer) for x in a] == [(0, 0, (0, 0), 0), (0, 1, (0, 0), 0), (0, 2, (0, 0), 1),
                                                                    (0x100, 0, (1, 0), 0), (0x100, 1, (1, 0), 0),
                                                                    (0x200, 0, (2, 0), 0)] and nav.sectors == [0, 0x100, 0x200],
              'areas: per sector in order, with their index, tile and layer; the sectors')
        check([x.detailed for x in a] == [False, True, False, False, False, False] and a[2].min_y == a[2].max_y == 10,
              'a pitch under 0.5 m is a detailed zone; the height range as stored')
        check(len(a[0].outline) == 1 and a[0].outline[0].dtype == np.float32 and len(a[0].outline[0]) == 4
              and [p[2] for p in a[0].portals] == [1] and np.allclose(a[0].portals[0][0], (8, Y, 8)) and not a[0].borders
              and len(a[1].borders) == 1 and len(a[5].outline[0]) == 5,
              'boundary pieces: outline polylines (float32), portals with their target area, border portals')
        cells = [x.cells for x in a]
        check(cells == [100, 100, 25, 100, 25, 9] and len(nav.cell_xz) == sum(cells)
              and np.bincount(nav.cell_area).tolist() == cells,
              f'cells on the 0.8 m grid inside each area, each with its area ({cells})')
        on2 = nav.cell_area == 2
        check(np.allclose(nav.cell_y[on2], 10, atol=1e-4) and np.allclose(nav.cell_y[~on2], 0, atol=1e-4)
              and nav.cell_xz[on2].min() >= 0 and nav.cell_xz[on2].max() < 4,
              f'cell ground heights from the boundary (stored {Y:g} m above the ground)')
        check(len(nav.node_pos) == 5 and nav.node_sector.tolist() == [0, 0, 0, 0x100, 0x100] and len(nav.edges) == 6,
              'route nodes: a border node in two sectors is one node (in the sector seen first)')
        stored = np.floor(np.linalg.norm(nav.node_pos[nav.edges[:, 1]] - nav.node_pos[nav.edges[:, 0]], axis=1) * 16) / 16
        check(nav.edges.tolist() == [[0, 1], [1, 0], [0, 2], [2, 0], [1, 3], [3, 1]] and np.allclose(nav.edge_cost, stored),
              "edges as node index pairs; costs in metres as stored (the tag's floor(distance * 16) / 16)")
        check(nav.ladder_edge is not None and nav.ladder_edge.tolist() == [False, False, True, True, False, False],
              'ladder links: an edge climbing over 2.5 m, steeper than 1.5 m per metre')
        isl = [(i.nodes, i.cells) for i in nav.islands]
        check(isl == [(4, 325), (1, 25)] and nav.node_island.tolist() == [0, 0, 0, 0, 1]
              and np.allclose(nav.islands[1].centre, (42, Y, 42))
              and np.allclose(nav.islands[0].centre, nav.node_pos[:4].mean(0)),
              f'islands: connected parts of the graph, largest first, with their nodes, cells and centre ({isl})')
        check([x.island for x in a] == [0, 0, 0, 0, 1, -1],
              "each area on its nearest node's island (across a sector border too); none in a sector without nodes")
        check([x.ladder for x in a] == [False, False, True, False, False, False],
              "ladder ground: an area reached only through a ladder link (the roof), not the island's walking part")
        s = navdata.summary(nav)
        check(s == {'areas': 6, 'detailed areas': 1, 'cells': 359, 'nodes': 5, 'edges': 6, 'islands': 2,
                    'large islands (25+ nodes)': 0, 'sectors': 3, 'ladder links': 1, 'ladder ground cells': 25},
              f'summary ({s})')
        low = os.path.join(tmp, 'low_roof.hpd')
        open(low, 'wb').write(made_up(roof=2.0))
        nl = navdata.load(low)
        check(not nl.ladder_edge.any() and not any(x.ladder for x in nl.areas) and len(nl.islands) == 2,
              'a 2 m climb is no ladder link: no ladder ground')

        # the terrain height field: its heights where a cell lies on terrain (within 1.5 m of the estimate)
        nh = navdata.load(path, heightfield=flat_heightfield(0.5))
        check(np.allclose(nh.cell_y[~on2], 0.5) and np.allclose(nh.cell_y[on2], 10),
              "with a height field: the terrain's heights on the ground, the boundary's on the roof")

        # compare_cells: the same file; floors moved; an area missing; nothing to compare with
        same_a, same_b = navdata.compare_cells(nav, nav)
        check(same_a.all() and same_b.all() and len(same_a) == len(nav.cell_xz), 'a file against itself: every cell the same')
        other = os.path.join(tmp, 'other.hpd')
        open(other, 'wb').write(made_up(roof=16.0, east=False, side_y=3.0))
        nb = navdata.load(other)
        ca, cb = navdata.compare_cells(nav, nb)
        every = {k: bool(ca[nav.cell_area == k].all()) for k in range(6)}
        none = {k: not ca[nav.cell_area == k].any() for k in range(6)}
        check(every[0] and every[3] and every[4] and every[5] and none[1] and none[2],
              'cells: the same where both files have ground (one floor each side pairs whatever its height: area 3 '
              f'3 m up), not where an area is missing or a stacked floor is over 4 m off (the roof 10 m -> 16 m) ({every})')
        check(len(cb) == len(nb.cell_xz) and cb[nb.cell_area == 0].all() and not cb[nb.cell_area == 1].any(),
              "and the same from the other file's side (its roof unmatched)")
        e = np.zeros(0)
        empty = navdata.NavData('', [], np.zeros((0, 2)), e, np.zeros(0, np.int32), np.zeros((0, 3)), e,
                                np.zeros((0, 2), np.int64), e, np.zeros(0, np.int32), [])
        ea, eb = navdata.compare_cells(nav, empty)
        check(not ea.any() and len(ea) == len(nav.cell_xz) and len(eb) == 0, 'against a file without cells: nothing the same')

        # the cache: the whole result per file and height source, the cells per file
        cache = os.path.join(tmp, 'cache')
        c1 = navdata.load(path, cache_dir=cache)
        pkl = [f for f in os.listdir(cache) if f.endswith('.navdata.pkl')]
        c2 = navdata.load(path, cache_dir=cache)
        check(len(pkl) == 1 and np.array_equal(c1.cell_xz, nav.cell_xz) and np.array_equal(c2.edges, nav.edges)
              and [x.island for x in c2.areas] == [x.island for x in nav.areas] and c2.path == os.path.abspath(path),
              'the cache gives the same data as a fresh read')
        h1 = navdata.load(path, heightfield=flat_heightfield(0.5), cache_dir=cache)
        h2 = navdata.load(path, heightfield=flat_heightfield(0.5), cache_dir=cache)
        h3 = navdata.load(path, heightfield=flat_heightfield(0.8), cache_dir=cache)
        pkl = [f for f in os.listdir(cache) if f.endswith('.navdata.pkl')]
        check(np.allclose(h1.cell_y[~on2], 0.5) and np.allclose(h2.cell_y, h1.cell_y) and np.allclose(h3.cell_y[~on2], 0.8)
              and len(pkl) == 3 and len([f for f in os.listdir(cache) if f.endswith('.npz')]) == 1,
              'one entry per height field content (a new object with the same heights hits it), the cells shared')
        hf = flat_heightfield(0.5)
        check(navdata._terrain_id(hf) == navdata._terrain_id(flat_heightfield(0.5)) != navdata._terrain_id(None)
              and getattr(hf, '_navdata_id', None) == navdata._terrain_id(hf),
              "a height field's fingerprint: by content, kept on the object")
        for f in pkl:
            open(os.path.join(cache, f), 'wb').write(b'damaged')
        c3 = navdata.load(path, cache_dir=cache)
        check(np.array_equal(c3.cell_area, nav.cell_area) and len(c3.islands) == 2
              and open(os.path.join(cache, navdata._key(path) + '.navdata.pkl'), 'rb').read() != b'damaged',
              'a damaged cache entry is rebuilt and written again')
        before = navdata._key(path)
        open(path, 'wb').write(made_up(east=False))
        os.utime(path, ns=(1, 1))
        c4 = navdata.load(path, cache_dir=cache)
        check(navdata._key(path) != before and len(c4.areas) == 5,
              'a changed file is read again (the cache is keyed by its size and date)')
        check(not [f for f in os.listdir(cache) if f.endswith('.tmp')], 'cache entries leave no temporary file')
        r = subprocess.run([sys.executable, '-m', 'mohcore.navdata', other], capture_output=True, text=True,
                           cwd=os.path.join(HERE, '..'))
        check(r.returncode == 0 and json.loads(r.stdout or 'null') == navdata.summary(nb),
              f'python -m mohcore.navdata: the summary ({r.stderr.strip()[-200:]})')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    lv = next((a for a in argv[1:] if not a.startswith('--')), 'mp_07_koth')
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    cache_checks(check)
    built_checks(check)
    if '--quick' in argv:
        print('all passed' if not failed else f'{failed} check(s) failed')
        return 1 if failed else 0
    path = os.path.join(navkit.find_opfor(None), 'work', 'navkit', lv, 'pathdata.hpd')
    if not os.path.isfile(path):
        print(f'FAIL no NavKit build of {lv} ({path}): run navkit.py build {lv}')
        return 1
    tmp = tempfile.mkdtemp(prefix='navdata_test_')
    try:
        fresh = navdata.load(path)
        cached1 = navdata.load(path, cache_dir=tmp)
        cached2 = navdata.load(path, cache_dir=tmp)
        check(np.array_equal(fresh.cell_xz, cached2.cell_xz) and np.array_equal(fresh.cell_area, cached2.cell_area)
              and np.array_equal(fresh.edges, cached2.edges) and len(cached1.islands) == len(cached2.islands),
              'the cache gives the same data as a fresh read')
        nav = fresh
        groups = len(ordercheck.PathData(path).group_size)
        check(len(nav.islands) == groups, f'{len(nav.islands)} islands, as navparity counts them ({groups})')
        if lv in ISLANDS:
            check(len(nav.islands) == ISLANDS[lv], f'and as recorded for the export ({ISLANDS[lv]})')
        check(all(a.island >= 0 for a in nav.areas), 'every area is on an island')
        check(sum(i.nodes for i in nav.islands) == len(nav.node_pos), 'every route node is on one island')
        same_a, same_b = navdata.compare_cells(nav, nav)
        check(same_a.all() and same_b.all(), 'a file against itself: every cell the same')

        g = navpath.ground_nodes(nav)
        main_nodes = np.nonzero(nav.node_island == 0)[0]
        # NavKit's drop links are one-way edges: an island is joined, but not every node of it reaches every other.
        # The two ends: of a few main-network nodes, the one with the most nodes it reaches and is reached from,
        # and of those the farthest from it
        both = max((reach(nav, int(k)) & reach(nav, int(k), back=True), int(k))
                   for k in main_nodes[::max(1, len(main_nodes) // 8)])
        ka = both[1]
        kb = max(both[0], key=lambda k: float(np.hypot(*(nav.node_pos[k] - nav.node_pos[ka])[[0, 2]])))
        a, b = g[ka], g[kb]
        r = navpath.route(nav, a, b)
        check(r.found and r.start_island == r.end_island == 0, 'a route across the main network is found')
        edge_cost = {(int(s), int(d)): c for (s, d), c in zip(nav.edges, nav.edge_cost)}
        steps = list(zip(r.nodes[:-1], r.nodes[1:]))
        check(all(st in edge_cost for st in steps), 'every step of the route is an edge of the file')
        check(abs(sum(edge_cost[st] for st in steps) - r.cost) < 1e-6, f'its cost ({r.cost:.1f} m) is the sum of the stored edge costs')
        check(r.cost >= r.straight * 0.95, 'and it is not shorter than the straight line')
        back = navpath.route(nav, b, a)
        check(back.found, 'the way back is found too')
        pairs = set(map(tuple, nav.edges.tolist()))
        drop = next(((s, d) for s, d in sorted(pairs) if (d, s) not in pairs and s not in reach(nav, d)), None)
        if drop is not None:
            s, d = drop
            r1 = navpath.route(nav, g[d], g[s])
            check(not r1.found and 'directions' in r1.reason, f'a drop link is not walked back up ({r1.reason})')
        if len(nav.islands) > 1:
            other = np.nonzero(nav.node_island == 1)[0]
            r2 = navpath.route(nav, a, g[other[0]])
            check(not r2.found and r2.gap is not None and r2.gap[2] > 0, f'no route to another island; the gap is given ({r2.reason})')
        r3 = navpath.route(nav, a, a + np.array([5000.0, 0, 5000.0]))
        check(not r3.found and 'no route node' in r3.reason, 'a point far from the graph is refused')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
