r"""Smoke test of the 3D tools: the Map Inspector and the Navigation Studio open a level, draw it and exit; the
screenshot must not be blank (some detail in it, not one flat colour). Needs a display and OpenGL 3.3, and
Project OPFOR's game copy. Pixels are not compared: drawing differs between GPUs.

  python tests\test_viewers.py [level]          default sp_004_b (small; has DICE path data)
"""
import os
import subprocess
import sys
import tempfile

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
RUNS = [
    ('Map Inspector', os.path.join(SUITE, 'moh-map-inspector', 'inspector.py'), []),
    ('Navigation Studio, islands', os.path.join(SUITE, 'moh-navigation-studio', 'studio.py'), ['--color', 'island']),
    ('Navigation Studio, difference', os.path.join(SUITE, 'moh-navigation-studio', 'studio.py'), ['--color', 'difference']),
]


def main(argv):
    import numpy as np
    import matplotlib.image as mpimg
    lv = argv[1] if len(argv) > 1 else 'sp_004_b'
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for k, (name, script, extra) in enumerate(RUNS):
            shot = os.path.join(tmp, f'{k}.png')
            r = subprocess.run([sys.executable, script, lv, '--screenshot', shot, '--frames', '60', *extra],
                               capture_output=True, text=True, timeout=1200)
            gpu = next((l[5:] for l in r.stdout.splitlines() if l.startswith('GPU: ')), '?')
            problem = None
            if r.returncode != 0:
                problem = f'exit {r.returncode}: {(r.stdout + r.stderr).strip().splitlines()[-1:]}'
            elif not os.path.isfile(shot):
                problem = 'no screenshot'
            else:
                im = mpimg.imread(shot)[..., :3]
                spread = float(np.std(im.reshape(-1, 3), axis=0).max())
                if spread < 0.02:
                    problem = f'the screenshot is blank (colour spread {spread:.3f})'
            failed += problem is not None
            print(f'{"FAIL" if problem else "ok  "} {name} on {lv}' + (f': {problem}' if problem else f'  (GPU: {gpu})'))
    print('all passed' if not failed else f'{failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
