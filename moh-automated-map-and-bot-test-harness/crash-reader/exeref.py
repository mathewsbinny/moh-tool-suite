r"""exeref.py - where mohmpgame.exe uses a string or an address, with a short disassembly window around each place.

  python exeref.py str <text> [--exact] [--max 20] [--dis [N]]
        the exe's C strings containing <text> (or equal to it, --exact); for each, the instructions that use its
        address (push / mov / cmp / lea ... with it as an immediate or displacement), in which function; --dis shows
        N instructions (default 6) before and after each
  python exeref.py refs <address> [--max 40] [--dis [N]]
        the instructions that use an address: as an immediate or displacement, or as the target of a call / jmp
  python exeref.py dis <address> [--before 0x20] [--after 0x40]
        a short window of code around an address (at most 0x100 bytes each way), the instruction at it marked;
        strings and known functions named
Options: --exe <mohmpgame.exe> (default: Project OPFOR's game copy, as the crash reader finds it), --symbols <file>
(more names, the crash reader's format; symbols_592256.txt is always read).

For targeted questions ("which code reads this setting name", "who calls this function") - the suite's rule is short,
targeted disassembly, no bulk decompiling: windows are capped and nothing is exported. Needs pefile and capstone
(pip install pefile capstone), like the crash reader's code view. Reads the exe only.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crashreader  # noqa: E402

MAX_WINDOW = 0x100


class ExeRefError(Exception):
    pass


class Exe(crashreader.Image):
    """The crash reader's image (pefile + capstone) with string and reference searches."""

    def __init__(self, path):
        super().__init__(path)
        import pefile
        pe = pefile.PE(path, fast_load=True)
        self.sections = [(s.Name.rstrip(b'\0').decode('latin1'), self.base + s.VirtualAddress, s.Misc_VirtualSize,
                          s.Characteristics) for s in pe.sections]

    def data_ranges(self):
        return [(lo, lo + n) for name, lo, n, ch in self.sections if not ch & 0x20000000 and name not in ('.rsrc', '.reloc')]

    def code_ranges(self):
        return [(lo, lo + n) for name, lo, n, ch in self.sections if ch & 0x20000000]

    def cstr(self, va, limit=120):
        if not (self.base <= va < self.end):
            return None
        o = va - self.base
        e = self.data.find(b'\0', o, o + limit + 1)
        if e < 0 or e == o:
            return None
        s = self.data[o:e]
        return s.decode('ascii') if all(32 <= c < 127 or c in (9, 10) for c in s) else None

    def find_strings(self, text, exact=False):
        """[(va, string)] of the C strings in the data sections that contain text (any case) or equal it."""
        out = []
        if exact:
            pat = re.compile(rb'(?<=\x00)' + re.escape(text.encode('latin1')) + rb'(?=\x00)')
        else:
            pat = re.compile(rb'(?<=\x00)[\x20-\x7e]*' + re.escape(text.encode('latin1')) + rb'[\x20-\x7e]*(?=\x00)', re.I)
        for lo, hi in self.data_ranges():
            for m in pat.finditer(self.data, lo - self.base, hi - self.base):
                out.append((self.base + m.start(), m.group().decode('latin1')))
        return out

    def instruction_at(self, start, value):
        """The instruction starting at start if it holds value as an immediate or displacement."""
        for ins in self.md.disasm(self.bytes_at(start, 16), start):
            for op in ins.operands:
                if op.type == 2 and (op.imm & 0xFFFFFFFF) == value:           # X86_OP_IMM
                    return ins
                if op.type == 3 and (op.mem.disp & 0xFFFFFFFF) == value:      # X86_OP_MEM
                    return ins
            return None
        return None

    def on_boundary(self, va):
        """Whether a linear decode from the function start (int3 padding, as the crash reader finds it) reaches va."""
        start = self.function_start(va)
        if start is None or va - start > 0x8000:
            return True                                 # unknown: keep it
        for ins in self.md.disasm(self.bytes_at(start, va - start + 16), start):
            if ins.address >= va:
                return ins.address == va
        return True                                     # the decode stopped on data (a jump table) before va

    def refs(self, value, limit=200):
        """Code addresses of instructions using value: (va, instruction, kind)."""
        import numpy as np
        out, pat = [], struct.pack('<I', value)
        for lo, hi in self.code_ranges():
            o, end = lo - self.base, hi - self.base
            j = self.data.find(pat, o, end)
            while 0 <= j < end and len(out) < limit:
                for back in range(1, 8):
                    s = self.base + j - back
                    ins = self.instruction_at(s, value)
                    if ins and ins.address + ins.size >= self.base + j + 4 and self.on_boundary(ins.address):
                        out.append((ins.address, ins, 'operand'))
                        break
                j = self.data.find(pat, j + 1, end)
            # call / jmp rel32 to value
            arr = np.frombuffer(self.data, dtype=np.uint8, count=end - o, offset=o)
            for opc in (0xE8, 0xE9):
                pos = np.nonzero(arr[:-5] == opc)[0]
                rel = (arr[pos + 1].astype(np.int64) | (arr[pos + 2].astype(np.int64) << 8) |
                       (arr[pos + 3].astype(np.int64) << 16) | (arr[pos + 4].astype(np.int64) << 24))
                rel = np.where(rel >= 1 << 31, rel - (1 << 32), rel)
                hit = pos[(lo + pos + 5 + rel) == value]
                for p in hit[:limit]:
                    va = lo + int(p)
                    if self.on_boundary(va):
                        ins = next(self.md.disasm(self.bytes_at(va, 5), va), None)
                        if ins is not None:
                            out.append((va, ins, 'call' if opc == 0xE8 else 'jmp'))
        return sorted(out, key=lambda x: x[0])[:limit]

    def data_refs(self, value, limit=10):
        """Addresses in the data sections that hold value (4-byte aligned): tables of pointers, vtables."""
        out, pat = [], struct.pack('<I', value)
        for lo, hi in self.data_ranges():
            j = self.data.find(pat, lo - self.base, hi - self.base)
            while j >= 0 and len(out) < limit:
                if j % 4 == 0:
                    out.append(self.base + j)
                j = self.data.find(pat, j + 1, hi - self.base)
        return out


