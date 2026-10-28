"""The harness's offline test kinds, as functions of data already loaded (no file paths below the level of a path-data
file, so the quick tests can run them on navigation data built in the test).

Every test returns a Result: a status, flat numeric metrics (what runs are compared on), a one-line summary and
detail lines (capped). Statuses:
  pass   the expectation holds            warn   worth a look, not a failure
  fail   the expectation does not hold    info   a measurement without an expectation (a baseline)
  skip   not applicable here              hold   needs the game (an in-game test not yet run: ON HOLD)
  error  the test could not run (the exception is in the details and the log)

Test kinds:
  structure      hpdcheck's checks of the file (closed boundaries, edges, node order, zero-length edges)
  reachability   level points over the route graph: soldier spawns -> AI targets (AreaOrders, M-COMs, capture points,
                 mission objectives), each joined to its nearest route node as the path test does (navpath.join)
                 and followed along the directed edges; islands; against a reference file (DICE's), the share of
                 the pairs it connects that this file connects too
  battery        many point pairs through the path test (navpath.route): random ground points drawn from the
                 reference file's walkable cells with a fixed seed (the same pairs in every run), and spawn ->
                 target pairs; success, and route lengths against the reference's routes for the same pairs
  orders         every AI order position (AreaOrder, PatrolOrder and its waypoints, InterludeOrder, FireAtArea;
                 ordercheck.order_points) has a route node within the join limits, and lies on an area
  offsets        the sector offsets the file's own sector ids imply (OPFOR's apply_sector_offsets rule) against the
                 level's SectorOffsets asset
  parity         NavKit's DICE-parity measures (navparity.run_level), taken over unchanged
"""
import math
import zlib
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

STATUSES = ['pass', 'info', 'warn', 'skip', 'hold', 'fail', 'error']
MAX_DETAILS = 25
MIN_AGREEMENT = 0.75            # sector-offset rule: share of sectors that must agree (apply_sector_offsets.py)


@dataclass
class Result:
    status: str
    summary: str = ''
    metrics: dict = field(default_factory=dict)
    details: List[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)          # kept in the run for later comparison (pair bits ...)
    seconds: float = 0.0

    def as_dict(self):
        d = {'status': self.status, 'summary': self.summary, 'metrics': self.metrics,
             'details': self.details[:MAX_DETAILS] + ([f'... and {len(self.details) - MAX_DETAILS} more']
                                                      if len(self.details) > MAX_DETAILS else []),
             'seconds': round(self.seconds, 1)}
        if self.data:
            d['data'] = self.data
        return d


@dataclass
class Point:
    kind: str                       # 'spawn', 'AreaOrder', 'M-COM', 'capture point', 'objective', order kinds ...
    name: str
    pos: tuple                      # world x, y, z (y up)
    team: Optional[int] = None
    ai: Optional[bool] = None
    cls: str = 'infantry'           # classify.py: infantry points are tested, the others counted
    why: str = ''

    def label(self):
        t = f' team {self.team}' if self.team is not None else ''
        c = f' [{self.cls}: {self.why}]' if self.cls != 'infantry' else ''
        return f'{self.kind}{t} "{self.name or "?"}" at ({self.pos[0]:.0f}, {self.pos[1]:.0f}, {self.pos[2]:.0f}){c}'


def pct(a, b):
    return round(100.0 * a / b, 2) if b else 0.0


def worst(statuses):
    order = {'error': 6, 'fail': 5, 'warn': 4, 'hold': 3, 'skip': 0, 'info': 1, 'pass': 2}
    return max(statuses, key=lambda s: order.get(s, 0)) if statuses else 'skip'


# ------------------------------------------------------------------------------------------------ structure

def structure(problems):
    """problems: hpdcheck.check(path)'s {check: [descriptions]}."""
    n = {k: len(v) for k, v in problems.items()}
    total = sum(n.values())
    details = [f'{k}: {v[0]}' + (f' (and {len(v) - 1} more)' if len(v) > 1 else '') for k, v in problems.items() if v]
    return Result('fail' if total else 'pass',
                  f'{total} problem(s)' + (': ' + ', '.join(f'{k} {v}' for k, v in n.items() if v) if total else
                                           f' ({", ".join(problems)})'),
                  {'problems': total, **{k.replace(' ', '_'): v for k, v in n.items()}}, details)


# ------------------------------------------------------------------------------------------------ the route graph

