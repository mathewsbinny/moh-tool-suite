r"""navregress's progress estimates (no game data): durations in words, each level's expected time from an earlier
run (the first run that has it), from its sample file size at the median seconds per MB of the timed levels, or
the default when nothing is known.

  python test_regress_progress.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navregress  # noqa: E402


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    d = navregress.duration
    check((d(40), d(89.4), d(90), d(600), d(3600), d(3725)) == ('40 s', '89 s', '2 min', '10 min', '1 h 00 min', '1 h 02 min'),
          'durations in words')

    root = tempfile.mkdtemp()                           # no level inputs here: sizes unknown
    est = navregress.estimates(root, ['a', 'b', 'c'], {'a': {'seconds': 40}}, {'a': {'seconds': 99}, 'b': {'seconds': 300}})
    check(est == {'a': 40, 'b': 300, 'c': navregress.DEFAULT_SECONDS},
          'expected times: first run that has the level, then the next, else the default')
    check(navregress.estimates(root, ['x'], {}) == {'x': navregress.DEFAULT_SECONDS}, 'no earlier run: the default')

    sizes = {'a': 10e6, 'b': 20e6, 'c': 40e6}           # sample files of 10, 20, 40 MB
    real = navregress.inputs
    navregress.inputs = lambda root, lv: (os.path.join(root, lv + '.bin'),)
    for lv, n in sizes.items():
        with open(os.path.join(root, lv + '.bin'), 'wb') as fh:
            fh.truncate(int(n))
    try:
        est = navregress.estimates(root, ['a', 'b', 'c'], {'a': {'seconds': 30}, 'b': {'seconds': 80}})
        only_c = navregress.estimates(root, ['c'], {'a': {'seconds': 30}}, {'b': {'seconds': 80}})
    finally:
        navregress.inputs = real
    check(abs(est['c'] - 40 * 4.0) < 1e-6, f'untimed level from its size at the median rate (4 s/MB): {est["c"]:.0f} s')
    check(est['a'] == 30 and est['b'] == 80, 'timed levels keep their time')
    check(abs(only_c['c'] - 160) < 1e-6, 'the rate also comes from timed levels not being estimated (levels already done)')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
