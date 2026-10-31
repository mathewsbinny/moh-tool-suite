"""A MOH level as the suite's tools see it: entities with transforms and sources, collision meshes, terrain.

    lvl = load('sp_01')                       # by level name, from Project OPFOR's game copy
    lvl.entities                              # every instance of the level partition (Entity)
    lvl.meshes                                # world-space collision triangles per placed object / group member
    lvl.terrain                               # (vertices, triangles): the level's height field (terrain.py),
                                              # or NavKit's in-game samples when it cannot be read, or None
    lvl.heightfield                           # terrain.Heightfield, for exact heights (height(x, z))
    lvl.shapes                                # outlines: areas (capture, combat, order areas) and routes (Shape)
    lvl.water                                 # water.Water: surface triangles and each one's shader (water.py)

Data comes from the level as NavKit prepares it (OPFOR's work\\levels\\<level>\\dist and pkg, and the shared
data in work\\navkit\\common\\); a level that is not prepared yet is prepared on first use (offline, no game).
"""
import glob
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from . import dbx, gamecopy, terrain, water

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
NAVKIT = os.path.join(SUITE, 'moh-auto-navkit')
sys.path.insert(0, NAVKIT)
sys.path.insert(0, os.path.join(NAVKIT, 'tools'))

# entity categories by type (DESIGN.md open question 6)
CATEGORIES = [
    ('soldier spawn', r'\.(SoldierSpawnEntityData|AlternateSpawnEntityData|EndOfRoundSoldierSpawnEntityData)$'),
    ('vehicle spawn', r'\.(VehicleSpawnEntityData|VehicleProjectileSpawnEntityData)$'),
    ('objective', r'\.(MissionObjectiveEntityData|ArmDisarmAwardEntityData)$'),
    ('capture point', r'\.CapturePointEntityData$'),
    ('combat area', r'\.(CombatAreaTriggerEntityData|DamageAreaTriggerEntityData)$'),
    ('team', r'\.(TeamEntityData|AutoTeamEntityData|TeamFilterEntityData)$'),
    ('squad', r'\.SquadData$'),
    ('round flow', r'\.(RoundStartTimerEntityData|PreRoundEntityData|InningEntityData|LifeCounterEntityData)$'),
    ('ai order', r'\.(AreaOrderEntityData|PatrolOrderEntityData|InterludeOrderEntityData|FireAtAreaOrderEntityData)$'),
    ('area point', r'\.AreaGeometryEntityData$'),
    ('placed object', r'\.ReferenceObjectData$'),
]
# objective blueprints (placed objects an attacker arms or a defender guards): MOH's Combat Mission / Objective Raid
# bombs, crates, radars and generators (2026-10-30, every MP level's partition), and BFBC2's Rush M-COM stations
OBJECTIVE_BLUEPRINTS = ('objects/weapons/handheld/python/us_bomb/', 'objects/python/props/objectiveprops/',
                        'objects/python/props/supplycrate_01/objective_crate', 'objects/mp05_overrun_bomb',
                        'objects/python/props/hangar_01/hangar_gate_01_objective',
                        'objects/weapons/handheld/python/insurgent_mortar/insurgent_mortar_objective',
                        'objects/objects/mpobjective')
# placed objects (ReferenceObjectData), by what their blueprint's object is
OBJECT_CATEGORIES = {
    'LadderEntityData': 'ladder',
    'DynamicModelEntityData': 'dynamic object',      # movable or breakable props: crates, barrels
    'RigidModelEntityData': 'dynamic object',        # breakable fences
    'BangerEntityData': 'dynamic object',            # explosive props (gas cylinders)
    'EffectEntityData': 'effect',
}
TEAMS = {'Team1': 1, 'Team2': 2, 'TeamNeutral': 0}
SHAPE_FIELDS = (('Geometry', 'area'), ('SurroundingGeometry', 'outer area'), ('RouteWaypoints', 'route'))


