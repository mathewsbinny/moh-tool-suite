r"""The suite's 3D tools without game data and without a window: the viewer (core\mohcore\viewer.py), the Map Inspector,
the Navigation Studio with its overlays (overlay.py) and the level picker (core\mohcore\pick.py), on inputs built
here - a made-up Project OPFOR folder with a level written as XML (core\tests\test_level.py's helpers) and path data
written with hpdtool / kymesh (core\tests\test_navdata.py's: two islands, a ladder roof). The viewer draws into an
offscreen framebuffer of a hidden (standalone) OpenGL context; panels run in ImGui frames without a window; clicks,
drags, keys and the wheel go in as ImGui input events.

Checked:
  viewer     the scene (layers, batches, bounds, triangle counts, lines, polylines, the layer limit); the first view
             frames the geometry's bounds; triangles are uploaded and drawn - a red box and a green box where they
             project, the background elsewhere; layer ranges (batches reordered by layer, set_colors still on the
             right batch, before and after the upload); hiding a layer; the selection's highlight; overlay lines;
             picking: the right object id and a world point on it at a pixel, nothing on the background or on a
             hidden layer; a click selects (on_pick), a double-click focuses, a drag rotates, the wheel zooms, F and
             fly mode with W, hover; a failing panel listed in Problems instead of stopping; Viewer.run with
             ImGui's runner replaced (the window settings, frames until exit_after, the screenshot).
  inspector  the object / entity listing (layers per category and team, counts, types, references both ways),
             the drawn level, selection by a click (and the links overlay), a click on the sky, measuring (M, Esc,
             points, the water above a point), the panels, the tooltip, following a reference, main() with
             --select / --measure / --screenshot.
  studio     islands colouring (two islands: two colours, on the drawn ground too), difference colouring (a file
             against itself: no difference, nothing missing; against another file: extra ground orange, DICE's
             missing ground on its own layer), sector / pitch / anomaly colours, cell quads, spawns' islands,
             island gaps, a click on the ground selects its area, the path test, the panels, the compare overlay
             (reference markers, the line to the nearest node), main(): no path data, default files, --compare.
  overlay    a level's AI layer from XML (spawners, area orders, patrol chains, objectives, HasAI), sector offsets,
             the reference report against a NavKit build (nearest nodes, ground), anomalies (small islands,
             spawners on them, orders without a node, lonely nodes), reference sources, main() for both reports.
  pick       the level list of a made-up OPFOR folder (prepared or not, DICE path data), the picker's Open and
             Cancel buttons and its error message (tkinter's main loop replaced: no window is shown).

Pixels are compared with a tolerance (GPUs round differently). Without an OpenGL 3.3 context (no GPU driver) the
test is skipped (exit 0).

  python tests\test_3d_quick.py
"""
import os
import shutil
import sys
import tempfile
import time
import types

import numpy as np

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
STUDIO_DIR = os.path.join(SUITE, 'moh-navigation-studio')
for p in (os.path.join(SUITE, 'moh-auto-navkit', 'tools'), os.path.join(SUITE, 'moh-auto-navkit'),
          os.path.join(SUITE, 'core', 'tests'), os.path.join(SUITE, 'core'),
          os.path.join(SUITE, 'moh-map-inspector'), STUDIO_DIR):
    sys.path.insert(0, p)

import moderngl  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from mohcore import level as levelmod, navdata, viewer  # noqa: E402

BACKGROUND = np.array([0.13, 0.14, 0.16]) * 255
LIGHT = np.array([0.4, 0.8, 0.3]) / np.linalg.norm([0.4, 0.8, 0.3])
FLAT_SHADE = 0.35 + 0.65 * LIGHT[1]               # a horizontal surface's shading (the fragment shader's)
W, H = 1600, 950
WIDE = (2400, 1400)            # the studio's views: its panels leave the middle free for clicks


# ------------------------------------------------------------------------------------------------ offscreen frames
class Offscreen:
    """A viewer drawn into an offscreen framebuffer, its ImGui frames run without a window."""

    def __init__(self, v, size=(W, H), init=True):
        self.v, self.size = v, size
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(*size)
        io.display_framebuffer_scale = imgui.ImVec2(1, 1)
        if init:
            v._init()
        self.fbo = v.ctx.simple_framebuffer(size)
        self.mouse = (size[0] - 5.0, size[1] / 2)

    def frame(self, n=1, background=None, gui=None):
        io = imgui.get_io()
        for _ in range(n):
            io.display_size = imgui.ImVec2(*self.size)
            io.delta_time = 1 / 60
            imgui.new_frame()
            self.fbo.use()
            (background or self.v._background)()
            (gui or self.v._gui)()
            imgui.render()
        return self.pixels()

    def pixels(self):
        w, h = self.size
        return np.flipud(np.frombuffer(self.fbo.read(components=3), np.uint8).reshape(h, w, 3)).astype(np.float64)

    def move(self, x, y, n=1):
        imgui.get_io().add_mouse_pos_event(float(x), float(y))
        self.mouse = (x, y)
        return self.frame(n)

    def button(self, b, down):
        imgui.get_io().add_mouse_button_event(b, down)
        return self.frame()

    def click(self, x, y, b=0):
        self.move(x, y)
        self.button(b, True)
        return self.button(b, False)

    def key(self, k, frames_down=1):
        io = imgui.get_io()
        io.add_key_event(k, True)
        self.frame(frames_down)
        io.add_key_event(k, False)
        return self.frame()


def click_world(off, v, point):
    """A click on a world point: the camera turned onto it, the click in the middle of a view wide enough that no
    panel window is there (ImGui would take the click)."""
    v.camera.focus(point, v.camera.distance)
    off.frame()
    return off.click(off.size[0] / 2, off.size[1] / 2)


def at(img, xy):
    x, y = xy
    return img[int(round(y)), int(round(x))]


def near(px, rgb, tol=12.0):
    return float(np.abs(np.asarray(px) - np.asarray(rgb)).max()) <= tol


def is_background(px):
    return near(px, BACKGROUND, 3)


