"""NavKit: the traversal annotations of a navigation side file (ladders, jumps, vaults, drops) as
`<level>.traversal.json`, for Project OPFOR's bot layer (DESIGN.md 5.1-5.2).

They are not written into the path data: a route edge there would make the game's AI plan through a ladder
or a jump its movement cannot perform, and get stuck. Whether Kynapse's format can carry such links is
open question 3 in DESIGN.md. The file gives each annotation its two ends, the route node at each end (the
nearest within 15 m horizontally and 4 m in height, in the written path data) and that node's island, so the
bot layer knows which parts of the route graph it joins.

{
  "format": "moh-traversal", "version": 1, "level": "Levels/SP_01", "pathdata": "<file>", "pathdata_sha1": "...",
  "links": [{"kind": "ladder", "two-way": true, "bottom": [x, y, z], "top": [x, y, z], "rise": 6.0, "length": 6.1,
             "bottom_node": {"sector": 515, "id": 1234, "pos": [x, y, z], "island": 0} | null,
             "top_node": {...} | null, "joins_islands": true, "by": "MATHEWS", "when": "..."}]
}
Islands are numbered by size (0: the largest, the main route network), as the Navigation Studio shows them.
"""
import hashlib
import json
import math
import os
from collections import Counter

import numpy as np

FORMAT, VERSION = 'moh-traversal', 1
JOIN_XZ, JOIN_Y, Y_OFFSET = 15.0, 4.0, 0.95


def graph(f):
    """Unique nodes (by id) with their sector, position and island (0 = the largest)."""
    index, ids, secs, pos, edges = {}, [], [], [], []
    for s in f.sectors:
        local = []
        for n in s.nodes:
            if n.id not in index:
                index[n.id] = len(ids)
                ids.append(n.id)
                secs.append(s.id)
                pos.append(n.pos)
            local.append(index[n.id])
        edges += [(local[e.src], local[e.dst]) for e in s.edges]
    parent = list(range(len(ids)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = [find(k) for k in range(len(ids))]
    rank = {r: k for k, (r, _) in enumerate(Counter(roots).most_common())}
    return ids, secs, np.array(pos, dtype=np.float64).reshape(-1, 3), [rank[r] for r in roots]


def nearest(g, p):
    ids, secs, pos, island = g
    if not len(pos):
        return None
    d = np.hypot(pos[:, 0] - p[0], pos[:, 2] - p[2])
    d = np.where(np.abs(pos[:, 1] - Y_OFFSET - p[1]) <= JOIN_Y, d, np.inf)
    k = int(np.argmin(d))
    if d[k] > JOIN_XZ:
        return None
    return {'sector': int(secs[k]), 'id': int(ids[k]), 'pos': [round(float(v), 3) for v in pos[k]], 'island': int(island[k]),
            'distance': round(float(d[k]), 2)}


def write(f, pathdata_path, operations, level, out_path, log=print):
    """Writes the traversal annotations among operations to out_path (an empty list when there are none).
    Returns the links written."""
    g = graph(f)
    links = []
    for o in operations:
        if o.get('op') != 'traversal':
            continue
        b, t = o['bottom'], o['top']
        nb, nt_ = nearest(g, b), nearest(g, t)
        link = {'kind': o['kind'], 'two-way': o.get('two-way', True), 'bottom': b, 'top': t,
                'rise': round(t[1] - b[1], 2), 'length': round(math.dist(b, t), 2),
                'bottom_node': nb, 'top_node': nt_,
                'joins_islands': bool(nb and nt_ and nb['island'] != nt_['island']),
                'by': o.get('by'), 'when': o.get('when')}
        links.append(link)
        ends = ' and '.join(f'{name} {"island " + str(n["island"]) if n else "no route node within 15 m"}'
                            for name, n in (('bottom', nb), ('top', nt_)))
        log(f'  traversal {o["kind"]}{" (two-way)" if link["two-way"] else ""}: {link["length"]:.1f} m, {ends}'
            + (' - joins two islands' if link['joins_islands'] else ''))
    sha = hashlib.sha1(open(pathdata_path, 'rb').read()).hexdigest()
    doc = {'format': FORMAT, 'version': VERSION, 'level': level, 'pathdata': os.path.basename(pathdata_path),
           'pathdata_sha1': sha, 'links': links}
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, indent=1)
        fh.write('\n')
    os.replace(tmp, out_path)
    return links
