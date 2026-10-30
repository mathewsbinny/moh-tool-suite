"""NavKit: object placements and pathfinding flags of a level, from its extracted data.

Reads an unpacked + XML-converted level (FbrbTool unpack of Dist and Package level-00.fbrb, then
DbxEdit xml on both folders) and writes, next to it:
  objects.json       individually placed objects (Entity.ReferenceObjectData): blueprint, flags, matrix
  group_instances.json  instances of static-model groups / forest / middlegrowth (Physics.CompositeHavokAsset:
                     Assets[i] + Transforms[i]), with blueprint flags
  forbid_tris.npy    world-space collision triangles (N x 3 x 3) of every placed object whose blueprint has
                     ForbidPathfinding = true (from the blueprint's <name>_Havok.res packfile)
  stair_tris.npy     collision of staircases and ramps (blueprint names with stair / ramp / steps): floor that
                     does not block its own users' clearance
  dynamic_tris.npy   the same for dynamic (movable / destructible) objects - DynamicModelEntityData blueprints
                     such as crates and barrels - for objects with DisablePathfinding = PdtWhenAlive (doors), and
                     for barrier arms (a forbidden object that is one thin raised bar): path data treats them as absent

Usage: levelobjects.py <level dir> <level name> [extra blueprint dirs ...]
  e.g. levelobjects.py work\\levels\\sp_01 SP_01 work\\audit\\sp_common work\\audit\\mp_common
"""
import glob
import json
import os
import re
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
import hkpack  # noqa: E402
from mohcore import xmlinst  # noqa: E402

SEP = chr(92)


def positions(level_xml, *types):
    """The Transform translations of the level's instances of the given types (type-name suffixes)."""
    pts = []
    for typ in types:
        for _typ, _guid, body in xmlinst.instances(level_xml, typ):
            t = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
            if t:
                v = [float(a) for a in t.group(1).split('/')]
                if len(v) >= 15 and any(abs(c) > 1e-3 for c in v[12:15]):
                    pts.append(v[12:15])
    return pts


# Medal of Honor MP has no AI orders (no AreaOrder / PatrolOrder entities). The places bots are sent to are its
# objectives: Combat Mission's ObjectiveTriggerEntityData (5 on MP_01 / MP_04), the capture points of Sector Control,
# Hot Zone and Objective Raid (CapturePointEntityData; Combat Mission's are team-base markers some 18 m up, which the
# build ignores - an order point attaches only to ground within 1 m), and the soldiers' spawn points.
OBJECTIVE_TYPES = ('.ObjectiveTriggerEntityData', '.CapturePointEntityData')
SPAWN_TYPES = ('.SoldierSpawnEntityData', '.AlternateSpawnEntityData')


def order_points(level_xml):
    """Positions route nodes are guaranteed at (navbuild.ORDER_NODES): the level's objectives and spawn points. (BFBC2:
    every AreaGeometryEntityData - its AI orders' points; on MOH those are combat-area and spawn-area outline
    vertices, so they are not taken.)"""
    return positions(level_xml, *(OBJECTIVE_TYPES + SPAWN_TYPES))


def area_orders(level_xml):
    """Objective positions for navwalk.OBJECTIVE_FLOOR (the floor under and around an objective on a ForbidPathfinding
    object stays walkable): MOH's objectives (OBJECTIVE_TYPES), and any AreaOrder entity (BFBC2's M-COM targets)."""
    return positions(level_xml, '.AreaOrderEntityData', *OBJECTIVE_TYPES)


def blueprint_flags(bases):
    """lower-case blueprint path -> (ForbidPathfinding, ExcludeFromPathfinding, AllowPathdataSeeds, dynamic,
    DisablePathfinding value - e.g. PdtNever / PdtWhenAlive, NeedsDetailedPathfinding)."""
    flags = {}
    for base in bases:
        for f in glob.glob(os.path.join(base, '**', '*.xml'), recursive=True):
            t = open(f, encoding='utf-8', errors='replace').read()
            dynamic = 'type="GameSharedResources.DynamicModelEntityData"' in t
            if 'ForbidPathfinding' not in t and 'NeedsDetailedPathfinding">true' not in t and not dynamic:
                continue
            rel = os.path.relpath(f, base).replace(SEP, '/').lower()[:-4]
            dis = re.search(r'<field name="DisablePathfinding">([A-Za-z]*)<', t)
            flags.setdefault(rel, ('<field name="ForbidPathfinding">true' in t,
                                   '<field name="ExcludeFromPathfinding">true' in t,
                                   '<field name="AllowPathdataSeeds">true' in t, dynamic, dis.group(1) if dis else '',
                                   '<field name="NeedsDetailedPathfinding">true' in t))
    return flags


