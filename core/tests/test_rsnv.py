"""RSNV, the navigation runtime's per-level file (core rsnv.py), on a file made here (no game data): written in both
byte orders it reads back identical; the Python reference search finds the cheapest path; locate finds the walkable
cell; line of sight keeps to one floor where two areas are stacked (it starts on the floor nearest the start's
height); damaged files (truncated, a section outside the file, a bad marker, a wrong stride) are rejected with a
ValueError, not a crash.

  python -B test_rsnv.py
"""
import os
import struct
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from mohcore import rsnv  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def tiny():
    """Four nodes in a square (one sector, one 4 x 4 area at 0.8 m): 0-1-2 along the bottom and right, 0-3-2 by a
    detour, plus a jump edge."""
    nodes = [(0.0, 10.0, 0.0), (2.4, 10.0, 0.0), (2.4, 10.0, 2.4), (0.0, 10.0, 6.0)]
    edges = {0: [(1, 38), (3, 96)], 1: [(0, 38), (2, 38)], 2: [(1, 38), (3, 70, rsnv.EDGE_JUMP)], 3: [(0, 96), (2, 70, rsnv.EDGE_JUMP)]}
    node_rows, edge_rows = [], []
    for n, p in enumerate(nodes):
        node_rows.append((*p, len(edge_rows)))
        for e in edges[n]:
            kind = e[2] if len(e) > 2 else rsnv.EDGE_WALK
            edge_rows.append((e[0] | kind << 24, e[1], 0))
    node_rows.append((0.0, 0.0, 0.0, len(edge_rows)))
    bmap = bytes([0b1111, 0b1111, 0b1111, 0b0111])      # 4 x 4 cells, the last row's x = 3 cell missing
    sections = {
        'SECT': [(0x0B0B, 0, 1, 0.0, 0.0, 100.0, 100.0, 0, 0, 1, 0)],
        'NODE': node_rows,
        'NAUX': [(0, 0, 0, 0)] * 4,
        'EDGE': edge_rows,
        'AREA': [(0.0, 0.0, 2.4, 2.4, 10.0, 10.0, 0, 4, 4, 0, 0, 0, 0, 0, 0, 4, 800, 0, 1, 1, 0)],
        'BMAP': bmap,
        'HGHT': [(0,)],
        'GBKT': [(0, 1)] + [(1, 0)] * (rsnv.BUCKETS * rsnv.BUCKETS - 1),
        'GLST': [(0,)],
        'ANOD': [(0,), (1,), (2,), (3,)],
        'ISLD': [(4, 0, rsnv.ISLD_MAIN, 0, 0)],
        'META': b'{"exporter": "test"}',
    }
    header = {'level': 'tiny', 'sea_level': 5.0, 'radius': 0.35, 'height': 1.8, 'step': 0.37, 'water_depth': 1.4,
              'hpd_sha256': '00' * 32}
    return sections, header


# 2026-10-26: a chain of short edges, each cost rounded down, beside one long edge; with the 0.999 heuristic the
# search came back with the long edge (83) although the chain costs 82 (found by a random search over such graphs)
ADMISSIBLE_CASE = [(0.0, 0.0, 0.0), (5.2374, 0.0, 0.0), (0.418, 0.0, 0.2017), (0.8804, 0.0, 0.2182),
                   (1.2934, 0.0, 0.1968), (1.7043, 0.0, 0.0716), (2.1912, 0.0, -0.0472), (2.6205, 0.0, 0.21),
                   (3.0516, 0.0, 0.0805), (3.4706, 0.0, 0.041), (3.8816, 0.0, -0.052), (4.3344, 0.0, -0.0136),
                   (4.8342, 0.0, 0.0736)]


