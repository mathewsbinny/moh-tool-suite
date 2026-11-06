"""NavKit - a small Windows front end for navkit.py (double-click to start; needs Python 3 with numpy).

Every button runs the matching navkit.py step for the chosen level and shows its output; see QUICKSTART.md.
"""
import functools
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'core'))
from mohcore import errors  # noqa: E402
import navkit  # noqa: E402

PYTHON = os.path.join(os.path.dirname(sys.executable), 'python.exe')   # console python for the steps
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable
NAVKIT = os.path.join(HERE, 'navkit.py')     # the script every step runs (tests put a stand-in here)
MARKS = {navkit.Status.DONE: ('✔', '#060'), navkit.Status.STALE: ('old', '#b35900'), navkit.Status.MISSING: ('', '#000')}

STEPS = [('prepare', '1. Prepare', 'Unpack the level, extract its objects, choose the sampling box'),
         ('sample', '2. Sample', 'Samples the level\'s collision from its files - no game launch (a few minutes)'),
         ('build', '3. Build', 'Generate pathdata.hpd from the samples'),
         ('check', '4. Check', 'Score the result (against the original path data if the level has one)')]


def kill_tree(proc: subprocess.Popen[str]) -> None:
    """Ends a step and every process it started (FbrbTool, DbxEdit, ordercheck ...): Popen.kill ended navkit.py alone
    and left its children running on Windows (2026-10-31). taskkill /T /F; Popen.kill when taskkill is not there."""
    if proc.poll() is not None:
        return
    try:
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    if proc.poll() is None:
        proc.kill()


