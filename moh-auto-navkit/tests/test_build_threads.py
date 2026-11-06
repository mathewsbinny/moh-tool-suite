r"""NavKit builds are thread-safe (navbuild.build: the grid settings in a frozen navbuild.Pitch, the build's own data in
a BuildState): two levels built at once in two threads come out byte for byte as each built alone - the .hpd, its
.cells.npz and the build's log lines. Before 2026-11-01 the pitch settings were module globals switched by set_pitch,
so a detailed-zone pass (pitch 0.2) in one thread changed the grid of a normal build (0.8) in the other: real levels
came out wrong, and with that navbuild.py this test's synth_a fails in every round (min() of no sectors, 2026-11-05).

Quick (no game data): two small synthetic levels made here - A at pitch 0.8 only (rolling terrain, a 0.7 m terrace,
a deck on a ramp, a pit), B with a detailed zone (a pitch-0.2 run over one of its two sectors holding a stair of
0.15 m steps and a low wall) - each built alone, then both at once in two threads, three times.
--game: mp_07_koth and mp_03_elimination from their built sample inputs (navkit.build_inputs), each built alone in
its own process (both processes at once), then both in two threads of this one; outputs only in a temp folder
(several minutes).

time.localtime is fixed during the builds, so the sectors' date / clock fields and the .npz members' timestamps are
the same in every run.

  python test_build_threads.py [--game] [--keep]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', 'tools')
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import navbuild  # noqa: E402
import navsample  # noqa: E402
import navsample_offline as no  # noqa: E402

FIXED = time.struct_time((2026, 11, 1, 12, 0, 0, 6, 305, 0))
ROUNDS = 3
GAME_LEVELS = ('mp_07_koth', 'mp_03_elimination')
TIMING = re.compile(r'\(\d+ s\)')


# ---------------------------------------------------------------------------------------------------------- levels

def run(pitch, x0, z0, x1, z1, ground, decks=(), blocked=None):
    """A sampling run (navsample.Samples) over [x0, x1] x [z0, z1] at pitch: per column the terrain at ground(x, z)
    (None: no ground, a pit) and, under each deck (x0, z0, x1, z1, top, thickness), its top and its underside (blocked:
    the capsule does not fit inside a deck); blocked(x, z): terrain columns flagged blocked (a wall)."""
    nx, nz = int(round((x1 - x0) / pitch)) + 1, int(round((z1 - z0) / pitch)) + 1
    counts, ys, ns, fs, hs = [], [], [], [], []
    e = 0.01
    for j in range(nz):
        for i in range(nx):
            x, z = x0 + i * pitch, z0 + j * pitch
            col = []
            for dx0, dz0, dx1, dz1, top, th in decks:
                if dx0 <= x <= dx1 and dz0 <= z <= dz1:
                    col.append((top, (0.0, 1.0, 0.0), 0, no.MESH_SHAPE_VTABLE))
                    col.append((top - th, (0.0, 1.0, 0.0), navsample.BLOCKED, no.MESH_SHAPE_VTABLE))
            g = ground(x, z)
            if g is not None:
                at = lambda u, v: g if ground(u, v) is None else ground(u, v)
                gx = (at(x + e, z) - at(x - e, z)) / (2 * e)
                gz = (at(x, z + e) - at(x, z - e)) / (2 * e)
                n = np.array([-gx, 1.0, -gz])
                flag = navsample.BLOCKED if blocked is not None and blocked(x, z) else 0
                col.append((g, tuple(n / np.linalg.norm(n)), flag, no.TERRAIN_SHAPE_VTABLE))
            col.sort(key=lambda c: -c[0])
            counts.append(len(col))
            for y, n, f, h in col:
                ys.append(y)
                ns.append(n)
                fs.append(f)
                hs.append((h, 0, 0))
    start = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    return navsample.Samples(pitch, x0, z0, nx, nz, 60.0, -20.0, no.RADIUS, no.CAP_BOTTOMS[''], no.HEIGHT,
                             no.RAY_RADIUS, start, np.array(ys, np.float32), np.array(ns, np.float32).reshape(-1, 3),
                             np.array(fs, np.uint8), np.array(hs, np.int64).reshape(-1, 3))


def ground_a(x, z):
    """Level A: rolling ground, a terrace 0.7 m up for x > 110, a pit."""
    if 66 <= x <= 70 and 10 <= z <= 14:
        return None
    y = 0.6 * np.sin(x / 9.0) * np.cos(z / 7.0)
    if x > 110:
        y += 0.7
    return float(y)


def ground_b(x, z):
    """Level B: a slope rising along z; in sector (1, 0) a stair of 0.15 m steps every 0.4 m (x 106 .. 112)."""
    y = 0.04 * z + 0.3 * np.sin(x / 13.0)
    if 106 <= x < 112 and 4 <= z <= 20:
        y += 0.15 * (int((x - 106) / 0.4) + 1)
    elif x >= 112 and 4 <= z <= 20:
        y += 0.15 * 15
    return float(y)


def wall_b(x, z):
    """Level B: a low wall (blocked columns) across the detailed sector, with a gap."""
    return 116 <= x <= 117 and not 10 <= z <= 13


def make_inputs(d):
    """The two synthetic levels' build inputs in folder d: {name: (samples, forbid, xoff, zoff, keyword arguments)}."""
    deck = (120.0, 12.0, 130.0, 22.0, 3.5, 0.3)                  # level A: a deck on a ramp
    ramp = lambda x, z: (0.7 + (x - 106.0) * 0.2 if 106 <= x < 120 and 15 <= z <= 19 else None)

    def ground_a2(x, z):
        r = ramp(x, z)
        return r if r is not None and r > ground_a(x, z) else ground_a(x, z)
    a = os.path.join(d, 'synth_a_navkit.bin')
    no.write(a, run(0.8, 56.0, 0.0, 144.0, 32.0, ground_a2, decks=[deck]))
    b = os.path.join(d, 'synth_b_navkit.bin')
    no.write(b, run(0.8, 64.0, 0.0, 124.0, 24.0, ground_b, blocked=wall_b))
    b02 = os.path.join(d, 'synth_b_navkit_p02.bin')
    no.write(b02, run(0.2, 96.0, 0.0, 124.8, 24.8, ground_b, blocked=wall_b))
    return {
        'synth_a': (a, None, 0, 0, dict(seeds=[(60.0, float(ground_a(60.0, 5.0)), 5.0)],
                                        order_points=[(125.0, 3.5, 17.0)])),
        'synth_b': (b, None, 0, 0, dict(seeds=[(70.0, float(ground_b(70.0, 5.0)), 5.0)],
                                        detailed=dict(samples=b02, high=None, sectors={(1, 0)}))),
    }


