r"""NavKit's vehicle path data on one real level (game tier; needs an OPFOR install with the level prepared and sampled -
`navkit.py prepare` / `sample`): builds ONS_MP_002's vehicle file (tools/navvehicle.py, about a minute) into OPFOR's
work\navvehicle\test\, then checks it: hpdcheck's structure checks, a byte-exact round trip, DICE's vehicle header
values, and parity against DICE's vehicle file of the level above floors set below today's measured values (so a
change that loses ground or connectivity fails). Nothing is written into the game folder.

  python test_vehicle_level.py [--opfor <folder>] [--level ons_mp_002]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import hpdcheck  # noqa: E402
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import navvehicle  # noqa: E402

# floors: today's measured values on ONS_MP_002 minus a margin (see README "Vehicle path data")
FLOORS = {'ground': 82.0, 'connectivity': 50.0}     # measured 2026-10-26: 87.34 / 58.65
CEILINGS = {'extra_ground': 55.0}                    # measured 49.00


def main(argv):
    root = navvehicle.opfor_root(argv[argv.index('--opfor') + 1] if '--opfor' in argv else None)
    lv = argv[argv.index('--level') + 1] if '--level' in argv else 'ons_mp_002'
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    ref = navvehicle.reference_of(root, lv)
    if not ref or not navvehicle.samples_of(root, lv):
        print(f'SKIP {lv}: no DICE vehicle file or no in-game samples in {root}')
        return 0
    out = os.path.join(root, 'work', 'navvehicle', 'test', lv, 'pathdata.hpd')
    out, st = navvehicle.build_level(root, lv, out, 'reference', log=lambda *a: None)
    check(os.path.isfile(out) and 'game' not in os.path.relpath(out, root).split(os.sep)[0].lower(),
          f'built into the work folder ({os.path.relpath(out, root)}, {st["seconds"]} s)')
    data = open(out, 'rb').read()
    f = hpdtool.parse(data)
    check(hpdtool.write(f) == data, 'byte-exact round trip (hpdtool)')
    meshes = [kymesh.parse_mesh(s.mesh) for s in f.sectors]
    check(all(kymesh.write_mesh(m) == s.mesh for m, s in zip(meshes, f.sectors)), 'byte-exact mesh round trip (kymesh)')
    bad = {k: v for k, v in hpdcheck.check(out).items() if v}
    check(not bad, 'hpdcheck structure checks' + (f' - {list(bad.items())[0][0]}: {list(bad.items())[0][1][:2]}' if bad else ''))
    inf = navvehicle.info(out)
    check(set(inf['pitch']) == {1.0} and set(inf['radius']) == {2.5} and inf['dist_edge_max'] == [50.0],
          'vehicle settings: pitch 1, radius 2.5, DistEdgeMax 50')
    check(inf['offsets'] == navvehicle.file_offsets(ref), f'sector offsets as DICE\'s file ({inf["offsets"]})')
    r = navvehicle.compare(ref, out, log=print)
    for k, v in FLOORS.items():
        check(r[k] >= v, f'{k} {r[k]} >= {v}')
    for k, v in CEILINGS.items():
        check(r[k] <= v, f'{k} {r[k]} <= {v}')
    print('all passed' if not failed else f'{failed} FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
