r"""apt.py - read-only reader for EA "APT" UI movies (Battlefield: Bad Company 2 menus), PC and Xbox 360.

Usage: python apt.py info  <movie>                  movie size, frames, characters by type, byte order
       python apt.py chars <movie> [type]           every character (type: shape, text, font, sprite, image, movie...)
       python apt.py dump  <movie> [out.txt]        structure (frames, labels, placements) and bytecode
       python apt.py find  <movie> <text>           constants and strings containing <text>, with their users

<movie> is a menu resource (a SwfMovie .res: a BIGF archive of <name>.apt, <name>.const, <name>.geo) or the
.apt file itself with its .const next to it.

Format (OpenSAGE's APT reader and inspection of BC2's files; OPFOR's AptTool documents the same layout):
  .const : "Apt constant file\x1A\0\0" | u32 movie offset (in .apt) | u32 count | u32 entries offset
           | count x {u32 type, u32 value}: type 1 string (value = offset of a C string in .const), 4 register.
  .apt   : "Apt Data:..." then data; every pointer is a file offset into .apt. Characters start {u32 type,
           u32 0x09876543}: 1 shape, 2 text, 3 font, 4 button, 5 sprite, 7 image, 9 movie, 10 static text.
           Movie: frame count, frames, ptr, character count, characters, width, height, ms per frame,
           import count, imports, export count, exports. Sprite: frame count, frames. Image: texture id.
           Shape: bounds (4 floats), geometry id (a shape in .geo). Frame {u32 item count, u32 items -> u32[]}.
           Items {u32 type}: 1 action, 2 frame label {name, flags, frame}, 3 place object, 4 remove object,
           5 background colour, 8 init action {sprite, bytecode}.
  Bytecode: AVM1 (Flash ActionScript 1/2) plus EA opcodes; opcodes >= 0x80 carry arguments, 32-bit ones aligned
  to 4 bytes from the start of the file. Unlike Flash, GetURL2 (0x9A) has no argument and GotoFrame2 (0x9F) an
  aligned 32-bit one (2026-10-26: with these, every function body of the 56 PC and Xbox 360 menu and HUD movies
  checked decodes exactly to its end; aptcheck.py in moh-menu-tools relies on that).
The PC files are little-endian, the Xbox 360 ones big-endian: every 32-bit field, float and multi-byte immediate
is swapped; the layout is the same. The byte order is detected from the .const header (the movie offset that lies
inside the .apt file).

Related formats (2026-10-24, same byte order rule):
  .geo  : "Apt PCGL geometry file\x1A\0" | u32 count at 0x18 | u32 table at 0x1C -> count x {u32 geometry id
          (= the shape's character id), u32 part count, u32 -> u32[] part pointers}. A textured part is 88 bytes:
          u32 5, u32 triangle count, u32 -> vertices (float x, y; 3 per triangle), u32 colour, a texture matrix,
          u32 image character id at +80.
  Image characters' pixels live in the menu's texture atlas (<menu>_textures_Win32 / _Xenon): atlas_meta.dbx
  (UI.UITextureAtlasInfo: TextureFile = the image character id, MinU / MaxU / MinV / MaxV) and atlas0.res (92-byte
  header with width / height at 16 / 20; PC: linear DXT5 4096 x 4096; Xbox 360: tiled, 16-bit-swapped DXT5).
Apt converts a movie's offsets to pointers once, in place, by walking its structures when it loads it (BC2's
runtime 0x1309880 for bytecode; the character walk and its inverse at 0x1319730) - see OPFOR's DEVELOPMENT_NOTES.md
for the rules this sets for editing movies.
"""
import os
import re
import struct
import sys
from collections import namedtuple

CHAR_TYPES = {1: 'shape', 2: 'text', 3: 'font', 4: 'button', 5: 'sprite', 6: 'sound', 7: 'image', 8: 'morph',
              9: 'movie', 10: 'statictext', 11: 'none', 12: 'video'}
SIG = 0x09876543

