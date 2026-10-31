r"""Regression tests for the NavKit driver and analysis tools' review defects (2026-10-31), no game data:

- after `navkit.py install` onto a level with no original (MOH: an .opfor-new marker, no .opfor-orig) the installed
  file is not taken as DICE's original - dice_pathdata, navparity.reference_of, navregress.reference_bounds;
- navkit apply's graph edits re-key the build's cells (pathdata.cells.npz) to the edited file;
- sample with --box recomputes rule sector offsets (a box below the old sector 0 gave sector x -1);
- navkit.py's options: --ingame a flag, --pitch without --ingame refused, an option without a value an error;
- the GUI's Cancel ends the step's whole process tree, and "installed" sees either marker;
- export fails when rsnavcheck's pass rule fails;
- navregress.py's options are checked before the run (--save alone: the reference name);
- navparity.py show handles self-check rows and an empty run;
- navdiff.py --png draws cells on the 0.8 m comparison grid;
- sample_all.py defaults to the 0.8 m pair.

  python tests\test_review_fixes3.py
"""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit', 'tools'))
QUIET = lambda *a, **k: None   # noqa: E731


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''))


@contextlib.contextmanager
def patched(obj, **values):
    saved = {k: getattr(obj, k) for k in values}
    for k, v in values.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


class StandIn:
    """A stand-in Project on a temp OPFOR folder (no game in it): the game copy's path-data layout, NavKit's work."""
    def __init__(self, root):
        import navkit
        self.root = root
        self.copy = navkit.gamecopy.GameCopy(root)
        self.work = os.path.join(root, 'work')

    def pathdata(self, lv):
        return self.copy.pathdata(lv)

    def dice_pathdata(self, lv):
        import navkit
        return navkit.Project.dice_pathdata(self, lv)

    def out_dir(self, lv):
        d = os.path.join(self.work, 'navkit', lv)
        os.makedirs(d, exist_ok=True)
        return d

    def settings_path(self, lv):
        return os.path.join(self.out_dir(lv), 'navkit.json')

    def settings(self, lv):
        import json
        return json.load(open(self.settings_path(lv)))

    def samples(self, lv, suffix=''):
        return os.path.join(self.work, 'navsample', f'{lv}_navkit{suffix}.bin')


def installed_is_not_original(c, work):
    import navkit
    import navparity
    import navregress
    pr = StandIn(os.path.join(work, 'opfor1'))
    lv = 'mp_07_koth'
    ours = os.path.join(pr.out_dir(lv), 'pathdata.hpd')
    open(ours, 'wb').write(b'navkit build')
    with patched(navkit, game_running=lambda: False):
        c.check(pr.dice_pathdata(lv) is None and not os.path.isfile(navparity.reference_of(pr.root, lv)),
                'no path data: no original')
        navkit.install(pr, lv, src=ours, log=QUIET)
        dest = pr.pathdata(lv)
        c.check(os.path.isfile(dest) and os.path.isfile(dest + navkit.NEW_SUFFIX), 'stand-in install wrote the file and marker')
        c.check(pr.dice_pathdata(lv) is None, 'dice_pathdata: NavKit\'s install (.opfor-new) is not DICE\'s original')
        c.check(not os.path.isfile(navparity.reference_of(pr.root, lv)),
                'navparity.reference_of: NavKit\'s install is not a reference')
        c.check(navregress.reference_bounds(pr.root, lv) is None, 'navregress.reference_bounds: none after install')
        navkit.install(pr, lv, src=ours, log=QUIET)                 # a second install: still no original
        c.check(pr.dice_pathdata(lv) is None, 'dice_pathdata: still none after a second install')
        navkit.restore(pr, lv, log=QUIET)
        # a level with an original: the file itself, then its backup once NavKit's is installed
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, 'wb').write(b'dice')
        c.check(pr.dice_pathdata(lv) == dest and navparity.reference_of(pr.root, lv) == dest,
                'an original without markers is the original')
        navkit.install(pr, lv, src=ours, log=QUIET)
        c.check(pr.dice_pathdata(lv) == dest + navkit.BACKUP_SUFFIX
                and navparity.reference_of(pr.root, lv) == dest + navkit.BACKUP_SUFFIX,
                'installed over an original: its backup is the original')
        navkit.restore(pr, lv, log=QUIET)