def window(exe, va, before, after, syms):
    """Lines of a short disassembly around va: decoded from the function start when it is near, so the
    instructions before va are on real boundaries."""
    before, after = min(before, MAX_WINDOW), min(after, MAX_WINDOW)
    insns = []
    start = exe.function_start(va)
    if start is not None and va - start <= 0x2000:
        insns = list(exe.md.disasm(exe.bytes_at(start, va - start + after + 16), start))
    if not any(i.address == va for i in insns):
        # no clean decode from the function start (data in between): the nearest start before va that lands on it
        for s in range(va - before, va + 1):
            insns = list(exe.md.disasm(exe.bytes_at(s, va - s + after + 16), s))
            if any(i.address == va for i in insns):
                break
    lines = []
    for ins in insns:
        if ins.address < va - before:
            continue
        if ins.address > va + after:
            break
        note = annotate(exe, ins, syms)
        mark = '>' if ins.address <= va < ins.address + ins.size else ' '
        lines.append(f'  {mark} {ins.address:08X}  {ins.mnemonic:6s} {ins.op_str}{note}')
    return lines


def annotate(exe, ins, syms):
    notes = []
    for tok in re.findall(r'0x[0-9a-f]+', ins.op_str):
        v = int(tok, 16)
        s = exe.cstr(v)
        if s and len(s) >= 2:
            notes.append(f'"{s[:60]}"')
        elif v in syms:
            notes.append(syms[v][0])
    if ins.mnemonic == 'call' and not notes:
        m = re.fullmatch(r'0x([0-9a-f]+)', ins.op_str)
        if m:
            n, _ = crashreader.name_of(int(m.group(1), 16), syms, exe)
            if n:
                notes.append(n[:-5] if n.endswith(' +0x0') else n)
    return ('   ; ' + ', '.join(notes)) if notes else ''


