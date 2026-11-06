"""Per-map configuration of a stock level's AI layer: the format, defaults, and the generator of a first proposal
(option c of STOCK_MP_AI_PLAN.md: placed automatically from the level's own objectives and spawns, then tidied by
hand).

Format (JSON, `<work>\\stockmp\\<lv>\\config.json`):

    {"format": "moh-stockmp-ai", "version": 1, "lv": "mp_005", "level": "Levels/MP_005", "mode": "conquest",
     "layer": "full" | "minimal",            # minimal: HasAI, PathfindingTypes, path data, SectorOffsets only
     "stock_spawners": "human" | "keep",     # stock SSAiPlayer spawners (the human spawn points) -> SSHumanPlayer
     "pathdata": {"infantry": <hpd>, "vehicle": <hpd>, "source": "..."},
     "template": <AI template ref>, "template_parts": [...sp_common partitions to pack...],
     "teams": {"Team1": {"faction": "FactionUS", "kits": {"assault": <ref>, ...}}, "Team2": {...}},
     "defaults": {"spawner": {<field>: <value>}, "order": {...}, "issue_on": ["spawn", "player_enter", "trigger"]},
     "spawners": [{"id": "t1_00", "team": "Team1", "pos": [x, y, z], "yaw": 0, "kit": "assault", "stage": null}],
     "orders": [{"id": "t1_B", "team": "Team1", "type": "AreaMoveTo" | "AreaHoldGround", "pos": [x, y, z],
                 "half_extents": [8, 3, 8], "soldiers": ["t1_00", ...], "stage": null, "objective": "B",
                 "trigger_radius": 100}],
     "stages": [{"index": 0, "advance": {"source": <guid>, "event": "OnTrigger"}, "name": "..."}],
     "notes": [...]}

Positions are world coordinates; `generate` snaps them to the path data (a ground point on a navigation area, else the
nearest route node within SNAP_RADIUS), and records what it could not place in "notes".
"""
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import levelmodel
    import ordercheck

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit', 'tools'))

FORMAT, VERSION = 'moh-stockmp-ai', 1
KIT_NAMES = ('assault', 'demolition', 'support', 'recon')     # the multiplayer classes (internal names)
FACTION_KITS = {'FactionUS': 'us', 'FactionRUS': 'ru'}
TEMPLATE = 'objects/soldiers/templates/spawntemplate_enemy_grunt/2eedbef1-52de-4b20-819c-e71097c15cbe'
TEMPLATE_PARTS = ['Objects/Soldiers/Templates/SpawnTemplate_Enemy_Grunt.dbx',
                  'Objects/Soldiers/Templates/AI/Enemy_Grunt_Sense.dbx',
                  'Objects/Soldiers/Templates/AI/Enemy_Grunt_Bhv.dbx']
SNAP_RADIUS = 6.0          # horizontal, metres: a spawn point farther from any route node is dropped
NODE_Y = 0.95              # route nodes sit this far above the ground (NavKit ordercheck.Y_OFFSET)
SPAWN_LIFT = 0.3
Config = dict[str, Any]                 # a configuration (the format above)
Point = Sequence[float]                 # x, y, z

DEFAULTS: dict[str, Any] = {
    # Napisal's PC-proven respawning AI spawner (Amount 0 = keep respawning, SpawnSoldierFromBeginning) with DICE's
    # SpawnEnemyRadius; the stock MP SpawnDelay
    'spawner': {'Amount': 0, 'SpawnDelay': 15.0, 'InitializedSpawnDelay': 0.0, 'SpawnSoldierFromBeginning': True,
                'AllowMulipleSpawn': False, 'Immortal': False, 'FakeImmortal': False, 'SpawnEnemyRadius': 20.0,
                'LodDistance': 0.0, 'LocationNameSid': '', 'UseAsAlternateSpawnPoint': False,
                'TryToSpawnAwayFromSight': False, 'SoldierCreationPoint': False, 'OnSpottedType': 'OSPlayer',
                'SoldierSquadId': 'SquadNone', 'Enumeration': 0},
    # Napisal's AreaOrders on ONS_MP_002 (the bots advance on the objectives and fire - user play test 2026-10-21)
    'order': {'IssueOrderDelay': 2.0, 'OnCompleteTimeOut': 0.0, 'OnFailedTimeOut': 0.0, 'IsUrgent': True,
              'ReissueIfSoldierIsKilled': True, 'ReadinessOnStart': 'RLNone', 'ReadinessOnComplete': 'RLNone',
              'ReadinessOnFail': 'RLNone', 'UnspawnOnComplete': False, 'UnspawnOnFail': False,
              'AllowStunAnimations': 'SASNoChange', 'Stance': 'SONoChange', 'SpeedState': 'SSORunning',
              'AimState': 'AsoNoChange', 'AnimationIndex': -1, 'IdleAnimation': 'null', 'Enumeration': 0},
    'issue_on': ['spawn', 'player_enter', 'trigger'],
}


