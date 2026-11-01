"""Medal of Honor (2010) crash-report reader: turns crashreport.xml into something you can act on.

  python crashreader.py [crashreport.xml] [--exe MoHMPGame.exe] [--symbols file]... [--log opfor.log] [--no-code]

Without a path it uses the newest crashreport.xml in Documents\\Project OPFOR\\MOH\\EA Games\\Medal of Honor\\Multiplayer
(OPFOR's) or Documents\\EA Games\\Medal of Honor\\Multiplayer (the base game's). It shows:
  - when, which build, the crash address, and the registers (null ones marked)
  - the code around the crash, disassembled from your own MoHMPGame.exe (needs: pip install pefile capstone),
    with the register the faulting instruction reads through when that register is null
  - the call stack: each return address with its function's name (from symbols_592256.txt and --symbols files)
    and the call instruction that made it; values that are not code addresses are marked
  - hints for known crash sites (e.g. a broken level: run DbxEdit check)
  - with --log (or OPFOR's game\\logs\\opfor_mp.log found above the exe), the log lines just before the crash

The game's own crash reporter writes the file; this tool only reads it and the exe. Nothing is sent anywhere.
"""
from __future__ import annotations

import datetime
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from types import ModuleType
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = '592256'                          # MoHMPGame.exe as patched (PE time stamp 0x4CC17C02)
SYMBOLS = os.path.join(HERE, f'symbols_{BUILD}.txt')
REGISTERS = ['Eax', 'Ebx', 'Ecx', 'Edx', 'Esi', 'Edi', 'Ebp', 'Esp', 'Eip']

Symbols = dict[int, tuple[str, str]]     # address -> (name, hint), from the symbol files
Insn = Any                               # a capstone instruction (CsInsn: capstone ships no type information)


# --------------------------------------------------------------------------------------------------- inputs

def _gamecopy() -> ModuleType:
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import gamecopy
    return gamecopy


def find_reports() -> list[str]:
    """The MP client's crashreport.xml files, newest first: OPFOR's (Documents\\Project OPFOR\\MOH\\EA Games\\Medal of
    Honor\\Multiplayer, where the OPFOR core sends the game's Documents folder), then the base game's."""
    found: list[str] = []
    for d in _gamecopy().documents_dirs():
        p = os.path.join(d, 'crashreport.xml')
        if os.path.isfile(p):
            found.append(p)
    return sorted(set(found), key=os.path.getmtime, reverse=True)


def find_exe() -> str | None:
    """Project OPFOR's MoHMPGame.exe (the game copy core's gamecopy finds), or None without a game copy."""
    gamecopy = _gamecopy()
    try:
        return gamecopy.GameCopy().exe
    except gamecopy.GameNotFound:
        return None


def read_report(path: str) -> dict[str, Any]:
    """The crash report's fields: type, SKU, build, time, category, the stack's values and the registers."""
    root = ET.parse(path).getroot()
    text = lambda tag: (root.findtext(tag) or '').strip()
    stack = [int(v, 16) for v in text('stack').split() if re.fullmatch(r'0x[0-9A-Fa-f]+', v)]
    regs: dict[str, int] = {}
    for name, value in re.findall(r'(\w+)\s+([0-9A-Fa-f]{8})', text('contextdata')):
        if name.capitalize() in REGISTERS:
            regs[name.capitalize()] = int(value, 16)
    category = text('categoryid')
    return {
        'type': text('type'), 'sku': text('sku'), 'build': text('buildsignature'), 'time': text('createtime'),
        'category': int(category, 16) if re.fullmatch(r'0x[0-9A-Fa-f]+', category) else None,
        'stack': stack, 'registers': regs,
    }


def read_symbols(paths: Iterable[str]) -> Symbols:
    """{address: (name, hint)} from symbol files ('address name | hint' lines, # comments); missing files are
    skipped."""
    syms: Symbols = {}
    for p in paths:
        if not os.path.isfile(p):
            continue
        for line in open(p, encoding='utf-8'):
            line = line.split('#', 1)[0].rstrip()
            m = re.match(r'\s*(?:0x)?([0-9A-Fa-f]{6,8})\s+([^|]+?)\s*(?:\|\s*(.*))?$', line)
            if m:
                syms[int(m.group(1), 16)] = (m.group(2).strip(), (m.group(3) or '').strip())
    return syms


# --------------------------------------------------------------------------------------------------- the exe

