# Menu tools

Command-line tools for Battlefield: Bad Company 2's menus and HUD (EA APT movies), built on the suite's APT reader
(`moh-console-readers\apt\apt.py`). They read your own game copy - straight from its bundles, nothing unpacked - and
never write into it. There is no menu viewer here.

| Tool | For |
|---|---|
| `aptcheck.py` | checking menu patches before a build reaches the game: the rules whose breaking crashes the menu loader |
| `aptrefs.py` | what the movies ask of the engine (`extern.<name>`), send it (FSCommands) and handle (engine events), with the exe's name / id table |
| `bc2loc.py` | the game's localized texts: look up a key, search the texts, the keys a movie uses |

Python 3.10 or newer (numpy is not needed). Movies are named the same way in all three:

| You give | Read from |
|---|---|
| a path to a `.res` (or a `.apt` with its `.const` beside it) | that file |
| `main_nav` | `Menu/main_nav.res` of the main menu bundle the game loads (`Package` over `Dist`) |
| `pristine:main_nav` | OPFOR's pristine copy of the main menu bundle (`original\Package\menu\main\mainmenu-00.fbrb`, kept by `build_menus.bat`): before OPFOR's patches |
| `<bundle.fbrb>:main_nav` | any bundle |

Project OPFOR's folder is found as the other tools find it (`--opfor`, `OPFOR_ROOT`, or a `Project OPFOR` folder next
to the toolkit). Outputs made from game data go to `<OPFOR>\work\tools_out\`.

## aptcheck.py - menu patch checks

```
python aptcheck.py lint "C:\Games\Project OPFOR\src\menus\*.patch" --installed --brief
python aptcheck.py verify pristine:main_nav main_nav --patch "C:\Games\Project OPFOR\src\menus\main_nav.patch"
python aptcheck.py check main_root
```

- **`lint <file.patch>...`** reads OPFOR's patch format (`<member> <hex offset> <expected bytes> -> <new bytes> # note`
  and `append <member> <label> "text"`, as `src\tools\apt\AptTool.cs` applies them): syntax, the size kept, lines
  overlapping, labels, a comment on the line or just above it. It then applies the file to the source movie (default
  `pristine:<file name>`) with every expected byte checked, and verifies the result. `--installed` also says whether
  the game's installed movie is that result (if not, the build is stale - or, for main_nav, `onslaught_art.py` added
  the Onslaught picture after the patches).
- **`verify <original> <patched>`** names every changed byte range (the instructions before and after and the
  function they are in, a constant's old and new text, or data), checks that every instruction away from the changed
  bytes decodes exactly as before, and that the patched movie breaks none of the rules below where the original did
  not. With `--patch`, every changed byte must be one the patch file names.
- **`check <movie>`** runs the rules on one movie by itself.

The rules (OPFOR's DEVELOPMENT_NOTES.md section 3, learned from menus that crashed while loading):

| Finding | Meaning |
|---|---|
| `shared-list` | a PushData item list used by two PushData: the loader converts each list in place, a second user crashes the menus |
| `end-in-function` | an End (00) opcode inside a function body |
| `zero-opcode` | a changed byte that is now opcode 00 (use `59 17`, push 0 / pop, as filler) |
| `body-end` | a function body that does not decode exactly to its end (an operand misaligned, an instruction straddling the end) |
| `branch-target` | a branch that does not land on an instruction of its own stream |
| `const-index` | a constant index past the constant table, or a pool index past the current constant pool |
| `decode-shift` | instructions away from the changed bytes decode differently (alignment or an operand moved) |
| `unknown-op` | an opcode with an argument the reader does not know |

Exit code 1 when there is an error. Every movie of the game copy's movie sets (128: menus, HUDs, loaders) and the
Xbox 360 menus pass the rules by themselves; OPFOR's 12 patch files lint with no error (2026-10-26).

## aptrefs.py - externs, FSCommands and events

```
python aptrefs.py xref                       every name across every movie set -> work\tools_out\aptrefs.txt / .json
python aptrefs.py find playerStats           one name: its kind, engine id, getter case, movies
python aptrefs.py movie wag                  one movie: externs read, FSCommands sent, events handled
python aptrefs.py table                      the exe's name -> id table
```

- **From the movies:** `extern.<name>` reads (`name$param` when a parameter is added: `unlockWeaponData$<id>`), the
  FSCommands sent (`"FSCommand:<name>"` strings, `fscommand` / `fscommandProxy` calls with a constant name), and the
  engine events handled: `_root.catchEvent(name, handler)` in the HUD, `gameEventListener.<name> = handler` in the
  menus, with the handler's parameter count.
- **From the exe (data only):** the name -> id table (574 names at `0x0143C898` in build 795745, found from its
  `presenceRequestOnline` entry, so other builds work too; getter and FSCommand names share one id list, and ids repeat
  between the two kinds); for build 795745, which getter switch has a case for each id - the main menu getter
  `0x76EC30` (ids 0x35-0x11A and 0x123-0x15D) and the UI manager's generic getter `0x95A1B0` (ids 1-0x15F). The HUD
  getter `0x777B30` is not read, so a name only HUD movies read shows "none - HUD getter not read".
- **"NOT in the exe"** marks a name the exe has no string for: an event the engine probably never sends by that name
  (some are built at run time, so it is a hint).

## bc2loc.py - localized texts

```
python bc2loc.py get MENU_PLAY_NOW PC_SERVER_BROWSER IDK_A
python bc2loc.py search "server browser" --lang english
python bc2loc.py movie main_nav
python bc2loc.py dump --lang russian        -> work\tools_out\loc_russian.txt
python bc2loc.py langs
```

The texts are `Localization/bin/language_<lang>_normal.bin` in `Dist\win32\overlay-00.fbrb`: a table of key hashes
(h = h * 33 + byte from 0xFFFFFFFF) and texts encoded through the language's `_histogram.bin` (one byte per character,
or two for Japanese). PC texts are stored under `WIN32_<key>` (`WIN32_PC_SERVER_BROWSER`, `WIN32_IDK_A`); `get` looks
there first, as the PC game does. Keys themselves are not stored: `search` and `dump` name the ones found among the
exe's strings and the movies' constants (about 840 of the 7,077 English entries). The format is in the script's header.

## Tests

`tests\test_menu_tools.py`: a movie, patch files, a language file and an exe built in the test (quick tier); with
`--game`, OPFOR's patch files, every movie of the game copy, the exe's table and getter cases, known events, and the
English, Russian and Japanese texts (game tier).

Developed for Project OPFOR. Battlefield: Bad Company 2 is © Electronic Arts / DICE.
