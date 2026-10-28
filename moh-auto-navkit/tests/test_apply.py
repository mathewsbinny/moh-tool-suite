r"""navkit apply with walkability edits, on a level's NavKit build (sp_004_b by default; about 3 minutes):

- no edits: the build is the regression build's (the same sector meshes and route graphs; only the build
  timestamps differ), so the edit plumbing changes nothing by itself;
- an unwalkable square on the main network: no walkable ground is left inside it;
- an unseed on another island: that island is gone;
- a walkable box: no walkable ground is lost inside it;
- the output reads back exactly, and the link edit is applied after the build (or reported with its reason).

  python test_apply.py [level]          needs the level's samples and work\navkit\regress\<level>.hpd
"""
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
from mohcore import navdata, navpath, navtune as nt  # noqa: E402


def cells_in(nav, region, inset=0.0):
    """Walkable cells inside a region; inset (m) shrinks it on every side - drawn cell heights can differ from
    the sampled surface heights the edit tests by a few centimetres (terrain height field vs samples)."""
    x, z, y = nav.cell_xz[:, 0], nav.cell_xz[:, 1], nav.cell_y
    if 'box' in region:
        x0, y0, z0, x1, y1, z1 = region['box']
        return int(((x >= x0 + inset) & (x <= x1 - inset) & (z >= z0 + inset) & (z <= z1 - inset)
                    & (y >= y0 + inset) & (y <= y1 - inset)).sum())
    from matplotlib.path import Path
    lo, hi = region['y']
    m = (y >= lo + inset) & (y <= hi - inset)
    return int(Path(np.asarray(region['polygon'])).contains_points(nav.cell_xz[m], radius=-2 * inset).sum())


def walkable_box(root, lv, nav, g, main_nodes, half=5.0):
    """A box on the edge of the main network (some walkable ground in it already) with the most sampled surfaces
    that are not walkable ground in the build (ground blocked by objects, steep spots): forcing it walkable must
    add ground."""
    import navregress
    import navsample
    import navedits
    samples = navregress.inputs(root, lv)[0]
    s, _ = navsample.load(samples)
    sx, sz, sy = navedits.surface_positions(s)
    best = None
    for n in main_nodes[::max(1, len(main_nodes) // 300)]:
        p = g[n]
        surf = (np.abs(sx - p[0]) <= half) & (np.abs(sz - p[2]) <= half) & (np.abs(sy - p[1]) <= 1.0)
        cells = (np.abs(nav.cell_xz[:, 0] - p[0]) <= half) & (np.abs(nav.cell_xz[:, 1] - p[2]) <= half) & \
            (np.abs(nav.cell_y - p[1]) <= 1.0)
        gain = int(surf.sum()) - int(cells.sum())
        if cells.sum() < 30:                   # forced ground still has to join the network (the step rule)
            continue
        if best is None or gain > best[0]:
            best = (gain, p)
    p = best[1]
    return nt.box(p[0] - half, p[1] - 1.0, p[2] - half, p[0] + half, p[1] + 1.0, p[2] + half), p


def main(argv):
    lv = argv[1] if len(argv) > 1 else 'sp_004_b'
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    pr = navkit.Project(None)
    base = os.path.join(pr.root, 'work', 'navkit', 'regress', f'{lv}.hpd')
    if not os.path.isfile(base):
        print(f'FAIL no regression build of {lv} ({base}): run navregress.py --levels {lv}')
        return 1
    tmp = tempfile.mkdtemp(prefix='apply_test_')
    try:
        nav0 = navdata.load(base)
        g = navpath.ground_nodes(nav0)
        main_nodes = np.nonzero(nav0.node_island == 0)[0]
        c = g[main_nodes[len(main_nodes) // 3]]
        square = nt.polygon([(c[0] - 10, c[2] - 10), (c[0] + 10, c[2] - 10), (c[0] + 10, c[2] + 10), (c[0] - 10, c[2] + 10)],
                            c[1] - 3, c[1] + 3)
        box, d = walkable_box(pr.root, lv, nav0, g, main_nodes)
        other = np.nonzero(nav0.node_island == 1)[0]
        isl_pt = g[other[len(other) // 2]]
        island1_cells = nav0.islands[1].cells

        # 1. no edits
        empty = nt.NavTune.new(f'Levels/{lv}', base)
        p_empty = os.path.join(tmp, 'empty.navtune.json')
        empty.save(p_empty)
        out0 = navkit.apply(pr, lv, p_empty, os.path.join(tmp, 'none.hpd'), log=lambda *a: None)
        a, b = hpdtool.parse(open(base, 'rb').read()), hpdtool.parse(open(out0, 'rb').read())
        same = len(a.sectors) == len(b.sectors) and all(
            sa.id == sb.id and sa.mesh == sb.mesh and sa.nodes == sb.nodes and sa.edges == sb.edges
            for sa, sb in zip(a.sectors, b.sectors))
        check(same, 'no edits: the same sectors, meshes and route graphs as the regression build')

        # 2. edits
        t = nt.NavTune.new(f'Levels/{lv}', base)
        t.add(nt.op('unwalkable', region=square))
        t.add(nt.op('unseed', at=isl_pt))
        t.add(nt.op('walkable', region=box, value=True))
        t.add(nt.op('link-areas', a=c, b=d))
        p = os.path.join(tmp, f'{lv}.navtune.json')
        t.save(p)
        lines = []
        out = navkit.apply(pr, lv, p, os.path.join(tmp, 'edited.hpd'), log=lines.append)
        data = open(out, 'rb').read()
        check(hpdtool.write(hpdtool.parse(data)) == data, 'the edited file reads back exactly')
        nav1 = navdata.load(out)
        before, after = cells_in(nav0, square, 0.25), cells_in(nav1, square, 0.25)
        check(before > 50 and after == 0, f'unwalkable square: {before} walkable cells before, {after} after '
                                          f'(25 cm inside its limits)')
        g1 = navpath.ground_nodes(nav1)
        near = np.hypot(g1[:, 0] - isl_pt[0], g1[:, 2] - isl_pt[2]) < 3.0
        near &= np.abs(g1[:, 1] - isl_pt[1]) < 3.0
        check(not near.any(), f'unseed: island 1 ({island1_cells} cells) is gone - no route node left where it was')
        bb, ba = cells_in(nav0, box), cells_in(nav1, box)
        check(ba > bb, f'walkable box (beside the main network, where sampled ground is not walkable): '
                       f'{bb} walkable cells before, {ba} after')
        check(any('link-areas' in l for l in lines) and any('link / route-edge edit(s) applied' in l for l in lines),
              'the link edit is applied after the build, or reported with the reason it could not be')
        check(any('hand edits' in l for l in lines), 'the build log lists the hand edits')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
