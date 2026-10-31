"""Sources, build and packaging of a stock level's AI layer.

Inputs, all read from the user's own installs (nothing is shipped):
- the level's level-00 bundles from Project OPFOR's game copy (MOH: MP\\dist\\win32 and its patch layer, from
  mohcore.gamecopy.GameCopy), unpacked into the work folder;
- the donor level (DICE's SP_004_B: AreaOrder / AreaTrigger layouts, the SectorOffsets asset layout);
- sp_common (the AI template chain to pack) and mp_common (kits, PathfindingSettings - only read for checks);
- the path data named in the configuration (DICE's file where one exists for the world, else a NavKit build).

Output, only under <OPFOR>\\work\\stockmp\\<lv>\\:
    src\\dist, src\\patch               the unpacked stock bundles (cache)
    build\\dist, build\\patch           the bundles' folders with the authored level and the added entries
    out\\game\\...                      what an install copies into the game, at the game's own paths:
        MP\\dist\\win32\\levels\\<lv>\\level-00.fbrb, MP\\dist\\win32\\patch\\levels\\<lv>\\level-00.fbrb,
        MP\\Output\\win32\\ai\\levels\\<lv>\\infantry|vehicle\\pathdata.hpd
    build_report.json                   hashes of every input and output (the configuration's too: config_sha256),
                                        counts, the SectorOffsets values
"""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from collections import Counter

import author
import config as cfgmod
import dbxbin
import levelmodel

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
FBRB = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
DBX = os.path.join(SUITE, 'moh-dbx-tools', 'DbxEdit.exe')
NAVKIT = os.path.join(SUITE, 'moh-auto-navkit')
sys.path[:0] = [NAVKIT, os.path.join(NAVKIT, 'tools'), os.path.join(SUITE, 'core')]

from mohcore import gamecopy  # noqa: E402  (the game copy's layout)

DONOR_LEVEL = 'SP_004_B'
MIN_AGREEMENT = 0.75          # as apply_sector_offsets.py
SECTOR_NS = 'opfor:sectoroffsets'
KINDS = (('Infantry', 'infantry'), ('Vehicle', 'vehicle'))


class BuildError(Exception):
    pass


class NoLevelBundle(BuildError):
    """The level has no level-00 bundle of its own (or is not a level of the game copy)."""


def run(*args, ok=(0,)):
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, errors='replace')
    if r.returncode not in ok:
        raise BuildError(f'failed: {" ".join(map(str, args))}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}')
    return r.stdout


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


