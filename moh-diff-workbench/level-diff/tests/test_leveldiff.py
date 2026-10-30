r"""LevelDiff on two small levels built here (XML written by the test, made binary with DbxEdit import), with
known differences: a changed field, a placed object re-created under a new GUID (paired only with
--match-objects), reference lists with entries added, removed or only reordered, instances added and
removed. Also: --ignore, --type, the exit codes, the JSON output, and bad input.

  python test_leveldiff.py            no game data (builds LevelDiff.exe and DbxEdit.exe if missing)
  python test_leveldiff.py --game     also MOH's mp_01 against itself and its Package (patch) copy against its Dist
                                      copy (MOH ships them the same), and mp_05 Domination against mp_05 TDM (one map,
                                      two modes: a real difference)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
LD = os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'LevelDiff.exe')
DBX = os.path.join(SUITE, 'moh-dbx-tools', 'DbxEdit.exe')


def g(n):
    return f'{n:08x}-0000-0000-0000-000000000000'


def ref_object(guid, blueprint, pos, slot):
    t = '1.0/0.0/0.0/0.0/0.0/1.0/0.0/0.0/0.0/0.0/1.0/0.0/{:.2f}/{:.2f}/{:.2f}/1.0'.format(*pos)
    return (f'\t<instance guid="{guid}" type="Entity.ReferenceObjectData">\n'
            f'\t\t<field name="Enumeration">{slot}</field>\n'
            f'\t\t<complex name="Transform">{t}</complex>\n'
            f'\t\t<field ref="objects/{blueprint}/{blueprint}/{g(0xB0)}" name="ReferencedObject" />\n'
            f'\t</instance>\n')


def with_list(guid, typ, team, name, refs):
    items = ''.join(f'\t\t\t<item ref="{r}" />\n' for r in refs)
    team_field = f'\t\t<field name="Team">{team}</field>\n' if team else ''
    return f'\t<instance guid="{guid}" type="{typ}">\n{team_field}\t\t<array name="{name}">\n{items}\t\t</array>\n\t</instance>\n'


def plain(guid, typ):
    return f'\t<instance guid="{guid}" type="{typ}">\n\t\t<field name="Name">x</field>\n\t</instance>\n'


def partition(instances):
    return ('<?xml version="1.0"?>\n'
            f'<partition guid="{g(1)}" primaryInstance="{g(0x10)}" exportMode="All">\n' + ''.join(instances) + '</partition>\n')


SPAWN, ALT, SET = 'GameSharedResources.SoldierSpawnEntityData', 'GameSharedResources.AlternateSpawnEntityData', 'Entity.ObjectSet'
LEVEL_A = [ref_object(g(0x10), 'house', (10.5, 2.25, -7.75), 5),
           ref_object(g(0x20), 'tree', (20.5, 0.5, 5.25), 6),
           with_list(g(0x30), SPAWN, 'Team1', 'AlternativeSpawnPoints', [g(0x40), g(0x50)]),
           plain(g(0x40), ALT), plain(g(0x50), ALT),
           plain(g(0x60), ALT),
           with_list(g(0x80), SET, None, 'Objects', [g(0x40), g(0x50)])]
LEVEL_B = [ref_object(g(0x10), 'house', (10.5, 2.25, -7.75), 9),            # Enumeration 5 -> 9
           ref_object(g(0x21), 'tree', (20.5, 0.5, 5.25), 6),               # the tree, re-created: new GUID
           with_list(g(0x30), SPAWN, 'Team2', 'AlternativeSpawnPoints', [g(0x50), g(0x70)]),   # team; -0x40 +0x70
           plain(g(0x40), ALT), plain(g(0x50), ALT),
           plain(g(0x70), ALT),                                              # 0x60 removed, 0x70 added
           with_list(g(0x80), SET, None, 'Objects', [g(0x50), g(0x40)])]     # same entries, another order


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')


def leveldiff(a, b, *opts, work):
    out = os.path.join(work, 'r.json')
    if os.path.exists(out):
        os.remove(out)
    r = subprocess.run([LD, a, b, '--json', out, *opts], capture_output=True, text=True)
    data = json.load(open(out, encoding='utf-8')) if os.path.exists(out) else None
    return r.returncode, data, r.stdout + r.stderr


def guids(items):
    return {i['guid'].lower() for i in items}


def build_tools():
    for exe, bat in ((DBX, os.path.join(SUITE, 'moh-dbx-tools', 'build.bat')),
                     (LD, os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'build.bat'))):
        if not os.path.isfile(exe):
            subprocess.run(['cmd', '/c', bat], check=True, capture_output=True)


def main(argv):
    build_tools()
    c = Checks()
    work = tempfile.mkdtemp(prefix='leveldiff_test_')
    try:
        files = {}
        for name, inst in (('a', LEVEL_A), ('b', LEVEL_B)):
            x = os.path.join(work, name + '.xml')
            open(x, 'w', encoding='utf-8').write(partition(inst))
            files[name] = os.path.join(work, name + '.dbx')
            r = subprocess.run([DBX, 'import', x, files[name]], capture_output=True, text=True)
            if r.returncode not in (0, 3) or not os.path.isfile(files[name]):
                print('FAIL DbxEdit import of the test level:', (r.stdout + r.stderr)[-300:])
                return 1
        a, b = files['a'], files['b']

        code, d, _ = leveldiff(a, a, work=work)
        c.check(code == 0 and d is not None and not d['only_in_a'] and not d['only_in_b'] and not d['changed'],
                'a level against itself: exit 0, nothing different')

        code, d, out = leveldiff(a, b, work=work)
        c.check(code == 1, 'different levels: exit 1')
        c.check(guids(d['only_in_a']) == {g(0x20), g(0x60)}, 'only in A: the old tree GUID and the removed instance')
        c.check(guids(d['only_in_b']) == {g(0x21), g(0x70)}, 'only in B: the new tree GUID and the added instance')
        changed = {i['guid'].lower(): i for i in d['changed']}
        c.check(set(changed) == {g(0x10), g(0x30), g(0x80)}, 'changed: the house, the spawn and the object set')
        house = changed.get(g(0x10), {'fields': []})
        c.check([(f['path'], f['a'], f['b']) for f in house['fields']] == [('Enumeration', '5', '9')], 'house: Enumeration 5 -> 9')
        spawn = changed.get(g(0x30), {'fields': [], 'removed': [], 'added': []})
        c.check(any(f['path'] == 'Team' and f['a'] == 'Team1' and f['b'] == 'Team2' for f in spawn['fields']), 'spawn: Team1 -> Team2')
        c.check(spawn['removed'] == [f'AlternativeSpawnPoints: {g(0x40)}'] and spawn['added'] == [f'AlternativeSpawnPoints: {g(0x70)}'],
                'spawn: one alternative spawn point removed, one added')
        oset = changed.get(g(0x80), {'fields': [], 'removed': [], 'added': []})
        c.check(not oset['removed'] and not oset['added'] and any(f['b'] == 'in another order' for f in oset['fields']),
                'object set: same entries in another order is reported as an order change only')
        c.check('only in A' in out and 'changed' in out, 'the text report has its sections')

        code, d, _ = leveldiff(a, b, '--match-objects', work=work)
        c.check(guids(d['only_in_a']) == {g(0x60)} and guids(d['only_in_b']) == {g(0x70)} and d['same_instances'] == 6,
                '--match-objects pairs the re-created tree by blueprint + position')

        code, d, _ = leveldiff(a, b, '--ignore', 'Enumeration', work=work)
        c.check(g(0x10) not in {i['guid'].lower() for i in d['changed']}, '--ignore Enumeration: the house is no longer changed')

        code, d, _ = leveldiff(a, b, '--type', 'SoldierSpawn', work=work)
        c.check(not d['only_in_a'] and not d['only_in_b'] and guids(d['changed']) == {g(0x30)}, '--type SoldierSpawn: only the spawn')

        code, d, out = leveldiff(a, b, '--summary', work=work)
        c.check(code == 1 and 'only in A' in out and g(0x60) not in out.lower(), '--summary: counts without the instance lists')

        x = os.path.join(work, 'a.xml')
        r = subprocess.run([LD, a, x], capture_output=True, text=True)
        c.check(r.returncode == 2, 'a non-binary file: exit 2')
        r = subprocess.run([LD, a], capture_output=True, text=True)
        c.check(r.returncode == 2 and 'usage' in (r.stdout + r.stderr), 'one file only: exit 2 with the usage line')

        if '--game' in argv:
            sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
            import navkit
            levels = os.path.join(navkit.find_opfor(None), 'work', 'levels')
            lv = os.path.join(levels, 'mp_01')
            dist, pkg = os.path.join(lv, 'dist', 'levels', 'MP_01.dbx'), os.path.join(lv, 'pkg', 'levels', 'MP_01.dbx')
            dom = os.path.join(levels, 'mp_05_domination', 'dist', 'levels', 'MP_05_Domination.dbx')
            tdm = os.path.join(levels, 'mp_05_tdm', 'dist', 'levels', 'MP_05_TDM.dbx')
            if all(os.path.isfile(f) for f in (dist, pkg, dom, tdm)):
                code, d, _ = leveldiff(dist, dist, work=work)
                c.check(code == 0 and not d['changed'], 'mp_01 against itself: nothing different')
                # MOH's patch copy of MP_01.dbx is the same as its Dist copy: exit 0, every instance the same
                code, d, _ = leveldiff(dist, pkg, '--match-objects', work=work)
                c.check(code == 0 and d is not None and d['same_instances'] > 1000 and not d['changed']
                        and not d['only_in_a'] and not d['only_in_b'],
                        f'mp_01 Dist against Package: the same level ({d["same_instances"] if d else 0} same instances)')
                # one map, two modes: shared scenery, mode-specific spawns and objectives
                code, d, _ = leveldiff(dom, tdm, '--match-objects', work=work)
                c.check(code == 1 and d is not None and d['same_instances'] > 1000 and d['changed']
                        and d['only_in_a'] and d['only_in_b'],
                        f'mp_05 Domination against TDM: different ({d["same_instances"] if d else 0} same instances, '
                        f'{len(d["changed"]) if d else 0} changed, {len(d["only_in_a"]) if d else 0} only in Domination, '
                        f'{len(d["only_in_b"]) if d else 0} only in TDM)')
            else:
                c.check(False, 'mp_01 / mp_05_domination / mp_05_tdm not prepared (run the level loader or navkit.py prepare <level> once)')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
