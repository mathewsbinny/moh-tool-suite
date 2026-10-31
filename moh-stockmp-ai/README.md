# moh-stockmp-ai

Gives a stock Medal of Honor (2010) multiplayer level - which ships with no AI layer - the AI spawners, AI orders and event
wiring bots need, as level data the game loads like its own. It is option c of Project OPFOR's
`STOCK_MP_AI_PLAN.md`: the tool proposes the layer from the level's own objectives and spawns, a person tidies the
per-map configuration, and the tool builds and checks it offline.

Nothing EA-owned is in this folder. The tool reads the user's own game copy and writes only under
`<OPFOR>\work\stockmp\<level>\`; an install into the game is a separate, explicit step.

## Use

```
python stockmp.py summary  mp_005                 what the tool reads: mode, teams, flags, Rush stages
python stockmp.py generate mp_005 [--bots 8]      a first configuration: work\stockmp\mp_005\config.json
python stockmp.py build    mp_005                 author, pack, stage:   work\stockmp\mp_005\out\game\...
python stockmp.py validate mp_005                 the offline checks:    work\stockmp\mp_005\validation.json
python stockmp.py all      mp_005                 the three in a row (keeps an existing config.json)
python stockmp.py install  mp_005 [--apply | --revert]     plan (default), copy into the game, or undo
```

Options: `--pathdata <folder>` (a folder under the game's `MP\Output\win32\ai\levels`; default the level's own, else the
DICE folder of the same world, e.g. mp_005 -> ons_mp_005gr), `--minimal` (experiment 0: HasAI, PathfindingTypes,
path data and SectorOffsets only, stock spawners untouched), `--keep-stock-spawners`, `--variant <name>` (a second
build beside the first: `work\stockmp\<level>-<name>`).

Edit `config.json` by hand and run `build` again; the build is deterministic (same configuration, same bytes).
`build_report.json` records the configuration's sha256 (`config_sha256`), and `validate` fails at once when the
configuration it is given is not the one built (edited after the build, or a build from before the hash was recorded).

The game paths are MOH's, from `core\mohcore\gamecopy.py`: a level's `level-00.fbrb` in `game\MP\dist\win32\levels`
and its patch layer `game\MP\dist\win32\patch\levels` (the game loads the patch's copy when there is one), path data
under `game\MP\Output\win32\ai\levels`. A level without a level bundle of its own (the DLC modes mp_03_elimination and
mp_07_koth, loaded from their map's common_mp_<nn>) or a name that is not a level is refused with the reason. MOH's
`MoHMPGame.exe` has no AI types (no AreaOrderEntityData, HasAI, PathfindingTypes or PathdataSectorOffsets), and MOH
has no sp_common / mp_common or campaign donor level, so the layer cannot be built or run for MOH: `summary` reads a
MOH level, and the build steps stop with what is missing.

`install --apply` and `--revert` check every file first: when one was changed after the install, nothing is copied or
restored (the game is never left with the base layer new and the patch old).

## What a build contains

`out\game\` mirrors the game folder, so an install is a copy with backups:

| file | content |
|---|---|
| `MP\dist\win32\levels\<lv>\level-00.fbrb`, `MP\dist\win32\patch\levels\<lv>\level-00.fbrb` (the layers the level has) | the level partition with the AI layer; `Levels/<LV>/Precomputed/Infantry|Vehicle/SectorOffsets` (from the path data's own sector ids); the AI template chain (SpawnTemplate_Enemy_Grunt, its Sense and Behaviour, from the user's sp_common) when the level does not load sp_common |
| `MP\Output\win32\ai\levels\<lv>\infantry|vehicle\pathdata.hpd` | the path data named in the configuration |

The AI layer (author.py, with where each part was taken from):

- **LevelData:** `HasAI` true; `PathfindingTypes` = mp_common's infantry and vehicle PathfindingSettings (every level
  with working AI has them; stock MP levels have the array empty); every new entity in `Objects`; the new
  `EventConnections`.
- **AI spawners:** SoldierSpawnEntityData cloned from the level's own spawner; `PlayerType` SSAiPlayer; the AI template
  (default the campaign grunt - the fix that made Napisal's bots fire); a multiplayer kit of the team's faction, the
  four classes in turn; Napisal's respawning set-up (Amount 0, SpawnSoldierFromBeginning) with DICE's
  SpawnEnemyRadius 20. Placed on the ground spawn points of the team's base, checked against the path data.
- **Orders:** an AreaOrderEntityData per team and objective (AreaMoveTo on Conquest flags; on Rush, AreaHoldGround for
  the defenders and AreaMoveTo for the attackers at each M-COM), with its Soldiers and an AabbGeometry area. Issued by
  each soldier's OnSpawn, the human player's OnPlayerEnter, and an AreaTrigger at the objective (Napisal's pattern).
- **Rush stages:** read from the stock logic (the stage's MultipleTrigger fed by its M-COMs' onDestroyed); its OnTrigger
  disables and kills the stage's AI and enables the next stage's spawners and orders.
- **Stock spawners** marked SSAiPlayer (they are the human spawn points, listed by the capture points) become
  SSHumanPlayer, unless `--keep-stock-spawners`.

## Offline checks (validate.py)

DbxEdit `check` (no new error or warning, references resolved against the built bundle and mp_common); DbxEdit
`roundtrip` and XML export -> import; the suite's second DBX reader: references, Objects, event connections; only the
intended changes to existing instances; new references into other partitions resolve; SectorOffsets stored as
integers and equal to the staged path data's; the packed bundles unpack to the built files; every order has a route
node nearby, every AI spawner stands on the navigation data and is route-connected to its objectives; Rush stages
start disabled and their switch sources exist.

## Files

| file | what |
|---|---|
| `stockmp.py` | command line |
| `dbxbin.py` | binary DBX as an editable typed tree, written back byte for byte (8,216 game files checked) |
| `levelmodel.py` | the level as the tool sees it: capture points, spawners and ground points, M-COMs, Rush stages |
| `config.py` | the configuration format, defaults and the generator (Conquest, Rush) |
| `author.py` | inserts the layer into the level partition |
| `pipeline.py` | sources, SectorOffsets asset, template packing, bundle packing, staging |
| `validate.py` | the offline checks |
| `tests/test_stockmp.py` | quick: synthetic levels; `--game`: Atacama Conquest built and validated twice |

Needs Python 3.10+ and `numpy`; FbrbTool and DbxEdit built (`build.bat` in their folders); NavKit's tools are imported
for the path data (`hpdtool`, `ordercheck`). Logs: `%LOCALAPPDATA%\moh-tool-suite\logs\stockmp-*.log`.

## Status

Built and checked offline (2026-10-26): Atacama Conquest (MP_005, 16 bots, 6 orders) and its experiment-0 variant;
Valparaiso Rush (MP_002, 40 bots over 5 stages) as a test of the stage logic. Not yet run in the game: what the
engine does with this layer is open until a play test (`STOCK_MP_AI_PLAN.md` section 12).
