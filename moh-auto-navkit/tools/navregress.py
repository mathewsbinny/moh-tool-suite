r"""NavKit regression test: rebuilds levels with the current code and checks that no DICE-parity measure got
worse than a saved reference run.

  navregress.py [--levels sp_004_b,sp_009] [--reference <run>] [--save <run>] [--tolerance 0.05] [--opfor <folder>]

Per level: build with the same inputs as `navkit.py build` (in-game samples, ForbidPathfinding, dynamic-object
and stair triangles, the high-capsule samples, soldier spawns as seeds; the level's SectorOffsets), into
OPFOR's work\navkit\regress\<level>.hpd; its structure (hpdcheck.py: closed boundaries, node order, edges - a
problem fails the level); then navparity's measures (navparity.py) against DICE's file.

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
Default levels: the 13 campaign levels and the Onslaught maps with DICE path data (ONS_MP_002 / 004, when imported) -
about 5 hours with detailed zones; --levels for a subset.
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


def detailed_of(root, lv, samples, xoff, zoff):
    """The detailed-zone pass, as navkit.py build does it: the level's pitch-0.2 samples (navkit.py sample --pitch 0.2,
    work\\navsample\\<lv>_navkit_p02.bin, with _hi_p02) and its detailed sectors chosen from the level alone
    (detailed.py); None without pitch-0.2 samples."""
    import detailed
    p02 = os.path.join(root, 'work', 'navsample', f'{lv}_navkit_p02.bin')
    if not os.path.isfile(p02):
        return None
    hi = p02[:-4].replace('_p02', '_hi_p02') + '.bin'
    import navkit
    common = navkit.common_dirs(root, lv)
    chosen, _why = detailed.choose(os.path.join(root, 'work', 'levels', lv), common, samples, xoff, zoff)
    return dict(samples=p02, high=hi if os.path.isfile(hi) else None, sectors=set(chosen))


def ladders_of(root, lv):
    """The level's ladders (ladders.py, written by navkit.py prepare), or None."""
    p = os.path.join(root, 'work', 'levels', lv, 'ladders.json')
    return json.load(open(p)) if os.path.isfile(p) else None


def build_level(root, lv, out, log=lambda *a: None):
    samples, xoff, zoff, f = inputs(root, lv)
    navbuild.build(samples, f['forbid_tris.npy'], xoff, zoff, out, dynamic_path=f['dynamic_tris.npy'],
                   high_path=samples[:-4] + '_hi.bin', stairs_path=f['stair_tris.npy'],
                   seeds=json.load(open(f['soldier_spawns.json'])), water=level_water(root, lv),
                   order_points=order_points_of(root, lv), area_orders=area_orders_of(root, lv),
                   bounds=reference_bounds(root, lv), log=log,
                   detailed=detailed_of(root, lv, samples, xoff, zoff), ladders=ladders_of(root, lv))


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
    for k in HIGHER + LOWER:
        a, b = ref.get(k), now.get(k)
        if a is None or b is None:
            continue
        d = (b - a) if k in HIGHER else (a - b)
        if d < -tol:
            worse.append((k, a, b))
        elif d > tol:
            better.append((k, a, b))
    for k in COUNTS:
        if ref.get(k) != now.get(k):
            counts.append((k, ref.get(k), now.get(k)))
    return worse, better, counts


def main(argv):
    args = argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    root = navparity.opfor_root(opt('--opfor'))
    levels = opt('--levels').split(',') if opt('--levels') else [
        lv for lv in navparity.LEVELS + navparity.ONSLAUGHT if os.path.isfile(navparity.reference_of(root, lv))]
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
            print(f'      measuring parity against DICE ({duration(time.time() - t0)} in)')
            row = navparity.run_level(root, lv, out, lambda *a: None)
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
        print(f'{"FAIL" if worse else "ok  "} {lv:9s} ground {row["ground"]:6.2f}  extra {row["extra_ground"]:6.2f}  '
              f'nodes {row["nodes_same"]:5.2f}  connect {row["connectivity"]:6.2f}  over {row["over_connection"]:6.2f}  '
              f'AI {row["ai_coverage"]:5.1f}  ({time.time() - t0:.0f} s)')
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
