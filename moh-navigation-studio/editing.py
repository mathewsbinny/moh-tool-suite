"""The Navigation Studio's edit mode (milestone M4 step 2): draw edits on the level and keep them in a side file
(`<level>.navtune.json`, core/mohcore/navtune.py). Edits are recorded and shown here; applying them (the
rebuild for regions and seeds, links and route edges written into the file) comes with `navkit apply`.

Tools (E opens the panel): walkable / unwalkable region (an outline clicked on the ground, or a box from two
corners, with a height range), seed, unseed, link two areas, add / remove a route edge, traversal (ladder,
jump, vault, drop: bottom then top), note. Enter finishes an outline, Esc cancels the current drawing,
Ctrl+Z / Ctrl+Y undo and redo.
"""
import os

import numpy as np
from imgui_bundle import imgui

from mohcore import navtune as nt

TOOLS = [('walkable', 'walkable region'), ('unwalkable', 'unwalkable region'), ('seed', 'seed'), ('unseed', 'unseed'),
         ('link-areas', 'link two areas'), ('route-edge add', 'add a route edge'), ('route-edge remove', 'remove a route edge'),
         ('traversal', 'traversal (ladder, jump ...)'), ('note', 'note')]
COLOURS = {'walkable': (0.2, 1.0, 0.35), 'unwalkable': (1.0, 0.25, 0.2), 'seed': (0.2, 0.9, 1.0), 'unseed': (1.0, 0.3, 0.9),
           'link-areas': (1.0, 0.6, 0.1), 'route-edge': (1.0, 0.9, 0.2), 'route-edge remove': (1.0, 0.2, 0.2),
           'traversal': (0.95, 0.95, 0.95), 'note': (0.8, 0.8, 1.0), 'drawing': (1.0, 1.0, 0.6)}


def ring(points2, y, colour, closed=True):
    p = [np.array([x, y, z]) for x, z in points2]
    if closed and len(p) > 2:
        p.append(p[0])
    return [(a, b, colour) for a, b in zip(p[:-1], p[1:])]


