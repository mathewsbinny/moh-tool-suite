r"""mohloc.py - the game's localized texts (Localization/bin/language_<lang>_normal.bin): look up a key, search the
texts, list the keys a menu movie uses.

  python mohloc.py get <key | 0xHASH>... [--lang english]   the text of a key (its PC text "WIN32_<key>" first)
  python mohloc.py search <text> [--lang english]           every text containing <text> (any case), with its key
  python mohloc.py movie <movie>... [--lang english]        the movie's string constants that are localization keys
  python mohloc.py dump [--lang english] [--out <file>]     every entry: hash, key (where found), text; default
                                                            <OPFOR>\work\tools_out\loc_<lang>.txt
  python mohloc.py langs                                    the languages of the game copy
Options: --file <language_x_normal.bin> reads that file (its _histogram.bin beside it) instead of the game copy's
Dist\win32\overlay-00.fbrb; --no-keys skips naming keys (search / dump); --opfor <folder>.

Format (little-endian; the same layout for _normal, _huge and _numbers):
  u32 0x00039000 | u32 file size - 8 | u32 count | u32 entry table offset | u32 string offset (both from byte 8)
  | char[16] name ("NORMAL") | count x {u32 hash, u32 text offset from the string offset}, sorted by hash
  | NUL-terminated texts.
  Hash of a key: h = 0xFFFFFFFF, then h = h * 33 + byte for each byte (32-bit). PC-specific texts are stored under
  "WIN32_<key>" (e.g. WIN32_PC_SERVER_BROWSER, WIN32_IDK_A), the others under the key itself.
  Texts are byte strings decoded through language_<lang>_histogram.bin (u32 0x00039001 | u32 size - 8 | u32 count |
  u16 table[]): a byte below 0x80 is ASCII; a byte c >= 0x80 is table[c] when that is >= 0x80, else it starts a
  two-byte character table[table[c] * 128 + (next byte & 0x7F)] (Japanese).
Keys are not stored, only their hashes: names come from the strings of mohmpgame.exe and the constants of the menu
and HUD movies that hash to an entry. Nothing is written into the game folder.
"""
import os
import re
import struct
import sys

import menucommon as mc

OVERLAY = 'overlay-00.fbrb'
PC_PREFIX = 'WIN32_'


def khash(key, h=0xFFFFFFFF):
    for c in key.encode('latin1', 'replace') if isinstance(key, str) else key:
        h = (h * 33 + c) & 0xFFFFFFFF
    return h


class LocFile:
    """One language_<lang>_<kind>.bin: {hash: raw bytes}; text(hash) decoded with the histogram."""

    def __init__(self, data, histogram=None, name='localization file'):
        if len(data) < 36 or struct.unpack_from('<I', data, 0)[0] != 0x39000:
            raise mc.MenuToolError(f'{name} is not a localization text file (language_<lang>_normal.bin)')
        _, size, count, table, strings = struct.unpack_from('<5I', data, 0)
        if size != len(data) - 8 or 8 + table + 8 * count > len(data):
            raise mc.MenuToolError(f'{name}: the header does not match the file (size {size + 8}, {count} entries)')
        self.name = data[20:36].split(b'\0')[0].decode('latin1')
        base, self.raw, self.order = 8 + strings, {}, []
        for i in range(count):
            h, o = struct.unpack_from('<II', data, 8 + table + 8 * i)
            e = data.find(b'\0', base + o)
            self.raw[h] = data[base + o:e if e >= 0 else len(data)]
            self.order.append(h)
        self.table = None
        if histogram:
            if struct.unpack_from('<I', histogram, 0)[0] != 0x39001:
                raise mc.MenuToolError(f'{name}: its histogram file is not one')
            self.table = struct.unpack_from(f'<{(len(histogram) - 12) // 2}H', histogram, 12)

    def decode(self, raw):
        t, out, i = self.table, [], 0
        while i < len(raw):
            c = raw[i]
            i += 1
            if c < 0x80 or t is None:
                out.append(chr(c) if c < 0x80 else f'\\x{c:02x}')
                continue
            v = t[c] if c < len(t) else 0
            if v >= 0x80:
                out.append(chr(v))
            elif i < len(raw):
                k = v * 128 + (raw[i] & 0x7F)
                i += 1
                out.append(chr(t[k]) if k < len(t) and t[k] else '?')
        return ''.join(out)

    def text(self, h):
        return self.decode(self.raw[h]) if h in self.raw else None

    def lookup(self, key):
        """(hash, the name it was found under, text) - the PC text WIN32_<key> first, then the key; or None."""
        if re.fullmatch(r'0x[0-9A-Fa-f]{1,8}', key):
            h = int(key, 16)
            return (h, key, self.text(h)) if h in self.raw else None
        for name in (PC_PREFIX + key, key):
            h = khash(name)
            if h in self.raw:
                return h, name, self.text(h)
        return None


def languages(root):
    from mohcore import fbrb
    out = {}
    for bundle in mc.bundle_layers(root, OVERLAY):
        for e in fbrb.entries(bundle):
            m = re.fullmatch(r'Localization/bin/language_(\w+?)_normal\.bin', e.name, re.I)
            if m and not e.deleted:
                out[m.group(1).lower()] = bundle
    return out