def mesh_aliases(bases):
    """lower-case blueprint path -> blueprint path of its Mesh reference (without _mesh), for blueprints that
    reuse another object's mesh and collision (InvWall_SP03 -> invisiblewall/invwall)."""
    out = {}
    for base in bases:
        for f in glob.glob(os.path.join(base, '**', '*.xml'), recursive=True):
            t = open(f, encoding='utf-8', errors='replace').read()
            ref = xmlinst.field_ref(t, 'Mesh')
            if not ref or ref == 'null':
                continue
            rel = os.path.relpath(f, base).replace(SEP, '/').lower()[:-4]
            mesh = ref.rsplit('/', 1)[0].lower()
            out.setdefault(rel, re.sub(r'_mesh$', '', mesh))
    return out


def havok_index(bases):
    out = {}
    for base in bases:
        for f in glob.glob(os.path.join(base, '**', '*_havok.res'), recursive=True):
            rel = os.path.relpath(f, base).replace(SEP, '/').lower()[:-len('_havok.res')]
            out.setdefault(rel, f)
            # some assets name their packfile <name>_Havok_Havok.res (sp_009's animated aircraft door,
            # PdtWhenAlive: without its collision the closed door stayed solid and cut the plane's cabins apart;
            # inv_Wall): the blueprint is <name>
            while rel.endswith('_havok'):
                rel = rel[:-len('_havok')]
                out.setdefault(rel, f)
    return out