def region_segments(region, colour):
    if 'polygon' in region:
        lo, hi = region['y']
        segs = ring(region['polygon'], lo, colour) + ring(region['polygon'], hi, colour)
        segs += [(np.array([x, lo, z]), np.array([x, hi, z]), colour) for x, z in region['polygon']]
        return segs
    x0, y0, z0, x1, y1, z1 = region['box']
    c = [np.array([x, y, z]) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    return [(c[a], c[b], colour) for a, b in edges]


def cross(p, size, colour):
    p = np.asarray(p, float)
    return [(p - d, p + d, colour) for d in (np.array([size, 0, 0]), np.array([0, 0, size]))]


def post(p, height, colour):
    p = np.asarray(p, float)
    return [(p, p + np.array([0, height, 0]), colour)]


def op_segments(o):
    name = o['op']
    if name in ('walkable', 'unwalkable'):
        return region_segments(o['region'], COLOURS[name])
    if name in ('seed', 'unseed'):
        return post(o['at'], 2.5, COLOURS[name]) + cross(np.asarray(o['at']) + [0, 2.5, 0], 0.6, COLOURS[name])
    if name == 'note':
        return post(o['at'], 3.5, COLOURS['note']) + cross(np.asarray(o['at']) + [0, 3.5, 0], 0.4, COLOURS['note'])
    if name == 'traversal':
        b, t = np.asarray(o['bottom'], float), np.asarray(o['top'], float)
        return [(b, t, COLOURS['traversal'])] + cross(b, 0.4, COLOURS['traversal']) + cross(t, 0.8, COLOURS['traversal'])
    a, b = np.asarray(o['a'], float) + [0, 0.5, 0], np.asarray(o['b'], float) + [0, 0.5, 0]
    col = COLOURS['route-edge remove'] if name == 'route-edge' and o.get('action') == 'remove' else COLOURS.get(name, (1, 1, 1))
    return [(a, b, col)] + post(a - [0, 0.5, 0], 1.5, col) + post(b - [0, 0.5, 0], 1.5, col)


class Editor:
    def __init__(self, studio, root, lv, level_name):
        self.st, self.root, self.lv = studio, root, lv
        self.target = 'NavKit' if 'NavKit' in studio.files else 'DICE'
        nav = studio.files[self.target]
        self.path = nt.default_path(root, lv)
        self.tune = None
        self.message = ''
        if os.path.isfile(self.path):
            try:
                self.tune = nt.load(self.path)
                if self.tune.base_changed(nav.path):
                    self.message = (f'the side file was made on another version of {self.tune.base.get("file", "the base file")}; '
                                    f'its edits are by position, so they still show and apply')
            except ValueError as e:
                self.message = f'side file not read: {e}'
        if self.tune is None:
            self.tune = nt.NavTune.new(level_name, nav.path, kind='navkit' if self.target == 'NavKit' else 'dice')
        self.open = False
        self.tool = None
        self.shape = 'polygon'
        self.clicks = []                        # points of the drawing in progress
        self.y_margin = [2.0, 4.0]              # region height range around the clicked points: below, above
        self.trav_kind, self.two_way, self.note_text = 0, True, ''
        self.by = nt.author()
        self.job = None                         # the running `navkit.py apply`
        self.job_log = []
        self.job_out = os.path.join(root, 'work', 'navkit', lv, 'pathdata.hpd')
        self.install_msg = ''

    # --- build with the edits (navkit apply) --------------------------------------------------------
    def start_apply(self):
        import subprocess
        import sys
        import threading
        self.tune.save(self.path)
        navkit_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'moh-auto-navkit', 'navkit.py')
        cmd = [sys.executable, '-u', navkit_py, 'apply', self.lv, '--navtune', self.path, '--out', self.job_out, '--opfor', self.root]
        self.job_log = ['saved the side file; building with its edits (a full rebuild: about 1-8 minutes) ...']
        self.job = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace',
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        def read():
            for line in self.job.stdout:
                self.job_log.append(line.rstrip())
        threading.Thread(target=read, daemon=True).start()

    def run_navkit(self, args):
        """navkit.py install / restore (quick): runs to the end, its last line shown in the panel."""
        import subprocess
        import sys
        navkit_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'moh-auto-navkit', 'navkit.py')
        r = subprocess.run([sys.executable, navkit_py, *args, '--opfor', self.root], capture_output=True, text=True,
                           errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        out = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.strip()]
        self.install_msg = ' / '.join(out[-2:]) if out else f'navkit {args[0]}: exit {r.returncode}'
        return r.returncode

    def job_state(self):
        if self.job is None:
            return None
        code = self.job.poll()
        return 'running' if code is None else ('done' if code == 0 else 'failed')

    def reopen_on_result(self, v):
        """A new studio window on the built file (the scene is uploaded once, so the window is replaced)."""
        import subprocess
        import sys
        from imgui_bundle import hello_imgui
        studio_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'studio.py')
        subprocess.Popen([sys.executable, studio_py, self.lv, '--candidate', self.job_out, '--navtune', self.path, '--edit'])
        hello_imgui.get_runner_params().app_shall_exit = True

    # --- input --------------------------------------------------------------------------------------
    def active(self):
        return self.open and self.tool is not None

    def click(self, v, p):
        if p is None or self.tool is None:
            return
        p = np.asarray(p, dtype=np.float64)
        self.clicks.append(p)
        name = self.tool
        if name in ('seed', 'unseed', 'note'):
            if name == 'note' and not self.note_text.strip():
                self.message = 'type the note first'
                self.clicks = []
                return
            self.commit(nt.op(name, at=p, by=self.by, **({'text': self.note_text.strip()} if name == 'note' else {})))
        elif name in ('link-areas', 'route-edge add', 'route-edge remove', 'traversal') and len(self.clicks) == 2:
            a, b = self.clicks
            if name == 'traversal':
                o = nt.op('traversal', kind=nt.TRAVERSALS[self.trav_kind], bottom=a, top=b, by=self.by, **{'two-way': self.two_way})
            elif name == 'link-areas':
                o = nt.op('link-areas', a=a, b=b, by=self.by)
            else:
                o = nt.op('route-edge', a=a, b=b, action=name.split()[1], by=self.by, **{'two-way': self.two_way})
            self.commit(o)
        elif name in ('walkable', 'unwalkable') and self.shape == 'box' and len(self.clicks) == 2:
            a, b = self.clicks
            lo, hi = min(a[1], b[1]) - self.y_margin[0], max(a[1], b[1]) + self.y_margin[1]
            self.commit(nt.op(name, region=nt.box(a[0], lo, a[2], b[0], hi, b[2]), by=self.by, **({'value': True} if name == 'walkable' else {})))
        self.refresh(v)

    def finish_polygon(self, v):
        if self.tool in ('walkable', 'unwalkable') and self.shape == 'polygon' and len(self.clicks) >= 3:
            ys = [c[1] for c in self.clicks]
            region = nt.polygon([(c[0], c[2]) for c in self.clicks], min(ys) - self.y_margin[0], max(ys) + self.y_margin[1])
            self.commit(nt.op(self.tool, region=region, by=self.by, **({'value': True} if self.tool == 'walkable' else {})))
            self.refresh(v)

    def commit(self, o):
        try:
            self.tune.add(o)
            self.message = 'added: ' + self.tune.summary(len(self.tune.operations) - 1)
        except ValueError as e:
            self.message = f'not added: {e}'
        self.clicks = []

    def keys(self, v):
        io = imgui.get_io()
        if io.want_capture_keyboard:
            return
        if imgui.is_key_pressed(imgui.Key.e):
            self.set_open(v, not self.open)
        if not self.open:
            return
        if imgui.is_key_pressed(imgui.Key.escape) and self.clicks:
            self.clicks = []
            self.refresh(v)
        if imgui.is_key_pressed(imgui.Key.enter):
            self.finish_polygon(v)
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.z):
            self.undo(v)
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.y):
            self.redo(v)

    def set_open(self, v, on):
        self.open = on
        if not on:
            self.tool, self.clicks = None, []
        self.st.sync_click_mode(v)
        self.refresh(v)

    def undo(self, v):
        o = self.tune.undo()
        self.message = 'undone: ' + o['op'] if o else 'nothing to undo'
        self.refresh(v)

    def redo(self, v):
        o = self.tune.redo()
        self.message = 'redone: ' + o['op'] if o else 'nothing to redo'
        self.refresh(v)

    # --- drawing ------------------------------------------------------------------------------------
    def segments(self):
        segs = []
        for o in self.tune.operations:
            segs += op_segments(o)
        c = COLOURS['drawing']
        for p in self.clicks:
            segs += cross(p, 0.5, c) + post(p, 1.0, c)
        if len(self.clicks) >= 2:
            pts = [(p[0], p[2]) for p in self.clicks]
            if self.tool in ('walkable', 'unwalkable') and self.shape == 'polygon':
                segs += ring(pts, float(np.mean([p[1] for p in self.clicks])) + 0.3, c, closed=False)
        return segs

    def refresh(self, v):
        self.st.refresh_overlay(v)

    # --- panel --------------------------------------------------------------------------------------
    def panel(self, v):
        self.keys(v)
        if not self.open:
            return
        io = imgui.get_io()
        imgui.set_next_window_pos((io.display_size.x - 580, 440), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((570, 520), imgui.Cond_.first_use_ever)
        imgui.begin('Edit (E)')
        kind = self.tune.kind
        imgui.text(f'editing {self.target}\'s data ({kind} base) - side file {os.path.relpath(self.path, self.root)}')
        imgui.text_disabled('The button below builds the edits into the path data: regions and seeds by a rebuild, '
                            'links and route edges into its route graph. Traversals are recorded (a later step).')
        changed, self.by = imgui.input_text('recorded as', self.by)
        imgui.separator()
        for key, label in TOOLS:
            base_op = key.split()[0]
            allowed = self.tune.allowed(base_op)
            if not allowed:
                imgui.begin_disabled()
            if imgui.radio_button(f'{label}##tool{key}', self.tool == key):
                self.tool = None if self.tool == key else key
                self.clicks = []
                self.st.sync_click_mode(v)
                self.refresh(v)
            if not allowed:
                imgui.end_disabled()
                if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
                    imgui.set_tooltip('region edits need a NavKit base (DESIGN.md 5.1)')
        imgui.separator()
        t = self.tool
        if t in ('walkable', 'unwalkable'):
            for s in ('polygon', 'box'):
                imgui.same_line() if s == 'box' else None
                if imgui.radio_button(f'{s}##shape', self.shape == s):
                    self.shape, self.clicks = s, []
            _, self.y_margin = imgui.input_float2('height range: below / above the clicked points (m)', self.y_margin)
            imgui.text(f'{"click the outline, Enter to finish" if self.shape == "polygon" else "click two opposite corners"}'
                       f' ({len(self.clicks)} point(s))')
            if self.shape == 'polygon' and imgui.button('finish outline (Enter)'):
                self.finish_polygon(v)
        elif t == 'traversal':
            _, self.trav_kind = imgui.combo('kind', self.trav_kind, list(nt.TRAVERSALS))
            _, self.two_way = imgui.checkbox('two-way', self.two_way)
            imgui.text(f'click the bottom, then the top ({len(self.clicks)} of 2)')
        elif t == 'note':
            _, self.note_text = imgui.input_text('text', self.note_text)
            imgui.text('then click where it belongs')
        elif t in ('link-areas', 'route-edge add', 'route-edge remove'):
            if t != 'link-areas':
                _, self.two_way = imgui.checkbox('two-way', self.two_way)
            imgui.text(f'click the first point, then the second ({len(self.clicks)} of 2); each joins the route node '
                       f'nearest to it')
        elif t in ('seed', 'unseed'):
            imgui.text('click a point on the ground')
        else:
            imgui.text_disabled('choose a tool; clicks then add to the drawing instead of selecting')
        if self.message:
            imgui.text_wrapped(self.message)
        imgui.separator()
        if imgui.button('undo (Ctrl+Z)'):
            self.undo(v)
        imgui.same_line()
        if imgui.button('redo (Ctrl+Y)'):
            self.redo(v)
        imgui.same_line()
        if imgui.button('save' + (' *' if self.tune.dirty else '')):
            self.tune.save(self.path)
            self.message = f'saved {len(self.tune.operations)} edit(s) to {self.path}'
        imgui.same_line()
        if imgui.button('reload'):
            try:
                self.tune = nt.load(self.path)
                self.message = f'loaded {len(self.tune.operations)} edit(s)'
            except (OSError, ValueError) as e:
                self.message = f'not loaded: {e}'
            self.refresh(v)
        state = self.job_state()
        if self.tune.kind == 'dice' and os.path.basename(self.job_out) == 'pathdata.hpd':
            self.job_out = os.path.join(os.path.dirname(self.job_out), 'pathdata_dice_edited.hpd')   # never over NavKit's
        if state == 'running':
            imgui.begin_disabled()
        label = 'build with these edits (navkit apply)' if self.tune.kind == 'navkit' else \
            'apply to a copy of DICE\'s file (this PC only)'
        if imgui.button(label):
            self.start_apply()
        if state == 'running':
            imgui.end_disabled()
        imgui.same_line()
        imgui.text_disabled(f'-> {os.path.relpath(self.job_out, self.root)}')
        if state is not None:
            imgui.text({'running': 'building ...', 'done': 'built.', 'failed': 'the build failed:'}[state])
            for line in self.job_log[-6:]:
                imgui.text_disabled(line[:110])
            if state == 'done':
                if imgui.button('open the result'):
                    self.reopen_on_result(v)
                imgui.same_line()
                if imgui.button('install into the game'):
                    self.run_navkit(['install', self.lv, '--from', self.job_out])
                imgui.same_line()
                if imgui.button('restore DICE\'s original'):
                    self.run_navkit(['restore', self.lv])
                imgui.text_disabled(f'report: {os.path.relpath(self.job_out + ".report.txt", self.root)}')
        if self.install_msg:
            imgui.text_wrapped(self.install_msg)
        imgui.separator()
        imgui.text(f'{len(self.tune.operations)} edit(s)')
        imgui.begin_child('ops', (0, 0))
        drop = None
        for k in range(len(self.tune.operations)):
            o = self.tune.operations[k]
            if imgui.small_button(f'fly##op{k}'):
                at = o.get('at') or o.get('a') or o.get('bottom')
                if at is None:
                    r = o['region']
                    at = ([np.mean([p[0] for p in r['polygon']]), r['y'][0], np.mean([p[1] for p in r['polygon']])]
                          if 'polygon' in r else [(r['box'][0] + r['box'][3]) / 2, r['box'][1], (r['box'][2] + r['box'][5]) / 2])
                v.camera.focus(np.asarray(at, float), 30.0)
            imgui.same_line()
            if imgui.small_button(f'x##op{k}'):
                drop = k
            imgui.same_line()
            imgui.text(f'{k + 1}. ' + self.tune.summary(k))
        imgui.end_child()
        if drop is not None:
            self.tune.remove(drop)
            self.refresh(v)
        imgui.end()
