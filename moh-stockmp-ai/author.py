"""Insert an AI layer, described by a configuration (config.py), into a stock level partition (dbxbin).

What it writes, and where each part was taken from (STOCK_MP_AI_PLAN.md, section 12):
- LevelData: HasAI true; PathfindingTypes = the infantry and vehicle PathfindingSettings of mp_common (as every level
  with working AI lists them: campaign, Napisal's ports, DICE's Xbox Onslaught); every new entity in Objects; the new
  EventConnections.
- AI spawners: SoldierSpawnEntityData cloned from the level's own spawner (so every field keeps the level's types),
  PlayerType SSAiPlayer, a real AI template, a multiplayer kit of the team's faction, Napisal's respawn set-up
  (Amount 0, SpawnSoldierFromBeginning true - proven on PC) with DICE's SpawnEnemyRadius 20.
- Orders: AreaOrderEntityData (prototype from a DICE level, the donor) with its Soldiers listing the spawners and an
  AabbGeometryEntityData area (prototype from the level itself) at the objective.
- Issuing the orders, per order's issue_on: 'spawn' - each listed spawner's OnSpawn -> IssueOrder; 'player_enter' -
  the level's HumanPlayerEntityData OnPlayerEnter -> IssueOrder; 'trigger' - an AreaTriggerEntityData (ATPlayers,
  radius) at the objective, OnEnter -> IssueOrder (Napisal's pattern, proven on PC).
- Rush stages: the stage's own trigger (the stock MultipleTrigger fed by its M-COMs) OnTrigger -> this stage's AI
  spawners Disable + KillSoldier and orders Disable, the next stage's spawners and orders Enable (+ IssueOrder).
- Optionally the stock spawners marked SSAiPlayer become SSHumanPlayer (they are the human spawn points).

New GUIDs are deterministic (UUIDv5 of the level, the item's id and its role), so a rebuild gives the same file.
"""
import struct
from collections import Counter, defaultdict

import dbxbin
from levelmodel import local, text

NS = 'opfor:stockmp'
PATHFINDING = ['ai/pathfinding/infantry/3096da77-3146-479c-ae35-c822273a54a7',
               'ai/pathfinding/vehicles/de958285-789b-4273-8a8d-a35e95c2612e']


class AuthorError(Exception):
    pass


class NumberKinds:
    """Per field name, whether its single 4-byte value is an integer or a decimal, learned from the level and the donor
    (a value counts as evidence when only one reading is plausible; negative integers count, unlike DbxEdit's import
    type book - its known limitation with XOffset -3)."""

    def __init__(self, *files):
        self.ints, self.floats = Counter(), Counter()
        for d in files:
            for e in d.partition.walk():
                if e.kind != dbxbin.NUMBERS or not e.attrs:
                    continue
                size, vals = e.numbers()
                if size != 4 or len(vals) != 1:
                    continue
                b = struct.unpack('>I', vals[0])[0]
                if b == 0:
                    continue
                i = struct.unpack('>i', vals[0])[0]
                if -(1 << 24) < i < (1 << 24):
                    self.ints[e.name] += 1
                else:
                    self.floats[e.name] += 1

    def kind(self, name):
        i, f = self.ints[name], self.floats[name]
        if i == f == 0:
            return None
        return 'i' if i > f else 'f'