def installed(pr: navkit.Project, lv: str) -> bool:
    """NavKit's file is installed for the level: DICE's original backed up (.opfor-orig), or - a level that had no
    path data, every MOH MP level - the no-original marker (.opfor-new)."""
    p = pr.pathdata(lv)
    return os.path.isfile(p + navkit.BACKUP_SUFFIX) or os.path.isfile(p + navkit.NEW_SUFFIX)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: subprocess.Popen[str] | None = None
        self.saved: list[str] = []
        self.problem = ''                        # what is wrong with the level's files (a damaged navkit.json), or ''
        self.cancelled = False                   # Cancel ended a running step: its failure is not reported
        self.lines: queue.Queue[str | tuple[str, str, int]] = queue.Queue()     # output, and ('done', step, exit code)
        root.title('MOH NavKit')
        root.minsize(760, 520)
        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        try:
            self.pr = navkit.Project()
        except navkit.NavKitError as e:
            messagebox.showerror('NavKit', str(e))
            root.destroy()
            return

        top = ttk.Frame(root, padding=10)
        top.pack(fill='both', expand=True)
        self.level_row(top)
        self.area_box(top)
        self.steps_box(top)
        self.log_pane(top)
        self.fill_levels()
        root.after(100, self.pump)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def level_row(self, top: ttk.Frame) -> None:
        """The top row: the level list, the AI-levels-only box and the output folder button."""
        row = ttk.Frame(top)
        row.pack(fill='x')
        ttk.Label(row, text='Level:').pack(side='left')
        self.level = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.level, state='readonly', width=34)
        self.combo.pack(side='left', padx=(6, 6))
        self.combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        self.only_ai = tk.BooleanVar(value=False)       # MOH's MP levels ship no AI path data: all are listed
        ttk.Checkbutton(row, text='Only levels with AI', variable=self.only_ai,
                        command=self.fill_levels).pack(side='left')
        ttk.Button(row, text='Open output folder', command=self.open_folder).pack(side='right')

    def area_box(self, top: ttk.Frame) -> None:
        """The sampling area's six fields and the note on where the area came from."""
        box = ttk.LabelFrame(top, text='Sampling area (filled in by Prepare; edit to override)', padding=8)
        box.pack(fill='x', pady=(10, 0))
        self.vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate((('minx', 'min X'), ('minz', 'min Z'), ('maxx', 'max X'), ('maxz', 'max Z'),
                                          ('ybot', 'bottom Y'), ('ytop', 'top Y'))):
            ttk.Label(box, text=label).grid(row=0, column=i * 2, sticky='e', padx=(8 if i else 0, 4))
            v = tk.StringVar()
            ttk.Entry(box, textvariable=v, width=8).grid(row=0, column=i * 2 + 1)
            self.vars[key] = v
        self.area_note = ttk.Label(box, text='', foreground='#555')
        self.area_note.grid(row=1, column=0, columnspan=12, sticky='w', pady=(6, 0))

    def steps_box(self, top: ttk.Frame) -> None:
        """The step buttons with their done marks and tips, Run all / Install / Restore / Cancel and the install state."""
        steps = ttk.LabelFrame(top, text='Steps', padding=8)
        steps.pack(fill='x', pady=(10, 0))
        self.buttons: list[ttk.Button] = []
        self.marks: dict[str, ttk.Label] = {}
        for r, (cmd, label, tip) in enumerate(STEPS):
            b = ttk.Button(steps, text=label, width=20, command=functools.partial(self.run_step, cmd))
            b.grid(row=r, column=0, sticky='w', pady=2)
            self.buttons.append(b)
            m = ttk.Label(steps, text='', width=4)
            m.grid(row=r, column=1)
            self.marks[cmd] = m
            ttk.Label(steps, text=tip, foreground='#555').grid(row=r, column=2, sticky='w')
        side = ttk.Frame(steps)
        side.grid(row=0, column=3, rowspan=4, sticky='ne', padx=(20, 0))
        steps.columnconfigure(2, weight=1)
        for label, cmd in (('Run all (1-4)', 'all'), ('Install in game', 'install'), ('Restore original', 'restore')):
            b = ttk.Button(side, text=label, width=18, command=functools.partial(self.run_step, cmd))
            b.pack(pady=2)
            self.buttons.append(b)
        self.cancel = ttk.Button(side, text='Cancel', width=18, command=self.stop, state='disabled')
        self.cancel.pack(pady=(10, 2))
        self.installed = ttk.Label(steps, text='', foreground='#555')
        self.installed.grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 0))

    def log_pane(self, top: ttk.Frame) -> None:
        """The read-only output log and the status line below it."""
        logf = ttk.Frame(top)
        logf.pack(fill='both', expand=True, pady=(10, 0))
        self.log = tk.Text(logf, height=12, wrap='none', font=('Consolas', 9), state='disabled')
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.status = ttk.Label(top, text='Ready', anchor='w')
        self.status.pack(fill='x', pady=(6, 0))

    # --- levels and state ------------------------------------------------------------------------------
    def fill_levels(self) -> None:
        """Fills the level list (AI levels only, when that box is ticked) and keeps the chosen level when it is still
        listed."""
        names = navkit.level_names(self.pr)
        shown = [lv for lv, ai in names if ai or not self.only_ai.get()]
        self.combo['values'] = shown
        if self.level.get() not in shown:
            self.level.set(shown[0] if shown else '')
        self.refresh()

    def refresh(self) -> None:
        """Fills the sampling area from the level's navkit.json, marks each step done or out of date
        (navkit.stage_status) and says whose path data the game uses. A damaged navkit.json is said, not raised."""
        lv = self.level.get()
        if not lv:
            return
        self.problem = ''
        self.show_area(self.read_settings(lv))
        status = navkit.stage_status(self.pr, lv)
        if self.problem:
            status['prepare'] = navkit.Status.STALE      # there, but not usable: Prepare again
        for k, m in self.marks.items():
            m['text'], m['foreground'] = MARKS[status[k]]
        stale = [k for k in navkit.STAGES if status[k] is navkit.Status.STALE]
        hpd = os.path.join(self.pr.work, 'navkit', lv, 'pathdata.hpd')
        rec = navkit.build_record(hpd) if status['build'] is not navkit.Status.MISSING else None
        inst = installed(self.pr, lv)
        orig = self.pr.dice_pathdata(lv)
        self.installed['text'] = '\n'.join(t for t in (
            ('NavKit\'s path data is INSTALLED in the game for this level.' if inst else
             'The game uses the level\'s original path data.' if orig else
             'MOH\'s MP levels ship no AI path data: NavKit\'s build is for OPFOR\'s bots.'),
            'The build has the Navigation Studio\'s edits in it (navkit.py apply).' if rec and rec.get('kind') == 'apply' else '',
            f'RSNV export: {status["export"].value}.',
            f'Out of date ("old": made before the step it comes from was run again): {", ".join(stale)}.' if stale else '')
            if t)
        if self.problem:
            self.status['text'] = self.problem

    def read_settings(self, lv: str) -> dict[str, Any] | None:
        """The level's navkit.json with the fields the window shows, or None when it is not there or cannot be read
        (a cut-off or hand-edited file: self.problem says so)."""
        p = os.path.join(self.pr.work, 'navkit', lv, 'navkit.json')
        if not os.path.isfile(p):
            return None
        try:
            with open(p) as fh:
                s = json.load(fh)
            box, y = [float(v) for v in s['box']], [float(v) for v in s['y']]
            if len(box) != 4 or len(y) != 2:
                raise ValueError('box needs 4 numbers and y 2')
            s['box'], s['y'] = box, y
            return dict(s)
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.problem = f'{lv}: navkit.json cannot be read ({type(e).__name__}: {e}) - run Prepare again'
            return None

    def show_area(self, s: dict[str, Any] | None) -> None:
        """The sampling area's fields and note from the level's settings (s), or empty with a hint without them."""
        self.saved = []
        if s is None:
            for v in self.vars.values():
                v.set('')
            self.area_note['text'] = self.problem or 'Run Prepare to fill these in (or type your own and Prepare keeps them).'
            return
        for k, v in zip(('minx', 'minz', 'maxx', 'maxz'), s['box']):
            self.vars[k].set(f'{v:g}')
        self.vars['ybot'].set(f'{s["y"][0]:g}')
        self.vars['ytop'].set(f'{s["y"][1]:g}')
        self.saved = [self.vars[k].get() for k in ('minx', 'minz', 'maxx', 'maxz', 'ybot', 'ytop')]
        self.area_note['text'] = (f'Box from: {s.get("box_source", "?")}.  Sector offsets {s.get("xoff", "?")}, '
                                  f'{s.get("zoff", "?")} ({s.get("offsets_source", "?")}).')

    def area_args(self) -> list[str]:
        """The --box / --y arguments for the typed sampling area; empty when it is blank or unchanged. Raises
        ValueError for non-numbers."""
        vals = [self.vars[k].get().strip() for k in ('minx', 'minz', 'maxx', 'maxz', 'ybot', 'ytop')]
        if not all(vals) or vals == self.saved:
            return []   # empty or unchanged: navkit.py uses its own / the saved area
        try:
            [float(v) for v in vals]
        except ValueError:
            raise ValueError('The sampling area must be numbers.')
        return ['--box', ','.join(vals[:4]), '--y', ','.join(vals[4:])]

    # --- running steps ---------------------------------------------------------------------------------
    def run_step(self, cmd: str) -> None:
        """Runs one navkit.py step on the chosen level in the background, asking first for install and restore."""
        lv = self.level.get()
        if not lv or self.proc:
            return
        args = [PYTHON, '-u', NAVKIT, cmd, lv]
        try:
            if cmd in ('prepare', 'sample', 'all'):
                args += self.area_args()
        except ValueError as e:
            messagebox.showerror('NavKit', str(e))
            return
        if cmd in ('sample', 'all'):
            args.append('--yes')               # offline sampling: no game launch to confirm
        if cmd == 'install' and not messagebox.askokcancel(
                'NavKit', f'Install NavKit\'s path data for {lv.upper()} into the game?\n\n'
                          'The original file is kept and can be put back with "Restore original".'):
            return
        if cmd == 'restore' and not messagebox.askokcancel('NavKit', f'Put the original path data of {lv.upper()} back?'):
            return
        if cmd in ('build', 'all') and not self.may_replace_apply(lv):
            return
        self.write(f'\n> navkit.py {" ".join(args[3:])}\n')
        try:
            proc = subprocess.Popen(args, cwd=self.pr.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, ValueError) as e:
            # before set_busy: a step that cannot start leaves the buttons as they were (2026-11-05: disabled for good)
            self.write(f'could not start: {e}\n')
            errors.logger().error(f'starting navkit.py {cmd} {lv}: {e}')
            messagebox.showerror('NavKit', f'{cmd} could not be started:\n\n{e}')
            return
        self.proc = proc
        self.cancelled = False
        self.set_busy(True, f'Running {cmd} on {lv} ...')
        threading.Thread(target=self.reader, args=(proc, cmd), daemon=True).start()

    def may_replace_apply(self, lv: str) -> bool:
        """True unless the level's build has the Navigation Studio's edits in it (navkit.py apply) and the user, asked,
        does not want a plain build to replace it: build and apply write the same pathdata.hpd."""
        rec = navkit.build_record(os.path.join(self.pr.work, 'navkit', lv, 'pathdata.hpd'))
        if not rec or rec.get('kind') != 'apply':
            return True
        return bool(messagebox.askyesno(
            'NavKit', f'The build of {lv.upper()} has the Navigation Studio\'s edits in it (navkit.py apply with '
                      f'{rec.get("navtune") or "its side file"}).\n\nA plain Build replaces it without the edits; the side '
                      'file stays and apply can build them in again. Build anyway?'))

    def reader(self, proc: subprocess.Popen[str], cmd: str) -> None:
        """Copies the step's output into the queue line by line, keeping its last 10 lines, then queues its exit
        code."""
        self.tail: list[str] = []
        assert proc.stdout is not None         # run_step starts it with stdout=PIPE
        for line in proc.stdout:
            self.lines.put(line)
            self.tail = (self.tail + [line.rstrip()])[-10:]
        rc = proc.wait()
        self.lines.put(('done', cmd, rc))

    def pump(self) -> None:
        """Moves queued output into the log on the Tk thread and, when a step ends, re-enables the buttons and warns
        if it failed; runs itself again every 100 ms - whatever an item raises (2026-11-05: an error in refresh ended
        the pump for good, and the step never counted as ended)."""
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self.step_ended(*item[1:])
                else:
                    self.write(item)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.pump)

    def step_ended(self, cmd: str, rc: int) -> None:
        """A step's exit: the buttons back, the marks refreshed, and a warning when it failed and was not cancelled."""
        cancelled, self.cancelled = self.cancelled, False
        self.proc = None
        text = f'{cmd}: ' + ('finished' if rc == 0 else 'cancelled' if cancelled else 'failed - see the log above')
        self.set_busy(False, text)
        errors.logger().info(f'{cmd} {self.level.get()}: exit {rc}')
        try:
            self.refresh()
        finally:
            if self.problem:
                self.status['text'] = f'{text}; {self.problem}'
        if rc != 0 and not cancelled:
            said = '\n'.join(l for l in getattr(self, 'tail', []) if l.strip())[-1500:]
            errors.logger().warning(f'{cmd} failed; last lines:\n{said}')
            messagebox.showwarning('NavKit', f'{cmd} did not finish (exit code {rc}).\n\nIt said last:\n'
                                             f'{said or "(no output)"}\n\nThe whole output is above; the log:\n'
                                             f'{errors._state.get("path")}')

    def stop(self) -> None:
        """Cancels the running step and everything it started. Only a step still running counts as cancelled: one that
        ended before Cancel was pressed (its exit not yet shown) keeps its own result, a failure included. The game is
        not touched - sampling is offline (navkit.py sample without --ingame) - so nothing here can close a game the
        user is playing or wait for it (2026-11-05: close_game ran taskkill on MoHMPGame.exe and held the window up to
        12 s). opfor.ini is put back only if an in-game sampling run left its backup."""
        if not self.proc or self.proc.poll() is not None:
            return
        self.cancelled = True
        kill_tree(self.proc)
        self.write('\ncancelled\n')
        navkit.recover_ini(self.pr, lambda m: self.write(m + '\n'))

    def set_busy(self, busy: bool, text: str) -> None:
        """Disables the step buttons and the level list while a step runs (busy), enables them again otherwise, and
        shows text as the status."""
        for b in self.buttons:
            b['state'] = 'disabled' if busy else 'normal'
        self.combo['state'] = 'disabled' if busy else 'readonly'
        self.cancel['state'] = 'normal' if busy else 'disabled'
        self.status['text'] = text

    def write(self, text: str) -> None:
        """Appends text to the read-only log and scrolls to it."""
        self.log['state'] = 'normal'
        self.log.insert('end', text)
        self.log.see('end')
        self.log['state'] = 'disabled'

    def open_folder(self) -> None:
        """Opens the chosen level's NavKit work folder in Explorer (created if missing)."""
        lv = self.level.get()
        d = os.path.join(self.pr.work, 'navkit', lv) if lv else os.path.join(self.pr.work, 'navkit')
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def close(self) -> None:
        """Quits, after asking whether to cancel a step that is still running."""
        if self.proc and not messagebox.askyesno('NavKit', 'A step is still running. Cancel it and quit?'):
            return
        self.stop()
        self.root.destroy()


def main() -> None:
    """Starts the NavKit window, with high-DPI text, logging and the error guard."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on high-DPI screens
    except Exception:  # noqa: BLE001 - optional: no shcore (older Windows) or no windll: the window just looks blurred
        pass
    errors.setup('navkit-gui')
    with errors.guard():
        root = tk.Tk()
        root.report_callback_exception = lambda t, v, tb: errors.report(v, tb=tb, where='a button')
        App(root)
        root.mainloop()


if __name__ == '__main__':
    main()
