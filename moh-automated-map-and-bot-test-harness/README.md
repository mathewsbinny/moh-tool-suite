# moh-automated-map-and-bot-test-harness

Repeatable tests of maps and bots, with results saved per run and compared between builds (`../DESIGN.md`
section 4.5, milestone M5). It reads Project OPFOR's game copy and work folder and never writes to the game
copy. It never starts the game: the in-game tests are designed and wait for play tests (**ON HOLD - needs
playtest**, below).

**Ready**

- `harness.py`: the offline harness (milestone M5), described below.
- `crash-reader/`: turns the game's `crashreport.xml` into function names, code, null pointers and hints.
- The suite's own tests: `python ../run_tests.py quick | game | full` (see the suite README). They check the
  tools themselves (format readers, bundle round trips, the level loader) before map and bot tests rely on
  them.
- In-game level runs are done by Project OPFOR's `testrun.bat` (start a level offline, watch its log,
  report: in game, crashed, timed out), which uses the crash reader. Campaign levels often stall on a black
  screen through its developer trigger; play-test those from the campaign menu, with OPFOR's probes
  logging (Project OPFOR `DEVELOPMENT_NOTES.md`, "NavKit play tests of sp_004_b").

## Use

```
python harness.py run nightly --levels sp_004_b,sp_009         test two levels, save the run as "nightly"
python harness.py run after_fix --levels sp_004_b --reference nightly
                                                                the same, then compared with "nightly"
python harness.py compare nightly after_fix                     what got worse or better (exit code 1 on a regression)
python harness.py show nightly                                  a saved run as text
python harness.py list                                          the saved runs
python harness.py import-log nightly --level ons_mp_002         the in-game checks from your last session in opfor.log
python harness.py ingame mp_005                                 what the in-game tests will do (on hold)
```

Options of `run`: `--levels` (default: every level with DICE path data, the 13 campaign levels and ONS_MP_002 /
004), `--files dice,navkit,installed` (default `dice,navkit`), `--navkit auto|regress|build|<path with {lv}>`
(which NavKit build: `regress` is `work\navkit\regress\<lv>.hpd`, navregress's latest; `build` is
`work\navkit\<lv>\pathdata.hpd`; `auto` takes the first that exists), `--tests` (a subset of
`structure,reachability,battery,orders,offsets,parity,level`), `--pairs 200` and `--level-pairs 100` (battery
sizes), `--seed 1`, `--reference <run>`, `--scale 1` (multiplies every tolerance), `--opfor <folder>`,
`--runs <folder>` (where runs are kept; default `<OPFOR>\work\harness\runs`). Exit code: 0 nothing failed and no
regression, 1 a test failed or something got worse, 2 a run named that does not exist.

Example (sp_004_b, NavKit's regression build of 2026-10-24; shortened):

```
SP_004_B   spawns 107, targets 69, orders 310
  infantry points tested: 97 spawns, 55 targets, 282 orders
  not infantry (counted, not tested): spawns 3 vehicle: air, 3 airborne, 2 vehicle: land, 1 water, 1 vehicle: stationary weapon; targets 8 airborne, 6 objective marker; orders 28 airborne
  dice      ...\game\Output\win32\ai\levels\sp_004_b\infantry\pathdata.hpd  (09214cc1)
    ok   structure        0 problem(s) (closed boundaries, edges, node order, zero-length edges, detail features)
    warn reachability     97 spawns -> 55 targets: 24.2 % of 2328 pairs reached, 42 % of targets from some spawn; ...
    info battery          random 200: 38.0 % routed; level 100: 8.0 % routed
    ok   offsets          sector ids imply -9,-10; the Infantry SectorOffsets asset says -9,-10
  navkit    ...\work\navkit\regress\sp_004_b.hpd  (c41f9140)
    FAIL reachability     ...; 76.5 % of the reference's pairs
           reached in the reference, from no spawn here: AreaOrder "Hold Near Player" at (20, 590, -799)
    FAIL battery          random 200: 46.0 % routed, 96.0 % of the reference's, length x1.06 (p90 x1.25); ...
    ok   parity           ground 98.5 % (+7.3 % extra), ...; from navparity run 'regression_last' (2026-10-26 06:35); not worse than navregress's reference
  level checks (AI readiness, from files)
    ok   has-ai           HasAI true
    ok   ai-templates     96 of 96 AI spawners with a real template (spawntemplate_enemy_demo 37, ...)
    ok   templates-loaded 9 of 9 templates in the load set (3 bundles), 18 of 18 partitions they refer to
  in game
    hold loads-ai-init    the level loads, reaches the match, and AI init runs - ON HOLD - needs playtest ...
```

## What a test run is

A **run** is one invocation of `harness.py run`: a name, the date, the PC, the suite's commit (and whether the
working tree had changes), the options, and per level:

