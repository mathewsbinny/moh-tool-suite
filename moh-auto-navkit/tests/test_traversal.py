r"""Traversal annotations, the apply report and installing (M4 steps 5-6), on a DICE base (no rebuild, so quick;
sp_004_b by default):

- traversals go to <level>.traversal.json, not into the path data (only traversals: the copy is byte-identical
  to DICE's file); each has its ends, rise, the route node and island at each end, and whether it joins two
  islands; one far from the route graph is kept, with no node at that end;
- the apply report (<output>.report.txt) lists what happened, notes stay in the side file;
- installing (into a stand-in game folder, not the real one): DICE's file kept once as .opfor-orig, the
  traversal file installed beside it, removed again when a later install has none, and restore puts the
  original back; apply --install does both steps.

  python test_traversal.py [level]
"""
import json
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NAVKIT = os.path.abspath(os.path.join(HERE, '..'))
SUITE = os.path.abspath(os.path.join(NAVKIT, '..'))
sys.path.insert(0, NAVKIT)
sys.path.insert(0, os.path.join(NAVKIT, 'tools'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
import navkit  # noqa: E402
from mohcore import navdata, navpath, navtune as nt  # noqa: E402


def main(argv):
    lv = argv[1] if len(argv) > 1 else 'sp_004_b'
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    pr = navkit.Project(None)
    dice = pr.dice_pathdata(lv)
    if not dice:
        print(f'FAIL {lv} has no DICE path data')
        return 1
    tmp = tempfile.mkdtemp(prefix='traversal_test_')
    try:
        nav = navdata.load(dice)
        g = navpath.ground_nodes(nav)
        main_n = np.nonzero(nav.node_island == 0)[0]
        other_n = np.nonzero(nav.node_island == 1)[0]
        bottom, top = g[main_n[0]], g[other_n[0]]
        t = nt.NavTune.new(f'Levels/{lv}', dice, kind='dice')
        t.add(nt.op('traversal', kind='ladder', bottom=bottom, top=top, **{'two-way': True}))
        t.add(nt.op('traversal', kind='drop', bottom=g[main_n[1]], top=g[main_n[1]] + [0, 3, 0], **{'two-way': False}))
        t.add(nt.op('traversal', kind='jump', bottom=g[main_n[0]] + [5000, 0, 5000], top=g[main_n[0]] + [5003, 0, 5000]))
        t.add(nt.op('note', at=bottom, text='checked'))
        p = os.path.join(tmp, f'{lv}.navtune.json')
        t.save(p)
        out = os.path.join(tmp, 'out', 'pathdata_dice_edited.hpd')
        os.makedirs(os.path.dirname(out))
        lines = []
        navkit.apply(pr, lv, p, out, log=lines.append)
        check(open(out, 'rb').read() == open(dice, 'rb').read(), 'traversals only: the path data is DICE\'s, byte for byte')
        trav = navkit.traversal_path(os.path.dirname(out), lv)
        doc = json.load(open(trav))
        check(doc['format'] == 'moh-traversal' and doc['level'] == f'Levels/{lv}' and len(doc['links']) == 3,
              'the traversal file: format, level, 3 annotations')
        lad, drop, jump = doc['links']
        check(lad['kind'] == 'ladder' and lad['two-way'] and lad['bottom_node']['island'] == 0 and lad['top_node']['island'] == 1
              and lad['joins_islands'], 'the ladder: two-way, from the main network (island 0) to island 1 - joins them')
        check(drop['two-way'] is False and abs(drop['rise'] - 3.0) < 0.01 and not drop['joins_islands'], 'the drop: one-way, 3 m, within one island')
        check(jump['bottom_node'] is None and jump['top_node'] is None, 'a jump far from the route graph is kept, with no node at its ends')
        check(doc['pathdata'] == os.path.basename(out) and len(doc['pathdata_sha1']) == 40, 'it names the path data it was made for')
        report = out + '.report.txt'
        text = open(report, encoding='utf-8').read() if os.path.isfile(report) else ''
        check('traversal ladder' in text and '1 note(s)' in text and 'read back exactly' in text, 'the apply report lists what happened')

        # installing, into a stand-in game folder
        game = os.path.join(tmp, 'game', 'Output', 'win32', 'ai', 'levels', lv, 'infantry')
        os.makedirs(game)
        dest = os.path.join(game, 'pathdata.hpd')
        open(dest, 'wb').write(b'DICE original')
        pr.pathdata = lambda level: dest              # installs go to the stand-in ...
        pr.dice_pathdata = lambda level: dice         # ... while DICE's file is still read from the game copy
        navkit.install(pr, lv, log=lambda *a: None, src=out)
        check(open(dest + navkit.BACKUP_SUFFIX, 'rb').read() == b'DICE original' and open(dest, 'rb').read() == open(out, 'rb').read(),
              'install: the original kept as .opfor-orig, the edited file in its place')
        tdest = navkit.traversal_path(game, lv)
        check(os.path.isfile(tdest) and json.load(open(tdest)) == doc, 'install: the traversal file beside it')
        t2 = nt.NavTune.new(f'Levels/{lv}', dice, kind='dice')
        t2.add(nt.op('note', at=bottom, text='nothing to build'))
        p2 = os.path.join(tmp, 'notes.navtune.json')
        t2.save(p2)
        navkit.apply(pr, lv, p2, out, log=lambda *a: None, install_after=True)
        check(not os.path.isfile(trav) and not os.path.isfile(tdest), 'apply --install without traversals: no stale traversal files')
        check(open(dest + navkit.BACKUP_SUFFIX, 'rb').read() == b'DICE original', 'a second install keeps the first original')
        navkit.restore(pr, lv, log=lambda *a: None)
        check(open(dest, 'rb').read() == b'DICE original' and not os.path.isfile(dest + navkit.BACKUP_SUFFIX), 'restore puts the original back')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
