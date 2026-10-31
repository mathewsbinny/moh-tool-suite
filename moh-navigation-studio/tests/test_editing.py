r"""The Navigation Studio's edit mode without a window: the studio is built for a MOH level (mp_07_koth by default)
on NavKit's build of it, and the editor is driven as clicks would drive it - every tool, an outline finished
with Enter, a box, two-point tools, a note without text refused, undo / redo, save and reload, the click mode
(clicks stop selecting while a tool is on), the apply button (into a temporary file, never the level's export),
and a graph-only base (side file kind "dice") refusing region edits and taking links - MOH ships no path data,
so that base is NavKit's build standing in for DICE's file. Needs Project OPFOR's game copy and a NavKit build of
the level.

First, without game data (--quick runs only these; 2026-10-31): an unreadable side file is not saved over (the
editor warns, starts empty, and the save keeps the file as .unreadable.bak), reload with unsaved edits needs a
second click, a deleted edit comes back in its place on undo, and E does not toggle the panel while flying with
the right button held.

  python test_editing.py [level] [--quick]
"""
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, STUDIO)
sys.path.insert(0, os.path.join(STUDIO, '..', 'core'))
import studio  # noqa: E402
import editing  # noqa: E402
from mohcore import level as levelmod, navdata, navpath, navtune as nt  # noqa: E402


class FakeViewer:
    """What the editor and studio use of the viewer: the click mode, the overlay and the camera."""
    def __init__(self):
        self.select_on_click = True
        self.overlay = []

    def set_overlay(self, segs):
        self.overlay = list(segs)


class FakeStudio:
    """What the editor uses of the studio, on a stand-in base file (no game data)."""
    def __init__(self, base):
        self.files = {'NavKit': type('Nav', (), {'path': base})()}
        self.refreshed = 0

    def sync_click_mode(self, v):
        pass

    def refresh_overlay(self, v):
        self.refreshed += 1


