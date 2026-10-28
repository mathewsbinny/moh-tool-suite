r"""NavKit's shadow-island rule (navislands.py, SHADOW_ISLANDS) on route graphs built here (no game data): a small route
island next to a large one loses its edges (and, through navbuild.finalize_graph, its nodes); one with an anchor
(spawn / AI order on its floor), one far away or far above, and a large one stay; border ids join sectors; with
SHADOW_OBJECTS_ONLY a pocket on terrain stays (note_nodes records the terrain nodes); the switch off leaves the graphs
as they were.

  python test_navislands.py
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navbuild  # noqa: E402
import navislands  # noqa: E402

Y = navislands.Y_OFFSET


def chain(ids, xs, y, z):
    """pts and pairs of a straight chain of nodes (ids) at x positions xs, ground height y."""
    pts = [(i, (x, y + Y, z), None, 0, 0) for i, x in zip(ids, xs)]
    pairs = [(a, a + 1, abs(xs[a + 1] - xs[a])) for a in range(len(ids) - 1)]
    return pts, pairs


def sector(parts, sid=0x0101, origin=(0.0, 0.0)):
    pts, pairs = [], []
    for p, q in parts:
        off = len(pts)
        pts += p
        pairs += [(a + off, b + off, d) for a, b, d in q]
    return (sid, (0, 100, 0, 10, 0, 100), b'', pts, pairs, origin)


def kept_ids(graphs):
    return {g[3][i][0] for g in graphs for a, b, _d in g[4] for i in (a, b)}


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    navislands.SHADOW_ISLANDS = True
    main_ = chain(list(range(1, 31)), [2.0 * k for k in range(30)], 10.0, 5.0)          # 30 nodes, x 0..58, z 5
    speck = chain([101, 102], [20.0, 21.0], 10.8, 8.0)                                   # 0.8 m up, 3 m beside
    far = chain([201, 202], [20.0, 21.0], 10.0, 40.0)                                    # 35 m away
    roof = chain([301, 302], [30.0, 31.0], 15.5, 6.0)                                    # 5.5 m up
    g = [sector([main_, speck, far, roof])]
    out = navislands.prune(g)
    k = kept_ids(out)
    check(101 not in k and 102 not in k, 'a 2-node pocket 0.8 m above the main island, 3 m beside it, loses its edges')
    check(201 in k and 301 in k, 'a pocket 35 m away and one 5.5 m above stay')
    check(all(i in k for i in range(1, 31)), 'the main island stays whole')
    check(navislands.STATS.get('islands') == 1 and navislands.STATS.get('nodes') == 2, 'stats: 1 island, 2 nodes')

    out = navislands.prune(g, anchors=[(20.5, 10.8, 8.0)])
    check(101 in kept_ids(out), 'an anchor (spawn / order) on the pocket floor keeps it')
    out = navislands.prune(g, anchors=[(20.5, 10.0, 5.0)])
    check(101 not in kept_ids(out), 'an anchor on the main floor below does not keep the pocket')

    big = chain(list(range(401, 401 + navislands.SHADOW_MIN_NODES)), [0.5 * q for q in range(navislands.SHADOW_MIN_NODES)], 10.8, 9.0)
    out = navislands.prune([sector([main_, big])])
    check(401 in kept_ids(out), f'an island of {navislands.SHADOW_MIN_NODES} nodes (SHADOW_MIN_NODES) stays')

    # two sectors: the main island crosses through a border id; the pocket spans both through another border id
    b = 0x80000000
    a1 = chain(list(range(1, 16)) + [b | 1], [2.0 * q for q in range(16)], 10.0, 5.0)
    a2 = chain([b | 1] + list(range(16, 31)), [30.0 + 2.0 * q for q in range(16)], 10.0, 5.0)
    p1 = chain([501, b | 2], [28.0, 30.0], 11.0, 7.0)
    p2 = chain([b | 2, 502], [30.0, 32.0], 11.0, 7.0)
    two = [sector([a1, p1], 0x0101), sector([a2, p2], 0x0201, (100.0, 0.0))]
    out = navislands.prune(two)
    k = kept_ids(out)
    check(not ({501, 502, b | 2} & k), 'a pocket spanning two sectors (joined by a border id) is dropped in both')
    check(b | 1 in k and 1 in k and 30 in k, 'the main island joined through a border id stays')
    root, size, _pos = navislands.components(two)
    check(sorted(size.values()) == [3, 31], 'border ids count once: islands of 31 and 3 nodes')

    # finalize: the pocket's nodes are gone from the sector's node table
    linked = kept_ids(out)
    nodes, edges = navbuild.finalize_graph(out[0][3], out[0][4], linked, out[0][5])
    ids = {n.id for n in nodes}
    check(501 not in ids and (b | 2) not in ids and 1 in ids, 'finalize_graph drops the pocket nodes (no edges)')
    check(all(e.src < len(nodes) and e.dst < len(nodes) for e in edges), 'edges index the remaining nodes')

    # SHADOW_OBJECTS_ONLY: a pocket on terrain (an island DICE seeds as well) stays; note_nodes records terrain nodes
    from types import SimpleNamespace
    tpocket = chain([601, 602, 603], [10.0, 11.0, 12.0], 10.4, 9.0)
    lvl = SimpleNamespace(terrain=[False, True, True, True])
    navislands.TERRAIN_IDS.clear()
    navislands.note_nodes(lvl, [(601, (0, 0, 0), 1, 0, 0), (602, (0, 0, 0), 2, 0, 0), (603, (0, 0, 0), 3, 0, 0),
                                (101, (0, 0, 0), 0, 0, 0), (102, (0, 0, 0), None, 0, 0)])
    check(navislands.TERRAIN_IDS == {601, 602, 603}, 'note_nodes: nodes on terrain surfaces recorded, object / unknown not')
    gt = [sector([main_, speck, tpocket])]
    navislands.SHADOW_OBJECTS_ONLY = True
    k = kept_ids(navislands.prune(gt))
    check(601 in k and 101 not in k, 'objects only: the terrain pocket stays, the object pocket goes')
    navislands.SHADOW_OBJECTS_ONLY = False
    k = kept_ids(navislands.prune(gt))
    check(601 not in k and 101 not in k, 'objects only off: both pockets go')
    navislands.SHADOW_OBJECTS_ONLY = True
    navislands.TERRAIN_IDS.clear()

    navislands.SHADOW_ISLANDS = False
    out = navislands.prune(g)
    check(out is g and 101 in kept_ids(out), 'SHADOW_ISLANDS off: graphs unchanged')
    navislands.SHADOW_ISLANDS = True
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