- the level's **points**: soldier spawns (sources); AI targets - AreaOrders, M-COM stations
  (`objects/objects/mpobjective` objects), capture points and mission objectives; AI orders (AreaOrder, PatrolOrder
  and its waypoints, InterludeOrder, FireAtArea - NavKit's `ordercheck.order_points`); each with its class (below):
  the infantry ones are tested, the others counted (`excluded`);
- per **path-data file** (`dice`: DICE's original, the kept `.opfor-orig` where NavKit's file is installed;
  `navkit`: NavKit's build; `installed`: what the game loads, when it is not DICE's) its path, sha1 and the
  results of the file tests;
- the **level checks** (AI readiness from files) and the **in-game** checks (on hold until a session is imported).

Every result has a status (`pass`, `fail`, `warn`, `info` - a measurement without an expectation, `skip`,
`hold` - needs the game, `error` - the test could not run, traceback in the log), a one-line summary, flat numeric
metrics (what runs are compared on), detail lines and, for batteries, reachability and orders, the per-pair or
per-point bits that let two runs be compared point by point.

DICE's file is the **reference** (the baseline): NavKit's builds are measured against it on the same points, and
an absolute expectation that DICE's own file does not meet (orders with no route node near them on sp_009, many of
which stand far above any path data) is a warning on DICE's file, not a failure - DICE's data works in the game.

## Test kinds

| Kind | Per | What it checks | Fails when |
|---|---|---|---|
| `structure` | file | NavKit's `hpdcheck`: closed area boundaries, edges, node order, zero-length edges | any problem |
| `reachability` | file | spawns -> targets over the route graph along the edges' directions; each point joined through the area it stands on (the nearest walkable cell within 1.2 m across / 2.5 m in height: the nearest route node of that area's island), else the path test's nearest node (15 m across, 4 m in height); islands, the main island's share; per team | a target DICE's file reaches from some spawn is reached from none, or under 95 % of DICE's connected pairs are connected |
| `battery` | file | `--pairs` random ground points drawn from DICE's walkable cells with a fixed seed (the same pairs in every run, for the same DICE file) and `--level-pairs` spawn -> target pairs, each through the path test (`core/mohcore/navpath.py`; pairs whose route nodes are on different islands are refused without a search); routed share, why not (no node near, other island, one-way), detour; against DICE's routes on the same pairs: the share of DICE's routes found, extra routes, route length ratio (median, 90th percentile) | under 98 % of DICE's routes found |
| `orders` | file | a route node within 15 m of every AI order, and the order on an area (`ordercheck.PathData.on_area`) | an order DICE's file has a node near has none here (without DICE: any order without a node) |
| `offsets` | file | the sector offsets the file's own sector ids imply (OPFOR's `apply_sector_offsets.py` rule, which reproduces all 27 shipped PC assets) against the level's Infantry SectorOffsets asset | they differ, or the level ships no asset |
| `parity` | NavKit file | NavKit's DICE-parity measures (`navparity.run_level`), taken over: from a saved navparity / navregress run of the same file when the file is not newer than that run, else measured once and cached by both files' sha1; judged by `navregress.compare` against navregress's reference run `regression` | a measure worse than navregress's reference |
| `level` | level | AI readiness from the game copy's bundles (below) | per check |

### Point classes: infantry points only

The infantry tests (reachability, the spawn -> target battery, orders) run on infantry path data, so they take
**infantry points only**. Every other point gets a class and is counted in the run (`excluded`: counts per class for
spawns, targets and orders, and examples with the reason), not tested and never a failure
(`testharness/classify.py`):