class FakeRunner:
    """Stands in for imgui-bundle's runner (immapp.run) and hello_imgui's window functions: Viewer.run's callbacks
    are called as the runner calls them, the frames drawn offscreen until the viewer asks to exit."""

    def __init__(self, ini_dir):
        self.ini_dir, self.state, self.params, self.viewers, self.shot, self.frames = ini_dir, None, None, [], None, 0
        real = viewer.hello_imgui
        self.hello = types.SimpleNamespace(RunnerParams=real.RunnerParams, IniFolderType=real.IniFolderType,
                                           DefaultImGuiWindowType=real.DefaultImGuiWindowType,
                                           ini_folder_location=lambda kind: ini_dir,
                                           get_runner_params=lambda: self.state,
                                           final_app_window_screenshot=lambda: self.shot)
        self.immapp = types.SimpleNamespace(run=self.run)

    def run(self, p):
        self.params, self.state = p, types.SimpleNamespace(app_shall_exit=False)
        p.callbacks.post_init()
        off = Offscreen(self.viewers[-1], tuple(p.app_window_params.window_geometry.size), init=False)
        bg, gui = p.callbacks.custom_background, p.callbacks.show_gui
        self.frames = 0
        while not self.state.app_shall_exit and self.frames < 300:
            off.frame(background=bg, gui=gui)
            self.frames += 1
        self.shot = off.pixels().astype(np.uint8)

    def __enter__(self):
        self.saved = viewer.immapp, viewer.hello_imgui, viewer.Viewer._init
        real_init, viewers = viewer.Viewer._init, self.viewers

        def init(v):
            real_init(v)
            viewers.append(v)
        viewer.immapp, viewer.hello_imgui, viewer.Viewer._init = self.immapp, self.hello, init
        return self

    def __exit__(self, *exc):
        viewer.immapp, viewer.hello_imgui, viewer.Viewer._init = self.saved


