r"""FbrbTool: unpack then pack gives back the same bundle (decompressed header and payload identical), *deleted*
markers are listed in fbrb.manifest but not written as files, and a folder from an older unpack (markers as
empty files) packs the same. MOH's uncompressed layouts come back too: an async (ondemand_*) bundle's 4 KiB
alignment (header padded with zeros, each entry on 4 KiB with 0xBF fill, the payload size ending at the last
entry's size2), and build_overlay's 64 0xBF bytes after an entry.

  python test_fbrb.py                   bundles built here (no game data)
  python test_fbrb.py --game [N]        also N bundles of Project OPFOR's game copy (game\MP\dist, every 358 / N-th
                                        in name order; default 12, which takes async\ondemand_awards-00 (4 KiB
                                        aligned) and level, loader, weapon, font, patch and eula bundles; 0 = all 358)
"""
import gzip
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
TOOL = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
DELETED = '*deleted*'


def build(entries, zipped=True, align=0, fill=None):
    """A bundle from [(name, type, flags, data)], laid out as FbrbTool writes one; align=4096: as MOH's async
    bundles (uncompressed, header and entries on 4 KiB, 0xBF between, the payload size ending at the last entry's
    size2 = size - 64); fill={entry index: n}: n 0xBF bytes after that entry (build_overlay)."""
    strings, names, types = bytearray(), [], {}
    for name, typ, _, _ in entries:
        names.append(len(strings))
        strings += name.encode('ascii') + b'\0'
        if typ not in types:
            types[typ] = len(strings)
            strings += typ.encode('ascii') + b'\0'
    payload, table, size2 = bytearray(), bytearray(), 0
    for k, ((name, typ, flags, data), off) in enumerate(zip(entries, names)):
        if align and data:
            payload += b'\xbf' * (-len(payload) % align)
        short = 64 if align and data else 0
        table += struct.pack('>6I', off, flags, len(payload), len(data), len(data) - short, types[typ])
        size2 = len(payload) + len(data) - short
        payload += data + b'\xbf' * (fill or {}).get(k, 0)
    header = struct.pack('>2I', 2, len(strings)) + strings + struct.pack('>I', len(entries)) + table
    header += bytes([1 if zipped else 0]) + struct.pack('>I', size2 if align else len(payload))
    part1 = gzip.compress(bytes(header))
    if align:
        part1 += bytes(-(8 + len(part1)) % align)
    part2 = gzip.compress(bytes(payload)) if zipped else bytes(payload)
    return b'FbRB' + struct.pack('>I', len(part1)) + part1 + part2


def parts(path):
    d = open(path, 'rb').read()
    cut = struct.unpack('>I', d[4:8])[0]
    header, rest = gzip.decompress(d[8:8 + cut]), d[8 + cut:]
    try:
        rest = gzip.decompress(rest)
    except OSError:
        pass
    return header, rest


