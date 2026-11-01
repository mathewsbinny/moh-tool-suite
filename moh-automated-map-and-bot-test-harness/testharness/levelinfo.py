"""What the harness reads about a level, from files only (Project OPFOR's game copy and work folder; never written).

    info = gather(root, lv, cache_dir, log)      LevelInfo: points, AI layer, load set, assets
    files = pathdata_files(root, lv, which)      [(key, path)]: 'dice', 'installed', 'navkit'

Two sources, on purpose:
  - points (spawns, AI targets, orders) come from the level as NavKit prepares it (work\\levels\\<lv>, core
    level.load without geometry, ordercheck.order_points on its XML) - positions are what reachability needs, and
    the M-COM objects are known from their blueprints;
  - the AI layer the game will load (HasAI, AI spawners and their templates, the SectorOffsets assets, the load set)
    comes from the game copy's own bundles (mohcore.fbrb, no unpacking): OPFOR's import steps change those bundles
    after a level was prepared (ONS_MP_002's templates and assets, 2026-10-21), so the work copy can be out of date.
    A work copy that differs from the game's level partition is reported (re-prepare: navkit.py prepare <level>).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import struct
import sys
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, cast

from .checks import Point

if TYPE_CHECKING:
    from mohcore import dbx, level
    from mohcore.terrain import Heightfield
    from mohcore.water import Water

Log = Callable[..., object]
Asset = tuple[int, int] | str | None    # a SectorOffsets asset: (x, z), 'unread' (present, values not read), None

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
NAVKIT = os.path.join(SUITE, 'moh-auto-navkit')
for p in (os.path.join(SUITE, 'core'), NAVKIT, os.path.join(NAVKIT, 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

PLACEHOLDER = 'objects/soldiers/templates/defaultsoldier'     # "FAIL No Template": aims, never fires (MP log, test A')
KINDS = ('Infantry', 'Vehicle')


@dataclass
class Spawner:
    guid: str
    name: str
    team: Optional[int]
    ai: bool
    enabled: bool
    template: Optional[str]          # partition path of the Template ref (lower case), or None for null
    kit: Optional[str]               # partition path of the SoldierKit ref


@dataclass
class LevelInfo:
    lv: str
    name: str = ''
    sources: List[Point] = field(default_factory=list)       # soldier spawns
    targets: List[Point] = field(default_factory=list)       # AreaOrders, M-COMs, capture points, mission objectives
    orders: List[Point] = field(default_factory=list)        # AI orders and patrol waypoints (ordercheck)
    has_ai: Optional[bool] = None
    spawners: List[Spawner] = field(default_factory=list)
    area_orders: int = 0
    load_set: List[tuple[str, str, bool]] = field(default_factory=list)      # (target as package.mft writes it, path, exists)
    entries: dict[str, set[str]] = field(default_factory=dict)              # bundle path -> set of lower-case entry names
    assets: dict[str, Asset] = field(default_factory=dict)  # 'Infantry' / 'Vehicle' -> (x, z), None (absent), 'unread'
    template_refs: dict[str, list[str] | None] = field(default_factory=dict)   # template partition -> [partitions it refers to]
    installed: Optional[str] = None                          # the path data the game loads (infantry), or None
    notes: List[str] = field(default_factory=list)
    level_source: str = ''                                   # bundle the AI layer was read from
    level_partition_sha1: Optional[str] = None
    heightfield: Optional[Heightfield] = None                # terrain.Heightfield, for the ground classes
    water: Optional[Water] = None                            # water.Water

    def in_load_set(self, partition: str) -> str | None:
        """The first load-set bundle holding <partition>.dbx (lower-case match), or None."""
        name = partition.lower() + '.dbx'
        for _t, path, ok in self.load_set:
            if ok and name in self.entries.get(path, ()):
                return path
        return None


# ------------------------------------------------------------------------------------------------ caches

def _stat_key(path: str) -> str:
    st = os.stat(path)
    return hashlib.sha1(f'{os.path.abspath(path).lower()}|{st.st_size}|{st.st_mtime_ns}'.encode()).hexdigest()[:16]


def _save(cp: str, data: bytes | object) -> None:
    """A cache entry written whole or not at all (a temporary file, then os.replace, as runs.save): entries are keyed
    on the bundle's state, so a cut-off one would otherwise be read for as long as the bundle is unchanged."""
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    tmp = cp + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data if isinstance(data, bytes) else json.dumps(data).encode())
    os.replace(tmp, cp)