@dataclass
class Entity:
    index: int
    guid: str
    type: str
    short_type: str
    category: str
    id: Optional[str]
    name: Optional[str]
    partition: str
    matrix: Optional[np.ndarray]          # 4 x 4, rows: right, up, forward, translation
    blueprint: Optional[str]
    element: dbx.Element
    group: Optional[str] = None           # static-group partition, for group members
    blueprint_type: Optional[str] = None  # placed objects: the type of the blueprint's object (StaticModelEntityData ...)
    destructible: bool = False            # placed objects: the blueprint has health states / breakable parts
    team: Optional[int] = None            # 1, 2, 0 (neutral): spawns, capture points (initial owner), triggers
    ai: Optional[bool] = None             # soldier spawns: True for AI soldiers, False for human players

    @property
    def position(self):
        return None if self.matrix is None else self.matrix[3, :3]

    def refs(self):
        """Every reference in the property tree: (field path, ref), local GUIDs and partition/GUID paths."""
        out = []

        def walk(el, path):
            for c in el.children or ():
                name = c.attr('name') or c.tag
                r = c.attr('ref')
                if r is not None and r != 'null':
                    out.append((path + name, r))
                if c.children is not None:
                    walk(c, path + name + '/')
        walk(self.element, '')
        return out


@dataclass
class Mesh:
    entity: int                           # index into Level.entities
    triangles: np.ndarray                 # (n, 3, 3) world space


@dataclass
class Shape:
    entity: int                           # the entity whose field it is (CapturePoint, CombatArea, an order ...)
    kind: str                             # 'area', 'outer area' or 'route'
    points: np.ndarray                    # (n, 3) world positions, in chain order
    closed: bool                          # areas are rings; routes are open
    height: float                         # areas: prism height above the points (0: flat)
    field: str


@dataclass
class Level:
    name: str
    lv: str
    opfor: str
    entities: List[Entity] = field(default_factory=list)
    meshes: List[Mesh] = field(default_factory=list)
    terrain: Optional[tuple] = None
    heightfield: Optional[object] = None  # terrain.Heightfield, when the level's terrain bundle was read
    water: Optional[object] = None        # water.Water (may have no triangles), from the same bundle
    shapes: List[Shape] = field(default_factory=list)
    missing_collision: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def by_guid(self, guid):
        g = guid.lower()
        return next((e for e in self.entities if e.guid.lower() == g), None)


def category_of(t):
    for name, rx in CATEGORIES:
        if re.search(rx, t or ''):
            return name
    return 'other'


def transform_of(e):
    t = e.child('Transform')
    if t is None or t.kind != dbx.NUMBERS:
        return None
    v = dbx.floats(t)
    return np.array(v, dtype=np.float64).reshape(4, 4) if len(v) == 16 else None


def blueprint_of(e):
    r = e.child('ReferencedObject')
    ref = r.attr('ref') if r is not None else None
    return ref.rsplit('/', 1)[0].lower() if ref and '/' in ref else None


