"""NavKit: drop links - one-way route edges where a soldier steps off a ledge onto walkable ground below it, between
ground regions no walk joins (navbuild.DROP_LINKS).

MOH's soldier climbs a 0.3 m step and jumps 0.65 m, but walks off any ledge: ground a ledge above the rest of the
map is left by dropping. Without a link such ground is a route island of its own - mp_02's western spawn basin (six
spawns at 90-94 m, fenced by invisible walls and rock, whose way out is a 1.7 m drop over a steep bank) was cut off
from the network (2026-10-30). A drop link joins the surface at the ledge to the ground below; the runtime (rsnav)
reads a one-way edge as RSNV's drop kind and walks it.

Rules (on the build's kept surfaces, per stage and pitch):
  - regions: kept surfaces joined to their 8 neighbours by a height step of at most navbuild.LINK_STEP (components;
    numpy labelling, no recursion);
  - a candidate: a kept surface k of any region but the largest and at least DROP_MIN_AREA, a column at most DROP_REACH
    away (metres, diagonals by their length: at 0.8 m pitch two cells straight, one diagonally) in one of 8 directions
    holding a kept surface n of a larger region DROP_MIN .. DROP_MAX below k, with no surface
    in between from k's height + mohsoldier.STEP up to k's height + the soldier's height (no wall at the ledge), every
    column on the way holding a surface at or below k's height + mohsoldier.STEP (a column with none is one the
    sampler's cast stopped in: inside solid geometry, 2026-10-30), no column on the way whose surfaces within
    mohsoldier.STEP of k's height are all capsule-blocked (a wall taller than the soldier: the sampler records only
    its top, above the body range, and the floor at the ledge's height under it is too cramped for the capsule -
    mp_09_overrun's links through a facade, 2026-10-31), and nothing above n in its own column below k's height + the
    soldier's height (the landing is open);
  - floor within reach of a run off the ledge: the first column on the way with a surface DROP_MIN .. DROP_MAX below
    k (kept or not: floor too cramped for the capsule at a curb's foot is still floor to land on, mp_10's 0.5 m curbs)
    no farther out than the soldier runs (mohsoldier.RUN) while it falls that far, plus one cell; from there it walks
    to n (mp_09_overrun had a 1 m drop to a landing 2.8 m out with no floor before it, through a wall);
  - a diagonal step blocked by a wall (either kind above) in either of the two straight columns beside it (a corner);
  - at most DROP_PER_PAIR links per pair of regions, DROP_SPACING apart, the shortest first.
"""
import math
from collections import defaultdict

import numpy as np

import mohsoldier
import navsample

DROP_MIN = 0.5            # m: lower than this, a walk link (LINK_STEP) or the step rule joins the floors
DROP_MAX = 3.0            # m: the highest ledge a soldier drops from (7.7 m/s at the landing; MOH's data has no soldier
                          # fall-damage threshold to read - a conservative choice)
DROP_REACH = 2.0          # m: the landing at most this far out from the ledge's surface
DROP_PER_PAIR = 3
DROP_MIN_AREA = 4.0       # m2: an upper region smaller than this (a rock top, a crate) gets no drop link
DROP_SPACING = 4.0        # m
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
STATS = {}


def run_reach(drop, pitch):
    """How far out a landing drop metres down can be: the run (mohsoldier.RUN) for the time of the fall, plus a cell."""
    return mohsoldier.RUN * math.sqrt(2.0 * drop / mohsoldier.GRAVITY) + pitch


def components(s, keep, link_step):
    """A label per surface (kept surfaces joined to their 8 neighbours by |dy| <= link_step; -1 for the others)."""
    ks = np.nonzero(keep)[0]
    lab = np.full(len(s.y), -1, np.int64)
    if not len(ks):
        return lab
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    y = s.y.astype(np.float64)
    # kept surfaces per column, as (column, layer) -> surface: the layer is the surface's rank among its column's kept ones
    kc = col[ks]
    order = np.lexsort((y[ks], kc))
    ks, kc = ks[order], kc[order]
    first = np.r_[0, np.nonzero(np.diff(kc))[0] + 1]
    rank = np.arange(len(ks)) - np.repeat(first, np.diff(np.r_[first, len(ks)]))
    layers = int(rank.max()) + 1
    grid = np.full((layers, s.nz, s.nx), -1, np.int64)
    grid[rank, kc // s.nx, kc % s.nx] = ks
    us, vs = [], []
    for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1)):
        ia0, ia1 = max(0, -di), s.nx - max(0, di)          # a's cell (i, j) pairs with b's (i + di, j + dj)
        ja0, ja1 = max(0, -dj), s.nz - max(0, dj)
        if ia1 <= ia0 or ja1 <= ja0:
            continue
        a = grid[:, ja0:ja1, ia0:ia1]
        b = grid[:, ja0 + dj:ja1 + dj, ia0 + di:ia1 + di]
        for la in range(layers):
            for lb in range(layers):
                u, v = a[la].ravel(), b[lb].ravel()
                m = (u >= 0) & (v >= 0)
                u, v = u[m], v[m]
                m = np.abs(y[u] - y[v]) <= link_step
                us.append(u[m])
                vs.append(v[m])
    u = np.concatenate(us) if us else np.zeros(0, np.int64)
    v = np.concatenate(vs) if vs else np.zeros(0, np.int64)
    lab[ks] = ks
    while True:                                      # until no label changes (each pass only lowers labels: it ends)
        lu, lv = lab[u], lab[v]
        mn = np.minimum(lu, lv)
        before = lab.copy()
        np.minimum.at(lab, lu, mn)
        np.minimum.at(lab, lv, mn)
        while True:                                  # pointer jumping: every label to its root
            nxt = lab[np.maximum(lab, 0)]
            nxt[lab < 0] = -1
            if np.array_equal(nxt, lab):
                break
            lab = nxt
        if np.array_equal(lab, before):
            break
    return lab


