r"""aptcheck.py - checks patched APT menu movies against the rules that crash the game's menu loader, before a build
reaches the game.

  python aptcheck.py verify <original> <patched> [--patch <file.patch>] [--brief]
  python aptcheck.py lint <file.patch>... [--source <movie>] [--installed] [--brief]
  python aptcheck.py check <movie>...

<original>, <patched>, <movie>: a menu .res, a .apt (its .const beside it), a movie name from the game's main menu
bundle (main_nav), pristine:<name> (OPFOR's pristine copy, before its patches) or <bundle.fbrb>:<name>.

verify  compares a patched movie with its original:
          - every member's size kept (bytes appended at the end - AptTool's "append" - are noted, not refused);
          - the changed bytes, each range named: the instructions before and after and the function they are in, a
            constant's entry (old and new text), or data;
          - every instruction outside the changed bytes decodes exactly as before (same offset, opcode and length:
            alignment and the walk unchanged);
          - and, in the patched movie, none of the following that the original does not already have:
              shared-list   a PushData item list used by more than one PushData (the loader converts each list in
                            place, so a second user crashes the menus while they load);
              body-end      a function body that does not decode exactly to its end (an operand misaligned, an
                            instruction straddling the end);
              end-in-function   an End (00) opcode inside a function body;
              zero-opcode   a changed byte that is now an opcode 00;
              branch-target a branch that does not land on an instruction of its own stream (or the body's end);
              const-index   a PushData / ConstantPool constant index past the constant table, or a constant-pool
                            index (EA_PushConstant ...) past the current pool;
              unknown-op    an opcode with an argument the reader does not know.
          With --patch: every changed byte must be one the patch file names (unexpected changes are errors).
lint    checks OPFOR's menu patch files (src\menus\<movie>.patch, applied by build_menus.bat with AptTool): the
        syntax, sizes kept, lines overlapping, labels, a comment on every line; then applies the file to the source
        movie (default pristine:<file name>) with every expected byte checked, and verifies the result as above.
        --installed also says whether the movie the game has installed is that result (a stale build otherwise).
check   the rules above on one movie by itself (the stock movies have a few of their own, listed as notes).

--brief leaves out the changed ranges' details. --opfor <folder> names Project OPFOR (default: OPFOR_ROOT, or a
"Project OPFOR" folder next to the toolkit).
Exit code: 0 no errors, 1 errors (or the tool could not run). Rules: OPFOR's DEVELOPMENT_NOTES.md section 3 ("APT
menus", "Menu build pipeline"); the format: moh-console-readers\apt\apt.py. Nothing is written into the game folder.
"""
import bisect
import glob
import os
import sys
from collections import namedtuple

import menucommon as mc

Finding = namedtuple('Finding', 'kind key text')
BRANCHES = (0x99, 0x9D, 0xB8)


# ------------------------------------------------------------------------------------------------ analysis

