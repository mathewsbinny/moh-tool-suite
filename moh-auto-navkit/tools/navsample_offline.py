"""NavKit: offline collision sampler - the in-game sampler's output (OPFOR's [dev] navsample, src\\dll\\navsample.cpp)
computed from the level's own data, without starting the game: the terrain height field plus the Havok collision of
every placed object and static-group member, as the game's physics world holds them.

Usage:
  navsample_offline.py sample  <level> [--pitch 0.8|0.2] [--box minX,minZ,maxX,maxZ] [--y bottom,top] [--bodies he]
                               both capsule runs (normal and high) of one pitch; at 0.2 over the level's detailed sectors
                               (as navkit.py sample does). Writes <work>\\navsample_offline\\<level>_navkit[_hi][_p02].bin
  navsample_offline.py like    <level> <in-game samples.bin> [--bodies he]
                               the same grid as an in-game file (for validation): <level>_like_<file name>
  navsample_offline.py validate <level> <in-game.bin> ...   'like' for several files (geometry read once), measures
                               saved as <level>_validate.json
  navsample_offline.py compare <in-game.bin> <offline.bin>   per column: heights, layer counts, walkable mask
  navsample_offline.py parity  <level> ... [--name offline]   offline samples on the in-game grids, NavKit built
                               from both, navdiff (offline build against the in-game build) and navparity (both
                               against DICE); work\\navkit_offline\\parity_<name>.json
  navsample_offline.py build   <level>                  NavKit build from the offline samples (navkit.py build's inputs)
                               into <work>\\navkit_offline\\<level>\\pathdata.hpd
  navsample_offline.py check   <level>                  hpdcheck + roundtrip + ordercheck on that build
  navsample_offline.py all     <level>                  prepare (offline; a map without path data: the box from
                               its combat areas) + sample 0.8 + 0.2 + build + check (stockmp on one level)
  navsample_offline.py stockmp [level ...] [--steps prepare,sample,build,check]
                               the stock MP maps (STOCK_MP), one after another; work\\navkit_offline\\stockmp.json

What an in-game sample is (navsample.cpp), and how it is reproduced here:
  * a sphere cast (radius 0.05) straight down from yTop; after a hit at y the next cast starts at y - 0.1; a cast that
    hits within 0.07 of its start (it started inside geometry) is not recorded - the start steps down 0.5 m, at most
    10 m in a row. Here: the ray's hits per column are found by rasterising every collision triangle onto the grid
    (vertical line through the column centre against the triangle), then that loop is replayed on the sorted hits.
    The height field is solid: a cast starting under it hits at once, so nothing under the terrain is ever recorded.
    Convex shapes are solid too (a start inside one hits at once); triangle meshes (MOPP) are not.
  * mesh triangles are hit from above only (one-sided: in-game samples never have a downward normal); convex shapes
    and boxes by their upper faces.
  * each recorded surface gets a capsule test: radius 0.4, from stepMax (0.28; the high run 0.6) to 1.9 m above it.
    Here: for every triangle near the column, the height interval of the vertical line inside (triangle + 0.4 m ball)
    is computed exactly (face slab, edge cylinders, vertex spheres); a surface is blocked when its capsule axis
    (y + stepMax + 0.4 .. y + 1.9 - 0.4) meets one. The height field: its height under the capsule's lower
    hemisphere, sampled on rings (0, 0.1 .. 0.4 m, 16 directions).
  * hit info (the record's three words): the shape vtables the in-game file has - terrain TERRAIN_SHAPE_VTABLE,
    mesh MESH_SHAPE_VTABLE, convex / box CONVEX_SHAPE_VTABLE - then RAY_SHAPE_VTABLE, then 0. No soldiers are in this
    world (navwalk treats them as absent anyway).

Which Havok bodies are in the world: every blueprint's *_Havok.res holds one or two physics systems of rigid bodies
named 'h', 'e' or 'c' (one letter). Measured against the in-game samples (README, "Standalone sampling"): the AI ray
and capsule see bodies named BODIES (default 'h' and 'c'); 'e' bodies are another layer.

Clean room: only the level's data files are read (Havok packfiles through hkpack.py, the height field through
core's terrain.py); no game code runs.
"""
import glob
import json
import math
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import hkpack  # noqa: E402
import navsample  # noqa: E402

# --- the in-game sampler's settings (navsample.cpp, navkit.py AGENTS) ----------------------------------------------
RAY_RADIUS = 0.05
SKIP = 0.1                      # gap below a hit where the next cast starts
SOLID_STEP, MAX_SOLID = 0.5, 10.0
AT_ONCE = RAY_RADIUS + 0.02     # a hit this close below the start: the cast started inside geometry
MAX_LAYERS = 32
RADIUS, HEIGHT = 0.4, 1.9
CAP_BOTTOMS = {'': 0.28, '_hi': 0.6}     # normal and high-capsule runs
# The capsule as the in-game flags show it: heights of its lowest and highest sphere centres above the surface, per
# run. navsample.cpp asks for one from stepMax to 1.9 m (sphere centres 0.68 .. 1.5 m; high run 1.0 .. 1.5), but its
# blocked flags fit a capsule whose lowest centre is higher: fitted on sp_004_b's 0.8 m runs (agreement of the blocked
# flag on matched surfaces, terrain / objects): normal 0.68 -> 98.9 / 94.3 %, 0.95 -> 99.3 / 96.6 %; high 1.0 -> 98.9 /
# 94.9 %, 1.15 -> 99.2 / 96.3 % (the upper end hardly matters: 1.3 .. 1.9 within 0.1 point). Steep terrain shows it
# plainly: in game the normal run's capsule is blocked from a slope of ~64 deg, the high run's from ~69 deg.
CAPSULE_AXIS = {0.28: (0.95, 1.5), 0.6: (1.15, 1.5)}

TERRAIN_SHAPE_VTABLE = 0x1454D14
MESH_SHAPE_VTABLE = 0x1483B38    # MOPP-tree object hits in the in-game files (44,298 of sp_004_b's 0.8 m samples)
CONVEX_SHAPE_VTABLE = 0x14864B0  # the rare third kind in the in-game files (39 on sp_004_b)
RAY_SHAPE_VTABLE = 0x14852B8     # second word of every in-game record (the cast's own body)

BODIES = 'hc'                    # rigid-body names whose collision the AI queries see (measured, see the docstring)
CONVEX_RADIUS = True             # convex shapes and boxes grown by their convex radius (Havok's 0.05 m shell)
TERRAIN_LIFT = True              # terrain hits RAY_RADIUS * (1 - ny) above the height field, as in game
CONVEX_IN_TREE_HOLLOW = False    # convex pieces in a MOPP tree (a list shape under it) are not solid: the cast hits
                                 # their faces from both sides, reporting the face's outward normal - the in-game files
                                 # have downward normals only there (ONS_MP_002: stacked faces 106.85 / -0.46,
                                 # 106.36 / -0.8 under a 108.26 top)
MESH_TWO_SIDED = True            # mesh triangles are hit from both sides, the normal facing the ray (measured)
INTACT_ONLY = True               # leave out mesh parts an object shows only once damaged (destroyed_parts)
CONVEX_GROW = 2 * RAY_RADIUS     # ... and past a convex piece's top face (the sphere plus the piece's 0.05 m shell)
EDGE_GROW = RAY_RADIUS           # columns this close outside a mesh triangle's edge still hit it (the cast's sphere)

KIND_TERRAIN, KIND_MESH, KIND_CONVEX = 0, 1, 2
VTABLE_OF_KIND = np.array([TERRAIN_SHAPE_VTABLE, MESH_SHAPE_VTABLE, CONVEX_SHAPE_VTABLE], dtype=np.uint32)


# --- Havok assets ----------------------------------------------------------------------------------------------------
# Havok 5.5 packfiles (32-bit). These files carry no class descriptions, so the offsets below were read off the data
# (objects found through the packfile's virtual fixups, hkpack.py):
#   hkpPhysicsSystem       +0x08 rigid bodies (array of pointers)
#   hkpRigidBody           +0x10 shape, +0x70 name (one letter: h / e / c), +0xE0 transform (3 rotation columns,
#                          translation; row-vector convention as the level's matrices: p @ M[:3, :3] + M[3, :3])
#   hkpMoppBvTreeShape     +0x34 child shape (hkpStorageExtendedMeshShape or hkpListShape)
#   hkpStorageExtendedMeshShape  +0xC0 mesh storages (array of pointers; every one holds triangles)
#   ...MeshSubpartStorage  +0x08 vertices (vec4), +0x14 16-bit indices (3 corners + padding per triangle)
#   hkpListShape           +0x18 children (16 bytes each, shape pointer first)
#   hkpConvexTranslateShape +0x18 child, +0x20 translation
#   hkpConvexVerticesShape +0x10 convex radius, +0x40 vertices (FourVectors: x4 y4 z4), +0x4C vertex count,
#                          +0x50 plane equations (n, d: n . v + d = 0, n outward)
#   hkpBoxShape            +0x10 convex radius, +0x20 half extents
def _arr(pf, at):
    p = pf.deref(at)
    n = struct.unpack_from('<i', pf.d, at + 4)[0]
    return p, (n if p else 0)


def _hull_faces(verts, planes, radius):
    """Triangles of a convex hull given its vertices and outward plane equations: per plane, the vertices on it in
    order around it, fanned. Grown by radius along each face normal (CONVEX_RADIUS)."""
    tris = []
    scale = max(1.0, float(np.abs(verts).max()))
    for n in planes:
        nn = n[:3]
        ln = np.linalg.norm(nn)
        if ln < 1e-6:
            continue
        nn = nn / ln
        d = n[3] / ln
        on = verts[np.abs(verts @ nn + d) < 2e-3 * scale]
        if len(on) < 3:
            continue
        on = np.unique(np.round(on, 5), axis=0)
        if len(on) < 3:
            continue
        c = on.mean(0)
        u = np.cross(nn, [0, 1, 0] if abs(nn[1]) < 0.9 else [1, 0, 0])
        u /= np.linalg.norm(u)
        v = np.cross(nn, u)
        ang = np.arctan2((on - c) @ v, (on - c) @ u)
        on = on[np.argsort(ang)]
        off = nn * radius if CONVEX_RADIUS else 0
        for k in range(1, len(on) - 1):
            t = np.array([on[0], on[k], on[k + 1]]) + off
            if np.dot(np.cross(t[1] - t[0], t[2] - t[0]), nn) < 0:
                t = t[[0, 2, 1]]
            tris.append(t)
    return np.array(tris, dtype=np.float64).reshape(-1, 3, 3)