class Image:
    """The exe's code, for disassembly (pefile + capstone)."""

    def __init__(self, path: str) -> None:
        import capstone
        import pefile
        pe = pefile.PE(path, fast_load=True)
        self.base: int = pe.OPTIONAL_HEADER.ImageBase
        self.data: bytes = pe.get_memory_mapped_image()
        self.end = self.base + len(self.data)
        self.code: list[tuple[int, int]] = [(self.base + s.VirtualAddress, self.base + s.VirtualAddress + s.Misc_VirtualSize)
                     for s in pe.sections if s.Characteristics & 0x20000000]
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True

    def is_code(self, a: int) -> bool:
        """Whether the address is in one of the exe's code sections."""
        return any(lo <= a < hi for lo, hi in self.code)

    def bytes_at(self, a: int, n: int) -> bytes:
        """n bytes of the image at address a (fewer at its end)."""
        o = a - self.base
        return self.data[o:o + n]

    def function_start(self, a: int, limit: int = 0x4000) -> int | None:
        """Heuristic: MSVC pads functions with int3 (0xCC); the function starts after the last pad before a."""
        for p in range(a, max(a - limit, self.base), -1):
            o = p - self.base
            if self.data[o - 1] == 0xCC and self.data[o] != 0xCC and (p % 16 == 0 or self.data[o - 2] == 0xCC):
                return p
        return None

    def disasm(self, start: int, stop: int) -> list[Insn]:
        """The instructions decoded from start, linearly, to a little past stop."""
        return list(self.md.disasm(self.bytes_at(start, stop - start + 16), start))

    def call_before(self, ret: int) -> Insn | None:
        """The call instruction that ends exactly at a return address, if any."""
        for length in (5, 6, 2, 3, 7, 4):
            for ins in self.md.disasm(self.bytes_at(ret - length, length), ret - length):
                if ins.address + ins.size == ret and ins.mnemonic == 'call':
                    return ins
                break
        return None


def null_operands(ins: Insn, regs: Mapping[str, int]) -> list[tuple[str, int, int]]:
    """Registers a memory operand of the instruction reads through that hold a null (or tiny) pointer."""
    import capstone.x86 as x86
    bad: list[tuple[str, int, int]] = []
    for op in ins.operands:
        if op.type != x86.X86_OP_MEM:
            continue
        for r in (op.mem.base, op.mem.index):
            if r == 0:
                continue
            name = ins.reg_name(r).capitalize()
            v = regs.get(name)
            if v is not None and v < 0x10000:
                bad.append((name, v, op.mem.disp))
    return bad


# --------------------------------------------------------------------------------------------------- report

def name_of(a: int, syms: Mapping[int, tuple[str, str]], image: Image | None) -> tuple[str, str]:
    """(name, hint) of an address: its symbol, else its function's symbol plus the offset, else the nearest symbol
    before it in the same function, else FUN_<start>; ('', '') when nothing is known."""
    if a in syms:
        return syms[a][0], syms[a][1]
    start = image.function_start(a) if image and image.is_code(a) else None
    if start is not None and start in syms:
        return f'{syms[start][0]} +0x{a - start:X}', syms[start][1]
    near = max((s for s in syms if s <= a and a - s < 0x800), default=None)
    if near is not None and start is not None and near >= start:
        return f'{syms[near][0]} +0x{a - near:X}', syms[near][1]
    if start is not None:
        return f'FUN_{start:08x} +0x{a - start:X}', ''
    return '', ''


def log_before_crash(path: str, lines: int = 18) -> list[str]:
    """The last log lines before the log's last crash-report line (the per-frame probe spam left out), and that
    line; [] when the log is unreadable or shows no crash."""
    try:
        text = open(path, encoding='utf-8', errors='replace').read().splitlines()
    except OSError:
        return []
    marks = [i for i, l in enumerate(text) if 'crashreport.xml' in l]
    if not marks:
        return []
    end = marks[-1]
    # skip the per-frame spam probes write, keep what led up to the crash
    picked = [l for l in text[max(0, end - 400):end] if 'Kynapse error' not in l and 'per frame' not in l]
    return picked[-lines:] + [text[end]]


