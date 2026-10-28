r"""navdata.py and navpath.py on DICE's path data (sp_004_b by default): the islands agree with navparity's count
for the file, a file compared with itself is the same everywhere, a found route's cost is the sum of its
edges' stored costs and each step is an edge of the file, routes are refused between islands (with the gap)
and for points far from the graph, and the cache gives the same data as a fresh read.

  python test_navdata.py [level]
"""
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'moh-auto-navkit'))
from mohcore import navdata, navpath  # noqa: E402
import navkit  # noqa: E402

ISLANDS = {'sp_004_b': 69, 'sp_01': 506, 'sp_009': 3}      # DICE's files, as baseline A counted them


def main(argv):
    lv = argv[1] if len(argv) > 1 else 'sp_004_b'
    path = os.path.join(navkit.find_opfor(None), 'game', 'Output', 'win32', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    tmp = tempfile.mkdtemp(prefix='navdata_test_')
    try:
        fresh = navdata.load(path)
        cached1 = navdata.load(path, cache_dir=tmp)
        cached2 = navdata.load(path, cache_dir=tmp)
        check(np.array_equal(fresh.cell_xz, cached2.cell_xz) and np.array_equal(fresh.cell_area, cached2.cell_area)
              and np.array_equal(fresh.edges, cached2.edges) and len(cached1.islands) == len(cached2.islands),
              'the cache gives the same data as a fresh read')
        nav = fresh
        if lv in ISLANDS:
            check(len(nav.islands) == ISLANDS[lv], f'{len(nav.islands)} islands, as navparity counts them ({ISLANDS[lv]})')
        check(all(a.island >= 0 for a in nav.areas), 'every area is on an island')
        check(sum(i.nodes for i in nav.islands) == len(nav.node_pos), 'every route node is on one island')
        same_a, same_b = navdata.compare_cells(nav, nav)
        check(same_a.all() and same_b.all(), 'a file against itself: every cell the same')

        g = navpath.ground_nodes(nav)
        main_nodes = np.nonzero(nav.node_island == 0)[0]
        a, b = g[main_nodes[0]], g[main_nodes[len(main_nodes) // 2]]
        r = navpath.route(nav, a, b)
        check(r.found and r.start_island == r.end_island == 0, 'a route across the main network is found')
        edge_cost = {(int(s), int(d)): c for (s, d), c in zip(nav.edges, nav.edge_cost)}
        steps = list(zip(r.nodes[:-1], r.nodes[1:]))
        check(all(st in edge_cost for st in steps), 'every step of the route is an edge of the file')
        check(abs(sum(edge_cost[st] for st in steps) - r.cost) < 1e-6, f'its cost ({r.cost:.1f} m) is the sum of the stored edge costs')
        check(r.cost >= r.straight * 0.95, 'and it is not shorter than the straight line')
        back = navpath.route(nav, b, a)
        check(back.found, 'the way back is found too')
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
