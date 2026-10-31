"""NavKit driver: one command per step, from a level name to its navigation data (pathdata.hpd, and the RSNV file
OPFOR's bots read).

  python navkit.py levels                     list the game's levels
  python navkit.py prepare MP_05_TDM          unpack + convert the level, extract its objects,
                                              choose the sampling box (its combat areas) and sector offsets
  python navkit.py sample  MP_05_TDM          collision sampling from the level's files (no game launch): two runs at
                                              0.8 m, then two at 0.2 m over the level's detailed zones (if it has any);
                                              --ingame: BFBC2's in-game sampler (not in MOH's OPFOR core)
  python navkit.py build   MP_05_TDM          generate pathdata.hpd (and its cells, pathdata.cells.npz)
  python navkit.py check   MP_05_TDM          score it (self-check: routes between the spawn points and objectives)
  python navkit.py export  MP_05_TDM          write work\\navkit\\<level>\\<level>.rsnv for the navigation runtime
                                              (tools\\navexport.py) and check it (tools\\rsnavcheck.py, when rsnav's
                                              host build is found; it fails as rsnavcheck does: points not placed,
                                              pairs not routed, smoothing failures, C search != Python)
  python navkit.py apply  SP_01 [--navtune file] [--out file] [--install]
                                              build with the edits of the level's navigation side file
                                              (work\\navkit\\<level>\\<level>.navtune.json, made in the
                                              Navigation Studio) into work\\navkit\\<level>\\pathdata.hpd,
                                              traversals into <level>.traversal.json, a report next to it;
                                              --install: then install it
  python navkit.py install SP_01 [--from file]  copy it (or another built file) into the game, with its
                                              traversal annotations (DICE's file is backed up once)
  python navkit.py restore SP_01              put the original file back
  python navkit.py all     SP_01 [--yes]      prepare, sample, build, check, export
  python navkit.py status  SP_01              what has been done for a level
  python navkit.py compare SP_01 [--reference a.hpd] [--candidate b.hpd]
                                              two path-data files on the level's AI positions
                                              (default: the original against NavKit's build)

Options: --box minX,minZ,maxX,maxZ and --y bottom,top (prepare / sample: override the sampling box; sample
         recomputes the sector offsets from it when they came from the box rule),
         --pitch 0.8 / 0.2 (sample --ingame: that pass only; 0.2 = a detailed-zone run over the level's detailed sectors;
         writes <level>_navkit_p02.bin / _hi_p02.bin, the level's settings and its 0.8 m samples untouched),
         --opfor <Project OPFOR folder> (default: OPFOR_ROOT, else "Project OPFOR" next to the toolkit),
         --no-detail (build / apply / all: no D lines or F cells - navbuild.DETAIL_FEATURES off).

NavKit works on Project OPFOR's game copy, reading each level's collision and terrain. FbrbTool / DbxEdit come from
the toolkit (..\\moh-fbrb-tool, ..\\moh-dbx-tools, built with their build.bat) or from OPFOR's tools\\ folder.

Files (in the OPFOR folder): work\\levels\\<level>\\ (unpacked level, objects), work\\navkit\\<level>\\
(settings, pathdata.hpd and its cells, check report, <level>.rsnv), work\\navsample\\<level>_navkit*.bin (samples).
See QUICKSTART.md.
"""
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)   # no console flashes when run from the GUI
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tools'))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'core'))
from mohcore import gamecopy  # noqa: E402  (the game copy's layout)
import mohsoldier  # noqa: E402  (the MOH soldier path data is built for)

DETAILED_BUDGET_MS = 400   # pitch-0.2 runs: ms of sampling per frame - 10x the columns of a normal run, so it must finish
                           # in less game time than a round or a campaign level allows an idle player (150: ONS_MP_002 at
                           # 95 %, SP_006 and SP_007 back at the menu after ~11 min; 400: all 15 levels, 2026-10-22)
AGENTS = [('normal', f'{mohsoldier.RADIUS},{mohsoldier.STEP},{mohsoldier.HEIGHT}', ''),          # radius,stepMax,height
          ('high capsule', f'{mohsoldier.RADIUS},{mohsoldier.HIGH_STEP},{mohsoldier.HEIGHT}', '_hi')]
SAMPLE_DONE = re.compile(r'navsample: (done|no physics|cannot create|empty box|needs \[debug\])')
BACKUP_SUFFIX = '.opfor-orig'
NEW_SUFFIX = '.opfor-new'       # where NavKit installed onto a level that had no path data to back up


def original_pathdata(p):
    """The original of a level's installed path data p: its backup (p.opfor-orig), else p itself unless NavKit put
    it there (p.opfor-new: the level had none), else None."""
    if os.path.isfile(p + BACKUP_SUFFIX):
        return p + BACKUP_SUFFIX
    if os.path.isfile(p) and not os.path.isfile(p + NEW_SUFFIX):
        return p
    return None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


class NavKitError(Exception):
    pass


def find_opfor(given=None):
    """The Project OPFOR: Medal of Honor folder: --opfor, else OPFOR_ROOT, else project-opfor-moh next to the suite
    (core\\mohcore\\gamecopy.py)."""
    try:
        return gamecopy.find_root(given)
    except gamecopy.GameNotFound as e:
        raise NavKitError(str(e)) from None


def find_tool(opfor, exe, toolkit_dir):
    """A toolkit tool: built in the toolkit (..\\<toolkit_dir>\\<exe>), else OPFOR's tools\\ folder."""
    for p in (os.path.join(os.path.dirname(HERE), toolkit_dir, exe), os.path.join(opfor, 'tools', exe)):
        if os.path.isfile(p):
            return p
    raise NavKitError(f'{exe} not found - run build.bat in the toolkit\'s {toolkit_dir} folder')


