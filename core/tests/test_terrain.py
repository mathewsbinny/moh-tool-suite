r"""Checks terrain.py against the in-game terrain samples NavKit took (work\navsample\<level>_navkit.bin or
_full.bin): per level, the share of terrain samples more than 0.1 m from the decoded height field.

  python test_terrain.py [level ...]          default: every level with samples; unpacks terrain-00.fbrb
                                              into OPFOR's work\levels\<level>\terrain when needed
"""
import glob
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from mohcore import level as levelmod  # noqa: E402  (also puts NavKit's tools on the path)

import navkit  # noqa: E402
import navsample  # noqa: E402
import navwalk  # noqa: E402

LIMIT = 0.1          # percent of samples allowed more than 0.1 m off (measured: at most 0.03 %)


def check(root, lv, samples):
    hf = levelmod.heightfield_of(root, lv, log=lambda *a: None)
    if hf is None:
        return None
    s, _ = navsample.load(samples)
    cols = s.nx * s.nz
    n = np.diff(s.start[:cols + 1])
    c = np.nonzero(n)[0]
    first = s.start[c]
    t = s.hit[first, 0] == navwalk.TERRAIN_SHAPE_VTABLE      # columns whose top surface is terrain
    c, first = c[t], first[t]
    x = s.min_x + (c % s.nx) * s.pitch
    z = s.min_z + (c // s.nx) * s.pitch
    e = np.abs(hf.height(x, z) - s.y[first])
    return len(e), np.median(e), np.percentile(e, 99.9), np.mean(e > 0.1) * 100


def main(argv):
    root = navkit.find_opfor(None)
    found = {}
    for f in sorted(glob.glob(os.path.join(root, 'work', 'navsample', '*.bin'))):
        name = os.path.basename(f)
        for suffix in ('_navkit.bin', '_full.bin'):
            if name.endswith(suffix):
                found.setdefault(name[:-len(suffix)], f)
    levels = [a.lower() for a in argv[1:]] or sorted(found)
    failed = 0
    for lv in levels:
        if lv not in found:
            print(f'{lv:10s} no samples')
            continue
        r = check(root, lv, found[lv])
        if r is None:
            print(f'{lv:10s} no height field')
            continue
        ok = r[3] <= LIMIT
        failed += not ok
        print(f'{lv:10s} {"ok  " if ok else "FAIL"} {r[0]:8d} samples, median {r[1] * 1000:5.1f} mm, '
              f'99.9 % within {r[2] * 1000:5.1f} mm, {r[3]:.3f} % more than 0.1 m off')
    print('all passed' if not failed else f'{failed} level(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
