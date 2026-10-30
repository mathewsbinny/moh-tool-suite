r"""aptrefs.py - what the menu and HUD movies ask of the engine and what it sends them, cross-referenced with the
engine's name / id table in mohmpgame.exe.

  python aptrefs.py movie <movie>... [--json <out.json>]
        per movie: the extern.<name> values it reads (name$param: with a parameter), the FSCommands it sends
        ("FSCommand:<name>", or "FSCommand:" + "<name>" as MOH's movies build them), and the engine events it
        handles (_root.catchEvent(name, handler), gameEventListener.<name> = handler: BFBC2's movies; MOH's movies
        register none this way) with the handler's parameter count
  python aptrefs.py table [--exe <mohmpgame.exe>]
        the exe's extern / FSCommand name -> id table (the main menu's; getter and FSCommand ids are one list, ids
        repeat between the two kinds), and for build 592256 which getter switch has a case for each id
  python aptrefs.py xref [--sets menu,mp_hud,...] [--movie <movie>]... [--exe <exe>] [--out <file>] [--json <file>]
        every name across the movies: kind (extern / FSCommand / event), the movies using it, its engine id, the
        getter case (build 592256), and whether the exe has the name as a string at all. Default: every movie set
        of the game copy (menu, mp_hud, mp_loader, awards, mycharacter; Package over Dist);
        --out defaults to <OPFOR>\work\tools_out\aptrefs.txt (and .json)
  python aptrefs.py find <name> [same options as xref]
        one name (a part of it, any case) across the movies and the exe's table

<movie>: a menu .res, a .apt (its .const beside it), a name from the main menu bundle (main_nav), pristine:<name> or
<bundle.fbrb>:<name> (see menucommon.py). Reads the exe's data only (its strings and tables; no code is decoded).
The engine side (build 592256): the name -> id table at 0x0151EAA8 is two runs the exe registers one after the other
(0x7C0000): 260 FSCommand names, then 386 extern names at 0x0151F2C8. Extern names go through the screen's getter
(main menu 0x848DE0, HUD 0x84AB80, the loading screen's 0x836C40) and their id, then the getter's own switch, then
the UI manager's generic getter 0x7DA420. Only the main menu getter's and the generic getter's switch tables are read
here; a name only the HUD reads may be answered by the HUD getter, which is not.
"""
import json
import os
import re
import struct
import sys

import menucommon as mc

IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
STRING_PUSH = (0xA1, 0xA2, 0xA3)                 # EA_PushString, EA_PushConstant, EA_PushWordConstant
CALLS = {0xB0: 'func', 0xB1: 'func', 0xB2: 'method', 0xB3: 'method'}
COUNT_PUSH = {0x59: 0, 0x5A: 1}                  # EA_PushZero / EA_PushOne; EA_PushByte (0xB5) carries the count

# Build 592256 (PE time stamp 0x4CC17C02): the getters' switch tables (index table of bytes, jump table of
# addresses; (None, 1, id, target): a single compare before the switch); ids outside a switch, or on its default
# case, fall through to the next getter. The main menu getter's default calls the generic getter.
BUILD = '592256'
BUILD_STAMP = 0x4CC17C02
GETTERS = [
    ('menu', 'main menu getter 0x848DE0', 0x84A37D, [(0x84A474, 0xF2, 0x35, 0x84A3D8), (None, 1, 0x12E, 0x849B4F),
                                                     (0x84A5C8, 0x58, 0x12F, 0x84A568)]),
    ('generic', 'UI manager getter 0x7DA420', 0x7DD49F, [(0x7DD6F8, 0x187, 1, 0x7DD4F8)]),
]


# ------------------------------------------------------------------------------------------------ the exe

def extern_table(img):
    """The exe's name -> id table: [(name, id, entry va)]. Found from the string "presenceRequestOnline" (an FSCommand
    every build has): the {char* name, u32 id} pair that points at it, then the run of such pairs around it."""
    def pair(off):
        if off < 0 or off + 8 > len(img.b):
            return None
        p, i = struct.unpack_from('<II', img.b, off)
        s = img.cstr(p, 80)
        return (s, i) if s and IDENT.match(s) and i < 0x1000 else None
    best = []
    for va in img.find_cstring('presenceRequestOnline'):
        for j in img.find_dword(va):
            if not pair(j):
                continue
            lo = j
            while pair(lo - 8):
                lo -= 8
            hi = j
            while pair(hi + 8):
                hi += 8
            run = [(*pair(o), img.va(o)) for o in range(lo, hi + 8, 8)]
            if len(run) > len(best):
                best = run
    if len(best) < 50:
        raise mc.MenuToolError(f'{img.path}: the extern / FSCommand name table was not found (is it mohmpgame.exe?)')
    return best