def adjacency(nav):
    """(first edge per node, destinations) over the directed edges, sorted by source."""
    from mohcore import navpath
    start, nbr, _cost = navpath._adjacency(nav)
    return start, nbr


def reach_from(nav, node, adj=None):
    """The set of nodes reachable from node along the edges' directions."""
    start, nbr = adj or adjacency(nav)
    seen = {node}
    q = deque([node])
    while q:
        n = q.popleft()
        for k in range(start[n], start[n + 1]):
            m = int(nbr[k])
            if m not in seen:
                seen.add(m)
                q.append(m)
    return seen


STAND_XZ, STAND_DY = 1.2, 2.5          # a point stands on a walkable cell this close (m across / in height)


def _cell_index(nav):
    """Cells sorted by their 0.8 m grid key, for the lookups of join()."""
    idx = getattr(nav, '_harness_cells', None)
    if idx is None:
        from mohcore import navdata
        ij = np.round(nav.cell_xz / navdata.GRID).astype(np.int64)
        key = (ij[:, 0] + 50_000) * 100_000 + (ij[:, 1] + 50_000)
        order = np.argsort(key, kind='stable')
        idx = (key[order], order)
        nav._harness_cells = idx
    return idx


def join(nav, pos):
    """(node or None, horizontal metres) for a level point, the way the runtime starts a route: from the area the
    point stands on (the nearest walkable cell within STAND_XZ / STAND_DY, navdata's cells) - the nearest route node
    of that area's island; a point on no area, or whose island has no node within the limits, takes navpath.join's
    nearest node (15 m across, 4 m in height). Nearest-node alone joins points beside a small roof or detailed-zone
    island to it, though they stand on the main ground."""
    from mohcore import navdata, navpath
    if len(nav.cell_xz) and len(nav.node_pos):
        keys, order = _cell_index(nav)
        gi, gj = int(round(pos[0] / navdata.GRID)), int(round(pos[2] / navdata.GRID))
        cand = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                k = (gi + di + 50_000) * 100_000 + (gj + dj + 50_000)
                lo, hi = np.searchsorted(keys, k, 'left'), np.searchsorted(keys, k, 'right')
                cand.extend(order[lo:hi].tolist())
        best, best_d = None, np.inf
        for c in cand:
            dxz = math.hypot(nav.cell_xz[c, 0] - pos[0], nav.cell_xz[c, 1] - pos[2])
            dy = abs(nav.cell_y[c] - pos[1])
            if dxz <= STAND_XZ and dy <= STAND_DY and dxz + dy < best_d:
                best, best_d = c, dxz + dy
        if best is not None:
            isl = nav.areas[int(nav.cell_area[best])].island
            if isl >= 0:
                g = navpath.ground_nodes(nav)
                d = np.hypot(g[:, 0] - pos[0], g[:, 2] - pos[2])
                d = np.where((nav.node_island == isl) & (np.abs(g[:, 1] - pos[1]) <= navpath.JOIN_Y), d, np.inf)
                k = int(np.argmin(d))
                if d[k] <= navpath.JOIN_XZ:
                    return k, float(d[k])
    return navpath.join(nav, pos)


def joins(nav, points):
    """[(node or None, horizontal metres)] per point (join())."""
    return [join(nav, p.pos) for p in points]


def near_text(d):
    return f'nearest {d:.0f} m' if np.isfinite(d) else 'none within 4 m in height'


def baseline_cap(status, baseline):
    """On the reference file (DICE's, which works in the game) an absolute expectation that fails is a warning: the
    expectation is too strict for that level, the file is not broken."""
    return 'warn' if baseline and status == 'fail' else status