def func_name(exe, va, syms):
    n, _ = crashreader.name_of(va, syms, exe)
    return n or 'function start unknown'


def parse_int(s):
    try:
        return int(s, 16) if s.lower().startswith('0x') or re.fullmatch(r'[0-9A-Fa-f]{6,8}', s) else int(s, 0)
    except ValueError:
        raise ExeRefError(f'"{s}" is not an address (hex, e.g. 0x0143375C)')


def take(args, flag, default=None, has_value=True):
    if flag not in args:
        return default
    i = args.index(flag)
    if not has_value:
        del args[i]
        return True
    if i + 1 >= len(args) or args[i + 1].startswith('--'):
        del args[i]
        return True
    v = args[i + 1]
    del args[i:i + 2]
    return v


def main(argv):
    args = list(argv[1:])
    if not args or args[0] not in ('str', 'refs', 'dis'):
        print(__doc__)
        return 1 if args else 0
    cmd = args.pop(0)
    exe_path = take(args, '--exe') or crashreader.find_exe()
    if not exe_path or exe_path is True:
        raise ExeRefError('MoHMPGame.exe not found - give it with --exe <path>')
    if not os.path.isfile(exe_path):
        raise FileNotFoundError(exe_path)
    extra = []
    while '--symbols' in args:
        extra.append(take(args, '--symbols'))
    syms = crashreader.read_symbols([crashreader.SYMBOLS] + extra)
    exact = take(args, '--exact', False, has_value=False)
    mx = int(take(args, '--max', '40'))
    dis = take(args, '--dis')
    dis_n = 0 if dis is None else (6 if dis is True else int(dis))
    before = parse_int(take(args, '--before', '0x20'))
    after = parse_int(take(args, '--after', '0x40'))
    if not args:
        raise ExeRefError(f'{cmd} needs a {"text" if cmd == "str" else "address"}')
    exe = Exe(exe_path)

    def show_refs(value):
        found = exe.refs(value, mx)
        data = exe.data_refs(value)
        if data:
            print('    held in data at ' + ', '.join(f'{d:08X}' for d in data) + (' ...' if len(data) >= 10 else '')
                  + '  (a table or a vtable: exeref.py refs <that address> finds its users)')
        for va, ins, kind in found:
            print(f'    {va:08X}  {ins.mnemonic} {ins.op_str:40s} in {func_name(exe, va, syms)}')
            if dis_n:
                for line in window(exe, va, dis_n * 4, dis_n * 4, syms):
                    print('    ' + line)
                print()
        return found

    if cmd == 'dis':
        va = parse_int(args[0])
        if not exe.is_code(va):
            raise ExeRefError(f'{va:#x} is not in the exe\'s code')
        print(f'{va:08X} in {func_name(exe, va, syms)}')
        for line in window(exe, va, before, after, syms):
            print(line)
        return 0
    if cmd == 'refs':
        va = parse_int(args[0])
        s = exe.cstr(va)
        print(f'{va:08X}' + (f'  "{s}"' if s else '') + (f'  {syms[va][0]}' if va in syms else ''))
        found = show_refs(va)
        print(f'{len(found)} place(s)' + (' (--max reached)' if len(found) >= mx else ''))
        return 0
    text = ' '.join(args)
    strings = exe.find_strings(text, exact)
    if not strings:
        print(f'no string {"equal to" if exact else "containing"} "{text}" in the exe')
        return 1
    for va, s in strings[:mx]:
        print(f'{va:08X}  "{s}"')
        found = show_refs(va)
        if not found:
            print('    (no instruction uses its address directly)')
    if len(strings) > mx:
        print(f'... {len(strings) - mx} more strings (--max)')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
    from mohcore import errors
    errors.setup('exeref', gui=False)            # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
