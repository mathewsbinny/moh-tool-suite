r"""Runs the suite's tests: one line per test, the failures' output at the end, exit code 1 if any failed.

  python run_tests.py [quick | game | full] [--only <name>] [--list]

  quick   no game data, about a minute: repository rules, DbxEdit unit tests, and on files built by the
          tests: FbrbTool, LevelDiff, the crash reader and exeref, the console readers, the menu tools, the test
          harness (needs Windows for the C# tools)
  game    + Project OPFOR's game copy, about 10 minutes: FbrbTool on 12 game bundles, water on every level,
          the level loader against its snapshot, terrain against the in-game samples, path-data and mesh
          round trips, LevelDiff on MP_005, the crash reader's code view and exeref on mohmpgame.exe, the menu
          tools on the game's movies and OPFOR's menu patches, the console
          readers on your own Xbox 360 files (skipped when they are not on the PC), the 3D tools opening a
          level (needs a display), the structure of DICE's path data (hpdcheck), the test harness on two small
          levels, NavKit's regression test on two small levels
  full    + the corpus runs, about 90 minutes: DbxEdit and the Python DBX reader on every unpacked .dbx,
          FbrbTool on all 1,055 bundles, NavKit's regression test on the 13 campaign levels and the imported
          Onslaught maps (about 5 hours)

Run `quick` before every commit and `game` before every push; `full` after changes to a format reader.
"""
import os
import subprocess
import sys
import time

SUITE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TIERS = ['quick', 'game', 'full']


def opfor():
    sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
    import navkit
    return navkit.find_opfor(None)


def dice_pathdata_files(root):
    """DICE's path-data files in the game copy: pathdata.hpd, or its kept original where NavKit's is installed."""
    out = []
    for dp, _, fs in os.walk(os.path.join(root, 'game', 'Output', 'win32', 'ai')):
        if 'pathdata.hpd' in [f.lower() for f in fs]:
            orig = [f for f in fs if f.lower().startswith('pathdata.hpd.')]
            out.append(os.path.join(dp, orig[0] if orig else 'pathdata.hpd'))
    return sorted(out)


def pathdata_files(root):
    """Every path-data file: the game copy's (with DICE's originals kept as pathdata.hpd.<suffix> where NavKit's
    is installed) and OPFOR's work folder's (Xbox Onslaught copies, NavKit's own builds)."""
    out = []
    for top in (os.path.join(root, 'game', 'Output', 'win32', 'ai'), os.path.join(root, 'work')):
        for dp, _, fs in os.walk(top):
            out += [os.path.join(dp, f) for f in fs if f.lower().startswith('pathdata.hpd')]
    return sorted(out)


