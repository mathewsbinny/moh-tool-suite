"""NavKit: reader and checks for collision samples taken in game by OPFOR's [dev] navsample option.

Usage:
  navsample.py info    <samples.bin>
  navsample.py compare <samples.bin> <pathdata.hpd> [out.png]   agreement with an existing pathdata.hpd

File format: see src/dll/navsample.cpp. Columns are j-major (z), then i (x); sample (i, j) is at
(minX + i * pitch, minZ + j * pitch), minX/minZ being whole multiples of the pitch (as in the original,
whose outlines run halfway between samples).
"""
import math
import os
import struct
import sys
from collections import deque
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hpdtool  # noqa: E402
import kymesh  # noqa: E402

HEADER = struct.Struct('<4sI3f2i6fI')
BLOCKED, FAULT = 1, 2


@dataclass
class Samples:
    pitch: float
    min_x: float
    min_z: float
    nx: int
    nz: int
    y_top: float
    y_bottom: float
    radius: float
    cap_bottom: float
    cap_top: float
    ray_radius: float
    start: np.ndarray    # per column: index of its first surface (length nx*nz + 1)
    y: np.ndarray        # per surface
    normal: np.ndarray   # per surface, (n, 3) floats
    flags: np.ndarray    # per surface
    hit: np.ndarray = None   # per surface (version 2): shape vtable, shape word +0x0C, collidable word +0x1C

    def centre(self, i, j):
        return self.min_x + i * self.pitch, self.min_z + j * self.pitch

    def column(self, i, j):
        c = j * self.nx + i
        return range(self.start[c], self.start[c + 1])


def load(path):
    data = open(path, 'rb').read()
    magic, version, pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_b, cap_t, ray_r, _ = \
        HEADER.unpack_from(data, 0)
    if magic != b'ONS1' or version not in (1, 2, 3):
        raise ValueError(f'{path}: not a navsample file')
    if version == 3:
        # records, then the column counts in one block, then { u64 offset of the counts, "CNT3" } - read at once
        if len(data) < HEADER.size + 12 or data[-4:] != b'CNT3':
            raise ValueError(f'{path}: sampling run not finished (no column counts)')
        at = struct.unpack_from('<Q', data, len(data) - 12)[0]
        counts = np.frombuffer(data, np.uint8, nx * nz, at).astype(np.int64)
        start = np.zeros(nx * nz + 1, dtype=np.int64)
        np.cumsum(counts, out=start[1:])
        rec = np.frombuffer(data, np.dtype([('y', '<f4'), ('n', 'i1', 3), ('f', 'u1'), ('h', '<u4', 3)]),
                            int(start[-1]), HEADER.size)
        return Samples(pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_b, cap_t, ray_r, start,
                       rec['y'].astype(np.float32), (rec['n'] / 127.0).astype(np.float32),
                       rec['f'].astype(np.uint8), rec['h'].astype(np.uint32)), nx * nz
    pos = HEADER.size
    cols = nx * nz
    start = np.zeros(cols + 1, dtype=np.int64)
    ys, normals, flags, hits = [], [], [], []
    rec = 8 if version == 1 else 20
    c = 0
    while c < cols and pos < len(data):
        n = data[pos]
        pos += 1
        for _ in range(n):
            y, a, b, d, f = struct.unpack_from('<f3bB', data, pos)
            if version == 2:
                hits.append(struct.unpack_from('<3I', data, pos + 8))
            pos += rec
            ys.append(y)
            normals.append((a / 127.0, b / 127.0, d / 127.0))
            flags.append(f)
        c += 1
        start[c] = len(ys)
    start[c:] = len(ys)   # an interrupted run leaves the rest empty
    return Samples(pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_b, cap_t, ray_r, start,
                   np.array(ys, dtype=np.float32), np.array(normals, dtype=np.float32).reshape(-1, 3),
                   np.array(flags, dtype=np.uint8), np.array(hits, dtype=np.uint32).reshape(-1, 3)), c


