r"""level.load against a saved snapshot: per level, the entity counts by category and team, shapes, collision,
terrain and water. Any change shows as a difference, so a loader change that moves or loses objects is
seen at once. After an intended change, review the differences and save a new snapshot with --update.

The snapshot is MOH's (recorded 2026-10-30 on the loader that reads a level's map common_mp_<nn> before common_mp
and the patch's copies first): one level per MP map, plus the DLC modes whose partition or terrain is only in the
patch or in their map's common bundle (mp_01_elimination, mp_02_koth, mp_03_elimination, mp_07_koth).

Without game data (first; --quick runs only these, 2026-10-31): water is read from the patch's terrain layer
(terrain.patch) before the base's, and falls back to the base's; a failed terrain unpack leaves no folder (no
partial folder taken as unpacked later) and its error carries FbrbTool's output; a successful one is renamed into
place. And level.load on a made-up Project OPFOR folder (partitions written as XML here and encoded to binary .dbx,
static groups, blueprints, stand-in Havok files and FbrbTool, height fields, water, NavKit samples): entity
categories (placed objects by their blueprint's object: ladders, dynamic objects, effects, objectives, destructible
buildings), teams and AI / human (alternate spawn points take their spawner's), area / outer area / route shapes,
static-group members (the patch's group file wins), world-space collision (blueprint mesh aliases, the blueprints
without collision), the patch's terrain bundle unpacked once and its height field and water winning, damaged terrain
or water files as notes with NavKit's samples as the terrain, geometry=False, a level prepared on first use, and a
missing partition refused.

  python test_level.py [level ...] [--update] [--quick]     default levels: DEFAULT (12 of the 26 MP levels)
"""
import collections
import json
import os
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from mohcore import dbx  # noqa: E402
from mohcore import level as levelmod  # noqa: E402
from mohcore import terrain as terrainmod  # noqa: E402

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


# ------------------------------------------------------------------------------------------------ made-up levels
# A made-up Project OPFOR folder (no game in it): partitions written as the readable XML (the form dbx.to_xml and
# `DbxEdit xml` show) and encoded to the binary .dbx the loader reads, static groups, blueprints, stand-in Havok
# files, terrain height fields, water and NavKit samples.

NUMBER_FIELDS = {'Transform', 'Transforms', 'Height'}       # 4-byte numbers; the other values are text or true/false
CONTAINER_TAGS = {'partition', 'instance', 'array', 'complex'}
ROT = ((0, 0, -1), (0, 1, 0), (1, 0, 0))                    # 90 degrees about y: local x -> world -z


def _varint(v):
    out = bytearray()
    while True:
        b, v = v & 0x7F, v >> 7
        out.append(b | 0x80 if v else b)
        if not v:
            return bytes(out)


def dbx_bytes(xml_text):
    """The binary .dbx of a partition given as XML (moh-dbx-tools FORMAT.md sections 1-2: the string table, then
    the elements)."""
    strings, index = [''], {'': 0}

    def s(t):
        if t not in index:
            index[t] = len(strings)
            strings.append(t)
        return _varint(index[t])

    def element(e):
        text = (e.text or '').strip()
        attrs = b''.join(s(k) + s(v) for k, v in e.attrib.items())
        head = s(e.tag)
        if e.get('name') in NUMBER_FIELDS and text:
            vals = [float(v) for v in text.split('/')]
            return (head + bytes([dbx.NUMBERS << 4 | len(e.attrib)]) + attrs + _varint(len(vals)) + _varint(4)
                    + struct.pack(f'>{len(vals)}f', *vals))
        if len(e) or e.tag in CONTAINER_TAGS:
            return head + bytes([dbx.CONTAINER << 4 | len(e.attrib)]) + attrs + b'\0' + b''.join(map(element, e)) + b'\0'
        if text in ('true', 'false'):
            return head + bytes([1 << 4 | len(e.attrib)]) + attrs + bytes([0, text == 'true'])
        return head + bytes([dbx.TEXT << 4 | len(e.attrib)]) + attrs + s(text)

    body = element(ET.fromstring(xml_text))
    blob, offs = b'', []
    for t in strings:
        offs.append(len(blob))
        blob += t.encode() + b'\0'
    return (dbx.MAGIC + bytes(8) + struct.pack('>II', 4 * len(strings) + len(blob), len(strings))
            + struct.pack(f'>{len(strings)}I', *offs) + blob + body)


