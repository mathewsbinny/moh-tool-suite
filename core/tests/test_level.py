r"""level.load against a saved snapshot: per level, the entity counts by category and team, shapes, collision,
terrain and water. Any change shows as a difference, so a loader change that moves or loses objects is
seen at once. After an intended change, review the differences and save a new snapshot with --update.

  python test_level.py [level ...] [--update]     default levels: sp_01 mp_005 mp_009gr ons_mp_004
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from mohcore import level as levelmod  # noqa: E402

SNAPSHOT = os.path.join(HERE, 'level_snapshot.json')
DEFAULT = ['sp_01', 'mp_005', 'mp_009gr', 'ons_mp_004']


def summary(lvl):
    def counter(items):
        return dict(sorted(collections.Counter(items).items()))
    hf = lvl.heightfield
    w = lvl.water
    return {
        'entities': len(lvl.entities),
        'categories': counter(e.category for e in lvl.entities),
        'teams': counter(f'{e.category} / team {e.team} / ai {e.ai}' for e in lvl.entities
                         if e.category in ('soldier spawn', 'vehicle spawn', 'capture point')),
        'destructible': sum(e.destructible for e in lvl.entities),
        'shapes': counter(f'{lvl.entities[s.entity].short_type} {s.kind} {"closed" if s.closed else "open"}' for s in lvl.shapes),
        'meshes': len(lvl.meshes),
        'collision triangles': int(sum(len(m.triangles) for m in lvl.meshes)),
        'blueprints without collision': len(lvl.missing_collision),
        'terrain': None if hf is None else {'size': int(hf.size), 'min': [round(float(x), 2) for x in hf.min],
                                            'max': [round(float(x), 2) for x in hf.max]},
        'water triangles': None if w is None else len(w),
        'notes': len(lvl.notes),
    }


def diff(a, b, path=''):
    out = []
    for k in sorted(set(a) | set(b), key=str):
        x, y = a.get(k), b.get(k)
        if isinstance(x, dict) and isinstance(y, dict):
            out += diff(x, y, f'{path}{k} / ')
        elif x != y:
            out.append(f'{path}{k}: {x} -> {y}')
    return out


def main(argv):
    update = '--update' in argv
    levels = [a.lower() for a in argv[1:] if not a.startswith('--')] or DEFAULT
    saved = json.load(open(SNAPSHOT)) if os.path.isfile(SNAPSHOT) else {}
    failed = 0
    for lv in levels:
        now = summary(levelmod.load(lv, log=lambda *a: None))
        if update:
            saved[lv] = now
            print(f'saved {lv}: {now["entities"]} entities')
            continue
        if lv not in saved:
            print(f'FAIL {lv}: not in the snapshot (run with --update)')
            failed += 1
            continue
        d = diff(saved[lv], now)
        failed += bool(d)
        print(f'{"FAIL" if d else "ok  "} {lv}: {now["entities"]} entities, {now["meshes"]} with collision, '
              f'{sum(now["shapes"].values())} shapes, water {now["water triangles"]}')
        for line in d[:30]:
            print('       ' + line)
    if update:
        json.dump(saved, open(SNAPSHOT, 'w'), indent=1, sort_keys=True)
        print('snapshot written:', SNAPSHOT)
        return 0
    print('all passed' if not failed else f'{failed} level(s) differ from the snapshot')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