def chain_file(path, h_scale_q):
    """ADMISSIBLE_CASE as an RSNV file: node 0 to 1 by a direct edge and by the chain 0-2-3-...-12-1, costs
    floor(16 x length) from the float32 positions."""
    import math
    import numpy as np
    pos = [tuple(float(np.float32(v)) for v in p) for p in ADMISSIBLE_CASE]
    links = [(0, 1)] + list(zip([0] + list(range(2, 13)), list(range(2, 13)) + [1]))
    adj = {n: [] for n in range(len(pos))}
    for a_, b_ in links:
        c = math.floor(16 * math.dist(pos[a_], pos[b_]))
        adj[a_].append((b_, c))
        adj[b_].append((a_, c))
    sections, header = tiny()
    node_rows, edge_rows = [], []
    for n, p in enumerate(pos):
        node_rows.append((*p, len(edge_rows)))
        edge_rows += [(t, c, 0) for t, c in sorted(adj[n])]
    node_rows.append((0.0, 0.0, 0.0, len(edge_rows)))
    sections.update(NODE=node_rows, EDGE=edge_rows, NAUX=[(0, 0, 0, 0)] * len(pos),
                    ANOD=[(n,) for n in range(len(pos))], ISLD=[(len(pos), 0, rsnv.ISLD_MAIN, 0, 0)])
    sections['AREA'] = [tuple(list(sections['AREA'][0][:15]) + [len(pos)] + list(sections['AREA'][0][16:]))]
    rsnv.write(path, sections, dict(header, h_scale_q=h_scale_q))
    src = [n for n in range(len(pos)) for _ in adj[n]]
    dst = [t for n in range(len(pos)) for t, _c in sorted(adj[n])]
    cost = [c for n in range(len(pos)) for _t, c in sorted(adj[n])]
    return pos, src, dst, cost


def stacked():
    """Two 0.2 m areas over the same 8 x 8 cells, 0.8 m apart in height (a stair landing over the floor below), the
    lower one first in index order: area 0 at 10.0 m, area 1 at 10.8 m; a third area beside them at 10.0 m east of
    x = 1.6 joins area 0's floor. Nodes 0 and 1 on the upper floor, 2 on the lower, 3 on the side area."""
    nodes = [(0.2, 10.8, 0.2), (1.2, 10.8, 1.2), (1.2, 10.0, 1.2), (2.4, 10.0, 0.6)]
    links = [(0, 1), (2, 3)]
    adj = {n: [] for n in range(len(nodes))}
    for a_, b_ in links:
        c = int(16 * ((nodes[a_][0] - nodes[b_][0]) ** 2 + (nodes[a_][2] - nodes[b_][2]) ** 2) ** 0.5) + 1
        adj[a_].append((b_, c))
        adj[b_].append((a_, c))
    node_rows, edge_rows = [], []
    for n, p_ in enumerate(nodes):
        node_rows.append((*p_, len(edge_rows)))
        edge_rows += [(t, c, 0) for t, c in adj[n]]
    node_rows.append((0.0, 0.0, 0.0, len(edge_rows)))
    full = bytes([0xFF] * 8)
    sections = {
        'SECT': [(0x0B0B, 0, 1, 0.0, 0.0, 100.0, 100.0, 0, 0, 3, 0)],
        'NODE': node_rows,
        'NAUX': [(1, 0, 0, 0), (1, 0, 0, 0), (0, 0, 1, 0), (2, 0, 1, 0)],
        'EDGE': edge_rows,
        # x0, z0, x1, z1, minY, maxY, bmap, w, h, first portal, portals, sector, island, flags, first node, nodes,
        # pitch mm, hght, hw, hh, hstep mm
        'AREA': [(0.0, 0.0, 1.4, 1.4, 10.0, 11.52, 0, 8, 8, 0, 0, 0, 1, 0, 0, 1, 200, 0, 1, 1, 0),
                 (0.0, 0.0, 1.4, 1.4, 10.8, 12.32, 8, 8, 8, 0, 0, 0, 0, 0, 1, 2, 200, 1, 1, 1, 0),
                 (1.6, 0.0, 3.0, 1.4, 10.0, 11.52, 16, 8, 8, 0, 0, 0, 1, 0, 3, 1, 200, 2, 1, 1, 0)],
        'BMAP': full * 3,
        'HGHT': [(0,), (0,), (0,)],
        'GBKT': [(0, 3)] + [(3, 0)] * (rsnv.BUCKETS * rsnv.BUCKETS - 1),
        'GLST': [(0,), (2,), (1,)],
        'ANOD': [(2,), (0,), (1,), (3,)],
        'ISLD': [(2, 0, rsnv.ISLD_MAIN, 0, 0), (2, 2, 0, 0, 0)],
        'META': b'{"exporter": "test"}',
    }
    header = {'level': 'stacked', 'sea_level': 0.0, 'radius': 0.35, 'height': 1.8, 'step': 0.37, 'water_depth': 1.4,
              'hpd_sha256': '00' * 32}
    return sections, header