def load(path: str) -> Config:
    """Reads a configuration file; raises ValueError listing its schema problems."""
    with open(path, encoding='utf-8') as f:
        c = json.load(f)
    problems = check(c)
    if problems:
        raise ValueError(f'{path}: ' + '; '.join(problems))
    return cast(Config, c)


def save(c: Config, path: str) -> None:
    """Writes a configuration as indented JSON (folder created)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(c, f, indent=1)
        f.write('\n')


def check(c: Config) -> list[str]:
    """Schema problems (empty list when the configuration is usable)."""
    p: list[str] = []
    if c.get('format') != FORMAT or c.get('version') != VERSION:
        p.append(f'not a {FORMAT} v{VERSION} configuration')
        return p
    for k in ('lv', 'level', 'mode', 'teams', 'defaults', 'spawners', 'orders', 'template'):
        if k not in c:
            p.append(f'missing "{k}"')
    if p:
        return p
    ids: set[Any] = set()
    for s in c['spawners']:
        if s.get('id') in ids:
            p.append(f'spawner id {s.get("id")} twice')
        ids.add(s.get('id'))
        if s.get('team') not in c['teams']:
            p.append(f'spawner {s.get("id")}: team {s.get("team")} not in teams')
        elif s.get('kit') not in c['teams'][s['team']]['kits']:
            p.append(f'spawner {s.get("id")}: kit {s.get("kit")} not in the team\'s kits')
        if not (isinstance(s.get('pos'), list) and len(s['pos']) == 3):
            p.append(f'spawner {s.get("id")}: pos must be [x, y, z]')
    teams_of = {s['id']: s.get('team') for s in c['spawners']}
    stage_of = {s['id']: s.get('stage') for s in c['spawners']}
    for o in c['orders']:
        if o.get('id') in ids:
            p.append(f'order id {o.get("id")} used twice')
        ids.add(o.get('id'))
        if not o.get('soldiers'):
            p.append(f'order {o.get("id")}: no soldiers')
        for x in o.get('soldiers', []):
            if x not in teams_of:
                p.append(f'order {o.get("id")}: unknown soldier {x}')
            elif teams_of[x] != o.get('team'):
                p.append(f'order {o.get("id")}: soldier {x} is on another team')
            elif stage_of[x] != o.get('stage'):
                p.append(f'order {o.get("id")}: soldier {x} belongs to another stage')
        if o.get('type', 'AreaMoveTo') not in ('AreaMoveTo', 'AreaHoldGround'):
            p.append(f'order {o.get("id")}: type {o.get("type")}')
    stages = {s.get('stage') for s in c['spawners']} - {None}
    if stages and sorted(stages) != list(range(len(stages))):
        p.append(f'stages must be 0..n-1, have {sorted(stages)}')
    for st in c.get('stages') or []:
        if 'advance' not in st or 'source' not in st['advance']:
            p.append(f'stage {st.get("index")}: no advance source')
    return p


# ---------------------------------------------------------------------------------------------- generator

class Snapper:
    """Places points on the path data (NavKit's ordercheck.PathData: areas and route nodes)."""

    def __init__(self, hpd: str | None) -> None:
        self.pd: 'ordercheck.PathData | None' = None
        if hpd and os.path.isfile(hpd):
            import ordercheck
            self.pd = ordercheck.PathData(hpd)
            self.biggest = max(self.pd.group_size.values())

    def nearest(self, p: Point) -> tuple[float, tuple[float, ...], int]:
        """The nearest route node within 4 m in height of p: (plan distance, its position, the node count of its
        network)."""
        import numpy as np
        pd = cast('ordercheck.PathData', self.pd)       # only called with path data
        P = pd.pos
        d = np.hypot(P[:, 0] - p[0], P[:, 2] - p[2])
        d = np.where(np.abs(P[:, 1] - NODE_Y - p[1]) <= 4.0, d, 1e9)
        k = int(np.argmin(d))
        return float(d[k]), tuple(float(v) for v in P[k]), pd.group_size[pd.group[pd.ids[k]]]

    def ground(self, p: Point) -> tuple[tuple[float, ...] | None, str]:
        """(point, how) or (None, why)."""
        if self.pd is None:
            return (p[0], p[1] + SPAWN_LIFT, p[2]), 'unchecked (no path data)'
        dist, node, size = self.nearest(p)
        if size < 0.25 * self.biggest:
            return None, f'nearest route node on a small island ({size} nodes)'
        if self.pd.on_area(p) and dist <= SNAP_RADIUS:
            return (p[0], p[1] + SPAWN_LIFT, p[2]), f'on an area, node {dist:.1f} m'
        if dist <= SNAP_RADIUS:
            return (node[0], node[1] - NODE_Y + SPAWN_LIFT, node[2]), f'moved {dist:.1f} m to a route node'
        return None, f'no route node within {SNAP_RADIUS} m ({dist:.1f} m)'


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _centroid(pts: Sequence[Point]) -> tuple[float, ...]:
    return tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))