class Paths:
    """variant: a second build of the same level next to the first (e.g. 'exp0', the minimal layer of experiment 0):
    <work>\\<lv>-<variant>\\, sharing <work>\\<lv>\\src\\ (the unpacked stock bundles).
    The game paths are MOH's, from mohcore.gamecopy.GameCopy (self.gc): the level's bundles in MP\\dist\\win32 ('dist')
    and its patch layer ('patch': the game loads it instead when there is one), navigation data under MP\\Output\\win32.
    game: the game copy folder (<OPFOR>\\game; default the OPFOR folder's)."""

    def __init__(self, lv, opfor=None, work=None, variant=None, game=None):
        if opfor is None:
            import navkit
            opfor = navkit.find_opfor(None)
        self.opfor = opfor
        self.lv = lv.lower()
        self.gc = gamecopy.GameCopy(os.path.dirname(os.path.abspath(game)) if game else opfor)
        self.game = self.gc.game
        self.work = work or os.path.join(opfor, 'work', 'stockmp')
        self.variant = variant
        self.root = os.path.join(self.work, self.lv + (f'-{variant}' if variant else ''))
        self.cache = os.path.join(self.work, '_cache')
        self.common = os.path.join(opfor, 'work', 'navkit', 'common')

    def src(self, variant):
        return os.path.join(self.work, self.lv, 'src', variant)

    def layers(self):
        """(variant, folder) of the game's data layers, lowest first: the base and its patch (GameCopy.layers)."""
        return (('dist', self.gc.dist), ('patch', self.gc.patch))

    def bundle(self, variant):
        """The game path of the level's level-00 bundle in one layer (the level folder as the game copy spells it)."""
        return os.path.join(dict(self.layers())[variant], 'levels', self.gc.levels().get(self.lv, self.lv), 'level-00.fbrb')

    def bundles(self):
        """(variant, game bundle path) of the level's level-00 bundles that exist, lowest first - the last is the one
        the game loads. [] for a level without a bundle of its own (a DLC mode loaded from its map's common_mp_<nn>)."""
        return [(v, self.bundle(v)) for v, _top in self.layers() if os.path.isfile(self.bundle(v))]

    def loaded_bundle(self):
        """(variant, path) of the bundle the game loads for the level; a BuildError saying why when there is none."""
        found = self.bundles()
        if found:
            return found[-1]
        where = ' or '.join(os.path.join(top, 'levels') for _v, top in self.layers())
        if self.lv in self.gc.levels():
            why = (f'the game loads it from {", ".join(self.gc.commons(self.lv))} only (a mode without a level bundle of '
                   'its own), and the AI layer goes into the level\'s own partition')
        else:
            why = 'it is not a level of this game copy'
        raise NoLevelBundle(f'{self.lv}: no {self.lv}\\level-00.fbrb in {where} - {why}')

    def _out(self, game_path):
        """Where a game file's new copy is staged: out\\game\\ mirrors the game folder."""
        return os.path.join(self.root, 'out', 'game', os.path.relpath(game_path, self.game))

    def out_bundle(self, variant):
        return self._out(self.bundle(variant))

    def out_pathdata(self, kind):
        return self._out(self.gc.pathdata(self.lv, kind))

    def config(self):
        return os.path.join(self.root, 'config.json')


def unpacked(bundle, d):
    """A cached unpack of a bundle (re-unpacked when the bundle's hash changed)."""
    stamp = os.path.join(d, '.source.sha256')
    h = sha(bundle)
    if os.path.isfile(os.path.join(d, 'fbrb.manifest')) and os.path.isfile(stamp) and open(stamp).read().strip() == h:
        return d
    shutil.rmtree(d, ignore_errors=True)
    run(FBRB, 'unpack', bundle, d)
    open(stamp, 'w').write(h)
    return d


def level_entry(d, lv_upper=None):
    """The level partition's entry path in an unpacked bundle (Levels/<LV>.dbx), or None."""
    for line in open(os.path.join(d, 'fbrb.manifest'), encoding='utf-8').read().splitlines():
        name = line.split('|', 1)[0]
        parts = name.replace('\\', '/').split('/')
        if len(parts) == 2 and parts[0].lower() == 'levels' and parts[1].lower() == (lv_upper or '').lower() + '.dbx' \
                and '|*deleted*|' not in line:
            return name
    return None


def find_entry(folder, name):
    p = os.path.join(folder, *name.split('/'))
    return p if os.path.isfile(p) else None


def donor(paths):
    """DICE's SP_004_B level partition and its SectorOffsets assets (from the prepared copy, else the game)."""
    for base in (os.path.join(paths.opfor, 'work', 'levels', 'sp_004_b', v) for v in ('pkg', 'dist')):
        lvl = os.path.join(base, 'levels', 'SP_004_B.dbx')
        inf = os.path.join(base, 'levels', 'sp_004_b', 'Precomputed', 'Infantry', 'SectorOffsets.dbx')
        if os.path.isfile(lvl) and os.path.isfile(inf):
            return lvl, inf
    found = paths.gc.level_bundles('sp_004_b')
    if not found:
        raise BuildError('the donor level SP_004_B was not found: not in work\\levels\\sp_004_b and not in the game copy '
                         f'({paths.gc.dist}) - MOH\'s multiplayer game has no campaign level; the layer\'s layouts '
                         'come from BFBC2\'s SP_004_B')
    d = unpacked(found[-1], os.path.join(paths.cache, 'sp_004_b'))
    lvl = find_entry(d, 'Levels/SP_004_B.dbx')
    inf = find_entry(d, 'Levels/SP_004_B/Precomputed/Infantry/SectorOffsets.dbx')
    if not (lvl and inf):
        raise BuildError('the donor level SP_004_B or its SectorOffsets asset was not found')
    return lvl, inf