def apply_rekeys_cells(c, work):
    import numpy as np
    import navbuild
    import navkit
    hpd = os.path.join(work, 'rekey', 'pathdata.hpd')
    os.makedirs(os.path.dirname(hpd))
    open(hpd, 'wb').write(b'before the graph edits')

    def write_cells(key):
        np.savez_compressed(navbuild.cells_path(hpd), sector=np.array([0x101], np.int32), pitch=np.array([0.8], np.float32),
                            count=np.array([2], np.int64), i=np.array([1, 2], np.int32), j=np.array([3, 4], np.int32),
                            y=np.array([5, 6], np.float32), hpd_sha256=np.array(key))
    before = hashlib.sha256(open(hpd, 'rb').read()).hexdigest()
    write_cells(before)
    c.check(navbuild.read_cells(hpd) is not None, 'cells belong to the build')
    open(hpd, 'wb').write(b'after the graph edits')                  # what navlinks.apply + hpdtool.write do
    c.check(navbuild.read_cells(hpd) is None, 'the edited file no longer matches its cells (the defect)')
    c.check(navkit.rekey_cells(hpd, before, QUIET), 'rekey_cells re-keys them')
    cells = navbuild.read_cells(hpd)
    c.check(cells is not None and len(cells) == 1 and cells[0][0] == 0x101 and list(cells[0][2]) == [1, 2]
            and list(cells[0][4]) == [5, 6], 'the re-keyed cells read back, areas unchanged')
    write_cells('a stale key')
    c.check(not navkit.rekey_cells(hpd, before, QUIET) and navbuild.read_cells(hpd) is None,
            'a stale cells file is not re-keyed')
    src = open(os.path.join(SUITE, 'moh-auto-navkit', 'navkit.py'), encoding='utf-8').read()
    c.check('rekey_cells(out, before, log)' in src, '_apply calls rekey_cells after the graph edits')


def box_recomputes_offsets(c, work):
    import json
    import navkit
    import navregress
    import navsample_offline
    pr = StandIn(os.path.join(work, 'opfor3'))
    lv = 'mp_01'
    json.dump({'level': 'MP_01', 'box': [0, 0, 300, 300], 'y': [0, 100], 'box_source': 'combat',
               'offsets_source': 'rule: box', 'xoff': -1, 'zoff': -1}, open(pr.settings_path(lv), 'w'))
    with patched(navregress, reference_bounds=lambda root, lv: None), \
            patched(navsample_offline, sample_level=lambda root, lv, log=None: {'p08': [], 'p02': None, 'detailed': []}):
        navkit.sample_level(pr, lv, log=QUIET, box=[-150, -20, 300, 300])
        s = pr.settings(lv)
        c.check(s['box'] == [-150, -20, 300, 300] and (s['xoff'], s['zoff']) == (-3, -2),
                'sample --box: rule offsets recomputed from the new box', f"{s['xoff']},{s['zoff']}")
        s.update(offsets_source='SectorOffsets', xoff=5, zoff=6)
        json.dump(s, open(pr.settings_path(lv), 'w'))
        navkit.sample_level(pr, lv, log=QUIET, box=[-450, -20, 300, 300])
        s = pr.settings(lv)
        c.check((s['xoff'], s['zoff']) == (5, 6), 'a SectorOffsets asset\'s offsets are kept')


def option_parsing(c, work):
    import navkit
    calls = []

    class FakeProject:
        def __init__(self, root=None):
            pass

        def level(self, name):
            return name.lower()
    stubs = {n: (lambda n: lambda *a, **k: calls.append((n, a[1] if len(a) > 1 else None, k.get('pitch'))))(n)
             for n in ('sample', 'sample_level', 'sample_level_ingame')}
    with patched(navkit, Project=FakeProject, **stubs):
        def run(*argv):
            del calls[:]
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    rc = navkit.main(['navkit.py'] + list(argv))
            except Exception as e:      # noqa: BLE001 - the defect was an exception
                rc = f'{type(e).__name__}: {e}'
            return rc, list(calls), out.getvalue()
        rc, cl, _ = run('sample', 'mp_07_koth', '--ingame')
        c.check(rc == 0 and cl == [('sample_level_ingame', 'mp_07_koth', None)], 'sample lv --ingame: the in-game route', cl)
        rc, cl, _ = run('sample', '--ingame', 'mp_07_koth')
        c.check(rc == 0 and cl == [('sample_level_ingame', 'mp_07_koth', None)], 'sample --ingame lv: the level, not "--ingame"', cl)
        rc, cl, _ = run('sample', 'mp_07_koth', '--ingame', '--pitch', '0.2')
        c.check(rc == 0 and cl == [('sample', 'mp_07_koth', 0.2)], 'sample --ingame --pitch 0.2: that pass', cl)
        rc, cl, out = run('sample', 'mp_07_koth', '--pitch', '0.2')
        c.check(rc == 1 and not cl and '--pitch' in out, '--pitch without --ingame is refused, no resample', f'{rc} {cl}')
        rc, cl, out = run('sample', 'mp_07_koth', '--box')
        c.check(rc == 1 and not cl and 'needs a value' in out, 'an option without a value: a clear error', f'{rc} {out!r}')
        rc, cl, out = run('sample', 'mp_07_koth', '--box', '--yes')
        c.check(rc == 1 and not cl and 'needs a value' in out, 'an option followed by another: a clear error', f'{rc} {out!r}')
        rc, cl, out = run('sample', 'mp_07_koth', '--box', '1,2,3')
        c.check(rc == 1 and not cl and '4 comma-separated' in out, '--box with 3 numbers: a clear error', f'{rc} {out!r}')
        rc, cl, _ = run('sample', 'mp_07_koth', '--box', '-10,-20,30,40', '--y', '-5,100', '--yes')
        c.check(rc == 0 and cl == [('sample_level', 'mp_07_koth', None)], 'negative --box / --y values parse', f'{rc} {cl}')


