r"""The crash reader on crash reports built here, in the layout the game writes (one line of XML; registers as
"registers: Edi 1c0c28ac, ..." in <contextdata>; a stack of hex values, some of them text):

- the report is read (type, build, crash address, registers, stack) and the bundled symbols load;
- names and hints for known places (the window-position registry read: "the OPFOR core sends this key");
- null registers are marked, another build is noted, the log lines before the crash are picked;
- with --game: the code view on Project OPFOR's MoHMPGame.exe (needs pefile and capstone): the function
  start found for a crash site inside a known function, the null register the faulting instruction reads
  through, the call before a return address, and text on the stack told apart from addresses.

  python test_crashreader.py [--game]
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, TOOL)
import crashreader  # noqa: E402

SITE, FUNCTION = 0x0040794A, 0x00407910      # inside the window-position registry read (build 592256)
DOCUMENTS = 0x009349F0                       # the Documents folder function, a return address on the stack


def report(eip, regs, stack, build=crashreader.BUILD):
    reg_text = ', '.join(f'{k} {v:08x}' for k, v in regs.items())
    return ('<?xml version="1.0" encoding="UTF-8"?><report><version>2</version><sessionid>0123456789abcdef</sessionid>'
            '<type>crash</type><sku>ea.dice.Python.Cl</sku><createtime>2026-10-16 12:00:00</createtime>'
            f'<buildsignature>{build}</buildsignature><systemconfig></systemconfig><categoryid> 0x{eip:X}</categoryid>'
            f'<stack> {" ".join(f"0x{a:X}" for a in stack)}</stack><threads></threads><screenshot></screenshot>'
            f'<memdump></memdump><contextdata>registers: {reg_text}, Eip {eip:08x}, Esp 001ae65c</contextdata></report>')


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''))


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = crashreader.main(['crashreader.py'] + argv)
    return code, buf.getvalue()


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='crashreader_test_')
    try:
        regs = {'Edi': 0x1C0C28AC, 'Esi': 0x182B0C0C, 'Ebx': 0x1C0C28B4, 'Edx': 0, 'Ecx': 0x1C0F45A4, 'Eax': 0, 'Ebp': 0x0019F000}
        text_value = int.from_bytes(b'clie', 'little')                  # text on the stack, as real reports have
        stack = [SITE, 0x0040143D, 0x76FAB5AC, text_value, DOCUMENTS]
        path = os.path.join(work, 'crashreport.xml')
        open(path, 'w', encoding='utf-8').write(report(SITE, regs, stack))

        r = crashreader.read_report(path)
        c.check(r['type'] == 'crash' and r['build'] == crashreader.BUILD and r['category'] == SITE, 'report: type, build and crash address')
        c.check(r['registers'].get('Eip') == SITE and r['registers'].get('Eax') == 0 and r['registers'].get('Edi') == 0x1C0C28AC,
                'report: registers from "registers: Edi 1c0c28ac, ..."', str(r['registers']))
        c.check(r['stack'] == stack, 'report: the stack, in order')

        syms = crashreader.read_symbols([crashreader.SYMBOLS])
        c.check(len(syms) >= 4 and FUNCTION in syms and 'OPFOR core' in syms[FUNCTION][1], 'bundled symbols load, with hints')
        own = os.path.join(work, 'mine.txt')
        open(own, 'w').write('# my notes\n0x00ABCDEF  my function  | my hint\n00123456 another   # trailing comment\nnot a line\n')
        extra = crashreader.read_symbols([own, os.path.join(work, 'missing.txt')])
        c.check(extra == {0xABCDEF: ('my function', 'my hint'), 0x123456: ('another', '')},
                'a --symbols file: 0x prefix, hints, comments; a missing file is skipped', str(extra))
        c.check(crashreader.name_of(FUNCTION, syms, None)[0].startswith('registry read'), 'a known function is named without the exe')
        c.check(crashreader.name_of(0x00DEAD00, syms, None) == ('', ''), 'an unknown address without the exe has no name')

        code, out = run([path, '--no-code'])
        c.check(code == 0, 'runs on the report (--no-code)')
        c.check(f'Crash at 0x{SITE:08X}' in out, 'shows the crash site')
        c.check('Eax 00000000 (null)' in out and 'Edx 00000000 (null)' in out and 'Edi 1C0C28AC (null)' not in out, 'marks null registers')
        c.check('Documents folder' in out, 'names a known function on the stack (the Documents folder)')
        c.check('not build' not in out, f'no build note for build {crashreader.BUILD}')

        other = os.path.join(work, 'other.xml')
        open(other, 'w', encoding='utf-8').write(report(SITE, regs, stack, build='581582'))
        c.check(f'not build {crashreader.BUILD}' in run([other, '--no-code'])[1], 'another build (the unpatched 581582) is noted')

        log = os.path.join(work, 'opfor.log')
        lines = [f'line {k}' for k in range(30)] + ['Kynapse error: spam', 'per frame spam', 'last real line',
                                                     'crash: report written to crashreport.xml', 'after the crash']
        open(log, 'w').write('\n'.join(lines) + '\n')
        before = crashreader.log_before_crash(log)
        c.check(before[-1] == 'crash: report written to crashreport.xml' and before[-2] == 'last real line'
                and not any('spam' in l for l in before) and len(before) == 19, 'log: the 18 lines before the crash line, spam left out')
        open(os.path.join(work, 'none.log'), 'w').write('nothing here\n')
        c.check(crashreader.log_before_crash(os.path.join(work, 'none.log')) == [] and crashreader.log_before_crash(os.path.join(work, 'x.log')) == [],
                'log: none without a crash line or a file')
        c.check('Last log lines before the crash' in run([path, '--no-code', '--log', log])[1], '--log shows the lines before the crash')

        if '--game' in argv:
            exe = crashreader.find_exe()
            try:
                image = crashreader.Image(exe) if exe else None
            except ImportError:
                image = None
                c.check(False, 'code view needs: pip install pefile capstone')
            if exe is None:
                c.check(False, 'MoHMPGame.exe of Project OPFOR not found')
            if image is not None:
                c.check(image.is_code(SITE) and not image.is_code(0x76FAB5AC), 'the exe: code ranges')
                c.check(image.function_start(SITE) == FUNCTION, 'the function start found for the crash site',
                        f'0x{image.function_start(SITE) or 0:08X}')
                ins = [x for x in image.disasm(SITE - 0x20, SITE + 0x10) if x.address == SITE]
                zero = {k: 0 for k in crashreader.REGISTERS}
                c.check(bool(ins) and bool(crashreader.null_operands(ins[0], zero)), 'the faulting instruction reads memory through a register')
                calls = [x for x in image.disasm(FUNCTION, FUNCTION + 0x60) if x.mnemonic == 'call']
                if calls:
                    ret = calls[0].address + calls[0].size
                    back = image.call_before(ret)
                    c.check(back is not None and back.address == calls[0].address, 'the call before a return address')
                code, out = run([path, '--exe', exe])
                c.check(code == 0 and f'Code (function starts near 0x{FUNCTION:08X})' in out, 'the code view around the crash')
                c.check('(text on the stack: "clie"' in out, 'text on the stack is told apart from addresses')
                c.check('(outside the exe' in out, 'addresses outside the exe are marked')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
