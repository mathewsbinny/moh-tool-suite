r"""NavKit's routes across domains (tools\navdomains.py) on graphs built here (no game data): a bay with a beach on
each side, a boat at a dock, a road with a jeep; the planner takes the boat across the bay and walks the rest, the
jeep along the road, walks when the boat is gone or the trip is short, finds goals reachable only by boat, never
boards at a shore link (boats are entered at their spawn only), and its hierarchical plans cost exactly what one
search over all domains joined costs (300 random queries on random worlds), legs expanded into positions.

  python test_navdomains.py
"""
import math
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import navdomains as nd  # noqa: E402


def chain(xs, y=0.0, z=0.0):
    pos = [(x, y, z) for x in xs]
    return pos, [(k, k + 1) for k in range(len(pos) - 1)]


def bay_world(boat=True, jeep=True):
    """West shore: infantry nodes x 0..100 (z 0); a walkway around the bay x 100..900 at z 400 (long way round);
    east shore x 900..1000 (z 0). Boat graph across the bay x 110..890 (z 0). Road for a jeep along the west shore
    x 0..100 is short; a land graph from x 0 to 100. Dock at x 100 (boat spawn), beach exit at x 890 -> 900."""
    west, _ = chain(np.arange(0, 101, 10))
    around = [(100, 0, 0)] + [(x, 0, 400) for x in np.arange(100, 901, 50)] + [(900, 0, 0)]
    east, _ = chain(np.arange(900, 1001, 10))
    pos = west + around[1:-1] + east
    n_w, n_a = len(west), len(around) - 2
    edges = [(k, k + 1) for k in range(n_w - 1)]
    edges += [(n_w - 1, n_w)] + [(n_w + k, n_w + k + 1) for k in range(n_a - 1)] + [(n_w + n_a - 1, n_w + n_a)]
    edges += [(n_w + n_a + k, n_w + n_a + k + 1) for k in range(len(east) - 1)]
    # an island only boats reach: one node at (500, 0, -300)
    pos.append((500, 0, -300))
    inf = nd.graph_domain('infantry', pos, edges, nd.INFANTRY_SPEED)
    bpos, bedges = chain(np.arange(110, 891, 30))
    bpos.append((500, 0, -280))
    mid = min(range(len(bpos) - 1), key=lambda k: abs(bpos[k][0] - 500))
    bedges.append((mid, len(bpos) - 1))
    boat_d = nd.graph_domain('boat_pbl', bpos, bedges, 17 * nd.SPEED_FACTOR)
    lpos, ledges = chain(np.arange(0, 101, 25), z=5.0)
    land = nd.graph_domain('land', lpos, ledges, nd.LAND_SPEED * nd.SPEED_FACTOR)
    W = nd.World([inf, boat_d, land])
    idx = lambda d, p: nd.nearest(W.domains[d].pos, p)
    if boat:
        W.add('enter', ('infantry', idx('infantry', (100, 0, 0))), ('boat_pbl', 0), nd.ENTER_S, 'boat', (105, 0, 0))
    W.add('exit', ('boat_pbl', idx('boat_pbl', (890, 0, 0))), ('infantry', idx('infantry', (900, 0, 0))), nd.EXIT_S + 4, 'beach east')
    W.add('exit', ('boat_pbl', len(bpos) - 1), ('infantry', len(pos) - 1), nd.EXIT_S + 6, 'island beach')
    W.add('exit', ('boat_pbl', 0), ('infantry', idx('infantry', (100, 0, 0))), nd.EXIT_S, 'dock back')
    if jeep:
        W.add('enter', ('infantry', 0), ('land', 0), nd.ENTER_S, 'jeep', (0, 0, 2))
    for v in range(land.n):
        W.add('exit', ('land', v), ('infantry', idx('infantry', land.pos[v])), nd.EXIT_S, 'dismount')
    return W


def random_world(rng):
    """Random geometric graphs per domain and random transitions (enter only from infantry, exits to it)."""
    doms = []
    for name, n, speed in (('infantry', 60, 4.0), ('land', 25, 9.0), ('boat_pbl', 20, 10.0), ('air', 8, 40.0)):
        pos = rng.uniform(0, 1000, (n, 3))
        pos[:, 1] = 0
        edges = []
        for a in range(n):
            d = np.hypot(*(pos[:, [0, 2]] - pos[a, [0, 2]]).T)
            for b in np.argsort(d)[1:4]:
                edges.append((a, int(b)))
        doms.append(nd.graph_domain(name, pos, edges, speed))
    W = nd.World(doms)
    for d in doms[1:]:
        for _ in range(3):
            W.add('enter', ('infantry', int(rng.integers(60))), (d.name, int(rng.integers(d.n))), float(rng.uniform(2, 6)), f'spawn {d.name}')
        for _ in range(6):
            W.add('exit', (d.name, int(rng.integers(d.n))), ('infantry', int(rng.integers(60))), float(rng.uniform(1, 8)), 'exit')
    return W


