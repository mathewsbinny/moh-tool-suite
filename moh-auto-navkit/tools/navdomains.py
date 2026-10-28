r"""NavKit: routes across domains (on foot, land vehicle, boat, aircraft) - the offline domain graph and a planner
(prototype, 2026-10-26). Design: research\CROSS_DOMAIN_ROUTES.md.

  navdomains.py build <level> [--watermesh <pbl file>] [--infantry <hpd>] [--vehicle <hpd>] [--out <folder>] [--opfor <folder>]
  navdomains.py plan  <level> <x,y,z> <x,y,z> [--domains <file>] ...    best route between two points, legs listed

Domains are graphs of positions with two-way edges whose costs are seconds:
  infantry  the Kynapse infantry route graph (pathdata.hpd), at INFANTRY_SPEED
  land      the Kynapse vehicle route graph (vehicle\pathdata.hpd), at the vehicles' AI speed
  boat_pbl, boat_pwc  the boat meshes' route graphs (navwater_mesh.py side files, one per hull profile), at the
            boat's AI speed
  air       a graph given by the caller (no generator yet - design only)
Transitions join them: 'enter' at a vehicle spawn (infantry node near the spawn -> vehicle-domain node near it),
'exit' where a vehicle can let its crew off (land: a vehicle node with infantry ground beside it; boat: the mesh's
shore links; air: landing zones). A vehicle domain is entered only at a spawn, so every vehicle leg starts where a
vehicle is; the vehicle is left behind at the exit.

The planner is hierarchical: per domain, a table of costs between its transition points (one Dijkstra per
transition point, offline); at query time the start's and goal's infantry costs to every transition point, then a
Dijkstra over the small graph of transitions; each chosen leg is expanded by a query inside its domain (in game:
Kynapse for infantry / land, the side file for boats). plan_flat runs one Dijkstra over every domain joined, the
reference the tests compare it with.

Output: <level>.domains.json (format 'opfor-domains' 1) beside the other side files, under the --out folder
(default OPFOR's work\navwater\<level>\).
"""
import hashlib
import heapq
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))

FORMAT, VERSION = 'opfor-domains', 1
INFANTRY_SPEED = 4.0          # m/s, a soldier running (BC2 sprint is faster; bots mostly run)
SPEED_FACTOR = 0.6            # share of the AI's MaxSpeed a vehicle averages along a route (turns, slopes)
LAND_SPEED = 15.0             # m/s, AI MaxSpeed of the tanks / IFVs (mp_common AI/Vehicles MobilityData 15-20)
ENTER_S, EXIT_S = 3.0, 2.0    # s, getting in / out of a vehicle
WADE_SPEED = 1.5              # m/s, through shallow water between a boat and the beach
ENTER_REACH = 12.0            # m, an infantry route node this near a spawn reaches its vehicle
VEHICLE_SNAP = 30.0           # m, a vehicle-domain node this near a spawn is where the vehicle starts its route
DISMOUNT_REACH = 6.0          # m, a land-vehicle node with infantry ground this near lets the crew off
DISMOUNT_SPACING = 40.0       # m, one dismount point per this many metres (per grid tile)


# -------------------------------------------------------------------------------------------- domain graphs

@dataclass
class Domain:
    name: str
    pos: np.ndarray                         # (n, 3)
    adj: list                               # [[(m, seconds)]]
    speed: float
    source: dict = field(default_factory=dict)

    @property
    def n(self):
        return len(self.pos)


def graph_domain(name, pos, edges, speed, costs=None, source=None):
    """A domain from positions and two-way edges; cost = metres (given, else 3-D length) / speed."""
    pos = np.asarray(pos, float).reshape(-1, 3)
    adj = [[] for _ in range(len(pos))]
    seen = set()
    for k, (a, b) in enumerate(edges):
        a, b = int(a), int(b)
        if a == b or (a, b) in seen:
            continue
        m = float(costs[k]) if costs is not None else float(np.linalg.norm(pos[a] - pos[b]))
        s = m / speed
        seen.add((a, b))
        seen.add((b, a))
        adj[a].append((b, s))
        adj[b].append((a, s))
    return Domain(name, pos, adj, speed, source or {})