def tests():
    """(name, tier, argv). Game paths are only looked up for the tiers that need them."""
    t = [
        ('repository rules', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_repo.py')]),
        ('DbxEdit unit tests', 'quick', ['cmd', '/c', os.path.join(SUITE, 'moh-dbx-tools', 'tests', 'run_tests.bat')]),
        ('FbrbTool build', 'quick', ['cmd', '/c', os.path.join(SUITE, 'moh-fbrb-tool', 'build.bat')]),
        ('FbrbTool round trip, built bundle', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_fbrb.py')]),
        ('error handling and logs', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_errors.py')]),
        ('every command-line tool reports errors', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_cli_errors.py')]),
        ('review fixes: apply inputs, stock-MP reinstall, studio flags, cache key', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_review_fixes.py')]),
        ('review fixes 2: harness missing files, navkit no-original reinstall, old fbrb manifests', 'quick',
         [PY, os.path.join(SUITE, 'tests', 'test_review_fixes2.py')]),
        ('navigation side files (navtune)', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_navtune.py')]),
        ('NavKit water rule (ground too deep under water)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_water_rule.py')]),
        ('NavKit walkability edits (navedits)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_navedits.py')]),
        ('NavKit area link rule', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_area_link.py')]),
        ('NavKit regression progress estimates', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_regress_progress.py')]),
        ('NavKit boundary diagnostic (navboundary)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_navboundary.py')]),
        ('NavKit portal symmetry', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_portal_symmetry.py')]),
        ('NavKit reference extent clip', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_reference_clip.py')]),
        ('NavKit soldiers absent from path data', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_characters.py')]),
        ('NavKit headroom band', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_headroom.py')]),
        ('NavKit narrow lips and door sills', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_lips.py')]),
        ('NavKit dynamic props (floor under a prop, door leaves)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_dynamic_props.py')]),
        ('NavKit roof pruning', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_roof_prune.py')]),
        ('NavKit objective floors (M-COM)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_objective_floor.py')]),
        ('NavKit 45-degree bridges: closed outlines', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_bridge_union.py')]),
        ('NavKit D lines and F cells', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_detail_features.py')]),
        ('NavKit collision index (Havok packfile names)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_havok_index.py')]),
        ('NavKit boat mesh (navwater_mesh)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_navwater_mesh.py')]),
        ('NavKit routes across domains (navdomains)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_navdomains.py')]),
        ('NavKit vehicle path data (navvehicle)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_vehicle.py')]),
        ('NavKit shadow islands (navislands)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_navislands.py')]),
        ('NavKit offline sampler (navsample_offline)', 'quick', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_navsample_offline.py')]),
        ('LevelDiff build', 'quick', ['cmd', '/c', os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'build.bat')]),
        ('LevelDiff, built levels', 'quick', [PY, os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'tests', 'test_leveldiff.py')]),
        ('crash reader, built reports', 'quick', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'crash-reader', 'tests', 'test_crashreader.py')]),
        ('console readers, built images', 'quick', [PY, os.path.join(SUITE, 'moh-console-readers', 'tests', 'test_console_readers.py')]),
        ('menu tools (aptcheck, aptrefs, bc2loc), built data', 'quick', [PY, os.path.join(SUITE, 'moh-menu-tools', 'tests', 'test_menu_tools.py')]),
        ('exe string / address references (exeref), built exe', 'quick', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'crash-reader', 'tests', 'test_exeref.py')]),
        ('test harness, built data', 'quick', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'tests', 'test_harness.py')]),
        ('stock-MP AI layer, synthetic levels', 'quick', [PY, os.path.join(SUITE, 'moh-stockmp-ai', 'tests', 'test_stockmp.py')]),
        ('LevelDiff on MP_005', 'game', [PY, os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'tests', 'test_leveldiff.py'), '--game']),
        ('crash reader code view (mohmpgame.exe)', 'game', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'crash-reader', 'tests', 'test_crashreader.py'), '--game']),
        ('console readers on your Xbox files', 'game', [PY, os.path.join(SUITE, 'moh-console-readers', 'tests', 'test_console_readers.py'), '--game']),
        ('menu tools on the game copy and OPFOR\'s patches', 'game', [PY, os.path.join(SUITE, 'moh-menu-tools', 'tests', 'test_menu_tools.py'), '--game']),
        ('exeref on mohmpgame.exe', 'game', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'crash-reader', 'tests', 'test_exeref.py'), '--game']),
        ('FbrbTool round trip, 12 game bundles', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_fbrb.py'), '--game', '12']),
        ('water, every level', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_water.py')]),
        ('level loader snapshot', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_level.py')]),
        ('terrain against in-game samples', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_terrain.py')]),
        ('path data round trip (hpdtool)', 'game', lambda root: [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'hpdtool.py'),
                                                               'roundtrip', *pathdata_files(root)]),
        ('navigation mesh round trip (kymesh)', 'game', lambda root: [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'hpdtool.py'),
                                                                    'meshtrip', *pathdata_files(root)]),
        ('navigation data and the path test', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_navdata.py')]),
        ("path data structure, DICE's files (hpdcheck)", 'game', lambda root: [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'hpdcheck.py'),
                                                                             *dice_pathdata_files(root)]),
        ('NavKit boat mesh and routes on ONS_MP_004', 'game', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_navwater_mesh.py'), '--game']),
        ('NavKit vehicle path data on ONS_MP_002', 'game', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_vehicle_level.py')]),
        ('NavKit links and route edges', 'game', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_links.py')]),
        ('traversals, the apply report, installing', 'game', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_traversal.py')]),
        ('navkit apply with walkability edits', 'game', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tests', 'test_apply.py')]),
        ('Navigation Studio edit mode', 'game', [PY, os.path.join(SUITE, 'moh-navigation-studio', 'tests', 'test_editing.py')]),
        ('the suite window (hub) and the level picker', 'game', [PY, os.path.join(SUITE, 'tests', 'test_hub.py')]),
        ('3D tools open and draw (Map Inspector, Navigation Studio)', 'game', [PY, os.path.join(SUITE, 'tests', 'test_viewers.py')]),
        ('test harness on sp_009 and sp_004_b', 'game', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'tests', 'test_harness.py'), '--game']),
        ('stock-MP AI layer, Atacama Conquest built', 'game', [PY, os.path.join(SUITE, 'moh-stockmp-ai', 'tests', 'test_stockmp.py'), '--game']),
        ('NavKit regression, 2 small levels', 'game', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'navregress.py'),
                                                       '--levels', 'sp_004_b,sp_009']),
        ('NavKit regression, all levels with DICE path data', 'full', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'navregress.py')]),
        ('DbxEdit corpus and fingerprints', 'full', lambda root: ['cmd', '/c', os.path.join(SUITE, 'moh-dbx-tools', 'tests', 'run_tests.bat'),
                                                                 os.path.join(root, 'work', 'levels'), os.path.join(root, 'work', 'navkit', 'common')]),
        ('Python DBX reader against DbxEdit', 'full', lambda root: [PY, os.path.join(SUITE, 'core', 'tests', 'test_dbx.py'),
                                                                   os.path.join(root, 'work', 'levels'), os.path.join(root, 'work', 'navkit', 'common')]),
        ('FbrbTool round trip, every game bundle', 'full', [PY, os.path.join(SUITE, 'core', 'tests', 'test_fbrb.py'), '--game', '0']),
    ]
    return t