class Project:
    def __init__(self, root=None):
        self.root = find_opfor(root)
        self.copy = gamecopy.GameCopy(self.root)
        self.game = self.copy.mp          # the MP client's folder (MoHMPGame.exe, dist\\, Output\\)
        self.work = os.path.join(self.root, 'work')
        self.ini = self.copy.ini
        self.log_path = self.copy.log
        self.fbrbtool = find_tool(self.root, 'FbrbTool.exe', 'moh-fbrb-tool')
        self.dbxedit = find_tool(self.root, 'DbxEdit.exe', 'moh-dbx-tools')

    # --- levels --------------------------------------------------------------------------------------
    def level_dirs(self):
        """{level: the folder of its lowest level-00.fbrb} - base levels in dist\\win32\\levels, DLC levels only in
        dist\\win32\\patch\\levels; level_bundles gives every layer."""
        out = {}
        for lv in self.copy.levels():
            own = self.copy.level_bundles(lv) or self.copy.load_set(lv)[-1:]   # mp_03_elimination: its common_mp_03
            out[lv] = os.path.dirname(own[0]) if own else None
        return {lv: d for lv, d in out.items() if d}

    def level_bundles(self, lv, name='level-00.fbrb'):
        return self.copy.level_bundles(lv, name)

    def pathdata(self, lv):
        return self.copy.pathdata(lv)

    def dice_pathdata(self, lv):
        """The level's original path data (the backup if NavKit's file is installed), or None. A file with the
        no-original marker beside it (.opfor-new) is NavKit's own install, not an original (2026-10-31: on MOH it was
        taken as DICE's file - prepare's box and offsets, the build's clip and check all came from NavKit's earlier
        build)."""
        return original_pathdata(self.pathdata(lv))

    def level(self, name):
        lv = name.replace('\\', '/').split('/')[-1].lower()
        dirs = self.level_dirs()
        if lv not in dirs:
            raise NavKitError(f'unknown level {name!r} (see: navkit.py levels)')
        return lv

    def level_work(self, lv):
        return os.path.join(self.work, 'levels', lv)

    def out_dir(self, lv):
        d = os.path.join(self.work, 'navkit', lv)
        os.makedirs(d, exist_ok=True)
        return d

    def settings_path(self, lv):
        return os.path.join(self.out_dir(lv), 'navkit.json')

    def settings(self, lv):
        p = self.settings_path(lv)
        if not os.path.isfile(p):
            raise NavKitError(f'{lv}: not prepared yet (navkit.py prepare {lv.upper()})')
        return json.load(open(p))

    def samples(self, lv, suffix=''):
        return os.path.join(self.work, 'navsample', f'{lv}_navkit{suffix}.bin')

    def samples08(self, lv):
        """The level's 0.8 m samples: <lv>_navkit.bin (navkit.py sample) or <lv>_full.bin (older campaign runs)."""
        for name in (f'{lv}_navkit', f'{lv}_full'):
            q = os.path.join(self.work, 'navsample', name + '.bin')
            if os.path.isfile(q):
                return q
        return None

    def run(self, args, log):
        r = subprocess.run(args, capture_output=True, text=True, errors='replace', creationflags=NO_WINDOW)
        out = (r.stdout + r.stderr).strip().splitlines()
        if r.returncode != 0:
            raise NavKitError(f'{os.path.basename(args[0])} failed: ' + ' | '.join(out[-3:]))
        return out

    def unpack(self, bundle, dest, log):
        stamp = os.path.join(dest, '.source.sha256')
        source_hash = _sha256(bundle)
        if os.path.isfile(os.path.join(dest, 'fbrb.manifest')) and os.path.isfile(stamp) \
                and open(stamp).read().strip() == source_hash:
            log(f'  {os.path.relpath(dest, self.root)} already unpacked')
            return
        log(f'  unpacking {os.path.relpath(bundle, self.game)} ...')
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        self.run([self.fbrbtool, 'unpack', bundle, dest], log)
        log('  converting binary DBX to XML ...')
        log('  ' + self.run([self.dbxedit, 'xml', dest], log)[-1])
        open(stamp, 'w').write(source_hash)


# --- steps -----------------------------------------------------------------------------------------------
def levels(pr, log=print):
    log('level              AI path data   NavKit')
    for lv, d in pr.level_dirs().items():
        ai = 'yes (samplable)' if pr.dice_pathdata(lv) else 'no'
        done = []
        if os.path.isfile(os.path.join(pr.work, 'navkit', lv, 'navkit.json')):
            done.append('prepared')
        if os.path.isfile(os.path.join(pr.work, 'navkit', lv, 'pathdata.hpd')):
            done.append('built')
        if os.path.isfile(pr.pathdata(lv) + BACKUP_SUFFIX) or os.path.isfile(pr.pathdata(lv) + NEW_SUFFIX):
            done.append('INSTALLED')
        log(f'{lv:18s} {ai:14s} {", ".join(done)}')


def level_names(pr):
    """[(level, samplable)] for the GUI."""
    return [(lv, pr.dice_pathdata(lv) is not None) for lv in pr.level_dirs()]


def prepare(pr, lv, log=print, box=None, y=None):
    import levelobjects
    log(f'== prepare {lv}')
    common = []
    for c in pr.copy.commons(lv):
        # the base copy into common\\<c>, the patch's (which replaces entries) into common\\<c>.patch
        for bundle in pr.copy.layers(f'levels/{c}/level-00.fbrb'):
            name = c + ('.patch' if os.path.normcase(bundle).startswith(os.path.normcase(pr.copy.patch)) else '')
            pr.unpack(bundle, os.path.join(pr.work, 'navkit', 'common', name), log)
    common = common_dirs(pr.root, lv)
    L = pr.level_work(lv)
    unpack_level(pr, lv, L, log, common)
    LV = level_partition_name(L, lv, common)
    log('  extracting objects, collision and spawn points ...')
    levelobjects.main(['levelobjects.py', L, LV] + common)
    import ladders
    lad = ladders.extract(L, common)
    json.dump(lad, open(os.path.join(L, 'ladders.json'), 'w'), indent=1)
    log(f'  ladders: {len(lad)} (ladder links: their far ends seeded and linked in the build)')

    xoff = zoff = None
    so = glob.glob(os.path.join(L, 'dist', 'levels', lv, 'Precomputed', 'Infantry', 'SectorOffsets.xml'))
    if so:
        t = open(so[0], encoding='utf-8').read()
        xoff = int(re.search(r'name="XOffset">(-?\d+)<', t).group(1))
        zoff = int(re.search(r'name="ZOffset">(-?\d+)<', t).group(1))
    auto_box, auto_y, source = sampling_box(pr, lv, L)
    s = {'level': LV, 'box': box or auto_box, 'y': y or auto_y, 'box_source': 'given' if box else source}
    if xoff is None:
        xoff, zoff, s['offsets_source'] = rule_offsets(pr, lv, s['box'])
    else:
        s['offsets_source'] = 'SectorOffsets'
    s['xoff'], s['zoff'] = xoff, zoff
    json.dump(s, open(pr.settings_path(lv), 'w'), indent=1)
    cols = (s['box'][2] - s['box'][0]) / 0.8 * (s['box'][3] - s['box'][1]) / 0.8
    log(f'  box {fmt(s["box"])} ({s["box_source"]}), y {fmt(s["y"])}, sector offsets {s["xoff"]}, {s["zoff"]} '
        f'({s["offsets_source"]}); {cols / 1e6:.1f} M columns per sampling run')
    log(f'  settings: {os.path.relpath(pr.settings_path(lv), pr.root)}')


def rule_offsets(pr, lv, box):
    """(xoff, zoff, source) of a level without a SectorOffsets asset (Onslaught and other MP levels): DICE's rule, one
    sector below the path data's lowest extent, so the first sector id is 1,1 - it holds for all 15 shipped
    SectorOffsets (13 campaign, ons_mp_005, ons_mp_008). A new map has no path data; its box (which holds the ground)
    stands in."""
    import navregress
    ext = navregress.reference_bounds(pr.root, lv)
    lo = ext[:2] if ext else box[:2]
    return (int(math.floor(lo[0] / 100.0)) - 1, int(math.floor(lo[1] / 100.0)) - 1,
            'rule: path data extent' if ext else 'rule: box')