def load(level, opfor=None, terrain_stride=2, log=print, geometry=True):
    """geometry=False: the level partition's entities, teams and shapes only - no static-group members, collision,
    terrain or water (a second or two; the test harness's reachability and level checks)."""
    import navkit
    import levelobjects
    root = navkit.find_opfor(opfor)
    lv = level.replace('\\', '/').split('/')[-1].lower()
    base = os.path.join(root, 'work', 'levels', lv)
    if not os.path.isdir(os.path.join(base, 'dist')):           # first use: NavKit's offline prepare step
        log(f'{lv}: not prepared yet - unpacking it (navkit.py prepare {lv}, once)')
        pr = navkit.Project(root)
        navkit.prepare(pr, pr.level(lv), log=log)
    bases = [os.path.join(base, 'pkg'), os.path.join(base, 'dist')]
    bases += navkit.common_dirs(root, lv)
    # the level partition (the patch's copy wins over the base one; mp_03_elimination's is in common_mp_03)
    part = None
    for b in bases:
        for f in glob.glob(os.path.join(b, 'levels', '*.dbx')):
            if os.path.splitext(os.path.basename(f))[0].lower() == lv:
                part = part or f
    if part is None:
        raise FileNotFoundError(f'{lv}: level partition not found under {base}')
    LV = os.path.splitext(os.path.basename(part))[0]
    lvl = Level(LV, lv, root)
    log(f'{LV}: reading {os.path.relpath(part, root)}')
    f = dbx.read(part)
    pname = 'levels/' + LV
    for el in f.instances:
        t = el.attr('type') or ''
        lvl.entities.append(Entity(len(lvl.entities), el.attr('guid') or '', t, t.rsplit('.', 1)[-1], category_of(t), el.attr('id'),
                                   (el.child('Name').text if el.child('Name') is not None and el.child('Name').kind == dbx.TEXT else None),
                                   pname, transform_of(el), blueprint_of(el), el))
    # static model groups: one entity per member placement. Each group file once: the patch's copy wins over
    # the base one, as in the game (and levels\ / Levels\ are the same folder on Windows)
    group_files = {}
    for b in (bases[:2] if geometry else ()):
        for gf in glob.glob(os.path.join(b, 'levels', lv, '*_HavokAsset.dbx')):
            group_files.setdefault(os.path.basename(gf).lower(), gf)
    for gf in group_files.values():
            g = dbx.read(gf)
            for el in g.instances:
                tr, assets = el.child('Transforms'), el.child('Assets')
                if tr is None or assets is None or tr.kind != dbx.NUMBERS or not assets.children:
                    continue
                vals = np.array(dbx.floats(tr), dtype=np.float64)
                for k, item in enumerate(assets.children):
                    if 16 * (k + 1) > len(vals):
                        break
                    bp = re.sub(r'_havok$', '', (item.attr('ref') or '').rsplit('/', 1)[0].lower())
                    lvl.entities.append(Entity(len(lvl.entities), f'{el.attr("guid")}#{k}', 'StaticGroupMember', 'StaticGroupMember',
                                               'group member', None, bp.rsplit('/', 1)[-1], os.path.basename(gf)[:-4],
                                               vals[16 * k:16 * (k + 1)].reshape(4, 4), bp, item, group=os.path.basename(gf)))
    resolve_blueprints(lvl, bases)
    assign_teams(lvl)
    lvl.shapes = shapes_of(lvl)
    if not geometry:
        log(f'{LV}: {len(lvl.entities)} entities (no geometry)')
        return lvl
    # collision
    havok = levelobjects.havok_index(bases)
    try:
        for bp, target in levelobjects.mesh_aliases(bases).items():
            if bp not in havok and target in havok:
                havok[bp] = havok[target]
    except Exception as e:                    # needs the converted XML NavKit's prepare writes
        lvl.notes.append(f'blueprint mesh aliases not read: {e}')
    cache, missing = {}, set()
    for e in lvl.entities:
        if e.blueprint is None or e.matrix is None:
            continue
        if e.blueprint not in cache:
            cache[e.blueprint] = levelobjects.triangles(havok[e.blueprint]) if e.blueprint in havok else None
        t = cache[e.blueprint]
        if t is None:
            missing.add(e.blueprint)
            continue
        m = e.matrix
        lvl.meshes.append(Mesh(e.index, (t @ m[:3, :3] + m[3, :3]).astype(np.float32)))
    lvl.missing_collision = sorted(missing)
    source = 'none'
    try:
        lvl.heightfield = heightfield_of(root, lv, log, terrain_name(f.instances))
    except Exception as e:                    # no terrain bundle, FbrbTool missing, unexpected layout
        lvl.notes.append(f'terrain height field not read: {e}')
    try:
        lvl.water = water_in(root, lv, terrain_name(f.instances) or lv)
    except Exception as e:
        lvl.notes.append(f'water not read: {e}')
    if lvl.heightfield is not None:
        lvl.terrain, source = lvl.heightfield.mesh(terrain_stride), 'height field'
    else:
        lvl.terrain = terrain_from_samples(root, lv, terrain_stride)
        source = 'samples' if lvl.terrain else 'none'
    n_tris = sum(len(m.triangles) for m in lvl.meshes)
    log(f'{LV}: {len(lvl.entities)} entities, {len(lvl.meshes)} with collision ({n_tris} triangles), '
        f'{len(missing)} blueprints without collision, terrain from {source}')
    return lvl


