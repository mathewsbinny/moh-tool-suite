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
- sample_all.py defaults to the 0.8 m pair;
(2026-11-05, the NavKit window's and the suite window's run state:)
- the GUI's Cancel never closes the game (close_game), a step that cannot start leaves the buttons usable, and a
  Cancel after the step ended keeps its failure;
- the GUI's pump outlives an error, a damaged navkit.json is shown, not raised; navkit.json is written atomically;
- navkit.stage_status: done / out of date / missing from the files' times; a plain build records itself and notes
  (the GUI asks) when it replaces an apply build;
- the suite window: a stopped run is matched by its process (a Studio window closing does not clear it), an earlier
  run's exit does not end a newer run's Stop, and its pump outlives an error.

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
        return navkit.original_pathdata(self.pathdata(lv))     # as Project.dice_pathdata (patched in GUI tests)

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

    def samples08(self, lv):
        for name in (f'{lv}_navkit', f'{lv}_full'):              # as navkit.Project.samples08 (patched in GUI tests)
            q = os.path.join(self.work, 'navsample', name + '.bin')
            if os.path.isfile(q):
                return q
        return None


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


STAND_IN_STEP = '''import sys, time
cmd = sys.argv[1]
print("stand-in", cmd, flush=True)
if cmd == "sample":
    time.sleep(30)
print("error: the stand-in step failed" if cmd == "build" else "done", flush=True)
sys.exit(3 if cmd == "build" else 0)
'''


