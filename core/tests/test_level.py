r"""level.load against a saved snapshot: per level, the entity counts by category and team, shapes, collision,
terrain and water. Any change shows as a difference, so a loader change that moves or loses objects is
seen at once. After an intended change, review the differences and save a new snapshot with --update.

The snapshot is MOH's (recorded 2026-10-30 on the loader that reads a level's map common_mp_<nn> before common_mp
and the patch's copies first): one level per MP map, plus the DLC modes whose partition or terrain is only in the
patch or in their map's common bundle (mp_01_elimination, mp_02_koth, mp_03_elimination, mp_07_koth).

Without game data (first; --quick runs only these, 2026-10-31): water is read from the patch's terrain layer
(terrain.patch) before the base's, and falls back to the base's; a failed terrain unpack leaves no folder (no
partial folder taken as unpacked later) and its error carries FbrbTool's output; a successful one is renamed into
place.

  python test_level.py [level ...] [--update] [--quick]     default levels: DEFAULT (12 of the 26 MP levels)
"""
import collections
import json
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from mohcore import level as levelmod  # noqa: E402

SNAPSHOT = os.path.join(HERE, 'level_snapshot.json')
DEFAULT = ['mp_01', 'mp_01_elimination', 'mp_02', 'mp_02_koth', 'mp_03_elimination', 'mp_04', 'mp_05_tdm',
           'mp_06_tdm', 'mp_07_koth', 'mp_08_tdm', 'mp_09_overrun', 'mp_10_tdm']


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


def water_mesh(folder, name, triangles):
    """A WaterMesh file of one body with the given number of triangles, under folder/Terrains/<name>."""
    d = os.path.join(folder, 'Terrains', name)
    os.makedirs(d, exist_ok=True)
    n = 3 * triangles
    data = struct.pack('<II', 1, n) + struct.pack(f'<{3 * n}f', *([0.0] * 3 * n)) + struct.pack(f'<I{n}I', n, *range(n))
    open(os.path.join(d, f'{name}.Water.Mesh.res'), 'wb').write(data)


def quick_checks(check):
    tmp = tempfile.mkdtemp(prefix='level_test_')
    try:
        terrain = os.path.join(tmp, 'work', 'levels', 'lv', 'terrain')
        water_mesh(terrain, 'mp_x', 1)
        check(len(levelmod.water_in(tmp, 'lv', 'mp_x')) == 1, "water: the base layer's when there is no patch layer")
        os.makedirs(terrain + '.patch')
        check(len(levelmod.water_in(tmp, 'lv', 'mp_x')) == 1, "water: the base layer's when the patch layer has none")
        water_mesh(terrain + '.patch', 'mp_x', 2)
        check(len(levelmod.water_in(tmp, 'lv', 'mp_x')) == 2, "water: the patch layer's (terrain.patch) wins")

        # a stand-in FbrbTool: writes a file into the output folder, then fails (or succeeds with "ok" as the bundle)
        tool = os.path.join(tmp, 'fake_fbrbtool.py')
        open(tool, 'w').write('import os, sys\n'
                              'os.makedirs(sys.argv[3], exist_ok=True)\n'
                              'open(os.path.join(sys.argv[3], "part.res"), "w").write("x")\n'
                              'if sys.argv[2] != "ok":\n'
                              '    print("cannot read bundle: header damaged"); sys.exit(3)\n')
        d = os.path.join(tmp, 'unpacked')
        try:
            levelmod.unpack_once([sys.executable, tool], 'bad', d)
            why = None
        except RuntimeError as e:
            why = str(e)
        check(why is not None and 'header damaged' in why and 'exit 3' in why,
              f"a failed unpack raises with FbrbTool's output ({why})")
        check(not os.path.exists(d) and not os.path.exists(d + '.tmp'), 'and leaves no folder, partial or temporary')
        os.makedirs(d + '.tmp')                              # left by an interrupted run
        check(levelmod.unpack_once([sys.executable, tool], 'ok', d) and os.path.isfile(os.path.join(d, 'part.res'))
              and not os.path.exists(d + '.tmp'), 'a successful unpack is renamed into place (an old .tmp folder cleared)')
        check(levelmod.unpack_once([sys.executable, tool], 'bad', d) is False, 'an unpacked folder is not unpacked again')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    update = '--update' in argv
    levels = [a.lower() for a in argv[1:] if not a.startswith('--')] or DEFAULT
    saved = json.load(open(SNAPSHOT)) if os.path.isfile(SNAPSHOT) else {}
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')
    if not update:
        quick_checks(check)
    if '--quick' in argv:
        print('all passed' if not failed else f'{failed} check(s) failed')
        return 1 if failed else 0
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
