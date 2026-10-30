r"""NavKit regression test: rebuilds levels with the current code and checks that no measure got worse than a
saved reference run - on a level with DICE path data its DICE-parity measures, on a level without (every Medal of
Honor MP level: MOH ships none) NavKit's own self-check figures.

  navregress.py [--levels mp_07_koth,mp_03_elimination] [--reference <run>] [--save <run>] [--tolerance 0.05]
                [--opfor <folder>]

Per level: build with the same inputs as `navkit.py build` (navkit.build_inputs: the level's samples,
ForbidPathfinding, dynamic-object and stair triangles, the high-capsule samples, soldier spawns as seeds, water,
detailed zones, ladders; the level's sector offsets), into OPFOR's work\navkit\regress\<level>.hpd (never the
level's own work\navkit\<level>\ export); its structure (hpdcheck.py: closed boundaries, node order, edges - a
problem fails the level); then its measures:
  - with DICE path data: navparity's measures (navparity.py) against DICE's file;
  - without (self_check): of the level's AI positions (spawn points and objectives, ordercheck.order_points) the
    share on an area, with a route node within 15 m, in a large route group; of the pairs on areas, the share the
    route network joins (ordercheck's self-check) and, of the ordered pairs, the share routed along the edges'
    directions (a drop link goes one way); and of the main island's route nodes inside the combat areas, the share
    in its largest strongly connected part (each of its nodes reaches every other). Counts: points, sectors, areas,
    detailed areas, nodes, edges, one-way edges, islands, large islands, walkable cells, and a digest of the sector
    meshes and route graphs (the file without its build timestamps: the same digest is the reference's build byte
    for byte; a change is listed, not failed).

Each measure is compared with the reference run (default "regression"; navparity.py's work\navkit\parity\
<run>.json). Measures where higher is better must not drop, measures where lower is better must not rise, by
more than the tolerance (points); counts are listed when they change but do not fail. Exit code 0: nothing
worse; 1: something worse (or a level failed to build); 2: no reference run.

Progress: before each level, its number, its expected time and the run's share done and time left (from each
level's time in the last run or the reference - saved per level as "seconds" - else from its sample file size at the seconds per MB of the timed levels; each finished level
updates the estimates of the rest, and the time left is scaled by the pace so far);
the build's stages are printed indented as they finish.

The run is saved as "regression_last". After an intended improvement, make it the reference:
  navregress.py --save regression          (or: copy regression_last.json to regression.json)
Default levels: the 13 campaign levels and the Onslaught maps with DICE path data (ONS_MP_002 / 004, when imported);
without any (MOH) every level NavKit has prepared (work\navkit\<level>\navkit.json: the 26 MP levels, about 1 to 2
hours); --levels for a subset.
"""
import glob
import json
import os
import re
import sys
import functools
import time

print = functools.partial(print, flush=True)   # progress shows at once when the output goes to a file

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import navbuild  # noqa: E402
import navparity  # noqa: E402
import hpdcheck  # noqa: E402

HIGHER = ['ground', 'areas_same', 'portals', 'outline', 'borders', 'costs', 'nodes_same', 'nodes_near', 'edges_same',
          'connectivity', 'ai_coverage', 'ai_pairwise',
          'df_d_precision', 'df_d_recall', 'df_f_precision', 'df_f_recall']     # D lines / F cells (navdf.py)
LOWER = ['extra_ground', 'over_connection', 'sectors_missing']
COUNTS = ['sectors_candidate', 'areas_candidate', 'detailed_candidate', 'islands_candidate', 'large_islands_candidate',
          'ai_main_candidate', 'df_d_candidate', 'df_f_candidate']
# a level without DICE path data (self_check): NavKit's own figures
SELF_HIGHER = ['self_on_area', 'self_near_node', 'self_large_group', 'self_pairwise', 'self_directed', 'self_strong']
SELF_COUNTS = ['self_points', 'self_sectors', 'self_areas', 'self_detailed', 'self_nodes', 'self_edges', 'self_one_way',
               'self_islands', 'self_large_islands', 'self_cells', 'self_digest']
LARGE = 25                                      # nodes: a large island (navparity's rule)


