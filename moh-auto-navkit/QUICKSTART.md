# NavKit Quickstart

NavKit makes navigation data for Medal of Honor (2010)'s multiplayer maps: where a soldier can walk, and
which routes connect. MOH's multiplayer client has no AI and ships no navigation data, so NavKit's output is for
Project OPFOR's own bots. You choose a level; NavKit reads its collision and terrain from the level's files and
builds the data. No game launch is needed.

To learn how it works inside, read [README.md](README.md) and the
[specification](research/KYNAPSE_SPEC.md) of the file format NavKit writes.

## What works today

| Level | Can NavKit build it? |
|---|---|
| All 26 MP levels (`mp_01` ... `mp_10_tdm`, DLC included) | Yes: prepare, sample, build and check. Each mode is its own level (its own partition, objects and combat area). |

The data is built for MOH's own soldier (`tools\mohsoldier.py`, from `DefaultSoldierPhysics`): radius 0.35 m,
height 1.8 m (crouched 1.15), step 0.3 m, slopes up to 45 degrees, jumps 0.65 m, water up to 1.4 m deep.

The last step, **export**, turns the build into the file OPFOR's bots will read: RSNV, the format of the navigation
runtime rsnav. When rsnav's host build is on the PC (project-opfor-moh `src\rsnav`, `python -B build.py host`), export also loads the file in rsnav and
routes between the level's spawn points. What is not there yet: the bots themselves (`project-opfor-moh\BOT_PLAN.md`).

## Before you start

You need:

1. **Project OPFOR: Medal of Honor, set up.** NavKit reads the levels from OPFOR's copy of the game: run
   `setup_game.bat` in the OPFOR folder once. NavKit looks for `project-opfor-moh` next to the suite; if yours is
   elsewhere, set the environment variable `OPFOR_ROOT` to it, or add `--opfor <folder>` to every command.