# a decoded instruction: at (offset in .apt), op, name, arg (as the dump shows it), end (the next instruction; a
# function's body start), target (branch target), plist (PushData's item list), items (PushData / ConstantPool
# constant indices), func (Func of a function definition), index (constant-pool index of A2 / A3 / AE-B3), unknown
# (an opcode with an argument this reader does not know), pool (the constant pool's length at the instruction)
Ins = namedtuple('Ins', 'at op name arg end target plist items func index unknown pool')
Func = namedtuple('Func', 'name nparams extra size body')

NAMES = {
    0x00: 'End', 0x04: 'NextFrame', 0x05: 'PrevFrame', 0x06: 'Play', 0x07: 'Stop', 0x0A: 'Add', 0x0B: 'Subtract',
    0x0C: 'Multiply', 0x0D: 'Divide', 0x0E: 'Equals', 0x0F: 'Less', 0x10: 'And', 0x11: 'Or', 0x12: 'Not',
    0x13: 'StringEquals', 0x14: 'StringLength', 0x15: 'StringExtract', 0x17: 'Pop', 0x18: 'ToInteger',
    0x1C: 'GetVariable', 0x1D: 'SetVariable', 0x20: 'SetTarget2', 0x21: 'StringAdd', 0x22: 'GetProperty',
    0x23: 'SetProperty', 0x24: 'CloneSprite', 0x25: 'RemoveSprite', 0x26: 'Trace', 0x27: 'StartDrag',
    0x28: 'EndDrag', 0x29: 'StringLess', 0x2A: 'Throw', 0x2B: 'CastOp', 0x2C: 'ImplementsOp', 0x30: 'RandomNumber',
    0x34: 'GetTime', 0x3A: 'Delete', 0x3B: 'Delete2', 0x3C: 'DefineLocal', 0x3D: 'CallFunction', 0x3E: 'Return',
    0x3F: 'Modulo', 0x40: 'NewObject', 0x41: 'DefineLocal2', 0x42: 'InitArray', 0x43: 'InitObject', 0x44: 'TypeOf',
    0x45: 'TargetPath', 0x46: 'Enumerate', 0x47: 'Add2', 0x48: 'Less2', 0x49: 'Equals2', 0x4A: 'ToNumber',
    0x4B: 'ToString', 0x4C: 'PushDuplicate', 0x4D: 'StackSwap', 0x4E: 'GetMember', 0x4F: 'SetMember',
    0x50: 'Increment', 0x51: 'Decrement', 0x52: 'CallMethod', 0x53: 'NewMethod', 0x54: 'InstanceOf',
    0x55: 'Enumerate2', 0x56: 'EA_PushThis', 0x57: 'EA_PushGlobal', 0x58: 'EA_ZeroVar', 0x59: 'EA_PushZero',
    0x5A: 'EA_PushOne', 0x5B: 'EA_PushNull', 0x5C: 'EA_PushUndefined', 0x5D: 'EA_CallMethodPop', 0x60: 'BitAnd',
    0x61: 'BitOr', 0x62: 'BitXor', 0x63: 'BitLShift', 0x64: 'BitRShift', 0x65: 'BitURShift', 0x66: 'StrictEquals',
    0x67: 'Greater', 0x68: 'StringGreater', 0x69: 'Extends', 0x70: 'EA_PushThisVar', 0x71: 'EA_PushGlobalVar',
    0x72: 'EA_ZeroVar2', 0x73: 'EA_PushTrue', 0x74: 'EA_PushFalse', 0x75: 'EA_PushNull2', 0x76: 'EA_PushUndefined2',
    0x81: 'GotoFrame', 0x83: 'GetURL', 0x87: 'SetRegister', 0x88: 'ConstantPool', 0x8A: 'WaitForFrame',
    0x8B: 'SetTarget', 0x8C: 'GotoLabel', 0x8D: 'WaitForFrame2', 0x8E: 'DefineFunction2', 0x8F: 'Try',
    0x94: 'With', 0x96: 'PushData', 0x99: 'BranchAlways', 0x9A: 'GetURL2', 0x9B: 'DefineFunction',
    0x9D: 'BranchIfTrue', 0x9E: 'CallFrame', 0x9F: 'GotoFrame2', 0xA1: 'EA_PushString', 0xA2: 'EA_PushConstant',
    0xA3: 'EA_PushWordConstant', 0xA4: 'EA_GetStringVar', 0xA5: 'EA_GetStringMember', 0xA6: 'EA_SetStringVar',
    0xA7: 'EA_SetStringMember', 0xAE: 'EA_PushValueOfVar', 0xAF: 'EA_GetNamedMember', 0xB0: 'EA_CallNamedFuncPop',
    0xB1: 'EA_CallNamedFunc', 0xB2: 'EA_CallNamedMethodPop', 0xB3: 'EA_CallNamedMethod', 0xB4: 'EA_PushFloat',
    0xB5: 'EA_PushByte', 0xB6: 'EA_PushShort', 0xB7: 'EA_PushLong', 0xB8: 'EA_BranchIfFalse', 0xB9: 'EA_PushRegister',
}


