"""MOH Navigation Studio (viewing, milestone M3): a level's AI navigation data in 3D - NavKit's and DICE's.

  python studio.py <level> [--candidate <pathdata.hpd>] [--reference <pathdata.hpd>] [--color island|sector|pitch|difference]
  python studio.py <level> --screenshot out.png [--frames N] [--color ...] [--island N] [--hide <layer>,...]   (tests)
  python studio.py <level> --compare <source>      a reference level's AI layer on this level (overlay.py; sources
                                                     level:<lv>, xbox:<lv> or a level .xml); works without path data

Defaults: the reference is DICE's path data in Project OPFOR's game copy; the candidate is NavKit's newest
build of the level (the newest of work\\navkit\\<level>\\pathdata.hpd, work\\navkit\\regress\\<level>.hpd and
work\\navbuild\\<level>_W.hpd). Either may be missing.

Each file has its layers: walkable ground (its 0.8 m cells), outlines, portals, border portals and the route graph.
Colour the ground by island (the main network is green), by sector, by pitch (detailed 0.2 m zones in orange), or
by difference: NavKit's ground that DICE also has (grey-green) or does not (orange), and DICE's ground NavKit
lacks (red, its own layer). Panels: the difference figures, the islands (largest first, fly to each), soldier
spawns that are not on the main network, and the selected area. The camera works as in the Map Inspector.
"""
import colorsys
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
if __name__ == '__main__':                      # the log and error reports start before the heavy imports,
    from mohcore import errors                 # so a missing library is reported too
    errors.setup('navigation-studio', gui='--screenshot' not in sys.argv)

import numpy as np  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from mohcore import level as levelmod, navdata, navpath, viewer  # noqa: E402

FILES = ('NavKit', 'DICE')
ID_BASE = {'NavKit': 0x200000, 'DICE': 0x400000}          # + area index
TERRAIN_ID, WATER_ID = 0xFFFFFE, 0xFFFFFD
COLOR_MODES = ['island', 'sector', 'pitch', 'difference', 'anomaly']
REF_BASE = 0x600000                                      # + reference entity index (compare overlay)
DIMMED, WARNING = (0.45, 0.48, 0.45), (0.95, 0.25, 0.85)  # anomaly colours: islands of 25+ nodes, islands under 25
REF_COLOURS = {'AI spawners (enabled)': (0.95, 0.15, 0.15), 'AI spawners (disabled)': (0.95, 0.45, 0.45),
               'human spawners (enabled)': (0.2, 0.45, 1.0), 'human spawners (disabled)': (0.55, 0.7, 1.0),
               'area orders': (1.0, 0.85, 0.2), 'patrol orders': (1.0, 0.6, 0.1), 'objectives': (0.85, 0.85, 0.85),
               'path data outlines': (0.6, 0.95, 0.6)}
ANOMALY_COLOURS = {'spawners on small islands': (1.0, 0.1, 0.1), 'orders without a node': (1.0, 0.7, 0.0),
                   'sliver areas': (1.0, 0.95, 0.3), 'lonely nodes': (1.0, 0.4, 0.9)}
COMPARE_FILTERS = {'AI spawners': ('AI spawners (enabled)', 'AI spawners (disabled)'),
                   'human spawners': ('human spawners (enabled)', 'human spawners (disabled)'),
                   'orders': ('area orders', 'patrol orders'), 'objectives': ('objectives',), 'path data': ('path data outlines',)}
MAIN, SAME_C, EXTRA, MISSING, NORMAL, DETAILED = (0.35, 0.75, 0.35), (0.55, 0.62, 0.55), (1.0, 0.55, 0.15), \
    (0.9, 0.15, 0.15), (0.30, 0.60, 0.80), (1.0, 0.60, 0.15)
LIFT = 0.06                                              # ground cells drawn just above the surface
LADDER = (0.62, 0.35, 1.0)                               # ground reached only by ladder links, and the links
PATH_COLOURS = {'NavKit': (1.0, 0.85, 0.1), 'DICE': (0.3, 0.9, 1.0)}


def hue(k, s=0.65, v=0.92):
    return colorsys.hsv_to_rgb((k * 0.618034) % 1.0, s, v)


def default_files(root, lv):
    import navparity
    ref = navparity.reference_of(root, lv)
    builds = [p for p in (os.path.join(root, 'work', 'navkit', lv, 'pathdata.hpd'), os.path.join(root, 'work', 'navkit', 'regress', f'{lv}.hpd'),
                          os.path.join(root, 'work', 'navbuild', f'{lv}_W.hpd')) if os.path.isfile(p)]
    cand = max(builds, key=os.path.getmtime) if builds else None          # the newest NavKit build
    return cand, (ref if ref and os.path.isfile(ref) else None)


def box_edges(centre, size):
    """The 12 edges of an axis-aligned box (an outline marker)."""
    c, h = np.asarray(centre, dtype=np.float64), size / 2.0
    k = np.array([[x, y, z] for x in (-h, h) for y in (-h, h) for z in (-h, h)]) + c
    pairs = [(a, b) for a in range(8) for b in range(a + 1, 8) if bin(a ^ b).count('1') == 1]
    return np.array([[k[a], k[b]] for a, b in pairs])


