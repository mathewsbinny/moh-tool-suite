# FbrbTool

A command-line tool to unpack and repack Medal of Honor (2010)'s `.fbrb` bundles (Frostbite 1
archives: the game's levels, menus and shared data).

## Build

Run `build.bat`. It compiles `FbrbTool.exe` into this folder with the C# compiler that ships with
Windows (.NET Framework 4) and needs no install.

## Use

```
FbrbTool list   <file.fbrb>                list the entries
FbrbTool unpack <file.fbrb> <outdir>       write every entry under <outdir>, plus fbrb.manifest
FbrbTool pack   <dir> <file.fbrb>          rebuild a bundle from an unpacked folder
```

`unpack` writes `fbrb.manifest`, which lists the entries in archive order with their type, flags and
sizes. `pack` uses it to rebuild the bundle faithfully. To change a bundle, unpack it, edit or add files
(add new ones to the manifest), then pack it.

Zero-byte entries are `*deleted*` markers: they remove shared data from the bundle's level (mp_005's
level bundle has 4,334 of its 6,692 entries as markers; all bundles together 286,055). `unpack` lists them in
`fbrb.manifest` but writes no file for them, so other tools do not meet thousands of empty files that look
broken; `pack` restores them from the manifest, and the rebuilt bundle's header and payload are identical
to the original's. Folders unpacked by older versions, which hold the markers as empty files, pack the same.

Two MOH layouts are kept (2026-10-30): an uncompressed bundle whose header ends and entries start on 4 KiB
boundaries (the async `ondemand_*` bundles) gets `align=4096` on the manifest's first line - `pack` pads the header
and fills the gaps with 0xBF again; and where 0xBF fill bytes follow an entry (64 after each `.dbx` in the
`build_overlay` bundles) the manifest's optional 6th column holds their count. All 358 of MOH's bundles come back
identical (`core\tests\test_fbrb.py --game 0`).

## Errors and logs

FbrbTool keeps a log in `%LOCALAPPDATA%\moh-tool-suite\logs\` like the suite's other tools. When it cannot do
a job it says why in words - a file that is not an FbRB bundle or is damaged, a file in use, a missing file,
a read-only folder - what to do, and where the details are; exit code 1.

## Credits

The format follows **Frankelstner**'s fbrb script and **Heico**'s MOH Toolkit. Developed for
Project OPFOR. Medal of Honor (2010) is © Electronic Arts / DICE.
