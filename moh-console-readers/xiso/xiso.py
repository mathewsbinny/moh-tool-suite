"""xiso.py - read-only lister/extractor for Xbox / Xbox 360 game discs (XDVDFS).

Usage: python xiso.py list <image.iso> [filter]
       python xiso.py extract <image.iso> <outdir> [filter]     (filter = case-insensitive substring)

XDVDFS: the game partition starts at a disc-type dependent offset (redump-style images contain the
video partition first). Volume descriptor at sector 32 of the partition: "MICROSOFT*XBOX*MEDIA",
root directory sector (u32) and size (u32). Directory entries form a binary tree:
u16 left, u16 right (offsets in dwords), u32 sector, u32 size, u8 attributes, u8 name length, name;
entries are 4-byte aligned and never cross a sector (0xFF padding). All little-endian.
"""
import os, struct, sys

SECTOR = 2048
MAGIC = b'MICROSOFT*XBOX*MEDIA'
PARTITIONS = (0x0, 0xFD90000, 0x2080000, 0x18300000)  # plain/xiso, XGD2, XGD3, XGD1


def find_partition(f):
    for base in PARTITIONS:
        f.seek(base + 32 * SECTOR)
        if f.read(20) == MAGIC:
            return base
    raise SystemExit('no XDVDFS volume found')


def walk(f, base, sector, size, prefix=''):
    f.seek(base + sector * SECTOR)
    table = f.read(size)
    seen = set()

    def node(off):
        if off in seen or off + 14 > len(table): return
        seen.add(off)
        left, right, sec, sz, attr, nlen = struct.unpack_from('<HHIIBB', table, off)
        if left == 0xFFFF: return
        name = table[off + 14: off + 14 + nlen].decode('latin-1')
        if left: yield from node(left * 4)
        path = prefix + name
        if attr & 0x10:
            if sz: yield from walk(f, base, sec, sz, path + '/')
        else:
            yield path, sec, sz
        if right: yield from node(right * 4)

    yield from node(0)


def main():
    cmd, iso = sys.argv[1], sys.argv[2]
    with open(iso, 'rb') as f:
        base = find_partition(f)
        f.seek(base + 32 * SECTOR + 20)
        root_sec, root_size = struct.unpack('<II', f.read(8))
        files = list(walk(f, base, root_sec, root_size))
        if cmd == 'list':
            flt = sys.argv[3].lower() if len(sys.argv) > 3 else ''
            print(f'partition at 0x{base:X}, {len(files)} files')
            for path, sec, sz in files:
                if flt in path.lower(): print(f'{sz:12d}  {path}')
        elif cmd == 'extract':
            out = sys.argv[3]; flt = sys.argv[4].lower() if len(sys.argv) > 4 else ''
            n = 0
            for path, sec, sz in files:
                if flt not in path.lower(): continue
                dst = os.path.join(out, *path.split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                f.seek(base + sec * SECTOR)
                with open(dst, 'wb') as o:
                    left = sz
                    while left:
                        chunk = f.read(min(left, 1 << 22)); o.write(chunk); left -= len(chunk)
                n += 1
            print(f'extracted {n} files to {out}')


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('xiso', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main())