def set_box(pr, lv, s, box=None, y=None):
    """A new sampling box / heights (--box / --y) into the level's settings, the sector offsets recomputed with
    prepare's rule when they came from it (2026-10-31: they were kept, so a box reaching below the old sector 0 gave
    sector x -1, masked to 0xFF in the sector ids). A SectorOffsets asset's offsets are the level's own and stay."""
    s['box'], s['y'] = box or s['box'], y or s['y']
    if str(s.get('offsets_source', '')).startswith('rule'):
        s['xoff'], s['zoff'], s['offsets_source'] = rule_offsets(pr, lv, s['box'])
    json.dump(s, open(pr.settings_path(lv), 'w'), indent=1)
    return s


def common_dirs(root, lv=None):
    """The unpacked shared bundles a level loads (work\\navkit\\common\\<c>[.patch]) in the game's order - its map's
    common_mp_<nn>, then common_mp, each the patch's copy first; readers take the first file found. Without a level:
    common_mp alone."""
    out = []
    for c in (gamecopy.GameCopy(root).commons(lv) if lv else ['common_mp']):
        for name in (c + '.patch', c):
            d = os.path.join(root, 'work', 'navkit', 'common', name)
            if os.path.isdir(d):
                out.append(d)
    return out


def unpack_level(pr, lv, L, log, common=()):
    """The level as the game loads it, in L\\dist: its level-00 bundle unpacked there, with the patch's copy (unpacked
    into L\\pkg) laid over it file by file - levelobjects.py and the other readers read L\\dist. A level without a
    bundle of its own (mp_03_elimination, mp_07_koth) gets its files (levels\\<lv>.*, levels\\<lv>\\) from its map's
    common bundles ('common': their unpacked folders, first found wins). Returns the files laid over the base copy
    (listed in L\\pkg_overlay.txt)."""
    layers = pr.level_bundles(lv)
    dist, pkg = os.path.join(L, 'dist'), os.path.join(L, 'pkg')
    os.makedirs(pkg, exist_ok=True)
    if layers:
        pr.unpack(layers[0], dist, log)
        sources = []
        if len(layers) > 1:
            pr.unpack(layers[-1], pkg, log)
            sources = [pkg]
        if not any(_has_partition(d, lv) for d in [dist] + sources):
            # a DLC mode's level-00 bundle holds only resources (mp_01_elimination, mp_02_koth2, mp_04_koth): its
            # partition (levels\<lv>.dbx, levels\<lv>\) is in its map's common_mp_<nn> bundle
            sources += list(common)
        if not sources:
            return []
    else:
        os.makedirs(dist, exist_ok=True)
        sources = list(common)
    from_common = [os.path.normcase(c) for c in common]
    copied, seen = [], set()
    for src in sources:
        top = os.path.join(src, 'levels')
        for dp, _, fs in os.walk(top):
            for f in fs:
                rel = os.path.relpath(os.path.join(dp, f), top)
                first = rel.replace('\\', '/').split('/')[0].lower()
                if os.path.normcase(src) in from_common and not (first == lv or first.startswith(lv + '.') or
                                                                  first.startswith(lv + '_description')):
                    continue                                   # from a common bundle: this level's files only
                if rel.lower() in seen:
                    continue
                seen.add(rel.lower())
                dst = os.path.join(dist, 'levels', rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(dp, f), dst)
                copied.append(rel)
    open(os.path.join(L, 'pkg_overlay.txt'), 'w', encoding='utf-8').write('\n'.join(sorted(copied)) + '\n')
    log(f'  {"patch / common" if layers else "common"} overlay: {len(copied)} level files laid over '
        f'{os.path.relpath(dist, pr.root)}')
    return copied


def _has_partition(d, lv):
    return any(os.path.splitext(os.path.basename(f))[0].lower() == lv
               for f in glob.glob(os.path.join(d, 'levels', '*.*')) if f.lower().endswith(('.dbx', '.xml')))


def level_partition_name(L, lv, common=()):
    for d in [os.path.join(L, 'pkg'), os.path.join(L, 'dist')] + list(common):
        for f in glob.glob(os.path.join(d, 'levels', '*.xml')):
            b = os.path.splitext(os.path.basename(f))[0]
            if b.lower() == lv:
                return b
    raise NavKitError(f'{lv}: level partition levels/{lv}.xml not found in the bundle')


def sampling_box(pr, lv, L):
    """Box (minX, minZ, maxX, maxZ) and y (bottom, top): the original path data's extent plus 20 m if the
    level has path data (none on MOH), else its combat areas plus navsample_offline.COMBAT_MARGIN (every MOH MP level:
    90-430 m across), else the extent of its placed objects and spawn points plus 50 m. y: the objects and spawns in
    the box, 30 m below to 150 m above."""
    pts = []
    ref = pr.dice_pathdata(lv)
    if ref:
        import hpdtool
        import kymesh
        f = hpdtool.parse(open(ref, 'rb').read())
        for s in f.sectors:
            for a in kymesh.parse_mesh(s.mesh).areas:
                for line in a.outline:
                    pts.extend((p[0], p[1], p[2]) for p in line)
        margin, source = 20.0, 'extent of the level\'s path data + 20 m'
    combat = None
    if not pts:
        import navsample_offline
        combat = navsample_offline.combat_box(pr.root, lv)
    if not pts:
        for name in ('objects.json', 'group_instances.json'):
            for o in json.load(open(os.path.join(L, name))):
                pts.append((o['m'][12], o['m'][13], o['m'][14]))
        pts += [tuple(p) for p in json.load(open(os.path.join(L, 'soldier_spawns.json')))]
        margin, source = 50.0, 'extent of the level\'s objects + 50 m'
    if not pts:
        raise NavKitError(f'{lv}: no objects to size the sampling box - pass --box and --y')
    if combat:
        inside = [p for p in pts if combat[0] <= p[0] <= combat[2] and combat[1] <= p[2] <= combat[3]] or pts
        ys = [p[1] for p in inside]
        yr = [math.floor((min(ys) - 30) / 10) * 10, math.ceil((max(ys) + 150) / 10) * 10]
        import navsample_offline
        return combat, yr, f'combat areas + {navsample_offline.COMBAT_MARGIN:g} m'
    xs, ys, zs = zip(*pts)
    box = [math.floor((min(xs) - margin) / 10) * 10, math.floor((min(zs) - margin) / 10) * 10,
           math.ceil((max(xs) + margin) / 10) * 10, math.ceil((max(zs) + margin) / 10) * 10]
    yr = [math.floor((min(ys) - 30) / 10) * 10, math.ceil((max(ys) + 150) / 10) * 10]
    return box, yr, source


def fmt(v):
    return ','.join(f'{a:g}' for a in v)


def game_running():
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq MoHMPGame.exe', '/NH'], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    return 'mohmpgame.exe' in r.stdout.lower()