def kits_for(faction: str | None, kit_refs: Mapping[str, str]) -> dict[str, str]:
    """The faction's multiplayer kit references by kit name; raises ValueError for a faction without known kits."""
    prefix = FACTION_KITS.get(faction) if faction is not None else None
    if prefix is None:
        raise ValueError(f'no multiplayer kits known for {faction}')
    return {k: kit_refs[f'{prefix}/{k}'] for k in KIT_NAMES}


def place(snap: Snapper, candidates: Sequence[Point], n: int, notes: list[str], label: str,
          ring: float = 2.5) -> list[tuple[float, ...]]:
    """n spawn positions from the candidate ground points: each candidate in turn, later rounds on rings around them."""
    good: list[tuple[float, ...]] = []
    for c in candidates:
        g, how = snap.ground(c)
        if g is None:
            notes.append(f'{label}: candidate {tuple(round(v, 1) for v in c)} dropped - {how}')
        else:
            good.append(g)
    if not good:
        return []
    out: list[tuple[float, ...]] = []
    k = 0
    while len(out) < n and k < 50 * n:
        base = good[k % len(good)]
        r = k // len(good)
        p: tuple[float, ...] | None
        if r == 0:
            p = base
        else:
            a = (k * 2.399963) % (2 * math.pi)             # golden-angle spread
            q = (base[0] + ring * r * math.cos(a), base[1] - SPAWN_LIFT, base[2] + ring * r * math.sin(a))
            p, _how = snap.ground(q)
        if p is not None and all(_dist(p, o) > 1.0 for o in out):
            out.append(tuple(round(v, 3) for v in p))
        k += 1
    if len(out) < n:
        notes.append(f'{label}: only {len(out)} of {n} spawn positions found')
    return out


def _yaw_to(a: Point, b: Point) -> float:
    return round(math.degrees(math.atan2(b[0] - a[0], b[2] - a[2])), 1)


def generate(model: 'levelmodel.Model', lv: str, kit_refs: Mapping[str, str], pathdata: dict[str, Any],
             bots_per_team: int = 8, layer: str = 'full', stock_spawners: str = 'human', half_extent: float = 8.0,
             trigger_radius: float = 100.0, rush_bots: tuple[int, int] = (4, 4)) -> Config:
    """A first configuration for the level, from its own objectives and spawns."""
    snap = Snapper(pathdata.get('infantry'))
    notes: list[str] = []
    teams = {t: {'faction': model.factions.get(t), 'kits': kits_for(model.factions.get(t), kit_refs)}
             for t in ('Team1', 'Team2')}
    c: Config = {'format': FORMAT, 'version': VERSION, 'lv': lv, 'level': model.name, 'mode': model.mode, 'layer': layer,
         'stock_spawners': stock_spawners, 'pathdata': pathdata, 'template': TEMPLATE, 'template_parts': TEMPLATE_PARTS,
         'teams': teams, 'defaults': json.loads(json.dumps(DEFAULTS)), 'spawners': [], 'orders': [], 'stages': [],
         'notes': notes}
    if layer == 'minimal':
        return c
    if model.mode == 'conquest':
        _conquest(c, model, snap, bots_per_team, half_extent, trigger_radius, notes)
    elif model.mode == 'rush':
        _rush(c, model, snap, rush_bots, half_extent, trigger_radius, notes)
    else:
        raise ValueError(f'{model.name}: mode {model.mode} - only conquest and rush are generated so far')
    return c


def _order_pos(snap: Snapper, p: Point, notes: list[str], label: str) -> list[float]:
    if snap.pd is None:
        return list(p)
    dist, node, size = snap.nearest(p)
    if dist > 15.0:
        notes.append(f'{label}: no route node within 15 m of the objective ({dist:.1f} m)')
    return [round(v, 3) for v in p]