def reachability(nav, sources, targets, reference=None, max_parity_loss=5.0, baseline=False):
    """sources -> targets over the route graph. reference: the 'data' of the reference file's reachability result on
    the same points (its pair bits), for the parity measures. Pair bits: one character per (source, target), source
    major: '1' reached, '0' not, '-' a point without a route node near it."""
    adj = adjacency(nav)
    js, jt = joins(nav, sources), joins(nav, targets)
    cache = {}
    bits = []
    for (ns, _), s in zip(js, sources):
        if ns is not None and ns not in cache:
            cache[ns] = reach_from(nav, ns, adj)
        for nt, _ in jt:
            if ns is None or nt is None:
                bits.append('-')
            else:
                bits.append('1' if nt in cache[ns] else '0')
    bits = ''.join(bits)
    S, T = len(sources), len(targets)
    joined_s = sum(n is not None for n, _ in js)
    joined_t = sum(n is not None for n, _ in jt)
    pairs = bits.count('1') + bits.count('0')
    reached = bits.count('1')
    t_reached = [any(bits[i * T + j] == '1' for i in range(S)) for j in range(T)]
    s_reached = [any(bits[i * T + j] == '1' for j in range(T)) for i in range(S)]
    island_sizes = [i.nodes for i in nav.islands]
    main = int(np.argmax(island_sizes)) if island_sizes else -1
    on_main = lambda js_: sum(1 for n, _ in js_ if n is not None and int(nav.node_island[n]) == main)
    m = {
        'sources': S, 'targets': T, 'sources_joined_pct': pct(joined_s, S), 'targets_joined_pct': pct(joined_t, T),
        'pairs': pairs, 'pairs_reached_pct': pct(reached, pairs),
        'targets_reached_pct': pct(sum(t_reached), T), 'sources_reaching_pct': pct(sum(s_reached), S),
        'sources_on_main_pct': pct(on_main(js), S), 'targets_on_main_pct': pct(on_main(jt), T),
        'islands': len(nav.islands), 'large_islands': sum(n >= 25 for n in island_sizes),
        'main_island_nodes_pct': pct(max(island_sizes) if island_sizes else 0, len(nav.node_pos)),
    }
    for team in sorted({p.team for p in sources if p.team in (1, 2)}):
        idx = [i for i, p in enumerate(sources) if p.team == team]
        tb = [bits[i * T + j] for i in idx for j in range(T)]
        m[f'team{team}_pairs_reached_pct'] = pct(tb.count('1'), tb.count('1') + tb.count('0'))
    details = [f'no route node near {p.label()} ({near_text(d)})' for (n, d), p in zip(js + jt, sources + targets) if n is None]
    details += [f'reached from no spawn: {targets[j].label()}' for j in range(T) if not t_reached[j] and jt[j][0] is not None]
    status = 'info'
    if T and not S:
        status = 'skip'
    if reference and reference.get('bits') and len(reference['bits']) == len(bits) and '1' in reference['bits']:
        rb = reference['bits']
        both = sum(1 for a, b in zip(bits, rb) if a == '1' and b == '1')
        ref_n = rb.count('1')
        only_here = sum(1 for a, b in zip(bits, rb) if a == '1' and b != '1')
        m['reach_parity_pct'] = pct(both, ref_n)
        m['reach_over_pct'] = pct(only_here, max(bits.count('1'), 1))
        lost = [j for j in range(T) if any(rb[i * T + j] == '1' for i in range(S)) and not t_reached[j]]
        m['targets_lost'] = len(lost)
        details = [f'reached in the reference, from no spawn here: {targets[j].label()}' for j in lost] + details
        status = 'fail' if lost or m['reach_parity_pct'] < 100 - max_parity_loss else \
                 'warn' if m['reach_parity_pct'] < 100 else 'pass'
    elif status != 'skip':
        status = 'warn' if (T and not all(t_reached)) else 'pass'
    summ = (f'{S} spawns -> {T} targets: {m["pairs_reached_pct"]:.1f} % of {pairs} pairs reached, '
            f'{m["targets_reached_pct"]:.0f} % of targets from some spawn; {m["islands"]} islands, main '
            f'{m["main_island_nodes_pct"]:.0f} % of nodes')
    if 'reach_parity_pct' in m:
        summ += f'; {m["reach_parity_pct"]:.1f} % of the reference\'s pairs'
    return Result(baseline_cap(status, baseline), summ, m, details, {'bits': bits})


# ------------------------------------------------------------------------------------------------ path batteries

def random_points(nav, n, seed, level):
    """n ground points from the file's walkable cells, the same for the same file, seed and level."""
    if not len(nav.cell_xz):
        return []
    rng = np.random.default_rng([seed, zlib.crc32(level.encode())])
    idx = rng.choice(len(nav.cell_xz), size=min(n, len(nav.cell_xz)), replace=False)
    return [(float(nav.cell_xz[k, 0]), float(nav.cell_y[k]), float(nav.cell_xz[k, 1])) for k in idx]