def common(paths, name):
    d = os.path.join(paths.common, name)
    if os.path.isfile(os.path.join(d, 'fbrb.manifest')):
        return d
    found = paths.gc.level_bundles(name)          # lowest first: the patch's copy, when there is one, is the game's
    if not found:
        raise BuildError(f'{name} not found: no levels\\{name}\\level-00.fbrb in the game copy ({paths.gc.dist}) and no '
                         f'unpacked copy in {d} (MOH\'s shared bundles are common_mp and common_mp_<nn>)')
    return unpacked(found[-1], os.path.join(paths.cache, name))


def kit_refs(paths):
    """'us/assault' -> 'objects/soldiers/us/assault/<SoldierAsset guid>' from mp_common."""
    mp = common(paths, 'mp_common')
    out = {}
    for fac in ('US', 'RU'):
        for kit in cfgmod.KIT_NAMES:
            p = os.path.join(mp, 'Objects', 'Soldiers', fac, kit.capitalize() + '.dbx')
            if not os.path.isfile(p):
                raise BuildError(f'mp_common has no {fac}/{kit} kit ({p})')
            d = dbxbin.read(p)
            asset = [e for e in d.instances() if (e.attr('type') or '').endswith('.SoldierAsset')]
            if len(asset) != 1:
                raise BuildError(f'{p}: expected one SoldierAsset')
            out[f'{fac.lower()}/{kit}'] = f'objects/soldiers/{fac.lower()}/{kit}/{asset[0].attr("guid").lower()}'
    return out


def sector_offsets(hpd):
    """(x, z) and agreement from a path-data file's own sector ids (apply_sector_offsets.py's rule)."""
    import hpdtool
    cx, cz, n = Counter(), Counter(), 0
    for s in hpdtool.parse(open(hpd, 'rb').read()).sectors:
        b = s.bounds
        cx[math.floor((b[0] + b[1]) / 200) - (s.id >> 8)] += 1
        cz[math.floor((b[4] + b[5]) / 200) - (s.id & 0xff)] += 1
        n += 1
    (x, nx), (z, nz) = cx.most_common(1)[0], cz.most_common(1)[0]
    if min(nx, nz) / n < MIN_AGREEMENT:
        raise BuildError(f'{hpd}: sector ids disagree ({dict(cx)} / {dict(cz)})')
    return (x, z), (nx / n, nz / n)


def sector_asset(ref_path, level_upper, kind, lv, xz):
    """A PathdataSectorOffsets partition (bytes) for the level, from the donor asset's layout (dbxbin keeps its integer
    type); guids as apply_sector_offsets.py makes them."""
    d = dbxbin.read(ref_path)
    inst = d.instances()
    if len(inst) != 1 or not (inst[0].attr('type') or '').endswith('.PathdataSectorOffsets'):
        raise BuildError(f'{ref_path} is not a PathdataSectorOffsets asset')
    e = inst[0]
    part_g = dbxbin.new_guid(SECTOR_NS, lv, kind.lower(), 'partition')
    inst_g = dbxbin.new_guid(SECTOR_NS, lv, kind.lower(), 'instance')
    d.set_attr(d.partition, 'guid', part_g)
    d.set_attr(d.partition, 'primaryInstance', inst_g)
    d.set_attr(e, 'guid', inst_g)
    d.set_text(e.child('Name'), f'Levels/{level_upper}/Precomputed/{kind}/SectorOffsets')
    for f, v in (('XOffset', xz[0]), ('ZOffset', xz[1])):
        c = e.child(f)
        size, old = c.numbers()
        if size != 4 or len(old) != 1:
            raise BuildError(f'{ref_path}: {f} is not one 4-byte number')
        d.set_numbers(c, [int(v)], 'i')
    return dbxbin.write(d), part_g, inst_g