def walkable(s, slope_max_deg=40.0):
    """Per surface: the soldier fits (no capsule overlap) and the ground is not too steep."""
    min_ny = math.cos(math.radians(slope_max_deg)) - 0.01
    return ((s.flags & (BLOCKED | FAULT)) == 0) & (s.normal[:, 1] >= min_ny)


def reference_cells(s, hpd_path):
    """Rasterises the areas of an existing pathdata.hpd onto the sample grid.
    Returns {column: [(min_y, max_y, coverage), ...]}. Outlines run on cell corners ((k + 0.5) * pitch)
    with axis-aligned and 45-degree segments; a 45-degree segment passes through a sample and cuts its
    cell in half. Coverage is measured at four points inside the cell, off both diagonals:
    1 = whole cell inside, 0.5 = cut by a diagonal, 0.25/0.75 = rarer cases. Layers of overlapping
    areas (sector margins) are merged, keeping the largest coverage."""
    f = hpdtool.parse(open(hpd_path, 'rb').read())
    cells = {}
    for sec in f.sectors:
        m = kymesh.parse_mesh(sec.mesh)
        for a in m.areas:
            # The boundary of an area is its outline polylines (open) closed by its portal segments
            # (B to neighbouring areas, C on the sector border).
            segs = [(line[k], line[k + 1]) for line in a.outline for k in range(len(line) - 1)]
            segs += [(b.p0, b.p1) for b in a.portals] + [(c.p0, c.p1) for c in a.border_portals]
            pts = [p for seg in segs for p in seg]
            if not pts:
                continue
            xs = [p[0] for p in pts]
            zs = [p[2] for p in pts]
            i0 = max(0, int(math.ceil((min(xs) - s.min_x) / s.pitch)))
            i1 = min(s.nx - 1, int(math.floor((max(xs) - s.min_x) / s.pitch)))
            j0 = max(0, int(math.ceil((min(zs) - s.min_z) / s.pitch)))
            j1 = min(s.nz - 1, int(math.floor((max(zs) - s.min_z) / s.pitch)))
            if i0 > i1 or j0 > j1:
                continue
            gi, gj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
            px = s.min_x + gi * s.pitch
            pz = s.min_z + gj * s.pitch
            count = np.zeros(px.shape, dtype=np.int8)
            for ox, oz in ((0.25, 0.1), (-0.25, -0.1), (0.1, -0.25), (-0.1, 0.25)):
                qx, qz = px + ox * s.pitch, pz + oz * s.pitch
                inside = np.zeros(px.shape, dtype=bool)
                for (x1, _, z1), (x2, _, z2) in segs:    # even-odd rule
                    if z1 == z2:
                        continue
                    cross = (qz >= min(z1, z2)) & (qz < max(z1, z2))
                    xi = x1 + (qz - z1) * (x2 - x1) / (z2 - z1)
                    inside ^= cross & (qx < xi)
                count += inside
            ys = [p[1] for p in pts]
            lo, hi = min(ys), max(ys)
            for i, j, n in zip(gi[count > 0], gj[count > 0], count[count > 0]):
                layers = cells.setdefault(int(j) * s.nx + int(i), [])
                cov = n / 4.0
                for k, (l2, h2, c2) in enumerate(layers):
                    if lo <= h2 + 0.6 and l2 <= hi + 0.6:      # same surface (overlapping sectors)
                        layers[k] = (min(lo, l2), max(hi, h2), max(cov, c2))
                        break
                else:
                    layers.append((lo, hi, cov))
    return cells


def flood(s, ok, seeds, max_dy):
    """Surfaces reachable from the seed surfaces over walkable neighbours (4-connected, |dy| <= max_dy)."""
    reach = np.zeros(len(s.y), dtype=bool)
    q = deque()
    for k in seeds:
        if ok[k] and not reach[k]:
            reach[k] = True
            q.append(k)
    col_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
    while q:
        k = q.popleft()
        c = col_of[k]
        i, j = c % s.nx, c // s.nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if not (0 <= ni < s.nx and 0 <= nj < s.nz):
                continue
            for n in s.column(ni, nj):
                if ok[n] and not reach[n] and abs(float(s.y[n]) - float(s.y[k])) <= max_dy:
                    reach[n] = True
                    q.append(n)
    return reach