def find(s, keep, link_step, pitch):
    """[(upper surface, lower surface)] drop links of a stage's kept surfaces."""
    STATS.clear()
    lab = components(s, keep, link_step)
    ks = np.nonzero(keep)[0]
    if not len(ks):
        return []
    size = defaultdict(int)
    for v in lab[ks].tolist():
        size[v] += 1
    largest = max(size, key=size.get)
    y = s.y.astype(np.float64)
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    reach = max(1, int(math.ceil(DROP_REACH / pitch)) + 1)
    head = mohsoldier.HEIGHT
    blocked = navsample.BLOCKED | navsample.FAULT
    cands = defaultdict(list)                       # (upper region, lower region) -> [(horizontal distance, k, n)]
    small = max(1, int(math.ceil(DROP_MIN_AREA / (pitch * pitch))))
    for k in ks[lab[ks] != largest].tolist():
        if size[int(lab[k])] < small:
            continue
        c = int(col[k])
        i, j = c % s.nx, c // s.nx
        yk = y[k]

        def cramped(rng):
            # floor at the ledge's height (within a step) only where the capsule does not fit, under something above
            # the head: inside a wall whose surfaces in the body range the sampler never recorded (a mesh wall: only
            # its top - mp_09_overrun's facades). Without anything overhead it is the cramped edge of open ground,
            # which a soldier crosses (mp_10's 0.5 m curbs, 2026-10-31)
            near = [m for m in rng if abs(y[m] - yk) <= mohsoldier.STEP]
            return (bool(near) and all(s.flags[m] & blocked for m in near) and
                    any(y[m] >= yk + head for m in rng))

        def wall(a, b):
            cc = b * s.nx + a
            rng = range(s.start[cc], s.start[cc + 1])
            return any(yk + mohsoldier.STEP < y[m] < yk + head for m in rng) or cramped(rng)
        for di, dj in DIRS:
            land = None                                     # how far out the first floor at drop height lies
            for r in range(1, reach + 1):
                a, b = i + di * r, j + dj * r
                dist = r * pitch * math.hypot(di, dj)
                if dist > DROP_REACH + pitch * math.hypot(di, dj) + 1e-6 or not (0 <= a < s.nx and 0 <= b < s.nz):
                    break                                   # DROP_REACH in metres to the first floor, the walkable
                                                            # landing a cell past it at most (a curb's cramped foot)
                cc = b * s.nx + a
                rng = range(s.start[cc], s.start[cc + 1])
                if not any(y[m] <= yk + mohsoldier.STEP for m in rng):
                    break                                   # no floor under the way: inside solid geometry
                if cramped(rng):
                    break                                   # in a wall too tall for the sampled body range
                if land is None and any(DROP_MIN <= yk - y[m] <= DROP_MAX for m in rng):
                    land = (dist, max(y[m] for m in rng if DROP_MIN <= yk - y[m] <= DROP_MAX))
                hit = None
                for n in rng:
                    if (keep[n] and lab[n] != lab[k] and size[int(lab[n])] > size[int(lab[k])] and
                            DROP_MIN <= yk - y[n] <= DROP_MAX and land is not None and
                            land[0] <= DROP_REACH + 1e-6 and land[0] <= run_reach(yk - land[1], pitch)):
                        if not any(y[n] + 0.1 < y[m] < yk + head for m in rng):   # the landing is open
                            hit = n
                            break
                # a diagonal step passes the corner between two straight neighbours: a wall in either side column
                # blocks it (the soldier's 0.35 m radius does not pass a corner between 0.2 m columns)
                if di and dj and (wall(a - di, b) or wall(a, b - dj)):
                    break
                if hit is not None:
                    cands[(int(lab[k]), int(lab[hit]))].append((dist, k, hit))
                    break
                # the path out over this column: nothing from the ledge's step up to the soldier's height
                if any(yk + mohsoldier.STEP < y[m] < yk + head for m in rng):
                    break
    out = []
    for key, lst in cands.items():
        lst.sort()
        chosen = []
        for d, k, n in lst:
            ck = int(col[k])
            px, pz = ck % s.nx * pitch, ck // s.nx * pitch
            if all(math.hypot(px - qx, pz - qz) >= DROP_SPACING for qx, qz in chosen):
                chosen.append((px, pz))
                out.append((k, n))
                if len(chosen) >= DROP_PER_PAIR:
                    break
    STATS.update(regions=len(size), candidates=sum(len(v) for v in cands.values()), pairs=len(cands), links=len(out))
    return out
