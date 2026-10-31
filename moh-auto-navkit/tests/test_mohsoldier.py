r"""NavKit's MOH soldier (tools\mohsoldier.py): the values every tool takes from it (sampler capsule, walkability,
link steps, headroom, node heights, water), its derived values, and - when Project OPFOR's game copy has been
prepared (work\navkit\common) - the same values in the game's DefaultSoldierPhysics. The MOH-specific readers
beside it: levelobjects' spawn and objective types on a level partition built here (a spawner's marker - a
SoldierSpawnEntityData listing alternative spawn points - is no spawn point).

  python test_mohsoldier.py [<OPFOR root>]
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'core'))
import mohsoldier as ms  # noqa: E402


LEVEL = '''<?xml version="1.0"?>
<partition guid="P" primaryInstance="L" exportMode="All">
\t<instance guid="A1" type="GameSharedResources.AlternateSpawnEntityData">
\t\t<complex name="Transform">1/0/0/0/0/1/0/0/0/0/1/0/10/20/30/0</complex>
\t</instance>
\t<instance guid="S1" type="GameSharedResources.SoldierSpawnEntityData">
\t\t<complex name="Transform">1/0/0/0/0/1/0/0/0/0/1/0/1/74.7/2/0</complex>
\t</instance>
\t<instance guid="S2" type="GameSharedResources.SoldierSpawnEntityData">
\t\t<complex name="Transform">1/0/0/0/0/1/0/0/0/0/1/0/3/80/4/0</complex>
\t\t<array name="AlternativeSpawnPoints">
\t\t\t<item ref="a1" />
\t\t</array>
\t\t<field name="UseAsAlternateSpawnPoint">false</field>
\t</instance>
\t<instance guid="O1" type="GameSharedResources.ObjectiveTriggerEntityData">
\t\t<complex name="Transform">1/0/0/0/0/1/0/0/0/0/1/0/5/6/7/0</complex>
\t</instance>
\t<instance guid="C1" type="GameSharedResources.CapturePointEntityData">
\t\t<complex name="Transform">1/0/0/0/0/1/0/0/0/0/1/0/8/9/10/0</complex>
\t</instance>
\t<instance guid="G1" type="Entity.AreaGeometryEntityData">
\t\t<complex name="Transform">1/0/0/0/0/1/0/0/0/0/1/0/100/0/100/0</complex>
\t</instance>
</partition>
'''


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}')

    check((ms.RADIUS, ms.HEIGHT, ms.CROUCH_HEIGHT, ms.STEP, ms.SLOPE, ms.JUMP, ms.WATER_DEPTH) ==
          (0.35, 1.8, 1.15, 0.3, 45.0, 0.65, 1.4), 'MOH soldier: 0.35 / 1.8 / 1.15 / 0.3 / 45 / 0.65 / 1.4')
    check((ms.Y_OFFSET, ms.AREA_Y_MAX, ms.HEADROOM, ms.HIGH_STEP) == (0.9, 1.44, 1.9, 0.65),
          'derived: node height 0.9, area rise 1.44, headroom 1.9, high run 0.65')
    check(ms.link_step(0.8) == 0.8 and ms.link_step(0.2) == 0.3, 'link step: the 45 deg slope over 0.8 m, the step at 0.2 m')
    lo, hi = ms.capsule_axis(ms.STEP)
    check(abs(lo - 0.65) < 1e-9 and abs(hi - 1.45) < 1e-9, 'capsule: sphere centres 0.65 .. 1.45 m over the floor')

    import navbuild
    import navsample_offline
    import navwalk
    check(navsample_offline.RADIUS == ms.RADIUS and navsample_offline.HEIGHT == ms.HEIGHT and
          navsample_offline.CAP_BOTTOMS == {'': ms.STEP, '_hi': ms.HIGH_STEP}, 'the sampler uses the soldier')
    check(navbuild.RADIUS == ms.RADIUS and navbuild.Y_OFFSET == ms.Y_OFFSET and navbuild.MAX_WATER_DEPTH == ms.WATER_DEPTH
          and navbuild.LINK_STEP == ms.link_step(0.8), 'the build uses the soldier')
    check(navwalk.HEADROOM_BAND[1] == ms.HEADROOM and navwalk.OUTLINE_Y_OFFSET == ms.Y_OFFSET,
          'walkability uses the soldier')
    check(navsample_offline.BODIES == 'hcdE', "collision layers: h, c, detail, and e where it is an asset's only one")

    import levelobjects
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'MP_TEST.xml')
        open(path, 'w', encoding='utf-8').write(LEVEL)
        x = LEVEL                                    # levelobjects' readers take the partition's text
        sp = levelobjects.spawn_points(x)
        check(sorted(map(tuple, sp)) == [(1.0, 74.7, 2.0), (10.0, 20.0, 30.0)],
              f'spawns: alternate, and a soldier spawn that is no spawner marker (S2 left out) {sp}')
        op = levelobjects.order_points(x)
        check(sorted(map(tuple, op)) == [(1.0, 74.7, 2.0), (5.0, 6.0, 7.0), (8.0, 9.0, 10.0), (10.0, 20.0, 30.0)],
              f'order points: objectives and spawns, no area-geometry vertices {op}')
        check(sorted(map(tuple, levelobjects.area_orders(x))) == [(5.0, 6.0, 7.0), (8.0, 9.0, 10.0)],
              'objective floors: objective triggers and capture points')
        import ordercheck
        kinds = sorted(k for k, _n, _p in ordercheck.order_points(path))
        check(kinds == ['AlternateSpawn', 'CapturePoint', 'ObjectiveTrigger', 'SoldierSpawn'],
              f'the check takes objectives and spawns on a level without AI orders {kinds}')

    # DbxEdit writes a denormal float as its bits (MP_09_TDM's invisible walls): read back as about 0, not 98304
    from mohcore import xmlinst
    m = xmlinst.transform('3.42285418E-08/-1.39582864E-33/0.9999999/-431602080/98304/1/1.39582864E-33/-431602080/'
                          '-0.9999999/181005/3.42285418E-08/-431602080/27.7396584/69.45882/-6.44292927/-431602080')
    check(abs(m[4]) < 1e-30 and abs(m[9]) < 1e-30 and m[5] == 1.0 and abs(m[12] - 27.7396584) < 1e-6,
          'transform: a denormal written as its bits reads as ~0; rotation and position kept')
    m2 = xmlinst.transform('2/0/0/0/0/2/0/0/0/0/2/0/1500/20/3000/0')
    check(m2[0] == 2.0 and m2[12] == 1500.0 and m2[14] == 3000.0, 'transform: a small scale and large positions kept')

    root = argv[1] if len(argv) > 1 else None
    if root is None:
        import navkit
        try:
            root = navkit.find_opfor(None)
        except Exception:                   # no OPFOR next to the suite: the game check is skipped
            root = None
    diff = ms.differences(root) if root else None
    if diff is None:
        print('skip game copy: DefaultSoldierPhysics not unpacked (navkit.py prepare a level first)')
    else:
        check(not diff, f"the game's DefaultSoldierPhysics has the same values {diff}")
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
