r"""The console readers on images built here:

- xiso.py: XDVDFS discs with the partition at offset 0 and at the XGD3 offset; a directory tree (binary
  tree entries, a nested folder, an empty folder); list and extract, with a filter; no volume found;
- stfs.py: STFS packages of both kinds (read-only: one hash table per level; others: two), with a
  consecutive file, a fragmented file (hash-entry chain), a folder; info, list and extract, with a filter.

With --game, also the user's own Xbox 360 files when they are on this PC (else skipped): the disc image
(the XGD2 game partition, default.xex) and the three Update packages: Onslaught path data from Update #2
and #3 identical, of the documented size, and identical to Napisal's PC files where the game copy has
them - an independent check of the block mapping in large packages (level-1 hash tables).

  python test_console_readers.py [--game] [--xbox <folder>]
"""
import contextlib
import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
SUITE = os.path.abspath(os.path.join(ROOT, '..'))
sys.path.insert(0, os.path.join(ROOT, 'stfs'))
sys.path.insert(0, os.path.join(ROOT, 'xiso'))
import stfs  # noqa: E402
import xiso  # noqa: E402

XBOX_DEFAULT = r'C:\Games\Battlefield Bad Company 2 Limited Edition (XBOX 360)'
SECTOR = 2048


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''))


# ------------------------------------------------------------------------------------------------ XDVDFS

def dir_table(entries):
    """A directory table: entries (name, sector, size, attr) as a binary tree, root node first."""
    entries = sorted(entries, key=lambda e: e[0].lower())
    order, links = [], {}

    def place(lo, hi):
        if lo >= hi:
            return None
        mid = (lo + hi) // 2
        k = len(order)
        order.append(entries[mid])
        left = place(lo, mid)
        right = place(mid + 1, hi)
        links[k] = (left, right)
        return k

    place(0, len(entries))
    offsets, pos = [], 0
    for name, *_ in order:
        offsets.append(pos)
        pos += (14 + len(name) + 3) & ~3
    out = bytearray()
    for k, (name, sec, size, attr) in enumerate(order):
        left, right = links[k]
        e = struct.pack('<HHIIBB', offsets[left] // 4 if left is not None else 0, offsets[right] // 4 if right is not None else 0,
                        sec, size, attr, len(name)) + name.encode('latin-1')
        out += e + b'\xff' * (((len(e) + 3) & ~3) - len(e))
    return bytes(out)


def build_xiso(path, base):
    """Root: default.xex, Levels/MP_005/level-00.fbrb, Levels/readme.txt, Empty/ (size 0)."""
    data = {'default.xex': os.urandom(3000), 'Levels/MP_005/level-00.fbrb': os.urandom(5000), 'Levels/readme.txt': b'hello'}
    sectors = {'default.xex': 40, 'Levels/MP_005/level-00.fbrb': 42, 'Levels/readme.txt': 46}
    mp = dir_table([('level-00.fbrb', 42, 5000, 0x80)])
    levels = dir_table([('MP_005', 35, len(mp), 0x10), ('readme.txt', 46, 5, 0x80)])
    root = dir_table([('default.xex', 40, 3000, 0x80), ('Levels', 34, len(levels), 0x10), ('Empty', 0, 0, 0x10)])
    with open(path, 'wb') as f:
        def at(sector, blob):
            f.seek(base + sector * SECTOR)
            f.write(blob)
        at(32, xiso.MAGIC + struct.pack('<II', 33, len(root)))
        at(33, root)
        at(34, levels)
        at(35, mp)
        for name, s in sectors.items():
            at(s, data[name])
        f.seek(base + 48 * SECTOR - 1)
        f.write(b'\0')
    return data


def xiso_cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, 'xiso', 'xiso.py'), *args], capture_output=True, text=True)


# ------------------------------------------------------------------------------------------------ STFS

