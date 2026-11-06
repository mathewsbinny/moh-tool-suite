"""The Navigation Studio's edit mode (milestone M4 step 2): draw edits on the level and keep them in a side file
(`<level>.navtune.json`, core/mohcore/navtune.py). Edits are recorded and shown here; applying them (the
rebuild for regions and seeds, links and route edges written into the file) comes with `navkit apply`.

Tools (E opens the panel; not while flying with the right button held, where E climbs): walkable / unwalkable region (an outline clicked on the ground, or a box from two
corners, with a height range), seed, unseed, link two areas, add / remove a route edge, traversal (ladder,
jump, vault, drop: bottom then top), note. Enter finishes an outline, Esc cancels the current drawing,
Ctrl+Z / Ctrl+Y undo and redo (a removed edit is undone in place).

A side file that cannot be read (a newer version's, an invalid operation, a JSON typo) is never saved over: the
editor starts empty with a warning, and the first save (or build) keeps the unreadable file as
<side file>.unreadable.bak (.unreadable.1.bak, ... when taken). Reload asks for a second click when there are unsaved
edits, and so does closing the window (2026-11-05).

The build (navkit apply) is an ApplyJob: its state, its process, its output file, its own log, and the edits'
version it was built from. Once the edits change (or are reloaded) its result is shown as out of date, and
installing it or opening it is disabled until it is built again. Opening the result saves the edits first (they are
the built ones: an out-of-date result cannot be opened), since the new window reads the side file.
"""
import enum
import os
import subprocess
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from imgui_bundle import imgui

from mohcore import navtune as nt, viewer

if TYPE_CHECKING:
    import studio

Colour = tuple[float, float, float]
Segment = tuple[np.ndarray, np.ndarray, Colour]       # overlay line: from, to, colour
Point = Sequence[float] | np.ndarray

TOOLS = [('walkable', 'walkable region'), ('unwalkable', 'unwalkable region'), ('seed', 'seed'), ('unseed', 'unseed'),
         ('link-areas', 'link two areas'), ('route-edge add', 'add a route edge'), ('route-edge remove', 'remove a route edge'),
         ('traversal', 'traversal (ladder, jump ...)'), ('note', 'note')]
COLOURS: dict[str, Colour] = {'walkable': (0.2, 1.0, 0.35), 'unwalkable': (1.0, 0.25, 0.2), 'seed': (0.2, 0.9, 1.0), 'unseed': (1.0, 0.3, 0.9),
           'link-areas': (1.0, 0.6, 0.1), 'route-edge': (1.0, 0.9, 0.2), 'route-edge remove': (1.0, 0.2, 0.2),
           'traversal': (0.95, 0.95, 0.95), 'note': (0.8, 0.8, 1.0), 'drawing': (1.0, 1.0, 0.6)}


def ring(points2: Iterable[Sequence[float]], y: float, colour: Colour, closed: bool = True) -> list[Segment]:
    """Line segments (a, b, colour) joining the (x, z) points at height y, closed back to the first point unless
    closed is False."""
    p = [np.array([x, y, z]) for x, z in points2]
    if closed and len(p) > 2:
        p.append(p[0])
    return [(a, b, colour) for a, b in zip(p[:-1], p[1:])]