def kynapse_domain(name, path, speed):
    """A domain from a Kynapse pathdata.hpd's route graph (border nodes shared between sectors by id). Edges are
    taken two-way; their stored costs (metres) divided by speed."""
    import hpdtool
    f = hpdtool.parse(open(path, 'rb').read())
    index, pos, edges, costs = {}, [], [], []
    for s in f.sectors:
        local = []
        for nd in s.nodes:
            if nd.id not in index:
                index[nd.id] = len(pos)
                pos.append(nd.pos)
            local.append(index[nd.id])
        for e in s.edges:
            edges.append((local[e.src], local[e.dst]))
            costs.append((e.tag & 0x7FFFFFFF) / 16.0)
    return graph_domain(name, pos, edges, speed, costs, {'file': path, 'sha1': sha1(path)})


def watermesh_domain(path_or_dict, speed=None, name=None):
    """A boat domain from a navwater_mesh side file (path or loaded dict); named boat_<profile> by default."""
    d = json.load(open(path_or_dict)) if isinstance(path_or_dict, str) else path_or_dict
    sp = speed or d['profile']['speed'] * SPEED_FACTOR
    pos = [n[:3] for n in d['nodes']]
    edges = [(a, b) for a, b, _ in d['edges']]
    costs = [c for _, _, c in d['edges']]
    src = {'file': path_or_dict, 'sha1': sha1(path_or_dict)} if isinstance(path_or_dict, str) else {}
    return graph_domain(name or f"boat_{d['profile']['name']}", pos, edges, sp, costs, src), d


def boat_profile(vehicle):
    """The boat mesh profile a sea vehicle uses: jet skis (pwc) their own, every other boat the patrol boat's."""
    return 'pwc' if vehicle.lower().startswith('pwc') else 'pbl'


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def nearest(pos, p, limit=math.inf, dy_weight=1.0):
    """Index of the position nearest p (horizontal distance + dy_weight * height difference), or None."""
    if not len(pos):
        return None
    d = np.hypot(pos[:, 0] - p[0], pos[:, 2] - p[2]) + dy_weight * np.abs(pos[:, 1] - p[1])
    k = int(np.argmin(d))
    return k if d[k] <= limit else None


# ---------------------------------------------------------------------------------------------- transitions

@dataclass
class Transition:
    id: int
    kind: str                # enter | exit
    a: tuple                 # (domain, node) left
    b: tuple                 # (domain, node) reached
    cost: float              # seconds
    tag: str = ''            # spawn name, shore link id ...
    pos: tuple = None        # where it happens (x, y, z)


