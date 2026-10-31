# LevelDiff

Compares two Medal of Honor (2010) binary `.dbx` files instance by instance: what was added, what was
removed, and which fields changed. It works on levels and on any other partition. Typical uses:

- the same map in two builds (PC, Xbox 360, Linux server);
- a level before and after an edit or a mod;
- Napisal's levels against the stock ones.

## Build

Run `build.bat`. It compiles `LevelDiff.exe` with the C# compiler that ships with Windows. It reads files
with the suite's DbxEdit code (`..\..\moh-dbx-tools`; or set `DBX_TOOLS` to
that folder).

## Use

```
LevelDiff <a.dbx> <b.dbx> [options]
```

| Option | What it does |
|---|---|
| `--match-objects` | also pair placed objects whose GUIDs differ, by blueprint and position (builds of one map often re-create them) |
| `--type <text>` | only instances whose type contains `<text>`, e.g. `--type ReferenceObjectData` |
| `--ignore <field>` | leave a field out, e.g. `--ignore Enumeration` (repeatable) |
| `--summary` | counts only |
| `--all` | list everything (default: the first 40 per section) |
| `--json <file>` | also write the full result as JSON |

The exit code is 0 if the files have no differences, 1 if they do, 2 for bad input.

Instances are matched by GUID. Each changed instance lists its fields as `path: old  ->  new`. Fields are
compared by what they hold (attributes, kind and value bytes), not by their text: two values that print the
same, like the decimal 2.0 and the integer 2, are shown with their kind and bytes in `[...]`. Lists of
references (like a level's `Objects`) are compared as sets: `-` for entries only in A, `+` for entries only
in B. A list with the same entries in another order is reported as such.

Example: PC Nelson Bay against the Linux server's version. All 318 placed objects that look new are the
same objects under new GUIDs:

```
LevelDiff MP_008_pc.dbx MP_008_linux.dbx --match-objects --type ReferenceObjectData --ignore Enumeration
same instance in both: 1681 (318 placed objects paired by blueprint + position); changed: 174; ...
ReferenceObjectData    C3A93730-7A4B-488D-A41A-8ACE4F7D3695
    - ObjectSets: 0b523726-1582-11df-be34-cb90b801a0f9
    + ObjectSets: 0058f00b-01de-11e0-aba9-cc78fd21fd3a
```

## Errors and logs

LevelDiff keeps a log in `%LOCALAPPDATA%\moh-tool-suite\logs\` like the suite's other tools (`ToolLog`, shared
with DbxEdit). When it cannot do something it prints the cause, what to do and the log's path, and exits with
code 1. Tests: `tests\test_leveldiff.py` (quick and game tiers of the suite's `run_tests.py`).

## Credits

Developed for Project OPFOR. The DBX format knowledge comes from the MOH modding community, above all
Frankelstner. Medal of Honor (2010) is © Electronic Arts / DICE.
