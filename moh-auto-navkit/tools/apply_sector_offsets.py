"""Give a MOH MP level the Precomputed/Infantry/SectorOffsets asset its generated path data needs,
so the game numbers its sectors as the path-data file does. Reversible, idempotent.

  python apply_sector_offsets.py --plan  [--level <lv>]   print offsets, name, guids, entry; change nothing
  python apply_sector_offsets.py --apply [--level <lv>]   add the asset to the level's level-00.fbrb bundle
  python apply_sector_offsets.py --revert [--level <lv>]  put the original bundle back
  (no --level: every prepared level, i.e. work/navkit/<lv>/navkit.json)

The asset is a GameSharedResources.PathdataSectorOffsets partition (Name, MemoryReportGroup,
XOffset, ZOffset) with UUIDv5 guids, written directly as a binary .dbx (no reference asset needed)
and packed into the level bundle with FbrbTool. The offsets are the level's navkit.json xoff/zoff -
the values NavKit used to build the path data - so they always match the built file.

MOH is infantry-only (no vehicle path data); this writes the Infantry asset only.
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
NAVKIT = os.path.abspath(os.path.join(HERE, '..'))
sys.path[:0] = [NAVKIT]
import navkit  # noqa: E402

FBRB = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
BACKUP = '.opfor-sectoroff-orig'
APPLIED = '.opfor-sectoroff-applied'
CONTAINER, TEXT, NUMBERS = 0xA, 2, 7
NS = 'opfor:sectoroffsets'


def run(*args):
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f'failed: {" ".join(map(str, args))}\n{r.stdout}\n{r.stderr}')
    return r.stdout


def sha(path):
    import hashlib
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class Dbx:
    """A minimal binary .dbx writer (FORMAT.md sections 1-2)."""

    def __init__(self):
        self.strings = ['']            # string 0 is the empty string
        self._idx = {'': 0}
        self.tree = bytearray()

    def intern(self, s):
        if s not in self._idx:
            self._idx[s] = len(self.strings)
            self.strings.append(s)
        return self._idx[s]

    def open(self, tag, attrs):
        self.tree += _varint(self.intern(tag))
        self.tree += bytes([(CONTAINER << 4) | len(attrs)])
        for k, v in attrs:
            self.tree += _varint(self.intern(k)) + _varint(self.intern(v))
        self.tree += b'\x00'          # container byte (kept as-is)

    def field(self, name, kind, value, raw=b''):
        self.tree += _varint(self.intern('field'))
        self.tree += bytes([(kind << 4) | 1])
        self.tree += _varint(self.intern('name')) + _varint(self.intern(name))
        if kind == TEXT:
            self.tree += _varint(self.intern(value))
        elif kind == NUMBERS:
            self.tree += _varint(1) + _varint(len(raw)) + raw
        else:
            self.tree += raw

    def close(self):
        self.tree += _varint(0)

    def build(self):
        base = 24 + 4 * len(self.strings)
        offsets, sdata = [], bytearray()
        for s in self.strings:
            offsets.append(len(sdata))
            sdata += s.encode('utf-8') + b'\0'
        area = 4 * len(self.strings) + len(sdata)
        out = b'{binary}'
        out += struct.pack('>I', 24 + area)
        out += struct.pack('>I', 0)
        out += struct.pack('>I', area)
        out += struct.pack('>I', len(self.strings))
        for off in offsets:
            out += struct.pack('>I', off)
        out += bytes(sdata)
        out += bytes(self.tree)
        return out


# --- level / offset / guid -----------------------------------------------------

def prepared_levels(pr):
    """Every level with a navkit.json (prepare has run), sorted."""
    d = os.path.join(pr.work, 'navkit')
    if not os.path.isdir(d):
        return []
    return sorted(lv for lv in os.listdir(d) if os.path.isfile(os.path.join(d, lv, 'navkit.json')))


def offsets(pr, lv):
    s = json.load(open(pr.settings_path(lv)))
    if 'xoff' not in s or 'zoff' not in s:
        raise SystemExit(f'{lv}: no sector offsets in {pr.settings_path(lv)} (run navkit.py prepare {lv})')
    return s['xoff'], s['zoff']


def guids(lv):
    g = [str(uuid.uuid5(uuid.NAMESPACE_URL, f'{NS}:{lv}:infantry:{part}')).upper() for part in ('partition', 'instance')]
    return g[0], g[1]


def entry_name(lv):
    return f'Levels/{lv.upper()}/Precomputed/Infantry/SectorOffsets'


def build_asset(lv, xoff, zoff):
    part, inst = guids(lv)
    name = entry_name(lv)
    d = Dbx()
    d.open('partition', [('guid', part), ('primaryInstance', inst), ('exportMode', 'All')])
    d.open('instance', [('guid', inst), ('type', 'GameSharedResources.PathdataSectorOffsets'), ('id', 'SectorOffsets')])
    d.field('Name', TEXT, name)
    d.field('MemoryReportGroup', TEXT, 'MemoryReportGroup_default')
    d.field('XOffset', NUMBERS, None, struct.pack('>i', xoff))
    d.field('ZOffset', NUMBERS, None, struct.pack('>i', zoff))
    d.close()                      # instance
    d.close()                      # partition
    return d.build()


def bundle(pr, lv):
    return os.path.join(pr.game, 'dist', 'win32', 'levels', lv, 'level-00.fbrb')


# --- commands ------------------------------------------------------------------

def plan(pr, lv):
    xoff, zoff = offsets(pr, lv)
    part, inst = guids(lv)
    print(f'{lv}: XOffset {xoff}, ZOffset {zoff}; Name {entry_name(lv)}; '
          f'partition {part}, instance {inst}; entry {entry_name(lv)}.dbx in '
          f'{os.path.relpath(bundle(pr, lv), pr.root)}')


def apply(pr, lv, log=print):
    b = bundle(pr, lv)
    if not os.path.isfile(b):
        raise SystemExit(f'{lv}: no level bundle at {b}')
    if os.path.isfile(b + BACKUP):
        # already applied or backed up: idempotent check by marker hash
        if os.path.isfile(b + APPLIED) and open(b + APPLIED).read().strip() == sha(b):
            print(f'  {os.path.relpath(b, pr.root)}: already applied ({sha(b)[:8]})')
            return
        raise SystemExit(f'{os.path.relpath(b, pr.root)}: backup present but bundle changed - not applying')
    xoff, zoff = offsets(pr, lv)
    data = build_asset(lv, xoff, zoff)
    d = os.path.join(pr.work, 'sectoroff', lv)
    os.makedirs(d, exist_ok=True)
    unpacked = os.path.join(d, 'unpacked')
    shutil.rmtree(unpacked, ignore_errors=True)
    run(FBRB, 'unpack', b, unpacked)
    dst = os.path.join(unpacked, *entry_name(lv).split('/')) + '.dbx'
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, 'wb').write(data)
    m = os.path.join(unpacked, 'fbrb.manifest')
    text = open(m, encoding='utf-8').read().rstrip('\r\n')
    text += f'\n{entry_name(lv)}.dbx|<non-resource>|00010000|{len(data)}|{len(data)}\n'
    open(m, 'w', encoding='utf-8', newline='').write(text)
    packed = os.path.join(d, 'level-00.fbrb')
    run(FBRB, 'pack', unpacked, packed)
    shutil.copyfile(b, b + BACKUP)
    shutil.copyfile(packed, b)
    open(b + APPLIED, 'w').write(sha(b))
    print(f'  {os.path.relpath(b, pr.root)}: {sha(b + BACKUP)[:8]} -> {sha(b)[:8]}')


def revert(pr, lv):
    b = bundle(pr, lv)
    if not os.path.isfile(b + BACKUP):
        return
    if os.path.isfile(b + APPLIED) and open(b + APPLIED).read().strip() != sha(b):
        raise SystemExit(f'{os.path.relpath(b, pr.root)}: changed after apply - not reverting')
    os.replace(b + BACKUP, b)
    if os.path.isfile(b + APPLIED):
        os.remove(b + APPLIED)
    print(f'restored {os.path.relpath(b, pr.root)} ({sha(b)[:8]})')


def main(argv):
    pr = navkit.Project()
    lvs = [argv[argv.index('--level') + 1].lower()] if '--level' in argv else prepared_levels(pr)
    if not lvs:
        raise SystemExit('no prepared levels (run navkit.py prepare <level> first)')
    for lv in lvs:
        if '--apply' in argv:
            apply(pr, lv)
        elif '--revert' in argv:
            revert(pr, lv)
        elif '--plan' in argv:
            plan(pr, lv)
        else:
            print(__doc__)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