def region_segments(region: dict[str, Any], colour: Colour) -> list[Segment]:
    """The outline segments of an edit region: a polygon prism's floor, roof and posts, or a box's 12 edges."""
    if 'polygon' in region:
        lo, hi = region['y']
        segs = ring(region['polygon'], lo, colour) + ring(region['polygon'], hi, colour)
        segs += [(np.array([x, lo, z]), np.array([x, hi, z]), colour) for x, z in region['polygon']]
        return segs
    x0, y0, z0, x1, y1, z1 = region['box']
    c = [np.array([x, y, z]) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    return [(c[a], c[b], colour) for a, b in edges]


def cross(p: Point, size: float, colour: Colour) -> list[Segment]:
    """Two horizontal line segments (along x and z) crossing at p, size metres from it each way."""
    c = np.asarray(p, float)
    return [(c - d, c + d, colour) for d in (np.array([size, 0, 0]), np.array([0, 0, size]))]


def post(p: Point, height: float, colour: Colour) -> list[Segment]:
    """A vertical line segment from p up height metres."""
    b = np.asarray(p, float)
    return [(b, b + np.array([0, height, 0]), colour)]


def op_segments(o: dict[str, Any]) -> list[Segment]:
    """The overlay segments that draw one navtune operation, coloured by its kind."""
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
    col: Colour = COLOURS['route-edge remove'] if name == 'route-edge' and o.get('action') == 'remove' else COLOURS.get(name, (1, 1, 1))
    return [(a, b, col)] + post(a - [0, 0.5, 0], 1.5, col) + post(b - [0, 0.5, 0], 1.5, col)


def op_focus(o: dict[str, Any]) -> Point:
    """Where the camera flies to for an edit: its point, its first end, or the middle of its region's bottom."""
    at = o.get('at') or o.get('a') or o.get('bottom')
    if at is None:
        r = o['region']
        at = ([np.mean([p[0] for p in r['polygon']]), r['y'][0], np.mean([p[1] for p in r['polygon']])]
              if 'polygon' in r else [(r['box'][0] + r['box'][3]) / 2, r['box'][1], (r['box'][2] + r['box'][5]) / 2])
    return at


class JobState(enum.Enum):
    """An apply job's state: none started, running, finished with exit code 0, or with another."""
    IDLE = 'idle'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'


@dataclass
class ApplyJob:
    """One `navkit.py apply` run: its process, the file it writes, its log (its own: the reader thread of an older
    job appends to that job's log only), and the side file, edits and version it was built from."""
    proc: 'subprocess.Popen[str] | None' = None
    out: str = ''
    log: list[str] = field(default_factory=list)
    side_file: str = ''
    tune: nt.NavTune | None = None
    version: int | None = None
    reader: threading.Thread | None = None

    def state(self) -> JobState:
        """IDLE without a process, else RUNNING until it exits, then DONE (exit code 0) or FAILED."""
        if self.proc is None:
            return JobState.IDLE
        code = self.proc.poll()
        return JobState.RUNNING if code is None else (JobState.DONE if code == 0 else JobState.FAILED)

    def built_from(self, tune: nt.NavTune, side_file: str) -> bool:
        """True when these edits (the same NavTune, at the version built, the same side file) are the ones the job
        was started with; False after an edit, an undo past it, a reload or another side file."""
        return self.tune is tune and self.version == tune.version and self.side_file == side_file


class Editor:
    def __init__(self, studio: 'studio.Studio', root: str, lv: str, level_name: str) -> None:
        self.st, self.root, self.lv = studio, root, lv
        self.target = 'NavKit' if 'NavKit' in studio.files else 'DICE'
        nav = studio.files[self.target]
        self.level_name = level_name
        self.message = ''
        self.warning = ''                       # shown at the panel's top until the side file is saved or read
        self.unreadable: str | None = None      # a side file that could not be read: backed up, never saved over
        self.reload_armed = False               # reload with unsaved edits: the second click discards them
        self.close_armed = False                # closing the window with unsaved edits: the second close discards
        self.open_side_file(nt.default_path(root, lv), nav.path)
        self.open = False
        self.tool: str | None = None
        self.shape = 'polygon'
        self.clicks: list[np.ndarray] = []      # points of the drawing in progress
        self.y_margin = [2.0, 4.0]              # region height range around the clicked points: below, above
        self.trav_kind, self.two_way, self.note_text = 0, True, ''
        self.by = nt.author()
        self.job = ApplyJob()                   # the last `navkit.py apply` (IDLE: none yet)
        self.job_out = os.path.join(root, 'work', 'navkit', lv, 'pathdata.hpd')   # where the next build writes
        self.install_msg = ''

    # --- the side file -----------------------------------------------------------------------------
    def open_side_file(self, path: str, base_path: str | None = None) -> None:
        """Reads the side file at path, or starts empty edits when there is none. 2026-10-31: when it cannot be read
        (a newer version's, an invalid operation, a JSON typo), the edits start empty but the file is remembered
        (self.unreadable, a warning on the panel) and save() backs it up first - before, the empty edits were saved
        over it and the user's file was lost."""
        self.path = path
        self.unreadable = None
        self.warning = ''
        base_path = base_path or self.st.files[self.target].path
        tune: nt.NavTune | None = None
        if os.path.isfile(path):
            try:
                tune = nt.load(path)
                if tune.base_changed(base_path):
                    self.message = (f'the side file was made on another version of {tune.base.get("file", "the base file")}; '
                                    f'its edits are by position, so they still show and apply')
            except (OSError, ValueError) as e:
                self.unreadable = path
                self.warning = (f'The side file could not be read, so the edits start empty: {e}. Saving (or building) '
                                f'keeps it as {os.path.basename(self.backup_path())} - fix it and reload, or go on with '
                                f'new edits.')
        self.tune = tune or nt.NavTune.new(self.level_name, base_path, kind='navkit' if self.target == 'NavKit' else 'dice')
        self.reload_armed = self.close_armed = False

    def backup_path(self) -> str:
        """A free name for the unreadable side file's backup: <path>.unreadable.bak, then .unreadable.1.bak ..."""
        p, k = self.path + '.unreadable.bak', 1
        while os.path.exists(p):
            p, k = f'{self.path}.unreadable.{k}.bak', k + 1
        return p

    def save(self) -> str:
        """Saves the edits to the side file, an unreadable one there renamed to a backup first. Returns the message."""
        kept = ''
        if self.unreadable is not None and os.path.isfile(self.path) \
                and os.path.normcase(os.path.abspath(self.unreadable)) == os.path.normcase(os.path.abspath(self.path)):
            b = self.backup_path()
            os.replace(self.path, b)
            kept = f' (the unreadable side file was kept as {b})'
        self.unreadable = None
        self.warning = ''
        self.tune.save(self.path)
        self.reload_armed = self.close_armed = False
        return f'saved {len(self.tune.operations)} edit(s) to {self.path}{kept}'

    def reload(self, v: viewer.Viewer) -> bool:
        """Reads the side file again. Unsaved edits are thrown away only on a second click (2026-10-31); a file that
        cannot be read leaves the edits on screen as they are. Returns True when it was read."""
        if self.tune.dirty and not self.reload_armed:
            self.reload_armed = True
            self.message = 'there are unsaved edits: click reload again to discard them and read the side file'
            return False
        before, self.message = self.tune, ''
        self.open_side_file(self.path)              # sets the message when the base file changed
        loaded = self.unreadable is None
        if not loaded:
            self.tune = before
            self.message = 'not loaded: ' + self.warning
        else:
            self.message = f'loaded {len(self.tune.operations)} edit(s)' + (f'; {self.message}' if self.message else '')
        self.refresh(v)
        return loaded

    # --- build with the edits (navkit apply) --------------------------------------------------------
    def start_apply(self) -> None:
        """Saves the side file and starts navkit.py apply with it in the background (a new ApplyJob)."""
        import sys
        self.message = self.save()
        navkit_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'moh-auto-navkit', 'navkit.py')
        cmd = [sys.executable, '-u', navkit_py, 'apply', self.lv, '--navtune', self.path, '--out', self.job_out, '--opfor', self.root]
        self.start_job(cmd, 'saved the side file; building with its edits (a full rebuild: about 1-8 minutes) ...')

    def start_job(self, cmd: list[str], first_line: str) -> ApplyJob:
        """Starts cmd as the new apply job on the edits as they are now, its output read into the job's own log by a
        thread of its own. Returns the job."""
        job = ApplyJob(out=self.job_out, log=[first_line], side_file=self.path, tune=self.tune, version=self.tune.version)
        proc = job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                           errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        log = job.log

        def read() -> None:
            assert proc.stdout is not None      # started with stdout=PIPE
            for line in proc.stdout:
                log.append(line.rstrip())
        job.reader = threading.Thread(target=read, daemon=True)
        job.reader.start()
        self.job = job
        return job

    def run_navkit(self, args: list[str]) -> int:
        """navkit.py install / restore (quick): runs to the end, its last line shown in the panel."""
        import sys
        navkit_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'moh-auto-navkit', 'navkit.py')
        r = subprocess.run([sys.executable, navkit_py, *args, '--opfor', self.root], capture_output=True, text=True,
                           errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        out = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.strip()]
        self.install_msg = ' / '.join(out[-2:]) if out else f'navkit {args[0]}: exit {r.returncode}'
        return r.returncode

    def job_state(self) -> JobState:
        """The apply job's state (IDLE when none was started)."""
        return self.job.state()

    def result_stale(self) -> bool:
        """True when the job's result is not a build of the edits as they are now (they changed or were reloaded)."""
        return not self.job.built_from(self.tune, self.path)

    def result_unusable(self) -> str | None:
        """None when the result can be installed or opened, else why not."""
        if self.job_state() is not JobState.DONE:
            return 'there is no finished build'
        if self.result_stale():
            return 'the edits changed since this build (or were reloaded): build again first'
        return None

    def install_result(self) -> bool:
        """Installs the finished build into the game (navkit install) when it is a build of the current edits;
        returns True when it was run."""
        why = self.result_unusable()
        if why is not None:
            self.install_msg = f'not installed: {why}'
            return False
        self.run_navkit(['install', self.lv, '--from', self.job.out])
        return True

    def open_result(self, v: viewer.Viewer) -> bool:
        """Opens the finished build in a new studio window (this one closes) when it is a build of the current
        edits. The new window reads the side file, so unsaved edits are saved first - they are the built ones
        (version unchanged since the build), only their save point differs. Returns True when it was opened."""
        why = self.result_unusable()
        if why is not None:
            self.message = f'not opened: {why}'
            return False
        if self.tune.dirty:
            try:
                self.message = self.save()
            except OSError as e:
                self.message = f'not opened: the edits could not be saved first ({e})'
                return False
        self.reopen_on_result(v)
        return True

    def reopen_on_result(self, v: viewer.Viewer) -> None:
        """A new studio window on the built file (the scene is uploaded once, so the window is replaced)."""
        import sys
        from imgui_bundle import hello_imgui
        studio_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'studio.py')
        subprocess.Popen([sys.executable, studio_py, self.lv, '--candidate', self.job.out, '--navtune', self.path, '--edit'])
        hello_imgui.get_runner_params().app_shall_exit = True

    def confirm_close(self) -> bool:
        """The viewer's close hook (the window's close button, Alt+F4): True lets the window close. With unsaved
        edits the first close only warns (the panel opened, the message on it) and the second one closes; an edit,
        undo, redo or save between them asks again. Called outside ImGui's frame: no ImGui here."""
        if not self.tune.dirty or self.close_armed:
            return True
        self.close_armed = True
        self.open = True
        self.message = 'there are unsaved edits: save them, or close the window again to discard them'
        return False

    # --- input --------------------------------------------------------------------------------------
    def active(self) -> bool:
        """True when the editor is open and a tool is chosen, so clicks in the level go to it."""
        return self.open and self.tool is not None

    def click(self, v: viewer.Viewer, p: Point | None) -> None:
        """Takes a click on the level at p for the current tool and commits the edit once the tool has all its
        points."""
        if p is None or self.tool is None:
            return
        at = np.asarray(p, dtype=np.float64)
        self.clicks.append(at)
        name = self.tool
        if name in ('seed', 'unseed', 'note'):
            if name == 'note' and not self.note_text.strip():
                self.message = 'type the note first'
                self.clicks = []
                return
            text: dict[str, Any] = {'text': self.note_text.strip()} if name == 'note' else {}
            self.commit(nt.op(name, at=at, by=self.by, **text))
        elif name in ('link-areas', 'route-edge add', 'route-edge remove', 'traversal') and len(self.clicks) == 2:
            a, b = self.clicks
            two_way: dict[str, Any] = {'two-way': self.two_way}
            if name == 'traversal':
                o = nt.op('traversal', kind=nt.TRAVERSALS[self.trav_kind], bottom=a, top=b, by=self.by, **two_way)
            elif name == 'link-areas':
                o = nt.op('link-areas', a=a, b=b, by=self.by)
            else:
                o = nt.op('route-edge', a=a, b=b, action=name.split()[1], by=self.by, **two_way)
            self.commit(o)
        elif name in ('walkable', 'unwalkable') and self.shape == 'box' and len(self.clicks) == 2:
            a, b = self.clicks
            lo, hi = min(a[1], b[1]) - self.y_margin[0], max(a[1], b[1]) + self.y_margin[1]
            value: dict[str, Any] = {'value': True} if name == 'walkable' else {}
            self.commit(nt.op(name, region=nt.box(a[0], lo, a[2], b[0], hi, b[2]), by=self.by, **value))
        self.refresh(v)

    def finish_polygon(self, v: viewer.Viewer) -> None:
        """Commits the polygon being drawn (three points or more) as a walkable or unwalkable region."""
        if self.tool in ('walkable', 'unwalkable') and self.shape == 'polygon' and len(self.clicks) >= 3:
            assert self.tool is not None
            ys = [c[1] for c in self.clicks]
            region = nt.polygon([(c[0], c[2]) for c in self.clicks], min(ys) - self.y_margin[0], max(ys) + self.y_margin[1])
            value: dict[str, Any] = {'value': True} if self.tool == 'walkable' else {}
            self.commit(nt.op(self.tool, region=region, by=self.by, **value))
            self.refresh(v)

    def commit(self, o: dict[str, Any]) -> None:
        """Adds the operation to the edits and reports it, or why it was refused; the pending clicks are cleared
        either way."""
        self.reload_armed = self.close_armed = False
        try:
            self.tune.add(o)
            self.message = 'added: ' + self.tune.summary(len(self.tune.operations) - 1)
        except ValueError as e:
            self.message = f'not added: {e}'
        self.clicks = []

    @staticmethod
    def e_toggles(v: viewer.Viewer, right_down: bool) -> bool:
        """Whether E opens / closes the panel: not while flying with the right button held, where the viewer's fly
        mode climbs with E (2026-10-31: it closed the panel and dropped a half-drawn outline)."""
        return not (getattr(v.camera, 'fly', False) and right_down)

    def keys(self, v: viewer.Viewer) -> None:
        """The editor's keys: E opens or closes it, Esc drops the pending clicks, Enter finishes a polygon, Ctrl+Z /
        Ctrl+Y undo and redo."""
        io = imgui.get_io()
        if io.want_capture_keyboard:
            return
        if imgui.is_key_pressed(imgui.Key.e) and self.e_toggles(v, imgui.is_mouse_down(imgui.MouseButton_.right)):
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

    def set_open(self, v: viewer.Viewer, on: bool) -> None:
        """Opens or closes the editor; closing drops the tool and the pending clicks."""
        self.open = on
        if not on:
            self.tool, self.clicks = None, []
        self.st.sync_click_mode(v)
        self.refresh(v)

    def undo(self, v: viewer.Viewer) -> None:
        """Undoes the last edit and redraws."""
        self.reload_armed = self.close_armed = False
        o = self.tune.undo()
        self.message = 'undone: ' + o['op'] if o else 'nothing to undo'
        self.refresh(v)

    def redo(self, v: viewer.Viewer) -> None:
        """Redoes the last undone edit and redraws."""
        self.reload_armed = self.close_armed = False
        o = self.tune.redo()
        self.message = 'redone: ' + o['op'] if o else 'nothing to redo'
        self.refresh(v)

    # --- drawing ------------------------------------------------------------------------------------
    def segments(self) -> list[Segment]:
        """The overlay segments for every edit plus the clicks of the edit being drawn."""
        segs: list[Segment] = []
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

    def refresh(self, v: viewer.Viewer) -> None:
        """Redraws the Studio's overlay."""
        self.st.refresh_overlay(v)

    # --- panel --------------------------------------------------------------------------------------
    def panel(self, v: viewer.Viewer) -> None:
        """The edit panel, drawn each frame: header, tools, the tool's options, actions, the build, the edits."""
        self.keys(v)
        if not self.open:
            return
        io = imgui.get_io()
        imgui.set_next_window_pos((io.display_size.x - 580, 440), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((570, 520), imgui.Cond_.first_use_ever)
        imgui.begin('Edit (E)')
        self.panel_header()
        self.panel_tools(v)
        self.panel_tool_options(v)
        if self.message:
            imgui.text_wrapped(self.message)
        imgui.separator()
        self.panel_actions(v)
        self.panel_build(v)
        if self.install_msg:
            imgui.text_wrapped(self.install_msg)
        imgui.separator()
        self.panel_operations(v)
        imgui.end()

    def panel_header(self) -> None:
        """The panel's top: the side file's warning, what is edited and where, the author field."""
        kind = self.tune.kind
        if self.close_armed and self.tune.dirty:
            imgui.text_colored(imgui.ImVec4(1.0, 0.45, 0.3, 1.0), 'unsaved edits: save them, or close the window again '
                               'to discard them')
        if self.warning:
            imgui.push_style_color(imgui.Col_.text, (1.0, 0.45, 0.3, 1.0))
            imgui.text_wrapped(self.warning)
            imgui.pop_style_color()
        imgui.text(f'editing {self.target}\'s data ({kind} base) - side file {os.path.relpath(self.path, self.root)}')
        imgui.text_disabled('The button below builds the edits into the path data: regions and seeds by a rebuild, '
                            'links and route edges into its route graph. Traversals are recorded (a later step).')
        changed, self.by = imgui.input_text('recorded as', self.by)
        imgui.separator()

    def panel_tools(self, v: viewer.Viewer) -> None:
        """The tool buttons (a second click turns the tool off); region tools disabled on a graph-only base."""
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

    def panel_tool_options(self, v: viewer.Viewer) -> None:
        """The chosen tool's options and what to click next."""
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

    def panel_actions(self, v: viewer.Viewer) -> None:
        """Undo, redo, save and reload (a second click to discard unsaved edits)."""
        if imgui.button('undo (Ctrl+Z)'):
            self.undo(v)
        imgui.same_line()
        if imgui.button('redo (Ctrl+Y)'):
            self.redo(v)
        imgui.same_line()
        if imgui.button('save' + (' *' if self.tune.dirty else '')):
            try:
                self.message = self.save()
            except OSError as e:
                self.message = f'not saved: {e}'
        imgui.same_line()
        if imgui.button('reload: click again to discard the edits' if self.reload_armed else 'reload'):
            self.reload(v)

    def panel_build(self, v: viewer.Viewer) -> None:
        """The build button (navkit apply in the background, disabled while it runs) and its output file."""
        state = self.job_state()
        if self.tune.kind == 'dice' and os.path.basename(self.job_out) == 'pathdata.hpd':
            self.job_out = os.path.join(os.path.dirname(self.job_out), 'pathdata_dice_edited.hpd')   # never over NavKit's
        if state is JobState.RUNNING:
            imgui.begin_disabled()
        label = 'build with these edits (navkit apply)' if self.tune.kind == 'navkit' else \
            'apply to a copy of DICE\'s file (this PC only)'
        if imgui.button(label):
            self.start_apply()
        if state is JobState.RUNNING:
            imgui.end_disabled()
        imgui.same_line()
        imgui.text_disabled(f'-> {os.path.relpath(self.job_out, self.root)}')
        if state is not JobState.IDLE:
            self.panel_build_result(v, state)

    def panel_build_result(self, v: viewer.Viewer, state: JobState) -> None:
        """The build's state and last log lines; once built: open the result and install it (both only while it is
        a build of the current edits; shown out of date otherwise), restore DICE's file."""
        stale = self.result_stale()
        imgui.text({JobState.RUNNING: 'building ...', JobState.DONE: 'built.', JobState.FAILED: 'the build failed:'}[state])
        if stale:
            imgui.same_line()
            imgui.text_colored(imgui.ImVec4(1.0, 0.6, 0.2, 1.0), 'out of date: the edits changed since (or were reloaded)')
        for line in self.job.log[-6:]:
            imgui.text_disabled(line[:110])
        if state is JobState.DONE:
            if stale:
                imgui.begin_disabled()
            if imgui.button('open the result'):
                self.open_result(v)
            imgui.same_line()
            if imgui.button('install into the game'):
                self.install_result()
            if stale:
                imgui.end_disabled()
                if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
                    imgui.set_tooltip('build again: this result does not have the current edits')
            imgui.same_line()
            if imgui.button('restore DICE\'s original'):
                self.run_navkit(['restore', self.lv])
            imgui.text_disabled(f'report: {os.path.relpath(self.job.out + ".report.txt", self.root)}')

    def panel_operations(self, v: viewer.Viewer) -> None:
        """The list of edits: fly to one, delete one (Ctrl+Z puts it back)."""
        imgui.text(f'{len(self.tune.operations)} edit(s)')
        imgui.begin_child('ops', (0, 0))
        drop: int | None = None
        for k in range(len(self.tune.operations)):
            o = self.tune.operations[k]
            if imgui.small_button(f'fly##op{k}'):
                v.camera.focus(np.asarray(op_focus(o), float), 30.0)
            imgui.same_line()
            if imgui.small_button(f'x##op{k}'):
                drop = k
            imgui.same_line()
            imgui.text(f'{k + 1}. ' + self.tune.summary(k))
        imgui.end_child()
        if drop is not None:
            o = self.tune.remove(drop)
            self.reload_armed = self.close_armed = False
            self.message = f'removed: {o["op"]} (Ctrl+Z puts it back)'
            self.refresh(v)
