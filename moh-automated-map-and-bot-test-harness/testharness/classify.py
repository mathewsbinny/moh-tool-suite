"""Which level points are infantry points. The infantry tests (reachability, the spawn -> target battery, orders) run on
infantry path data, so they take infantry points only; every other point is counted by its class, not tested.

From the entities (levelinfo.points):
  spawner marker   a SoldierSpawnEntityData with AlternativeSpawnPoints: soldiers appear at its alternate points
                   (tested), not at the spawner's own transform, which is a map marker (often tens of metres away)
  vehicle: <kind>  a soldier spawn with a Vehicle (the soldier starts in it), and the alternate points of such a
                   spawner; the kind from the vehicle spawn's blueprint: land, sea, air, stationary weapon
  other map        a spawn whose LocationNameSid names another map and that does not stand on the reference's
                   walkable ground (ONS_MP_004 holds 56 AI spawners named ONS_MP_005GR_*, 60 m above its islands); a
                   spawn named for another map that stands on this map's ground is tested (names were copied between
                   ports: ONS_MP_005GR's MP_008_* alternates)
  objective marker a MissionObjectiveEntityData: the HUD marker of an objective, placed above it
  base             a capture point on a level with M-COM stations (Rush, Onslaught): the teams' deployment bases,
                   not targets; on other levels (Conquest) capture points are the targets
From the ground (by_ground, once the reference path data is loaded; the same classes for every file of a level):
  water            over water deeper than WATER_DEPTH, within WATER_DY of its surface, with no walkable cell of the
                   reference at the point (a boat's patrol: "Sea_1st_Patrol")
  airborne         more than AIR_DY above the terrain, the water and every walkable cell of the reference beneath it
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

import numpy as np

if TYPE_CHECKING:
    from mohcore import level, navdata
    from mohcore.terrain import Heightfield
    from mohcore.water import Water

    from .checks import Point

INFANTRY = 'infantry'
OTHER_MAP_UNSURE = 'other map?'     # named for another map: decided by the ground (by_ground)
AIR_DY = 10.0
WATER_DEPTH, WATER_DY = 1.0, 5.0
COLUMN_XZ = 2.0                     # walkable cells this close across count as "beneath" a point


def world(name: str | None) -> str | None:
    """The map a level or location name belongs to: 'mp_005' for ONS_MP_005GR, MP_005SR, ONS_MP_005GR_3; None."""
    m = re.match(r'(?:levels/)?(?:ons_|nam_)?((?:mp|sp)_\d+(?:_b)?)', (name or '').lower())
    return m.group(1) if m else None


def vehicle_kind(blueprint: str | None) -> str:
    """The vehicle class of a vehicle spawn's blueprint path: 'land', 'sea', 'air', 'stationary weapon', or
    'other'."""
    b = (blueprint or '').lower()
    for key, kind in (('objects/vehicles/sea/', 'sea'), ('objects/vehicles/air/', 'air'), ('objects/vehicles/land/', 'land'),
                      ('objects/weapons/stationary/', 'stationary weapon')):
        if b.startswith(key):
            return kind
    return 'other'


def _value(e: level.Entity, name: str) -> str | None:
    from mohcore import dbx
    c = e.element.child(name)
    return dbx.value(c) if c is not None and c.children is None else None


def _refs(e: level.Entity, name: str) -> list[str]:
    c = e.element.child(name)
    # the filter drops the elements without a ref, which mypy cannot see through the repeated attr() call
    return cast(list[str], [i.attr('ref') for i in (c.children or ()) if i.attr('ref') and i.attr('ref') != 'null']) if c is not None else []


def spawn_classes(entities: Sequence[level.Entity], lv: str) -> dict[int, tuple[str, str]]:
    """{entity index: (class, reason)} for the level's soldier spawns."""
    by_guid = {e.guid.lower(): e for e in entities}
    here = world(lv)
    out: dict[int, tuple[str, str]] = {}
    parent: dict[int, level.Entity] = {}
    for e in entities:
        for r in _refs(e, 'AlternativeSpawnPoints'):
            if r.lower() in by_guid:
                parent[by_guid[r.lower()].index] = e

    def own(e: level.Entity) -> tuple[str, str] | None:
        veh = _refs(e, 'Vehicle')
        if veh:
            v = by_guid.get(veh[0].lower())
            c = v.element.child('Vehicle') if v is not None else None
            bp = (c.attr('ref') or '').rsplit('/', 1)[0] if c is not None and c.attr('ref') else ''
            return f'vehicle: {vehicle_kind(bp)}', f'starts in {bp or "a vehicle"}'
        sid = _value(e, 'LocationNameSid')
        w = world(sid)
        if here and w and w != here:
            return OTHER_MAP_UNSURE, f'LocationNameSid {sid}'
        return None
    for e in entities:
        if e.category != 'soldier spawn':
            continue
        cls = own(e)
        if cls is None and e.index in parent:
            p = own(parent[e.index])
            if p is not None:
                cls = (p[0], f'alternate point of "{parent[e.index].name}": {p[1]}')
        if cls is None and _refs(e, 'AlternativeSpawnPoints'):
            cls = ('spawner marker', f'its {len(_refs(e, "AlternativeSpawnPoints"))} alternate points are tested instead')
        out[e.index] = cls or (INFANTRY, '')
    return out


