"""BC2 terrain height fields: Terrains/<Level>/<Level>.Heightfield-000.res (type TerrainHeightfield, in each
level's terrain-00.fbrb).

    hf = read(path)            # Heightfield: bounds, size, heights (size x size, metres)
    hf.height(x, z)            # world height, grid squares split as the game splits them
    vertices, triangles = hf.mesh(stride=2)

Layout (all little-endian; checked against the in-game terrain samples of 13 campaign levels, see DESIGN.md
"Terrain height field"):

    0x00  6 x f32      world bounds: min x, min y, min z, max x, max y, max z
    0x18  u32          size: samples per side (1024 or 2048)
    0x1C  u32 x 5      log2(size) - 3, log2(size) - 2, about 256 * size, 0, a count - not decoded
    0x30  u8           0 in every level seen
    0x31  u16 x size^2 heights, rows along z, columns along x: height = min y + value / 65535 * (max y - min y),
                       sample (i, j) at (min x + i * (max x - min x) / size, min z + j * (max z - min z) / size)
    then               a table of u32 offsets (0, 2, 4, ...) and further data, probably level of detail - not decoded
"""
import os
import struct
from dataclasses import dataclass

import numpy as np

HEADER = struct.Struct('<6f6IB')


@dataclass
class Heightfield:
    min: np.ndarray        # (x, y, z)
    max: np.ndarray
    size: int
    raw: np.ndarray        # (size, size) uint16 as stored
    heights: np.ndarray    # (size, size) float32 metres

    @property
    def cell(self):
        return (self.max[0] - self.min[0]) / self.size, (self.max[2] - self.min[2]) / self.size

    def height(self, x, z):
        """Heights at world (x, z), each grid square split along its (i, j)-(i+1, j+1) diagonal."""
        cx, cz = self.cell
        u = (np.asarray(x, float) - self.min[0]) / cx
        w = (np.asarray(z, float) - self.min[2]) / cz
        n = self.size
        i = np.clip(np.floor(u).astype(int), 0, n - 2)
        j = np.clip(np.floor(w).astype(int), 0, n - 2)
        fu, fw = u - i, w - j
        H = self.heights
        a, b, c, d = H[j, i], H[j, i + 1], H[j + 1, i], H[j + 1, i + 1]
        return np.where(fu > fw, a + (b - a) * fu + (d - b) * fw, a + (c - a) * fw + (d - c) * fu)

    def mesh(self, stride=2):
        """(vertices (n, 3) float32, triangles (m, 3) uint32) on every <stride>-th sample."""
        cx, cz = self.cell
        idx = np.arange(0, self.size, stride)
        h = self.heights[np.ix_(idx, idx)]
        X, Z = np.meshgrid(self.min[0] + idx * cx, self.min[2] + idx * cz)
        verts = np.stack([X.ravel(), h.ravel(), Z.ravel()], axis=1).astype(np.float32)
        n = len(idx)
        k = np.arange(n * n).reshape(n, n)
        a, b, c, d = k[:-1, :-1].ravel(), k[:-1, 1:].ravel(), k[1:, :-1].ravel(), k[1:, 1:].ravel()
        tris = np.concatenate([np.stack([a, c, d], 1), np.stack([a, d, b], 1)]).astype(np.uint32)
        return verts, tris


def read(path):
    data = open(path, 'rb').read()
    f = HEADER.unpack_from(data, 0)
    lo, hi, size = np.array(f[0:3]), np.array(f[3:6]), f[6]
    if size <= 0 or size & (size - 1) or HEADER.size + 2 * size * size > len(data):
        raise ValueError(f'{path}: not a TerrainHeightfield (size {size})')
    raw = np.frombuffer(data, '<u2', size * size, HEADER.size).reshape(size, size)
    heights = (lo[1] + raw * ((hi[1] - lo[1]) / 65535.0)).astype(np.float32)
    return Heightfield(lo, hi, size, raw, heights)


def find(folder, lv):
    """The level's .Heightfield-000.res under an unpacked terrain-00.fbrb, or None."""
    for root, _, files in os.walk(folder):
        for name in files:
            if name.lower() == f'{lv}.heightfield-000.res':
                return os.path.join(root, name)
    return None
