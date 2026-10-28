r"""NavKit's collision index (levelobjects.havok_index) on a folder built here (no game data): a blueprint's packfile
`<name>_Havok.res` and one named `<name>_Havok_Havok.res` (sp_009's animated aircraft door) are both found under the
blueprint's name; the first base wins for a name in two bases.

  python test_havok_index.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import levelobjects  # noqa: E402


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, 'a'), os.path.join(tmp, 'b')
        files = [os.path.join(a, 'Objects', 'Crate', 'Crate_Havok.res'),
                 os.path.join(a, 'Objects', 'Plane', 'Door_Animate_Havok_Havok.res'),
                 os.path.join(b, 'Objects', 'Crate', 'Crate_Havok.res')]
        for f in files:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            open(f, 'wb').close()
        idx = levelobjects.havok_index([a, b])
        check(idx.get('objects/crate/crate') == files[0], 'a <name>_Havok.res packfile is found under the blueprint')
        check(idx.get('objects/plane/door_animate') == files[1], 'a <name>_Havok_Havok.res packfile is found under the blueprint')
        check(idx.get('objects/plane/door_animate_havok') == files[1], '... and still under its literal name')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
