r"""Regression tests for four defects found in a code review (2026-10-26), no game data:

- NavKit's apply (a build with the Navigation Studio's walkability edits) builds with the same inputs as a normal
  build - detailed zones and ladders included (it used to leave both out);
- the stock-MP installer reinstalls a rebuilt file (it used to say "already" when the installed file matched its own
  install stamp, without comparing it with the new build) and keeps the original as the backup, not an earlier build;
- the Navigation Studio's option parser takes --edit as a flag (it used to swallow the next option as its value);
- the navigation-data cache key depends on the terrain's content (it used to record only whether there was one).

  python tests\test_review_fixes.py
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit', 'tools'))


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''))


class Stop(Exception):
    pass


def navkit_apply(c, work):
    import navkit
    import navbuild
    from mohcore import navtune
    seen = {}

    class Pr:
        root = work

        def out_dir(self, lv):
            return work

    class Tune:
        kind = 'navkit'
        operations = [{'op': 'walkable'}]

    def fake_inputs(pr, lv, log=print):
        return 's.bin', 'forbid.npy', 1, 2, dict(detailed={'sectors': {1}}, ladders=[{'a': 1}], seeds=[], log=log)

    def fake_build(*a, **kw):
        seen.update(kw)
        raise Stop
    side = os.path.join(work, 'lv.navtune.json')
    open(side, 'w').write('{}')
    saved = navkit.build_inputs, navbuild.build, navtune.load
    navkit.build_inputs, navbuild.build, navtune.load = fake_inputs, fake_build, lambda p: Tune()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            navkit._apply(Pr(), 'lv', side, None, lambda *a: None)
    except Stop:
        pass
    finally:
        navkit.build_inputs, navbuild.build, navtune.load = saved
    c.check(seen.get('detailed') == {'sectors': {1}} and seen.get('ladders') == [{'a': 1}] and 'edits' in seen,
            'navkit apply: built with the normal build\'s inputs (detailed zones, ladders) plus the edits', str(seen.keys()))


def stockmp_install(c, work):
    sys.path.insert(0, os.path.join(SUITE, 'moh-stockmp-ai'))
    import stockmp
    import pipeline
    game = os.path.join(work, 'game')
    paths = pipeline.Paths('mp_test', opfor=work, work=os.path.join(work, 'stockmp'), game=game)
    out = os.path.join(paths.root, 'out', 'game', 'Levels')
    os.makedirs(out)
    os.makedirs(os.path.join(game, 'Levels'))
    built, dst = os.path.join(out, 'level.fbrb'), os.path.join(game, 'Levels', 'level.fbrb')
    open(dst, 'wb').write(b'original')
    run = (lambda: stockmp.install(paths, ['stockmp.py', 'install', 'mp_test', '--apply']))
    open(built, 'wb').write(b'build 1')
    with contextlib.redirect_stdout(io.StringIO()):
        run()
    open(built, 'wb').write(b'build 2')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run()
    c.check('installed' in buf.getvalue() and open(dst, 'rb').read() == b'build 2',
            'stock-MP install: a rebuilt file is installed again, not reported "already"', buf.getvalue().strip())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run()
    c.check('already' in buf.getvalue(), 'stock-MP install: an unchanged build is "already" installed', buf.getvalue().strip())
    c.check(open(dst + stockmp.BACKUP, 'rb').read() == b'original', 'stock-MP install: the backup stays the original')
    with contextlib.redirect_stdout(io.StringIO()):
        stockmp.install(paths, ['stockmp.py', 'install', 'mp_test', '--revert'])
    c.check(open(dst, 'rb').read() == b'original', 'stock-MP install: revert restores the original after a reinstall')


def studio_args(c):
    sys.path.insert(0, os.path.join(SUITE, 'moh-navigation-studio'))
    try:
        import studio
    except ImportError as e:
        print(f'skip the Navigation Studio parser (cannot import: {e})')
        return
    opts, args = studio.parse_args(['studio.py', 'sp_009', '--edit', '--candidate', 'new.hpd'])
    c.check(opts.get('--edit') is True and opts.get('--candidate') == 'new.hpd' and args == ['sp_009'],
            'Navigation Studio: --edit is a flag, --candidate keeps its value', f'{opts} {args}')
    opts, args = studio.parse_args(['studio.py', '--candidate', 'a.hpd', 'sp_009', '--edit'])
    c.check(opts == {'--candidate': 'a.hpd', '--edit': True} and args == ['sp_009'],
            'Navigation Studio: --edit last, a value option first', f'{opts} {args}')


def navdata_key(c, work):
    import numpy as np
    from mohcore import navdata, terrain
    p = os.path.join(work, 'x.hpd')
    open(p, 'wb').write(b'x')

    def hf(v):
        raw = np.full((4, 4), v, np.uint16)
        return terrain.Heightfield(np.array([0., 0., 0.]), np.array([4., 10., 4.]), 4, raw, raw.astype(np.float32))
    a, b, a2 = navdata._key(p, hf(1)), navdata._key(p, hf(2)), navdata._key(p, hf(1))
    c.check(a != b and a == a2 and a != navdata._key(p, None),
            'navigation cache key: different terrain content -> a different key; the same content -> the same key')


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='review_fixes_')
    try:
        navkit_apply(c, work)
        stockmp_install(c, work)
        studio_args(c)
        navdata_key(c, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