def resolve_blueprints(lvl, bases):
    """Placed objects: the blueprint's object type and whether it is destructible; categories from them."""
    files = {}

    def instances(path):
        if path not in files:
            files[path] = {}
            for b in bases:
                p = os.path.join(b, path + '.dbx')
                if os.path.isfile(p):
                    try:
                        files[path] = {(i.attr('guid') or '').lower(): i for i in dbx.read(p).instances}
                    except Exception:
                        pass
                    break
        return files[path]

    def broken_up(insts):
        """Health states or breakable part links anywhere in the blueprint (destructible buildings)."""
        for i in insts.values():
            for name in ('HealthStates', 'PartLinks'):
                c = i.child(name)
                if c is not None and c.children:
                    return True
        return False

    destructible = {}
    for e in lvl.entities:
        if e.short_type != 'ReferenceObjectData':
            continue
        r = e.element.child('ReferencedObject')
        ref = r.attr('ref') if r is not None else None
        if not ref or '/' not in ref:
            continue
        path, guid = ref.rsplit('/', 1)
        insts = instances(path)
        obj = bp = insts.get(guid.lower())
        o = bp.child('Object') if bp is not None else None
        oref = o.attr('ref') if o is not None else None
        if oref and oref != 'null':
            if '/' in oref:
                p2, g2 = oref.rsplit('/', 1)
                obj = instances(p2).get(g2.lower())
            else:
                obj = insts.get(oref.lower())
        if obj is None:
            continue
        e.blueprint_type = (obj.attr('type') or '').rsplit('.', 1)[-1]
        if path not in destructible:
            destructible[path] = broken_up(insts)
        e.destructible = e.blueprint_type == 'StaticModelEntityData' and destructible[path]
        if (e.blueprint or '').startswith(OBJECTIVE_BLUEPRINTS):
            e.category = 'objective'                     # what an attacker arms or a defender guards
        elif e.destructible:
            e.category = 'destructible'
        else:
            e.category = OBJECT_CATEGORIES.get(e.blueprint_type, e.category)


def assign_teams(lvl):
    """Team and AI / human from the entity's own fields; alternate spawn points take their spawner's team."""
    guids = {e.guid.lower(): e for e in lvl.entities}
    for e in lvl.entities:
        for name in ('Team', 'InitialOwnerTeam'):
            c = e.element.child(name)
            if c is not None and c.kind == dbx.TEXT and c.text in TEAMS:
                e.team = TEAMS[c.text]
                break
        c = e.element.child('PlayerType')
        if c is not None and c.kind == dbx.TEXT and c.text:
            e.ai = c.text != 'SSHumanPlayer'
    for e in lvl.entities:
        alt = e.element.child('AlternativeSpawnPoints')
        for item in (alt.children if alt is not None and alt.children else ()):
            t = guids.get((item.attr('ref') or '').lower())
            if t is not None and t.team is None:
                t.team, t.ai = e.team, e.ai