def cell_quads(xz, y, heightfield=None, lift=LIFT):
    """Two triangles per 0.8 m cell. Cells on terrain follow it (each corner at the terrain's height), so slopes
    show as a surface rather than as steps; other cells (floors, roofs, bridges) are flat at their height."""
    h = navdata.GRID / 2
    x, z = xz[:, 0], xz[:, 1]
    corners = ((-h, -h), (h, -h), (h, h), (-h, h))
    on_terrain = np.zeros(len(y), dtype=bool)
    if heightfield is not None and len(y):
        on_terrain = np.abs(heightfield.height(x, z) - y) < 0.05
    c = []
    for dx, dz in corners:
        cy = y.copy()
        if on_terrain.any():
            cy[on_terrain] = heightfield.height(x[on_terrain] + dx, z[on_terrain] + dz)
        c.append(np.stack([x + dx, cy + lift, z + dz], axis=1))
    return np.stack([np.stack([c[0], c[1], c[2]], 1), np.stack([c[0], c[2], c[3]], 1)], axis=1).reshape(-1, 3, 3)


class Studio:
    def __init__(self, lvl, files, color='island', compare=None):
        self.lvl, self.files, self.color = lvl, files, color
        self.scene = viewer.Scene()
        L = self.scene.layer
        self.layers = {'terrain': L('terrain', (0.40, 0.44, 0.36)), 'water': L('water', (0.18, 0.42, 0.78)),
                       'objects': L('objects', (0.58, 0.57, 0.55)), 'soldier spawns': L('soldier spawns', (0.2, 0.55, 1.0))}
        if lvl.terrain is not None:
            self.scene.add_indexed(lvl.terrain[0], lvl.terrain[1], TERRAIN_ID, self.layers['terrain'])
        if lvl.water is not None and len(lvl.water):
            self.scene.add_indexed(lvl.water.vertices, lvl.water.triangles, WATER_ID, self.layers['water'])
        for m in lvl.meshes:
            self.scene.add_triangles(m.triangles, 0xFFFFF0, self.layers['objects'])
        self.spawns = [e for e in lvl.entities if e.category == 'soldier spawn' and e.position is not None]
        for k, e in enumerate(self.spawns):
            self.scene.add_box(e.position + np.array([0, 0.6, 0]), 1.2, 0x100000 + k, self.layers['soldier spawns'])
        self.ground_batch, self.same = {}, {}
        for name, nav in files.items():
            base = ID_BASE[name]
            lay = {k: L(f'{name} {k}', col) for k, col in (('ground', (0.5, 0.7, 0.5)), ('outlines', (0.95, 0.95, 0.95)),
                                                            ('portals', (0.3, 0.9, 1.0)), ('border portals', (1.0, 0.9, 0.2)),
                                                            ('route graph', (1.0, 0.4, 0.9)))}
            self.layers.update({f'{name} {k}': v for k, v in lay.items()})
            quads = cell_quads(nav.cell_xz, nav.cell_y, lvl.heightfield)
            self.ground_batch[name] = self.scene.add_triangles(quads, np.repeat(base + nav.cell_area, 2), lay['ground'])
            lines = [(k, line) for k, a in enumerate(nav.areas) for line in a.outline]
            psegs = [(k, p0, p1) for k, a in enumerate(nav.areas) for p0, p1, _ in a.portals]
            bsegs = [(k, p0, p1) for k, a in enumerate(nav.areas) for p0, p1 in a.borders]
            drop = np.array([0, navdata.Y_OFFSET - 0.12, 0])
            if lines:                                    # all outline polylines at once: segments within each line
                pts = np.concatenate([l for _, l in lines])
                owner = np.repeat(np.arange(len(lines)), [len(l) for _, l in lines])
                keep = owner[:-1] == owner[1:]
                area_of_line = np.array([k for k, _ in lines])
                segs = np.stack([pts[:-1][keep], pts[1:][keep]], axis=1)
                self.scene.add_lines(segs - drop, base + area_of_line[owner[:-1][keep]], lay['outlines'])
            pids = [base + k for k, _, _ in psegs]
            bids = [base + k for k, _, _ in bsegs]
            psegs = [[p0, p1] for _, p0, p1 in psegs]
            bsegs = [[p0, p1] for _, p0, p1 in bsegs]
            if psegs:
                self.scene.add_lines(np.array(psegs) - drop, np.array(pids), lay['portals'])
            if bsegs:
                self.scene.add_lines(np.array(bsegs) - drop, np.array(bids), lay['border portals'])
            if len(nav.edges):
                p = nav.node_pos - np.array([0, navdata.Y_OFFSET - 0.2, 0])
                self.scene.add_lines(np.stack([p[nav.edges[:, 0]], p[nav.edges[:, 1]]], axis=1), 0xFFFFF1, lay['route graph'])
            # ladder ground (reached only through ladder links) and the ladder links themselves, as their own layers
            lad_area = np.array([a.ladder for a in nav.areas], dtype=bool)
            if lad_area.any() and len(nav.cell_area):
                sel = lad_area[nav.cell_area]
                lay['ladder ground'] = self.layers[f'{name} ladder ground'] = L(f'{name} ladder ground', LADDER)
                self.scene.add_triangles(cell_quads(nav.cell_xz[sel], nav.cell_y[sel], lvl.heightfield, LIFT + 0.05),
                                         np.repeat(base + nav.cell_area[sel], 2), lay['ladder ground'])
            if nav.ladder_edge is not None and nav.ladder_edge.any():
                lay['ladder links'] = self.layers[f'{name} ladder links'] = L(f'{name} ladder links', LADDER)
                e = nav.edges[nav.ladder_edge]
                p = nav.node_pos - np.array([0, navdata.Y_OFFSET - 0.3, 0])
                self.scene.add_lines(np.stack([p[e[:, 0]], p[e[:, 1]]], axis=1), 0xFFFFF1, lay['ladder links'])
        self.diff = None
        if len(files) == 2:
            same_c, same_r = navdata.compare_cells(files['NavKit'], files['DICE'])
            self.same = {'NavKit': same_c, 'DICE': same_r}
            ref = files['DICE']
            miss = ~same_r
            self.layers['DICE ground missing in NavKit'] = L('DICE ground missing in NavKit', MISSING)
            if miss.any():
                self.scene.add_triangles(cell_quads(ref.cell_xz[miss], ref.cell_y[miss], lvl.heightfield, LIFT + 0.03),
                                         np.repeat(ID_BASE['DICE'] + ref.cell_area[miss], 2), self.layers['DICE ground missing in NavKit'])
            self.diff = {'reproduced': 100.0 * same_r.mean() if len(same_r) else 0.0,
                         'extra': 100.0 * (~same_c & ~self._ladder_cells(files['NavKit'])).sum() / max(len(same_r), 1),
                         'ladder': 100.0 * self._ladder_cells(files['NavKit']).sum() / max(len(same_r), 1),
                         'dice cells': int(len(same_r)), 'navkit cells': int(len(same_c))}
        self.selected = None
        self.editor = None                                  # editing.Editor, set by main()
        self.island_file = 'NavKit' if 'NavKit' in files else 'DICE'
        self.spawn_report = {name: self.spawn_islands(nav) for name, nav in files.items()}
        self.ref_segs = []
        self.compare, self.compare_index = compare, None
        self.ref_items = []                                 # (kind, entry) per reference marker, id REF_BASE + index
        if compare is not None:
            self.add_compare_layers(compare)
        self.anomaly = None
        if 'NavKit' in files:
            self.add_anomaly_layers()
        self.apply_colors()

    # --- piece 1: a reference level's AI layer -----------------------------------------------------------
    def add_compare_layers(self, compare):
        import overlay
        L = self.scene.layer
        lay = {k: L(f'reference {k}', c) for k, c in REF_COLOURS.items()}
        self.layers.update({f'reference {k}': v for k, v in lay.items()})
        rep = compare['report']
        for s in rep['spawners']:
            ai = s['player_type'] == 'SSAiPlayer'
            key = f'{"AI" if ai else "human"} spawners ({"enabled" if s["enabled"] else "disabled"})'
            oid = REF_BASE + len(self.ref_items)
            self.ref_items.append(('spawner', s))
            p = np.array(s['pos']) + np.array([0, 0.9, 0])
            if s['enabled']:
                self.scene.add_box(p, 1.4, oid, lay[key])
            else:
                self.scene.add_lines(box_edges(p, 1.4), oid, lay[key])
        for o in rep['area_orders']:
            oid = REF_BASE + len(self.ref_items)
            self.ref_items.append(('area order', o))
            p = np.array(o['pos']) + np.array([0, 1.2, 0])
            self.scene.add_box(p, 1.0, oid, lay['area orders'])
            self.scene.add_lines(np.array([[p - [0, 1.2, 0], p]]), oid, lay['area orders'])
        for pt in compare['layer']['patrols']:
            q = np.array(pt['points']) + np.array([0, 0.5, 0])
            if len(q) > 1:
                oid = REF_BASE + len(self.ref_items)
                self.ref_items.append(('patrol', {'name': pt['name'], 'pos': list(q[0] - [0, 0.5, 0]), 'points': pt['points']}))
                self.scene.add_lines(np.stack([q[:-1], q[1:]], axis=1), oid, lay['patrol orders'])
        for o in rep['objectives']:
            oid = REF_BASE + len(self.ref_items)
            self.ref_items.append(('objective', o))
            self.scene.add_box(np.array(o['pos']) + np.array([0, 0.6, 0]), 1.2, oid, lay['objectives'])
        nav = compare.get('pathdata')
        if nav is not None:
            lines = [line for a in nav.areas for line in a.outline]
            if lines:
                pts = np.concatenate(lines)
                owner = np.repeat(np.arange(len(lines)), [len(l) for l in lines])
                keep = owner[:-1] == owner[1:]
                segs = np.stack([pts[:-1][keep], pts[1:][keep]], axis=1) - np.array([0, navdata.Y_OFFSET - 0.15, 0])
                self.scene.add_lines(segs, 0xFFFFF2, lay['path data outlines'])
        self.compare_index = overlay.NavIndex(self.files['NavKit']) if 'NavKit' in self.files else None

    def select_reference(self, k):
        """A reference marker selected: the line to the target's nearest NavKit route node on its floor."""
        self.selected = ('ref', k)
        self.ref_segs = []
        kind, e = self.ref_items[k]
        self.ref_near = None
        if self.compare_index is not None:
            n = self.compare_index.nearest_node(e['pos'], r=1e9)
            if n:
                node = self.files['NavKit'].node_pos[n[1]] - np.array([0, navdata.Y_OFFSET, 0])
                self.ref_near = n
                col = (0.3, 1.0, 0.3) if n[0] <= 15.0 else MISSING
                self.ref_segs = [(np.array(e['pos']) + [0, 0.3, 0], node + [0, 0.3, 0], col)]

    def panel_compare(self, v):
        if self.compare is None:
            return
        rep = self.compare['report']
        w = imgui.get_io().display_size.x
        imgui.set_next_window_pos((w - 430, 680), imgui.Cond_.first_use_ever)
        imgui.begin('Compare with reference', flags=imgui.WindowFlags_.always_auto_resize)
        imgui.text(f'reference: {rep["reference"]}')
        imgui.text_disabled(os.path.basename(rep['reference_xml'] or ''))
        imgui.text(f'HasAI {rep["reference_has_ai"]}; SectorOffsets {rep["reference_sector_offsets"] or "none"}')
        s = rep['summary']
        imgui.text(f'{s["spawners"]} spawners ({s["ai_spawners_enabled"]} AI enabled), {s["area_orders"]} area orders, '
                   f'{s["patrols"]} patrols, {s["objectives"]} objectives')
        imgui.text(f'target NavKit build: {os.path.basename(os.path.dirname(rep["target_navkit_build"])) if rep["target_navkit_build"] else "none"}')
        imgui.separator()
        for label, names in COMPARE_FILTERS.items():
            ids = [self.layers[f'reference {n}'] for n in names]
            changed, on = imgui.checkbox(f'{label}##cmp', all(v.layer_visible(i) for i in ids))
            if changed:
                for i in ids:
                    v.set_layer_visible(i, on)
        dis = [self.layers['reference AI spawners (disabled)'], self.layers['reference human spawners (disabled)']]
        changed, only = imgui.checkbox('enabled spawners only##cmp', not any(v.layer_visible(i) for i in dis))
        if changed:
            for i in dis:
                v.set_layer_visible(i, not only)
        if imgui.button('export JSON##cmp'):
            self.compare['saved'] = save_json(self.lvl.opfor, f'{self.lvl.lv}_vs_{self.compare["slug"]}.json', rep)
        if self.compare.get('saved'):
            imgui.text_disabled(self.compare['saved'])
        imgui.end()

    # --- piece 2: anomalies of NavKit's build -----------------------------------------------------------
    def add_anomaly_layers(self):
        import overlay
        try:
            _l, xml, _h, _o = overlay.reference_source(f'level:{self.lvl.lv}', self.lvl.opfor)
            layer = overlay.read_ai_layer(xml) if xml else {'spawners': [], 'area_orders': [], 'patrols': []}
        except (OSError, ValueError):
            layer = {'spawners': [], 'area_orders': [], 'patrols': []}
        nav = self.files['NavKit']
        self.anomaly = overlay.anomalies(nav, layer)
        L = self.scene.layer
        lay = {k: L(f'anomaly: {k}', c) for k, c in ANOMALY_COLOURS.items()}
        self.layers.update({f'anomaly: {k}': v for k, v in lay.items()})
        for a in self.anomaly['spawners_on_small_islands']:
            self.scene.add_lines(box_edges(np.array(a['pos']) + [0, 0.9, 0], 3.0), 0xFFFFF3, lay['spawners on small islands'])
        for a in self.anomaly['orders_without_node']:
            self.scene.add_lines(box_edges(np.array(a['pos']) + [0, 1.2, 0], 3.0), 0xFFFFF3, lay['orders without a node'])
        sl = np.isin(nav.cell_area, [a['area'] for a in self.anomaly['sliver_areas']])
        if sl.any():
            self.scene.add_triangles(cell_quads(nav.cell_xz[sl], nav.cell_y[sl], self.lvl.heightfield, LIFT + 0.05),
                                     np.repeat(ID_BASE['NavKit'] + nav.cell_area[sl], 2), lay['sliver areas'])
        for a in self.anomaly['lonely_nodes']:
            self.scene.add_box(np.array(a['pos']) - [0, navdata.Y_OFFSET - 0.4, 0], 0.7, 0xFFFFF3, lay['lonely nodes'])

    def panel_anomaly(self, v):
        if self.anomaly is None:
            return
        imgui.set_next_window_pos((380, 300), imgui.Cond_.first_use_ever)
        imgui.begin('Anomaly overlay', flags=imgui.WindowFlags_.always_auto_resize)
        if imgui.radio_button('colour islands by size (anomaly)', self.color == 'anomaly'):
            self.color = 'anomaly'
            self.apply_colors(v)
        imgui.text_disabled('ground: main network green, islands of 25+ nodes grey, under 25 magenta')
        imgui.text(f'small islands (under 25 nodes): {len(self.anomaly["small_islands"])}')
        for label, key in (('spawners on small islands', 'spawners_on_small_islands'), ('orders without a node', 'orders_without_node'),
                           ('sliver areas', 'sliver_areas'), ('lonely nodes', 'lonely_nodes')):
            i = self.layers[f'anomaly: {label}']
            imgui.color_button(f'##{label}', imgui.ImVec4(*ANOMALY_COLOURS[label], 1.0), size=(14, 14))
            imgui.same_line()
            changed, on = imgui.checkbox(f'{label}: {len(self.anomaly[key])}', v.layer_visible(i))
            if changed:
                v.set_layer_visible(i, on)
        if imgui.button('export JSON##anom'):
            self.anomaly_saved = save_json(self.lvl.opfor, f'{self.lvl.lv}_anomalies.json',
                                           {'target': self.lvl.lv, 'navkit_build': self.files['NavKit'].path,
                                            'counts': {k: len(x) for k, x in self.anomaly.items()}, **self.anomaly})
        if getattr(self, 'anomaly_saved', None):
            imgui.text_disabled(self.anomaly_saved)
        imgui.end()

    # --- colours ------------------------------------------------------------------------------------
    def cell_colors(self, name):
        nav = self.files[name]
        a = nav.cell_area
        if self.color == 'island':
            isl = np.array([x.island for x in nav.areas])[a] if len(a) else np.zeros(0, int)
            table = np.array([MAIN] + [hue(k) for k in range(1, max(len(nav.islands), 1))]).reshape(-1, 3)
            return table[np.clip(isl, 0, len(table) - 1)]
        if self.color == 'sector':
            sec = np.array([x.sector for x in nav.areas])[a]
            uniq, inv = np.unique(sec, return_inverse=True)
            return np.array([hue(k * 7 + 3, 0.5, 0.85) for k in range(len(uniq))])[inv]
        if self.color == 'pitch':
            det = np.array([x.detailed for x in nav.areas])[a]
            return np.where(det[:, None], np.array(DETAILED), np.array(NORMAL))
        if self.color == 'anomaly':
            isl = np.array([x.island for x in nav.areas])[a] if len(a) else np.zeros(0, int)
            size = np.array([i.nodes for i in nav.islands] or [0])
            n = size[np.clip(isl, 0, len(size) - 1)]
            col = np.where((n >= 25)[:, None], np.array(DIMMED), np.array(WARNING))
            col[isl == 0] = MAIN
            return col
        same = self.same.get(name)
        if same is None:
            return np.tile(np.array(SAME_C), (len(a), 1))
        col = np.where(same[:, None], np.array(SAME_C if name == 'NavKit' else (0.55, 0.58, 0.68)), np.array(EXTRA if name == 'NavKit' else MISSING))
        lad = np.array([x.ladder for x in nav.areas], dtype=bool)[a] if len(a) else np.zeros(0, bool)
        col[lad & ~same] = LADDER                       # ladder ground DICE lacks: not 'extra', its own colour
        return col

    @staticmethod
    def _ladder_cells(nav):
        """bool per cell: ground reached only through ladder links (navdata)."""
        if not len(nav.cell_area):
            return np.zeros(0, bool)
        return np.array([a.ladder for a in nav.areas], dtype=bool)[nav.cell_area]

    def apply_colors(self, v=None):
        for name, b in self.ground_batch.items():
            c = np.repeat(self.cell_colors(name), 2, axis=0)
            (v.set_colors if v is not None else self._scene_colors)(b, c)

    def _scene_colors(self, batch, colors):
        p, t, _ = self.scene.batches[batch]
        self.scene.batches[batch] = (p, t, viewer.triangle_colors(colors, len(p) // 3))

    # --- inspection ---------------------------------------------------------------------------------
    def spawn_islands(self, nav):
        """Per soldier spawn: the island of the nearest route node (within 15 m, 4 m in height), or None."""
        out = []
        if not len(nav.node_pos):
            return [(e, None, None) for e in self.spawns]
        g = nav.node_pos - np.array([0, navdata.Y_OFFSET, 0])
        for e in self.spawns:
            p = e.position
            d = np.hypot(g[:, 0] - p[0], g[:, 2] - p[2])
            d = np.where(np.abs(g[:, 1] - p[1]) <= 4.0, d, 1e9)
            k = int(np.argmin(d))
            out.append((e, int(nav.node_island[k]) if d[k] <= 15.0 else None, float(d[k])))
        return out

    def island_gaps(self, name, limit=300):
        """Per island (the first <limit>): horizontal distance from its nodes to the main network's nearest node.
        Computed once per file, on a 10 m grid of the main network's nodes."""
        cache = self.__dict__.setdefault('_gaps', {})
        if name not in cache:
            nav = self.files[name]
            main = nav.node_pos[nav.node_island == 0][:, [0, 2]] if len(nav.islands) else np.zeros((0, 2))
            grid = {}
            for p in main:
                grid.setdefault((int(p[0] // 10), int(p[1] // 10)), []).append(p)
            grid = {k: np.array(v) for k, v in grid.items()}
            out = [0.0]
            for k in range(1, min(limit, len(nav.islands))):
                pts = nav.node_pos[nav.node_island == k][:, [0, 2]]
                best = np.inf
                for r in (1, 3, 10, 40, 1000):                     # widen the search until something is found
                    for p in pts:
                        cx, cz = int(p[0] // 10), int(p[1] // 10)
                        near = [grid[(x, z)] for x in range(cx - r, cx + r + 1) for z in range(cz - r, cz + r + 1) if (x, z) in grid] \
                            if r < 40 else [main]
                        if near:
                            q = np.concatenate(near)
                            best = min(best, float(np.min(np.hypot(q[:, 0] - p[0], q[:, 1] - p[1]))))
                    if np.isfinite(best):
                        break
                out.append(best if np.isfinite(best) else 0.0)
            cache[name] = out
        return cache[name]

    def area_of_id(self, oid):
        for name, base in ID_BASE.items():
            if name in self.files and base <= oid < base + len(self.files[name].areas):
                return name, oid - base
        return None

    # --- path test (P) ------------------------------------------------------------------------------
    def set_path_mode(self, v, on):
        self.path_mode = on
        if on and self.editor is not None:
            self.editor.tool = None                       # one click mode at a time
        self.sync_click_mode(v)

    def sync_click_mode(self, v):
        """Clicks select unless the path test or an edit tool takes them."""
        editing = self.editor is not None and self.editor.active()
        if editing:
            self.path_mode = False
        v.select_on_click = not (getattr(self, 'path_mode', False) or editing)

    def refresh_overlay(self, v):
        """The overlay: the path test's points and routes, and every edit with the drawing in progress."""
        v.set_overlay(getattr(self, 'path_segs', []) + getattr(self, 'ref_segs', []) +
                      (self.editor.segments() if self.editor is not None else []))

    def path_point(self, v, p):
        """A click in path mode: the start, then the end (a third click starts again)."""
        pts = self.__dict__.setdefault('path_pts', [])
        if len(pts) >= 2:
            pts.clear()
        pts.append(np.asarray(p, dtype=np.float64))
        self.routes = {}
        if len(pts) == 2:
            for name, nav in self.files.items():
                self.routes[name] = navpath.route(nav, pts[0], pts[1])
        self.update_path_overlay(v)

    def update_path_overlay(self, v):
        seg = []
        for p in getattr(self, 'path_pts', []):
            for d in ((0.6, 0, 0), (0, 1.5, 0), (0, 0, 0.6)):
                seg.append((p - np.array(d), p + np.array(d), (1.0, 1.0, 1.0)))
        for name, r in getattr(self, 'routes', {}).items():
            nav = self.files[name]
            col = PATH_COLOURS[name]
            g = navpath.ground_nodes(nav) + np.array([0, 0.35 if name == 'NavKit' else 0.25, 0])
            if r.found:
                chain = g[r.nodes]
                seg += [(chain[k], chain[k + 1], col) for k in range(len(chain) - 1)]
                seg.append((self.path_pts[0], chain[0], col))
                seg.append((chain[-1], self.path_pts[1], col))
            elif r.gap:
                a, b = g[r.gap[0]], g[r.gap[1]]
                seg += [(a, b, MISSING), (a, a + np.array([0, 3, 0]), MISSING), (b, b + np.array([0, 3, 0]), MISSING)]
        self.path_segs = seg
        self.refresh_overlay(v)

    def panel_path(self, v):
        if not getattr(self, 'path_mode', False) and not getattr(self, 'routes', None):
            return
        imgui.set_next_window_pos((380, 10), imgui.Cond_.first_use_ever)
        imgui.begin('Path test', flags=imgui.WindowFlags_.always_auto_resize)
        changed, on = imgui.checkbox('path test (P): click the start, then the end', getattr(self, 'path_mode', False))
        if changed:
            self.set_path_mode(v, on)
        if imgui.small_button('clear##path'):
            self.path_pts, self.routes, self.path_segs = [], {}, []
            self.refresh_overlay(v)
        pts = getattr(self, 'path_pts', [])
        for label, p in zip(('start', 'end'), pts):
            imgui.text(f'{label}: {p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}')
        for name, r in getattr(self, 'routes', {}).items():
            imgui.separator()
            imgui.text_colored(imgui.ImVec4(*PATH_COLOURS[name], 1.0), name)
            if r.found:
                imgui.text(f'  route: cost {r.cost:.1f} m over {len(r.nodes)} nodes (length {r.length:.1f} m, '
                           f'straight {r.straight:.1f} m, x{r.cost / max(r.straight, 1e-9):.2f})')
                imgui.text(f'  joined at nodes {r.start_join:.1f} m and {r.end_join:.1f} m from the points; island {r.start_island}')
            else:
                imgui.text(f'  no route: {r.reason}')
                if r.gap and imgui.small_button(f'fly to the gap##{name}'):
                    v.camera.focus((self.files[name].node_pos[r.gap[0]] + self.files[name].node_pos[r.gap[1]]) / 2, 40.0)
        routes = getattr(self, 'routes', {})
        if len(routes) == 2 and all(r.found for r in routes.values()):
            a, b = routes['NavKit'].cost, routes['DICE'].cost
            imgui.separator()
            imgui.text(f'NavKit\'s route costs {100.0 * (a - b) / max(b, 1e-9):+.1f} % against DICE\'s')
        imgui.text_disabled('Over the route graph only; the game also smooths the route through the areas.')
        imgui.end()

    def on_pick(self, v):
        def handler(oid):
            if self.editor is not None and self.editor.active():
                self.editor.click(v, v.pick_point)
                return
            if getattr(self, 'path_mode', False):
                if v.pick_point is not None:
                    self.path_point(v, v.pick_point)
                return
            hit = self.area_of_id(oid) if oid is not None else None
            self.selected = hit
            if hit is None and oid is not None and 0x100000 <= oid < 0x100000 + len(self.spawns):
                self.selected = ('spawn', oid - 0x100000)
            if hit is None and oid is not None and REF_BASE <= oid < REF_BASE + len(self.ref_items):
                self.select_reference(oid - REF_BASE)
                self.refresh_overlay(v)
            elif self.ref_segs:
                self.ref_segs = []
                self.refresh_overlay(v)
        return handler

    # --- panels -------------------------------------------------------------------------------------
    def panel_main(self, v):
        imgui.set_next_window_pos((10, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((360, 700), imgui.Cond_.first_use_ever)
        imgui.begin('Navigation')
        imgui.text(f'{self.lvl.name}')
        for name, nav in self.files.items():
            s = navdata.summary(nav)
            imgui.text_disabled(f'{name}: {os.path.basename(os.path.dirname(os.path.dirname(nav.path))) if name == "DICE" else os.path.basename(nav.path)}')
            imgui.text(f'  {s["areas"]:,} areas ({s["detailed areas"]:,} detailed), {s["cells"]:,} cells')
            imgui.text(f'  {s["nodes"]:,} nodes, {s["edges"]:,} edges, {s["islands"]} islands ({s["large islands (25+ nodes)"]} large)')
            if s['ladder links']:
                imgui.text(f'  {s["ladder links"]} ladder links, {s["ladder ground cells"]:,} cells of ladder ground')
        if self.diff:
            imgui.separator()
            imgui.text(f'DICE ground reproduced: {self.diff["reproduced"]:.1f} %')
            imgui.text(f'NavKit extra ground: about +{self.diff["extra"]:.1f} % of DICE\'s')
            if self.diff.get('ladder'):
                imgui.text(f'NavKit ladder ground: {self.diff["ladder"]:.2f} % (reached by ladder links)')
            imgui.text_disabled('(cell estimate; navparity.py gives the exact figures)')
        imgui.separator()
        imgui.text('colour the ground by')
        for m in COLOR_MODES:
            if (m == 'difference' and not self.diff) or (m == 'anomaly' and self.anomaly is None):
                continue
            imgui.same_line()
            if imgui.radio_button(m, self.color == m):
                self.color = m
                self.apply_colors(v)
        imgui.separator()
        for name, i in self.layers.items():
            changed, on = imgui.checkbox(name, v.layer_visible(i))
            if changed:
                v.set_layer_visible(i, on)
        imgui.end()

    def panel_islands(self, v):
        if not self.files:
            return
        w = imgui.get_io().display_size.x
        imgui.set_next_window_pos((w - 430, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((420, 420), imgui.Cond_.first_use_ever)
        imgui.begin('Islands and spawns')
        for name in self.files:
            imgui.same_line()
            if imgui.radio_button(f'{name}##isl', self.island_file == name):
                self.island_file = name
        nav = self.files[self.island_file]
        bad = [(e, isl, d) for e, isl, d in self.spawn_report[self.island_file] if isl != 0]
        imgui.text(f'{len(self.spawns)} soldier spawns; {len(bad)} not on the main network')
        if bad and imgui.tree_node(f'spawns off the main network##{self.island_file}'):
            for k, (e, isl, d) in enumerate(bad[:100]):
                label = f'island {isl} (route node {d:.0f} m away)' if isl is not None else 'no route node within 15 m'
                if imgui.small_button(f'fly##sp{k}'):
                    v.camera.focus(e.position, 30.0)
                imgui.same_line()
                imgui.text(f'{e.name or e.short_type}: {label}')
            imgui.tree_pop()
        imgui.separator()
        gaps = self.island_gaps(self.island_file)
        imgui.text(f'{len(nav.islands)} islands (largest first; island 0 is the main network)')
        imgui.begin_child('islands', (0, 0))
        for k, isl in enumerate(nav.islands[:300]):
            gap = f', {gaps[k]:.0f} m from the main network' if k and k < len(gaps) else ''
            if imgui.small_button(f'fly##is{k}'):
                v.camera.focus(isl.centre - np.array([0, navdata.Y_OFFSET, 0]), 40.0 if k else 300.0)
            imgui.same_line()
            imgui.text(f'{k}: {isl.nodes:,} nodes, {isl.cells:,} cells{gap}')
        imgui.end_child()
        imgui.end()

    def panel_selection(self, v):
        if self.selected is None:
            return
        io = imgui.get_io()
        imgui.set_next_window_pos((io.display_size.x - 430, 440), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((420, 230), imgui.Cond_.first_use_ever)
        imgui.begin('Selection')
        if self.selected[0] == 'ref':
            kind, e = self.ref_items[self.selected[1]]
            imgui.text(f'reference {kind}: {e.get("name") or e.get("type") or ""}')
            p = e['pos']
            imgui.text(f'at {p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}')
            if kind == 'spawner':
                imgui.text(f'{e["player_type"]}, {"enabled" if e["enabled"] else "disabled"}, {e["team"]}, {e["squad_id"]}'
                           f'{", in a squad entity" if e.get("in_squad_entity") else ""}')
            if kind == 'area order':
                imgui.text(f'AreaType {e.get("area_type")}')
            n = getattr(self, 'ref_near', None)
            if self.compare_index is None:
                imgui.text_disabled('no NavKit build of the target level')
            elif n is None:
                imgui.text('no NavKit route node on its floor')
            else:
                imgui.text(f'nearest NavKit route node on its floor: {n[0]:.1f} m ({"within" if n[0] <= 15 else "beyond"} R = 15 m)')
                imgui.text(f'  in a network of {n[2]:,} nodes{" (the main one)" if n[3] else ""}')
            imgui.end()
            return
        if self.selected[0] == 'spawn':
            e = self.spawns[self.selected[1]]
            imgui.text(f'{e.short_type} {e.name or ""}')
            imgui.text_disabled(e.guid)
            for name in self.files:
                _, isl, d = self.spawn_report[name][self.selected[1]]
                imgui.text(f'{name}: ' + (f'island {isl}' + (' (main network)' if isl == 0 else '') + f', route node {d:.1f} m away'
                                          if isl is not None else 'no route node within 15 m'))
        else:
            name, k = self.selected
            nav = self.files[name]
            a = nav.areas[k]
            isl = nav.islands[a.island] if 0 <= a.island < len(nav.islands) else None
            imgui.text(f'{name} area {k}: sector 0x{a.sector:04X}, area {a.index}, tile {a.tile[0]},{a.tile[1]}, layer {a.layer}')
            imgui.text(f'pitch {a.pitch:.2f} m{" (detailed zone)" if a.detailed else ""}, {a.cells:,} cells, height {a.min_y:.1f} - {a.max_y:.1f} m')
            if a.ladder:
                imgui.text_colored(imgui.ImVec4(*LADDER, 1.0), 'ladder ground: reached only through a ladder link')
            imgui.text(f'{len(a.outline)} outline lines, {len(a.portals)} portals, {len(a.borders)} border portals')
            if isl is not None:
                imgui.text(f'island {a.island}{" (main network)" if a.island == 0 else ""}: {isl.nodes:,} nodes, {isl.cells:,} cells')
        imgui.end()

    def panel_status(self, v):
        io = imgui.get_io()
        if not io.want_capture_keyboard and imgui.is_key_pressed(imgui.Key.p):
            self.set_path_mode(v, not getattr(self, 'path_mode', False))
        if not io.want_capture_mouse and v.hovered not in (viewer.NO_ID,):
            hit = self.area_of_id(v.hovered)
            if hit:
                a = self.files[hit[0]].areas[hit[1]]
                imgui.set_tooltip(f'{hit[0]} area {hit[1]} - sector 0x{a.sector:04X}, island {a.island}, pitch {a.pitch:.1f}')
        imgui.set_next_window_pos((10, io.display_size.y - 40), imgui.Cond_.always)
        imgui.set_next_window_size((io.display_size.x - 20, 30), imgui.Cond_.always)
        imgui.begin('##status', flags=imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_inputs | imgui.WindowFlags_.no_background)
        imgui.text(f'{v.fps:5.1f} fps   {self.triangles:,} triangles   {v.renderer}   colour: {self.color}   '
                   f'camera: {"fly" if v.camera.fly else "orbit"} (F)')
        imgui.end()


def save_json(opfor, name, data):
    """An export into work\\navstudio; returns the path."""
    import json
    d = os.path.join(opfor, 'work', 'navstudio')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, 'w') as fh:
        json.dump(data, fh, indent=1, default=lambda o: o.tolist() if hasattr(o, 'tolist') else str(o))
    return p


FLAGS = {'--edit'}                                          # options without a value


def parse_args(argv):
    """(options, positional arguments): '--name value' pairs, the FLAGS on their own."""
    opts, args, k = {}, [], 1
    while k < len(argv):
        if argv[k] in FLAGS:
            opts[argv[k]] = True
            k += 1
        elif argv[k].startswith('--') and k + 1 < len(argv):
            opts[argv[k]] = argv[k + 1]
            k += 2
        else:
            args.append(argv[k])
            k += 1
    return opts, args


def main(argv):
    opts, args = parse_args(argv)
    if not args:                                            # started without a level: ask for one
        from mohcore import pick
        lv = pick.pick_level('MOH Navigation Studio')
        if not lv:
            return 1
        args = [lv]
    lvl = levelmod.load(args[0])
    import navparity
    cand, ref = default_files(lvl.opfor, lvl.lv)
    cand, ref = opts.get('--candidate', cand), opts.get('--reference', ref)
    cache = os.path.join(lvl.opfor, 'work', 'navstudio', 'cache')
    files = {}
    for name, p in (('NavKit', cand), ('DICE', ref)):
        if p and os.path.isfile(p):
            print(f'{name}: {p}')
            files[name] = navdata.load(p, lvl.heightfield, cache)
    compare = None
    if '--compare' in opts:
        import overlay
        spec = opts['--compare']
        rep = overlay.reference_report(lvl.lv, spec, lvl.opfor, files['NavKit'].path if 'NavKit' in files else None)
        ref_nav = navdata.load(rep['reference_pathdata'], None, cache) if rep['reference_pathdata'] else None
        compare = {'report': rep, 'layer': overlay.read_ai_layer(rep['reference_xml']), 'pathdata': ref_nav,
                   'slug': ''.join(c if c.isalnum() else '_' for c in spec.lower())}
        print(f'reference: {rep["reference"]} - {rep["summary"]}')
    if not files and compare is None:
        print('no path data for this level (neither DICE\'s nor a NavKit build); --compare <source> works without')
        return 1
    st = Studio(lvl, files, opts.get('--color', 'island'), compare)
    st.triangles = st.scene.triangle_count()
    import editing
    if files:                                               # edits need path data; a compare-only view has none
        st.editor = editing.Editor(st, lvl.opfor, lvl.lv, f'Levels/{lvl.name}')
    if st.editor is not None and '--navtune' in opts:       # a side file somewhere else (tests, shared files)
        st.editor.open_side_file(opts['--navtune'])
    if st.editor is not None and st.editor.warning:         # an unreadable side file: said at once, panel open
        from mohcore import errors
        errors.warn(st.editor.warning, show=True)
        st.editor.open = True
    v = viewer.Viewer(st.scene, f'MOH Navigation Studio - {lvl.name}',
                      panels=[st.panel_main, st.panel_islands, st.panel_selection, st.panel_path, st.panel_compare,
                              st.panel_anomaly] + ([st.editor.panel] if st.editor is not None else []) + [st.panel_status])
    v.on_pick = st.on_pick(v)
    hide = set(opts.get('--hide', 'objects,DICE ground,DICE outlines,DICE portals,DICE border portals,DICE route graph,'
                                   'NavKit outlines,NavKit portals,NavKit border portals,NavKit route graph').split(','))
    for name, i in st.layers.items():
        if name in hide:
            v.set_layer_visible(i, False)
    if '--island' in opts:
        nav = files.get('NavKit') or files['DICE']
        n = int(opts['--island'])
        if n < len(nav.islands):
            v.camera.focus(nav.islands[n].centre, 60.0)
    if st.editor is not None and ('--edit' in opts or '--navtune' in opts):
        st.editor.open = True
    st.refresh_overlay(v)
    if '--path' in opts:                                    # tests: a path test between two points
        st.set_path_mode(v, True)
        for p in opts['--path'].split(';'):
            st.path_point(v, [float(c) for c in p.split(',')])
        for name, r in st.routes.items():
            print(f'path {name}: ' + (f'cost {r.cost:.1f} m, {len(r.nodes)} nodes' if r.found else f'none - {r.reason}'))
    if '--focus' in opts:                                   # tests: the camera on a point
        v.camera.focus(np.array([float(c) for c in opts['--focus'].split(',')]), float(opts.get('--distance', 45)))
    if '--screenshot' in opts:
        v.exit_after = int(opts.get('--frames', 40))
    v.run()
    if '--screenshot' in opts and v.screenshot is not None:
        import matplotlib.pyplot as plt
        plt.imsave(opts['--screenshot'], np.asarray(v.screenshot))
        print('screenshot:', opts['--screenshot'])
    _ = navparity
    return 0


if __name__ == '__main__':
    with errors.guard():                         # a crash: logged, explained in a window, exit code 1
        sys.exit(main(sys.argv))