def dismount_points(land, infantry, reach=DISMOUNT_REACH, spacing=DISMOUNT_SPACING):
    """(land node, infantry node) pairs: a land-vehicle node with an infantry node within reach (height within
    2 m), one per spacing tile."""
    keep = {}
    inf = infantry.pos
    tiles = {}
    for k, p in enumerate(inf):
        tiles.setdefault((int(p[0] // reach), int(p[2] // reach)), []).append(k)
    for v, p in enumerate(land.pos):
        ti, tj = int(p[0] // reach), int(p[2] // reach)
        best, bd = None, math.inf
        for a in (ti - 1, ti, ti + 1):
            for b in (tj - 1, tj, tj + 1):
                for k in tiles.get((a, b), ()):
                    q = inf[k]
                    d = math.hypot(q[0] - p[0], q[2] - p[2])
                    if d <= reach and abs(q[1] - p[1]) <= 2.0 and d < bd:
                        best, bd = k, d
        if best is None:
            continue
        key = (int(p[0] // spacing), int(p[2] // spacing))
        if key not in keep or bd < keep[key][2]:
            keep[key] = (v, best, bd)
    return [(v, k, d) for v, k, d in keep.values()]


def vehicle_spawns(level_xml_text):
    """[(name, domain land|boat|air|static, vehicle, team, (x, y, z))] of the level's VehicleSpawnEntityData."""
    from mohcore import xmlinst
    out = []
    for _typ, _guid, body in xmlinst.instances(level_xml_text, '.VehicleSpawnEntityData'):
        ref = (xmlinst.field_ref(body, 'Vehicle') or '').lower()
        m = re.search(r'vehicles/(land|sea|air|static)/', ref)
        if not m:
            continue
        dom = {'sea': 'boat'}.get(m.group(1), m.group(1))
        t = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        name = re.search(r'<field name="Name">([^<]*)</field>', body)
        team = re.search(r'<field name="Team">([^<]*)</field>', body)
        if t:
            v = [float(a) for a in t.group(1).split('/')]
            out.append((name.group(1) if name else '', dom, ref.rsplit('/', 1)[0].split('/')[-1],
                        team.group(1) if team else '', tuple(v[12:15])))
    return out


class World:
    """Domains and transitions; transition ids index self.transitions."""

    def __init__(self, domains):
        self.domains = {d.name: d for d in domains}
        self.transitions = []
        self.skipped = []

    def add(self, kind, a, b, cost, tag='', pos=None):
        t = Transition(len(self.transitions), kind, a, b, float(cost), tag, tuple(pos) if pos is not None else None)
        self.transitions.append(t)
        return t

    def add_spawn(self, name, domain, pos, team=''):
        """An enter transition at a vehicle spawn of a domain, if both ends find a node."""
        inf, veh = self.domains.get('infantry'), self.domains.get(domain)
        if inf is None or veh is None:
            self.skipped.append((name, domain, 'no such domain'))
            return None
        k = nearest(inf.pos, pos, ENTER_REACH, 2.0)
        v = nearest(veh.pos, pos, VEHICLE_SNAP, 0.5)
        if k is None or v is None:
            self.skipped.append((name, domain, 'no infantry node near' if k is None else f'no {domain} node near'))
            return None
        walk = math.hypot(inf.pos[k][0] - pos[0], inf.pos[k][2] - pos[2]) / INFANTRY_SPEED
        return self.add('enter', ('infantry', k), (domain, v), ENTER_S + walk, f'{team}:{name}' if team else name, pos)

    # --------------------------------------------------------------------------------------- planning

    def leg_dijkstra(self, domain, src, targets=None):
        d = self.domains[domain]
        return dijkstra(d.adj, src, d.n, targets)

    def tables(self):
        """Per domain: {node of a transition point: {other such node: seconds}} - the offline cost tables."""
        points = {}
        for t in self.transitions:
            points.setdefault(t.a[0], set()).add(t.a[1])
            points.setdefault(t.b[0], set()).add(t.b[1])
        out = {}
        for dom, nodes in points.items():
            nodes = sorted(nodes)
            out[dom] = {}
            for n in nodes:
                dist = self.leg_dijkstra(dom, n)
                out[dom][n] = {m: dist[m] for m in nodes if math.isfinite(dist[m])}
        return out

    def plan(self, start, goal, tables=None, unavailable=()):
        """Hierarchical plan from (domain, node) start to (domain, node) goal (both normally infantry).
        Returns (seconds, legs [(domain, from node, to node, seconds) | ('transition', id, kind, seconds)])."""
        tables = tables if tables is not None else self.tables()
        sd, sn = start
        gd, gn = goal
        from_start = self.leg_dijkstra(sd, sn)
        to_goal = self.leg_dijkstra(gd, gn)                     # edges are two-way: reverse = forward
        # abstract graph: S, G, and the two ends of every transition
        S, G = ('S',), ('G',)
        adj = {S: [], G: []}

        def edge(a, b, c, what):
            if math.isfinite(c):
                adj.setdefault(a, []).append((b, c, what))
                adj.setdefault(b, adj.get(b, []))
        if sd == gd and math.isfinite(from_start[gn]):
            edge(S, G, from_start[gn], ('leg', sd, sn, gn))
        ends = {}
        for t in self.transitions:
            if t.tag in unavailable or t.id in unavailable:
                continue
            edge(('a', t.id), ('b', t.id), t.cost, ('transition', t.id))
            ends.setdefault(t.a, []).append(('a', t.id))
            ends.setdefault(t.b, []).append(('b', t.id))
        for (dom, node), keys in ends.items():
            if dom == sd:
                for k in keys:
                    if k[0] == 'a':
                        edge(S, k, from_start[node], ('leg', dom, sn, node))
            if dom == gd:
                for k in keys:
                    if k[0] == 'b':
                        edge(k, G, to_goal[node], ('leg', dom, node, gn))
        # inside a domain: from a reached end ('b') to a leaving end ('a') by the table
        for (dom, node), keys in ends.items():
            row = tables.get(dom, {}).get(node, {})
            for k in keys:
                if k[0] != 'b':
                    continue
                for (dom2, node2), keys2 in ends.items():
                    if dom2 != dom or node2 not in row:
                        continue
                    for k2 in keys2:
                        if k2[0] == 'a' and k2[1] != k[1]:
                            edge(k, k2, row[node2], ('leg', dom, node, node2))
        best = {S: 0.0}
        prev = {}
        h = [(0.0, 0, S)]
        tie = 1
        while h:
            c, _, u = heapq.heappop(h)
            if c > best.get(u, math.inf):
                continue
            if u == G:
                break
            for v, w, what in adj.get(u, ()):
                if c + w < best.get(v, math.inf):
                    best[v] = c + w
                    prev[v] = (u, what, w)
                    heapq.heappush(h, (c + w, tie, v))
                    tie += 1
        if G not in best:
            return math.inf, []
        legs, u = [], G
        while u != S:
            u0, what, w = prev[u]
            if what[0] == 'leg':
                legs.append((what[1], what[2], what[3], w))
            else:
                t = self.transitions[what[1]]
                legs.append(('transition', t.id, t.kind, w))
            u = u0
        return best[G], legs[::-1]

    def plan_flat(self, start, goal, unavailable=()):
        """One Dijkstra over all domains joined by the transitions (the reference for plan)."""
        offs, n = {}, 0
        for name, d in self.domains.items():
            offs[name] = n
            n += d.n
        adj = [[] for _ in range(n)]
        for name, d in self.domains.items():
            o = offs[name]
            for a, nb in enumerate(d.adj):
                adj[o + a] = [(o + b, c) for b, c in nb]
        for t in self.transitions:
            if t.tag in unavailable or t.id in unavailable:
                continue
            adj[offs[t.a[0]] + t.a[1]].append((offs[t.b[0]] + t.b[1], t.cost))
        dist = dijkstra(adj, offs[start[0]] + start[1], n)
        return dist[offs[goal[0]] + goal[1]]

    def expand(self, legs):
        """Each leg as positions: domain legs by a path query inside the domain."""
        out = []
        for leg in legs:
            if leg[0] == 'transition':
                t = self.transitions[leg[1]]
                out.append({'transition': t.kind, 'tag': t.tag, 'from': t.a[0], 'to': t.b[0], 'seconds': round(leg[3], 1)})
                continue
            dom, a, b, s = leg
            path = shortest_path(self.domains[dom].adj, a, b, self.domains[dom].n)
            P = self.domains[dom].pos
            length = sum(float(np.linalg.norm(P[path[k + 1]] - P[path[k]])) for k in range(len(path) - 1))
            out.append({'domain': dom, 'from': [round(float(v), 1) for v in P[a]], 'to': [round(float(v), 1) for v in P[b]],
                        'nodes': len(path), 'metres': round(length, 1), 'seconds': round(s, 1)})
        return out


def dijkstra(adj, src, n, targets=None):
    dist = [math.inf] * n
    dist[src] = 0.0
    left = set(targets) if targets else None
    h = [(0.0, src)]
    while h:
        d, a = heapq.heappop(h)
        if d > dist[a]:
            continue
        if left is not None:
            left.discard(a)
            if not left:
                break
        for b, c in adj[a]:
            if d + c < dist[b]:
                dist[b] = d + c
                heapq.heappush(h, (d + c, b))
    return dist


def shortest_path(adj, src, dst, n):
    dist = [math.inf] * n
    prev = [-1] * n
    dist[src] = 0.0
    h = [(0.0, src)]
    while h:
        d, a = heapq.heappop(h)
        if a == dst:
            break
        if d > dist[a]:
            continue
        for b, c in adj[a]:
            if d + c < dist[b]:
                dist[b] = d + c
                prev[b] = a
                heapq.heappush(h, (d + c, b))
    if not math.isfinite(dist[dst]):
        return []
    out = [dst]
    while out[-1] != src:
        out.append(prev[out[-1]])
    return out[::-1]


# ------------------------------------------------------------------------------------------- a level's world

def level_world(root, lv, watermesh=None, infantry=None, vehicle=None, log=print):
    import navparity
    inf_path = infantry or navparity.reference_of(root, lv)
    domains = [kynapse_domain('infantry', inf_path, INFANTRY_SPEED)]
    veh_path = vehicle or os.path.join(root, 'game', 'Output', 'win32', 'ai', 'levels', lv, 'vehicle', 'pathdata.hpd')
    if os.path.isfile(veh_path + '.opfor-orig'):
        veh_path += '.opfor-orig'
    land = kynapse_domain('land', veh_path, LAND_SPEED * SPEED_FACTOR) if os.path.isfile(veh_path) else None
    if land:
        domains.append(land)
    meshes = {}
    for prof in ('pbl', 'pwc'):
        wm_path = (watermesh if (watermesh and prof == 'pbl') else
                   os.path.join(root, 'work', 'navwater', lv, f'{lv}.{prof}.watermesh.json'))
        if os.path.isfile(wm_path):
            boat, wmd = watermesh_domain(wm_path)
            domains.append(boat)
            meshes[boat.name] = wmd
    W = World(domains)
    inf = W.domains['infantry']
    xml = open(navparity.level_xml(root, lv), encoding='utf-8', errors='replace').read()
    spawns = vehicle_spawns(xml)
    for name, dom, veh, team, pos in spawns:
        if dom == 'static':
            continue
        if dom == 'boat':
            dom = f'boat_{boat_profile(veh)}'
            wmd = meshes.get(dom)
            if wmd is None:
                W.skipped.append((name, dom, 'no boat mesh'))
                continue
            # a boat starts at the mesh's launch cell for its spawn (navwater_mesh attach)
            rec = next((s for s in wmd['spawns'] if s['name'] == name and np.allclose(s['pos'], pos, atol=0.05)), None)
            if rec is None or rec['node'] is None:
                W.skipped.append((name, dom, 'spawn not on the boat mesh'))
                continue
            k = nearest(inf.pos, pos, ENTER_REACH, 2.0)
            if k is None:
                W.skipped.append((name, dom, 'no infantry node near (crewed where it spawns)'))
                continue
            walk = math.hypot(inf.pos[k][0] - pos[0], inf.pos[k][2] - pos[2]) / INFANTRY_SPEED
            W.add('enter', ('infantry', k), (dom, rec['node']), ENTER_S + walk, f'{team}:{name}:{veh}', pos)
            continue
        W.add_spawn(f'{name}:{veh}', dom, pos, team)
    for dom, wmd in meshes.items():
        for l in wmd['shore_links']:
            if l['boat_node'] is None or l['land_node'] is None:
                continue
            # the land node indexes navdata's node order - the same first-seen-id order as kynapse_domain's
            wade = l['distance_m'] / WADE_SPEED
            W.add('exit', (dom, l['boat_node']), ('infantry', l['land_node']), EXIT_S + wade, f'shore {l["id"]} {l["kind"]}', l['land'])
    if land is not None:
        for v, k, d in dismount_points(land, inf):
            W.add('exit', ('land', v), ('infantry', k), EXIT_S + d / INFANTRY_SPEED, 'dismount', land.pos[v])
    log(f'{lv}: domains ' + ', '.join(f'{d.name} {d.n:,} nodes' for d in domains) +
        f'; transitions {len(W.transitions)} (' + ', '.join(f'{k} {v}' for k, v in sorted(count_kinds(W).items())) +
        f'); spawns skipped {len(W.skipped)}')
    return W


def count_kinds(W):
    out = {}
    for t in W.transitions:
        key = f'{t.kind} {t.b[0] if t.kind == "enter" else t.a[0]}'
        out[key] = out.get(key, 0) + 1
    return out


def to_json(W, lv, tables):
    doms = {}
    for name, d in W.domains.items():
        src = dict(d.source)
        if 'file' in src:
            src['file'] = os.path.basename(os.path.dirname(os.path.dirname(src['file']))) + '/' + \
                os.path.basename(os.path.dirname(src['file'])) + '/' + os.path.basename(src['file']) \
                if src['file'].lower().endswith(('.hpd', '.opfor-orig')) else os.path.basename(src['file'])
        doms[name] = {'nodes': d.n, 'speed_mps': round(d.speed, 2), **src}
    return {'format': FORMAT, 'version': VERSION, 'level': lv, 'domains': doms,
            'costs': {'infantry_speed': INFANTRY_SPEED, 'speed_factor': SPEED_FACTOR, 'enter_s': ENTER_S, 'exit_s': EXIT_S,
                      'wade_speed': WADE_SPEED},
            'transitions': [{'id': t.id, 'kind': t.kind, 'from': [t.a[0], t.a[1]], 'to': [t.b[0], t.b[1]],
                             'seconds': round(t.cost, 2), 'tag': t.tag,
                             'pos': [round(float(v), 2) for v in t.pos] if t.pos is not None else None} for t in W.transitions],
            'tables': {dom: {str(a): {str(b): round(c, 2) for b, c in row.items()} for a, row in rows.items()}
                       for dom, rows in tables.items()},
            'skipped_spawns': [list(s) for s in W.skipped]}


def main(argv):
    args = argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if len(args) < 2 or args[0] not in ('build', 'plan'):
        print(__doc__)
        return 1
    import navparity
    root = navparity.opfor_root(opt('--opfor'))
    lv = args[1].lower()
    t0 = time.time()
    W = level_world(root, lv, opt('--watermesh'), opt('--infantry'), opt('--vehicle'))
    tables = W.tables()
    out_dir = opt('--out', os.path.join(root, 'work', 'navwater', lv))
    os.makedirs(out_dir, exist_ok=True)
    if args[0] == 'build':
        p = os.path.join(out_dir, f'{lv}.domains.json')
        json.dump(to_json(W, lv, tables), open(p, 'w'), separators=(',', ':'))
        print(f'written {p} ({os.path.getsize(p):,} bytes, {time.time() - t0:.1f} s)')
        return 0
    a = [float(v) for v in args[2].split(',')]
    b = [float(v) for v in args[3].split(',')]
    inf = W.domains['infantry']
    s, g = nearest(inf.pos, a), nearest(inf.pos, b)
    cost, legs = W.plan(('infantry', s), ('infantry', g), tables)
    walk = W.plan_flat(('infantry', s), ('infantry', g), unavailable={t.id for t in W.transitions})
    print(f'best {cost:.1f} s (on foot only {walk:.1f} s)')
    for leg in W.expand(legs):
        print('  ', leg)
    return 0


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('navdomains', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
