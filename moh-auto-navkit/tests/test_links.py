r"""navlinks.py and navkit apply's graph edits, on NavKit's build of a MOH level (work\navkit\<level>\pathdata.hpd,
the current export, read only; mp_07_koth by default; no rebuild, so quick): a link joins two islands (the path
test finds a route that had none), a one-way route edge goes one way only, removing an edge removes it (both
ways by default), costs are the generator's (the rule every edge of the build follows), every node's first-edge
index still follows the format's rule, an unchanged file stays byte-identical, a link no sector can hold is
reported and not applied, and a graph-only base gets its links on a copy (navkit apply's DICE-base path: MOH
ships no path data, so a copy of NavKit's build stands in for DICE's file).

  python test_links.py [level]
"""
import math
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NAVKIT = os.path.abspath(os.path.join(HERE, '..'))
SUITE = os.path.abspath(os.path.join(NAVKIT, '..'))
sys.path.insert(0, NAVKIT)
sys.path.insert(0, os.path.join(NAVKIT, 'tools'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
import navkit  # noqa: E402
import hpdtool  # noqa: E402
import navlinks  # noqa: E402
from mohcore import navdata, navpath, navtune as nt  # noqa: E402


def first_edge_rule(f):
    """True when every node's `a` is its first outgoing edge + 1 (0 without edges) and edges are sorted by source."""
    for s in f.sectors:
        srcs = [e.src for e in s.edges]
        if srcs != sorted(srcs):
            return False
        first = {}
        for k, e in enumerate(s.edges):
            first.setdefault(e.src, k)
        if any(node.a != (first[n] + 1 if n in first else 0) for n, node in enumerate(s.nodes)):
            return False
    return True


def main(argv):
    lv = argv[1] if len(argv) > 1 else 'mp_07_koth'
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    pr = navkit.Project(None)
    base = os.path.join(pr.root, 'work', 'navkit', lv, 'pathdata.hpd')
    if not os.path.isfile(base):
        print(f'FAIL no NavKit build of {lv} ({base}): run navkit.py build {lv}')
        return 1
    tmp = tempfile.mkdtemp(prefix='links_test_')
    try:
        raw = open(base, 'rb').read()
        f = hpdtool.parse(raw)
        check(first_edge_rule(f), 'the build follows the first-edge rule')
        res = navlinks.apply(f, [], log=lambda *a: None)
        check(res == [] and hpdtool.write(f) == raw, 'no graph edits: the file stays byte-identical')

        nav = navdata.load(base)
        # two islands that come closest within a link's reach, inside one sector: the closest pair between the
        # main network and the nearest island small enough to be cut off
        best = None
        for k in range(1, min(len(nav.islands), 40)):
            gap = navpath.closest_between(nav, 0, k)
            if gap and 1.0 < gap[2] < 12.0 and nav.node_sector[gap[0]] == nav.node_sector[gap[1]]:
                best = gap if best is None or gap[2] < best[2] else best
        check(best is not None, 'an island within 12 m of the main network, in the same sector')
        g = navpath.ground_nodes(nav)
        a, b = g[best[0]], g[best[1]]
        r0 = navpath.route(nav, a, b)
        check(not r0.found, f'before the link: no route ({r0.reason})')

        t = nt.NavTune.new(f'Levels/{lv}', base)
        t.add(nt.op('link-areas', a=a, b=b))
        far_a, far_b = g[0], g[-1]
        t.add(nt.op('link-areas', a=far_a, b=far_b))                       # far apart: no sector holds both
        p = os.path.join(tmp, f'{lv}.navtune.json')
        t.save(p)
        lines = []                                     # (navkit apply on a NavKit base rebuilds: see test_apply.py)
        f2 = hpdtool.parse(raw)
        results = navlinks.apply(f2, t.operations, log=lines.append)
        check(results[0][1] and 'added' in results[0][2], f'the link is applied: {results[0][2]}')
        check(not results[1][1] and 'no sector' in results[1][2], 'a link no sector can hold is reported, not applied')
        check(first_edge_rule(f2), 'after the link: the first-edge rule holds')
        data = hpdtool.write(f2)
        check(hpdtool.write(hpdtool.parse(data)) == data, 'the linked file reads back exactly')
        out = os.path.join(tmp, 'linked.hpd')
        open(out, 'wb').write(data)
        nav2 = navdata.load(out)
        r1 = navpath.route(nav2, a, b)
        check(r1.found, f'after the link: a route ({r1.cost:.1f} m over {len(r1.nodes)} nodes)')
        check(len(nav2.islands) == len(nav.islands) - 1, f'one island fewer ({len(nav.islands)} -> {len(nav2.islands)})')
        # costs: the rule every edge follows (from the stored positions; DICE's files and NavKit's builds alike), for
        # the new edges and the whole build
        def costs_ok(ff, only=None):
            for sec in ff.sectors:
                for e in sec.edges:
                    if only and (sec.id, e.src, e.dst) not in only:
                        continue
                    pa, pb = sec.nodes[e.src].pos, sec.nodes[e.dst].pos
                    if e.tag != (0x80000000 | int(math.floor(math.dist(pa, pb) * 16))):
                        return False
            return True
        old = {(s.id, e.src, e.dst) for s in f.sectors for e in s.edges}
        new_edges = {(s.id, e.src, e.dst) for s in f2.sectors for e in s.edges} - old
        check(len(new_edges) == 2 and costs_ok(f2, new_edges), 'the 2 new edges cost 0x80000000 | floor(stored distance * 16)')
        check(costs_ok(f), 'the same rule as every edge of NavKit\'s build')

        # one-way, then removal
        f3 = hpdtool.parse(data)
        navlinks.apply(f3, [nt.op('route-edge', a=a, b=b, action='remove')], log=lambda *x: None)
        nav3_path = os.path.join(tmp, 'removed.hpd')
        open(nav3_path, 'wb').write(hpdtool.write(f3))
        check(not navpath.route(navdata.load(nav3_path), a, b).found, 'removing the edge (both ways) cuts the link again')
        f4 = hpdtool.parse(raw)
        navlinks.apply(f4, [nt.op('route-edge', a=a, b=b, action='add', **{'two-way': False})], log=lambda *x: None)
        p4 = os.path.join(tmp, 'oneway.hpd')
        open(p4, 'wb').write(hpdtool.write(f4))
        nav4 = navdata.load(p4)
        check(navpath.route(nav4, a, b).found and not navpath.route(nav4, b, a).found, 'a one-way route edge goes one way only')

        # a graph-only base (side file kind "dice"): the link goes into a copy of the base file, no rebuild. MOH has
        # no DICE file: a copy of NavKit's build stands in for it (pr.dice_pathdata, where apply reads it from)
        dice = os.path.join(tmp, 'base', 'pathdata.hpd')
        os.makedirs(os.path.dirname(dice))
        shutil.copyfile(base, dice)
        pr.dice_pathdata = lambda level: dice
        td = nt.NavTune.new(f'Levels/{lv}', dice, kind='dice')
        td.add(nt.op('link-areas', a=a, b=b))                              # the same gap as above
        pd = os.path.join(tmp, 'dice.navtune.json')
        td.save(pd)
        outd = navkit.apply(pr, lv, pd, os.path.join(tmp, 'dice_linked.hpd'), log=lambda *x: None)
        ndl = navdata.load(outd)
        check(navpath.route(ndl, a, b).found and len(ndl.islands) == len(nav.islands) - 1,
              'a graph-only base: the link goes into a copy of the base file, joining two of its islands')
        check(open(dice, 'rb').read() == raw and open(outd, 'rb').read() != raw, 'the base file itself is untouched')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
