"""Saved runs and the comparison between two runs.

A run (JSON, <OPFOR>\\work\\harness\\runs\\<name>.json):

  {"format": "moh-harness-run", "version": 1, "name": "...", "date": "2026-10-26 23:40", "host": "PC name",
   "suite": {"commit": "9d085a9", "changed": true}, "options": {...}, "seconds": 312,
   "levels": {"sp_004_b": {
       "name": "SP_004_B", "notes": [...], "points": {"sources": 107, "targets": 64, "orders": 179},
       "files": {"dice":   {"path": "...", "sha1": "...", "tests": {"structure": RESULT, "reachability": RESULT, ...}},
                 "navkit": {...}},
       "level":  {"has-ai": RESULT, ...},                 AI-readiness checks from files
       "ingame": {"loads-ai-init": RESULT, ...}}}}        'hold' until a session is imported (ingame.py)
  RESULT = {"status": "pass", "summary": "...", "metrics": {"name": number}, "details": ["..."], "seconds": 1.2,
            "data": {...}}                                 data: pair bits etc., for pair-by-pair comparison

Comparison (compare(base, new)): per level, file key, test and metric present in both runs, against the metric's
direction and tolerance (SPEC): worse beyond the tolerance is a regression, better is listed, counts that change are
listed, metrics in one run only are listed. A test whose status goes from pass / info / warn to fail or error, or
from anything to error, is a regression; any other status change is listed. A level, or a test of a level still
in the new run, that is gone from the new run is a regression (a lost file or level must not pass as "no
regressions"), and so is a test new in the new run that fails or errs. Files are matched by key (dice, navkit ...),
so two builds of NavKit compare directly; a file whose sha1 is unchanged is expected to give the same numbers.
"""
import json
import os
import socket
import subprocess
import time

FORMAT, VERSION = 'moh-harness-run', 1
LABEL = {'pass': 'ok  ', 'info': 'info', 'warn': 'warn', 'fail': 'FAIL', 'skip': 'skip', 'hold': 'hold', 'error': 'ERR '}
BAD = {'fail', 'error'}

# parity measures: navregress's own lists and tolerance (points)
try:
    import navregress as _nr
    PARITY_HIGHER, PARITY_LOWER, PARITY_TOL = list(_nr.HIGHER), list(_nr.LOWER), 0.05
except Exception:                                  # quick tests without NavKit on the path still compare
    PARITY_HIGHER = ['ground', 'areas_same', 'portals', 'outline', 'borders', 'costs', 'nodes_same', 'nodes_near',
                     'edges_same', 'connectivity', 'ai_coverage', 'ai_pairwise']
    PARITY_LOWER = ['extra_ground', 'over_connection', 'sectors_missing']
    PARITY_TOL = 0.05


def spec(test, metric):
    """(direction, tolerance) of a metric: 'higher' / 'lower' is better, 'one' (closer to 1.0 is better), or 'count'
    (listed when it changes, never a regression)."""
    base = metric.split('.')[-1]
    if test == 'parity':
        return ('higher', PARITY_TOL) if base in PARITY_HIGHER else ('lower', PARITY_TOL) if base in PARITY_LOWER else ('count', 0)
    if test == 'structure':
        return 'lower', 0
    if test == 'battery':
        if base in ('found_pct', 'found_of_ref_pct'):
            return 'higher', 1.0
        if base in ('extra_pct', 'no_node_pct', 'other_island_pct', 'one_way_pct'):
            return 'lower', 1.0
        if base == 'length_ratio_median':
            return 'one', 0.02
        if base == 'length_ratio_p90':
            return 'one', 0.05
        return 'count', 0
    if test == 'reachability':
        if base == 'reach_over_pct':
            return 'lower', 0.5
        if base == 'targets_lost':
            return 'lower', 0
        if base.endswith('_pct') and base != 'main_island_nodes_pct':
            return 'higher', 0.5
        return 'count', 0
    if test == 'orders':
        if base == 'orders_lost':                  # as reachability's targets_lost: one more lost is worse
            return 'lower', 0
        return ('higher', 0.5) if base.endswith('_pct') else ('count', 0)
    if test == 'offsets':
        return ('higher', 0) if base == 'matches' else ('count', 0)
    return 'count', 0


