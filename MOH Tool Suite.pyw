"""MOH Tool Suite - one window for every tool (double-click to start; needs Python 3 with numpy, and for the 3D
tools moderngl, glfw and imgui-bundle).

Tabs: Levels (open a level in the Map Inspector or the Navigation Studio), Bundles (FbrbTool), Level diff
(LevelDiff), Crash reader, Xbox files (STFS packages and disc images), Tests (run_tests.py), More tools
(DbxEditGUI, NavKit's window). Every tool runs as its own process; its output appears in the pane at the bottom.
The tools read your own game copy (Project OPFOR's) - nothing is sent anywhere.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SUITE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
from mohcore import errors  # noqa: E402

# library -> what needs it (checked when the window opens)
LIBRARIES = [('numpy', 'every tool'), ('matplotlib', 'NavKit, screenshots'), ('moderngl', 'the 3D tools'),
             ('glfw', 'the 3D tools'), ('imgui_bundle', 'the 3D tools'), ('pefile', 'the crash reader\'s code view'),
             ('capstone', 'the crash reader\'s code view')]
PYTHON = os.path.join(os.path.dirname(sys.executable), 'python.exe')        # console python for the tools
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

TOOLS = {
    'fbrb': (os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe'), os.path.join(SUITE, 'moh-fbrb-tool', 'build.bat')),
    'leveldiff': (os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'LevelDiff.exe'),
                  os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'build.bat')),
    'dbxgui': (os.path.join(SUITE, 'moh-dbx-tools', 'DbxEditGUI.exe'), os.path.join(SUITE, 'moh-dbx-tools', 'build.bat')),
}
SCRIPTS = {
    'inspector': os.path.join(SUITE, 'moh-map-inspector', 'inspector.py'),
    'studio': os.path.join(SUITE, 'moh-navigation-studio', 'studio.py'),
    'navkit': os.path.join(SUITE, 'moh-auto-navkit', 'navkit.py'),
    'navkitgui': os.path.join(SUITE, 'moh-auto-navkit', 'navkitGUI.pyw'),
    'crash': os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'crash-reader', 'crashreader.py'),
    'stfs': os.path.join(SUITE, 'moh-console-readers', 'stfs', 'stfs.py'),
    'xiso': os.path.join(SUITE, 'moh-console-readers', 'xiso', 'xiso.py'),
    'tests': os.path.join(SUITE, 'run_tests.py'),
}


def kill_tree(proc):
    """Ends a process and all its descendants (taskkill /T on Windows; the process alone elsewhere)."""
    if os.name == 'nt':
        r = subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
        if r.returncode == 0 or proc.poll() is not None:
            return
    proc.kill()


def tool(name):
    """The tool's exe, built first (with the C# compiler Windows ships) if it is not there yet."""
    exe, bat = TOOLS[name]
    if not os.path.isfile(exe):
        r = subprocess.run(['cmd', '/c', bat], capture_output=True, text=True, errors='replace', creationflags=NO_WINDOW)
        if not os.path.isfile(exe):
            errors.logger().error(f'building {os.path.basename(exe)} failed:\n{r.stdout}{r.stderr}')
            raise RuntimeError(f'{os.path.basename(exe)} could not be built (the C# compiler that comes with Windows, '
                               f'.NET Framework 4, is needed). Run {bat} to see why.')
    return exe