def target_class(e: level.Entity, rush_like: bool) -> tuple[str, str]:
    """(class, reason) of an AI target: mission objectives are HUD markers, capture points on a level with M-COM
    stations are bases; the rest are infantry targets."""
    if e.short_type == 'MissionObjectiveEntityData':
        return 'objective marker', 'a HUD marker'
    if e.short_type == 'CapturePointEntityData' and rush_like:
        return 'base', 'a deployment base on a level with M-COM stations'
    return INFANTRY, ''


class Ground:
    """Terrain, water and the reference's walkable cells under a point."""

    def __init__(self, nav: navdata.NavData | None = None, heightfield: Heightfield | None = None,
                 water: Water | None = None) -> None:
        self.nav, self.hf, self.water = nav, heightfield, water
        self._tri: np.ndarray | None = None
        if water is not None and len(water):
            v = np.asarray(water.vertices, float)
            t = np.asarray(water.triangles, np.int64)
            self._tri = v[t]                                     # (m, 3, 3)
            self._lo = self._tri[:, :, [0, 2]].min(1)
            self._hi = self._tri[:, :, [0, 2]].max(1)

    def terrain(self, x: float, z: float) -> float | None:
        """The terrain height at (x, z), or None without terrain or outside it."""
        if self.hf is None:
            return None
        if not (self.hf.min[0] <= x <= self.hf.max[0] and self.hf.min[2] <= z <= self.hf.max[2]):
            return None
        return float(self.hf.height(np.array([x]), np.array([z]))[0])

    def water_surface(self, x: float, z: float) -> float | None:
        """The highest water surface over (x, z), or None."""
        if self._tri is None:
            return None
        k = np.nonzero((self._lo[:, 0] <= x) & (x <= self._hi[:, 0]) & (self._lo[:, 1] <= z) & (z <= self._hi[:, 1]))[0]
        best: float | None = None
        for t in self._tri[k]:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
            d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
            if abs(d) < 1e-9:
                continue
            l1 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d
            l2 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) >= -1e-6:
                y = l1 * ay + l2 * by + l3 * cy
                best = y if best is None else max(best, y)
        return best

    def cells(self, x: float, z: float) -> np.ndarray:
        """Heights of the reference's walkable cells within COLUMN_XZ across."""
        n = self.nav
        if n is None or not len(n.cell_xz):
            return np.zeros(0)
        m = (np.abs(n.cell_xz[:, 0] - x) <= COLUMN_XZ) & (np.abs(n.cell_xz[:, 1] - z) <= COLUMN_XZ)
        return n.cell_y[m]

    def stands(self, p: Sequence[float]) -> bool:
        """The point is within 2.5 m in height of a walkable cell of the reference beneath it."""
        cells = self.cells(p[0], p[2])
        return bool(len(cells) and np.min(np.abs(cells - p[1])) <= 2.5)

    def classify(self, p: Sequence[float]) -> tuple[str, str]:
        """(class, reason) of an infantry point by what is under it."""
        x, y, z = p
        cells = self.cells(x, z)
        standing = cells[np.abs(cells - y) <= 2.5]
        t = self.terrain(x, z)
        w = self.water_surface(x, z)
        if not len(standing) and w is not None and (t is None or w - t >= WATER_DEPTH) and abs(y - w) <= WATER_DY:
            return 'water', f'water surface {w:.0f} m' + (f', {w - t:.0f} m deep' if t is not None else '')
        under = [h for h in ([t] if t is not None else []) + ([w] if w is not None else []) + cells[cells <= y + 2.5].tolist()]
        if under and y - max(under) > AIR_DY:
            return 'airborne', f'{y - max(under):.0f} m above the ground, water or path data beneath'
        return INFANTRY, ''


def by_ground(points: Iterable[Point], ground: Ground) -> None:
    """Reclassify the infantry points of a list by what is under them (in place)."""
    for p in points:
        if p.cls == OTHER_MAP_UNSURE:
            if ground.stands(p.pos):
                p.cls, p.why = INFANTRY, ''
            else:
                p.cls = 'other map'
                continue
        if p.cls == INFANTRY:
            p.cls, p.why = ground.classify(p.pos)


def split(points: Sequence[Point]) -> tuple[list[Point], dict[str, int]]:
    """(infantry points, {class: count} of the others)."""
    from collections import Counter
    keep = [p for p in points if p.cls == INFANTRY]
    return keep, dict(Counter(p.cls for p in points if p.cls != INFANTRY))


def excluded_text(counts: Mapping[str, int]) -> str:
    """The not-infantry counts in words, largest first: '12 vehicle: land, 3 airborne'."""
    return ', '.join(f'{v} {k}' for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
