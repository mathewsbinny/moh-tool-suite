"""NavKit - a small Windows front end for navkit.py (double-click to start; needs Python 3 with numpy).

Every button runs the matching navkit.py step for the chosen level and shows its output; see QUICKSTART.md.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'core'))
from mohcore import errors  # noqa: E402
import navkit  # noqa: E402

PYTHON = os.path.join(os.path.dirname(sys.executable), 'python.exe')   # console python for the steps
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable

STEPS = [('prepare', '1. Prepare', 'Unpack the level, extract its objects, choose the sampling box'),
         ('sample', '2. Sample', 'Samples the level\'s collision from its files - no game launch (a few minutes)'),
         ('build', '3. Build', 'Generate pathdata.hpd from the samples'),
         ('check', '4. Check', 'Score the result (against the original path data if the level has one)')]


def kill_tree(proc):
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


def installed(pr, lv):
    """NavKit's file is installed for the level: DICE's original backed up (.opfor-orig), or - a level that had no
    path data, every MOH MP level - the no-original marker (.opfor-new)."""
    p = pr.pathdata(lv)
    return os.path.isfile(p + navkit.BACKUP_SUFFIX) or os.path.isfile(p + navkit.NEW_SUFFIX)


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.saved = []
        self.lines = queue.Queue()
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

        box = ttk.LabelFrame(top, text='Sampling area (filled in by Prepare; edit to override)', padding=8)
        box.pack(fill='x', pady=(10, 0))
        self.vars = {}
        for i, (key, label) in enumerate((('minx', 'min X'), ('minz', 'min Z'), ('maxx', 'max X'), ('maxz', 'max Z'),
                                          ('ybot', 'bottom Y'), ('ytop', 'top Y'))):
            ttk.Label(box, text=label).grid(row=0, column=i * 2, sticky='e', padx=(8 if i else 0, 4))
            v = tk.StringVar()
            ttk.Entry(box, textvariable=v, width=8).grid(row=0, column=i * 2 + 1)
            self.vars[key] = v
        self.area_note = ttk.Label(box, text='', foreground='#555')
        self.area_note.grid(row=1, column=0, columnspan=12, sticky='w', pady=(6, 0))

        steps = ttk.LabelFrame(top, text='Steps', padding=8)
        steps.pack(fill='x', pady=(10, 0))
        self.buttons, self.marks = [], {}
        for r, (cmd, label, tip) in enumerate(STEPS):
            b = ttk.Button(steps, text=label, width=20, command=lambda c=cmd: self.run_step(c))
            b.grid(row=r, column=0, sticky='w', pady=2)
            self.buttons.append(b)
            m = ttk.Label(steps, text='', width=3)
            m.grid(row=r, column=1)
            self.marks[cmd] = m
            ttk.Label(steps, text=tip, foreground='#555').grid(row=r, column=2, sticky='w')
        side = ttk.Frame(steps)
        side.grid(row=0, column=3, rowspan=4, sticky='ne', padx=(20, 0))
        steps.columnconfigure(2, weight=1)
        for label, cmd in (('Run all (1-4)', 'all'), ('Install in game', 'install'), ('Restore original', 'restore')):
            b = ttk.Button(side, text=label, width=18, command=lambda c=cmd: self.run_step(c))
            b.pack(pady=2)
            self.buttons.append(b)
        self.cancel = ttk.Button(side, text='Cancel', width=18, command=self.stop, state='disabled')
        self.cancel.pack(pady=(10, 2))
        self.installed = ttk.Label(steps, text='', foreground='#555')
        self.installed.grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 0))

        logf = ttk.Frame(top)
        logf.pack(fill='both', expand=True, pady=(10, 0))
        self.log = tk.Text(logf, height=12, wrap='none', font=('Consolas', 9), state='disabled')
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.status = ttk.Label(top, text='Ready', anchor='w')
        self.status.pack(fill='x', pady=(6, 0))

        self.fill_levels()
        root.after(100, self.pump)
        root.protocol('WM_DELETE_WINDOW', self.close)

    # --- levels and state ------------------------------------------------------------------------------
    def fill_levels(self):
        names = navkit.level_names(self.pr)
        shown = [lv for lv, ai in names if ai or not self.only_ai.get()]
        self.combo['values'] = shown
        if self.level.get() not in shown:
            self.level.set(shown[0] if shown else '')
        self.refresh()

    def refresh(self):
        lv = self.level.get()
        if not lv:
            return
        out = os.path.join(self.pr.work, 'navkit', lv)
        s = None
        self.saved = []
        if os.path.isfile(os.path.join(out, 'navkit.json')):
            s = json.load(open(os.path.join(out, 'navkit.json')))
            for k, v in zip(('minx', 'minz', 'maxx', 'maxz'), s['box']):
                self.vars[k].set(f'{v:g}')
            self.vars['ybot'].set(f'{s["y"][0]:g}')
            self.vars['ytop'].set(f'{s["y"][1]:g}')
            self.saved = [self.vars[k].get() for k in ('minx', 'minz', 'maxx', 'maxz', 'ybot', 'ytop')]
            self.area_note['text'] = (f'Box from: {s["box_source"]}.  Sector offsets {s["xoff"]}, {s["zoff"]} '
                                      f'({s["offsets_source"]}).')
        else:
            for v in self.vars.values():
                v.set('')
            self.area_note['text'] = 'Run Prepare to fill these in (or type your own and Prepare keeps them).'
        done = {'prepare': s is not None,
                'sample': all(os.path.isfile(self.pr.samples(lv, a[2])) for a in navkit.AGENTS),
                'build': os.path.isfile(os.path.join(out, 'pathdata.hpd')),
                'check': os.path.isfile(os.path.join(out, 'check.txt'))}
        for k, m in self.marks.items():
            m['text'] = '✔' if done[k] else ''
        inst = installed(self.pr, lv)
        orig = self.pr.dice_pathdata(lv)
        self.installed['text'] = ('NavKit\'s path data is INSTALLED in the game for this level.' if inst else
                                  'The game uses the level\'s original path data.' if orig else
                                  'MOH\'s MP levels ship no AI path data: NavKit\'s build is for OPFOR\'s bots.')

    def area_args(self):
        vals = [self.vars[k].get().strip() for k in ('minx', 'minz', 'maxx', 'maxz', 'ybot', 'ytop')]
        if not all(vals) or vals == self.saved:
            return []   # empty or unchanged: navkit.py uses its own / the saved area
        try:
            [float(v) for v in vals]
        except ValueError:
            raise ValueError('The sampling area must be numbers.')
        return ['--box', ','.join(vals[:4]), '--y', ','.join(vals[4:])]

    # --- running steps ---------------------------------------------------------------------------------
    def run_step(self, cmd):
        lv = self.level.get()
        if not lv or self.proc:
            return
        args = [PYTHON, '-u', os.path.join(HERE, 'navkit.py'), cmd, lv]
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
        self.write(f'\n> navkit.py {" ".join(args[3:])}\n')
        self.set_busy(True, f'Running {cmd} on {lv} ...')
        self.proc = subprocess.Popen(args, cwd=self.pr.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self.reader, args=(self.proc, cmd), daemon=True).start()

    def reader(self, proc, cmd):
        self.tail = []
        for line in proc.stdout:
            self.lines.put(line)
            self.tail = (self.tail + [line.rstrip()])[-10:]
        rc = proc.wait()
        self.lines.put(('done', cmd, rc))

    def pump(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    _, cmd, rc = item
                    cancelled, self.cancelled = getattr(self, 'cancelled', False), False
                    self.proc = None
                    self.set_busy(False, f'{cmd}: ' + ('finished' if rc == 0 else 'failed - see the log above'))
                    self.refresh()
                    errors.logger().info(f'{cmd} {self.level.get()}: exit {rc}')
                    if rc != 0 and not cancelled:
                        said = '\n'.join(l for l in getattr(self, 'tail', []) if l.strip())[-1500:]
                        errors.logger().warning(f'{cmd} failed; last lines:\n{said}')
                        messagebox.showwarning('NavKit', f'{cmd} did not finish (exit code {rc}).\n\nIt said last:\n'
                                                         f'{said or "(no output)"}\n\nThe whole output is above; the log:\n'
                                                         f'{errors._state.get("path")}')
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def stop(self):
        if not self.proc:
            return
        self.cancelled = True
        kill_tree(self.proc)
        self.write('\ncancelled\n')
        # a sampling run killed mid-way leaves the game open and opfor.ini changed
        navkit.close_game()
        navkit.recover_ini(self.pr, lambda m: self.write(m + '\n'))

    def set_busy(self, busy, text):
        for b in self.buttons:
            b['state'] = 'disabled' if busy else 'normal'
        self.combo['state'] = 'disabled' if busy else 'readonly'
        self.cancel['state'] = 'normal' if busy else 'disabled'
        self.status['text'] = text

    def write(self, text):
        self.log['state'] = 'normal'
        self.log.insert('end', text)
        self.log.see('end')
        self.log['state'] = 'disabled'

    def open_folder(self):
        lv = self.level.get()
        d = os.path.join(self.pr.work, 'navkit', lv) if lv else os.path.join(self.pr.work, 'navkit')
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def close(self):
        if self.proc and not messagebox.askyesno('NavKit', 'A step is still running. Cancel it and quit?'):
            return
        self.stop()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on high-DPI screens
    except Exception:
        pass
    errors.setup('navkit-gui')
    with errors.guard():
        root = tk.Tk()
        root.report_callback_exception = lambda t, v, tb: errors.report(v, tb=tb, where='a button')
        App(root)
        root.mainloop()


if __name__ == '__main__':
    main()