def load_gui():
    path = os.path.join(SUITE, 'moh-auto-navkit', 'navkitGUI.pyw')
    loader = importlib.machinery.SourceFileLoader('navkitGUI_t', path)
    spec = importlib.util.spec_from_loader('navkitGUI_t', loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def pid_alive(pid):
    r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True)
    return str(pid) in r.stdout


def gui_kill_and_installed(c, work):
    import navkit
    gui = load_gui()
    # a parent (navkit.py) with a child (FbrbTool ...): both must end
    code = ('import subprocess, sys, time; p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]); '
            'print(p.pid, flush=True); time.sleep(120)')
    parent = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    try:
        c.check(pid_alive(child), 'the stand-in step started a child')
        gui.kill_tree(parent)
        parent.wait(10)
        for _ in range(20):
            if not pid_alive(child):
                break
            time.sleep(0.25)
        c.check(not pid_alive(child), 'GUI cancel: the step\'s child process ends too (kill_tree)')
    finally:
        if pid_alive(child):
            subprocess.run(['taskkill', '/F', '/PID', str(child)], capture_output=True)
    src = open(os.path.join(SUITE, 'moh-auto-navkit', 'navkitGUI.pyw'), encoding='utf-8').read()
    c.check('kill_tree(self.proc)' in src and 'self.proc.kill()' not in src, 'App.stop uses kill_tree')
    pr = StandIn(os.path.join(work, 'opfor5'))
    dest = pr.pathdata('mp_02')
    os.makedirs(os.path.dirname(dest))
    c.check(not gui.installed(pr, 'mp_02'), 'GUI: nothing installed')
    open(dest + navkit.NEW_SUFFIX, 'w').close()
    c.check(gui.installed(pr, 'mp_02'), 'GUI: an .opfor-new install shows as installed')
    os.remove(dest + navkit.NEW_SUFFIX)
    open(dest + navkit.BACKUP_SUFFIX, 'w').close()
    c.check(gui.installed(pr, 'mp_02'), 'GUI: an .opfor-orig install shows as installed')


def export_pass_rule(c, work):
    import navkit
    good = {'placed': 5, 'either_way': 10, 'unordered': 10, 'smooth_failed': 0, 'reference_equal': [20, 20]}
    fake_export = types.ModuleType('navexport')
    fake_export.export = lambda root, lv, log=None: ('x.rsnv', None)
    fake_export.check = lambda path, nav, log=None: ([], None)
    result = {}
    fake_check = types.ModuleType('rsnavcheck')
    fake_check.find_rsnav = lambda: 'rsnav'
    fake_check.bindings = lambda d: 'rb'
    fake_check.check_level = lambda root, lv, rb, log=None: dict(result)
    saved = {k: sys.modules.get(k) for k in ('navexport', 'rsnavcheck')}
    sys.modules.update(navexport=fake_export, rsnavcheck=fake_check)
    pr = types.SimpleNamespace(root=work)
    try:
        for what, change, ok in (('a passing check', {}, True), ('one point placed', {'placed': 1}, False),
                                 ('a pair not routed', {'either_way': 9}, False),
                                 ('a smoothing failure', {'smooth_failed': 1}, False),
                                 ('C != Python', {'reference_equal': [19, 20]}, False)):
            result.clear()
            result.update(good, **change)
            try:
                navkit.export(pr, 'mp_01', log=QUIET)
                passed = True
            except navkit.NavKitError:
                passed = False
            c.check(passed == ok, f'export: {what} {"passes" if ok else "fails"}')
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def navregress_options(c, work):
    import navregress
    args, bad = navregress.check_options(['--save'])
    c.check(bad is None and args == ['--save', 'regression'], '--save alone: saved as "regression"', args)
    args, bad = navregress.check_options(['--save', '--levels', 'mp_01'])
    c.check(bad is None and args[args.index('--save') + 1] == 'regression' and args[args.index('--levels') + 1] == 'mp_01',
            '--save --levels x: not saved as "--levels"', args)
    args, bad = navregress.check_options(['--reference', 'mine', '--save'])
    c.check(bad is None and args[-1] == 'mine', '--save alone with --reference: the reference name', args)
    _, bad = navregress.check_options(['--levels'])
    c.check(bad and 'needs a value' in bad, '--levels without a value: an error before the run')
    _, bad = navregress.check_options(['--tolerance', 'x'])
    c.check(bad and 'tolerance' in bad, '--tolerance x: an error before the run')
    with contextlib.redirect_stdout(io.StringIO()), \
            patched(navregress.navparity, opfor_root=lambda g=None: (_ for _ in ()).throw(AssertionError('ran'))):
        rc = navregress.main(['navregress.py', '--levels'])
    c.check(rc == 2, 'navregress main: a bad option exits 2 before finding OPFOR or building')


