"""NavKit: the level's ladders, for ladder links in the path data (navwalk.walkable seeds the far end of a ladder whose
near end is walkable; navbuild links a node at each end).

Ladders are objects (LadderEntityData): a soldier who walks into one, facing it, climbs it - the AI too (a friendly
bot followed the user up a ladder on Atacama, 2026-10-21). DICE's path data has no ladder links (no route edge on
any level climbs steeper than walkable ground), so ground reached only by ladder is not in it; NavKit adds them.

A ladder is found by its blueprint path (containing 'ladder', with a collision model) among the level's placed
objects (objects.json from levelobjects.py); its collision model in world space gives the box: foot = the box
bottom, top = the box top.

Usage: ladders.py <level dir> [<more blueprint folders>...]   writes <level dir>\\ladders.json
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import levelobjects  # noqa: E402


def extract(level_dir, extra_bases=()):
    """[{'bp', 'lo': [x, y, z], 'hi': [x, y, z]}] - world boxes of the level's placed ladders."""
    bases = [b for b in [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + list(extra_bases)
             if os.path.isdir(b)]
    hv = levelobjects.havok_index(bases)
    out = []
    for o in json.load(open(os.path.join(level_dir, 'objects.json'))):
        bp = o['bp'].lower()
        if 'ladder' not in bp.rsplit('/', 1)[-1] or bp not in hv:
            continue
        tris = levelobjects.triangles(hv[bp])
        if tris is None:
            continue
        m = o['m']
        R = np.array([m[0:3], m[4:7], m[8:11]])
        V = tris.reshape(-1, 3) @ R + np.array(m[12:15])
        lo, hi = V.min(0), V.max(0)
        if hi[1] - lo[1] < 1.5:                    # a ladder prop lying down, or a fragment: nothing to climb
            continue
        out.append({'bp': o['bp'], 'lo': [round(float(v), 3) for v in lo], 'hi': [round(float(v), 3) for v in hi]})
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    lad = extract(argv[1], argv[2:])
    json.dump(lad, open(os.path.join(argv[1], 'ladders.json'), 'w'), indent=1)
    print(f'{len(lad)} ladders -> {os.path.join(argv[1], "ladders.json")}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