def level_pairs(sources, targets, n, seed, level):
    """Up to n (spawn, target) position pairs, drawn with a fixed seed."""
    allp = [(s.pos, t.pos) for s in sources for t in targets]
    if len(allp) <= n:
        return allp
    rng = np.random.default_rng([seed, zlib.crc32(level.encode()), 1])
    return [allp[k] for k in sorted(rng.choice(len(allp), size=n, replace=False))]


def points_key(pairs):
    h = zlib.crc32(repr([tuple(round(c, 2) for c in a + b) for a, b in pairs]).encode())
    return f'{h:08x}'


def route_pairs(nav, pairs):
    """Per pair (found, length m, cost m, reason): the path test, skipped where the two points' route nodes are on
    different islands (the A* would only search the start island to its end)."""
    from mohcore import navpath
    out = []
    for a, b in pairs:
        na, _ = navpath.join(nav, a)
        nb, _ = navpath.join(nav, b)
        if na is None or nb is None:
            out.append((False, 0.0, 0.0, 'no node near'))
        elif nav.node_island[na] != nav.node_island[nb]:
            out.append((False, 0.0, 0.0, 'islands'))
        else:
            r = navpath.route(nav, a, b)
            out.append((r.found, r.length, r.cost, '' if r.found else 'one-way'))
    return out


def battery(name, pairs, routes, ref_routes=None, max_loss=2.0):
    """One battery's result from its routes on this file (route_pairs) and, when given, the reference file's routes
    over the same pairs."""
    n = len(pairs)
    found = [r[0] for r in routes]
    why = Counter(r[3] for r in routes if not r[0])
    m = {'pairs': n, 'found_pct': pct(sum(found), n), 'no_node_pct': pct(why['no node near'], n),
         'other_island_pct': pct(why['islands'], n), 'one_way_pct': pct(why['one-way'], n)}
    detour = [r[1] / max(math.dist(a, b), 1e-6) for (a, b), r in zip(pairs, routes) if r[0] and math.dist(a, b) > 5]
    if detour:
        m['detour_median'] = round(float(np.median(detour)), 3)
    bits = ''.join('1' if f else '0' for f in found)
    details, status = [], 'info'
    if ref_routes is not None and len(ref_routes) == n and any(r[0] for r in ref_routes):
        rf = [r[0] for r in ref_routes]
        ref_n = sum(rf)
        both = [k for k in range(n) if found[k] and rf[k]]
        m['found_of_ref_pct'] = pct(len(both), ref_n)
        m['extra_pct'] = pct(sum(1 for k in range(n) if found[k] and not rf[k]), n)
        ratio = [routes[k][1] / ref_routes[k][1] for k in both if ref_routes[k][1] > 1.0]
        if ratio:
            m['length_ratio_median'] = round(float(np.median(ratio)), 3)
            m['length_ratio_p90'] = round(float(np.percentile(ratio, 90)), 3)
        for k in range(n):
            if rf[k] and not found[k]:
                (a, b), r = pairs[k], routes[k]
                details.append(f'routed in the reference ({ref_routes[k][1]:.0f} m), not here ({r[3]}): '
                               f'({a[0]:.0f}, {a[1]:.0f}, {a[2]:.0f}) -> ({b[0]:.0f}, {b[1]:.0f}, {b[2]:.0f})')
        status = 'fail' if m['found_of_ref_pct'] < 100 - max_loss else 'warn' if m['found_of_ref_pct'] < 100 else 'pass'
    summ = f'{name} {n}: {m["found_pct"]:.1f} % routed'
    if 'found_of_ref_pct' in m:
        summ += f', {m["found_of_ref_pct"]:.1f} % of the reference\'s'
        if 'length_ratio_median' in m:
            summ += f', length x{m["length_ratio_median"]:.2f} (p90 x{m["length_ratio_p90"]:.2f})'
    return Result(status, summ, m, details, {'bits': bits, 'points': points_key(pairs)})


def merge(results, names):
    """One Result of several batteries: metrics prefixed with the battery's name, the worst status."""
    m, details, data = {}, [], {}
    for name, r in zip(names, results):
        m.update({f'{name}.{k}': v for k, v in r.metrics.items()})
        details += [f'{name}: {d}' for d in r.details]
        data[name] = r.data
    return Result(worst([r.status for r in results]), '; '.join(r.summary for r in results), m, details, data)


# ------------------------------------------------------------------------------------------------ orders