def settle(root, cond=lambda: False, timeout=10.0):
    """Runs the Tk loop until cond() holds or the time is up; returns cond()."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        root.update()
        if cond():
            return True
        time.sleep(0.05)
    return cond()


@contextlib.contextmanager
def gui_app(work, name):
    """The NavKit window on a stand-in project (one level, mp_01), its dialogs recorded and answered, its steps a
    stand-in script (STAND_IN_STEP), close_game and game_running recorded; yields (gui, app, pr, calls)."""
    import tkinter as tk
    import navkit
    gui = load_gui()
    pr = StandIn(os.path.join(work, name))
    os.makedirs(pr.root, exist_ok=True)
    script = os.path.join(pr.root, 'standin_navkit.py')
    open(script, 'w').write(STAND_IN_STEP)
    calls = {'told': [], 'answers': [], 'game': [], 'errors': []}

    def dialog(kind):
        def f(title, text, **k):
            calls['told'].append((kind, text))
            return calls['answers'].pop(0) if calls['answers'] else True
        return f
    box = types.SimpleNamespace(**{k: dialog(k) for k in ('showerror', 'showwarning', 'showinfo', 'askokcancel', 'askyesno')})
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = lambda t, v, tb: calls['errors'].append(v)
    try:
        with patched(navkit, Project=lambda root=None: pr, level_names=lambda p: [('mp_01', False)],
                     close_game=lambda: calls['game'].append('close_game'),
                     game_running=lambda: calls['game'].append('game_running') or False), \
                patched(gui, messagebox=box, NAVKIT=script):
            app = gui.App(root)
            yield gui, app, pr, calls
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def gui_cancel_and_start(c, work):
    with gui_app(work, 'opfor_gui1') as (gui, app, pr, calls):
        root = app.root
        app.run_step('sample')
        c.check(app.proc is not None and str(app.cancel['state']) == 'normal', 'GUI: a step runs, Cancel enabled')
        settle(root, lambda: 'stand-in sample' in app.log.get('1.0', 'end'))
        t0 = time.time()
        app.stop()
        took = time.time() - t0
        c.check(not calls['game'], 'GUI Cancel: close_game / game_running never called (offline sampling)', calls['game'])
        c.check(took < 3, f'GUI Cancel: returns at once ({took:.1f} s; close_game held it up to 12 s)')
        settle(root, lambda: app.proc is None)
        c.check(app.proc is None and str(app.buttons[0]['state']) == 'normal'
                and not any(k == 'showwarning' for k, _ in calls['told']), 'GUI Cancel: the step ends, no failure warning')
        app.close()                                                 # nothing running: closes without the game either
        c.check(not calls['game'], 'GUI close: no close_game')

    with gui_app(work, 'opfor_gui2') as (gui, app, pr, calls):
        with patched(gui, PYTHON=os.path.join(work, 'no_such_folder', 'python.exe')):
            app.run_step('check')
        c.check(any(k == 'showerror' for k, _ in calls['told']), 'GUI: a step that cannot start is reported')
        c.check(app.proc is None and all(str(b['state']) == 'normal' for b in app.buttons)
                and str(app.cancel['state']) == 'disabled' and str(app.combo['state']) == 'readonly',
                'GUI: a step that cannot start leaves the buttons usable')

        app.run_step('build')                                       # the stand-in exits 3 at once
        proc = app.proc
        t0 = time.time()
        while proc.poll() is None and time.time() - t0 < 10:       # ended, its exit not yet pumped (no Tk loop)
            time.sleep(0.05)
        app.stop()
        c.check(not app.cancelled, 'GUI: Cancel after the step ended does not mark it cancelled')
        settle(app.root, lambda: app.proc is None)
        warned = [t for k, t in calls['told'] if k == 'showwarning']
        c.check(warned and 'exit code 3' in warned[-1], 'GUI: ... so its real failure is still reported', calls['told'])
        c.check(not calls['game'], 'GUI: no close_game on that Cancel either')


def gui_pump_and_settings(c, work):
    import navkit
    with gui_app(work, 'opfor_gui3') as (gui, app, pr, calls):
        root = app.root
        p = pr.settings_path('mp_01')
        for what, text in (('cut off', '{"level": "MP_01", "box": [0, 0, 3'), ('without y', '{"box": [0, 0, 300, 300]}'),
                           ('a bad box', '{"box": [0, 0], "y": [0, 1]}')):
            open(p, 'w').write(text)
            try:
                app.refresh()
                ok = 'navkit.json' in app.problem and 'navkit.json' in str(app.status['text']) \
                    and app.marks['prepare']['text'] == 'old' and app.vars['minx'].get() == ''
            except Exception as e:      # noqa: BLE001 - the defect was an exception
                ok = False
                calls['errors'].append(e)
            c.check(ok, f'GUI: a navkit.json {what} is shown in the status, not raised', app.problem)
        os.remove(p)

        def broken():
            raise RuntimeError('refresh broke')
        app.refresh = broken
        app.proc = subprocess.Popen([sys.executable, '-c', 'pass'])
        app.set_busy(True, 'running')
        app.lines.put(('done', 'build', 0))
        settle(root, lambda: calls['errors'], 3)
        c.check(any('refresh broke' in str(e) for e in calls['errors']), 'GUI pump: the error is reported')
        c.check(app.proc is None and str(app.buttons[0]['state']) == 'normal', 'GUI pump: the step still counts as ended')
        del app.refresh
        app.lines.put('after the error\n')
        c.check(settle(root, lambda: 'after the error' in app.log.get('1.0', 'end'), 3),
                'GUI pump: still running after an error (try/finally)')

    # navkit.json and the other JSON files are written whole or not at all
    target = os.path.join(work, 'atomic.json')
    navkit.write_json(target, {'box': [1, 2, 3, 4]})
    real = os.replace
    try:
        os.replace = lambda *a: (_ for _ in ()).throw(OSError('killed mid-write'))
        try:
            navkit.write_json(target, {'box': [9, 9, 9, 9]})
        except OSError:
            pass
    finally:
        os.replace = real
    import json
    c.check(json.load(open(target)) == {'box': [1, 2, 3, 4]} and not os.path.exists(target + '.tmp'),
            'write_json: a write cut off leaves the old file whole and no temporary file')
    src = open(os.path.join(SUITE, 'moh-auto-navkit', 'navkit.py'), encoding='utf-8').read()
    import re
    c.check(not re.search(r'json\.dump\([^\n]*open\(', src) and src.count('write_json(pr.settings_path(lv)') == 3,
            'navkit.py: every navkit.json write goes through write_json')


def stage_status_chain(c, work):
    import navkit
    S = navkit.Status
    pr = StandIn(os.path.join(work, 'opfor_stages'))
    lv = 'mp_01'
    d = pr.out_dir(lv)
    os.makedirs(os.path.join(pr.work, 'navsample'), exist_ok=True)
    files = {'json': pr.settings_path(lv), 's': pr.samples(lv), 'hi': pr.samples(lv, '_hi'),
             'hpd': os.path.join(d, 'pathdata.hpd'), 'check': os.path.join(d, 'check.txt'), 'rsnv': os.path.join(d, f'{lv}.rsnv')}
    st = lambda: navkit.stage_status(pr, lv)    # noqa: E731
    c.check(st() == {k: S.MISSING for k in navkit.STAGES}, 'stage_status: nothing done - all missing', st())
    t = time.time() - 1000

    def touch(key_or_path, at):
        path = files.get(key_or_path, key_or_path)
        if not os.path.isfile(path):
            open(path, 'w').write('{"box": [0, 0, 300, 300], "y": [0, 100]}' if path == files['json'] else 'x')
        os.utime(path, (t + at, t + at))

    def chain():
        for i, k in enumerate(('json', 's', 'hi', 'hpd', 'check', 'rsnv')):
            touch(k, i * 10)
    chain()
    c.check(st() == {k: S.DONE for k in navkit.STAGES}, 'stage_status: in order - all done', st())
    touch('json', 100)
    c.check(st() == {'prepare': S.DONE, 'sample': S.STALE, 'build': S.STALE, 'check': S.STALE, 'export': S.STALE},
            'stage_status: re-prepared - sample, build, check and export out of date', st())
    chain()
    touch('hi', 35)
    c.check(st() == {'prepare': S.DONE, 'sample': S.DONE, 'build': S.STALE, 'check': S.STALE, 'export': S.STALE},
            'stage_status: re-sampled after the build - build, check, export out of date', st())
    chain()
    touch('check', 25)
    c.check(st()['check'] is S.STALE and st()['export'] is S.DONE,
            'stage_status: a check older than the build is out of date; the export follows the build, not check.txt', st())
    chain()
    p02 = pr.samples(lv, '_p02')
    touch(p02, 35)
    c.check(st()['sample'] is S.DONE and st()['build'] is S.STALE, 'stage_status: a newer detailed 0.2 m sample file '
            'makes the build out of date', st())
    os.remove(p02)
    chain()
    os.remove(files['hi'])
    c.check(st()['sample'] is S.MISSING and st()['build'] is S.STALE, 'stage_status: samples gone - the build is out of '
            'date', st())
    chain()
    # the GUI shows them: a tick, "old", nothing
    with gui_app(work, 'opfor_stages') as (gui, app, _pr, calls):
        touch('hi', 35)
        app.refresh()
        marks = {k: app.marks[k]['text'] for k in app.marks}
        c.check(marks == {'prepare': '✔', 'sample': '✔', 'build': 'old', 'check': 'old'}
                and 'Out of date' in app.installed['text'] and 'build, check, export' in app.installed['text'],
                'GUI: out-of-date steps are shown as "old", distinct from done', marks)


def build_records(c, work):
    import navkit
    from mohcore import navtune
    pr = StandIn(os.path.join(work, 'opfor_records'))
    lv = 'mp_01'
    hpd = os.path.join(pr.out_dir(lv), 'pathdata.hpd')
    fake = types.ModuleType('navbuild')
    fake.build = lambda samples, forbid, xoff, zoff, out, **kw: open(out, 'wb').write(b'plain build ' + os.urandom(4))
    saved = sys.modules.get('navbuild')
    sys.modules['navbuild'] = fake
    lines = []
    try:
        with patched(navkit, build_inputs=lambda pr, lv, log=None: ('s', 'f', 0, 0, {})):
            navkit.build(pr, lv, log=lines.append)
            rec = navkit.build_record(hpd)
            c.check(rec and rec['kind'] == 'build' and not any('note' in l for l in lines), 'build: recorded as a plain build')
            navkit.write_build_record(hpd, 'apply', 'my.navtune.json')
            del lines[:]
            navkit.build(pr, lv, log=lines.append)
            c.check(any('note' in l and 'apply' in l and 'my.navtune.json' in l for l in lines),
                    'build over an apply build: a note says so', lines)
            c.check(navkit.build_record(hpd)['kind'] == 'build', 'build: the record is a plain build again')
            navkit.write_build_record(hpd, 'apply', 'my.navtune.json')
            open(hpd, 'ab').write(b'changed')
            c.check(navkit.build_record(hpd) is None, 'a record of an earlier file is not read (SHA-256)')
    finally:
        if saved is None:
            sys.modules.pop('navbuild', None)
        else:
            sys.modules['navbuild'] = saved

    def fake_apply(pr, lv, navtune_path, out, log):
        open(out, 'wb').write(b'apply build')
        return out
    with patched(navkit, _apply=fake_apply):
        navkit.apply(pr, lv, 'given.navtune.json', hpd, log=QUIET)
        rec = navkit.build_record(hpd)
        c.check(rec and rec['kind'] == 'apply' and rec['navtune'] == 'given.navtune.json', 'apply: recorded with its side file')
        navkit.apply(pr, lv, None, hpd, log=QUIET)
        c.check(navkit.build_record(hpd)['navtune'] == navtune.default_path(pr.root, lv),
                'apply: the default side file is recorded')

    # the GUI asks before a plain Build replaces an apply build
    with gui_app(work, 'opfor_records') as (gui, app, _pr, calls):
        calls['answers'].append(False)
        app.run_step('build')
        asked = [t for k, t in calls['told'] if k == 'askyesno']
        c.check(asked and 'edits' in asked[-1] and app.proc is None, 'GUI Build over an apply build: asked; No keeps it')
        calls['answers'].append(True)
        app.run_step('build')
        c.check(app.proc is not None, 'GUI Build over an apply build: Yes builds')
        settle(app.root, lambda: app.proc is None)
        navkit.write_build_record(hpd, 'build')
        n = len(calls['told'])
        app.run_step('check')
        settle(app.root, lambda: app.proc is None)
        c.check(not any(k == 'askyesno' for k, _ in calls['told'][n:]), 'GUI: no question for other steps or a plain build')


def load_hub():
    spec = importlib.util.spec_from_file_location('suite_hub_t', os.path.join(SUITE, 'MOH Tool Suite.pyw'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hub_run_state(c, work):
    import threading
    import tkinter as tk
    import navkit
    hubmod = load_hub()
    told = []
    root = tk.Tk()
    root.withdraw()
    errs = []
    py = sys.executable

    def no_project(root=None):
        raise navkit.NavKitError('no game copy in this test')
    try:
        with patched(hubmod.Hub, notify=lambda self, kind, title, text: told.append((kind, text))), \
                patched(navkit, Project=no_project):
            hub = hubmod.Hub(root)
        hub.notify = lambda kind, title, text: told.append((kind, text))
        root.report_callback_exception = lambda t, v, tb: errs.append(v)
        del told[:]
        # a test tier stopped while a Studio window closes: the window's exit must not clear the stop
        gate = threading.Event()
        reader = hub.reader
        hub.reader = lambda proc, label, main: (gate.wait(20), reader(proc, label, main))
        hub.run([py, '-c', 'import time; time.sleep(30)'], 'Tests: quick')
        hub.stop()
        studio = subprocess.Popen([py, '-c', 'pass'])
        studio.wait()
        hub.lines.put(('done', 'Navigation Studio: mp_01', 0, False, studio))
        settle(root, timeout=0.3)
        gate.set()
        settle(root, lambda: hub.proc is None)
        del hub.reader
        c.check(not told, 'hub: a stopped run is not a failure, though a launched window closed first', told)
        c.check(hub.status.get() == 'Ready.' and str(hub.stop_btn['state']) == 'disabled', 'hub: the stopped run ends')

        # an earlier run's exit, shown late, does not end a newer run's Stop
        hub.run([py, '-c', 'print("first")'], 'Tests: first')
        first = hub.proc
        first.wait(10)
        time.sleep(0.5)                                            # its exit queued, not pumped (no Tk loop)
        hub.run([py, '-c', 'import time; time.sleep(30)'], 'Tests: second')
        second = hub.proc
        settle(root, lambda: 'done: Tests: first' in hub.text.get('1.0', 'end'), 3)
        c.check(second is not first and hub.proc is second and str(hub.stop_btn['state']) == 'normal'
                and hub.status.get() == 'Running: Tests: second',
                'hub: the first run\'s late exit leaves the second run\'s status and Stop', hub.status.get())
        hub.stop()
        settle(root, lambda: hub.proc is None)
        c.check(not told and str(hub.stop_btn['state']) == 'disabled', 'hub: the second run stopped, no failure')

        # the stop is matched to its run: a later failing run is still reported
        hub.run([py, '-c', 'print("error: broken"); raise SystemExit(3)'], 'A failing tool')
        settle(root, lambda: hub.proc is None)
        c.check(told and 'exit code 3' in told[-1][1], 'hub: a failing run after a stopped one is reported')

        # the pump outlives an error in an item
        hub.lines.put(('done', 'malformed'))
        settle(root, lambda: errs, 3)
        hub.lines.put('still pumping\n')
        c.check(errs and settle(root, lambda: 'still pumping' in hub.text.get('1.0', 'end'), 3),
                'hub pump: still running after an error (try/finally)')
    finally:
        root.destroy()


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='moh-review3-')
    try:
        for t in (installed_is_not_original, apply_rekeys_cells, box_recomputes_offsets, option_parsing,
                  gui_kill_and_installed, export_pass_rule, navregress_options, navparity_show, navdiff_png_grid,
                  sample_all_default, gui_cancel_and_start, gui_pump_and_settings, stage_status_chain, build_records,
                  hub_run_state):
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