| Class | Point | Decided by |
|---|---|---|
| spawner marker | a SoldierSpawnEntityData with AlternativeSpawnPoints | the soldiers appear at its alternate points (those are tested); its own transform is a map marker, 25-400 m off the ground on the Onslaught maps |
| vehicle: land / sea / air / stationary weapon | a soldier spawn with a Vehicle (it starts in the vehicle), and the alternate points of such a spawner | the blueprint of the vehicle spawn it refers to (`objects/vehicles/sea/...`) |
| other map | a spawn whose LocationNameSid names another map **and** that does not stand on the reference's walkable ground | ONS_MP_004 holds 55 AI spawners named ONS_MP_005GR_*, 60 m above its islands; a spawn named for another map that stands on this map's ground is tested (names were copied between ports) |
| objective marker | MissionObjectiveEntityData | the HUD marker, placed above its objective |
| base | a capture point on a level with M-COM stations (Rush, Onslaught) | a deployment base, not a target; on a Conquest level capture points are the targets |
| water | a point over water deeper than 1 m, within 5 m of its surface, with no walkable cell of the reference at it | the level's water mesh and terrain (core `water.py`, `terrain.py`): boat patrols such as "Sea_1st_Patrol" |
| airborne | a point more than 10 m above the terrain, the water and every walkable cell of the reference beneath it | spawns and orders in the air (helicopter and drop positions) |

The ground classes use DICE's file (the reference), so every file of a level is tested on the same points.

Onslaught maps, DICE's console path data (run `mp_reach2`, 2026-10-26; before the classes, `mp_reach`: 57 % / 71 % of
targets reached on ONS_MP_002 / 004):

| Map | Infantry points tested (spawns, targets, orders) | Not infantry | DICE: pairs reached, targets reached | NavKit's build |
|---|---|---|---|---|
| ONS_MP_002 | 249, 20, 10 | spawns: 12 spawner marker, 1 water; targets: 10 base, 5 objective marker | 100 %, 100 % | 99.6 % of DICE's pairs (warn); battery 88.8 % of DICE's random routes (fail) |
| ONS_MP_004 | 35, 12, 6 | spawns: 55 other map, 27 air vehicle, 5 spawner marker, 4 sea vehicle, 3 airborne, 2 land vehicle; targets: 5 base; orders: 4 water, 3 airborne | 100 %, 100 % | 100 % of DICE's pairs; battery 97.3 % random, 67 % of the spawn -> target routes (fail) |
| ONS_MP_005GR | 105, 9, 10 | spawns: 72 airborne, 15 spawner marker, 5 air vehicle, 3 land vehicle, 3 other map; targets: 8 base, 4 objective marker; orders: 3 airborne | 100 %, 89 % (8 harbour spawn points 17-28 m from a node, one AreaOrder 171 m) | no build |
| ONS_MP_008 | 51, 29, 41 | spawns: 6 land vehicle, 3 stationary weapon, 3 spawner marker; targets: 3 airborne; orders: 5 airborne | 72 %, 52 % (14 AreaOrders 24-78 m from DICE's console nodes) | no build |

The NavKit column is NavKit's build before route-island pruning. With it (`navislands.SHADOW_ISLANDS`, 2026-10-26;
NavKit README "Route islands") ONS_MP_004's spawn -> target battery routes 100 % of DICE's pairs (was 67 %);
ONS_MP_002's stays at 98.0 %.

