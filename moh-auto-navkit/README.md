# BC2 NavKit

Standalone tooling to create AI navigation data (`pathdata.hpd`) for Battlefield: Bad Company 2
levels – including multiplayer maps that never shipped with any – so the game's own AI can move on
them. Developed for Project OPFOR and published for other modders as `moh-auto-navkit`, the automated
navigation-data generator of the MOH suite. It measures levels through Project OPFOR's in-game sampler,
so it needs an OPFOR install, plus the suite's FbrbTool and DbxEdit. Since 2026-10-26 the same measurements can
also be computed without the game (`tools\navsample_offline.py`, "Standalone sampling"; not yet part of
`navkit.py` or the window).

**To use it, start with [QUICKSTART.md](QUICKSTART.md).** Double-click `navkitGUI.pyw` for the window, or
run `python navkit.py` from a command prompt.

## Why

BC2's AI uses Autodesk Kynapse. The game ships the Kynapse *runtime* (which loads `pathdata.hpd`)
under DICE's runtime licence, but the *generator* that DICE used to create that data is a separately
licensed developer tool: the copy that is still inside the game executable stops with a FLEXnet
licence error. NavKit replaces it with independent code.

## Rules (clean-room approach)

1. **Research** (`research\`): the Kynapse map builder in mohmpgame.exe and the `.hpd` files are
   reverse engineered for interoperability. Results are written up as a *specification* – file
   formats, data structures, units, what each build stage produces. Licence checks are only located
   and documented as the boundary; they are not analysed in order to defeat them and nothing in this
   project bypasses them.
2. **Implementation** (`src\`): written from the specification with our own code and open-source
   libraries (e.g. recastnavigation, zlib licence). No Kynapse code is copied or translated
   line-by-line, no Kynapse binaries are shipped, and the tool never runs Kynapse code.
3. Game data (levels, collision meshes) always comes from the user's own installation.
4. Geometry is sampled through the **game engine's own physics queries** (DICE code, via Project
   OPFOR's `[dev] navsample` option) or read from the level's own data files – never through Kynapse.
   Observations of the original generator are limited to its boundary with DICE's code (the point
   queries it makes, the files it writes) and its output data.
5. The private folder `research\kynapse mapbuilder research\` is git-ignored and never published.

## Layout

```
QUICKSTART.md        how to build path data for a level, step by step
navkitGUI.pyw        the NavKit window (Tkinter): the steps below as buttons
navkit.py            driver: levels / prepare / sample / build / check / install / restore / status / all
research\            specification (KYNAPSE_SPEC.md) and images (img\)
tools\               NavKit tools (Python 3.13 + numpy / matplotlib)
  hpdtool.py         pathdata.hpd: info / sector / roundtrip / coverage / meshtrip
  kymesh.py          per-sector runtime mesh: parse_mesh / write_mesh (byte-exact)
  hpdview.py         render a sector to PNG (outlines, portals, graph)
  navsample.py       read in-game collision samples; compare with a pathdata.hpd
  hkpack.py          Havok 5.5 binary packfile reader (the level's *_Havok.res collision assets)
  levelobjects.py    object placements, pathfinding flags, forbidden-object triangles of a level
                     (reads the level XML through core's mohcore/xmlinst.py: instances and field refs in any
                     attribute order - DICE's tools write guid / ref first, Napisal's type / name first)
  navwalk.py         walkability rules + scoring against existing path data
  detailed.py        which sectors are detailed zones (pitch 0.2), from the level alone
  ladders.py         the level's ladders and their collision boxes, for ladder links (written by prepare)
  sample_all.py      unattended in-game sampling of several levels
  navbuild.py        the generator: samples + level objects -> pathdata.hpd
  hpdcheck.py        structure of a pathdata.hpd: closed boundaries, edges, node order, D / F (as DICE's files)
  navdetail.py       D lines and F cells of an area (DICE's rule; used by navbuild; tests\test_detail_features.py)
  navislands.py      shadow islands: small route islands beside a larger one lose their nodes (used by navbuild;
                     tests\test_navislands.py; see "Route islands")
  ordercheck.py      scoring: AI order positions on the areas, pairwise route check, self-check
  navcompare.py      two files on a level's AI positions: coverage, main network, pairwise, size
  navdiff.py         two files layer by layer: sectors, walkable cells, areas, boundaries, route graph
  navdf.py           two files' D lines and F cells: counts, precision / recall per pitch (navparity adds them)
  navparity.py       DICE-parity report over many levels, saved per named run
  navregress.py      regression test: rebuild levels with the current code, fail if a parity measure got worse
  navwater.py        water depth against path-data coverage (does the data leave out ground under water?)
  navboundary.py     area boundaries cell by cell against DICE's: matching / missing / extra edges, merged areas, portals; CSV + maps
  navedits.py        the walkability edits of a navigation side file as generator inputs (navkit.py apply)
  navlinks.py        a side file's links and route edges written into a path-data file's route graph
  navtraversal.py    a side file's traversal annotations (ladders, jumps ...) as <level>.traversal.json
  navvehicle.py      land-vehicle path data (vehicle\pathdata.hpd): build / compare / parity / info (see "Vehicle path data")
  navwater_mesh.py   boat navigation mesh on a level's water, a side file for OPFOR's bot layer (prototype)
  navdomains.py      domain graphs and a route planner across foot / land vehicle / boat / aircraft (prototype;
                     research\CROSS_DOMAIN_ROUTES.md)
  navsample_offline.py  the in-game sampler's output computed from the level's Havok collision and height field, no
                     game launch; the stock MP batch (see "Standalone sampling")
tests\               NavKit's tests (run by the suite's run_tests.py; the suite README lists them)
src\                 (reserved, empty)
```

## Standalone sampling (2026-10-26)

`tools\navsample_offline.py` writes the same files as OPFOR's `[dev] navsample` (version 3, both capsule runs, 0.8 m
and 0.2 m over the detailed sectors) without starting the game, into `work\navsample_offline\`. NavKit's build takes
them unchanged (`navbuild.build` with those paths).

```
python tools\navsample_offline.py all <level>                   prepare + sample 0.8 / 0.2 + build + check
python tools\navsample_offline.py stockmp [level ...] [--steps prepare,sample,build,check]
python tools\navsample_offline.py validate <level> <in-game.bin> ...   on the in-game files' grids, measured per column
python tools\navsample_offline.py parity <level> ...            offline vs in-game build, both against DICE
```

**Geometry:** the terrain height field (core `terrain.py`) and every placed object and static-group member's
`*_Havok.res` (`hkpack.py`; offsets in the tool's header): physics systems -> rigid bodies (transform at +0xE0) ->
MOPP -> storage extended meshes (per subpart its *part*), list shapes, convex-translate, convex-vertices (hull from the
plane equations, grown by the 0.05 m convex radius), boxes. Measured choices (sp_004_b, against the in-game files):
- bodies named `h` and `c` (not `e`, a second, detailed layer);
- parts an object shows only once damaged are left out: the mesh names of a blueprint's `PartComponentData` health
  states below each component's full-health state (House_06_8x8's `ruin_downed` rubble) - offline surfaces that are
  real 97.7 -> 99.4 %;
- mesh triangles are hit from both sides, the normal facing the ray (in-game files have no downward normal on a mesh
  hit) - in-game surfaces found 97.6 -> 99.1 %, object surfaces 74.6 -> 92.7 %;
- the cast's 0.05 m sphere touches edges (thin sheets' tops) and terrain hits are reported 0.05 x (1 - ny) above the
  height field (fit within 1 mm per 0.1 of ny); a cast starting inside a convex piece or under the terrain hits at once;
- the capsule test: exact capsule-triangle intervals; the in-game blocked flags fit a capsule whose lowest sphere centre
  is 0.95 m (high run 1.15 m) over the surface, not navsample.cpp's nominal 0.68 / 1.0 m (blocked-flag agreement on
  sp_004_b 92 -> 99 %; steep terrain blocks from ~64 / ~69 degrees in game);
- **stock MP levels are read with their Package bundle over Dist** (`package_overlay`): every stock MP map's level
  partition and static groups are patched in Package (MP_002SR's partition is only there), and `levelobjects.py` reads
  Dist; the batch copies Package's `levels\` over the work copy before `navkit.py prepare`;
- the sampling box of a map without path data is its combat areas + 50 m (`combat_box`; the objects' extent is
  backdrop: MP_005 4.7 x 4.0 km against 0.9 x 0.9 km), sector offsets by navkit's rule (one sector below the box).

**Validation** (per in-game surface: one offline surface within 0.1 m in its column; walkable = clear and <= 40 deg):

| Level / run | Layers equal | In-game surfaces found | Offline surfaces real | Object surfaces found | Walkable agree | Blocked agree |
|---|---|---|---|---|---|---|
| sp_004_b 0.8 | 99.2 | 99.3 | 99.0 | 95.5 | 97.1 | 98.8 |
| sp_004_b 0.2 | 99.0 | 99.2 | 98.9 | 96.6 | 98.5 | 98.7 |
| sp_009 0.8 | 99.3 | 99.3 | 99.7 | 93.3 | 99.7 | 99.6 |
| sp_009 0.2 | 98.8 | 98.8 | 99.2 | 93.1 | 99.3 | 99.2 |
| ONS_MP_002 0.8 | 96.8 | 96.9 | 96.3 | 85.8 | 97.0 | 97.7 |
| ONS_MP_002 0.2 | 96.2 | 94.9 | 96.5 | 83.8 | 97.8 | 97.0 |

(ONS_MP_002 0.2: before the last cast-loop fix.) What is still off: ONS_MP_002's in-game files record faces under
object tops with downward normals (2,528 surfaces; inside rock meshes, at heights below the terrain - the game's cast
continues under the height field there, the offline one stops; making convex pieces in MOPP trees hollow found them
but added 7 % false surfaces, off: `CONVEX_IN_TREE_HOLLOW`); ONS_MP_002's work copy is read from Dist while the game
loads OPFOR's Package import (not overlaid, it is another worker's reference level); about 1 % of in-game object
surfaces have no collision within 0.3 m (objects not in the level partition).

**NavKit built from offline vs in-game samples** (same code, run `parity_offline_v1`, `work\navkit_offline\`):

| Level | Offline build vs in-game build: ground / extra | Against DICE, offline: ground / extra / connectivity / over / AI pos / pairwise | Against DICE, in-game |
|---|---|---|---|
| sp_009 | 99.7 / 0.2 | 99.98 / 2.0 / 100 / 0.5 / 100 / 73.4 | 99.97 / 2.2 / 100 / 0.5 / 100 / 69.5 |
| sp_004_b | 98.8 / 1.2 | 98.5 / 7.2 / 99.4 / 19.1 / 97.2 / 82.0 | 98.5 / 7.3 / 99.4 / 18.1 / 99.4 / 84.1 |

**The stock MP maps** (`navsample_offline.py stockmp`, 2026-10-26; every level name built on its own - a mode variant
has its own partition, objects and combat area; output `work\navkit_offline\<level>\pathdata.hpd`, report
`work\navkit_offline\stockmp.json`; not installed). `hpdcheck`, `hpdtool roundtrip` and `meshtrip` pass on all 20.
Reachability: the test harness (run `offline_stockmp`, infantry points only; spawns -> capture points / M-COMs):

| Map | Box (combat areas + 50 m) | Offsets | Detailed sectors | MB | Sample / build (s) | Spawns -> targets tested | Pairs reached | Targets reached | Islands (main %) |
|---|---|---|---|---|---|---|---|---|---|
| mp_001 | -290,-350 .. 210,380 | -4,-5 | 46 | 11.6 | 421 / 1410 | 68 -> 5 | 98.5 | 100 | 69 (96) |
| mp_001sr | -180,-260 .. 50,130 | -3,-4 | 44 | 5.7 | 307 / 736 | 16 -> 2 | 100 | 100 | 44 (94) |
| mp_002 | -160,-550 .. 350,430 | -3,-7 | 35 | 11.7 | 301 / 823 | 111 -> 10 | 100 | 100 | 100 (88) |
| mp_002sr | -10,-190 .. 240,290 | -2,-3 | 25 | 5.5 | 217 / 442 | 21 -> 2 | 100 | 100 | 42 (67) |
| mp_003 | -160,-270 .. 200,210 | -3,-4 | 16 | 8.0 | 183 / 749 | 50 -> 5 | 100 | 100 | 50 (78) |
| mp_004 | -250,-470 .. 170,430 | -4,-6 | 18 | 9.5 | 207 / 685 | 57 -> 6 | 100 | 100 | 64 (97) |
| mp_004sdm | -180,-40 .. 50,280 | -3,-2 | 16 | 3.8 | 139 / 386 | 19 -> 2 | 94.7 | 100 | 26 (94) |
| mp_005 | -540,-310 .. 340,580 | -7,-5 | 60 | 13.4 | 515 / 1364 | 41 -> 4 | 87.8 | 75 | 88 (95) |
| mp_005sr | -310,-80 .. 80,340 | -5,-2 | 58 | 6.9 | 240 / 785 | 23 -> 2 | 100 | 100 | 30 (96) |
| mp_006 | -370,-220 .. 920,430 | -5,-4 | 56 | 17.8 | 512 / 2496 | 104 -> 10 | 97.1 | 100 | 179 (95) |
| mp_006cq | -80,-180 .. 420,370 | -2,-3 | 48 | 10.4 | 463 / 1500 | 65 -> 3 | 95.4 | 100 | 86 (95) |
| mp_006sdm | 20,-40 .. 300,330 | -1,-2 | 45 | 6.0 | 247 / 576 | 30 -> 1 | 96.7 | 100 | 53 (95) |
| mp_007 | -230,-280 .. 300,30 | -4,-4 | 24 | 6.1 | 238 / 405 | 49 -> 3 | 100 | 100 | 56 (83) |
| mp_007sdm | -220,-240 .. 140,20 | -4,-4 | 20 | 4.8 | 186 / 309 | 19 -> 1 | 100 | 100 | 39 (80) |
| mp_008 | -320,-350 .. 390,-30 | -5,-5 | 27 | 9.5 | 246 / 540 | 57 -> 6 | 100 | 100 | 76 (90) |
| mp_009cq | -140,-80 .. 310,370 | -3,-2 | 37 | 8.7 | 191 / 436 | 50 -> 5 | 100 | 100 | 135 (58) |
| mp_009gr | -440,-440 .. 310,380 | -6,-6 | 45 | 16.1 | 256 / 856 | 138 -> 10 | 100 | 100 | 218 (68) |
| mp_009sdm | -100,-30 .. 290,330 | -2,-2 | 31 | 6.2 | 300 / 495 | 18 -> 1 | 100 | 100 | 75 (71) |
| mp_012gr | -270,-440 .. 320,540 | -4,-6 | 48 | 13.4 | 428 / 1006 | 89 -> 8 | 100 | 100 | 73 (95) |
| mp_012sr | -190,-150 .. 90,320 | -3,-3 | 36 | 4.8 | 303 / 423 | 25 -> 2 | 100 | 100 | 22 (96) |

Prepare took 25-160 s per map; in all about 5 hours on one process. Open: MP_005's US/RUS base capture point
"RUS_Base" stands 10 m over walkable ground (60 m from a node within the harness's height tolerance) - a point-height
question, the ground there is sampled walkable; capture points the harness calls airborne (MP_006CQ 2, MP_007 2, ...)
are not tested; MP_002SR's spawn -> target battery routes 0 of 42 pairs through the path test while the route graph
connects all 42 (to look at); the battery's random pairs come from NavKit's own cells on these maps (no DICE file), so
its low shares on MP_009CQ / GR (36 / 50 %) measure the many small islands (roofs, props), not missing routes.
**Before a play test:** each map needs a `Precomputed/Infantry/SectorOffsets` asset with the offsets above (OPFOR's
`apply_sector_offsets.py`; the runtime uses 0,0 without one - the harness's offsets check fails for that reason) and
the AI layer (`moh-stockmp-ai`: HasAI, AI templates, orders).

**Integration (not done - navkit.py / navbuild.py belong to another worker):** `navkit.py sample --offline` calling
`navsample_offline.sample_level` (and `all --offline`), `Project.samples08` / `build` looking in
`work\navsample_offline\` when asked; `prepare` reading Package over Dist (`package_overlay`) and taking the combat
box for levels without path data; the window's "Sample" step with an offline choice.

## Vehicle path data (2026-10-26)

BC2's land vehicles use their own file, `ai\levels\<level>\vehicle\pathdata.hpd` - the infantry file's format with
DICE's `AI/Pathfinding/Vehicles` settings (pitch 1 m, radius 2.5 m, height 4 m, DistEdgeMax 50). What DICE's 16 vehicle
files show and the rules found: spec section 2.2. `tools\navvehicle.py` builds one from the same inputs as the
infantry build (in-game samples, level objects, water, level XML), offline, into `work\navvehicle\<level>\`:

```
python tools\navvehicle.py build sp_04                  vehicle file of a level (sectors: DICE's when it has a file)
python tools\navvehicle.py compare <dice.hpd> <ours.hpd> ground, extra ground, areas, graph, connectivity
python tools\navvehicle.py parity --save <run>          build + compare on every level with DICE vehicle data
python tools\navvehicle.py info <pathdata.hpd>          sectors, offsets, pitch / radius, areas, D / F, nodes
```

- **Walkability (1 m grid, resampled from the 0.8 m samples along each surface's ground plane):** slope by the normal
  <= 26 deg (`SLOPE_MAX`); no obstacle within 2.5 m (`CLEARANCE`): objects rising more than 0.4 m (StepMax) and at most
  4 m above the local ground plane, soldier-capsule-blocked samples (thin walls), terrain only from a 0.8 m rise
  (`TERRAIN_RISE`); ExcludeFromPathfinding objects crushed (`USE_EXCLUSIONS`), dynamic props and soldiers absent,
  ForbidPathfinding surfaces not ground, ground over 1.6 m under water out.
- **Seeds:** land-vehicle spawns (VehicleSpawnEntityData; aircraft, boats and fixed weapons by blueprint name are
  left out) and points every 2 m along the level's terrain splines (`SEED_ROADS`, DICE's BuildRoads). **Flood per
  sector** (`FLOOD = 'sector'`): a sector gets ground only from seeds in its own window, as DICE's files show.
- **Mesh and graph:** navbuild's pipeline (`navbuild.stage`) at pitch 1, 50-sample tiles, nodes 2 m over the ground,
  area range ground .. +3.2 m, route edges to 50 m, D / F as navbuild builds them.
- **Validation:** `hpdcheck` passes and `hpdtool roundtrip` / `meshtrip` are byte-exact on all 14 builds;
  `tests\test_vehicle.py` (quick), `tests\test_vehicle_level.py` (game: ONS_MP_002 with parity floors).

**Parity against DICE's vehicle files** (run `tonight_v1`, 2026-10-26, `work\navvehicle\parity\`; DICE's sector set,
PC samples; ONS_MP_002 / 004 compare PC geometry with DICE's console data):

| Level | Ground % | Extra % | Connectivity | Islands (25+ nodes) ours / DICE |
|---|---|---|---|---|
| ons_mp_002 | 87.3 | 49.0 | 58.7 | 2 / 1 |
| ons_mp_004 | 85.8 | 105.0 | 43.4 | 5 / 1 |
| sp_003_b | 81.4 | 36.5 | 91.5 | 3 / 4 |
| sp_004_b | 30.2 | 48.8 | 38.7 | 1 / 1 |
| sp_005 | 90.7 | 37.8 | 95.9 | 2 / 2 |
| sp_005_b | 86.9 | 168.8 | 38.7 | 9 / 3 |
| sp_006 | 94.5 | 9.4 | 32.0 | 8 / 2 |
| sp_007 | 73.2 | 41.7 | 41.2 | 12 / 9 |
| sp_008 | 90.2 | 39.2 | 100.0 | 1 / 1 |
| sp_008_b | 92.5 | 638.1 | 0.0 | 1 / 1 |
| sp_01 | 93.5 | 28.5 | 21.7 | 8 / 2 |
| sp_02 | 65.2 | 25.1 | 100.0 | 4 / 1 |
| sp_03 | 19.0 | 61.6 | 57.9 | 2 / 4 |
| sp_04 | 90.7 | 39.7 | 93.3 | 5 / 3 |
| **mean** | **77.2** | **95.0** | **58.1** | |

Areas: almost none identical (DICE splits vehicle ground into many small areas by a rule not found yet); route nodes
within 2 m of DICE's 16 %. sp_008_b's "extra" is relative to DICE's tiny file (2,428 cells). Starting point for
comparison: spawn seeds only and one level-wide flood gave sp_01 66 / 7 and sp_04 83 / 59 (ground / extra).

**Open:** the route graph splits where DICE's is one network (sp_006: 22 islands, DICE 7; ground 94.5 % - the splits
are at narrow crossings of sector seams, in navbuild's graph joining, not missing ground); which splines are roads;
sp_004_b / sp_03 (ground DICE seeds from something other than spawns and splines); DICE's area division; the sector
set of a new map (every seeded sector).

**Integration plan (not done - navbuild / navkit.py were being changed by another worker):**
1. `navbuild.stage`: accept a ready grid - `stage(..., grid=(samples, keep, gx, gz))` skipping `navsample.load` and
   `navwalk.walkable` - and a `profile` dict for the settings `navvehicle.vehicle_settings` now swaps in (PITCH,
   TILE 50, RESOLUTION, RADIUS, DIST_EDGE_MAX, Y_OFFSET, AREA_Y_MIN / MAX, NODE_COVER, ORDER_NODES off, DETAIL_STEP);
   `set_pitch(1.0)` must give TILE 50 (today `round(40 / p)` = 40). Then `navvehicle.stage_on_grid` calls it directly
   instead of substituting the two module references.
2. `navbuild.write_file(graphs, out, log, t0, dist_edge_max=...)` (it reads the global today).
3. `navkit.py build <level> --vehicle` (and `all`): after the infantry build, `navvehicle.build_level(root, lv,
   <work>\navkit\<lv>\vehicle\pathdata.hpd, sectors='all' or 'reference')`; `install` / `restore` to copy it to
   `ai\levels\<lv>\vehicle\pathdata.hpd` keeping DICE's original as `pathdata.hpd.opfor-orig` (as infantry), and the
   level's `Precomputed/Vehicle/SectorOffsets` written with the build's offsets (OPFOR's apply_sector_offsets.py).
4. `navregress.py`: a vehicle column set from `navvehicle.compare` (saved run `tonight_v1` as the first reference).
5. The window: a "Vehicle data" checkbox on the build step.

**Play test (ON HOLD, needs a launch):** install a NavKit vehicle file on a level with AI drivers (sp_005's tanks and
Humvees, sp_04's trucks / Vodniks, or ONS_MP_002 / 004's T-90s) beside DICE's infantry file, with
`Precomputed/Vehicle/SectorOffsets` matching; check the file loads, AI vehicles get routes and follow them (probe the
vehicle pathfinder's go-to results as for infantry), no crash at level exit; then DICE's vehicle file in the same
launch for comparison.

## Route islands (2026-10-26)

ONS_MP_004's route graph had 109 islands against DICE's 42, and the test harness's spawn -> target battery routed
only 67 % of DICE's pairs ("islands"). The diagnosis (spec §4.5, "Route islands"): every DICE route edge checked on
NavKit's own ground - 13,026 edges within one NavKit island, 1 between two; **no island boundary at a sector seam**.
Of the 108 extra islands, 84 stand on ground DICE does not have (object tops, crates, M-COM cases, roofs, rooms - seeded
from objects on the sector perimeter lines or from the normal build's ground in the pitch-0.2 pass), 15 are islands
DICE has as well, 3 are DICE ground cut by steep cells (the beach at (-24, 77, -379)) and 4 are ground the group
joining missed inside one sector. The failed routes were the nearest-node join: a pocket's node beside or above the
floor is the nearest node of a point on the main ground (two M-COM targets: a 2-node pocket 0.6 m above the floor at
(-55, 82, -156), an 18-node roof 3-4 m above the target at (-47, 84, 103)).

**`SHADOW_ISLANDS`** (`tools\navislands.py`, on; called by `navbuild.build` before the file is written): a route
island under 25 nodes (`SHADOW_MIN_NODES`), mostly on objects (`SHADOW_OBJECTS_ONLY` - DICE seeds terrain only, and
every small island DICE has as well stands on terrain), with no soldier spawn / AI order / objective on its floor,
within 8 m across and 4 m in height of a node of an island of 25 or more (`SHADOW_XZ`, `SHADOW_DY`), loses its edges
and nodes. Its areas stay - the ground measures do not move. ONS_MP_004: 109 -> 41 islands, spawn -> target battery
67 -> 100 % of DICE's pairs, connectivity 97.37 -> 97.90, AI pairwise 40 -> 100 %.

Not fixed here: the steep cut (`STEEP_TERRAIN`, off - spec §3), the 4 group-joining misses (the joining walks one
neighbour surface per column), and the vehicle graph's seam splits (`navvehicle.FLOOD` floods each sector's window
on its own - a vehicle rule, not navbuild's graph joining).

Regression, the same build with the rule off -> on (2026-10-26; battery = harness spawn -> target pairs routed, of
DICE's): ground, extra ground and AI coverage unchanged on every level, connectivity the same or higher on every
level; the cost is fewer DICE nodes with a NavKit node within 2 m (0.0-0.6 points: the dropped pocket nodes stood
next to DICE nodes) and over-connection +0.08 on sp_004_b.

| Level | Islands (DICE) | Connectivity | AI pairwise | Battery | Nodes near |
|---|---|---|---|---|---|
| ons_mp_004 | 109 -> 41 (44) | 97.37 -> 97.90 | 40.0 -> 100 | 67.0 -> 100 % | 61.15 -> 61.06 |
| ons_mp_002 | 113 -> 68 (30) | 99.86 -> 100 | 100 -> 100 | 98.0 -> 98.0 % | 55.02 -> 54.94 |
| sp_004_b | 84 -> 54 (69) | 99.39 -> 100 | 84.1 -> 84.1 | 75.0 -> 75.0 % | 57.20 -> 56.95 |
| sp_009 | 13 -> 3 (3) | 100 -> 100 | 69.5 -> 71.8 | - | 41.08 -> 41.08 |
| sp_02 | 155 -> 107 (149) | 99.16 -> 99.50 | 92.3 -> 96.1 | 80.3 -> 86.4 % | 44.09 -> 44.04 |
| sp_04 | 313 -> 245 (306) | 99.47 -> 99.61 | 94.7 -> 97.3 | 67.5 -> 72.5 % | 40.75 -> 40.71 |
| sp_007 | 1200 -> 1117 (1116) | 97.83 -> 98.43 | 86.8 -> 92.7 | 66.7 -> 69.2 % | 49.54 -> 49.49 |
| sp_01 | 520 -> 435 (514) | 98.57 -> 98.67 | 94.6 -> 96.4 | 100 -> 100 % | 46.34 -> 46.27 |
| sp_03 | 970 -> 918 (1060) | 98.10 -> 98.47 | 97.9 -> 97.9 | 100 -> 100 % | 43.50 -> 43.46 |
| sp_003_b | 956 -> 914 (1225) | 93.36 -> 93.67 | 69.9 -> 70.3 | 66.7 -> 66.7 % | 40.55 -> 40.52 |
| sp_005_b | 901 -> 767 (791) | 99.18 -> 99.37 | 94.5 -> 95.6 | 100 -> 100 % | 43.04 -> 43.00 |
| sp_008 | 468 -> 348 (98) | 98.48 -> 98.65 | 95.2 -> 96.2 | 68.4 -> 68.4 % | 60.43 -> 60.14 |
| sp_008_b | 251 -> 164 (36) | 98.29 -> 98.70 | 98.2 -> 98.3 | 66.7 -> 66.7 % | 62.59 -> 61.96 |

## D lines and F cells (2026-10-26)

DICE's rule, measured on its files (spec §4.5, "D lines and F cells - the generator rule"): a side between two cells
of the **same area** whose heights differ by more than the link step (0.6 m) is not a link. Such a side is a **D line**,
stored once per cell (two lines facing each other, the owning cell on the right, vertex heights the mean of the owner's
cells along the line); a cell with **both sides along x (or z) broken** is an **F cell**, and the F cells of one axis
form polygons stored twice, -axis then +axis, each corner's height from the cells on that side. These sides are not
outline. The runtime's straight-move test reads them (spec §4.5); route edges in DICE's files never cross a D line.

- **`navbuild.DETAIL_FEATURES`** (on; `navkit.py build --no-detail` / `navbuild.py --no-detail` for the output
  before): `tools\navdetail.py` per area, called by `navbuild.detail_pass` before the outline is emitted; broken sides
  leave the outline only in pairs (every boundary stays closed - `hpdcheck.py` also checks D / F structure now).
  `DETAIL_STEP` 0.6 m at pitch 0.8, 0.3 m (NavKit's own link step) at pitch 0.2.
- **`navbuild.DETAIL_BLOCKS_EDGES`** (on): route edges do not step across a D side (straight or diagonal), as in DICE's
  graph; F cells may be crossed (DICE's edges cross F polygon sides on 0.4-0.6 %).
- **Parity** (`tools\navdf.py`; its measures are in every `navparity` / `navregress` row as `df_*`): see the table
  below. The rule itself, run on DICE's own areas with NavKit's sample heights, finds DICE's normal-pitch D sides at
  76 % precision / 46 % recall (sp_004_b; the sides it misses are 0.5-0.6 m steps where the samples and DICE's heights
  differ by a few cm, sides of 45-degree cut half cells - an unexplained case - and cells the oracle could not
  sample). On NavKit's own builds the match is bounded by area parity: a D line needs both cells in one area, and
  NavKit's areas on steep ground differ from DICE's.
- **Detailed sectors - terrain only, and off by default:** in DICE's detailed sectors D sides lie only between two
  terrain cells (objects never: 24 of 401,562 sides) with a step over 0.173 m (on DICE's own areas: precision 25 %,
  recall 86 %; spec §4.5). Implemented (`DETAIL_STEP` 0.175, `DETAIL_TERRAIN_ONLY`, no F there - `DETAIL_F`), but
  **off** (`DETAIL_IN_DETAILED`): NavKit's detailed areas join steep terrain DICE's do not, and the rule wrote 10-20x
  DICE's lines (detailed D recall 0.1 -> 41-69 %, precision 2.5-3 %; sp_004_b, sp_01, ONS_MP_002), lowering the
  all-pitch F1 on all three levels and keeping route edges off thousands of extra lines.
- **Corner cuts and L-shaped lines (`navdetail.CUT_CORNERS`, `CHAIN_TURNS`, on):** a cell owning D on two perpendicular
  sides gets the diagonal across it, its neighbours one L round the corner (DICE: 409 of 453 such corners; the 3-8
  point lines). Unit-side parity unchanged; DICE's 3+ point lines found on its own areas 0 -> 4.5 % (precision 31 %) -
  the step rule finds few of the corners DICE cuts.
- **Half cells of 45-degree cuts (`DETAIL_HALF_CELLS`, off):** the cut cell's legs follow the plain rule in DICE's data
  (352 of 389), but NavKit cuts different corners (only its bridges): on NavKit's builds the legs cost D precision at
  pitch 0.8 (sp_01 44.6 -> 27.9 %, sp_004_b 59.3 -> 44.0 %) for 0-3 points of recall.
- **ON HOLD - needs a play test:** whether bots stop cutting corners next to D lines and step correctly at F cells;
  whether the point -> route node resolver (a straight-line test on the mesh) still finds a node for every order
  position (NavKit's nodes do not yet take D lines into account when they are placed); straight moves across stairs
  in detailed zones.

Run `regression_df_v1` (2026-10-26, all 15 levels; `navparity.py show regression_df_v1`). D lines / F records in the
files, then precision / recall (%) of D sides and F cells at pitch 0.8 (normal sectors) and over all sectors:

| Level | D DICE / NavKit | F DICE / NavKit | D normal P / R | F normal P / R | D all P / R | F all P / R |
|---|---|---|---|---|---|---|
| sp_01 | 26,292 / 15,166 | 1,780 / 1,988 | 44.6 / 25.2 | 21.6 / 39.1 | 38.3 / 22.9 | 21.0 / 38.4 |
| sp_02 | 12,932 / 9,588 | 1,600 / 1,766 | 40.4 / 32.2 | 26.7 / 42.8 | 30.7 / 21.9 | 26.0 / 37.5 |
| sp_03 | 68,244 / 40,774 | 5,846 / 5,660 | 50.2 / 30.9 | 27.4 / 39.7 | 45.7 / 28.5 | 27.3 / 38.7 |
| sp_04 | 33,769 / 23,712 | 4,260 / 4,568 | 49.4 / 31.5 | 35.6 / 38.4 | 39.1 / 28.0 | 35.1 / 34.8 |
| sp_003_b | 79,073 / 51,612 | 6,728 / 7,198 | 50.0 / 33.2 | 31.8 / 48.3 | 45.8 / 31.3 | 31.6 / 46.9 |
| sp_004_b | 4,105 / 7,384 | 496 / 562 | 59.3 / 27.1 | 49.0 / 28.3 | 9.8 / 19.1 | 34.5 / 25.5 |
| sp_005 | 84,060 / 59,396 | 6,694 / 6,050 | 53.7 / 32.3 | 31.5 / 33.2 | 40.0 / 28.6 | 29.3 / 31.2 |
| sp_005_b | 80,319 / 50,008 | 5,434 / 4,408 | 54.7 / 31.3 | 30.0 / 33.3 | 41.8 / 26.1 | 28.7 / 29.9 |
| sp_006 | 48,491 / 45,788 | 6,608 / 8,278 | 45.3 / 36.8 | 31.9 / 51.2 | 30.2 / 29.1 | 31.2 / 49.0 |
| sp_007 | 40,073 / 17,994 | 2,396 / 1,564 | 53.2 / 24.2 | 24.3 / 27.1 | 44.6 / 20.9 | 23.6 / 24.2 |
| sp_008 | 6,434 / 8,444 | 374 / 280 | 57.8 / 23.8 | 41.0 / 19.6 | 16.3 / 22.1 | 29.2 / 18.0 |
| sp_008_b | 1,202 / 5,002 | 36 / 220 | 34.9 / 11.7 | 27.3 / 15.8 | 0.5 / 2.4 | 1.7 / 8.3 |
| sp_009 | 58 / 102 | 0 / 14 | - | - | 0 / 0 | 0 / - |
| ONS_MP_002 | 9,619 / 5,576 | 914 / 596 | 49.7 / 32.1 | 25.5 / 32.4 | 16.9 / 10.6 | 23.8 / 19.2 |
| ONS_MP_004 | 5,839 / 5,612 | 336 / 102 | 12.1 / 20.3 | 6.7 / 12.5 | 1.4 / 1.3 | 1.6 / 0.2 |
| **mean (14, without sp_009)** | | | **46.8 / 28.0** | **29.3 / 33.0** | **28.6 / 20.9** | **24.6 / 28.7** |

DICE's normal-pitch D heights are matched to a median 1 cm. Against the reference run `regression` (`regression_det_v8`)
nothing else moved beyond 0.3 points: ground, extra ground and AI coverage identical on all 15 levels, connectivity
equal or higher (sp_003_b 93.26 -> 93.38, sp_005 99.15 -> 99.25), nodes_near higher on 14 levels; over-connection
+0.12 to +0.20 on sp_003_b, sp_03, sp_005_b, sp_005 (-0.28 on sp_01), and the edge-cost match (measured on a few
dozen matched edges) one or two edges lower on sp_003_b, sp_03, sp_005_b - both from `DETAIL_BLOCKS_EDGES` (sp_003_b
rebuilt with it off gives the reference's 29.16 / 92.68 exactly). The blocking was accepted (2026-10-26):
`regression_df_v1` is now the reference `regression` (the one before stays as `regression_det_v8`).

- **Infantry only:** DICE's vehicle files do not follow this rule (D lines mostly single, a quarter of their segments
  diagonal, F records in eight directions without twins) and their route edges cross D lines on 0.58 % of edges
  (infantry 0.004 %). Vehicle builds (`navvehicle.py`) write no D / F and do not block edges (`navvehicle.DETAIL` off,
  `DETAIL_BLOCKS_EDGES` off in `vehicle_settings`); spec §4.5.

## Detailed zones, ladders and walkability rules (2026-10-21 / 22)

All 15 levels with DICE path data, rebuilt and measured on PC ground (`navregress.py`, run `regression_det_v8`,
2026-10-24, now the default reference `regression`): **ground 98.2 %** of DICE's, **extra ground 8.3 %**,
**connectivity 98.5** (of the routes DICE's data allows, the share NavKit's allows), over-connection 15.3 % (routes
only NavKit's allows: open slopes, stairs, steps and ladders - see below), **AI coverage 98.5** (AI order positions on
an area; campaign levels 99.0) and pairwise AI routes 86.0. **Every area boundary is closed** on all 15 builds (0 open
areas; before the exact bridge tracing below, 1.7 - 3.1 % of the areas on sp_004_b / sp_01 were open, DICE has none).
**M-COM targets:** all 14 Onslaught attack orders on an M-COM are on an area (DICE 14), with the objective floor
rule below; AI order coverage on both Onslaught maps 100 %.

- **Exact bridge tracing (`navbuild.BRIDGE_UNION`, 2026-10-23):** an area's 45-degree bridges are the exact union of
  the quarter cells they cut, traced as one outline, in place of clipping each bridge on its own (`clip_bridges`,
  which left gaps where bridges met: open boundaries, and walkable ground in no area). Ground 98.12 -> 98.22 %,
  extra 8.36 -> 8.31; checked by `tests\test_bridge_union.py` (600 random patterns; the old method fails 107) and by
  `hpdcheck.py` on every regression build.

- **The default pipeline:** `navkit.py sample` (and `all`, and the window's buttons) samples the level at 0.8 m, chooses
  its detailed zones from the level alone, then samples those at 0.2 m (`DETAILED_BUDGET_MS` = 400 ms of sampling per
  frame: a 0.2 m run is ten times the columns and must end before a round, or a campaign level with an idle player,
  does). A run whose level ends first is reported, not waited out. `navkit.py build` builds the detailed sectors at
  0.2 and merges them with the normal build.
- **Detailed zones (pitch 0.2):** chosen by `tools\detailed.py` without DICE's data - sectors near an object whose
  blueprint sets `NeedsDetailedPathfinding`, and sectors with 100+ stacked-floor columns in the 0.8 m samples (93 % of
  DICE's choice on 14 levels). Built with 6 x 6 tiles, link step 0.3 m, a Gabriel-pruned route graph and coverage
  nodes every 4 m.
- **Ladder links:** `navkit.py prepare` writes `ladders.json` (`tools\ladders.py`: placed ladder objects and their
  collision boxes); the build seeds the far end of a ladder whose near end is walkable and links a node at each end.
  DICE's path data has none; the game puts any soldier who walks into a ladder on it.
- **Walkability rules added 2026-10-22** (`tools\navwalk.py`, each with a switch and its measurements in the code):
  - `LIP_WIDTH`: at 0.2 m, a band of up to two samples steeper than 40 deg between walkable ground (a lip the 0.8 m
    step rule stepped over) is walkable; `LIP_OBJECT_RISE`: the same on objects when the band is within 0.3 m of the
    floor on both sides (door sills, thresholds) - steeper than 55 deg only between flat floors (`LIP_OBJECT_FLAT`
    15 deg: a rock mesh meeting sloped terrain is no sill). sp_007's north strip, sp_009's aircraft aisles (sp_009
    connectivity 90.9 -> 100).
  - `DYNAMIC_FLOOR`, `DYNAMIC_ABSENT_HEADROOM`: dynamic props (and `PdtWhenAlive` doors) are absent for path data -
    the floor under a prop lying on it counts, and neither the prop nor rays grazing its vertical faces count as a
    ceiling. sp_003_b's monastery gate, sp_009's aircraft doors.
  - `ROOF_PRUNE`: roof islands nothing reaches are dropped - an island with no soldier spawn, AI order position or
    ladder end, mostly on objects with ground 2 m or more below, and no reached ground within 2 m at its own height.
    Ground-level islands stay (DICE keeps those). sp_008 extra ground 29.5 -> 22.7 %, sp_01 12.2 -> 9.6 %.
  - Not adopted: a slope test from heights instead of normals (`STEEP_TERRAIN`, off - DICE cuts at 40 deg by the
    normal: 15 % of 40-45 deg terrain covered), pruning islands by size or by combat area (DICE keeps small islands
    and ground outside its combat areas).
- **Collision of assets named `<name>_Havok_Havok.res`** (`levelobjects.havok_index`): indexed under the blueprint's
  name; before, such an object had no collision in NavKit (sp_009's animated aircraft door stayed solid).
- **navparity** clips DICE's reference to the level's terrain (ground beyond it does not exist on PC), leaves out
  DICE's route nodes with no PC ground within 1 m of their height (`stale_nodes`, column *stale_reference_nodes*:
  data built before late geometry - sp_03's only valley-to-plateau links run under a cliff mesh added afterwards),
  matches nodes within 1 m of height, and reports ground reached by ladder as its own column (*ladder %*), not as
  extra ground.
- **"Up" border portals** (a border portal whose direction is (0, 1, 0)): about half of DICE's border portals
  (50-58 % on sp_004_b, sp_01, ONS_MP_002), none of NavKit's. Not a kind of portal: they lie where DICE's ordinary
  ones lie (the sector margin, along the line), the neighbour sector's area is across them as for the others (100 %),
  and along one border line consecutive pieces of the same geometry alternate between (1, 0, 0) and (0, 1, 0); the
  y slot also holds 0.38, 0.92, 0.71 and -2.0. A default or left-over value in DICE's generator; NavKit writes the
  outward direction, and its files route across sector borders in game. (DICE also splits border portals per 0.8 m
  cell - 3-4x NavKit's count, the same geometry.)
- **Anomalies against DICE** (Navigation Studio's overlay counts, 15 levels, 2026-10-22 - DICE / NavKit): soldier
  spawns on islands under 25 nodes 147 / 72, AI orders without a route node 768 / 686, sliver areas 77,996 /
  62,708, lonely route nodes 5,081 / 10,011, islands under 25 nodes 5,684 / 6,390. NavKit has fewer of the two that
  stop bots; more lonely nodes, and more slivers on levels with many detailed sectors (clutter at 0.2 m).
  - *Lonely nodes* (no other node within 12 m) are node spacing, not junk: on sp_006 2,634 of NavKit's 2,638 are on
    islands of 25+ nodes - normal sectors get route nodes only at area centres and portals. Proposed: coverage nodes
    in normal sectors too (`NODE_COVER`, about 8-10 m), tuned by regression.
  - *Sliver areas* (under 5 cells, no portal) are mostly 0.2 m clutter (sp_006: 7,712 of 9,060 in detailed zones;
    DICE 1,160): one-area islands nothing routes onto. Proposed: drop isolated areas of a few 0.2 m cells unless a
    spawn, order or ladder anchors them.
  - *Border-to-border duplicate nodes* (two border nodes at one spot joined by a zero-length edge): none any more -
    sp_01, sp_004_b and ONS_MP_002 have 0, as DICE's (shared corner nodes, 2026-10-20).
- **Missing ground** (DICE's walkable ground NavKit lacks, about 1.9 %; five levels classified, 2026-10-23): 74-97 %
  of it is a rim beside NavKit's ground. By what NavKit's samples show there: terrain steeper than 40 deg 56 % (DICE's
  areas reach a cell past where the samples turn steep), walkable but not kept 22 % (on sp_01: DICE's own small
  unreached islands of 2-23 nodes 64 %, cells outside their own unclosed outline 22 % - fixed by `BRIDGE_UNION`),
  no surface at DICE's height 17 % (half of it under objects - on ONS_MP_002 vegetation; making vegetation passable
  was tried and dropped: about 0.1 % of DICE's ground to gain, and both versions lost ground), no room 3.5 %.
- **Closed area boundaries** (`navbuild.BRIDGE_UNION`, 2026-10-23): DICE's files have no area whose outline, portals
  and border portals leave an open end; NavKit's had them in 1.7-3.1 % of areas (sp_004_b, sp_01), all at cell
  corners where a 45-degree bridge piece ends - overlapping bridges were clipped asymmetrically, and an area's cells
  fell outside its own outline. The bridges' outline is now the exact union of the half-cells they cover.
- **M-COM targets on the Onslaught maps** (2026-10-23): 14 of the 15 AI attack orders that sit on an M-COM
  (`mpobjective`, within 3 m; ONS_MP_002 7 of 8, ONS_MP_004 6 of 6) are on no area in NavKit's file - DICE's console
  file has ground at all 15, its layout having no object there. The M-COM is `ForbidPathfinding` (its top is not
  floor - DICE's campaign files carve all 24 of theirs); the hole at its centre is its collision (no room for the
  soldier), not the flag, so clearing the flag would not help. The game's destination query fails there and units
  stop short. No campaign order sits on an M-COM.
  **Objective floors (`navwalk.OBJECTIVE_FLOOR`, 2026-10-24):** an AI order position (the level's `AreaOrder`
  entities, `area_orders.json` from `levelobjects.py` - not the area-geometry points of `order_points.json`) that lies
  on a forbidden object keeps the floor under it, out to `OBJECTIVE_REACH` (1.5 m): the surfaces with the object's
  top 0.3-2 m above, and the floor at that height the object blocks by clearance. The floor under an M-COM counts as
  on the object itself (its collision has a bottom face on the ground) - the rule takes it all the same; the top stays
  forbidden. ONS_MP_002 7 of 8 targets on an area (was 1), ONS_MP_004 6 of 6 (was 0, now at DICE's distances); AI
  coverage 30 -> 90 and 0 -> 100; campaign levels unchanged. `tests\test_objective_floor.py`.
  A first version took the area-geometry points: it fired on campaign levels at objects under area corners
  (sp_02's AI coverage rose 98.5 -> 98.8 from it) and missed the M-COMs - DICE does keep ground at some AI targets on
  forbidden objects beyond M-COMs, which is worth a separate, measured rule.
- **Route node on a border node** (`navbuild.build_graph`, 2026-10-24): an area node is never placed on a border
  node's position - a shared node carried from the other pitch's build is attached to ground up to 1 m away, so its
  surface could differ from the one under it; sp_007 had a zero-length edge there (`hpdcheck`), sp_005 a coincident
  pair without one.
- **Destruction:** path data describes the intact level (DICE's and NavKit's). Objects carry `DisablePathfinding`:
  `PdtNever` (32,854 placed objects on the 15 levels), `PdtWhenAlive` (729: doors, gates, scripted walls - absent for
  path data; NavKit honours it), `PdtWhenDestroyed` (556: houses, factories, walkways, rope bridges - inferred: the
  runtime switches their path data off once destroyed), `PdtWhenPartDestroyed` (13); level settings
  `DestructibleLocalPathfindingParts`, `PathObjectCount`, `MaxLocalPathfindingObjects` point to runtime handling.
  Not yet checked that the switch-off works on NavKit's files (a play-test item). Openings made by destruction are
  not routes in either; precomputed "breach links" for OPFOR's bot layer are on the TODO list (OPFOR
  `DEVELOPMENT_NOTES.md`).
- **Still open:** sp_003_b (connectivity 93.3: a rocky gully whose rough terrain fails the slope test at 0.2 m almost
  everywhere); soldier spawns on small islands where NavKit has more than DICE (sp_008_b, sp_02, sp_003_b, sp_006,
  sp_01, a few each); ONS_MP_002's power-line pylons and a cliff top that roof pruning keeps (they stand beside each
  other at one height); the last M-COM target (ONS_MP_002, 0.87 m from walkable ground); lonely nodes and slivers
  (above); D lines and F cells in detailed zones and beside 45-degree cut half cells (generated since 2026-10-26, see
  above; those two cases do not follow DICE); vehicle path data (built offline since 2026-10-26, "Vehicle path
  data" above; its open items there); and play tests of the current builds (M-COM arming, ladder links and
  destroyed houses included).

## Plan and status (2026-10-26)

The newest work is in the sections above (2026-10-26): standalone sampling, vehicle path data, route islands, D lines
and F cells. None of it is play-tested yet. The steps below keep their history.

1. **`.hpd` format – done.** Header, sector table, per-sector graph and runtime mesh fully decoded
   (spec §4); `hpdtool.py` parses every campaign file.
2. **Byte-exact rewrite – done.** `hpdtool.py roundtrip` / `meshtrip` re-emit every shipped file and
   all 7,944 sector meshes identically.
3. **Builder behaviour – done.** Spec §3: stages, the sampling grid, area cuts, graph rules,
   auto-seeding, the walkability rules (§3.7), structures and doors (§3.8), and the rules found on the
   other campaign levels (§3.9).
4. **Generator – runs on all 13 campaign levels and the Onslaught maps (`tools\navbuild.py`).**
   - Full pipeline: walkable surfaces from two in-game sampling runs (normal and high capsule) plus the
     level's objects (`levelobjects.py`), 100 m sectors (level `SectorOffsets`; without one, DICE's rule:
     one sector below the path data's lowest extent), 3 × 3 tiles of 50
     samples, single-layer 8-connected areas, outlines with 45° bridges, portals (B), border portals (C),
     neighbour links, route network (area, portal and shared border nodes; walkable edges up to 15 m;
     groups joined through string-pulled waypoints; no lone nodes), written as `pathdata.hpd`. Seeds:
     sector perimeter lines and soldier spawn points.
   - Rules added 2026-10-17 to 10-19 (details and measurements: spec §4.5):
     - **areas** are whole pieces of a tile joined by DICE's raw 0.6 m terrain step (`AREA_LINK`); no
       20 m bisection;
     - **edge sectors** (on the sector grid's minimum X / Z) start their tiles and their rectangle at
       their own edge (`EDGE_SECTOR_TILES`, `EDGE_SECTOR_RULE`);
     - on a level with DICE path data the ground is **bounded to DICE's extent** (`CLIP_TO_REFERENCE` -
       a bound for comparison, not a rule found in the level data; a new map keeps its sampling box);
     - **portals** stand on both of their areas; a piece whose target sees that side as its own interior
       becomes outline (`SYMMETRIC_PORTALS`, `ONE_WAY_PORTALS`);
     - **soldiers** standing in the level while it was sampled are not in the path data
       (`navwalk.CHARACTERS_ABSENT`);
     - each area's **height range** follows DICE's convention: lowest ground to highest ground + 1.52 m.
   - **In game:** sp_01's file loads and the AI walks on it (play test 2026-10-15). sp_004_b (play tests
     2026-10-19, same launch as DICE's file, only the `.hpd` differing): the file loads, the pathfinder
     finds paths (394 of 400 requests), but **the friendly squad does not follow them**, where with DICE's
     file it does. After a found path the game checks whether its last point equals the requested destination
     exactly, and further checks decide whether it is followed (`DEVELOPMENT_NOTES.md` in Project OPFOR);
     measuring how NavKit's paths end, and which check they fail, is the next step. Leaving the level with NavKit's file also crashes at shutdown (a Kynapse block released after its memory manager was
     destroyed; not with DICE's file) - a separate open bug.
   - Accuracy against DICE's files (`ordercheck.py`: AI order / patrol positions on the areas, and the
     pairwise route check), 2026-10-15:

| Level | DICE-covered order positions also on NavKit's areas | Pairwise route check | Main route network (NavKit / DICE) |
|---|---|---|---|
| sp_01 | 618 / 633 (97.6 %) | 89.5 % | 639 / 607 |
| sp_02 | 241 / 260 (92.7 %) | 84.2 % | 313 / 362 |
| sp_03 | 343 / 353 (97.2 %) | 92.4 % | 374 / 232 |
| sp_04 | 500 / 526 (95.1 %) | 89.5 % | 582 / 593 |
| sp_003_b | 600 / 635 (94.5 %) | 87.3 % | 686 / 712 |
| sp_004_b | 169 / 181 (93.4 %) | 86.4 % | 185 / 192 |
| sp_005 | 304 / 309 (98.4 %) | 97.2 % | 308 / 312 |
| sp_005_b | 671 / 683 (98.2 %) | 94.8 % | 722 / 728 |
| sp_006 | 258 / 270 (95.6 %) | 92.7 % | 280 / 263 |
| sp_007 | 367 / 388 (94.6 %) | 88.6 % | 427 / 443 |
| sp_008 | 278 / 289 (96.2 %) | 83.4 % | 44 / 16 |
| sp_008_b | 110 / 113 (97.3 %) | 79.3 % | 107 / 58 |
| sp_009 | 68 / 72 (94.4 %) | 20.1 % | – (plane interior; measure unsuited) |
| **All 13** | **4,527 / 4,712 (96.1 %)** | | |

   - The pairwise check compares route-network layout; DICE's own network has gaps (spec §3.8), so it
     is a guide, not ground truth. Open leads: sp_008_b (sloped bridge foundation / fence passage DICE leaves
     out), sp_009 (plane aisles narrower than the capsule; DICE connects them), sp_03 / sp_008 (DICE network gaps).
   - Not generated yet: diagonal border portals, the 20 m midline cuts. Done since: detailed zones (pitch 0.2,
     2026-10-21), ladder links (2026-10-22), D lines and F cells (2026-10-26), vehicle data (2026-10-26,
     `navvehicle.py`, offline - not yet in `navkit.py`); the "up" border portals
     are a quirk of DICE's data, not generated on purpose.
   - Then: MP maps (self-check, no DICE data); later a standalone mode that reads Havok collision
     directly instead of sampling in game - done 2026-10-26 (`navsample_offline.py`, "Standalone sampling"), with
     the stock MP maps built from it.
5. **Tooling – done (2026-10-15).** `navkit.py` runs the whole workflow from a level name (unpack, objects,
   automatic sampling box and sector offsets, both in-game sampling runs with `opfor.ini` restored
   afterwards, build, check, install / restore with the original kept); `navkitGUI.pyw` is a small window
   over it. Stock MP maps wait for MP support: the in-game sampler needs the level's AI running. (2026-10-26: the
   offline sampler does not - "Standalone sampling" above, its stock MP batch; and `moh-stockmp-ai` gives a stock
   map the AI layer bots need.)
6. **DICE parity – the goal from 2026-10-16: 100 % of DICE's content, not identical bytes.**
   - **Measured by content:**
     - `tools\navdiff.py` compares two files layer by layer: sectors, walkable cells, areas,
       boundaries, route graph. A file against itself scores 100 % on every layer.
     - `tools\navparity.py` runs it on every level, with islands (connectivity, over-connection) and the
       AI-position figures, saved per named run.
   - **Baseline A** (the builds behind the table above), means over the 13 levels:
     - 98.0 % of DICE's walkable ground reproduced, but +36.9 % extra ground;
     - 0 % of areas identical, and 0.6 % of route nodes in the same place;
     - connectivity 96.6 %, over-connection 26.4 %;
     - AI positions 95.8 %, pairwise 83.5 %.

     `python tools\navparity.py show "baseline A"` prints the per-level table.
   - **The earlier figures measured less:** the AI-position coverage in the table above counts only where
     the level's AI is ordered to go.
   - **Regression test (2026-10-16):** `tools\navregress.py` rebuilds levels with the current code (the
     inputs of `navkit.py build`) and fails if any parity measure got worse than the reference run
     (`regression`, made from the code of 2026-10-16 and compared with baseline A). Run it before and after
     every generator change; `--save regression` after an intended improvement.
     - **The reference against baseline A** (13 levels, 70 minutes): mostly within a few tenths of a point
       either way, from different build inputs (baseline A's files were built by hand, the reference uses
       `navkit.py build`'s: spawns as seeds, no exclusion files). Larger: over-connection on SP_008
       (10.5 -> 35.5 %) and SP_008_B (32.2 -> 69.0 %), connectivity on SP_009 (85.9 -> 83.8 %), AI pairwise on
       SP_008_B (79.3 -> 76.6 %) and SP_02 (84.2 -> 82.7 %); connectivity better on SP_02 (+0.7) and SP_008
       (+1.2). Which input makes the SP_008 difference is still to find (likely the exclusion files).
   - **Hand edits (2026-10-16): `navkit.py apply <level>`** builds the level with the edits of its
     navigation side file (`work\navkit\<level>\<level>.navtune.json`, made in the Navigation Studio):
     unwalkable regions remove the sampled surfaces inside them before seeding (so a cut really
     disconnects), walkable regions force them in past the slope and clearance rules (they still join the
     network by the step rule), seeds add a seed, unseeds drop the island at the point. Later edits win where
     regions overlap. Then links and route edges go into the route graph (`tools\navlinks.py`): each point
     joins its nearest route node, both nodes must be in one sector (border nodes count for both), links and
     added edges are two-way unless set one-way, costs follow DICE's rule. On a DICE base there is no rebuild:
     the links go into a copy of DICE's file, `pathdata_dice_edited.hpd`, for this PC only. The output is
     read back to check it. Traversals (ladders, jumps, vaults, drops) go to `<level>.traversal.json` next to
     it (`tools\navtraversal.py`) for OPFOR's bot layer, not into the path data: the game's AI would plan
     through a ladder it cannot climb. Each has its ends, the route node and island at each end, and whether
     it joins two islands. The whole log is kept as `<output>.report.txt`. `--install` then installs the
     result (`navkit.py install --from <file>` installs any built file), with the traversal file beside the
     installed path data; `restore` removes both and puts DICE's file back. A full rebuild (about 1-8
     minutes a level); without edits the build is unchanged.
   - **Edge costs (fixed 2026-10-16):** every DICE edge costs `floor(distance * 16)` from the positions as
     stored; NavKit computed it from unrounded positions, and 138 of SP_004_B's 60,142 costs came out one
     unit off. NavKit now uses the stored positions (0 off; parity measures unchanged).
   - **Ground under water (2026-10-16, `tools\navwater.py`):** DICE keeps walkable ground down to about
     1.5 m under water and almost none deeper (11 campaign levels: 85-90 % of NavKit's ground at 0-1.5 m is
     DICE's too, 1-8 % below that; DICE has 241 cells at 1.5-2 m, 154 at 2-3 m and none deeper; median
     per-level limit 1.45 m). NavKit keeps it all: 29 % of NavKit's extra ground is deeper than DICE goes on
     that level (SP_01 73 %, SP_02 52 %). DICE's multiplayer data does the same (Onslaught: ONS_MP_002 72 of
     18,878 floors under water deeper than 1.5 m, ONS_MP_004 1 of 19,639).
   - **The water rule (2026-10-16):** NavKit's builder leaves out ground more than 1.5 m under the level's
     water (`navbuild.MAX_WATER_DEPTH`; the water mesh from the terrain bundle; hand edits forcing ground
     walkable still win). Over the 13 levels, against baseline A: extra ground +36.9 % -> +29.1 % (SP_01
     54 -> 16 %, SP_008_B 127 -> 91 %, SP_007 17 -> 10 %), DICE ground reproduced 98.0 % (unchanged),
     connectivity 96.6 % (unchanged); over-connection lower than the earlier reference on most levels.
     Connectivity fell 0.16 points on SP_04 and SP_004_B (SP_04 is the level where DICE keeps ground deepest,
     2.64 m) - accepted; a per-level limit is possible later. The reference run `regression` now includes the
     rule (the one before it: `regression_before_water`).
   - **Area division and bounds (2026-10-17 to 10-19):** `tools\navboundary.py` compares area boundaries
     cell by cell with DICE's (maps, CSV, precision / recall). On sp_004_b, from the start of this work to
     now: boundary precision / recall about 59 / 54 -> 99.4 / 94.1 %, extra ground 21.5 -> 8.6 %,
     over-connection 55.7 -> 21.7 %, sectors 50 -> 40 (DICE 40), portals with a portal back 78 -> 100 %,
     AI positions 93.4 -> 97.2 %, AI pairwise 85.0 -> 91.2 %; route nodes within 2 m of DICE's 36.5 -> 34.7 %
     (the bisection's extra nodes had matched by chance). On the other levels measured (sp_005, sp_007,
     sp_01, sp_006, sp_008_b) the structure measures improve the same way; nodes_near is still below the
     old reference. Levels whose DICE ground is mostly detailed zones (sp_008, sp_008_b, sp_009,
     ons_mp_005gr) are to be reported apart until NavKit builds detailed zones. The full 13-level regression
     is run only when asked (`navregress.py` shows progress and time left per level).
   - **What the game needs from the file (found in play tests on sp_004_b, 2026-10-19 to 10-20):** with
     these four rules the friendly squad's go-to requests succeed 100 % (they had stalled at the spawn, then
     at the top of the stairs), and leaving the level no longer crashes (also checked on sp_01). Each is a
     `tools\navbuild.py` setting, on by default:
     1. **one route node per surface** (`DEDUP_GRAPH_NODES`): no two ordinary nodes at one position, as in
        DICE's files (NavKit had 377 duplicate pairs joined by zero-length edges);
     2. **each sector's nodes sorted by id** (`SORT_NODES_BY_ID`), ordinary nodes first, border nodes
        (id flag 80000000h) last, as DICE writes them: the game binary-searches these arrays, and unsorted
        ones made it miss path ids and crash when leaving the level;
     3. **one owner per border node** (`BORDER_OWNER`): bit 16 of a node's `c` field is set only on the
        copies in sectors that do not own it (DICE: the owner is the sector whose 100 m square holds the
        node); NavKit had set it on every copy, and searches did not expand through border nodes;
     4. **a route node at each AI order position** (`ORDER_NODES`): the game resolves a go-to point to the
        nearest node within 15 m, and the request failed where that node was 3.6-6.3 m from the order
        point (DICE: 0.2 m). Order positions are the level's `AreaGeometryEntityData` points, written as
        `work\levels\<level>\order_points.json` by `levelobjects.py` (part of `navkit.py prepare`).
     A detour repair pass (`DETOUR_REPAIR`) is kept but off: shorter routes did not fix the stairs-top
     failure. How the game uses the file is written up in spec §4.5 and in OPFOR's
     `research\MOH_DECOMP_RESEARCH.md` §9.
   - **13-level regression with the four rules - accepted (2026-10-20):** order nodes in route islands under
     25 nodes are dropped (`ORDER_MIN_ISLAND`; it replaced a per-level exception for sp_009); zero-cost edges
     0 everywhere, sorted sectors 100 %, owners 98-100 %.
   - **Multiplayer, first map: ONS_MP_002 (2026-10-20)** - Napisal's co-op Rush level on Valparaiso, whose
     path data is DICE's Xbox 360 Onslaught file (byte-identical). Found and done on the way:
     - levels without a `SectorOffsets` asset (the Onslaught ports, stock MP maps, new maps) get DICE's rule
       for the sector numbering: one sector below the path data's lowest extent. Correction (2026-10-21): the
       rule holds for every shipped infantry asset but not for 7 of 13 vehicle assets; a DICE file's own offset is
       read from its sector ids (floor(sector centre / 100) - grid id, the most common value: 27 of 27 shipped
       assets). NavKit's own files are numbered by its own offsets, so its builds stay consistent;
     - the level reader accepts the attribute orders other tools write (`core/mohcore/xmlinst.py`);
     - the game itself reads `Precomputed/<kind>/SectorOffsets` at level load and uses 0,0 when it is
       missing - every route request from one sector then fails, on DICE's own file. OPFOR's
       `[dev] sector_offsets=-3,-7` supplies DICE's values; with it the AI advance and destroy an M-COM;
     - the first NavKit-against-DICE play test on an MP map (per unit, with OPFOR's unit counters): NavKit's
       file fails 1.8-2.5 % of route requests (DICE's 0.09 %) but gets more units walking. The failures:
       units stopping at (83, 254) - probed in game, not a route split and not node placement: the request's
       node lookup fails at the runtime's query-chain check at that spot (0.65 % of requests; parked as a known
       confound); and the objective targets, where NavKit carves the M-COM footprints and the destination point
       query fails (a mission-layer question). An order-node rule for orders inside obstacles was tried and not
       adopted (it fired on campaign levels; kept as a stash).
     - The in-game samples have a coverage hole south of z ~ -511 on this map (a sampler question).
     - Stock MP maps have no AI layer (HasAI false, one disabled AI spawner, no orders, squads or path data):
       bots there need an AI layer authored on top, as Napisal did; NavKit's part comes after that. (2026-10-26:
       the suite's `moh-stockmp-ai` authors that layer; built for Atacama Conquest and Valparaiso Rush, not yet
       played.)
     Details: OPFOR's `MP_APPLICATION_LOG.md`, spec section 4.5.
   - **Since then (2026-10-21 to 10-23):** combat on ONS_MP_002 fixed (OPFOR's Onslaught import); the sampler's
     south "hole" is beyond the PC terrain (the reference is now clipped to it); detailed zones at 0.2 m, chosen
     from the level alone, sampled and built by default; ladder links; the walkability rules and parity measures in
     the section above; "up" border portals found to be a quirk of DICE's data. The stock-MP AI layer is planned in
     OPFOR's `STOCK_MP_AI_PLAN.md`. 2026-10-23 / 24: closed area boundaries (`BRIDGE_UNION`), the objective floor
     rule (all 14 M-COM targets on an area, AI coverage on the two maps 100 / 100; the floor is the sampled layer
     nearest the target with enough hits, so a slab over the terrain and stray hits on the object's sides do not
     mislead it), and what D and F are for at run time (spec §4.5). 2026-10-26: D lines and F cells generated
     (`regression_df_v1`, the new reference), route-island pruning (`SHADOW_ISLANDS`: ONS_MP_004 109 -> 41 islands,
     spawn -> target routes 67 -> 100 % of DICE's), vehicle path data (`navvehicle.py`), boat meshes and routes across
     domains (prototypes), the offline sampler; the suite's test harness measures path-data files against DICE's.
   - **Next:** a play test on ONS_MP_002 (Valparaiso) with the current build (bots reaching and arming M-COMs; a
     destroyed house's floors; ladders; corner cutting and steps, where D / F now show; route islands), then the open
     items above (spawns on small islands first), a play test of a NavKit vehicle file ("Vehicle path data"), and
     wiring the offline sampler and the vehicle build into `navkit.py`. Later, with OPFOR's bot layer: breach links
     for walls opened by destruction. The 20 m midline cuts are parity only (conditional, one axis; not what broke
     the sp_004_b routes).
   - **Accepted limits:** hand edits, and geometry that differs from the shipped game.

Credits: level data is read with the suite's FbrbTool and DbxEdit, based on **Frankelstner**'s format
documentation and **Heico**'s MOH Toolkit. **Napisal**'s path data for multiplayer maps (BBC2 ModPack)
showed that BC2's AI can run on them. Kynapse is a trademark of Autodesk; Battlefield: Bad Company 2 is
© Electronic Arts / DICE.