def build_stfs(path, read_only):
    """Data blocks: 0 file table; 1-2 Content/a.bin (consecutive); 5 -> 3 -> 4 Content/b.bin (fragmented);
    6 top.txt."""
    files = {'Content/a.bin': os.urandom(6000), 'Content/b.bin': os.urandom(9000), 'top.txt': b'top level'}
    h = bytearray(0x1000)
    h[0:4] = b'LIVE'
    header_size = 0x971A
    struct.pack_into('>I', h, 0x340, header_size)
    h[0x37B] = 1 if read_only else 0
    struct.pack_into('<H', h, 0x37C, 1)
    h[0x37E:0x381] = (0).to_bytes(3, 'little')
    name = 'Test Package'.encode('utf-16-be')
    h[0x411:0x411 + len(name)] = name
    base = (header_size + 0xFFF) & ~0xFFF
    tables = 1 if read_only else 2

    def entry(name, flags, start, count, parent, size):
        e = bytearray(0x40)
        e[:len(name)] = name.encode('latin-1')
        e[0x28] = len(name) | flags
        e[0x29:0x2C] = count.to_bytes(3, 'little')
        e[0x2C:0x2F] = count.to_bytes(3, 'little')
        e[0x2F:0x32] = start.to_bytes(3, 'little')
        struct.pack_into('>H', e, 0x32, parent)
        struct.pack_into('>I', e, 0x34, size)
        return bytes(e)

    table = (entry('Content', 0x80, 0, 0, 0xFFFF, 0) + entry('a.bin', 0x40, 1, 2, 0, 6000) +
             entry('b.bin', 0x00, 5, 3, 0, 9000) + entry('top.txt', 0x40, 6, 1, 0xFFFF, 9))
    blocks = {0: table, 1: files['Content/a.bin'][:0x1000], 2: files['Content/a.bin'][0x1000:],
              5: files['Content/b.bin'][:0x1000], 3: files['Content/b.bin'][0x1000:0x2000], 4: files['Content/b.bin'][0x2000:],
              6: files['top.txt']}
    hashes = bytearray(0x1000)
    for b, nxt in ((0, 0xFFFFFF), (5, 3), (3, 4), (4, 0xFFFFFF)):
        hashes[b * 0x18 + 0x15:b * 0x18 + 0x18] = nxt.to_bytes(3, 'big')
    with open(path, 'wb') as f:
        f.write(h)
        for k in range(tables):                       # the level-0 hash table(s) before the first data block
            f.seek(base + k * 0x1000)
            f.write(hashes)
        for b, blob in blocks.items():
            f.seek(base + (tables + b) * 0x1000)
            f.write(blob.ljust(0x1000, b'\0'))
    return files