class Code:
    """The decoded code of a movie: instructions by offset, their byte spans, function bodies and names."""

    def __init__(self, movie):
        self.m = movie
        self.recs = movie.instructions()
        self.by_at = {r[0].at: r for r in self.recs}
        self.starts = sorted(self.by_at)
        self.streams = {}
        self.funcs = []                        # (body start, body end, definition offset, name)
        last = {}
        for ins, start, limit, depth in self.recs:
            self.streams.setdefault(start, set()).add(ins.at)
            if ins.func:
                prev = last.get(start)
                name = ins.func.name or (prev.arg.strip('"') if prev is not None and prev.op in (0xA2, 0xA3, 0xA1)
                                         and prev.arg.startswith('"') else '<anon>')
                self.funcs.append((ins.func.body, ins.func.body + ins.func.size, ins.at, name))
            last[start] = ins
        self.funcs.sort()

    def span(self, ins):
        return ins.at, max(ins.end, ins.at + 1)

    def containing(self, off):
        """The instruction whose bytes hold offset off, or None."""
        k = bisect.bisect_right(self.starts, off) - 1
        if k >= 0:
            ins = self.by_at[self.starts[k]][0]
            a, b = self.span(ins)
            if a <= off < b:
                return ins
        return None

    def function_at(self, off):
        """The innermost function whose body holds off: (definition offset, name), or None."""
        best = None
        for body, end, at, name in self.funcs:
            if body <= off < end and (best is None or body >= best[0]):
                best = (body, at, name)
        return (best[1], best[2]) if best else None

    def findings(self):
        m, out = self.m, {}
        nconst = len(m.const)
        users = {}
        for ins, start, limit, depth in self.recs:
            if ins.plist is not None:
                users.setdefault(ins.plist, []).append(ins.at)
            if ins.unknown:
                out[('unknown-op', ins.at)] = Finding('unknown-op', ins.at, f'{ins.at:06X} {ins.name}: an opcode with an argument the reader does not know')
            if ins.op == 0x00 and depth > 0:
                out[('end-in-function', ins.at)] = Finding('end-in-function', ins.at, f'{ins.at:06X} End inside {self.fname(ins.at)}')
            if ins.op in BRANCHES and ins.target is not None:
                ok = ins.target in self.streams.get(start, ()) or ins.target == limit
                if not ok:
                    out[('branch-target', ins.at)] = Finding('branch-target', ins.at,
                                                             f'{ins.at:06X} {ins.name} {ins.arg}: the target is not an instruction of this stream')
            if ins.op in (0x96, 0x88) and ins.items is not None:
                bad = [g for g in ins.items if g >= nconst]
                if bad:
                    out[('const-index', ins.at)] = Finding('const-index', ins.at, f'{ins.at:06X} {ins.name}: constant index '
                                                           f'{bad[0]} past the {nconst} constants')
            if ins.index is not None and ins.index >= ins.pool:
                out[('const-index', ins.at)] = Finding('const-index', ins.at, f'{ins.at:06X} {ins.name}: pool index {ins.index} '
                                                       f'past the current pool ({ins.pool} constants)')
        for plist, ats in users.items():
            if len(ats) > 1:
                out[('shared-list', plist)] = Finding('shared-list', plist, f'PushData list {plist:#x} used by {len(ats)} '
                                                      f'PushData: {", ".join(f"{a:06X}" for a in sorted(ats))}')
        ends = {}
        for ins, start, limit, depth in self.recs:
            if depth > 0:
                e = ins.func.body + ins.func.size if ins.func else ins.end
                ends[(start, limit)] = max(ends.get((start, limit), start), e)
        for body, end, at, name in self.funcs:
            got = ends.get((body, end), body)
            if got != end:
                out[('body-end', at)] = Finding('body-end', at, f'function {name} at {at:06X}: its body decodes to {got:06X}, '
                                                f'not its end {end:06X}')
        return out

    def fname(self, off):
        f = self.function_at(off)
        return f'function {f[1]} ({f[0]:06X})' if f else 'a frame action'

    def text(self, ins):
        if ins.func:
            return f'{ins.name} {ins.func.name or "<anon>"}({ins.func.nparams} params) size={ins.func.size}'
        return f'{ins.name}' + (f' {ins.arg}' if ins.arg else '')


def ranges(offsets, gap=4):
    """Changed offsets as [start, end) ranges; ranges fewer than gap bytes apart are shown as one."""
    out = []
    for o in sorted(offsets):
        if out and o - out[-1][1] < gap:
            out[-1][1] = o + 1
        else:
            out.append([o, o + 1])
    return out


def const_layout(m):
    count, entries = m.cu(24), m.cu(28)
    return entries, entries + 8 * count


# ------------------------------------------------------------------------------------------------ verify

class Report:
    def __init__(self, title, brief=False):
        self.title, self.errors, self.warnings, self.notes, self.lines = title, 0, 0, 0, []
        self.brief = brief                     # leave out the details (each changed range and its instructions)

    def add(self, level, text, detail=False):
        if detail and self.brief:
            return
        if level == 'error':
            self.errors += 1
        elif level == 'warning':
            self.warnings += 1
        elif level == 'note':
            self.notes += 1
        self.lines.append(f'  {level + ":" if level != "info" else "     "} {text}' if level != 'info' else f'    {text}')

    def show(self):
        print(self.title)
        for line in self.lines:
            print(line)
        print(f'  -> {"OK" if not self.errors else f"{self.errors} error(s)"}'
              + (f', {self.warnings} warning(s)' if self.warnings else '') + (f', {self.notes} note(s)' if self.notes else ''))