def inputs(root, lv):
    """The build inputs of a level, as navkit.py build uses them; raises if something is missing."""
    L = os.path.join(root, 'work', 'levels', lv)
    samples = None
    for name in (f'{lv}_navkit', f'{lv}_full'):
        p = os.path.join(root, 'work', 'navsample', name + '.bin')
        if os.path.isfile(p) and os.path.isfile(p[:-4] + '_hi.bin'):
            samples = p
            break
    if samples is None:
        raise RuntimeError(f'{lv}: no in-game samples (work\\navsample\\{lv}_navkit.bin or _full.bin, with _hi)')
    settings = os.path.join(root, 'work', 'navkit', lv, 'navkit.json')
    if os.path.isfile(settings):
        s = json.load(open(settings))
        xoff, zoff = s['xoff'], s['zoff']
    else:
        so = glob.glob(os.path.join(L, 'dist', 'levels', lv, 'Precomputed', 'Infantry', 'SectorOffsets.xml'))
        if not so:
            raise RuntimeError(f'{lv}: no navkit.json and no SectorOffsets.xml (run navkit.py prepare {lv})')
        t = open(so[0], encoding='utf-8').read()
        xoff = int(re.search(r'name="XOffset">(-?\d+)<', t).group(1))
        zoff = int(re.search(r'name="ZOffset">(-?\d+)<', t).group(1))
    files = {k: os.path.join(L, k) for k in ('forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy', 'soldier_spawns.json')}
    missing = [k for k, p in files.items() if not os.path.isfile(p)]
    if missing:
        raise RuntimeError(f'{lv}: {", ".join(missing)} missing (run navkit.py prepare {lv})')
    return samples, xoff, zoff, files


def level_water(root, lv):
    """The level's water mesh (vertices, triangles) for the build, or None (core/mohcore/water.py)."""
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import level as levelmod
    w = levelmod.water_of(root, lv, log=lambda *a: None)
    return (w.vertices, w.triangles) if w is not None and len(w) else None


def reference_bounds(root, lv):
    """(minX, minZ, maxX, maxZ) of DICE's path data for the level - the extent of its area outlines and portals at
    every pitch (detailed zones included: on sp_008, sp_008_b, ons_mp_005gr they reach past the normal-pitch ground) -
    or None when the level has no DICE path data (a new map). Border portals are left out: they sit on sector margins."""
    import hpdtool
    import kymesh
    import numpy as np
    p = navparity.reference_of(root, lv)
    if not os.path.isfile(p):
        return None
    pts = []
    for s in hpdtool.parse(open(p, 'rb').read()).sectors:
        for a in kymesh.parse_mesh(s.mesh).areas:
            pts += [q for line in a.outline for q in line]
            pts += [q for pt in a.portals for q in (pt.p0, pt.p1)]
    if not pts:
        return None
    P = np.asarray(pts, float)
    return float(P[:, 0].min()), float(P[:, 2].min()), float(P[:, 0].max()), float(P[:, 2].max())


def area_orders_of(root, lv):
    """The level's AreaOrder positions (work/levels/<lv>/area_orders.json from levelobjects.py; for a level prepared
    before that file existed, read from the level's XML), or None."""
    p = os.path.join(root, 'work', 'levels', lv, 'area_orders.json')
    if os.path.isfile(p):
        return json.load(open(p))
    try:
        import levelobjects
        x = navparity.level_xml(root, lv)
        return levelobjects.area_orders(open(x, encoding='utf-8', errors='replace').read()) if x else None
    except Exception:
        return None


def order_points_of(root, lv):
    """The level's AI order positions (work/levels/<lv>/order_points.json, written by levelobjects.py), or None."""
    p = os.path.join(root, 'work', 'levels', lv, 'order_points.json')
    return json.load(open(p)) if os.path.isfile(p) else None


def build_level(root, lv, out, log=lambda *a: None):
    """The level built into out with navkit.py build's own inputs (navkit.build_inputs: detailed zones, ladders and
    all) - on MOH a rebuild with unchanged code gives the export's sector meshes and route graphs byte for byte."""
    import navkit
    samples, forbid, xoff, zoff, kw = navkit.build_inputs(navkit.Project(root), lv, log=lambda *a: None)
    kw['log'] = log
    navbuild.build(samples, forbid, xoff, zoff, out, **kw)