def bundle_names(path: str, cache_dir: str | None) -> set[str]:
    """The lower-case entry names of a bundle, cached per bundle state."""
    from mohcore import fbrb
    if cache_dir:
        cp = os.path.join(cache_dir, 'bundles', _stat_key(path) + '.names.json')
        if os.path.isfile(cp):
            return set(json.load(open(cp)))
    names = set(fbrb.names(path))
    if cache_dir:
        _save(cp, sorted(names))
    return names


def bundle_read(path: str, wanted: Iterable[str], cache_dir: str | None) -> dict[str, bytes]:
    """{name: bytes} of entries (names as the bundle writes them), cached per bundle state and entry."""
    from mohcore import fbrb
    out: dict[str, bytes] = {}
    need: list[str] = []
    key = _stat_key(path) if cache_dir else None
    for n in wanted:
        cp = os.path.join(cache_dir, 'bundles', cast(str, key), n.replace('/', '__')) if cache_dir else None
        if cp and os.path.isfile(cp):
            out[n] = open(cp, 'rb').read()
        else:
            need.append(n)
    if need:
        got = fbrb.read(path, need)
        for n, b in got.items():
            out[n] = b
            if cache_dir:
                _save(os.path.join(cache_dir, 'bundles', cast(str, key), n.replace('/', '__')), b)     # set with cache_dir
    return out


# ------------------------------------------------------------------------------------------------ game copy

def bundle_path(root: str, target: str) -> str:
    """package.mft's 'game:levels/x/level-00.fbrb' or 'game:patch/levels/x/...' as a file path (under MP\\dist\\win32)."""
    from mohcore import gamecopy
    _, _, rel = target.partition(':')
    return os.path.join(gamecopy.GameCopy(root).dist, *rel.split('/'))


def load_set(root: str, lv: str, mft: str | None = None) -> list[tuple[str, str, bool]]:
    """[(target, path, exists)]: the bundles that load with the level's level-00.fbrb (its BundleSource block in
    MP\\dist\\win32\\package.mft: the patch's copy, the level's own, its map's common_mp_<nn>, common_mp); [] when
    there is no block."""
    from mohcore import gamecopy
    mft = mft or os.path.join(gamecopy.GameCopy(root).dist, 'package.mft')
    lines = open(mft, encoding='utf-8', errors='replace').read().splitlines()
    src = f'BundleSource game:levels/{lv}/level-00.fbrb'.lower()
    for i, line in enumerate(lines):
        if line.strip().lower() == src:
            out: list[tuple[str, str, bool]] = []
            for x in lines[i + 1:]:
                if not x.startswith('BundleTarget '):
                    break
                t = x.split(' ', 1)[1].strip()
                p = bundle_path(root, t)
                out.append((t, p, os.path.isfile(p)))
            return out
    return []


def _refs(el: dbx.Element, out: set[str]) -> None:
    for c in el.children or ():
        r = c.attr('ref')
        if r and r != 'null' and '/' in r:
            out.add(r.rsplit('/', 1)[0].lower())
        if c.children is not None:
            _refs(c, out)


def _ref_partition(el: dbx.Element, name: str) -> str | None:
    c = el.child(name)
    r = c.attr('ref') if c is not None else None
    return r.rsplit('/', 1)[0].lower() if r and r != 'null' and '/' in r else None


