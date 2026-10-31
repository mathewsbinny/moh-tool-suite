# moh-navigation-studio

View a level's AI navigation data (Kynapse `pathdata.hpd`) in 3D: NavKit's build and DICE's original
side by side, over the level's terrain, water and objects. Milestone M3 (viewing); editing, the path test
and side files are M4 (`../DESIGN.md` sections 4.2 and 5).

**Opening it:** in the suite window (`MOH Tool Suite.pyw`), Levels tab: choose a level, then **Open in
Navigation Studio** (optionally with a NavKit file of your choice). Or double-click `studio.py` here: a level
picker opens. Or from a command line:

```
python studio.py <level>                       e.g. python studio.py sp_01
python studio.py <level> --candidate <file.hpd> --reference <file.hpd> --color island|sector|pitch|difference
```

**Files.** The reference is DICE's path data in Project OPFOR's game copy; the candidate is NavKit's newest
build of the level (`work\navkit\<level>\pathdata.hpd`, `work\navkit\regress\<level>.hpd` or
`work\navbuild\<level>_W.hpd`). Either can be given, and either may be missing. The level is prepared on
first use (offline); each file's walkable cells are cached in `work\navstudio\cache` (the first opening of a
large level takes about a minute, later ones seconds).

**Layers, per file:** walkable ground (its 0.8 m cells, on the exact terrain height where they lie on
terrain), outlines, portals, border portals and the route graph; plus terrain, water, objects and soldier
spawns. NavKit's ground is shown at start, the rest can be switched on.

**Colour the ground by**