def ini_set(text, section, key, value):
    """Sets key=value in [section] of an ini text (adds the key if missing)."""
    lines = text.split('\n')
    sec, start, end = None, None, len(lines)
    for i, l in enumerate(lines):
        m = re.match(r'\s*\[([^\]]+)\]', l)
        if m:
            if sec == section.lower():
                end = i
                break
            sec = m.group(1).strip().lower()
            if sec == section.lower():
                start = i
    cr = '\r' if '\r\n' in text else ''   # keep the file's line endings
    if start is None:
        return text.rstrip('\r\n') + f'{cr}\n{cr}\n[{section}]{cr}\n{key}={value}{cr}\n'
    for i in range(start + 1, end):
        if re.match(rf'\s*{re.escape(key)}\s*=', lines[i]):
            lines[i] = f'{key}={value}{cr}'
            return '\n'.join(lines)
    lines.insert(start + 1, f'{key}={value}{cr}')
    return '\n'.join(lines)


def ini_backup(pr):
    return os.path.join(pr.work, 'navkit', 'opfor.ini.backup')


def recover_ini(pr, log=print):
    """A sampling run that was killed leaves opfor.ini changed: put the saved copy back."""
    b = ini_backup(pr)
    if os.path.isfile(b):
        shutil.copyfile(b, pr.ini)
        os.remove(b)
        log('  restored opfor.ini from the copy an interrupted sampling run left behind')


def ensure_settings(pr, lv):
    """The level's settings (navkit.json); a level sampled before navkit.json existed gets one from what is there: its
    name (the converted level partition), box and heights (its 0.8 m samples), sector offsets (its SectorOffsets)."""
    if os.path.isfile(pr.settings_path(lv)):
        return pr.settings(lv)
    import navsample
    L = pr.level_work(lv)
    sp = pr.samples08(lv)
    so = glob.glob(os.path.join(L, 'dist', 'levels', '*', 'Precomputed', 'Infantry', 'SectorOffsets.xml'))
    if not sp or not so:
        raise NavKitError(f'{lv}: not prepared yet (navkit.py prepare {lv.upper()})')
    t = open(so[0], encoding='utf-8').read()
    s, _ = navsample.load(sp)
    name = level_partition_name(L, lv)
    st = {'level': name, 'box': [round(s.min_x, 3), round(s.min_z, 3), round(s.min_x + (s.nx - 1) * s.pitch, 3),
                                 round(s.min_z + (s.nz - 1) * s.pitch, 3)],
          'y': [round(s.y_bottom, 3), round(s.y_top, 3)], 'box_source': 'its 0.8 m samples',
          'offsets_source': 'its SectorOffsets', 'xoff': int(re.search(r'name="XOffset">(-?\d+)<', t).group(1)),
          'zoff': int(re.search(r'name="ZOffset">(-?\d+)<', t).group(1))}
    json.dump(st, open(pr.settings_path(lv), 'w'), indent=1)
    return st


def detailed_sectors(pr, lv):
    """The sectors to build as detailed zones (pitch 0.2), as (grid x, grid z) - chosen from the level alone
    (tools\\detailed.py: flagged objects, stacked floors in the 0.8 m samples); cached in work\\navkit\\<lv>\\
    detailed.json while the samples are the same."""
    import detailed
    s = ensure_settings(pr, lv)
    sp = pr.samples08(lv)
    cache = os.path.join(pr.out_dir(lv), 'detailed.json')
    key = f'{os.path.basename(sp) if sp else None}|{os.path.getmtime(sp) if sp else 0}|{detailed.MARGIN}|{detailed.STACKED_MIN}'
    if os.path.isfile(cache):
        c = json.load(open(cache))
        if c.get('key') == key:
            return [tuple(x) for x in c['sectors']]
    common = common_dirs(pr.root, lv)
    chosen, why = detailed.choose(pr.level_work(lv), common, sp, s['xoff'], s['zoff'])
    json.dump({'key': key, 'sectors': chosen, 'why': why}, open(cache, 'w'))
    return chosen


def detailed_rects(pr, lv, margin=1.0):
    """The detailed sectors' squares plus a margin (the sampling window's overlap), for navsample_rects."""
    s = ensure_settings(pr, lv)
    return [[(i + s['xoff']) * 100 - margin, (j + s['zoff']) * 100 - margin, (i + s['xoff']) * 100 + 100 + margin,
             (j + s['zoff']) * 100 + 100 + margin] for i, j in detailed_sectors(pr, lv)]


def dice_detailed_sectors(pr, lv):
    """The sectors DICE's path data builds as detailed zones (pitch 0.2), as (grid x, grid z) ids; empty without it.
    For scoring detailed_sectors only - NavKit does not choose from it."""
    import hpdtool
    import kymesh
    p = pr.dice_pathdata(lv)
    if not p:
        return []
    return sorted((sec.id >> 8, sec.id & 0xff) for sec in hpdtool.parse(open(p, 'rb').read()).sectors
                  if abs(kymesh.parse_mesh(sec.mesh).pitch - 0.2) < 1e-3)


def detailed_box(pr, lv, margin=1.0):
    """The box around the detailed sectors (their 100 m squares plus a margin), for a pitch-0.2 sampling run."""
    s = ensure_settings(pr, lv)
    ids = detailed_sectors(pr, lv)
    if not ids:
        raise NavKitError(f'{lv}: no detailed sectors (no flagged objects or stacked floors) - nothing to sample at 0.2')
    xs, zs = [(i + s['xoff']) * 100 for i, _ in ids], [(j + s['zoff']) * 100 for _, j in ids]
    return [min(xs) - margin, min(zs) - margin, max(xs) + 100 + margin, max(zs) + 100 + margin]


def pitch_suffix(pitch):
    """Sample-file suffix of a pitch: '' for the normal 0.8, '_p02' for 0.2."""
    return '' if abs(pitch - 0.8) < 1e-6 else f'_p{int(round(pitch * 10)):02d}'


def sample(pr, lv, log=print, confirm=None, box=None, y=None, timeout=3600, pitch=0.8, budget_ms=None):
    s = ensure_settings(pr, lv)
    if abs(pitch - 0.8) > 1e-6:
        # a detailed-zone run: its own box (the detailed sectors by default) and only their squares sampled
        # (navsample_rects); the level's settings stay as they are
        s = dict(s, box=box or detailed_box(pr, lv), y=y or s['y'], rects=None if box else detailed_rects(pr, lv))
    elif box or y:
        set_box(pr, lv, s, box, y)
    log(f'== sample {lv}: box {fmt(s["box"])}, y {fmt(s["y"])}, pitch {pitch}')
    if not pr.dice_pathdata(lv):
        log('  warning: this level has no AI path data, so its AI (and the sampler) may not start - see QUICKSTART.md')
    if game_running():
        raise NavKitError('the game is running - close it first')
    if confirm and not confirm(f'NavKit will start the game twice to sample {lv.upper()} '
                               '(about 1-5 minutes each; the screen may stay black). Continue?'):
        log('  cancelled')
        return False
    recover_ini(pr, log)
    os.makedirs(os.path.join(pr.work, 'navsample'), exist_ok=True)
    ps = pitch_suffix(pitch)
    for label, agent, suffix in AGENTS:
        log(f'  run {label} ({agent}) ...')
        out = pr.samples(lv, suffix + ps)
        if os.path.isfile(out):
            os.remove(out)
        result = sample_run(pr, s, agent, os.path.basename(out)[:-4], log, timeout, pitch, budget_ms)
        if not os.path.isfile(out) or 'done' not in result:
            raise NavKitError(f'sampling did not finish: {result or "no result in the log"}')
    log('  samples: ' + ', '.join(os.path.relpath(pr.samples(lv, a[2] + ps), pr.root) for a in AGENTS))
    return True


