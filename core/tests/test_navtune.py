r"""navtune.py, the navigation side files (no game data): every operation kind saves and loads unchanged, undo
and redo (a removal undone in place), removing one operation, author and time are recorded (the PC's name by default), invalid
operations are refused with a reason, a DICE base refuses region edits, the base file's hash is checked,
and a newer format version is refused.

  python test_navtune.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from mohcore import navtune as nt  # noqa: E402


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    tmp = tempfile.mkdtemp(prefix='navtune_test_')
    try:
        base = os.path.join(tmp, 'pathdata.hpd')
        open(base, 'wb').write(b'not real path data, only hashed')
        t = nt.NavTune.new('Levels/SP_TEST', base, kind='navkit')
        ops = [nt.op('walkable', region=nt.polygon([(0, 0), (10, 0), (10, 10)], 5, 9), value=True),
               nt.op('unwalkable', region=nt.box(1, 2, 3, -4, 8, 6)),
               nt.op('seed', at=(1.23456, 2, 3)), nt.op('unseed', at=(4, 5, 6)),
               nt.op('link-areas', a=(0, 0, 0), b=(5, 0, 5)),
               nt.op('route-edge', a=(0, 0, 0), b=(5, 0, 5), action='add'),
               nt.op('traversal', kind='ladder', bottom=(0, 0, 0), top=(0, 6, 0), **{'two-way': True}),
               nt.op('note', at=(1, 1, 1), text='check this corner')]
        for o in ops:
            t.add(o)
        check(len(t.operations) == 8 and t.dirty, 'every operation kind is accepted on a NavKit base')
        check(all(o['by'] == nt.author() and o['when'] for o in t.operations), f'by (the PC\'s name: {nt.author()}) and when are recorded')
        check(nt.op('note', at=(0, 0, 0), text='x', by='ABEL')['by'] == 'ABEL', 'another name can be given')
        check(t.operations[1]['region']['box'] == [-4.0, 2.0, 3.0, 1.0, 8.0, 6.0], 'a box is stored low corner first')
        check(t.operations[2]['at'] == [1.235, 2.0, 3.0], 'positions are stored to the millimetre')
        p = os.path.join(tmp, 'sp_test.navtune.json')
        t.save(p)
        check(not t.dirty and not os.path.exists(p + '.tmp'), 'saved (no temporary file left)')
        u = nt.load(p)
        check(u.operations == t.operations and u.level == t.level and u.base == t.base, 'loads back unchanged')
        u.save(p)
        check(json.load(open(p)) == t.to_json(), 'saving again gives the same file')

        t.undo(); t.undo()
        check(len(t.operations) == 6 and len(t.redo_stack) == 2, 'undo twice')
        t.redo()
        check(len(t.operations) == 7 and t.operations[-1]['op'] == 'traversal', 'redo brings back the last undone')
        t.add(nt.op('note', at=(2, 2, 2), text='new'))
        check(not t.redo_stack, 'a new edit clears what could be redone')
        removed = t.remove(0)
        check(removed['op'] == 'walkable' and t.operations[0]['op'] == 'unwalkable', 'one operation removed from the middle of the list')
        # 2026-10-31: undo after a removal puts the removed operation back where it was (it used to drop the last
        # edit instead, and the removed one was lost); redo removes it again, and earlier actions undo in order
        before = [o['op'] for o in t.operations]
        mid = t.operations[3]
        t.remove(3)
        undone = t.undo()
        check(undone is mid and t.operations[3] is mid and [o['op'] for o in t.operations] == before,
              'undo after a removal puts it back at its place (not the last edit removed)')
        t.redo()
        check(len(t.operations) == len(before) - 1 and mid not in t.operations, 'redo removes it again')
        t.undo(); t.undo()
        check([o['op'] for o in t.operations] == ['walkable'] + before and t.undo_stack[-1][0] == 'add',
              'undo again: both removals undone in order (the first one at index 0), the adds next')
        t.redo(); t.redo()
        check(len(t.operations) == len(before) - 1 and not t.redo_stack, 'and redo replays both')
        t.save(p)
        check(json.load(open(p))['operations'] == t.operations and set(json.load(open(p))) == set(t.to_json()),
              'the file format is unchanged (operations only; undo history not saved)')
        check(all(isinstance(t.summary(k), str) for k in range(len(t.operations))), 'every operation has a summary line')

        bad = [nt.op('fly', at=(0, 0, 0)), {'op': 'seed'}, nt.op('route-edge', a=(0, 0, 0), b=(1, 1, 1), action='swap'),
               nt.op('traversal', kind='teleport', bottom=(0, 0, 0), top=(1, 1, 1)), nt.op('note', at=(0, 0, 0), text='  '),
               nt.op('walkable', region={'polygon': [[0, 0], [1, 1]], 'y': [0, 1]}),
               nt.op('walkable', region={'polygon': [[0, 0], [1, 1], [2, 0]], 'y': [5, 1]})]
        refused = 0
        for o in bad:
            try:
                t.add(o)
            except ValueError:
                refused += 1
        check(refused == len(bad), f'invalid operations are refused with a reason ({refused} of {len(bad)})')

        d = nt.NavTune.new('Levels/SP_TEST', base, kind='dice')
        try:
            d.add(nt.op('unwalkable', region=nt.box(0, 0, 0, 1, 1, 1)))
            check(False, 'a DICE base refuses region edits')
        except ValueError as e:
            check('DICE base' in str(e), 'a DICE base refuses region edits')
        d.add(nt.op('link-areas', a=(0, 0, 0), b=(1, 0, 1)))
        check(len(d.operations) == 1 and not d.allowed('seed') and d.allowed('traversal'), 'and takes additive ones')

        check(not t.base_changed(base), 'the base file is the one the edits were made on')
        open(base, 'ab').write(b'changed')
        check(t.base_changed(base), 'a changed base file is noticed')

        j = t.to_json()
        j['version'] = nt.VERSION + 1
        newer = os.path.join(tmp, 'newer.navtune.json')
        json.dump(j, open(newer, 'w'))
        try:
            nt.load(newer)
            check(False, 'a newer format version is refused')
        except ValueError:
            check(True, 'a newer format version is refused')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
