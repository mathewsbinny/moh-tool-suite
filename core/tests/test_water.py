r"""water.py on every level of Project OPFOR's game copy: each .Water.Mesh.res is read to its last byte, its
indices are in range and its vertices finite, and the VisualWater ranges give every triangle a shader where
the two files' indices agree (all levels with water but SP_04). SP_006 and SP_009 have no water.

  python test_water.py          unpacks each level's terrain-00.fbrb into a temporary folder
"""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
from mohcore import water  # noqa: E402
import navkit  # noqa: E402

TOOL = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
NO_WATER = {'sp_006', 'sp_009'}
SHADERS_DIFFER = {'sp_04'}           # its VisualWater has 4,818 vertices for the mesh's 18


def main(argv):
    game = os.path.join(navkit.find_opfor(None), 'game')
    base = os.path.join(game, 'Dist', 'win32', 'levels')
    work = tempfile.mkdtemp(prefix='water_test_')
    failed = checked = 0
    try:
        for lv in sorted(os.listdir(base)):
            bundle = os.path.join(base, lv, 'terrain-00.fbrb')
            if not os.path.isfile(bundle):
                continue
            out = os.path.join(work, lv)
            subprocess.run([TOOL, 'unpack', bundle, out], check=True, capture_output=True)
            terrains = os.path.join(out, 'Terrains')
            name = os.listdir(terrains)[0].lower() if os.path.isdir(terrains) else lv.lower()
            problems = []
            try:
                w = water.read(out, name)
            except Exception as e:
                w, problems = None, [f'not read: {e}']
            key = lv.lower()
            if w is None and not problems:
                problems.append('no water files')
            elif w is not None:
                v, t = w.vertices, w.triangles
                if (len(t) == 0) != (key in NO_WATER):
                    problems.append(f'{len(t)} triangles (expected {"none" if key in NO_WATER else "some"})')
                if len(t) and (t.max() >= len(v) or not np.isfinite(v).all()):
                    problems.append('index out of range or vertex not finite')
                if len(t) and key not in SHADERS_DIFFER and (w.shader is None or not all(w.shader)):
                    problems.append('triangles without a shader (VisualWater ranges did not match)')
            checked += 1
            failed += bool(problems)
            if problems or '-v' in argv:
                n = len(w) if w is not None else 0
                print(f'{"FAIL" if problems else "ok  "} {lv:12s} {n:6d} triangles ' + '; '.join(problems))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f'{checked} levels checked; ' + ('all passed' if not failed else f'{failed} failed'))
    return 1 if failed or not checked else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