def sample_level(pr, lv, log=print, confirm=None, box=None, y=None):
    """The level's sampling, offline (tools\\navsample_offline.py: the level's Havok collision and height field, no game
    launch - MOH's OPFOR core has no in-game sampler): the normal and high-capsule runs at 0.8 m, then - when the level
    has detailed sectors (stacked floors in the 0.8 m samples) - the same two at 0.2 m over those sectors. The files go
    where build reads them (work\\navsample\\<lv>_navkit*.bin)."""
    import navsample_offline
    if box or y:
        set_box(pr, lv, ensure_settings(pr, lv), box, y)
    out = navsample_offline.sample_level(pr.root, lv, log=log)
    os.makedirs(os.path.join(pr.work, 'navsample'), exist_ok=True)
    for suf in ('_p02', '_hi_p02'):                          # a stale 0.2 m pair would be built in
        if os.path.isfile(pr.samples(lv, suf)):
            os.remove(pr.samples(lv, suf))
    for p in out['p08'] + (out['p02'] or []):
        shutil.copyfile(p, os.path.join(pr.work, 'navsample', os.path.basename(p)))
    log(f'  samples in {os.path.relpath(os.path.join(pr.work, "navsample"), pr.root)}: 0.8 m pair'
        + (f', 0.2 m pair over {len(out["detailed"])} detailed sectors' if out['p02'] else ''))
    return True


def sample_level_ingame(pr, lv, log=print, confirm=None, box=None, y=None):
    """BFBC2's route (kept for an OPFOR core with the [dev] navsample sampler; MOH's has none): the in-game runs, the
    normal and high-capsule runs at 0.8 m, then 0.2 m over the detailed sectors. One confirmation for all launches."""
    if confirm and not confirm(f'NavKit will start the game up to four times to sample {lv.upper()} (two 0.8 m runs, then '
                               'two 0.2 m runs over its detailed zones; about 1-15 minutes each; the screen may stay '
                               'black). Continue?'):
        log('  cancelled')
        return False
    if not sample(pr, lv, log=log, box=box, y=y):
        return False
    ids = detailed_sectors(pr, lv)
    if not ids:
        log(f'  {lv}: no detailed sectors - no 0.2 m runs')
        return True
    log(f'  {lv}: {len(ids)} detailed sectors - 0.2 m runs')
    return sample(pr, lv, log=log, pitch=0.2)


def sample_run(pr, s, agent, name, log, timeout, pitch=0.8, budget_ms=None):
    orig = open(pr.ini, 'rb').read()
    shutil.copyfile(pr.ini, ini_backup(pr))
    log_start = os.path.getsize(pr.log_path) if os.path.isfile(pr.log_path) else 0
    t = orig.decode('utf-8', errors='replace')
    for sec, k, v in (('dev', 'level', 'Levels/' + s['level']), ('dev', 'autostart', '1'), ('dev', 'navsample', '1'),
                      ('dev', 'navsample_name', name), ('dev', 'navsample_box', fmt(s['box'])),
                      ('dev', 'navsample_y', fmt(s['y'])), ('dev', 'navsample_pitch', fmt([pitch])),
                      ('dev', 'navsample_offset', '0'), ('dev', 'navsample_agent', agent), ('debug', 'probes', '1'),
                      ('dev', 'navsample_rects', ';'.join(fmt(r) for r in (s.get('rects') or ())))) + (
            # a pitch-0.2 run is 10x the columns: a larger time budget per frame, so it finishes before a round with
            # working bots (or a campaign level's idle player: SP_006 back at the menu at 60 %, 2026-10-22) can end;
            # budget_ms overrides it (a retry at a larger budget: less game time for the same columns)
            # working bots can end (ONS_MP_002 at 40 ms: back to the menu at 95 %, 2026-10-21); normal runs keep the ini's
            (('dev', 'navsample_budget_ms', str(budget_ms)),) if budget_ms else
            () if abs(pitch - 0.8) < 1e-6 else (('dev', 'navsample_budget_ms', str(DETAILED_BUDGET_MS)),)):
        t = ini_set(t, sec, k, v)
    result = ''
    try:
        open(pr.ini, 'wb').write(t.encode('utf-8'))
        subprocess.Popen([os.path.join(pr.root, 'OPFOR.exe')], cwd=pr.root)
        t0 = time.time()
        last = ''
        time.sleep(15)
        while time.time() - t0 < timeout:
            time.sleep(5)
            new = read_log(pr, log_start)
            prog = [l for l in new if 'navsample:' in l]
            if prog and prog[-1] != last:
                last = prog[-1]
                log('    ' + last.split('navsample:', 1)[1].strip())
            m = [SAMPLE_DONE.search(l) for l in new]
            m = [x for x in m if x]
            if m:
                result = m[-1].group(1)
                break
            if not game_running():
                result = 'the game exited'
                break
            # back at the main menu after sampling began: the level ended (a round over, a campaign level failed)
            first = next((i for i, l in enumerate(new) if 'navsample: sampling' in l), None)   # not 'armed': before the menu
            if first is not None and any('UI cmd [main menu]' in l for l in new[first:]):
                result = 'the level ended (back at the main menu) - retry with a larger --budget'
                break
        else:
            result = 'timed out'
    finally:
        close_game()
        open(pr.ini, 'wb').write(orig)
        os.remove(ini_backup(pr))
    return result


def read_log(pr, start):
    with open(pr.log_path, 'rb') as f:
        f.seek(start)
        return f.read().decode('utf-8', errors='replace').splitlines()