def mat(x, y, z, rows=((1, 0, 0), (0, 1, 0), (0, 0, 1))):
    """A Transform's 16 values: rows right, up, forward, translation."""
    return '/'.join(str(v) for r in rows for v in (*r, 0)) + f'/{x}/{y}/{z}/1'


def inst(guid, typ, *fields):
    return f'<instance guid="{guid}" type="{typ}">' + ''.join(fields) + '</instance>'


def fld(name, value):
    return f'<field name="{name}">{value}</field>' if value != '' else f'<field name="{name}" />'


def ref(name, r):
    return f'<field ref="{r}" name="{name}" />'


def arr(name, *refs):
    return f'<array name="{name}">' + ''.join(f'<item ref="{r}" />' for r in refs) + '</array>'


def xform(x, y, z, rows=((1, 0, 0), (0, 1, 0), (0, 0, 1))):
    return f'<complex name="Transform">{mat(x, y, z, rows)}</complex>'


def partition(*instances):
    return '<partition guid="P" primaryInstance="P">' + ''.join(instances) + '</partition>'


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').write(data.encode() if isinstance(data, str) else data)


def heightfield_res(raw, lo=(-8.0, 0.0, -8.0), hi=(8.0, 100.0, 8.0)):
    """A .Heightfield-000.res (terrain.py's layout) of size x size u16 samples."""
    raw = np.asarray(raw, '<u2')
    return terrainmod.HEADER.pack(*lo, *hi, len(raw), 0, 0, 0, 0, 0, 0) + raw.tobytes()


def samples_v3(nx, nz, pitch, columns):
    """A NavKit sample file (navsample.py, version 3): columns[j * nx + i] = [(y, on terrain), ...], top first."""
    import navsample
    import navwalk
    rec = np.dtype([('y', '<f4'), ('n', 'i1', 3), ('f', 'u1'), ('h', '<u4', 3)])
    surfaces = [s for c in columns for s in c]
    r = np.zeros(len(surfaces), rec)
    r['y'] = [y for y, _ in surfaces]
    r['n'] = (0, 127, 0)
    r['h'][:, 0] = [navwalk.TERRAIN_SHAPE_VTABLE if t else 0x1234 for _, t in surfaces]
    head = navsample.HEADER.pack(b'ONS1', 3, pitch, 0.0, 0.0, nx, nz, 100.0, -10.0, 0.3, 0.0, 1.8, 0.1, 0)
    at = len(head) + r.nbytes
    return head + r.tobytes() + bytes(len(c) for c in columns) + struct.pack('<Q', at) + b'CNT3'


def blueprint(guid, obj_type, *extra, obj=None):
    """A blueprint partition: the blueprint instance and its object (obj_type None: the object is obj, elsewhere)."""
    if obj_type is None:
        return partition(inst(guid, 'Entity.ObjectBlueprint', ref('Object', obj)), *extra)
    return partition(inst(guid, 'Entity.ObjectBlueprint', ref('Object', guid + '_OBJ')),
                     inst(guid + '_OBJ', obj_type), *extra)


