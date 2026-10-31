r"""Tests of the stock-MP AI authoring tool.

  python tests\test_stockmp.py           quick: synthetic levels built in memory (no game data): the DBX writer, the
                                         number kinds, authoring a Conquest and a Rush layer, the configuration
                                         checks, the SectorOffsets asset, MOH's game layout (GameCopy: MP dist
                                         and its patch, Output; a level without a bundle refused with the reason),
                                         install / revert on a fake game folder (all targets checked first), the
                                         configuration hash validate checks, and DbxEdit's check and round trip
  python tests\test_stockmp.py --game    + Project OPFOR's game copy: the DBX writer on every file of mp_01's level
                                         bundles (work\levels\mp_01) byte for byte, and that MoHMPGame.exe has no AI
                                         types (AreaOrderEntityData, HasAI, PathfindingTypes, PathdataSectorOffsets):
                                         MOH's MP exe has no AI runtime, so no MOH level is generated, built or
                                         validated here (the layer's BFBC2 game test built Atacama Conquest)
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
SUITE = os.path.dirname(TOOL)
sys.path[:0] = [TOOL, os.path.join(SUITE, 'core')]

import author  # noqa: E402
import config as cfgmod  # noqa: E402
import dbxbin  # noqa: E402
import levelmodel  # noqa: E402
import pipeline  # noqa: E402
from mohcore import dbx as coredbx  # noqa: E402

FAILED = []


def check(ok, what):
    print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)
    if not ok:
        FAILED.append(what)


# ------------------------------------------------------------------------------------------ synthetic files

class B:
    """Builds a binary DBX tree with dbxbin elements."""

    def __init__(self):
        self.d = dbxbin.Dbx()
        self.d.intern('')

    def el(self, tag, kind, attrs=(), text=None, raw=b'', children=None):
        e = dbxbin.Element(self.d, self.d.intern(tag), kind)
        e.attrs = [(self.d.intern(k), self.d.intern(v)) for k, v in attrs]
        if kind == dbxbin.TEXT:
            e.text_i = self.d.intern(text or '')
        elif kind == dbxbin.CONTAINER:
            e.children = list(children or [])
        else:
            e.raw = raw
        return e

    def text(self, name, value):
        return self.el('field', dbxbin.TEXT, [('name', name)], text=value)

    def ref(self, name, value):
        return self.el('field', dbxbin.TEXT, [('ref', value), ('name', name)], text='')

    def bool(self, name, v):
        return self.el('field', 6, [('name', name)], raw=bytes([1, 1 if v else 0]))

    def num(self, name, values, kinds, tag='field'):
        body = b''.join(struct.pack('>f' if k == 'f' else '>i', v) for v, k in zip(values, kinds))
        return self.el(tag, dbxbin.NUMBERS, [('name', name)], raw=dbxbin._varint_bytes(len(values)) + dbxbin._varint_bytes(4) + body)

    def transform(self, pos):
        v = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, pos[0], pos[1], pos[2], 0]
        return self.num('Transform', v, 'f' * 16, tag='complex')

    def array(self, name, refs):
        if not refs:
            return self.el('array', dbxbin.TEXT, [('name', name)], text='')
        return self.el('array', dbxbin.CONTAINER, [('name', name)],
                       children=[self.el('item', dbxbin.TEXT, [('ref', r)], text='') for r in refs])

    def inst(self, guid, typ, fields):
        return self.el('instance', dbxbin.CONTAINER, [('guid', guid), ('type', typ)], children=fields)

    def event(self, src, se, tgt, te):
        return self.el('complex', dbxbin.CONTAINER, [], children=[
            self.ref('Source', src.lower()), self.ref('Target', tgt.lower()),
            self.el('complex', dbxbin.CONTAINER, [('name', 'SourceEvent')], children=[self.text('Name', se), self.num('Id', [0], 'i')]),
            self.el('complex', dbxbin.CONTAINER, [('name', 'TargetEvent')], children=[self.text('Name', te), self.num('Id', [0], 'i')])])

    def done(self, part_guid, instances):
        self.d.roots = [self.el('partition', dbxbin.CONTAINER, [('guid', part_guid), ('primaryInstance', instances[0].attr('guid')),
                                                               ('exportMode', 'All')], children=instances)]
        data = dbxbin.write(self.d)
        return data


G = lambda n: f'00000000-0000-4000-8000-{n:012d}'


def synthetic_level(mode='conquest'):
    b = B()
    sp, alt, hp = G(1), G(2), G(3)
    cps = [G(10), G(11), G(12)]
    aabb = G(20)
    spawner = b.inst(sp, 'GameSharedResources.SoldierSpawnEntityData', [
        b.array('Components', []), b.num('Enumeration', [0], 'i'), b.text('Name', 'stock spawn'), b.transform((0, 10, 0)),
        b.bool('Enabled', True), b.text('Team', 'Team1'), b.num('Amount', [1], 'i'), b.num('SpawnDelay', [15.0], 'f'),
        b.num('InitializedSpawnDelay', [0], 'i'), b.bool('AllowMulipleSpawn', False), b.bool('Immortal', False),
        b.bool('FakeImmortal', False), b.array('Vehicle', []), b.array('Squad', []), b.array('AlternativeSpawnPoints', [alt.lower()]),
        b.ref('SoldierKit', 'objects/soldiers/us/assault/e10ee50d-47fa-441b-8dfe-249ac41768d8'),
        b.num('MeshShaderSetNumber', [0], 'i'), b.ref('Template', 'null'), b.text('PlayerType', 'SSAiPlayer'),
        b.num('HumanTargetPreference', [-1.0], 'f'), b.num('LodDistance', [125.0], 'f'), b.text('LocationNameSid', 'X'),
        b.bool('UseAsAlternateSpawnPoint', False), b.bool('TryToSpawnAwayFromSight', False),
        b.bool('SpawnSoldierFromBeginning', False), b.text('OnSpottedType', 'OSPlayer'), b.bool('SoldierCreationPoint', False),
        b.text('SoldierSquadId', 'SquadNone'), b.num('SpawnEnemyRadius', [20.0], 'f')])
    alt_i = b.inst(alt, 'GameSharedResources.AlternateSpawnEntityData', [b.transform((2, 0, 2))])
    insts = [spawner, alt_i]
    owners = ['Team1', 'Team2', 'TeamNeutral']
    poss = [(0, 0, 0), (200, 0, 0), (100, 0, 50)]
    for g, o, p, lab in zip(cps, owners, poss, ['US', 'RU', 'A']):
        insts.append(b.inst(g, 'GameSharedResources.CapturePointEntityData', [
            b.text('Name', 'cp ' + lab), b.transform(p), b.array('Spawners', [sp.lower()] if o == 'Team1' else []),
            b.array('Geometry', []), b.text('ControlPointName', lab), b.text('InitialOwnerTeam', o)]))
    insts.append(b.inst(aabb, 'Entity.AabbGeometryEntityData', [
        b.array('Components', []), b.num('Enumeration', [0], 'i'), b.text('Name', ''), b.transform((5, 5, 5)),
        b.el('complex', dbxbin.NUMBERS, [('name', 'Extents')], raw=dbxbin._varint_bytes(4) + dbxbin._varint_bytes(4) + struct.pack('>fffI', 1.5, 2.5, 3.5, 0xE6464660)),
        b.num('Weight', [1], 'i')]))
    insts.append(b.inst(hp, 'GameSharedResources.HumanPlayerEntityData', [b.text('Name', 'hp'), b.transform((0, 0, 0))]))
    events = [b.event(hp, 'OnPlayerEnter', cps[0], 'Show')]
    extra = []
    if mode == 'rush':
        m = [G(30 + i) for i in range(4)]
        mt = [G(40), G(41)]
        for k, g in enumerate(m):
            extra.append(b.inst(g, 'Entity.ReferenceObjectData', [
                b.text('Name', f'mcom {k}'), b.transform((50 * k, 0, 10)),
                b.ref('ReferencedObject', 'objects/objects/mpobjective/1d6bd3f4-0000-4000-8000-000000000001')]))
        for k, g in enumerate(mt):
            extra.append(b.inst(g, 'GameSharedResources.MultipleTriggerEntityData', [
                b.text('Name', f'stage {k} taken'), b.num('TriggerAmount', [2], 'i'), b.bool('Enabled', k == 0)]))
        events += [b.event(m[0], 'onDestroyed', mt[0], 'Trigger'), b.event(m[1], 'onDestroyed', mt[0], 'Trigger'),
                   b.event(m[2], 'onDestroyed', mt[1], 'Trigger'), b.event(m[3], 'onDestroyed', mt[1], 'Trigger'),
                   b.event(mt[0], 'OnTrigger', mt[1], 'Enable')]
    insts += extra
    gm = 'gamemodes/conquest/a4c882e4-2e29-46fb-8cb0-8a1789059aad' if mode == 'conquest' else 'gamemodes/goldrush/3eb0392d-dc32-4667-bd0e-9e6051144a1f'
    level = b.inst(G(99), 'GameSharedResources.LevelData', [
        b.text('Name', 'Levels/TEST_01'), b.array('Objects', [i.attr('guid').lower() for i in insts]),
        b.el('array', dbxbin.CONTAINER, [('name', 'EventConnections')], children=events),
        b.array('PathfindingTypes', []), b.bool('HasAI', False),
        b.el('complex', dbxbin.CONTAINER, [('name', 'LevelDescription')], children=[
            b.ref('GameMode', gm), b.text('FactionTeam1', 'FactionUS'), b.text('FactionTeam2', 'FactionRUS'),
            b.text('AttackerTeam', 'Team1' if mode == 'rush' else 'TeamNeutral')])])
    return b.done(G(98), [level] + insts)


def synthetic_donor():
    b = B()
    order = b.inst(G(500), 'GameSharedResources.AreaOrderEntityData', [
        b.array('Components', []), b.num('Enumeration', [0], 'i'), b.text('Name', 'donor'), b.transform((1, 1, 1)),
        b.bool('Enabled', True), b.array('Squads', []), b.array('Soldiers', [G(501).lower()]), b.num('IssueOrderDelay', [1.5], 'f'),
        b.num('OnCompleteTimeOut', [0], 'i'), b.num('OnFailedTimeOut', [0], 'i'), b.bool('IsUrgent', False),
        b.bool('ReissueIfSoldierIsKilled', False), b.text('ReadinessOnStart', 'RLReady'), b.text('AllowStunAnimations', 'SASNoChange'),
        b.text('ReadinessOnComplete', 'RLNone'), b.bool('UnspawnOnComplete', False), b.text('ReadinessOnFail', 'RLNone'),
        b.bool('UnspawnOnFail', False), b.text('Stance', 'SONoChange'), b.text('SpeedState', 'SSONoChange'),
        b.text('AimState', 'AsoNoChange'), b.array('Area', [G(502).lower()]), b.text('AreaType', 'AreaHoldGround'),
        b.num('AnimationIndex', [-1], 'i'), b.ref('IdleAnimation', 'null')])
    trig = b.inst(G(510), 'GameSharedResources.AreaTriggerEntityData', [
        b.array('Components', []), b.num('Enumeration', [0], 'i'), b.text('Name', ''), b.transform((0, 0, 0)), b.bool('Enabled', True),
        b.num('Delay', [0.5], 'f'), b.bool('RunOnce', True), b.array('Soldiers', []), b.array('Geometry', [G(502).lower()]),
        b.ref('ObjectToFollow', 'null'), b.text('Include', 'ATPlayers'), b.num('Radius', [2.5], 'f'),
        b.num('InsideAreaEventRepeatTime', [0], 'i'), b.bool('OneInsideAreaEventPerSoldier', False),
        b.bool('TriggerOnlyOnLeave', False), b.bool('TriggerOnControllable', False)])
    return b.done(G(599), [order, trig])


KITS = {f'{f}/{k}': f'objects/soldiers/{f}/{k}/{G(700 + i).lower()}' for i, (f, k) in
        enumerate((f, k) for f in ('us', 'ru') for k in cfgmod.KIT_NAMES)}


def dbxedit(*args):
    exe = pipeline.DBX
    if not os.path.isfile(exe):
        return None
    r = subprocess.run([exe, *map(str, args)], capture_output=True, text=True, errors='replace')
    return r.returncode, r.stdout + r.stderr


# ------------------------------------------------------------------------------------------ quick tests

def quick(tmp):
    lvl = synthetic_level('conquest')
    check(dbxbin.write(dbxbin.parse(lvl)) == lvl, 'DBX writer: a synthetic level comes back byte for byte')
    check(coredbx.parse(lvl).instances[0].attr('type') == 'GameSharedResources.LevelData',
          'the suite\'s DBX reader reads the written file')
    d = dbxbin.parse(lvl)
    d.intern('a new string')
    out = dbxbin.write(d)
    d2 = dbxbin.parse(out)
    check(d2.strings[-1] == 'a new string' and coredbx.to_xml(out) == coredbx.to_xml(lvl),
          'DBX writer: a rebuilt string table reads as the same tree')

    donor = synthetic_donor()
    k = author.NumberKinds(dbxbin.parse(lvl), dbxbin.parse(donor))
    check(k.kind('HumanTargetPreference') == 'f' and k.kind('AnimationIndex') == 'i' and k.kind('Amount') == 'i'
          and k.kind('SpawnDelay') == 'f' and k.kind('OnCompleteTimeOut') is None,
          'number kinds: -1.0 is a decimal, -1 an integer (negative integers count as evidence), 0 tells nothing')

    # conquest layer
    m = levelmodel.Model(dbxbin.parse(lvl))
    check(m.mode == 'conquest' and [cp.label for cp in m.flags()] == ['A'] and len(m.bases('Team1')) == 1
          and m.ground_points(flag=G(10).upper()) == [(2.0, 0.0, 2.0)],
          'level model: mode, flags, bases and ground spawn points')
    c = cfgmod.generate(m, 'test_01', KITS, {'infantry': None, 'vehicle': None}, bots_per_team=3)
    check(cfgmod.check(c) == [] and len(c['spawners']) == 6 and len(c['orders']) == 2,
          f'generator: 3 bots per team, one order per team and flag ({len(c["spawners"])} spawners, {len(c["orders"])} orders)')
    d = dbxbin.parse(lvl)
    rep = author.Author(d, dbxbin.parse(donor), c, levelmodel.Model(d)).build()
    data = dbxbin.write(d)
    f = coredbx.parse(data)
    inst = {e.attr('guid').upper(): e for e in f.instances}
    lv = inst[G(99)]
    objs = [i.attr('ref').upper() for i in lv.child('Objects').children]
    new = [g for g in inst if g not in {G(n) for n in (1, 2, 3, 10, 11, 12, 20, 99)}]
    check(set(new) <= set(objs) and len(objs) == len(set(objs)), f'authored: {len(new)} new instances, each in Objects once')
    check(coredbx.value(lv.child('HasAI')) == 'true' and len(lv.child('PathfindingTypes').children) == 2,
          'authored: HasAI true, PathfindingTypes infantry + vehicles')
    sp_g = dbxbin.new_guid(author.NS, 'test_01', 'spawner', 't1_00')
    sp = inst[sp_g]
    size, vals = coredbx.numbers(sp.child('Amount'))
    _s2, sd = coredbx.numbers(sp.child('SpawnDelay'))
    check(sp.child('Template').attr('ref') == cfgmod.TEMPLATE and coredbx.value(sp.child('PlayerType')) == 'SSAiPlayer'
          and struct.unpack('>i', vals[0])[0] == 0 and struct.unpack('>f', sd[0])[0] == 15.0
          and sp.child('AlternativeSpawnPoints').kind == coredbx.TEXT,
          'authored spawner: template, AI player, Amount integer 0, SpawnDelay decimal 15, empty array kept as DICE stores it')
    check(coredbx.value(inst[G(1)].child('PlayerType')) == 'SSHumanPlayer', 'stock SSAiPlayer spawner made SSHumanPlayer')
    evs = [(c_.child('Source').attr('ref'), coredbx.value(c_.child('SourceEvent').child('Name')),
            coredbx.value(c_.child('TargetEvent').child('Name'))) for c_ in lv.child('EventConnections').children]
    check(sum(1 for _s, se, te in evs if se == 'OnSpawn' and te == 'IssueOrder') == 6
          and sum(1 for _s, se, _te in evs if se == 'OnPlayerEnter') == 3
          and sum(1 for _s, se, _te in evs if se == 'OnEnter') == 2,
          f'authored events: OnSpawn x6, OnPlayerEnter x2 (+1 stock), trigger OnEnter x2 ({rep.get("event connections")} added)')
    a = dbxbin.new_guid(author.NS, 'test_01', 'area', 't1_flag_A')
    ext = coredbx.numbers(inst[a].child('Extents'))[1]
    check(struct.unpack('>f', ext[0])[0] == 8.0 and ext[3] == struct.pack('>I', 0xE6464660),
          'order area: half extents set, the padding word kept')
    # deterministic
    d3 = dbxbin.parse(lvl)
    author.Author(d3, dbxbin.parse(donor), c, levelmodel.Model(d3)).build()
    check(dbxbin.write(d3) == data, 'authoring is deterministic (same configuration, same bytes)')
    p_l, p_o = os.path.join(tmp, 'TEST_01.dbx'), os.path.join(tmp, 'orig.dbx')
    open(p_l, 'wb').write(data)
    open(p_o, 'wb').write(lvl)
    r = dbxedit('check', p_l)
    if r is not None:
        check(' 0 error(s)' in r[1], 'DbxEdit check: the authored synthetic level has no errors')
        r = dbxedit('roundtrip', p_l)
        check('1 identical' in r[1], 'DbxEdit roundtrip: identical')
        dbxedit('xml', p_l)
        r = dbxedit('import', p_l[:-4] + '.xml', os.path.join(tmp, 'back.dbx'), '--like', p_l)
        check(r[0] == 0 and open(os.path.join(tmp, 'back.dbx'), 'rb').read() == data,
              'DbxEdit xml -> import gives the authored file back')
    else:
        check(False, 'DbxEdit.exe not built (run moh-dbx-tools\\build.bat)')

    # minimal layer
    cm = cfgmod.generate(m, 'test_01', KITS, {}, layer='minimal', stock_spawners='keep')
    d4 = dbxbin.parse(lvl)
    author.Author(d4, dbxbin.parse(donor), cm, levelmodel.Model(d4)).build()
    f4 = coredbx.parse(dbxbin.write(d4))
    check(len(f4.instances) == len(coredbx.parse(lvl).instances)
          and coredbx.value(f4.instances[0].child('HasAI')) == 'true'
          and coredbx.value({e.attr('guid').upper(): e for e in f4.instances}[G(1)].child('PlayerType')) == 'SSAiPlayer',
          'minimal layer (experiment 0): HasAI and PathfindingTypes only, stock spawners kept')

    # rush
    rl = synthetic_level('rush')
    mr = levelmodel.Model(dbxbin.parse(rl))
    st = mr.rush_stages()
    check([s.trigger for s in st] == [G(40).upper(), G(41).upper()] and [len(s.mcoms) for s in st] == [2, 2],
          'Rush stages read from the stock logic (M-COM onDestroyed -> stage trigger, chained by Enable)')
    cr = cfgmod.generate(mr, 'test_01', KITS, {}, rush_bots=(2, 2))
    check(cfgmod.check(cr) == [] and len(cr['stages']) == 1 and {s['stage'] for s in cr['spawners']} == {0, 1},
          'generator, Rush: spawners per stage and one stage switch')
    dr = dbxbin.parse(rl)
    author.Author(dr, dbxbin.parse(donor), cr, levelmodel.Model(dr)).build()
    fr = coredbx.parse(dbxbin.write(dr))
    ir = {e.attr('guid').upper(): e for e in fr.instances}
    later = [s for s in cr['spawners'] if s['stage'] == 1]
    check(all(coredbx.value(ir[dbxbin.new_guid(author.NS, 'test_01', 'spawner', s['id'])].child('Enabled')) == 'false' for s in later),
          'Rush: later-stage spawners start disabled')
    lvr = next(e for e in fr.instances if e.attr('type').endswith('LevelData'))
    stock = {e.attr('guid').upper() for e in coredbx.parse(rl).instances}
    sw = [(c_.child('Source').attr('ref').upper(), coredbx.value(c_.child('TargetEvent').child('Name')))
          for c_ in lvr.child('EventConnections').children if c_.child('Target').attr('ref').upper() not in stock]
    from collections import Counter
    n = Counter(te for s, te in sw if s == G(40).upper())
    n0 = sum(1 for s in cr['spawners'] if s['stage'] == 0)
    n1 = sum(1 for s in cr['spawners'] if s['stage'] == 1)
    o0 = sum(1 for o in cr['orders'] if o['stage'] == 0)
    o1 = sum(1 for o in cr['orders'] if o['stage'] == 1)
    check(n['KillSoldier'] == n0 and n['Disable'] == n0 + o0 and n['Enable'] == n1 + o1 and n['IssueOrder'] == o1,
          f'Rush: the stage trigger disables / kills stage 0 and enables stage 1 ({dict(n)})')

    # configuration checks
    bad = json.loads(json.dumps(c))
    bad['orders'][0]['soldiers'] = ['t2_00']
    check(any('another team' in p for p in cfgmod.check(bad)), 'configuration check: an order listing the other team is refused')
    bad = json.loads(json.dumps(c))
    bad['spawners'][0]['kit'] = 'pilot'
    check(any('kit' in p for p in cfgmod.check(bad)), 'configuration check: an unknown kit is refused')

    # SectorOffsets asset
    b = B()
    so = b.inst(G(800), 'GameSharedResources.PathdataSectorOffsets', [
        b.text('Name', 'Levels/SP_X/Precomputed/Infantry/SectorOffsets'), b.text('MemoryReportGroup', 'MemoryReportGroup_default'),
        b.num('XOffset', [-2], 'i'), b.num('ZOffset', [-5], 'i')])
    ref = os.path.join(tmp, 'so.dbx')
    open(ref, 'wb').write(b.done(G(801), [so]))
    raw, pg, ig = pipeline.sector_asset(ref, 'TEST_01', 'Infantry', 'test_01', (-9, -4))
    e = coredbx.parse(raw).instances[0]
    check(coredbx.value(e.child('Name')) == 'Levels/TEST_01/Precomputed/Infantry/SectorOffsets'
          and struct.unpack('>i', coredbx.numbers(e.child('XOffset'))[1][0])[0] == -9
          and struct.unpack('>i', coredbx.numbers(e.child('ZOffset'))[1][0])[0] == -4 and e.attr('guid') == ig,
          'SectorOffsets asset: name, offsets as 4-byte integers, OPFOR\'s guids')

    # MOH's game layout (mohcore.gamecopy): MP\dist\win32 and its patch layer, MP\Output\win32
    import stockmp
    root = os.path.join(tmp, 'opfor')
    game = os.path.join(root, 'game')
    mpd = os.path.join(game, 'MP', 'dist', 'win32')
    for top, lv in ((mpd, 'test_01'), (os.path.join(mpd, 'patch'), 'test_01'), (mpd, 'common_mp_03')):
        f = os.path.join(top, 'levels', lv, 'level-00.fbrb')
        os.makedirs(os.path.dirname(f))
        open(f, 'wb').write(b'stock bundle' if top == mpd else b'stock patch bundle')
    open(os.path.join(mpd, 'package.mft'), 'w').write(
        'BundleSource game:levels/test_01/level-00.fbrb\nBundleTarget game:patch/levels/test_01/level-00.fbrb\n'
        'BundleTarget game:levels/test_01/level-00.fbrb\nBundleSource game:levels/test_03/level-00.fbrb\n'
        'BundleTarget game:levels/common_mp_03/level-00.fbrb\n')
    paths = pipeline.Paths('test_01', opfor=root, work=os.path.join(root, 'work', 'stockmp'))
    rel = lambda p: os.path.relpath(p, root).replace(os.sep, '/')
    check([(v, rel(p)) for v, p in paths.bundles()] == [('dist', 'game/MP/dist/win32/levels/test_01/level-00.fbrb'),
                                                        ('patch', 'game/MP/dist/win32/patch/levels/test_01/level-00.fbrb')]
          and paths.loaded_bundle()[0] == 'patch', 'MOH layout: the level\'s bundles in MP\\dist\\win32 and its patch (the one loaded)')
    check(rel(paths.out_bundle('patch')) == 'work/stockmp/test_01/out/game/MP/dist/win32/patch/levels/test_01/level-00.fbrb'
          and rel(paths.out_pathdata('infantry')) == 'work/stockmp/test_01/out/game/MP/Output/win32/ai/levels/test_01/infantry/pathdata.hpd',
          'the build stages at the game\'s own MOH paths (out\\game mirrors the game folder)')
    check(pipeline.loads_sp_common(paths) is False, 'package.mft read through GameCopy: test_01 does not load sp_common')
    for lv, why in (('test_03', 'common_mp_03'), ('test_02', 'not a level')):
        try:
            pipeline.Paths(lv, opfor=root, work=paths.work).loaded_bundle()
            check(False, f'{lv}: no bundle of its own is refused')
        except pipeline.NoLevelBundle as e:
            check(why in str(e), f'{lv}: no bundle of its own is refused with the reason ({why})')
    try:
        stockmp.main(['stockmp.py', 'summary', 'test_03', '--opfor', root, '--work', paths.work])
        check(False, 'summary of a level without a bundle: a clear error')
    except SystemExit as e:
        check('no test_03\\level-00.fbrb' in str(e.code) and 'common_mp_03' in str(e.code),
              'summary of a level without a bundle: a clear error, no IndexError')

    # install / revert on the fake game folder
    outs = [paths.out_bundle('dist'), paths.out_bundle('patch')]
    tgts = [paths.bundle('dist'), paths.bundle('patch')]
    for f in outs:
        os.makedirs(os.path.dirname(f))
        open(f, 'wb').write(b'new bundle')
    stockmp.install(paths, ['--plan'])
    check(open(tgts[1], 'rb').read() == b'stock patch bundle', 'install --plan changes nothing')
    stockmp.install(paths, ['--apply'])
    check(all(open(t, 'rb').read() == b'new bundle' for t in tgts) and open(tgts[1] + stockmp.BACKUP, 'rb').read() == b'stock patch bundle',
          'install --apply copies the build and keeps the originals')
    for f in outs:
        open(f, 'wb').write(b'newer bundle')
    open(tgts[1], 'wb').write(b'changed by hand')                 # the patch's copy changed after the install
    for mode, verb in (('--apply', 'installed'), ('--revert', 'reverted')):
        try:
            stockmp.install(paths, [mode])
            check(False, f'install {mode}: a file changed after the install stops it')
        except SystemExit as e:
            check(open(tgts[0], 'rb').read() == b'new bundle' and open(tgts[1], 'rb').read() == b'changed by hand'
                  and f'nothing {verb}' in str(e.code),
                  f'install {mode}: every target checked first - one changed after the install, nothing touched (dist not newer than patch)')
    open(tgts[1], 'wb').write(b'new bundle')
    stockmp.install(paths, ['--revert'])
    check(open(tgts[0], 'rb').read() == b'stock bundle' and open(tgts[1], 'rb').read() == b'stock patch bundle'
          and not any(os.path.exists(t + x) for t in tgts for x in (stockmp.BACKUP, stockmp.APPLIED)),
          'install --revert restores the originals')

    # validate judges the configuration the build was made from
    import validate
    c = json.loads(json.dumps(c))
    shuffled = json.loads(json.dumps(dict(reversed(list(c.items())))))
    check(pipeline.config_sha(c) == pipeline.config_sha(shuffled), 'config_sha: the content counts, not key order')
    os.makedirs(paths.root, exist_ok=True)
    rep_path = os.path.join(paths.root, 'build_report.json')
    json.dump({'config_sha256': pipeline.config_sha(c), 'sector_offsets': {}, 'variants': {}}, open(rep_path, 'w'))
    check(validate.check_config(json.load(open(rep_path)), c)[0], 'a build of this configuration: the configuration check passes')
    edited = json.loads(json.dumps(c))
    edited['spawners'] = edited['spawners'][:-1]
    for name, rep_cfg, want in (('edited after the build', edited, 'another configuration'), ('no hash (older build)', None, 'no configuration hash')):
        if rep_cfg is None:
            json.dump({'sector_offsets': {}, 'variants': {}}, open(rep_path, 'w'))
        res = validate.validate(paths, edited if rep_cfg is not None else c, log=lambda *a: None)
        check(not res['passed'] and res['results'][0]['check'] == 'configuration' and want in res['results'][0]['detail']
              and not json.load(open(os.path.join(paths.root, 'validation.json')))['passed'],
              f'validate fails on a configuration {name} (validation.json says so)')


# ------------------------------------------------------------------------------------------ game tests

def game(tmp):
    """MOH's half: the DBX writer on a MOH level's files. The layer itself has no MOH counterpart - MoHMPGame.exe
    (build 592256) has no AI runtime, so there is no level to author, build and validate."""
    import navkit
    opfor = navkit.find_opfor(None)
    n = bad = 0
    for top in (os.path.join(opfor, 'work', 'levels', 'mp_01'),):
        for dp, _ds, fs in os.walk(top):
            for fn in fs:
                if fn.lower().endswith('.dbx'):
                    data = open(os.path.join(dp, fn), 'rb').read()
                    if dbxbin.is_binary(data):
                        n += 1
                        bad += dbxbin.write(dbxbin.parse(data)) != data
    check(n > 2000 and bad == 0, f'DBX writer: {n} files of mp_01\'s bundles come back byte for byte ({bad} differ)')
    exe = open(os.path.join(navkit.Project().game, 'MoHMPGame.exe'), 'rb').read()
    missing = [t for t in ('AreaOrderEntityData', 'HasAI', 'PathfindingTypes', 'PathdataSectorOffsets') if t.encode() not in exe]
    check(len(missing) == 4, 'MoHMPGame.exe has no AI order, HasAI, path finding or SectorOffsets type: no stock-MP AI '
          f'layer to build for MOH (not in the exe: {", ".join(missing)})')


def main(argv):
    tmp = tempfile.mkdtemp(prefix='stockmp_test_')
    try:
        quick(tmp)
        if '--game' in argv:
            game(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{len(FAILED)} failed' if FAILED else 'all passed')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
