r"""NavKit: a level's RSNV file in the navigation runtime itself (rsnav, through its ctypes bindings in
project-opfor-moh - src/rsnav/bindings/rsnav.py and its host build build/rsnav/rsnav.dll).

  rsnavcheck.py <level> [<level> ...] [--rsnav <rsnav folder>] [--opfor <root>] [--reference N] [--json out.json]

Per level: the file loads (rsn_load checks every offset, index and coordinate), the level's spawn points and
objectives (ordercheck.order_points) are placed on the walkable cells (rsn_locate within 1.5 m) and given a start
node in sight (rsn_start_node), and a route is searched from every placed point to every other (both directions: a
drop link goes one way) with the soldier's filter (rsn_filter_soldier: water costs, tight edges priced) and smoothed
(rsn_smooth). Reported: points placed, routes found (done / partial, over the ordered pairs), the unordered pairs
routed at least one way and both ways, search time, and - on N random pairs (--reference, default 20) - that the C search's
cost equals the Python reference's (core rsnv.search) on the same file.

Finds rsnav at --rsnav, else RSNAV_ROOT, else project-opfor-moh's src/rsnav next to the suite's folder.
"""
import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.dirname(HERE), os.path.join(SUITE, 'core')]
from mohcore import rsnv  # noqa: E402

LOCATE = 1.5          # m: a point is placed on a walkable cell this close (rsn_locate's max_dist)
START = 6.0           # m: its start node in sight this close (rsn_start_node)


def find_rsnav(given=None):
    for c in (given, os.environ.get('RSNAV_ROOT'),
              os.path.join(os.path.dirname(SUITE), 'project-opfor-moh', 'src', 'rsnav')):
        if c and os.path.isfile(os.path.join(c, 'bindings', 'rsnav.py')):
            return c
    return None


def bindings(rsnav_dir):
    sys.path.insert(0, os.path.join(rsnav_dir, 'bindings'))
    import rsnav as rb
    if not os.path.isfile(rb.LIB):
        raise FileNotFoundError(f'{rb.LIB}: build rsnav first (python -B build.py host in {rsnav_dir})')
    return rb


def check_level(root, lv, rb, reference=20, log=print):
    import navkit
    import ordercheck
    pr = navkit.Project(root)
    path = os.path.join(pr.out_dir(lv), f'{lv}.rsnv')
    data = open(path, 'rb').read()
    t0 = time.perf_counter()
    w = rb.World(data)
    load_ms = (time.perf_counter() - t0) * 1000
    xml = os.path.join(pr.level_work(lv), 'dist', 'levels', pr.settings(lv)['level'] + '.xml')
    pts = ordercheck.order_points(xml)
    placed = []
    for kind, name, p in pts:
        loc = w.locate(p[0], p[1], p[2], LOCATE)
        if loc is None:
            continue
        g = w.ground(loc[0], p[0], p[2])
        q = (p[0], g, p[2])
        n = w.start_node(q, START)
        if n is not None:
            placed.append((kind, q, n))
    filt = w.soldier_filter()
    done = partial = 0
    t_search = 0.0
    smooth_fail = 0
    fails = []
    pairs = [(a, b) for a in range(len(placed)) for b in range(len(placed)) if a != b]
    costs = {}
    for a, b in pairs:
        t1 = time.perf_counter()
        st, cost, nodes, _exp, _steps = w.search(placed[a][2], placed[b][2], filt=filt)
        t_search += time.perf_counter() - t1
        if st == 'done':
            done += 1
            costs[(a, b)] = cost
            if nodes and not w.smooth(nodes, placed[a][1]):
                smooth_fail += 1
        else:
            partial += 1
            fails.append((placed[a][0], [round(v, 1) for v in placed[a][1]], placed[b][0], [round(v, 1) for v in placed[b][1]]))
    # the C search against the Python reference on the same file, same filter (water x1.5 / x3, tight +50 m)
    ref_ok = ref_n = 0
    if reference and costs:
        pw = rsnv.load(data)
        rng = random.Random(lv)
        for a, b in rng.sample(sorted(costs), min(reference, len(costs))):
            r = rsnv.search(pw, placed[a][2], placed[b][2], water_scale=(256, 384, 768), tight_extra=50 * 16)
            ref_n += 1
            ref_ok += r is not None and r[1] == costs[(a, b)]       # (status, cost, nodes, expansions)
    either = sum(1 for a in range(len(placed)) for b in range(a + 1, len(placed)) if (a, b) in costs or (b, a) in costs)
    both = sum(1 for a in range(len(placed)) for b in range(a + 1, len(placed)) if (a, b) in costs and (b, a) in costs)
    unordered = len(placed) * (len(placed) - 1) // 2
    res = {'level': lv, 'file_mb': round(len(data) / 1e6, 2), 'load_ms': round(load_ms, 1), 'nodes': w.nodes,
           'edges': w.edges, 'areas': w.areas, 'points': len(pts), 'placed': len(placed), 'pairs': len(pairs),
           'done': done, 'partial': partial, 'smooth_failed': smooth_fail,
           'search_ms_mean': round(t_search / max(len(pairs), 1) * 1000, 3), 'reference_equal': [ref_ok, ref_n],
           'not_routed': fails[:10], 'unordered': unordered, 'either_way': either, 'both_ways': both}
    log(f'{lv}: loaded in {load_ms:.1f} ms ({res["file_mb"]} MB, {w.nodes} nodes, {w.edges} edges, {w.areas} areas); '
        f'points placed {len(placed)}/{len(pts)}; routes {done}/{len(pairs)}'
        + (f' ({partial} partial)' if partial else '') + f'; pairs routed one way {either}/{unordered}, both ways '
        f'{both}/{unordered}; {res["search_ms_mean"]} ms per search; '
        f'smoothing failed {smooth_fail}; C = Python reference cost on {ref_ok}/{ref_n}')
    return res


def main(argv):
    ap = argparse.ArgumentParser(prog='rsnavcheck.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('levels', nargs='+')
    ap.add_argument('--rsnav')
    ap.add_argument('--opfor')
    ap.add_argument('--reference', type=int, default=20)
    ap.add_argument('--json')
    a = ap.parse_args(argv[1:])
    import navkit
    root = navkit.find_opfor(a.opfor)
    d = find_rsnav(a.rsnav)
    if d is None:
        print('rsnav not found (--rsnav or RSNAV_ROOT: the folder with bindings\\rsnav.py)')
        return 1
    rb = bindings(d)
    out = [check_level(root, navkit.Project(root).level(lv), rb, a.reference) for lv in a.levels]
    if a.json:
        json.dump(out, open(a.json, 'w'), indent=1)
    # at least two points placed (else there is nothing to route: a broken export would pass), every pair routed at
    # least one way (a drop link's ground is left one way only), no smoothing failure, the C search equal to the
    # reference
    return 0 if all(r['placed'] >= 2 and r['either_way'] == r['unordered'] and r['smooth_failed'] == 0 and
                    r['reference_equal'][0] == r['reference_equal'][1] for r in out) else 1


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('rsnavcheck', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
