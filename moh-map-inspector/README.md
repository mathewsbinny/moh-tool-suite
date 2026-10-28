# moh-map-inspector

Browse a Battlefield: Bad Company 2 level in 3D and inspect every object: where it is, what it is, which
file it comes from, its full property tree, and how it links to other objects.

**Opening it:** in the suite window (`MOH Tool Suite.pyw`), Levels tab: choose a level, then **Open in Map
Inspector** (or double-click the level). Or double-click `inspector.py` here: a level picker opens. Or from
a command line:

```
python inspector.py <level>          e.g. python inspector.py mp_005   or   sp_01, mp_009gr, ons_mp_004
```

The level is read from Project OPFOR's game copy. The first time a level is opened, it is unpacked
(NavKit's offline `prepare` step plus the terrain bundle, about 20 s; no game launch). Needs Python 3.10+
with `numpy`, `moderngl`, `glfw` and `imgui-bundle` (`pip install numpy moderngl glfw imgui-bundle`).

## What it shows

| Layer | From |
|---|---|
| terrain | the level's height field (`core/mohcore/terrain.py`), exact to a few centimetres |
| placed objects, destructibles, dynamic objects, ladders | `ReferenceObjectData` collision; the group comes from the blueprint's object type (destructible: a static model with health states or breakable parts) |
| group members | static model groups (`*_HavokAsset`) |
| soldier and vehicle spawns, per team | `SoldierSpawn`, `AlternateSpawn` (team of the spawner that lists it), `VehicleSpawn`; a short line shows the facing |
| objectives | Rush M-COMs (`mpobjective` blueprints), campaign `MissionObjective` and `ArmDisarmAward` entities |
| capture points | Conquest flags (`CapturePointEntityData`) and their capture areas |
| combat areas | `CombatAreaTrigger` (and its outer area) and `DamageAreaTrigger` outlines |
| AI orders | area, patrol, interlude and fire-at-area orders; patrol routes as lines |
| effects, area points, other | hidden at start |

Areas and routes are chains of `AreaGeometryEntityData` points (their `Next` links): a chain that returns
to its start is an area (drawn with its height as a prism), an open one is a route.

## Using it

- **Camera:** left drag rotates, right or middle drag pans, the wheel zooms, double-click flies to the
  point. F switches to fly mode: hold the right button and use W A S D, Q / E for down / up, Shift for speed.
- **Select:** click an object. The Selection panel shows its type, GUID, partition, blueprint, team,
  player type and position, the objects that reference it, and the full property tree; references in the
  tree are buttons that follow them (with *back*). Over the scene, yellow lines go to what it references and
  cyan lines come from what references it.
- **Search:** by GUID, type, id, name or blueprint, and filter by type; results fly the camera to the object.
- **Measure (M):** clicks add points; the panel gives the last segment (length, horizontal, height, slope),
  the path total, and the last point's height above the terrain. Esc clears.
- Hovering shows what is under the mouse and where.

Window layout is kept in `%APPDATA%\moh-tool-suite\`.

For tests: `inspector.py <level> --screenshot out.png [--frames N] [--select <guid>] [--measure "x,y,z;x,y,z"]`
draws, saves a screenshot and exits.

"Player type" shows the spawn's `PlayerType` field as stored: most of MP_005's multiplayer spawns say
`SSAiPlayer`, so it does not tell who actually spawns there.
