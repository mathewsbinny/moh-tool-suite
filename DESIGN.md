# MOH Tool Suite: design

Eight tools for building and debugging AI on Medal of Honor (2010) maps, plus Project OPFOR's asset
builder. They share one core: level loading, a 3D view, picking and the game's data formats.

- **Modes of the 3D view:** the inspector, the navigation studio, the tactical authoring tool and the bot
  debugger.
- **Command-line tools on the same core:** the asset pipeline, the diff workbench, the behaviour
  simulator and the test harness.

This document fixes what each tool does, what they share, the file formats they write, and the order
they are built in. It is a draft for review, and it changes as the tools are built (last revised
2026-10-26: the test harness, sections 4.5 and 5.5; the two tools added outside the original eight, section 1;
statuses in sections 3.1, 4.2 and 7).

## 1. The tools

| Tool | For | In short |
|---|---|---|
| **moh-map-inspector** | any MOH modder | Browse a level in 3D: geometry, collision, spawns, objectives, destructibles, every placed entity. Click anything to see where it comes from and its properties. |
| **moh-navigation-studio** | any MOH modder, Project OPFOR | View and edit the AI's navigation data (Kynapse `pathdata.hpd`, made by NavKit). Force ground walkable or unwalkable, link areas and routes, inspect disconnected regions, annotate traversal links, test paths. Export a finished file or a side file. |
| **moh-tactical-map-authoring-tool** | Project OPFOR bots | A layer above the navigation data: objectives, cover candidates, firing positions, rally points, vehicle approaches, squad routes, strategic regions. Generated as candidates, corrected by hand. |
| **moh-bot-ai-debugger** | Project OPFOR bots | Live view of the AI while the game runs: positions, orders, targets, path requests, tactical states, line of sight, threats, squads. |
| **moh-automated-map-and-bot-test-harness** | Project OPFOR | Repeatable scenarios across maps, with results saved per build so changes can be compared: reachability, path tests, bot traversal, stuck detection, squad coordination, vehicle use. |
| **moh-asset-pipeline** (in the core) | any MOH modder | Batch extraction into common formats: geometry and collision to OBJ/glTF, entities and transforms, spawns and objectives to JSON, textures and materials later, dependency reports. It is the same readers the viewer uses. |
| **moh-diff-workbench** | research | Binary and asset diffing: ImHex pattern files for MOH formats, plus a report of which structures are understood and what changed, with reproducible extraction logs and hashes. |
| **moh-ai-behavior-simulator** | Project OPFOR bots | Runs OPFOR's bot decision code (behaviour trees or state machines, threat scoring, squad orders, objective priorities) on simplified maps, without the game. It finds logic flaws cheaply, though not problems with controls, physics or animation. |
| **moh-asset-builder** (asset builder & validator, in the OPFOR repo) | Project OPFOR | Turns editor outputs into versioned runtime packages for the standalone client, with schema and dependency checks and a report of missing or incompatible data. |

Added beside these eight (2026-10-26, both built; their READMEs):

| Tool | For | In short |
|---|---|---|
| **moh-stockmp-ai** | any MOH modder, Project OPFOR bots | Gives a stock MP level an AI layer (AI spawners, AI orders, event wiring, HasAI, PathfindingTypes, SectorOffsets, AI template) as level data, built into its bundles and checked offline. Not the tactical authoring tool: it writes game data, not annotation side files. |
| **moh-menu-tools** | any MOH modder, Project OPFOR | `aptcheck` (menu patches against the rules that crash the menu loader), `aptrefs` (externs, FSCommands and engine events of every movie, with the exe's name / id table), `mohloc` (localized texts). |

## 2. Rules

- **Game data comes from the user's own install** and is read where it is. Nothing EA-owned is shipped:
  no geometry, no level data, and no DICE path data.
- **NavKit stays clean-room.** We learn only from DICE's output files and the generator's boundary with
  DICE code. NavKit never uses the licensed Kynapse generator, and nothing in the suite bypasses its
  licence check. (The toolkit's `kynapse-map-builder` only prepares runs for someone holding a valid
  licence; it is not part of the suite.)
  When a NavKit file loads but the game's AI misbehaves on it, the runtime may be observed to find out why:
  OPFOR's probes, and short, targeted disassembly of the instructions around the failing check (no bulk
  decompiling). That says which property of the file the runtime rejects; how to produce that property
  is still taken from DICE's output files, and no Kynapse code is copied or translated into NavKit.
