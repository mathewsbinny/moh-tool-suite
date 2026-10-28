# Boats and routes across domains: research and design (2026-10-26)

Companion to `KYNAPSE_SPEC.md` §2.1 and Project OPFOR's `STOCK_MP_AI_PLAN.md` §11. The research below was done
offline: level and common data, DICE's path data and string tables of the executable. Nothing was run in the game.
Tools: `tools\navwater_mesh.py` (boat meshes), `tools\navdomains.py` (domain graph and planner). Tests:
`tests\test_navwater_mesh.py` and `tests\test_navdomains.py`.

## 1. Findings: water and boats in MOH's AI

- **The engine knows three navigation types, and none is for water.** `AIVehicleBehaviourData.Navigation` has
  exactly three values: `NTNone`, `NTGround` and `NTHelicopter`. These are the only `NT*` enum strings in
  mohmpgame.exe. There are four movement plans (`MoveToGround`, `MoveToInfantry`, `MoveToInfantrySpecial`,
  `IdleAir` / `IdleGround`) and four control kinds (`CarMovement`, `TankMovement`, `Helicopter`, `Soldier` /
  `Turret`). No boat, water or swim entry exists in the AI enums. Kynapse path data stays ground only:
  `ForbidWater` is true in both `PathfindingSettings`, with `WaterLevelAdjustment` -3.5 for infantry and -1.6 for
  vehicles.
- **The patrol boat's driver never asks for a route.** `mp_common` and `sp_common` `AI/Behaviours` both contain a
  vehicle profile named `PBL`:
  - Controls `CarMovement`, MoveTo plan `MoveToGround`.
  - **`Navigation` `NTNone`, `PathfindingType` null.**
  - Goals: Idle, MoveTo, Change, Interlude, MoveToRestrictedArea, Avoid (no Attack).

  The PBL's driver entry (`Pbl.xml`, `Entry Driver` AIData) points to it, with `MobilityData` MaxSpeed 17. The
  other profiles for comparison:

  | Profile | Navigation | PathfindingType |
  |---|---|---|
  | Car, Tank, UnarmedTank | `NTGround` | `VehicleData` |
  | Infantry, Squad, Sniper, Support | `NTGround` | `InfantryData` |
  | Helicopter, SerdarHind, UnarmedHelicopter | `NTHelicopter` | null |
  | PBL, MarkV, Fixed, UnarmedFixed, OpenVehicle | `NTNone` | null |

  So a Kynapse water layer would be ignored by the stock boat driver. With NTNone the driver can only steer
  straight at a MoveTo target. That fits Napisal's Isla Inocentes boats having no orders.
- **The jet ski's driver uses the `Car` profile** (`PWC_test.xml` AIData 4ea863e0 → `Car`: `NTGround` on
  `VehicleData`, MaxSpeed 20). An AI jet-ski driver would ask the land vehicle file for routes, and that file has
  no water. No level seats AI on a jet ski.
- **DICE's campaign never pathfinds a boat.** Sea-vehicle spawns per level:
  - SP_01's Zodiacs: the player plus cutscene drivers.
  - SP_03: `SP03_AB2_BoatIntro`, an AI driver (`SSAiPlayer`, tank-driver template) and two AI gunners, moved by a
    `CutsceneVehicleEntityData`. Its `ChangeOrderEntityData` only sets readiness for the gunners. The player's
    PBL is player-driven.
  - SP_003_B: a cutscene rowing boat.
  - SP_008 / SP_008_B: no sea-vehicle spawn in the level partition.
- **Napisal's Isla Inocentes (ONS_MP_004).** Two Team2 `Spawn_Pbl` at (45, 77, -319) and (-8, 76, -2), each
  crewed by `Stranded Ship_SS03` `SSAiPlayer` spawners (driver seat 0, gunner seat 1). No order entity, waypoint
  or patrol references them. Nine more sea spawns (PBLs and PWCs, mostly at the US base dock) are for players.
  The level's vehicle file stays on land (spec §2.1).