def quick_checks(check):
    tmp = tempfile.mkdtemp(prefix='editing_quick_')
    try:
        base = os.path.join(tmp, 'pathdata.hpd')
        open(base, 'wb').write(b'stand-in base file, only hashed')
        st, v = FakeStudio(base), FakeViewer()
        side = nt.default_path(tmp, 'lv')
        good = nt.NavTune.new('Levels/LV', base)
        good.add(nt.op('note', at=(1, 2, 3), text='the user\'s edit'))
        j = good.to_json()
        j['version'] = nt.VERSION + 1                     # a newer version's file: not readable here
        os.makedirs(os.path.dirname(side))
        import json
        json.dump(j, open(side, 'w'))
        original = open(side, 'rb').read()
        ed = editing.Editor(st, tmp, 'lv', 'Levels/LV')
        check(ed.unreadable == side and not ed.tune.operations and 'could not be read' in ed.warning,
              'an unreadable side file: the editor warns and starts with no edits')
        check(open(side, 'rb').read() == original, 'and the file is left as it was')
        ed.tune.add(nt.op('seed', at=(0, 0, 0)))
        msg = ed.save()
        bak = side + '.unreadable.bak'
        check(os.path.isfile(bak) and open(bak, 'rb').read() == original and 'kept as' in msg and not ed.warning,
              'the first save keeps the unreadable file as .unreadable.bak (not saved over)')
        check([o['op'] for o in nt.load(side).operations] == ['seed'], 'and saves the new edits to the side file')
        ed.save()
        check(open(bak, 'rb').read() == original and not os.path.exists(side + '.unreadable.1.bak'),
              'a second save writes over its own file only')
        # a JSON typo, read by reload: the edits on screen stay
        open(side, 'w').write('{"format": "moh-navtune", ')
        ed.tune.add(nt.op('unseed', at=(1, 0, 0)))
        first = ed.reload(v)
        check(not first and ed.reload_armed and len(ed.tune.operations) == 2, 'reload with unsaved edits: the first click only asks')
        second = ed.reload(v)
        check(not second and len(ed.tune.operations) == 2 and ed.unreadable == side,
              'the second click reads the file; unreadable (a JSON typo), so the edits stay and the file is kept')
        ed.save()
        check(os.path.isfile(side + '.unreadable.1.bak'), 'saved: that file kept as .unreadable.1.bak')
        ed.tune.add(nt.op('note', at=(2, 0, 0), text='new'))
        ed.reload(v)
        ed.undo(v)                                       # another action: the next reload asks again
        check(not ed.reload_armed, 'an edit, undo or redo between the clicks cancels the confirmation')
        ed.save()
        check(ed.reload(v) and len(ed.tune.operations) == 2, 'reload without unsaved edits reads the file at once')
        # delete then undo: the deleted edit comes back in its place
        ed.tune.add(nt.op('note', at=(3, 0, 0), text='last'))
        ops = list(ed.tune.operations)
        ed.tune.remove(0)
        ed.undo(v)
        check(ed.tune.operations == ops and ed.tune.operations[0] is ops[0], 'delete then Ctrl+Z: the deleted edit is back in its place')
        # E while flying (right button held) climbs in the viewer; it does not close the panel
        v.camera = type('Cam', (), {'fly': True})()
        check(not editing.Editor.e_toggles(v, True) and editing.Editor.e_toggles(v, False),
              'E does not toggle the panel while flying with the right button held')
        v.camera.fly = False
        check(editing.Editor.e_toggles(v, True), 'and toggles it in orbit mode')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    lv = next((a for a in argv[1:] if not a.startswith('--')), 'mp_07_koth')
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    quick_checks(check)
    if '--quick' in argv:
        print('all passed' if not failed else f'{failed} check(s) failed')
        return 1 if failed else 0

    lvl = levelmod.load(lv, log=lambda *a: None)
    cand, ref = studio.default_files(lvl.opfor, lvl.lv)
    cache = os.path.join(lvl.opfor, 'work', 'navstudio', 'cache')
    files = {name: navdata.load(p, lvl.heightfield, cache) for name, p in (('NavKit', cand), ('DICE', ref)) if p}
    if 'NavKit' not in files:
        print(f'FAIL no NavKit build of {lv} (run navkit.py build or navregress.py first)')
        return 1
    st = studio.Studio(lvl, files)
    tmp = tempfile.mkdtemp(prefix='editing_test_')
    try:
        v = FakeViewer()
        ed = editing.Editor(st, lvl.opfor, lvl.lv, f'Levels/{lvl.name}')
        ed.path = os.path.join(tmp, f'{lv}.navtune.json')
        ed.tune = nt.NavTune.new(f'Levels/{lvl.name}', files['NavKit'].path)
        st.editor = ed
        check(ed.target == 'NavKit' and ed.tune.kind == 'navkit', 'the editor works on NavKit\'s build (a NavKit base)')
        g = navpath.ground_nodes(files['NavKit'])
        p0 = g[np.nonzero(files['NavKit'].node_island == 0)[0][50]]
        pts = [p0 + d for d in ([0, 0, 0], [10, 0, 0], [10, 1, 10], [0, 1, 10])]

        ed.set_open(v, True)
        ed.tool = 'unwalkable'
        st.sync_click_mode(v)
        check(not v.select_on_click, 'with a tool on, clicks stop selecting')
        for p in pts:
            ed.click(v, p)
        ed.finish_polygon(v)
        o = ed.tune.operations[-1]
        check(o['op'] == 'unwalkable' and len(o['region']['polygon']) == 4 and o['by'] == nt.author(),
              'an outline of 4 clicks becomes an unwalkable region, recorded as this PC')
        lo, hi = o['region']['y']
        check(abs(lo - (p0[1] - 2.0)) < 0.01 and abs(hi - (p0[1] + 1 + 4.0)) < 0.01, 'its height range: 2 m below, 4 m above the clicks')
        ed.tool, ed.shape = 'walkable', 'box'
        ed.click(v, pts[0]); ed.click(v, pts[2])
        check(ed.tune.operations[-1]['op'] == 'walkable' and 'box' in ed.tune.operations[-1]['region'], 'two corners make a walkable box')
        ed.shape = 'polygon'
        for tool in ('seed', 'unseed'):
            ed.tool = tool
            ed.click(v, pts[1])
        ed.tool = 'note'
        ed.note_text = ''
        n = len(ed.tune.operations)
        ed.click(v, pts[1])
        check(len(ed.tune.operations) == n and ed.message == 'type the note first', 'a note without text is not added')
        ed.note_text = 'check the stairs'
        ed.click(v, pts[1])
        for tool in ('link-areas', 'route-edge add', 'route-edge remove'):
            ed.tool = tool
            ed.click(v, pts[0]); ed.click(v, pts[3])
        ed.tool, ed.trav_kind, ed.two_way = 'traversal', 0, False
        ed.click(v, pts[0]); ed.click(v, pts[0] + [0, 6, 0])
        names = [o['op'] + (' ' + o['action'] if 'action' in o else '') for o in ed.tune.operations]
        check(names == ['unwalkable', 'walkable', 'seed', 'unseed', 'note', 'link-areas', 'route-edge add', 'route-edge remove', 'traversal'],
              'every tool adds its operation')
        t = ed.tune.operations[-1]
        check(t['kind'] == 'ladder' and t['two-way'] is False and abs(t['top'][1] - t['bottom'][1] - 6.0) < 0.002,
              'the traversal: a one-way ladder, 6 m')                   # ends stored to the millimetre
        check(len(v.overlay) > 40, f'the edits are drawn ({len(v.overlay)} overlay lines)')
        ed.undo(v); ed.undo(v)
        check(len(ed.tune.operations) == 7, 'undo twice')
        ed.redo(v)
        check(len(ed.tune.operations) == 8, 'redo once')
        ed.tune.save(ed.path)
        again = nt.load(ed.path)
        check(again.operations == ed.tune.operations, 'saved and loaded back unchanged')
        # the apply button: navkit apply in the background, on a copy of the side file, into a temporary file
        export = os.path.join(lvl.opfor, 'work', 'navkit', lv, 'pathdata.hpd')
        export_stamp = os.path.getmtime(export) if os.path.isfile(export) else None
        ed.job_out = os.path.join(tmp, 'edited.hpd')
        ed.start_apply()
        import time
        t0 = time.time()
        while ed.job_state() == 'running' and time.time() - t0 < 900:
            time.sleep(2)
        state = ed.job_state()
        check(state == 'done' and os.path.isfile(ed.job_out) and any('read back' in l for l in ed.job_log),
              f'the apply button builds with the edits in the background ({time.time() - t0:.0f} s): {state}')
        check(any('link / route-edge edit(s) applied' in l for l in ed.job_log), 'its log reports the links and route edges')
        check(export_stamp is None or os.path.getmtime(export) == export_stamp, 'the level\'s export is untouched')
        ed.set_open(v, False)
        check(v.select_on_click and ed.tool is None, 'closing the panel gives clicks back to selecting')

        dice = editing.Editor(st, lvl.opfor, lvl.lv, f'Levels/{lvl.name}')
        dice.target = 'DICE'
        # a graph-only base: DICE's file where a level has one; on MOH (none) NavKit's build stands in for it
        dice.tune = nt.NavTune.new(f'Levels/{lvl.name}', files.get('DICE', files['NavKit']).path, kind='dice')
        dice.open, dice.tool = True, 'seed'
        dice.click(v, pts[0])
        check(not dice.tune.operations and 'DICE base' in dice.message, 'on a graph-only (DICE) base, region edits are refused')
        dice.tool = 'link-areas'
        dice.click(v, pts[0]); dice.click(v, pts[3])
        check(len(dice.tune.operations) == 1, 'and links are taken')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