def main(argv):
    tier = next((a for a in argv[1:] if a in TIERS), 'quick')
    only = argv[argv.index('--only') + 1].lower() if '--only' in argv else None
    chosen = [x for x in tests() if TIERS.index(x[1]) <= TIERS.index(tier) and (only is None or only in x[0].lower())]
    if '--list' in argv:
        for name, t, _ in chosen:
            print(f'{t:6s} {name}')
        return 0
    root = opfor() if any(callable(a) or t != 'quick' for _, t, a in chosen) else None
    print(f'MOH Tool Suite tests: {tier}, {len(chosen)} test(s)' + (f', OPFOR at {root}' if root else ''))
    failures, start = [], time.time()
    for name, t, args in chosen:
        if callable(args):
            args = args(root)
        t0 = time.time()
        r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=SUITE)
        out = (r.stdout + r.stderr).strip()
        main_out = r.stdout.strip() or out            # the summary line is the test's own last line, not a warning it printed
        last = main_out.splitlines()[-1] if main_out else ''
        ok = r.returncode == 0
        print(f'{"ok  " if ok else "FAIL"} {name:42s} {time.time() - t0:6.1f} s   {last[:90]}', flush=True)
        if not ok:
            failures.append((name, out))
    for name, out in failures:
        print(f'\n--- {name} (last 25 lines) ---')
        print('\n'.join(out.splitlines()[-25:]))
    print(f'\n{len(chosen) - len(failures)} of {len(chosen)} passed in {time.time() - start:.0f} s')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
    from mohcore import errors
    errors.setup('run-tests', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