def main(argv: list[str]) -> int:
    """The command line: reads the crash report (the newest one without a path) and prints what it shows."""
    args: list[str] = []
    opts: dict[str, Any] = {'--symbols': []}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ('--exe', '--log') and i + 1 < len(argv):
            opts[a] = argv[i + 1]; i += 2; continue
        if a == '--symbols' and i + 1 < len(argv):
            opts['--symbols'].append(argv[i + 1]); i += 2; continue
        if a in ('-h', '--help'):
            print(__doc__); return 0
        if a == '--no-code':
            opts[a] = True
        else:
            args.append(a)
        i += 1
    report_path = args[0] if args else next(iter(find_reports()), None)
    if not report_path:
        print('No crashreport.xml found (looked in ' + ' and '.join(_gamecopy().documents_dirs()[:2]) + '). Give its path.')
        return 1
    r = read_report(report_path)
    syms = read_symbols([SYMBOLS] + opts['--symbols'])
    exe = opts.get('--exe') or find_exe()
    image: Image | None = None
    if exe and not opts.get('--no-code'):
        try:
            image = Image(exe)
        except ImportError:
            print('(code view off: pip install pefile capstone)')
        except Exception as e:  # noqa: BLE001 - optional code view (pefile/capstone errors): the report prints without it
            print(f'(code view off: cannot read {exe}: {e})')

    when = datetime.datetime.fromtimestamp(os.path.getmtime(report_path)).strftime('%Y-%m-%d %H:%M:%S')
    print(f'Crash report {report_path}')
    print(f'  written {when} local ({r["time"]} UTC); type {r["type"]}; build {r["build"]}; {r["sku"]}')
    if r['build'] and r['build'] != BUILD:
        print(f'  note: this is not build {BUILD} - the bundled names may not match')
    eip = r['registers'].get('Eip')
    if eip is None:                                # Eip 0 is an address (a call through a null pointer), not "none"
        eip = r['category']
    name, hint = name_of(eip, syms, image) if eip is not None else ('', '')
    if eip == 0:
        name = '(a call or jump through a null function pointer: the caller is the first return address below)'
    print(f'\nCrash at 0x{eip:08X}  {name}' if eip is not None else '\nCrash address unknown')
    regs = r['registers']
    print('  registers: ' + '  '.join(f'{k} {regs[k]:08X}' + (' (null)' if regs[k] == 0 else '') for k in REGISTERS if k in regs))

    if image and eip is not None and image.is_code(eip):
        start = image.function_start(eip) or eip - 0x40
        ins = [x for x in image.disasm(max(start, eip - 0x60), eip + 0x10) if x.address <= eip + 0x10]
        at = next((k for k, x in enumerate(ins) if x.address == eip), None)
        print(f'\nCode (function starts near 0x{start:08X}):')
        window = ins[max(0, (at or 0) - 10):(at or 0) + 4] if at is not None else ins[-14:]
        for x in window:
            mark = '>' if x.address == eip else ' '
            print(f'  {mark} {x.address:08X}  {x.mnemonic} {x.op_str}')
        if at is not None:
            for reg, v, disp in null_operands(ins[at], regs):
                print(f'\n  The faulting instruction reads memory through {reg} = 0x{v:X} (+0x{disp:X}): a null pointer.')
                print('  The value it expected was never set - typically data the game looked up and did not find.')
    if hint:
        print(f'\nHint: {hint}')

    print('\nCall stack (innermost first; return addresses):')
    shown = 0
    for a in r['stack']:
        if image and not (image.base <= a < image.end):
            label = '(outside the exe: a system DLL, or a value on the stack that is not an address)'
            if 0x20202020 <= a <= 0x7A7A7A7A and all(32 <= b < 127 for b in a.to_bytes(4, 'little')):
                label = f'(text on the stack: "{a.to_bytes(4, "little").decode()}" - not an address)'
            print(f'  0x{a:08X}  {label}')
            continue
        n, h = name_of(a, syms, image)
        call = image.call_before(a) if image and image.is_code(a) and a != eip else None
        line = f'  0x{a:08X}  {n or "?"}'
        if call:
            line += f'    <- {call.mnemonic} {call.op_str}'
        elif image and image.is_code(a) and a != eip:
            line += '    (no call before it: probably not a return address)'
        elif image and not image.is_code(a):
            line += '    (not code)'
        print(line)
        if h and a != eip:
            print(f'              hint: {h}')
        shown += 1
        if shown >= 40:
            print('  ...')
            break

    log = opts.get('--log')
    if not log and exe:
        candidate = os.path.join(os.path.dirname(os.path.dirname(exe)), 'logs', 'opfor_mp.log')   # game\\logs
        log = candidate if os.path.isfile(candidate) else None
    if log:
        before = log_before_crash(log)
        if before:
            print(f'\nLast log lines before the crash ({log}):')
            for l in before:
                print('  ' + l[:200])
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('crashreader', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
