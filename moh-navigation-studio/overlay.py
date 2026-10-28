"""Navigation Studio overlays (read-only): a reference level's AI layer drawn on a target level, and the anomalies of
NavKit's path data for a level. No level data is written; path data is read, never rebuilt.

  python overlay.py <target level> --reference <source> [--candidate <pathdata.hpd>] [--out <file.json>]
  python overlay.py <target level> --anomalies [--candidate <pathdata.hpd>] [--out <file.json>]

A reference source is one of
  level:<lv>        a level prepared in Project OPFOR (work\\levels\\<lv>): campaign, Napisal's ONS levels, stock MP;
                    its path data is the level's installed original (DICE's, or the console file Napisal's pack installs)
  xbox:<lv>         DICE's Xbox 360 Onslaught level from the user's DLC packages, as unpacked into work\\xbox_dlc
                    (ons_mp_002 in work\\xbox_dlc\\o2, ons_mp_005 / ons_mp_008 in work\\xbox_dlc\\u3)
  <path to a level .xml>
The AI layer read from a level (the field list of OPFOR's MP_APPLICATION_LOG.md structure table): HasAI, soldier
spawners (PlayerType, Enabled, Team, SoldierSquadId), AreaOrders (with AreaType - they carry no team or squad),
PatrolOrders (their AreaGeometry waypoint chains), objective objects (mpobjective / communicationstation), the path
data's area outlines and the level's SectorOffsets (None when the level has no such asset - never inferred).
"""
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..'))
sys.path[:0] = [os.path.join(SUITE, 'core'), os.path.join(SUITE, 'moh-auto-navkit', 'tools')]

from mohcore import navdata, xmlinst  # noqa: E402

R_GATHER = 15.0          # the runtime's node gather radius (DistEdgeMax)
SAME_FLOOR = 2.5         # a node / point pair is on the same floor within this height (node y - 0.95 m)
SMALL_ISLAND = 25        # islands under this many nodes (navbuild.ORDER_MIN_ISLAND)
SLIVER_CELLS = 5         # areas under this many cells, with no portal or border portal
LONELY_NODE = 12.0       # a route node whose nearest same-floor node is farther than this
OBJECTIVES = ('mpobjective', 'communicationstation')


