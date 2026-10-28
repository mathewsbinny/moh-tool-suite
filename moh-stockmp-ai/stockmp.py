r"""Stock multiplayer maps with an AI layer: generate a per-map configuration, author it into the level data, package it
and check it offline (STOCK_MP_AI_PLAN.md, option c). Nothing is written outside <OPFOR>\work\stockmp\<lv>\ except
by `install --apply`, which is for the user to run (never run by the build).

  python stockmp.py summary  <lv>                       the level as the tool reads it: mode, teams, flags, stages
  python stockmp.py generate <lv> [--bots N] [--pathdata <level folder>] [--minimal] [--keep-stock-spawners]
                                                        write work\stockmp\<lv>\config.json (a first proposal)
  python stockmp.py build    <lv> [--config <file>]     author, pack and stage: work\stockmp\<lv>\out\game\...
  python stockmp.py validate <lv>                       the offline checks (validate.py); exit 1 if any fails
  python stockmp.py all      <lv> [generate options]    generate (unless config.json exists), build, validate
  python stockmp.py install  <lv> [--apply | --revert] [--game <game folder>]
                                                        copy out\game into the game with backups (default: --plan)
  common options: --variant <name> (a second build, e.g. exp0: work\stockmp\<lv>-<name>), --work <folder>

--pathdata names a folder under the game's Output\win32\ai\levels (default: the level's own, else the DICE folder of
the same world known for it: mp_005 -> ons_mp_005gr). The output keeps the game's own paths, so an install is a plain
copy of out\game over the game folder.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'core'))

import config as cfgmod  # noqa: E402
import dbxbin  # noqa: E402
import levelmodel  # noqa: E402
import pipeline  # noqa: E402

# worlds whose path data the user's install has under another level's name (DICE's console Onslaught files, which
# Napisal's ports install): stock level -> path-data folder
SAME_WORLD = {'mp_005': 'ons_mp_005gr', 'mp_005gr': 'ons_mp_005gr', 'mp_005sr': 'ons_mp_005gr',
              'mp_002': 'ons_mp_002', 'mp_002sr': 'ons_mp_002', 'mp_004': 'ons_mp_004', 'mp_004sdm': 'ons_mp_004',
              'mp_008': 'ons_mp_008', 'mp_008cq': 'ons_mp_008', 'mp_008sdm': 'ons_mp_008'}
BACKUP = '.opfor-stockmp-orig'
APPLIED = '.opfor-stockmp-applied'


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def pathdata_for(paths, folder=None):
    base = os.path.join(paths.game, 'Output', 'win32', 'ai', 'levels')
    for f in [folder] if folder else [paths.lv, SAME_WORLD.get(paths.lv)]:
        if f and os.path.isfile(os.path.join(base, f, 'infantry', 'pathdata.hpd')):
            src = 'the level\'s own path data' if f == paths.lv else f'path data of {f} (the same world)'
            return {'infantry': os.path.join(base, f, 'infantry', 'pathdata.hpd'),
                    'vehicle': os.path.join(base, f, 'vehicle', 'pathdata.hpd'), 'source': src}
    raise SystemExit(f'{paths.lv}: no path data found (looked in {base} for {folder or paths.lv} / '
                     f'{SAME_WORLD.get(paths.lv)}); build one with NavKit and pass --pathdata')


def summary(paths):
    v, b = paths.bundles()[-1]
    d = pipeline.unpacked(b, paths.src(v))
    m = levelmodel.Model(dbxbin.read(pipeline.find_entry(d, pipeline.level_entry(d, pipeline._level_name(d, paths.lv)))))
    print(f'{m.name}: mode {m.mode}, factions {m.factions}, attacker {m.attacker}, HasAI {m.has_ai}')
    for cp in m.capture_points:
        print(f'  capture point {cp.label or cp.name:14s} owner {cp.owner:11s} at {tuple(round(x) for x in cp.pos)}, '
              f'{len(cp.spawners)} spawners')
    kinds = {}
    for s in m.spawners:
        kinds[(s.team, s.player_type)] = kinds.get((s.team, s.player_type), 0) + 1
    print(f'  soldier spawners: {kinds}')
    for st in m.rush_stages():
        print(f'  stage {st.index}: {st.trigger_name}, M-COMs {[tuple(round(x) for x in p) for _g, p in st.mcoms]}')


def installed_files(game, lv):
    """Rel paths of the files --apply installed for <lv>, from the *.opfor-stockmp-applied markers under the game
    folder - the installation record, independent of what is still in the build output."""
    rels = []
    for dp, _d, fs in os.walk(game):
        for f in fs:
            if f.endswith(APPLIED):
                rel = os.path.relpath(os.path.join(dp, f), game)[:-len(APPLIED)]
                if lv.lower() in {c.lower() for c in rel.replace(os.sep, '/').split('/')}:
                    rels.append(rel)
    return rels


def install(paths, argv):
    game = opt(argv, '--game', paths.game)
    out = os.path.join(paths.root, 'out', 'game')
    mode = '--apply' if '--apply' in argv else '--revert' if '--revert' in argv else '--plan'
    if mode == '--revert':
        files = set(os.path.relpath(os.path.join(dp, f), out) for dp, _d, fs in os.walk(out) for f in fs)
        files |= set(installed_files(game, paths.lv))
        if not files:
            print(f'nothing installed by stock-MP in {game} (no {APPLIED} markers, nothing built)')
            return
    else:
        files = [os.path.relpath(os.path.join(dp, f), out) for dp, _d, fs in os.walk(out) for f in fs]
        if not files:
            raise SystemExit(f'nothing built in {out} (run build first)')
    for rel in sorted(files):
        dst = os.path.join(game, rel)
        src = os.path.join(out, rel)
        if mode == '--plan':
            state = 'replace' if os.path.isfile(dst) else 'new'
            print(f'{state:8s} {rel}')
        elif mode == '--apply':
            marker = dst + APPLIED
            installed = False
            if os.path.isfile(marker):
                if not os.path.isfile(dst):
                    installed = False          # deleted after the install: install the new build again
                else:
                    installed = open(marker).read().strip() == pipeline.sha(dst)
                    if not installed:
                        raise SystemExit(f'{rel} changed after the install - not overwriting it '
                                         f'(revert it first, or delete {marker})')
            if installed and pipeline.sha(dst) == pipeline.sha(src):
                print(f'already   {rel}')
                continue
            if os.path.isfile(dst) and not os.path.isfile(dst + BACKUP) and not installed:
                shutil.copyfile(dst, dst + BACKUP)      # the original only - never an earlier build of ours
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            open(dst + APPLIED, 'w').write(pipeline.sha(dst))
            print(f'installed {rel}')
        else:
            if os.path.isfile(dst) and os.path.isfile(dst + APPLIED) and \
                    open(dst + APPLIED).read().strip() != pipeline.sha(dst):
                raise SystemExit(f'{rel} changed after the install - not reverting it')
            if os.path.isfile(dst + BACKUP):
                os.replace(dst + BACKUP, dst)
                print(f'restored  {rel}')
            elif os.path.isfile(dst + APPLIED):
                if os.path.isfile(dst):
                    os.remove(dst)
                print(f'removed   {rel}')
            if os.path.isfile(dst + APPLIED):
                os.remove(dst + APPLIED)
    if mode == '--plan':
        var = f' --variant {paths.variant}' if paths.variant else ''
        print(f'(plan only: `install {paths.lv}{var} --apply` copies these into {game}, keeping *{BACKUP} backups)')


def main(argv):
    if len(argv) < 3 or argv[1] not in ('summary', 'generate', 'build', 'validate', 'all', 'install'):
        print(__doc__)
        return 1
    cmd, lv = argv[1], argv[2].lower()
    paths = pipeline.Paths(lv, opfor=opt(argv, '--opfor'), work=opt(argv, '--work'), variant=opt(argv, '--variant'))
    if cmd == 'summary':
        summary(paths)
        return 0
    if cmd == 'install':
        install(paths, argv)
        return 0
    if cmd in ('generate', 'all') and (cmd == 'generate' or not os.path.isfile(paths.config())):
        c = pipeline.generate(paths, pathdata=pathdata_for(paths, opt(argv, '--pathdata')),
                              bots_per_team=int(opt(argv, '--bots', 8)),
                              layer='minimal' if '--minimal' in argv else 'full',
                              stock_spawners='keep' if '--keep-stock-spawners' in argv or '--minimal' in argv else 'human')
        print(f'{paths.config()}: {len(c["spawners"])} AI spawners, {len(c["orders"])} orders, {len(c["stages"])} stage '
              f'switches; {len(c["notes"])} note(s)')
        for n in c['notes']:
            print('  note: ' + n)
        if cmd == 'generate':
            return 0
    if cmd in ('build', 'all'):
        c = cfgmod.load(opt(argv, '--config', paths.config()))
        rep = pipeline.build(paths, c)
        print(json.dumps({v: r['authored'] for v, r in rep['variants'].items()}))
    c = cfgmod.load(opt(argv, '--config', paths.config()))
    import validate
    res = validate.validate(paths, c)
    print('all checks passed' if res['passed'] else 'SOME CHECKS FAILED')
    return 0 if res['passed'] else 1


if __name__ == '__main__':
    from mohcore import errors
    errors.setup('stockmp', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
