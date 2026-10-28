"""NavKit: compare two pathdata.hpd files of a level on the positions where its AI is ordered to go.

Usage: navcompare.py <level.xml> <reference.hpd> <candidate.hpd> [--json]

For the level's AI order and patrol positions (ordercheck.order_points) it reports:
  coverage       of the positions the reference has on its walkable areas, how many the candidate has too
                 (NavKit's headline accuracy figure), plus positions only the candidate covers
  main network   positions in a route group at least 25 % the size of the largest group, per file
  pairwise       of all position pairs the reference's route network connects, the share the candidate also
                 connects (a guide: the reference has gaps of its own)
  size           sectors, route nodes and edges per file

The reference is normally the level's original (DICE) file, the candidate NavKit's build; any two files work.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hpdtool  # noqa: E402
import ordercheck  # noqa: E402


def main_network(data, points):
    largest = max(data.group_size.values()) if data.group_size else 0
    count = 0
    for _, _, p in points:
        _, size = data.nearest_group(p)
        if size and size >= 0.25 * largest:
            count += 1
    return count


def compare(level_xml, reference, candidate):
    points = ordercheck.order_points(level_xml)
    ref, cand = ordercheck.PathData(reference), ordercheck.PathData(candidate)
    on_ref = [ref.on_area(p) for _, _, p in points]
    on_cand = [cand.on_area(p) for _, _, p in points]
    group_ref = [ref.nearest_group(p)[0] if on_ref[i] else None for i, (_, _, p) in enumerate(points)]
    group_cand = [cand.nearest_group(p)[0] if on_cand[i] else None for i, (_, _, p) in enumerate(points)]
    both = sum(1 for a, b in zip(on_ref, on_cand) if a and b)
    only_cand = sum(1 for a, b in zip(on_ref, on_cand) if b and not a)
    idx = [i for i in range(len(points)) if group_ref[i] is not None]
    pairs = connected = 0
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            x, y = idx[a], idx[b]
            if group_ref[x] == group_ref[y]:
                pairs += 1
                if group_cand[x] is not None and group_cand[x] == group_cand[y]:
                    connected += 1
    files = {}
    for name, path in (('reference', reference), ('candidate', candidate)):
        f = hpdtool.parse(open(path, 'rb').read())
        files[name] = {'file': path, 'sectors': len(f.sectors), 'nodes': sum(len(s.nodes) for s in f.sectors),
                       'edges': sum(len(s.edges) for s in f.sectors)}
    return {
        'positions': len(points),
        'reference_covers': sum(on_ref),
        'candidate_covers_of_those': both,
        'coverage_percent': round(both / max(sum(on_ref), 1) * 100, 1),
        'candidate_only': only_cand,
        'main_network': {'reference': main_network(ref, points), 'candidate': main_network(cand, points)},
        'pairwise': {'connected': connected, 'pairs': pairs, 'percent': round(connected / max(pairs, 1) * 100, 1)},
        'files': files,
    }


def report(r, log=print):
    log(f"{r['positions']} order positions; the reference covers {r['reference_covers']}, the candidate {r['candidate_covers_of_those']} "
        f"of those ({r['coverage_percent']} %) + {r['candidate_only']} more")
    log(f"main route network: reference {r['main_network']['reference']}, candidate {r['main_network']['candidate']}; "
        f"pairwise {r['pairwise']['connected']}/{r['pairwise']['pairs']} ({r['pairwise']['percent']} %)")
    fr, fc = r['files']['reference'], r['files']['candidate']
    log(f"sectors {fr['sectors']} / {fc['sectors']}; nodes {fr['nodes']} / {fc['nodes']}; edges {fr['edges']} / {fc['edges']}")


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    if len(args) != 3:
        print(__doc__)
        return 1
    r = compare(*args)
    if '--json' in argv:
        print(json.dumps(r, indent=1))
    else:
        report(r)
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('navcompare', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