def run(*args):
    r = subprocess.run([TOOL, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'FbrbTool {" ".join(args[:1])} failed: {(r.stdout + r.stderr).strip()[-300:]}')
    return r.stdout


def roundtrip(bundle, work, old_style=False):
    """Problems found for one bundle (empty list: fine)."""
    problems = []
    out, re = os.path.join(work, 'u'), os.path.join(work, 're.fbrb')
    shutil.rmtree(out, ignore_errors=True)
    run('unpack', bundle, out)
    lines = [l for l in open(os.path.join(out, 'fbrb.manifest'), encoding='ascii') if l.strip() and l[0] != '#']
    markers = [l.split('|')[0] for l in lines if l.split('|')[1] == DELETED]
    for name in markers:
        if os.path.exists(os.path.join(out, name.replace('/', os.sep))):
            problems.append(f'*deleted* marker written as a file: {name}')
            break
    if old_style:                                   # an older unpack: the markers as empty files
        for name in markers:
            p = os.path.join(out, name.replace('/', os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').close()
    run('pack', out, re)
    h1, p1 = parts(bundle)
    h2, p2 = parts(re)
    if h1 != h2:
        problems.append('header differs after unpack + pack')
    if p1 != p2:
        problems.append('payload differs after unpack + pack')
    return problems, len(lines), len(markers)


def main(argv):
    if not os.path.isfile(TOOL):
        print(f'FAIL: {TOOL} not built (run moh-fbrb-tool\\build.bat)')
        return 1
    failed = 0
    work = tempfile.mkdtemp(prefix='fbrb_test_')
    try:
        entries = [('Levels/Test/Test.dbx', '<non-resource>', 0x10000, b'{binary}' + bytes(range(200))),
                   ('Shared/Gone.dbx', DELETED, 0, b''),
                   ('Terrains/Test/Test.Heightfield-000.res', 'TerrainHeightfield', 0x10000, os.urandom(5000)),
                   ('Shared/AlsoGone.res', DELETED, 0, b''),
                   ('Levels/Test/Empty.txt', 'Text', 0, b'')]
        for zipped in (True, False):
            src = os.path.join(work, f'built_{int(zipped)}.fbrb')
            open(src, 'wb').write(build(entries, zipped))
            for old in (False, True):
                problems, n, m = roundtrip(src, work, old_style=old)
                label = f'built bundle (zipped={int(zipped)}{", older unpack" if old else ""})'
                failed += bool(problems)
                print(f'{"FAIL" if problems else "ok  "} {label}: {n} entries, {m} markers' + ''.join('; ' + p for p in problems))
            # a non-marker zero-byte entry is still written (only *deleted* markers are left out)
            if not os.path.isfile(os.path.join(work, 'u', 'Levels', 'Test', 'Empty.txt')):
                failed += 1
                print('FAIL empty non-marker entry was not written')
        # MOH's uncompressed layouts: an async bundle aligned on 4 KiB, a build_overlay with 64 fill bytes after entries
        moh = [('Menu/Art/a.res', 'ITexture', 0x10000, os.urandom(5000)), ('Menu/Art/b.res', 'ITexture', 0x10000, os.urandom(300)),
               ('Menu/Art/c.res', 'ITexture', 0x10000, os.urandom(9000))]
        for label, data, first in (('async bundle (4 KiB aligned)', build(moh, False, align=4096), 'align=4096'),
                                   ('build_overlay bundle (64 fill bytes after entries)', build(moh, False, fill={0: 64, 1: 64}), '|64')):
            src = os.path.join(work, 'moh.fbrb')
            open(src, 'wb').write(data)
            problems, n, m = roundtrip(src, work)
            manifest = open(os.path.join(work, 'u', 'fbrb.manifest'), encoding='ascii').read()
            if first not in manifest:
                problems.append(f'{first} not in fbrb.manifest')
            re_ = open(os.path.join(work, 're.fbrb'), 'rb').read()
            if len(re_) != len(data):
                problems.append(f'{len(re_)} bytes packed, {len(data)} in the bundle')
            failed += bool(problems)
            print(f'{"FAIL" if problems else "ok  "} {label}: {n} entries' + ''.join('; ' + p for p in problems))
        if '--game' in argv:
            sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
            import navkit
            game = navkit.Project().game
            k = argv.index('--game')
            n = int(argv[k + 1]) if k + 1 < len(argv) and argv[k + 1].isdigit() else 12
            bundles = sorted(os.path.join(dp, f) for top in ('dist',) for dp, _, fs in os.walk(os.path.join(game, top))
                             for f in fs if f.lower().endswith('.fbrb'))
            pick = bundles if n == 0 or n >= len(bundles) else [bundles[i * len(bundles) // n] for i in range(n)]
            total_markers = 0
            for b in pick:
                problems, entries_n, m = roundtrip(b, work)
                total_markers += m
                failed += bool(problems)
                if problems:
                    print(f'FAIL {os.path.relpath(b, game)}: ' + '; '.join(problems))
            print(f'{"ok  " if not failed else "FAIL"} {len(pick)} game bundles round-tripped ({total_markers} *deleted* markers)')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