def ai_layer(info: LevelInfo, data: bytes) -> None:
    """HasAI, AI spawners and AreaOrder count from the level partition's bytes (binary DBX)."""
    from mohcore import dbx
    f = dbx.parse(data)
    teams: dict[str | None, int] = {'Team1': 1, 'Team2': 2, 'TeamNeutral': 0}
    for el in f.instances:
        t = (el.attr('type') or '').rsplit('.', 1)[-1]
        if t == 'LevelData':
            c = el.child('HasAI')
            info.has_ai = (dbx.value(c) == 'true') if c is not None else None
        elif t == 'SoldierSpawnEntityData':
            v: Callable[[str], str | None] = lambda n: (dbx.value(cast('dbx.Element', el.child(n)))
                                                        if el.child(n) is not None else None)
            info.spawners.append(Spawner(el.attr('guid') or '', v('Name') or '', teams.get(v('Team')), v('PlayerType') == 'SSAiPlayer',
                                         v('Enabled') == 'true', _ref_partition(el, 'Template'), _ref_partition(el, 'SoldierKit')))
        elif t == 'AreaOrderEntityData':
            info.area_orders += 1


def game_level(root: str, lv: str, info: LevelInfo, cache_dir: str | None, log: Log) -> None:
    """The AI layer, assets and load set from the game copy's bundles."""
    from mohcore import dbx
    info.load_set = load_set(root, lv)
    if not info.load_set:
        info.notes.append(f'no BundleSource block for levels/{lv}/level-00.fbrb in package.mft')
    for _t, p, ok in info.load_set:
        if ok:
            info.entries[p] = bundle_names(p, cache_dir)
        else:
            info.notes.append(f'load-set bundle not on disk: {_t}')
    own = [p for t, p, ok in info.load_set if ok and f'levels/{lv}/level-00.fbrb' in t.lower()]
    part = f'levels/{lv}.dbx'
    # the level's own bundles first (the patch's copy before the base one, as package.mft lists them), then the rest
    # of the load set: five MOH DLC modes (mp_01_elimination, mp_02_koth2, mp_03_elimination, mp_04_koth,
    # mp_07_koth) have their partition only in their map's common_mp_<nn>
    for p in own + [p for _t, p, ok in info.load_set if ok and p not in own]:
        names = info.entries[p]
        if part in names:
            real = next(n for n in _real_names(p, cache_dir) if n.lower() == part)
            info.level_source = p
            data = bundle_read(p, [real], cache_dir)[real]
            ai_layer(info, data)
            info.name = os.path.basename(real)[:-4]
            info.level_partition_sha1 = hashlib.sha1(data).hexdigest()
            break
    else:
        info.notes.append('the level partition was not found in its load set')
    for kind in KINDS:
        name = f'levels/{lv}/precomputed/{kind.lower()}/sectoroffsets.dbx'
        holder = next((p for p in own if name in info.entries[p]), None)
        if holder is None:
            info.assets[kind] = None
            continue
        try:
            real = next(n for n in _real_names(holder, cache_dir) if n.lower() == name)
            el = dbx.parse(bundle_read(holder, [real], cache_dir)[real]).instances[0]
            # a missing child is None: dbx.value raises AttributeError on it, caught below as 'unread'
            info.assets[kind] = (int(dbx.value(cast('dbx.Element', el.child('XOffset')))),
                                 int(dbx.value(cast('dbx.Element', el.child('ZOffset')))))
        except (OSError, ValueError, KeyError, IndexError, StopIteration, AttributeError, struct.error,
                zlib.error) as e:                        # present but not readable as expected (no XOffset child...)
            info.assets[kind] = 'unread'
            info.notes.append(f'{kind} SectorOffsets asset not read: {e}')
    # the templates the AI spawners use: read each one's partition where the load set has it, for its own refs
    for tpl in sorted({s.template for s in info.spawners if s.ai and s.template}):
        holder = info.in_load_set(tpl)
        if holder is None:
            info.template_refs[tpl] = None                   # not loadable: reported by the load-set check
            continue
        try:
            real = next(n for n in _real_names(holder, cache_dir) if n.lower() == tpl + '.dbx')
            refs: set[str] = set()
            for el in dbx.parse(bundle_read(holder, [real], cache_dir)[real]).instances:
                _refs(el, refs)
            info.template_refs[tpl] = sorted(refs - {tpl})
        except (OSError, ValueError, KeyError, IndexError, StopIteration, struct.error, zlib.error) as e:
            info.template_refs[tpl] = []
            info.notes.append(f'template {tpl} not read: {e}')
    from mohcore import gamecopy
    inst = gamecopy.GameCopy(root).pathdata(lv)
    info.installed = inst if os.path.isfile(inst) else None