def strong_parts(n, edges):
    """A label per node: its strongly connected part of the directed route graph (Kosaraju; no recursion)."""
    fw, bw = [[] for _ in range(n)], [[] for _ in range(n)]
    for a, b in edges:
        fw[a].append(b)
        bw[b].append(a)
    order, seen = [], [False] * n
    for s0 in range(n):                         # finishing order of a depth-first walk along the edges
        if seen[s0]:
            continue
        seen[s0] = True
        stack = [(s0, iter(fw[s0]))]
        while stack:
            u, it = stack[-1]
            v = next(it, None)
            if v is None:
                stack.pop()
                order.append(u)
            elif not seen[v]:
                seen[v] = True
                stack.append((v, iter(fw[v])))
    part = [-1] * n
    for s0 in reversed(order):                  # then against the edges, latest finished first
        if part[s0] >= 0:
            continue
        part[s0] = s0
        stack = [s0]
        while stack:
            for v in bw[stack.pop()]:
                if part[v] < 0:
                    part[v] = s0
                    stack.append(v)
    return part


def reach(fw, start):
    """Every node reachable from start along the edges' directions."""
    seen, stack = {start}, [start]
    while stack:
        for v in fw[stack.pop()]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def combat_areas(root, lv):
    """The level's combat-area outlines as (x, z) polygons (CombatAreaTriggerEntityData, every team and stage), or []."""
    import numpy as np
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import level as levelmod
    L = levelmod.load(lv, opfor=root, geometry=False, log=lambda *a: None)
    return [np.asarray(s.points, float)[:, [0, 2]] for s in L.shapes
            if L.entities[s.entity].category == 'combat area' and s.kind == 'area']