def make_level(root):
    """mp_t, the level under test, in work\\levels\\mp_t (pkg: the patch's files, dist: the base's)."""
    lvdir = os.path.join(root, 'work', 'levels', 'mp_t')
    pkg, dist = os.path.join(lvdir, 'pkg'), os.path.join(lvdir, 'dist')
    ro, gs, point = 'Entity.ReferenceObjectData', 'GameSharedResources.', 'Entity.AreaGeometryEntityData'

    def placed(guid, bp, x=0, y=0, z=0, rows=((1, 0, 0), (0, 1, 0), (0, 0, 1))):
        return inst(guid, ro, xform(x, y, z, rows), ref('ReferencedObject', bp))
    put(os.path.join(pkg, 'levels', 'MP_T.dbx'), dbx_bytes(partition(
        inst('TERR', gs + 'TerrainEntityData', xform(0, 30, 0), ref('TerrainAsset', 'terrains/mp_x/mp_x/HF')),
        inst('SP1', gs + 'SoldierSpawnEntityData', fld('Name', 'Spawn US'), xform(1, 2, 3), fld('Team', 'Team1'),
             fld('PlayerType', 'SSHumanPlayer'), arr('AlternativeSpawnPoints', 'alt1', 'ALT2', 'NOWHERE'),
             ref('Squad', 'null'), fld('Enabled', 'true')),
        inst('ALT1', gs + 'AlternateSpawnEntityData', xform(4, 2, 3)),
        inst('ALT2', gs + 'AlternateSpawnEntityData', xform(5, 2, 3), fld('Team', 'Team2')),
        inst('SP2', gs + 'SoldierSpawnEntityData', xform(9, 2, 9), fld('Team', 'Team2'), fld('PlayerType', 'SSAIPlayer')),
        inst('CP', gs + 'CapturePointEntityData', xform(0, 0, 0), fld('InitialOwnerTeam', 'TeamNeutral'),
             arr('Geometry', 'A1')),
        inst('CA', gs + 'CombatAreaTriggerEntityData', fld('Team', 'TeamUnknown'), arr('SurroundingGeometry', 'B1')),
        inst('PO', 'Entity.PatrolOrderEntityData', arr('RouteWaypoints', 'A1', 'ONE')),
        inst('AO', 'Entity.AreaOrderEntityData', arr('Geometry', 'L1', 'MISSING')),
        inst('A1', point, xform(0, 0, 0), ref('Next', 'A2'), fld('Height', '0')),
        inst('A2', point, xform(10, 0, 0), ref('Next', 'A3'), fld('Height', '4')),
        inst('A3', point, xform(10, 0, 10), ref('Next', 'a1'), fld('Height', '7')),
        inst('B1', point, xform(-50, 0, -50), ref('Next', 'B2')),
        inst('B2', point, xform(50, 0, -50), ref('Next', 'B3')),
        inst('B3', point, xform(0, 0, 50), ref('Next', 'B1')),
        inst('ONE', point, xform(3, 0, 3), ref('Next', 'null')),
        inst('L1', point, xform(1, 0, 1), ref('Next', 'L2')),
        inst('L2', point, xform(2, 0, 1)),
        inst('VS', gs + 'VehicleSpawnEntityData', xform(0, 0, 0)),
        inst('MO', gs + 'MissionObjectiveEntityData'),
        inst('TM', gs + 'TeamEntityData'),
        inst('SQ', gs + 'SquadData'),
        inst('RS', gs + 'RoundStartTimerEntityData'),
        inst('ODD', 'Entity.SomethingElse', '<complex name="Transform">1/0/0/0/0/1/0/0/0/0/1/0</complex>'),
        inst('RO_CRATE', ro, fld('Name', ''), xform(10, 0, 20, ROT), ref('ReferencedObject', 'objects/props/crate/CRATE')),
        placed('RO_CRATE2', 'Objects/Props/Crate/CRATE', -5, 1, 0),
        placed('RO_LADDER', 'objects/props/ladder/LAD'),
        placed('RO_HOUSE', 'objects/props/house/HOUSE'),
        placed('RO_WALL', 'objects/props/wall/WALL'),
        placed('RO_BOMB', 'objects/objects/mpobjective/bomb/BOMB'),
        placed('RO_FX', 'objects/fx/smoke/FX'),
        placed('RO_BARREL', 'objects/props/barrel/BAR'),
        placed('RO_GONE', 'objects/props/gone/X'),
        placed('RO_BROKEN', 'objects/props/broken/X'),
        placed('RO_DELETED', 'objects/props/deleted/X'),
        placed('RO_ALIAS', 'objects/props/crate_alias/CA', 0, 5, 0),
        placed('RO_NULL', 'null'),
        inst('RO_FLOAT', ro, ref('ReferencedObject', 'objects/props/crate/CRATE')))))      # no transform: no collision
    # the base's partition (the patch's copy wins), an old copy of the level's static group and a group of its own
    put(os.path.join(dist, 'levels', 'MP_T.dbx'), dbx_bytes(partition(inst('OLD', 'Old.Thing'))))
    put(os.path.join(pkg, 'levels', 'mp_t', 'trees_HavokAsset.dbx'), dbx_bytes(partition(
        inst('TREES', 'Physics.CompositeHavokAsset', f'<array name="Transforms">{mat(7, 0, 7)}</array>',
             arr('Assets', 'objects/tree/tree_havok/T1')))))
    put(os.path.join(dist, 'levels', 'mp_t', 'Trees_HavokAsset.dbx'), dbx_bytes(partition(
        inst('OLDTREES', 'Physics.CompositeHavokAsset', f'<array name="Transforms">{mat(1, 0, 1)}/{mat(2, 0, 2)}</array>',
             arr('Assets', 'objects/tree/tree_havok/T1', 'objects/tree/tree_havok/T1')))))
    put(os.path.join(dist, 'levels', 'mp_t', 'Rocks_HavokAsset.dbx'), dbx_bytes(partition(
        inst('ROCKS', 'Physics.CompositeHavokAsset', f'<array name="Transforms">{mat(1, 0, 0)}/{mat(2, 0, 0)}</array>',
             arr('Assets', 'objects/rock/rock_havok/R', 'objects/rock/rock_havok/R', 'objects/rock/rock_havok/R')),
        inst('EMPTY', 'Physics.CompositeHavokAsset', arr('Assets', 'objects/rock/rock_havok/R')))))
    # blueprints
    props = os.path.join(dist, 'objects', 'props')
    put(os.path.join(props, 'crate.dbx'), dbx_bytes(blueprint('CRATE', 'Entity.DynamicModelEntityData')))
    put(os.path.join(props, 'ladder.dbx'), dbx_bytes(blueprint('LAD', 'Entity.LadderEntityData')))
    put(os.path.join(props, 'house.dbx'), dbx_bytes(blueprint(
        'HOUSE', 'Entity.StaticModelEntityData', inst('HS', 'Entity.DestructionData', arr('HealthStates', 'HOUSE_OBJ')))))
    put(os.path.join(props, 'wall.dbx'), dbx_bytes(blueprint(
        'WALL', 'Entity.StaticModelEntityData', inst('WD', 'Entity.DestructionData', arr('HealthStates')))))
    put(os.path.join(dist, 'objects', 'objects', 'mpobjective', 'bomb.dbx'),
        dbx_bytes(blueprint('BOMB', 'Entity.DynamicModelEntityData')))
    put(os.path.join(dist, 'objects', 'fx', 'smoke.dbx'), dbx_bytes(partition(inst('FX', 'Entity.EffectEntityData'))))
    put(os.path.join(props, 'barrel.dbx'), dbx_bytes(blueprint('BAR', None, obj='objects/props/barrel_data/BD')))
    put(os.path.join(pkg, 'objects', 'props', 'barrel_data.dbx'), dbx_bytes(partition(inst('BD', 'Entity.BangerEntityData'))))
    put(os.path.join(props, 'broken.dbx'), b'not a dbx file')
    put(os.path.join(props, 'deleted.dbx'), b'')                       # a bundle's *deleted* entry
    # collision: stand-in Havok files (a stand-in reader gives their triangles) and a blueprint that reuses the
    # crate's mesh and collision (NavKit's converted XML)
    for p in ('objects/props/crate_havok.res', 'objects/props/house_havok.res', 'objects/tree/tree_havok.res'):
        put(os.path.join(dist, *p.split('/')), b'havok')
    put(os.path.join(props, 'crate_alias.xml'), '<instance guid="CA"><field ref="objects/props/crate_mesh/M" name="Mesh" /></instance>')
    # terrain: the base layer unpacked already (heights 0, one water triangle); the patch's bundle not yet (heights
    # 20 m, two water triangles), unpacked by a stand-in FbrbTool from the folder the "bundle" names
    terr = os.path.join(lvdir, 'terrain')
    put(os.path.join(terr, 'Terrains', 'mp_x', 'mp_x.Heightfield-000.res'), heightfield_res(np.zeros((4, 4))))
    water_mesh(terr, 'mp_x', 1)
    src = os.path.join(root, 'patch_terrain_source')
    put(os.path.join(src, 'Terrains', 'MP_X', 'MP_X.Heightfield-000.res'), heightfield_res(np.full((4, 4), 13107)))
    water_mesh(src, 'mp_x', 2)
    put(os.path.join(root, 'game', 'MP', 'dist', 'win32', 'levels', 'mp_t', 'terrain-00.fbrb'), 'base bundle')
    put(os.path.join(root, 'game', 'MP', 'dist', 'win32', 'patch', 'levels', 'mp_t', 'terrain-00.fbrb'), src)


