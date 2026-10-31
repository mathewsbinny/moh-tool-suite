r"""MOH map and bot test harness: offline tests of levels and their path data, saved per named run and compared
between runs (DESIGN.md section 4.5, milestone M5; this folder's README).

  harness.py run [<name>] [--levels mp_01,mp_03_elimination] [--files dice,navkit,installed] [--navkit auto|regress|build|<path {lv}>]
                 [--tests structure,reachability,battery,orders,offsets,parity,level] [--pairs 200] [--level-pairs 100]
                 [--seed 1] [--reference <run>] [--scale 1] [--opfor <folder>] [--runs <folder>]
  harness.py show <run> [--details 3]          a saved run as text (a name in the runs folder, or a .json path)
  harness.py compare <base> <new> [--scale 1]  what got worse or better; exit code 1 on a regression
  harness.py list                              the saved runs
  harness.py import-log <run> --level <lv> [--log <opfor.log>] [--session -1]
                                               the in-game checks from a session of an opfor.log the user's own run left
                                               (OPFOR's testrun.bat or a play test); the game is never started here
  harness.py ingame <level>                    ON HOLD - needs playtest: what the in-game tests will do

run: per level, every path-data file asked for (dice: DICE's original; navkit: NavKit's build - by default
work\navkit\regress\<lv>.hpd, else work\navkit\<lv>\pathdata.hpd; installed: what the game loads, when not DICE's):
  structure     hpdcheck (closed boundaries, edges, node order, zero-length edges)
  reachability  soldier spawns -> AreaOrders / M-COMs / capture points / objectives over the route graph; islands;
                against DICE's file, the share of its connected pairs that this file connects
  battery       --pairs random ground point pairs from DICE's cells (seeded: the same in every run) and --level-pairs
                spawn -> target pairs through the path test; success, length against DICE's routes
  orders        a route node within 15 m of every AI order, and the order on an area
  offsets       the file's sector ids against the level's SectorOffsets asset
  parity        NavKit's DICE-parity measures (navparity), from a saved navparity / navregress run of the same file
                when there is one, else measured and cached; against navregress's reference run "regression"
and per level the AI-readiness checks from files (HasAI, AI spawners, templates and their partitions in the load set,
kits, AreaOrders, the SectorOffsets asset, installed path data); the in-game checks stay on hold.
Default levels: every level with DICE path data; MOH ships none, so there every level NavKit has built
(work\navkit\<lv>\pathdata.hpd: the 26 MP levels); --levels for a subset. --files defaults to dice,navkit only when one
of the levels has DICE path data, else (MOH) to navkit. MOH's
levels have no AI layer (no HasAI, AI spawners or SectorOffsets asset: MoHMPGame.exe has none of those types), so the
AI-readiness checks fail there by design.
Saved as <OPFOR>\work\harness\runs\<name>.json (default name "last"); with --reference, compared with that run.
Exit code: 0 nothing failed (and no regression), 1 a test failed or something got worse, 2 a run not found.
"""
import functools
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..'))
NAVKIT = os.path.join(SUITE, 'moh-auto-navkit')
for _p in (HERE, os.path.join(SUITE, 'core'), NAVKIT, os.path.join(NAVKIT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from testharness import checks, classify, ingame, levelchecks, runs  # noqa: E402

print = functools.partial(print, flush=True)      # progress shows at once when the output goes to a file
TESTS = ['structure', 'reachability', 'battery', 'orders', 'offsets', 'parity', 'level']


def opfor(given):
    import navkit
    return navkit.find_opfor(given)


def cache_dir(root):
    d = os.path.join(root, 'work', 'harness', 'cache')
    os.makedirs(d, exist_ok=True)
    return d


def timed(fn, *a, **k):
    """A test run with its time; an exception becomes an 'error' result (logged with its traceback)."""
    t0 = time.time()
    try:
        r = fn(*a, **k)
    except Exception as e:  # noqa: BLE001 - harness boundary: any failure of a test becomes its 'error' result
        from mohcore import errors
        errors.logger().exception(f'test failed to run: {e}')
        r = checks.Result('error', f'{type(e).__name__}: {e}', details=['the traceback is in the log'])
    r.seconds = time.time() - t0
    return r


# ------------------------------------------------------------------------------------------------ parity (NavKit's)

def parity_row(root, lv, path, cache):
    """navparity's row for the file: from a saved navparity run that measured this file (and the file is not newer
    than that run), else from the harness cache (by both files' sha1), else measured now (navparity.run_level)."""
    import json
    import navparity
    from testharness import levelinfo
    mtime = os.path.getmtime(path)
    for name in ('regression_last', 'regression'):
        p = navparity.save_path(root, name)
        if not os.path.isfile(p):
            continue
        run = json.load(open(p))
        try:
            when = time.mktime(time.strptime(run['date'], '%Y-%m-%d %H:%M'))
        except (KeyError, TypeError, ValueError, OverflowError):     # no date, or not one strptime/mktime reads
            continue
        for row in run['levels']:
            if row.get('level') == lv and os.path.normcase(os.path.abspath(row.get('candidate', ''))) == os.path.normcase(os.path.abspath(path)) \
                    and mtime <= when:
                return row, f"navparity run '{run['name']}' ({run['date']})"
    ref = navparity.reference_of(root, lv)
    key = f'{levelinfo.sha1(ref)[:12]}_{levelinfo.sha1(path)[:12]}'
    cp = os.path.join(cache, 'parity', f'{lv}_{key}.json')
    if os.path.isfile(cp):
        return json.load(open(cp)), 'the harness cache'
    row = navparity.run_level(root, lv, path, lambda *a: None)
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    tmp = cp + '.tmp'                              # whole or not at all: a cut-off entry would be read forever
    with open(tmp, 'w') as fh:
        json.dump(row, fh, indent=1)
    os.replace(tmp, cp)
    return row, 'measured now'


def parity(root, lv, path, cache):
    import json
    import navparity
    import navregress
    row, source = parity_row(root, lv, path, cache)
    m = {k: v for k, v in row.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k != 'seconds'}
    summ = (f"ground {m.get('ground', 0):.1f} % (+{m.get('extra_ground', 0):.1f} % extra), nodes {m.get('nodes_same', 0):.1f} %, "
            f"connectivity {m.get('connectivity', 0):.1f} % (over {m.get('over_connection', 0):.1f} %), "
            f"AI positions {m.get('ai_coverage', 0):.0f} %; from {source}")
    status, details = 'info', []
    rp = navparity.save_path(root, 'regression')
    if os.path.isfile(rp):
        ref = {r['level']: r for r in json.load(open(rp))['levels']}.get(lv)
        if ref is not None:
            worse, better, _counts = navregress.compare(ref, row, runs.PARITY_TOL)
            status = 'fail' if worse else 'pass'
            details = [f'worse than navregress\'s reference: {k} {a} -> {b}' for k, a, b in worse]
            details += [f'better than navregress\'s reference: {k} {a} -> {b}' for k, a, b in better]
            summ += f"; {'worse than' if worse else 'not worse than'} navregress's reference"
    return checks.Result(status, summ, m, details)


# ------------------------------------------------------------------------------------------------ one level

def run_level(root, lv, opts, cache, log=print):
    import hpdcheck
    import hpdtool
    import ordercheck
    from mohcore import navdata
    from testharness import levelinfo
    L = {'name': lv, 'notes': [], 'points': {}, 'files': {}, 'level': {}, 'ingame': {}}
    t0 = time.time()
    try:
        info = levelinfo.gather(root, lv, cache, log=lambda *a: None)
    except Exception as e:  # noqa: BLE001 - harness boundary: a level that cannot be read is reported, the run goes on
        from mohcore import errors
        errors.logger().exception(f'{lv}: level not read')
        L['notes'].append(f'level not read: {type(e).__name__}: {e}')
        L['level']['level-data'] = checks.Result('error', f'the level could not be read: {e}').as_dict()
        return L
    L['name'] = info.name or lv
    L['notes'] += info.notes
    log(f'  level read ({time.time() - t0:.0f} s): {len(info.sources)} spawns, {len(info.targets)} targets, {len(info.orders)} orders')
    files = levelinfo.pathdata_files(root, lv, opts['files'], opts['navkit'])
    for key in opts['files']:
        if not any(k == key for k, _ in files):
            L['notes'].append(f'no {key} path-data file found for this level')
            L['files'][key] = {'tests': {'missing': checks.Result(
                'error', f'{key} path data not found for this level').as_dict()}}
    ref_key = 'dice' if any(k == 'dice' for k, _ in files) else (files[0][0] if files else None)
    files.sort(key=lambda kp: kp[0] != ref_key)               # the reference file first
    ref = {}
    tests = opts['tests']
    # infantry points only for the infantry tests: classes from the entities (levelinfo) and from what lies under each
    # point - terrain, water and the reference file's walkable cells (the same classes for every file of the level)
    ref_nav = navdata.load(files[0][1], cache_dir=os.path.join(cache, 'navdata')) if files else None
    ground = classify.Ground(ref_nav, info.heightfield, info.water)
    for pts in (info.sources, info.targets, info.orders):
        classify.by_ground(pts, ground)
    sources, ex_s = classify.split(info.sources)
    targets, ex_t = classify.split(info.targets)
    orders, ex_o = classify.split(info.orders)
    L['points'] = {'sources': len(info.sources), 'targets': len(info.targets), 'orders': len(info.orders),
                   'infantry_sources': len(sources), 'infantry_targets': len(targets), 'infantry_orders': len(orders)}
    L['excluded'] = {'sources': ex_s, 'targets': ex_t, 'orders': ex_o,
                     'examples': [p.label() for p in info.sources + info.targets + info.orders if p.cls != classify.INFANTRY][:12]}
    log(f'  infantry points: {len(sources)} spawns, {len(targets)} targets, {len(orders)} orders; not infantry: '
        + '; '.join(f'{k} {classify.excluded_text(v)}' for k, v in (('spawns', ex_s), ('targets', ex_t), ('orders', ex_o)) if v))
    for key, path in files:
        tf = time.time()
        F = {'path': path, 'sha1': levelinfo.sha1(path), 'size': os.path.getsize(path), 'tests': {}}
        L['files'][key] = F
        res = F['tests']
        nav = ref_nav if key == ref_key else navdata.load(path, cache_dir=os.path.join(cache, 'navdata'))
        if 'structure' in tests:
            res['structure'] = timed(lambda: checks.structure(hpdcheck.check(path)))
        if 'reachability' in tests:
            r = timed(checks.reachability, nav, sources, targets, None if key == ref_key else ref.get('reach'),
                      baseline=key == 'dice')
            res['reachability'] = r
            if key == ref_key:
                ref['reach'] = r.data
        if 'battery' in tests:
            def bat():
                if key == ref_key:
                    ref['pairs'] = {'random': list(zip(*[iter(checks.random_points(nav, 2 * opts['pairs'], opts['seed'], lv))] * 2)),
                                    'level': checks.level_pairs(sources, targets, opts['level_pairs'], opts['seed'], lv)}
                    ref['routes'] = {}
                out, names = [], []
                for name, pairs in ref['pairs'].items():
                    routes = checks.route_pairs(nav, pairs)
                    if key == ref_key:
                        ref['routes'][name] = routes
                    out.append(checks.battery(name, pairs, routes, None if key == ref_key else ref['routes'].get(name)))
                    names.append(name)
                return checks.merge(out, names)
            res['battery'] = timed(bat)
        if 'orders' in tests:
            r = timed(lambda: checks.orders(nav, orders, ordercheck.PathData(path).on_area,
                                            None if key == ref_key else ref.get('orders'), baseline=key == 'dice'))
            res['orders'] = r
            if key == ref_key:
                ref['orders'] = r.data
        if 'offsets' in tests:
            res['offsets'] = timed(lambda: checks.offsets(checks.sector_offsets(hpdtool.parse(open(path, 'rb').read()).sectors),
                                                          info.assets.get('Infantry')))
        if 'parity' in tests and key != 'dice' and ref_key == 'dice':
            res['parity'] = timed(parity, root, lv, path, cache)
        for t in list(res):
            res[t] = res[t].as_dict()
        log(f'  {key:9s} {os.path.relpath(path, root)}: ' + ', '.join(f'{t} {runs.LABEL[r["status"]].strip()}' for t, r in res.items())
            + f' ({time.time() - tf:.0f} s)')
    if 'level' in tests:
        for name, r in levelchecks.all_checks(info, levelinfo.PLACEHOLDER).items():
            (L['ingame'] if r.status == 'hold' else L['level'])[name] = r.as_dict()
        for name, r in ingame.telemetry_tests().items():
            L['ingame'][name] = r.as_dict()
        log('  level checks: ' + ', '.join(f'{t} {runs.LABEL[r["status"]].strip()}' for t, r in L['level'].items()))
    return L


# ------------------------------------------------------------------------------------------------ commands

def opt(args, k, d=None):
    if k in args:
        i = args.index(k)
        if i + 1 >= len(args):
            raise ValueError(f'{k} needs a value')
        return args[i + 1]
    return d


def positional(args):
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a.startswith('--'):
            skip = True
        else:
            out.append(a)
    return out


def default_files(root, levels):
    """--files when not given: 'dice,navkit' when one of the levels has DICE's path data (navparity.reference_of),
    else 'navkit' - MOH ships none, and a 'dice' there would only record "path data not found" for every level."""
    import navparity
    return 'dice,navkit' if any(os.path.isfile(navparity.reference_of(root, lv)) for lv in levels) else 'navkit'


def cmd_run(args):
    import navparity
    root = opfor(opt(args, '--opfor'))
    folder = runs.runs_dir(root, opt(args, '--runs'))
    name = (positional(args) or ['last'])[0]
    levels = opt(args, '--levels')
    levels = [x.strip().lower() for x in levels.split(',')] if levels else [
        lv for lv in navparity.LEVELS + navparity.ONSLAUGHT if os.path.isfile(navparity.reference_of(root, lv))]
    if not levels:          # MOH: no DICE path data - the levels NavKit has built
        nk = os.path.join(root, 'work', 'navkit')
        levels = sorted(lv for lv in (os.listdir(nk) if os.path.isdir(nk) else [])
                        if os.path.isfile(os.path.join(nk, lv, 'pathdata.hpd')))
    tests = [t.strip() for t in opt(args, '--tests', ','.join(TESTS)).split(',')]
    unknown = [t for t in tests if t not in TESTS]
    if unknown:
        raise ValueError(f'unknown test kind(s) {", ".join(unknown)} (known: {", ".join(TESTS)})')
    options = {'levels': levels, 'files': [f.strip() for f in opt(args, '--files', default_files(root, levels)).split(',')],
               'navkit': opt(args, '--navkit', 'auto'), 'tests': tests, 'pairs': int(opt(args, '--pairs', 200)),
               'level_pairs': int(opt(args, '--level-pairs', 100)), 'seed': int(opt(args, '--seed', 1))}
    reference = opt(args, '--reference')
    ref_path = runs.path_of(reference, folder) if reference else None
    if reference and not os.path.isfile(ref_path):
        print(f'no saved run "{reference}" ({ref_path}); see: harness.py list')
        return 2
    out_path = runs.path_of(name, folder)
    if reference and os.path.normcase(os.path.abspath(out_path)) == os.path.normcase(os.path.abspath(ref_path)):
        print(f'--reference "{reference}" names the same file as the output run "{name}" ({out_path}); '
              f'give the output a different name so the baseline is not overwritten')
        return 2
    base = runs.load(ref_path) if reference else None
    run = runs.new_run(name, options, SUITE)
    cache = cache_dir(root)
    start = time.time()
    print(f"harness run '{name}': {len(levels)} level(s), files {', '.join(options['files'])}, tests {', '.join(tests)}")
    for n, lv in enumerate(levels, 1):
        print(f'[{n}/{len(levels)}] {lv}')
        run['levels'][lv] = run_level(root, lv, options, cache)
    run['seconds'] = round(time.time() - start)
    path = runs.save(run, out_path)
    print()
    runs.report(run)
    print(f'saved: {path}')
    bad = any(r['status'] in runs.BAD for *_x, r in runs.results(run))
    if reference:
        print()
        bad |= runs.compare_report(base, run, runs.compare(base, run, float(opt(args, '--scale', 1.0)))) > 0
    return 1 if bad else 0


def runs_folder(args):
    given = opt(args, '--runs')
    if given:
        return given
    import navkit
    try:
        return runs.runs_dir(opfor(opt(args, '--opfor')))
    except (navkit.NavKitError, OSError):
        return os.getcwd()            # paths to .json files still work without Project OPFOR


def cmd_show(args):
    p = positional(args)
    if not p:
        raise ValueError('show needs a run name or a .json path')
    runs.report(runs.load(runs.path_of(p[0], runs_folder(args))), details=int(opt(args, '--details', 3)))
    return 0


def cmd_compare(args):
    p = positional(args)
    if len(p) < 2:
        raise ValueError('compare needs two runs: harness.py compare <base> <new>')
    folder = runs_folder(args)
    base, new = (runs.load(runs.path_of(x, folder)) for x in p[:2])
    return 1 if runs.compare_report(base, new, runs.compare(base, new, float(opt(args, '--scale', 1.0)))) else 0


def cmd_list(args):
    folder = runs_folder(args)
    rows = runs.listing(folder)
    print(f'{len(rows)} saved run(s) in {folder}')
    for name, date, n, c in rows:
        print(f'  {name:24s} {date}  {n} level(s)  {runs.counts_text(c)}')
    return 0


def cmd_import_log(args):
    p = positional(args)
    lv = opt(args, '--level')
    if not p or not lv:
        raise ValueError('import-log needs a run and --level <level>')
    lv = lv.lower().split('/')[-1]
    folder = runs_folder(args)
    path = runs.path_of(p[0], folder)
    run = runs.load(path)
    log_path = opt(args, '--log') or os.path.join(opfor(opt(args, '--opfor')), 'game', 'logs', 'opfor_mp.log')
    text = open(log_path, encoding='utf-8', errors='replace').read()      # read only
    found = ingame.for_level(ingame.sessions(text), lv)
    if not found:
        print(f'no session of {lv} in {log_path}')
        return 2
    s = found[int(opt(args, '--session', -1))]
    L = run['levels'].setdefault(lv, {'name': lv, 'notes': [], 'points': {}, 'files': {}, 'level': {}, 'ingame': {}})
    m = L.get('level', {}).get('offsets-asset', {}).get('metrics', {})     # the asset's values, from the run's level checks
    asset = (m['x'], m['z']) if 'x' in m and 'z' in m else None
    for name, r in ingame.evaluate(s, asset).items():
        L.setdefault('ingame', {})[name] = r.as_dict()
    runs.save(run, path)
    print(f'{lv}: session {s.start} (PID {s.pid}) of {len(found)} in {log_path}')
    for name in ('loads-ai-init', 'offsets-read', 'pathfinders'):
        r = L['ingame'][name]
        print(f"  {runs.LABEL[r['status']]} {name:16s} {r['summary']}")
    print(f'saved: {path}')
    return 1 if any(L['ingame'][n]['status'] in runs.BAD for n in ('loads-ai-init', 'offsets-read', 'pathfinders')) else 0


def cmd_ingame(args):
    p = positional(args)
    lv = p[0] if p else '<level>'
    print(f'In-game tests of {lv}: ON HOLD - needs playtest. The harness does not start the game.')
    print('Planned (ingame.py): testrun.bat per level with [dev] unit_counters_dump=1, then the session imported;')
    print('until then run it yourself and import the log:')
    print(f'  testrun.bat {lv}            (Project OPFOR; or play the level)')
    print(f'  harness.py import-log <run> --level {lv}')
    for name, what in levelchecks.INGAME:
        print(f'  hold {name:16s} {what}')
    for name, what in ingame.TELEMETRY_TESTS:
        print(f'  hold {name:16s} {what} (needs OPFOR telemetry, M6)')
    return 0


COMMANDS = {'run': cmd_run, 'show': cmd_show, 'compare': cmd_compare, 'list': cmd_list, 'import-log': cmd_import_log,
            'ingame': cmd_ingame}


def main(argv):
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 1 if len(argv) >= 2 else 0
    return COMMANDS[argv[1]](argv[2:])


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('harness', gui=False)        # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