def compare(s, hpd_path, png=None, slope=40.0, step=0.28, tol=0.6):
    ref = reference_cells(s, hpd_path)
    ok = walkable(s, slope)
    tp = fn = fp_raw = 0
    part_yes = part_no = 0
    matched = np.zeros(len(s.y), dtype=bool)
    status = np.zeros((s.nz, s.nx), dtype=np.uint8)   # 0 none, 1 both, 2 only reference, 3 only ours, 4 cut cell
    for c in range(s.nx * s.nz):
        surf = range(s.start[c], s.start[c + 1])
        layers = ref.get(c, [])
        j, i = divmod(c, s.nx)
        for lo, hi, cov in layers:
            hit = [k for k in surf if ok[k] and lo - tol <= s.y[k] <= hi + tol]
            matched[hit] = True
            if cov < 1:        # cut by a diagonal: either answer is consistent with the reference
                part_yes += bool(hit)
                part_no += not hit
                status[j, i] = max(status[j, i], 4)
            elif hit:
                tp += 1
                status[j, i] = 1
            else:
                fn += 1
                status[j, i] = 2
    # Our walkable surfaces connected to the matched ones; the rest is unreachable (the original drops it).
    max_dy = max(step, s.pitch * math.tan(math.radians(slope))) + 0.05
    reach = flood(s, ok, np.nonzero(matched)[0], max_dy)
    fp = reach & ~matched
    fp_raw = int((ok & ~matched).sum())
    for k in np.nonzero(fp)[0]:
        c = int(np.searchsorted(s.start, k, side='right') - 1)
        j, i = divmod(c, s.nx)
        if status[j, i] == 0:
            status[j, i] = 3
    total_ref = tp + fn
    print(f'reference cells (layers) fully inside: {total_ref}; cut by a diagonal: {part_yes + part_no} '
          f'(we mark {part_yes} walkable)')
    print(f'  found walkable by sampling: {tp} ({tp / max(total_ref, 1) * 100:.1f}%)')
    print(f'  missed:                     {fn} ({fn / max(total_ref, 1) * 100:.1f}%)')
    print(f'extra walkable, connected to the reference: {int(fp.sum())} '
          f'({fp.sum() / max(total_ref, 1) * 100:.1f}% of reference); unconnected walkable surfaces: '
          f'{fp_raw - int(fp.sum())}')
    if png:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap
        cmap = ListedColormap(['#202020', '#3cb44b', '#e6194b', '#4363d8', '#7a9a7a'])
        h = s.pitch / 2
        ext = (s.min_x - h, s.min_x + s.nx * s.pitch - h, s.min_z - h, s.min_z + s.nz * s.pitch - h)
        fig, ax = plt.subplots(figsize=(12, 12 * s.nz / max(s.nx, 1)))
        ax.imshow(status, origin='lower', extent=ext, cmap=cmap, vmin=0, vmax=4, interpolation='nearest')
        ax.set_title('green = both, red = only reference, blue = only NavKit (connected), grey-green = cell cut by a diagonal')
        fig.savefig(png, dpi=110, bbox_inches='tight')
        print('wrote', png)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    s, cols = load(argv[2])
    if argv[1] == 'info':
        n = np.diff(s.start)
        ok = walkable(s)
        print(f'{s.nx} x {s.nz} columns at {s.pitch} m from ({s.min_x}, {s.min_z}); {cols} sampled')
        print(f'surfaces {len(s.y)}, per column max {n.max() if len(n) else 0}, '
              f'blocked {int((s.flags & BLOCKED).astype(bool).sum())}, faults {int((s.flags & FAULT).astype(bool).sum())}, '
              f'walkable {int(ok.sum())}')
        print(f'agent capsule r {s.radius} from {s.cap_bottom} to {s.cap_top} m, ray radius {s.ray_radius}')
    elif argv[1] == 'compare':
        compare(s, argv[3], argv[4] if len(argv) > 4 else None)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('navsample', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