def shapes_of(lvl):
    """Outlines: chains of AreaGeometryEntityData points (Next links) referenced by Geometry, SurroundingGeometry
    and RouteWaypoints fields. A chain that comes back to its start is an area."""
    guids = {e.guid.lower(): e for e in lvl.entities}
    out = []
    for e in lvl.entities:
        for name, kind in SHAPE_FIELDS:
            c = e.element.child(name)
            for item in (c.children if c is not None and c.children else ()):
                p = guids.get((item.attr('ref') or '').lower())
                pts, seen, closed, height = [], set(), False, 0.0
                while p is not None and p.position is not None:
                    if p.guid in seen:
                        closed = True
                        break
                    seen.add(p.guid)
                    pts.append(p.position)
                    h = p.element.child('Height')
                    if h is not None and h.kind == dbx.NUMBERS and not height:
                        height = float(dbx.floats(h)[0])
                    n = p.element.child('Next')
                    p = guids.get((n.attr('ref') or '').lower()) if n is not None else None
                if len(pts) >= 2:
                    out.append(Shape(e.index, kind, np.array(pts), closed and kind != 'route', height, name))
    return out


def terrain_name(entities):
    """The terrain's name from the level's TerrainEntityData.TerrainAsset (terrains/mp_009/mp_009 for MP_009GR:
    a mode variant shares its base map's terrain), or None.

    The entity's transform is NOT applied to the heights: on 11 of the 13 campaign levels it moves the
    terrain by 1-59 m, yet the stored heights unmoved match the game's terrain to within centimetres."""
    for el in entities:
        if (el.attr('type') or '').endswith('.TerrainEntityData'):
            a = el.child('TerrainAsset')
            parts = ((a.attr('ref') or '') if a is not None else '').split('/')
            return parts[1].lower() if len(parts) >= 3 else None
    return None


def water_of(root, lv, log=print):
    """The level's water (water.Water, or None), its terrain bundle unpacked once (as heightfield_of does)."""
    part = None
    for b in ('pkg', 'dist'):
        for f in glob.glob(os.path.join(root, 'work', 'levels', lv, b, 'levels', '*.dbx')):
            if os.path.splitext(os.path.basename(f))[0].lower() == lv:
                part = part or f
    name = (terrain_name(dbx.read(part).instances) if part else None) or lv
    heightfield_of(root, lv, log, name)                  # unpacks the terrain bundle when it is not there yet
    return water_in(root, lv, name)


def water_in(root, lv, name):
    """The water of terrain <name> from the unpacked terrain layers: the patch's copy (terrain.patch) first, as the
    game loads its entries over the base's (water.py, test_water.py); then the base layer's. None when neither has
    water files. (2026-10-31: only work\\levels\\<lv>\\terrain was read, so a patched base level got the base's
    water.)"""
    dest = os.path.join(root, 'work', 'levels', lv, 'terrain')
    for d in (dest + '.patch', dest):
        if os.path.isdir(d):
            w = water.read(d, name)
            if w is not None:
                return w
    return None


