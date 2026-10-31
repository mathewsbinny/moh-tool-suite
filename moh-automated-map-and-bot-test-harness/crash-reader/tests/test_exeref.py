r"""exeref.py on a program built here (quick), and with --game on Project OPFOR's MoHMPGame.exe (build 592256):

- a string found (part of it, any case; or whole with --exact), the instruction that pushes its address, in which
  function (named from a symbols file); a function's callers (call rel32); an address held in data (a table);
- the short window: the line at the address marked, the string and the called function named, the window capped;
- the command line, and an address outside the code refused.
With --game: "ClientWindowPosX" (0x014FCFA8) pushed at 0x0043A2D7 before the registry write 0x00407980, which is
called there at 0x0043A2DC; the Documents folder function's SHGetSpecialFolderPathA call at 0x009349FD;
"SOFTWARE\\DICE\\PYTHON\\Game" held in data (0x015F88A8); "presenceRequestOnline" held in the extern table.
Needs pefile and capstone (as the crash reader's code view); without them the test says so and passes.

  python test_exeref.py [--game]
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, TOOL)


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''), flush=True)


def pe32(sections, base=0x400000):
    """A minimal valid PE32 image: [(name, rva, data, characteristics)]."""
    falign, salign, head = 0x200, 0x1000, 0x400
    raw, pos = [], head
    for name, rva, data, ch in sections:
        size = (len(data) + falign - 1) // falign * falign
        raw.append((name, rva, data, ch, pos, size))
        pos += size
    image = max(rva + ((len(d) + salign - 1) // salign) * salign for _, rva, d, _ in sections)
    out = bytearray(pos)
    out[0:2] = b'MZ'
    struct.pack_into('<I', out, 0x3C, 0x80)
    out[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', out, 0x84, 0x14C, len(sections), 0x12345678, 0, 0, 0xE0, 0x0102)
    code = sum(s[5] for s in raw if s[3] & 0x20)
    struct.pack_into('<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII', out, 0x98, 0x10B, 9, 0, code, 0, 0, sections[0][1], sections[0][1],
                     sections[-1][1], base, salign, falign, 5, 1, 0, 0, 5, 1, 0, image, head, 0, 3, 0,
                     0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    for k, (name, rva, data, ch, ptr, size) in enumerate(raw):
        struct.pack_into('<8sIIIIIIHHI', out, 0x98 + 0xE0 + 40 * k, name.encode(), len(data), rva, size, ptr, 0, 0, 0, 0, ch)
        out[ptr:ptr + len(data)] = data
    return bytes(out)


def build():
    """Code: 16 int3, f1 at 0x401010 (push ebp; mov ebp, esp; push "Server.TestSetting"; call f2; add esp, 4; pop ebp;
    ret), int3 padding, f2 at 0x401030 (mov eax, table; ret). Data: the string, and a table holding its address."""
    base = 0x400000
    s_va = base + 0x2004
    rdata = b'\0' * 4 + b'Server.TestSetting\0'
    rdata += b'\0' * ((-len(rdata)) % 4)
    table_va = base + 0x2000 + len(rdata)
    rdata += struct.pack('<II', s_va, 7)
    f1, f2 = base + 0x1010, base + 0x1030
    code = bytearray(b'\xcc' * 0x10)
    code += b'\x55\x8b\xec' + b'\x68' + struct.pack('<I', s_va)
    call_at = base + 0x1000 + len(code)
    code += b'\xe8' + struct.pack('<i', f2 - (call_at + 5))
    code += b'\x83\xc4\x04\x5d\xc3'
    code += b'\xcc' * (0x30 - len(code))
    code += b'\xb8' + struct.pack('<I', table_va) + b'\xc3' + b'\xcc' * 10
    exe = pe32([('.text', 0x1000, bytes(code), 0x60000020), ('.rdata', 0x2000, rdata, 0x40000040)], base)
    return exe, dict(s_va=s_va, table_va=table_va, f1=f1, f2=f2, push=f1 + 3, call=call_at)


def run(*args):
    r = subprocess.run([sys.executable, os.path.join(TOOL, 'exeref.py'), *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return r.returncode, r.stdout + r.stderr


def main(argv):
    c = Checks()
    try:
        import capstone  # noqa: F401
        import pefile  # noqa: F401
    except ImportError:
        print('pefile / capstone not installed (pip install pefile capstone) - exeref not tested')
        return 0
    import exeref
    work = tempfile.mkdtemp(prefix='exeref_')
    try:
        data, at = build()
        path = os.path.join(work, 'test.exe')
        open(path, 'wb').write(data)
        syms = os.path.join(work, 'syms.txt')
        open(syms, 'w').write(f'{at["f1"]:08X}  read the test setting\n{at["f2"]:08X}  table getter\n')
        exe = exeref.Exe(path)
        found = exe.find_strings('testsetting')
        c.check(found == [(at['s_va'], 'Server.TestSetting')] and exe.find_strings('Server.TestSetting', exact=True) == found
                and not exe.find_strings('TestSetting', exact=True), 'a string by part (any case) and whole (--exact)', str(found))
        refs = exe.refs(at['s_va'])
        c.check([(va, k) for va, _, k in refs] == [(at['push'], 'operand')], 'the push of the string\'s address', str(refs))
        refs = exe.refs(at['f2'])
        c.check([(va, k) for va, _, k in refs] == [(at['call'], 'call')], 'a function\'s caller (call rel32)', str(refs))
        c.check(exe.data_refs(at['s_va']) == [at['table_va']], 'the string\'s address held in data (a table)')
        code, out = run('str', 'TestSetting', '--exe', path, '--symbols', syms, '--dis', '3')
        c.check(code == 0 and f'{at["push"]:08X}  push' in out and 'in read the test setting +0x3' in out
                and f'> {at["push"]:08X}' in out and '"Server.TestSetting"' in out and 'table getter' in out
                and f'held in data at {at["table_va"]:08X}' in out, 'str command: site, function, window with names', out)
        code, out = run('dis', f'{at["call"]:#x}', '--exe', path, '--symbols', syms, '--before', '0x1000', '--after', '4')
        lines = [l for l in out.splitlines() if l.startswith('  ')]
        c.check(code == 0 and f'> {at["call"]:08X}  call' in out and lines and lines[0].split()[0] == f'{at["f1"]:08X}',
                'dis command: from the function start (the window capped), the address marked', out)
        code, out = run('refs', f'{at["f2"]:#x}', '--exe', path)
        c.check(code == 0 and '1 place(s)' in out, 'refs command', out)
        code, out = run('refs', '--dis', f'{at["f2"]:#x}', '--exe', path)
        c.check(code == 0 and '1 place(s)' in out and f'> {at["call"]:08X}' in out,
                'refs --dis <address>: the address is not taken as N (the default window)', out)
        code, out = run('refs', f'{at["f2"]:#x}', '--dis', '2', '--exe', path)
        c.check(code == 0 and '1 place(s)' in out and f'> {at["call"]:08X}' in out, 'refs <address> --dis 2: a plain decimal is N', out)
        code, out = run('dis', f'{at["s_va"]:#x}', '--exe', path)
        c.check(code == 1 and 'not in the exe\'s code' in out and 'what to do' in out, 'an address outside the code is refused', out)

        if '--game' in argv:
            import crashreader
            game = crashreader.find_exe()
            if not game:
                print('skip --game: MoHMPGame.exe not found')
            else:
                code, out = run('str', 'ClientWindowPosX', '--exact')
                c.check(code == 0 and '014FCFA8' in out and '0043A2D7  push 0x14fcfa8' in out, 'game: the value name pushed '
                        'before the registry write', out[-400:])
                code, out = run('refs', '0x407980')
                c.check(code == 0 and 'registry write' in out and '0043A2DC  call 0x407980' in out,
                        'game: the registry write (named from the symbols) and its callers', out[-400:])
                code, out = run('dis', '0x9349FD', '--before', '0x10', '--after', '0x10')
                c.check(code == 0 and '> 009349FD  call   dword ptr [0x1c60894]' in out and 'Documents folder' in out,
                        'game: the Documents folder function\'s shell call, the function named', out)
                code, out = run('str', 'SOFTWARE\\DICE\\PYTHON\\Game', '--exact')
                c.check(code == 0 and 'held in data at 015F88A8' in out, 'game: the window-position key held in data', out)
                code, out = run('str', 'presenceRequestOnline', '--exact')
                c.check(code == 0 and 'held in data at 0151EAE8' in out, 'game: an FSCommand name held in the extern table', out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
