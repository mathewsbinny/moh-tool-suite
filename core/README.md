# core

The suite's shared Python package, `mohcore`: the game's data formats, the level loader and the 3D view
that the 3D tools are built on. Python 3.10+ with `numpy`; the view also needs `moderngl`, `glfw` and
`imgui-bundle`.

| Module | What it does | Checked by |
|---|---|---|
| `dbx.py` | reads binary `.dbx` files into an element tree and writes DbxEdit's XML, from `moh-dbx-tools/FORMAT.md` | `tests/test_dbx.py`: XML identical to DbxEdit's on all 31,404 fingerprinted files |
| `terrain.py` | terrain height fields (`.Heightfield-000.res`): heights, exact height at any point, a mesh | `tests/test_terrain.py`: within 4.5 cm of the in-game terrain for 99.9 % of 40 million samples (13 levels) |
| `water.py` | water meshes (`.Water.Mesh.res`) and each triangle's shader (from `.Water.res`) | `tests/test_water.py`: all 36 levels |
| `xmlinst.py` | the `<instance>` elements of a converted level partition (type, guid, body) and a field's reference (`field_ref`), whatever the attribute order (DICE writes `guid` / `ref` first, Napisal `type` / `name` first); used by NavKit's `levelobjects.py` and `ordercheck.py` | output identical to the old readers on the 13 campaign levels and ONS_MP_004 (2026-10-20) |
| `level.py` | a level: entities (types, transforms, blueprints, teams, player types), static-group members, world-space collision, areas and routes, terrain and water; prepares a level on first use; `geometry=False` reads only the entities, teams and shapes (a few seconds; the test harness) | `tests/test_level.py`: counts against `tests/level_snapshot.json` |
| `fbrb.py` | `.fbrb` bundles without unpacking, read only: the entry list (the header alone, fast) and single entries (the payload streamed up to them) | the test harness's tests: bundles built in the test; the same entries as FbrbTool on sp_009 |
| `viewer.py` | the 3D view: triangles and lines tagged with object ids and layers, orbit and fly camera, GPU picking with the world point, an overlay for links and measurements, ImGui panels | the Map Inspector's screenshot runs |
| `navdata.py` | a Kynapse path-data file as the 3D tools draw it: walkable cells (ground heights from the terrain), area boundaries, the route graph and its islands; a cache per file; cell comparison of two files | `tests/test_navdata.py` |
| `navpath.py` | the path test: a route between two points over a file's route graph (costs from the stored edges), or why there is none (no node near, different islands) | `tests/test_navdata.py` |
| `navtune.py` | navigation side files (`<level>.navtune.json`): the edits made to a level's AI navigation, their author (the PC's name) and the base they were made on | `tests/test_navtune.py` |
| `pick.py` | the level picker the 3D tools show when started without a level | `../tests/test_viewers.py` |
| `errors.py` | error handling and logs for every tool: a log per tool and day in `%LOCALAPPDATA%\moh-tool-suite\logs`, the cause and what to do when something fails, a window for the windowed tools | `tests/test_errors.py` |

```python
import sys; sys.path.insert(0, r'<suite>\core')
from mohcore import level
lvl = level.load('mp_005')                    # from Project OPFOR's game copy (found next to the suite)
flags = [e for e in lvl.entities if e.category == 'capture point']
lvl.heightfield.height(-111.0, 163.3)         # terrain height there
```

The formats are described in the module docstrings and in `../DESIGN.md` section 7. Tests:
`python ..\run_tests.py quick` (no game data) and `python ..\run_tests.py game`.