def _real_names(path: str, cache_dir: str | None) -> list[str]:
    """Entry names with their case (bundle_names keeps lower case only)."""
    from mohcore import fbrb
    if cache_dir:
        cp = os.path.join(cache_dir, 'bundles', _stat_key(path) + '.real.json')
        if os.path.isfile(cp):
            return cast(list[str], json.load(open(cp)))
    names = [e.name for e in fbrb.entries(path) if not e.deleted]
    if cache_dir:
        _save(cp, names)
    return names


# ------------------------------------------------------------------------------------------------ work copy (points)

def level_xml(root: str, lv: str) -> str | None:
    """The converted level partition NavKit's prepare wrote (Package copy first, as the game loads it), or None."""
    for b in ('pkg', 'dist'):
        for f in glob.glob(os.path.join(root, 'work', 'levels', lv, b, 'levels', '*.xml')):
            if os.path.splitext(os.path.basename(f))[0].lower() == lv:
                return f
    return None


def points(root: str, lv: str, info: LevelInfo, log: Log) -> level.Level:
    """Spawns, AI targets and orders from the prepared level (core level.load, geometry off)."""
    from mohcore import level
    from . import classify
    import ordercheck
    try:
        lvl = level.load(lv, root, log=log, geometry=False)
    except Exception as err:
        # NavKit's prepare stops on a level whose partition is only in its Package bundle (ONS_MP_005GR: its Dist
        # bundle holds MP_005's partitions); it has unpacked both bundles by then, so the loader reads them
        if not os.path.isdir(os.path.join(root, 'work', 'levels', lv, 'dist')):
            raise
        info.notes.append(f'NavKit\'s prepare stopped ({err}); the level was read from its unpacked bundles')
        lvl = level.load(lv, root, log=log, geometry=False)
    info.name = info.name or lvl.name
    seen: set[tuple[str, int, int, int]] = set()
    spawn_cls = classify.spawn_classes(lvl.entities, lv)
    rush_like = any(e.category == 'objective' and (e.blueprint or '').startswith('objects/objects/mpobjective') for e in lvl.entities)

    def add(lst: list[Point], kind: str, e: level.Entity, cls: tuple[str, str] = (classify.INFANTRY, '')) -> None:
        p = e.position
        if p is None or not any(abs(c) > 1e-3 for c in p):
            return
        key = (kind, round(float(p[0])), round(float(p[1])), round(float(p[2])))
        if key in seen:
            return
        seen.add(key)
        lst.append(Point(kind, e.name or '', (float(p[0]), float(p[1]), float(p[2])), e.team, e.ai, cls[0], cls[1]))
    for e in lvl.entities:
        if e.category == 'soldier spawn':
            add(info.sources, 'spawn', e, spawn_cls.get(e.index, (classify.INFANTRY, '')))
            continue
        cls = classify.target_class(e, rush_like)
        if e.short_type == 'AreaOrderEntityData':
            add(info.targets, 'AreaOrder', e, cls)
        elif e.category == 'objective' and (e.blueprint or '').startswith('objects/objects/mpobjective'):
            add(info.targets, 'M-COM', e, cls)
        elif e.short_type == 'CapturePointEntityData':
            add(info.targets, 'capture point', e, cls)
        elif e.short_type == 'MissionObjectiveEntityData':
            add(info.targets, 'objective', e, cls)
    x = level_xml(root, lv)
    if x:
        for kind, name, p in ordercheck.order_points(x):
            info.orders.append(Point(kind, name, tuple(float(c) for c in p)))
        # a work copy older than the game copy: say so (the AI layer is read from the game copy)
        dbxp = x[:-4] + '.dbx'
        sha = getattr(info, 'level_partition_sha1', None)
        if sha and os.path.isfile(dbxp) and hashlib.sha1(open(dbxp, 'rb').read()).hexdigest() != sha:
            info.notes.append(f'the prepared level ({os.path.relpath(dbxp, root)}) differs from the game copy\'s level '
                              f'partition - points are from the prepared copy; re-prepare it (navkit.py prepare {lv}) if '
                              f'the game copy was changed on purpose')
    else:
        info.notes.append('no converted level XML (navkit.py prepare): orders not read')
    # terrain and water, for the ground classes (classify.Ground); a level without them classifies by path data only
    try:
        info.heightfield = level.heightfield_of(root, lv, log)
    except Exception as e:  # noqa: BLE001 - optional terrain (FbrbTool unpack, terrain file): never stops the level
        info.notes.append(f'terrain not read: {e}')
    try:
        info.water = level.water_of(root, lv, log)
    except Exception as e:  # noqa: BLE001 - optional water (FbrbTool unpack, water files): never stops the level
        info.notes.append(f'water not read: {e}')
    return lvl


