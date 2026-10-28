r"""NavKit's bound to the reference extent (navbuild.outside_bounds, CLIP_TO_REFERENCE; navregress.reference_bounds) and
the edge-sector rule switch, without game data: surfaces outside the box (every floor of a column) are marked, the
box edges are inclusive; reference_bounds takes outlines and portals at every pitch and leaves out border portals,
and gives None for a level without path data; the defaults.

  python test_reference_clip.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navbuild  # noqa: E402
import navregress  # noqa: E402
import kymesh  # noqa: E402


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    # 5 x 3 columns at 0.8 m from (0, 0); column (2, 1) has two floors
    nx, nz = 5, 3
    counts = [2 if (i, j) == (2, 1) else 1 for j in range(nz) for i in range(nx)]
    s = SimpleNamespace(nx=nx, nz=nz, pitch=0.8, min_x=0.0, min_z=0.0, start=np.concatenate([[0], np.cumsum(counts)]))
    out = navbuild.outside_bounds(s, (0.8, 0.8, 2.4, 0.8))      # keeps x 0.8..2.4, z 0.8: columns i 1..3, j 1
    col = np.repeat(np.arange(nx * nz), counts)
    kept = sorted({(c % nx, c // nx) for c in col[~out].tolist()})
    check(kept == [(1, 1), (2, 1), (3, 1)], f'inside: columns 1..3 of row 1, edges inclusive ({kept})')
    check(int((~out).sum()) == 4, 'both floors of a column inside are kept')
    check(navbuild.outside_bounds(s, (-1, -1, 99, 99)).sum() == 0, 'a box around everything leaves nothing out')

    # reference_bounds on a file faked through navparity / hpdtool
    area = SimpleNamespace(outline=[[(1.0, 0.0, 2.0), (3.0, 0.0, 2.0)]], portals=[SimpleNamespace(p0=(0.5, 0, 2.5), p1=(0.5, 0, 4.0))],
                           border_portals=[SimpleNamespace(p0=(-50.0, 0, -50.0), p1=(-50.0, 0, 60.0))])
    detailed = SimpleNamespace(outline=[[(3.0, 0.0, 2.0), (3.2, 0.0, 1.6)]], portals=[], border_portals=[])
    tmp = tempfile.mkdtemp()
    fake = os.path.join(tmp, 'pathdata.hpd')
    open(fake, 'wb').write(b'x')
    real = (navregress.navparity.reference_of, navregress.hpdtool if hasattr(navregress, 'hpdtool') else None)
    import hpdtool
    real_parse, real_mesh = hpdtool.parse, kymesh.parse_mesh
    try:
        navregress.navparity.reference_of = lambda root, lv: fake if lv == 'with' else os.path.join(tmp, 'none.hpd')
        hpdtool.parse = lambda data: SimpleNamespace(sectors=[SimpleNamespace(mesh=b'')])
        kymesh.parse_mesh = lambda mesh: SimpleNamespace(areas=[area, detailed])
        b = navregress.reference_bounds(tmp, 'with')
        none = navregress.reference_bounds(tmp, 'without')
    finally:
        navregress.navparity.reference_of = real[0]
        hpdtool.parse, kymesh.parse_mesh = real_parse, real_mesh
    check(b == (0.5, 1.6, 3.2, 4.0), f'reference extent from outlines and portals of every area, no border portals ({b})')
    check(none is None, 'no reference path data: no bound (a new map keeps its sampling box)')
    check(navbuild.CLIP_TO_REFERENCE and navbuild.EDGE_SECTOR_RULE == 'grid_min' and navbuild.EDGE_SECTOR_TILES,
          'defaults: clip to the reference extent, edge-sector tiles on the grid minimum')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