def orders(nav, points, on_area, reference=None, baseline=False):
    """points: [Point] of AI orders and patrol waypoints; on_area(pos) -> bool (ordercheck.PathData.on_area).
    reference: the reference file's orders result data (its 'near' bits): an order near a node there and not here
    fails; without one, every order must have a node near it."""
    if not points:
        return Result('skip', 'no AI orders in the level', {'orders': 0})
    js = joins(nav, points)
    near = [n is not None for n, _ in js]
    area = [bool(on_area(p.pos)) for p in points]
    big = max((i.nodes for i in nav.islands), default=0)
    large = [n is not None and nav.islands[int(nav.node_island[n])].nodes >= 0.25 * big for n, _ in js]
    m = {'orders': len(points), 'near_node_pct': pct(sum(near), len(points)), 'on_area_pct': pct(sum(area), len(points)),
         'in_large_island_pct': pct(sum(large), len(points))}
    details = [f'no route node within 15 m of {p.label()} ({near_text(d)})' for (n, d), p in zip(js, points) if n is None]
    details += [f'not on an area: {p.label()}' for p, a, nn in zip(points, area, near) if not a and nn]
    bits = ''.join('1' if x else '0' for x in near)
    rb = (reference or {}).get('near')
    if rb and len(rb) == len(bits):
        lost = [k for k in range(len(points)) if rb[k] == '1' and not near[k]]
        m['orders_lost'] = len(lost)
        details = [f'a route node near it in the reference, none here: {points[k].label()}' for k in lost] + details
        status = 'fail' if lost else 'warn' if not all(near) or not all(area) else 'pass'
    else:
        status = baseline_cap('fail' if not all(near) else 'warn' if not all(area) else 'pass', baseline)
    kinds = Counter(p.kind for p in points)
    return Result(status, f'{len(points)} orders ({", ".join(f"{v} {k}" for k, v in kinds.most_common())}): '
                          f'{m["near_node_pct"]:.0f} % with a route node within 15 m, {m["on_area_pct"]:.0f} % on an area',
                  m, details, {'near': bits})


# ------------------------------------------------------------------------------------------------ sector offsets

def sector_offsets(sectors):
    """((x, z), (share x, share z)) from a file's sectors (hpdtool.Sector: id 0xXXYY, bounds minX, maxX, minY, maxY,
    minZ, maxZ): per sector floor(centre / 100) - its grid id, the most common value (OPFOR's
    src/tools/onslaught/apply_sector_offsets.py; reproduces all 27 shipped PC assets). None without sectors."""
    cx, cz = Counter(), Counter()
    for s in sectors:
        b = s.bounds
        cx[math.floor((b[0] + b[1]) / 200) - (s.id >> 8)] += 1
        cz[math.floor((b[4] + b[5]) / 200) - (s.id & 0xff)] += 1
    if not cx:
        return None
    n = sum(cx.values())
    (x, nx), (z, nz) = cx.most_common(1)[0], cz.most_common(1)[0]
    return (x, z), (nx / n, nz / n)


def offsets(implied, asset, kind='Infantry'):
    """implied: sector_offsets() of the file; asset: (x, z) of the level's SectorOffsets asset, or None when the level
    has none, or 'unread' (present, values not readable)."""
    if implied is None:
        return Result('skip', 'no sectors', {})
    (x, z), (ax, az) = implied
    m = {'implied_x': x, 'implied_z': z, 'agreement_pct': pct(min(ax, az), 1)}
    if min(ax, az) < MIN_AGREEMENT:
        return Result('warn', f'sector ids disagree ({ax:.0%} / {az:.0%} of sectors give {x},{z})', m)
    if asset is None:
        return Result('fail', f'sector ids imply {x},{z}; the level loads no {kind} SectorOffsets asset (the runtime '
                              f'then numbers sectors from 0,0 and route requests from some sectors fail)', {**m, 'matches': 0})
    if asset == 'unread':
        return Result('warn', f'sector ids imply {x},{z}; the {kind} SectorOffsets asset is present but was not read', m)
    ok = tuple(asset) == (x, z)
    m.update(asset_x=asset[0], asset_z=asset[1], matches=int(ok))
    return Result('pass' if ok else 'fail', f'sector ids imply {x},{z}; the {kind} SectorOffsets asset says '
                                            f'{asset[0]},{asset[1]}' + ('' if ok else ' - they differ'), m)
