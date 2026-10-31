"""The suite's 3D view: GPU-drawn triangles with per-object ids, orbit and fly camera, exact picking, panels.

A tool builds a Scene (batches of triangles, each tagged with an object id and a layer), then runs a Viewer with
its own panel functions. Picking renders the object ids into a hidden buffer and reads the pixel under the mouse.

Camera: orbit - left drag rotates, right or middle drag pans, wheel zooms, double-click focuses on what is under
the mouse; fly - hold the right button and use W A S D (Q / E down / up, Shift faster). F toggles orbit / fly.
"""
from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import moderngl
import numpy as np
from imgui_bundle import hello_imgui, imgui, immapp

MAX_LAYERS = 32
NO_ID = 0xFFFFFF
OVERLAY_MAX = 32768             # overlay segments drawn (links, measurements, path tests, edits)

VERTEX = """
#version 330
uniform mat4 mvp;
in vec3 in_pos;
in uint in_tag;            // bits 0-23 object id, 24-31 layer
in vec4 in_col;            // own colour when alpha > 0.5, else the layer's
out vec3 v_world;
flat out uint v_tag;
flat out vec4 v_col;
void main() {
    v_world = in_pos;
    v_tag = in_tag;
    v_col = in_col;
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""
FRAGMENT = """
#version 330
uniform vec3 palette[32];
uniform uint hidden;       // one bit per layer
uniform uint selected;     // object id, or 0xFFFFFF
uniform uint hovered;
uniform vec3 light;
uniform int pick;          // 1: write the object id instead of a colour
uniform int lines;         // 1: drawing lines (no surface normal to shade by)
in vec3 v_world;
flat in uint v_tag;
flat in vec4 v_col;
out vec4 color;
void main() {
    uint layer = v_tag >> 24u;
    uint id = v_tag & 0xFFFFFFu;
    if (((hidden >> layer) & 1u) != 0u) discard;
    if (pick == 1) {
        color = vec4(float(id & 255u), float((id >> 8u) & 255u), float((id >> 16u) & 255u), 255.0) / 255.0;
        return;
    }
    float shade = 1.0;
    if (lines == 0) {
        vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));
        shade = 0.35 + 0.65 * abs(dot(n, light));
    }
    vec3 c = (v_col.a > 0.5 ? v_col.rgb : palette[layer]) * shade;
    if (id == selected) c = mix(c, vec3(1.0, 1.0, 1.0), 0.6);
    else if (id == hovered) c = mix(c, vec3(1.0, 1.0, 1.0), 0.2);
    color = vec4(c, 1.0);
}
"""
# overlay: coloured lines the tool sets every frame (links of the selection, measurements), drawn over everything
OVERLAY_VERTEX = """
#version 330
uniform mat4 mvp;
in vec3 in_pos;
in vec3 in_col;
out vec3 v_col;
void main() { v_col = in_col; gl_Position = mvp * vec4(in_pos, 1.0); }
"""
OVERLAY_FRAGMENT = """
#version 330
in vec3 v_col;
out vec4 color;
void main() { color = vec4(v_col, 1.0); }
"""


# ------------------------------------------------------------------------------------------------ scene

def triangle_colors(colors: np.ndarray | Sequence | None, n: int, per: int = 3) -> np.ndarray:
    """Per-vertex RGBA bytes from one RGB per primitive (alpha 255), or zeros (alpha 0: the layer's colour)."""
    if colors is None:
        return np.zeros((n * per, 4), dtype=np.uint8)
    c = np.clip(np.asarray(colors, dtype=np.float32).reshape(n, 3) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out = np.empty((n, 4), dtype=np.uint8)
    out[:, :3], out[:, 3] = c, 255
    return np.repeat(out, per, axis=0)


class Scene:
    """Triangles to draw: add batches, then the viewer uploads them once."""

    def __init__(self) -> None:
        self.layers: list[tuple[str, tuple[float, float, float]]] = []  # (name, colour)
        # (positions (n*3, 3) float32, tags (n*3,) uint32, colours (n*3, 4) uint8)
        self.batches: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        # (positions (n*2, 3) float32, tags (n*2,) uint32, colours (n*2, 4) uint8)
        self.line_batches: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        self.ranges: list[tuple[int, int]] = []  # per triangle batch: (first vertex, vertex count), for Viewer.set_colors
        self._vertices = 0
        self._sample: list[np.ndarray] = []

    def layer(self, name: str, colour: tuple[float, float, float]) -> int:
        for i, (n, _) in enumerate(self.layers):
            if n == name:
                return i
        if len(self.layers) >= MAX_LAYERS:
            raise ValueError('too many layers')
        self.layers.append((name, colour))
        return len(self.layers) - 1

    def add_triangles(self, triangles: np.ndarray, object_id: int | np.ndarray, layer: int,
                      colors: np.ndarray | None = None) -> int:
        """triangles: (n, 3, 3) world positions. object_id: one id, or one per triangle (an (n,) array).
        colors: None (the layer's colour) or one RGB per triangle ((n, 3) floats 0-1). Returns the batch index."""
        t = np.asarray(triangles, dtype=np.float32).reshape(-1, 3)
        n = len(t) // 3
        ids = np.broadcast_to(np.asarray(object_id, dtype=np.uint32) & 0xFFFFFF, (n,))
        tag = np.repeat((np.uint32(layer) << np.uint32(24)) | ids, 3).astype(np.uint32)
        self.batches.append((t, tag, triangle_colors(colors, n)))
        self.ranges.append((self._vertices, len(t)))
        self._vertices += len(t)
        step = max(1, len(t) // 2000)
        self._sample.append(t[::step])
        return len(self.batches) - 1

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Where the bulk of the geometry is: the 1-99 % range of a sample of positions (a few far-away objects,
        such as sky or background pieces, do not decide the camera's first view)."""
        if not self._sample:
            return None
        pts = np.concatenate(self._sample)
        return np.percentile(pts, 1, axis=0), np.percentile(pts, 99, axis=0)

    def add_indexed(self, vertices: np.ndarray, triangles: np.ndarray, object_id: int | np.ndarray, layer: int) -> None:
        self.add_triangles(np.asarray(vertices, dtype=np.float32)[np.asarray(triangles)], object_id, layer)

    def add_box(self, centre: Sequence[float] | np.ndarray, size: float, object_id: int, layer: int) -> None:
        c = np.asarray(centre, dtype=np.float32)
        h = size / 2.0
        corners = np.array([[x, y, z] for x in (-h, h) for y in (-h, h) for z in (-h, h)], dtype=np.float32) + c
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        tris = []
        for a, b, cc, d in faces:
            tris += [corners[[a, b, cc]], corners[[a, cc, d]]]
        self.add_triangles(np.array(tris), object_id, layer)

    def add_lines(self, segments: np.ndarray, object_id: int | np.ndarray, layer: int,
                  colors: np.ndarray | None = None) -> None:
        """segments: (n, 2, 3) world positions; object_id one id or one per segment; colors None or (n, 3)."""
        s = np.asarray(segments, dtype=np.float32).reshape(-1, 3)
        n = len(s) // 2
        ids = np.broadcast_to(np.asarray(object_id, dtype=np.uint32) & 0xFFFFFF, (n,))
        tag = np.repeat((np.uint32(layer) << np.uint32(24)) | ids, 2).astype(np.uint32)
        self.line_batches.append((s, tag, triangle_colors(colors, n, per=2)))

    def add_polyline(self, points: np.ndarray | Sequence, object_id: int, layer: int, closed: bool = False) -> None:
        p = np.asarray(points, dtype=np.float32)
        if len(p) < 2:
            return
        q = np.concatenate([p, p[:1]]) if closed else p
        self.add_lines(np.stack([q[:-1], q[1:]], axis=1), object_id, layer)

    def triangle_count(self) -> int:
        return sum(len(x[0]) for x in self.batches) // 3


# ------------------------------------------------------------------------------------------------ camera

def perspective(fov_y: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_y) / 2)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def look_at(eye: Sequence[float] | np.ndarray, target: Sequence[float] | np.ndarray,
            up: Sequence[float] = (0, 1, 0)) -> np.ndarray:
    eye, target, up = (np.asarray(v, dtype=np.float64) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-9:
        s = np.array([1.0, 0, 0])
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m.astype(np.float32)


class Camera:
    def __init__(self) -> None:
        self.target = np.zeros(3)
        self.yaw, self.pitch, self.distance = 0.6, 0.6, 300.0
        self.fly = False
        self.eye_fly = np.array([0.0, 100.0, 0.0])
        self.fov = 60.0

    def frame(self, lo: np.ndarray, hi: np.ndarray) -> None:
        self.target = (np.asarray(lo) + np.asarray(hi)) / 2
        self.distance = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) * 0.6 + 10

    def direction(self) -> np.ndarray:
        return np.array([math.cos(self.pitch) * math.sin(self.yaw), math.sin(self.pitch), math.cos(self.pitch) * math.cos(self.yaw)])

    def eye(self) -> np.ndarray:
        return self.eye_fly if self.fly else self.target + self.direction() * self.distance

    def matrices(self, aspect: float) -> tuple[np.ndarray, np.ndarray]:
        eye = self.eye()
        target = eye - self.direction() if self.fly else self.target
        near = max(0.05, (self.distance if not self.fly else 50.0) / 2000.0)
        return perspective(self.fov, aspect, near, near * 200000.0), look_at(eye, target)

    def toggle_fly(self) -> None:
        if not self.fly:
            self.eye_fly = self.eye().copy()
        else:
            self.target = self.eye_fly - self.direction() * self.distance
        self.fly = not self.fly

    def focus(self, point: Sequence[float] | np.ndarray, distance: float | None = None) -> None:
        self.fly = False
        self.target = np.asarray(point, dtype=np.float64)
        if distance:
            self.distance = distance


# ------------------------------------------------------------------------------------------------ viewer

class Viewer:
    """Runs the window. panels: functions called every frame inside ImGui (for the tool's windows).
    on_pick(object_id or None): called when the user clicks; the tool decides what selecting means."""

    def __init__(self, scene: Scene, title: str, panels: Iterable[Callable[[Viewer], object]] = (),
                 on_pick: Callable[[int | None], object] | None = None, size: tuple[int, int] = (1600, 950)) -> None:
        self.scene, self.title, self.panels, self.on_pick, self.size = scene, title, list(panels), on_pick, size
        self.camera = Camera()
        if scene.bounds is not None:
            self.camera.frame(*scene.bounds)
        self.hidden = 0
        self.selected = NO_ID
        self.hovered = NO_ID
        self.hover_point: np.ndarray | None = None
        self.ctx: moderngl.Context | None = None
        self.renderer = ''                # the GPU OpenGL runs on (set when the window opens)
        self.fps = 0.0
        self._last = time.time()
        self._frames = 0
        self.exit_after: int | None = None  # frames, for screenshots in tests
        self.screenshot: Any = None
        self._click_start: tuple[float, float] | None = None
        self._last_click = 0.0
        self.pick_point: np.ndarray | None = None  # world point of the last click (None: clicked the sky)
        self.select_on_click = True       # a tool's mode (measuring) can turn selection by click off
        # errors caught in a frame: (where, type, message) -> text shown
        self._problems: dict[tuple[str, str, str], str] = {}
        # (a, b, colour) segments, set by the tool (set_overlay), drawn over the scene
        self.overlay: list[tuple[Sequence[float], Sequence[float], Sequence[float]]] = []
        self._overlay_key: int | None = None
        self._overlay_version = 0

    # --- GPU ----------------------------------------------------------------------------------------
    def _init(self) -> None:
        io = imgui.get_io()                  # a panel that fails half-way: its windows are closed, not an assert
        io.config_error_recovery = True
        io.config_error_recovery_enable_assert = False
        io.config_error_recovery_enable_tooltip = False
        self.ctx = moderngl.create_context()
        self.renderer = self.ctx.info.get('GL_RENDERER', '?').split('/')[0]
        from . import errors
        errors.logger().info(f'GPU: {self.renderer}, OpenGL {self.ctx.info.get("GL_VERSION", "?")}')
        print(f'GPU: {self.renderer}' + ('  (on a PC with two GPUs, set python.exe to "High performance" in Windows '
                                         'Settings > System > Display > Graphics for the faster one)'
                                         if 'Radeon(TM) Graphics' in self.renderer or 'Intel' in self.renderer else ''))
        self.prog = self.ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        # batches ordered by layer, so each layer is one range of the buffer and hidden layers are not drawn at all
        b = self.scene.batches
        order = sorted(range(len(b)), key=lambda k: int(b[k][1][0] >> 24) if len(b[k][1]) else 0)
        self.layer_ranges, start = {}, 0
        new_ranges = list(self.scene.ranges)
        for k in order:
            n = len(b[k][0])
            lay = int(b[k][1][0] >> 24) if n else 0
            first, count = self.layer_ranges.get(lay, (start, 0))
            self.layer_ranges[lay] = (first, count + n)
            new_ranges[k] = (start, n)
            start += n
        self.scene.ranges = new_ranges
        b = [b[k] for k in order]
        pos = np.concatenate([x[0] for x in b]) if b else np.zeros((3, 3), np.float32)
        tag = np.concatenate([x[1] for x in b]) if b else np.zeros(3, np.uint32)
        col = np.concatenate([x[2] for x in b]) if b else np.zeros((3, 4), np.uint8)
        self.vbo_pos = self.ctx.buffer(pos.astype(np.float32).tobytes())
        self.vbo_tag = self.ctx.buffer(tag.astype(np.uint32).tobytes())
        self.vbo_col = self.ctx.buffer(col.tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(self.vbo_pos, '3f', 'in_pos'), (self.vbo_tag, 'u', 'in_tag'),
                                                     (self.vbo_col, '4f1', 'in_col')])
        self.line_vao = None
        lb = sorted(self.scene.line_batches, key=lambda x: int(x[1][0] >> 24) if len(x[1]) else 0)
        self.line_ranges, start = {}, 0
        for x in lb:
            lay = int(x[1][0] >> 24) if len(x[1]) else 0
            first, count = self.line_ranges.get(lay, (start, 0))
            self.line_ranges[lay] = (first, count + len(x[0]))
            start += len(x[0])
        if lb:
            self.line_vbos = tuple(self.ctx.buffer(np.concatenate([x[k] for x in lb]).tobytes()) for k in range(3))
            self.line_vao = self.ctx.vertex_array(self.prog, [(self.line_vbos[0], '3f', 'in_pos'), (self.line_vbos[1], 'u', 'in_tag'),
                                                              (self.line_vbos[2], '4f1', 'in_col')])
        self.overlay_prog = self.ctx.program(vertex_shader=OVERLAY_VERTEX, fragment_shader=OVERLAY_FRAGMENT)
        self.overlay_vbo = self.ctx.buffer(reserve=6 * 4 * 2 * OVERLAY_MAX, dynamic=True)
        self.overlay_vao = self.ctx.vertex_array(self.overlay_prog, [(self.overlay_vbo, '3f 3f', 'in_pos', 'in_col')])
        self._overlay_count = 0
        palette = [c for _, c in self.scene.layers] + [(0.5, 0.5, 0.5)] * (MAX_LAYERS - len(self.scene.layers))
        self.prog['palette'].value = [tuple(float(x) for x in c) for c in palette]
        light = np.array([0.4, 0.8, 0.3])
        self.prog['light'].value = tuple(light / np.linalg.norm(light))
        self.pick_fbo = None
        self.scene.batches = []           # uploaded; free the CPU copy
        self.scene.line_batches = []

    def _ensure_pick_fbo(self, w: int, h: int) -> None:
        if self.pick_fbo is None or self.pick_fbo.size != (w, h):
            if self.pick_fbo is not None:
                self.pick_fbo.release()
            self.pick_fbo = self.ctx.framebuffer(color_attachments=[self.ctx.texture((w, h), 4)],
                                                 depth_attachment=self.ctx.depth_texture((w, h)))

    def _mvp(self, w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        proj, view = self.camera.matrices(w / max(h, 1))
        return proj @ view, proj, view

    def _draw(self) -> None:
        w, h = self._window_size()
        if w <= 0 or h <= 0:
            return
        self.ctx.viewport = (0, 0, w, h)
        self.ctx.clear(0.13, 0.14, 0.16, depth=1.0)
        self.ctx.enable(moderngl.DEPTH_TEST)
        mvp, _, _ = self._mvp(w, h)
        self.prog['mvp'].write(mvp.T.astype(np.float32).tobytes())
        self.prog['hidden'].value = self.hidden
        self.prog['selected'].value = self.selected
        self.prog['hovered'].value = self.hovered
        self.prog['pick'].value = 0
        self.prog['lines'].value = 0
        self._render_layers()
        self.ctx.disable(moderngl.DEPTH_TEST)
        self._draw_overlay(mvp)

    def _render_layers(self) -> None:
        """The visible layers only: each is one range of the triangle (and line) buffer."""
        for lay, (first, count) in self.layer_ranges.items():
            if count and self.layer_visible(lay):
                self.vao.render(moderngl.TRIANGLES, vertices=count, first=first)
        if self.line_vao is not None:
            lines = self.prog['lines'].value
            self.prog['lines'].value = 1
            for lay, (first, count) in self.line_ranges.items():
                if count and self.layer_visible(lay):
                    self.line_vao.render(moderngl.LINES, vertices=count, first=first)
            self.prog['lines'].value = lines

    def _draw_overlay(self, mvp: np.ndarray) -> None:
        if not self.overlay:
            return
        key = self._overlay_version
        if key != self._overlay_key:
            seg = self.overlay[:OVERLAY_MAX]
            data = np.array([[*a, *c, *b, *c] for a, b, c in seg], dtype=np.float32)
            self.overlay_vbo.write(data.tobytes())
            self._overlay_count, self._overlay_key = 2 * len(seg), key
        self.overlay_prog['mvp'].write(mvp.T.astype(np.float32).tobytes())
        self.overlay_vao.render(moderngl.LINES, vertices=self._overlay_count)

    def set_colors(self, batch: int, colors: np.ndarray | None) -> None:
        """New colours for a triangle batch after upload: one RGB per triangle ((n, 3) floats 0-1), or None for
        the layer's colour again. (Before the window opens, the scene's own copy is changed.)"""
        start, count = self.scene.ranges[batch]
        data = triangle_colors(colors, count // 3)
        if self.ctx is None:
            p, t, _ = self.scene.batches[batch]
            self.scene.batches[batch] = (p, t, data)
            return
        self.vbo_col.write(data.tobytes(), offset=start * 4)

    def set_overlay(self, segments: Iterable[tuple[Sequence[float], Sequence[float], Sequence[float]]]) -> None:
        """Replace the overlay: a list of (a, b, (r, g, b)) segments, uploaded once per call."""
        self.overlay = list(segments)
        self._overlay_version += 1

    def project(self, point: Sequence[float] | np.ndarray) -> tuple[float, float] | None:
        """Window position (x, y from the top left, in imgui units) of a world point, or None if behind the camera."""
        w, h = self._window_size()
        mvp, _, _ = self._mvp(w, h)
        p = mvp.astype(np.float64) @ np.array([*point, 1.0])
        if p[3] <= 1e-6:
            return None
        s = imgui.get_io().display_framebuffer_scale
        return ((p[0] / p[3] + 1) / 2 * w / s.x, (1 - p[1] / p[3]) / 2 * h / s.y)

    def pick(self, x: float, y: float) -> tuple[int | None, np.ndarray | None]:
        """Object id under window pixel (x, y from the top left), or None; also the world point there."""
        w, h = self._window_size()
        px, py = int(x), int(h - 1 - y)
        if not (0 <= px < w and 0 <= py < h):
            return None, None
        self._ensure_pick_fbo(w, h)
        self.pick_fbo.use()
        self.ctx.viewport = (0, 0, w, h)
        self.pick_fbo.clear(1.0, 1.0, 1.0, 1.0, depth=1.0)
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.scissor = (px, py, 1, 1)           # only the pixel under the mouse is drawn
        mvp, proj, view = self._mvp(w, h)
        self.prog['mvp'].write(mvp.T.astype(np.float32).tobytes())
        self.prog['hidden'].value = self.hidden
        self.prog['pick'].value = 1
        self._render_layers()
        self.prog['pick'].value = 0
        self.ctx.scissor = None
        rgba = self.pick_fbo.read(viewport=(px, py, 1, 1), components=4)
        depth = np.frombuffer(self.pick_fbo.read(viewport=(px, py, 1, 1), attachment=-1, dtype='f4'), dtype=np.float32)[0]
        self.ctx.screen.use()
        self.ctx.disable(moderngl.DEPTH_TEST)
        oid = rgba[0] | (rgba[1] << 8) | (rgba[2] << 16)
        if oid == NO_ID:
            return None, None
        ndc = np.array([2 * px / w - 1, 2 * py / h - 1, 2 * depth - 1, 1.0])
        world = np.linalg.inv(mvp.astype(np.float64)) @ ndc
        return oid, world[:3] / world[3]

    def _window_size(self) -> tuple[int, int]:
        d = imgui.get_io().display_size
        s = imgui.get_io().display_framebuffer_scale
        return int(d.x * s.x), int(d.y * s.y)

    # --- input --------------------------------------------------------------------------------------
    def _input(self) -> None:
        io = imgui.get_io()
        scale = io.display_framebuffer_scale
        mx, my = io.mouse_pos.x * scale.x, io.mouse_pos.y * scale.y
        if not io.want_capture_keyboard and imgui.is_key_pressed(imgui.Key.f):
            self.camera.toggle_fly()
        if io.want_capture_mouse:
            return
        dx, dy = io.mouse_delta.x, io.mouse_delta.y
        cam = self.camera
        if cam.fly:
            if imgui.is_mouse_down(imgui.MouseButton_.right):
                cam.yaw -= dx * 0.005
                cam.pitch = max(-1.5, min(1.5, cam.pitch + dy * 0.005))
                speed = (60.0 if io.key_shift else 15.0) * io.delta_time
                fwd = -cam.direction()
                right = np.cross(fwd, [0, 1, 0])
                right /= max(np.linalg.norm(right), 1e-9)
                move = np.zeros(3)
                for key, v in ((imgui.Key.w, fwd), (imgui.Key.s, -fwd), (imgui.Key.d, right), (imgui.Key.a, -right),
                               (imgui.Key.e, np.array([0, 1.0, 0])), (imgui.Key.q, np.array([0, -1.0, 0]))):
                    if imgui.is_key_down(key):
                        move += v
                cam.eye_fly = cam.eye_fly + move * speed
        else:
            if imgui.is_mouse_down(imgui.MouseButton_.left) and (abs(dx) + abs(dy)) > 0:
                cam.yaw -= dx * 0.006
                cam.pitch = max(-1.55, min(1.55, cam.pitch + dy * 0.006))
            if (imgui.is_mouse_down(imgui.MouseButton_.right) or imgui.is_mouse_down(imgui.MouseButton_.middle)) and (abs(dx) + abs(dy)) > 0:
                right = np.cross(cam.direction(), [0, 1, 0])
                right /= max(np.linalg.norm(right), 1e-9)
                up = np.cross(right, cam.direction())
                k = cam.distance * 0.0015
                cam.target = cam.target + right * dx * k + up * dy * k
            if io.mouse_wheel:
                cam.distance = max(1.0, cam.distance * (0.85 ** io.mouse_wheel))
        # click (press and release without dragging) selects; double-click focuses
        if imgui.is_mouse_clicked(imgui.MouseButton_.left):
            self._click_start = (mx, my)
        if imgui.is_mouse_released(imgui.MouseButton_.left) and self._click_start is not None:
            sx, sy = self._click_start
            self._click_start = None
            if abs(mx - sx) + abs(my - sy) < 4:
                oid, point = self.pick(mx, my)
                now = time.time()
                if now - self._last_click < 0.35 and point is not None:
                    self.camera.focus(point, min(self.camera.distance, 60.0))
                self._last_click = now
                self.pick_point = point
                if self.select_on_click:
                    self.selected = oid if oid is not None else NO_ID
                if self.on_pick:
                    self._guarded('a click', self.on_pick, oid)
        # hover: once the mouse has moved and then rests for a moment (a pick re-renders the scene)
        if abs(dx) + abs(dy) > 0:
            self._hover_due = time.time() + 0.12
        elif getattr(self, '_hover_due', None) and time.time() >= self._hover_due:
            self._hover_due = None
            oid, point = self.pick(mx, my)
            self.hovered = oid if oid is not None else NO_ID
            self.hover_point = point

    # --- frame --------------------------------------------------------------------------------------
    def _background(self) -> None:
        if self.ctx is None:
            return
        self._guarded('drawing the 3D view', self._draw)

    def _guarded(self, where: str, fn: Callable[..., object], *args: Any) -> None:
        """Runs one part of a frame; an error is logged once (with its cause) and listed in the Problems window
        instead of closing the tool."""
        try:
            fn(*args)
        except Exception as e:          # noqa: BLE001 - a frame's boundary: any error is listed, the tool keeps running
            key = (where, type(e).__name__, str(e))
            if key not in self._problems:
                from . import errors
                errors.report(e, where=where, show=False)
                cause, fix = errors.explain(e)
                self._problems[key] = f'{where}: {cause}  ({fix})'

    def _problems_window(self) -> None:
        if not self._problems:
            return
        from . import errors
        io = imgui.get_io()
        imgui.set_next_window_pos((io.display_size.x / 2 - 300, 60), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((600, 0), imgui.Cond_.first_use_ever)
        imgui.begin('Problems', flags=imgui.WindowFlags_.always_auto_resize)
        imgui.text_colored(imgui.ImVec4(1.0, 0.45, 0.35, 1.0), 'Something went wrong; the tool keeps running.')
        for key, msg in list(self._problems.items())[-8:]:
            imgui.text_wrapped(msg)
            if imgui.small_button(f'dismiss##{hash(key)}'):
                self._problems.pop(key, None)
        imgui.text_disabled(f'Details: {errors._state.get("path") or "(no log file)"}')
        imgui.end()

    def _gui(self) -> None:
        self._frames += 1
        self._fps_frames = getattr(self, '_fps_frames', 0) + 1
        now = time.time()
        if now - self._last >= 1.0:
            self.fps = self._fps_frames / (now - self._last)
            self._fps_frames, self._last = 0, now
        self._guarded('handling the mouse and keys', self._input)
        for p in self.panels:
            self._guarded(f'the {getattr(p, "__name__", "panel").replace("panel_", "").replace("_", " ")} panel', p, self)
        self._problems_window()
        if self.exit_after is not None and self._frames >= self.exit_after:
            hello_imgui.get_runner_params().app_shall_exit = True

    def layer_visible(self, layer: int) -> bool:
        return not (self.hidden >> layer) & 1

    def set_layer_visible(self, layer: int, visible: bool) -> None:
        self.hidden = (self.hidden & ~(1 << layer)) if visible else (self.hidden | (1 << layer))

    def run(self) -> None:
        p = hello_imgui.RunnerParams()
        p.app_window_params.window_title = self.title
        p.app_window_params.window_geometry.size = self.size
        p.callbacks.post_init = self._init
        p.callbacks.custom_background = self._background
        p.callbacks.show_gui = self._gui
        p.fps_idling.enable_idling = False
        # window layout: one file per tool in %APPDATA%\moh-tool-suite, not in the current folder
        os.makedirs(os.path.join(hello_imgui.ini_folder_location(hello_imgui.IniFolderType.app_user_config_folder),
                                 'moh-tool-suite'), exist_ok=True)
        p.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
        p.ini_filename = 'moh-tool-suite/' + ''.join(ch if ch.isalnum() else '_' for ch in self.title.split(' - ')[0]) + '.ini'
        p.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.no_default_window
        immapp.run(p)
        if self.exit_after is not None:
            try:
                self.screenshot = hello_imgui.final_app_window_screenshot()
            except Exception:           # noqa: BLE001 - the native binding's failure: no screenshot (tests only)
                self.screenshot = None