def game_inputs():
    """The game levels' build inputs (navkit.build_inputs: their built samples, detailed zones, ladders, ...)."""
    import navkit
    pr = navkit.Project(navkit.find_opfor(None))
    out = {}
    for lv in GAME_LEVELS:
        samples, forbid, xoff, zoff, kw = navkit.build_inputs(pr, lv, log=lambda *a: None)
        kw.pop('log', None)
        out[lv] = (samples, forbid, xoff, zoff, kw)
    return pr, out


def listing(pr):
    """(name, size, mtime) of every file in the game levels' NavKit folders (work\\navkit\\<level>): the test writes
    none of them."""
    got = []
    for lv in GAME_LEVELS:
        d = os.path.join(pr.work, 'navkit', lv)
        for base, _dirs, files in os.walk(d):
            for f in files:
                st = os.stat(os.path.join(base, f))
                got.append((os.path.relpath(os.path.join(base, f), pr.work), st.st_size, st.st_mtime_ns))
    return sorted(got)


# ---------------------------------------------------------------------------------------------------------- builds

def build_one(name, inputs, out_dir, errors, barrier=None):
    """Builds one level into out_dir\\<name>.hpd (and .cells.npz, .log: its log lines)."""
    try:
        samples, forbid, xoff, zoff, kw = inputs[name]
        lines = []
        kw = dict(kw, log=lambda *a: lines.append(' '.join(str(x) for x in a)))
        if barrier is not None:
            barrier.wait()
        navbuild.build(samples, forbid, xoff, zoff, os.path.join(out_dir, name + '.hpd'), **kw)
        with open(os.path.join(out_dir, name + '.log'), 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
    except BaseException as e:  # noqa: BLE001 - reported as a failed check
        errors.append(f'{name}: {type(e).__name__}: {e}')


def threaded(names, inputs, out_dir):
    """Builds the levels at once, one thread each (started together). Returns the errors."""
    os.makedirs(out_dir, exist_ok=True)
    errors = []
    barrier = threading.Barrier(len(names))
    ths = [threading.Thread(target=build_one, args=(n, inputs, out_dir, errors, barrier), name=n) for n in names]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return errors


def outputs(out_dir, name):
    """(hpd bytes, cells.npz bytes, log lines with timings and the folder masked) of a build."""
    def read(ext):
        p = os.path.join(out_dir, name + ext)
        return open(p, 'rb').read() if os.path.isfile(p) else None
    log = read('.log')
    lines = []
    if log is not None:
        txt = log.decode('utf-8').replace(os.path.abspath(out_dir), '<out>').replace(out_dir, '<out>')
        lines = [TIMING.sub('(T s)', ln) for ln in txt.splitlines()]
    return read('.hpd'), read('.cells.npz'), lines


def differences(a, b):
    """What differs between two builds' outputs (empty when they are the same)."""
    diff = []
    for what, x, y in (('hpd', a[0], b[0]), ('cells', a[1], b[1])):
        if x is None or y is None:
            diff.append(f'{what} missing')
        elif x != y:
            diff.append(f'{what} differs ({len(x)} / {len(y)} bytes)')
    if a[2] != b[2]:
        k = next((i for i, (p, q) in enumerate(zip(a[2], b[2])) if p != q), min(len(a[2]), len(b[2])))
        diff.append(f'log differs from line {k + 1}')
    return diff


def frozen_clock():
    """Fixes time.localtime (the sectors' date / clock, the .npz members' timestamps); returns the undo."""
    real = time.localtime
    time.localtime = lambda *a: FIXED
    return lambda: setattr(time, 'localtime', real)


# ------------------------------------------------------------------------------------------------------------ main

def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    game = '--game' in argv
    keep = '--keep' in argv
    tmp = tempfile.mkdtemp(prefix='navkit_threads_')
    undo = frozen_clock()
    old_switch = sys.getswitchinterval()
    try:
        t0 = time.time()
        if game:
            pr, inputs = game_inputs()
            before = listing(pr)
            names = list(GAME_LEVELS)
            print(f'{", ".join(names)}: inputs read; building alone (one process each, at once) ...', flush=True)
            solo_dir = os.path.join(tmp, 'solo')
            procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), '--solo-game', n, solo_dir])
                     for n in names]
            codes = [p.wait() for p in procs]
            check(all(c == 0 for c in codes), f'built alone in their own processes ({time.time() - t0:.0f} s, exit {codes})')
            rounds = 1
        else:
            inputs = make_inputs(os.path.join(tmp, 'inputs'))
            names = sorted(inputs)
            print(f'synthetic inputs written ({time.time() - t0:.1f} s)', flush=True)
            solo_dir = os.path.join(tmp, 'solo')
            os.makedirs(solo_dir)
            errors = []
            for n in names:
                t = time.time()
                build_one(n, inputs, solo_dir, errors)
                print(f'     {n} built alone in {time.time() - t:.1f} s', flush=True)
            check(not errors, f'built alone {errors or ""}')
            rounds = ROUNDS
            sys.setswitchinterval(0.0005)            # the threads take turns often: more chances to meet mid-stage
        solo = {n: outputs(solo_dir, n) for n in names}
        check(all(solo[n][0] and solo[n][1] for n in names),
              'solo builds: ' + ', '.join(f'{n} {len(solo[n][0] or b"")} B hpd, {len(solo[n][1] or b"")} B cells'
                                          for n in names))
        if not game:
            check(any('detailed pass: 1 sectors' in ln for ln in solo['synth_b'][2]) and
                  any('merged: 1 normal + 1 detailed sectors' in ln for ln in solo['synth_b'][2]),
                  'synth_b has a detailed sector built at pitch 0.2 (both pitches run in the threads)')
            again = os.path.join(tmp, 'solo2')
            os.makedirs(again)
            errors = []
            build_one('synth_b', inputs, again, errors)
            check(not errors and not differences(solo['synth_b'], outputs(again, 'synth_b')),
                  'a second solo build is the same (the build is deterministic)')
        for r in range(rounds):
            t = time.time()
            d = os.path.join(tmp, f'thread{r + 1}')
            errors = threaded(names, inputs, d)
            check(not errors, f'round {r + 1}: {len(names)} threads built ({time.time() - t:.1f} s) {errors or ""}')
            for n in names:
                diff = differences(solo[n], outputs(d, n))
                check(not diff, f'round {r + 1}: {n} in a thread = {n} alone (hpd, cells, log)'
                      + (f': {"; ".join(diff)}' if diff else ''))
        if game:
            check(listing(pr) == before, f'the levels\' NavKit folders untouched (outputs only in {tmp})')
        print(f'     {time.time() - t0:.1f} s in all')
    finally:
        sys.setswitchinterval(old_switch)
        undo()
        if keep:
            print(f'     outputs kept in {tmp}')
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


def solo_game(name, out_dir):
    """--solo-game <level> <folder>: one game level built alone in this process (the reference for the threads)."""
    undo = frozen_clock()
    try:
        _pr, inputs = game_inputs()
        os.makedirs(out_dir, exist_ok=True)
        errors = []
        t = time.time()
        build_one(name, {name: inputs[name]}, out_dir, errors)
        print(f'     {name} built alone in {time.time() - t:.0f} s {errors or ""}', flush=True)
        return 1 if errors else 0
    finally:
        undo()


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--solo-game':
        sys.exit(solo_game(sys.argv[2], sys.argv[3]))
    sys.exit(main(sys.argv))