# ------------------------------------------------------------------------------------------------ viewer
def viewer_checks(check, tmp):
    s = viewer.Scene()
    red, green, blue = s.layer('red', (1.0, 0.1, 0.1)), s.layer('green', (0.1, 1.0, 0.1)), s.layer('lines', (0, 0, 1))
    check(s.layer('green', (0, 0, 0)) == green and s.bounds is None and s.triangle_count() == 0,
          'scene: a layer named again is the same layer; no bounds before any geometry')
    s.add_box((5, 0, 0), 2.0, 22, green)                     # batch 0, layer 1: uploaded after layer 0's
    s.add_box((-5, 0, 0), 2.0, 11, red)                      # batch 1, layer 0
    s.add_lines([[(-5, 3, 0), (5, 3, 0)]], 33, blue)
    s.add_polyline([(0, 4, 0), (1, 4, 0), (1, 4, 1)], 34, blue, closed=True)
    s.add_polyline([(0, 4, 0)], 35, blue)                    # one point: nothing
    lo, hi = s.bounds
    check(s.triangle_count() == 24 and len(s.batches) == 2 and len(s.line_batches) == 2
          and len(s.line_batches[1][0]) == 6 and s.ranges == [(0, 36), (36, 36)],
          'scene: two boxes of 12 triangles, a line and a closed polyline (3 segments), batch ranges')
    check(np.allclose(lo, (-6, -1, -1), atol=0.3) and np.allclose(hi, (6, 1, 1), atol=0.3),
          f'scene bounds: the geometry\'s extent ({lo.round(2).tolist()} .. {hi.round(2).tolist()})')
    tc = viewer.triangle_colors([[1, 0, 0]], 1)
    check(tc.shape == (3, 4) and tc[0].tolist() == [255, 0, 0, 255] and not viewer.triangle_colors(None, 2).any(),
          'per-triangle colours: one RGB per triangle (alpha 255), none: the layer\'s (alpha 0)')
    full = viewer.Scene()
    for k in range(viewer.MAX_LAYERS):
        full.layer(f'l{k}', (0, 0, 0))
    try:
        full.layer('one too many', (0, 0, 0))
        too_many = False
    except ValueError:
        too_many = True
    check(too_many, f'a scene takes {viewer.MAX_LAYERS} layers, no more')

    picked = []
    v = viewer.Viewer(s, 'test', on_pick=picked.append, size=(640, 480))
    check(np.allclose(v.camera.target, (lo + hi) / 2) and abs(v.camera.distance - (np.linalg.norm(hi - lo) * 0.6 + 10)) < 1e-6,
          'the first view frames the bounds: target at their centre, distance from their size')
    v.set_colors(0, np.array([[0.1, 1.0, 0.1]] * 12))          # before the upload: the scene's own copy
    check(v.scene.batches[0][2][0].tolist() == [26, 255, 26, 255], 'set_colors before the window opens changes the scene')
    off = Offscreen(v, (640, 480))
    check(v.layer_ranges == {0: (0, 36), 1: (36, 36)} and v.scene.ranges == [(36, 36), (0, 36)]
          and v.line_ranges == {2: (0, 8)} and not v.scene.batches,
          f'upload: triangles ordered by layer (one range each), batch ranges moved with them, lines ({v.layer_ranges})')
    check(bool(v.renderer) and v.ctx is not None, f'an OpenGL context ({v.renderer})')
    img = off.frame()
    pa, pb = v.project((-5, 0, 0)), v.project((5, 0, 0))
    inside = all(pt is not None and 0 < pt[0] < 640 and 0 < pt[1] < 480 for pt in (pa, pb))
    check(inside, f'both boxes project into the window ({pa}, {pb})')
    a, b = at(img, pa), at(img, pb)
    check(a[0] > 2 * a[1] and a[0] > 2 * a[2] and b[1] > 2 * b[0] and b[1] > 2 * b[2],
          f'drawn: the red box red and the green box green where they project ({a.tolist()}, {b.tolist()})')
    corner = at(img, (3, 3))
    check(is_background(corner) and is_background(at(img, (320, 470))),
          f'the background elsewhere ({corner.tolist()})')
    line_px = at(img, v.project((0, 3, 0)))
    check(line_px[2] > 150 and line_px[0] < 100, f'the line layer drawn in its colour ({line_px.tolist()})')

    oid, world = v.pick(*pa)
    check(oid == 11 and world is not None and abs(world[0] + 5) <= 1.01 and abs(world[1]) <= 1.01,
          f'picking: the red box\'s id at its pixel, and a world point on it ({oid}, {None if world is None else world.round(2).tolist()})')
    check(v.pick(*pb)[0] == 22 and v.pick(3, 3) == (None, None) and v.pick(-1, 5) == (None, None),
          'picking: the green box\'s id; nothing on the background or outside the window')

    v.set_colors(1, np.array([[0.1, 0.1, 1.0]] * 12))         # batch 1 (the red box), after upload
    img = off.frame()
    a2, b2 = at(img, pa), at(img, pb)
    check(a2[2] > 2 * a2[0] and near(b2, b, 2), f'set_colors after upload: the red box turns blue, the green one stays ({a2.tolist()})')
    v.set_colors(1, None)
    img = off.frame()
    check(near(at(img, pa), a, 2), 'set_colors None: the layer\'s colour again')

    v.set_layer_visible(red, False)
    img = off.frame()
    check(not v.layer_visible(red) and is_background(at(img, pa)) and v.pick(*pa)[0] is None and near(at(img, pb), b, 2),
          'a hidden layer is not drawn and not picked; the others are')
    v.set_layer_visible(red, True)
    v.selected = 22
    img = off.frame()
    check(at(img, pb).sum() > b.sum() + 60, f'the selection is drawn lighter ({at(img, pb).tolist()} vs {b.tolist()})')
    v.selected = viewer.NO_ID

    v.set_overlay([((-2, -3, 0), (2, -3, 0), (1.0, 1.0, 0.0))])
    img = off.frame()
    ov = at(img, v.project((0, -3, 0)))
    check(ov[0] > 200 and ov[1] > 200 and ov[2] < 80 and v._overlay_count == 2, f'overlay lines drawn over the scene ({ov.tolist()})')

    # input: a click selects and calls on_pick, a second one soon after focuses
    off.click(*pa)
    check(picked[-1:] == [11] and v.selected == 11 and v.pick_point is not None, f'a click selects what is under it ({picked})')
    v._last_click = time.time()
    off.click(*pa)
    check(not v.camera.fly and v.camera.distance <= 60.0 and np.allclose(v.camera.target, v.pick_point),
          'a double-click focuses the camera on the point clicked')
    v.select_on_click = False
    off.click(3, 3)
    check(picked[-1] is None and v.selected == 11, 'with selection by click off, the click is reported but selects nothing')
    v.select_on_click = True
    off.click(3, 3)
    check(v.selected == viewer.NO_ID and v.pick_point is None, 'a click on the background clears the selection')
    yaw = v.camera.yaw
    off.move(300, 300)
    off.button(0, True)
    off.move(360, 300, 2)
    off.button(0, False)
    check(v.camera.yaw != yaw and picked[-1] is None, f'a left drag rotates the camera ({yaw:.2f} -> {v.camera.yaw:.2f})')
    target = v.camera.target.copy()
    off.button(1, True)
    off.move(380, 320, 2)
    off.button(1, False)
    check(not np.allclose(target, v.camera.target), 'a right drag pans')
    d = v.camera.distance
    imgui.get_io().add_mouse_wheel_event(0.0, 1.0)
    off.frame()
    check(v.camera.distance < d, f'the wheel zooms in ({d:.1f} -> {v.camera.distance:.1f})')
    pb = v.project((5, 0, 0))
    off.move(*pb)                                               # moved: a hover pick is due a moment later
    v._hover_due = time.time() - 0.01
    off.frame()
    check(v.hovered == 22 and v.hover_point is not None, f'hover: the object under a resting mouse ({v.hovered})')
    off.key(imgui.Key.f)
    check(v.camera.fly, 'F switches to fly mode')
    eye = v.camera.eye_fly.copy()
    io = imgui.get_io()
    io.add_mouse_button_event(1, True)
    io.add_key_event(imgui.Key.w, True)
    off.move(390, 330, 5)
    io.add_key_event(imgui.Key.w, False)
    io.add_mouse_button_event(1, False)
    off.frame()
    check(not np.allclose(eye, v.camera.eye_fly), 'fly mode: the right button held and W move the camera')
    proj, view = v.camera.matrices(4 / 3)
    check(proj.shape == view.shape == (4, 4), 'fly mode: camera matrices')
    off.key(imgui.Key.f)
    check(not v.camera.fly, 'F again: orbit mode')
    m = viewer.look_at((0, 10, 0), (0, 0, 0))
    check(np.isfinite(m).all(), 'look_at straight down (no side vector from up) still gives a matrix')

    # a failing panel: listed in Problems, the frame goes on
    def panel_broken(_v):
        raise ValueError('broken on purpose')
    v.panels = [panel_broken]
    v._last = time.time() - 2
    print('(an error on purpose follows: a panel that fails)', flush=True)
    img = off.frame(2)
    check(len(v._problems) == 1 and 'broken panel' in next(iter(v._problems.values())) and v.fps > 0
          and img.shape == (480, 640, 3),
          f'a failing panel is listed in Problems and the view is still drawn ({list(v._problems.values())})')
    v.ctx = None
    v._background()                                             # before the window opens: nothing to draw
    check(len(v._problems) == 1, 'drawing before the context exists does nothing (no new problem)')

    # Viewer.run, with the runner replaced: the window's settings, frames until exit_after, the screenshot
    s2 = viewer.Scene()
    s2.add_box((0, 0, 0), 2.0, 1, s2.layer('a', (0.8, 0.2, 0.2)))
    v2 = viewer.Viewer(s2, 'MOH Test Tool - level', size=(320, 240))
    v2.exit_after = 4
    with FakeRunner(tmp) as r:
        v2.run()
    shot = np.asarray(v2.screenshot) if v2.screenshot is not None else np.zeros((1, 1, 3))
    p = r.params
    check(r.frames == 4 and shot.shape == (240, 320, 3) and float(shot.reshape(-1, 3).std(axis=0).max()) > 5
          and p.app_window_params.window_title == 'MOH Test Tool - level' and p.ini_filename == 'moh-tool-suite/MOH_Test_Tool.ini'
          and os.path.isdir(os.path.join(tmp, 'moh-tool-suite')),
          f'Viewer.run: title, window layout file, {r.frames} frames until exit_after, a screenshot that is not blank')


# ------------------------------------------------------------------------------------------------ the made-up OPFOR folder
def make_opfor(tmp):
    """A Project OPFOR folder without a game: mp_t (test_level.make_level, prepared) and mp_u (a DLC level, not
    prepared, with DICE path data); FbrbTool and the Havok reader stand-ins as test_level uses them."""
    import test_level as tl
    tl.put(os.path.join(tmp, 'game', 'MP', 'MoHMPGame.exe'), b'')
    tl.make_level(tmp)
    win32 = os.path.join(tmp, 'game', 'MP', 'dist', 'win32')
    tl.put(os.path.join(win32, 'levels', 'mp_t', 'level-00.fbrb'), 'bundle')
    tl.put(os.path.join(win32, 'patch', 'levels', 'mp_u', 'level-00.fbrb'), 'bundle')
    tool = os.path.join(tmp, 'fake_fbrbtool.py')
    tl.put(tool, 'import shutil, sys\nshutil.copytree(open(sys.argv[2]).read(), sys.argv[3])\n')
    return tool


