r"""The map and bot test harness.

Quick (no game data; navigation data, levels, bundles and logs built in the test):
  - reachability over a route graph of two islands and a one-way edge: pair bits, islands, targets reached from no
    spawn; against a reference, the share of its pairs and the targets lost (fail; a warning on the baseline file);
    a point standing on the main ground beside a small roof island joins the main ground (area join), where the
    nearest-node join takes the roof;
  - path batteries: the same random points for the same seed, other points for another; routes found, refused
    between islands and where no node is near; the share of the reference's routes, length ratios;
  - orders near nodes and on areas, against a reference; structure (hpdcheck's result); sector offsets from sector
    ids and against the SectorOffsets asset (match, mismatch, no asset);
  - AI-readiness checks on a campaign-like and a stock-MP-like level: HasAI, AI spawners, the placeholder and null
    templates, templates and the partitions they refer to in the load set, kits, AreaOrders, the asset, path data;
    the in-game checks on hold; package.mft load sets and bundle paths;
  - mohcore.fbrb on bundles built here: entries, deleted markers, reading entries (zipped and not), errors; the
    harness's bundle caches;
  - opfor.log sessions: level, states, AI events, SectorOffsets loads and the override, pathfinder counts, crash; the
    in-game criteria passed, failed and on hold;
  - saved runs: save / load, another format refused; comparison: worse beyond the tolerance, within it, closer to
    1.0, a test now failing and one fixed, counts, missing and new, battery pairs lost; the report;
  - the command line: compare (exit 1 on a regression, 0 without), show, list, import-log, ingame (on hold, starts
    nothing), an error explained with its log.
Game (--game, Project OPFOR's game copy; outputs to a temporary folder): the reader against FbrbTool on a level bundle;
a run on sp_009 and sp_004_b (DICE's files pass the structure and offset checks; the level checks pass; points, pair
data and parity are there); the same run again compares with no regression; a damaged copy of sp_009's path data
(half its sectors without route edges) is flagged as a regression; the user's opfor.log is read (read only).

  python test_harness.py [--game]
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, '..'))
SUITE = os.path.abspath(os.path.join(TOOL, '..'))
for p in (TOOL, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'moh-auto-navkit'), os.path.join(SUITE, 'moh-auto-navkit', 'tools')):
    sys.path.insert(0, p)
from mohcore import fbrb, navdata  # noqa: E402
from testharness import checks, classify, ingame, levelchecks, levelinfo, runs  # noqa: E402
from testharness.checks import Point  # noqa: E402

PY = sys.executable
HARNESS = os.path.join(TOOL, 'harness.py')


# ------------------------------------------------------------------------------------------------ built data

def grid_nav(parts, extra_edges=(), one_way=(), cells_of=None):
    """NavData of lattices: parts = [(x0, z0, nx, nz, y, step)], each a lattice of route nodes (bidirectional edges
    to the right and forward neighbours) with one area of 0.8 m cells under it; extra_edges / one_way: (a, b) node
    index pairs joined both ways / one way. Islands from the edges, as navdata does."""
    pos, edges, areas, cxz, cy, ca = [], [], [], [], [], []
    for k, (x0, z0, nx, nz, y, step) in enumerate(parts):
        base = len(pos)
        for j in range(nz):
            for i in range(nx):
                pos.append((x0 + i * step, y + navdata.Y_OFFSET, z0 + j * step))
                n = base + j * nx + i
                if i + 1 < nx:
                    edges += [(n, n + 1), (n + 1, n)]
                if j + 1 < nz:
                    edges += [(n, n + nx), (n + nx, n)]
        areas.append(navdata.NavArea(0, k, (0, 0), 0, 0.8, y, y))
        xs = np.arange(x0, x0 + (nx - 1) * step + 0.01, navdata.GRID)
        zs = np.arange(z0, z0 + (nz - 1) * step + 0.01, navdata.GRID)
        X, Z = np.meshgrid(xs, zs)
        cxz.append(np.stack([X.ravel(), Z.ravel()], 1))
        cy.append(np.full(X.size, float(y)))
        ca.append(np.full(X.size, k, np.int32))
    for a, b in extra_edges:
        edges += [(a, b), (b, a)]
    edges += list(one_way)
    pos = np.array(pos, float)
    E = np.array(edges, np.int64).reshape(-1, 2)
    cost = np.linalg.norm(pos[E[:, 1]] - pos[E[:, 0]], axis=1)
    parent = list(range(len(pos)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in E:
        parent[find(a)] = find(b)
    roots = [find(k) for k in range(len(pos))]
    from collections import Counter
    order = [r for r, _ in Counter(roots).most_common()]
    rank = {r: k for k, r in enumerate(order)}
    node_island = np.array([rank[r] for r in roots], np.int32)
    islands = [navdata.Island(r, int((node_island == k).sum()), 0, pos[node_island == k].mean(0)) for k, r in enumerate(order)]
    xz, y, ar = np.concatenate(cxz), np.concatenate(cy), np.concatenate(ca)
    for k, a in enumerate(areas):                       # the area's island: its lattice's first node's
        first = sum(p[2] * p[3] for p in parts[:k])
        a.island = int(node_island[first])
        a.cells = int((ar == k).sum())
    return navdata.NavData('built', areas, xz, y, ar, pos, np.zeros(len(pos), int), E, np.floor(cost * 16) / 16,
                           node_island, islands, np.zeros(len(E), bool))


class Sector:
    def __init__(self, sid, bounds):
        self.id, self.bounds = sid, bounds


def level_info(stock=False):
    info = levelinfo.LevelInfo('test_level', 'TEST_LEVEL')
    S = levelinfo.Spawner
    g = 'objects/soldiers/templates/spawntemplate_enemy_grunt'
    if stock:
        info.has_ai = False
        info.spawners = [S('A', 'US base', 1, True, True, None, None), S('B', 'player', 1, False, True, None, None)]
        info.load_set = [('game:levels/test_level/level-00.fbrb', 'own.fbrb', True)]
        info.entries = {'own.fbrb': {'levels/test_level.dbx'}}
        info.assets = {'Infantry': None, 'Vehicle': None}
        return info
    info.has_ai = True
    info.spawners = [S('A', 'grunt 1', 2, True, True, g, 'objects/soldiers/sp/ru_assault'),
                     S('B', 'grunt 2', 2, True, False, g, 'objects/soldiers/sp/ru_assault'),
                     S('C', 'player', 1, False, True, None, 'objects/soldiers/sp/marlowe')]
    info.area_orders = 4
    info.load_set = [('package:levels/test_level/level-00.fbrb', 'pkg.fbrb', False),
                     ('game:levels/test_level/level-00.fbrb', 'own.fbrb', True),
                     ('game:levels/sp_common/level-00.fbrb', 'common.fbrb', True)]
    info.entries = {'own.fbrb': {'levels/test_level.dbx', 'levels/test_level/precomputed/infantry/sectoroffsets.dbx'},
                    'common.fbrb': {g + '.dbx', 'objects/soldiers/templates/ai/enemy_grunt_sense.dbx',
                                    'objects/soldiers/templates/ai/enemy_grunt_bhv.dbx', 'objects/soldiers/sp/ru_assault.dbx'}}
    info.template_refs = {g: ['objects/soldiers/templates/ai/enemy_grunt_bhv', 'objects/soldiers/templates/ai/enemy_grunt_sense']}
    info.assets = {'Infantry': (-9, -10), 'Vehicle': (-9, -10)}
    info.installed = 'pathdata.hpd'
    return info


LOG = """[10:00:00.000] ---- opfor.dll loaded into PID 100 ----
[10:00:00.100] dev trigger: level='Levels/MP_005GR' autostart=0 delay=20000ms
[11:00:00.000] ---- opfor.dll loaded into PID 200 ----
[11:00:01.000] dev trigger: level='Levels/ONS_MP_002' autostart=1 delay=20000ms
[11:00:20.000] queued hostMultiplayerLocal request: level='Levels/ONS_MP_002' (uiManager=1CA89D80)
[11:00:25.000] probe Session::updateState (arg1 = state)                  ecx=180AD880 arg1=00000005 (5) arg2=0 ret=0 tid=1
[11:00:30.000] probe AI: init (arg1 = difficulty)                         ecx=06E170D4 arg1=00000001 (1) arg2=0 ret=00575081 tid=2
[11:00:31.000] probe AI: sector offsets load handle 056D3420 085C33C0 0154C451; object 056D3420  X(+1Ch)=-3 Z(+18h)=-7 tid=2
[11:00:31.000] probe AI: sector offsets load handle 056D3480 085C3390 0154C451; object 056D3480  X(+1Ch)=-3 Z(+18h)=-7 tid=2
[11:00:40.000] probe Session::updateState (arg1 = state)                  ecx=180AD880 arg1=00000003 (3) arg2=0 ret=0 tid=1
[11:01:40.000] unit counters (interval): 0 pathfinders - pf: requests true false
[11:02:40.000] unit counters (interval): 21 pathfinders - pf: requests true false
[11:03:00.000] levelfix: exit(0) called; stack code addresses: 00C51FD5
[12:00:00.000] ---- opfor.dll loaded into PID 300 ----
[12:00:20.000] queued hostMultiplayerLocal request: level='Levels/ONS_MP_002' (uiManager=1CA89D80)
[12:00:21.000] sector offsets: on - X=-3 Z=-7 where a level's SectorOffsets gives 0
[12:00:30.000] probe AI: init (arg1 = difficulty)                         ecx=06E170D4 arg1=00000001 (1) arg2=0 ret=00575081 tid=2
[12:00:31.000] probe AI: sector offsets load handle 056D3420 085C33C0 0154C451; object 056D3420  X(+1Ch)=0 Z(+18h)=0 tid=2
[12:00:50.000] the game is writing crashreport.xml
"""


def result(status, metrics=None, data=None):
    return {'status': status, 'summary': status, 'metrics': metrics or {}, 'details': [], 'seconds': 0, **({'data': data} if data else {})}


def saved_run(name, files_metrics, level_status='pass', bits='1111'):
    r = runs.new_run(name, {}, SUITE)
    r['levels']['sp_test'] = {'name': 'SP_TEST', 'notes': [], 'points': {'sources': 2, 'targets': 2, 'orders': 0},
                              'files': {'navkit': {'path': 'x.hpd', 'sha1': name * 5, 'tests': {
                                  'reachability': result('pass', files_metrics),
                                  'battery': result('info', {'random.found_pct': files_metrics.get('found', 90.0),
                                                             'random.length_ratio_median': files_metrics.get('ratio', 1.05)},
                                                    {'random': {'bits': bits, 'points': 'abcd1234'}}),
                                  'structure': result('pass', {'problems': 0})}}},
                              'level': {'has-ai': result(level_status)}, 'ingame': {'pathfinders': result('hold')}}
    return r


def entity(index, guid, short, category, name, pos, **fields):
    """A level.Entity with a property tree: fields as text, ('ref', r) or ('refs', [r, ...])."""
    from mohcore import dbx, level
    kids = []
    for n, v in fields.items():
        if isinstance(v, tuple) and v[0] == 'refs':
            kids.append(dbx.Element('array', dbx.CONTAINER, [('name', n)], children=[dbx.Element('item', dbx.TEXT, [('ref', r)]) for r in v[1]]))
        elif isinstance(v, tuple):
            kids.append(dbx.Element('field', dbx.TEXT, [('name', n), ('ref', v[1])]))
        else:
            kids.append(dbx.Element('field', dbx.TEXT, [('name', n)], text=v))
    m = np.eye(4)
    m[3, :3] = pos
    return level.Entity(index, guid, 'Entity.' + short, short, category, None, name, 'levels/test', m, None,
                        dbx.Element('instance', dbx.CONTAINER, [('guid', guid)], children=kids))


def classes(check):
    """classify.py: point classes from the entities and from the ground."""
    E = [entity(0, 'S1', 'SoldierSpawnEntityData', 'soldier spawn', 'base spawner', (0, 50, 0), AlternativeSpawnPoints=('refs', ['A1', 'A2'])),
         entity(1, 'A1', 'AlternateSpawnEntityData', 'soldier spawn', 'alt 1', (5, 0, 5)),
         entity(2, 'A2', 'AlternateSpawnEntityData', 'soldier spawn', 'alt 2', (6, 0, 5)),
         entity(3, 'S2', 'SoldierSpawnEntityData', 'soldier spawn', 'pilot', (10, 0, 10), Vehicle=('refs', ['V1']),
                AlternativeSpawnPoints=('refs', ['A3'])),
         entity(4, 'A3', 'AlternateSpawnEntityData', 'soldier spawn', 'pilot alt', (12, 0, 10)),
         entity(5, 'V1', 'VehicleSpawnEntityData', 'vehicle spawn', 'heli', (10, 0, 10), Vehicle=('ref', 'objects/vehicles/air/ah64/ah64/0000')),
         entity(6, 'S3', 'SoldierSpawnEntityData', 'soldier spawn', 'copied', (20, 60, 20), LocationNameSid='ONS_MP_005GR_3'),
         entity(7, 'S4', 'SoldierSpawnEntityData', 'soldier spawn', 'own', (20, 0, 20), LocationNameSid='MP_004_2'),
         entity(8, 'S5', 'SoldierSpawnEntityData', 'soldier spawn', 'copied but on ground', (25, 0, 25), LocationNameSid='MP_008_1')]
    c = classify.spawn_classes(E, 'ons_mp_004')
    got = {E[k].name: v[0] for k, v in c.items()}
    check(got == {'base spawner': 'spawner marker', 'alt 1': 'infantry', 'alt 2': 'infantry', 'pilot': 'vehicle: air',
                  'pilot alt': 'vehicle: air', 'copied': 'other map?', 'own': 'infantry', 'copied but on ground': 'other map?'},
          'spawn classes: a spawner with alternates is a marker, its alternates tested; a vehicle spawn and its alternates '
          'by the vehicle\'s blueprint (air); a spawn named for another map held for the ground test')
    check(classify.world('ONS_MP_005GR_3') == 'mp_005' and classify.world('sp_004_b') == 'sp_004_b' and classify.world('MP_US_DEPLOYMENT') is None
          and classify.vehicle_kind('objects/vehicles/sea/pwc/pwc_test') == 'sea' and classify.vehicle_kind('objects/weapons/stationary/zu23') == 'stationary weapon',
          'map names (ONS_MP_005GR_3 -> mp_005) and vehicle kinds')
    mo = entity(9, 'M', 'MissionObjectiveEntityData', 'objective', '', (0, 0, 0))
    cp = entity(10, 'C', 'CapturePointEntityData', 'capture point', 'US Base', (0, 0, 0))
    check(classify.target_class(mo, False)[0] == 'objective marker' and classify.target_class(cp, True)[0] == 'base'
          and classify.target_class(cp, False)[0] == 'infantry',
          'targets: mission objectives are HUD markers; capture points are bases on a level with M-COMs, targets otherwise')
    from mohcore import water
    nav = grid_nav([(0, 0, 5, 5, 0.0, 10.0)])
    sea = water.Water(np.array([[100, 5, -50], [300, 5, -50], [100, 5, 150], [300, 5, 150]], np.float32), np.array([[0, 1, 2], [1, 3, 2]], np.uint32))
    g = classify.Ground(nav, None, sea)
    pts = [Point('spawn', 'on the ground', (20.0, 0.0, 20.0)), Point('spawn', 'above it', (20.0, 30.0, 20.0)),
           Point('PatrolWaypoint', 'boat patrol', (150.0, 5.5, 10.0)), Point('spawn', 'copied, floating', (20.0, 60.0, 20.0), cls='other map?'),
           Point('spawn', 'copied, on the ground', (25.0, 0.0, 25.0), cls='other map?')]
    classify.by_ground(pts, g)
    check([p.cls for p in pts] == ['infantry', 'airborne', 'water', 'other map', 'infantry'],
          'ground classes: on the ground, airborne (30 m over the path data), on the water, another map\'s spawn off the '
          'ground; one on this map\'s ground is tested: ' + ', '.join(p.cls for p in pts))
    keep, counts = classify.split(pts)
    check(len(keep) == 2 and counts == {'airborne': 1, 'water': 1, 'other map': 1} and '[airborne' in pts[1].label(),
          f'infantry points kept, the others counted ({classify.excluded_text(counts)}) and labelled')


# ------------------------------------------------------------------------------------------------ quick

def quick(check, tmp):
    classes(check)
    # two lattices 200 m apart (islands 0 and 1), and a one-way edge out of a third lattice
    nav = grid_nav([(0, 0, 5, 5, 0.0, 10.0), (200, 0, 3, 3, 0.0, 10.0), (0, 100, 2, 1, 0.0, 10.0)], one_way=[(25 + 9, 0)])
    check(len(nav.islands) == 2 and nav.islands[0].nodes == 27 and nav.islands[1].nodes == 9,
          f'built route graph: islands of {[i.nodes for i in nav.islands]} nodes (one-way edge joins the third lattice)')
    sources = [Point('spawn', 'A', (1.0, 0.0, 1.0), 1), Point('spawn', 'B', (39.0, 0.0, 39.0), 2),
               Point('spawn', 'far', (900.0, 0.0, 900.0), 2)]
    targets = [Point('AreaOrder', 'on main', (20.0, 0.0, 20.0)), Point('M-COM', 'on the other island', (210.0, 0.0, 10.0)),
               Point('AreaOrder', 'one-way end', (0.0, 0.0, 100.0))]
    r = checks.reachability(nav, sources, targets)
    check(r.data['bits'] == '100100---', f'pair bits: reached, other island, one-way edge the wrong way, unjoined ({r.data["bits"]})')
    check(r.metrics['pairs'] == 6 and r.metrics['pairs_reached_pct'] == 33.33 and r.metrics['sources_joined_pct'] == 66.67,
          f'pairs {r.metrics["pairs"]}, reached {r.metrics["pairs_reached_pct"]} %, sources joined {r.metrics["sources_joined_pct"]} %')
    check(r.status == 'warn' and any('reached from no spawn' in d and 'other island' in d for d in r.details)
          and any('far' in d and 'no route node' in d for d in r.details), 'targets reached from no spawn and unjoined points listed (warn)')
    r2 = checks.reachability(nav, sources[:2], targets[:1])
    check(r2.status == 'pass' and r2.metrics['team1_pairs_reached_pct'] == 100.0, 'every target reached: pass; per-team shares')
    # a candidate whose main lattice is cut in two (edges across x = 20..30 removed)
    cut = grid_nav([(0, 0, 3, 5, 0.0, 10.0), (30, 0, 2, 5, 0.0, 10.0), (200, 0, 3, 3, 0.0, 10.0)])
    rc = checks.reachability(cut, sources, targets, reference=r.data)
    check(rc.status == 'fail' and rc.metrics['reach_parity_pct'] == 50.0 and rc.metrics['targets_lost'] == 0,
          f'against the reference: {rc.metrics["reach_parity_pct"]} % of its pairs - fail')
    rb = checks.reachability(cut, sources, targets, reference=r.data, baseline=True)
    check(rb.status == 'warn', 'the same on the baseline (DICE) file: a warning, not a failure')
    rs = checks.reachability(nav, sources, targets, reference={'bits': '000000---'})
    check('reach_parity_pct' not in rs.metrics, 'a reference that connects no pair gives no parity measure')
    # area join: a point on the main ground, beside a small roof island 2 m up whose node is nearer
    roofed = grid_nav([(0, 0, 5, 5, 0.0, 10.0), (20.2, 19.4, 2, 1, 2.0, 1.0)])
    p = (20.4, 0.0, 19.6)
    from mohcore import navpath
    kn, _ = navpath.join(roofed, p)
    ka, _ = checks.join(roofed, p)
    check(roofed.node_island[kn] == 1 and roofed.node_island[ka] == 0,
          'a point on the main ground joins the main ground (area join); the nearest-node join takes the roof island')

    # batteries
    a = checks.random_points(nav, 20, 1, 'sp_test')
    check(a == checks.random_points(nav, 20, 1, 'sp_test') and a != checks.random_points(nav, 20, 2, 'sp_test')
          and a != checks.random_points(nav, 20, 1, 'sp_other'), 'random points: the same for the same seed and level, others otherwise')
    pairs = [((0.0, 0.0, 0.0), (40.0, 0.0, 40.0)), ((0.0, 0.0, 0.0), (220.0, 0.0, 20.0)), ((0.0, 0.0, 0.0), (500.0, 0.0, 0.0)),
             ((10.0, 0.0, 0.0), (10.0, 0.0, 30.0))]
    routes = checks.route_pairs(nav, pairs)
    check([x[0] for x in routes] == [True, False, False, True] and routes[1][3] == 'islands' and routes[2][3] == 'no node near'
          and abs(routes[0][1] - 80.0) < 1e-6, f'routes: found (80 m), between islands, no node near ({[x[3] or round(x[1]) for x in routes]})')
    longer = [(True, 100.0, 100.0, ''), (False, 0, 0, 'islands'), (False, 0, 0, 'no node near'), (False, 0, 0, 'islands')]
    b = checks.battery('random', pairs, longer, ref_routes=routes)
    check(b.metrics['found_of_ref_pct'] == 50.0 and b.metrics['length_ratio_median'] == 1.25 and b.status == 'fail'
          and len(b.details) == 1, f'battery against the reference: {b.metrics["found_of_ref_pct"]} % of its routes, length x{b.metrics["length_ratio_median"]} - fail')
    b0 = checks.battery('random', pairs, routes)
    check(b0.status == 'info' and b0.metrics['found_pct'] == 50.0 and b0.data['bits'] == '1001', 'the reference battery itself: a measurement (info)')
    m = checks.merge([b0, b], ['random', 'level'])
    check(m.status == 'fail' and 'random.found_pct' in m.metrics and 'level.found_of_ref_pct' in m.metrics, 'batteries merged: prefixed metrics, worst status')
    lp = checks.level_pairs(sources, targets, 4, 1, 'sp_test')
    check(len(lp) == 4 and lp == checks.level_pairs(sources, targets, 4, 1, 'sp_test'), 'spawn -> target pairs drawn with a fixed seed')

    # orders, structure, offsets
    ords = [Point('AreaOrder', 'a', (10.0, 0.0, 10.0)), Point('PatrolWaypoint', 'b', (40.0, 0.0, 0.0)), Point('AreaOrder', 'c', (600.0, 0.0, 0.0))]
    o = checks.orders(nav, ords, lambda p: p[0] < 20)
    check(o.status == 'fail' and o.metrics['near_node_pct'] == 66.67 and o.metrics['on_area_pct'] == 33.33,
          'orders: one with no route node within 15 m fails; on-area share')
    o2 = checks.orders(nav, ords, lambda p: True, reference={'near': '110'})
    check(o2.status == 'warn' and o2.metrics['orders_lost'] == 0, 'against a reference that has no node there either: a warning')
    o3 = checks.orders(nav, ords, lambda p: True, reference={'near': '111'})
    check(o3.status == 'fail' and o3.metrics['orders_lost'] == 1, 'an order near a node in the reference and not here: fail')
    check(checks.orders(nav, ords, lambda p: True, baseline=True).status == 'warn', 'on the baseline file: a warning')
    s = checks.structure({'closed boundaries': [], 'edges': ['sector 0101: edge 3 -> 9 of 4 nodes'], 'node order': [], 'zero-length edges': []})
    check(s.status == 'fail' and s.metrics['problems'] == 1, 'structure: a problem fails')
    check(checks.structure({'closed boundaries': [], 'edges': []}).status == 'pass', 'structure: none passes')
    secs = [Sector((i << 8) | j, (100 * (i - 9), 100 * (i - 8), 0, 0, 100 * (j - 10), 100 * (j - 9)))
            for i in range(3) for j in range(3)]
    imp = checks.sector_offsets(secs)
    check(imp[0] == (-9, -10) and imp[1] == (1.0, 1.0), f'sector ids imply offsets {imp[0]} (every sector agrees)')
    check(checks.offsets(imp, (-9, -10)).status == 'pass' and checks.offsets(imp, (0, 0)).status == 'fail'
          and checks.offsets(imp, None).status == 'fail' and checks.offsets(imp, 'unread').status == 'warn',
          'offsets against the asset: equal passes, different or missing fails, unread warns')

    # level checks
    camp = levelchecks.all_checks(level_info(), levelinfo.PLACEHOLDER)
    file_checks = [k for k, r in camp.items() if r.status != 'hold']
    check(all(camp[k].status in ('pass',) for k in file_checks) and [k for k, r in camp.items() if r.status == 'hold'] == ['loads-ai-init', 'offsets-read', 'pathfinders'],
          'a campaign-like level passes every file check; the in-game checks are on hold: ' + ', '.join(f'{k} {camp[k].status}' for k in file_checks))
    stock = levelchecks.all_checks(level_info(stock=True), levelinfo.PLACEHOLDER)
    want = {'has-ai': 'fail', 'ai-spawners': 'pass', 'ai-templates': 'fail', 'templates-loaded': 'skip', 'orders': 'fail',
            'offsets-asset': 'fail', 'path-data': 'fail'}
    check(all(stock[k].status == v for k, v in want.items()), 'a stock-MP-like level: ' + ', '.join(f'{k} {stock[k].status}' for k in want))
    info = level_info()
    info.spawners[1].template = levelinfo.PLACEHOLDER
    check(levelchecks.ai_templates(info, levelinfo.PLACEHOLDER).status == 'warn', 'one AI spawner on the DefaultSoldier placeholder: a warning')
    info = level_info()
    info.entries['common.fbrb'].discard('objects/soldiers/templates/ai/enemy_grunt_sense.dbx')
    t = levelchecks.templates_loaded(info, levelinfo.PLACEHOLDER)
    check(t.status == 'fail' and t.metrics['chain_missing'] == 1, 'a partition the template refers to is not in the load set: fail')
    info = level_info()
    info.entries['common.fbrb'] = set()
    check(levelchecks.templates_loaded(info, levelinfo.PLACEHOLDER).metrics['templates_missing'] == 1
          and levelchecks.kits_loaded(info).status == 'warn', 'the template itself not loadable: fail; a kit missing: a warning')
    mft = os.path.join(tmp, 'package.mft')
    open(mft, 'w').write('BundleSource game:levels/other/level-00.fbrb\nBundleTarget game:levels/other/level-00.fbrb\n\n'
                         'BundleSource game:levels/test_level/level-00.fbrb\nBundleTarget package:levels/test_level/level-00.fbrb\n'
                         'BundleTarget game:levels/test_level/level-00.fbrb\nBundleTarget game:levels/sp_common/level-00.fbrb\n\n'
                         'BundleSource game:levels/test_level/loader-00.fbrb\nBundleTarget game:levels/test_level/loader-00.fbrb\n')
    ls = levelinfo.load_set(tmp, 'test_level', mft)
    check([x[0] for x in ls] == ['package:levels/test_level/level-00.fbrb', 'game:levels/test_level/level-00.fbrb', 'game:levels/sp_common/level-00.fbrb']
          and ls[0][1] == os.path.join(tmp, 'game', 'Package', 'levels', 'test_level', 'level-00.fbrb')
          and ls[2][1] == os.path.join(tmp, 'game', 'Dist', 'win32', 'levels', 'sp_common', 'level-00.fbrb'),
          'load set from package.mft: the level-00 block only; package: and game: targets as files')
    check(levelinfo.load_set(tmp, 'missing_level', mft) == [], 'a level without a block: an empty load set')

    # fbrb
    items = [('Levels/TEST.dbx', '<non-resource>', b'level' * 100), ('Levels/Gone.dbx', '*deleted*', b''),
             ('Levels/TEST/Precomputed/Infantry/SectorOffsets.dbx', '<non-resource>', b'offsets!')]
    for zipped in (True, False):
        bp = os.path.join(tmp, f'test{int(zipped)}.fbrb')
        open(bp, 'wb').write(fbrb.build(items, zipped))
        e = fbrb.entries(bp)
        n = fbrb.names(bp)
        got = fbrb.read(bp, ['levels/test/precomputed/infantry/sectoroffsets.dbx', 'Levels/TEST.dbx'])
        check(len(e) == 3 and e[1].deleted and 'levels/gone.dbx' not in n and got['Levels/TEST.dbx'] == b'level' * 100
              and got['levels/test/precomputed/infantry/sectoroffsets.dbx'] == b'offsets!',
              f'fbrb reader ({"zipped" if zipped else "not zipped"}): entries, deleted markers left out of names, entries read')
    try:
        fbrb.read(bp, ['nope.dbx'])
        check(False, 'a missing entry raises KeyError')
    except KeyError:
        check(True, 'a missing entry raises KeyError')
    junk = os.path.join(tmp, 'junk.fbrb')
    open(junk, 'wb').write(b'not a bundle at all')
    try:
        fbrb.entries(junk)
        check(False, 'a file that is not a bundle is refused')
    except ValueError as ex:
        check('not an FbRB bundle' in str(ex), 'a file that is not a bundle is refused')
    cache = os.path.join(tmp, 'cache')
    n1 = levelinfo.bundle_names(bp, cache)
    r1 = levelinfo.bundle_read(bp, ['Levels/TEST.dbx'], cache)
    open(bp, 'ab').close()
    check(n1 == levelinfo.bundle_names(bp, cache) and r1 == levelinfo.bundle_read(bp, ['Levels/TEST.dbx'], cache)
          and os.path.isdir(os.path.join(cache, 'bundles')), 'bundle names and entries cached per bundle state')

    # opfor.log sessions
    ss = ingame.sessions(LOG)
    check(len(ss) == 3 and ss[1].level == 'Levels/ONS_MP_002' and ss[1].in_game and ss[1].events == ['AI init']
          and ss[1].offset_loads == [(-3, -7), (-3, -7)] and ss[1].pathfinders == [0, 21] and ss[1].exited and not ss[1].crashed,
          'opfor.log: sessions, level, in game, AI init, both SectorOffsets loads, pathfinder counts, exit')
    check(len(ingame.for_level(ss, 'ons_mp_002')) == 2 and ss[2].override == (-3, -7) and ss[2].crashed, 'sessions of one level; the override and a crash')
    ev = ingame.evaluate(ss[1], (-3, -7))
    check(all(ev[k].status == 'pass' for k in ('loads-ai-init', 'offsets-read', 'pathfinders')), 'a good session passes the three in-game criteria')
    ev2 = ingame.evaluate(ss[2], (-3, -7))
    check(ev2['loads-ai-init'].status == 'fail' and ev2['offsets-read'].status == 'fail' and ev2['pathfinders'].status == 'hold',
          'a crashed session with the override and 0,0 loads fails; no unit counters: on hold')
    check(all(r.status == 'hold' for r in ingame.telemetry_tests().values()), 'the telemetry tests (M6) are on hold')

    # saved runs and comparison
    base = saved_run('base', {'pairs_reached_pct': 80.0, 'reach_parity_pct': 99.0, 'islands': 5})
    new = saved_run('new', {'pairs_reached_pct': 79.8, 'reach_parity_pct': 97.0, 'islands': 7, 'found': 95.0, 'ratio': 1.2},
                    level_status='fail', bits='1011')
    p = runs.save(base, os.path.join(tmp, 'base.json'))
    check(runs.load(p) == json.load(open(p)) and not os.path.exists(p + '.tmp'), 'a run saves and loads (no temporary file left)')
    other = os.path.join(tmp, 'other.json')
    json.dump({'format': 'moh-navtune'}, open(other, 'w'))
    try:
        runs.load(other)
        check(False, 'another format is refused')
    except ValueError:
        check(True, 'another format is refused')
    found = runs.compare(base, new)
    kinds = {(f[0], f[3], f[4]) for f in found}
    check(('worse', 'reachability', 'reach_parity_pct') in kinds and ('worse', 'reachability', 'pairs_reached_pct') not in kinds,
          'worse beyond the tolerance (99 -> 97) is a regression; within it (80 -> 79.8) is not')
    check(('better', 'battery', 'random.found_pct') in kinds and ('worse', 'battery', 'random.length_ratio_median') in kinds,
          'higher success is better; a length ratio further from 1.0 is worse')
    check(('changed', 'reachability', 'islands') in kinds and ('status', 'has-ai', '') in kinds and ('pairs', 'battery', 'random.lost_pairs') in kinds,
          'counts listed; a check now failing; battery pairs routed before and not now')
    fixed = runs.compare(new, base)
    check(any(f[0] == 'fixed' for f in fixed), 'a failing check that passes again is listed as fixed')
    gone = saved_run('gone', {})
    gone['levels']['sp_extra'] = gone['levels'].pop('sp_test')
    check({f[0] for f in runs.compare(base, gone)} == {'missing', 'new'}, 'levels missing from and new in a run are listed')
    check(not [f for f in runs.compare(base, base) if f[0] in runs.REGRESSION], 'a run against itself: nothing worse')
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        runs.report(new)
        bad = runs.compare_report(base, new, found)
    text = out.getvalue()
    check(bad == 3 and 'FAIL has-ai' in text and '3 regression(s)' in text and 'reach_parity_pct: 99.0 -> 97.0' in text,
          'the report and the comparison as text')

    # the command line
    np_ = runs.save(new, os.path.join(tmp, 'new.json'))
    env = {**os.environ, 'LOCALAPPDATA': tmp}
    rc = subprocess.run([PY, HARNESS, 'compare', p, np_], capture_output=True, text=True, env=env)
    check(rc.returncode == 1 and 'worse' in rc.stdout and '3 regression(s)' in rc.stdout, f'harness.py compare: a regression, exit {rc.returncode}')
    rc = subprocess.run([PY, HARNESS, 'compare', p, p], capture_output=True, text=True, env=env)
    check(rc.returncode == 0 and 'no regressions' in rc.stdout, f'harness.py compare of a run with itself: exit {rc.returncode}')
    rc = subprocess.run([PY, HARNESS, 'show', np_], capture_output=True, text=True, env=env)
    check(rc.returncode == 0 and 'SP_TEST' in rc.stdout and 'summary:' in rc.stdout, 'harness.py show')
    rc = subprocess.run([PY, HARNESS, 'list', '--runs', tmp], capture_output=True, text=True, env=env)
    check(rc.returncode == 0 and 'base' in rc.stdout and 'new' in rc.stdout, 'harness.py list')
    logf = os.path.join(tmp, 'opfor.log')
    open(logf, 'w').write(LOG)
    base['levels']['ons_mp_002'] = {'name': 'ONS_MP_002', 'files': {}, 'level': {'offsets-asset': {
        'status': 'pass', 'summary': 'Infantry SectorOffsets -3,-7', 'metrics': {'asset': 1, 'x': -3, 'z': -7}}}, 'ingame': {}}
    runs.save(base, p)
    rc = subprocess.run([PY, HARNESS, 'import-log', p, '--level', 'ons_mp_002', '--log', logf, '--session', '0'], capture_output=True, text=True, env=env)
    got = runs.load(p)['levels']['ons_mp_002']['ingame']
    check(rc.returncode == 0 and got['offsets-read']['status'] == 'pass' and got['pathfinders']['metrics']['pathfinders_peak'] == 21,
          f'harness.py import-log: the in-game checks from a session, saved in the run (exit {rc.returncode})')
    rc = subprocess.run([PY, HARNESS, 'ingame', 'mp_005'], capture_output=True, text=True, env=env)
    check(rc.returncode == 0 and 'ON HOLD' in rc.stdout and 'does not start the game' in rc.stdout, 'harness.py ingame: on hold, starts nothing')
    rc = subprocess.run([PY, HARNESS, 'show', os.path.join(tmp, 'missing.json')], capture_output=True, text=True, env=env)
    logs = os.path.join(tmp, 'moh-tool-suite', 'logs')
    logged = any(f.startswith('harness-') and 'ERROR' in open(os.path.join(logs, f), encoding='utf-8').read()
                 for f in (os.listdir(logs) if os.path.isdir(logs) else []))
    check(rc.returncode == 1 and 'missing' in rc.stderr.lower() and 'what to do' in rc.stderr and logged,
          'a missing run: exit 1, the cause and what to do in words, the error in the log')


# ------------------------------------------------------------------------------------------------ game

def game(check, tmp):
    import hpdtool
    import navkit
    root = navkit.find_opfor(None)
    bundle = os.path.join(root, 'game', 'Dist', 'win32', 'levels', 'sp_009', 'level-00.fbrb')
    tool = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
    if os.path.isfile(tool):
        out = subprocess.run([tool, 'list', bundle], capture_output=True, text=True).stdout.splitlines()[:-1]
        theirs = sorted(l.split(None, 2)[2].lower() for l in out if '*deleted*' not in l)      # names may hold spaces
        check(theirs == sorted(fbrb.names(bundle)), f'the fbrb reader lists the same {len(theirs)} entries as FbrbTool (sp_009)')
    runs_dir = os.path.join(tmp, 'runs')
    env = {**os.environ, 'LOCALAPPDATA': tmp}

    def harness(*a):
        return subprocess.run([PY, HARNESS, *a, '--runs', runs_dir], capture_output=True, text=True, env=env, timeout=3600)
    r = harness('run', 'g1', '--levels', 'sp_009,sp_004_b', '--pairs', '100', '--level-pairs', '50')
    path = os.path.join(runs_dir, 'g1.json')
    check(os.path.isfile(path), f'a run on sp_009 and sp_004_b is saved (exit {r.returncode}: '
          + (r.stdout.strip().splitlines() or [''])[-2].strip() + ')')
    if not os.path.isfile(path):
        print(r.stdout[-3000:], r.stderr[-3000:])
        return
    g1 = runs.load(path)
    for lv in ('sp_009', 'sp_004_b'):
        L = g1['levels'][lv]
        d = L['files'].get('dice', {}).get('tests', {})
        check(d.get('structure', {}).get('status') == 'pass' and d.get('offsets', {}).get('status') == 'pass',
              f'{lv}: DICE\'s file passes structure and its sector ids match the SectorOffsets asset')
        lvl = L['level']
        check(all(lvl[k]['status'] == 'pass' for k in ('has-ai', 'ai-spawners', 'templates-loaded', 'orders', 'offsets-asset', 'path-data')),
              f'{lv}: level checks pass (' + ', '.join(f'{k} {v["status"]}' for k, v in lvl.items()) + ')')
        check(L['points']['sources'] > 0 and L['points']['targets'] > 0 and 'random' in d.get('battery', {}).get('data', {}),
              f'{lv}: points ({L["points"]}) and the battery\'s pair data')
        nk = L['files'].get('navkit', {}).get('tests', {})
        check('parity' in nk and 'ground' in nk['parity']['metrics'] and 'found_of_ref_pct' in ''.join(nk['battery']['metrics']),
              f'{lv}: NavKit\'s build has parity measures and battery results against DICE\'s routes')
    r2 = harness('run', 'g2', '--levels', 'sp_009,sp_004_b', '--pairs', '100', '--level-pairs', '50', '--reference', 'g1')
    check('no regressions' in r2.stdout, 'the same run again: no regressions (the measures are deterministic)')
    # a damaged copy of DICE's sp_009 file: half its sectors without route edges
    src = os.path.join(root, 'game', 'Output', 'win32', 'ai', 'levels', 'sp_009', 'infantry', 'pathdata.hpd')
    f = hpdtool.parse(open(src, 'rb').read())
    xs = sorted((s.bounds[0] + s.bounds[1]) / 2 for s in f.sectors)
    for s in f.sectors:
        if (s.bounds[0] + s.bounds[1]) / 2 < xs[len(xs) // 2]:
            s.edges = []
    bad = os.path.join(tmp, 'broken', 'sp_009.hpd')
    os.makedirs(os.path.dirname(bad))
    open(bad, 'wb').write(hpdtool.write(f))
    r3 = harness('run', 'g3', '--levels', 'sp_009', '--navkit', bad.replace('sp_009.hpd', '{lv}.hpd'),
                 '--tests', 'structure,reachability,battery,orders,offsets')
    r4 = harness('compare', 'g1', 'g3')
    check(r4.returncode == 1 and 'worse' in r4.stdout and 'sp_009 navkit' in r4.stdout,
          f'a damaged file in place of NavKit\'s build is flagged (exit {r4.returncode}): '
          + '; '.join(l.strip() for l in r4.stdout.splitlines() if 'worse' in l)[:300])
    # point classes on the Onslaught maps: infantry points only, the others counted; ONS_MP_005GR's partition (in its
    # Package bundle only) read
    r5 = harness('run', 'g5', '--levels', 'ons_mp_004,ons_mp_005gr', '--files', 'dice', '--tests', 'reachability,orders')
    p5 = os.path.join(runs_dir, 'g5.json')
    if os.path.isfile(p5):
        g5 = runs.load(p5)['levels']
        a, b = g5['ons_mp_004'], g5['ons_mp_005gr']
        ex = a.get('excluded', {})
        check(ex.get('sources', {}).get('vehicle: air', 0) > 0 and ex.get('sources', {}).get('other map', 0) > 0
              and ex.get('orders', {}).get('water', 0) > 0 and ex.get('targets', {}).get('base', 0) > 0,
              f'ONS_MP_004: vehicle, other-map, base and water points counted, not tested ({ex.get("sources")}, {ex.get("targets")}, {ex.get("orders")})')
        rr = a['files']['dice']['tests']['reachability']
        check(rr['status'] == 'pass' and rr['metrics']['targets_reached_pct'] == 100.0,
              f'ONS_MP_004: DICE\'s file reaches every infantry target ({rr["summary"][:90]})')
        check(b['points'].get('infantry_sources', 0) > 0 and not any('level not read' in n for n in b['notes']),
              f'ONS_MP_005GR: its level partition read from the Package bundle ({b["points"]})')
    else:
        check(False, f'a run on ONS_MP_004 and ONS_MP_005GR (exit {r5.returncode}): {r5.stdout[-500:]}')
    log = os.path.join(root, 'game', 'logs', 'opfor.log')
    if os.path.isfile(log):
        ss = ingame.sessions(open(log, encoding='utf-8', errors='replace').read())
        check(len(ss) > 0, f'the user\'s opfor.log reads: {len(ss)} session(s), {sum(1 for s in ss if s.in_game)} in game (read only)')


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    tmp = tempfile.mkdtemp(prefix='harness_test_')
    try:
        (game if '--game' in argv else quick)(check, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