def stacked_los_cases():
    """(a, b, expected, why) over stacked(): line of sight on each floor, never between them (the start rule's
    negative cases)."""
    return [((0.2, 10.8, 0.2), (1.2, 10.8, 1.2), True, 'upper floor: from its node to its node'),
            ((1.2, 10.8, 1.2), (0.2, 10.8, 0.2), True, 'upper floor, the other way'),
            ((0.2, 10.0, 0.2), (1.2, 10.0, 1.2), True, 'lower floor'),
            ((0.6, 10.0, 0.6), (2.4, 10.0, 0.6), True, 'lower floor to the side area at its height'),
            ((0.2, 10.8, 0.2), (1.2, 10.0, 1.2), False, 'upper floor to the lower one below it: 0.8 m off at the end'),
            ((0.2, 10.0, 0.2), (1.2, 10.8, 1.2), False, 'lower floor to the upper one'),
            ((0.6, 10.8, 0.6), (2.4, 10.0, 0.6), False, 'upper floor across its edge to the side area 0.8 m below'),
            ((0.2, 10.42, 0.2), (1.2, 10.8, 1.2), True, 'a start nearer the upper floor (0.38 m) starts on it'),
            ((0.2, 10.38, 0.2), (1.2, 10.8, 1.2), False, 'a start nearer the lower floor (0.38 m) starts there')]


def dijkstra(w, s, g):
    import heapq
    d, q = {s: 0}, [(0, s)]
    while q:
        c, n = heapq.heappop(q)
        if n == g:
            return c
        if c > d[n]:
            continue
        for k in range(int(w.node_first_edge[n]), int(w.node_first_edge[n + 1])):
            m, nc = int(w.edge_target[k]), c + int(w.edge_cost[k])
            if nc < d.get(m, 1 << 60):
                d[m] = nc
                heapq.heappush(q, (nc, m))


