"""moh-config — inspect Medal of Honor (2010) MP configuration and reflection metadata.

Commands:
  list                 list the files inside config.zz
  extract NAME         print one file from config.zz
  settings             print the key/value settings parsed from config.zz's .cfg files
  reflect [EXE]        print the engine's reflection catalog (enums + fields) from mohmpgame.exe

config.zz is a ZIP holding the Frostbite config (.cfg / .lua). `reflect` reads the
engine's own reflection tables (the `fieldinf` PE section of mohmpgame.exe) to enumerate
the config surface: enums with their integer values, and fields with their offsets.

Paths are auto-detected from Project OPFOR's game copy (project-opfor-moh\\game next to the suite, or OPFOR_ROOT);
override with --zz / --exe.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def find_game():
    """Project OPFOR's game copy (its game\\ folder, found by core\\mohcore\\gamecopy.py), or None."""
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'core'))
    from mohcore import gamecopy
    try:
        return gamecopy.GameCopy().game
    except gamecopy.GameNotFound:
        return None


def default_zz():
    g = find_game()
    return os.path.join(g, 'MP', 'dist', 'win32', 'config.zz') if g else None


def default_exe():
    g = find_game()
    return os.path.join(g, 'MP', 'MoHMPGame.exe') if g else None


# --- config.zz ---------------------------------------------------------------

def cmd_list(args):
    with zipfile.ZipFile(args.zz) as z:
        for info in z.infolist():
            print('%8d  %s' % (info.file_size, info.filename))


def cmd_extract(args):
    with zipfile.ZipFile(args.zz) as z:
        data = z.read(args.name)
    sys.stdout.buffer.write(data)


def parse_cfg(text):
    """Key/value lines (Key Value), skipping comments and Lua code."""
    out = []
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, val = parts
        if not (key[0].isalpha() and all(ch.isalnum() or ch in '._' for ch in key)):
            continue
        out.append((key, val.strip()))
    return out


def cmd_settings(args):
    with zipfile.ZipFile(args.zz) as z:
        for info in z.infolist():
            if not info.filename.endswith('.cfg'):
                continue
            text = z.read(info.filename).decode('utf-8', 'replace')
            for k, v in parse_cfg(text):
                print('%-28s %s = %s' % (info.filename, k, v))


# --- reflection ---------------------------------------------------------------

def _load_pe(exe):
    import pefile
    pe = pefile.PE(exe, fast_load=True)
    ib = pe.OPTIONAL_HEADER.ImageBase
    sections = {s.Name.rstrip(b'\x00').decode(): s for s in pe.sections}
    data = open(exe, 'rb').read()

    def v2f(va):
        rva = va - ib
        for s in sections.values():
            if s.VirtualAddress <= rva < s.VirtualAddress + s.SizeOfRawData:
                return s.PointerToRawData + (rva - s.VirtualAddress)
        return None

    def cstr(va):
        off = v2f(va)
        if off is None:
            return None
        end = data.find(b'\x00', off)
        if end < 0 or end - off > 256:
            return None
        b = data[off:end]
        if all(0x20 <= c < 0x7f for c in b):
            return b.decode('ascii')
        return None

    return pe, ib, sections, data, v2f, cstr


def common_prefix(strings):
    if not strings:
        return ''
    p = strings[0]
    for s in strings[1:]:
        while p and not s.startswith(p):
            p = p[:-1]
    return p


def cmd_reflect(args):
    pe, ib, sections, data, v2f, cstr = _load_pe(args.exe)
    fi = sections['fieldinf']
    raw = data[fi.PointerToRawData:fi.PointerToRawData + fi.Misc_VirtualSize]

    # fieldinf records are 24 bytes: name_ptr, ?, type_ptr, ?, value, ?
    records = []
    for i in range(0, len(raw) - 23, 24):
        name_ptr, _, type_ptr, _, value, _ = struct.unpack_from('<6I', raw, i)
        records.append((name_ptr, type_ptr, value))

    # Enums: runs of records with type_ptr == 0, split where the value resets to 0.
    print('== ENUMS ==')
    cur = []

    def flush():
        if not cur:
            return
        names = [cstr(r[0]) or '' for r in cur]
        ename = common_prefix(names).rstrip('_') or 'enum'
        print('%s (name inferred from member prefix):' % ename)
        for r in cur:
            print('    %d = %s' % (r[2], cstr(r[0]) or '?'))
        print()

    for rec in records:
        if rec[1] == 0:
            if rec[2] == 0 and cur:
                flush()
                cur = []
            cur.append(rec)
        else:
            flush()
            cur = []
    flush()

    # Fields: grouped by their parent type pointer (type_ptr != 0).
    print('== FIELDS (grouped by parent type) ==')
    groups, order = {}, []
    for rec in records:
        if rec[1] == 0:
            continue
        t = rec[1]
        if t not in groups:
            groups[t] = []
            order.append(t)
        groups[t].append((cstr(rec[0]) or '?', rec[2]))
    for t in order:
        print('type 0x%08X:' % t)
        for name, off in groups[t]:
            print('    %-44s @ 0x%X' % (name, off))


# --- CLI ----------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog='moh-config', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--zz', default=default_zz(), help='path to config.zz')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='list files in config.zz')
    e = sub.add_parser('extract', help='print one file from config.zz')
    e.add_argument('name')
    sub.add_parser('settings', help='print key/value settings from config.zz')
    r = sub.add_parser('reflect', help='print reflection catalog from the exe')
    r.add_argument('exe', nargs='?', default=default_exe(), help='path to mohmpgame.exe')

    args = ap.parse_args(argv)
    if args.cmd == 'list':
        cmd_list(args)
    elif args.cmd == 'extract':
        cmd_extract(args)
    elif args.cmd == 'settings':
        cmd_settings(args)
    elif args.cmd == 'reflect':
        cmd_reflect(args)


if __name__ == '__main__':
    main()