def read_bigf(path):
    """Members of a BIGF archive (menu SwfMovie resource): {name: bytes}. Index entries are big-endian on every
    platform: u32 offset, u32 size, C string name."""
    b = open(path, 'rb').read()
    if b[:4] != b'BIGF':
        raise ValueError(f'{path} is not a BIGF archive (a menu .res)')
    n = struct.unpack_from('>I', b, 8)[0]
    p, out = 16, {}
    for _ in range(n):
        off, size = struct.unpack_from('>II', b, p)
        e = b.index(b'\0', p + 8)
        out[b[p + 8:e].decode()] = b[off:off + size]
        p = e + 1
    return out


class Movie:
    sink = None              # instructions(): called with each decoded instruction while the dump walks the movie

    def __init__(self, path=None, data=None):
        """path: a menu .res or a .apt with its .const beside it; or data = (name, .apt bytes, .const bytes)."""
        if data is not None:
            self.name, self.a, self.c = data
        elif path.lower().endswith('.apt'):
            self.name = os.path.splitext(os.path.basename(path))[0]
            self.a = open(path, 'rb').read()
            self.c = open(os.path.splitext(path)[0] + '.const', 'rb').read()
        else:
            members = read_bigf(path)
            apt = next(k for k in members if k.lower().endswith('.apt'))
            self.name = apt[:-4]
            self.a, self.c = members[apt], members[self.name + '.const']
        if not self.c.startswith(b'Apt constant file'):
            raise ValueError('not an APT constant file')
        le, be = struct.unpack_from('<I', self.c, 20)[0], struct.unpack_from('>I', self.c, 20)[0]
        self.big = not (0 < le < len(self.a)) and 0 < be < len(self.a)
        self.e = '>' if self.big else '<'
        self.movie = be if self.big else le
        count, entries = self.cu(24), self.cu(28)
        self.const = []
        for i in range(count):
            t, v = self.cu(entries + 8 * i), self.cu(entries + 8 * i + 4)
            if t == 1:
                self.const.append('"' + self.cstr(self.c, v) + '"')
            elif t == 4:
                self.const.append(f'reg{v}')
            else:
                self.const.append(f'type{t}:{v}')

    # field readers in the movie's byte order
    def cu(self, o): return struct.unpack_from(self.e + 'I', self.c, o)[0]
    def u(self, o): return struct.unpack_from(self.e + 'I', self.a, o)[0]
    def i(self, o): return struct.unpack_from(self.e + 'i', self.a, o)[0]
    def f(self, o): return struct.unpack_from(self.e + 'f', self.a, o)[0]
    def h(self, o): return struct.unpack_from(self.e + 'h', self.a, o)[0]
    def w(self, o): return struct.unpack_from(self.e + 'H', self.a, o)[0]

    @staticmethod
    def cstr(buf, p):
        if not p or p >= len(buf):
            return 'null'
        e = buf.find(b'\0', p)
        return buf[p:e if e >= 0 else len(buf)].decode('latin1')

    def s(self, p): return self.cstr(self.a, p)

    # --- characters -------------------------------------------------------------------------------------------
    def characters(self):
        m = self.movie
        count, ptr = self.u(m + 20), self.u(m + 24)
        out = []
        for c in range(count):
            cp = self.u(ptr + 4 * c)
            if not cp or cp + 8 > len(self.a) or self.u(cp + 4) != SIG:
                out.append((c, cp, None))
                continue
            out.append((c, cp, self.u(cp)))
        return out

    def describe(self, c, cp, t):
        if t is None:
            return f'char {c}: (none)'
        name = CHAR_TYPES.get(t, f'type{t}')
        if t == 5:
            return f'char {c}: sprite @{cp:#x} frames={self.u(cp + 8)}'
        if t == 7:
            return f'char {c}: image @{cp:#x} texture={self.u(cp + 8)}'
        if t == 1:
            x0, y0, x1, y1 = (self.f(cp + 8 + 4 * k) for k in range(4))
            return f'char {c}: shape @{cp:#x} bounds=({x0:g},{y0:g})-({x1:g},{y1:g}) geometry={self.u(cp + 24)}'
        if t == 9:
            return f'char {c}: movie @{cp:#x} frames={self.u(cp + 8)}'
        return f'char {c}: {name} @{cp:#x}'

    # --- bytecode ---------------------------------------------------------------------------------------------
    def decode(self, p, pool):
        """The instruction at p: (Ins, the constant pool after it). Ins.end is the next instruction (for a function
        definition, the start of its body). Raises struct.error / IndexError at the end of the file."""
        a, at, op = self.a, p, self.a[p]
        p += 1
        name = NAMES.get(op, f'op_{op:02X}')
        arg, target, plist, items, func, index = '', None, None, None, None, None
        K = (lambda k: pool[k] if k < len(pool) else f'pool[{k}]?')
        al = (lambda q: (q + 3) & ~3)
        if op == 0xB7:
            arg = str(self.i(p)); p += 4
        elif op in (0x81, 0x87, 0x99, 0x9D, 0xB8, 0x94, 0x9F):
            p = al(p); v = self.i(p); p += 4
            if op in (0x99, 0x9D, 0xB8):
                target = p + v
                arg = f'{v:+d} -> {target:#x}'
            else:
                arg = str(v)
        elif op == 0x83:
            p = al(p); arg = f'"{self.s(self.u(p))}", "{self.s(self.u(p + 4))}"'; p += 8
        elif op == 0x88:
            p = al(p); n, ptr = self.u(p), self.u(p + 4); p += 8
            items = [self.u(ptr + 4 * k) for k in range(n)]
            pool = [self.const[g] if g < len(self.const) else f'?{g}' for g in items]
            arg = f'{n} constants'
        elif op == 0x8A:
            p = al(p); arg = f'frame {self.i(p)} skip {self.i(p + 4)}'; p += 8
        elif op in (0x8B, 0x8C, 0xA1, 0xA4, 0xA5, 0xA6, 0xA7):
            p = al(p); arg = f'"{self.s(self.u(p))}"'; p += 4
        elif op == 0x96:
            p = al(p); n, plist = self.u(p), self.u(p + 4); p += 8
            items = [self.u(plist + 4 * k) for k in range(min(n, 32))]
            items += [self.u(plist + 4 * k) for k in range(32, min(n, 4096)) if plist + 4 * k + 4 <= len(a)]
            shown = [self.const[g] if g < len(self.const) else f'#{g}' for g in items[:32]]
            arg = ', '.join(shown) + f'   [list {plist:#x}]'
        elif op == 0x8D:
            arg = str(a[p]); p += 1
        elif op in (0xA2, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3):
            index = a[p]; arg = K(index); p += 1
        elif op == 0xA3:
            index = self.w(p); arg = K(index); p += 2
        elif op == 0xB4:
            arg = f'{self.f(p):g}'; p += 4
        elif op == 0xB5:
            arg = str(struct.unpack_from('b', a, p)[0]); p += 1
        elif op == 0xB6:
            arg = str(self.h(p)); p += 2
        elif op == 0xB9:
            arg = f'r{a[p]}'; p += 1
        elif op in (0x9B, 0x8E):
            p = al(p)
            fname, nparams = self.s(self.u(p)), self.i(p + 4)
            if op == 0x8E:
                extra = f' regs={a[p + 8] if not self.big else a[p + 11]}'
                size, body = self.i(p + 16), p + 28
            else:
                extra, size, body = '', self.i(p + 12), p + 24
            func = Func(fname, nparams, extra, size, body)
            p = body
        return Ins(at, op, name, arg, p, target, plist, items, func, index,
                   op >= 0x80 and op not in NAMES, len(pool)), pool

    def disasm(self, start, indent, out, limit=None, pool=None):
        pool = list(pool or [])
        a, p, guard = self.a, start, 0
        limit = limit if limit is not None else len(a)
        while p < len(a) and p < limit and guard < 200000:
            guard += 1
            ins, pool = self.decode(p, pool)
            if self.sink is not None:
                self.sink(ins, start, limit, indent)
            if ins.func:
                f = ins.func
                out.append(f'{indent}{ins.at:06X}  {ins.name} {f.name or "<anon>"}({f.nparams} params){f.extra} size={f.size}')
                self.disasm(f.body, indent + '    ', out, f.body + f.size, pool)
                p = f.body + f.size
                continue
            if ins.unknown:
                out.append(f'{indent}{ins.at:06X}  {ins.name}  ** unknown opcode with argument - stopping')
                return
            out.append(f'{indent}{ins.at:06X}  {ins.name}' + (f' {ins.arg}' if ins.arg else ''))
            p = ins.end
            if ins.op == 0x00 and limit == len(a):
                return

    def instructions(self):
        """Every instruction the dump decodes, once each, in dump order: [(Ins, stream start, stream limit, depth)].
        A frame's action stream has depth 0 and limit = the file's end; a function body's instructions have the
        function's depth + 1 and limit = the body's end. The dump's text is kept in self.dump_lines."""
        seen, recs = set(), []

        def sink(ins, start, limit, indent):
            if ins.at not in seen:
                seen.add(ins.at)
                recs.append([ins, start, limit, indent])
        self.sink = sink
        try:
            self.dump_lines = self.dump()
        finally:
            self.sink = None
        depth = {}                                 # a stream's depth: 0 for the streams that no function contains
        for ins, start, limit, _ in recs:
            depth.setdefault(start, 0)
            if ins.func:
                depth[ins.func.body] = depth[start] + 1
        return [(ins, start, limit, depth.get(start, 0)) for ins, start, limit, _ in recs]

    # --- structure --------------------------------------------------------------------------------------------
    def frames(self, ptr, count, indent, owner, out):
        for fr in range(count):
            n, items = self.u(ptr + 8 * fr), self.u(ptr + 8 * fr + 4)
            out.append(f'{indent}{owner} frame {fr} ({n} items)')
            ind = indent + '  '
            for k in range(n):
                try:
                    self.item(items, k, ind, out)
                except (struct.error, IndexError, ValueError) as e:
                    out.append(f'{ind}** item {k} of this frame unreadable: {e}')

    def item(self, items, k, ind, out):
                it = self.u(items + 4 * k)
                t = self.u(it)
                if t == 1:
                    out.append(f'{ind}Action @{self.u(it + 4):X}')
                    self.disasm(self.u(it + 4), ind + '    ', out)
                elif t == 2:
                    out.append(f'{ind}FrameLabel "{self.s(self.u(it + 4))}" frame {self.i(it + 12)}')
                elif t == 3:
                    nm = self.s(self.u(it + 0x34))
                    out.append(f'{ind}PlaceObject depth={self.i(it + 8)} char={self.i(it + 12)} flags={self.u(it + 4):#x}'
                               + (f' name="{nm}"' if nm != 'null' else '')
                               + f' @{it:X} x={self.f(it + 0x20):g} y={self.f(it + 0x24):g}')
                    clip = self.u(it + 0x3C)
                    if clip and clip < len(self.a):
                        cn, cpp = self.u(clip), self.u(clip + 4)
                        for c in range(min(cn, 64)):
                            ev = cpp + 12 * c
                            out.append(f'{ind}    ClipEvent flags={self.u(ev):#x} key={self.u(ev + 4)} @{self.u(ev + 8):X}')
                            self.disasm(self.u(ev + 8), ind + '        ', out)
                elif t == 4:
                    out.append(f'{ind}RemoveObject depth={self.i(it + 4)}')
                elif t == 5:
                    out.append(f'{ind}BackgroundColor')
                elif t == 8:
                    out.append(f'{ind}InitAction sprite={self.i(it + 4)} @{self.u(it + 8):X}')
                    self.disasm(self.u(it + 8), ind + '    ', out)
                else:
                    out.append(f'{ind}item type {t} @{it:X}')

    def dump(self):
        m, out = self.movie, []
        frames, fptr = self.u(m + 8), self.u(m + 12)
        out.append(f'Movie {self.name} {self.i(m + 28)}x{self.i(m + 32)} frames={frames} characters={self.u(m + 20)} '
                   f'({"big-endian, Xbox 360" if self.big else "little-endian, PC"})')
        for k in range(self.u(m + 40)):
            e = self.u(m + 44) + 16 * k
            out.append(f'  import "{self.s(self.u(e))}" :: "{self.s(self.u(e + 4))}" -> char {self.u(e + 8)}')
        for k in range(self.u(m + 48)):
            e = self.u(m + 52) + 8 * k
            out.append(f'  export "{self.s(self.u(e))}" = char {self.u(e + 4)}')
        self.frames(fptr, frames, '', 'Movie', out)
        for c, cp, t in self.characters():
            if t is not None:
                out.append(self.describe(c, cp, t))
            if t == 5:
                self.frames(self.u(cp + 12), self.u(cp + 8), '  ', f'Sprite {c}', out)
        return out


