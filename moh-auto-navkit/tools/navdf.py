r"""NavKit: D lines and F cells of two path-data files compared (a candidate against DICE's reference).

  navdf.py <reference.hpd> <candidate.hpd> [--clip minX,minZ,maxX,maxZ]
  navdf.py --level <level> <candidate.hpd> [--opfor <folder>]      DICE's file of the level as the reference

D lines are compared as unit sides - one pitch of a line with its owner side (the cell on the line's right, whose
height it carries) - and F cells as the cells their polygons cover, per axis and direction; normal (0.8 m) and
detailed (0.2 m) areas apart. A side or cell matches one of the other file at the same place within 1 m of height
(stacked floors). Measures (percent):
  df_d_precision / df_d_recall     candidate D sides that are DICE's / DICE's D sides the candidate has
  df_d_recall_near                 ... DICE's D sides with a candidate side on the same or the next line (one pitch)
  df_d_recall_shared               ... DICE's D sides whose owner cell is candidate ground (the D rule on shared
                                   ground: ground NavKit lacks is the ground measure's matter, not this one's)
  df_f_precision / df_f_recall     the same for F cells
  df_d_dy                          median height difference of matched D sides (m)
Counts: df_d_reference / df_d_candidate (D lines), df_f_reference / df_f_candidate (F records), per class too.
navparity.py adds these to every level row (navregress.py checks them against the reference run).
"""
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hpdtool  # noqa: E402
import kymesh  # noqa: E402

DY = 1.0                    # height tolerance of a match (m): one floor
GRID = 0.8                  # ground cells of navdiff (shared-ground test)


def _cls(pitch):
    return 'd' if pitch < 0.5 else 'n'


def _in_poly(x, z, pts):
    inside = False
    n = len(pts)
    for k in range(n):
        (ax, _, az), (bx, _, bz) = pts[k], pts[(k + 1) % n]
        if (az > z) != (bz > z) and x < ax + (z - az) * (bx - ax) / (bz - az):
            inside = not inside
    return inside


def features(meshes, clip=None):
    """{'d': {(cls, axis, line, along, side): [heights]}, 'f': {(cls, axis, sign, i, j): [heights]}, counts}.
    meshes: [kymesh.Mesh]; clip: (minX, minZ, maxX, maxZ) or None (features outside are left out)."""
    d, f = defaultdict(list), defaultdict(list)
    cnt = defaultdict(int)

    def inside(x, z):
        return clip is None or (clip[0] <= x <= clip[2] and clip[1] <= z <= clip[3])
    for m in meshes:
        for a in m.areas:
            P, c = a.pitch, _cls(a.pitch)
            H = P / 2.0
            for line in a.d_lines:
                if not inside(line[0][0], line[0][2]):
                    continue
                cnt[f'd_{c}'] += 1
                for p0, p1 in zip(line[:-1], line[1:]):
                    dx, dz = p1[0] - p0[0], p1[2] - p0[2]
                    L = math.hypot(dx, dz)
                    if L < 1e-6:
                        continue
                    if abs(dx) > 1e-3 and abs(dz) > 1e-3:
                        cnt[f'd_diag_{c}'] += 1
                        continue
                    n = max(1, int(round(L / P)))
                    for k in range(n):
                        t = (k + 0.5) / n
                        x, z, y = p0[0] + dx * t, p0[2] + dz * t, p0[1] + (p1[1] - p0[1]) * t
                        if abs(dx) < 1e-3:   # x-line; owner on the right of the direction
                            key = (c, 'x', int(round(x / H)), int(round(z / P)), 1 if dz > 0 else -1)
                        else:
                            key = (c, 'z', int(round(z / H)), int(round(x / P)), 1 if dx < 0 else -1)
                        d[key].append(y)
            for cell in a.cells:
                pts = cell.points
                if not pts or not inside(pts[0][0], pts[0][2]):
                    continue
                cnt[f'f_{c}'] += 1
                axis = 'x' if abs(cell.direction[0]) > 0.5 else 'z'
                sign = 1 if (cell.direction[0] + cell.direction[2]) > 0 else -1
                xs, zs = [p[0] for p in pts], [p[2] for p in pts]
                y = sum(p[1] for p in pts) / len(pts)
                for i in range(int(math.floor(min(xs) / P)), int(math.ceil(max(xs) / P)) + 1):
                    for j in range(int(math.floor(min(zs) / P)), int(math.ceil(max(zs) / P)) + 1):
                        if _in_poly(i * P + P * 1.37e-3, j * P + P * 0.71e-3, pts):
                            f[(c, axis, sign, i, j)].append(y)
    return {'d': d, 'f': f, 'counts': dict(cnt)}


def _match(ref, cand):
    """(matched, |dy| of the matches): per key, heights paired greedily by nearest within DY."""
    hit, dys = 0, []
    for key, ys in ref.items():
        cs = list(cand.get(key, ()))
        for y in ys:
            if not cs:
                break
            k = min(range(len(cs)), key=lambda q: abs(cs[q] - y))
            if abs(cs[k] - y) <= DY:
                hit += 1
                dys.append(abs(cs[k] - y))
                cs.pop(k)
    return hit, dys


def _near(ref, cand):
    """DICE's D sides with a candidate side on the same line or the next one (one pitch), same direction, within DY."""
    hit = 0
    for (c, axis, line, along, side), ys in ref.items():
        for y in ys:
            if any(abs(cy - y) <= DY for dl in (-2, 0, 2) for cy in cand.get((c, axis, line + dl, along, side), ())):
                hit += 1
    return hit