def corrupt_cases():
    """{what: change to the sections} - each table's index fields set out of range, one at a time."""
    def area(s, at, vals):
        r = list(s['AREA'][0])
        r[at:at + len(vals)] = vals
        s['AREA'] = [tuple(r)]

    def sect(s, at, v):
        r = list(s['SECT'][0])
        r[at] = v
        s['SECT'] = [tuple(r)]
    cases = {
        'ANOD node': lambda s: s.update(ANOD=[(99,)] + s['ANOD'][1:]),
        'AREA first node + nodes': lambda s: area(s, 14, [3, 4]),
        'AREA bitmap': lambda s: area(s, 6, [99]),
        'AREA height grid': lambda s: area(s, 17, [9]),
        'AREA portals': lambda s: area(s, 9, [0, 2]),
        'AREA sector': lambda s: area(s, 11, [5]),
        'NAUX area': lambda s: s.update(NAUX=[(7, 0, 0, 0)] + s['NAUX'][1:]),
        'NAUX sector': lambda s: s.update(NAUX=[(0, 3, 0, 0)] + s['NAUX'][1:]),
        'EDGE target': lambda s: s.update(EDGE=[(99, 38, 0)] + s['EDGE'][1:]),
        # 2026-10-31: an edge kind past the known ones (7..255), refused by validate and by MOH's rsn_load alike
        'EDGE kind 7': lambda s: s.update(EDGE=[(1 | rsnv.EDGE_KINDS << 24, 38, 0)] + s['EDGE'][1:]),
        'EDGE kind 255': lambda s: s.update(EDGE=[(1 | 255 << 24, 38, 0)] + s['EDGE'][1:]),
        'LADR node': lambda s: s.update(LADR=[(0.0, 10.0, 0.0, 0.0, 12.0, 0.0, 0.0, 0, 99, 0)]),
        'OFFM node': lambda s: s.update(OFFM=[(1, 3, 0, 99, 0.0, 10.0, 0.0, 0.0, 1.0, 0)]),
        'ISLD node': lambda s: s.update(ISLD=[(4, 99, 0, 0, 0)]),
        'PORT area': lambda s: (s.update(PORT=[(0.0, 0.0, 1.0, 0.0, 10.0, 0, 5)]), area(s, 9, [0, 1])),
        'GLST area': lambda s: s.update(GLST=[(0xFFFF,)]),
        'GBKT range': lambda s: s.update(GBKT=[(0, 9)] + s['GBKT'][1:]),
        'SECT buckets': lambda s: sect(s, 7, 5),
        'SECT areas': lambda s: sect(s, 9, 3),
        'NODE sentinel': lambda s: s.update(NODE=s['NODE'][:-1] + [(0.0, 0.0, 0.0, 3)]),
        'NODE coordinate': lambda s: s.update(NODE=[(1e7, 10.0, 0.0, 0)] + s['NODE'][1:]),
        'CEDG not flagged': lambda s: s.update(CEDG=[(0, 0, 1, 0)], OREF=[(0,)], OBJT=[(1, 2, 0.0, 0.0, 0.0, 0)]),
        'OREF object': lambda s: s.update(CEDG=[(0, 0, 1, 0)], OREF=[(5,)], OBJT=[(1, 2, 0.0, 0.0, 0.0, 0)],
                                          EDGE=[(s['EDGE'][0][0], s['EDGE'][0][1], rsnv.EDGEF_COND)] + s['EDGE'][1:]),
    }
    return cases


def corruptions(sections, header, tmp):
    """Each table's index fields set out of range, one at a time: load must raise ValueError (rsnav's rsn_load
    refuses each), never crash."""
    cases = corrupt_cases()
    out = []
    for what, data in corrupt_blobs(sections, header, tmp, cases):
        try:
            rsnv.load(data)
            out.append((what, None))
        except ValueError as e:
            out.append((what, str(e)))
        except Exception as e:  # noqa: BLE001 - test harness: anything but ValueError is a recorded crash
            out.append((what, f'CRASH {type(e).__name__}: {e}'))
    return out


def corrupt_blobs(sections, header, tmp, cases=None):
    """[(what, file bytes)] of corruptions(): each table's index fields out of range (rsnav's test_rsnav.py loads
    them in the C runtime too)."""
    import copy
    cases = corrupt_cases() if cases is None else cases
    out = []
    for what, f in cases.items():
        s2 = copy.deepcopy(sections)
        f(s2)
        path = os.path.join(tmp, 'bad.rsnv')
        rsnv.write(path, s2, header)
        out.append((what, open(path, 'rb').read()))
    return out


