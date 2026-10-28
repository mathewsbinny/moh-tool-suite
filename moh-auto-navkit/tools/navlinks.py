"""NavKit: links and route edges of a navigation side file (DESIGN.md 5.1), written into a path-data file's route
graph (navkit apply, after the build - or on a copy of DICE's file for a DICE base).

  link-areas a b            a two-way route edge between the nodes nearest a and b (joins two areas / islands)
  route-edge a b add        the same, one-way when "two-way" is false
  route-edge a b remove     removes the edge(s) between the nodes nearest a and b

A point joins the route node nearest to it (within 15 m horizontally and 4 m in height, as the path test
joins points; node heights are ground + 0.95 m). An edge lives in one sector and joins two of its nodes, so
the two nodes are looked for in the same sector: every sector with a node near each point is tried and the
closest pair wins (border nodes, which belong to two sectors, let a link cross a sector edge). Costs are as
the generator writes them: 0x80000000 | floor(3-D distance * 16). Edges stay sorted by source node and each
node's first-edge index (`a`: first outgoing edge + 1, 0 when it has none) is recomputed - the rule every
DICE and NavKit file follows.
"""
import math

import numpy as np

import hpdtool

JOIN_XZ, JOIN_Y, Y_OFFSET = 15.0, 4.0, 0.95
GRAPH_OPS = ('link-areas', 'route-edge')


def cost_tag(p, q):
    return 0x80000000 | int(math.floor(math.dist(p, q) * 16))


def _nearest(sector, p):
    """(local index, horizontal distance) of the sector's node nearest p within the join limits, or None."""
    if not sector.nodes:
        return None
    pos = np.array([n.pos for n in sector.nodes], dtype=np.float64)
    d = np.hypot(pos[:, 0] - p[0], pos[:, 2] - p[2])
    d = np.where(np.abs(pos[:, 1] - Y_OFFSET - p[1]) <= JOIN_Y, d, np.inf)
    k = int(np.argmin(d))
    return (k, float(d[k])) if d[k] <= JOIN_XZ else None


def find_pair(f, a, b):
    """(sector index, local node a, local node b) - the closest pair of nodes in one sector, or None."""
    best = None
    for si, s in enumerate(f.sectors):
        x0, x1, _, _, z0, z1 = s.bounds
        reach = JOIN_XZ + 1
        if not (x0 - reach <= a[0] <= x1 + reach and z0 - reach <= a[2] <= z1 + reach):
            continue
        na, nb = _nearest(s, a), _nearest(s, b)
        if na and nb and na[0] != nb[0]:
            score = na[1] + nb[1]
            if best is None or score < best[0]:
                best = (score, si, na[0], nb[0])
    return None if best is None else best[1:]


def recompute_first_edges(sector):
    """Edges sorted by source node (stable: existing order kept within a source); each node's `a` set."""
    sector.edges.sort(key=lambda e: e.src)
    first = {}
    for k, e in enumerate(sector.edges):
        first.setdefault(e.src, k)
    for n, node in enumerate(sector.nodes):
        node.a = first[n] + 1 if n in first else 0


def apply(f, operations, log=print):
    """Applies the graph operations of a side file to a parsed path-data file (hpdtool.HpdFile) in place.
    Returns one result per graph operation: (operation index, applied: bool, message)."""
    results, touched = [], set()
    for k, o in enumerate(operations):
        name = o.get('op')
        if name not in GRAPH_OPS:
            continue
        a, b = o['a'], o['b']
        pair = find_pair(f, a, b)
        if pair is None:
            results.append((k, False, f'{name}: no sector has route nodes near both points (within {JOIN_XZ:.0f} m) - '
                                      f'split it at the sector border, or put the points nearer the route graph'))
            continue
        si, ia, ib = pair
        s = f.sectors[si]
        pa, pb = s.nodes[ia].pos, s.nodes[ib].pos
        two_way = o.get('two-way', True)
        dirs = [(ia, ib), (ib, ia)] if two_way else [(ia, ib)]
        length = math.dist(pa, pb)
        limit = next((n.value for n in s.nodes if n.value), 15.0)
        warn = f' - longer than the file\'s DistEdgeMax ({limit:.0f} m): the game may not use it' if length > limit else ''
        if name == 'link-areas' or o.get('action') == 'add':
            added = 0
            have = {(e.src, e.dst) for e in s.edges}
            for src, dst in dirs:
                if (src, dst) not in have:
                    s.edges.append(hpdtool.Edge(cost_tag(s.nodes[src].pos, s.nodes[dst].pos), src, dst))
                    added += 1
            touched.add(si)
            results.append((k, True, f'{name}: {added} edge(s) added in sector 0x{s.id:04X}, {length:.1f} m'
                                     f'{" (already there)" if not added else ""}{warn}'))
        else:
            before = len(s.edges)
            drop = set(dirs)
            s.edges[:] = [e for e in s.edges if (e.src, e.dst) not in drop]
            removed = before - len(s.edges)
            touched.add(si)
            results.append((k, removed > 0, f'route-edge remove: {removed} edge(s) removed in sector 0x{s.id:04X}'
                                            if removed else f'route-edge remove: no edge between those nodes in sector 0x{s.id:04X}'))
    for si in touched:
        recompute_first_edges(f.sectors[si])
    for _, ok, msg in results:
        log(('  ' if ok else '  NOT APPLIED: ') + msg)
    return results