- **Other water data:** no waypoint or patrol paths on water. `AnimatedWaypointData` / `WaypointData` are soldier
  routes. The water itself is in `Terrains/<t>/<t>.Water.Mesh.res` (`core/mohcore/water.py`).

**Consequence.** OPFOR's bots must drive boats themselves (option B of plan §11): steer by input along OPFOR's
own boat graph. The game cannot use a Kynapse water layer (option A) for the PBL driver unless the data is changed.
That would mean switching `PBL` to `NTGround` with a third `PathfindingType`, plus a level `PathfindingTypes`
entry and a `<OutputName>\pathdata.hpd`. Whether the engine loads a third type, and how `MoveToGround` drives a
boat, can only be tested in game; this is on the ON HOLD list. For a jet ski, water areas added to the vehicle
file would be used as they are (the Car profile), with the snapping risk plan §11 names.

## 2. Chosen approach: a boat mesh as a side file (b), holding its own waypoint graph (c)

`navwater_mesh.py <level> [--profile pbl|pwc]` writes `work\navwater\<level>\<level>.<profile>.watermesh.json`:

1. **Grid.** Cells of 2 m over the water surface (`water.py`; the highest piece where pieces overlap), inside a
   box. The default box is OPFOR's in-game sampling box; `--box` gives another.
