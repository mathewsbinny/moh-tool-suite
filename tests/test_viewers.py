r"""Smoke test of the 3D tools: the Map Inspector and the Navigation Studio open a MOH level, draw it and exit; the
screenshot must not be blank (some detail in it, not one flat colour). The studio draws NavKit's newest build of
the level coloured by island, then by difference against the level's export (work\navkit\<level>\pathdata.hpd as
the reference - MOH ships no DICE path data to compare with; with a regression build present that is the current
code's rebuild against the export, else the export against itself), and that run must have read both files.
Needs a display and OpenGL 3.3, Project OPFOR's game copy and a NavKit build of the level. Pixels are not
compared: drawing differs between GPUs.

  python tests\test_viewers.py [level]          default mp_07_koth (small: 1,797 route nodes)
"""
import os
import subprocess
import sys
import tempfile

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
RUNS = [
    ('Map Inspector', os.path.join(SUITE, 'moh-map-inspector', 'inspector.py'), []),
    ('Navigation Studio, islands', os.path.join(SUITE, 'moh-navigation-studio', 'studio.py'), ['--color', 'island']),
    ('Navigation Studio, difference', os.path.join(SUITE, 'moh-navigation-studio', 'studio.py'),
     ['--color', 'difference', '--reference', '{export}']),
]


def main(argv):
    import numpy as np
    import matplotlib.image as mpimg
    lv = argv[1] if len(argv) > 1 else 'mp_07_koth'
    sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
    import navkit
    export = os.path.join(navkit.find_opfor(None), 'work', 'navkit', lv, 'pathdata.hpd')
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for k, (name, script, extra) in enumerate(RUNS):
            shot = os.path.join(tmp, f'{k}.png')
            extra = [export if a == '{export}' else a for a in extra]
            r = subprocess.run([sys.executable, script, lv, '--screenshot', shot, '--frames', '60', *extra],
                               capture_output=True, text=True, timeout=1200)
            gpu = next((l[5:] for l in r.stdout.splitlines() if l.startswith('GPU: ')), '?')
            problem = None
            if r.returncode != 0:
                problem = f'exit {r.returncode}: {(r.stdout + r.stderr).strip().splitlines()[-1:]}'
            elif not os.path.isfile(shot):
                problem = 'no screenshot'
            elif '--reference' in extra and not any(l.startswith('DICE: ') for l in r.stdout.splitlines()):
                problem = 'the reference file was not read (no difference to draw)'
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
