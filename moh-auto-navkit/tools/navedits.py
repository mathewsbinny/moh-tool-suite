"""NavKit: the walkability edits of a navigation side file (`<level>.navtune.json`, DESIGN.md section 5.1) as
inputs to the generator - which sampled surfaces are forced walkable or unwalkable, extra seeds, and the
points whose islands are dropped.

  walkable / unwalkable  every sampled surface inside the region (its outline or box in x/z, and the height
                         range) is forced walkable (slope and clearance rules skipped) or unwalkable; later
                         operations win where regions overlap
  seed                   a seed at the point: its island is kept (navwalk's seeding, as soldier spawns are)
  unseed                 the island the point is on is dropped after seeding (even if it has other seeds)

Links, route edges and traversal annotations are not walkability: navkit apply handles them after the build.
"""
import numpy as np
from matplotlib.path import Path

WALKABILITY = ('walkable', 'unwalkable', 'seed', 'unseed')


def surface_positions(s):
    """(x, z, y) of every sampled surface."""
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x = s.min_x + (col % s.nx) * s.pitch
    z = s.min_z + (col // s.nx) * s.pitch
    return x, z, s.y.astype(np.float64)


def region_mask(x, z, y, region):
    if 'box' in region:
        x0, y0, z0, x1, y1, z1 = region['box']
        return (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1) & (y >= y0) & (y <= y1)
    lo, hi = region['y']
    poly = np.asarray(region['polygon'], dtype=np.float64)
    m = (y >= lo) & (y <= hi) & (x >= poly[:, 0].min()) & (x <= poly[:, 0].max()) & (z >= poly[:, 1].min()) & (z <= poly[:, 1].max())
    if m.any():
        idx = np.nonzero(m)[0]
        inside = Path(poly).contains_points(np.stack([x[idx], z[idx]], axis=1))
        m[idx] = inside
    return m


def edits_for(s, operations):
    """dict(force_walkable, force_unwalkable: bool per surface; seeds, unseeds: lists of (x, y, z); counts)."""
    x, z, y = surface_positions(s)
    fw = np.zeros(len(y), dtype=bool)
    fu = np.zeros(len(y), dtype=bool)
    seeds, unseeds = [], []
    counts = {}
    for o in operations:
        name = o.get('op')
        if name not in WALKABILITY:
            continue
        counts[name] = counts.get(name, 0) + 1
        if name in ('walkable', 'unwalkable'):
            m = region_mask(x, z, y, o['region'])
            if name == 'walkable':
                fw |= m
                fu &= ~m
            else:
                fu |= m
                fw &= ~m
        elif name == 'seed':
            seeds.append(tuple(o['at']))
        else:
            unseeds.append(tuple(o['at']))
    return {'force_walkable': fw, 'force_unwalkable': fu, 'seeds': seeds, 'unseeds': unseeds, 'counts': counts,
            'surfaces_walkable': int(fw.sum()), 'surfaces_unwalkable': int(fu.sum())}