2. **Blocked.** A cell is blocked if anything lies in the hull band, from `draft` under the surface to `height`
   above it:
   - the terrain (the highest of five height-field points per cell);
   - any surface OPFOR's in-game samples recorded (soldiers excepted);
   - any collision triangle of a placed object or group instance (`objects.json` / `group_instances.json` and the
     blueprints' `_Havok.res`, sampled every pitch / 3).

   Piers, ship hulls, wrecks, buoys and bridge piers block. A bridge deck above the band does not, and walls are
   caught because their triangles cross the band.
3. **Free.** Navigable cells with no blocked or dry cell within the hull radius: where the boat's centre may be.
4. **Regions** of free cells (8-connected; a diagonal needs a free side cell).
5. **Route graph.** One node per region piece in each 8 x 8 block, at the cell with the most clearance. Edges go
   to nodes up to two blocks away that are in sight over free cells (cost in metres). Where a region's graph would
   split, repair paths are added (grid search, string-pulled).
6. **Shore links.** Each link joins a free cell within boarding reach (radius + 2 m) to infantry ground at the
   water line, from a path-data file (DICE's by default):
   - **beach:** terrain ground from 1.5 m under the surface (DICE's wading limit, `navwater.py`) to 0.5 m above
     it;
   - **dock:** object ground 0.3-3 m above the surface.

   There is one link per 20 m tile of shore, per land island and boat region. Each link records its land
   island, its route node and the file's SHA-1.
7. **Spawns.** Sea-vehicle `VehicleSpawnEntityData` are attached to the mesh. A spawn off the free water (at a
   dock) gets a launch cell up to 15 m out.

Profiles: `pbl` (radius 3.5 m, draft 1.0, height 3.0, 17 m/s) and `pwc` (1.5 / 0.5 / 1.5, 20 m/s). The speeds are
the AI's MaxSpeed values.

### Validation (offline)

ONS_MP_004, PBL, default box (x -220..120, z -420..500):
- water: 24,457 cells (97,828 m²);
- navigable 87.7 %, free 79.8 %;
- 10 regions, the largest holding 99.9 % of the free water;
- 439 nodes, 3,443 edges, 4 repair paths.

Structure checks:
- 0 nodes off free cells, 0 edges blocked or crossing regions, 0 split regions;
- in 2,000 sampled free cells, 100 % see a node;
- the side file reads back identical.

Shore links:
- 111 links (109 beach, 2 dock);
- 100 % land on the infantry file's cells;
- 90.1 % land on islands of 25+ nodes, 85.6 % have a route node within 10 m.

Spawns:
- all 11 sea-vehicle spawns are attached, in one region;
- the two AI `Spawn_Pbl` are on free water;
- the dock spawns launch 4-12.7 m out (PBL) and 0-2 m (PWC);
- every spawn reaches 97 of the 111 links over the graph.

Wider box (x to 700): 55,325 water cells, 86.2 % free, structure OK. PWC: 93.0 % free, 143 links. Other levels,
all with the structure OK and links 100 % on infantry cells:

| Level | Free water | Regions (largest) | Shore links |
|---|---|---|---|
| ONS_MP_002 | 90.2 % | 8 (98.4 %) | 138 |
| SP_01 | 77.5 % | 56 (73.3 %) | 95 |
| SP_02 (river) | 47.4 % | 4 | 52 |
| SP_008 (pond) | 85.6 % | 1 | 2 |

SP_02's low figure needs a look: about 4,700 collision cells, from vegetation or rocks in the river (§6).

## 3. Domains

| Domain | Graph | Speed | Source | In game |
|---|---|---|---|---|
| infantry | Kynapse infantry route graph | 4 m/s | `infantry\pathdata.hpd` (DICE's or NavKit's) | Kynapse queries (the game's own AI, or OPFOR's pathfinder probes) |
| land | Kynapse vehicle route graph | 0.6 x 15 m/s | `vehicle\pathdata.hpd`: DICE's, or `navvehicle.py`'s `work\navvehicle\<level>\pathdata.hpd` | the game's `NTGround` driver can follow it itself (Car / Tank profiles) |
| boat_pbl, boat_pwc | `navwater_mesh` route graph | 0.6 x MaxSpeed | `<level>.<profile>.watermesh.json` | OPFOR's bot layer: A* on the side file, steered by input |
| air | (design) 3-D corridors over a safe-altitude field | MaxSpeed | to be built: height field + tall objects, helipads | the game's `NTHelicopter` for helicopters, or the bot layer |

Costs are seconds: an edge's metres divided by the domain's speed. Later multipliers: risk (enemy fire along the
leg, from the bot layer's threat map), exposure (an open boat), and vehicle health.

## 4. Transitions

| Kind | From | To | Where | Cost |
|---|---|---|---|---|
| enter | infantry node within 12 m of the spawn | vehicle node within 30 m (boats: the spawn's launch cell) | `VehicleSpawnEntityData` of the domain (blueprint path `vehicles/land`, `/sea`, `/air`) | walk to the vehicle + 3 s |
| exit (land) | land node | infantry node within 6 m (height within 2 m) | dismount points, one per 40 m tile | 2 s + walk |
| exit (boat) | boat node | infantry route node at the link's land end | shore links (beach, dock) | 2 s + wading at 1.5 m/s |
| exit (air) | air node | infantry | landing zones (flat open ground, helipads), parachute drops | design |
| enter (parked) | infantry | vehicle | a vehicle left somewhere: a run-time transition the bot layer adds while the vehicle is there | as at a spawn |

Rules:
- A vehicle domain is entered only through an enter transition, so a vehicle leg always starts where a vehicle
  is. Shore links and dismount points are exits only: the vehicle is left behind.
- Seats matter for passengers: a squad can share one vehicle when the driver's plan and the passengers' agree.
  The plan carries the spawn's tag, and the bot layer claims seats.
- Availability is dynamic. `plan(..., unavailable=...)` takes spawn tags or transition ids: an empty spawn, a
  claimed seat, a destroyed vehicle.
- Crewed AI spawns (Napisal's `Spawn_Pbl`, out on the water with no infantry node within 12 m) are skipped as
  enter points. Their crews spawn aboard, and their plans start in the boat domain.

## 5. Planner

Hierarchical. Implemented in `navdomains.World.plan`, with `plan_flat` as the reference:

1. **Offline.** Per domain, a table of costs between all of its transition points: one Dijkstra per point. On
   ONS_MP_004 this covers 319 transitions and takes 1.9 s in Python.
2. **Query.** From the start (domain, node), one Dijkstra in the start's domain to every transition point. To the
   goal, one in the goal's domain (edges are two-way). Then a Dijkstra over the small graph S, G and the two ends
   of every transition: domain legs come from the tables, transition edges from their costs, and unavailable
   transitions are dropped.
3. **Expansion.** Each chosen leg becomes a path query inside its domain. Offline this is Dijkstra on the graph;
   in game, Kynapse for infantry and land, and A* on the side file for boats.

The answer is exact: the abstract graph holds every route the joined graph allows, because vehicle domains are
entered at transition points only. The tests check this on 300 random queries (random worlds, random unavailable
transitions) and on 40 random ONS_MP_004 queries, with 0 differing.

ONS_MP_004 (DICE's infantry and vehicle files, both boat meshes), 150 random soldier-spawn → AI-order pairs:
- 39 walked, 74 by land vehicle, 23 by jet ski, 9 by jet ski and land vehicle;
- 106 faster than on foot, by a median of 27 s;
- 14 reachable only with a vehicle (islands);
- 5 unreachable (order points under water or off any mesh).

Example: from the US base dock to the east beach at (80, -200), 30 s by jet ski (230 m of water); that island
cannot be reached on foot.

Data format, beside `pathdata.hpd` (written to `work\navwater\<level>\` for now):
`<level>.domains.json`, format `opfor-domains` 1. It holds:
- `domains`: per domain the node count, speed, file and SHA-1;
- `costs`: the cost constants;
- `transitions`: kind, from and to (domain, node index), seconds, tag and position;
- `tables`: per domain, the transition-point cost tables;
- `skipped_spawns`.

Node indices follow the graph loaders: Kynapse node ids in first-seen order, as in `core/mohcore/navdata.py`;
boat node indices as in the watermesh file. Positions are stored too, so the run-time side can re-resolve a point
when the path data changes. A SHA-1 mismatch means the file must be rebuilt.

## 6. What the game does itself, and what OPFOR's bot layer does

| Task | Game's AI | OPFOR's bot layer |
|---|---|---|
| infantry movement | Kynapse routes, orders | chooses goals; plans across domains |
| land vehicles | driver follows `VehicleData` routes (`NTGround`), if given a MoveTo order | picks the vehicle and destination, issues the order, or steers by input |
| boats (PBL) | none: `NTNone`, straight steering at best | reads the boat mesh, A* to a shore link or patrol point, steers by input (throttle / yaw), avoids other boats |
| jet ski | Car profile on `VehicleData`: no water routes | as the PBL, with the `pwc` mesh |
| helicopters | `NTHelicopter` (the engine's own flying, no Kynapse data) | landing zones, corridors, choosing to fly |
| enter / exit | spawners seat AI (`SSAiPlayer` + `Vehicle` array) | enter / exit at transitions: needs the game's enter-vehicle action for bots (to find) |

## 7. How vehicle path data plugs in

The vehicle worker's `navvehicle.py build <level>` writes a standard Kynapse file to
`work\navvehicle\<level>\pathdata.hpd`. `navdomains.py build <level> --vehicle <that file>` takes it as the
`land` domain unchanged: graph and costs come from the file, and dismount points from its nodes. Where the level
has DICE's vehicle file, that file is the default. Boats stay out of the vehicle file (option A stays a separate
experiment).

## 8. Implemented, and designed only

- **Implemented and tested offline:**
  - boat meshes (PBL and PWC profiles) with shore links and spawns;
  - the domain graph for infantry, land and boats from a level's files;
  - the hierarchical planner with per-domain tables, availability and leg expansion.
- **Designed only:**
  - the air domain (no generator: the planner accepts an air graph, and the tests use one);
  - landing zones;
  - parked-vehicle enter transitions at run time;
  - risk costs and seat claims;
  - the run-time side in OPFOR's DLL: loading the side files, A* on the boat mesh, steering by input, using the
    game's enter-vehicle action;
  - option A (a Kynapse water type).
- **Known limits:**
  - The default box is the sampling box, and water beyond it is left out (`--box` widens it; there, objects and
    terrain are the only obstacle sources).
  - Vegetation or other group instances standing in shallow water count as obstacles (SP_02).
  - Dynamic objects (floating crates) count as static.
  - Edges are dense (about 16 neighbours per node; prune if run-time memory matters).
  - Dock links are few (2 on ONS_MP_004): piers higher than 3 m or ending more than 5.5 m from free water give
    none.
