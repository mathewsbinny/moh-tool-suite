# moh-config

Inspect Medal of Honor (2010) **multiplayer** configuration and the engine's reflection
metadata. This is the tool we use to understand and author the game's config surface —
the foundation for the offline/LAN bypass.

## Commands

```
python config.py list                 list the files inside config.zz
python config.py extract NAME         print one file from config.zz
python config.py settings             print key/value settings parsed from config.zz
python config.py reflect [EXE]        print the reflection catalog (enums + fields)
```

`config.zz` is a **ZIP** (despite the name) at `MP\dist\win32\config.zz` holding the
Frostbite config: `Game.cfg` (Lua), `GameSettings.cfg` (key/value), `GameStandalone.cfg`,
`ServerGame.cfg`, and `Scripts\*.lua`. Paths are auto-detected from the game copy
(`Medal of Honor\MP\mohmpgame.exe`); override with `--zz` / the `reflect` positional.

## Examples

```
> python config.py list
  16336  Game.cfg
   1088  GameSettings.cfg
   5055  GameStandalone.cfg
   ...

> python config.py settings
AutosmokeMultiplayer.cfg     Client.IsMultiplayer = true
GameSettings.cfg             Network.ProtocolVersion = 54
GameSettings.cfg             Game.MaxPlayerCount = 24
...

> python config.py extract GameSettings.cfg
```

## `reflect` — the engine's reflection tables

`reflect` parses the `fieldinf` PE section of `mohmpgame.exe` and prints:

- **Enums** — every enum with its integer values. Example:
  ```
  BackendType (name inferred from member prefix):
      0 = BackendType_Blaze
      1 = BackendType_Lan
      2 = BackendType_Plasma
      3 = BackendType_PlatformDefault
  ```
- **Fields** — config/reflection fields grouped by parent type pointer, with their
  offsets. Example:
  ```
  type 0x01C78004:
      InProcBackend      @ 0xB7
      BypassMatchmaking  @ 0x1CB
  ```

This is how we discovered the offline levers: `BackendType_Lan` (= 1), `PlasmaAccount*`
(the in-proc dev backend's account), `Game.InProcBackend` and `Game.BypassMatchmaking`.

### Format notes (for maintainers)

- `fieldinf` records are 24 bytes: `name_ptr, ?, type_ptr, ?, value, ?`.
  - `type_ptr == 0` → an **enum member** (`value` = the enum integer).
  - `type_ptr != 0` → a **field** of the type at `type_ptr` (`value` = the field offset).
- Enum names are **inferred** from the common member-name prefix (the `typeinfo` section
  layout is non-uniform, so exact type-name resolution is a future refinement). Field
  groups are keyed by the raw parent-type pointer.