def gather(root: str, lv: str, cache_dir: str | None = None, log: Log = lambda *a: None) -> LevelInfo:
    """Everything the harness reads about a level: the AI layer, assets and load set from the game copy, then the
    points from the prepared level."""
    info = LevelInfo(lv)
    game_level(root, lv, info, cache_dir, log)
    points(root, lv, info, log)
    return info


# ------------------------------------------------------------------------------------------------ path data

def pathdata_files(root: str, lv: str, which: Sequence[str] = ('dice', 'navkit'),
                   navkit_source: str = 'auto') -> list[tuple[str, str]]:
    """[(key, path)] of the level's path-data files that exist:
      dice       DICE's original (navparity.reference_of: the kept .opfor-orig where NavKit's file is installed)
      installed  what the game loads now, when it is not DICE's file (a NavKit build installed)
      navkit     NavKit's build: navkit_source 'regress' (work\\navkit\\regress\\<lv>.hpd, navregress's latest),
                 'build' (work\\navkit\\<lv>\\pathdata.hpd, navkit.py build), 'auto' (regress, else build), or a
                 path with {lv}"""
    import navparity
    out: list[tuple[str, str]] = []
    from mohcore import gamecopy
    dice = navparity.reference_of(root, lv)
    inst = gamecopy.GameCopy(root).pathdata(lv)
    for key in which:
        if key == 'dice' and os.path.isfile(dice) and _is_dice(dice):
            out.append(('dice', dice))
        elif key == 'installed' and os.path.isfile(inst) and os.path.abspath(inst) != os.path.abspath(dice):
            out.append(('installed', inst))
        elif key == 'navkit':
            cands = {'regress': [os.path.join(root, 'work', 'navkit', 'regress', f'{lv}.hpd')],
                     'build': [os.path.join(root, 'work', 'navkit', lv, 'pathdata.hpd')]}
            cands['auto'] = cands['regress'] + cands['build']
            for p in cands.get(navkit_source, [navkit_source.replace('{lv}', lv)]):
                if os.path.isfile(p):
                    out.append(('navkit', p))
                    break
    return out


def _is_dice(path: str) -> bool:
    """DICE's file, not a NavKit build installed without a kept original: DICE's sectors carry 2009-2010 build dates
    (hpdtool's Sector.date, YYMMDD; DICE's PC files 2010, the console
    Onslaught files 2010); NavKit writes its build date (2026)."""
    import hpdtool
    try:
        f = hpdtool.parse(open(path, 'rb').read())
    except (OSError, ValueError, IndexError, struct.error):
        return False
    return bool(f.sectors) and all(s.date < 200000 for s in f.sectors[:5])


def sha1(path: str) -> str:
    """The file's SHA-1, hex."""
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

