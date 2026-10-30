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
  - a candidate: a kept surface k of any region but the largest and at least DROP_MIN_AREA, a column up to DROP_REACH
    away in one of 8 directions holding a kept surface n of a larger region DROP_MIN .. DROP_MAX below k, with no surface in between from k's
    height + mohsoldier.STEP up to k's height + the soldier's height (no wall at the ledge), and nothing above n in its
    own column below k's height + the soldier's height (the landing is open);
  - at most DROP_PER_PAIR links per pair of regions, DROP_SPACING apart, the shortest first.
"""
import math
from collections import defaultdict

import numpy as np

import mohsoldier

DROP_MIN = 0.5            # m: lower than this, a walk link (LINK_STEP) or the step rule joins the floors
DROP_MAX = 3.0            # m: the highest ledge a soldier drops from (7.7 m/s at the landing; MOH's data has no soldier
                          # fall-damage threshold to read - a conservative choice)
DROP_REACH = 2.0          # m: the landing at most this far out from the ledge's surface
DROP_PER_PAIR = 3
DROP_MIN_AREA = 4.0       # m2: an upper region smaller than this (a rock top, a crate) gets no drop link
DROP_SPACING = 4.0        # m
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
STATS = {}


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
    for _ in range(200):
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
    reach = max(1, int(math.ceil(DROP_REACH / pitch)))
    head = mohsoldier.HEIGHT
    cands = defaultdict(list)                       # (upper region, lower region) -> [(horizontal distance, k, n)]
    small = max(1, int(math.ceil(DROP_MIN_AREA / (pitch * pitch))))
    for k in ks[lab[ks] != largest].tolist():
        if size[int(lab[k])] < small:
            continue
        c = int(col[k])
        i, j = c % s.nx, c // s.nx
        yk = y[k]
        for di, dj in DIRS:
            for r in range(1, reach + 1):
                a, b = i + di * r, j + dj * r
                if not (0 <= a < s.nx and 0 <= b < s.nz):
                    break
                cc = b * s.nx + a
                rng = range(s.start[cc], s.start[cc + 1])
                hit = None
                for n in rng:
                    if (keep[n] and lab[n] != lab[k] and size[int(lab[n])] > size[int(lab[k])] and
                            DROP_MIN <= yk - y[n] <= DROP_MAX):
                        if not any(y[n] + 0.1 < y[m] < yk + head for m in rng):   # the landing is open
                            hit = n
                            break
                if hit is not None:
                    cands[(int(lab[k]), int(lab[hit]))].append((r * pitch * math.hypot(di, dj), k, hit))
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