def level_checks(check):
    """level.load on made-up levels: categories, teams, shapes, static groups, blueprints, collision, terrain, water."""
    import levelobjects
    import navkit
    tmp = tempfile.mkdtemp(prefix='level_load_test_')
    real = levelobjects.triangles, navkit.find_tool, navkit.prepare
    unit = np.array([[[0, 0, 0], [1, 0, 0], [0, 0, 1]]], np.float32)
    try:
        put(os.path.join(tmp, 'game', 'MP', 'MoHMPGame.exe'), b'')
        make_level(tmp)
        tool = os.path.join(tmp, 'fake_fbrbtool.py')
        put(tool, 'import shutil, sys\nshutil.copytree(open(sys.argv[2]).read(), sys.argv[3])\n')
        levelobjects.triangles = lambda path: None if 'house' in path else unit      # the house's packfile: unreadable
        navkit.find_tool = lambda root, exe, toolkit: [sys.executable, tool]
        logs = []
        lvl = levelmod.load('levels/MP_T', opfor=tmp, log=logs.append)
        by = {e.guid: e for e in lvl.entities if e.group is None}
        check(lvl.name == 'MP_T' and lvl.lv == 'mp_t' and 'OLD' not in by and len(by) == 38,
              f"the patch's partition (pkg) wins over the base's: {len(by)} entities of it")
        cats = {'SP1': 'soldier spawn', 'ALT1': 'soldier spawn', 'VS': 'vehicle spawn', 'MO': 'objective',
                'CP': 'capture point', 'CA': 'combat area', 'TM': 'team', 'SQ': 'squad', 'RS': 'round flow',
                'PO': 'ai order', 'AO': 'ai order', 'A1': 'area point', 'ODD': 'other', 'TERR': 'other',
                'RO_CRATE': 'dynamic object', 'RO_LADDER': 'ladder', 'RO_HOUSE': 'destructible',
                'RO_WALL': 'placed object', 'RO_BOMB': 'objective', 'RO_FX': 'effect', 'RO_BARREL': 'dynamic object',
                'RO_GONE': 'placed object', 'RO_BROKEN': 'placed object', 'RO_DELETED': 'placed object',
                'RO_NULL': 'placed object'}
        wrong = {g: by[g].category for g, c in cats.items() if g not in by or by[g].category != c}
        check(not wrong, f'categories by type, placed objects by their blueprint object (wrong: {wrong})')
        bt = {g: by[g].blueprint_type for g in ('RO_CRATE', 'RO_HOUSE', 'RO_BARREL', 'RO_FX', 'RO_GONE', 'RO_BROKEN')}
        check(bt == {'RO_CRATE': 'DynamicModelEntityData', 'RO_HOUSE': 'StaticModelEntityData',
                     'RO_BARREL': 'BangerEntityData', 'RO_FX': 'EffectEntityData', 'RO_GONE': None, 'RO_BROKEN': None},
              f'blueprint object types: local, in another file of another layer, the blueprint itself; none for a '
              f'missing, damaged or deleted file ({bt})')
        check([g for g, e in by.items() if e.destructible] == ['RO_HOUSE'],
              'destructible: a static model whose blueprint has health states (an empty list is not)')
        teams = {g: (by[g].team, by[g].ai) for g in ('SP1', 'ALT1', 'ALT2', 'SP2', 'CP', 'CA', 'VS')}
        check(teams == {'SP1': (1, False), 'ALT1': (1, False), 'ALT2': (2, None), 'SP2': (2, True), 'CP': (0, None),
                        'CA': (None, None), 'VS': (None, None)},
              f"teams and AI: own fields; an alternate spawn point takes its spawner's unless it has one ({teams})")
        sp1 = lvl.by_guid('sp1')
        check(sp1 is by['SP1'] and sp1.name == 'Spawn US' and sp1.position.tolist() == [1, 2, 3]
              and by['ODD'].matrix is None and by['ODD'].position is None and by['RO_CRATE'].name == ''
              and lvl.by_guid('nothing') is None, 'by_guid (any case), names, positions; 12 numbers are no Transform')
        check(sp1.refs() == [('AlternativeSpawnPoints/item', 'alt1'), ('AlternativeSpawnPoints/item', 'ALT2'),
                             ('AlternativeSpawnPoints/item', 'NOWHERE')], f'refs: every reference but null ({sp1.refs()})')
        check(by['RO_CRATE2'].blueprint == 'objects/props/crate' and by['RO_NULL'].blueprint is None,
              'blueprint paths in lower case; none for a null reference')
        shapes = {(lvl.entities[s.entity].guid, s.field): s for s in lvl.shapes}
        sa = shapes.get(('CP', 'Geometry'))
        check(len(lvl.shapes) == 4 and sa is not None and sa.kind == 'area' and sa.closed and sa.height == 4
              and sa.points.tolist() == [[0, 0, 0], [10, 0, 0], [10, 0, 10]],
              'an area: the point chain in order, closed where it comes back, the first non-zero height')
        so, sr = shapes.get(('CA', 'SurroundingGeometry')), shapes.get(('PO', 'RouteWaypoints'))
        sl = shapes.get(('AO', 'Geometry'))
        check(so is not None and so.kind == 'outer area' and so.closed and so.height == 0,
              'a surrounding area (outer area), no height')
        check(sr is not None and sr.kind == 'route' and not sr.closed and len(sr.points) == 3,
              'a route is open even where its chain loops; a one-point chain is no shape')
        check(sl is not None and not sl.closed and len(sl.points) == 2, 'an area chain that stops is open')
        members = sorted((e.name, e.group, e.partition, tuple(e.position.tolist())) for e in lvl.entities if e.group)
        check(members == [('rock', 'Rocks_HavokAsset.dbx', 'Rocks_HavokAsset', (1, 0, 0)),
                          ('rock', 'Rocks_HavokAsset.dbx', 'Rocks_HavokAsset', (2, 0, 0)),
                          ('tree', 'trees_HavokAsset.dbx', 'trees_HavokAsset', (7, 0, 7))],
              f"static groups: one member per placement - the patch's file wins, assets past the transforms and "
              f"instances without transforms skipped ({members})")
        check(all(e.category == 'group member' and e.blueprint in ('objects/tree/tree', 'objects/rock/rock')
                  for e in lvl.entities if e.group), 'group members: their blueprint without _havok')
        mesh = {lvl.entities[m.entity].guid.split('#')[0]: m.triangles for m in lvl.meshes}
        check(sorted(mesh) == ['RO_ALIAS', 'RO_CRATE', 'RO_CRATE2', 'TREES'] and len(lvl.meshes) == 4,
              f'collision for every placement with a transform and a readable Havok asset, aliases too ({sorted(mesh)})')
        check(np.allclose(mesh['RO_CRATE'][0], [[10, 0, 20], [10, 0, 19], [11, 0, 20]])
              and np.allclose(mesh['RO_CRATE2'][0], unit[0] + [-5, 1, 0])
              and np.allclose(mesh['RO_ALIAS'][0], unit[0] + [0, 5, 0]) and mesh['RO_CRATE'].dtype == np.float32,
              'collision triangles in world space (rotation rows, then translation)')
        check(lvl.missing_collision == ['objects/fx/smoke', 'objects/objects/mpobjective/bomb', 'objects/props/barrel',
                                        'objects/props/broken', 'objects/props/deleted', 'objects/props/gone',
                                        'objects/props/house', 'objects/props/ladder', 'objects/props/wall',
                                        'objects/rock/rock'],
              f'blueprints without collision, once each, sorted; an unreadable packfile among them ({lvl.missing_collision})')
        hf = lvl.heightfield
        check(hf is not None and abs(float(hf.height(0.0, 0.0)) - 20) < 0.01 and any('unpacking' in m for m in logs),
              "terrain: the patch's bundle unpacked on first use and its height field wins; the terrain entity's "
              'transform is not applied')
        check(lvl.terrain is not None and lvl.terrain[0].shape == (4, 3) and len(lvl.terrain[1]) == 2
              and np.allclose(lvl.terrain[0][:, 1], 20), 'the terrain mesh from the height field (every 2nd sample)')
        check(lvl.water is not None and len(lvl.water) == 2 and not lvl.notes, f"water: the patch layer's ({lvl.notes})")
        logs.clear()
        again = levelmod.load('mp_t', opfor=tmp, log=logs.append, terrain_stride=1)
        check(not any('unpacking' in m for m in logs) and again.terrain[0].shape == (16, 3) and len(again.terrain[1]) == 18,
              'a second load unpacks nothing; terrain_stride 1: every sample')
        w = levelmod.water_of(tmp, 'mp_t', log=logs.append)
        h = levelmod.heightfield_of(tmp, 'mp_t', log=logs.append)
        check(w is not None and len(w) == 2 and h is not None and abs(float(h.height(1.0, 1.0)) - 20) < 0.01,
              "water_of and heightfield_of find the terrain's name in the partition")
        light = levelmod.load('mp_t', opfor=tmp, log=logs.append, geometry=False)
        check(not any(e.group for e in light.entities) and not light.meshes and light.terrain is None
              and light.water is None and len(light.shapes) == 4 and light.by_guid('ALT1').team == 1,
              'geometry=False: entities, teams and shapes only')

        # a level whose terrain layer holds a damaged height field and water file: notes, and NavKit's samples
        s = os.path.join(tmp, 'work', 'levels', 'mp_s')
        put(os.path.join(s, 'dist', 'levels', 'MP_S.dbx'), dbx_bytes(partition(inst('X', 'Entity.SomethingElse'))))
        put(os.path.join(s, 'terrain', 'Terrains', 'mp_s', 'mp_s.Heightfield-000.res'), b'\0' * 64)
        put(os.path.join(s, 'terrain', 'Terrains', 'mp_s', 'mp_s.Water.Mesh.res'), struct.pack('<IIII', 1, 0, 0, 7))
        put(os.path.join(tmp, 'game', 'MP', 'dist', 'win32', 'levels', 'mp_s', 'terrain-00.fbrb'), 'bundle')
        ground = [(1.0, True)]
        cols = [ground, [(9.0, False), (1.5, True)], ground,           # j = 0: an object above the terrain at i = 1
                ground, ground, ground,
                [(10.0, True)], ground, [(3.0, False)]]               # j = 2: a cliff at i = 0, no terrain at i = 2
        put(os.path.join(tmp, 'work', 'navsample', 'mp_s_navkit.bin'), samples_v3(3, 3, 2.0, cols))
        ls = levelmod.load('mp_s', opfor=tmp, log=logs.append, terrain_stride=1)
        notes = ' / '.join(ls.notes)
        check(ls.heightfield is None and 'terrain height field not read' in notes and 'water not read' in notes,
              f'a damaged height field and water file are notes, not errors ({notes})')
        v, t = ls.terrain if ls.terrain is not None else (np.zeros((0, 3)), np.zeros((0, 3)))
        check(len(v) == 9 and np.allclose(v[:, 1], [1, 1.5, 1, 1, 1, 1, 10, 1, 0]) and np.allclose(v[4, [0, 2]], [2, 2]),
              "terrain from NavKit's samples: per column the highest terrain surface (an object above it ignored)")
        check(len(t) == 4 and not (t == 6).any() and not (t == 8).any(),
              f'no triangles across a cliff (over 4 m) or into a column without terrain ({len(t)})')

        # a level not prepared yet: NavKit's prepare step runs once; a level without its partition is refused
        put(os.path.join(tmp, 'game', 'MP', 'dist', 'win32', 'levels', 'mp_p', 'level-00.fbrb'), 'bundle')
        prepared = []

        def prepare(pr, lv, log=print):
            prepared.append(lv)
            put(os.path.join(pr.root, 'work', 'levels', lv, 'dist', 'levels', 'MP_P.dbx'),
                dbx_bytes(partition(inst('S', 'GameSharedResources.SoldierSpawnEntityData'))))
        navkit.prepare = prepare
        logs.clear()
        lp = levelmod.load('mp_p', opfor=tmp, log=logs.append, geometry=False)
        check(prepared == ['mp_p'] and any('not prepared yet' in m for m in logs)
              and [e.category for e in lp.entities] == ['soldier spawn'], 'a level not prepared yet is prepared on first use')
        os.makedirs(os.path.join(tmp, 'work', 'levels', 'mp_q', 'dist'))
        try:
            levelmod.load('mp_q', opfor=tmp, log=logs.append)
            why = None
        except FileNotFoundError as e:
            why = str(e)
        check(why is not None and 'partition not found' in why, f'a level without its partition: FileNotFoundError ({why})')
    finally:
        levelobjects.triangles, navkit.find_tool, navkit.prepare = real
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
        level_checks(check)
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
