"""A level's AI-readiness checks that files can decide: Project OPFOR's validation criteria for AI levels
(STOCK_MP_AI_PLAN.md section 6.3, MP_APPLICATION_LOG.md: what made the Onslaught ports' bots move and fire), on any
level - campaign levels pass them, stock MP maps show what they still lack.

From files (each a checks.Result):
  has-ai             LevelData.HasAI (false: the server skips AI init; OPFOR's force_ai sets it at load)
  ai-spawners        soldier spawners with PlayerType SSAiPlayer, enabled at level start or later
  ai-templates       every AI spawner has a real AI template - not DefaultSoldier (the "FAIL No Template"
                     placeholder: the AI aim and never fire), not null
  templates-loaded   each template partition, and the partitions it refers to (Sensing, Behaviour), is in a bundle
                     the level loads (its package.mft block)
  kits-loaded        the AI spawners' soldier kits likewise
  orders             AreaOrders exist (the AI's destinations)
  offsets-asset      the level ships a Precomputed/Infantry SectorOffsets asset (without it the runtime uses 0,0)
  path-data          the game copy has the level's infantry path data installed
Per path-data file, in the file tests: offsets (the file's sector ids against the asset) and orders (a route node
near every order).

In game (ON HOLD - needs playtest; filled in from an opfor.log of a run the user made, see ingame.py):
  loads-ai-init      the level loads, reaches the match, and AI init runs
  offsets-read       both SectorOffsets loads read the asset's values, no override
  pathfinders        pathfinders appear after spawning (unit counters)
"""
from collections import Counter

from .checks import Result

INGAME = [('loads-ai-init', 'the level loads, reaches the match, and AI init runs'),
          ('offsets-read', 'both SectorOffsets loads read the asset\'s values (no [dev] override)'),
          ('pathfinders', 'pathfinders appear after spawning (OPFOR unit counters)')]
HOLD = 'ON HOLD - needs playtest: run testrun.bat <level> yourself, then harness.py import-log'


def has_ai(info):
    if info.has_ai is None:
        return Result('error', 'no LevelData.HasAI read')
    return Result('pass' if info.has_ai else 'fail',
                  'HasAI true' if info.has_ai else 'HasAI false: the server level load skips AI init (OPFOR\'s force_ai '
                                                   'sets it at load; not yet tried on a stock map)', {'has_ai': int(info.has_ai)})


def ai_spawners(info):
    ai = [s for s in info.spawners if s.ai]
    on = [s for s in ai if s.enabled]
    teams = Counter(s.team for s in ai)
    m = {'ai_spawners': len(ai), 'ai_spawners_enabled': len(on), 'ai_team1': teams.get(1, 0), 'ai_team2': teams.get(2, 0)}
    return Result('pass' if ai else 'fail',
                  f'{len(ai)} AI spawners ({len(on)} enabled at level start; team 1: {m["ai_team1"]}, team 2: {m["ai_team2"]})'
                  if ai else 'no AI spawners (PlayerType SSAiPlayer)', m)


def ai_templates(info, placeholder):
    ai = [s for s in info.spawners if s.ai]
    if not ai:
        return Result('skip', 'no AI spawners')
    ph = [s for s in ai if s.template == placeholder]
    null = [s for s in ai if not s.template]
    real = [s for s in ai if s.template and s.template != placeholder]
    m = {'real_template': len(real), 'placeholder_template': len(ph), 'null_template': len(null),
         'real_template_pct': round(100.0 * len(real) / len(ai), 2)}
    used = Counter(s.template.rsplit('/', 1)[-1] for s in real)
    details = [f'placeholder (DefaultSoldier): "{s.name}" {s.guid}' for s in ph] + [f'null template: "{s.name}" {s.guid}' for s in null]
    status = 'fail' if not real else 'warn' if ph or null else 'pass'
    return Result(status, f'{len(real)} of {len(ai)} AI spawners with a real template'
                          + (f' ({", ".join(f"{k} {v}" for k, v in used.most_common(4))})' if used else '')
                          + (f'; {len(ph)} on the DefaultSoldier placeholder' if ph else '') + (f'; {len(null)} null' if null else ''),
                  m, details)


def templates_loaded(info, placeholder):
    tpls = sorted({s.template for s in info.spawners if s.ai and s.template and s.template != placeholder})
    if not tpls:
        return Result('skip', 'no real AI templates used')
    missing, chain_missing, checked = [], [], 0
    for t in tpls:
        if info.in_load_set(t) is None:
            missing.append(t)
            continue
        for r in info.template_refs.get(t) or ():
            checked += 1
            if info.in_load_set(r) is None:
                chain_missing.append(f'{t} -> {r}')
    m = {'templates': len(tpls), 'templates_missing': len(missing), 'chain_partitions': checked,
         'chain_missing': len(chain_missing)}
    details = [f'not in the load set: {t}' for t in missing] + [f'referenced partition not in the load set: {c}' for c in chain_missing]
    return Result('fail' if missing or chain_missing else 'pass',
                  f'{len(tpls) - len(missing)} of {len(tpls)} templates in the load set ({len(info.load_set)} bundles), '
                  f'{checked - len(chain_missing)} of {checked} partitions they refer to', m, details)


def kits_loaded(info):
    kits = sorted({s.kit for s in info.spawners if s.ai and s.kit})
    if not kits:
        return Result('skip', 'no kits on AI spawners')
    missing = [k for k in kits if info.in_load_set(k) is None]
    return Result('warn' if missing else 'pass', f'{len(kits) - len(missing)} of {len(kits)} AI soldier kits in the load set',
                  {'kits': len(kits), 'kits_missing': len(missing)}, [f'not in the load set: {k}' for k in missing])


def orders(info):
    n = info.area_orders
    return Result('pass' if n else 'fail', f'{n} AreaOrders' if n else 'no AreaOrders: the AI has no destinations',
                  {'area_orders': n})


def offsets_asset(info):
    a = info.assets.get('Infantry')
    v = info.assets.get('Vehicle')
    if a is None:
        return Result('fail', 'no Precomputed/Infantry/SectorOffsets asset in the level\'s bundles (the runtime uses 0,0)',
                      {'asset': 0})
    txt = 'present, not read' if a == 'unread' else f'{a[0]},{a[1]}'
    vt = '' if v is None else f'; Vehicle {"present" if v == "unread" else f"{v[0]},{v[1]}"}'
    m = {'asset': 1, **({'x': a[0], 'z': a[1]} if a != 'unread' else {})}
    return Result('pass' if a != 'unread' else 'warn', f'Infantry SectorOffsets {txt}{vt}', m)


def path_data(info):
    return Result('pass' if info.installed else 'fail',
                  'infantry path data installed' if info.installed else 'no infantry path data in the game copy '
                  '(Output\\win32\\ai\\levels\\<level>\\infantry\\pathdata.hpd)', {'installed': int(bool(info.installed))})


def all_checks(info, placeholder):
    """{name: Result} of the file checks, in order, then the in-game ones (on hold)."""
    out = {'has-ai': has_ai(info), 'ai-spawners': ai_spawners(info), 'ai-templates': ai_templates(info, placeholder),
           'templates-loaded': templates_loaded(info, placeholder), 'kits-loaded': kits_loaded(info),
           'orders': orders(info), 'offsets-asset': offsets_asset(info), 'path-data': path_data(info)}
    for name, what in INGAME:
        out[name] = Result('hold', f'{what} - {HOLD}')
    return out
