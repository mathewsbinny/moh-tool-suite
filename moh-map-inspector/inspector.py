"""MOH Map Inspector: browse a level in 3D and inspect every object's source and properties.

  python inspector.py <level>                 e.g. inspector.py sp_01 or mp_005 (prepared on first use)
  python inspector.py <level> --screenshot out.png [--select <guid>] [--frames N]   draw, save, exit (tests)
                     [--measure "x,y,z;x,y,z;..."]   with measure points (tests)

Click an object to select it, double-click to fly the camera to it. Left drag rotates, right or middle drag
pans, the wheel zooms; F switches to fly mode (hold the right button, W A S D, Q / E, Shift faster).
M switches measuring on and off: clicks then add points (distance, height difference); Esc clears them.
The selection's links are drawn over the scene: yellow to what it references, cyan from what references it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'core'))
if __name__ == '__main__':                      # the log and error reports start before the heavy imports,
    from mohcore import errors                 # so a missing library is reported too
    errors.setup('map-inspector', gui='--screenshot' not in sys.argv)

import numpy as np  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from mohcore import dbx, level as levelmod, viewer  # noqa: E402

TERRAIN_ID = 0xFFFFFE
WATER_ID = 0xFFFF00          # + the index of the triangle's shader in Inspector.water_shaders
BLUE, RED, GREY = (0.20, 0.55, 1.00), (1.00, 0.30, 0.25), (0.70, 0.70, 0.75)
# layer: colour, marker size for entities without collision (None: not drawn without collision)
LAYERS = [
    ('terrain', (0.42, 0.47, 0.36), None),
    ('water', (0.18, 0.42, 0.78), None),
    ('placed object', (0.62, 0.60, 0.56), 1.0),
    ('destructible', (0.80, 0.64, 0.46), 1.0),
    ('dynamic object', (0.62, 0.48, 0.34), 0.6),
    ('ladder', (0.95, 0.90, 0.35), 1.0),
    ('group member', (0.55, 0.53, 0.50), None),
    ('soldier spawn, team 1', BLUE, 1.2),
    ('soldier spawn, team 2', RED, 1.2),
    ('soldier spawn, no team', GREY, 1.2),
    ('vehicle spawn, team 1', (0.35, 0.80, 1.00), 2.5),
    ('vehicle spawn, team 2', (1.00, 0.58, 0.35), 2.5),
    ('vehicle spawn, neutral', (0.75, 0.85, 0.80), 2.5),
    ('objective', (1.00, 0.60, 0.10), 2.0),
    ('capture point', (1.00, 0.85, 0.20), 2.0),
    ('combat area', (0.85, 0.25, 0.85), 1.5),
    ('ai order', (0.55, 0.90, 0.30), 1.0),
    ('effect', (0.60, 0.80, 0.95), 0.6),
    ('area point', (0.70, 0.70, 0.95), 0.6),
    ('other', (0.60, 0.60, 0.60), 0.6),
]
HIDDEN_AT_START = {'area point', 'other', 'effect'}
TEAM_NAME = {1: 'team 1', 2: 'team 2', 0: 'neutral', None: 'no team'}
REF_TO, REF_FROM, MEASURE = (1.0, 0.9, 0.2), (0.3, 0.95, 1.0), (1.0, 1.0, 1.0)


def layer_name(e):
    if e.category == 'soldier spawn':
        return {1: 'soldier spawn, team 1', 2: 'soldier spawn, team 2'}.get(e.team, 'soldier spawn, no team')
    if e.category == 'vehicle spawn':
        return {1: 'vehicle spawn, team 1', 2: 'vehicle spawn, team 2'}.get(e.team, 'vehicle spawn, neutral')
    return e.category


class Inspector:
    def __init__(self, lvl):
        self.lvl = lvl
        self.scene = viewer.Scene()
        self.layer_index = {}
        for name, colour, _ in LAYERS:
            self.layer_index[name] = self.scene.layer(name, colour)
        sizes = {name: size for name, _, size in LAYERS}
        self.entity_layer = [self.layer_index.get(layer_name(e), self.layer_index['other']) for e in lvl.entities]
        with_mesh = set()
        for m in lvl.meshes:
            self.scene.add_triangles(m.triangles, m.entity, self.entity_layer[m.entity])
            with_mesh.add(m.entity)
        for e in lvl.entities:
            if e.index in with_mesh or e.position is None:
                continue
            size = sizes.get(layer_name(e), 0.6)
            if size:
                lay = self.entity_layer[e.index]
                centre = e.position + np.array([0, size / 2, 0])
                self.scene.add_box(centre, size, e.index, lay)
                if e.category in ('soldier spawn', 'vehicle spawn'):        # facing: the transform's forward row
                    self.scene.add_lines([[centre, centre + e.matrix[2, :3] * (size * 2.5)]], e.index, lay)
        for s in lvl.shapes:                                               # outlines, in their owner's layer
            lay = self.entity_layer[s.entity]
            self.scene.add_polyline(s.points, s.entity, lay, closed=s.closed)
            if s.height > 0:
                top = s.points + np.array([0, s.height, 0])
                self.scene.add_polyline(top, s.entity, lay, closed=s.closed)
                self.scene.add_lines(np.stack([s.points, top], axis=1), s.entity, lay)
        if lvl.terrain is not None:
            self.scene.add_indexed(lvl.terrain[0], lvl.terrain[1], TERRAIN_ID, self.layer_index['terrain'])
        self.water_shaders = []
        w = lvl.water
        if w is not None and len(w):
            shader = np.array(w.shader if w.shader else [''] * len(w))
            for name in dict.fromkeys(shader):                             # one id per shader, for the tooltip
                k = len(self.water_shaders)
                self.water_shaders.append(name)
                self.scene.add_indexed(w.vertices, w.triangles[shader == name], WATER_ID + k, self.layer_index['water'])
        self.counts = {}
        for e in lvl.entities:
            self.counts[layer_name(e)] = self.counts.get(layer_name(e), 0) + 1
        self.guids = {e.guid.lower(): e for e in lvl.entities}
        self.referrers = {}                                                # entity index -> [(field, entity)]
        for e in lvl.entities:
            if e.short_type == 'LevelData':                                # lists every object; not a link
                continue
            for path, r in e.refs():
                t = self.guids.get(r.lower())
                if t is not None and t is not e:
                    self.referrers.setdefault(t.index, []).append((path, e))
        types = {}
        for e in lvl.entities:
            types[e.short_type] = types.get(e.short_type, 0) + 1
        self.types = sorted(types.items(), key=lambda kv: -kv[1])
        self.type_filter = ''
        self.selected = None
        self.search = ''
        self.results = []
        self.history = []
        self.measuring = False
        self.measure = []                                                  # world points

    # --- selection ----------------------------------------------------------------------------------
    def select(self, v, entity, focus=False):
        self.selected = entity
        v.selected = entity.index if entity is not None else viewer.NO_ID
        if entity is not None and focus and entity.position is not None:
            v.camera.focus(entity.position, 40.0)
        self.update_overlay(v)

    def links(self, e):
        """Segments to the positioned entities the selection references (yellow) and that reference it (cyan)."""
        out = []
        if e is None or e.position is None:
            return out
        for _, r in e.refs():
            t = self.guids.get(r.lower())
            if t is not None and t is not e and t.position is not None:
                out.append((e.position, t.position, REF_TO))
        for _, t in self.referrers.get(e.index, ()):
            if t.position is not None:
                out.append((t.position, e.position, REF_FROM))
        return out

    def update_overlay(self, v):
        seg = self.links(self.selected)
        for a, b in zip(self.measure, self.measure[1:]):
            seg.append((a, b, MEASURE))
        for p in self.measure:                                             # small crosses at measure points
            for d in ((0.4, 0, 0), (0, 0.4, 0), (0, 0, 0.4)):
                seg.append((p - np.array(d), p + np.array(d), MEASURE))
        v.set_overlay(seg)

    def on_pick(self, v):
        def handler(oid):
            if self.measuring:
                if v.pick_point is not None:
                    self.measure.append(np.asarray(v.pick_point, dtype=np.float64))
                self.update_overlay(v)
                return
            if oid is None or oid >= len(self.lvl.entities):               # sky, terrain, water
                self.select(v, None)
            else:
                self.select(v, self.lvl.entities[oid])
        return handler

    def set_measuring(self, v, on):
        self.measuring = on
        v.select_on_click = not on

    def keys(self, v):
        if imgui.get_io().want_capture_keyboard:
            return
        if imgui.is_key_pressed(imgui.Key.m):
            self.set_measuring(v, not self.measuring)
        if imgui.is_key_pressed(imgui.Key.escape) and self.measure:
            self.measure = []
            self.update_overlay(v)

    def follow(self, v, target):
        self.history.append(self.selected)
        self.select(v, target, focus=True)

    # --- panels -------------------------------------------------------------------------------------
    def panel_layers(self, v):
        self.keys(v)
        imgui.set_next_window_pos((10, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((320, 640), imgui.Cond_.first_use_ever)
        imgui.begin('Layers and search')
        imgui.text(f'{self.lvl.name}: {len(self.lvl.entities)} entities')
        imgui.separator()
        for name, colour, _ in LAYERS:
            n = self.counts.get(name, 1 if name == 'terrain' else 0)
            if name == 'water':
                n = len(self.lvl.water) if self.lvl.water is not None else 0
            if not n:
                continue
            i = self.layer_index[name]
            imgui.color_button(f'##c{name}', imgui.ImVec4(*colour, 1.0), 0, (14, 14))
            imgui.same_line()
            changed, on = imgui.checkbox(f'{name} ({n})', v.layer_visible(i))
            if changed:
                v.set_layer_visible(i, on)
        imgui.separator()
        changed_q, self.search = imgui.input_text('search', self.search)
        changed_t = False
        if imgui.begin_combo('type', self.type_filter or 'any type'):
            if imgui.selectable('any type', not self.type_filter)[0]:
                self.type_filter, changed_t = '', True
            for t, n in self.types:
                if imgui.selectable(f'{t} ({n})', self.type_filter == t)[0]:
                    self.type_filter, changed_t = t, True
            imgui.end_combo()
        if changed_q or changed_t:
            q = self.search.lower().strip()
            pool = [e for e in self.lvl.entities if not self.type_filter or e.short_type == self.type_filter]
            if len(q) >= 2:
                pool = [e for e in pool if q in ' '.join((e.guid, e.type, e.id or '', e.name or '', e.blueprint or '')).lower()]
            elif not self.type_filter:
                pool = []
            self.results = pool[:500]
        imgui.text_disabled(f'{len(self.results)} shown - GUID, type, id, name or blueprint')
        imgui.begin_child('results', (0, 0))
        for e in self.results:
            label = f'{e.short_type}  {e.name or e.id or e.blueprint or ""}##{e.index}'
            if imgui.selectable(label, self.selected is e)[0]:
                self.select(v, e, focus=True)
        imgui.end_child()
        imgui.end()

    def tree(self, v, el, path):
        """Property tree of an element: containers expand, values show inline, local references are links."""
        for k, c in enumerate(el.children or ()):
            name = c.attr('name') or c.tag
            ref = c.attr('ref')
            if c.children is not None:
                label = f'{name}  [{len(c.children)}]##{path}/{k}'
                if imgui.tree_node(label):
                    self.tree(v, c, f'{path}/{k}')
                    imgui.tree_pop()
            elif ref is not None:
                target = self.guids.get(ref.lower())
                if target is not None:
                    if imgui.small_button(f'-> {name}##{path}/{k}'):
                        self.follow(v, target)
                    imgui.same_line()
                    imgui.text_disabled(f'{target.short_type} {target.name or target.id or ""}')
                else:
                    imgui.text(f'{name} -> {ref}')
            else:
                val = dbx.value(c)
                imgui.text(f'{name} = {val if len(val) < 140 else val[:140] + " ..."}')

    def panel_selection(self, v):
        w = imgui.get_io().display_size.x
        imgui.set_next_window_pos((w - 480, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((470, 720), imgui.Cond_.first_use_ever)
        imgui.begin('Selection')
        e = self.selected
        if e is None:
            imgui.text_disabled('Click an object (double-click: fly to it).')
        else:
            if self.history and imgui.small_button('< back'):
                self.select(v, self.history.pop(), focus=True)
            imgui.text(e.short_type)
            imgui.text_disabled(e.type)
            facts = [('guid', e.guid), ('id', e.id), ('name', e.name), ('category', e.category),
                     ('blueprint', e.blueprint), ('blueprint object', e.blueprint_type),
                     ('source', e.partition + (f'  ({e.group})' if e.group else ''))]
            if e.team is not None:
                facts.append(('team', TEAM_NAME[e.team]))
            if e.ai is not None:
                facts.append(('player type', 'AI (PlayerType is not SSHumanPlayer)' if e.ai else 'human player'))
            if e.destructible:
                facts.append(('destructible', 'yes (health states / breakable parts)'))
            for label, value in facts:
                if value:
                    imgui.text(f'{label}: {value}')
            if e.position is not None:
                p = e.position
                imgui.text(f'position: {p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}')
            if imgui.button('fly to'):
                self.select(v, e, focus=True)
            imgui.same_line()
            if imgui.button('copy guid'):
                imgui.set_clipboard_text(e.guid)
            refs = self.referrers.get(e.index, [])
            if refs and imgui.tree_node(f'referenced by [{len(refs)}]##refby'):
                for k, (f, t) in enumerate(refs[:200]):
                    if imgui.small_button(f'<- {t.short_type} {t.name or t.id or ""}##rb{k}'):
                        self.follow(v, t)
                    imgui.same_line()
                    imgui.text_disabled(f)
                imgui.tree_pop()
            imgui.separator()
            imgui.begin_child('props', (0, 0))
            self.tree(v, e.element, str(e.index))
            imgui.end_child()
        imgui.end()

    def panel_measure(self, v):
        if not self.measuring and not self.measure:
            return
        imgui.set_next_window_pos((340, 10), imgui.Cond_.first_use_ever)
        imgui.begin('Measure', flags=imgui.WindowFlags_.always_auto_resize)
        changed, on = imgui.checkbox('measuring (M)', self.measuring)
        if changed:
            self.set_measuring(v, on)
        imgui.same_line()
        if imgui.small_button('clear (Esc)'):
            self.measure = []
            self.update_overlay(v)
        pts = self.measure
        if not pts:
            imgui.text_disabled('Click points on the terrain or objects.')
        else:
            p = pts[-1]
            imgui.text(f'{len(pts)} points; last at {p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}')
            if self.lvl.heightfield is not None:
                imgui.text(f'last point above terrain: {p[1] - float(self.lvl.heightfield.height(p[0], p[2])):+.2f} m')
            depth = self.water_above(p)
            if depth is not None:
                imgui.text(f'water surface above it: {depth:+.2f} m')
            if len(pts) >= 2:
                d = pts[-1] - pts[-2]
                flat = float(np.hypot(d[0], d[2]))
                imgui.text(f'last segment: {np.linalg.norm(d):.2f} m')
                imgui.text(f'  horizontal {flat:.2f} m, height {d[1]:+.2f} m, '
                           f'slope {np.degrees(np.arctan2(d[1], max(flat, 1e-9))):+.1f} deg')
                imgui.text(f'path total: {sum(float(np.linalg.norm(b - a)) for a, b in zip(pts, pts[1:])):.2f} m')
        imgui.end()

    def water_above(self, p):
        """Height of the water surface over point p minus p's height, or None where there is no water."""
        w = self.lvl.water
        if w is None or not len(w):
            return None
        t = w.vertices[w.triangles].astype(np.float64)                     # (m, 3, 3)
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
        with np.errstate(divide='ignore', invalid='ignore'):
            l1 = ((b[:, 2] - c[:, 2]) * (p[0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[2] - c[:, 2])) / d
            l2 = ((c[:, 2] - a[:, 2]) * (p[0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[2] - c[:, 2])) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= 0) & (l2 >= 0) & (l3 >= 0) & np.isfinite(l1)
        if not inside.any():
            return None
        k = np.nonzero(inside)[0][0]
        return float(l1[k] * a[k, 1] + l2[k] * b[k, 1] + l3[k] * c[k, 1] - p[1])

    def tooltip(self, v):
        io = imgui.get_io()
        if io.want_capture_mouse or self.measuring or v.hovered == viewer.NO_ID or imgui.is_mouse_down(imgui.MouseButton_.left):
            return
        if v.hovered == TERRAIN_ID:
            text = 'terrain'
        elif WATER_ID <= v.hovered < WATER_ID + len(self.water_shaders):
            text = 'water  ' + (self.water_shaders[v.hovered - WATER_ID] or '(shader unknown)')
        elif v.hovered < len(self.lvl.entities):
            h = self.lvl.entities[v.hovered]
            text = f'{h.short_type}  {h.name or h.id or h.blueprint or ""}'
            if h.team is not None:
                text += f'\n{TEAM_NAME[h.team]}'
        else:
            return
        if v.hover_point is not None:
            p = v.hover_point
            text += f'\n{p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}'
        imgui.set_tooltip(text)

    def panel_status(self, v):
        self.tooltip(v)
        io = imgui.get_io()
        imgui.set_next_window_pos((10, io.display_size.y - 40), imgui.Cond_.always)
        imgui.set_next_window_size((io.display_size.x - 20, 30), imgui.Cond_.always)
        imgui.begin('##status', flags=imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_inputs | imgui.WindowFlags_.no_background)
        imgui.text(f'{v.fps:5.1f} fps   {self.scene_triangles:,} triangles   {v.renderer}   camera: {"fly" if v.camera.fly else "orbit"} (F)   '
                   f'click: {"measure" if self.measuring else "select"} (M)   '
                   f'terrain: {"height field" if self.lvl.heightfield is not None else "samples"}')
        imgui.end()


def main(argv):
    opts, args, k = {}, [], 1
    while k < len(argv):
        if argv[k].startswith('--') and k + 1 < len(argv):
            opts[argv[k]] = argv[k + 1]
            k += 2
        else:
            args.append(argv[k])
            k += 1
    if not args:                                            # started without a level: ask for one
        from mohcore import pick
        lv = pick.pick_level('MOH Map Inspector')
        if not lv:
            return 1
        args = [lv]
    lvl = levelmod.load(args[0])
    ins = Inspector(lvl)
    ins.scene_triangles = ins.scene.triangle_count()
    v = viewer.Viewer(ins.scene, f'MOH Map Inspector - {lvl.name}',
                      panels=[ins.panel_layers, ins.panel_selection, ins.panel_measure, ins.panel_status])
    v.on_pick = ins.on_pick(v)
    for name in HIDDEN_AT_START:
        v.set_layer_visible(ins.layer_index[name], False)
    if '--select' in opts:
        e = lvl.by_guid(opts['--select'])
        if e is not None:
            ins.select(v, e, focus=True)
    if '--measure' in opts:
        ins.measure = [np.array([float(c) for c in p.split(',')]) for p in opts['--measure'].split(';')]
        ins.set_measuring(v, True)
        ins.update_overlay(v)
    if '--screenshot' in opts:
        v.exit_after = int(opts.get('--frames', 40))
    v.run()
    if '--screenshot' in opts and v.screenshot is not None:
        import matplotlib.pyplot as plt
        plt.imsave(opts['--screenshot'], np.asarray(v.screenshot))
        print('screenshot:', opts['--screenshot'])
    return 0


if __name__ == '__main__':
    with errors.guard():                         # a crash: logged, explained in a window, exit code 1
        sys.exit(main(sys.argv))
