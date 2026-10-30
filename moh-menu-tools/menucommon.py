r"""Shared helpers of the menu tools: finding Project OPFOR's game copy, reading menu movies from files or straight from
the game's bundles (read only), and OPFOR's menu patch format.

Movie names on the command line:
  a path            a menu .res (BIGF archive of <name>.apt / .const / .geo), or a .apt with its .const beside it
  <name>            Menu/<name>.res of the main menu bundle the game loads (Package\menu\main\mainmenu-00.fbrb over
                    Dist\win32\menu\main\mainmenu-00.fbrb), e.g. main_nav
  pristine:<name>   the same from OPFOR's pristine copy (original\Package\menu\main\mainmenu-00.fbrb, kept by
                    build_menus.bat), i.e. before OPFOR's menu patches
  <bundle.fbrb>:<name>   Menu/<name>.res of any bundle
Nothing is ever written into the game folder.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..'))
for _p in (os.path.join(SUITE, 'core'), os.path.join(SUITE, 'moh-console-readers', 'apt')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import apt  # noqa: E402  (the suite's APT reader)

MENU_BUNDLE = 'menu/main/mainmenu-00.fbrb'
# bundles with menu movies: (label, path under MP\dist\win32 and its patch\); the patch's entries win
MOVIE_SETS = [('menu', 'menu/main/mainmenu-00.fbrb'), ('mp_hud', 'levels/common_mp/ingame-00.fbrb'),
              ('mp_loader', 'levels/common_mp/loader-00.fbrb'), ('awards', 'async/ondemand_awards-00.fbrb'),
              ('mycharacter', 'async/ondemand_mycharacter-00.fbrb')]


class MenuToolError(Exception):
    """A problem with the input the user can fix (explained in words by errors.guard)."""


def find_opfor(given=None):
    """The Project OPFOR folder: given, else OPFOR_ROOT, else project-opfor-moh next to the suite
    (core\\mohcore\\gamecopy.py)."""
    from mohcore import gamecopy
    try:
        return gamecopy.find_root(given)
    except gamecopy.GameNotFound as e:
        raise MenuToolError(str(e)) from None


def out_dir(root):
    """Where outputs made from game data go: <OPFOR>\\work\\tools_out (never the game folder)."""
    d = os.path.join(root, 'work', 'tools_out')
    os.makedirs(d, exist_ok=True)
    return d


def bundle_layers(root, rel):
    """The existing copies of a bundle, lowest first: MP\\dist\\win32\\<rel>, then MP\\dist\\win32\\patch\\<rel>."""
    from mohcore import gamecopy
    return gamecopy.GameCopy(root).layers(rel)


def bundle_movies(paths):
    """{movie name: (bundle path, entry name)} of the SwfMovie entries Menu/<name>.res in the bundles (later win;
    a *deleted* marker removes)."""
    from mohcore import fbrb
    out = {}
    for p in paths:
        for e in fbrb.entries(p):
            low = e.name.lower()
            if low.startswith('menu/') and low.endswith('.res') and '/' not in low[5:]:
                name = e.name[5:-4]
                if e.deleted:
                    out.pop(name.lower(), None)
                elif e.type == 'SwfMovie':
                    out[name.lower()] = (p, e.name)
    return out


def read_entry(bundle, entry):
    from mohcore import fbrb
    return fbrb.read(bundle, [entry])[entry]


def parse_bigf(b, where='file'):
    """BIGF members {name: bytes} and their order (apt.read_bigf, from bytes)."""
    if b[:4] != b'BIGF':
        raise MenuToolError(f'{where} is not a menu movie (.res, a BIGF archive)')
    n = struct.unpack_from('>I', b, 8)[0]
    p, out = 16, {}
    for _ in range(n):
        off, size = struct.unpack_from('>II', b, p)
        e = b.index(b'\0', p + 8)
        out[b[p + 8:e].decode()] = b[off:off + size]
        p = e + 1
    return out


def resolve(spec, root=None):
    """A movie spec (module doc) -> (label, {member: bytes}). Members of a .apt given alone: .apt and .const."""
    if os.path.isfile(spec):
        if spec.lower().endswith('.apt'):
            base = os.path.splitext(spec)[0]
            name = os.path.basename(base)
            if not os.path.isfile(base + '.const'):
                raise MenuToolError(f'{spec}: its constant file {name}.const is missing beside it')
            return spec, {name + '.apt': open(spec, 'rb').read(), name + '.const': open(base + '.const', 'rb').read()}
        return spec, parse_bigf(open(spec, 'rb').read(), spec)
    if '.fbrb:' in spec.lower():
        bundle, name = spec.rsplit(':', 1)
        if not os.path.isfile(bundle):
            raise FileNotFoundError(bundle)
        movies = bundle_movies([bundle])
    elif spec.lower().startswith('pristine:'):
        root = root or find_opfor()
        bundle = os.path.join(root, 'original', *('Package/' + MENU_BUNDLE).split('/'))
        if not os.path.isfile(bundle):
            raise MenuToolError(f'no pristine menu bundle at {bundle} (OPFOR\'s build_menus.bat keeps it there)')
        name = spec.split(':', 1)[1]
        movies = bundle_movies([bundle])
    elif os.sep in spec or '/' in spec or spec.lower().endswith(('.res', '.apt')):
        raise FileNotFoundError(spec)
    else:
        root = root or find_opfor()
        name = spec
        movies = bundle_movies(bundle_layers(root, MENU_BUNDLE))
    key = name.lower()[:-4] if name.lower().endswith('.res') else name.lower()
    if key not in movies:
        raise MenuToolError(f'no menu movie "{name}" in the bundle (known: {", ".join(sorted(movies)[:12])} ...)')
    bundle, entry = movies[key]
    return f'{bundle}:{entry}', parse_bigf(read_entry(bundle, entry), entry)


def movie_from_members(members, label='movie'):
    """An apt.Movie from BIGF members (or a .apt / .const pair)."""
    aptname = next((k for k in members if k.lower().endswith('.apt')), None)
    if aptname is None:
        raise MenuToolError(f'{label} has no .apt member (members: {", ".join(members)})')
    name = aptname[:-4]
    const = next((k for k in members if k.lower() == (name + '.const').lower()), None)
    if const is None:
        raise MenuToolError(f'{label} has no {name}.const member')
    try:
        return apt.Movie(data=(name, members[aptname], members[const]))
    except (ValueError, struct.error) as e:
        raise MenuToolError(f'{label} is not a readable APT movie: {e}')


# ------------------------------------------------------------------------------------------------ patch files

class PatchLine:
    def __init__(self, number, raw, kind, member, offset=None, expected=b'', new=None, label=None, text=None, comment=''):
        self.number, self.raw, self.kind, self.member = number, raw, kind, member
        self.offset, self.expected, self.new, self.label, self.text, self.comment = offset, expected, new, label, text, comment

    def where(self):
        return f'line {self.number}'


def parse_patch(path):
    """OPFOR's menu patch format (src\\tools\\apt\\AptTool.cs, the reference):
      <member> <hex offset> <expected hex bytes> -> <new hex bytes | @label>   # comment
      append <member> <label> "text"                                          # comment
    Returns ([PatchLine], [(line number, problem)]); new bytes may hold ('label', name) items."""
    lines, problems = [], []
    above = ''                                     # the comment block just above a line (OPFOR comments groups so)
    for n, raw in enumerate(open(path, encoding='utf-8', errors='replace').read().splitlines(), 1):
        line = raw.strip()
        if not line:
            above = ''
            continue
        if line.startswith('#'):
            above = line[1:].strip() or above
            continue
        comment_from = line.rfind('"') if line.startswith('append ') else 0
        h = line.find('#', max(comment_from, 0))
        comment = line[h + 1:].strip() if h >= 0 else above
        if h >= 0:
            line = line[:h].strip()
        if line.startswith('append '):
            q1, q2 = line.find('"'), line.rfind('"')
            head = line[:q1].split() if q1 > 0 else line.split()
            if q1 < 0 or q2 <= q1 or len(head) != 3:
                problems.append((n, 'an append line is: append <member> <label> "text"'))
                continue
            text = line[q1 + 1:q2]
            if any(ord(ch) > 127 for ch in text):
                problems.append((n, 'append text must be ASCII (AptTool writes it as ASCII)'))
            lines.append(PatchLine(n, raw, 'append', head[1], label=head[2], text=text, comment=comment))
            continue
        if line.count('->') != 1:
            problems.append((n, 'a patch line is: <member> <hex offset> <expected bytes> -> <new bytes>'))
            continue
        left, right = (s.split() for s in line.split('->'))
        if len(left) < 3:
            problems.append((n, 'missing member, offset or expected bytes'))
            continue
        try:
            offset = int(left[1], 16)
            expected = bytes(int(t, 16) for t in left[2:])
            if any(len(t) != 2 for t in left[2:]):
                raise ValueError
        except ValueError:
            problems.append((n, f'offset and expected bytes must be hex (bytes as two digits): {" ".join(left[1:])}'))
            continue
        new = []
        bad = False
        for t in right:
            if t.startswith('@'):
                new.append(('label', t[1:]))
            elif len(t) == 2 and all(ch in '0123456789abcdefABCDEF' for ch in t):
                new.append(int(t, 16))
            else:
                bad = True
        if bad or not new:
            problems.append((n, f'new bytes must be hex bytes (two digits) or @label: {" ".join(right)}'))
            continue
        lines.append(PatchLine(n, raw, 'patch', left[0], offset, expected, new, comment=comment))
    return lines, problems


def apply_patch(members, lines):
    """Applies parsed patch lines to BIGF members as AptTool does (appends 4-byte aligned, @label = the appended
    string's offset, little-endian; every expected byte checked). Returns (new members, {member: set of changed
    offsets the lines name}, [(line number, problem)])."""
    data = {k: bytearray(v) for k, v in members.items()}
    lower = {k.lower(): k for k in data}
    labels, named, problems = {}, {}, []
    for pl in lines:
        m = lower.get(pl.member.lower())
        if m is None:
            problems.append((pl.number, f'no member {pl.member} in the movie (members: {", ".join(data)})'))
            continue
        buf = data[m]
        if pl.kind == 'append':
            while len(buf) % 4:
                buf.append(0)
            if pl.label in labels:
                problems.append((pl.number, f'label {pl.label} defined twice'))
            labels[pl.label] = len(buf)
            buf += pl.text.encode('ascii', 'replace') + b'\0'
            continue
        new = bytearray()
        for t in pl.new:
            if isinstance(t, tuple):
                if t[1] not in labels:
                    problems.append((pl.number, f'undefined label @{t[1]} (an append line must define it first)'))
                    new += b'\0\0\0\0'
                else:
                    new += struct.pack('<I', labels[t[1]])
            else:
                new.append(t)
        if len(new) != len(pl.expected):
            problems.append((pl.number, f'size change: {len(pl.expected)} expected byte(s), {len(new)} new - patches must keep '
                                        'the size (AptTool refuses it)'))
            continue
        if pl.offset + len(pl.expected) > len(buf):
            problems.append((pl.number, f'offset {pl.offset:X} + {len(pl.expected)} is past the end of {m} ({len(buf):X})'))
            continue
        found = bytes(buf[pl.offset:pl.offset + len(pl.expected)])
        if found != pl.expected:
            problems.append((pl.number, f'{m} {pl.offset:X}: expected {pl.expected.hex(" ").upper()}, found '
                                        f'{found.hex(" ").upper()} - wrong source movie, or the line overlaps an earlier one'))
            continue
        buf[pl.offset:pl.offset + len(new)] = new
        named.setdefault(m, set()).update(range(pl.offset, pl.offset + len(new)))
    return {k: bytes(v) for k, v in data.items()}, named, problems


# ------------------------------------------------------------------------------------------------ the exe (data only)

class PEImage:
    """A 32-bit Windows exe's sections, for reading its data (strings, tables) - no code is decoded here."""

    def __init__(self, path):
        self.path = path
        b = self.b = open(path, 'rb').read()
        if b[:2] != b'MZ' or len(b) < 0x40:
            raise MenuToolError(f'{path} is not a Windows program (.exe)')
        pe = struct.unpack_from('<I', b, 0x3C)[0]
        if b[pe:pe + 4] != b'PE\0\0' or struct.unpack_from('<H', b, pe + 24)[0] != 0x10B:
            raise MenuToolError(f'{path} is not a 32-bit Windows program')
        n, opt = struct.unpack_from('<H', b, pe + 6)[0], struct.unpack_from('<H', b, pe + 20)[0]
        self.timestamp = struct.unpack_from('<I', b, pe + 8)[0]
        self.base = struct.unpack_from('<I', b, pe + 52)[0]
        self.sections = []                     # (name, va, virtual size, file offset, raw size, flags)
        for i in range(n):
            o = pe + 24 + opt + 40 * i
            vs, va, rs, ra = struct.unpack_from('<IIII', b, o + 8)
            self.sections.append((b[o:o + 8].rstrip(b'\0').decode('latin1'), self.base + va, vs, ra, rs,
                                  struct.unpack_from('<I', b, o + 36)[0]))

    def off(self, va):
        for _, sva, vs, ra, rs, _ in self.sections:
            if sva <= va < sva + min(vs, rs) if vs else sva <= va < sva + rs:
                return va - sva + ra
        return None

    def va(self, off):
        for _, sva, vs, ra, rs, _ in self.sections:
            if ra <= off < ra + rs:
                return sva + off - ra
        return None

    def is_code(self, va):
        return any(sva <= va < sva + vs and fl & 0x20000000 for _, sva, vs, _, _, fl in self.sections)

    def cstr(self, va, limit=200):
        """The printable ASCII C string at va, or None."""
        o = self.off(va) if va else None
        if o is None:
            return None
        e = self.b.find(b'\0', o, o + limit + 1)
        if e < 0:
            return None
        s = self.b[o:e]
        return s.decode('ascii') if s and all(32 <= ch < 127 for ch in s) else None

    def data_sections(self):
        return [s for s in self.sections if not s[5] & 0x20000000 and s[0] not in ('.rsrc', '.tls')]

    def strings(self, minimum=3):
        """The printable C strings of the data sections: {text: [va, ...]}. A string starts after a 00 byte or, inside
        a longer printable run (data before it that happens to be printable), at a 4-byte aligned offset."""
        import re
        out = {}
        pat = re.compile(rb'[\x20-\x7e]{%d,}(?=\x00)' % minimum)
        for _, sva, vs, ra, rs, _ in self.data_sections():
            for m in pat.finditer(self.b, ra, ra + rs):
                starts = [m.start()] + [o for o in range((m.start() + 4) & ~3, m.end() - minimum + 1, 4) if o != m.start()]
                for o in starts:
                    out.setdefault(self.b[o:m.end()].decode('ascii'), []).append(sva + o - ra)
        return out

    def find_cstring(self, text):
        """The virtual addresses of the C string text (whole string) in the data sections."""
        out, pat = [], b'\0' + text.encode('ascii') + b'\0'
        for _, sva, vs, ra, rs, _ in self.data_sections():
            j = self.b.find(pat, ra, ra + rs)
            while j >= 0:
                out.append(sva + j + 1 - ra)
                j = self.b.find(pat, j + 1, ra + rs)
        return out

    def find_dword(self, value, sections=None):
        """File offsets in the data sections where the little-endian 32-bit value is stored, 4-byte aligned."""
        pat, out = struct.pack('<I', value), []
        for s in sections or self.data_sections():
            _, sva, vs, ra, rs, _ = s
            j = self.b.find(pat, ra, ra + rs)
            while j >= 0:
                if (j - ra) % 4 == 0:
                    out.append(j)
                j = self.b.find(pat, j + 1, ra + rs)
        return out


def find_exe(root=None, given=None):
    if given:
        if not os.path.isfile(given):
            raise FileNotFoundError(given)
        return given
    from mohcore import gamecopy
    p = gamecopy.GameCopy(root or find_opfor()).exe
    if not os.path.isfile(p):
        raise MenuToolError(f'no MoHMPGame.exe in {os.path.dirname(p)} - pass --exe <path>')
    return p