def _conquest(c: Config, m: 'levelmodel.Model', snap: Snapper, n: int, half: float, radius: float,
              notes: list[str]) -> None:
    flags = m.flags()
    if not flags:
        raise ValueError('no neutral capture points (Conquest flags) found')
    for t in ('Team1', 'Team2'):
        bases = m.bases(t)
        if not bases:
            raise ValueError(f'{t}: no capture point owned at the start (its base)')
        cands: list[Point] = []
        for b in bases:
            cands += m.ground_points(team=None, flag=b.guid)
        if not cands:
            cands = [b.pos for b in bases]
            notes.append(f'{t}: the base has no ground spawn points; using the base flag position')
        home = _centroid([b.pos for b in bases])
        cands.sort(key=lambda p: _dist(p, home))
        pts = place(snap, cands, n, notes, f'{t} base')
        tag = 't1' if t == 'Team1' else 't2'
        order_flags = sorted(flags, key=lambda f: _dist(f.pos, home))
        assign: dict[str, list[str]] = {f.guid: [] for f in order_flags}
        for i, p in enumerate(pts):
            sid = f'{tag}_{i:02d}'
            f = order_flags[i % len(order_flags)]
            c['spawners'].append({'id': sid, 'team': t, 'pos': list(p), 'yaw': _yaw_to(p, f.pos),
                                  'kit': KIT_NAMES[i % len(KIT_NAMES)], 'stage': None})
            assign[f.guid].append(sid)
        for f in order_flags:
            if not assign[f.guid]:
                continue
            c['orders'].append({'id': f'{tag}_flag_{f.label or f.name}', 'team': t, 'type': 'AreaMoveTo',
                                'objective': f.label or f.name, 'pos': _order_pos(snap, f.pos, notes, f.name),
                                'half_extents': [half, 3.0, half], 'soldiers': assign[f.guid], 'stage': None,
                                'trigger_radius': radius})


def _rush(c: Config, m: 'levelmodel.Model', snap: Snapper, bots: tuple[int, int], half: float, radius: float,
          notes: list[str]) -> None:
    stages = m.rush_stages()
    if not stages:
        raise ValueError('no Rush stages found (M-COM triggers)')
    att = m.attacker if m.attacker in ('Team1', 'Team2') else 'Team1'
    dfn = 'Team2' if att == 'Team1' else 'Team1'
    centroids = [_centroid([p for _g, p in st.mcoms]) for st in stages]
    att_cps = [cp for cp in m.capture_points if cp.owner == att]
    def_cps = [cp for cp in m.capture_points if cp.owner == dfn]
    att_base = max(att_cps, key=lambda cp: min(_dist(cp.pos, x) for x in centroids)) if att_cps else None
    for st, cen in zip(stages, centroids):
        k = st.index
        dcp = min(def_cps, key=lambda cp: _dist(cp.pos, cen)) if def_cps else None
        acp = att_base if k == 0 or not att_cps else min(att_cps, key=lambda cp: _dist(cp.pos, centroids[k - 1]))
        for team, cp, n, kind, tag in ((dfn, dcp, bots[1], 'AreaHoldGround', 'd'), (att, acp, bots[0], 'AreaMoveTo', 'a')):
            if cp is None:
                notes.append(f'stage {k}: no {team} capture point to spawn at')
                continue
            cands: list[Point] = sorted(m.ground_points(flag=cp.guid), key=lambda p: _dist(p, cen)) or [cp.pos]
            pts = place(snap, cands, n, notes, f'stage {k} {team} ({cp.name})')
            ids: list[str] = []
            for i, p in enumerate(pts):
                sid = f's{k}{tag}_{i:02d}'
                ids.append(sid)
                c['spawners'].append({'id': sid, 'team': team, 'pos': list(p), 'yaw': _yaw_to(p, cen),
                                      'kit': KIT_NAMES[i % len(KIT_NAMES)], 'stage': k})
            for j, (g, mp) in enumerate(st.mcoms):
                sold = ids[j::len(st.mcoms)]
                if not sold:
                    continue
                c['orders'].append({'id': f's{k}{tag}_mcom{j}', 'team': team, 'type': kind, 'objective': g,
                                    'pos': _order_pos(snap, mp, notes, f'stage {k} M-COM {j}'),
                                    'half_extents': [half, 3.0, half], 'soldiers': sold, 'stage': k,
                                    'trigger_radius': radius})
        if k < len(stages) - 1:
            c['stages'].append({'index': k, 'name': st.trigger_name, 'advance': {'source': st.trigger, 'event': 'OnTrigger'}})
