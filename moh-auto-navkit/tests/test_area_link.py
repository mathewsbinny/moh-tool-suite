r"""NavKit's area link rule (navbuild.Level.connected with rule 'terrain' / 'raw', navbuild.components) on a sample
grid built here (no game data): straight neighbours join an area up to a raw 0.6 m step, diagonal ones when a
route through a side column (walkable or not) has both steps within 0.6 m; the slope rule still joins a steep
plane, so portals and routes keep it; object surfaces use the slope rule under 'terrain'.

  python test_area_link.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navbuild  # noqa: E402
import navwalk  # noqa: E402


def level(heights, keep=None, terrain=None):
    """One surface per column; heights[j][i]. Normals from the height differences (a plane gets its slope)."""
    h = np.array(heights, float)
    nz, nx = h.shape
    gx = np.gradient(h, 0.8, axis=1) if nx > 1 else np.zeros_like(h)
    gz = np.gradient(h, 0.8, axis=0) if nz > 1 else np.zeros_like(h)
    n = np.stack([-gx.ravel(), np.ones(h.size), -gz.ravel()], 1)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    hit = None
    if terrain is not None:
        hit = np.zeros((h.size, 2), np.int64)
        hit[np.array(terrain, bool).ravel(), 0] = navwalk.TERRAIN_SHAPE_VTABLE
    s = SimpleNamespace(nx=nx, nz=nz, pitch=0.8, min_x=0.0, min_z=0.0, start=np.arange(h.size + 1),
                        y=h.ravel().astype(np.float32), normal=n.astype(np.float32), hit=hit)
    k = np.ones(h.size, bool) if keep is None else np.array(keep, bool).ravel()
    return navbuild.Level(s, k, 0.28)


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    lv = level([[0.0, 0.55, 1.2]])
    check(lv.connected(0, 1, 1, 0, 'raw'), 'straight step 0.55 m links')
    check(not lv.connected(1, 2, 1, 0, 'raw'), 'straight step 0.65 m does not')

    plane = level([[0.0, 0.66, 1.32, 1.98]])           # a 39.5 deg plane: walkable, steps of 0.66 m
    check(lv is not None and plane.connected(0, 1, 1, 0, 'slope'), 'the slope rule joins a steep plane')
    check(not plane.connected(0, 1, 1, 0, 'raw'), 'the raw rule cuts it')
    check(len(navbuild.components(plane, {k: (k, 0) for k in range(4)}, 0, 0)) == (4 if navbuild.AREA_LINK != 'slope' else 1),
          'components: the steep plane gives one area per cell')

    # diagonal (0,0) -> (1,1), step 0.9: through side (1,0) at 0.45 (both steps 0.45) - links, even unwalkable
    d = level([[0.0, 0.45], [5.0, 0.9]], keep=[[1, 0], [0, 1]])
    check(d.connected(0, 3, 1, 1, 'raw'), 'diagonal links through an unwalkable side cell with steps within 0.6 m')
    d2 = level([[0.0, 5.0], [5.0, 0.9]])
    check(not d2.connected(0, 3, 1, 1, 'raw'), 'diagonal without such a side route does not link')
    d3 = level([[0.0, 0.0], [0.0, 0.0]])
    check(d3.connected(0, 3, 1, 1, 'raw'), 'flat diagonal links')

    obj = level([[0.0, 0.66, 1.32]], terrain=[[1, 0, 0]])
    check(obj.connected(1, 2, 1, 0, 'terrain'), "'terrain': two object surfaces use the slope rule")
    check(obj.connected(0, 1, 1, 0, 'terrain'), "'terrain': terrain next to an object uses the slope rule")
    ter = level([[0.0, 0.66, 1.32]], terrain=[[1, 1, 1]])
    check(not ter.connected(0, 1, 1, 0, 'terrain'), "'terrain': two terrain surfaces use the raw rule")
    check(navbuild.LINK_RULE == 'slope', 'portals and routes use the slope rule')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
