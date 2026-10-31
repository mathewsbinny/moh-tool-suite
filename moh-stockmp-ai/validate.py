"""Offline checks of a built AI layer (no game launch). Each check gives (name, ok, detail); validate() runs them all.

  0. The build is of the configuration given: build_report.json's config_sha256 (pipeline.config_sha) matches it -
     else validate fails at once (a configuration edited after the build is judged against nothing it built).

  1. DbxEdit check: no error, and no more warnings than the stock level has (references resolved against the built
     bundle folder and mp_common).
  2. DbxEdit roundtrip, and XML export -> import (--like) gives the same bytes.
  3. Read by the suite's other DBX reader (core/mohcore/dbx.py): every local reference resolves; every new entity is
     listed once in LevelData.Objects; every event connection's source and target exist; HasAI and
     PathfindingTypes set.
  4. Only the intended changes: compared with the stock level, the existing instances differ only in HasAI,
     PathfindingTypes and (stock_spawners=human) PlayerType; everything else is added.
  5. References into other partitions (template, kits, PathfindingSettings) resolve in the built bundle or mp_common.
  6. The packed template partitions and the SectorOffsets assets: present at the manifest's tail, the offsets stored as
     4-byte integers equal to the values computed from the staged path data.
  7. The AI layer against the path data: every order has a route node within ORDER_NODE m, every AI spawner stands on
     the navigation data (area, or node within SPAWN_NODE m) of the large route group, every spawner is listed by an
     order of its team and stage, and each spawner reaches its orders' objectives over the route graph (same group).
  8. Rush stages: later-stage spawners and orders start disabled; each stage's advance source is in the level.
  9. The packed bundles unpack to exactly the built files.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import Counter

import config as cfgmod
import dbxbin
import pipeline

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))
from mohcore import dbx as coredbx  # noqa: E402

ORDER_NODE = 15.0
SPAWN_NODE = cfgmod.SNAP_RADIUS + 0.5
ALLOWED_CHANGED = ('HasAI', 'PlayerType')


def _dbxedit(*args):
    r = subprocess.run([pipeline.DBX, *map(str, args)], capture_output=True, text=True, errors='replace')
    return r.returncode, r.stdout + r.stderr


def _counts(out):
    m = re.search(r'(\d+) error\(s\), (\d+) warning\(s\)', out)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def check_dbxedit(orig, authored, bundle_dir, orig_bundle_dir, mp_common):
    _rc, a = _dbxedit('check', authored, '--bundle', bundle_dir, '--bundle', mp_common)
    _rc, o = _dbxedit('check', orig, '--bundle', orig_bundle_dir, '--bundle', mp_common)
    ea, wa = _counts(a)
    eo, wo = _counts(o)
    ok = ea is not None and ea <= (eo or 0) and wa <= wo
    errs_a = sorted(l for l in a.splitlines() if l.startswith('ERROR'))
    errs_o = sorted(l for l in o.splitlines() if l.startswith('ERROR'))
    ok = ok and errs_a == errs_o
    return ok, f'authored {ea} error(s), {wa} warning(s); stock {eo} / {wo}'


def check_roundtrip(authored, tmp):
    rc, out = _dbxedit('roundtrip', authored)
    ok1 = '1 identical, 0 different' in out
    a = os.path.join(tmp, 'rt.dbx')
    shutil.copyfile(authored, a)
    _dbxedit('xml', a)
    rc2, out2 = _dbxedit('import', a[:-4] + '.xml', os.path.join(tmp, 'rt_back.dbx'), '--like', a)
    ok2 = rc2 == 0 and open(os.path.join(tmp, 'rt_back.dbx'), 'rb').read() == open(a, 'rb').read()
    return ok1 and ok2, f'roundtrip {"identical" if ok1 else "DIFFERENT"}; xml -> import {"identical" if ok2 else "DIFFERENT (exit " + str(rc2) + ")"}'


def _core_instances(path):
    f = coredbx.read(path)
    return f, {(e.attr('guid') or '').upper(): e for e in f.instances}


def _refs(e):
    for c in e.children or ():
        r = c.attr('ref')
        if r is not None and r != 'null':
            yield c, r
        yield from _refs(c)


def check_structure(orig, authored, c):
    f, inst = _core_instances(authored)
    _fo, oinst = _core_instances(orig)
    problems = []
    for g, e in inst.items():
        for _c, r in _refs(e):
            if '/' not in r and r.upper() not in inst:
                problems.append(f'{g}: local reference {r} unresolved')
    lvl = next(e for e in f.instances if (e.attr('type') or '').endswith('.LevelData'))
    objs = [i.attr('ref').upper() for i in (lvl.child('Objects').children or [])]
    dup = [k for k, v in Counter(objs).items() if v > 1]
    if dup:
        problems.append(f'{len(dup)} entities listed twice in Objects')
    new = [g for g in inst if g not in oinst]
    unlisted = [g for g in new if g not in set(objs)]
    if unlisted:
        problems.append(f'{len(unlisted)} new instances not in Objects')
    ec = lvl.child('EventConnections')
    nev = 0
    for cx in ec.children or ():
        for side in ('Source', 'Target'):
            r = cx.child(side).attr('ref')
            if '/' not in r and r.upper() not in inst:
                problems.append(f'event {side} {r} unresolved')
        nev += 1
    has_ai = coredbx.value(lvl.child('HasAI'))
    pft = [i.attr('ref') for i in (lvl.child('PathfindingTypes').children or [])]
    if has_ai != 'true':
        problems.append('HasAI is not true')
    if [p.lower() for p in pft] != [p.lower() for p in __import__('author').PATHFINDING]:
        problems.append(f'PathfindingTypes {pft}')
    return not problems, (f'{len(new)} new instances, all in Objects; {nev} event connections resolve; HasAI true; '
                          f'PathfindingTypes infantry + vehicles' if not problems else '; '.join(problems[:8]))


def check_changes(orig, authored, c, tmp):
    """The existing instances: only HasAI, PathfindingTypes, PlayerType, Objects and EventConnections may differ."""
    fo, oinst = _core_instances(orig)
    fa, ainst = _core_instances(authored)
    bad = []
    changed = Counter()

    def flat(e, path=''):
        out = {}
        for k, ch in enumerate(e.children or ()):
            name = ch.attr('name') or ch.tag
            p = f'{path}/{name}[{k}]'
            if ch.children is not None:
                out.update(flat(ch, p))
            else:
                out[p] = (ch.kind, coredbx.value(ch), tuple(ch.attributes))
        return out
    for g, e in oinst.items():
        a = ainst.get(g)
        if a is None:
            bad.append(f'{g} removed')
            continue
        if (e.attr('type') or '').endswith('.LevelData'):
            fe ={k: v for k, v in flat(e).items() if not k.startswith(('/Objects[', '/EventConnections[', '/PathfindingTypes['))}
            fa_ = {k: v for k, v in flat(a).items() if not k.startswith(('/Objects[', '/EventConnections[', '/PathfindingTypes['))}
            # the old Objects / EventConnections items must be kept, in order, at the front
            for arr in ('Objects', 'EventConnections'):
                oi = [repr(x) for x in (e.child(arr).children or [])]
                ai = [repr(x) for x in (a.child(arr).children or [])][:len(oi)]
                if oi != ai:
                    bad.append(f'LevelData.{arr}: existing items changed')
        else:
            fe, fa_ = flat(e), flat(a)
        for k in set(fe) | set(fa_):
            if fe.get(k) != fa_.get(k):
                field = re.sub(r'\[\d+\]', '', k).strip('/')
                if field in ALLOWED_CHANGED:
                    changed[field] += 1
                else:
                    bad.append(f'{g} {k}: {fe.get(k)} -> {fa_.get(k)}')
    want_pt = c.get('stock_spawners') == 'human'
    if changed['PlayerType'] and not want_pt:
        bad.append('PlayerType changed although stock_spawners is not "human"')
    return not bad, (f'existing instances unchanged except {dict(changed)}' if not bad else '; '.join(bad[:8]))


def _partition_index(folders):
    """lower-case partition path (no .dbx) -> folder file path, over unpacked bundle folders."""
    idx = {}
    for top in folders:
        for dp, _ds, fs in os.walk(top):
            for fn in fs:
                if fn.lower().endswith('.dbx'):
                    p = os.path.join(dp, fn)
                    rel = os.path.relpath(p, top)[:-4].replace('\\', '/').lower()
                    idx.setdefault(rel, p)
    return idx


def check_external(orig, authored, folders):
    _fo, oinst = _core_instances(orig)
    _f, inst = _core_instances(authored)
    idx = _partition_index(folders)
    wanted = set()
    for g, e in inst.items():
        if g in oinst and not (e.attr('type') or '').endswith('.LevelData'):
            continue
        for _c, r in _refs(e):
            if '/' in r:
                wanted.add(r.lower())
    # only the references the stock level does not already have
    old = set()
    for e in oinst.values():
        for _c, r in _refs(e):
            if '/' in r:
                old.add(r.lower())
    wanted -= old
    bad = []
    cache = {}
    for r in sorted(wanted):
        part, guid = r.rsplit('/', 1)
        p = idx.get(part)
        if p is None:
            bad.append(f'{r}: partition not found')
            continue
        if p not in cache:
            cache[p] = {(e.attr('guid') or '').lower() for e in coredbx.read(p).instances}
        if guid not in cache[p]:
            bad.append(f'{r}: instance not in {os.path.basename(p)}')
    return not bad, (f'{len(wanted)} new references into other partitions all resolve: ' + ', '.join(sorted(wanted))
                     if not bad else '; '.join(bad))


def check_assets(build_dir, rep, c, lv_upper):
    bad = []
    lines = [l for l in open(os.path.join(build_dir, 'fbrb.manifest'), encoding='utf-8').read().splitlines() if l.strip()]
    added = rep.get('added_entries', [])
    tail = [l.split('|', 1)[0] for l in lines[-len(added):]] if added else []
    if [t.lower() for t in tail] != [a.lower() for a in added]:
        bad.append(f'added entries are not the manifest tail ({tail})')
    for kind, _folder in pipeline.KINDS:
        p = pipeline.find_entry(build_dir, f'Levels/{lv_upper}/Precomputed/{kind}/SectorOffsets.dbx')
        if not p:
            bad.append(f'{kind} SectorOffsets missing')
            continue
        f = coredbx.read(p)
        e = f.instances[0]
        name = coredbx.value(e.child('Name'))
        if name != f'Levels/{lv_upper}/Precomputed/{kind}/SectorOffsets':
            bad.append(f'{kind}: Name {name}')
        want = rep['sector_offsets_values'][kind]
        for fld, v in (('XOffset', want[0]), ('ZOffset', want[1])):
            size, vals = coredbx.numbers(e.child(fld))
            if size != 4 or struct.unpack('>i', vals[0])[0] != v:
                bad.append(f'{kind} {fld} is not the integer {v}')
    for p in rep.get('template_pack', []):
        q = pipeline.find_entry(build_dir, p)
        if not q:
            bad.append(f'{p} not packed')
    if rep.get('template_pack'):
        t = pipeline.find_entry(build_dir, rep['template_pack'][0])
        g = c['template'].rsplit('/', 1)[1].lower()
        if g not in {(e.attr('guid') or '').lower() for e in coredbx.read(t).instances}:
            bad.append('the template instance is not in the packed template partition')
    return not bad, ('SectorOffsets ' + ', '.join(f'{k} {v[0]},{v[1]}' for k, v in rep['sector_offsets_values'].items())
                     + f' as 4-byte integers; {len(rep.get("template_pack", []))} template partitions packed; added entries at the tail'
                     if not bad else '; '.join(bad))


def check_navigation(c, hpd):
    snap = cfgmod.Snapper(hpd)
    if snap.pd is None:
        return False, f'no path data at {hpd}'
    bad, info = [], []
    groups = {}
    for s in c['spawners']:
        p = tuple(s['pos'])
        q = (p[0], p[1] - cfgmod.SPAWN_LIFT, p[2])
        dist, node, size = snap.nearest(q)
        on = snap.pd.on_area(q)
        if not on and dist > SPAWN_NODE:
            bad.append(f'spawner {s["id"]}: not on the navigation data (node {dist:.1f} m)')
        if size < 0.25 * snap.biggest:
            bad.append(f'spawner {s["id"]}: on a small island ({size} nodes)')
        groups[s['id']] = snap.pd.group[snap.pd.ids[_nearest_index(snap, q)]]
    listed = Counter()
    worst = 0.0
    for o in c['orders']:
        dist, node, size = snap.nearest(tuple(o['pos']))
        worst = max(worst, dist)
        if dist > ORDER_NODE:
            bad.append(f'order {o["id"]}: no route node within {ORDER_NODE} m ({dist:.1f} m)')
        og = snap.pd.group[snap.pd.ids[_nearest_index(snap, tuple(o['pos']))]]
        for x in o['soldiers']:
            listed[x] += 1
            if groups.get(x) != og:
                bad.append(f'order {o["id"]}: spawner {x} is not route-connected to the objective')
    unlisted = [s['id'] for s in c['spawners'] if not listed[s['id']]]
    if unlisted:
        bad.append(f'{len(unlisted)} AI spawners in no order: {unlisted[:6]}')
    return not bad, (f'{len(c["spawners"])} spawners on the navigation data, {len(c["orders"])} orders with a route node '
                     f'within {worst:.1f} m, every spawner route-connected to its objectives' if not bad else '; '.join(bad[:8]))


def _nearest_index(snap, p):
    import numpy as np
    P = snap.pd.pos
    d = np.hypot(P[:, 0] - p[0], P[:, 2] - p[2])
    d = np.where(np.abs(P[:, 1] - cfgmod.NODE_Y - p[1]) <= 4.0, d, 1e9)
    return int(np.argmin(d))


def check_stages(authored, c):
    stages = c.get('stages') or []
    if not stages and not any(s.get('stage') for s in c['spawners']):
        return True, 'no stages (one-stage mode)'
    _f, inst = _core_instances(authored)
    bad = []
    for s in c['spawners'] + c['orders']:
        if (s.get('stage') or 0) > 0:
            role = 'spawner' if s in c['spawners'] else 'order'
            g = dbxbin.new_guid('opfor:stockmp', c['lv'], role, s['id'])
            if coredbx.value(inst[g].child('Enabled')) != 'false':
                bad.append(f'{s["id"]} (stage {s["stage"]}) starts enabled')
    for st in stages:
        src = st['advance']['source'].upper()
        if src not in inst:
            bad.append(f'stage {st["index"]}: source {src} missing')
        elif not (inst[src].attr('type') or '').endswith('MultipleTriggerEntityData'):
            bad.append(f'stage {st["index"]}: source is a {inst[src].attr("type")}')
    return not bad, (f'{len(stages)} stage switches; later stages start disabled' if not bad else '; '.join(bad[:8]))


def check_packed(out_bundle, build_dir, tmp):
    d = os.path.join(tmp, 'unpacked')
    shutil.rmtree(d, ignore_errors=True)
    pipeline.run(pipeline.FBRB, 'unpack', out_bundle, d)
    bad = 0
    n = 0
    for dp, _ds, fs in os.walk(build_dir):
        for fn in fs:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, build_dir)
            if rel == 'fbrb.manifest':
                continue
            q = os.path.join(d, rel)
            n += 1
            if not os.path.isfile(q) or open(q, 'rb').read() != open(p, 'rb').read():
                bad += 1
    shutil.rmtree(d, ignore_errors=True)
    return bad == 0, f'{n} files unpack identical' if not bad else f'{bad} of {n} files differ after packing'


def check_config(rep, c):
    """0. The build is of this configuration: build_report.json's config_sha256 against the one given."""
    built, now = rep.get('config_sha256'), pipeline.config_sha(c)
    if built == now:
        return True, f'built from this configuration ({now[:12]})'
    if built is None:
        return False, 'the build records no configuration hash (built by an older version): build again'
    return False, f'the build is of another configuration ({built[:12]}, this one is {now[:12]}): build again'


