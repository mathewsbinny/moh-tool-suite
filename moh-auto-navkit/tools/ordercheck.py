"""NavKit: are a level's AI order positions on the walkable areas / route network of a pathdata.hpd?

Collects AreaOrder / PatrolOrder / InterludeOrder / FireAtArea positions and the patrol waypoints
(AreaGeometryEntityData chains referenced by PatrolOrders) from the converted level partition - and, on a level
with none (Medal of Honor MP has no AI orders), its objectives and spawn points (MOH_POINT_TYPES) - then for
each pathdata.hpd given reports per point: inside an area (horizontal, with height tolerance) and the
route-network group of the nearest node.

Usage: ordercheck.py <level.xml> <a.hpd> [<b.hpd> ...]

With two files (reference first), also the pairwise route check: of all point pairs the reference's route
network connects, the share the second file also connects. With one file, the self-check: the share of
point pairs on its areas that its route network connects (for maps without reference data).
"""
import itertools
import math
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
import hpdtool  # noqa: E402
from mohcore import xmlinst  # noqa: E402
import kymesh  # noqa: E402
import mohsoldier  # noqa: E402

Y_OFFSET = mohsoldier.Y_OFFSET
# the places bots go on a MOH MP level: Combat Mission's targets, the capture points of Sector Control, Hot Zone and
# Objective Raid (Combat Mission's are team-base markers some 18 m up - reported as off the areas), the spawn points
MOH_POINT_TYPES = ('ObjectiveTriggerEntityData', 'CapturePointEntityData', 'AlternateSpawnEntityData',
                   'SoldierSpawnEntityData')


def order_points(xml_path):
    x = open(xml_path, encoding='utf-8', errors='replace').read()
    inst = {}
    for typ, guid, body in xmlinst.instances(x):
        inst[guid.lower()] = (typ, body)

    def pos(body):
        t = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        if not t:
            return None
        v = [float(a) for a in t.group(1).split('/')]
        return (v[12], v[13], v[14]) if any(abs(c) > 1e-3 for c in v[12:15]) else None

    pts = []
    for guid, (typ, body) in inst.items():
        short = typ.split('.')[-1]
        if short in ('AreaOrderEntityData', 'PatrolOrderEntityData', 'InterludeOrderEntityData',
                     'FireAtAreaOrderEntityData'):
            name = (re.search(r'<field name="Name">([^<]*)</field>', body) or [None, ''])[1] if re.search(r'<field name="Name">([^<]*)</field>', body) else ''
            p = pos(body)
            if p:
                pts.append((short.replace('EntityData', ''), name, p))
            if short == 'PatrolOrderEntityData':
                for ref in re.findall(r'ref="([0-9a-f-]{36})"', body):
                    seen = set()
                    g = ref.lower()
                    while g in inst and g not in seen and inst[g][0].endswith('AreaGeometryEntityData'):
                        seen.add(g)
                        q = pos(inst[g][1])
                        if q:
                            pts.append(('PatrolWaypoint', name, q))
                        nxt = xmlinst.field_ref(inst[g][1], 'Next')
                        g = nxt.lower() if nxt and re.fullmatch(r'[0-9a-f-]{36}', nxt) else ''
    if not pts:
        for guid, (typ, body) in inst.items():
            short = typ.split('.')[-1]
            if short in MOH_POINT_TYPES:
                p = pos(body)
                if p:
                    name = re.search(r'<field name="Name">([^<]*)</field>', body)
                    pts.append((short.replace('EntityData', ''), name.group(1) if name else '', p))
    # unique
    out, seen = [], set()
    for k, n, p in pts:
        key = (k, round(p[0], 1), round(p[2], 1))
        if key not in seen:
            seen.add(key)
            out.append((k, n, p))
    return out


def drop_cracks(segs):
    """Coincident segment pairs (in plan) are a zero-width crack inside an area - two adjacent cells of it that
    are not connected to each other (a step) - not its boundary; both are removed for the inside test."""
    from collections import Counter
    key = lambda sg: tuple(sorted(((round(sg[0][0], 2), round(sg[0][2], 2)), (round(sg[1][0], 2), round(sg[1][2], 2)))))
    n = Counter(key(sg) for sg in segs)
    return [sg for sg in segs if n[key(sg)] % 2 == 1]


