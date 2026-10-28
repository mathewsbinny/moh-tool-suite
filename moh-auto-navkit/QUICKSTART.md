# NavKit Quickstart

NavKit makes the navigation data (`pathdata.hpd`) that Bad Company 2's AI soldiers need to find their
way around a level. You choose a level, NavKit measures the level in the running game, and it builds the
file. This page shows how to do that from start to finish.

To learn how it works inside, read [README.md](README.md) and the
[specification](research/KYNAPSE_SPEC.md).

## What works today

| Level | Can NavKit build it? |
|---|---|
| The 13 campaign levels (`sp_*`) | Yes. NavKit's files are checked against DICE's. |
| Onslaught maps (`ons_mp_*`) | Yes. They have AI, so they can be measured. These are Napisal's co-op Rush levels. Napisal's files lack the asset that tells the game how the path data is numbered; OPFOR's `import_onslaught.bat` adds it (since 2026-10-21), and Prepare reads it. On a level imported before then, set OPFOR's `[dev] sector_offsets` in game (ONS_MP_002: `-3,-7`) or many AI route requests fail. |
| Stock multiplayer maps (`mp_*`) | **Not from the window yet.** The window and `navkit.py` measure a level while its AI is running, and stock multiplayer maps start without AI. Since 2026-10-26 `tools\navsample_offline.py stockmp` measures and builds them without the game (README, "Standalone sampling"); bots on them also need an AI layer (the suite's `moh-stockmp-ai`). Not play-tested. |

The GUI and `navkit.py levels` mark the levels you can use today as *samplable*.

## Before you start

You need:

1. **Project OPFOR, set up and built.** NavKit measures levels in OPFOR's copy of the game, through
   OPFOR's in-game sampler. In the OPFOR folder, run `setup_game.bat` (the game copy), then `build.bat`
   (OPFOR's core). `OPFOR.exe` must be able to start the game.
2. **FbrbTool and DbxEdit** from the suite: run `build.bat` in its `moh-fbrb-tool` and `moh-dbx-tools` folders.
   NavKit also accepts the copies OPFOR's `build_menus.bat` puts in OPFOR's `tools\` folder.
3. **Where OPFOR is.** NavKit looks for a `Project OPFOR` folder next to one of the folders it sits in (for example
   `C:\Games\Project OPFOR` next to `C:\Games\Project OPFOR Toolkit`). If yours is elsewhere, set the
   environment variable `OPFOR_ROOT` to it, or add `--opfor <folder>` to every command.
4. **Python 3.10 or newer** from [python.org](https://www.python.org/). When you install it, tick
   "Add python.exe to PATH".
5. **numpy.** Open a command prompt and run:

   ```
   pip install numpy matplotlib
   ```

   `matplotlib` is only needed for NavKit's picture tools, not for building.

Close the game before you use NavKit. Two steps start and close the game on their own.

## The easy way: the NavKit window

Double-click **`navkitGUI.pyw`** in the `moh-auto-navkit` folder. The window has a level list at the top, the sampling area below
it, then the steps, and a log of what NavKit is doing at the bottom.

1. **Pick a level** in the *Level* list, for example `sp_01`. Leave *Only levels with AI* ticked to list
   only the levels NavKit can measure.
2. Click **1. Prepare.** NavKit unpacks the level, reads its objects (buildings, fences, stairs, spawn
   points, and the places the level's AI is sent to) and chooses the area to measure. It reads levels made with
   DICE's tools and with modders' tools such as Napisal's, which write the level files in a different order. A level prepared before
   2026-10-20 does not have those places yet: click Prepare once more. The first time, it also unpacks data shared by all levels,
   which takes about a minute. After that it takes seconds. The *Sampling area* boxes then show the
   chosen area.
3. Click **2. Sample in game** and confirm. NavKit starts the game, loads the level and measures it. Then
   it closes the game and does it again with a second soldier size. If the level has *detailed zones* -
   buildings and other places with floors above floors, which NavKit finds by itself - it then measures
   those places again, four times more finely, with the same two soldier sizes. That makes up to four game
   starts. The first two take about 1 to 5 minutes each, the fine ones up to about 15 minutes on a large
   level. The game window may stay black the whole time. This is normal.
   **Do not click in the game or close it.** NavKit closes it when it has finished. (If a level ends by
   itself while NavKit is measuring, NavKit notices, says so and stops; run Sample again.)
4. Click **3. Build.** NavKit creates `pathdata.hpd` from the measurements. This takes a few minutes
   for a large level.
5. Click **4. Check.** NavKit scores the result (see [Reading the check](#reading-the-check)).
6. To try it in game, click **Install in game**. NavKit keeps the level's original file. When you
   are done, click **Restore original** to put it back. See [Trying it in game](#trying-it-in-game).

**Run all (1-4)** does steps 1 to 4 in a row. A tick next to a step means it has been done for that
level. **Open output folder** shows that level's files.

## The same steps from a command prompt

Run these from the `moh-auto-navkit` folder:

```
python navkit.py levels            list levels; "yes (samplable)" = NavKit can build it
python navkit.py prepare sp_01
python navkit.py sample  sp_01     asks before it starts the game; add --yes to skip the question
                                   (--pitch 0.8 or --pitch 0.2: only the normal or only the fine measurement)
python navkit.py build   sp_01
python navkit.py check   sp_01
python navkit.py install sp_01     restore sp_01 puts the original back
python navkit.py status  sp_01     what has been done for a level
python navkit.py all     sp_01     prepare, sample, build and check in one go
python navkit.py compare sp_01     the original against NavKit's file; --reference / --candidate for any two files
```

## Reading the check

For a level that has an original file (campaign and Onslaught), **check** compares NavKit's file with it,
using the positions where the level's AI is told to go (patrol routes, positions to hold). For sp_01 it
prints:

```
1078 order / waypoint positions: ...
sp_01: pathdata.hpd  on an area: 633/1078  ...      <- the original file
navkit: pathdata.hpd  on an area: 651/1078  ...     <- NavKit's file
pairwise route check: 71277/79663 ... (89.5%)
```

- **"on an area"**: how many of those positions lie on walkable ground in each file. NavKit's number
  should be close to the original's or higher. Some positions are off the ground in both, such as
  windows and vehicle seats.
- **"pairwise route check"**: of all pairs of positions the original connects with routes, the share
  NavKit also connects. 85 to 95 % is typical. The original has gaps of its own, so this is a guide,
  not a pass mark.

After that comes a summary, which **compare** also prints on its own:

```
1078 order positions; the reference covers 633, the candidate 618 of those (97.6 %) + 33 more
main route network: reference 607, candidate 648; pairwise 71277/79663 (89.5 %)
sectors 126 / 154; nodes 30305 / 31789; edges 82602 / 165744
```

- **Coverage**: of the positions the original has on walkable ground, how many NavKit's file has too
  (about 96-98 % on the campaign levels). "+ 33 more" are positions only NavKit covers.
- **Main route network**: positions connected to a large part of the level's routes, in each file.

`compare` takes any two files with `--reference <file>` and `--candidate <file>`, for example two NavKit
builds, to see whether a change helped. The same comparison runs without the driver:
`python tools\navcompare.py <level.xml> <reference.hpd> <candidate.hpd> [--json]`.

For a level without an original file, check does a **self-check**: how many pairs of positions on
walkable ground NavKit's routes connect.

The full report is in `work\navkit\<level>\check.txt`.

For a closer comparison with the original - walkable ground, areas, area boundaries, routes, connected
regions - see the parity tools in [README.md](README.md) (`tools\navparity.py`, `tools\navboundary.py`,
`tools\navregress.py`).

## Trying it in game

1. Click **Install in game** (or `python navkit.py install <level>`).
2. Start Project OPFOR as usual (`OPFOR.exe`) and, for a campaign level, start the mission from the
   campaign menu. (OPFOR's developer trigger, `[dev] level=` / `testrun.bat`, often leaves a campaign level
   on a black screen.)
3. Play, watching the AI soldiers.
4. Quit, then click **Restore original** (or `python navkit.py restore <level>`).

State of NavKit's files today (2026-10-26): on SP_004_B the friendly squad follows the player and moves
through the mission on NavKit's file (play test 2026-10-20), and leaving a level no longer crashes the game.
Measured against DICE's files on 15 levels, NavKit's files have 98 % of DICE's walkable ground and allow 98 %
of the routes DICE's allow. The changes since 2026-10-22 (closed area boundaries, floors at the M-COMs, D lines
and F cells, fewer small route islands) are measured offline but not yet play-tested. Some places still differ
(see README.md), so the AI may still stop somewhere. Project OPFOR's `DEVELOPMENT_NOTES.md` has the details.

Tip: to start a mission your campaign save is not on, set `level=Levels/<LEVEL>` under `[dev]` in
`game\opfor.ini` (for example `level=Levels/SP_004_B`), start OPFOR and press **F9** in the main menu. Put
the line back afterwards (it is normally `level=Levels/MP_005GR`). This can hang on a black loading screen
for some campaign levels; if it does, close the game and use the campaign menu instead.

## Where the files go

All of these are inside the Project OPFOR folder:

| File | What it is |
|---|---|
| `work\navkit\<level>\navkit.json` | the sampling area and sector numbering chosen by Prepare (the level's own `SectorOffsets`, or DICE's rule when it has none) |
| `work\navsample\<level>_navkit.bin`, `..._navkit_hi.bin` | the two in-game measurements |
| `work\navsample\<level>_navkit_p02.bin`, `..._navkit_hi_p02.bin` | the two fine (0.2 m) measurements of the level's detailed zones |
| `work\navkit\<level>\detailed.json` | which 100 m squares are detailed zones, and why |
| `work\navkit\<level>\pathdata.hpd` | **the result** |
| `work\navkit\<level>\check.txt` | the full check report |
| `work\levels\<level>\` | the unpacked level and the objects NavKit read from it |
| `game\Output\win32\ai\levels\<level>\infantry\pathdata.hpd` | the file the game uses; Install puts NavKit's here and keeps the original as `pathdata.hpd.opfor-orig` |

NavKit never changes your original Bad Company 2 install. It works only on OPFOR's copy in `game\`.

## Changing the sampling area

Prepare sizes the area automatically:

- **Levels with path data:** the area that data covers, plus 20 m.
- **Other levels:** the area around the level's objects and spawn points, plus 50 m.

On a level that has an original file, NavKit keeps its ground inside the area the original covers (so
the two can be compared); a larger area then adds nothing at the edges. On other levels, if parts of the
level are missing at the edges, make the area larger. In the window, type new values in
*Sampling area* before you click Prepare or Sample in game. From a command prompt, add the area like this:

```
python navkit.py prepare sp_01 --box -900,-620,440,520 --y -30,600
```

`--box` is the west, south, east and north edge in metres (min X, min Z, max X, max Z). `--y` is the
bottom and top height. Make the top higher than the tallest roof. A larger area takes longer to sample.

## Troubleshooting

| Problem | What to do |
|---|---|
| "the game is running - close it first" | Quit Bad Company 2 (OPFOR) and try again. |
| "sampling did not finish: the game exited" | The level crashed or was closed during sampling. Look at the end of `game\logs\opfor.log`, then try again. |
| "sampling did not finish: timed out" | The run took over an hour. Use a smaller area or check that the level loads at all. |
| "...has no AI path data, so its AI (and the sampler) may not start" | The level is a stock multiplayer map, which the window and `navkit.py` do not support yet. See [What works today](#what-works-today). |
| A run was cancelled or the PC crashed during sampling | NavKit edits `game\opfor.ini` while sampling and puts it back afterwards. If that was interrupted, the next Sample puts it back, and **Cancel** in the window does too. The saved copy is `work\navkit\opfor.ini.backup`. |
| The AI acts oddly with NavKit's file installed | Click **Restore original** (or `navkit.py restore <level>`). The level is back to its original data. |
| "FbrbTool.exe not found" / "DbxEdit.exe not found" | Run `build.bat` in the suite's `moh-fbrb-tool` and `moh-dbx-tools` folders (see [Before you start](#before-you-start)). |
| "Project OPFOR not found" / "no game copy" | Tell NavKit where OPFOR is (`OPFOR_ROOT` or `--opfor`), and run OPFOR's `setup_game.bat` if it has no `game\` folder yet. |

## How NavKit stays clean

NavKit is written from a published specification of the file format. It contains no code from the
original licensed generator and never runs it. Levels are measured through the game's own physics
queries, and all game data comes from your own installation. See [README.md](README.md#rules-clean-room-approach).
