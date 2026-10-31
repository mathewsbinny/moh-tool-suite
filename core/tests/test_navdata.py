r"""navdata.py and navpath.py on NavKit's build of a MOH level (work\navkit\<level>\pathdata.hpd, the current export;
mp_07_koth by default - MOH ships no path data of its own): the islands agree with navparity's count (ordercheck's
route groups) for the file and with the count recorded for the export, a file compared with itself is the same
everywhere, a route across the main network (between two nodes that reach each other) is found both ways, its
cost is the sum of its edges' stored costs and each step is an edge of the file, a drop link (a one-way edge) is
not walked back up, routes are refused between islands (with the gap) and for points far from the graph, and
the cache gives the same data as a fresh read.

Without game data (run first; --quick runs only these): a cache entry made from a relative path, read from another
folder, gives this caller's absolute path; a truncated .npz cells entry is rebuilt, and no temporary file is left.

  python test_navdata.py [level] [--quick]
"""
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'moh-auto-navkit'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'moh-auto-navkit', 'tools'))
from mohcore import navdata, navpath  # noqa: E402
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


def main(argv):
    lv = next((a for a in argv[1:] if not a.startswith('--')), 'mp_07_koth')
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    cache_checks(check)
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