def load(lang, root=None, path=None):
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        hist = re.sub(r'_(normal|huge|numbers)\.bin$', '_histogram.bin', path, flags=re.I)
        return LocFile(open(path, 'rb').read(), open(hist, 'rb').read() if os.path.isfile(hist) and hist != path else None, path)
    root = root or mc.find_opfor()
    langs = languages(root)
    if lang.lower() not in langs:
        raise mc.MenuToolError(f'no language "{lang}" in the game copy (have: {", ".join(sorted(langs)) or "none"})')
    from mohcore import fbrb
    names = [f'Localization/bin/language_{lang.lower()}_normal.bin', f'Localization/bin/language_{lang.lower()}_histogram.bin']
    entries = {e.name.lower(): e.name for e in fbrb.entries(langs[lang.lower()])}
    got = fbrb.read(langs[lang.lower()], [entries[n.lower()] for n in names if n.lower() in entries])
    got = {k.lower(): v for k, v in got.items()}
    return LocFile(got[names[0].lower()], got.get(names[1].lower()), names[0])


def key_names(loc, root=None, exe=None, movies=True):
    """{hash: key name} for the entries whose key is among the exe's strings or the menu / HUD movies' constants."""
    cands = set()
    try:
        img = mc.PEImage(mc.find_exe(root, exe))
        cands.update(s for s in img.strings(2) if len(s) < 120)
    except (mc.MenuToolError, FileNotFoundError):
        pass
    if movies:
        try:
            cands.update(movie_constants_all(root))
        except mc.MenuToolError:
            pass
    out = {}
    for s in cands:
        for name in (s, PC_PREFIX + s):
            h = khash(name)
            if h in loc.raw and h not in out:
                out[h] = name
    return out


def movie_constants_all(root):
    from mohcore import fbrb
    root = root or mc.find_opfor()
    out = set()
    pristine = os.path.join(root, 'original', *('Package/' + mc.MENU_BUNDLE).split('/'))
    sets = [mc.bundle_layers(root, rel) for key, rel in mc.MOVIE_SETS] + ([[pristine]] if os.path.isfile(pristine) else [])
    for layers in sets:                  # the pristine menus too: OPFOR's patches retarget some key constants
        if not layers:
            continue
        by_bundle = {}
        for name, (bundle, entry) in mc.bundle_movies(layers).items():
            by_bundle.setdefault(bundle, []).append(entry)
        for bundle, entries in by_bundle.items():
            for entry, data in fbrb.read(bundle, entries).items():
                try:
                    m = mc.movie_from_members(mc.parse_bigf(data, entry), entry)
                except mc.MenuToolError:
                    continue
                out.update(c[1:-1] for c in m.const if c.startswith('"'))
    return out


def take(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            raise mc.MenuToolError(f'{flag} needs a value')
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return None


def main(argv):
    args = list(argv[1:])
    if not args or args[0] not in ('get', 'search', 'movie', 'dump', 'langs'):
        print(__doc__)
        return 1 if args else 0
    sys.stdout.reconfigure(errors='replace')
    cmd = args.pop(0)
    root = take(args, '--opfor')
    lang = take(args, '--lang') or 'english'
    path = take(args, '--file')
    out = take(args, '--out')
    no_keys = '--no-keys' in args
    args = [a for a in args if a != '--no-keys']
    if cmd == 'langs':
        for k, b in sorted(languages(root or mc.find_opfor()).items()):
            print(f'{k:10s} {b}')
        return 0
    loc = load(lang, root, path)
    if cmd == 'get':
        if not args:
            raise mc.MenuToolError('get needs a key')
        missing = 0
        for key in args:
            r = loc.lookup(key)
            if r:
                print(f'{key} = {r[2]!r}' + (f'   (as {r[1]}, hash {r[0]:#010x})'))
            else:
                missing += 1
                print(f'{key}: no text (hashes {khash(PC_PREFIX + key):#010x} / {khash(key):#010x} not in the file)')
        return 1 if missing == len(args) else 0
    if cmd == 'search':
        if not args:
            raise mc.MenuToolError('search needs a text')
        want = ' '.join(args).lower()
        names = {} if no_keys else key_names(loc, root)
        hits = [(h, loc.text(h)) for h in loc.order if want in loc.text(h).lower()]
        for h, t in hits:
            print(f'{h:#010x}  {names.get(h, "?"):40s} {t!r}')
        print(f'{len(hits)} text(s) contain "{want}"')
        return 0
    if cmd == 'movie':
        if not args:
            raise mc.MenuToolError('movie needs a movie')
        for spec in args:
            label, members = mc.resolve(spec, root)
            m = mc.movie_from_members(members, label)
            seen, rows = set(), []
            for c in m.const:
                if c.startswith('"') and c not in seen:
                    seen.add(c)
                    r = loc.lookup(c[1:-1]) if c[1:-1] else None
                    if r:
                        rows.append((c[1:-1], r))
            print(f'{label}: {len(rows)} localization key(s) among {len(seen)} string constants')
            for key, (h, name, text) in sorted(rows):
                print(f'  {key:40s} {text!r}' + ('   (PC text)' if name.startswith(PC_PREFIX) else ''))
        return 0
    if cmd == 'dump':
        names = {} if no_keys else key_names(loc, root)
        if not out:
            out = os.path.join(mc.out_dir(root or mc.find_opfor()), f'loc_{lang.lower()}.txt')
        with open(out, 'w', encoding='utf-8') as f:
            for h in loc.order:
                f.write(f'{h:#010x}\t{names.get(h, "")}\t{loc.text(h)!r}\n')
        print(f'{len(loc.order)} entries ({len(names)} with their key) -> {out}')
        return 0
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(mc.SUITE, 'core'))
    from mohcore import errors
    errors.setup('mohloc', gui=False)          # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