- **Edits never count toward DICE parity.** NavKit's parity score measures the generator alone. The
  studio's edits are for maps without DICE data, and for places shown to be beyond any rule (hand edits,
  geometry missing from the shipped game).
- **Everything the game reads stays in the game's formats.** Path data is Kynapse `pathdata.hpd`, written
  by NavKit's writer. Anything the format cannot carry goes into side files that OPFOR reads: ladder and
  jump links, tactical annotations.
- **No network traffic.** The live debugger connects to OPFOR on the same PC (127.0.0.1), which OPFOR's
  network guard allows.

## 3. The shared core (`core/`)

### 3.0 What already exists (merged into the suite with its history, 2026-10-16)

| Tool | Where | Used for |
|---|---|---|
| FbrbTool | `moh-fbrb-tool` | unpacking bundles |
| DbxEdit, DbxEditGUI, `FORMAT.md`, test suite and fingerprints | `moh-dbx-tools` | DBX reading and editing, level checks, cull-slot repair; the spec the Python reader is written from |
| LevelDiff | `moh-diff-workbench/level-diff` | field-level diffs of `.dbx` files |
| crash reader | `moh-automated-map-and-bot-test-harness/crash-reader` | in-game test failures |
| console readers | `moh-console-readers` | Xbox 360 sources |
| NavKit: `navkit.py`, `navkitGUI.pyw`, generator, `navdiff`, `navparity`, `navcompare`, `ordercheck` | `moh-auto-navkit` | navigation data, and the parity numbers (baseline A) the studio's difference view shows |
| `testrun.bat` | Project OPFOR | starting a level in the game and reporting the outcome |

### 3.1 Data layer