def navparity_show(c, work):
    import navparity
    import navregress
    own = {'level': 'mp_07_koth', 'mode': 'self-check (no DICE path data)', **{k: 90.0 for k in navregress.SELF_HIGHER},
           **{k: 7 for k in navregress.SELF_COUNTS}}
    lines = []
    try:
        navparity.table({'name': 'regression', 'date': 'd', 'levels': [own]}, log=lines.append)
        ok = any(l.startswith('mp_07_koth') for l in lines)
    except Exception as e:      # noqa: BLE001
        ok, lines = False, [repr(e)]
    c.check(ok, 'navparity show: a self-check row is shown', lines[-1:])
    lines = []
    try:
        navparity.table({'name': 'r', 'date': 'd', 'levels': [own]}, {'name': 'b', 'date': 'd', 'levels': [dict(own, self_on_area=80.0)]},
                        log=lines.append)
        ok = any('+10.0' in l for l in lines)
    except Exception as e:      # noqa: BLE001
        ok, lines = False, [repr(e)]
    c.check(ok, 'navparity show: two self-check runs compared', lines[-1:])
    lines = []
    try:
        navparity.table({'name': 'empty', 'date': 'd', 'levels': []}, log=lines.append)
        ok = any('no levels' in l for l in lines)
    except Exception as e:      # noqa: BLE001
        ok, lines = False, [repr(e)]
    c.check(ok, 'navparity show: an empty run', lines[-1:])


def navdiff_png_grid(c, work):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.axes
    import navdiff
    xs = []
    real = matplotlib.axes.Axes.scatter

    def scatter(self, x, y, *a, **k):
        xs.extend(x)
        return real(self, x, y, *a, **k)
    sec = types.SimpleNamespace(mesh=types.SimpleNamespace(pitch=0.2), cells=lambda: [{(100, 50): 0.0}])
    rows = [{'id': 0x0102, 'only_ref': [(110, 50)], 'only_cand': []}]
    with patched(matplotlib.axes.Axes, scatter=scatter):
        navdiff.pngs(rows, {0x0102: sec}, {}, os.path.join(work, 'png'), 1)
    c.check(sorted(xs) == [100 * navdiff.GRID, 110 * navdiff.GRID], 'navdiff --png: a detailed sector\'s cells at 0.8 m', xs)


def sample_all_default(c, work):
    import navkit
    import sample_all
    got = []
    pr = types.SimpleNamespace(work=work, samples08=lambda lv: 'x.bin')
    with patched(navkit, Project=lambda root=None: pr,
                 sample=lambda pr, lv, log=None, pitch=0.8, budget_ms=None: got.append(pitch) or True), \
            contextlib.redirect_stdout(io.StringIO()):
        rc = sample_all.main(['sample_all.py', '--levels', 'mp_01', '--log', os.path.join(work, 'sample_all.log')])
    c.check(rc == 0 and got == [0.8], 'sample_all: the 0.8 m pair by default', got)


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='moh-review3-')
    try:
        for t in (installed_is_not_original, apply_rekeys_cells, box_recomputes_offsets, option_parsing,
                  gui_kill_and_installed, export_pass_rule, navregress_options, navparity_show, navdiff_png_grid,
                  sample_all_default):
            try:
                t(c, work)
            except Exception as e:      # noqa: BLE001 - one broken case must not hide the others
                import traceback
                traceback.print_exc()
                c.check(False, f'{t.__name__} raised', repr(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