| Mode | Shows |
|---|---|
| island | connected parts of the route graph: the main network green, every other island its own colour |
| sector | the 100 m sectors |
| pitch | detailed zones (DICE's 0.2 m areas) in orange |
| difference | NavKit's ground that DICE also has (grey-green) or does not (orange); DICE's ground NavKit lacks is the red layer |

**Ladder ground (2026-10-21).** A NavKit build with ladder links (`tools\ladders.py`) has two more layers per file,
*ladder ground* (violet: ground reached only through a ladder link - roofs, decks) and *ladder links* (the climbs).
In the *difference* colouring that ground is violet, not orange: it is deliberate, not extra ground; the Navigation
panel gives its share apart, and a selected area says when it is ladder ground. Ladder links are found from the
file itself (route edges climbing more than 2.5 m and steeper than 1.5:1 - DICE's files have none; `core/mohcore/navdata.py`).

**Path test (P, milestone M4 step 1).** Press P, click a start and an end on the ground: the cheapest route
over each file's route graph (its own directed edges and stored costs) is drawn - NavKit's yellow, DICE's
cyan - with its cost, length, the detour against the straight line, and NavKit's cost against DICE's. Without
a route the panel says why: which island each point is on and where they come closest (drawn in red, with a
button that flies there), or that no route node is within 15 m of a point. It plans over the graph only; the
game also smooths routes through the areas. (`core/mohcore/navpath.py`; `--path "x,y,z;x,y,z"` for tests.)

**Edit mode (E, milestone M4 step 2).** Draw edits on the level; they are kept in a side file,
`work\navkit\<level>\<level>.navtune.json` (format: `../DESIGN.md` section 5.1; `core/mohcore/navtune.py`).
Tools: walkable or unwalkable region (click an outline and press Enter, or click two corners of a box; the
height range runs from 2 m below to 4 m above the clicked points, adjustable), seed, unseed, link two areas,
add or remove a route edge (two points), traversal (ladder, jump, vault or drop, one- or two-way: the
bottom, then the top), and note. Every edit records who made it (this PC's name unless you type another)
and when. The panel lists the edits (fly to one, delete one), with undo (Ctrl+Z; a deleted edit comes back in its
place), redo (Ctrl+Y), save and reload (with unsaved edits, a second click discards them); Esc cancels a drawing.
E does not close the panel while you fly with the right button held (E climbs there). Edits are drawn over the
scene: walkable green, unwalkable red, seeds cyan, unseeds magenta, links orange, route edges yellow (removals red),
traversals white. A side file that cannot be read (a newer version's, an invalid edit, a typo) is not saved over:
the studio says so, the edits start empty, and the first save or build keeps the file as
`<level>.navtune.json.unreadable.bak`.

**Build with these edits** (step 3) saves the side file and runs `navkit.py apply` in the background (its log in the
panel); when it is done, **open the result** reopens the studio on the built file. Walkability edits (regions,
seeds, unseeds) are built in, then links and route edges go into the route graph (step 4; each point joins its
nearest route node, and both must be in one sector - the log says when one could not be placed), and
traversals into `<level>.traversal.json` for OPFOR's bots (step 5). When the build is done: **install into the
game** (DICE's file is kept once and **restore DICE's original** puts it back), and the report file (step 6).
On
DICE's own data only additive edits are offered (links, route edges, traversals, notes; the button writes them
into a copy, `work\navkit\<level>\pathdata_dice_edited.hpd`, for this PC only): region edits need
a NavKit build to rebuild. `--navtune <file>` opens a side file from elsewhere, `--edit` opens the panel.

**Compare with a reference AI layer (read-only).** Draws another level's AI layer on this level's terrain, for
example Napisal's Onslaught AI on stock Valparaiso, which has none:

```
python studio.py mp_002 --compare level:ons_mp_002     Napisal's ONS_MP_002 (any level prepared in Project OPFOR)
python studio.py mp_002 --compare xbox:ons_mp_002      DICE's Xbox 360 Onslaught level (unpacked into work\xbox_dlc)
python studio.py mp_002 --compare <path to a level .xml>
```

It works on a level without any path data. Drawn, each a layer you can switch: AI spawners red, human spawners
blue (filled box = enabled, outline = disabled; the selection panel shows the team and squad id); AreaOrders
yellow (a box on a short stem; they carry no team or squad, so the panel shows their AreaType instead); patrol
orders orange, a line through their waypoints; objective objects (`mpobjective`, `communicationstation`) grey;
the reference's path-data area outlines light green. The *Compare with reference* panel shows the reference's
HasAI and SectorOffsets ("none" when the level has no such asset - never inferred), its counts, and filters
(AI spawners, human spawners, orders, objectives, path data, enabled spawners only). Click a reference marker:
a line runs to this level's nearest NavKit route node on the same floor (green within the game's 15 m gather
radius, red beyond) and the panel gives the distance and that node's network. **export JSON** writes
`work\navstudio\<level>_vs_<source>.json`: per spawner, order, patrol waypoint and objective its position and
fields, whether NavKit's build has walkable ground there, the nearest same-floor route node and whether it is on
the main network; a header with both levels, the files and the reference's HasAI and SectorOffsets. The same
export without opening a window: `python overlay.py <level> --reference <source> [--out file.json]`.

**Anomaly overlay (NavKit builds).** The *Anomaly overlay* panel, and the colour mode *anomaly*: the main
network green, islands of 25 or more nodes grey, islands under 25 nodes magenta. Flags on top, each a layer with
its count in the legend: soldier spawns whose nearest route node is on an island under 25 nodes (red outline);
AI orders and patrol waypoints with no route node within 15 m on their floor (amber outline); sliver areas -
under 5 cells, no portal or border portal (yellow cells); lonely route nodes - no other node within 12 m on the
same floor (magenta dots). The orders and spawns are the level's own (read from its level file). **export JSON**
writes `work\navstudio\<level>_anomalies.json`, per anomaly its class, position and value (island size, cell
count, distance); headless: `python overlay.py <level> --anomalies`. Read the counts against DICE's own file, not as
absolutes: on the campaign levels DICE's working data has as many orders and spawns without a nearby node as
NavKit's (first use, below) - AreaOrder positions are area volumes, not always walkable points. To count DICE's
file the same way, pass it as the candidate (`--anomalies --candidate <DICE's pathdata.hpd>`). All 15 levels with
DICE path data, NavKit's builds of 2026-10-22 (DICE / NavKit): spawns on small islands 147 / 72, orders without a
node 768 / 686, sliver areas 77,996 / 62,708, lonely nodes 5,081 / 10,011, islands under 25 nodes 5,684 / 6,390.

**First use (2026-10-21).** Napisal's ONS_MP_002 and DICE's Xbox ONS_MP_002 drawn on stock MP_002:
- none of Napisal's 28 enabled AI spawners lies within 3 m of a DICE console spawner (nearest 6.1-108.5 m, median
  58.9 m) - his AI layer is his own placement, not a copy of DICE's;
- 38 of his 41 AreaOrders sit on the PC Rush objectives (within 5 m; median 0.0 m) and only 3 are nearer a DICE
  console AreaOrder (10 within 5 m of one; median 8.3 m) - his orders follow the PC Rush layout;
- campaign sanity (AI spawners, AreaOrders and patrol waypoints without a route node within 15 m on their floor):
  sp_004_b NavKit 7 / 31 / 0 against DICE's 7 / 39 / 0; sp_01 NavKit 52 / 56 / 2 against DICE's 55 / 58 / 3; AI
  spawners on islands under 25 nodes: sp_004_b 31 (DICE 23), sp_01 19 (DICE 13). NavKit is at or below DICE on the
  first three and somewhat above on the small-island spawns.
Not computed as asked: AreaOrders carry no team or squad field (coloured by AreaType instead); a target level
without a SectorOffsets asset shows "none"; the reference's path-data outlines are drawn at their own heights, not
flattened; labels are in the panels (the 3D view has no text).

**Panels**

- *Navigation:* per file the areas (detailed ones), cells, route nodes and edges, islands; DICE ground
  reproduced and NavKit's extra ground (a cell estimate - `navparity.py` gives the exact figures); colour
  mode; layers.
- *Islands and spawns:* the islands of either file, largest first, with their size and distance from the
  main network, each with a button that flies there; the soldier spawns that are not on the main network
  (or have no route node within 15 m).
- *Selection:* click ground to see its area (sector, tile, layer, pitch, cells, boundary pieces, island);
  click a spawn to see its island in each file. Hovering shows the area under the mouse.

The camera works as in the Map Inspector (left drag rotates, right drag pans, wheel zooms, double-click flies
to a point, F for fly mode). For tests: `--screenshot out.png [--frames N] [--color ...] [--island N]
[--hide <layer>,...]`.

The data comes from `core/mohcore/navdata.py`, which reads the files with NavKit's byte-exact reader.