# --- sources -------------------------------------------------------------------------------------------
def reference_source(spec, opfor):
    """(label, level xml, path data or None, SectorOffsets xml or None) for a source spec."""
    if os.path.isfile(spec):
        d = os.path.dirname(spec)
        lv = os.path.splitext(os.path.basename(spec))[0].lower()
        return spec, spec, None, _offsets_xml(os.path.join(d, lv))
    kind, _, lv = spec.partition(':')
    lv = lv.lower()
    if kind == 'level':
        d = os.path.join(opfor, 'work', 'levels', lv, 'dist', 'levels')
        xml = next((os.path.join(d, f) for f in os.listdir(d) if f.lower() == lv + '.xml'), None) if os.path.isdir(d) else None
        import navparity
        hpd = navparity.reference_of(opfor, lv)
        return f'{lv} (OPFOR level copy)', xml, hpd if os.path.isfile(hpd) else None, _offsets_xml(os.path.join(d, lv))
    if kind == 'xbox':
        base = os.path.join(opfor, 'work', 'xbox_dlc')
        dirs = [os.path.join(base, 'o2', 'levels')] if lv == 'ons_mp_002' else [os.path.join(base, 'u3', 'levels', lv, 'x_level', 'levels')]
        xml = next((os.path.join(d, f) for d in dirs if os.path.isdir(d) for f in os.listdir(d) if f.lower() == lv + '.xml'), None)
        hpd = os.path.join(base, '454108A800000002', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')
        return f'{lv} (DICE Xbox 360 DLC)', xml, hpd if os.path.isfile(hpd) else None, \
            next((p for d in dirs for p in [_offsets_xml(os.path.join(d, lv))] if p), None)
    raise ValueError(f'unknown reference source {spec!r} (level:<lv>, xbox:<lv> or a level .xml)')


def _offsets_xml(level_dir):
    for kind in ('Precomputed', 'precomputed'):
        p = os.path.join(level_dir, kind, 'Infantry', 'SectorOffsets.xml')
        if os.path.isfile(p):
            return p
    return None


def sector_offsets(path):
    if not path:
        return None
    t = open(path, encoding='utf-8', errors='replace').read()
    x, z = re.search(r'name="XOffset">(-?\d+)<', t), re.search(r'name="ZOffset">(-?\d+)<', t)
    return (int(x.group(1)), int(z.group(1))) if x and z else None


# --- the AI layer of a level ----------------------------------------------------------------------------
def _field(body, name):
    m = re.search(rf'<field name="{name}">([^<]*)</field>', body)
    return m.group(1) if m else None


def _pos(body):
    m = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
    if not m:
        return None
    v = [float(a) for a in m.group(1).split('/')]
    return [round(v[12], 3), round(v[13], 3), round(v[14], 3)] if any(abs(c) > 1e-3 for c in v[12:15]) else None


def read_ai_layer(xml_path):
    """The AI layer of a level partition (see the module docstring)."""
    t = open(xml_path, encoding='utf-8', errors='replace').read()
    inst = {g.lower(): (ty, b) for ty, g, b in xmlinst.instances(t)}
    layer = {'has_ai': None, 'spawners': [], 'area_orders': [], 'patrols': [], 'objectives': []}
    for ty, b in inst.values():
        short = ty.split('.')[-1]
        if short == 'LevelData' and layer['has_ai'] is None:
            v = _field(b, 'HasAI')
            layer['has_ai'] = None if v is None else v == 'true'
        elif short == 'SoldierSpawnEntityData':
            p = _pos(b)
            if p:
                layer['spawners'].append({'pos': p, 'name': _field(b, 'Name') or '', 'player_type': _field(b, 'PlayerType'),
                                          'enabled': _field(b, 'Enabled') == 'true', 'team': _field(b, 'Team'),
                                          'squad_id': _field(b, 'SoldierSquadId'),
                                          'in_squad_entity': bool(re.search(r'<array name="Squad">\s*<item ', b))})
        elif short == 'AreaOrderEntityData':
            p = _pos(b)
            if p:
                layer['area_orders'].append({'pos': p, 'name': _field(b, 'Name') or '', 'area_type': _field(b, 'AreaType')})
        elif short == 'PatrolOrderEntityData':
            chain = []
            for ref in re.findall(r'ref="([0-9a-f-]{36})"', b):
                g, seen = ref.lower(), set()
                while g in inst and g not in seen and inst[g][0].endswith('AreaGeometryEntityData'):
                    seen.add(g)
                    q = _pos(inst[g][1])
                    if q:
                        chain.append(q)
                    nxt = xmlinst.field_ref(inst[g][1], 'Next')
                    g = nxt.lower() if nxt and re.fullmatch(r'[0-9a-f-]{36}', nxt) else ''
                if chain:
                    break
            if chain:
                layer['patrols'].append({'name': _field(b, 'Name') or '', 'points': chain})
        elif short == 'ReferenceObjectData':
            ref = (xmlinst.field_ref(b, 'ReferencedObject') or '').lower()
            kind = next((k for k in OBJECTIVES if k in ref), None)
            p = _pos(b)
            if kind and p:
                layer['objectives'].append({'pos': p, 'type': kind})
    return layer


# --- the target's NavKit data --------------------------------------------------------------------------
class NavIndex:
    """Nearest-node and ground lookups on a NavData (10 m buckets)."""

    def __init__(self, nav):
        self.nav = nav
        self.size = Counter(nav.node_island.tolist())
        self.ground_y = nav.node_pos[:, 1] - navdata.Y_OFFSET
        self.nodes = defaultdict(list)
        for k, (x, _y, z) in enumerate(nav.node_pos.tolist()):
            self.nodes[(int(x // 10), int(z // 10))].append(k)
        self.cells = defaultdict(list)
        for k, (x, z) in enumerate(nav.cell_xz.tolist()):
            self.cells[(int(x // 10), int(z // 10))].append(k)

    def _near(self, grid, x, z, r):
        cx, cz, n = int(x // 10), int(z // 10), int(math.ceil(r / 10))
        return [k for i in range(cx - n, cx + n + 1) for j in range(cz - n, cz + n + 1) for k in grid.get((i, j), ())]

    def nearest_node(self, p, r=R_GATHER, exclude=None):
        """(distance, node index, island size, on the main island) of the nearest same-floor node within r, or None.
        Beyond 200 m (any distance) it searches all nodes at once instead of the 10 m buckets."""
        if r > 200.0:
            if not len(self.nav.node_pos):
                return None
            d = np.hypot(self.nav.node_pos[:, 0] - p[0], self.nav.node_pos[:, 2] - p[2])
            d = np.where(np.abs(self.ground_y - p[1]) <= SAME_FLOOR, d, np.inf)
            if exclude is not None:
                d[exclude] = np.inf
            k = int(np.argmin(d))
            if not np.isfinite(d[k]) or d[k] > r:
                return None
            isl = int(self.nav.node_island[k])
            return round(float(d[k]), 2), k, self.size[isl], isl == 0
        best = None
        for k in self._near(self.nodes, p[0], p[2], r):
            if k == exclude or abs(self.ground_y[k] - p[1]) > SAME_FLOOR:
                continue
            d = math.hypot(self.nav.node_pos[k, 0] - p[0], self.nav.node_pos[k, 2] - p[2])
            if d <= r and (best is None or d < best[0]):
                best = (d, k)
        if best is None:
            return None
        isl = int(self.nav.node_island[best[1]])
        return round(best[0], 2), best[1], self.size[isl], isl == 0

    def has_ground(self, p, r=0.6, dy=1.5):
        return any(math.hypot(self.nav.cell_xz[k, 0] - p[0], self.nav.cell_xz[k, 1] - p[2]) <= r and abs(self.nav.cell_y[k] - p[1]) < dy
                   for k in self._near(self.cells, p[0], p[2], r))


def _node_row(idx, p):
    if idx is None:
        return {'navkit_ground': None, 'nearest_node_m': None, 'node_island_size': None, 'node_on_main_island': None}
    n = idx.nearest_node(p)
    return {'navkit_ground': idx.has_ground(p), 'nearest_node_m': n[0] if n else None,
            'node_island_size': n[2] if n else None, 'node_on_main_island': n[3] if n else None}


def reference_report(target, source_spec, opfor, candidate=None):
    """The JSON export of piece 1: the reference's AI layer against the target's NavKit build."""
    label, xml, hpd, off_xml = reference_source(source_spec, opfor)
    if not xml:
        raise FileNotFoundError(f'{source_spec}: no level .xml found')
    layer = read_ai_layer(xml)
    cand = candidate or default_candidate(opfor, target)
    idx = NavIndex(navdata.load(cand)) if cand else None
    rows = {'spawners': [], 'area_orders': [], 'patrol_points': [], 'objectives': []}
    for s in layer['spawners']:
        rows['spawners'].append({k: s[k] for k in ('pos', 'name', 'player_type', 'enabled', 'team', 'squad_id', 'in_squad_entity')} | _node_row(idx, s['pos']))
    for o in layer['area_orders']:
        rows['area_orders'].append(dict(o) | _node_row(idx, o['pos']))
    for pt in layer['patrols']:
        for q in pt['points']:
            rows['patrol_points'].append({'patrol': pt['name'], 'pos': q} | _node_row(idx, q))
    for o in layer['objectives']:
        rows['objectives'].append(dict(o) | _node_row(idx, o['pos']))
    return {'target': target, 'target_navkit_build': cand, 'reference': label, 'reference_xml': xml, 'reference_pathdata': hpd,
            'reference_has_ai': layer['has_ai'], 'reference_sector_offsets': sector_offsets(off_xml),
            'summary': {'spawners': len(rows['spawners']), 'ai_spawners_enabled': sum(1 for s in rows['spawners'] if s['player_type'] == 'SSAiPlayer' and s['enabled']),
                        'area_orders': len(rows['area_orders']), 'patrols': len(layer['patrols']), 'objectives': len(rows['objectives'])},
            **rows}


def default_candidate(opfor, lv):
    p = os.path.join(opfor, 'work', 'navkit', lv, 'pathdata.hpd')
    return p if os.path.isfile(p) else None


# --- piece 2: anomalies -------------------------------------------------------------------------------
def anomalies(nav, layer):
    """Per class, the anomalies of a NavKit build against the level's own AI layer (read_ai_layer of the target)."""
    idx = NavIndex(nav)
    out = {'small_islands': [], 'spawners_on_small_islands': [], 'orders_without_node': [], 'sliver_areas': [], 'lonely_nodes': []}
    for k, isl in enumerate(nav.islands):
        if isl.nodes < SMALL_ISLAND:
            out['small_islands'].append({'island': k, 'pos': [round(float(c), 2) for c in isl.centre], 'nodes': isl.nodes, 'cells': isl.cells})
    for s in layer['spawners']:
        n = idx.nearest_node(s['pos'])
        if n and n[2] < SMALL_ISLAND:
            out['spawners_on_small_islands'].append({'pos': s['pos'], 'name': s['name'], 'player_type': s['player_type'],
                                                     'enabled': s['enabled'], 'island_size': n[2], 'node_m': n[0]})
    points = [('AreaOrder', o['name'], o['pos']) for o in layer['area_orders']] + \
             [('PatrolWaypoint', pt['name'], q) for pt in layer['patrols'] for q in pt['points']]
    for kind, name, p in points:
        if idx.nearest_node(p) is None:
            out['orders_without_node'].append({'kind': kind, 'name': name, 'pos': p})
    for k, a in enumerate(nav.areas):
        if a.cells < SLIVER_CELLS and not a.portals and not a.borders:
            m = nav.cell_area == k
            c = nav.cell_xz[m].mean(axis=0) if m.any() else np.zeros(2)
            out['sliver_areas'].append({'area': k, 'sector': a.sector, 'cells': a.cells,
                                        'pos': [round(float(c[0]), 2), round(float(nav.cell_y[m].mean()) if m.any() else 0.0, 2), round(float(c[1]), 2)]})
    for k, p in enumerate(nav.node_pos.tolist()):
        n = idx.nearest_node([p[0], p[1] - navdata.Y_OFFSET, p[2]], r=LONELY_NODE, exclude=k)
        if n is None:
            out['lonely_nodes'].append({'node': k, 'pos': [round(c, 2) for c in p]})
    return out


def anomaly_report(target, opfor, candidate=None, xml=None):
    cand = candidate or default_candidate(opfor, target)
    if not cand:
        raise FileNotFoundError(f'{target}: no NavKit build (work\\navkit\\{target}\\pathdata.hpd)')
    if xml is None:
        _label, xml, _hpd, _off = reference_source(f'level:{target}', opfor)
    nav = navdata.load(cand)
    out = anomalies(nav, read_ai_layer(xml))
    return {'target': target, 'navkit_build': cand, 'level_xml': xml,
            'counts': {k: len(v) for k, v in out.items()}, **out}


def main(argv):
    opts, args, k = {}, [], 1
    while k < len(argv):
        if argv[k].startswith('--') and k + 1 < len(argv) and not argv[k + 1].startswith('--'):
            opts[argv[k]] = argv[k + 1]
            k += 2
        elif argv[k].startswith('--'):
            opts[argv[k]] = True
            k += 1
        else:
            args.append(argv[k])
            k += 1
    if not args:
        print(__doc__)
        return 1
    import navparity
    opfor = navparity.opfor_root(None)
    target = args[0].lower()
    rep = anomaly_report(target, opfor, opts.get('--candidate')) if '--anomalies' in opts else \
        reference_report(target, opts['--reference'], opfor, opts.get('--candidate'))
    out = opts.get('--out') or os.path.join(opfor, 'work', 'navstudio',
                                            f'{target}_anomalies.json' if '--anomalies' in opts else
                                            f'{target}_vs_{re.sub(r"[^a-z0-9_]+", "_", opts["--reference"].lower())}.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rep, open(out, 'w'), indent=1)
    print(json.dumps(rep.get('summary') or rep.get('counts')), '->', out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