def triangles(path):
    """Collision triangles (local space) of a blueprint's Havok asset (storage extended mesh subparts;
    16-bit indices in groups of four: three corners + padding)."""
    try:
        pf = hkpack.load(path)
    except ValueError:
        return None
    tris = []
    for off in pf.objects_of('hkpStorageExtendedMeshShapeMeshSubpartStorage'):
        p = pf.deref(off + 8)
        n = struct.unpack_from('<i', pf.d, off + 12)[0]
        ip = pf.deref(off + 0x14)
        ni = struct.unpack_from('<i', pf.d, off + 0x18)[0]
        if not (p and n > 0 and ip and ni > 0):
            continue
        v = np.frombuffer(pf.d, dtype='<f4', count=n * 4, offset=p).reshape(-1, 4)[:, :3]
        idx = np.frombuffer(pf.d, dtype='<u2', count=ni, offset=ip).reshape(-1, 4)[:, :3]
        if idx.max() < n:
            tris.append(v[idx])
    # convex shapes (boxes, rails, doors): the 12 triangles of each shape's bounding box. Vertices are stored as
    # FourVectors (x0..3, y0..3, z0..3); the first array member of a plausible size holds them.
    for off in pf.objects_of('hkpConvexVerticesShape'):
        for k in range(0x10, 0x60, 4):
            p = pf.deref(off + k)
            if not p:
                continue
            n = struct.unpack_from('<i', pf.d, off + k + 4)[0]
            if 0 < n < 10000:
                fv = np.frombuffer(pf.d, dtype='<f4', count=n * 12, offset=p).reshape(n, 3, 4)
                v = np.stack([fv[:, 0, :].ravel(), fv[:, 1, :].ravel(), fv[:, 2, :].ravel()], 1)
                lo, hi = v.min(0), v.max(0)
                c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
                faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
                         (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
                tris.append(c[np.array(faces)])
                break
    return np.concatenate(tris).astype(np.float32) if tris else None


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    level_dir, level = argv[1], argv[2]
    bases = [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + argv[3:]
    flags = blueprint_flags(bases)
    x = open(os.path.join(level_dir, 'dist', 'levels', level + '.xml'), encoding='utf-8', errors='replace').read()
    objs = []
    for _typ, _guid, body in xmlinst.instances(x, 'Entity.ReferenceObjectData'):
        t = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        r = xmlinst.field_ref(body, 'ReferencedObject')
        if t and r is not None:
            bp = r.rsplit('/', 1)[0].lower()
            objs.append({'bp': bp, 'flags': flags.get(bp), 'm': xmlinst.transform(t.group(1))})
    groups = []
    for f in glob.glob(os.path.join(level_dir, 'dist', 'levels', level, '*_HavokAsset.xml')):
        t = open(f, encoding='utf-8').read()
        tr = re.search(r'<array name="Transforms">([^<]*)<', t)
        if not tr:
            continue
        tr = xmlinst.transform(tr.group(1))          # 16 values per member (denormals' bits read as floats)
        assets = re.findall(r'<item ref="([^"]*)" />', t.split('<array name="Assets">')[1].split('</array>')[0])
        for i, a in enumerate(assets):
            bp = re.sub(r'_havok$', '', a.rsplit('/', 1)[0].lower())
            groups.append({'group': os.path.basename(f), 'bp': bp, 'flags': flags.get(bp),
                           'm': tr[i * 16:(i + 1) * 16]})
    # soldier spawn points (seed points of the path data): MOH places its soldiers at the AlternateSpawnEntityData
    # points (MP_01: 92, 93 % on the terrain); its SoldierSpawnEntityData are the spawn entities (all at one height)
    spawns = positions(x, *SPAWN_TYPES)
    json.dump(spawns, open(os.path.join(level_dir, 'soldier_spawns.json'), 'w'))
    json.dump(order_points(x), open(os.path.join(level_dir, 'order_points.json'), 'w'))
    json.dump(area_orders(x), open(os.path.join(level_dir, 'area_orders.json'), 'w'))
    json.dump(objs, open(os.path.join(level_dir, 'objects.json'), 'w'))
    json.dump(groups, open(os.path.join(level_dir, 'group_instances.json'), 'w'))

    havok = havok_index(bases)
    # blueprints without their own collision asset use the one of the object their Mesh reference names
    for bp, target in mesh_aliases(bases).items():
        if bp not in havok and target in havok:
            havok[bp] = havok[target]
    cache, missing = {}, set()

    def tris_of(o):
        if o['bp'] not in cache:
            cache[o['bp']] = triangles(havok[o['bp']]) if o['bp'] in havok else None
        t = cache[o['bp']]
        if t is None:
            missing.add(o['bp'])
            return None
        m = np.array(o['m']).reshape(4, 4)        # rows: right, up, forward, translation
        return (t @ m[:3, :3] + m[3, :3]).astype(np.float32)
    # dynamic objects, and objects with DisablePathfinding = PdtWhenAlive (doors ...): path data ignores them
    def barrier_arm(o):
        """Whole collision is one thin raised bar (boom barrier): >= 0.7 m above the object's origin, <= 0.5 m
        thick, >= 2 m long. Path data treats it as passable (DICE's routes cross closed checkpoint poles)."""
        if o['bp'] not in havok:
            return False
        if o['bp'] not in cache:
            cache[o['bp']] = triangles(havok[o['bp']])
        t = cache[o['bp']]
        if t is None:
            return False
        v = t.reshape(-1, 3)
        lo, hi = v.min(0), v.max(0)
        return lo[1] >= 0.7 and hi[1] - lo[1] <= 0.5 and max(hi[0] - lo[0], hi[2] - lo[2]) >= 2.0
    dyn = [w for w in (tris_of(o) for o in objs + groups
                       if o['flags'] and ((len(o['flags']) > 3 and o['flags'][3]) or
                                          (len(o['flags']) > 4 and o['flags'][4] == 'PdtWhenAlive') or
                                          (o['flags'][0] and barrier_arm(o)))) if w is not None]
    # staircases and ramps (DICE blueprint names): their own collision is floor, not an obstacle to clearance
    stair_like = re.compile(r'stair|ramp|steps', re.I)
    stairs = [w for w in (tris_of(o) for o in objs + groups if stair_like.search(o['bp'].rsplit('/', 1)[-1]))
              if w is not None]
    np.save(os.path.join(level_dir, 'stair_tris.npy'), np.concatenate(stairs) if stairs else np.zeros((0, 3, 3)))
    print(f'{sum(len(w) for w in stairs)} stair triangles from {len(stairs)} staircases / ramps')
    print('barrier arms:', sorted({o['bp'].split('/')[-1] for o in objs + groups if o['flags'] and o['flags'][0] and barrier_arm(o)}))
    np.save(os.path.join(level_dir, 'dynamic_tris.npy'), np.concatenate(dyn) if dyn else np.zeros((0, 3, 3)))
    print(f'{sum(len(w) for w in dyn)} dynamic-object triangles from {len(dyn)} instances')
    # objects excluded from path finding (fences, sandbags, palm trees): their collision does not block clearance
    # (added to the dynamic set); only their footprint at ground level - parts reaching within 0.5 m of the
    # object's origin height - stays a barrier (exclude_footprint_tris.npy) instead of the whole object
    excluded = [o for o in objs + groups if o['flags'] and o['flags'][1]]
    ex_all, ex_foot = [], []
    for o in excluded:
        w = tris_of(o)
        if w is None:
            continue
        ex_all.append(w)
        base = o['m'][13]
        ex_foot.append(w[w[:, :, 1].min(1) <= base + 0.5])
    np.save(os.path.join(level_dir, 'exclude_tris.npy'), np.concatenate(ex_all) if ex_all else np.zeros((0, 3, 3)))
    np.save(os.path.join(level_dir, 'exclude_footprint_tris.npy'),
            np.concatenate(ex_foot) if ex_foot else np.zeros((0, 3, 3)))
    print(f'{len(ex_all)} excluded objects: {sum(len(w) for w in ex_all)} triangles, footprint {sum(len(w) for w in ex_foot)}')
    world = []
    for o in objs + groups:
        if not (o['flags'] and o['flags'][0]) or o['flags'][1]:
            continue
        w = tris_of(o)
        if w is not None:
            world.append(w)
    np.save(os.path.join(level_dir, 'forbid_tris.npy'), np.concatenate(world) if world else np.zeros((0, 3, 3)))
    print(f'{len(objs)} placed objects, {len(groups)} group instances, {len(flags)} blueprints with flags; '
          f'{sum(len(w) for w in world)} forbid triangles from {len(world)} instances; no collision: {sorted(missing)}')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('levelobjects', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