class PathData:
    def __init__(self, path, clip=None, drop=None):
        """clip: (minX, minZ, maxX, maxZ) - route nodes outside it, and the edges touching them, are left out (a
        reference's ground beyond the level's terrain, which the game on PC does not have; navparity). drop: node ids
        left out the same way (a reference's nodes on ground PC does not have; navparity.stale_nodes)."""
        f = hpdtool.parse(open(path, 'rb').read())
        drop = drop or set()
        in_box = (lambda p: clip[0] <= p[0] <= clip[2] and clip[1] <= p[2] <= clip[3]) if clip else (lambda p: True)
        self.areas = []      # (minx, maxx, minz, maxz, miny, maxy, segs)
        for s in f.sectors:
            m = kymesh.parse_mesh(s.mesh)
            for a in m.areas:
                segs = [(l[k], l[k + 1]) for l in a.outline for k in range(len(l) - 1)]
                segs += [(b.p0, b.p1) for b in a.portals] + [(c.p0, c.p1) for c in a.border_portals]
                segs = drop_cracks(segs)
                if not segs:
                    continue
                xs = [p[0] for sg in segs for p in sg]
                zs = [p[2] for sg in segs for p in sg]
                ys = [p[1] for sg in segs for p in sg]
                self.areas.append((min(xs), max(xs), min(zs), max(zs), min(ys) - Y_OFFSET, max(ys) - Y_OFFSET, segs))
        # graph groups (shared border node ids join sectors)
        parent = {}

        def find(a):
            while parent.setdefault(a, a) != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        self.nodes = []
        def inside(n):
            return n.id not in drop and in_box(n.pos)
        for s in f.sectors:
            for e in s.edges:
                if inside(s.nodes[e.src]) and inside(s.nodes[e.dst]):
                    parent[find(s.nodes[e.src].id)] = find(s.nodes[e.dst].id)
            for n in s.nodes:
                if inside(n):
                    self.nodes.append((n.id, n.pos))
        self.group = {nid: find(nid) for nid, _ in self.nodes}
        sizes = Counter(self.group.values())
        self.group_size = sizes
        self.pos = np.array([p for _, p in self.nodes])
        self.ids = [nid for nid, _ in self.nodes]

    def on_area(self, p, ytol=2.5):
        x, y, z = p
        for x0, x1, z0, z1, y0, y1, segs in self.areas:
            if not (x0 <= x <= x1 and z0 <= z <= z1 and y0 - ytol <= y <= y1 + ytol):
                continue
            inside = False
            for (ax, _, az), (bx, _, bz) in segs:
                if az == bz:
                    continue
                if min(az, bz) <= z < max(az, bz) and x < ax + (z - az) * (bx - ax) / (bz - az):
                    inside = not inside
            if inside:
                return True
        return False

    def nearest_group(self, p, radius=15.0):
        d = np.hypot(self.pos[:, 0] - p[0], self.pos[:, 2] - p[2])
        d = np.where(np.abs(self.pos[:, 1] - Y_OFFSET - p[1]) <= 4.0, d, 1e9)
        k = int(np.argmin(d))
        if d[k] > radius:
            return None, None
        g = self.group[self.ids[k]]
        return g, self.group_size[g]


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    pts = order_points(argv[1])
    print(f'{len(pts)} order / waypoint positions: {dict(Counter(k for k, _, _ in pts))}')
    results = {}
    for path in argv[2:]:
        pdata = PathData(path)
        biggest = max(pdata.group_size.values())
        res = []
        for k, n, p in pts:
            on = pdata.on_area(p)
            g, size = pdata.nearest_group(p)
            res.append((on, size))
        results[path] = res
        on = sum(1 for o, _ in res if o)
        near = sum(1 for _, s in res if s)
        big = sum(1 for _, s in res if s and s >= 0.25 * biggest)
        print(f'{os.path.basename(os.path.dirname(os.path.dirname(path))) or path}: {os.path.basename(path)}  on an area: {on}/{len(pts)}  '
              f'node within 15 m: {near}  in a large route group (>= 25% of largest): {big}')
    groups = {}
    for path in argv[2:]:
        pdata = PathData(path)
        groups[path] = [pdata.nearest_group(p)[0] if pdata.on_area(p) else None for _, _, p in pts]
    if len(argv) == 3:
        g = groups[argv[2]]
        idx = [i for i in range(len(pts)) if g[i] is not None]
        total = len(idx) * (len(idx) - 1) // 2
        conn = sum(1 for a, b in itertools.combinations(idx, 2) if g[a] == g[b])
        print(f'self-check: {conn}/{total} point pairs on areas are route-connected ({conn / max(total, 1) * 100:.1f}%)')
        if idx:
            main_group = Counter(g[i] for i in idx).most_common(1)[0][0]
            apart = [i for i in idx if g[i] != main_group]
            if apart:
                print(f'\npoints on areas outside the route group holding most of them ({len(apart)}):')
                for i in apart[:60]:
                    k, n, p = pts[i]
                    print(f'  {k:22s} {n[:34]:34s} ({p[0]:8.1f} {p[1]:6.1f} {p[2]:8.1f})')
    if len(argv) == 4:
        gd, go = groups[argv[2]], groups[argv[3]]
        idx = [i for i in range(len(pts)) if gd[i] is not None]
        ref = both = extra = 0
        for a, b in itertools.combinations(idx, 2):
            dc = gd[a] == gd[b]
            oc = go[a] is not None and go[a] == go[b]
            ref += dc
            both += dc and oc
            extra += oc and not dc
        print(f'pairwise route check: {both}/{ref} reference-connected pairs also connected ({both / max(ref, 1) * 100:.1f}%); '
              f'{extra} pairs connected only in the second file')
    if len(results) == 2:
        (pa, ra), (pb, rb) = list(results.items())
        diff = [(pts[i], ra[i], rb[i]) for i in range(len(pts)) if ra[i][0] != rb[i][0] or bool(ra[i][1]) != bool(rb[i][1])]
        print(f'\npoints that differ ({len(diff)}): first = {os.path.basename(pa)}, second = {os.path.basename(pb)}')
        for (k, n, p), a, b in diff[:60]:
            print(f'  {k:22s} {n[:34]:34s} ({p[0]:8.1f} {p[1]:6.1f} {p[2]:8.1f})  on-area {a[0]!s:5} / {b[0]!s:5}   group size {a[1]} / {b[1]}')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('ordercheck', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
