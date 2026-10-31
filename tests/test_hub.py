r"""The suite's window (MOH Tool Suite.pyw) driven as clicks would drive it: it opens with its 7 tabs and the
game copy's levels; FbrbTool lists a bundle, LevelDiff compares a level with itself, the crash reader reads a
report built here, the test runner lists a tier - each through its tab's button, its output in the pane; and
the 3D tools' level picker lists the levels. On MOH: the 26 MP levels, each prepared, none with DICE path data
(MoHMPGame.exe ships none), mp_01 with its NavKit build; mp_01's terrain bundle, its level partition, and a crash
report of build 592256 at a known MoHMPGame.exe function. Needs a display and Project OPFOR's game copy, its
levels prepared and mp_01 built by NavKit.

  python tests\test_hub.py [--shots <folder>]       --shots: a screenshot of every tab (for a look)
"""
import importlib.util
import os
import sys
import tempfile
import time

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))


def load_hub():
    spec = importlib.util.spec_from_file_location('suite_hub', os.path.join(SUITE, 'MOH Tool Suite.pyw'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    import tkinter as tk
    hubmod = load_hub()
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    hubmod.dpi_aware()
    root = tk.Tk()
    root.geometry('1100x760+20+20')
    hub = hubmod.Hub(root)
    root.update()

    def wait(timeout=300):
        t0 = time.time()
        while time.time() - t0 < timeout:
            root.update()
            if hub.proc is None or hub.proc.poll() is not None:
                for _ in range(10):                      # let the last lines and the 'done' line arrive
                    root.update()
                    time.sleep(0.05)
                return
            time.sleep(0.05)

    def output():
        return hub.text.get('1.0', 'end')

    tabs = [hub.tabs.tab(t, 'text') for t in hub.tabs.tabs()]
    check(tabs == ['Levels', 'Bundles', 'Level diff', 'Crash reader', 'Xbox files', 'Tests', 'More tools'], f'7 tabs: {", ".join(tabs)}')
    levels = hub.tree.get_children()
    check(len(levels) == 26 and 'mp_01' in levels and 'mp_03_elimination' in levels and 'mp_10_tdm' in levels,
          f'the Levels tab lists the game copy\'s levels ({len(levels)}: the 26 MP levels, base and DLC)')
    check(all(hub.tree.set(lv, 'prepared') == 'yes' and hub.tree.set(lv, 'dice') == '-' for lv in levels)
          and 'mp_01' in levels and hub.tree.set('mp_01', 'navkit') == 'yes',
          'with what each has (every level prepared, no DICE path data; mp_01: a NavKit build)')

    if '--shots' in argv:
        from PIL import ImageGrab
        folder = argv[argv.index('--shots') + 1]
        os.makedirs(folder, exist_ok=True)
        for k, t in enumerate(hub.tabs.tabs()):
            hub.tabs.select(t)
            for _ in range(5):
                root.update()
                time.sleep(0.05)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(os.path.join(folder, f'hub_{k}.png'))

    bundle = os.path.join(hub.pr.game, 'dist', 'win32', 'levels', 'mp_01', 'terrain-00.fbrb')
    hub.fb_file.set(bundle)
    hub.tabs.select(1)
    hub.run([hubmod.tool('fbrb'), 'list', hub.fb_file.get()], 'FbrbTool list')
    wait()
    check('entries, payload' in output() and 'Heightfield' in output(), 'Bundles: FbrbTool lists a terrain bundle')

    lvl = os.path.join(hub.pr.work, 'levels', 'mp_01', 'dist', 'levels', 'MP_01.dbx')
    hub.ld_a.set(lvl)
    hub.ld_b.set(lvl)
    hub.ld_summary.set(True)
    hub.diff()
    wait()
    check('changed: 0; only in A: 0; only in B: 0' in output(), 'Level diff: a level against itself has no differences')

    tmp = tempfile.mkdtemp(prefix='hub_test_')
    rep = os.path.join(tmp, 'crashreport.xml')
    open(rep, 'w').write('<?xml version="1.0"?><report><type>crash</type><buildsignature>592256</buildsignature>'
                         '<categoryid> 0x407910</categoryid><stack> 0x407910 0x9349F0</stack>'
                         '<contextdata>registers: Eax 00000000, Eip 00407910, Esp 001ae65c</contextdata></report>')
    hub.cr_file.set(rep)
    hub.cr_nocode.set(True)
    hub.crash()
    wait()
    check('registry read' in output() and 'the OPFOR core sends this key' in output(),
          'Crash reader: names the crash site and gives the hint')

    hub.t_tier.set('quick')
    hub.run([hubmod.PYTHON, hubmod.SCRIPTS['tests'], 'quick', '--list'], 'Tests: list')
    wait()
    check('repository rules' in output(), 'Tests: lists what the quick tier runs')

    hub.tree.selection_set('mp_09_overrun')
    check(hub.chosen_level() == 'mp_09_overrun', 'a level chosen in the list is the one the 3D buttons open')

    # a tool that fails: the user is told, with what it said last; a stopped one is not a failure
    told = []
    hub.notify = lambda kind, title, text: told.append((kind, text))
    hub.run([hubmod.PYTHON, '-c', 'print("checking the input"); print("error: the bundle is damaged"); raise SystemExit(3)'],
            'A failing tool')
    wait()
    check(told and told[-1][0] == 'warning' and 'exit code 3' in told[-1][1] and 'the bundle is damaged' in told[-1][1],
          'a tool that fails: a warning with its exit code and its last lines')
    n = len(told)
    hub.run([hubmod.PYTHON, '-c', 'import time; time.sleep(30)'], 'A long tool')
    root.update()
    time.sleep(0.5)
    hub.stop()
    wait()
    check(len(told) == n, 'a tool the user stops: no failure warning')

    # Stop ends the tool's own children too (run_tests -> navregress, prepare -> FbrbTool)
    done = os.path.join(tmp, 'grandchild_done.txt')
    grandchild = f'import time; time.sleep(4); open({done!r}, "w").write("ran on")'
    hub.run([hubmod.PYTHON, '-c', f'import subprocess, sys; print("started", flush=True); '
             f'subprocess.run([sys.executable, "-c", {grandchild!r}])'], 'A tool with a child')
    root.update()
    time.sleep(1.5)
    hub.stop()
    t0 = time.time()
    while str(hub.stop_btn.cget('state')) != 'disabled' and time.time() - t0 < 10:   # the 'done' line arrived
        root.update()
        time.sleep(0.05)
    ended = time.time() - t0
    time.sleep(4.5)
    check(ended < 3 and not os.path.exists(done) and len(told) == n,
          f'Stop ends the tool and the process it started ({ended:.1f} s; the child never finished its work)')

    # LevelDiff's exit code 1 means the files differ: done, not a failure
    other = os.path.join(hub.pr.work, 'levels', 'mp_02', 'dist', 'levels', 'MP_02.dbx')
    hub.ld_b.set(other)
    hub.diff()
    wait()
    check(os.path.isfile(other) and 'done - the files differ: LevelDiff' in output() and len(told) == n,
          'Level diff: two different levels - "the files differ", no failure warning')
    check(root.report_callback_exception is not None, 'errors in the window\'s own buttons are caught and reported')
    root.destroy()

    # a 3D tool given a level the game does not have: a clean exit with the cause, and its log
    r = __import__('subprocess').run([hubmod.PYTHON, hubmod.SCRIPTS['studio'], 'no_such_level', '--screenshot', 'x.png'],
                                     capture_output=True, text=True, timeout=300)
    check(r.returncode == 1 and 'error:' in r.stderr and 'what to do' in r.stderr and 'details:' in r.stderr,
          'a 3D tool that cannot open a level exits cleanly, saying why and where the log is')

    from mohcore import pick
    items = pick.levels()
    check(len(items) == len(levels) and any(lv == 'mp_01' and s == 'prepared' for lv, s in items)
          and not any('DICE' in s for _, s in items),
          'the 3D tools\' level picker lists the same levels')
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