def stfs_cli(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = stfs.main(['stfs.py', *args])
    return code, buf.getvalue()


# ------------------------------------------------------------------------------------------------ APT

def build_apt(big):
    """A small APT movie (.apt, .const) in either byte order: character 0 the movie (one empty frame, an export
    "spr" = 1), 1 a sprite whose one frame has an action (EA_PushLong 0x01020304, Stop, End) and a label "intro",
    2 an image (texture 2), 3 a shape (bounds 0,0-10,20, geometry 3). Constants: "hello" and register 3."""
    e = '>' if big else '<'
    a = bytearray(b'Apt Data:74\x1a\0\0\0\0\0')

    def put(data):
        while len(a) % 4:
            a.append(0)
        at = len(a)
        a.extend(data)
        return at
    code = put(bytes([0xB7]) + struct.pack(e + 'i', 0x01020304) + bytes([0x07, 0x00]))
    label = put(b'intro\0')
    export_name = put(b'spr\0')
    action = put(struct.pack(e + 'II', 1, code))
    flabel = put(struct.pack(e + 'IIIi', 2, label, 0, 0))
    items = put(struct.pack(e + 'II', action, flabel))
    sframes = put(struct.pack(e + 'II', 2, items))
    sprite = put(struct.pack(e + 'IIIII', 5, 0x09876543, 1, sframes, 0))
    image = put(struct.pack(e + 'III', 7, 0x09876543, 2))
    shape = put(struct.pack(e + 'II4fI', 1, 0x09876543, 0, 0, 10, 20, 3))
    mframes = put(struct.pack(e + 'II', 0, 0))
    exports = put(struct.pack(e + 'II', export_name, 1))
    chars = put(b'\0' * 16)
    movie = put(struct.pack(e + 'IIIIIIIiiIIIII', 9, 0x09876543, 1, mframes, 0, 4, chars, 1280, 720, 33, 0, 0, 1, exports))
    struct.pack_into(e + '4I', a, chars, movie, sprite, image, shape)
    c = bytearray(b'Apt constant file\x1a\0\0')
    hello = 64
    c += struct.pack(e + 'III', movie, 2, 32) + struct.pack(e + 'IIII', 1, hello, 4, 3)
    c += b'\0' * (hello - len(c)) + b'hello\0'
    return bytes(a), bytes(c)


def apt_checks(c, work):
    sys.path.insert(0, os.path.join(ROOT, 'apt'))
    import apt
    dumps = {}
    for big in (False, True):
        label = 'big-endian' if big else 'little-endian'
        a, k = build_apt(big)
        base = os.path.join(work, f'movie_{int(big)}')
        open(base + '.apt', 'wb').write(a)
        open(base + '.const', 'wb').write(k)
        mv = apt.Movie(base + '.apt')
        c.check(mv.big == big, f'apt: byte order detected ({label})')
        c.check(mv.const == ['"hello"', 'reg3'], f'apt: constants ({label})', str(mv.const))
        kinds = [(cid, t) for cid, _, t in mv.characters()]
        c.check(kinds == [(0, 9), (1, 5), (2, 7), (3, 1)], f'apt: character table ({label})', str(kinds))
        text = '\n'.join(mv.dump())
        dumps[big] = text.split('\n', 1)[1]                               # all but the header line (byte order)
        c.check('FrameLabel "intro" frame 0' in text and 'EA_PushLong 16909060' in text and 'Stop' in text,
                f'apt: frames, labels and bytecode immediates ({label})')
        c.check('char 2: image' in text and 'texture=2' in text and 'bounds=(0,0)-(10,20) geometry=3' in text
                and 'export "spr" = char 1' in text, f'apt: images, shapes and exports ({label})')
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'apt', 'apt.py'), 'info', base + '.apt'],
                           capture_output=True, text=True)
        c.check(r.returncode == 0 and ('big-endian' if big else 'little-endian') in r.stdout
                and '1 image, 1 movie, 1 shape, 1 sprite' in r.stdout, f'apt: info command ({label})', r.stdout + r.stderr)
    c.check(dumps[False] == dumps[True], 'apt: both byte orders read the same movie')


def console_menu_checks(c, u3, work):
    """Update #3's console main menu: apt.py reads its main_nav (big-endian) and finds the Onslaught mode picture:
    the sprite with a frame "onslaught" places shape 634."""
    fbrb = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
    e = next((x for x in u3.entries if x['path'].lower() == 'menu/main/mainmenu-00.fbrb'), None)
    if e is None or not os.path.isfile(fbrb):
        print('skip the console menu (no menu bundle in Update #3, or FbrbTool.exe not built)')
        return
    bundle = os.path.join(work, 'mainmenu-00.fbrb')
    open(bundle, 'wb').write(u3.read(e))
    subprocess.run([fbrb, 'unpack', bundle, os.path.join(work, 'menu')], check=True, stdout=subprocess.DEVNULL)
    sys.path.insert(0, os.path.join(ROOT, 'apt'))
    import apt
    mv = apt.Movie(os.path.join(work, 'menu', 'Menu', 'main_nav.res'))
    c.check(mv.big, 'console main_nav: big-endian')
    text = mv.dump()
    c.check(sum('unreadable' in l for l in text) <= 1 and len(text) > 80000, 'console main_nav: dumped in full',
            f'{len(text)} lines')
    shape = None
    for cid, cp, t in mv.characters():
        if t == 5:
            fptr = mv.u(cp + 12)
            for fr in range(mv.u(cp + 8)):
                n, items = mv.u(fptr + 8 * fr), mv.u(fptr + 8 * fr + 4)
                its = [mv.u(items + 4 * k) for k in range(n)]
                if any(mv.u(i) == 2 and mv.s(mv.u(i + 4)) == 'onslaught' for i in its):
                    shape = next(mv.i(i + 12) for i in its if mv.u(i) == 3)
    c.check(shape == 634, 'console main_nav: the "onslaught" mode picture is shape 634', str(shape))