def guid(k):
    return f'00000000-0000-0000-0000-{k:012d}'


def ai_layer_xml():
    """A level partition's AI layer as DbxEdit's XML: HasAI, an AI spawner and a disabled human one, an area order far
    from any node, a patrol over an AreaGeometry chain, an objective."""
    import test_level as tl

    def t(x, y, z):
        return f'<complex name="Transform">{tl.mat(x, y, z)}</complex>'
    gs = 'GameSharedResources.'
    return ('<partition guid="P">'
            '<instance guid="LD" type="Entity.LevelData"><field name="HasAI">true</field></instance>'
            f'<instance guid="S1" type="{gs}SoldierSpawnEntityData"><field name="Name">ai one</field>{t(4, 0, 4)}'
            '<field name="PlayerType">SSAiPlayer</field><field name="Enabled">true</field><field name="Team">Team1</field>'
            '<field name="SoldierSquadId">Squad1</field><array name="Squad"><item ref="SQ" /></array></instance>'
            f'<instance guid="S2" type="{gs}SoldierSpawnEntityData">{t(12, 0, 4)}<field name="PlayerType">SSHumanPlayer</field>'
            '<field name="Enabled">false</field><field name="Team">Team2</field></instance>'
            f'<instance guid="S3" type="{gs}SoldierSpawnEntityData">{t(0, 0, 0)}</instance>'
            f'<instance guid="AO" type="Entity.AreaOrderEntityData"><field name="Name">far order</field>{t(90, 0, 90)}'
            '<field name="AreaType">Defend</field></instance>'
            f'<instance guid="PO" type="Entity.PatrolOrderEntityData"><field name="Name">patrol</field>'
            f'<array name="RouteWaypoints"><item ref="{guid(1)}" /></array></instance>'
            f'<instance guid="{guid(1)}" type="Entity.AreaGeometryEntityData">{t(4, 0, 4)}<field ref="{guid(2)}" name="Next" /></instance>'
            f'<instance guid="{guid(2)}" type="Entity.AreaGeometryEntityData">{t(20, 0, 4)}<field name="Next" ref="null" /></instance>'
            f'<instance guid="OBJ" type="Entity.ReferenceObjectData">{t(12, 0, 4)}'
            '<field ref="objects/objects/mpobjective/bomb/BOMB" name="ReferencedObject" /></instance>'
            '</partition>')


# ------------------------------------------------------------------------------------------------ pick
def pick_checks(check, tmp):
    from mohcore import pick
    import test_level as tl
    tl.put(os.path.join(tmp, 'game', 'MP', 'Output', 'win32', 'ai', 'levels', 'mp_u', 'infantry', 'pathdata.hpd'), b'x')
    items = pick.levels()
    check(items == [('mp_t', 'prepared'), ('mp_u', 'not prepared yet - about 20 s the first time, DICE path data')],
          f'pick.levels: the levels of the OPFOR folder, prepared or not, with DICE path data ({items})')
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as e:
        check(True, f'the picker window: skipped, no tkinter ({e})')
        return
    real_loop, real_err, real_levels = tk.Tk.mainloop, messagebox.showerror, pick.levels
    shown = []

    def press(label, select=None):
        def mainloop(root, n=0):
            root.withdraw()                                   # never mapped: no window on the screen
            widgets = list(root.winfo_children())
            while widgets:
                w = widgets.pop()
                widgets += w.winfo_children()
                if isinstance(w, ttk.Treeview) and select:
                    w.selection_set(select)
                    shown.append(w.get_children())
            for w in root.winfo_children():
                for b in w.winfo_children():
                    if isinstance(b, ttk.Button) and b.cget('text') == label:
                        b.invoke()
                        return
        return mainloop
    try:
        tk.Tk.mainloop = press('Open', 'mp_u')
        chosen = pick.pick_level('Test picker')
        check(chosen == 'mp_u' and list(shown[-1]) == ['mp_t', 'mp_u'], f'the picker lists the levels; Open gives the one selected ({chosen})')
        tk.Tk.mainloop = press('Cancel')
        check(pick.pick_level('Test picker') is None, 'Cancel gives None')
        errors = []
        messagebox.showerror = lambda title, msg: errors.append(msg)

        def broken():
            raise OSError('no game here')
        pick.levels = broken
        check(pick.pick_level('Test picker') is None and errors and 'no game here' in errors[0],
              f'no level list: an error message and None ({errors[:1]})')
    finally:
        tk.Tk.mainloop, messagebox.showerror, pick.levels = real_loop, real_err, real_levels


