"""What the authoring tool needs to know about a stock multiplayer level, read from its level partition (dbxbin).

    m = Model(dbxbin.read('MP_005.dbx'))
    m.mode                       # 'conquest', 'rush', 'squadrush', 'sdm', 'tdm' or the game-mode path
    m.factions                   # {'Team1': 'FactionUS', 'Team2': 'FactionRUS'}
    m.capture_points             # [CapturePoint]: flags and bases, with capture-area outline and spawners
    m.spawners                   # [Spawner]: SoldierSpawnEntityData, with their ground spawn points (alternates)
    m.mcoms                      # [(guid, name, pos)]: placed mpobjective objects (Rush M-COMs)
    m.rush_stages()              # [Stage]: Rush stages from the level's own stage logic (see below)

Rush stages are read from the stock logic (STOCK_MP_AI_PLAN.md, findings 2026-10-26): each stage has a
MultipleTriggerEntityData ("Fishing_Port_taken", TriggerAmount 2) fed by its M-COMs' onDestroyed; it is switched on
by the previous stage's MultipleTrigger (OnTrigger -> Enable) and its OnTrigger enables the next defender
CombatAreaTrigger. The first stage's trigger is the one no other MultipleTrigger enables.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import dbxbin

TEAMS = ('Team1', 'Team2')


def local(ref):
    """The GUID of a local reference (no '/'), upper case; None for null or a reference into another partition."""
    if not ref or ref == 'null' or '/' in ref:
        return None
    return ref.upper()


def position(e):
    t = e.child('Transform')
    if t is None or t.kind != dbxbin.NUMBERS:
        return None
    v = t.floats()
    return (v[12], v[13], v[14]) if len(v) == 16 else None


def text(e, name, default=None):
    c = e.child(name)
    if c is None:
        return default
    if c.kind == dbxbin.TEXT:
        return c.text
    if c.kind == dbxbin.NUMBERS:
        size, vals = c.numbers()
        return '/'.join(str(x) for x in (c.floats() if size == 4 else vals))
    if c.kind not in (dbxbin.CONTAINER,):
        return 'true' if c.value == 1 else 'false' if c.value == 0 else str(c.value)
    return default


def ref_of(e, name):
    c = e.child(name)
    return c.attr('ref') if c is not None else None


def item_refs(e, name):
    a = e.child(name)
    return [i.attr('ref') for i in (a.children or ())] if a is not None and a.children is not None else []


@dataclass
class CapturePoint:
    guid: str
    name: str
    label: str
    pos: Tuple[float, float, float]
    owner: str                       # InitialOwnerTeam
    spawners: List[str]
    outline: List[Tuple[float, float, float]] = field(default_factory=list)

    def half_extents(self, minimum=6.0, maximum=30.0):
        if not self.outline:
            return (minimum, 3.0, minimum)
        xs = [p[0] for p in self.outline]
        zs = [p[2] for p in self.outline]
        hx = max(abs(max(xs) - self.pos[0]), abs(self.pos[0] - min(xs)))
        hz = max(abs(max(zs) - self.pos[2]), abs(self.pos[2] - min(zs)))
        clamp = lambda v: max(minimum, min(maximum, v))
        return (clamp(hx), 3.0, clamp(hz))


@dataclass
class Spawner:
    guid: str
    name: str
    team: str
    player_type: str
    pos: Tuple[float, float, float]
    enabled: bool
    alternates: List[Tuple[str, Tuple[float, float, float]]]
    flag: Optional[str] = None       # the capture point that lists it (its Spawners)


@dataclass
class Stage:
    index: int
    trigger: str                     # MultipleTriggerEntityData guid: OnTrigger = this stage's M-COMs are destroyed
    trigger_name: str
    mcoms: List[Tuple[str, Tuple[float, float, float]]]


class Model:
    def __init__(self, d):
        self.d = d
        self.inst = {(e.attr('guid') or '').upper(): e for e in d.instances()}
        self.level = next(e for e in d.instances() if (e.attr('type') or '').endswith('.LevelData'))
        self.name = text(self.level, 'Name')
        desc = self.level.child('LevelDescription')
        gm = (ref_of(desc, 'GameMode') or '') if desc is not None else ''
        self.game_mode = gm
        g = gm.lower()
        self.mode = ('conquest' if g.startswith('gamemodes/conquest') else 'rush' if g.startswith('gamemodes/goldrush')
                     else 'squadrush' if 'squadrush' in g else 'sdm' if 'squaddeathmatch' in g else
                     'tdm' if 'teamdeathmatch' in g else gm)
        self.factions = {t: text(desc, 'Faction' + t) for t in TEAMS} if desc is not None else {}
        self.attacker = text(desc, 'AttackerTeam') if desc is not None else None
        self.has_ai = text(self.level, 'HasAI') == 'true'
        self.events = []
        ec = self.level.child('EventConnections')
        for c in (ec.children or ()) if ec is not None else ():
            src, tgt = local(ref_of(c, 'Source')), local(ref_of(c, 'Target'))
            se, te = c.child('SourceEvent'), c.child('TargetEvent')
            self.events.append((src, text(se, 'Name'), tgt, text(te, 'Name')))
        self.objects = [local(r) for r in item_refs(self.level, 'Objects')]
        self.capture_points = [self._capture_point(e) for e in d.of_type('.CapturePointEntityData')]
        flag_of = {s: cp.guid for cp in self.capture_points for s in cp.spawners}
        self.spawners = []
        for e in d.of_type('.SoldierSpawnEntityData'):
            g = e.attr('guid').upper()
            alts = []
            for r in item_refs(e, 'AlternativeSpawnPoints'):
                a = self.inst.get(local(r) or '')
                if a is not None and position(a):
                    alts.append((local(r), position(a)))
            self.spawners.append(Spawner(g, text(e, 'Name') or '', text(e, 'Team'), text(e, 'PlayerType'), position(e),
                                         text(e, 'Enabled') == 'true', alts, flag_of.get(g)))
        self.mcoms = []
        for e in d.of_type('.ReferenceObjectData'):
            if (ref_of(e, 'ReferencedObject') or '').lower().startswith('objects/objects/mpobjective/'):
                self.mcoms.append((e.attr('guid').upper(), text(e, 'Name') or '', position(e)))

    def typ(self, guid):
        e = self.inst.get(guid or '')
        return (e.attr('type') or '').rsplit('.', 1)[-1] if e is not None else None

    def _capture_point(self, e):
        g = e.attr('guid').upper()
        outline = []
        for r in item_refs(e, 'Geometry'):
            seen, cur = set(), local(r)
            while cur and cur in self.inst and cur not in seen:
                seen.add(cur)
                p = position(self.inst[cur])
                if p:
                    outline.append(p)
                cur = local(ref_of(self.inst[cur], 'Next'))
        return CapturePoint(g, text(e, 'Name') or '', text(e, 'ControlPointName') or text(e, 'LocationName') or '',
                            position(e), text(e, 'InitialOwnerTeam'), [local(r) for r in item_refs(e, 'Spawners') if local(r)],
                            outline)

    def bases(self, team):
        return [cp for cp in self.capture_points if cp.owner == team]

    def flags(self):
        """Capture points that start neutral (the Conquest flags)."""
        return [cp for cp in self.capture_points if cp.owner not in TEAMS]

    def ground_points(self, team=None, flag=None):
        """Ground spawn positions (alternate spawn points) of the stock spawners, by team / owning capture point."""
        out = []
        for s in self.spawners:
            if team and s.team != team:
                continue
            if flag and s.flag != flag:
                continue
            out += [p for _g, p in s.alternates]
        return out

    def rush_stages(self):
        """Stages in order, from the stock stage logic: MultipleTriggers fed by M-COM onDestroyed."""
        mcom = {g: p for g, _n, p in self.mcoms}
        fed = {}
        enabled_by = {}
        for s, se, t, te in self.events:
            if self.typ(t) != 'MultipleTriggerEntityData':
                continue
            if s in mcom and se == 'onDestroyed' and te == 'Trigger':
                fed.setdefault(t, []).append((s, mcom[s]))
            if self.typ(s) == 'MultipleTriggerEntityData' and se == 'OnTrigger' and te == 'Enable':
                enabled_by[t] = s
        stage_mts = [t for t in fed if len(fed[t]) >= 1]
        nxt = {v: k for k, v in enabled_by.items() if k in fed and v in fed}
        firsts = [t for t in stage_mts if t not in enabled_by or enabled_by[t] not in fed]
        # the chain from the first trigger that has a successor (team-wide "Team1_trigger"-style counters have none)
        firsts.sort(key=lambda t: -self._chain_len(t, nxt))
        if not firsts:
            return []
        out, cur, seen = [], firsts[0], set()
        while cur and cur not in seen:
            seen.add(cur)
            out.append(Stage(len(out), cur, text(self.inst[cur], 'Name') or '', sorted(fed[cur])))
            cur = nxt.get(cur)
        # the last stage's M-COMs feed a trigger outside the chain (the win counter): add it when its M-COMs are new
        used = {g for st in out for g, _p in st.mcoms}
        rest = [t for t in stage_mts if t not in seen and all(g not in used for g, _p in fed[t]) and len(fed[t]) >= 2]
        for t in rest:
            out.append(Stage(len(out), t, text(self.inst[t], 'Name') or '', sorted(fed[t])))
        return out

    @staticmethod
    def _chain_len(t, nxt):
        n, seen = 0, set()
        while t and t not in seen:
            seen.add(t)
            n += 1
            t = nxt.get(t)
        return n