2. **FbrbTool and DbxEdit** from the suite: run `build.bat` in its `moh-fbrb-tool` and `moh-dbx-tools` folders.
3. **Python 3.10 or newer** from [python.org](https://www.python.org/). When you install it, tick
   "Add python.exe to PATH".
4. **numpy.** Open a command prompt and run:

   ```
   pip install numpy matplotlib
   ```

   `matplotlib` is only needed for NavKit's picture tools, not for building.
5. **Memory:** sampling a level reads all of its collision at once - about 3 GB on a large map.

## The easy way: the NavKit window

Double-click **`navkitGUI.pyw`** in the `moh-auto-navkit` folder. The window has a level list at the top, the sampling
area below it, then the steps, and a log of what NavKit is doing at the bottom.

1. **Pick a level** in the *Level* list, for example `mp_05_tdm` (Kandahar Marketplace, Team Assault).
2. Click **1. Prepare.** NavKit unpacks the level, reads its objects (buildings, walls, stairs, ladders, spawn
   points, objectives) and takes its combat area plus 20 m as the area to measure. The first time, it also unpacks
   the data shared by all levels, which takes about a minute. After that it takes seconds. The *Sampling area*
   boxes then show the chosen area.
3. Click **2. Sample.** NavKit measures the level from its collision and terrain: twice at 0.8 m (the soldier
   standing, and with his feet lifted by a jump), then - where the level has floors above floors - twice more at
   0.2 m over those 100 m squares. About a minute on a small map.
4. Click **3. Build.** NavKit creates `pathdata.hpd` from the measurements: walkable areas, their outlines, and
   the route graph between them.
5. Click **4. Check.** NavKit scores the result (see [Reading the check](#reading-the-check)).

**Run all (1-4)** does steps 1 to 4 in a row. A tick next to a step means it has been done for that level.
**Open output folder** shows that level's files.

## The same steps from a command prompt

Run these from the `moh-auto-navkit` folder:

```
python navkit.py levels             list the levels
python navkit.py prepare mp_05_tdm
python navkit.py sample  mp_05_tdm
python navkit.py build   mp_05_tdm
python navkit.py check   mp_05_tdm
python navkit.py export  mp_05_tdm  the RSNV file for the bots' navigation runtime, checked (in rsnav when found)
python navkit.py status  mp_05_tdm  what has been done for a level
python navkit.py all     mp_05_tdm  prepare, sample, build, check and export in one go
python tools\navsample_offline.py stockmp            all 26 levels, one after another (a report in
                                                     work\navkit_offline\stockmp.json)
python tools\mohsoldier.py <OPFOR folder>            NavKit's soldier against the game's DefaultSoldierPhysics
```

## Reading the check

MOH's levels have no navigation data of their own to compare with, so **check** does a **self-check**: it
takes the level's spawn points and objectives (Combat Mission's targets, Sector Control's and Hot Zone's capture
points) and tests routes between them on NavKit's file:

- **"on an area"**: how many of those positions lie on walkable ground. Some never do - Combat Mission's team
  bases are markers high above the ground.
- **"route check"**: of the pairs of positions on walkable ground, the share NavKit's routes connect.

The full report is in `work\navkit\<level>\check.txt`.

## Where the files go

All of these are inside the Project OPFOR folder:

| File | What it is |
|---|---|
| `work\navkit\<level>\navkit.json` | the sampling area and sector numbering chosen by Prepare |
| `work\navsample\<level>_navkit.bin`, `..._navkit_hi.bin` | the two measurements at 0.8 m |
| `work\navsample\<level>_navkit_p02.bin`, `..._navkit_hi_p02.bin` | the two fine (0.2 m) measurements of the level's detailed zones |
| `work\navkit\<level>\detailed.json` | which 100 m squares are detailed zones, and why |
| `work\navkit\<level>\pathdata.hpd`, `pathdata.cells.npz` | **the build**, and every walkable cell of it with its height |
| `work\navkit\<level>\<level>.rsnv` | **the result for the bots**: the build in rsnav's format (export) |
| `work\navkit\<level>\check.txt` | the full check report |
| `work\levels\<level>\` | the unpacked level and the objects NavKit read from it |

**Install** copies the result (with its ladder and jump annotations, `<level>.traversal.json`) to
`game\Output\win32\ai\levels\<level>\infantry\`, where OPFOR's bot layer will read it. The game itself does not.
NavKit never changes your original Medal of Honor (2010) install. It works only on OPFOR's copy in `game\`.

## Changing the sampling area

Prepare takes the level's combat areas (every stage of a Combat Mission, both teams) plus 20 m: a soldier outside
the combat area dies within 5 s, so nothing beyond is needed. To change it, type new values in *Sampling area*
before you click Prepare or Sample, or from a command prompt:

```
python navkit.py prepare mp_05_tdm --box -70,-60,80,110 --y 60,270
```

`--box` is the west, south, east and north edge in metres (min X, min Z, max X, max Z). `--y` is the
bottom and top height. Make the top higher than the tallest roof.

## Troubleshooting

| Problem | What to do |
|---|---|
| "FbrbTool.exe not found" / "DbxEdit.exe not found" | Run `build.bat` in the suite's `moh-fbrb-tool` and `moh-dbx-tools` folders (see [Before you start](#before-you-start)). |
| "Project OPFOR not found" / "no game copy" | Tell NavKit where OPFOR is (`OPFOR_ROOT` or `--opfor`), and run OPFOR's `setup_game.bat` if it has no `game\` folder yet. |
| "level partition ... not found" | Prepare the level again; a DLC mode's partition is read from its map's shared bundle (`common_mp_<nn>`). |
| Sampling runs out of memory | Close other programs; sampling a large map reads about 3 GB of collision. |

## How NavKit stays clean

NavKit is written from a published specification of the file format. It contains no code from the
original licensed generator and never runs it. Levels are measured from their own collision and terrain files,
and all game data comes from your own installation. See [README.md](README.md#rules-clean-room-approach).