def _box_faces(h, radius):
    h = np.asarray(h[:3], float) + (radius if CONVEX_RADIUS else 0)
    c = np.array([[x, y, z] for x in (-h[0], h[0]) for y in (-h[1], h[1]) for z in (-h[2], h[2])])
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
             (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    t = c[np.array(faces)]
    # outward winding
    nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    flip = (nrm * t.mean(1)).sum(1) < 0
    t[flip] = t[flip][:, [0, 2, 1]]
    return t


def _shape_parts(pf, sh, offset, out, depth=0):
    """Appends (kind, local triangles) of a shape tree to out; offset: translation of convex-translate parents."""
    if not sh or depth > 8:
        return
    cls = pf.by_offset.get(sh)
    d = pf.d
    if cls == 'hkpMoppBvTreeShape':
        _shape_parts(pf, pf.deref(sh + 0x34), offset, out, depth + 1)
    elif cls == 'hkpStorageExtendedMeshShape':
        arr, n = _arr(pf, sh + 0xC0)
        sp, nsub = _arr(pf, sh + 0x50)
        for k in range(n):
            # the triangle subpart record (0x40 bytes) of storage k: +0x20 its part (the asset's PartInfoDatas index)
            part = struct.unpack_from('<i', d, sp + 0x40 * k + 0x20)[0] if sp and k < nsub else -1
            st = pf.deref(arr + 4 * k)
            if not st:
                continue
            p, nv = _arr(pf, st + 8)
            ip, ni = _arr(pf, st + 0x14)
            if not (p and nv > 0 and ip and ni >= 4):
                continue
            v = np.frombuffer(d, '<f4', nv * 4, p).reshape(-1, 4)[:, :3].astype(np.float64)
            idx = np.frombuffer(d, '<u2', ni - ni % 4, ip).reshape(-1, 4)[:, :3]
            if idx.max() >= nv:
                continue
            out.append((KIND_MESH, v[idx] + offset, part))
    elif cls == 'hkpListShape':
        arr, n = _arr(pf, sh + 0x18)
        for k in range(n):
            _shape_parts(pf, pf.deref(arr + 16 * k), offset, out, depth + 1)
    elif cls == 'hkpConvexTranslateShape':
        t = np.array(struct.unpack_from('<3f', d, sh + 0x20))
        _shape_parts(pf, pf.deref(sh + 0x18), offset + t, out, depth + 1)
    elif cls == 'hkpConvexVerticesShape':
        radius = struct.unpack_from('<f', d, sh + 0x10)[0]
        vp, nb = _arr(pf, sh + 0x40)
        nverts = struct.unpack_from('<i', d, sh + 0x4C)[0]
        pp, npl = _arr(pf, sh + 0x50)
        if not (vp and nb > 0 and pp and npl > 0) or nb > 100000:
            return
        fv = np.frombuffer(d, '<f4', nb * 12, vp).reshape(nb, 3, 4)
        verts = np.stack([fv[:, 0, :].ravel(), fv[:, 1, :].ravel(), fv[:, 2, :].ravel()], 1).astype(np.float64)
        verts = verts[:max(1, min(nverts, len(verts)))]
        planes = np.frombuffer(d, '<f4', npl * 4, pp).reshape(npl, 4).astype(np.float64)
        t = _hull_faces(verts, planes, radius)
        if len(t):
            out.append((KIND_CONVEX, t + offset, -1))
    elif cls == 'hkpBoxShape':
        radius = struct.unpack_from('<f', d, sh + 0x10)[0]
        h = struct.unpack_from('<3f', d, sh + 0x20)
        out.append((KIND_CONVEX, _box_faces(h, radius) + offset, -1))


def asset_bodies(path):
    """[(system index, body name, kind, triangles in the asset's space, part)] of a *_Havok.res (body transforms
    applied; part: the index into the asset's PartInfoDatas of a mesh piece, -1 for convex pieces; the last item: the
    kind of the body's root shape - what the in-game file's hit vtable names: a MOPP tree over convex pieces is a
    'mesh' hit there)."""
    try:
        pf = hkpack.load(path)
    except (ValueError, OSError, struct.error):
        return []
    out = []
    for si, sy in enumerate(pf.objects_of('hkpPhysicsSystem')):
        arr, n = _arr(pf, sy + 8)
        for k in range(n):
            b = pf.deref(arr + 4 * k)
            if not b or pf.by_offset.get(b) != 'hkpRigidBody':
                continue
            np_ = pf.deref(b + 0x70)
            name = pf.cstr(np_) if np_ else ''
            m = np.frombuffer(pf.d, '<f4', 16, b + 0xE0).reshape(4, 4).astype(np.float64)
            parts = []
            try:
                _shape_parts(pf, pf.deref(b + 0x10), np.zeros(3), parts)
            except (struct.error, ValueError, IndexError):
                continue
            root = pf.by_offset.get(pf.deref(b + 0x10))
            rk = KIND_MESH if root in ('hkpMoppBvTreeShape', 'hkpStorageExtendedMeshShape') else KIND_CONVEX
            for kind, t, part in parts:
                out.append((si, name, kind, t @ m[:3, :3] + m[3, :3], part, rk))
    return out


class Geometry:
    """World-space collision triangles of a level (not the terrain): tris (n, 3, 3) float64, kind (n,) uint8
    (KIND_MESH / KIND_CONVEX), solid (n,) int32 (convex: an id per placed convex piece, else -1), body (n,) uint8 (the
    body name's letter), inst (n,) int32 (index into names: the placed object's blueprint), off (n,) bool (a part
    that is not there while the object is intact: its destroyed state), vt (n,) uint8 (the kind of the body's root
    shape, for the hit vtable)."""

    def __init__(self, tris, kind, solid, body, inst, names, off=None, vt=None):
        self.tris, self.kind, self.solid, self.body, self.inst, self.names = tris, kind, solid, body, inst, names
        self.off = off if off is not None else np.zeros(len(tris), bool)
        self.vt = vt if vt is not None else kind.copy()

    def select(self, mask):
        return Geometry(self.tris[mask], self.kind[mask], self.solid[mask], self.body[mask], self.inst[mask], self.names,
                        self.off[mask], self.vt[mask])

    def bodies(self, letters):
        return self.select(np.isin(self.body, np.frombuffer(letters.encode(), np.uint8)))

    def intact(self):
        return self.select(~self.off)


def _xml_text(path):
    try:
        return open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return ''


def part_names(havok_res):
    """The asset's PartInfoDatas names, in order (from the converted <name>_Havok.xml next to the .res)."""
    t = _xml_text(havok_res[:-4] + '.xml')
    if '<array name="PartInfoDatas">' not in t:
        return []
    body = t.split('<array name="PartInfoDatas">', 1)[1].split('</array>', 1)[0]
    import re
    return [m.lower() for m in re.findall(r'<complex>\s*<field name="Name">([^<]*)</field>', body)]


def destroyed_parts(blueprint_xml):
    """Mesh parts a blueprint shows only once damaged: the MeshNames of its PartComponentData health states other than
    each component's full-health state (the highest Health), minus the full-health ones. (House_06_8x8: 'ruin_downed'
    - the rubble - is the 0-health state of the 'ruin' component.)"""
    import re
    t = _xml_text(blueprint_xml)
    if 'PartComponentData' not in t:
        return set()
    states = {}
    for g, body in re.findall(r'<instance guid="([^"]+)" type="GameSharedResources.HealthStateData">(.*?)</instance>', t, re.S):
        mn = re.search(r'<field name="MeshName">([^<]*)</field>', body)
        hp = re.search(r'<field name="Health">([-0-9.eE]+)</field>', body)
        states[g.lower()] = ((mn.group(1) if mn else '').lower(), float(hp.group(1)) if hp else 0.0)
    shown, later = set(), set()
    for body in re.findall(r'type="GameSharedResources.PartComponentData">(.*?)</instance>', t, re.S):
        hs = re.search(r'<array name="HealthStates">(.*?)</array>', body, re.S)
        refs = re.findall(r'<item ref="([^"]+)"', hs.group(1)) if hs else []
        st = [states[r.lower()] for r in refs if r.lower() in states]
        if not st:
            continue
        top = max(st, key=lambda s: s[1])
        if top[0]:
            shown.add(top[0])
        later.update(s[0] for s in st if s is not top and s[0])
    return later - shown


def level_geometry(level_dir, extra_bases=(), log=print, box=None):
    """The collision of a prepared level (work\\levels\\<level>: objects.json, group_instances.json from
    levelobjects.py, the unpacked bundles for the blueprints' *_Havok.res). box (minX, minZ, maxX, maxZ): only
    pieces reaching into it (plus 2 m)."""
    import levelobjects
    bases = [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + list(extra_bases)
    havok = levelobjects.havok_index(bases)
    try:
        for bp, target in levelobjects.mesh_aliases(bases).items():
            if bp not in havok and target in havok:
                havok[bp] = havok[target]
    except OSError:
        pass
    objs = json.load(open(os.path.join(level_dir, 'objects.json')))
    groups = json.load(open(os.path.join(level_dir, 'group_instances.json')))
    cache = {}
    T, K, S, B, I, OFF, V = [], [], [], [], [], [], []
    names = []
    name_id = {}
    missing = set()
    solid_id = 0
    for o in objs + groups:
        bp = o['bp']
        if bp not in cache:
            cache[bp] = asset_bodies(havok[bp]) if bp in havok else []
            if bp not in havok:
                missing.add(bp)
            elif cache[bp]:
                # parts shown only once the object is damaged (its rubble): marked, left out by intact()
                later = set()
                for base in bases:
                    if os.path.isfile(os.path.join(base, bp + '.xml')):
                        later = destroyed_parts(os.path.join(base, bp + '.xml'))
                        break
                names_ = part_names(havok[bp]) if later else []
                gone = {k for k, nm in enumerate(names_) if nm in later}
                cache[bp] = [(si, nm, kind, tt, part in gone, rk) for si, nm, kind, tt, part, rk in cache[bp]]
        if not cache[bp]:
            continue
        if len(cache[bp][0]) == 6 and not isinstance(cache[bp][0][4], bool):
            cache[bp] = [(si, nm, kind, tt, False, rk) for si, nm, kind, tt, _p, rk in cache[bp]]
        m = np.array(o['m'], dtype=np.float64).reshape(4, 4)
        if box is not None:
            p = m[3, :3]
            if not (box[0] - 300 <= p[0] <= box[2] + 300 and box[1] - 300 <= p[2] <= box[3] + 300):
                continue
        iid = name_id.setdefault(bp, len(names))
        if iid == len(names):
            names.append(bp)
        for _si, name, kind, t, gone, rk in cache[bp]:
            w = t @ m[:3, :3] + m[3, :3]
            if box is not None:
                keep = ((w[:, :, 0].max(1) >= box[0] - 2) & (w[:, :, 0].min(1) <= box[2] + 2) &
                        (w[:, :, 2].max(1) >= box[1] - 2) & (w[:, :, 2].min(1) <= box[3] + 2))
                w = w[keep]
                if not len(w):
                    continue
            T.append(w)
            K.append(np.full(len(w), kind, np.uint8))
            if kind == KIND_CONVEX:
                S.append(np.full(len(w), solid_id, np.int32))
                solid_id += 1
            else:
                S.append(np.full(len(w), -1, np.int32))
            B.append(np.full(len(w), ord(name[:1] or '?'), np.uint8))
            I.append(np.full(len(w), iid, np.int32))
            OFF.append(np.full(len(w), bool(gone)))
            V.append(np.full(len(w), rk, np.uint8))
    if not T:
        return Geometry(np.zeros((0, 3, 3)), np.zeros(0, np.uint8), np.zeros(0, np.int32), np.zeros(0, np.uint8),
                        np.zeros(0, np.int32), names)
    g = Geometry(np.concatenate(T), np.concatenate(K), np.concatenate(S), np.concatenate(B), np.concatenate(I), names,
                 np.concatenate(OFF), np.concatenate(V))
    log(f'  geometry: {len(objs)} objects, {len(groups)} group members, {len(g.tris):,} triangles '
        f'({int((g.kind == KIND_CONVEX).sum()):,} convex, {int(g.off.sum()):,} in destroyed-state parts); bodies '
        + ', '.join(f'{chr(c)} {int((g.body == c).sum()):,}' for c in np.unique(g.body))
        + f'; {len(missing)} blueprints without a collision asset')
    return g


# --- the grid --------------------------------------------------------------------------------------------------------
class Grid:
    def __init__(self, pitch, min_x, min_z, nx, nz, y_bottom, y_top, mask=None):
        self.pitch, self.min_x, self.min_z, self.nx, self.nz = float(pitch), float(min_x), float(min_z), int(nx), int(nz)
        self.y_bottom, self.y_top = float(y_bottom), float(y_top)
        self.mask = mask          # bool (nz, nx): columns to sample (None: all)

    @staticmethod
    def from_box(pitch, box, y, rects=None):
        """As navsample.cpp's Begin(): aligned to whole multiples of the pitch."""
        p = np.float32(pitch)
        min_x = np.float32(math.floor(np.float32(box[0]) / p)) * p
        min_z = np.float32(math.floor(np.float32(box[1]) / p)) * p
        nx = int(math.ceil((np.float32(box[2]) - min_x) / p)) + 1
        nz = int(math.ceil((np.float32(box[3]) - min_z) / p)) + 1
        g = Grid(p, min_x, min_z, nx, nz, y[0], y[1])
        if rects:
            xs = g.min_x + np.arange(nx) * g.pitch
            zs = g.min_z + np.arange(nz) * g.pitch
            # navsample.cpp's mask: a 1 m grid from the box's min; a column counts when its 1 m cell is in a rectangle
            w, h = int(math.ceil(box[2] - g.min_x)) + 2, int(math.ceil(box[3] - g.min_z)) + 2
            m1 = np.zeros((h, w), bool)
            for r in rects:
                i0, j0 = max(0, int(math.floor(r[0] - g.min_x))), max(0, int(math.floor(r[1] - g.min_z)))
                i1, j1 = min(w - 1, int(math.ceil(r[2] - g.min_x))), min(h - 1, int(math.ceil(r[3] - g.min_z)))
                m1[j0:j1 + 1, i0:i1 + 1] = True
            ii = np.floor(xs - g.min_x).astype(int)
            jj = np.floor(zs - g.min_z).astype(int)
            okx = (ii >= 0) & (ii < w)
            okz = (jj >= 0) & (jj < h)
            mask = np.zeros((nz, nx), bool)
            mask[np.ix_(okz, okx)] = m1[np.ix_(jj[okz], ii[okx])]
            g.mask = mask
        return g

    @staticmethod
    def like(s):
        """The grid of an in-game sample file (navsample.Samples); columns it left empty under navsample_rects stay
        sampled here (compare() looks at the columns both have)."""
        return Grid(s.pitch, s.min_x, s.min_z, s.nx, s.nz, s.y_bottom, s.y_top)

    @property
    def ncols(self):
        return self.nx * self.nz

    def xz(self, cols):
        return (self.min_x + (cols % self.nx) * self.pitch, self.min_z + (cols // self.nx) * self.pitch)


# --- rasterising triangles onto columns ------------------------------------------------------------------------------
def _ranges(lo, hi, origin, pitch, n):
    a = np.ceil((lo - origin) / pitch - 1e-9).astype(np.int64)
    b = np.floor((hi - origin) / pitch + 1e-9).astype(np.int64)
    return np.maximum(a, 0), np.minimum(b, n - 1)


def _pairs(i0, i1, j0, j1, budget=12_000_000):
    """Yields (triangle index, i, j) for every column in each triangle's index rectangle, in chunks."""
    ni = np.maximum(i1 - i0 + 1, 0)
    nj = np.maximum(j1 - j0 + 1, 0)
    cnt = ni * nj
    idx = np.nonzero(cnt)[0]
    if not len(idx):
        return
    cs = np.cumsum(cnt[idx])
    start = 0
    while start < len(idx):
        base = cs[start - 1] if start else 0
        end = int(np.searchsorted(cs, base + budget, 'right'))
        end = max(end, start + 1)
        sel = idx[start:end]
        c = cnt[sel]
        t = np.repeat(sel, c)
        lin = np.arange(int(c.sum()), dtype=np.int64) - np.repeat(np.cumsum(c) - c, c)
        yield t, i0[t] + lin % ni[t], j0[t] + lin // ni[t]
        start = end


def raster_hits(grid, tris, grow=EDGE_GROW, up_only=True, touch=False):
    """Where the cast meets each triangle in each column: (column, y, triangle index, normal[, touch]).
    The vertical line through the column centre passing through a (non-vertical) triangle hits it at its plane height
    (NaN normal row: the triangle's own). grow > 0: the cast's sphere (radius grow) also touches triangles up to grow
    away - an edge, a vertex, the top of a thin vertical sheet: the hit is where the sphere first touches, at the top
    of its touch interval (the heights of the line within grow of the triangle) less the sphere's reach there, with
    the normal from the touched point to the sphere's centre. up_only: only triangles facing up (one-sided rays).
    touch: also return (column, lo, hi) - every touch interval (the sphere overlaps the triangle while its centre is in
    it: a cast starting there hits at once)."""
    empty = np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64), np.zeros((0, 3))
    if not len(tris):
        return empty + ((np.zeros(0, np.int64), np.zeros(0), np.zeros(0)),) if touch else empty
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    nrm = np.cross(b - a, c - a)
    det = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
    flat = np.abs(det) > 1e-9                       # not vertical: the line can pass through it
    keep = (np.linalg.norm(nrm, axis=1) > 1e-12) & (flat | (grow > 0))
    if up_only:
        keep &= nrm[:, 1] > 0
    tid = np.nonzero(keep)[0]
    xs, zs = tris[tid, :, 0], tris[tid, :, 2]
    i0, i1 = _ranges(xs.min(1) - grow, xs.max(1) + grow, grid.min_x, grid.pitch, grid.nx)
    j0, j1 = _ranges(zs.min(1) - grow, zs.max(1) + grow, grid.min_z, grid.pitch, grid.nz)
    C, Y, TI, N, TC, TL, TH = [], [], [], [], [], [], []
    for t_, i, j in _pairs(i0, i1, j0, j1):
        T = tid[t_]
        if grid.mask is not None:
            m = grid.mask[j, i]
            T, i, j = T[m], i[m], j[m]
        px = grid.min_x + i * grid.pitch
        pz = grid.min_z + j * grid.pitch
        col = j * grid.nx + i
        ax, az, bx, bz, cx, cz = a[T, 0], a[T, 2], b[T, 0], b[T, 2], c[T, 0], c[T, 2]
        fl = flat[T]
        dt = np.where(fl, det[T], 1.0)
        l1 = ((bz - cz) * (px - cx) + (cx - bx) * (pz - cz)) / dt
        l2 = ((cz - az) * (px - cx) + (ax - cx) * (pz - cz)) / dt
        l3 = 1 - l1 - l2
        inside = fl & (l1 >= -1e-7) & (l2 >= -1e-7) & (l3 >= -1e-7)
        y = l1 * a[T, 1] + l2 * b[T, 1] + l3 * c[T, 1]
        n = np.full((len(T), 3), np.nan)
        hit = inside.copy()
        if grow > 0:
            lo, hi = _line_tri_interval(tris[T], px, pz, grow)
            near = lo <= hi
            if touch:
                TC.append(col[near])
                TL.append(lo[near])
                TH.append(hi[near])
            k = np.nonzero(near & ~inside)[0]
            if len(k):
                d2, _yy, ex, ez = _edge_dist2(tris[T[k]], px[k], pz[k])
                reach = np.sqrt(np.maximum(grow * grow - d2, 0))
                y[k] = hi[k] - reach
                hit[k] = True
                n[k, 0] = (px[k] - ex) / grow
                n[k, 2] = (pz[k] - ez) / grow
                n[k, 1] = reach / grow
        C.append(col[hit])
        Y.append(y[hit])
        TI.append(T[hit])
        N.append(n[hit])
    if not C:
        out = empty
        tc = (np.zeros(0, np.int64), np.zeros(0), np.zeros(0))
    else:
        out = np.concatenate(C), np.concatenate(Y), np.concatenate(TI), np.concatenate(N)
        tc = ((np.concatenate(TC), np.concatenate(TL), np.concatenate(TH)) if TC else
              (np.zeros(0, np.int64), np.zeros(0), np.zeros(0)))
    return out + (tc,) if touch else out


def _edge_dist2(t, px, pz):
    """Horizontal squared distance from (px, pz) to the nearest edge of each triangle, the height there, and that
    point's x, z."""
    best = np.full(len(px), np.inf)
    yb = np.zeros(len(px))
    xb = np.zeros(len(px))
    zb = np.zeros(len(px))
    for u, v in ((0, 1), (1, 2), (2, 0)):
        ex, ez = t[:, v, 0] - t[:, u, 0], t[:, v, 2] - t[:, u, 2]
        ll = ex * ex + ez * ez
        s = np.clip(((px - t[:, u, 0]) * ex + (pz - t[:, u, 2]) * ez) / np.where(ll > 0, ll, 1), 0, 1)
        qx, qz = t[:, u, 0] + s * ex, t[:, u, 2] + s * ez
        d2 = (qx - px) ** 2 + (qz - pz) ** 2
        # on a vertical edge (ll = 0) the sphere meets its top
        qy = np.where(ll > 0, t[:, u, 1] + s * (t[:, v, 1] - t[:, u, 1]), np.maximum(t[:, u, 1], t[:, v, 1]))
        better = d2 < best - 1e-12
        tie = np.abs(d2 - best) <= 1e-12
        yb = np.where(better, qy, np.where(tie, np.maximum(yb, qy), yb))
        best = np.where(better, d2, best)
        xb = np.where(better, qx, xb)
        zb = np.where(better, qz, zb)
    return best, yb, xb, zb


# --- terrain ---------------------------------------------------------------------------------------------------------
def terrain_at(hf, x, z, normals=True):
    """Height and normal of the height field at (x, z) (its grid squares split as terrain.Heightfield.height splits
    them); height NaN outside it."""
    cx, cz = hf.cell
    u = (np.asarray(x, float) - hf.min[0]) / cx
    w = (np.asarray(z, float) - hf.min[2]) / cz
    n = hf.size
    outside = (u < 0) | (w < 0) | (u > n - 1) | (w > n - 1)
    i = np.clip(np.floor(u).astype(int), 0, n - 2)
    j = np.clip(np.floor(w).astype(int), 0, n - 2)
    fu, fw = u - i, w - j
    H = hf.heights
    a, b, c, d = H[j, i], H[j, i + 1], H[j + 1, i], H[j + 1, i + 1]
    upper = fu > fw
    y = np.where(upper, a + (b - a) * fu + (d - b) * fw, a + (c - a) * fw + (d - c) * fu)
    y = np.where(outside, np.nan, y)
    if not normals:
        return y, None
    gx = np.where(upper, b - a, d - c) / cx          # dy/dx
    gz = np.where(upper, d - b, c - a) / cz          # dy/dz
    nrm = np.stack([-gx, np.ones_like(gx), -gz], -1)
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
    return y, nrm


def _rings():
    pts = [(0.0, 0.0)]
    for r in (0.1, 0.2, 0.3, 0.35, 0.4):
        n = 8 if r < 0.25 else 16
        for k in range(n):
            a = 2 * math.pi * k / n
            pts.append((r * math.cos(a), r * math.sin(a)))
    return np.array(pts)


RINGS = _rings()


def capsule_axis(bottom, radius=RADIUS, height=HEIGHT):
    """Heights above the surface of the capsule's lowest and highest sphere centres: CAPSULE_AXIS for the two runs
    navkit.py makes, else navsample.cpp's nominal capsule (bottom + radius .. height - radius)."""
    return CAPSULE_AXIS.get(round(float(bottom), 2), (bottom + radius, height - radius))


def terrain_blocks(hf, x, z, y, bottom, radius=RADIUS, chunk=100_000, height=HEIGHT):
    """Capsule (radius, stepMax bottom: capsule_axis) overlaps the height field: the terrain under its lower hemisphere
    rises above the hemisphere's surface somewhere (sampled on RINGS)."""
    out = np.zeros(len(y), bool)
    if hf is None or not len(y):
        return out
    d = np.hypot(RINGS[:, 0], RINGS[:, 1])
    lift = capsule_axis(bottom, radius, height)[0] - np.sqrt(np.maximum(radius * radius - d * d, 0))     # hemisphere surface above y
    for s in range(0, len(y), chunk):
        e = min(len(y), s + chunk)
        h, _ = terrain_at(hf, x[s:e, None] + RINGS[None, :, 0], z[s:e, None] + RINGS[None, :, 1], normals=False)
        out[s:e] = np.nanmax(np.where(np.isnan(h), -np.inf, h) - (y[s:e, None] + lift[None, :]), axis=1) > 1e-4
    return out


# --- the cast loop ---------------------------------------------------------------------------------------------------
class Intervals:
    """A union of height intervals per column, for 'is this height inside one' queries (vectorised)."""

    def __init__(self, grid, col, lo, hi):
        self.K = 2.0 ** int(math.ceil(math.log2(grid.y_top - grid.y_bottom + 200)))
        self.off = 100.0 - grid.y_bottom
        lo = np.clip(lo, grid.y_bottom - 50, grid.y_top + 50)
        hi = np.clip(hi, grid.y_bottom - 50, grid.y_top + 50)
        ok = lo <= hi
        col, lo, hi = col[ok], lo[ok], hi[ok]
        klo = col * self.K + (lo + self.off)
        khi = col * self.K + (hi + self.off)
        o = np.argsort(klo, kind='stable')
        klo, khi = klo[o], khi[o]
        if len(klo):
            run = np.maximum.accumulate(khi)
            first = np.r_[True, klo[1:] > run[:-1]]
            starts = np.nonzero(first)[0]
            self.lo = klo[starts]
            self.hi = np.maximum.reduceat(khi, starts)
        else:
            self.lo = self.hi = np.zeros(0)

    def __len__(self):
        return len(self.lo)

    def contains(self, col, y):
        if not len(self.lo):
            return np.zeros(len(col), bool)
        q = col * self.K + (y + self.off)
        k = np.searchsorted(self.lo, q, 'right') - 1
        return (k >= 0) & (self.hi[np.maximum(k, 0)] >= q)


def cast_columns(grid, hits_col, hits_y, hits_n, hits_kind, terrain_y, solid=None, log=print):
    """Replays navsample.cpp's SampleColumn on every column. hits_*: candidate hits (column, y, normal, kind) of
    the meshes, convex faces and the terrain; terrain_y: per column (NaN outside the height field) - the solid under
    it; solid: (column, lo, hi) heights where a cast starting there hits at once (the sphere overlaps geometry, or
    starts inside a convex piece). Returns (column, y, normal, kind) of the recorded surfaces, top to bottom per
    column, sorted by column."""
    K = 4096.0
    y_off = grid.y_top + 100.0
    # the cast runs from yTop down to yBottom (a hit just over yTop touches the sphere at its start)
    keep = (hits_y <= grid.y_top + RAY_RADIUS) & (hits_y >= grid.y_bottom)
    hits_col, hits_y, hits_n, hits_kind = hits_col[keep], hits_y[keep], hits_n[keep], hits_kind[keep]
    order = np.lexsort((-hits_y, hits_col))
    hc, hy, hn, hk = hits_col[order], hits_y[order], hits_n[order], hits_kind[order]
    key = hc * K + (y_off - hy)                      # ascending: by column, then by falling height
    inside_set = Intervals(grid, *solid) if solid is not None and len(solid[0]) else None
    cols = np.unique(hc)
    if grid.mask is not None:
        cols = cols[grid.mask.ravel()[cols]]
    start = np.full(len(cols), grid.y_top, dtype=np.float64)
    solid_run = np.zeros(len(cols))
    count = np.zeros(len(cols), np.int32)
    ty = terrain_y[cols] if terrain_y is not None else np.full(len(cols), np.nan)
    out_c, out_y, out_n, out_k = [], [], [], []
    act = np.arange(len(cols))
    it = 0
    while len(act):
        it += 1
        c = cols[act]
        st = start[act]
        # the first hit at or below start (+ the sphere's radius: one touching it at the start counts, at once)
        p = np.searchsorted(key, c * K + (y_off - (st + RAY_RADIUS)), 'left')
        have = p < len(key)
        pc = np.where(have, p, 0)
        have &= hc[pc] == c
        y = np.where(have, hy[pc], -np.inf)
        at_once = have & (st - y <= AT_ONCE)
        # inside the height field (solid): hits at once - and so on all the way down: nothing below is recorded
        under = ~np.isnan(ty[act]) & (st < ty[act] + RAY_RADIUS)
        inside = inside_set.contains(c, st) if inside_set is not None else np.zeros(len(act), bool)
        stop = under | (~have & ~inside) | (st <= grid.y_bottom)
        skip = ~stop & (at_once | inside)
        rec = ~stop & ~skip
        if rec.any():
            r = act[rec]
            out_c.append(cols[r])
            out_y.append(y[rec])
            out_n.append(hn[pc[rec]])
            out_k.append(hk[pc[rec]])
            count[r] += 1
            start[r] = y[rec] - SKIP
            solid_run[r] = 0
        if skip.any():
            s = act[skip]
            solid_run[s] += SOLID_STEP
            start[s] -= SOLID_STEP
        done = stop.copy()
        done[skip] = solid_run[act[skip]] > MAX_SOLID
        done[rec] = count[act[rec]] >= MAX_LAYERS
        act = act[~done]
    if not out_c:
        return np.zeros(0, np.int64), np.zeros(0), np.zeros((0, 3)), np.zeros(0, np.uint8)
    C, Y, N, Kd = (np.concatenate(out_c), np.concatenate(out_y), np.concatenate(out_n), np.concatenate(out_k))
    o = np.lexsort((-Y, C))
    log(f'  cast: {len(cols):,} columns with geometry, {len(C):,} surfaces, {it} passes')
    return C[o], Y[o], N[o], Kd[o]


# --- the capsule test ------------------------------------------------------------------------------------------------
def _line_tri_interval(t, px, pz, r):
    """Height interval [lo, hi] of the vertical line (px, ., pz) inside the triangle t (n, 3, 3) grown by a ball of
    radius r (lo > hi: none). Exact: the union of the face slab, the three edge cylinders and the vertex spheres."""
    n = len(px)
    lo = np.full(n, np.inf)
    hi = np.full(n, -np.inf)
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    # vertex spheres
    for v in (a, b, c):
        dx, dz = px - v[:, 0], pz - v[:, 2]
        q = r * r - dx * dx - dz * dz
        ok = q >= 0
        s = np.sqrt(np.where(ok, q, 0))
        lo = np.where(ok, np.minimum(lo, v[:, 1] - s), lo)
        hi = np.where(ok, np.maximum(hi, v[:, 1] + s), hi)
    # edge cylinders (capped by the edge's ends): points P = (px, h, pz) with dist(P, segment) <= r and the
    # projection parameter in [0, 1]
    for u, v in ((a, b), (b, c), (c, a)):
        e = v - u
        ee = (e * e).sum(1)
        ee = np.where(ee > 1e-12, ee, 1e-12)
        w0 = np.stack([px - u[:, 0], -u[:, 1], pz - u[:, 2]], 1)    # P - u with h = 0
        # P - u = w0 + h * Y; distance^2 to the line = |w|^2 - (w.e)^2 / ee: quadratic in h
        we0 = (w0 * e).sum(1)
        ey = e[:, 1]
        A = 1 - ey * ey / ee
        B = 2 * (w0[:, 1] - we0 * ey / ee)
        Cc = (w0 * w0).sum(1) - we0 * we0 / ee - r * r
        disc = B * B - 4 * A * Cc
        okq = (disc >= 0) & (A > 1e-9)
        sq = np.sqrt(np.where(okq, disc, 0))
        A2 = np.where(A > 1e-9, 2 * A, 1)
        h1, h2 = (-B - sq) / A2, (-B + sq) / A2
        # a vertical edge: the distance to its line does not change with h - inside for every h or for none
        vert = (A <= 1e-9) & (Cc <= 0)
        okq = okq | vert
        h1 = np.where(vert, -np.inf, h1)
        h2 = np.where(vert, np.inf, h2)
        # parameter s(h) = (we0 + h * ey) / ee in [0, 1]: an interval of h
        if True:
            with np.errstate(divide='ignore', invalid='ignore'):
                s1 = np.where(np.abs(ey) > 1e-12, (0 * ee - we0) / ey, -np.inf)
                s2 = np.where(np.abs(ey) > 1e-12, (1 * ee - we0) / ey, np.inf)
            sl, sh = np.minimum(s1, s2), np.maximum(s1, s2)
            flat = np.abs(ey) <= 1e-12
            inrange = (we0 >= 0) & (we0 <= ee)
            sl = np.where(flat, np.where(inrange, -np.inf, np.inf), sl)
            sh = np.where(flat, np.where(inrange, np.inf, -np.inf), sh)
        l2, h2b = np.maximum(h1, sl), np.minimum(h2, sh)
        ok = okq & (l2 <= h2b)
        lo = np.where(ok, np.minimum(lo, l2), lo)
        hi = np.where(ok, np.maximum(hi, h2b), hi)
    # face slab: |n . (P - a)| <= r and the projection of P onto the plane inside the triangle
    nrm = np.cross(b - a, c - a)
    ln = np.linalg.norm(nrm, axis=1)
    good = ln > 1e-12
    nrm = nrm / np.where(good, ln, 1)[:, None]
    ny = nrm[:, 1]
    okf = good & (np.abs(ny) > 1e-6)
    # signed distance f(h) = n . (P - a) = f0 + ny * h
    f0 = nrm[:, 0] * (px - a[:, 0]) + nrm[:, 2] * (pz - a[:, 2]) - ny * a[:, 1]
    nys = np.where(okf, ny, 1)
    hA, hB = (-r - f0) / nys, (r - f0) / nys
    fl, fh = np.minimum(hA, hB), np.maximum(hA, hB)
    # a vertical face: the distance to its plane does not change with h - the edges below bound the height
    wall = good & ~okf
    fl = np.where(wall, np.where(np.abs(f0) <= r, -np.inf, np.inf), fl)
    fh = np.where(wall, np.inf, fh)
    okf = good
    # projection inside the triangle: for each edge, the projected point on the inner side - linear in h
    for u, v in ((a, b), (b, c), (c, a)):
        e = v - u
        m = np.cross(nrm, e)                         # inward edge normal (in the plane) for a ccw triangle about n
        # projected Q = P - f(h) n; (Q - u) . m >= 0. (P - u) . m - f(h) (n . m) and n . m = 0 -> (P - u) . m >= 0
        g0 = m[:, 0] * (px - u[:, 0]) + m[:, 2] * (pz - u[:, 2]) - m[:, 1] * u[:, 1]
        gy = m[:, 1]
        with np.errstate(divide='ignore', invalid='ignore'):
            hb = -g0 / gy
        pos = gy > 1e-12
        neg = gy < -1e-12
        zero = ~pos & ~neg
        fl = np.where(pos, np.maximum(fl, hb), fl)
        fh = np.where(neg, np.minimum(fh, hb), fh)
        bad = zero & (g0 < 0)
        fl = np.where(bad, np.inf, fl)
    ok = okf & (fl <= fh)
    lo = np.where(ok, np.minimum(lo, fl), lo)
    hi = np.where(ok, np.maximum(hi, fh), hi)
    return lo, hi


def capsule_blocked(grid, cols, ys, tris, bottoms, hf=None, radius=RADIUS, height=HEIGHT, log=print):
    """Per surface (column, y) and capsule bottom (stepMax): does the soldier capsule overlap the triangles or the
    height field? Returns {bottom: bool array}."""
    out = {b: np.zeros(len(ys), bool) for b in bottoms}
    if not len(ys):
        return out
    K = 4096.0
    y_off = grid.y_top + 200.0
    key = cols * K + (ys + y_off)                    # ascending by column, then by rising height (cols sorted)
    o = np.argsort(key, kind='stable')
    skey = key[o]
    if len(tris):
        a = tris
        xs, zs, yy = a[:, :, 0], a[:, :, 2], a[:, :, 1]
        i0, i1 = _ranges(xs.min(1) - radius, xs.max(1) + radius, grid.min_x, grid.pitch, grid.nx)
        j0, j1 = _ranges(zs.min(1) - radius, zs.max(1) + radius, grid.min_z, grid.pitch, grid.nz)
        diff = {b: np.zeros(len(ys) + 1, np.int64) for b in bottoms}
        npairs = 0
        for t, i, j in _pairs(i0, i1, j0, j1, budget=4_000_000):
            px = grid.min_x + i * grid.pitch
            pz = grid.min_z + j * grid.pitch
            col = j * grid.nx + i
            # quick reject: the column must hold a surface whose capsule's height range meets the triangle's
            lo, hi = _line_tri_interval(a[t], px, pz, radius)
            ok = lo <= hi
            if not ok.any():
                continue
            col, lo, hi = col[ok], lo[ok], hi[ok]
            npairs += int(ok.sum())
            for b in bottoms:
                # the capsule's axis (capsule_axis above y) meets [lo, hi]
                c0, c1 = capsule_axis(b, radius, height)
                ya = np.clip(lo - c1, 1 - y_off, K - 1 - y_off)
                yb = np.clip(hi - c0, 1 - y_off, K - 1 - y_off)
                s = np.searchsorted(skey, col * K + (ya + y_off), 'left')
                e = np.searchsorted(skey, col * K + (yb + y_off), 'right')
                m = e > s
                np.add.at(diff[b], s[m], 1)
                np.add.at(diff[b], e[m], -1)
        for b in bottoms:
            blk = np.cumsum(diff[b][:-1]) > 0
            out[b][o] = blk
        log(f'  capsule: {npairs:,} triangle-column pairs near the line')
    if hf is not None:
        x, z = grid.xz(cols)
        for b in bottoms:
            out[b] |= terrain_blocks(hf, x, z, ys, b, radius, height=height)
    return out


# --- one sampling run ------------------------------------------------------------------------------------------------
def sample_grid(grid, geom, hf, log=print, bottoms=(0.28, 0.6)):
    """Samples a grid: {bottom: navsample.Samples} for each capsule bottom (one cast, one capsule test per bottom)."""
    t0 = time.time()
    # mesh hits (up-facing triangles), convex faces (their upper faces), terrain per column
    mesh = geom.kind == KIND_MESH
    if CONVEX_IN_TREE_HOLLOW:
        mesh |= geom.vt == KIND_MESH             # convex pieces under a MOPP tree: hit like its triangles (see the flag)
    mc, my, mt, men, mtouch = raster_hits(grid, geom.tris[mesh], up_only=not MESH_TWO_SIDED, touch=True)
    mt = np.nonzero(mesh)[0][mt]
    conv = ~mesh
    # convex pieces: their faces are grown by the convex radius already; the cast's sphere reaches past a top face's
    # edge (a thin fence sheet's top) by its radius and that rounded shell
    cc, cy, ct, cen, ctouch = raster_hits(grid, geom.tris[conv], grow=CONVEX_GROW, up_only=False, touch=True)
    ct = np.nonzero(conv)[0][ct]
    tri_n = np.cross(geom.tris[:, 1] - geom.tris[:, 0], geom.tris[:, 2] - geom.tris[:, 0])
    tri_n /= np.maximum(np.linalg.norm(tri_n, axis=1, keepdims=True), 1e-12)
    # convex: solid intervals per (column, piece), hits only on the upward faces
    # A cast that starts touching a triangle hits at once only when it moves onto it (navsample.cpp's 0.07 m rule,
    # in cast_columns): the sphere sliding down beside a wall it touches does not stop (taking every touch interval
    # as 'at once' lost real floors beside walls: sp_004_b's object surfaces found 94.3 -> 89.6 %). Inside a convex
    # piece it does (the in-game files have nothing inside one).
    sc, slo, shi = [np.zeros(0, np.int64)], [np.zeros(0)], [np.zeros(0)]
    del mtouch, ctouch
    if len(cc):
        # inside a convex piece (between its faces in the column, by the faces the line passes through): solid
        face = np.isnan(cen[:, 0])
        sid = geom.solid[ct[face]]
        key = cc[face] * (int(sid.max()) + 1) + sid
        u, inv = np.unique(key, return_inverse=True)
        lo = np.full(len(u), np.inf)
        hi = np.full(len(u), -np.inf)
        np.minimum.at(lo, inv, cy[face])
        np.maximum.at(hi, inv, cy[face])
        sc.append(u // (int(sid.max()) + 1))
        slo.append(lo - RAY_RADIUS)
        shi.append(hi + RAY_RADIUS)
        up = (tri_n[ct, 1] > 0) | ~face
        cc, cy, ct, cen = cc[up], cy[up], ct[up], cen[up]
    solid = (np.concatenate(sc), np.concatenate(slo), np.concatenate(shi))
    log(f'  hits: {len(mc):,} mesh, {len(cc):,} convex ({time.time() - t0:.0f} s)')
    if grid.mask is not None:
        all_cols = np.nonzero(grid.mask.ravel())[0]
    else:
        all_cols = np.arange(grid.ncols)
    ty = np.full(grid.ncols, np.nan)
    if hf is not None:
        for s in range(0, len(all_cols), 2_000_000):
            c = all_cols[s:s + 2_000_000]
            x, z = grid.xz(c)
            ty[c] = terrain_at(hf, x, z, normals=False)[0]
    tc = all_cols[~np.isnan(ty[all_cols]) & (ty[all_cols] < grid.y_top) & (ty[all_cols] > grid.y_bottom)]
    tn = terrain_at(hf, *grid.xz(tc))[1] if len(tc) else np.zeros((0, 3))
    # the cast reports terrain hits a little above the height field on slopes: RAY_RADIUS * (1 - ny) (sp_004_b, every
    # 0.1 of ny: 2.1 mm at 0.9 ... 41.7 mm at 0.1 - a fit within 1 mm)
    tyh = ty[tc] + RAY_RADIUS * (1 - tn[:, 1]) if TERRAIN_LIFT else ty[tc]
    hits_col = np.concatenate([mc, cc, tc])
    hits_y = np.concatenate([my, cy, tyh])
    mn = tri_n[mt]
    if MESH_TWO_SIDED:
        flip = (mn[:, 1] < 0) & (geom.kind[mt] == KIND_MESH)   # convex pieces keep their outward normal
        mn = np.where(flip[:, None], -mn, mn)            # the hit normal faces the ray
    mn = np.where(np.isnan(men[:, :1]), mn, men)        # edge contacts
    cn = np.where(np.isnan(cen[:, :1]), tri_n[ct], cen)
    hits_n = np.concatenate([mn, cn, tn])
    hits_k = np.concatenate([geom.vt[mt], geom.vt[ct],
                             np.full(len(tc), KIND_TERRAIN, np.uint8)])
    C, Y, N, Kd = cast_columns(grid, hits_col, hits_y, hits_n, hits_k, ty, solid, log=log)
    blocked = capsule_blocked(grid, C, Y, geom.tris, bottoms, hf, log=log)
    out = {}
    counts = np.bincount(C, minlength=grid.ncols).astype(np.int64)
    start = np.zeros(grid.ncols + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    hit = np.zeros((len(Y), 3), np.uint32)
    hit[:, 0] = VTABLE_OF_KIND[Kd]
    hit[:, 1] = RAY_SHAPE_VTABLE
    nq = (np.clip(np.rint(N * 127), -127, 127) / 127.0).astype(np.float32)
    for b in bottoms:
        out[b] = navsample.Samples(np.float32(grid.pitch), np.float32(grid.min_x), np.float32(grid.min_z), grid.nx,
                                   grid.nz, grid.y_top, grid.y_bottom, RADIUS, b, HEIGHT, RAY_RADIUS, start,
                                   Y.astype(np.float32), nq, blocked[b].astype(np.uint8), hit)
    log(f'  sampled {grid.nx} x {grid.nz} columns at {grid.pitch:g} m: {len(Y):,} surfaces, '
        + ', '.join(f'blocked {int(blocked[b].sum()):,} (capsule from {b})' for b in bottoms)
        + f' ({time.time() - t0:.0f} s)')
    return out


def write(path, s):
    """navsample.cpp's version-3 file."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    counts = np.diff(s.start)
    if len(counts) and counts.max() > 255:
        raise ValueError('more than 255 surfaces in a column')
    hdr = navsample.HEADER.pack(b'ONS1', 3, float(s.pitch), float(s.min_x), float(s.min_z), int(s.nx), int(s.nz),
                                float(s.y_top), float(s.y_bottom), float(s.radius), float(s.cap_bottom),
                                float(s.cap_top), float(s.ray_radius), 0)
    rec = np.zeros(len(s.y), np.dtype([('y', '<f4'), ('n', 'i1', 3), ('f', 'u1'), ('h', '<u4', 3)]))
    rec['y'] = s.y
    rec['n'] = np.clip(np.rint(s.normal * 127), -127, 127).astype(np.int8)
    rec['f'] = s.flags
    rec['h'] = s.hit if s.hit is not None and len(s.hit) else 0
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(hdr)
        f.write(rec.tobytes())
        at = f.tell()
        f.write(counts.astype(np.uint8).tobytes())
        f.write(struct.pack('<Q', at))
        f.write(b'CNT3')
    os.replace(tmp, path)


# --- comparison with an in-game file ---------------------------------------------------------------------------------
def compare(a, b, tol=0.1, slope=40.0, log=print, both_only=True):
    """Per column agreement of two sample files on the same grid (a: in game, b: offline). Columns empty in a (outside
    the in-game run's rectangles) are left out when both_only. Returns a dict of measures."""
    if (a.nx, a.nz) != (b.nx, b.nz) or abs(a.min_x - b.min_x) > 1e-3 or abs(a.min_z - b.min_z) > 1e-3:
        raise ValueError('not the same grid')
    na, nb = np.diff(a.start), np.diff(b.start)
    cols = np.nonzero(na > 0)[0] if both_only else np.arange(a.nx * a.nz)
    r = {'columns': int(len(cols)), 'surfaces_a': int(na[cols].sum()), 'surfaces_b': int(nb[cols].sum())}
    r['layers_equal'] = float(np.mean(na[cols] == nb[cols]) * 100)
    # top surface height
    both = cols[(na[cols] > 0) & (nb[cols] > 0)]
    dy = np.abs(a.y[a.start[both]] - b.y[b.start[both]])
    r['top_within_0.1'] = float(np.mean(dy <= tol) * 100)
    r['top_median_mm'] = float(np.median(dy) * 1000) if len(dy) else float('nan')
    r['top_missing_b'] = float(np.mean(nb[cols] == 0) * 100)
    # every surface of a: is there one in b's column within tol? (and vice versa)
    def matched(x, y_, cols_):
        ca = np.repeat(np.arange(x.nx * x.nz), np.diff(x.start))
        m = np.zeros(len(x.y), bool)
        sel = np.isin(ca, cols_)
        idx = np.nonzero(sel)[0]
        cc, yy = ca[idx], x.y[idx].astype(float)
        K, off = 4096.0, 1000.0
        cb = np.repeat(np.arange(y_.nx * y_.nz), np.diff(y_.start))
        kb = cb * K + (y_.y.astype(float) + off)
        o = np.argsort(kb)
        kb = kb[o]
        lo = np.searchsorted(kb, cc * K + (yy - tol + off), 'left')
        hi = np.searchsorted(kb, cc * K + (yy + tol + off), 'right')
        m[idx] = hi > lo
        partner = np.full(len(x.y), -1, np.int64)
        partner[idx[hi > lo]] = o[lo[hi > lo]]
        return m, partner, sel
    ma, pa, sa = matched(a, b, cols)
    mb, _, sb = matched(b, a, cols)
    r['surf_a_found'] = float(ma[sa].mean() * 100) if sa.any() else float('nan')
    r['surf_b_real'] = float(mb[sb].mean() * 100) if sb.any() else float('nan')
    wa = navsample.walkable(a, slope)
    wb = navsample.walkable(b, slope)
    # walkable mask: per in-game surface, walkable in both (b's matched surface)
    k = np.nonzero(sa & ma)[0]
    agree = wa[k] == wb[pa[k]]
    r['walk_agree_matched'] = float(agree.mean() * 100) if len(k) else float('nan')
    ka = np.nonzero(sa & wa)[0]
    r['walk_a_found_walk_b'] = float(np.mean(ma[ka] & wb[np.maximum(pa[ka], 0)]) * 100) if len(ka) else float('nan')
    kb_ = np.nonzero(sb & wb)[0]
    mbb, pbb, _ = matched(b, a, cols)
    r['walk_b_real_walk_a'] = float(np.mean(mbb[kb_] & wa[np.maximum(pbb[kb_], 0)]) * 100) if len(kb_) else float('nan')
    blk_a = (a.flags[k] & 1) > 0
    blk_b = (b.flags[pa[k]] & 1) > 0
    r['blocked_agree_matched'] = float(np.mean(blk_a == blk_b) * 100) if len(k) else float('nan')
    r['blocked_a'] = int(blk_a.sum())
    r['blocked_b'] = int(blk_b.sum())
    r['blocked_both'] = int((blk_a & blk_b).sum())
    terr_a = a.hit[:, 0] == TERRAIN_SHAPE_VTABLE if a.hit is not None else np.ones(len(a.y), bool)
    obj = sa & ~terr_a
    r['object_surf_a'] = int(obj.sum())
    r['object_surf_a_found'] = float(ma[obj].mean() * 100) if obj.any() else float('nan')
    for kk, v in r.items():
        log(f'  {kk:24s} {v:.2f}' if isinstance(v, float) else f'  {kk:24s} {v:,}')
    return r


# --- level driver ----------------------------------------------------------------------------------------------------
def out_dir(root):
    return os.path.join(root, 'work', 'navsample_offline')


def sample_path(root, lv, suffix=''):
    return os.path.join(out_dir(root), f'{lv}_navkit{suffix}.bin')


def common_bases(root):
    return [os.path.join(root, 'work', 'navkit', 'common', c) for c in ('sp_common', 'mp_common')]


def load_level(root, lv, box=None, log=print, bodies=None):
    from mohcore import level as levelmod
    L = os.path.join(root, 'work', 'levels', lv)
    geom = level_geometry(L, common_bases(root), log=log, box=box)
    geom = geom.bodies(bodies or BODIES)
    if INTACT_ONLY:
        geom = geom.intact()
    hf = levelmod.heightfield_of(root, lv, log=log)
    return geom, hf


def run_level(root, lv, pitch=0.8, box=None, y=None, log=print, bodies=None, rects=None, names=None, loaded=None):
    """Both capsule runs of one pitch for a prepared level, written as the in-game sampler names them (in the offline
    folder). loaded: (geometry, height field) already read. Returns the paths."""
    import navkit
    pr = navkit.Project(root)
    s = navkit.ensure_settings(pr, lv)
    box = box or s['box']
    y = y or s['y']
    grid = Grid.from_box(pitch, box, y, rects)
    log(f'== offline sample {lv}: box {box}, y {y}, pitch {pitch} -> {grid.nx} x {grid.nz} columns'
        + (f', {int(grid.mask.sum()):,} in {len(rects)} rectangles' if grid.mask is not None else ''))
    geom, hf = loaded or load_level(root, lv, box=box, log=log, bodies=bodies)
    res = sample_grid(grid, geom, hf, log=log, bottoms=tuple(CAP_BOTTOMS.values()))
    ps = '' if abs(pitch - 0.8) < 1e-6 else f'_p{int(round(pitch * 10)):02d}'
    paths = []
    for suf, b in CAP_BOTTOMS.items():
        p = (names or {}).get(suf) or sample_path(root, lv, suf + ps)
        write(p, res[b])
        paths.append(p)
        log(f'  wrote {p}')
    return paths


def detailed_rects(root, lv, ids, margin=1.0):
    """The detailed sectors' squares plus the sampling margin (navkit.detailed_rects) and their box."""
    import navkit
    st = navkit.ensure_settings(navkit.Project(root), lv)
    rects = [[(i + st['xoff']) * 100 - margin, (j + st['zoff']) * 100 - margin, (i + st['xoff']) * 100 + 100 + margin,
              (j + st['zoff']) * 100 + 100 + margin] for i, j in ids]
    box = [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)]
    return rects, box


def sample_level(root, lv, log=print, bodies=None):
    """What navkit.py sample does in game, offline: the 0.8 m pair over the level's box, then - when the level has
    detailed sectors (chosen from those samples, detailed.py) - the 0.2 m pair over their squares. The geometry is
    read once. Returns {'p08': paths, 'p02': paths or None, 'detailed': ids}."""
    import navkit
    s = navkit.ensure_settings(navkit.Project(root), lv)
    loaded = load_level(root, lv, box=s['box'], log=log, bodies=bodies)
    p08 = run_level(root, lv, 0.8, log=log, loaded=loaded)
    ids, _why = detailed_choice(root, lv, p08[0])
    out = {'p08': p08, 'p02': None, 'detailed': [list(i) for i in ids]}
    for suf in ('', '_hi'):                      # a stale 0.2 m pair would be built in by build_level
        old = sample_path(root, lv, suf + '_p02')
        if os.path.isfile(old):
            os.remove(old)
    if ids:
        rects, box = detailed_rects(root, lv, ids)
        out['p02'] = run_level(root, lv, 0.2, box=box, log=log, rects=rects, loaded=loaded)
    else:
        log(f'  {lv}: no detailed sectors - no 0.2 m runs')
    return out


# --- stock multiplayer maps ------------------------------------------------------------------------------------------
STOCK_MP = ['mp_001', 'mp_001sr', 'mp_002', 'mp_002sr', 'mp_003', 'mp_004', 'mp_004sdm', 'mp_005', 'mp_005sr', 'mp_006',
            'mp_006cq', 'mp_006sdm', 'mp_007', 'mp_007sdm', 'mp_008', 'mp_009cq', 'mp_009gr', 'mp_009sdm', 'mp_012gr',
            'mp_012sr']
COMBAT_MARGIN = 50.0     # sampling box of a map without path data: its combat areas' extent plus this (m)


def combat_box(root, lv, margin=COMBAT_MARGIN):
    """(minX, minZ, maxX, maxZ) of the level's combat areas (CombatAreaTriggerEntityData outlines, every team and
    stage) plus margin, on 10 m; None without combat areas. On a stock MP map the objects' extent (the box navkit.py
    prepare takes without path data) holds backdrop scenery kilometres away (MP_005: 4.7 x 4.0 km); the soldiers'
    ground is the combat area (MP_005: 0.77 x 0.77 km). ONS_MP_002 (Valparaiso, DICE's console path data): combat
    areas x -108..290, z -494..374; DICE's ground x -110..440, z -530..370 (150 m past the combat area on x)."""
    from mohcore import level as levelmod
    L = levelmod.load(lv, opfor=root, geometry=False, log=lambda *a: None)
    pts = [s.points for s in L.shapes if L.entities[s.entity].category == 'combat area' and s.kind == 'area']
    if not pts:
        return None
    P = np.concatenate(pts)
    return [math.floor((P[:, 0].min() - margin) / 10) * 10, math.floor((P[:, 2].min() - margin) / 10) * 10,
            math.ceil((P[:, 0].max() + margin) / 10) * 10, math.ceil((P[:, 2].max() + margin) / 10) * 10]


def package_overlay(pr, lv, log=print):
    """The game loads a level's Package bundle over its Dist bundle, file by file. Every stock MP map has its level
    partition and static-group assets in Package (game patches: MP_002 4,067,601 bytes there, 4,023,001 in Dist;
    MP_002SR's partition is in Package only), but navkit.py prepare and levelobjects.py read the Dist copy. This unpacks
    both (as prepare does) and copies Package's levels\\ files over the Dist copy in the work folder, so every NavKit
    step sees the level the game loads. The copied files are listed in work\\levels\\<lv>\\pkg_overlay.txt."""
    import shutil
    L = pr.level_work(lv)
    pr.unpack(os.path.join(pr.level_dirs()[lv], 'level-00.fbrb'), os.path.join(L, 'dist'), log)
    pkg = os.path.join(pr.game, 'Package', 'levels', os.path.basename(pr.level_dirs()[lv]), 'level-00.fbrb')
    if not os.path.isfile(pkg):
        return []
    pr.unpack(pkg, os.path.join(L, 'pkg'), log)
    src = os.path.join(L, 'pkg', 'levels')
    copied = []
    for dp, _, fs in os.walk(src):
        for f in fs:
            rel = os.path.relpath(os.path.join(dp, f), src)
            dst = os.path.join(L, 'dist', 'levels', rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(dp, f), dst)
            copied.append(rel)
    open(os.path.join(L, 'pkg_overlay.txt'), 'w', encoding='utf-8').write('\n'.join(sorted(copied)) + '\n')
    log(f'  Package overlay: {len(copied)} level files copied over the Dist copy')
    return copied


def prepare_mp(root, lv, log=print):
    """navkit.py prepare (offline: unpack, convert, objects), then - on a level without path data - the box set to
    its combat areas (combat_box) and the sector offsets by navkit's rule for such a level (one sector below the
    box)."""
    import navkit
    pr = navkit.Project(root)
    if not pr.dice_pathdata(lv):
        package_overlay(pr, pr.level(lv), log=log)
    navkit.prepare(pr, pr.level(lv), log=log)
    st = navkit.ensure_settings(pr, lv)
    if pr.dice_pathdata(lv):
        return st
    box = combat_box(root, lv)
    if box:
        st['box'], st['box_source'] = box, f'combat areas + {COMBAT_MARGIN:g} m (navsample_offline.combat_box)'
        st['xoff'], st['zoff'] = int(math.floor(box[0] / 100.0)) - 1, int(math.floor(box[1] / 100.0)) - 1
        st['offsets_source'] = 'rule: box'
        json.dump(st, open(pr.settings_path(lv), 'w'), indent=1)
        log(f'  box {box} ({st["box_source"]}), sector offsets {st["xoff"]}, {st["zoff"]}')
    return st


ALL_STEPS = ('prepare', 'sample', 'build', 'check')


def stock_mp(root, levels=None, log=print, steps=ALL_STEPS):
    """The whole offline pipeline on the stock MP maps, one after another; progress and timings in
    work\\navkit_offline\\stockmp.json (kept between runs; a level's entry is replaced when all its steps run again,
    updated when some do)."""
    levels = levels or STOCK_MP
    rep_path = os.path.join(root, 'work', 'navkit_offline', 'stockmp.json')
    os.makedirs(os.path.dirname(rep_path), exist_ok=True)
    report = json.load(open(rep_path)) if os.path.isfile(rep_path) else {}
    for lv in levels:
        r = {} if tuple(steps) == ALL_STEPS else report.get(lv, {})
        r.setdefault('steps', {})
        r.pop('error', None)
        log(f'##### {lv} ({time.strftime("%H:%M:%S")})')
        try:
            for step in steps:
                t0 = time.time()
                if step == 'prepare':
                    st = prepare_mp(root, lv, log=log)
                    r['box'], r['offsets'] = st['box'], [st['xoff'], st['zoff']]
                elif step == 'sample':
                    out = sample_level(root, lv, log=log)
                    r['detailed_sectors'] = len(out['detailed'])
                elif step == 'build':
                    r['pathdata'] = build_level(root, lv, log=log)
                    r['size_mb'] = round(os.path.getsize(r['pathdata']) / 1e6, 2)
                elif step == 'check':
                    path = r.get('pathdata') or os.path.join(root, 'work', 'navkit_offline', lv, 'pathdata.hpd')
                    res = check_level(root, lv, path, log=log)
                    r['check'] = {k: [v[0], v[1]] for k, v in res.items()}
                r['steps'][step] = round(time.time() - t0, 1)
                report[lv] = r
                json.dump(report, open(rep_path, 'w'), indent=1)
            r['ok'] = True
        except Exception as e:           # one map's failure does not stop the batch
            import traceback
            r['ok'] = False
            r['error'] = f'{type(e).__name__}: {e}'
            log(traceback.format_exc())
        report[lv] = r
        json.dump(report, open(rep_path, 'w'), indent=1)
    return report


def ingame_files(root, lv):
    """The level's in-game sample files as navkit.py build takes them: {suffix: path} for '', '_hi', '_p02',
    '_hi_p02' ('' and '_hi': <lv>_navkit.bin or the older <lv>_full.bin)."""
    d = os.path.join(root, 'work', 'navsample')
    out = {}
    for base in (f'{lv}_navkit', f'{lv}_full'):
        if os.path.isfile(os.path.join(d, base + '.bin')) and os.path.isfile(os.path.join(d, base + '_hi.bin')):
            out[''], out['_hi'] = os.path.join(d, base + '.bin'), os.path.join(d, base + '_hi.bin')
            break
    for suf in ('_p02', '_hi_p02'):
        q = os.path.join(d, f'{lv}_navkit{suf}.bin')
        if os.path.isfile(q):
            out[suf] = q
    return out


def parity(root, levels, name='offline', log=print):
    """For levels with in-game samples (and DICE path data): offline samples on the in-game files' grids, NavKit
    built from each, and the measures: navdiff of the offline build against the in-game build (same code, only the
    samples differ), and navparity of both against DICE's file. Saved as work\\navkit_offline\\parity_<name>.json."""
    import shutil
    import navdiff
    import navparity
    import navregress
    out_path = os.path.join(root, 'work', 'navkit_offline', f'parity_{name}.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    report = json.load(open(out_path)) if os.path.isfile(out_path) else {}
    for lv in levels:
        t0 = time.time()
        files = ingame_files(root, lv)
        if '' not in files:
            log(f'{lv}: no in-game samples - skipped')
            continue
        like = os.path.join(out_dir(root), 'like', lv)
        os.makedirs(like, exist_ok=True)
        geom = hf = None
        val = {}
        for suf, f in files.items():
            s, _ = navsample.load(f)
            grid = Grid.like(s)
            na = np.diff(s.start)
            if (na == 0).mean() > 0.5:
                grid.mask = (na > 0).reshape(grid.nz, grid.nx)
            if geom is None:
                geom, hf = load_level(root, lv, log=log)
            res = sample_grid(grid, geom, hf, log=log, bottoms=(float(s.cap_bottom),))[float(s.cap_bottom)]
            write(os.path.join(like, f'{lv}_navkit{suf}.bin'), res)
            log(f'  {lv}{suf}: offline against in game')
            val[suf or '_08'] = compare(s, res, log=lambda m: log('  ' + m))
        t_sample = time.time() - t0
        cdir = os.path.join(root, 'work', 'navkit_offline', 'compare')
        os.makedirs(cdir, exist_ok=True)
        off_hpd = os.path.join(cdir, f'{lv}_offline.hpd')
        ing_hpd = os.path.join(cdir, f'{lv}_ingame.hpd')
        t1 = time.time()
        build_level(root, lv, out=off_hpd, log=lambda *a: None, samples_dir=like)
        t_off = time.time() - t1
        t1 = time.time()
        navregress.build_level(root, lv, ing_hpd)
        t_ing = time.time() - t1
        d, _, _, _ = navdiff.diff(ing_hpd, off_hpd, log=lambda *a: None)
        row = {'validation': val, 'seconds': {'offline_samples': round(t_sample), 'build_offline': round(t_off),
                                             'build_ingame': round(t_ing)},
               'offline_vs_ingame': {'ground': d['cells']['parity'],
                                     'extra': round(100.0 * d['cells']['only_candidate'] / max(d['cells']['reference'], 1), 2),
                                     'areas_same': d['areas']['parity'], 'nodes_same': d['graph']['node_parity'],
                                     'edges_same': d['graph']['edge_parity']}}
        if os.path.isfile(navparity.reference_of(root, lv)):
            row['dice_offline'] = navparity.run_level(root, lv, off_hpd, log)
            row['dice_ingame'] = navparity.run_level(root, lv, ing_hpd, log)
        report[lv] = row
        json.dump(report, open(out_path, 'w'), indent=1)
        log(f'{lv}: offline build against the in-game build: ' + ', '.join(f'{k} {v}' for k, v in row['offline_vs_ingame'].items()))
        for k in ('dice_offline', 'dice_ingame'):
            if k in row:
                r = row[k]
                log(f'  {k}: ground {r["ground"]} extra {r["extra_ground"]} connectivity {r["connectivity"]} '
                    f'over {r["over_connection"]} AI {r["ai_coverage"]} pairwise {r["ai_pairwise"]}')
    log(f'wrote {out_path}')
    return report


def detailed_choice(root, lv, s08):
    """navkit.detailed_sectors with the offline 0.8 m samples (tools\\detailed.py)."""
    import detailed
    import navkit
    pr = navkit.Project(root)
    st = navkit.ensure_settings(pr, lv)
    chosen, why = detailed.choose(pr.level_work(lv), common_bases(root)[::-1], s08, st['xoff'], st['zoff'])
    return [tuple(c) for c in chosen], why


def build_level(root, lv, out=None, log=print, samples_dir=None):
    """navkit.py build with the offline samples: the same inputs (level objects, spawns, water, orders, ladders), the
    0.8 m pair and, when present, the 0.2 m pair over the detailed sectors chosen from the offline 0.8 m samples."""
    import navbuild
    import navkit
    import navregress
    pr = navkit.Project(root)
    s = navkit.ensure_settings(pr, lv)
    L = pr.level_work(lv)
    d = samples_dir or out_dir(root)
    s08 = os.path.join(d, f'{lv}_navkit.bin')
    if not os.path.isfile(s08) or not os.path.isfile(s08[:-4] + '_hi.bin'):
        raise RuntimeError(f'{lv}: offline samples missing ({s08} and its _hi run)')
    out = out or os.path.join(root, 'work', 'navkit_offline', lv, 'pathdata.hpd')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    detailed = None
    p02 = os.path.join(d, f'{lv}_navkit_p02.bin')
    if os.path.isfile(p02):
        ids, _ = detailed_choice(root, lv, s08)
        hi02 = os.path.join(d, f'{lv}_navkit_hi_p02.bin')
        detailed = dict(samples=p02, high=hi02 if os.path.isfile(hi02) else None, sectors=set(ids))
        log(f'  detailed zones: {len(ids)} sectors at pitch 0.2')
    t0 = time.time()
    lad = os.path.join(L, 'ladders.json')
    navbuild.build(s08, os.path.join(L, 'forbid_tris.npy'), s['xoff'], s['zoff'], out, detailed=detailed,
                   dynamic_path=os.path.join(L, 'dynamic_tris.npy'), high_path=s08[:-4] + '_hi.bin',
                   stairs_path=os.path.join(L, 'stair_tris.npy'),
                   seeds=json.load(open(os.path.join(L, 'soldier_spawns.json'))),
                   water=navregress.level_water(root, lv), bounds=navregress.reference_bounds(root, lv),
                   order_points=navregress.order_points_of(root, lv), area_orders=navregress.area_orders_of(root, lv),
                   ladders=json.load(open(lad)) if os.path.isfile(lad) else None,
                   log=lambda *a: log('  ' + ' '.join(str(x) for x in a)))
    log(f'  built {out} in {time.time() - t0:.0f} s ({os.path.getsize(out) / 1e6:.1f} MB)')
    return out


def check_level(root, lv, path, log=print):
    """hpdcheck, byte-exact roundtrip / meshtrip, and ordercheck (self-check without DICE data) of a build."""
    import subprocess
    import navkit
    pr = navkit.Project(root)
    s = navkit.ensure_settings(pr, lv)
    res = {}
    for name, args in (('hpdcheck', [os.path.join(HERE, 'hpdcheck.py'), path]),
                       ('roundtrip', [os.path.join(HERE, 'hpdtool.py'), 'roundtrip', path]),
                       ('meshtrip', [os.path.join(HERE, 'hpdtool.py'), 'meshtrip', path])):
        r = subprocess.run([sys.executable] + args, capture_output=True, text=True, errors='replace')
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or ['']
        res[name] = (r.returncode, tail[0])
        log(f'  {name}: exit {r.returncode}: {tail[0]}')
    xml = os.path.join(pr.level_work(lv), 'dist', 'levels', s['level'] + '.xml')
    ref = pr.dice_pathdata(lv)
    r = subprocess.run([sys.executable, os.path.join(HERE, 'ordercheck.py'), xml] + ([ref] if ref else []) + [path],
                       capture_output=True, text=True, errors='replace')
    out = (r.stdout + r.stderr).strip().splitlines()
    open(os.path.join(os.path.dirname(path), 'check.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    res['ordercheck'] = (r.returncode, [l for l in out if not l.startswith('  ')][-6:])
    for l in res['ordercheck'][1]:
        log('  ' + l)
    return res


def _opt(args, name, conv=str):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return conv(v)
    return None


def main(argv):
    args = list(argv[1:])
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    import navkit
    pitch = _opt(args, '--pitch', float) or 0.8
    box = _opt(args, '--box', lambda v: [float(a) for a in v.split(',')])
    yr = _opt(args, '--y', lambda v: [float(a) for a in v.split(',')])
    bodies = _opt(args, '--bodies')
    root = navkit.find_opfor(_opt(args, '--opfor'))
    cmd = args[0]
    if cmd == 'compare':
        a, _ = navsample.load(args[1])
        b, _ = navsample.load(args[2])
        compare(a, b)
        return 0
    if cmd == 'parity':
        parity(root, [a.lower() for a in args[1:]], name=_opt(args, '--name') or 'offline')
        return 0
    if cmd == 'stockmp':
        steps = _opt(args, '--steps') or ','.join(ALL_STEPS)
        sel = [a.lower() for a in args[1:]] or None
        stock_mp(root, sel, steps=tuple(steps.split(',')))
        return 0
    lv = args[1].lower()
    if cmd == 'sample':
        rects = None
        if abs(pitch - 0.8) > 1e-6:
            ids, _ = detailed_choice(root, lv, sample_path(root, lv))
            if not ids:
                print(f'{lv}: no detailed sectors')
                return 0
            st = navkit.ensure_settings(navkit.Project(root), lv)
            rects = [[(i + st['xoff']) * 100 - 1, (j + st['zoff']) * 100 - 1, (i + st['xoff']) * 100 + 101,
                      (j + st['zoff']) * 100 + 101] for i, j in ids]
            box = box or [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects),
                          max(r[3] for r in rects)]
        run_level(root, lv, pitch, box, yr, bodies=bodies, rects=rects)
    elif cmd in ('like', 'validate'):
        # validate: several in-game files of one level, the geometry read once; a JSON summary next to the outputs
        files = args[2:]
        geom = hf = None
        summary = {}
        for f in files:
            s, _ = navsample.load(f)
            grid = Grid.like(s)
            na = np.diff(s.start)
            if (na == 0).mean() > 0.5:          # a detailed-zone run: only the columns it sampled
                grid.mask = (na > 0).reshape(grid.nz, grid.nx)
            print(f'== offline sample {lv} on the grid of {os.path.basename(f)}: {grid.nx} x {grid.nz} at {grid.pitch:g}')
            if geom is None:
                geom, hf = load_level(root, lv, bodies=bodies)
            res = sample_grid(grid, geom, hf, bottoms=(float(s.cap_bottom),))
            out = os.path.join(out_dir(root), f'{lv}_like_{os.path.basename(f)}')
            write(out, res[float(s.cap_bottom)])
            print('wrote', out)
            summary[os.path.basename(f)] = compare(s, res[float(s.cap_bottom)])
        if cmd == 'validate':
            p = os.path.join(out_dir(root), f'{lv}_validate.json')
            json.dump(summary, open(p, 'w'), indent=1)
            print('wrote', p)
    elif cmd == 'build':
        build_level(root, lv)
    elif cmd == 'check':
        check_level(root, lv, os.path.join(root, 'work', 'navkit_offline', lv, 'pathdata.hpd'))
    elif cmd == 'all':
        stock_mp(root, [lv])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('navsample_offline', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