class Hub:
    def __init__(self, root):
        self.root = root
        self.lines = queue.Queue()
        self.proc = None                       # the tab's running tool (one at a time)
        root.title('MOH Tool Suite')
        root.minsize(900, 640)
        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        self.pr = None
        try:
            import navkit
            self.pr = navkit.Project()
        except Exception as e:                 # the suite still opens; the Levels tab explains
            self.pr_error = str(e)

        pane = ttk.PanedWindow(root, orient='vertical')
        pane.pack(fill='both', expand=True, padx=8, pady=8)
        self.tabs = ttk.Notebook(pane)
        pane.add(self.tabs, weight=3)
        out = ttk.Frame(pane)
        pane.add(out, weight=2)
        bar = ttk.Frame(out)
        bar.pack(fill='x')
        self.status = tk.StringVar(value='Ready.')
        ttk.Label(bar, textvariable=self.status).pack(side='left')
        ttk.Button(bar, text='Clear', command=lambda: self.text.delete('1.0', 'end')).pack(side='right')
        self.stop_btn = ttk.Button(bar, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.pack(side='right', padx=4)
        self.text = tk.Text(out, height=12, wrap='none', font=('Consolas', 9))
        sb = ttk.Scrollbar(out, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)

        self.tab_levels()
        self.tab_bundles()
        self.tab_diff()
        self.tab_crash()
        self.tab_xbox()
        self.tab_tests()
        self.tab_more()
        self.tails = {}                        # label -> the last lines of a tool's output (for error reports)
        self.ok_codes = {}                     # label -> {exit code: what it means}: the codes that are not a failure
        root.report_callback_exception = lambda t, v, tb: errors.report(v, tb=tb, where='a button')
        self.write(f'Log: {errors._state.get("path")}\n')
        self.check_libraries()
        root.after(100, self.pump)

    def notify(self, kind, title, text):
        """A window for the user (tests replace it)."""
        (messagebox.showerror if kind == 'error' else messagebox.showwarning)(title, text, parent=self.root)

    def check_libraries(self):
        import importlib.util
        missing = [(name, used) for name, used in LIBRARIES if importlib.util.find_spec(name) is None]
        if not missing:
            return
        pip = ' '.join(errors.PIP_NAMES.get(n, n) for n, _ in missing)
        lines = '\n'.join(f'  {n} - for {used}' for n, used in missing)
        errors.warn(f'libraries not installed: {", ".join(n for n, _ in missing)}')
        self.write(f'Missing libraries:\n{lines}\nInstall them with: pip install {pip}\n')
        self.notify('warning', 'MOH Tool Suite', f'Some libraries the tools use are not installed:\n\n{lines}\n\n'
                                                   f'Install them with:\npip install {pip}')

    # --- running tools ------------------------------------------------------------------------------
    def run(self, args, label, cwd=None, ok=None):
        """One tool in the background (the tab's), its output streamed to the pane. ok: the exit codes that mean
        the tool did its job, with what each says ({0: ''} by default; LevelDiff's 1 = the files differ)."""
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showinfo('MOH Tool Suite', 'A tool is still running - wait for it or press Stop.')
            return
        self.write(f'\n> {label}\n')
        errors.logger().info(f'run: {label}: {subprocess.list2cmdline([str(a) for a in args])}')
        self.tails[label] = []
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         errors='replace', cwd=cwd or SUITE, creationflags=NO_WINDOW)
        except OSError as e:
            errors.report(e, where=f'starting {label}')
            return
        self.ok_codes[label] = ok or {0: ''}
        self.status.set(f'Running: {label}')
        self.stop_btn.configure(state='normal')
        threading.Thread(target=self.reader, args=(self.proc, label, True), daemon=True).start()

    def launch(self, args, label):
        """A window of its own (3D tools, other GUIs): runs alongside, and on after this window closes. Its log goes
        to a file this window follows, so closing the suite never cuts the tool's output off."""
        import tempfile
        self.write(f'\n> {label}\n')
        errors.logger().info(f'launch: {label}: {subprocess.list2cmdline([str(a) for a in args])}')
        self.tails[label] = []
        log = tempfile.NamedTemporaryFile(prefix='mohsuite_', suffix='.log', delete=False)
        try:
            p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=SUITE, creationflags=NO_WINDOW,
                                 env={**os.environ, 'PYTHONUNBUFFERED': '1'})
        except OSError as e:
            log.close()
            errors.report(e, where=f'starting {label}')
            return
        log.close()
        threading.Thread(target=self.follow, args=(p, log.name, label), daemon=True).start()

    def follow(self, proc, path, label):
        import time
        with open(path, encoding='utf-8', errors='replace') as fh:
            while True:
                line = fh.readline()
                if line:
                    self.lines.put(line)
                    tail = self.tails.setdefault(label, [])
                    tail.append(line.rstrip())
                    del tail[:-12]
                    continue
                if proc.poll() is not None:
                    rest = fh.read()
                    if rest:
                        self.lines.put(rest)
                    break
                time.sleep(0.2)
        self.lines.put(('done', label, proc.returncode, False))
        try:
            os.remove(path)
        except OSError:
            pass

    def reader(self, proc, label, main):
        for line in proc.stdout:
            self.lines.put(line)
            tail = self.tails.setdefault(label, [])
            tail.append(line.rstrip())
            del tail[:-12]
        code = proc.wait()
        self.lines.put(('done', label, code, main))

    def pump(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    _, label, code, main = item
                    ok = self.ok_codes.get(label, {0: ''}) if main else {0: ''}
                    if code in ok:
                        self.write(f'done{" - " + ok[code] if ok[code] else ""}: {label}\n')
                    else:
                        self.write(f'finished with exit code {code}: {label}\n')
                    errors.logger().info(f'exit {code}: {label}')
                    if main:
                        self.status.set('Ready.')
                        self.stop_btn.configure(state='disabled')
                        if label.startswith('Levels'):
                            self.fill_levels()
                    if code not in ok and code is not None and not self.stopped:
                        self.failed(label, code, main)
                    self.stopped = False
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    stopped = False

    def failed(self, label, code, main):
        """A tool ended with an error: logged, and the user told what it said last. The 3D tools and other
        windows report their own errors in a window of their own, so for them the pane is enough."""
        tail = [l for l in self.tails.get(label, []) if l.strip()]
        errors.logger().warning(f'{label} failed (exit code {code}); last lines:\n  ' + '\n  '.join(tail))
        if not main:
            self.write(f'{label} closed with an error - see its message, and the log for details.\n')
            return
        said = '\n'.join(tail[-8:]) or '(no output)'
        self.notify('warning', 'MOH Tool Suite', f'{label} did not finish (exit code {code}).\n\nIt said last:\n{said}\n\n'
                                                   f'The whole output is in the pane below; the suite\'s log:\n{errors._state.get("path")}')

    def stop(self):
        """Ends the running tool and every process it started (run_tests -> navregress, prepare -> FbrbTool):
        terminating only the direct child would leave those running, and holding the output pipe open."""
        if self.proc is not None and self.proc.poll() is None:
            self.stopped = True
            kill_tree(self.proc)
            self.write('stopped.\n')
            errors.logger().info('stopped by the user')

    def write(self, s):
        self.text.insert('end', s)
        self.text.see('end')

    def form(self, title, help_text):
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text=title)
        ttk.Label(f, text=help_text, wraplength=820, foreground='#444').grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 8))
        f.columnconfigure(1, weight=1)
        return f

    def file_row(self, f, row, label, var, kind='open', types=(('All files', '*.*'),)):
        ttk.Label(f, text=label).grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(f, textvariable=var).grid(row=row, column=1, sticky='we', padx=6)

        def browse():
            if kind == 'dir':
                p = filedialog.askdirectory(title=label)
            elif kind == 'save':
                p = filedialog.asksaveasfilename(title=label, filetypes=types)
            else:
                p = filedialog.askopenfilename(title=label, filetypes=types)
            if p:
                var.set(os.path.normpath(p))
        ttk.Button(f, text='Browse...', command=browse).grid(row=row, column=2, sticky='w')

    @staticmethod
    def need(*values):
        missing = [name for name, v in values if not v.get().strip()]
        if missing:
            messagebox.showwarning('MOH Tool Suite', 'Choose ' + ' and '.join(missing) + ' first.')
            return False
        return True

    # --- Levels: the 3D tools -----------------------------------------------------------------------
    def tab_levels(self):
        f = self.form('Levels', 'Pick a level, then open it in the Map Inspector (the level in 3D: every object, its '
                                'properties and links; measuring) or in the Navigation Studio (the AI\'s navigation '
                                'data: NavKit\'s and DICE\'s, islands, path test, edits). A level is unpacked the first '
                                'time it is opened (offline, about 20 s); the first opening of its navigation data takes '
                                'up to a minute, later ones seconds. The tool\'s progress shows below.')
        cols = ('prepared', 'dice', 'navkit', 'edits')
        self.tree = ttk.Treeview(f, columns=cols, height=12, selectmode='browse')
        self.tree.heading('#0', text='Level')
        for c, title in zip(cols, ('Prepared', 'DICE path data', 'NavKit build', 'Saved edits')):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=120, anchor='center')
        self.tree.column('#0', width=160)
        self.tree.grid(row=1, column=0, columnspan=3, sticky='nsew')
        self.tree.bind('<Double-1>', lambda e: self.open_3d('inspector'))
        f.rowconfigure(1, weight=1)
        btns = ttk.Frame(f)
        btns.grid(row=2, column=0, columnspan=3, sticky='w', pady=8)
        ttk.Button(btns, text='Open in Map Inspector', command=lambda: self.open_3d('inspector')).pack(side='left')
        ttk.Button(btns, text='Open in Navigation Studio', command=lambda: self.open_3d('studio')).pack(side='left', padx=6)
        ttk.Button(btns, text='Prepare level', command=self.prepare).pack(side='left', padx=6)
        ttk.Button(btns, text='Open level folder', command=self.open_level_folder).pack(side='left', padx=6)
        ttk.Button(btns, text='Refresh', command=self.fill_levels).pack(side='left', padx=6)
        self.nav_file = tk.StringVar()
        self.file_row(f, 3, 'NavKit file for the Studio (optional; default: the newest build):', self.nav_file,
                      types=(('Path data', '*.hpd'), ('All files', '*.*')))
        self.fill_levels()

    def fill_levels(self):
        self.tree.delete(*self.tree.get_children())
        if self.pr is None:
            self.tree.insert('', 'end', text='(no game copy found)', values=('', '', '', ''))
            self.write(f'Levels: {getattr(self, "pr_error", "Project OPFOR not found")}\n'
                       'Put the suite next to the project-opfor-moh folder (with its game\\ copy), or set OPFOR_ROOT.\n')
            return
        for lv in self.pr.level_dirs():
            work = os.path.join(self.pr.work, 'levels', lv)
            nk = os.path.join(self.pr.work, 'navkit', lv)
            builds = [p for p in (os.path.join(nk, 'pathdata.hpd'), os.path.join(self.pr.work, 'navkit', 'regress', f'{lv}.hpd'),
                                  os.path.join(self.pr.work, 'navbuild', f'{lv}_W.hpd')) if os.path.isfile(p)]
            edits = os.path.join(nk, f'{lv}.navtune.json')
            n_edits = ''
            if os.path.isfile(edits):
                try:
                    n_edits = str(len(json.load(open(edits, encoding='utf-8')).get('operations', [])))
                except (OSError, ValueError):
                    n_edits = '?'
            self.tree.insert('', 'end', iid=lv, text=lv, values=(
                'yes' if os.path.isdir(os.path.join(work, 'dist')) else '-',
                'yes' if self.pr.dice_pathdata(lv) else '-', 'yes' if builds else '-', n_edits or '-'))

    def chosen_level(self):
        sel = self.tree.selection()
        if not sel or self.pr is None:
            messagebox.showinfo('MOH Tool Suite', 'Choose a level in the list first.')
            return None
        return sel[0]

    def open_3d(self, which):
        lv = self.chosen_level()
        if not lv:
            return
        args = [PYTHON, SCRIPTS[which], lv]
        if which == 'studio' and self.nav_file.get().strip():
            args += ['--candidate', self.nav_file.get().strip()]
        name = 'Map Inspector' if which == 'inspector' else 'Navigation Studio'
        self.launch(args, f'{name}: {lv} (the window opens when the level is loaded)')

    def prepare(self):
        lv = self.chosen_level()
        if lv:
            self.run([PYTHON, SCRIPTS['navkit'], 'prepare', lv], f'Levels: prepare {lv}')

    def open_level_folder(self):
        lv = self.chosen_level()
        if lv:
            d = os.path.join(self.pr.work, 'levels', lv)
            os.startfile(d if os.path.isdir(d) else self.pr.work)

    # --- Bundles: FbrbTool --------------------------------------------------------------------------
    def tab_bundles(self):
        f = self.form('Bundles', 'Look inside the game\'s .fbrb bundles, unpack them to a folder, or pack an unpacked '
                                 'folder back into a bundle (FbrbTool). Packing needs the folder\'s fbrb.manifest, '
                                 'which unpacking writes.')
        self.fb_file, self.fb_dir, self.fb_out = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.file_row(f, 1, 'Bundle (.fbrb):', self.fb_file, types=(('Bundles', '*.fbrb'), ('All files', '*.*')))
        self.file_row(f, 2, 'Folder to unpack into:', self.fb_dir, kind='dir')
        b = ttk.Frame(f)
        b.grid(row=3, column=1, sticky='w', pady=6)
        ttk.Button(b, text='List entries', command=lambda: self.need(('a bundle', self.fb_file)) and
                   self.run([tool('fbrb'), 'list', self.fb_file.get()], 'FbrbTool list')).pack(side='left')
        ttk.Button(b, text='Unpack', command=lambda: self.need(('a bundle', self.fb_file), ('a folder', self.fb_dir)) and
                   self.run([tool('fbrb'), 'unpack', self.fb_file.get(), self.fb_dir.get()], 'FbrbTool unpack')).pack(side='left', padx=6)
        ttk.Separator(f).grid(row=4, column=0, columnspan=3, sticky='we', pady=8)
        self.fb_src = tk.StringVar()
        self.file_row(f, 5, 'Unpacked folder to pack:', self.fb_src, kind='dir')
        self.file_row(f, 6, 'New bundle:', self.fb_out, kind='save', types=(('Bundles', '*.fbrb'),))
        ttk.Button(f, text='Pack', command=lambda: self.need(('a folder', self.fb_src), ('a new bundle', self.fb_out)) and
                   self.run([tool('fbrb'), 'pack', self.fb_src.get(), self.fb_out.get()], 'FbrbTool pack')).grid(row=7, column=1, sticky='w', pady=6)

    # --- Level diff ---------------------------------------------------------------------------------
    def tab_diff(self):
        f = self.form('Level diff', 'Compare two binary .dbx files (two builds of a level, or any partition) instance by '
                                    'instance: what was added, removed or changed, field by field (LevelDiff).')
        self.ld_a, self.ld_b, self.ld_type, self.ld_ignore, self.ld_json = (tk.StringVar() for _ in range(5))
        types = (('DBX', '*.dbx'), ('All files', '*.*'))
        self.file_row(f, 1, 'File A:', self.ld_a, types=types)
        self.file_row(f, 2, 'File B:', self.ld_b, types=types)
        ttk.Label(f, text='Only types containing:').grid(row=3, column=0, sticky='w')
        ttk.Entry(f, textvariable=self.ld_type, width=30).grid(row=3, column=1, sticky='w', padx=6)
        ttk.Label(f, text='Leave out fields (comma-separated):').grid(row=4, column=0, sticky='w')
        ttk.Entry(f, textvariable=self.ld_ignore, width=30).grid(row=4, column=1, sticky='w', padx=6)
        self.ld_match, self.ld_summary, self.ld_all = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
        opts = ttk.Frame(f)
        opts.grid(row=5, column=1, sticky='w', pady=4)
        ttk.Checkbutton(opts, text='Pair re-created objects by blueprint + position', variable=self.ld_match).pack(side='left')
        ttk.Checkbutton(opts, text='Counts only', variable=self.ld_summary).pack(side='left', padx=8)
        ttk.Checkbutton(opts, text='List everything', variable=self.ld_all).pack(side='left')
        self.file_row(f, 6, 'Also save as JSON (optional):', self.ld_json, kind='save', types=(('JSON', '*.json'),))
        ttk.Button(f, text='Compare', command=self.diff).grid(row=7, column=1, sticky='w', pady=6)

    def diff(self):
        if not self.need(('file A', self.ld_a), ('file B', self.ld_b)):
            return
        args = [tool('leveldiff'), self.ld_a.get(), self.ld_b.get()]
        if self.ld_match.get():
            args.append('--match-objects')
        if self.ld_summary.get():
            args.append('--summary')
        if self.ld_all.get():
            args.append('--all')
        if self.ld_type.get().strip():
            args += ['--type', self.ld_type.get().strip()]
        for field in (x.strip() for x in self.ld_ignore.get().split(',')):
            if field:
                args += ['--ignore', field]
        if self.ld_json.get().strip():
            args += ['--json', self.ld_json.get().strip()]
        self.run(args, 'LevelDiff', ok={0: '', 1: 'the files differ'})

    # --- Crash reader -------------------------------------------------------------------------------
    def tab_crash(self):
        f = self.form('Crash reader', 'After the game crashes, this turns its crashreport.xml into something you can act '
                                      'on: where it crashed (function names), the code there, null pointers, the call '
                                      'stack, hints for known crash causes and the log lines before the crash. Leave the '
                                      'report empty to read the newest one.')
        self.cr_file, self.cr_log = tk.StringVar(), tk.StringVar()
        self.file_row(f, 1, 'crashreport.xml (optional):', self.cr_file, types=(('Crash reports', '*.xml'), ('All files', '*.*')))
        self.file_row(f, 2, 'Game log (optional):', self.cr_log, types=(('Logs', '*.log *.txt'), ('All files', '*.*')))
        self.cr_nocode = tk.BooleanVar()
        ttk.Checkbutton(f, text='Without the code view (it needs: pip install pefile capstone)', variable=self.cr_nocode).grid(row=3, column=1, sticky='w')
        ttk.Button(f, text='Read the crash report', command=self.crash).grid(row=4, column=1, sticky='w', pady=6)

    def crash(self):
        args = [PYTHON, SCRIPTS['crash']]
        if self.cr_file.get().strip():
            args.append(self.cr_file.get().strip())
        if self.cr_log.get().strip():
            args += ['--log', self.cr_log.get().strip()]
        if self.cr_nocode.get():
            args.append('--no-code')
        self.run(args, 'Crash reader')

    # --- Xbox files ---------------------------------------------------------------------------------
    def tab_xbox(self):
        f = self.form('Xbox files', 'Read your own Xbox 360 files (read-only): content packages such as the game\'s '
                                    'DLC updates (STFS), and disc images (.iso). List what is inside, or extract it - all, '
                                    'or only paths containing the filter text.')
        self.xb_file, self.xb_dir, self.xb_filter = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.xb_kind = tk.StringVar(value='package')
        k = ttk.Frame(f)
        k.grid(row=1, column=1, sticky='w')
        ttk.Radiobutton(k, text='Content package (STFS)', value='package', variable=self.xb_kind).pack(side='left')
        ttk.Radiobutton(k, text='Disc image (.iso)', value='iso', variable=self.xb_kind).pack(side='left', padx=8)
        self.file_row(f, 2, 'File:', self.xb_file)
        self.file_row(f, 3, 'Extract into:', self.xb_dir, kind='dir')
        ttk.Label(f, text='Filter (optional):').grid(row=4, column=0, sticky='w')
        ttk.Entry(f, textvariable=self.xb_filter, width=30).grid(row=4, column=1, sticky='w', padx=6)
        b = ttk.Frame(f)
        b.grid(row=5, column=1, sticky='w', pady=6)
        ttk.Button(b, text='Info', command=lambda: self.xbox('info')).pack(side='left')
        ttk.Button(b, text='List', command=lambda: self.xbox('list')).pack(side='left', padx=6)
        ttk.Button(b, text='Extract', command=lambda: self.xbox('extract')).pack(side='left')

    def xbox(self, cmd):
        if not self.need(('a file', self.xb_file)):
            return
        iso = self.xb_kind.get() == 'iso'
        if cmd == 'info' and iso:
            cmd = 'list'
        args = [PYTHON, SCRIPTS['xiso' if iso else 'stfs'], cmd, self.xb_file.get()]
        if cmd == 'extract':
            if not self.need(('a folder to extract into', self.xb_dir)):
                return
            args.append(self.xb_dir.get())
        if cmd != 'info' and self.xb_filter.get().strip():
            args.append(self.xb_filter.get().strip())
        self.run(args, f'{"Disc image" if iso else "Package"} {cmd}')

    # --- Tests --------------------------------------------------------------------------------------
    def tab_tests(self):
        f = self.form('Tests', 'Check that the tools work on this PC: quick (about a minute, no game data), game (your '
                               'game copy, about 15 minutes) or full (everything, about 90 minutes). One line per test; '
                               'a failure shows its output at the end.')
        self.t_tier = tk.StringVar(value='quick')
        r = ttk.Frame(f)
        r.grid(row=1, column=1, sticky='w')
        for t in ('quick', 'game', 'full'):
            ttk.Radiobutton(r, text=t, value=t, variable=self.t_tier).pack(side='left', padx=(0, 10))
        b = ttk.Frame(f)
        b.grid(row=2, column=1, sticky='w', pady=6)
        ttk.Button(b, text='Run the tests', command=lambda: self.run([PYTHON, '-u', SCRIPTS['tests'], self.t_tier.get()],
                                                                     f'Tests ({self.t_tier.get()})')).pack(side='left')
        ttk.Button(b, text='What would run', command=lambda: self.run([PYTHON, SCRIPTS['tests'], self.t_tier.get(), '--list'],
                                                                      'Tests: list')).pack(side='left', padx=6)

    # --- More tools ---------------------------------------------------------------------------------
    def tab_more(self):
        f = self.form('More tools', 'Tools with windows of their own.')
        rows = [('DbxEditGUI', 'View, edit, check and repair binary .dbx files; XML export and import.',
                 lambda: self.launch([tool('dbxgui')], 'DbxEditGUI')),
                ('NavKit', 'Generate AI navigation data for a level: prepare, sample in game, build, check, install.',
                 lambda: self.launch([PYTHON, SCRIPTS['navkitgui']], 'NavKit')),
                ('Suite folder', 'The suite\'s files, READMEs and DESIGN.md.', lambda: os.startfile(SUITE))]
        for k, (name, text, cmd) in enumerate(rows, 1):
            ttk.Button(f, text=f'Open {name}', command=cmd, width=22).grid(row=k, column=0, sticky='w', pady=4)
            ttk.Label(f, text=text).grid(row=k, column=1, sticky='w', padx=8)


def dpi_aware():
    """Sharp text on scaled displays (125 %, 150 %): Windows otherwise draws a Tk window blurred and enlarged."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main():
    errors.setup('suite')
    dpi_aware()
    with errors.guard():
        root = tk.Tk()
        Hub(root)
        root.mainloop()


if __name__ == '__main__':
    main()
