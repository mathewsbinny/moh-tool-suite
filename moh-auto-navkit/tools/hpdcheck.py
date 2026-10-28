"""NavKit: structural checks of a pathdata.hpd - what DICE's files always satisfy and the game relies on.

  hpdcheck.py <pathdata.hpd> [...]          exit code 0 when every file passes, 1 otherwise

Checks, per file:
  closed boundaries   every area's outline, portals and border portals meet in pairs at every end point (DICE: no
                      open area; an open one misleads point-in-area tests - navbuild.BRIDGE_UNION)
  edges               every edge's two node indices exist in its sector
  node order          each sector's nodes sorted by unsigned id, border nodes (id flag 80000000h) after the others
                      (the game binary-searches these arrays - navbuild.SORT_NODES_BY_ID)
  zero-length edges   no edge between two different nodes at one position (navbuild.DEDUP_GRAPH_NODES)
  detail features     every D line has 2+ points; every F polygon is closed (first point repeated) with a horizontal
                      unit direction (infantry files: +-x / +-z; vehicle files also diagonals) - navbuild.DETAIL_FEATURES
Every check holds on DICE's 15 files (13 campaign levels, ONS_MP_002 / 004; 2026-10-23). Not checked: the owner bit
of border nodes (field c bit 16) - DICE's files have border nodes with no owner copy and with two or more.
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hpdtool  # noqa: E402
import kymesh  # noqa: E402

BORDER = 0x80000000


def detail_problem(a):
    """What is wrong with an area's D lines / F cells (DICE's conventions), or None."""
    if any(len(line) < 2 for line in a.d_lines):
        return 'a D line with fewer than 2 points'
    for c in a.cells:
        dx, dy, dz = c.direction
        if abs(dy) > 1e-6 or abs(dx * dx + dz * dz - 1) > 1e-4:
            return f'an F record with direction {c.direction}'
        if len(c.points) < 4 or tuple(c.points[0]) != tuple(c.points[-1]):
            return 'an F polygon not closed'
    return None


def check(path):
    """{check name: [problem descriptions]} (empty lists: passed)."""
    f = hpdtool.parse(open(path, 'rb').read())
    out = {'closed boundaries': [], 'edges': [], 'node order': [], 'zero-length edges': [], 'detail features': []}
    for s in f.sectors:
        m = kymesh.parse_mesh(s.mesh)
        for ai, a in enumerate(m.areas):
            deg = Counter()
            segs = [(l[k], l[k + 1]) for l in a.outline for k in range(len(l) - 1)]
            segs += [(b.p0, b.p1) for b in a.portals] + [(c.p0, c.p1) for c in a.border_portals]
            for p, q in segs:
                deg[(round(p[0], 2), round(p[2], 2))] += 1
                deg[(round(q[0], 2), round(q[2], 2))] += 1
            odd = [k for k, v in deg.items() if v % 2]
            if odd:
                out['closed boundaries'].append(f'sector {s.id:04x} area {ai}: {len(odd)} open end(s), e.g. {odd[0]}')
            bad = detail_problem(a)
            if bad:
                out['detail features'].append(f'sector {s.id:04x} area {ai}: {bad}')
        n = len(s.nodes)
        for e in s.edges:
            if not (0 <= e.src < n and 0 <= e.dst < n):
                out['edges'].append(f'sector {s.id:04x}: edge {e.src} -> {e.dst} of {n} nodes')
                continue
            a, b = s.nodes[e.src], s.nodes[e.dst]
            if a.id != b.id and tuple(a.pos) == tuple(b.pos):
                out['zero-length edges'].append(f'sector {s.id:04x}: nodes {a.id:08x} and {b.id:08x} at {tuple(round(v, 2) for v in a.pos)}')
        ids = [nd.id & 0xFFFFFFFF for nd in s.nodes]
        key = [((i & BORDER) != 0, i) for i in ids]
        if key != sorted(key):
            out['node order'].append(f'sector {s.id:04x}: nodes not sorted by id with border nodes last')
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    bad = 0
    for path in argv[1:]:
        res = check(path)
        fails = {k: v for k, v in res.items() if v}
        bad += bool(fails)
        print(f'{"ok  " if not fails else "FAIL"} {os.path.basename(path)}' +
              ''.join(f'\n       {k}: {len(v)} - {v[0]}' for k, v in fails.items()))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