Level checks (`testharness/levelchecks.py`; Project OPFOR `STOCK_MP_AI_PLAN.md` 6.3 and `MP_APPLICATION_LOG.md`):
`has-ai` (LevelData.HasAI), `ai-spawners` (PlayerType SSAiPlayer), `ai-templates` (a real AI template - not the
DefaultSoldier placeholder, whose AI aim and never fire; not null), `templates-loaded` (each template partition,
and the partitions it refers to - Sensing, Behaviour - in a bundle of the level's load set, its `package.mft`
block), `kits-loaded`, `orders` (AreaOrders exist), `offsets-asset` (Precomputed/Infantry SectorOffsets in the
level's bundles), `path-data` (infantry path data installed). These read the **game copy's bundles** directly
(`core/mohcore/fbrb.py`, no unpacking): OPFOR's import steps change those bundles after a level was prepared
(ONS_MP_002's templates and SectorOffsets assets, 2026-10-21), so the prepared copy in `work\levels` can be out of
date; when it differs from the game copy, the run says so. The points come from the prepared copy (core
`level.load` without geometry), which `navkit.py prepare <level>` refreshes.

On a stock MP map the level checks show what it still lacks - MP_005 (Atacama Desert, Conquest): HasAI false, 7 AI spawners all
with null templates, no AreaOrders, no SectorOffsets asset, no path data. ONS_MP_002 after OPFOR's import passes
them all (148 AI spawners on the grunt template, its SectorOffsets -3,-7).

## Saved runs and comparison

Runs are JSON, `<OPFOR>\work\harness\runs\<name>.json` (format `moh-harness-run`, version 1; the layout is in
`testharness/runs.py`). Caches (navigation data, bundle listings and entries, parity rows) are in
`<OPFOR>\work\harness\cache\`; delete it to start afresh.

`compare <base> <new>` matches levels, file keys (`dice`, `navkit` ...), tests and metrics. Each metric has a
direction and a tolerance (`runs.spec`): shares where higher is better (0.5 points for reachability and orders,
1 point for batteries), shares where lower is better (extra routes, refusals, over-connection), length ratios where
closer to 1.0 is better (0.02 at the median, 0.05 at the 90th percentile), structure problems (0), the offsets
match, and NavKit's parity measures with navregress's own lists and tolerance (0.05 points). Worse beyond the
tolerance is a **regression**, as is a test going from pass / info / warn to fail or error. Counts that change
(islands, AI spawners), better measures, fixed tests, battery pairs routed before and not now, and levels or tests
missing from either run are listed but are not regressions. A file whose sha1 did not change gives the same
numbers (the game-tier test checks that).

## Tiers

| Tier | What | Time |
|---|---|---|
| quick | `tests\test_harness.py`: the point classes, every test kind, the level checks, the bundle reader, opfor.log sessions, saved runs, the comparison and the command line, on data built in the test | seconds |
| game | `tests\test_harness.py --game`: a run on sp_009 and sp_004_b, the same run again without a regression, a damaged copy of sp_009's path data flagged; ONS_MP_004's vehicle, other-map, base and water points counted and every infantry target reached on DICE's file; ONS_MP_005GR's level partition read from its Package bundle; the bundle reader against FbrbTool | about two minutes with the caches filled, a few more without |
| by hand | `harness.py run <name>` over every level with DICE path data, before and after a NavKit change, then `compare` | over an hour the first time (parity of each NavKit build), then minutes |

Both test tiers are part of the suite's runner (`python ..\run_tests.py quick | game`).

## In-game tests: ON HOLD - needs playtest

The harness never starts the game. The in-game parts are designed and plug in as follows (`testharness/ingame.py`):

- **Now: importing a session.** `import-log <run> --level <lv> [--log <opfor.log>] [--session N]` reads an
  `opfor.log` (default the game copy's, read only), splits it into sessions (one per `opfor.dll loaded` line), takes
  the latest one that started the level and adds three checks to the run: `loads-ai-init` (in game, AI init seen,
  no crash), `offsets-read` (the SectorOffsets loads read the asset's values, no `[dev] sector_offsets` override),
  `pathfinders` (the unit-counter dumps show pathfinders after spawning; needs `[dev] unit_counters_dump=1`). These
  are the validation criteria of OPFOR's Onslaught ports.
- **ON HOLD: `testrun.bat` per level.** The harness will run OPFOR's `testrun.bat <level> --yes` for each level of
  a run (with `unit_counters_dump=1`) and import its session as above. It stays out until a play test confirms the
  sequence on a stock MP map (campaign levels stall on a black screen through the dev trigger).
- **ON HOLD: telemetry (milestone M6).** Bot traversal (bots reach given points within a time), stuck detection
  (no progress for N seconds), squad coordination and vehicle use, from a recording of OPFOR's telemetry stream
  (`DESIGN.md` 5.4) - listed in every run as `hold` until the stream exists.
- **ON HOLD: the player's judgement.** Do bots spawn on both teams, advance on and defend the objectives, and fire?
  In Rush, is a later stage reached? Only a play test answers these.

## Files

| File | What |
|---|---|
| `harness.py` | the command line |
| `testharness/checks.py` | the offline test kinds |
| `testharness/classify.py` | point classes: which spawns, targets and orders are infantry points |
| `testharness/levelchecks.py` | AI readiness from files; the in-game checks' names |
| `testharness/levelinfo.py` | what is read about a level: points, the AI layer, the load set, assets, path-data files |
| `testharness/ingame.py` | opfor.log sessions and the in-game criteria; the plan for testrun and telemetry |
| `testharness/runs.py` | the saved-run format, the comparison and the text report |
| `tests/test_harness.py` | the harness's own tests |

Errors are logged like the suite's other tools (`%LOCALAPPDATA%\moh-tool-suite\logs\harness-<date>.log`): on an
error it prints the cause, what to do and the log's path (exit code 1).
