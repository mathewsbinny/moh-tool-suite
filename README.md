# MOH Tool Suite

Tools for building and debugging AI on Medal of Honor (2010) maps, in one repository. The 3D tools
share one core: level loading, a 3D view, picking and the game's data formats.

## Getting started

1. Install Python 3.10 or newer, then the libraries the tools use:
   `pip install numpy matplotlib moderngl glfw imgui-bundle pillow` (and `pefile capstone` for the crash
   reader's code view).
2. Put this folder next to your `project-opfor-moh` folder (or set `OPFOR_ROOT` to it): the tools read the
   game copy in its `game\` folder (`game\MP\MoHMPGame.exe` and its data; `core\mohcore\gamecopy.py` has the layout).
3. Double-click **`MOH Tool Suite.pyw`**. One window has every tool:
   - **Levels:** choose a level, then **Open in Map Inspector** or **Open in Navigation Studio** (or
     double-click the level). A level is unpacked the first time (offline, about 20 s); the tool's progress
     shows at the bottom and its 3D window opens when the level is loaded.
   - **Bundles, Level diff, Crash reader, Xbox files, Tests:** the command-line tools with file pickers and
     options; their output appears at the bottom.
   - **More tools:** DbxEditGUI and NavKit's window.

   The test harness, the menu tools and the stock-MP AI tool have no tab yet: run them from a command prompt
   (their READMEs).

The 3D tools can also be started on their own: double-click `moh-map-inspector\inspector.py` or
`moh-navigation-studio\studio.py` and a level picker opens, or give the level on the command line
(`python studio.py mp_01`). On a laptop with two GPUs, see "The 3D tools and your GPU" below.

### Medal of Honor's data (2026-10-29)

The suite came from the BFBC2 suite and is being moved to Medal of Honor (2010). What holds now:

- **Levels:** the 26 multiplayer levels of `package.mft`, base and DLC (`mp_01` ... `mp_10_tdm`, the Elimination
  and King of the Hill maps). A level loads the patch's copy of a bundle (`MP\dist\win32\patch\`) over the base
  one, and its map's `common_mp_<nn>` before `common_mp`; two DLC levels (`mp_03_elimination`, `mp_07_koth`) have
  no bundle of their own and come from `common_mp_03` / `common_mp_07`. NavKit's prepare step, the level loader and
  the 3D tools read them that way (checked on mp_01 and mp_03_elimination).
- **No AI in MoHMPGame.exe:** the multiplayer client has no Kynapse runtime and no AI behaviour (no AI orders,
  `HasAI`, `PathfindingTypes` or `SectorOffsets`) - only the leftover AI-player spawn path (`SoldierSpawnEntityData`
  with `PlayerType` `SSAiPlayer`), which OPFOR's bots will use as BF1943's do (project-opfor-moh\BOT_PLAN.md);
  Medal of Honor's AI is in the Unreal Engine 3 singleplayer game. No level ships `pathdata.hpd`. So the parts built on the BFBC2 game's own AI - DICE parity
  scores, the stock-MP AI layer (`moh-stockmp-ai`), the harness's AI-readiness checks, NavKit's in-game sampler -
  have nothing to run against yet: OPFOR's bots need their own runtime, which will read NavKit's navigation data.
  Their tests still use BFBC2 fixtures (`sp_*`, `ons_mp_*`, build 795745).
- **NavKit on MOH (2026-10-30):** builds all 26 MP levels offline from their collision and terrain, for MOH's own
  soldier (`moh-auto-navkit\tools\mohsoldier.py`: radius 0.35, height 1.8, step 0.3, slope 45 deg), inside each
  level's combat areas; its README's "Medal of Honor MP" section lists what was checked on the maps.
- **Build:** the MP client is build 592256. The crash reader and exeref name its functions from
  `symbols_592256.txt` (a start: the window-position registry key, the Documents folder); aptrefs reads its extern
  table but its getter tables are still BFBC2's (build 795745) and are skipped.

### When something goes wrong

Every tool keeps a log in `%LOCALAPPDATA%\moh-tool-suite\logs\` (one file per tool and day, the 20 newest
kept): what it did, warnings, and every error with its full details. When a tool crashes, a window says what
happened, the likely cause and what to do - for example "a Python library the tool needs is not installed
(imgui_bundle) - pip install imgui-bundle", "the 3D view could not start OpenGL 3.3 - update the graphics driver",
"the level is not prepared", "a file could not be written - close the game" - and where the log is. In the 3D
tools an error in one panel does not close the window: it is listed in a Problems window and logged once. The
suite window checks the libraries when it opens, and when a tool it runs fails it shows that tool's last lines.
The command-line tools - FbrbTool, DbxEdit, LevelDiff, NavKit's `navkit.py` and `tools\*.py`, the crash reader,
the console readers, the menu tools, the test harness, the test runner - log the same way and, on an error, print the cause, what to do and the
log's path (exit code 1); DbxEditGUI and NavKit's window show it in a window. If you report a problem, send the
log file.

**Ready to use**

| Folder | Tool |
|---|---|
| `moh-auto-navkit/` | **MOH NavKit**: generates AI navigation data (`pathdata.hpd`) for levels. Start with `QUICKSTART.md`, or `navkitGUI.pyw`. |
| `moh-dbx-tools/` | **DbxEdit** and **DbxEditGUI**: view, edit, check and repair binary `.dbx` files; XML export and import |
| `moh-fbrb-tool/` | **FbrbTool**: unpack and repack `.fbrb` bundles |
| `moh-console-readers/` | read-only readers for Xbox 360 disc images and content packages |
| `moh-diff-workbench/level-diff/` | **LevelDiff**: what differs between two `.dbx` files, instance by instance |
| `moh-automated-map-and-bot-test-harness/` | **test harness** (offline, M5): per level and path-data file (DICE's, NavKit's) structure, spawn-to-objective reachability, path-test batteries, orders near the route network, sector offsets, NavKit's DICE parity, and AI readiness from the game copy's bundles; saved runs compared between builds (`harness.py`); in-game tests on hold |
| `moh-automated-map-and-bot-test-harness/crash-reader/` | **crash reader**: the game's `crashreport.xml` turned into names, code, null pointers and hints; `exeref.py`: where `mohmpgame.exe` uses a string or an address, with a short code window |
| `moh-menu-tools/` | **menu tools**: `aptcheck.py` checks menu patches against the rules that crash the menu loader (OPFOR's `src\menus\*.patch` too); `aptrefs.py` cross-references the externs, FSCommands and engine events of every menu and HUD movie with the exe's name / id table; `mohloc.py` looks up and searches the localized texts |
| `core/` | the shared core: Python readers for `.dbx`, `.fbrb` bundles (read only), terrain height fields and water; the level loader (entities, teams, collision, areas and routes); the 3D view with picking |
| `moh-map-inspector/` | **Map Inspector**: browse a level in 3D and inspect every object's source, properties and links; measure |
| `moh-navigation-studio/` | **Navigation Studio**: NavKit's and DICE's navigation data in 3D, by island, sector, pitch or difference; islands and spawns off the main network; the path test; edit mode (walkable / unwalkable regions, seeds, links, route edges, traversals) into a side file, built with `navkit apply` and installed; a reference level's AI layer drawn on another level (`--compare`) and an anomaly overlay of NavKit builds, both with JSON export (`overlay.py`) |
| `moh-stockmp-ai/` | **stock-MP AI layer**: gives a stock multiplayer level AI spawners, AI orders and their event wiring (HasAI, PathfindingTypes, SectorOffsets, AI template), proposed from the level's own objectives and spawns, built into its bundles and checked offline (`stockmp.py`) |

**Planned** (see [DESIGN.md](DESIGN.md))

| Folder | Tool | Milestone |
|---|---|---|
| `moh-navigation-studio/` | M4 step 7: an in-game check that the game's AI uses a hand-made link (steps 1-6 - path test, editing, side files, `navkit apply`, install - are done) | M4 |
| `moh-automated-map-and-bot-test-harness/` | in-game tests: `testrun.bat` per level (on hold - needs playtest), bot traversal, stuck detection, squads and vehicles from OPFOR's telemetry | M6, then growing |
| `moh-bot-ai-debugger/` | live view of the AI while the game runs (through Project OPFOR) | M6 |
| `moh-tactical-map-authoring-tool/` | tactical annotations above the navigation data for bots | M7 |
| `moh-diff-workbench/` | ImHex patterns for MOH formats, format coverage and structure-aware diffs (LevelDiff, in `level-diff/`, is ready) | alongside |
| `moh-ai-behavior-simulator/` | runs OPFOR's bot decision code on simplified maps, without the game | with Goal 3 |

The asset builder & validator, `moh-asset-builder`, is part of the Project OPFOR repository (DESIGN.md
section 4.9).

## The 3D tools and your GPU

The Map Inspector and the Navigation Studio draw with OpenGL 3.3. Each prints the GPU it uses when it starts
(and shows it in its status line). On a laptop with two GPUs, Windows may give Python the slower integrated
one: set `python.exe` to "High performance" in Settings > System > Display > Graphics. On the development
PC that took the Navigation Studio on a 10.4-million-triangle level from the AMD Radeon integrated GPU to
the GeForce RTX 4060, at 144 frames a second.

## Tests

```
python run_tests.py quick      before every commit: no game data, about a minute
python run_tests.py game       before every push: uses Project OPFOR's game copy, about 10 minutes
python run_tests.py full       after changing a format reader: every unpacked file, about 30 minutes
python run_tests.py --list     what each tier runs;   --only <name> runs the tests whose name contains it
```

| Test | Tier | Checks |
|---|---|---|
| `tests/test_repo.py` | quick | no game files or large files in the history, no co-author lines, no build output tracked, READMEs, LICENSE, every Python file compiles |
| `tests/test_review_fixes.py` | quick | four defects from a code review (2026-10-26): NavKit's apply builds with the normal build's inputs (detailed zones, ladders); the stock-MP installer reinstalls a rebuilt file and keeps the original as its backup; the Navigation Studio's `--edit` is a flag; the navigation cache key follows the terrain's content |
| DbxEdit unit tests | quick | 93 checks of the format, XML export and import, edits, level check, cull-slot fixer, command line, window |
| `core/tests/test_fbrb.py` | quick, game, full | unpack + pack gives the same bundle (built here; 12 game bundles; all 1,055), `*deleted*` markers handled |
| `moh-diff-workbench/level-diff/tests/test_leveldiff.py` | quick, game | two levels built in the test with known differences (changed field, re-created object, list entries added / removed / reordered, instances added / removed); `--match-objects`, `--ignore`, `--type`, `--summary`, JSON, exit codes; MP_005 against itself and Dist against Package |
| `moh-automated-map-and-bot-test-harness/tests/test_harness.py` | quick, game | the test harness on data built in the test: point classes (spawner markers, vehicle spawns by kind, other maps' spawns, objective markers, bases, water, airborne - counted, not tested), reachability (pair bits, islands, one-way edges, against a reference, the area join), batteries (seeded points, refusals, the share of the reference's routes, length ratios), orders, structure, sector offsets, the AI-readiness checks on a campaign-like and a stock-MP-like level, load sets, the `.fbrb` reader, opfor.log sessions and the in-game criteria, saved runs and their comparison, the command line; with the game: the `.fbrb` reader against FbrbTool, a run on sp_009 and sp_004_b, a repeat with no regression, a damaged path-data file flagged, ONS_MP_004's non-infantry points counted and every infantry target reached on DICE's file, ONS_MP_005GR's level read from its Package bundle |
| `moh-automated-map-and-bot-test-harness/crash-reader/tests/test_crashreader.py` | quick, game | crash reports built in the game's layout: registers, stack, symbols and hints, null registers, build note, log lines; with the exe: function start, null operand, call before a return address, text on the stack |
| `moh-automated-map-and-bot-test-harness/crash-reader/tests/test_exeref.py` | quick, game | `exeref.py` on a program built in the test: strings by part and whole, the instruction using a string's address and its function, a function's callers, an address held in a table, the capped window with names, the command line; with the exe: the `Server.UnrestrictedUnlocks` lookup, `applyLicenses`' caller, the generic getter's "Win32", an FSCommand name in the extern table |
| `moh-menu-tools/tests/test_menu_tools.py` | quick, game | on a movie, patch files, a language file and an exe built in the test: the APT decoder (GetURL2, GotoFrame2, function bodies to their end), every `aptcheck` rule caught when broken and a good patch passing, OPFOR's patch format applied as AptTool does, externs / FSCommands / event handlers with their parameter counts, the exe's name table, localization keys, PC texts and one- and two-byte characters; with the game: OPFOR's 12 menu patch files lint clean, all 128 movies of the game copy pass the rules, the exe's 574-name table and getter cases, known event handlers, English, Russian and Japanese texts |
| `moh-console-readers/tests/test_console_readers.py` | quick, game | XDVDFS images (two partition offsets, nested and empty folders) and STFS packages (both hash-table layouts, a fragmented file); with your Xbox 360 files: the disc's 693 files, Onslaught path data from Update #2 and #3 identical and identical to Napisal's PC files |
| `moh-stockmp-ai/tests/test_stockmp.py` | quick, game | the stock-MP AI layer on levels built in the test: the DBX writer byte for byte, number kinds, a Conquest and a Rush layer authored (Objects, events, stage switches, kinds kept), the minimal layer, configuration checks, the SectorOffsets asset, install / revert on a stand-in game folder, DbxEdit check and round trips; with the game: the writer on Atacama's bundles, Atacama Conquest generated, built and validated, twice identical |
| `core/tests/test_water.py` | game | every level's water read exactly, indices in range, a shader per triangle |
| `core/tests/test_level.py` | game | the loader's counts per category, team, shape, collision, terrain and water against `level_snapshot.json` (`--update` after an intended change) |
| `core/tests/test_terrain.py` | game | heights within 0.1 m of the in-game terrain samples (13 levels) |
| `hpdtool roundtrip / meshtrip` | game | every path-data file and navigation mesh written back byte for byte |
| `core/tests/test_navdata.py` | game | path data as the studio loads it (islands, cache, a file against itself) and the path test (routes, costs from the stored edges, refusals between islands) |
| `tests/test_cli_errors.py` | quick | every command-line tool (C# and Python) given input that must fail: an error exit, the cause and what to do in words, the error in its log |
| `core/tests/test_errors.py` | quick | error handling: causes explained in words, the log (errors with tracebacks, warnings, other threads), crashes caught with exit code 1, old logs pruned, a failing 3D panel kept to the Problems window |
| `core/tests/test_navtune.py` | quick | navigation side files: every operation saves and loads unchanged, undo / redo, the PC's name recorded, invalid edits refused, DICE bases refuse region edits, the base file's hash |
| `moh-auto-navkit/tests/test_water_rule.py` | quick | NavKit's water rule on a sample grid built in the test: ground deeper than the soldier's water limit left out, shallower ground, decks and dry ground kept, overlapping water |
| `moh-auto-navkit/tests/test_area_link.py` | quick | NavKit's area link rule on a grid built in the test: straight steps up to the link step (MOH: the 45 deg slope over a cell, 0.8 m) join an area, diagonals through a side cell, a steeper plane cut into single cells, object surfaces and portals on the slope rule |
| `moh-auto-navkit/tests/test_regress_progress.py` | quick | `navregress.py`'s progress estimates: durations in words, each level's expected time from earlier runs, its sample file size or the default |
| `moh-auto-navkit/tests/test_navboundary.py` | quick | `navboundary.py` on path data built in the test: merged and split areas, matching / missing / extra boundary edges, precision and recall with and without tolerance, portal displacement, the CSV and map export |
| `moh-auto-navkit/tests/test_portal_symmetry.py` | quick | NavKit's portals on grids built in the test: a portal each way between two areas, no edge with two portal pieces, a stacked column's one-way portal turned into outline |
| `moh-auto-navkit/tests/test_reference_clip.py` | quick | NavKit's bound to the reference extent: surfaces outside the box (every floor), inclusive edges, the extent from outlines and portals of every pitch without border portals, no bound for a new map |
| `moh-auto-navkit/tests/test_characters.py` | quick | NavKit treats soldiers standing in the level while it was sampled as absent: their surfaces are not floor, the floor under and beside them is not blocked, other floors keep their flags; the rule off blocks as before |
| `moh-auto-navkit/tests/test_headroom.py` | quick | NavKit's headroom band: ground with the next surface 0.5-1.9 m (the soldier's 1.8 + 0.1) above it is not walkable; a step-height surface or a ceiling above keeps the ground; soldiers overhead do not count; the rule off keeps it all |
| `moh-auto-navkit/tests/test_mohsoldier.py` | quick | NavKit's MOH soldier (`mohsoldier.py`): its sizes and derived values, the sampler / build / walkability using them, the collision layers; spawn points (soldier and alternate), objectives as order points and objective floors, and the check's points on a level without AI orders; with OPFOR's prepared game copy, the same values in the game's `DefaultSoldierPhysics`; transforms whose denormal floats DbxEdit wrote as their bits read as ~0 |
| `moh-auto-navkit/tools/navexport.py mp_03_elimination --check` | game (needs the level's build) | the RSNV export of a level: written, read back (`rsnv.World.validate`), its graph equal to the build's; `tools\rsnavcheck.py` does the same in rsnav itself when its host build is found |
| `moh-auto-navkit/tests/test_lips.py` | quick | NavKit's narrow lips and door sills at 0.2 m: a two-sample steep terrain band is crossed, a three-sample one is not; a 5 cm object sill is crossed between flat floors, not between sloped ones or when 0.5 m high; nothing at 0.8 m; the rules off |
| `moh-auto-navkit/tests/test_dynamic_props.py` | quick | NavKit's dynamic props: a closed door's bottom rail stands for the floor under it, a door leaf's face hit 1 m over a sill is no ceiling, both rows crossed; each rule off cuts its row |
| `moh-auto-navkit/tests/test_roof_prune.py` | quick | NavKit's roof pruning: an unreached roof is dropped; a roof beside an anchored deck at its height, a roof with an anchor on it, a ground-level patch and the main ground stay; the rule off |
| `moh-auto-navkit/tests/test_objective_floor.py` | quick | NavKit's objective floors (M-COMs): an AI order position on a forbidden box keeps the floor under and around it, joined to the ground, also when the box's bottom face lies on the ground; the box top stays forbidden; an order position away from forbidden objects changes nothing; the rule off |
| `moh-auto-navkit/tests/test_bridge_union.py` | quick | NavKit's 45-degree bridges on 600 random cell patterns: every area boundary closed (the old clipping left open ends on about 100 of them); a lone bridge's two diagonals and covered sides |
| `moh-auto-navkit/tests/test_detail_features.py` | quick | NavKit's D lines and F cells (`navdetail.py`) on synthetic areas: none on flat ground; facing D pairs at a raised cell (owner on the right, its heights); an F pair (-x then +x, DICE's start corners and -0.0 directions, heights from each side) at a cell stepped into and out of; 45-degree bridges between diagonal F cells; 300 random areas (D sides pair up, F polygons closed and covering exactly the F cells); kymesh round trip; outline sides moved only in pairs (`navbuild.detail_pass`); route lines do not step across a D side (`navbuild.line_walkable`) |
| `moh-auto-navkit/tests/test_navislands.py` | quick | NavKit's route-island pruning (`navislands.py`, `SHADOW_ISLANDS`) on route graphs built in the test: a small island next to a large one loses its edges and nodes; one with a spawn or AI order on its floor, one far away or far above, and a large one stay; border ids join sectors; a pocket on terrain stays with `SHADOW_OBJECTS_ONLY`; the rule off |
| `moh-auto-navkit/tests/test_havok_index.py` | quick | NavKit's collision index: `<name>_Havok.res` and `<name>_Havok_Havok.res` packfiles found under the blueprint's name |
| `moh-auto-navkit/tests/test_navsample_offline.py` | quick | NavKit's offline sampler on geometry built in the test: the in-game cast loop (layers, the 0.1 m gap, thin solids stepped through, nothing under the terrain, solid convex pieces, two-sided meshes, edge contacts), the capsule test (walls, ceilings, steep terrain; capsule-triangle intervals against brute force), the version-3 file read back, navsample.cpp's grid alignment, destroyed-state parts |
| `moh-auto-navkit/tools/hpdcheck.py` | game (DICE's files); every regression build | the structure of a path-data file: closed area boundaries, edges, node order, no zero-length edges - all hold on DICE's files; `navregress.py` fails a level whose build breaks one |
| `moh-auto-navkit/tests/test_navedits.py` | quick | walkability edits as generator inputs on a sample grid built in the test: outlines, boxes, height ranges over stacked floors, later edits winning, seeds and unseeds |
| `moh-auto-navkit/tests/test_links.py` | game | links and route edges written into a route graph: a link joins two islands (no route before, one after), one-way edges, removal, costs by DICE's rule, the first-edge index rule, unreachable links reported, a DICE base edited on a copy |
| `moh-auto-navkit/tests/test_traversal.py` | game | traversal annotations in `<level>.traversal.json` (ends, route nodes, islands joined; the path data untouched), the apply report, install / restore into a stand-in game folder, `apply --install` |
| `moh-auto-navkit/tests/test_apply.py` | game | `navkit apply` on sp_004_b: no edits gives the regression build; an unwalkable square leaves no ground, an unseed removes an island, a walkable box adds ground; the output reads back exactly |
| `moh-navigation-studio/tests/test_editing.py` | game | the studio's edit mode driven as clicks would drive it: every tool, outline and box, undo / redo, save and reload, the click mode, DICE-base limits, the apply button building in the background |
| `tests/test_hub.py` | game | the suite window driven as clicks would: its tabs and the levels, FbrbTool, LevelDiff, the crash reader and the test runner through their tabs; a failing tool reported with its last lines, a stopped one not; a 3D tool with a bad level exiting cleanly; the level picker |
| `tests/test_viewers.py` | game | the Map Inspector and the Navigation Studio open a level, draw it (screenshot not blank) and exit; prints the GPU used |
| `moh-auto-navkit/tools/navregress.py` | game (2 levels), full (15: the 13 campaign levels and ONS_MP_002 / 004, about 5 hours with detailed zones) | NavKit rebuilds levels with the current code (by default every level with DICE path data); fails if a build's structure breaks (hpdcheck) or any DICE-parity measure is worse than the reference run (`--reference`, default `regression` - kept as the latest accepted run) |
| DbxEdit corpus, `core/tests/test_dbx.py` | full | every unpacked `.dbx`: DbxEdit's fingerprints, and the Python reader's XML identical to DbxEdit's |

Each tool's folder has its own README. The tools that existed before the suite were merged in with their
full history (2026-10-16), so `git log -- <folder>/<file>` shows each file's history.

## Licence

MIT (see `LICENSE`) for all code and documentation in this repository. The licence covers only this
repository's own work: no Electronic Arts / DICE game data, code or other content is included, and the
tools need your own copy of the game. Third-party libraries keep their own licences (moderngl, imgui-bundle:
MIT; glfw: zlib; numpy: BSD). Not affiliated with or endorsed by Electronic Arts or DICE; Medal of Honor is a
trademark of Electronic Arts Inc.

Developed for Project OPFOR. Game data is read from your own installation and never distributed.
Medal of Honor (2010) is © Electronic Arts / DICE.