# ------------------------------------------------------------------------------------------------ inspector
def inspector_checks(check, tmp, lvl):
    import inspector as insmod
    ins = insmod.Inspector(lvl)
    by = {e.guid: e for e in lvl.entities if e.group is None}
    lay = {g: ins.scene.layers[ins.entity_layer[by[g].index]][0] for g in ('SP1', 'SP2', 'ALT1', 'VS', 'CP', 'RO_LADDER', 'RO_FX')}
    check(lay == {'SP1': 'soldier spawn, team 1', 'SP2': 'soldier spawn, team 2', 'ALT1': 'soldier spawn, team 1',
                  'VS': 'vehicle spawn, neutral', 'CP': 'capture point', 'RO_LADDER': 'ladder', 'RO_FX': 'effect'},
          f'inspector: each entity in its category\'s layer, spawns by team ({lay})')
    check(ins.counts.get('soldier spawn, team 1') == 2 and ins.counts.get('soldier spawn, team 2') == 2
          and sum(ins.counts.values()) == len(lvl.entities) and ins.types[0][1] >= ins.types[-1][1],
          f'the listing: counts per layer, types most common first ({ins.counts})')
    refby = [e.guid for _, e in ins.referrers.get(by['ALT1'].index, [])]
    check(refby == ['SP1'], f'references both ways: ALT1 is referenced by SP1 ({refby})')
    links = ins.links(by['SP1'])
    check(len(links) == 2 and all(c == insmod.REF_TO for _, _, c in links) and ins.links(None) == [],
          'the links of a selection: to the two alternate spawn points it references (one reference finds nothing)')
    links_cp = ins.links(by['A1'])
    check(any(c == insmod.REF_FROM for _, _, c in links_cp), 'and from the entities that reference it (an area point)')

    v = viewer.Viewer(ins.scene, 'inspector', panels=[ins.panel_layers, ins.panel_selection, ins.panel_measure, ins.panel_status])
    ins.scene_triangles = ins.scene.triangle_count()
    v.on_pick = ins.on_pick(v)
    for name in insmod.HIDDEN_AT_START:
        v.set_layer_visible(ins.layer_index[name], False)
    lo, hi = ins.scene.bounds
    check(np.allclose(v.camera.target, (lo + hi) / 2), 'the first view frames the level\'s bounds')
    off = Offscreen(v)
    img = off.frame(2)
    corners = [v.project(by[g].position) for g in ('B1', 'B2', 'B3')]     # the combat area's corners: the level's extent
    mid = v.project((lo + hi) / 2)
    check(all(c is not None and 0 <= c[0] < W and 0 <= c[1] < H for c in corners) and np.allclose(mid, (W / 2, H / 2), atol=1),
          f'the first view frames the level\'s bounds ({lo.round(1).tolist()} .. {hi.round(1).tolist()}): their centre in '
          'the middle, the combat area\'s corners inside the view')
    terrain_px = at(img, v.project(lvl.terrain[0].mean(axis=0)))
    terrain_c = np.array(insmod.LAYERS[0][1]) * FLAT_SHADE * 255
    drawn = float((np.abs(img - BACKGROUND).max(axis=2) > 3).mean())
    check(near(terrain_px, terrain_c) and drawn > 0.001,
          f'the level is drawn: the terrain (20 m up) in its colour where it projects ({terrain_px.tolist()}), '
          f'objects and outlines ({100 * drawn:.2f} % of the view)')

    # select a soldier spawn by a click: terrain and water hidden, the camera on it
    for name in ('terrain', 'water', 'combat area', 'area point'):
        v.set_layer_visible(ins.layer_index[name], False)
    sp1 = by['SP1']
    ins.select(v, sp1, focus=True)
    check(ins.selected is sp1 and v.selected == sp1.index and np.allclose(v.camera.target, sp1.position)
          and len(v.overlay) == 2, 'select with focus: the camera on it, its links as the overlay')
    target = sp1.position + np.array([0, 0.6, 0])
    v.camera.distance = 15.0
    off.frame()
    ins.select(v, None)
    pt = v.project(target)
    off.click(*pt)
    check(ins.selected is sp1 and v.selected == sp1.index and len(v.overlay) == 2,
          f'a click on the spawn\'s box selects it ({ins.selected.guid if ins.selected else None})')
    img = off.frame(2)
    check(not is_background(at(img, pt)), 'the selection panel, the links drawn')
    off.click(W - 5, H / 2)
    check(ins.selected is None and v.selected == viewer.NO_ID, 'a click on the sky clears the selection')

    # measuring: M, two clicks on the box, the panel; Esc clears
    off.key(imgui.Key.m)
    check(ins.measuring and not v.select_on_click, 'M: measuring on, clicks no longer select')
    off.click(*pt)
    off.click(pt[0] + 3, pt[1] + 2)
    off.frame(2)
    check(len(ins.measure) == 2 and ins.selected is None and len(v.overlay) == 1 + 2 * 3,
          f'two measure points: a segment and a cross at each ({len(ins.measure)} points, {len(v.overlay)} segments)')
    off.key(imgui.Key.escape)
    check(ins.measure == [] and v.overlay == [], 'Esc clears the points')
    ins.set_measuring(v, False)
    check(v.select_on_click, 'measuring off: clicks select again')

    class Water:
        vertices = np.array([[0, 5, 0], [10, 5, 0], [0, 5, 10]], np.float32)
        triangles = np.array([[0, 1, 2]])
        shader = ['river']

        def __len__(self):
            return 1
    real_water = lvl.water
    lvl.water = Water()
    d_in, d_out = ins.water_above(np.array([1.0, 2.0, 1.0])), ins.water_above(np.array([20.0, 2.0, 20.0]))
    check(d_in is not None and abs(d_in - 3.0) < 1e-6 and d_out is None, f'the water surface above a point ({d_in}, {d_out})')
    ins.measure = [np.array([1.0, 2.0, 1.0]), np.array([4.0, 6.0, 5.0])]
    ins.update_overlay(v)
    off.frame(2)
    lvl.water = real_water
    check(not v._problems and len(v.overlay) == 1 + 2 * 3, 'the measure panel with water and terrain under the points')
    ins.measure = []
    ins.update_overlay(v)

    # tooltips, following a reference and going back, the property tree
    for hov in (insmod.TERRAIN_ID, insmod.WATER_ID, sp1.index, 0xFFFFF0):
        v.hovered = hov
        v.hover_point = np.array([1.0, 2.0, 3.0])
        off.frame()
    v.hovered = viewer.NO_ID
    ins.select(v, sp1)
    ins.follow(v, by['ALT1'])
    off.frame(2)
    check(ins.selected is by['ALT1'] and ins.history == [sp1], 'following a reference: the target selected, the way back kept')
    ins.select(v, ins.history.pop(), focus=True)
    ins.results = [sp1, by['ALT1']]
    off.frame(2)
    check(not v._problems, f'the panels draw without problems ({list(v._problems.values())})')

    # main(): the level by name, --select, --measure, --screenshot
    shot = os.path.join(tmp, 'inspector.png')
    with FakeRunner(tmp) as r:
        rc = insmod.main(['inspector.py', 'mp_t', '--select', 'sp1', '--measure', '1,2,3;4,5,6', '--screenshot', shot, '--frames', '3'])
    vi = r.viewers[-1] if r.viewers else None
    import matplotlib.image as mpimg
    im = mpimg.imread(shot)[..., :3] if os.path.isfile(shot) else np.zeros((1, 1, 3))
    check(rc == 0 and r.frames == 3 and vi is not None and vi.selected == by['SP1'].index and not vi.select_on_click
          and float(np.std(im.reshape(-1, 3), axis=0).max()) > 0.02 and r.params.app_window_params.window_title == 'MOH Map Inspector - MP_T',
          f'main(): the level opened by name, the selection and measuring set, a screenshot saved that is not blank (exit {rc})')


