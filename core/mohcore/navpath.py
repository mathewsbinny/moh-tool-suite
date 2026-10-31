"""The path test: a route between two points over a path-data file's route graph, or why there is none.

    r = route(nav, start, end)          # nav: navdata.NavData; start, end: world (x, y, z) on the ground
    r.found, r.nodes, r.cost, r.reason, r.start_island, r.end_island, r.gap

The points are joined to the graph at the nearest route node within 15 m horizontally and 4 m in height (as
the studio's spawn check does); the route is the cheapest over the file's own directed edges and their
stored costs (A*, straight-line distance as the estimate). Without a route, the result says which island
each point is on and where the two islands come closest. This plans over the graph only: the game also
smooths the route through the areas, which this does not do (DESIGN.md section 7, question 4).
"""
import heapq
from dataclasses import dataclass, field

import numpy as np

from . import navdata

JOIN_XZ, JOIN_Y = 15.0, 4.0


@dataclass
class Route:
    found: bool
    nodes: list[int] = field(default_factory=list)        # node indices, start to end
    cost: float = 0.0                                     # sum of the stored edge costs (m)
    length: float = 0.0                                   # 3-D length of the node chain (m)
    straight: float = 0.0                                 # start to end, straight (m)
    start_node: int | None = None
    end_node: int | None = None
    start_join: float = 0.0                               # metres from the point to its node
    end_join: float = 0.0
    start_island: int | None = None
    end_island: int | None = None
    gap: tuple[int, int, float] | None = None             # (node in start island, node in end island, metres)
    reason: str = ''
    expanded: int = 0


def ground_nodes(nav: navdata.NavData) -> np.ndarray:
    return nav.node_pos - np.array([0, navdata.Y_OFFSET, 0])


def join(nav: navdata.NavData, p: np.ndarray) -> tuple[int | None, float]:
    """(node index, horizontal distance) nearest to p within the join limits, or (None, distance)."""
    if not len(nav.node_pos):
        return None, np.inf
    g = ground_nodes(nav)
    d = np.hypot(g[:, 0] - p[0], g[:, 2] - p[2])
    d = np.where(np.abs(g[:, 1] - p[1]) <= JOIN_Y, d, np.inf)
    k = int(np.argmin(d))
    return (k, float(d[k])) if d[k] <= JOIN_XZ else (None, float(d[k]))


def _adjacency(nav: navdata.NavData) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cache = getattr(nav, '_adj', None)
    if cache is None:
        order = np.argsort(nav.edges[:, 0], kind='stable') if len(nav.edges) else np.zeros(0, int)
        src = nav.edges[order, 0] if len(order) else np.zeros(0, int)
        start = np.searchsorted(src, np.arange(len(nav.node_pos) + 1))
        cache = (start, nav.edges[order, 1] if len(order) else np.zeros(0, int), nav.edge_cost[order] if len(order) else np.zeros(0))
        nav._adj = cache
    return cache


def closest_between(nav: navdata.NavData, ia: int, ib: int) -> tuple[int, int, float] | None:
    """The closest pair of nodes of two islands (horizontal metres)."""
    a = np.nonzero(nav.node_island == ia)[0]
    b = np.nonzero(nav.node_island == ib)[0]
    if not len(a) or not len(b):
        return None
    pa, pb = nav.node_pos[a][:, [0, 2]], nav.node_pos[b][:, [0, 2]]
    best: tuple = (None, None, np.inf)
    for k in range(0, len(pa), 512):
        d = np.hypot(pa[k:k + 512, None, 0] - pb[None, :, 0], pa[k:k + 512, None, 1] - pb[None, :, 1])
        i, j = np.unravel_index(int(np.argmin(d)), d.shape)
        if d[i, j] < best[2]:
            best = (int(a[k + i]), int(b[j]), float(d[i, j]))
    return best


def route(nav: navdata.NavData, start: np.ndarray | list[float], end: np.ndarray | list[float]) -> Route:
    start, end = np.asarray(start, float), np.asarray(end, float)
    r = Route(False, straight=float(np.linalg.norm(end - start)))
    r.start_node, r.start_join = join(nav, start)
    r.end_node, r.end_join = join(nav, end)
    if r.start_node is None or r.end_node is None:
        which = 'start' if r.start_node is None else 'end'
        r.reason = f'no route node within {JOIN_XZ:.0f} m of the {which} point (nearest {min(r.start_join if which == "start" else r.end_join, 9999):.0f} m)'
        return r
    r.start_island, r.end_island = int(nav.node_island[r.start_node]), int(nav.node_island[r.end_node])
    start_idx, nbr, cost = _adjacency(nav)
    pos = nav.node_pos
    goal = r.end_node
    best = {r.start_node: 0.0}
    came: dict[int, int] = {}
    heap = [(float(np.linalg.norm(pos[r.start_node] - pos[goal])), 0.0, r.start_node)]
    done: set[int] = set()
    while heap:
        _, g, n = heapq.heappop(heap)
        if n in done:
            continue
        done.add(n)
        if n == goal:
            break
        for k in range(start_idx[n], start_idx[n + 1]):
            m = int(nbr[k])
            ng = g + float(cost[k])
            if ng < best.get(m, np.inf):
                best[m] = ng
                came[m] = n
                heapq.heappush(heap, (ng + float(np.linalg.norm(pos[m] - pos[goal])), ng, m))
    r.expanded = len(done)
    if goal not in done:
        if r.start_island != r.end_island:
            r.gap = closest_between(nav, r.start_island, r.end_island)
            r.reason = (f'the points are on different islands ({r.start_island} and {r.end_island}); they come closest '
                        f'{r.gap[2]:.1f} m apart' if r.gap else f'the points are on different islands ({r.start_island}, {r.end_island})')
        else:
            r.reason = 'same island, but no route along the edges\' directions (one-way edges)'
        return r
    chain = [goal]
    while chain[-1] != r.start_node:
        chain.append(came[chain[-1]])
    r.nodes = chain[::-1]
    r.cost = best[goal]
    r.length = float(np.sum(np.linalg.norm(np.diff(pos[r.nodes], axis=0), axis=1))) if len(r.nodes) > 1 else 0.0
    r.found = True
    return r