class Author:
    def __init__(self, d, donor, config, model):
        self.d, self.donor, self.cfg, self.m = d, donor, config, model
        self.kinds = NumberKinds(d, donor)
        self.lv = config['lv']
        self.level = model.level
        self.new = []                  # new instance elements, in order
        self.events = []               # (source guid, source event, target guid, target event)
        self.guid_of = {}              # config id -> guid
        self.report = Counter()
        self._protos()

    # ------------------------------------------------------------------ prototypes
    def _protos(self):
        d = self.d
        sp = d.of_type('.SoldierSpawnEntityData')
        if not sp:
            raise AuthorError('the level has no SoldierSpawnEntityData to take the spawner layout from')
        self.p_spawner = sp[0]
        aabb = d.of_type('.AabbGeometryEntityData') or self.donor.of_type('.AabbGeometryEntityData')
        self.p_aabb, self.p_aabb_src = (aabb[0], d) if d.of_type('.AabbGeometryEntityData') else (aabb[0], self.donor)
        orders = [e for e in self.donor.of_type('.AreaOrderEntityData')]
        if not orders:
            raise AuthorError('the donor level has no AreaOrderEntityData')
        self.p_order = orders[0]
        trig = self.donor.of_type('.AreaTriggerEntityData')
        if not trig:
            raise AuthorError('the donor level has no AreaTriggerEntityData')
        self.p_trigger = trig[0]
        objs = self.level.child('Objects')
        if not objs or not objs.children:
            raise AuthorError('LevelData.Objects is empty - no item to take the layout from')
        self.p_item = objs.children[0]
        ec = self.level.child('EventConnections')
        if not ec or not ec.children:
            raise AuthorError('LevelData.EventConnections is empty - no connection to take the layout from')
        self.p_event = ec.children[0]

    # ------------------------------------------------------------------ helpers
    def guid(self, *parts):
        return dbxbin.new_guid(NS, self.lv, *parts)

    def instance(self, proto, src, guid):
        e = self.d.clone(proto, src)
        self.d.set_attr(e, 'guid', guid)
        e.attrs = [(k, v) for k, v in e.attrs if self.d.strings[k] in ('guid', 'type')]
        return e

    def field(self, e, name):
        c = e.child(name)
        if c is None:
            raise AuthorError(f'{e.attr("type")}: no field {name}')
        return c

    def set(self, e, name, value):
        """Set a direct field, in the kind it has."""
        c = self.field(e, name)
        if c.attr('ref') is not None:
            self.d.set_ref(c, value)
        elif c.kind == dbxbin.TEXT:
            self.d.set_text(c, str(value))
        elif c.kind == dbxbin.NUMBERS:
            k = self.kinds.kind(name)
            if isinstance(value, float) and not float(value).is_integer():
                k = 'f' if k is None else k
            if k is None:
                if value != 0:
                    raise AuthorError(f'{name}: integer or decimal is not known from the level or donor')
                k = 'i'
            if k == 'i' and not float(value).is_integer():
                raise AuthorError(f'{name}: {value} is not an integer')
            self.d.set_numbers(c, [int(value) if k == 'i' else float(value)], k)
        elif c.kind == dbxbin.CONTAINER:
            raise AuthorError(f'{name} is a container')
        else:
            self.d.set_bool(c, value)

    def set_items(self, e, name, refs):
        """An array of references. DICE's files store an empty array as an empty text element (the XML's
        <array name="X" />) and a filled one as a container; both forms are kept that way."""
        a = self.field(e, name)
        if a.tag != 'array' or a.kind not in (dbxbin.CONTAINER, dbxbin.TEXT) or (a.kind == dbxbin.TEXT and a.text):
            raise AuthorError(f'{name} is not an array')
        if not refs:
            a.kind, a.children, a.raw, a.text_i = dbxbin.TEXT, None, b'', self.d.intern('')
            return
        a.kind, a.children, a.cbyte = dbxbin.CONTAINER, [], 0
        for r in refs:
            it = self.d.clone(self.p_item, self.d)
            self.d.set_ref(it, r)
            a.children.append(it)

    def transform(self, e, pos, yaw_deg=0.0):
        import math
        c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
        self.d.set_transform(self.field(e, 'Transform'), [(c, 0, -s), (0, 1, 0), (s, 0, c), tuple(pos)])

    def event(self, src, src_event, tgt, tgt_event):
        self.events.append((src, src_event, tgt, tgt_event))

    # ------------------------------------------------------------------ the layer
    def build(self):
        cfg = self.cfg
        d = self.d
        self.set(self.level, 'HasAI', True)
        pt = self.field(self.level, 'PathfindingTypes')
        have = [i.attr('ref') for i in pt.children or ()]
        if not have:
            self.set_items(self.level, 'PathfindingTypes', PATHFINDING)
            self.report['PathfindingTypes set'] += 1
        elif [h.lower() for h in have] != PATHFINDING:
            raise AuthorError(f'PathfindingTypes already set to {have}')
        if cfg.get('stock_spawners') == 'human':
            for e in d.of_type('.SoldierSpawnEntityData'):
                if text(e, 'PlayerType') == 'SSAiPlayer':
                    self.set(e, 'PlayerType', 'SSHumanPlayer')
                    self.report['stock spawners made SSHumanPlayer'] += 1
        if cfg.get('layer', 'full') == 'minimal':
            return self.finish()
        for s in cfg['spawners']:
            self.spawner(s)
        triggers = {}
        for o in cfg['orders']:
            self.order(o, triggers)
        self.stages()
        return self.finish()

    def spawner(self, s):
        cfg, d = self.cfg, self.d
        g = self.guid('spawner', s['id'])
        if s['id'] in self.guid_of:
            raise AuthorError(f'spawner id {s["id"]} used twice')
        self.guid_of[s['id']] = g
        e = self.instance(self.p_spawner, d, g)
        team = cfg['teams'][s['team']]
        values = dict(cfg['defaults']['spawner'])
        values.update(s.get('fields', {}))
        values['Name'] = s.get('name', f'OPFOR AI {s["id"]}')
        values['Team'] = s['team']
        values['Enabled'] = s.get('stage') in (None, 0) and s.get('enabled', True)
        values['SoldierKit'] = team['kits'][s['kit']]
        values['Template'] = s.get('template', cfg['template'])
        values['PlayerType'] = 'SSAiPlayer'
        for k, v in values.items():
            self.set(e, k, v)
        for arr in ('AlternativeSpawnPoints', 'Squad', 'Vehicle'):
            if e.child(arr) is not None:
                self.set_items(e, arr, [])
        self.transform(e, s['pos'], s.get('yaw', 0.0))
        self.new.append(e)
        self.report['AI spawners'] += 1
        self.report[f'AI spawners {s["team"]}'] += 1

    def order(self, o, triggers):
        cfg, d = self.cfg, self.d
        g = self.guid('order', o['id'])
        self.guid_of[o['id']] = g
        e = self.instance(self.p_order, self.donor, g)
        values = dict(cfg['defaults']['order'])
        values.update(o.get('fields', {}))
        values['Name'] = o.get('name', f'OPFOR {o["id"]}')
        values['Enabled'] = o.get('stage') in (None, 0)
        values['AreaType'] = o.get('type', 'AreaMoveTo')
        for k, v in values.items():
            self.set(e, k, v)
        soldiers = [self.guid_of[x] for x in o['soldiers']]
        if not soldiers:
            raise AuthorError(f'order {o["id"]} has no soldiers')
        self.set_items(e, 'Soldiers', [x.lower() for x in soldiers])
        self.set_items(e, 'Squads', [])
        area = self.instance(self.p_aabb, self.p_aabb_src, self.guid('area', o['id']))
        self.d.set_transform(self.field(area, 'Transform'), [(1, 0, 0), (0, 1, 0), (0, 0, 1), tuple(o['pos'])])
        ext = self.field(area, 'Extents')
        size, old = ext.numbers()
        hx, hy, hz = o.get('half_extents', (8.0, 3.0, 8.0))
        ext.raw = dbxbin._varint_bytes(4) + dbxbin._varint_bytes(4) + b''.join(
            struct.pack('>f', float(v)) for v in (hx, hy, hz)) + (old[3] if len(old) == 4 else struct.pack('>f', 0.0))
        if area.child('Name') is not None:
            self.set(area, 'Name', f'OPFOR area {o["id"]}')
        self.set_items(e, 'Area', [area.attr('guid').lower()])
        self.transform(e, o['pos'])
        self.new += [e, area]
        self.report['orders'] += 1
        self.report['order areas'] += 1
        issue = o.get('issue_on', cfg['defaults'].get('issue_on', ['spawn']))
        for x in issue:
            if x == 'spawn':
                for sg in soldiers:
                    self.event(sg, 'OnSpawn', g, 'IssueOrder')
            elif x == 'player_enter':
                hp = self.m.d.of_type('.HumanPlayerEntityData')
                if not hp:
                    raise AuthorError('issue_on player_enter: the level has no HumanPlayerEntityData')
                self.event(hp[0].attr('guid').upper(), 'OnPlayerEnter', g, 'IssueOrder')
            elif x == 'trigger':
                key = (round(o['pos'][0]), round(o['pos'][2]), o.get('trigger_radius', 100))
                if key not in triggers:
                    tg = self.guid('trigger', o['id'])
                    t = self.instance(self.p_trigger, self.donor, tg)
                    tv = {'Name': f'OPFOR trigger {o["id"]}', 'Enabled': True, 'RunOnce': False, 'Include': 'ATPlayers',
                          'Radius': float(key[2]), 'Delay': 0.0, 'OneInsideAreaEventPerSoldier': False,
                          'TriggerOnlyOnLeave': False}
                    for k, v in tv.items():
                        if t.child(k) is not None:
                            self.set(t, k, v)
                    for arr in ('Geometry', 'Soldiers'):
                        if t.child(arr) is not None:
                            self.set_items(t, arr, [])
                    if t.child('ObjectToFollow') is not None:
                        self.set(t, 'ObjectToFollow', 'null')
                    self.transform(t, o['pos'])
                    self.new.append(t)
                    triggers[key] = tg
                    self.report['area triggers'] += 1
                self.event(triggers[key], 'OnEnter', g, 'IssueOrder')
            else:
                raise AuthorError(f'order {o["id"]}: unknown issue_on {x}')

    def stages(self):
        stages = self.cfg.get('stages') or []
        if not stages:
            return
        by_stage = defaultdict(lambda: {'spawners': [], 'orders': []})
        for s in self.cfg['spawners']:
            if s.get('stage') is not None:
                by_stage[s['stage']]['spawners'].append(self.guid_of[s['id']])
        for o in self.cfg['orders']:
            if o.get('stage') is not None:
                by_stage[o['stage']]['orders'].append(self.guid_of[o['id']])
        for st in stages:
            k = st['index']
            src = st['advance']['source'].upper()
            ev = st['advance'].get('event', 'OnTrigger')
            if src not in self.m.inst:
                raise AuthorError(f'stage {k}: advance source {src} is not in the level')
            for sg in by_stage[k]['spawners']:
                self.event(src, ev, sg, 'Disable')
                self.event(src, ev, sg, 'KillSoldier')
            for og in by_stage[k]['orders']:
                self.event(src, ev, og, 'Disable')
            for sg in by_stage[k + 1]['spawners']:
                self.event(src, ev, sg, 'Enable')
            for og in by_stage[k + 1]['orders']:
                self.event(src, ev, og, 'Enable')
                self.event(src, ev, og, 'IssueOrder')
            self.report['stage switches'] += 1

    def finish(self):
        d = self.d
        part = d.partition
        part.children.extend(self.new)
        objs = self.field(self.level, 'Objects')
        for e in self.new:
            it = d.clone(self.p_item, d)
            d.set_ref(it, e.attr('guid').lower())
            objs.children.append(it)
        ec = self.field(self.level, 'EventConnections')
        for s, se, t, te in self.events:
            c = d.clone(self.p_event, d)
            d.set_ref(c.child('Source'), s.lower())
            d.set_ref(c.child('Target'), t.lower())
            d.set_text(c.child('SourceEvent').child('Name'), se)
            d.set_text(c.child('TargetEvent').child('Name'), te)
            ec.children.append(c)
        self.report['event connections'] += len(self.events)
        self.report['objects added'] += len(self.new)
        return dict(self.report)