def close_game():
    if not game_running():
        return
    subprocess.run(['taskkill', '/IM', 'MoHMPGame.exe'], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(10):
        time.sleep(1)
        if not game_running():
            return
    subprocess.run(['taskkill', '/F', '/IM', 'MoHMPGame.exe'], capture_output=True, creationflags=NO_WINDOW)
    time.sleep(2)


def build_inputs(pr, lv, log=print):
    """(samples, forbid, xoff, zoff, keyword arguments) of navbuild.build for a level: one place, so that build and
    apply (a build with walkability edits) always pass the same inputs - detailed zones and ladders included."""
    import navregress
    s = pr.settings(lv)
    L = pr.level_work(lv)
    s08 = pr.samples08(lv)                      # <lv>_navkit.bin, or <lv>_full.bin of older campaign runs
    if not s08 or not os.path.isfile(s08[:-4] + '_hi.bin'):
        raise NavKitError(f'{lv}: samples missing ({os.path.basename(pr.samples(lv))} and its _hi run) - run sample first')
    detailed = None
    if os.path.isfile(pr.samples(lv, '_p02')):
        # the detailed-zone pass: DICE's pitch-0.2 sectors built from the level's pitch-0.2 samples (sample --pitch 0.2)
        hi02 = pr.samples(lv, '_hi_p02')
        detailed = dict(samples=pr.samples(lv, '_p02'), high=hi02 if os.path.isfile(hi02) else None,
                        sectors=set(detailed_sectors(pr, lv)))
        log(f'  detailed zones: {len(detailed["sectors"])} sectors at pitch 0.2'
            + ('' if detailed['high'] else ' (no high-capsule run at 0.2 - stairs use the normal capsule there)'))
    kw = dict(detailed=detailed,
              dynamic_path=os.path.join(L, 'dynamic_tris.npy'), high_path=s08[:-4] + '_hi.bin',
              stairs_path=os.path.join(L, 'stair_tris.npy'),
              seeds=json.load(open(os.path.join(L, 'soldier_spawns.json'))),
              water=navregress.level_water(pr.root, lv), bounds=navregress.reference_bounds(pr.root, lv),
              order_points=navregress.order_points_of(pr.root, lv), area_orders=navregress.area_orders_of(pr.root, lv),
              ladders=json.load(open(os.path.join(L, 'ladders.json'))) if os.path.isfile(os.path.join(L, 'ladders.json')) else None,
              log=lambda *a: log('  ' + ' '.join(str(x) for x in a)))
    return s08, os.path.join(L, 'forbid_tris.npy'), s['xoff'], s['zoff'], kw


def build(pr, lv, log=print):
    import navbuild
    out = os.path.join(pr.out_dir(lv), 'pathdata.hpd')
    log(f'== build {lv} -> {os.path.relpath(out, pr.root)}')
    t0 = time.time()
    samples, forbid, xoff, zoff, kw = build_inputs(pr, lv, log)
    navbuild.build(samples, forbid, xoff, zoff, out, **kw)
    log(f'  built in {time.time() - t0:.0f} s ({os.path.getsize(out) / 1e6:.1f} MB)')


def check(pr, lv, log=print):
    s = pr.settings(lv)
    ours = os.path.join(pr.out_dir(lv), 'pathdata.hpd')
    if not os.path.isfile(ours):
        raise NavKitError(f'{lv}: not built yet')
    xml = os.path.join(pr.level_work(lv), 'dist', 'levels', s['level'] + '.xml')
    ref = pr.dice_pathdata(lv)
    args = [sys.executable, os.path.join(HERE, 'tools', 'ordercheck.py'), xml] + ([ref] if ref else []) + [ours]
    log(f'== check {lv}' + (' against the original path data' if ref else ' (self-check: no original path data)'))
    out = pr.run(args, log)
    report = os.path.join(pr.out_dir(lv), 'check.txt')
    open(report, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    for l in out:
        if re.search(r'positions:|on an area|route check|self-check', l) and not l.startswith('  '):
            log('  ' + l)
    if ref:
        import navcompare
        log('  summary against the original:')
        navcompare.report(navcompare.compare(xml, ref, ours), lambda m: log('    ' + m))
    log(f'  full report: {os.path.relpath(report, pr.root)}')


def export(pr, lv, log=print):
    """The build as the navigation runtime's file (tools\\navexport.py: work\\navkit\\<lv>\\<lv>.rsnv), read back and
    checked against the build; then, when rsnav's host build is found (tools\\rsnavcheck.py), loaded in rsnav and
    routed between the level's spawn points and objectives."""
    import navexport
    import rsnavcheck
    log(f'== export {lv}')
    path, nav = navexport.export(pr.root, lv, log=lambda m: log('  ' + m))
    problems, _ = navexport.check(path, nav, log=lambda m: log('  ' + m))
    if problems:
        raise NavKitError(f'{lv}: the RSNV file does not match the build ({len(problems)} problem(s))')
    d = rsnavcheck.find_rsnav()
    if d is None:
        log('  rsnav not found (RSNAV_ROOT): the file was not tried in the runtime')
        return
    try:
        rb = rsnavcheck.bindings(d)
    except FileNotFoundError as e:
        log(f'  {e}')
        return
    r = rsnavcheck.check_level(pr.root, lv, rb, log=lambda m: log('  ' + m))
    # rsnavcheck.main's pass rule (2026-10-31: the result was dropped, so export and all passed whatever rsnav found):
    # two points placed at least, every pair routed one way, no smoothing failed, the C search equal to Python's
    why = [m for bad, m in ((r['placed'] < 2, f'{r["placed"]} point(s) placed'),
                            (r['either_way'] != r['unordered'], f'pairs routed {r["either_way"]}/{r["unordered"]}'),
                            (r['smooth_failed'], f'{r["smooth_failed"]} smoothing failure(s)'),
                            (r['reference_equal'][0] != r['reference_equal'][1],
                             f'C search equal to Python on {r["reference_equal"][0]}/{r["reference_equal"][1]}')) if bad]
    if why:
        raise NavKitError(f'{lv}: the RSNV file fails in rsnav - ' + '; '.join(why))


def compare(pr, lv, log=print, reference=None, candidate=None):
    """Two path-data files of a level on its AI positions (default: the original against NavKit's build)."""
    import navcompare
    s = pr.settings(lv)
    reference = reference or pr.dice_pathdata(lv)
    candidate = candidate or os.path.join(pr.out_dir(lv), 'pathdata.hpd')
    if not reference or not os.path.isfile(reference):
        raise NavKitError(f'{lv}: no reference file (the level has no original path data; pass --reference)')
    if not os.path.isfile(candidate):
        raise NavKitError(f'{lv}: no candidate file {candidate} (build first, or pass --candidate)')
    xml = os.path.join(pr.level_work(lv), 'dist', 'levels', s['level'] + '.xml')
    log(f'== compare {lv}: reference {os.path.relpath(reference, pr.root)}, candidate {os.path.relpath(candidate, pr.root)}')
    navcompare.report(navcompare.compare(xml, reference, candidate), lambda m: log('  ' + m))


def traversal_path(folder, lv):
    """The traversal annotations written next to a level's path data (navtraversal.py)."""
    return os.path.join(folder, f'{lv}.traversal.json')


def install(pr, lv, log=print, src=None):
    """Copies a built file into the game (DICE's original is kept once, as <file>.opfor-orig), with the level's
    traversal annotations when it has them (next to the installed path data, for OPFOR's bot layer)."""
    ours = src or os.path.join(pr.out_dir(lv), 'pathdata.hpd')
    if not os.path.isfile(ours):
        raise NavKitError(f'{lv}: not built yet')
    if game_running():
        raise NavKitError('the game is running - close it first')
    dest = pr.pathdata(lv)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if not os.path.isfile(dest + NEW_SUFFIX) and not os.path.isfile(dest + BACKUP_SUFFIX):
        if os.path.isfile(dest):
            shutil.copyfile(dest, dest + BACKUP_SUFFIX)
            log(f'  original kept as {os.path.basename(dest)}{BACKUP_SUFFIX}')
        else:
            open(dest + NEW_SUFFIX, 'w').close()  # no original: record that, so restore can remove the file
            log(f'  noting {os.path.basename(dest)} had no original (restore will remove it)')
    shutil.copyfile(ours, dest)
    log(f'== installed {os.path.relpath(ours, pr.root)} as {os.path.relpath(dest, pr.root)}')
    trav, trav_dest = traversal_path(os.path.dirname(ours), lv), traversal_path(os.path.dirname(dest), lv)
    if os.path.isfile(trav):
        shutil.copyfile(trav, trav_dest)
        log(f'  traversal annotations installed as {os.path.relpath(trav_dest, pr.root)}')
    elif os.path.isfile(trav_dest):
        os.remove(trav_dest)                     # none with this file: no stale annotations from an earlier one


def restore(pr, lv, log=print):
    dest = pr.pathdata(lv)
    if not os.path.isfile(dest + BACKUP_SUFFIX) and not os.path.isfile(dest + NEW_SUFFIX):
        log(f'{lv}: nothing to restore (no NavKit file installed)')
        return
    if game_running():
        raise NavKitError('the game is running - close it first')
    if os.path.isfile(dest + BACKUP_SUFFIX):
        shutil.move(dest + BACKUP_SUFFIX, dest)
        if os.path.isfile(dest + NEW_SUFFIX):
            os.remove(dest + NEW_SUFFIX)      # a stale no-original marker: the original is restored now
        log(f'== restored the original {os.path.relpath(dest, pr.root)}')
    else:
        if os.path.isfile(dest):
            os.remove(dest)
        os.remove(dest + NEW_SUFFIX)
        log(f'== removed the installed {os.path.relpath(dest, pr.root)} (the level had no path data before)')
    trav = traversal_path(os.path.dirname(dest), lv)
    if os.path.isfile(trav):
        os.remove(trav)


def apply(pr, lv, navtune_path=None, out=None, log=print, install_after=False):
    """Builds the level with the edits of its navigation side file (DESIGN.md sections 5.1-5.2): walkability
    edits (regions, seeds, unseeds) go into the build, then links and route edges into its route graph
    (navlinks.py); on a DICE base only the graph edits, on a copy of DICE's file. Traversal annotations go to
    <level>.traversal.json next to it (navtraversal.py). The result is read back to check it, the whole log is
    kept as <output>.report.txt, and with install_after the result is installed. Returns the output path."""
    lines = []

    def say(msg):
        lines.append(msg)
        log(msg)
    try:
        out = _apply(pr, lv, navtune_path, out, say)
    except NavKitError as e:
        if out:
            open(out + '.report.txt', 'w', encoding='utf-8').write('\n'.join(lines + [f'error: {e}']) + '\n')
        raise
    open(out + '.report.txt', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    if install_after:
        install(pr, lv, log=log, src=out)
    return out


def _apply(pr, lv, navtune_path, out, log):
    sys.path.insert(0, os.path.join(HERE, '..', 'core'))
    from mohcore import navtune
    import navbuild
    import navregress
    import hpdtool
    navtune_path = navtune_path or navtune.default_path(pr.root, lv)
    if not os.path.isfile(navtune_path):
        raise NavKitError(f'{lv}: no side file {navtune_path} (make edits in the Navigation Studio first)')
    try:
        tune = navtune.load(navtune_path)
    except ValueError as e:
        raise NavKitError(str(e))
    import navlinks
    walk = [o for o in tune.operations if o['op'] in ('walkable', 'unwalkable', 'seed', 'unseed')]
    graph = [o for o in tune.operations if o['op'] in navlinks.GRAPH_OPS]
    later = [o for o in tune.operations if o['op'] == 'traversal']
    t0 = time.time()
    if tune.kind == 'navkit':
        out = out or os.path.join(pr.out_dir(lv), 'pathdata.hpd')
        log(f'== apply {lv}: {len(tune.operations)} edit(s) from {os.path.relpath(navtune_path, pr.root)} '
            f'-> {os.path.relpath(out, pr.root)}')
        # the same inputs as build (detailed zones, ladders ...), plus the edits
        samples, forbid, xoff, zoff, kw = build_inputs(pr, lv, log)
        navbuild.build(samples, forbid, xoff, zoff, out, edits=walk, **kw)
        log(f'  built in {time.time() - t0:.0f} s; {len(walk)} walkability edit(s) applied')
    else:
        # a DICE base: no rebuild - the graph edits go into a copy of DICE's file, for this PC only (never
        # shared: it contains DICE's data; the side file is what can be shared)
        if walk:
            raise NavKitError(f'{lv}: region edits need a NavKit base (DESIGN.md 5.1)')
        src = pr.dice_pathdata(lv)
        if not src:
            raise NavKitError(f'{lv}: the side file\'s base is DICE\'s data, but the level has none')
        out = out or os.path.join(pr.out_dir(lv), 'pathdata_dice_edited.hpd')
        log(f'== apply {lv}: {len(tune.operations)} edit(s) on a copy of DICE\'s file -> {os.path.relpath(out, pr.root)} '
            f'(for this PC only)')
        if tune.base_changed(src):
            log(f'  note: DICE\'s file is not the one the edits were made on (another game version?); the edits are '
                f'by position and are applied, but check them')
        shutil.copyfile(src, out)
    if graph:
        before = _sha256(out)
        f = hpdtool.parse(open(out, 'rb').read())
        results = navlinks.apply(f, graph, log=log)
        open(out, 'wb').write(hpdtool.write(f))
        log(f'  {sum(ok for _, ok, _ in results)} of {len(graph)} link / route-edge edit(s) applied')
        rekey_cells(out, before, log)
    data = open(out, 'rb').read()
    f = hpdtool.parse(data)
    if hpdtool.write(f) != data:
        raise NavKitError(f'{out}: does not read back exactly - not usable')
    trav = traversal_path(os.path.dirname(out), lv)
    if later:
        import navtraversal
        navtraversal.write(f, out, later, tune.level, trav, log=log)
        log(f'  {len(later)} traversal annotation(s) written to {os.path.relpath(trav, pr.root)} (for OPFOR\'s bot layer; '
            f'not route edges - the game\'s AI cannot climb or jump them)')
    elif os.path.isfile(trav):
        os.remove(trav)                          # an earlier run's annotations no longer apply
    notes = sum(o['op'] == 'note' for o in tune.operations)
    if notes:
        log(f'  {notes} note(s): kept in the side file only')
    log(f'  read back exactly ({time.time() - t0:.0f} s in all)')
    return out


def rekey_cells(hpd, before, log=print):
    """The build's cells (navbuild.cells_path) re-keyed to its file after graph edits: they are keyed to the file's
    SHA-256 (navbuild.read_cells), and link / route-edge edits change the file but not its areas - without this, export
    silently fell back to rasterised outlines (2026-10-31). Only a cells file that belonged to the file before the edits
    ('before': its SHA-256 then) is re-keyed; a stale one stays stale."""
    import numpy as np
    import navbuild
    p = navbuild.cells_path(hpd)
    if not os.path.isfile(p):
        return False
    with np.load(p) as z:
        arrays = {k: z[k] for k in z.files}
    if str(arrays.get('hpd_sha256')) != before:
        return False
    arrays['hpd_sha256'] = np.array(_sha256(hpd))
    np.savez_compressed(p, **arrays)
    log(f'  cells {os.path.basename(p)} re-keyed to the edited file (its areas are unchanged)')
    return True


def status(pr, lv, log=print):
    log(f'{lv}:')
    L = pr.level_work(lv)
    log(f'  original path data : {pr.dice_pathdata(lv) or "none"}')
    log(f'  prepared           : {"yes" if os.path.isfile(os.path.join(pr.work, "navkit", lv, "navkit.json")) else "no"}')
    if os.path.isfile(os.path.join(pr.work, 'navkit', lv, 'navkit.json')):
        s = pr.settings(lv)
        log(f'    box {fmt(s["box"])}, y {fmt(s["y"])}, offsets {s["xoff"]},{s["zoff"]}')
    for _, _, suf in AGENTS:
        p = pr.samples(lv, suf)
        log(f'  samples{suf or "":12s}: {time.ctime(os.path.getmtime(p)) if os.path.isfile(p) else "no"}')
    p = os.path.join(pr.work, 'navkit', lv, 'pathdata.hpd')
    log(f'  built              : {time.ctime(os.path.getmtime(p)) if os.path.isfile(p) else "no"}')
    log(f'  installed in game  : {"yes" if os.path.isfile(pr.pathdata(lv) + BACKUP_SUFFIX) or os.path.isfile(pr.pathdata(lv) + NEW_SUFFIX) else "no"}')
    del L


def ask(question):
    return input(question + ' [y/N] ').strip().lower() in ('y', 'yes')


VALUE_OPTIONS = ('--box', '--y', '--opfor', '--root', '--reference', '--candidate', '--navtune', '--out', '--from',
                 '--pitch')        # --root: older name of --opfor
FLAGS = ('--yes', '--install', '--no-detail', '--ingame')


def parse_options(argv):
    """(arguments, {option: value}, {flag}) of a command line, or NavKitError: each option needs a value (not another
    option), and the arguments left must not look like options (2026-10-31: --ingame was never parsed, so
    'sample --ingame lv' took '--ingame' as the level; an option without a value raised IndexError)."""
    args, opts, flags = [], {}, set()
    rest = list(argv[1:])
    while rest:
        a = rest.pop(0)
        if a in VALUE_OPTIONS:
            if not rest or rest[0].startswith('--'):
                raise NavKitError(f'{a} needs a value')
            opts[a] = rest.pop(0)
        elif a in FLAGS:
            flags.add(a)
        elif a.startswith('--'):
            raise NavKitError(f'unknown option {a}')
        else:
            args.append(a)
    return args, opts, flags


def numbers(opts, k, n):
    if k not in opts:
        return None
    try:
        v = [float(x) for x in opts[k].split(',')]
    except ValueError:
        v = []
    if len(v) != n:
        raise NavKitError(f'{k}: {n} comma-separated numbers expected, not {opts[k]!r}')
    return v


def main(argv):
    if '--help' in argv[1:] or argv[1:2] == ['-h']:
        print(__doc__)
        return 0
    try:
        args, opts, flags = parse_options(argv)
        box, y = numbers(opts, '--box', 4), numbers(opts, '--y', 2)
        pitch = numbers(opts, '--pitch', 1)
        if pitch and '--ingame' not in flags:
            # the offline sampler always does its 0.8 m pair and then the 0.2 m pair; a pass alone is the in-game
            # sampler's (2026-10-31: --pitch without --ingame was ignored - a whole resample)
            raise NavKitError('--pitch is a pass of the in-game sampler: sample --ingame --pitch 0.8 / 0.2 '
                              '(the offline sample always does both)')
    except NavKitError as e:
        print('error:', e)
        return 1
    yes = '--yes' in flags
    install_flag = '--install' in flags
    if '--no-detail' in flags:             # build / apply / all: no D lines or F cells (the output before 2026-10-26)
        import navbuild
        navbuild.DETAIL_FEATURES = False
    if not args or args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    confirm = None if yes else ask
    try:
        pr = Project(opts.get('--opfor') or opts.get('--root'))
        cmd = args[0]
        if cmd == 'levels':
            levels(pr)
            return 0
        if len(args) < 2:
            raise NavKitError(f'{cmd}: which level? (e.g. navkit.py {cmd} SP_01)')
        lv = pr.level(args[1])
        if cmd == 'prepare':
            prepare(pr, lv, box=box, y=y)
        elif cmd == 'sample':
            if '--ingame' in flags and pitch:          # BFBC2's in-game sampler (not in MOH's OPFOR core)
                sample(pr, lv, confirm=confirm, box=box, y=y, pitch=pitch[0])
            elif '--ingame' in flags:
                sample_level_ingame(pr, lv, confirm=confirm, box=box, y=y)
            else:
                sample_level(pr, lv, confirm=confirm, box=box, y=y)
        elif cmd == 'build':
            build(pr, lv)
        elif cmd == 'compare':
            compare(pr, lv, reference=opts.get('--reference'), candidate=opts.get('--candidate'))
        elif cmd == 'check':
            check(pr, lv)
        elif cmd == 'export':
            export(pr, lv)
        elif cmd == 'apply':
            apply(pr, lv, opts.get('--navtune'), opts.get('--out'), install_after=install_flag)
        elif cmd == 'install':
            install(pr, lv, src=opts.get('--from'))
        elif cmd == 'restore':
            restore(pr, lv)
        elif cmd == 'status':
            status(pr, lv)
        elif cmd == 'all':
            prepare(pr, lv, box=box, y=y)
            if sample_level(pr, lv, confirm=confirm):
                build(pr, lv)
                check(pr, lv)
                export(pr, lv)
        else:
            raise NavKitError(f'unknown command {cmd!r}')
    except NavKitError as e:
        print('error:', e)
        import logging
        logging.getLogger('moh').error(f'{" ".join(argv[1:])}: {e}')
        return 1
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(HERE, '..', 'core'))
    from mohcore import errors
    errors.setup('navkit', gui=False)          # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
