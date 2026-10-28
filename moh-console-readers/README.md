# Console readers

Read-only Python readers for Xbox 360 data. Use them to take Battlefield: Bad Company 2 content from your
own console discs and downloads. The disc and package readers only read standard file-system structures:
there is no decryption, and no keys are involved. The menu reader (`apt.py`) reads PC menus too.

| Folder | Reads | Commands |
|---|---|---|
| `xiso\xiso.py` | Xbox / Xbox 360 game disc images (XDVDFS) | `list <image.iso> [filter]`, `extract <image.iso> <outdir> [filter]` |
| `stfs\stfs.py` | Xbox 360 content packages (STFS: LIVE / PIRS / CON), e.g. downloaded map packs | `info <package>`, `list <package> [filter]`, `extract <package> <outdir> [filter]` |
| `apt\apt.py` | EA APT menu movies (a menu `.res`, or `<name>.apt` with its `.const`), PC and Xbox 360 - the byte order is detected | `info <movie>`, `chars <movie> [type]`, `dump <movie> [out.txt]`, `find <movie> <text>` |

`filter` is a case-insensitive part of the file name. All three need only Python 3.

`apt.py` shows a movie's characters (sprites, shapes, images, ...), each sprite's frames with their labels and
placements, and the menu code (ActionScript bytecode with EA's opcodes). Its header documents the format and the
geometry (`.geo`) and texture-atlas layout behind a menu's pictures. `Movie.instructions()` gives the decoded code as
records (offsets, lengths, branch targets, PushData lists, function bodies) for other tools: the suite's
`moh-menu-tools` checks menu patches and cross-references the movies with it. (2026-10-26: GetURL2 has no argument
and GotoFrame2 a 32-bit one in EA's bytecode; dumps made before then misread the bytes after those two opcodes - for
example a "shared PushData list" in wag that the movie does not have.) A console menu from your Update package:

```
python stfs\stfs.py extract "D:\Update3" out menu/main/mainmenu-00.fbrb
FbrbTool unpack out\menu\main\mainmenu-00.fbrb menu
python apt\apt.py dump menu\Menu\main_nav.res main_nav.txt
```

```
python xiso\xiso.py list "D:\MOH.iso" levels
python stfs\stfs.py extract "D:\Update3" out levels/ons_mp_008
```

## Errors and logs

The readers keep a log in `%LOCALAPPDATA%\moh-tool-suite\logs\` like the suite's other tools; on an error
they print the cause, what to do and the log's path (exit code 1). Tests: `tests\test_console_readers.py`
(built images and movies in the quick tier; with your Xbox 360 files in the game tier, including the console main menu).

Format notes are in each script's header (STFS follows the public Free60 documentation).

Developed for Project OPFOR. Xbox is a trademark of Microsoft; Battlefield: Bad Company 2 is
© Electronic Arts / DICE.
