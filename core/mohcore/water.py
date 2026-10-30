"""MOH water: Terrains/<Terrain>/<Terrain>.Water.Mesh.res (type WaterMesh) and .Water.res (type VisualWater), in each
level's terrain-00.fbrb.

    w = read(folder_or_mesh_path, name)    # Water: vertices, triangles, per-triangle shader (or None)
    w.vertices (n, 3) float32, w.triangles (m, 3) uint32, w.shader[k] -> shader path of triangle k

WaterMesh (little-endian; read on all 26 MOH MP levels, the patch's terrain layer first, each file consumed exactly -
core\tests\test_water.py):

    u32           bodies (1 in every level)
    per body:     u32 vertex count, vertex count x (f32 x, y, z)
                  u32 index count, index count x u32 (triangles; 0 vertices = no water: 12 of the 26, a 12-byte file -
                  mp_01_elimination, mp_02, mp_02_koth2, mp_06_*, mp_07_koth, mp_09_*, mp_10_*)

Heights are per vertex (the format allows sloping rivers; BFBC2's SP_02 had one).

VisualWater (the drawing copy; only the parts used here):

    u32 10, u32 vertex stride (36), u32 vertex bytes, vertices (half-float x, y, z first, then normal and texture
    coordinates), u32 index bytes, u32 indices (identical to the mesh's on 33 of 34 levels with water),
    u32 slot count (4) and the slots' shader paths (0-terminated, each followed by one 0 byte, then
    Systems/Water/WaterOceanFragment), u32 range count and per range (u32 slot, u32 triangle count,
    u32 first index, u32 index count), then u32 0 and the byte 0x46. Where the mesh has no water its header has 0 vertex
    and 0 index bytes.
"""
import os
import struct
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class Water:
    vertices: np.ndarray                  # (n, 3) float32
    triangles: np.ndarray                 # (m, 3) uint32
    shader: Optional[List[str]] = None    # per triangle, from the VisualWater ranges; None if they do not match
    shaders: Optional[List[str]] = None   # the VisualWater slots

    def __len__(self):
        return len(self.triangles)


def read_mesh(path):
    """(vertices, triangles) of every body in a .Water.Mesh.res, bodies joined."""
    d = open(path, 'rb').read()
    bodies = struct.unpack_from('<I', d, 0)[0]
    o, verts, tris = 4, [], []
    base = 0
    for _ in range(bodies):
        n = struct.unpack_from('<I', d, o)[0]
        v = np.frombuffer(d, '<f4', n * 3, o + 4).reshape(n, 3)
        o += 4 + 12 * n
        m = struct.unpack_from('<I', d, o)[0]
        i = np.frombuffer(d, '<u4', m, o + 4).reshape(-1, 3)
        o += 4 + 4 * m
        verts.append(v)
        tris.append(i.astype(np.uint32) + base)
        base += n
    if o != len(d):
        raise ValueError(f'{path}: {len(d) - o} unread bytes - not the WaterMesh layout')
    return (np.concatenate(verts).astype(np.float32) if verts else np.zeros((0, 3), np.float32),
            np.concatenate(tris) if tris else np.zeros((0, 3), np.uint32))


def read_visual(path):
    """(indices, slot shader paths, ranges [(slot, triangles, first index, index count)]) of a .Water.res."""
    d = open(path, 'rb').read()
    _, stride, size = struct.unpack_from('<3I', d, 0)
    o = 12 + size
    ib = struct.unpack_from('<I', d, o)[0]
    indices = np.frombuffer(d, '<u4', ib // 4, o + 4)
    o += 4 + ib
    slots = struct.unpack_from('<I', d, o)[0]
    o += 4
    names = []
    for _ in range(slots + 1):                 # the slots, then the fragment shader
        e = d.index(b'\0', o)
        names.append(d[o:e].decode('ascii', 'replace'))
        o = e + 1
        if len(names) <= slots and d[o:o + 1] == b'\0':
            o += 1
    count = struct.unpack_from('<I', d, o)[0]
    ranges = [struct.unpack_from('<4I', d, o + 4 + 16 * k) for k in range(count)]
    return indices, names[:slots], ranges


def read(folder, name):
    """The water of terrain <name> under an unpacked terrain-00.fbrb (None if it has no water files)."""
    mesh = visual = None
    for root, _, files in os.walk(folder):
        for f in files:
            low = f.lower()
            if low == f'{name}.water.mesh.res':
                mesh = os.path.join(root, f)
            elif low == f'{name}.water.res':
                visual = os.path.join(root, f)
    if mesh is None:
        return None
    v, t = read_mesh(mesh)
    w = Water(v, t)
    if visual is not None and len(t):
        try:
            indices, slots, ranges = read_visual(visual)
            if np.array_equal(indices, t.ravel()):
                shader = [''] * len(t)
                for slot, ntri, first, nidx in ranges:
                    for k in range(first // 3, first // 3 + ntri):
                        if k < len(shader):
                            shader[k] = slots[slot] if slot < len(slots) else f'slot {slot}'
                w.shader, w.shaders = shader, slots
        except (struct.error, ValueError, IndexError):
            pass
    return w