def getter_cases(img):
    """{getter key: {id: case address}} for build 592256; {} for another build."""
    if img.timestamp != BUILD_STAMP:
        return {}
    out = {}
    for key, _, default, switches in GETTERS:
        cases = {}
        for index, count, first, jumps in switches:
            if index is None:
                cases[first] = jumps
                continue
            io = img.off(index)
            for k in range(count):
                t = img.b[io + k]
                target = struct.unpack_from('<I', img.b, img.off(jumps + 4 * t))[0]
                if target != default:
                    cases[first + k] = target
        out[key] = cases
    return out


# ------------------------------------------------------------------------------------------------ the movies

def string_arg(ins):
    """The string an instruction pushes, or None."""
    if ins.op in STRING_PUSH and ins.arg.startswith('"') and ins.arg.endswith('"'):
        return ins.arg[1:-1]
    if ins.op == 0x96 and ins.arg.count('"') == 2 and ins.arg.startswith('"') and ', ' not in ins.arg.split('   [list')[0]:
        return ins.arg.split('"')[1]
    return None


def count_arg(ins):
    if ins.op in COUNT_PUSH:
        return COUNT_PUSH[ins.op]
    if ins.op == 0xB5:
        return int(ins.arg)
    return None


def movie_refs(code):
    """{'extern': {name: [offsets]}, 'extern_param': {...}, 'extern_write': {...}, 'fscommand': {...},
    'events': {name: [(offset, params or None, how)]}} of one movie (aptcheck.Code)."""
    streams, funcs_by_name = {}, {}
    for ins, start, limit, depth in code.recs:
        streams.setdefault(start, []).append(ins)
        if ins.func and ins.func.name:
            funcs_by_name.setdefault(ins.func.name, ins.func.nparams)
    out = {'extern': {}, 'extern_param': {}, 'extern_write': {}, 'fscommand': {}, 'events': {}}

    def add(kind, name, at, extra=None):
        out[kind].setdefault(name, []).append(at if extra is None else (at, *extra))

    def handler(ins):
        if ins is None:
            return None, 'handler unknown'
        if ins.func:
            return ins.func.nparams, 'function'
        name = string_arg(ins) or (ins.arg[1:-1] if ins.op in (0xAE, 0xAF) and ins.arg.startswith('"') else None)
        if name and name in funcs_by_name:
            return funcs_by_name[name], f'function {name}'
        return None, f'handler {name}' if name else 'handler unknown'

    for start, L in streams.items():
        L.sort(key=lambda i: i.at)
        for k, ins in enumerate(L):
            nxt = L[k + 1] if k + 1 < len(L) else None
            s = string_arg(ins)
            # extern.<name>
            if ins.op in (0xAE, 0xAF, 0xA4, 0xA5) and ins.arg == '"extern"' and nxt is not None:
                if nxt.op in (0xAF, 0xA5, 0xB2, 0xB3) and nxt.arg.startswith('"'):
                    add('extern', nxt.arg.strip('"'), nxt.at)
                else:
                    n = string_arg(nxt)
                    if n and '$' in n:                 # name$param: the parameter added at run time, or fixed
                        add('extern_param', n.split('$')[0], nxt.at)
                    elif n:
                        after = [x.op for x in L[k + 2:k + 6]]
                        if after and after[0] == 0x4E:
                            add('extern', n, nxt.at)
                        elif 0x4F in after:
                            add('extern_write', n, nxt.at)
                        else:
                            add('extern', n, nxt.at)
            # FSCommands: a "FSCommand:<name>" string, or fscommand / fscommandProxy calls with a constant name
            for text in ([s] if s else []) + ([ins.arg.split('"')[1]] if ins.op == 0x83 and ins.arg.startswith('"') else []):
                if text.startswith('FSCommand:') and len(text) > 10:
                    add('fscommand', text[10:], ins.at)
            # MOH's movies: "FSCommand:" + "<name>" (StringAdd), the argument pushed after, then GetURL2
            if s == 'FSCommand:' and nxt is not None and k + 2 < len(L) and L[k + 2].op == 0x21:
                n = string_arg(nxt)
                if n:
                    add('fscommand', n, nxt.at)
            if ins.op in CALLS and ins.arg.strip('"').lower() in ('fscommand', 'fscommandproxy', 'catchevent'):
                callee = ins.arg.strip('"').lower()
                back = L[max(0, k - 5):k]
                skip = 1 if CALLS[ins.op] == 'method' else 0
                # find the argument count push, then the first argument before it
                for j in range(len(back) - 1 - skip, -1, -1):
                    if count_arg(back[j]) is not None and count_arg(back[j]) >= 1 and j >= 1:
                        name = string_arg(back[j - 1])
                        if name:
                            if callee == 'catchevent':
                                params, how = handler(back[j - 2] if j >= 2 else None)
                                add('events', name, ins.at, (params, 'catchEvent ' + how))
                            else:
                                add('fscommand', name, ins.at)
                        break
            # gameEventListener.<name> = function (...)
            if ins.op in (0xAE, 0xAF) and ins.arg == '"gameEventListener"' and nxt is not None:
                name = string_arg(nxt)
                if name and k + 2 < len(L):
                    params, how = handler(L[k + 2])
                    if params is not None:             # a handler (not e.g. gameEventListener.frame = this)
                        add('events', name, nxt.at, (params, 'gameEventListener ' + how))
    return out