# ------------------------------------------------------------------------------------------------ user files

def sha(path):
    return hashlib.sha1(open(path, 'rb').read()).hexdigest()


def game_checks(c, xbox):
    iso = os.path.join(xbox, 'Battlefield Bad Company 2 Limited Edition (XBOX 360).iso')
    if os.path.isfile(iso):
        with open(iso, 'rb') as f:
            base = xiso.find_partition(f)
            f.seek(base + 32 * SECTOR + 20)
            root_sec, root_size = struct.unpack('<II', f.read(8))
            files = list(xiso.walk(f, base, root_sec, root_size))
        c.check(base == 0xFD90000, 'disc image: the XGD2 game partition at 0xFD90000')
        names = {p.lower() for p, _, _ in files}
        c.check(len(files) == 693 and 'default.xex' in names, f'disc image: all 693 files listed, default.xex among them',
                f'{len(files)} files')
    else:
        print(f'skip disc image (not found: {iso})')
    folder = os.path.join(xbox, 'Battlefield Bad Company 2 (3 DLCs Pack prototype)', '454108A8', '00000002')
    if not os.path.isdir(folder):
        print(f'skip Update packages (not found: {folder})')
        return
    pkgs = {}
    for f in sorted(os.listdir(folder)):
        try:
            p = stfs.Package(os.path.join(folder, f))
        except Exception as e:
            c.check(False, f'package {f} opens', str(e))
            continue
        pkgs[p.name] = p
    c.check(all(p.magic == b'LIVE' for p in pkgs.values()) and len(pkgs) == 3, f'three LIVE packages: {sorted(pkgs)}')
    u2 = next((p for n, p in pkgs.items() if n.endswith('Update #2')), None)
    u3 = next((p for n, p in pkgs.items() if n.endswith('Update #3')), None)
    if not (u2 and u3):
        c.check(False, 'Update #2 and #3 found by name')
        return
    work = tempfile.mkdtemp(prefix='stfs_game_')
    try:
        want = 'ai/levels/ons_mp_008/infantry/pathdata.hpd'
        got = {}
        for label, p in (('2', u2), ('3', u3)):
            e = next((x for x in p.entries if x['path'].lower().endswith(want)), None)
            if e is None:
                c.check(False, f'Update #{label} has {want}')
                continue
            out = os.path.join(work, f'u{label}.hpd')
            open(out, 'wb').write(p.read(e))
            got[label] = out
        if len(got) == 2:
            c.check(os.path.getsize(got['3']) == 1872532, 'Nelson Bay infantry path data: 1,872,532 bytes, as documented')
            c.check(sha(got['2']) == sha(got['3']), 'Update #2 and #3 give the same Nelson Bay path data')
            sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit', 'tools'))
            import hpdtool
            data = open(got['3'], 'rb').read()
            c.check(hpdtool.write(hpdtool.parse(data)) == data, 'it is valid path data (hpdtool reads and writes it back exactly)')
        sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
        import navkit
        game = os.path.join(navkit.find_opfor(None), 'game', 'Output', 'win32', 'ai', 'levels')
        compared = 0
        for lv in ('ons_mp_002', 'ons_mp_004'):
            pc = os.path.join(game, lv, 'infantry', 'pathdata.hpd')
            e = next((x for x in u3.entries if x['path'].lower().endswith(f'ai/levels/{lv}/infantry/pathdata.hpd')), None)
            if os.path.isfile(pc) and e is not None:
                compared += 1
                c.check(hashlib.sha1(u3.read(e)).hexdigest() == sha(pc), f'{lv}: Update #3 path data identical to Napisal\'s PC file')
        if not compared:
            print('skip comparison with Napisal\'s PC files (not installed in the game copy)')
        console_menu_checks(c, u3, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='console_test_')
    try:
        for base, label in ((0, 'plain image'), (0x2080000, 'XGD3 offset')):
            iso = os.path.join(work, f'disc_{base:x}.iso')
            data = build_xiso(iso, base)
            with open(iso, 'rb') as f:
                c.check(xiso.find_partition(f) == base, f'xiso: partition found ({label})')
            r = xiso_cli('list', iso)
            listed = {l.split(None, 1)[1]: int(l.split(None, 1)[0]) for l in r.stdout.splitlines()[1:] if l.strip()}
            c.check(listed == {k: len(v) for k, v in data.items()}, f'xiso: list - every file with its size, nested and empty folders ({label})',
                    str(listed))
            out = os.path.join(work, f'x_{base:x}')
            xiso_cli('extract', iso, out)
            same = all(open(os.path.join(out, *k.split('/')), 'rb').read() == v for k, v in data.items())
            c.check(same, f'xiso: extract - every file\'s bytes ({label})')
        out = os.path.join(work, 'filtered')
        r = xiso_cli('extract', iso, out, 'readme')
        c.check(r.returncode == 0 and 'extracted 1 files' in r.stdout and os.path.isfile(os.path.join(out, 'Levels', 'readme.txt')),
                'xiso: extract with a filter')
        blank = os.path.join(work, 'blank.iso')
        open(blank, 'wb').write(b'\0' * (40 * SECTOR))
        r = xiso_cli('list', blank)
        c.check(r.returncode != 0 and 'no XDVDFS volume' in (r.stdout + r.stderr), 'xiso: an image without a volume is refused')

        for read_only in (True, False):
            label = 'read-only package' if read_only else 'package with two hash tables'
            path = os.path.join(work, f'pkg_{int(read_only)}')
            files = build_stfs(path, read_only)
            p = stfs.Package(path)
            c.check(p.magic == b'LIVE' and p.name == 'Test Package' and p.shift == (0 if read_only else 1), f'stfs: header ({label})')
            paths = {e['path']: e for e in p.entries}
            c.check(set(paths) == {'Content', 'Content/a.bin', 'Content/b.bin', 'top.txt'} and paths['Content']['dir'],
                    f'stfs: file table with folder paths ({label})', str(sorted(paths)))
            c.check(all(p.read(paths[k]) == v for k, v in files.items()), f'stfs: consecutive and fragmented files read exactly ({label})')
            p.f.close()
            code, out = stfs_cli('list', path, 'content/')
            c.check(code == 0 and 'Content/a.bin' in out and 'top.txt' not in out and 'Content\n' not in out, f'stfs: list with a filter ({label})')
            dest = os.path.join(work, f's_{int(read_only)}')
            code, out = stfs_cli('extract', path, dest)
            c.check(all(open(os.path.join(dest, *k.split('/')), 'rb').read() == v for k, v in files.items()), f'stfs: extract ({label})')
            code, out = stfs_cli('info', path)
            c.check(code == 0 and '4 entries' in out, f'stfs: info ({label})')

        apt_checks(c, work)

        if '--game' in argv:
            xbox = argv[argv.index('--xbox') + 1] if '--xbox' in argv else XBOX_DEFAULT
            game_checks(c, xbox)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
