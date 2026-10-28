r"""The menu tools on data built here (quick), and with --game on Project OPFOR's game copy:

- apt.py's decoder: GetURL2 without an argument, GotoFrame2 with an aligned 32-bit one, every function body decoded to
  its end, the structured instruction list;
- aptcheck.py: a clean movie passes; a size-keeping patch verifies; each rule caught when broken (a PushData list
  shared, an End inside a function, a branch into an instruction, a constant index past the table, a decode shift, a
  member made shorter, bytes the patch file does not name); patch files: applied as AptTool does (appends and
  @labels), expected bytes checked, overlapping lines, size changes, lines without a comment; the command line;
- aptrefs.py: extern reads (plain and name$param), FSCommands (strings and fscommandProxy calls), catchEvent and
  gameEventListener handlers with their parameter counts; the exe's name / id table found in a built exe;
- mohloc.py: a language file and histogram built here - keys, WIN32_ PC texts, hashes, single- and two-byte
  characters; the command line.
With --game: every OPFOR menu patch file lints with no error; every movie of the game copy's movie sets passes the
rules by itself; the exe's table (574 names at 0x0143C898, known getter cases); known events with their parameter
counts; English, Russian and Japanese texts.

  python test_menu_tools.py [--game]
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
import menucommon as mc  # noqa: E402
import aptcheck  # noqa: E402
import aptrefs  # noqa: E402
import mohloc  # noqa: E402
import apt  # noqa: E402  (menucommon put the reader on the path)


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''), flush=True)


# ------------------------------------------------------------------------------------------------ a movie built here

STRINGS = ['extern', 'isLive', 'gameEventListener', 'onThing', 'catchEvent', '_root', 'hit', 'fscommandProxy',
           'presenceRequestOnline', 'unlockWeaponData$', 'FSCommand:quitgame', 'value']


class Asm:
    """Writes bytecode into the .apt buffer at its file offsets (alignment counts from the start of the file)."""

    def __init__(self, a):
        self.a = a
        self.marks = {}

    def at(self):
        return len(self.a)

    def b(self, *bs):
        self.a.extend(bs)

    def aligned(self, op, *words, fmt='I'):
        self.a.append(op)
        while len(self.a) % 4:
            self.a.append(0x00)
        for w in words:
            self.a.extend(struct.pack('<' + fmt, w))

    def mark(self, name):
        self.marks[name] = len(self.a)


def build_movie():
    """(.apt, .const, marks): one frame whose action defines gameEventListener.onThing = function(2 params) with a
    PushData, GotoFrame2 and GetURL2 in its body; reads extern.isLive with Not Not / BranchIfTrue; registers
    _root.catchEvent("hit", function(1 param)); calls fscommandProxy("presenceRequestOnline"); reads
    extern["unlockWeaponData$" + 7]; sends "FSCommand:quitgame" through GetURL2; a second PushData; End."""
    a = bytearray(b'Apt Data:74\x1a\0\0\0\0\0')
    K = {s: i for i, s in enumerate(STRINGS)}
    # lists first (data), then code
    while len(a) % 4:
        a.append(0)
    pool_list = len(a)
    a.extend(struct.pack('<%dI' % len(STRINGS), *range(len(STRINGS))))
    list1 = len(a)
    a.extend(struct.pack('<I', K['value']))
    list2 = len(a)
    a.extend(struct.pack('<I', K['isLive']))
    while len(a) % 4:
        a.append(0)
    code = len(a)
    s = Asm(a)
    s.aligned(0x88, len(STRINGS), pool_list)                      # ConstantPool
    s.mark('extern')
    s.b(0xAE, K['extern'], 0xAF, K['isLive'])                     # extern.isLive
    s.mark('notnot')
    s.b(0x12, 0x12)                                               # Not Not
    s.mark('branch')
    s.aligned(0x9D, 0, fmt='i')                                   # BranchIfTrue +2 (patched below)
    br_end = len(a)
    s.mark('pushbyte')
    s.b(0xB5, 5)                                                  # EA_PushByte 5
    s.mark('target')
    s.b(0x17)                                                     # Pop
    struct.pack_into('<i', a, br_end - 4, s.marks['target'] - br_end)
    # gameEventListener.onThing = function (a, b) { PushData [value]; GotoFrame2 0; GetURL2; Pop }
    s.b(0xAE, K['gameEventListener'], 0xA2, K['onThing'])
    s.mark('def1')
    s.aligned(0x8E)
    hdr = len(a)
    a.extend(struct.pack('<IiBBBBIIII', 0, 2, 3, 0, 0, 0, 0, 0, 0, 0))   # name, params, regs, ..., size at +16
    body = len(a)
    assert body == hdr + 28
    s.mark('push1')
    s.aligned(0x96, 1, list1)                                     # PushData "value"
    s.mark('goto2')
    s.aligned(0x9F, 0)                                            # GotoFrame2, aligned 32-bit argument
    s.mark('geturl2')
    s.b(0x9A)                                                     # GetURL2, no argument
    s.b(0x59, 0x17)                                               # push 0 / pop (filler)
    s.mark('body_end')
    struct.pack_into('<i', a, hdr + 16, len(a) - body)
    s.b(0x4F)                                                     # SetMember
    # _root.catchEvent("hit", function (x) {})
    s.aligned(0x8E)
    hdr = len(a)
    a.extend(struct.pack('<IiBBBBIIII', 0, 1, 2, 0, 0, 0, 0, 0, 0, 0))
    body = len(a)
    s.b(0x59, 0x17)
    struct.pack_into('<i', a, hdr + 16, len(a) - body)
    s.b(0xA2, K['hit'], 0xB5, 2, 0xAE, K['_root'], 0xB2, K['catchEvent'])
    # fscommandProxy("presenceRequestOnline")
    s.b(0xA2, K['presenceRequestOnline'], 0x5A, 0x56, 0xB2, K['fscommandProxy'])
    # extern["unlockWeaponData$" + 7]
    s.b(0xAE, K['extern'], 0xA2, K['unlockWeaponData$'], 0xB5, 7, 0x47, 0x4E, 0x17)
    # getURL2("FSCommand:quitgame", 0)
    s.b(0xA2, K['FSCommand:quitgame'], 0x59, 0x9A)
    s.mark('push2')
    s.aligned(0x96, 1, list2)                                     # PushData "isLive"
    s.b(0x17, 0x00)                                               # Pop, End
    s.mark('end')
    # structure: action item, frame, movie
    while len(a) % 4:
        a.append(0)
    action = len(a)
    a.extend(struct.pack('<II', 1, code))
    items = len(a)
    a.extend(struct.pack('<I', action))
    frames = len(a)
    a.extend(struct.pack('<II', 1, items))
    chars = len(a)
    a.extend(b'\0' * 4)
    movie = len(a)
    a.extend(struct.pack('<IIIIIIIiiIIIII', 9, 0x09876543, 1, frames, 0, 1, chars, 640, 480, 33, 0, 0, 0, 0))
    struct.pack_into('<I', a, chars, movie)
    c = bytearray(b'Apt constant file\x1a\0\0')
    entries = 32
    c += struct.pack('<III', movie, len(STRINGS), entries)
    c += b'\0' * (entries - len(c))
    strs = entries + 8 * len(STRINGS)
    blob = bytearray()
    for i, t in enumerate(STRINGS):
        struct.pack_into('<II', c, entries + 8 * i, 1, strs + len(blob)) if len(c) >= entries + 8 * i + 8 else None
        if len(c) < entries + 8 * i + 8:
            c += struct.pack('<II', 1, strs + len(blob))
        blob += t.encode() + b'\0'
    c += blob
    s.marks.update(list1=list1, list2=list2, code=code)
    return bytes(a), bytes(c), s.marks


def bigf(members):
    order = list(members)
    index = sum(8 + len(n) + 1 for n in order)
    pos, offs = 16 + index, []
    for n in order:
        pos = (pos + 3) & ~3
        offs.append(pos)
        pos += len(members[n])
    out = bytearray(pos)
    out[0:4] = b'BIGF'
    struct.pack_into('<I', out, 4, pos)
    struct.pack_into('>II', out, 8, len(order), 16 + index)
    ip = 16
    for n, o in zip(order, offs):
        struct.pack_into('>II', out, ip, o, len(members[n]))
        out[ip + 8:ip + 8 + len(n)] = n.encode()
        ip += 8 + len(n) + 1
        out[o:o + len(members[n])] = members[n]
    return bytes(out)


def patched(members, apt_changes=None, const_changes=None):
    m = {k: bytearray(v) for k, v in members.items()}
    for off, bs in (apt_changes or {}).items():
        m['t.apt'][off:off + len(bs)] = bs
    for off, bs in (const_changes or {}).items():
        m['t.const'][off:off + len(bs)] = bs
    return {k: bytes(v) for k, v in m.items()}


def verify_report(orig, new, named=None):
    rep = aptcheck.Report('test')
    aptcheck.verify(orig, new, rep, named)
    return rep, '\n'.join(rep.lines)


def run(script, *args, cwd=None):
    r = subprocess.run([sys.executable, os.path.join(ROOT, script), *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', cwd=cwd)
    return r.returncode, r.stdout + r.stderr


def quick(c, work):
    a, k, marks = build_movie()
    members = {'t.apt': a, 't.const': k}
    m = apt.Movie(data=('t', a, k))
    code = aptcheck.Code(m)
    ops = {ins.at: ins for ins, *_ in code.recs}
    c.check(ops.get(marks['geturl2']) is not None and ops[marks['geturl2']].end == marks['geturl2'] + 1,
            'apt: GetURL2 has no argument')
    g = ops.get(marks['goto2'])
    c.check(g is not None and g.end == ((marks['goto2'] + 4) & ~3) + 4, 'apt: GotoFrame2 has an aligned 32-bit argument')
    c.check(len(code.funcs) == 2 and not code.findings(), 'apt / aptcheck: both function bodies decode to their end, no finding',
            str(code.findings()))
    c.check(any(ins.op == 0 for ins, *_ in code.recs) and ops[marks['push1']].plist == marks['list1'],
            'apt: the structured instruction list (PushData lists, End)')

    # verify: a good patch
    good = patched(members, {marks['notnot']: b'\x17\x73'})
    rep, text = verify_report(members, good, {'t.apt': {marks['notnot'], marks['notnot'] + 1}})
    c.check(rep.errors == 0 and 'now' in text and 'EA_PushTrue' in text and 'as before' in text,
            'verify: Not Not -> Pop / push true passes, the change named', text)
    # the rules broken
    cases = [
        ('shared-list', {((marks['push2'] + 4) & ~3) + 4: struct.pack('<I', marks['list1'])}, 'a second PushData on one list'),
        ('end-in-function', {marks['body_end'] - 2: b'\x00\x00'}, 'End (00) inside a function'),
        ('zero-opcode', {marks['body_end'] - 2: b'\x00\x00'}, 'a changed byte now opcode 00'),
        ('branch-target', {(marks['branch'] + 4) & ~3: struct.pack('<i', 1)}, 'a branch into an instruction'),
        ('const-index', {marks['list1']: struct.pack('<I', 999)}, 'a PushData constant index past the table'),
        ('decode-shift', {marks['pushbyte']: b'\xB7'}, 'an opcode with a longer argument'),
    ]
    for kind, change, what in cases:
        rep, text = verify_report(members, patched(members, change))
        c.check(rep.errors > 0 and kind in text, f'verify: {what} is an error ({kind})', text[-400:])
    short = dict(members, **{'t.apt': a[:-4]})
    rep, text = verify_report(members, short)
    c.check(rep.errors > 0 and 'shorter' in text, 'verify: a member made shorter is an error')
    rep, text = verify_report(members, good, {'t.apt': {marks['notnot']}})
    c.check(rep.errors == 1 and 'unexpected-change' in text, 'verify: a byte the patch does not name is an error', text[-300:])
    grown = dict(good, **{'t.const': k + b'NEW\0'})
    rep, text = verify_report(members, grown)
    c.check(rep.errors == 0 and 'appended' in text, 'verify: bytes appended at a member\'s end are a note')
    entry = 32 + 8 * STRINGS.index('value') + 4
    rep, text = verify_report(members, patched(members, const_changes={entry: struct.pack('<I', 32 + 8 * len(STRINGS))}))
    c.check(rep.errors == 0 and 'constant 11 value: "value" -> "extern"' in text, 'verify: a constant retargeted is named', text)

    # patch files (AptTool's format)
    res = os.path.join(work, 't.res')
    open(res, 'wb').write(bigf(members))
    pf = os.path.join(work, 't.patch')
    open(pf, 'w').write('# test patch\n\n'
                        f't.apt {marks["notnot"]:X} 12 12 -> 17 73   # isLive popped, true pushed\n'
                        '# the constant gets new text\n'
                        'append t.const NEWTEXT "HELLO WORLD"\n'
                        f't.const {entry:X} {" ".join(f"{x:02X}" for x in k[entry:entry + 4])} -> @NEWTEXT\n')
    lines, problems = mc.parse_patch(pf)
    new, named, applied = mc.apply_patch(members, lines)
    nm = apt.Movie(data=('t', new['t.apt'], new['t.const']))
    c.check(not problems and not applied and nm.const[STRINGS.index('value')] == '"HELLO WORLD"' and new['t.apt'][marks['notnot']] == 0x17,
            'patch files: applied as AptTool does (append, @label, in-place bytes)', str(problems + applied))
    code_, out = run('aptcheck.py', 'lint', pf, '--source', res)
    c.check(code_ == 0 and '-> OK' in out and 'warning' not in out, 'lint: a good patch file passes (exit 0)', out[-600:])
    bad = os.path.join(work, 'bad.patch')
    open(bad, 'w').write(f't.apt {marks["notnot"]:X} 12 13 -> 17 73   # wrong expected byte\n'
                         f't.apt {marks["notnot"]:X} 12 -> 17\n'
                         f't.apt {marks["pushbyte"]:X} B5 05 -> B5   # size change\n'
                         't.apt zz 12 -> 17   # not hex\n')
    code_, out = run('aptcheck.py', 'lint', bad, '--source', res)
    c.check(code_ == 1 and 'expected 12 13, found 12 17' not in out and 'expected 12 13' in out and 'also patched on line 1' in out
            and 'size change' in out and 'must be hex' in out and 'no comment' in out,
            'lint: wrong expected bytes, overlapping lines, size change, bad hex, a line without a comment (exit 1)', out[-900:])
    newres = os.path.join(work, 'new.res')
    open(newres, 'wb').write(bigf(patched(members, {((marks['push2'] + 4) & ~3) + 4: struct.pack('<I', marks['list1'])})))
    code_, out = run('aptcheck.py', 'verify', res, newres)
    c.check(code_ == 1 and 'shared-list' in out, 'verify command: a shared PushData list fails (exit 1)', out[-400:])
    code_, out = run('aptcheck.py', 'check', res)
    c.check(code_ == 0 and '2 functions' in out, 'check command on a .res', out[-300:])

    # aptrefs
    refs = aptrefs.movie_refs(code)
    c.check(set(refs['extern']) == {'isLive'} and set(refs['extern_param']) == {'unlockWeaponData'},
            'aptrefs: extern reads, plain and name$param', str(refs['extern']) + str(refs['extern_param']))
    c.check(set(refs['fscommand']) == {'presenceRequestOnline', 'quitgame'}, 'aptrefs: FSCommands (proxy call and string)',
            str(refs['fscommand']))
    ev = {n: [u[1] for u in v] for n, v in refs['events'].items()}
    c.check(ev == {'onThing': [2], 'hit': [1]}, 'aptrefs: gameEventListener and catchEvent handlers with parameter counts', str(ev))
    exe = os.path.join(work, 'test.exe')
    open(exe, 'wb').write(build_exe())
    img = mc.PEImage(exe)
    table = aptrefs.extern_table(img)
    names = {n: i for n, i, _ in table}
    c.check(len(table) == 61 and names.get('presenceRequestOnline') == 0x1D and names.get('isLive') == 0x87,
            'aptrefs: the name / id table found in a built exe', f'{len(table)} {list(names)[:5]}')
    c.check('uInt' in img.strings(), 'exe strings: one after non-zero data, at an aligned offset')
    rows = aptrefs.cross_reference([('menu:t', 't', refs)], table, {}, img.strings())
    r = {x['name']: x for x in rows}
    c.check(r['isLive']['id'] == 0x87 and r['onThing']['in_exe'] is False and r['quitgame']['movies'] == {'menu:t': 1},
            'aptrefs: cross reference (ids, in the exe or not, movies)', str(rows)[:300])
    code_, out = run('aptrefs.py', 'table', '--exe', exe)
    c.check(code_ == 0 and '61 names' in out and 'not build 795745' in out, 'aptrefs table command', out[-300:])
    code_, out = run('aptrefs.py', 'movie', res)
    c.check(code_ == 0 and 'presenceRequestOnline' in out and 'onThing' in out, 'aptrefs movie command', out[-300:])

    # mohloc
    normal, hist = build_loc()
    lp = os.path.join(work, 'language_test_normal.bin')
    open(lp, 'wb').write(normal)
    open(os.path.join(work, 'language_test_histogram.bin'), 'wb').write(hist)
    loc = mohloc.load('test', path=lp)
    c.check(loc.lookup('MENU_PLAY_NOW')[2] == 'PLAY NOW' and loc.lookup('PC_SERVER_BROWSER')[1] == 'WIN32_PC_SERVER_BROWSER',
            'mohloc: keys, the PC text under WIN32_<key> first')
    c.check(loc.lookup('RU')[2] == 'ок' and loc.lookup('JP')[2] == '殺す', 'mohloc: single-byte and two-byte characters',
            repr(loc.lookup('RU')) + repr(loc.lookup('JP')))
    c.check(loc.lookup(f'{mohloc.khash("MENU_PLAY_NOW"):#x}')[2] == 'PLAY NOW' and loc.lookup('NOPE') is None,
            'mohloc: lookup by hash; a missing key')
    code_, out = run('mohloc.py', 'get', 'MENU_PLAY_NOW', 'PC_SERVER_BROWSER', '--file', lp)
    c.check(code_ == 0 and "'PLAY NOW'" in out and "'SERVER BROWSER'" in out, 'mohloc get command', out)
    code_, out = run('mohloc.py', 'get', 'NOPE', '--file', lp)
    c.check(code_ == 1 and 'no text' in out, 'mohloc get: an unknown key exits 1', out)
    code_, out = run('mohloc.py', 'search', 'browser', '--file', lp, '--no-keys')
    c.check(code_ == 0 and '1 text(s)' in out, 'mohloc search command', out)


def build_exe():
    """A minimal 32-bit PE: .text (a ret), .rdata with strings and an extern table of 61 {char*, id} pairs."""
    base, text_rva, data_rva = 0x400000, 0x1000, 0x2000
    rdata = bytearray(b'\0' * 4)
    names = ['setInputState', 'presenceRequestOnline', 'isLive'] + [f'name{i}' for i in range(58)]
    ids = [0x0D, 0x1D, 0x87] + [0x100 + i for i in range(58)]
    ptrs = []
    for n in names:
        while len(rdata) % 4:
            rdata.append(0)
        ptrs.append(base + data_rva + len(rdata))
        rdata += n.encode() + b'\0'
    rdata += b' \xc7q\xfc?'                       # float data that happens to be printable, then "uInt" aligned
    while len(rdata) % 4:
        rdata.append(0x3F)
    rdata += b'uInt\0'
    while len(rdata) % 4:
        rdata.append(0)
    rdata += b'\xff' * 8                          # not a pair: the table's start
    for p, i in zip(ptrs, ids):
        rdata += struct.pack('<II', p, i)
    rdata += b'\xff' * 8
    return pe32([('.text', text_rva, b'\xc3' + b'\xcc' * 15, 0x60000020), ('.rdata', data_rva, bytes(rdata), 0x40000040)], base)


def pe32(sections, base=0x400000, stamp=0x12345678):
    """A minimal valid PE32 image: [(name, rva, data, characteristics)]."""
    falign, salign = 0x200, 0x1000
    head = 0x400
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
    struct.pack_into('<HHIIIHH', out, 0x84, 0x14C, len(sections), stamp, 0, 0, 0xE0, 0x0102)
    code = sum(s[5] for s in raw if s[3] & 0x20)
    struct.pack_into('<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII', out, 0x98, 0x10B, 9, 0, code, 0, 0, sections[0][1], sections[0][1],
                     sections[-1][1], base, salign, falign, 5, 1, 0, 0, 5, 1, 0, image, head, 0, 3, 0,
                     0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    sh = 0x98 + 0xE0
    for k, (name, rva, data, ch, ptr, size) in enumerate(raw):
        struct.pack_into('<8sIIIIIIHHI', out, sh + 40 * k, name.encode(), len(data), rva, size, ptr, 0, 0, 0, 0, ch)
        out[ptr:ptr + len(data)] = data
    return bytes(out)


def build_loc():
    """A language file and its histogram: MENU_PLAY_NOW, WIN32_PC_SERVER_BROWSER, RU ('ок': bytes 0x81 0x82), JP
    ('殺す': the two-byte character 0x80 0x85, then the single-byte 0x83)."""
    table = [0] * 3072
    table[0x80] = 2                               # a lead byte: page 2 (entries 256 + low 7 bits of the next byte)
    table[0x81], table[0x82], table[0x83] = 0x043E, 0x043A, 0x3059
    table[256 + 5] = 0x6BBA
    hist = struct.pack('<III', 0x39001, 12 + 2 * 3072 - 8, 3072) + struct.pack('<3072H', *table)
    texts = {'MENU_PLAY_NOW': b'PLAY NOW', 'WIN32_PC_SERVER_BROWSER': b'SERVER BROWSER', 'RU': b'\x81\x82',
             'JP': b'\x80\x85\x83'}
    items = sorted((mohloc.khash(k), v) for k, v in texts.items())
    strings, offs = bytearray(), []
    for h, v in items:
        offs.append(len(strings))
        strings += v + b'\0'
    table_off = 0x1C
    strings_off = table_off + 8 * len(items)
    body = bytearray(b'NORMAL'.ljust(16, b'\0'))
    for (h, v), o in zip(items, offs):
        body += struct.pack('<II', h, o)
    body += strings
    data = struct.pack('<5I', 0x39000, 20 + len(body) - 8 + 0, len(items), table_off, strings_off) + body
    data = data[:4] + struct.pack('<I', len(data) - 8) + data[8:]
    return data, hist


# ------------------------------------------------------------------------------------------------ the game copy

def game(c):
    root = mc.find_opfor()
    import glob
    patches = sorted(glob.glob(os.path.join(root, 'src', 'menus', '*.patch')))
    if patches and os.path.isfile(os.path.join(root, 'original', 'Package', 'menu', 'main', 'mainmenu-00.fbrb')):
        code_, out = run('aptcheck.py', 'lint', *patches, '--brief')
        c.check(code_ == 0 and out.count('-> OK') == len(patches), f'lint: OPFOR\'s {len(patches)} menu patch files, no error',
                '\n'.join(l for l in out.splitlines() if 'error' in l)[:800])
    else:
        print('skip OPFOR\'s patch files (no src\\menus or no pristine bundle)')
    found, bad = 0, []
    for key, rel in mc.MOVIE_SETS:
        layers = mc.bundle_layers(root, rel)
        if not layers:
            continue
        from mohcore import fbrb
        by_bundle = {}
        for name, (bundle, entry) in mc.bundle_movies(layers).items():
            by_bundle.setdefault(bundle, []).append(entry)
        for bundle, entries in by_bundle.items():
            for entry, data in fbrb.read(bundle, entries).items():
                try:
                    m = mc.movie_from_members(mc.parse_bigf(data, entry), entry)
                except mc.MenuToolError:
                    continue
                found += 1
                f = aptcheck.Code(m).findings()
                if f:
                    bad.append(f'{key}:{entry} {list(f.values())[0].text}')
    c.check(found > 100 and not bad, f'every movie of the game copy\'s movie sets passes the rules by itself ({found} movies)',
            '; '.join(bad[:5]))
    img = mc.PEImage(mc.find_exe(root))
    table = aptrefs.extern_table(img)
    names = {n: i for n, i, _ in table}
    cases = aptrefs.getter_cases(img)
    c.check(len(table) == 574 and table[0][2] == 0x143C898 and names['platform'] == 0xB9 and names['isLive'] == 0x87,
            'aptrefs: the exe\'s table (574 names at 0x0143C898, platform 0xB9, isLive 0x87)')
    c.check(cases.get('generic', {}).get(0xB9) == 0x95B7D3 and cases['generic'].get(0x13C) == 0x95AF2E,
            'aptrefs: getter cases (platform -> 0x95B7D3, editableInputList -> 0x95AF2E)')
    found = aptrefs.scan_movies(['main_root'], None, root) + aptrefs.scan_movies(None, {'mp_hud'}, root)
    ev = {}
    for label, name, refs in found:
        for n, uses in refs['events'].items():
            ev.setdefault(n, set()).update(u[1] for u in uses)
    c.check(ev.get('presenceLoginAccount') == {2} and ev.get('serverSystemMessage') == {3} and ev.get('uInt') == {1},
            'aptrefs: presenceLoginAccount (2), serverSystemMessage (3), uInt (1) handlers', str({k: ev.get(k) for k in
                                                                                                ('presenceLoginAccount', 'serverSystemMessage', 'uInt')}))
    loc = mohloc.load('english', root)
    c.check(loc.lookup('MENU_PLAY_NOW')[2] == 'PLAY NOW' and loc.lookup('PC_SERVER_BROWSER')[2] == 'SERVER BROWSER'
            and loc.lookup('IDK_A')[2] == 'A' and len(loc.raw) > 7000, 'mohloc: English keys, PC texts, the pad key names')
    ru, jp = mohloc.load('russian', root), mohloc.load('japanese', root)
    cyr = sum(any('Ѐ' <= ch <= 'ӿ' for ch in ru.text(h)) for h in ru.order)
    bad_jp = sum('?' in jp.text(h) or '\\x' in jp.text(h) for h in jp.order)
    c.check(cyr > 5000 and bad_jp < 20, f'mohloc: Russian ({cyr} Cyrillic texts) and Japanese ({bad_jp} with an undecoded byte)')


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='menu_tools_')
    try:
        quick(c, work)
        if '--game' in argv:
            game(c)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