def ground_of(sectors):
    """{(i, j) on the 0.8 m grid: [ground heights]} from navdiff sectors (their cached cells)."""
    g = defaultdict(list)
    for s in sectors.values():
        for cells in s.cells():
            for ij, y in cells.items():
                g[ij].append(y)
    return g


def _shared(ref_d, ground):
    """DICE's D sides whose owner cell is ground in the other file: {key: heights} restricted."""
    out = {}
    for key, ys in ref_d.items():
        c, axis, line, along, side = key
        P = 0.2 if c == 'd' else 0.8
        owner = (line + side) // 2
        x, z = (owner * P, along * P) if axis == 'x' else (along * P, owner * P)
        ij = (int(round(x / GRID)), int(round(z / GRID)))
        keep = [y for y in ys if any(abs(gy + 0.95 - y) <= DY for gy in ground.get(ij, ()))]
        if keep:
            out[key] = keep
    return out


def pct(a, b):
    return round(100.0 * a / b, 2) if b else 100.0


def compare(ref_meshes, cand_meshes, clip=None, cand_ground=None):
    """The measures (module doc) as a dict. cand_ground: ground_of(candidate sectors), for df_d_recall_shared."""
    R, C = features(ref_meshes, clip), features(cand_meshes)
    row = {}
    for cls in ('', 'n', 'd'):
        rd = {k: v for k, v in R['d'].items() if not cls or k[0] == cls}
        cd = {k: v for k, v in C['d'].items() if not cls or k[0] == cls}
        rf = {k: v for k, v in R['f'].items() if not cls or k[0] == cls}
        cf = {k: v for k, v in C['f'].items() if not cls or k[0] == cls}
        nr, nc = sum(map(len, rd.values())), sum(map(len, cd.values()))
        hit, dys = _match(rd, cd)
        fr, fc = sum(map(len, rf.values())), sum(map(len, cf.values()))
        fhit, _ = _match(rf, cf)
        suf = f'_{cls}' if cls else ''
        row[f'df_d_precision{suf}'] = pct(hit, nc)
        row[f'df_d_recall{suf}'] = pct(hit, nr)
        row[f'df_d_recall_near{suf}'] = pct(_near(rd, cd), nr)
        row[f'df_f_precision{suf}'] = pct(fhit, fc)
        row[f'df_f_recall{suf}'] = pct(fhit, fr)
        row[f'df_d_sides_reference{suf}'] = nr
        row[f'df_d_sides_candidate{suf}'] = nc
        row[f'df_f_cells_reference{suf}'] = fr
        row[f'df_f_cells_candidate{suf}'] = fc
        if not cls:
            row['df_d_dy'] = round(sorted(dys)[len(dys) // 2], 3) if dys else None
            if cand_ground is not None:
                sh = _shared(rd, cand_ground)
                row['df_d_recall_shared'] = pct(_match(sh, cd)[0], sum(map(len, sh.values())))
    for side, X in (('reference', R), ('candidate', C)):
        k = X['counts']
        row[f'df_d_{side}'] = k.get('d_n', 0) + k.get('d_d', 0)
        row[f'df_f_{side}'] = k.get('f_n', 0) + k.get('f_d', 0)
        row[f'df_d_{side}_n'], row[f'df_d_{side}_d'] = k.get('d_n', 0), k.get('d_d', 0)
        row[f'df_f_{side}_n'], row[f'df_f_{side}_d'] = k.get('f_n', 0), k.get('f_d', 0)
    return row


def meshes_of(path):
    return [kymesh.parse_mesh(s.mesh) for s in hpdtool.parse(open(path, 'rb').read()).sectors]


def report(row, log=print):
    log(f"D lines   reference {row['df_d_reference']} ({row['df_d_reference_n']} normal / {row['df_d_reference_d']} detailed), "
        f"candidate {row['df_d_candidate']} ({row['df_d_candidate_n']} / {row['df_d_candidate_d']})")
    log(f"F cells   reference {row['df_f_reference']} ({row['df_f_reference_n']} / {row['df_f_reference_d']}), "
        f"candidate {row['df_f_candidate']} ({row['df_f_candidate_n']} / {row['df_f_candidate_d']})")
    for cls, name in (('', 'all'), ('_n', 'normal'), ('_d', 'detailed')):
        log(f"{name:9s} D sides {row['df_d_sides_candidate' + cls]} / {row['df_d_sides_reference' + cls]}: precision "
            f"{row['df_d_precision' + cls]} %, recall {row['df_d_recall' + cls]} % (one pitch: {row['df_d_recall_near' + cls]} %); "
            f"F cells {row['df_f_cells_candidate' + cls]} / {row['df_f_cells_reference' + cls]}: precision "
            f"{row['df_f_precision' + cls]} %, recall {row['df_f_recall' + cls]} %")
    if 'df_d_recall_shared' in row:
        log(f"D recall on shared ground {row['df_d_recall_shared']} %")
    log(f"D height difference of matched sides, median {row['df_d_dy']} m")


def main(argv):
    args = argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return 1
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    clip = tuple(float(v) for v in opt('--clip').split(',')) if opt('--clip') else None
    if '--level' in args:
        import navparity
        root = navparity.opfor_root(opt('--opfor'))
        lv = opt('--level')
        ref = navparity.reference_of(root, lv)
        cand = [a for a in args if a.lower().endswith('.hpd')][0]
        clip = clip or navparity.terrain_bounds(root, lv)
    else:
        ref, cand = [a for a in args if not a.startswith('--') and a.lower().endswith('.hpd')][:2]
    import navdiff
    cs = navdiff.load(cand)
    row = compare(meshes_of(ref), [s.mesh for s in cs.values()], clip, ground_of(cs))
    report(row)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