def suite_version(suite):
    try:
        c = subprocess.run(['git', '-C', suite, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, timeout=20)
        d = subprocess.run(['git', '-C', suite, 'status', '--porcelain'], capture_output=True, text=True, timeout=20)
        return {'commit': c.stdout.strip() or None, 'changed': bool(d.stdout.strip())}
    except Exception:
        return {'commit': None, 'changed': None}


def new_run(name, options, suite):
    return {'format': FORMAT, 'version': VERSION, 'name': name, 'date': time.strftime('%Y-%m-%d %H:%M'),
            'host': os.environ.get('COMPUTERNAME') or socket.gethostname(), 'suite': suite_version(suite),
            'options': options, 'levels': {}, 'seconds': 0}


def runs_dir(root, given=None):
    d = given or os.path.join(root, 'work', 'harness', 'runs')
    os.makedirs(d, exist_ok=True)
    return d


def path_of(name_or_path, folder):
    """A run's file: a path to a .json as given, else <folder>\\<name>.json."""
    if name_or_path.lower().endswith('.json') or os.path.sep in name_or_path or '/' in name_or_path:
        return name_or_path
    return os.path.join(folder, name_or_path.replace(' ', '_') + '.json')


def save(run, path):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(run, fh, indent=1)
    os.replace(tmp, path)
    return path


def load(path):
    with open(path, encoding='utf-8') as fh:
        run = json.load(fh)
    if run.get('format') != FORMAT:
        raise ValueError(f'{path} is not a saved harness run (format {run.get("format")!r})')
    if run.get('version', 0) > VERSION:
        raise ValueError(f'{path} was written by a newer harness (version {run["version"]}); update the suite')
    return run


def listing(folder):
    out = []
    for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        if f.endswith('.json'):
            try:
                r = load(os.path.join(folder, f))
                out.append((f[:-5], r['date'], len(r['levels']), counts(r)))
            except Exception:
                continue
    return out


def results(run):
    """(level, scope, test, result) of every result: scope is a file key, 'level' or 'ingame'."""
    for lv, L in run['levels'].items():
        for key, F in L.get('files', {}).items():
            for test, r in F.get('tests', {}).items():
                yield lv, key, test, r
        for scope in ('level', 'ingame'):
            for test, r in L.get(scope, {}).items():
                yield lv, scope, test, r


def counts(run):
    c = {}
    for *_x, r in results(run):
        c[r['status']] = c.get(r['status'], 0) + 1
    return c


def counts_text(c):
    order = ['pass', 'info', 'warn', 'fail', 'error', 'skip', 'hold']
    words = {'pass': 'ok', 'info': 'info', 'warn': 'warn', 'fail': 'FAIL', 'error': 'error', 'skip': 'skipped', 'hold': 'on hold'}
    return ', '.join(f'{c[k]} {words[k]}' for k in order if c.get(k))


def report(run, log=print, details=3):
    """The run as text: per level, per file, one line per test (and its first detail lines)."""
    s = run.get('suite') or {}
    log(f"harness run '{run['name']}' ({run['date']}, {run.get('host', '?')}; suite "
        f"{s.get('commit') or '?'}{' with changes' if s.get('changed') else ''}): {len(run['levels'])} level(s)")
    for lv, L in run['levels'].items():
        pts = L.get('points', {})
        log(f"\n{L.get('name') or lv}   spawns {pts.get('sources', 0)}, targets {pts.get('targets', 0)}, orders {pts.get('orders', 0)}")
        ex = L.get('excluded')
        if ex:
            words = lambda d: ', '.join(f'{v} {k}' for k, v in sorted(d.items(), key=lambda kv: -kv[1])) or 'none'
            log(f"  infantry points tested: {pts.get('infantry_sources', 0)} spawns, {pts.get('infantry_targets', 0)} targets, "
                f"{pts.get('infantry_orders', 0)} orders")
            log(f"  not infantry (counted, not tested): spawns {words(ex.get('sources', {}))}; targets {words(ex.get('targets', {}))}; "
                f"orders {words(ex.get('orders', {}))}")
        for n in L.get('notes', []):
            log(f'  note: {n}')
        for key, F in L.get('files', {}).items():
            log(f"  {key:9s} {F.get('path', '')}  ({(F.get('sha1') or '')[:8]})")
            for test, r in F.get('tests', {}).items():
                _line(log, test, r, details)
        for scope, title in (('level', 'level checks (AI readiness, from files)'), ('ingame', 'in game')):
            if L.get(scope):
                log(f'  {title}')
                for test, r in L[scope].items():
                    _line(log, test, r, details)
    log(f"\nsummary: {counts_text(counts(run))} ({run.get('seconds', 0):.0f} s)")


def _line(log, test, r, details):
    log(f"    {LABEL.get(r['status'], r['status'])} {test:16s} {r.get('summary', '')}")
    if r['status'] in ('fail', 'warn', 'error'):
        for d in r.get('details', [])[:details]:
            log(f'           {d}')


def compare(base, new, scale=1.0):
    """[(kind, level, scope, test, metric, before, after)]: kind 'worse', 'better', 'changed' (counts), 'missing'
    (a test or level in base, not in new), 'new' (in new only; after is its status), 'status' (a test now failing,
    or now an error: worse), 'fixed' (no longer failing), 'restatus' (any other status change), 'dropped' / 'added'
    (a metric in base only / new only), 'pairs' (battery pairs routed before and not now, with the same points).
    Which of them are regressions: regression()."""
    out = []
    a = {(lv, sc, t): r for lv, sc, t, r in results(base)}
    b = {(lv, sc, t): r for lv, sc, t, r in results(new)}
    for k in a.keys() - b.keys():
        if k[0] in new['levels']:
            out.append(('missing', *k, '', a[k]['status'], None))
    for k in b.keys() - a.keys():
        if k[0] in base['levels']:
            out.append(('new', *k, '', None, b[k]['status']))
    for lv in base['levels'].keys() - new['levels'].keys():
        out.append(('missing', lv, '', '', '', 'level', None))
    for lv in new['levels'].keys() - base['levels'].keys():
        out.append(('new', lv, '', '', '', None, 'level'))
    for k in sorted(a.keys() & b.keys()):
        ra, rb = a[k], b[k]
        sa, sb = ra['status'], rb['status']
        if sa != sb:
            if (sa not in BAD and sb in BAD) or sb == 'error':
                out.append(('status', *k, '', sa, sb))
            elif sa in BAD and sb not in BAD:
                out.append(('fixed', *k, '', sa, sb))
            else:                                  # pass -> skip, fail -> warn ...: listed
                out.append(('restatus', *k, '', sa, sb))
        test = k[2]
        ma, mb = ra.get('metrics', {}), rb.get('metrics', {})
        for m in sorted(ma.keys() - mb.keys()):
            out.append(('dropped', *k, m, ma[m], None))
        for m in sorted(mb.keys() - ma.keys()):
            out.append(('added', *k, m, None, mb[m]))
        for m in sorted(ma.keys() & mb.keys()):
            x, y = ra['metrics'][m], rb['metrics'][m]
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or x == y:
                continue
            direction, tol = spec(test, m)
            tol *= scale
            if direction == 'count':
                out.append(('changed', *k, m, x, y))
                continue
            gain = {'higher': y - x, 'lower': x - y, 'one': abs(x - 1.0) - abs(y - 1.0)}[direction]
            if gain < -tol - 1e-9:
                out.append(('worse', *k, m, x, y))
            elif gain > tol + 1e-9:
                out.append(('better', *k, m, x, y))
        if test == 'battery':
            for name, da in (ra.get('data') or {}).items():
                db = (rb.get('data') or {}).get(name)
                if isinstance(da, dict) and isinstance(db, dict) and da.get('points') == db.get('points') and \
                        len(da.get('bits', '')) == len(db.get('bits', '')):
                    lost = sum(1 for p, q in zip(da['bits'], db['bits']) if p == '1' and q == '0')
                    if lost:
                        out.append(('pairs', *k, f'{name}.lost_pairs', 0, lost))
    return out


REGRESSION = {'worse', 'status', 'missing'}


def regression(f):
    """Whether a compare() finding is a regression: worse, now failing or an error, gone from the new run, or a
    test new in the new run that fails or errs."""
    return f[0] in REGRESSION or (f[0] == 'new' and f[6] in BAD)


def compare_report(base, new, found, log=print):
    log(f"run '{new['name']}' ({new['date']}) against '{base['name']}' ({base['date']})")
    files_a = {(lv, key): F.get('sha1') for lv, L in base['levels'].items() for key, F in L.get('files', {}).items()}
    for lv, L in new['levels'].items():
        for key, F in L.get('files', {}).items():
            was, now = files_a.get((lv, key)), F.get('sha1') or ''
            if was is not None:
                log(f'  {lv:12s} {key:9s} ' + ('same file' if was == now else f'{was[:8]} -> {now[:8]}'))
    words = {'worse': 'worse  ', 'status': 'FAILS  ', 'better': 'better ', 'fixed': 'fixed  ', 'changed': 'count  ',
             'missing': 'MISSING', 'new': 'new    ', 'pairs': 'pairs  ', 'restatus': 'status ', 'dropped': 'dropped',
             'added': 'added  '}
    order = ['status', 'worse', 'missing', 'new', 'pairs', 'fixed', 'restatus', 'better', 'dropped', 'added', 'changed']
    for kind in order:
        for f in [x for x in found if x[0] == kind]:
            _k, lv, sc, test, m, x, y = f
            where = ' '.join(p for p in (lv, sc, test) if p)
            if kind in ('status', 'fixed', 'restatus'):
                log(f'  {words[kind]} {where}: {x} -> {y}')
            elif kind == 'pairs':
                log(f'  {words[kind]} {where}: {y} pair(s) routed before, not now ({m.split(".")[0]})')
            elif kind == 'missing':
                log(f'  {words[kind]} {where}' + (' (the whole level)' if x == 'level' else f' (was {x})'))
            elif kind == 'new':
                log(f'  {"NEW " + y.upper() if y in BAD else words[kind]} {where}')
            elif kind in ('dropped', 'added'):
                log(f'  {words[kind]} {where} {m}' + (f' (was {x})' if kind == 'dropped' else f' ({y})'))
            else:
                d, tol = spec(test, m)
                log(f'  {words[kind]} {where} {m}: {x} -> {y}' + (f'  (tolerance {tol:g})' if kind in ('worse', 'better') else ''))
    bad = sum(1 for f in found if regression(f))
    log(f'{bad} regression(s)' if bad else 'no regressions')
    return bad