def scan_movies(specs, sets, root):
    """[(label, movie name, refs)] for the movie specs given, else the movie sets of the game copy."""
    import aptcheck
    found = []
    if specs:
        for spec in specs:
            label, members = mc.resolve(spec, root)
            m = mc.movie_from_members(members, label)
            found.append((label, m.name, movie_refs(aptcheck.Code(m))))
        return found
    root = root or mc.find_opfor()
    for key, rel in mc.MOVIE_SETS:
        if sets and key not in sets:
            continue
        layers = mc.bundle_layers(root, rel)
        if not layers:
            continue
        movies = mc.bundle_movies(layers)
        from mohcore import fbrb
        by_bundle = {}
        for name, (bundle, entry) in movies.items():
            by_bundle.setdefault(bundle, []).append(entry)
        for bundle, entries in by_bundle.items():
            data = fbrb.read(bundle, entries)
            for entry in sorted(entries):
                try:
                    members = mc.parse_bigf(data[entry], entry)
                    m = mc.movie_from_members(members, entry)
                except mc.MenuToolError:
                    continue                       # a .res that is not a movie
                found.append((f'{key}:{m.name}', m.name, movie_refs(aptcheck.Code(m))))
    return found


# ------------------------------------------------------------------------------------------------ output

def cross_reference(found, table, cases, exe_strings):
    ids = {}
    for name, i, va in table:
        ids.setdefault(name, i)
    rows = {}
    for label, _, refs in found:
        for kind, key in (('extern', 'extern'), ('extern', 'extern_param'), ('extern-write', 'extern_write'),
                          ('fscommand', 'fscommand'), ('event', 'events')):
            for name, uses in refs[key].items():
                r = rows.setdefault((kind, name), {'kind': kind, 'name': name, 'movies': {}, 'param': False, 'params': set()})
                r['movies'][label] = len(uses)
                if key == 'extern_param':
                    r['param'] = True
                if key == 'events':
                    r['params'].update(u[1] for u in uses if u[1] is not None)
    out = []
    for (kind, name), r in sorted(rows.items(), key=lambda x: (x[0][0], x[0][1].lower())):
        i = ids.get(name)
        case = None
        if kind == 'extern' and i is not None:
            for key, title, _, _ in GETTERS:
                if i in cases.get(key, {}):
                    case = f'{key} {cases[key][i]:#x}'
                    break
            if case is None and cases:
                hud_only = all(not m.startswith('menu:') for m in r['movies']) and any(':' in m for m in r['movies'])
                case = 'none - HUD getter not read' if hud_only else 'none'
        out.append({'kind': kind, 'name': name + ('$' if r['param'] else ''), 'id': i, 'getter_case': case,
                    'in_exe': name in exe_strings if exe_strings is not None else None,
                    'params': sorted(r['params']), 'movies': r['movies']})
    return out


def print_rows(rows, f):
    for kind in ('extern', 'extern-write', 'fscommand', 'event'):
        sel = [r for r in rows if r['kind'] == kind]
        if not sel:
            continue
        title = {'extern': 'extern values read', 'extern-write': 'extern values written', 'fscommand': 'FSCommands sent',
                 'event': 'engine events handled'}[kind]
        print(f'\n== {title} ({len(sel)})', file=f)
        for r in sel:
            bits = [f'id {r["id"]:#x}' if r['id'] is not None else ('id -' if kind != 'event' else '')]
            if r['getter_case']:
                bits.append(f'case {r["getter_case"]}')
            if kind == 'event':
                bits.append(f'params {"/".join(map(str, r["params"])) or "?"}')
            if r['in_exe'] is False:
                bits.append('NOT in the exe')
            movies = ', '.join(f'{m}' + (f' x{n}' if n > 1 else '') for m, n in sorted(r['movies'].items()))
            print(f'  {r["name"]:36s} {"  ".join(b for b in bits if b):48s} {movies}', file=f)