if __name__ == '__main__':
    tmp = tempfile.mkdtemp(prefix='rsnv_')
    sections, header = tiny()
    le, be = os.path.join(tmp, 'le.rsnv'), os.path.join(tmp, 'be.rsnv')
    rsnv.write(le, sections, header)
    rsnv.write(be, sections, header, big_endian=True)
    a, b = rsnv.load(le), rsnv.load(be)
    check(not a.big_endian and b.big_endian, 'byte order recorded and detected')
    same = all((getattr(a, k) == getattr(b, k)).all() for k in ('node_pos', 'node_first_edge', 'edge_target', 'edge_kind',
                                                                'edge_cost', 'hght', 'gbkt', 'glst', 'anod'))
    check(same and a.areas == b.areas and a.bmap == b.bmap and a.islands == b.islands and a.meta == b.meta,
          'little- and big-endian files read back identical')
    check(a.validate() == [], f'validate: {a.validate() or "consistent"}')
    cost, path, exp = rsnv.astar(a, 0, 2)
    check(cost == 76 and path == [0, 1, 2], f'reference search: cost {cost} (1/16 m), path {path}')
    cost, path, _ = rsnv.astar(a, 0, 2, kind_mask=1 << rsnv.EDGE_WALK)
    check(path == [0, 1, 2], 'a kind mask without jumps keeps to walk edges')
    cost, path, _ = rsnv.astar(a, 1, 3, kind_mask=1 << rsnv.EDGE_WALK)
    check(path == [1, 0, 3] and cost == 134, f'without jump edges 1 -> 3 goes round (cost {cost})')
    st, cost, path, exp = rsnv.search(a, 0, 2, max_expansions=1)
    check(st == rsnv.PARTIAL and path == [0] and exp == 1, f'capped at 1 expansion: only the start expanded {path}')
    st, cost, path, exp = rsnv.search(a, 0, 2, max_expansions=2)
    check(st == rsnv.DONE and path == [0, 1, 2] and exp == 2,
          f'capped at 2: the goal is reached when popped, not expanded ({st}, {exp} expansions)')
    st, cost, path, _ = rsnv.search(a, 0, 3, kind_mask=0)
    check(st == rsnv.PARTIAL and path == [0], f'no usable edges: partial path [start] ({path})')
    # costs: a wading node triples its edges' costs (water_scale), kind extras, penalties
    st, cost, path, _ = rsnv.search(a, 0, 2, kind_extra=[0, 0, 0, 0, 0, 0, 0])
    check((st, cost, path) == (rsnv.DONE, 76, [0, 1, 2]), 'zero extras change nothing')
    st, cost, path, _ = rsnv.search(a, 1, 3, kind_extra=[0, 0, 0, 0, 200, 0, 0])
    check(path == [1, 0, 3], f'a jump extra of 200 (12.5 m) sends 1 -> 3 round by walking ({path})')
    st, cost, path, _ = rsnv.search(a, 0, 2, penalty={0: 400})
    check(path == [0, 3, 2] and cost == 166, f'a penalty on edge 0 -> 1 takes the other way ({path}, cost {cost})')
    # heights: a 45-degree slope along x on a 4 x 4 area of 0.8 m cells with a per-cell grid (hstep 800 mm)
    s2, h2 = tiny()
    s2['AREA'] = [(0.0, 0.0, 2.4, 2.4, 10.0, 12.4, 0, 4, 4, 0, 0, 0, 0, 0, 0, 4, 800, 0, 4, 4, 800)]
    s2['HGHT'] = [(i * 80,) for j in range(4) for i in range(4)]
    p2 = os.path.join(tmp, 'slope.rsnv')
    rsnv.write(p2, s2, h2)
    sl = rsnv.load(p2)
    ar = sl.areas[0]
    got = [round(sl.ground(ar, x, 1.2), 4) for x in (0.0, 1.2, 2.4, -0.4, 2.8, -3.0)]
    check(got == [10.0, 11.2, 12.4, 9.6, 12.8, 9.6],
          f'ground on a slope: exact between cells, continued half a cell past the edge, held beyond ({got})')
    s2['HGHT'][1 * 4 + 1] = (0xFFFF,)                              # a block with no cells
    rsnv.write(p2, s2, h2)
    sl = rsnv.load(p2)
    g = sl.ground(sl.areas[0], 1.0, 1.0)
    g2 = sl.ground(sl.areas[0], 1.05, 1.0)
    check(abs(g - 11.0) < 0.4 and abs(g2 - g) < 0.1,
          f'next to an empty block the others carry the height: {g:.3f} (planar 11.0, within half a cell\'s rise), '
          f'continuous ({g2 - g:+.3f} over 5 cm)')
    # line of sight on stacked floors: it starts on the area nearest the start's height (LOS_START_NEAREST,
    # 2026-10-29); before, on locate's area (the lower one, first in index order), and a line along the upper floor
    # failed at its end
    s3, h3 = stacked()
    p3 = os.path.join(tmp, 'stacked.rsnv')
    rsnv.write(p3, s3, h3)
    st3 = rsnv.load(p3)
    check(st3.validate() == [], f'stacked floors: {st3.validate() or "consistent"}')
    for pa, pb, want, why in stacked_los_cases():
        got = st3.los(pa, pb)
        check(got == want, f'stacked floors, line of sight {"in sight" if want else "not in sight"}: {why} ({got})')
    rsnv.LOS_START_NEAREST = False
    old_rule = st3.los((0.2, 10.8, 0.2), (1.2, 10.8, 1.2))
    rsnv.LOS_START_NEAREST = True
    check(not old_rule, 'stacked floors: with the start on locate\'s area (the rule before) the upper floor\'s line fails')
    tr = {}
    st3.los((0.2, 10.8, 0.2), (1.2, 10.0, 1.2), tr)
    check(tr.get('rule') == 'end' and abs(tr.get('d', 0) - 0.8) < 1e-6,
          f'the trace of a refused line names the rule and the amount ({tr.get("rule")}, {tr.get("d")})')
    loc = a.locate(1.7, 10.0, 1.7)
    check(loc is not None and loc[0] == 0 and loc[1] == (2, 2), f'locate a walkable cell: {loc}')
    loc = a.locate(2.4, 10.0, 2.4, max_dist=0.1)
    check(loc is None, 'the missing cell is not walkable (no area within 0.1 m)')
    loc = a.locate(1.0, 20.0, 1.0)
    check(loc is None, 'a point 10 m above the area is not located on it')
    raw = open(le, 'rb').read()
    for what, data in (('truncated', raw[:len(raw) // 2]),
                       ('bad marker', raw[:4] + b'\x00\x00\x00\x00' + raw[8:]),
                       ('section outside the file', raw[:0x44] + struct.pack('<I', len(raw) + 64) + raw[0x48:]),
                       ('wrong stride', raw[:0x4C] + struct.pack('<I', 31) + raw[0x50:]),
                       ('not RSNV', b'XXXX' + raw[4:])):
        try:
            rsnv.load(data)
            check(False, f'{what}: rejected')
        except ValueError as e:
            check(True, f'{what}: rejected ({e})')
    # every index a reader follows is checked on load (rsn_load's checks); corrupt tables are refused, never a crash
    for what, why in corruptions(*tiny(), tmp):
        check(why is not None and not why.startswith('CRASH'), f'corrupt {what}: refused ({why})')
    import random
    rng = random.Random(26)
    crashes, refused = [], 0
    for _ in range(3000):
        data = bytearray(raw)
        for _k in range(rng.randint(1, 4)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        try:
            w = rsnv.load(bytes(data))
            for n in range(min(len(w.node_pos), 4)):               # what readers do with it must not crash either
                rsnv.search(w, n, 0)
            w.locate(1.0, 10.0, 1.0)
            for k in range(len(w.areas)):
                w.nearest_node(k, 1.0, 1.0)
        except ValueError:
            refused += 1
        except Exception as e:  # noqa: BLE001 - test harness: anything but ValueError is a recorded crash
            crashes.append(f'{type(e).__name__}: {e}')
    check(not crashes, f'3,000 random byte corruptions: {refused} refused, the rest loaded and searched, '
                       f'{len(crashes)} crashes {crashes[:2]}')
    # the heuristic (2026-10-26): admissible with the file's own scale; 0.999 overestimates on this graph
    legacy, scaled = os.path.join(tmp, 'chain0.rsnv'), os.path.join(tmp, 'chain.rsnv')
    pos, src, dst, cost = chain_file(legacy, 0)
    q = rsnv.heuristic_scale(pos, src, dst, cost)
    chain_file(scaled, q)
    w0, w1 = rsnv.load(legacy), rsnv.load(scaled)
    best = dijkstra(w1, 0, 1)
    c0 = rsnv.search(w0, 0, 1)[1]
    c1 = rsnv.search(w1, 0, 1)[1]
    check(c0 > best, f'with the legacy 0.999 the search returns {c0} where {best} exists (the bug reproduced)')
    check(c1 == best, f"with the file's scale {q / rsnv.H_SCALE_ONE:.6f} the search returns the cheapest, {c1}")
    check(w1.h_scale_q == q and w0.h_scale == rsnv.H_SCALE_LEGACY, 'the scale round-trips; 0 reads as 0.999')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