def template_parts(paths, parts):
    """[(entry name, file, manifest line)] of the AI template partitions, from sp_common."""
    sp = common(paths, 'sp_common')
    lines = open(os.path.join(sp, 'fbrb.manifest'), encoding='utf-8').read().splitlines()
    out = []
    for p in parts:
        hit = [l for l in lines if l.split('|', 1)[0].lower() == p.lower() and '|*deleted*|' not in l]
        f = find_entry(sp, hit[0].split('|', 1)[0]) if hit else None
        if not f:
            raise BuildError(f'{p} is not in sp_common')
        out.append((hit[0].split('|', 1)[0], f, hit[0]))
    return out


def loads_sp_common(paths):
    """Whether the level's level-00 bundle loads sp_common (package.mft's load set, GameCopy.manifest; the patch's
    copy counts), i.e. the template resolves without packing."""
    targets = paths.gc.manifest().get(f'levels/{paths.lv}/level-00.fbrb', [])
    return any(t.split('/')[-2:] == ['sp_common', 'level-00.fbrb'] for t in targets)


def config_sha(c):
    """sha256 of a configuration's content (canonical JSON: key order and layout do not count), as build_report.json
    records it - validate checks a build against the configuration it is given."""
    return hashlib.sha256(json.dumps(c, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def generate(paths, **kw):
    """Write a first configuration for the level (config.json) and return it."""
    v, b = paths.loaded_bundle()                   # the patch's copy when there is one (the game loads it instead)
    d = unpacked(b, paths.src(v))
    entry = level_entry(d, _level_name(d, paths.lv))
    m = levelmodel.Model(dbxbin.read(find_entry(d, entry)))
    pd = kw.pop('pathdata')
    c = cfgmod.generate(m, paths.lv, kit_refs(paths), pd, **kw)
    cfgmod.save(c, paths.config())
    return c


def _level_name(d, lv):
    for line in open(os.path.join(d, 'fbrb.manifest'), encoding='utf-8').read().splitlines():
        name = line.split('|', 1)[0].replace('\\', '/')
        parts = name.split('/')
        if len(parts) == 2 and parts[0].lower() == 'levels' and parts[1].lower() == lv + '.dbx' and '|*deleted*|' not in line:
            return parts[1][:-4]
    raise BuildError(f'no Levels/{lv}.dbx in {d}')


def build(paths, c, log=print):
    """Author the level in every level-00 bundle of the level, pack, stage the path data. Returns the report."""
    if cfgmod.check(c):
        raise BuildError('configuration: ' + '; '.join(cfgmod.check(c)))
    paths.loaded_bundle()                          # a level without a bundle of its own: say so before any work
    rep = {'lv': paths.lv, 'level': c['level'], 'layer': c.get('layer', 'full'), 'config_sha256': config_sha(c),
           'inputs': {}, 'outputs': {}, 'variants': {}, 'sector_offsets': {}}
    donor_lvl, donor_so = donor(paths)
    rep['inputs']['donor'] = {'path': donor_lvl, 'sha256': sha(donor_lvl)}
    pd = c.get('pathdata') or {}
    so = {}
    for kind, folder in KINDS:
        src = pd.get(folder)
        if not src or not os.path.isfile(src):
            raise BuildError(f'path data for {folder} not found: {src}')
        xz, agree = sector_offsets(src)
        so[kind] = xz
        rep['sector_offsets'][kind] = {'x': xz[0], 'z': xz[1], 'agree': [round(a, 3) for a in agree]}
        rep['inputs'][f'pathdata_{folder}'] = {'path': src, 'sha256': sha(src)}
        dst = paths.out_pathdata(folder)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        rep['outputs'][os.path.relpath(dst, paths.root)] = sha(dst)
    pack = not loads_sp_common(paths)
    parts = template_parts(paths, c.get('template_parts') or cfgmod.TEMPLATE_PARTS) if pack and c.get('layer', 'full') != 'minimal' else []
    rep['template_pack'] = [p for p, _f, _l in parts]
    for v, bundle in paths.bundles():
        src = unpacked(bundle, paths.src(v))
        rep['inputs'][f'bundle_{v}'] = {'path': bundle, 'sha256': sha(bundle)}
        lv_upper = _level_name(src, paths.lv)
        entry = level_entry(src, lv_upper)
        dst = os.path.join(paths.root, 'build', v)
        log(f'{v}: copying the unpacked bundle to {os.path.relpath(dst, paths.opfor)}')
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns('.source.sha256'))
        lvl_file = find_entry(dst, entry)
        original = open(lvl_file, 'rb').read()
        d = dbxbin.parse(original)
        if dbxbin.write(d) != original:
            raise BuildError(f'{entry}: the DBX writer does not reproduce the original - stopping')
        a = author.Author(d, dbxbin.read(donor_lvl), c, levelmodel.Model(d))
        counts = a.build()
        data = dbxbin.write(d)
        shutil.copyfile(lvl_file, os.path.join(paths.root, 'build', f'{lv_upper}_{v}.original.dbx'))
        open(lvl_file, 'wb').write(data)
        shutil.copyfile(lvl_file, os.path.join(paths.root, 'build', f'{lv_upper}_{v}.authored.dbx'))
        added = []
        for kind, _folder in KINDS:
            ref = os.path.join(os.path.dirname(os.path.dirname(donor_so)), kind, 'SectorOffsets.dbx')
            raw, pg, ig = sector_asset(ref, lv_upper, kind, paths.lv, so[kind])
            name = f'Levels/{lv_upper}/Precomputed/{kind}/SectorOffsets.dbx'
            f = os.path.join(paths.root, 'build', f'SectorOffsets_{kind}.dbx')
            open(f, 'wb').write(raw)
            added.append((name, f, f'{name}|<non-resource>|00010000|{len(raw)}'))
        for p, f, line in parts:
            added.append((p, f, line))
        _add_entries(dst, added)
        out = paths.out_bundle(v)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        run(FBRB, 'pack', dst, out)
        rep['variants'][v] = {'entry': entry, 'authored': counts, 'added_entries': [n for n, _f, _l in added],
                              'level_sha256_before': hashlib.sha256(original).hexdigest(),
                              'level_sha256_after': hashlib.sha256(data).hexdigest()}
        rep['outputs'][os.path.relpath(out, paths.root)] = sha(out)
        log(f'{v}: {counts}; packed {os.path.relpath(out, paths.opfor)}')
    rep['guids'] = {'note': 'deterministic: UUIDv5(NAMESPACE_URL, "opfor:stockmp:<lv>:<role>:<id>")'}
    with open(os.path.join(paths.root, 'build_report.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, indent=1)
    return rep


def _add_entries(d, added):
    m = os.path.join(d, 'fbrb.manifest')
    text = open(m, encoding='utf-8').read()
    names = {x.split('|', 1)[0].lower() for x in text.splitlines() if '|*deleted*|' not in x}
    for name, f, line in added:
        if name.lower() in names:
            raise BuildError(f'{name} is already in the bundle')
        dst = os.path.join(d, *name.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(f, dst)
        text = text.rstrip('\r\n') + '\n' + line + '\n'
    open(m, 'w', encoding='utf-8', newline='').write(text)