def modes(legs):
    return [l[0] for l in legs if l[0] != 'transition' and l[3] > 0]


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    W = bay_world()
    inf = W.domains['infantry']
    at = lambda p: ('infantry', nd.nearest(inf.pos, p))
    tables = W.tables()
    cost, legs = W.plan(at((90, 0, 0)), at((1000, 0, 0)), tables)
    walk = W.plan_flat(at((90, 0, 0)), at((1000, 0, 0)), unavailable={t.id for t in W.transitions})
    check(modes(legs) == ['infantry', 'boat_pbl', 'infantry'] and cost < walk,
          f'across the bay: walk to the dock, boat, walk from the beach ({cost:.0f} s against {walk:.0f} s on foot)')
    exp = W.expand(legs)
    check(any(e.get('domain') == 'boat_pbl' and e['metres'] > 700 for e in exp) and sum('transition' in e for e in exp) == 2,
          'the plan expands into positions: a boat leg of 780 m, one enter and one exit')
    cost2, legs2 = W.plan(at((90, 0, 0)), at((1000, 0, 0)), tables, unavailable={'boat'})
    check(modes(legs2) == ['infantry'] and abs(cost2 - walk) < 1e-6, 'boat taken (unavailable): the long walk round')
    c3, l3 = W.plan(at((0, 0, 0)), at((30, 0, 0)), tables)
    check(modes(l3) == ['infantry'], 'a short trip is walked')
    c4, l4 = W.plan(at((0, 0, 0)), at((100, 0, 0)), tables)
    check('land' in modes(l4) or modes(l4) == ['infantry'], f'along the road: jeep or foot, the cheaper ({modes(l4)}, {c4:.1f} s)')
    c4w = W.plan_flat(at((0, 0, 0)), at((100, 0, 0)), unavailable={t.id for t in W.transitions})
    check(c4 <= c4w + 1e-9, 'a plan never costs more than walking')
    island = ('infantry', inf.n - 1)
    ci, li = W.plan(at((90, 0, 0)), island, tables)
    cw = W.plan_flat(at((90, 0, 0)), island, unavailable={t.id for t in W.transitions})
    check(math.isinf(cw) and math.isfinite(ci) and 'boat_pbl' in modes(li), 'the island: unreachable on foot, reached by boat')
    ce, le = W.plan(at((1000, 0, 0)), at((90, 0, 0)), tables)
    check('boat_pbl' not in modes(le), 'from the east shore no boat is boarded (shore links are exits; the boat is at the dock)')
    Wn = bay_world(boat=False)
    cn, ln = Wn.plan(at((90, 0, 0)), island)
    check(math.isinf(cn) and ln == [], 'no boat on the level: the island is unreachable')
    # hierarchical against one joined search, random worlds
    rng = np.random.default_rng(7)
    bad, n, used = 0, 0, set()
    for _ in range(10):
        Wr = random_world(rng)
        tab = Wr.tables()
        for _ in range(30):
            a, b = int(rng.integers(60)), int(rng.integers(60))
            un = set(int(x) for x in rng.choice(len(Wr.transitions), size=int(rng.integers(0, 4)), replace=False))
            c, lg = Wr.plan(('infantry', a), ('infantry', b), tab, unavailable=un)
            f = Wr.plan_flat(('infantry', a), ('infantry', b), unavailable=un)
            n += 1
            bad += not ((math.isinf(c) and math.isinf(f)) or abs(c - f) < 1e-6)
            used.update(modes(lg))
            # the legs add up to the plan's cost, transitions and legs alternate sensibly
            if math.isfinite(c) and abs(sum(l[3] for l in lg) - c) > 1e-6:
                bad += 1
    check(bad == 0 and n == 300, f'hierarchical plans cost what one joined search costs, legs add up ({n} queries, {bad} differ)')
    check({'land', 'boat_pbl', 'air'} <= used, f'the random queries used every domain ({sorted(used)})')
    print(f'\n{"all passed" if not failed else f"{failed} failed"}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