# ------------------------------------------------------------------------------------------------ studio and overlay
def studio_checks(check, tmp, lvl):
    import overlay
    import studio as stmod
    import test_level as tl
    import test_navdata as tn
    a_path, b_path = os.path.join(tmp, 'a.hpd'), os.path.join(tmp, 'b.hpd')
    open(a_path, 'wb').write(tn.made_up())
    open(b_path, 'wb').write(tn.made_up(roof=16.0, east=False, side_y=3.0))
    nav, other = navdata.load(a_path), navdata.load(b_path)

    # the level's own AI layer (for the anomalies) beside its partition, and a reference level's
    tl.put(os.path.join(tmp, 'work', 'levels', 'mp_t', 'dist', 'levels', 'mp_t.xml'), ai_layer_xml())
    ref_dir = os.path.join(tmp, 'reference')
    ref_xml = os.path.join(ref_dir, 'mp_r.xml')
    tl.put(ref_xml, ai_layer_xml())
    tl.put(os.path.join(ref_dir, 'mp_r', 'Precomputed', 'Infantry', 'SectorOffsets.xml'),
           '<partition><field name="XOffset">-12</field><field name="ZOffset">34</field></partition>')

    # overlay.py: the AI layer, the reference report, anomalies, sources
    layer = overlay.read_ai_layer(ref_xml)
    check(layer['has_ai'] is True and len(layer['spawners']) == 2 and len(layer['area_orders']) == 1
          and layer['patrols'] == [{'name': 'patrol', 'points': [[4.0, 0.0, 4.0], [20.0, 0.0, 4.0]]}]
          and layer['objectives'] == [{'pos': [12.0, 0.0, 4.0], 'type': 'mpobjective'}],
          'overlay: the AI layer of a level - HasAI, spawners (one at the origin has no position), an area order, '
          f'a patrol\'s waypoint chain, an objective ({ {k: len(x) if isinstance(x, list) else x for k, x in layer.items()} })')
    s1 = layer['spawners'][0]
    check(s1 == {'pos': [4.0, 0.0, 4.0], 'name': 'ai one', 'player_type': 'SSAiPlayer', 'enabled': True, 'team': 'Team1',
                 'squad_id': 'Squad1', 'in_squad_entity': True} and layer['spawners'][1]['enabled'] is False,
          f'a spawner\'s fields ({s1})')
    rep = overlay.reference_report('mp_t', ref_xml, tmp, a_path)
    rows = {(r['pos'][0], r['pos'][2]): r for r in rep['spawners']}
    check(rep['summary'] == {'spawners': 2, 'ai_spawners_enabled': 1, 'area_orders': 1, 'patrols': 1, 'objectives': 1}
          and rep['reference_sector_offsets'] == (-12, 34) and rep['reference_has_ai'] is True
          and rows[(4.0, 4.0)]['nearest_node_m'] == 0.0 and rows[(4.0, 4.0)]['navkit_ground'] is True
          and rep['area_orders'][0]['nearest_node_m'] is None and len(rep['patrol_points']) == 2,
          f'the reference report: the summary, sector offsets, nearest NavKit nodes and ground ({rep["summary"]})')
    norep = overlay.reference_report('mp_t', ref_xml, tmp, None)
    check(norep['spawners'][0]['nearest_node_m'] is None and norep['target_navkit_build'] is None,
          'without a NavKit build: no node figures')
    an = overlay.anomalies(nav, layer)
    check([x['island'] for x in an['small_islands']] == [0, 1] and len(an['spawners_on_small_islands']) == 2
          and [x['name'] for x in an['orders_without_node']] == ['far order']
          and {x['node'] for x in an['lonely_nodes']} == {2, 4} and an['sliver_areas'] == [],
          'anomalies: both islands small (under 25 nodes), the spawners on them, the far order without a node, the '
          f'roof and the lone node without a same-floor neighbour ({ {k: len(x) for k, x in an.items()} })')
    idx = overlay.NavIndex(nav)
    check(idx.has_ground([4, 0, 4]) and not idx.has_ground([4, 5, 4]) and idx.nearest_node([42, 0, 42], r=1e9, exclude=4)[0] > 30,
          'ground and nearest-node lookups (a floor 5 m up has no ground; beyond 200 m all nodes are searched)')
    lbl, xml, hpd, off_xml = overlay.reference_source('level:mp_t', tmp)
    check(xml and xml.endswith('mp_t.xml') and hpd is None and off_xml is None and 'OPFOR level copy' in lbl,
          f'reference sources: a prepared level\'s partition, no DICE path data ({lbl})')
    xl, xx, xh, _ = overlay.reference_source('xbox:ons_mp_005', tmp)
    try:
        overlay.reference_source('nonsense:x', tmp)
        refused = False
    except ValueError:
        refused = True
    check(xx is None and xh is None and 'Xbox' in xl and refused and overlay.sector_offsets(None) is None,
          'an Xbox source not unpacked: nothing found; an unknown source is refused')
    out_a = os.path.join(tmp, 'anom.json')
    out_r = os.path.join(tmp, 'ref.json')
    rc1 = overlay.main(['overlay.py', 'mp_t', '--anomalies', '--candidate', a_path, '--out', out_a])
    rc2 = overlay.main(['overlay.py', 'mp_t', '--reference', ref_xml, '--candidate', a_path, '--out', out_r])
    rc3 = overlay.main(['overlay.py'])
    import json
    ja = json.load(open(out_a)) if os.path.isfile(out_a) else {}
    check(rc1 == 0 and rc2 == 0 and rc3 == 1 and ja.get('counts', {}).get('lonely_nodes') == 2 and os.path.isfile(out_r),
          'overlay main(): the anomaly report and the reference report as JSON; no level: the usage')
    try:
        overlay.anomaly_report('mp_none', tmp)
        no_build = False
    except FileNotFoundError:
        no_build = True
    check(no_build, 'an anomaly report without a NavKit build is refused')

    # studio: islands
    quads = stmod.cell_quads(np.array([[0.4, 0.4]]), np.array([5.0]), tn.flat_heightfield(5.0))
    check(quads.shape == (2, 3, 3) and np.allclose(quads[:, :, 1], 5.0 + stmod.LIFT)
          and len(stmod.box_edges((0, 0, 0), 2.0)) == 12, 'cell quads (on terrain: at its height) and box edges')
    st = stmod.Studio(lvl, {'NavKit': nav}, 'island')
    st.triangles = st.scene.triangle_count()
    cols = st.cell_colors('NavKit')
    isl = np.array([a.island for a in nav.areas])[nav.cell_area]
    main_c, other_c = np.unique(cols[isl == 0], axis=0), np.unique(cols[isl == 1], axis=0)
    check(len(main_c) == 1 and len(other_c) == 1 and np.allclose(main_c[0], stmod.MAIN) and not np.allclose(other_c[0], main_c[0]),
          'island colours: the main network green, the second island another colour')
    rep_sp = st.spawn_report['NavKit']
    check(len(st.spawns) == 4 and all(i == 0 for _, i, _ in rep_sp), 'the level\'s soldier spawns, all on the main network')
    gaps = st.island_gaps('NavKit')
    check(len(gaps) == 2 and abs(gaps[1] - np.hypot(42 - 20, 42 - 4)) < 0.01, f'island gaps: the lone island 43.9 m from the main one ({gaps})')
    check(st.area_of_id(stmod.ID_BASE['NavKit'] + 3) == ('NavKit', 3) and st.area_of_id(stmod.ID_BASE['DICE']) is None,
          'object ids of areas')
    check(st.anomaly is not None and len(st.anomaly['orders_without_node']) == 1, 'the build\'s anomalies against the level\'s own AI layer')
    for m in ('sector', 'pitch', 'anomaly', 'difference'):
        st.color = m
        c = st.cell_colors('NavKit')
        check(c.shape == (len(nav.cell_area), 3), f'colour by {m}: one colour per cell')
    st.color = 'pitch'
    pitch = st.cell_colors('NavKit')
    check(np.allclose(pitch[nav.cell_area == 1], stmod.DETAILED) and np.allclose(pitch[nav.cell_area == 0], stmod.NORMAL),
          'by pitch: the 0.2 m zone orange')
    st.color = 'island'

    v = viewer.Viewer(st.scene, 'studio', panels=[st.panel_main, st.panel_islands, st.panel_selection, st.panel_path,
                                                  st.panel_compare, st.panel_anomaly, st.panel_status])
    v.on_pick = st.on_pick(v)
    keep = {'NavKit ground'}
    for name, i in st.layers.items():
        v.set_layer_visible(i, name in keep)
    v.camera.focus((20.0, 0.0, 20.0), 70.0)
    off = Offscreen(v, WIDE)
    st.apply_colors(v)
    img = off.frame(2)
    p0, p4 = v.project((6.0, stmod.LIFT, 2.0)), v.project((42.0, stmod.LIFT, 42.0))
    c0, c4 = at(img, p0), at(img, p4)
    check(near(c0, np.array(stmod.MAIN) * FLAT_SHADE * 255) and near(c4, np.array(other_c[0]) * FLAT_SHADE * 255),
          f'drawn: the main network\'s ground green and the lone island\'s in its own colour ({c0.tolist()}, {c4.tolist()})')
    click_world(off, v, (6.0, stmod.LIFT, 2.0))
    check(st.selected == ('NavKit', 0), f'a click on the ground selects its area ({st.selected})')
    off.frame(2)
    h = st.on_pick(v)
    h(0x100000)
    check(st.selected == ('spawn', 0), 'a spawn marker\'s id selects the spawn')
    off.frame(2)
    v.hovered = stmod.ID_BASE['NavKit'] + 1
    off.frame()
    v.hovered = viewer.NO_ID
    h(None)
    check(st.selected is None, 'a click on nothing clears the selection')

    # the path test
    off.key(imgui.Key.p)
    check(st.path_mode and not v.select_on_click, 'P: the path test, clicks no longer select')
    click_world(off, v, (6.0, stmod.LIFT, 2.0))
    click_world(off, v, (20.0, stmod.LIFT, 4.0))
    r = getattr(st, 'routes', {}).get('NavKit')
    check(r is not None and r.found and len(st.path_segs) > 6, f'two clicks: a route between them ({r.reason if r else None})')
    off.frame(2)
    st.path_point(v, [4.0, 0.0, 4.0])
    st.path_point(v, [42.0, 0.0, 42.0])
    r = st.routes['NavKit']
    check(not r.found and r.gap is not None and any(c == stmod.MISSING for _, _, c in st.path_segs),
          f'to the lone island: no route, the gap marked ({r.reason})')
    off.frame(2)
    st.set_path_mode(v, False)
    check(v.select_on_click, 'the path test off: clicks select again')

    # difference: a file against itself, and against another file
    same = stmod.Studio(lvl, {'NavKit': nav, 'DICE': nav}, 'difference')
    sc = same.cell_colors('NavKit')
    missing = same.layers['DICE ground missing in NavKit']
    check(same.diff['reproduced'] == 100.0 and same.diff['extra'] == 0.0 and np.allclose(sc, stmod.SAME_C)
          and not any(int(t[0] >> 24) == missing for _, t, _ in same.scene.batches) and same.same['DICE'].all(),
          f'difference, a file against itself: every cell the same colour, nothing extra or missing ({same.diff})')
    diff = stmod.Studio(lvl, {'NavKit': nav, 'DICE': other}, 'difference')
    diff.triangles = diff.scene.triangle_count()
    dc = diff.cell_colors('NavKit')
    check(np.allclose(dc[nav.cell_area == 1], stmod.EXTRA) and np.allclose(dc[nav.cell_area == 0], stmod.SAME_C)
          and np.allclose(dc[nav.cell_area == 2], stmod.LADDER) and diff.diff['reproduced'] < 100.0
          and diff.diff['ladder'] > 0,
          f'difference against another file: the ground it lacks orange, ladder ground its own colour ({diff.diff})')
    dv = viewer.Viewer(diff.scene, 'studio diff', panels=[diff.panel_main, diff.panel_islands, diff.panel_status])
    for name, i in diff.layers.items():
        dv.set_layer_visible(i, name in ('NavKit ground', 'DICE ground missing in NavKit'))
    dv.camera.focus((20.0, 0.0, 20.0), 70.0)
    doff = Offscreen(dv)
    img = doff.frame(2)
    e1 = at(img, dv.project((12.0, stmod.LIFT, 4.0)))
    s0 = at(img, dv.project((6.0, stmod.LIFT, 2.0)))
    check(near(e1, np.array(stmod.EXTRA) * FLAT_SHADE * 255) and near(s0, np.array(stmod.SAME_C) * FLAT_SHADE * 255),
          f'drawn: extra ground orange, ground both have grey-green ({e1.tolist()}, {s0.tolist()})')
    diff.island_file = 'DICE'
    diff.color = 'island'
    diff.apply_colors(dv)
    img2 = doff.frame(2)
    check(not near(at(img2, dv.project((12.0, stmod.LIFT, 4.0))), e1, 3), 'the colouring changed in the drawing (island colours)')

    # the compare overlay: reference markers, the line to the nearest NavKit node
    compare = {'report': rep, 'layer': layer, 'pathdata': other, 'slug': 'mp_r'}
    cs = stmod.Studio(lvl, {'NavKit': nav}, 'island', compare)
    cs.triangles = cs.scene.triangle_count()
    kinds = [k for k, _ in cs.ref_items]
    check(kinds == ['spawner', 'spawner', 'area order', 'patrol', 'objective'] and cs.compare_index is not None,
          f'compare: one marker per reference spawner, order, patrol and objective ({kinds})')
    cs.select_reference(2)
    check(cs.selected == ('ref', 2) and cs.ref_near is not None and len(cs.ref_segs) == 1 and cs.ref_segs[0][2] == stmod.MISSING,
          'a reference marker selected: the line to the nearest NavKit node, red beyond 15 m')
    cv = viewer.Viewer(cs.scene, 'studio compare', panels=[cs.panel_main, cs.panel_selection, cs.panel_compare,
                                                           cs.panel_anomaly, cs.panel_islands, cs.panel_status])
    cv.on_pick = cs.on_pick(cv)
    coff = Offscreen(cv)
    coff.frame(2)
    cs.on_pick(cv)(stmod.REF_BASE + 0)
    check(cs.selected == ('ref', 0) and cs.ref_segs[0][2] == (0.3, 1.0, 0.3) and len(cv.overlay) == 1,
          'a click on a spawner marker: the line to its node (green within 15 m) as the overlay')
    coff.frame(2)
    cs.on_pick(cv)(stmod.ID_BASE['NavKit'])
    check(cs.ref_segs == [] and cv.overlay == [], 'a click elsewhere: the line goes')
    coff.frame(2)
    check(not v._problems and not cv._problems and not dv._problems,
          f'the studio\'s panels draw without problems ({list(v._problems.values()) + list(cv._problems.values())})')
    saved = stmod.save_json(tmp, 'x.json', {'a': np.arange(2)})
    check(os.path.isfile(saved) and saved.startswith(os.path.join(tmp, 'work', 'navstudio')), 'save_json: into work\\navstudio')
    check(stmod.parse_args(['s', 'lv', '--edit', '--color', 'pitch']) == ({'--edit': True, '--color': 'pitch'}, ['lv']),
          'parse_args: flags and options')

    # main(): no path data; then the default files (NavKit's build, DICE's), difference, the path test, --compare
    with FakeRunner(tmp) as r:
        rc0 = stmod.main(['studio.py', 'mp_t'])
    check(rc0 == 1 and not r.viewers, 'main() without path data or --compare: exit 1, no window')
    build = os.path.join(tmp, 'work', 'navkit', 'mp_t', 'pathdata.hpd')
    dice = os.path.join(tmp, 'game', 'MP', 'Output', 'win32', 'ai', 'levels', 'mp_t', 'infantry', 'pathdata.hpd')
    tl.put(build, tn.made_up())
    tl.put(dice, tn.made_up(roof=16.0, east=False, side_y=3.0))
    cand, refp = stmod.default_files(tmp, 'mp_t')
    check(cand == build and refp == dice, 'default files: NavKit\'s newest build and the level\'s DICE path data')
    shot = os.path.join(tmp, 'studio.png')
    with FakeRunner(tmp) as r:
        rc = stmod.main(['studio.py', 'mp_t', '--color', 'difference', '--screenshot', shot, '--frames', '3',
                         '--island', '1', '--path', '4,0,4;20,0,4', '--focus', '10,0,4'])
    sv = r.viewers[-1] if r.viewers else None
    import matplotlib.image as mpimg
    im = mpimg.imread(shot)[..., :3] if os.path.isfile(shot) else np.zeros((1, 1, 3))
    check(rc == 0 and sv is not None and r.frames == 3 and float(np.std(im.reshape(-1, 3), axis=0).max()) > 0.02
          and np.allclose(sv.camera.target, (10, 0, 4)) and sv.select_on_click is False,
          f'main(): both files, difference colours, the path test, a screenshot that is not blank (exit {rc})')
    with FakeRunner(tmp) as r:
        rc = stmod.main(['studio.py', 'mp_t', '--compare', ref_xml, '--frames', '2', '--screenshot', shot])
    sv = r.viewers[-1] if r.viewers else None
    names = [n for n, _ in sv.scene.layers] if sv is not None else []
    check(rc == 0 and 'reference AI spawners (enabled)' in names and 'reference objectives' in names,
          f'main() --compare: the reference level\'s AI layer on the level (exit {rc})')