def self_check(root, lv, path):
    """NavKit's own figures for a build of a level without DICE path data (the module's docstring), as a row."""
    import hashlib
    from collections import Counter
    import numpy as np
    import hpdtool
    import ordercheck
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import navdata
    f = hpdtool.parse(open(path, 'rb').read())
    digest = hashlib.sha1()
    for s in f.sectors:                         # the build without its timestamps: meshes and route graphs
        digest.update(repr((s.id, s.mesh, s.nodes, s.edges)).encode())
    nav = navdata.load(path)
    pd = ordercheck.PathData(path)
    n = len(nav.node_pos)
    edges = nav.edges.tolist()
    fw = [[] for _ in range(n)]
    for a, b in edges:
        fw[a].append(b)
    pairs = set(map(tuple, edges))
    # the AI positions: on an area, and their route node - the nearest within 15 m and 4 m in height (ordercheck)
    pts = ordercheck.order_points(navparity.level_xml(root, lv))
    on, node = [], []
    for _, _, p in pts:
        on.append(pd.on_area(p))
        d = np.hypot(nav.node_pos[:, 0] - p[0], nav.node_pos[:, 2] - p[2])
        d = np.where(np.abs(nav.node_pos[:, 1] - ordercheck.Y_OFFSET - p[1]) <= 4.0, d, 1e9)
        k = int(np.argmin(d)) if n else -1
        node.append(k if k >= 0 and d[k] <= 15.0 else None)
    biggest = max((i.nodes for i in nav.islands), default=0)
    size = [nav.islands[nav.node_island[k]].nodes if k is not None else 0 for k in node]
    idx = [i for i in range(len(pts)) if on[i] and node[i] is not None]
    joined = sum(1 for a in idx for b in idx if a < b and nav.node_island[node[a]] == nav.node_island[node[b]])
    reached = {node[a]: reach(fw, node[a]) for a in idx}
    routed = sum(1 for a in idx for b in idx if a != b and node[b] in reached[node[a]])
    # the main island's route nodes inside the combat areas (all of them on a level without): the share in the
    # largest strongly connected part - ground left by a drop link with no way back is outside it
    part = strong_parts(n, edges)
    main = np.nonzero(nav.node_island == 0)[0]
    polys = combat_areas(root, lv)
    if polys and len(main):
        from matplotlib.path import Path
        inside = np.zeros(len(main), bool)
        for poly in polys:
            inside |= Path(poly).contains_points(nav.node_pos[main][:, [0, 2]])
        main = main[inside]
    parts = Counter(part[k] for k in main.tolist())

    def pct(a, b):
        return round(100.0 * a / b, 2) if b else 100.0
    return {
        'level': lv, 'candidate': path, 'mode': 'self-check (no DICE path data)',
        'self_points': len(pts), 'self_on_area': pct(sum(on), len(pts)),
        'self_near_node': pct(sum(k is not None for k in node), len(pts)),
        'self_large_group': pct(sum(1 for z in size if z and z >= 0.25 * biggest), len(pts)),
        'self_pairwise': pct(joined, len(idx) * (len(idx) - 1) // 2),
        'self_directed': pct(routed, len(idx) * (len(idx) - 1)),
        'self_strong': pct(parts.most_common(1)[0][1] if parts else 0, len(main)),
        'self_sectors': len(f.sectors), 'self_areas': len(nav.areas),
        'self_detailed': sum(1 for a in nav.areas if a.pitch < navdata.DETAILED),
        'self_nodes': n, 'self_edges': len(edges), 'self_one_way': sum(1 for a, b in pairs if (b, a) not in pairs),
        'self_islands': len(nav.islands), 'self_large_islands': sum(1 for i in nav.islands if i.nodes >= LARGE),
        'self_cells': int(len(nav.cell_xz)), 'self_digest': digest.hexdigest(),
    }


DEFAULT_SECONDS = 210                           # a level with no earlier timing: 45 minutes / 13 levels


def duration(s):
    s = int(round(s))
    if s < 90:
        return f'{s} s'
    if s < 3600:
        return f'{round(s / 60)} min'
    return f'{s // 3600} h {s % 3600 // 60:02d} min'


def estimates(root, levels, *runs):
    """Expected seconds per level: its time in the given earlier runs (first found), else its in-game sample file
    size times the median seconds per MB of every level the runs time, else DEFAULT_SECONDS. runs: {level: row}."""
    known = {}
    for rows in runs:
        for lv, row in rows.items():
            if lv not in known and row.get('seconds'):
                known[lv] = row['seconds']
    size = {}
    for lv in set(levels) | set(known):
        try:
            size[lv] = os.path.getsize(inputs(root, lv)[0]) / 1e6
        except Exception:
            pass
    rates = sorted(known[lv] / size[lv] for lv in known if size.get(lv))
    rate = rates[len(rates) // 2] if rates else None
    return {lv: known.get(lv) or (rate * size[lv] if rate and lv in size else DEFAULT_SECONDS) for lv in levels}


def compare(ref, now, tol):
    """(worse, better, count changes) for one level: lists of (measure, reference, now)."""
    worse, better, counts = [], [], []
    for k in HIGHER + LOWER + SELF_HIGHER:
        a, b = ref.get(k), now.get(k)
        if a is None or b is None:
            continue
        d = (a - b) if k in LOWER else (b - a)
        if d < -tol:
            worse.append((k, a, b))
        elif d > tol:
            better.append((k, a, b))
    for k in COUNTS + SELF_COUNTS:
        if ref.get(k) != now.get(k):
            counts.append((k, ref.get(k), now.get(k)))
    return worse, better, counts


def main(argv):
    args = argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    root = navparity.opfor_root(opt('--opfor'))
    levels = opt('--levels').split(',') if opt('--levels') else [
        lv for lv in navparity.LEVELS + navparity.ONSLAUGHT if os.path.isfile(navparity.reference_of(root, lv))]
    if not levels:                              # MOH: no DICE path data - every level NavKit has prepared
        d = os.path.join(root, 'work', 'navkit')
        levels = sorted(lv for lv in os.listdir(d) if os.path.isfile(os.path.join(d, lv, 'navkit.json')))
    reference = opt('--reference', 'regression')
    tol = float(opt('--tolerance', 0.05))
    ref_path = navparity.save_path(root, reference)
    ref_rows = {}
    if os.path.isfile(ref_path):
        ref_rows = {r['level']: r for r in json.load(open(ref_path))['levels']}
    elif '--save' not in args:
        print(f'no reference run "{reference}" ({ref_path}); make one with: navregress.py --save {reference}')
        return 2
    outdir = os.path.join(root, 'work', 'navkit', 'regress')
    os.makedirs(outdir, exist_ok=True)
    rows, failed = [], 0
    last_path = navparity.save_path(root, 'regression_last')
    last_rows = {r['level']: r for r in json.load(open(last_path))['levels']} if os.path.isfile(last_path) else {}
    est = estimates(root, levels, last_rows, ref_rows)
    total = sum(est.values())
    print(f'NavKit regression: {len(levels)} level(s), reference "{reference}", tolerance {tol} points; '
          f'expected about {duration(total)}')
    start, done_est, timed = time.time(), 0.0, {}
    for n, lv in enumerate(levels, 1):
        t0 = time.time()
        elapsed = t0 - start
        left = max(total - done_est, 0) * (elapsed / done_est if done_est else 1.0)   # scaled by the pace so far
        print(f'[{n}/{len(levels)}] {lv}: building (about {duration(est[lv])}); run {100 * done_est / total:.0f} % done, '
              f'{duration(elapsed)} so far, about {duration(left)} left')
        out = os.path.join(outdir, f'{lv}.hpd')
        try:
            build_level(root, lv, out, lambda m: print(f'      {m}'))
            structure = {k: v for k, v in hpdcheck.check(out).items() if v}    # what DICE's files always satisfy
            for k, v in structure.items():
                print(f'      structure: {k}: {len(v)} - {v[0]}')
            if os.path.isfile(navparity.reference_of(root, lv)):
                print(f'      measuring parity against DICE ({duration(time.time() - t0)} in)')
                row = navparity.run_level(root, lv, out, lambda *a: None)
            else:
                print(f'      self-check: no DICE path data ({duration(time.time() - t0)} in)')
                row = self_check(root, lv, out)
        except Exception as e:
            failed += 1
            done_est += est[lv]
            print(f'FAIL {lv}: {e}')
            continue
        done_est += est[lv]
        row['seconds'] = round(time.time() - t0)
        row['structure_problems'] = sum(len(v) for v in structure.values())
        rows.append(row)
        timed[lv] = {'seconds': row['seconds']}         # re-estimate the levels to come with this run's times
        rest = levels[n:]
        if rest:
            new = estimates(root, rest, timed, last_rows, ref_rows)
            total += sum(new[x] - est[x] for x in rest)
            est.update(new)
        ref = ref_rows.get(lv)
        if ref is None:
            failed += bool(structure)
            print(f'{"FAIL" if structure else "new "} {lv}: not in the reference run ({time.time() - t0:.0f} s)')
            continue
        worse, better, counts = compare(ref, row, tol)
        if structure:
            worse.append(('structure (hpdcheck)', ref.get('structure_problems', 0), row['structure_problems']))
        failed += bool(worse)
        if 'ground' in row:
            print(f'{"FAIL" if worse else "ok  "} {lv:9s} ground {row["ground"]:6.2f}  extra {row["extra_ground"]:6.2f}  '
                  f'nodes {row["nodes_same"]:5.2f}  connect {row["connectivity"]:6.2f}  over {row["over_connection"]:6.2f}  '
                  f'AI {row["ai_coverage"]:5.1f}  ({time.time() - t0:.0f} s)')
        else:
            same = 'the reference build' if row['self_digest'] == ref.get('self_digest') else 'changed from the reference'
            print(f'{"FAIL" if worse else "ok  "} {lv:9s} AI on areas {row["self_on_area"]:5.1f}  joined {row["self_pairwise"]:5.1f}  '
                  f'routed {row["self_directed"]:5.1f}  strong {row["self_strong"]:5.1f}  nodes {row["self_nodes"]}  '
                  f'islands {row["self_islands"]}; sectors: {same}  ({time.time() - t0:.0f} s)')
        for k, a, b in worse:
            print(f'       worse:  {k} {a} -> {b}')
        for k, a, b in better:
            print(f'       better: {k} {a} -> {b}')
        for k, a, b in counts:
            print(f'       count:  {k} {a} -> {b}')
    run = {'name': 'regression_last', 'date': time.strftime('%Y-%m-%d %H:%M'), 'levels': rows}
    json.dump(run, open(navparity.save_path(root, 'regression_last'), 'w'), indent=1)
    if '--save' in args:
        name = opt('--save')
        run['name'] = name
        if name == reference and ref_rows:                 # a subset run keeps the other levels' reference rows
            done = {r['level'] for r in rows}
            run['levels'] = rows + [r for lv, r in ref_rows.items() if lv not in done]
        json.dump(run, open(navparity.save_path(root, name), 'w'), indent=1)
        print(f'saved as reference run "{name}"')
    print(('all passed' if not failed else f'{failed} level(s) worse or failed') + f' ({duration(time.time() - start)})')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('navregress', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
