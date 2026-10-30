r"""water.py on every level of Project OPFOR's game copy (all 26 MP levels, base and DLC): each level's terrain-00.fbrb
layers are unpacked (dist\win32 and its patch; the patch's water files win, as in level.py), each .Water.Mesh.res is
read to its last byte, its indices are in range and its vertices finite, the VisualWater ranges give every triangle
a shader, and the triangle count is the one recorded from MOH's data (TRIANGLES). Twelve levels have no water: their
WaterMesh is one body of 0 vertices and their VisualWater has no vertices either (mp_01_elimination, mp_02,
mp_02_koth2, mp_06_*, mp_07_koth, mp_09_*, mp_10_*).

  python test_water.py          unpacks each level's terrain bundles into a temporary folder (-v: every level)
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
from mohcore import gamecopy, water  # noqa: E402

TOOL = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
TRIANGLES = {                        # water triangles per level, from MOH's terrain bundles (0: no water)
    'mp_01': 92, 'mp_01_elimination': 0, 'mp_02': 0, 'mp_02_koth': 251, 'mp_02_koth2': 0, 'mp_03_elimination': 29,
    'mp_04': 356, 'mp_04_koth': 356, 'mp_05_domination': 66, 'mp_05_overrun': 66, 'mp_05_tdm': 66,
    'mp_06_domination': 0, 'mp_06_elimination': 0, 'mp_06_overrun': 0, 'mp_06_tdm': 0, 'mp_07_koth': 0,
    'mp_08_domination': 234, 'mp_08_overrun': 234, 'mp_08_tdm': 234, 'mp_09_domination': 0, 'mp_09_elimination': 0,
    'mp_09_overrun': 0, 'mp_09_tdm': 0, 'mp_10_domination': 0, 'mp_10_overrun': 0, 'mp_10_tdm': 0}


def level_water(layers, out):
    """The level's water from its terrain bundle layers (lowest first): the highest layer with water files wins."""
    folders = []
    for k, bundle in enumerate(layers):
        d = os.path.join(out, str(k))
        subprocess.run([TOOL, 'unpack', bundle, d], check=True, capture_output=True)
        folders.append(d)
    names = [n.lower() for d in folders if os.path.isdir(os.path.join(d, 'Terrains'))
             for n in os.listdir(os.path.join(d, 'Terrains'))]
    for d in reversed(folders):
        for name in names:
            w = water.read(d, name)
            if w is not None:
                return w
    return None


def main(argv):
    gc = gamecopy.GameCopy()
    work = tempfile.mkdtemp(prefix='water_test_')
    failed = checked = 0
    try:
        levels = gc.levels()
        if sorted(levels) != sorted(TRIANGLES):
            print(f'FAIL the game copy\'s levels are not the 26 recorded: {sorted(set(levels) ^ set(TRIANGLES))}')
            failed += 1
        for lv in sorted(levels):
            layers = gc.level_bundles(lv, 'terrain-00.fbrb')
            problems = []
            w = None
            if not layers:
                problems.append('no terrain-00.fbrb')
            else:
                try:
                    w = level_water(layers, os.path.join(work, lv))
                except Exception as e:
                    problems.append(f'not read: {e}')
                if w is None and not problems:
                    problems.append('no water files')
            if w is not None:
                v, t = w.vertices, w.triangles
                if len(t) != TRIANGLES.get(lv):
                    problems.append(f'{len(t)} triangles (expected {TRIANGLES.get(lv)})')
                if len(t) and (t.max() >= len(v) or not np.isfinite(v).all()):
                    problems.append('index out of range or vertex not finite')
                if len(t) and (w.shader is None or not all(w.shader)):
                    problems.append('triangles without a shader (VisualWater ranges did not match)')
            checked += 1
            failed += bool(problems)
            if problems or '-v' in argv:
                n = len(w) if w is not None else 0
                print(f'{"FAIL" if problems else "ok  "} {lv:18s} {n:6d} triangles ' + '; '.join(problems))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f'{checked} levels checked; ' + ('all passed' if not failed else f'{failed} failed'))
    return 1 if failed or not checked else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