def unpack_once(tool, bundle, d):
    """Unpacks a bundle into folder d unless d is there: into d + '.tmp' first, renamed to d when FbrbTool
    succeeds and deleted when it fails, so an interrupted or failed unpack never leaves a partial folder that is
    taken as unpacked from then on (2026-10-31). tool: FbrbTool.exe's path, or a command list. A failure raises
    RuntimeError with FbrbTool's output."""
    import shutil
    import subprocess
    if os.path.isdir(d):
        return False
    tmp = d + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)               # left by an earlier run that was stopped
    cmd = (list(tool) if isinstance(tool, (list, tuple)) else [tool]) + ['unpack', bundle, tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
        if r.returncode != 0:
            out = ' / '.join(l.strip() for l in (r.stdout + '\n' + r.stderr).splitlines() if l.strip())
            raise RuntimeError(f'FbrbTool unpack {os.path.basename(bundle)} failed (exit {r.returncode}): '
                               f'{out[-800:] or "no output"}')
        os.replace(tmp, d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return True


def heightfield_of(root, lv, log=print, name=None):
    """The level's terrain height field, unpacking its terrain-00.fbrb into work\\levels\\<lv>\\terrain once.
    name: from terrain_name(); read from the level partition if not given."""
    import navkit
    if name is None:
        part = None
        for b in ('pkg', 'dist'):
            for f in glob.glob(os.path.join(root, 'work', 'levels', lv, b, 'levels', '*.dbx')):
                if os.path.splitext(os.path.basename(f))[0].lower() == lv:
                    part = part or f
        name = terrain_name(dbx.read(part).instances) if part else None
    name = name or lv
    # the lowest terrain bundle in work\levels\<lv>\terrain (a DLC level's only one, from the patch), the patch's
    # copy of a base level's in terrain.patch; the patch's file wins, as the game loads its entries over the base's
    dest = os.path.join(root, 'work', 'levels', lv, 'terrain')
    layers = gamecopy.GameCopy(root).level_bundles(lv, 'terrain-00.fbrb')
    tool = None
    path = None
    for bundle, d in reversed(list(zip(layers, (dest, dest + '.patch')))):
        if not os.path.isdir(d):
            log(f'{lv}: unpacking {os.path.relpath(bundle, root)}')
            tool = tool or navkit.find_tool(root, 'FbrbTool.exe', 'moh-fbrb-tool')
            unpack_once(tool, bundle, d)
        path = path or terrain.find(d, name)
    if path is None:
        return None
    return terrain.read(path)


def terrain_from_samples(root, lv, stride=2):
    """A height-field mesh from NavKit's in-game samples: per column the highest surface that hit terrain,
    every <stride>-th sample. Returns (vertices (n, 3), triangles (m, 3) indices) or None."""
    import navsample
    import navwalk
    path = None
    for name in (f'{lv}_navkit.bin', f'{lv}_full.bin'):
        p = os.path.join(root, 'work', 'navsample', name)
        if os.path.isfile(p):
            path = p
            break
    if path is None:
        return None
    s, _ = navsample.load(path)
    terrain = (s.hit[:, 0] == navwalk.TERRAIN_SHAPE_VTABLE) if s.hit is not None and len(s.hit) else np.ones(len(s.y), bool)
    cols = s.nx * s.nz
    column = np.repeat(np.arange(cols), np.diff(s.start[:cols + 1]))        # column of every surface
    top = np.full(cols, -np.inf, dtype=np.float32)
    np.maximum.at(top, column[terrain], s.y[terrain])                         # highest terrain surface per column
    full = np.where(np.isfinite(top), top, np.nan).reshape(s.nz, s.nx)
    ii = np.arange(0, s.nx, stride)
    jj = np.arange(0, s.nz, stride)
    h = full[np.ix_(jj, ii)]
    xs = s.min_x + ii * s.pitch
    zs = s.min_z + jj * s.pitch
    X, Z = np.meshgrid(xs, zs)
    verts = np.stack([X.ravel(), h.ravel(), Z.ravel()], axis=1).astype(np.float32)
    nz, nx = h.shape
    idx = np.arange(nz * nx).reshape(nz, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    ok = ~(np.isnan(h[:-1, :-1]) | np.isnan(h[:-1, 1:]) | np.isnan(h[1:, :-1]) | np.isnan(h[1:, 1:])).ravel()
    # no triangles across cliffs or between stacked surfaces (a step of more than 4 m between neighbours)
    hv = h.ravel()
    steep = (np.abs(hv[a] - hv[b]) > 4) | (np.abs(hv[a] - hv[c]) > 4) | (np.abs(hv[d] - hv[b]) > 4) | (np.abs(hv[d] - hv[c]) > 4)
    ok &= ~steep
    tris = np.concatenate([np.stack([a, c, b], 1)[ok], np.stack([b, c, d], 1)[ok]]).astype(np.uint32)
    verts[np.isnan(verts[:, 1]), 1] = 0
    return verts, tris