def print_movie(label, refs, f):
    print(f'\n{label}', file=f)
    for key, title in (('extern', 'extern read'), ('extern_param', 'extern read with a parameter (name$param)'),
                       ('extern_write', 'extern written'), ('fscommand', 'FSCommand sent')):
        if refs[key]:
            print(f'  {title}: ' + ', '.join(f'{n}' + (f' x{len(v)}' if len(v) > 1 else '') for n, v in sorted(refs[key].items())), file=f)
    if refs['events']:
        print('  events handled:', file=f)
        for n, uses in sorted(refs['events'].items()):
            for at, params, how in uses:
                print(f'    {n:32s} {("?" if params is None else params)} param(s)   {how} at {at:06X}', file=f)


# ------------------------------------------------------------------------------------------------ commands

def take(args, flag, many=False):
    vals = []
    while flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            raise mc.MenuToolError(f'{flag} needs a value')
        vals.append(args[i + 1])
        del args[i:i + 2]
    return vals if many else (vals[-1] if vals else None)


def main(argv):
    args = list(argv[1:])
    if not args or args[0] not in ('movie', 'table', 'xref', 'find'):
        print(__doc__)
        return 1 if args else 0
    cmd = args.pop(0)
    root_given = take(args, '--opfor')
    exe = take(args, '--exe')
    js = take(args, '--json')
    if cmd == 'movie':
        if not args:
            raise mc.MenuToolError('movie needs one or more movies')
        found = scan_movies(args, None, root_given)
        for label, _, refs in found:
            print_movie(label, refs, sys.stdout)
        if js:
            json.dump({label: refs for label, _, refs in found}, open(js, 'w', encoding='utf-8'), indent=1, default=list)
        return 0
    img = mc.PEImage(mc.find_exe(root_given, exe))
    table = extern_table(img)
    cases = getter_cases(img)
    if cmd == 'table':
        print(f'{img.path}: {len(table)} names, table at {table[0][2]:#x}'
              + ('' if cases else f' (not build {BUILD}: getter cases not read)'))
        for name, i, va in table:
            c = [f'{k} {cases[k][i]:#x}' for k, *_ in GETTERS if i in cases.get(k, {})]
            print(f'  {i:#6x}  {name:40s} {"  ".join(c)}')
        return 0
    sets = take(args, '--sets')
    movies = take(args, '--movie', many=True)
    out = take(args, '--out')
    found = scan_movies(movies, set(sets.split(',')) if sets else None, root_given)
    rows = cross_reference(found, table, cases, img.strings())
    if cmd == 'find':
        if not args:
            raise mc.MenuToolError('find needs a name')
        want = args[0].lower()
        sel = [r for r in rows if want in r['name'].lower()]
        print_rows(sel, sys.stdout)
        tab = [(n, i) for n, i, _ in table if want in n.lower()]
        if tab:
            print('\n== in the exe\'s name table: ' + ', '.join(f'{n} {i:#x}' for n, i in tab))
        if not sel and not tab:
            print(f'"{args[0]}": not used by the {len(found)} movies read, not in the exe\'s name table')
        return 0
    if not out:
        root = root_given or mc.find_opfor()
        out = os.path.join(mc.out_dir(root), 'aptrefs.txt')
        js = js or os.path.join(mc.out_dir(root), 'aptrefs.json')
    with open(out, 'w', encoding='utf-8') as f:
        print(f'aptrefs: {len(found)} movies, exe {img.path} ({len(table)} names'
              + ('' if cases else f'; not build {BUILD}: getter cases not read') + ')', file=f)
        print_rows(rows, f)
        print('\n\n== per movie', file=f)
        for label, _, refs in found:
            print_movie(label, refs, f)
    if js:
        json.dump({'movies': {label: refs for label, _, refs in found}, 'names': rows,
                   'table': [{'name': n, 'id': i} for n, i, _ in table]}, open(js, 'w', encoding='utf-8'), indent=1, default=list)
    kinds = {}
    for r in rows:
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    unanswered = [r['name'] for r in rows if r['kind'] == 'extern' and r['getter_case'] == 'none']
    print(f'{len(found)} movies: ' + ', '.join(f'{v} {k}' for k, v in sorted(kinds.items())) + f' -> {out}'
          + (f' and {js}' if js else ''))
    if unanswered:
        print(f'{len(unanswered)} extern name(s) main menu movies read with no case in the menu or generic getter: '
              + ', '.join(unanswered[:15]) + (' ...' if len(unanswered) > 15 else ''))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(mc.SUITE, 'core'))
    from mohcore import errors
    errors.setup('aptrefs', gui=False)         # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
