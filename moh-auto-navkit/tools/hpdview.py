"""hpdview.py - draw one sector of a pathdata.hpd top-down (x right, z up) to a PNG.

Usage: python hpdview.py <pathdata.hpd> <sector index | 0xID> <out.png>

Optional 4th/5th arguments: minX maxX minZ maxZ zoom window.

Colours: grey = tile grid, black = E lines, purple = D lines, cyan = F cells (arrow = row direction),
green = B segments, red = C segments with a horizontal direction (arrow), magenta = C segments with
direction "up", orange = graph nodes, light orange = graph edges.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import hpdtool

u32 = lambda m, o: struct.unpack_from('<I', m, o)[0]
f32 = lambda m, o: struct.unpack_from('<f', m, o)[0]


def segs_from_lists(m, o, count_off, table_off):
    """D/E lists: index table of count+1 offsets (relative to the object), data = 3-D points."""
    cnt = u32(m, o + count_off)
    out = []
    if not cnt:
        return out
    tab = o + u32(m, o + table_off)
    idx = [u32(m, tab + j * 4) for j in range(cnt + 1)]
    for j in range(cnt):
        a, b = o + idx[j], o + idx[j + 1]
        pts = [struct.unpack_from('<3f', m, q) for q in range(a, b - 11, 12)]
        out.append(pts)
    return out


def main():
    path, which, out = sys.argv[1], sys.argv[2], sys.argv[3]
    f = hpdtool.parse(open(path, 'rb').read())
    if which.startswith('0x'):
        s = next(x for x in f.sectors if x.id == int(which, 16))
    else:
        s = f.sectors[int(which)]
    m = s.mesh
    _, tiles, objects, _, _ = hpdtool.mesh_layout(m)
    fig, ax = plt.subplots(figsize=(12, 12))
    x0, x1, z0, z1 = (f32(m, 0x28 + i * 4) for i in range(4))
    nx, nz = u32(m, 0x38), u32(m, 0x3C)
    for i in range(nx + 1):
        x = x0 + (x1 - x0) * i / nx
        ax.plot([x, x], [z0, z1], color='0.85', lw=0.8)
    for i in range(nz + 1):
        z = z0 + (z1 - z0) * i / nz
        ax.plot([x0, x1], [z, z], color='0.85', lw=0.8)
    for o in objects:
        for pts in segs_from_lists(m, o, 0x40, 0x44):
            ax.plot([p[0] for p in pts], [p[2] for p in pts], color='black', lw=0.6)
        for pts in segs_from_lists(m, o, 0x38, 0x3C):
            ax.plot([p[0] for p in pts], [p[2] for p in pts], color='tab:purple', lw=1.6)
        fc = u32(m, o + 0x48)
        if fc:
            tab = o + u32(m, o + 0x4C)
            for j in range(fc):
                a, b = o + u32(m, tab + j * 16), o + u32(m, tab + (j + 1) * 16)
                pts = [struct.unpack_from('<3f', m, q) for q in range(a, b - 11, 12)]
                ax.fill([p[0] for p in pts], [p[2] for p in pts], color='tab:cyan', alpha=0.6, lw=0)
                d = struct.unpack_from('<3f', m, tab + j * 16 + 4)
                cx = sum(p[0] for p in pts) / len(pts); cz = sum(p[2] for p in pts) / len(pts)
                ax.arrow(cx, cz, d[0] * 0.6, d[2] * 0.6, head_width=0.2, color='teal', lw=0.5)
        nb = u32(m, o + 0x28)
        pos = o + 0x50
        for k in range(nb):
            r = pos + k * 0x20
            ax.plot([f32(m, r), f32(m, r + 12)], [f32(m, r + 8), f32(m, r + 20)], color='tab:green', lw=2)
        pos += nb * 0x20
        for k in range(u32(m, o + 0x30)):
            r = pos + k * 0x2C
            up = f32(m, r + 0x24) > 0.5
            ax.plot([f32(m, r), f32(m, r + 12)], [f32(m, r + 8), f32(m, r + 20)],
                    color='tab:red' if not up else 'magenta', lw=2)
            if not up:
                mx, mz = (f32(m, r) + f32(m, r + 12)) / 2, (f32(m, r + 8) + f32(m, r + 20)) / 2
                ax.arrow(mx, mz, f32(m, r + 0x20) * 0.8, f32(m, r + 0x28) * 0.8, head_width=0.25, color='tab:red', lw=0.5)
    for e in s.edges:
        a, b = s.nodes[e.src].pos, s.nodes[e.dst].pos
        ax.plot([a[0], b[0]], [a[2], b[2]], color='moccasin', lw=0.8, zorder=1)
    ax.scatter([n.pos[0] for n in s.nodes], [n.pos[2] for n in s.nodes], s=6, color='tab:orange', zorder=3)
    if len(sys.argv) >= 8:
        ax.set_xlim(float(sys.argv[4]), float(sys.argv[5]))
        ax.set_ylim(float(sys.argv[6]), float(sys.argv[7]))
    ax.set_aspect('equal')
    ax.set_title(f'sector {s.id:#06x}: {len(objects)} areas, {len(s.nodes)} nodes, {len(s.edges)} edges')
    fig.savefig(out, dpi=110, bbox_inches='tight')
    print(f'wrote {out}')


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('hpdview', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main())