| Source | How it is read | Status |
|---|---|---|
| `.fbrb` bundles | FbrbTool unpack; read-only access without unpacking in `core/mohcore/fbrb.py` (2026-10-26: entry list and single entries; used by the test harness) | works; a cache per bundle date (as `testrun` keeps) to add |
| binary `.dbx` (entities, transforms, properties) | a Python reader written from `moh-dbx-tools/FORMAT.md` (no exe calls, fast), `core/mohcore/dbx.py` | works (2026-10-16): XML identical to DbxEdit's on all 31,404 fingerprinted files (`core/tests/test_dbx.py`, full tier) |
| object collision (`*_Havok.res`) | NavKit's `hkpack.py` | works for the shapes NavKit uses; other shape types to add |
| static model groups (instance transforms) | `*_HavokAsset` partitions | works in NavKit |
| terrain heights | `Terrains/<level>/<level>.Heightfield-000.res` (in `terrain-00.fbrb`) | decoded (`core/mohcore/terrain.py`, section 7 question 1): a 1024² or 2048² grid of 16-bit heights, matching the in-game terrain to within 4.5 cm |
| water | `<terrain>.Water.Mesh.res` (geometry), `<terrain>.Water.res` (drawing copy), in `terrain-00.fbrb` | decoded (`core/mohcore/water.py`, section 7 question 2): every level's water mesh, and each triangle's shader (water, coast, wakes, river) |
| surfaces measured in game | NavKit's samples (`work\navsample\*.bin`, `navsample.py`) | works; the terrain fallback when a level's heightfield cannot be read |
| navigation data | NavKit's `hpdtool.py` and `kymesh.py` (byte-exact reader and writer) | works |
| spawn points, AI orders and patrol routes | level partition entities (as NavKit's `levelobjects` and `ordercheck` read them) | works |
| objectives (M-COMs, flags), destructibles, vehicles and vehicle spawns | level partition entities, and each placed object's blueprint | read (`core/mohcore/level.py`): M-COMs are `mpobjective` blueprints, flags `CapturePointEntityData`; a placed object's group comes from its blueprint's object type (`StaticModelEntityData` with health states or part links = destructible; `DynamicModelEntityData`, `LadderEntityData` ...); teams from `Team` / `InitialOwnerTeam` (alternate spawns: their spawner's); areas and routes are `AreaGeometryEntityData` chains |
| terrain name and placement | `TerrainEntityData` in the level partition | its `TerrainAsset` names the terrain (mode variants share their base map's: MP_009GR uses `mp_009`). Its transform is **not** applied to the heights: on 11 of 13 campaign levels it moves the terrain 1-59 m, but the stored heights unmoved match the game |

### 3.2 Scene model

A `Level` holds:

- **entities:** GUID, type, name, partition, transform, blueprint, and the properties from the DBX tree;
- **meshes:** collision triangles per entity and per static-group member, terrain and water;
- **navigation layers:** areas, outlines, portals, border portals, route graph, islands, detailed zones;
- **annotation layers:** from the tactical tool.

Every drawable item keeps a link to its source (file, partition, instance GUID, field path), so picking
can always say where something comes from.

### 3.3 View and interaction

- **Camera:** orbit, pan and zoom around a point, plus a free-fly mode (WASD and mouse), with a 360° view
  and bookmarked views per level.
- **Picking:** ray against a bounding-volume hierarchy of triangles, then entity bounds and navigation
  geometry. Clicking selects; hovering shows a tooltip.
- **Layers:** each can be shown or hidden, with its own colour scheme (areas by island, by sector, or by
  difference against a reference file) and transparency.
- **Selection and editing:** box, polygon prism (a drawn outline with a height range) and single item;
  undo and redo; a command log (every edit as a reproducible command, like DbxEdit's edit scripts).
- **Panels:** properties of the selection, search (GUID, type, name, value), layers, edit history,
  messages.

### 3.4 Stack

- Python 3.10+.
- `moderngl` with `glfw` for the GPU view: millions of triangles at interactive rates.
- Dear ImGui (`imgui-bundle`, MIT licence) for panels.
- `numpy`.

NavKit's Python code (reader, writer, sector build, checks) is used directly. That is the main reason for
Python: "force walkable, then rebuild the affected sectors" runs in-process, with no port of the
generator. Every library is under a permissive licence (MIT, zlib, BSD).

## 4. Tool scopes

### 4.1 moh-map-inspector (first)

- **Load:** a level from the game copy, by name.
- **Draw:**
  - terrain, collision meshes (static groups and placed objects) and water;
  - spawn points (per team, human or AI);
  - objectives (Rush and Onslaught M-COMs, Conquest flags);
  - destructible objects, vehicles and vehicle spawns;
  - AI orders and patrol routes (the level's own AreaOrder and PatrolOrder data).
- **Click anything** to see its type, GUID, partition, blueprint, transform and full property tree, with
  references you can follow to other entities.
- **Search, filter by type, measure** distances and heights.

### 4.2 moh-navigation-studio

- **Generate:** run NavKit on a level, with its sampling settings, from the studio. Sampling starts the
  game (NavKit measures levels through OPFOR's in-game sampler), so the studio asks first. Building from
  existing samples does not need the game. (2026-10-26: NavKit's `navsample_offline.py` computes the same
  samples from the level's collision without the game; not yet wired into `navkit.py` or the studio.)
- **View:**
  - areas coloured by island, sector or pitch; outlines, portals, border portals and the route graph;
  - detailed zones;
  - a difference view against a reference file (DICE's, or an earlier build), from NavKit's `navdiff`.
- **Edit:** each edit is recorded as an operation (§5.1).
  - force a region walkable or unwalkable, then rebuild the affected sectors;
  - add or remove seeds, to keep or drop an island;
  - link two areas, add or remove route edges;
  - annotate traversal links (ladders, jumps, vaults, drops);
  - mark regions as AI-only or vehicle-only (vehicle-only needs vehicle path data: NavKit builds it since
    2026-10-26 with `tools\navvehicle.py`, not yet part of `navkit.py`; region marking not built).
- **Inspect:** disconnected regions (islands sorted by size, with the nearest connection to the main
  network), unreachable spawns and objectives.
- **Path test:** pick two points and see the route over the route graph and areas, its cost, or why
  there is none (which island each point is on, and where they are closest).
- **Output:**
  - a finished `pathdata.hpd` (written by NavKit's writer and checked by reading it back), for your own
    installs and for maps without DICE data;
  - or a side file (§5.1), applied by `navkit apply` (§5.2) when a level is built or installed. This is
    the shareable form, and the only form for edits to DICE's files.

### 4.3 moh-tactical-map-authoring-tool (when OPFOR's bot layer takes shape)

- **Layers:**
  - objectives and their approaches;
  - cover candidates (positions with protection from one or more directions);
  - firing positions (with fields of view);
  - rally points, vehicle approach routes, squad routes;
  - strategic regions (sectors of a map with a role: base, front, flank).
- **Generators propose candidates** from geometry and the navigation data: cover from collision near
  walkable ground, sight lines by ray casts, approaches by path search. People correct them.
- **Output:** a per-level annotation file (§5.3) that OPFOR's bot layer reads.

### 4.4 moh-bot-ai-debugger (as soon as AI runs in OPFOR; first for the game's own AI)

- **OPFOR side:** a telemetry module in OPFOR's DLL streams AI state over 127.0.0.1 (§5.4), a few times a
  second: soldier positions and orientation, team, current order and goal, target, path request and
  route, tactical state, perceived threats, squad membership. It starts with what the game's own AI
  exposes, then adds OPFOR's bot layer as it is built.
- **Suite side:** the same 3D view, with the live soldiers drawn over the level and navigation data:
  - click a soldier for its state;
  - its current route, line of sight and threat lines;
  - a timeline to scrub back through the last minutes;
  - recording to a file for later.

### 4.5 moh-automated-map-and-bot-test-harness (grows from now on)

- **Offline tests** (no game):
  - spawn-to-objective reachability for every team and spawn;
  - unreachable-region detection;
  - path-test batteries;
  - NavKit parity (`navparity`).
- **In-game tests** (through OPFOR's `testrun.bat` and the debugger's telemetry):
  - a level loads and reaches the match;
  - bot traversal (bots reach given points within a time);
  - stuck detection (no progress for N seconds);
  - squad coordination and vehicle use.
- **Results:** saved per named run, as NavKit's parity runs are, and compared between runs to show
  whether a change improved things.

**Design (M5, 2026-10-26; `harness.py`, details in the folder's README).**

- **A test run** is one `harness.py run <name>`: per level, the level's points (soldier spawns; AI targets -
  AreaOrders, M-COM stations, capture points, mission objectives; AI orders and patrol waypoints), and per
  path-data file - `dice` (DICE's original), `navkit` (NavKit's build), `installed` (what the game loads, when not
  DICE's) - the results of the file tests; then the level's AI-readiness checks and its in-game checks. It records
  the date, the PC, the suite commit (and uncommitted changes), the options, and each file's sha1.
- **A result** has a status - pass, fail, warn, info (a measurement without an expectation), skip, hold (needs the
  game), error (could not run; traceback in the log) - a summary line, flat numeric metrics, detail lines and, where
  points are involved, per-pair or per-point bits for point-by-point comparison.
- **Infantry points only.** The infantry tests take infantry points; spawner markers, vehicle spawns (land, sea, air,
  stationary weapon), another map's spawns off this map's ground, objective markers, bases (capture points on
  M-COM levels), points on water and airborne points are classed and counted, not tested (`testharness/classify.py`).
- **DICE's file is the reference.** NavKit's builds are measured on the same points and pairs as DICE's file;
  failures are relative to it (a target DICE reaches and NavKit does not; DICE's routes NavKit does not find). An
  absolute expectation DICE's own file misses is a warning there, since DICE's data works in the game.
- **Test kinds, offline:** `structure` (hpdcheck), `reachability` (spawns -> targets over the directed route graph,
  points joined through the area they stand on; islands), `battery` (seeded random ground-point pairs from DICE's
  cells and spawn -> target pairs through the path test, `navpath`; success, refusals by cause, route length
  against DICE's), `orders` (a route node near every AI order, on an area), `offsets` (the file's sector ids
  against the level's SectorOffsets asset), `parity` (navparity's measures, taken from saved navparity / navregress
  runs or measured and cached, judged by `navregress.compare`), `level` (AI readiness from the game copy's
  bundles: HasAI, AI spawners, real templates, templates and their partitions in the load set, kits, AreaOrders,
  the SectorOffsets asset, installed path data - Project OPFOR's validation criteria, `STOCK_MP_AI_PLAN.md` 6.3).
- **Comparison:** `harness.py compare <base> <new>` per level, file key, test and metric, each metric with a
  direction (higher / lower / closer to 1.0 better, or a count) and a tolerance; worse beyond it, or a test that
  starts failing, is a regression (exit code 1). The format is section 5.5.
- **Tiers:** quick (every kind on data built in the test, seconds), game (a run on sp_009 and sp_004_b, a
  repeat without regression, a damaged file flagged; about a minute cached), by hand (every level with DICE data,
  before and after a NavKit change).
- **In-game tests plug in through the run's `ingame` part.** Now: `harness.py import-log` turns a session of the
  user's own `opfor.log` into three checks - the level loads and AI init runs, the SectorOffsets loads read the
  asset, pathfinders appear after spawning. **On hold (needs playtest):** the harness calling `testrun.bat` per
  level; the telemetry tests of M6 (bot traversal, stuck detection, squads, vehicles), which will read recordings
  of the stream in 5.4; and the player's judgement (bots fire, advance, defend).

### 4.6 moh-asset-pipeline (in the core; geometry and entities in M1)

- **Command line:** `moh-extract <level> --geometry obj|gltf --entities json --deps report.json`, and
  the same for any bundle.
- **One code path:** it uses the core's data layer, so whatever the viewer can show, the pipeline can
  export.
- **Dependency report:** the partitions and resources a level or bundle needs, and where each is found
  (which bundle), with missing ones flagged. This doubles as the game-wide search index ("where is X
  defined, who uses it").
- **Textures, materials and render meshes:** later, when the inspector needs them. Existing community
  converters are called as external tools where their licence does not allow reusing their code.
- **Every run writes a log:** inputs with SHA-1, tool versions, outputs with SHA-1.

### 4.7 moh-diff-workbench (alongside research, from M2)

- **ImHex pattern files** (`patterns/*.hexpat`) for MOH's formats, so a file can be viewed as structure
  and two files diffed by field in ImHex: `pathdata.hpd`, `.fbrb`, the binary DBX header and tree,
  terrain heightfields, Havok packfiles, then others as they are researched.
- **`moh-coverage <file>`:** which byte ranges of a file our readers understand, and which are still
  unknown. It tracks research progress per format.
- **`moh-bindiff <a> <b>`:** for known formats, changed fields by structure, using LevelDiff, `navdiff`
  and DbxEdit's parser; for unknown ones, changed byte ranges with their offsets.
- **Extraction logs and hashes**, so any finding can be reproduced from the same inputs.

### 4.8 moh-ai-behavior-simulator (with OPFOR's bot layer)

- **The rule this brings forward:** OPFOR's bot decision logic is written as an engine-independent
  library. It reaches the world only through an interface: positions, line of sight, paths, objective
  states, squad messages. OPFOR's DLL connects that interface to the game; the simulator connects it to a
  simplified map. Both run the same decision code, so a flaw found in simulation is a flaw in the bots.
- **The simplified map** comes from NavKit's navigation data (route graph, areas), the tactical
  annotations, and line of sight from collision geometry.
- **Scenarios:** teams, spawns and objectives from the level; scripted or random enemy behaviour; many
  runs faster than real time; results into the test harness.
- **Out of scope:** controls, physics, animation, weapon ballistics. Those stay with in-game tests.

### 4.9 moh-asset-builder: asset builder & validator (in the Project OPFOR repo)

- **Inputs:** NavKit output and side files, tactical annotations, level ports and recipes (such as the
  console Onslaught builds), menu patches.
- **Output:** versioned runtime packages for OPFOR's standalone client: bundles registered in
  `package.mft`, `bundleManifest` and the startup bundle; side files in place; backups kept for clean
  uninstall.
- **Checks before packaging:**
  - schemas of the side files;
  - `DbxEdit check` on every level, and `DbxEdit fixslots` where a level uses another level's cull
    trees;
  - references resolved across bundles;
  - path data present for every level that needs it, and read back after writing.

  (NavKit's parity is a development measure, not a packaging check: campaign levels ship with DICE's
  own path data.)
- **Report:** missing, incompatible or outdated data.
- **It absorbs** the import scripts (`import_onslaught.bat` and its manifest, startup and campaign-fill
  helpers), the Nelson Bay Onslaught import step that is still to be written, and the bundle manager
  (OPFOR backlog item 8). The installer builds on it.

## 5. File formats

### 5.1 Navigation side file: `<level>.navtune.json`

```json
{
  "format": "moh-navtune", "version": 1,
  "level": "Levels/MP_005", "base": {"kind": "navkit", "sha1": "..."},
  "operations": [
    {"op": "walkable",   "region": {"polygon": [[x, z], ...], "y": [low, high]}, "value": true,
     "by": "name", "when": "2026-10-16T12:00:00"},
    {"op": "unwalkable", "region": {"box": [x0, y0, z0, x1, y1, z1]}},
    {"op": "seed",       "at": [x, y, z]},
    {"op": "unseed",     "at": [x, y, z]},
    {"op": "link-areas", "a": [x, y, z], "b": [x, y, z]},
    {"op": "route-edge", "a": [x, y, z], "b": [x, y, z], "action": "add"},
    {"op": "traversal",  "kind": "ladder", "bottom": [x, y, z], "top": [x, y, z], "two-way": true},
    {"op": "note",       "at": [x, y, z], "text": "..."}
  ]
}
```

Operations are recorded by position, not by area index or node id, so they still apply after the level
is regenerated. The order matters, and every operation records who made it and when (`by`, `when`;
shown on the first one above). `by` is the name of the PC the edit was made on (Windows' computer name)
unless the user sets another name.

**Bases (decided 2026-10-16):** the normal base is NavKit's output - NavKit builds the level, the edits go
on top, and `navkit apply` re-applies them after every rebuild. Region edits (walkable, unwalkable, seeds)
are allowed only on a NavKit base. On a DICE base (kept where DICE's data is still better than NavKit's)
only additive edits are allowed: links, route edges, traversal annotations and notes. `base` names the file the edits were made on:
- `navkit`: NavKit's output for the level;
- `dice`: the level's own path data. Only the side file can be shared then, never the edited file,
  which would contain DICE's data.

Its `sha1` lets `navkit apply` warn when the base has changed since.

### 5.2 `navkit apply <level> <navtune>`

Status (2026-10-16): walkability operations work (`navkit.py apply <level> [--navtune file] [--out file]`, a full
rebuild), and link and edge operations are written into the route graph (both nodes in one sector; on a DICE
base into a copy of DICE's file, never shared), and traversal operations go to `<level>.traversal.json`
(format in `moh-auto-navkit/tools/navtraversal.py`): each annotation with its ends, the route node and island
at each end, and whether it joins two islands. Every run writes `<output>.report.txt`; `--install` installs
the result with its traversal file (`<game>\Output\win32\ai\levels\<level>\infantry\<level>.traversal.json`,
where OPFOR's bot layer will read it).

Applies a side file:

- **Walkability operations** feed into NavKit's build, which then rebuilds the affected sectors.
- **Link and edge operations** are applied to the written data.
- **Traversal links** the format cannot carry go into `<level>.traversal.json` for OPFOR.

The result is checked by reading it back, and reported. The same command is used by OPFOR's import
steps and by the installer.

### 5.3 Tactical annotations: `<level>.tactical.json`

Typed annotations, each with a position, orientation, extent, team relevance, game-mode relevance and
source (generated or hand-made). The schema is fixed when the bot layer's needs are known.

### 5.4 Telemetry stream

JSON lines over 127.0.0.1 TCP, from OPFOR to the debugger, one frame per update:
`{"t": ..., "soldiers": [...], "squads": [...], "events": [...]}`. The schema is fixed with the first
OPFOR telemetry module, and is versioned.

### 5.5 Test harness runs: `<OPFOR>\work\harness\runs\<name>.json`

```json
{
  "format": "moh-harness-run", "version": 1, "name": "nightly", "date": "2026-10-26 23:40", "host": "PC name",
  "suite": {"commit": "9d085a9", "changed": false}, "options": {"levels": ["sp_004_b"], "pairs": 200, "seed": 1},
  "seconds": 312,
  "levels": {"sp_004_b": {
    "name": "SP_004_B", "notes": ["..."], "points": {"sources": 107, "targets": 69, "orders": 310},
    "files": {
      "dice":   {"path": "...", "sha1": "...", "size": 0, "tests": {"structure": RESULT, "reachability": RESULT,
                 "battery": RESULT, "orders": RESULT, "offsets": RESULT}},
      "navkit": {"path": "...", "sha1": "...", "size": 0, "tests": {"...": RESULT, "parity": RESULT}}},
    "level":  {"has-ai": RESULT, "ai-spawners": RESULT, "ai-templates": RESULT, "templates-loaded": RESULT,
               "kits-loaded": RESULT, "orders": RESULT, "offsets-asset": RESULT, "path-data": RESULT},
    "ingame": {"loads-ai-init": RESULT, "offsets-read": RESULT, "pathfinders": RESULT,
               "bot-traversal": RESULT, "stuck": RESULT, "squads": RESULT, "vehicles": RESULT}}}
}
```

`RESULT` is `{"status": "pass|fail|warn|info|skip|hold|error", "summary": "...", "metrics": {"name": number},
"details": ["..."], "seconds": 1.2, "data": {...}}`; `data` holds the pair or point bits (`"1"` reached / routed /
near a node, `"0"` not, `"-"` a point without a route node) and, for batteries, a key of the point set, so two runs
on the same points compare pair by pair. A newer version is refused by older readers. Caches beside it
(`work\harness\cache`: navigation data, bundle listings and entries, parity rows) can be deleted at any time.

## 6. Order of work

| Milestone | Delivers | Also gives |
|---|---|---|
| M1 | the core: data layer (Python DBX reader, collision, terrain heightfield), scene, view, camera, picking, panels (done 2026-10-16) | |
| M2 | Map Inspector: entities, spawns, objectives, destructibles, properties, search (done 2026-10-16: see `moh-map-inspector/README.md`; water added the same day) | |
| M3 | Navigation Studio, viewing: navigation layers, islands, detailed zones, difference view against DICE (done 2026-10-16: `moh-navigation-studio/README.md`, `core/mohcore/navdata.py`) | the 3D view for NavKit's parity work |
| M4 | Navigation Studio, editing: regions, seeds, links, traversal annotations, path test, finished file and side file, `navkit apply` (steps 1-6 done 2026-10-16: `moh-navigation-studio/README.md`; step 7, an in-game check that the game's AI uses a hand-made link, open) | |
| M5 | Test harness, offline: reachability, path batteries, parity, saved runs (the suite's own tests came first, 2026-10-16: `run_tests.py`; NavKit's parity runs and regression - `navparity`, `navregress`, `navboundary` - exist in NavKit, 2026-10-16 to 10-19; the harness, 2026-10-26: `moh-automated-map-and-bot-test-harness/harness.py` - structure, reachability, batteries, orders, sector offsets, parity, AI readiness from the game copy's bundles, saved runs and their comparison, `import-log` for in-game sessions; running `testrun.bat` per level on hold until play-tested) | the harness's level checks for stock-MP bot maps (`STOCK_MP_AI_PLAN.md` 6.3) |
| M6 | OPFOR telemetry module and Bot AI Debugger, on the game's own AI first | the harness's in-game tests |
| M7 | Tactical authoring tool, once the bot layer's needs are known | |
| alongside | asset pipeline (geometry and entities with M1; dependency report with M2; textures later), diff workbench patterns (with each format researched) | |
| with Goal 3 | the bot decision library (engine-independent) and the AI behaviour simulator; moh-asset-builder with the installer | |

## 7. Open questions

1. **Terrain heightfield: resolved (2026-10-16).** Decoder in `core/mohcore/terrain.py`; the layout:
   - **Header (little-endian):** six floats (the world bounds: min x, y, z, max x, y, z), the grid size
     (1024 or 2048 samples per side), five more u32 (log2(size) - 3, log2(size) - 2, about 256 × size, 0, a
     count; not needed for heights), then **one byte, 0 in every level**.
   - **Heights from offset 0x31:** size × size **little-endian** u16, rows along z, columns along x,
     height = min y + value / 65535 × (max y - min y), sample (i, j) at min + (i, j) × extent / size
     (1 m on a 2048 m level). Grid squares split along the (i, j)-(i+1, j+1) diagonal.
   - **Then:** a table of u32 offsets (0, 2, 4, ...) and further data, probably level of detail; not decoded.
   - **Checked** against the in-game terrain samples of all 13 campaign levels (40 million samples): median
     error 0.1-10 mm, 99.9 % within 4.5 cm (the sampler's sphere cast has a 5 cm radius), at most 0.03 % of
     a level's samples more than 0.1 m off.
   - **Why it took a second look:** the odd byte at 0x30 makes the heights start at an odd offset. Read from
     0x30 as big-endian, the values look nearly right (a value's high byte comes from its neighbour), so
     about 20 % of samples were 1-4 m off along contour lines; the earlier test at even offsets found nothing.
2. **Water: resolved (2026-10-16).** Decoder in `core/mohcore/water.py`.
   - **WaterMesh** (`.Water.Mesh.res`, little-endian): u32 bodies (1 everywhere), then per body u32 vertex
     count, (f32 x, y, z) vertices, u32 index count, u32 indices. Read on all 36 levels with a terrain
     bundle, each file consumed exactly; SP_006 and SP_009 have 0 vertices (no water). Heights are per
     vertex: rivers slope (SP_02 17.9-156.8 m), seas are flat (SP_01 4.4 m).
   - **VisualWater** (`.Water.Res`): u32 10, u32 stride (36), vertex bytes, vertices (half-float position,
     normal, texture coordinates), u32 index bytes, indices (the mesh's, on 33 of 34 levels with water; SP_04
     differs), u32 slot count, the slots' shader paths, u32 range count, ranges (slot, triangles, first
     index, index count), u32 0, byte 0x46. The ranges give each triangle's shader.
   - **World frame:** the mesh is used as stored (no entity transform). Much of it lies under the terrain,
     which hides it (SP_01's sea runs under the island; on SP_02 only valley lakes show); on MP_005, 70 % of
     the water inside the terrain is 0-20 m above the ground. Not yet compared with the game in play.
   - **Next for NavKit:** test whether DICE's path data leaves out ground under water (the extra-ground gap).
3. **Traversal links in Kynapse data:** whether the format can carry a ladder or jump link the game's AI
   would use, to be tested in game. Until then, those links go to OPFOR's side file.
4. **The path test's fidelity:** routing over the graph matches what the game plans, but not its path
   smoothing. OPFOR's DLL could answer path queries from the real engine (`navquery`) for a
   check.
5. **Performance:** a whole level's collision (millions of triangles) in one view, with level-of-detail
   or culling if needed.
6. **Entity types (mostly answered):**
   - **Spawns:** `SoldierSpawnEntityData` and `AlternateSpawnEntityData` (soldiers),
     `VehicleSpawnEntityData` (vehicles).
   - **Objectives:**
     - Conquest flags are `CapturePointEntityData`.
     - Rush and Onslaught use `MissionObjectiveEntityData`, `ArmDisarmAwardEntityData`,
       `CapturePointEntityData` and the placed M-COM objects (`objects/objects/mpobjective`).
   - **Play area:** `CombatAreaTriggerEntityData`, one per team and Rush stage, each with a boundary
     polygon (`Geometry`, `SurroundingGeometry`) and the field `IsAiAllowedOutside`. This bears on
     NavKit's extra ground: DICE may have generated only inside the combat areas.
   - **Out of bounds:** `DamageAreaTriggerEntityData`.
   - **Still to identify:** destructibles (the `PredestructionEntityData` sets and destructible object
     types).
7. **Vehicle navigation data:** NavKit made infantry data only until 2026-10-26. Vehicle data (pitch 1 m, larger
   radius) is needed for vehicle-only regions and for the bots' vehicle use (Goal 4). **Since 2026-10-26:**
   `moh-auto-navkit/tools/navvehicle.py` builds land-vehicle files offline (77 % of DICE's vehicle ground on 14
   levels; NavKit README "Vehicle path data", spec §2.2); boat meshes (`navwater_mesh.py`) and a cross-domain route
   planner (`navdomains.py`, `research/CROSS_DOMAIN_ROUTES.md`) are prototypes for OPFOR's bot layer. Not yet in
   `navkit.py`, the studio or a play test.