def main(argv):
    if len(argv) < 3 or argv[1] not in ('info', 'chars', 'dump', 'find'):
        print(__doc__)
        return 1
    mv = Movie(argv[2])
    if argv[1] == 'info':
        from collections import Counter
        kinds = Counter(CHAR_TYPES.get(t, f'type{t}') for _, _, t in mv.characters() if t is not None)
        m = mv.movie
        print(f'{mv.name}: {"big-endian (Xbox 360)" if mv.big else "little-endian (PC)"}, {mv.i(m + 28)}x{mv.i(m + 32)}, '
              f'{mv.u(m + 8)} frame(s), {len(mv.const)} constants; characters: '
              + ', '.join(f'{v} {k}' for k, v in sorted(kinds.items())))
    elif argv[1] == 'chars':
        want = argv[3].lower() if len(argv) > 3 else None
        for c, cp, t in mv.characters():
            if t is not None and (not want or CHAR_TYPES.get(t) == want):
                print(mv.describe(c, cp, t))
    elif argv[1] == 'dump':
        text = '\n'.join(mv.dump()) + '\n'
        if len(argv) > 3:
            open(argv[3], 'w', encoding='utf-8').write(text)
            print(f'{argv[3]}: {text.count(chr(10))} lines')
        else:
            sys.stdout.reconfigure(errors='replace')            # names in other code pages: no crash on the console
            sys.stdout.write(text)
    elif argv[1] == 'find':
        if len(argv) < 4:
            print(__doc__)
            return 1
        needle = argv[3].lower()
        for k, v in enumerate(mv.const):
            if needle in v.lower():
                print(f'constant {k}: {v}')
        for m in re.finditer(re.escape(argv[3].encode()), mv.a, re.I):
            st = mv.a.rfind(b'\0', 0, m.start()) + 1
            print(f'.apt string at {st:#x}: "{mv.s(st)}"')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core'))
    from mohcore import errors
    errors.setup('apt', gui=False)        # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