# ------------------------------------------------------------------------------------------------ main
def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as e:  # noqa: BLE001 - no OpenGL 3.3 on this machine, whatever the driver says: the test is skipped
        print(f'skipped: no OpenGL 3.3 context could be created ({type(e).__name__}: {e})')
        return 0
    tmp = tempfile.mkdtemp(prefix='test_3d_quick_')
    imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename(os.path.join(tmp, 'imgui.ini'))        # not imgui.ini in the current folder
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures   # no renderer: the font textures are never drawn
    import levelobjects
    import navkit
    real = levelobjects.triangles, navkit.find_tool, os.environ.get('OPFOR_ROOT')
    unit = np.array([[[0, 0, 0], [1, 0, 0], [0, 0, 1]]], np.float32)
    try:
        print(f'GPU: {ctx.info.get("GL_RENDERER", "?")}')
        viewer_checks(check, tmp)
        tool = make_opfor(tmp)
        levelobjects.triangles = lambda path: unit
        navkit.find_tool = lambda root, exe, toolkit: [sys.executable, tool]
        os.environ['OPFOR_ROOT'] = tmp
        pick_checks(check, tmp)
        lvl = levelmod.load('mp_t', log=lambda *a: None)
        inspector_checks(check, tmp, lvl)
        studio_checks(check, tmp, lvl)
    finally:
        levelobjects.triangles, navkit.find_tool = real[:2]
        if real[2] is None:
            os.environ.pop('OPFOR_ROOT', None)
        else:
            os.environ['OPFOR_ROOT'] = real[2]
        imgui.destroy_context()
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