def verify(orig_members, new_members, rep, named=None, label='movie'):
    """Compares a patched movie's members with the original's (module doc); adds to the report."""
    lower = {k.lower(): k for k in new_members}
    changed = {}
    for k, ob in orig_members.items():
        nk = lower.get(k.lower())
        if nk is None:
            rep.add('error', f'member {k} is missing from the patched movie')
            continue
        nb = new_members[nk]
        if len(nb) < len(ob):
            rep.add('error', f'{k}: {len(ob)} -> {len(nb)} bytes - the member got shorter (patches keep the size)')
        elif len(nb) > len(ob):
            rep.add('note', f'{k}: {len(nb) - len(ob)} byte(s) appended at the end ({len(ob):X} -> {len(nb):X})')
        n = min(len(ob), len(nb))
        diff = {i for i in range(n) if ob[i] != nb[i]} if ob[:n] != nb[:n] else set()
        changed[k] = diff
    for k in new_members:
        if k.lower() not in {x.lower() for x in orig_members}:
            rep.add('error', f'member {k} is new in the patched movie')

    om, nm = mc.movie_from_members(orig_members, 'the original'), mc.movie_from_members(new_members, 'the patched movie')
    if om.big != nm.big:
        rep.add('error', 'the byte order differs between the two movies')
        return
    oc, nc = Code(om), Code(nm)
    aptk = next(k for k in orig_members if k.lower().endswith('.apt'))
    constk = next(k for k in orig_members if k.lower() == aptk[:-4].lower() + '.const')
    total = sum(len(v) for v in changed.values())
    rep.add('info', f'{total} byte(s) changed: ' + ', '.join(f'{k} {len(v)}' for k, v in changed.items() if v) if total else 'no byte changed in place')

    # the changed ranges, named
    for k, diff in changed.items():
        for a, b in ranges(diff):
            ob, nb = orig_members[k][a:b], new_members[lower[k.lower()]][a:b]
            head = f'{k} {a:X}: {ob.hex(" ").upper()} -> {nb.hex(" ").upper()}'
            if k == aptk:
                before = sorted({oc.containing(o).at for o in range(a, b) if oc.containing(o)})
                after = sorted({nc.containing(o).at for o in range(a, b) if nc.containing(o)})
                if before or after:
                    rep.add('info', f'{head}  in {nc.fname(a)}', True)
                    for at in before:
                        rep.add('info', f'      was {at:06X}  {oc.text(oc.by_at[at][0])}', True)
                    for at in after:
                        rep.add('info', f'      now {at:06X}  {nc.text(nc.by_at[at][0])}', True)
                else:
                    rep.add('info', f'{head}  (data, not code)', True)
                for o in sorted(o for o in diff if a <= o < b):
                    if new_members[aptk][o] == 0 and o in nc.by_at:
                        rep.add('error', f'zero-opcode: {aptk} {o:X} is now opcode 00 (End) in {nc.fname(o)} - use 59 17 '
                                         '(push 0 / pop) as filler')
            elif k == constk:
                lo, hi = const_layout(om)
                desc = []
                for idx, field in sorted({((o - lo) // 8, (o - lo) % 8 // 4) for o in diff if a <= o < b and lo <= o < hi}):
                    old = om.const[idx] if idx < len(om.const) else '?'
                    new = nm.const[idx] if idx < len(nm.const) else '?'
                    desc.append(f'constant {idx} {("type", "value")[field]}: {old} -> {new}')
                rep.add('info', head + ('  ' + '; '.join(desc) if desc else '  (constant data)'), True)
            else:
                rep.add('info', head, True)

    # every instruction outside the changed bytes decodes as before
    cset = changed.get(aptk, set())
    cs = sorted(cset)

    def touched(ins, code):
        a, b = code.span(ins)
        k = bisect.bisect_left(cs, a)
        return k < len(cs) and cs[k] < b
    shifted = []
    for at, (ins, *_r) in oc.by_at.items():
        if touched(ins, oc):
            continue
        new = nc.by_at.get(at)
        if new is None or new[0].op != ins.op or new[0].end != ins.end or new[0].target != ins.target \
                or new[0].plist != ins.plist or (ins.func and (new[0].func.size != ins.func.size)):
            shifted.append(at)
    for at, (ins, *_r) in nc.by_at.items():
        if at not in oc.by_at and not touched(ins, nc):
            shifted.append(at)
    if shifted:
        rep.add('error', f'decode-shift: {len(shifted)} instruction(s) away from the changed bytes decode differently, first at '
                         f'{min(shifted):06X} in {nc.fname(min(shifted))} - an operand or the alignment moved')
    else:
        rep.add('info', f'{len(nc.by_at)} instructions decode; every one away from the changed bytes as before')

    # the rules, on the patched movie, against the original's own
    of, nf = oc.findings(), nc.findings()
    for key, f in sorted(nf.items(), key=lambda x: (x[0][0], x[0][1])):
        if key not in of or (f.kind == 'shared-list' and f.text != of[key].text):
            rep.add('error', f'{f.kind}: {f.text}')
    old = [f for key, f in of.items() if key in nf]
    for f in old:
        rep.add('note', f'already in the original ({f.kind}): {f.text}')
    for key, f in of.items():
        if key not in nf:
            rep.add('info', f'gone in the patched movie ({f.kind}): {f.text}')

    # only the bytes the patch names
    if named is not None:
        for k, diff in changed.items():
            want = named.get(k, set())
            extra = sorted(diff - want)
            if extra:
                rep.add('error', f'unexpected-change: {k} differs at {len(extra)} byte(s) the patch does not name, first at {extra[0]:X}')


# ------------------------------------------------------------------------------------------------ commands

def cmd_verify(args, opfor):
    if len(args) < 2:
        raise mc.MenuToolError('verify needs <original> <patched>')
    patch = take(args, '--patch')
    ol, om = mc.resolve(args[0], opfor)
    nl, nm = mc.resolve(args[1], opfor)
    rep = Report(f'verify {ol}\n    -> {nl}')
    named = None
    if patch:
        lines, problems = mc.parse_patch(patch)
        _, named, applied_problems = mc.apply_patch(om, lines)
        for n, p in problems + applied_problems:
            rep.add('error', f'{os.path.basename(patch)} line {n}: {p}')
    verify(om, nm, rep, named)
    rep.show()
    return rep.errors


def cmd_lint(args, opfor):
    source = take(args, '--source')
    installed = take_flag(args, '--installed')
    brief = take_flag(args, '--brief')
    files = []
    for a in args:
        files += sorted(glob.glob(a)) if any(ch in a for ch in '*?') else [a]
    if not files:
        raise mc.MenuToolError('lint needs one or more .patch files')
    errors = 0
    for path in files:
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        movie = os.path.splitext(os.path.basename(path))[0]
        spec = source or f'pristine:{movie}'
        rep = Report(f'lint {path}  (source: {spec})', brief)
        lines, problems = mc.parse_patch(path)
        for n, p in problems:
            rep.add('error', f'line {n}: {p}')
        spans = {}
        for pl in lines:
            if not pl.comment:
                rep.add('warning', f'line {pl.number}: no comment, on the line or just above it (OPFOR comments every patch)')
            if pl.kind == 'patch':
                if bytes(t for t in pl.new if not isinstance(t, tuple)) == pl.expected and not any(isinstance(t, tuple) for t in pl.new):
                    rep.add('warning', f'line {pl.number}: changes nothing (new bytes = expected bytes)')
                for o in range(pl.offset, pl.offset + len(pl.expected)):
                    k = (pl.member.lower(), o)
                    if k in spans:
                        rep.add('error', f'line {pl.number}: {pl.member} {o:X} is also patched on line {spans[k]}')
                        break
                    spans[k] = pl.number
        label, members = mc.resolve(spec, opfor)
        new, named, applied = mc.apply_patch(members, lines)
        for n, p in applied:
            rep.add('error', f'line {n}: {p}')
        rep.add('info', f'{sum(1 for p in lines if p.kind == "patch")} patch line(s), {sum(1 for p in lines if p.kind == "append")} '
                        f'append(s) applied to {label}')
        if not applied:
            verify(members, new, rep, named)
        if installed:
            try:
                il, im = mc.resolve(movie, opfor)
                same = all(im.get(k) == v for k, v in new.items())
                rep.add('info' if same else 'note', f'installed movie ({il}): ' + ('is this result' if same else
                        'differs from this result - the build is stale, or later steps (onslaught_art.py) changed it'))
            except mc.MenuToolError as e:
                rep.add('note', f'installed movie not compared: {e}')
        rep.show()
        errors += rep.errors
    return errors


def cmd_check(args, opfor):
    if not args:
        raise mc.MenuToolError('check needs a movie')
    errors = 0
    for spec in args:
        label, members = mc.resolve(spec, opfor)
        code = Code(mc.movie_from_members(members, label))
        rep = Report(f'check {label}')
        f = code.findings()
        rep.add('info', f'{len(code.by_at)} instructions, {len(code.funcs)} functions, {len(code.m.const)} constants')
        for key, x in sorted(f.items(), key=lambda x: (x[0][0], x[0][1])):
            rep.add('note', f'{x.kind}: {x.text}')
        rep.show()
    return errors


def take(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            raise mc.MenuToolError(f'{flag} needs a value')
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return None


def take_flag(args, flag):
    if flag in args:
        args.remove(flag)
        return True
    return False


def main(argv):
    args = list(argv[1:])
    if not args or args[0] not in ('verify', 'lint', 'check'):
        print(__doc__)
        return 1 if args else 0
    cmd = args.pop(0)
    opfor = take(args, '--opfor')
    n = {'verify': cmd_verify, 'lint': cmd_lint, 'check': cmd_check}[cmd](args, opfor)
    return 1 if n else 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(mc.SUITE, 'core'))
    from mohcore import errors
    errors.setup('aptcheck', gui=False)        # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