def _save(paths, results):
    out = {'passed': all(ok for _n, ok, _d in results), 'results': [{'check': n, 'ok': ok, 'detail': d} for n, ok, d in results]}
    with open(os.path.join(paths.root, 'validation.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)
    return out


def validate(paths, c, log=print):
    rep = json.load(open(os.path.join(paths.root, 'build_report.json'), encoding='utf-8'))
    ok, detail = check_config(rep, c)
    log(f'{"ok  " if ok else "FAIL"} configuration: {detail}')
    if not ok:                                     # the other checks would judge this configuration against another build
        return _save(paths, [('configuration', ok, detail)])
    results = [('configuration', ok, detail)]
    tmp = tempfile.mkdtemp(prefix='stockmp_validate_')
    mp_common = pipeline.common(paths, 'mp_common')
    so = {k: (v['x'], v['z']) for k, v in rep['sector_offsets'].items()}
    try:
        for v, var in rep['variants'].items():
            lv_upper = var['entry'].split('/')[-1][:-4]
            orig = os.path.join(paths.root, 'build', f'{lv_upper}_{v}.original.dbx')
            auth = os.path.join(paths.root, 'build', f'{lv_upper}_{v}.authored.dbx')
            bdir = os.path.join(paths.root, 'build', v)
            sdir = paths.src(v)
            var = dict(var, sector_offsets_values=so, template_pack=rep.get('template_pack', []))
            for name, fn in (('DbxEdit check', lambda: check_dbxedit(orig, auth, bdir, sdir, mp_common)),
                             ('round trips', lambda: check_roundtrip(auth, tmp)),
                             ('structure (second reader)', lambda: check_structure(orig, auth, c)),
                             ('only intended changes', lambda: check_changes(orig, auth, c, tmp)),
                             ('references into other partitions', lambda: check_external(orig, auth, [bdir, mp_common])),
                             ('packed assets', lambda: check_assets(bdir, var, c, lv_upper)),
                             ('Rush stages', lambda: check_stages(auth, c)),
                             ('packed bundle', lambda: check_packed(paths.out_bundle(v), bdir, tmp))):
                try:
                    ok, detail = fn()
                except Exception as e:                       # a check that cannot run is a failed check
                    ok, detail = False, f'{type(e).__name__}: {e}'
                results.append((f'{v}: {name}', ok, detail))
                log(f'{"ok  " if ok else "FAIL"} {v}: {name}: {detail}')
        if c.get('layer', 'full') != 'minimal':
            ok, detail = check_navigation(c, paths.out_pathdata('infantry'))
            results.append(('navigation', ok, detail))
            log(f'{"ok  " if ok else "FAIL"} navigation: {detail}')
        for kind, folder in pipeline.KINDS:
            xz, _agree = pipeline.sector_offsets(paths.out_pathdata(folder))
            ok = tuple(xz) == tuple(so[kind])
            results.append((f'staged {folder} path data', ok, f'sector offsets {xz}'))
            log(f'{"ok  " if ok else "FAIL"} staged {folder} path data: offsets {xz} (asset {so[kind]})')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _save(paths, results)
