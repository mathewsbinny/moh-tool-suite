r"""Where Project OPFOR: Medal of Honor and its game copy are, and how the copy is laid out. Every tool finds the
game through this module (standard library only, so tools without the rest of the core can use it too).

  <root>                        Project OPFOR: Medal of Honor (default: project-opfor-moh next to the suite)
    game\                       the standalone game copy (OPFOR's setup_game.bat), never the original install
      opfor.ini, logs\          the OPFOR core's settings and logs (opfor_mp.log, opfor_sp.log)
      MP\MoHMPGame.exe          the multiplayer client (Frostbite) - the game the tools work on
      MP\dist\win32\            its data: levels\<level>\level-00.fbrb ..., package.mft, config.zz
      MP\dist\win32\patch\      the update's bundles: they replace the base ones, and the DLC levels
                                (mp_01_elimination, mp_02_koth, mp_03_elimination, mp_07_koth ...) are only here
      MP\patch\dist\win32\      the updater's staging folder (a 57-byte empty mainmenu bundle) - not a data layer
      MP\Output\win32\          movies; OPFOR installs its bots' navigation data under ai\levels\<level>\
      Binaries\moh.exe          the singleplayer game (Unreal Engine 3, MOHAGame\CookedPC\*.upk)
    work\                       the tools' unpacked levels and outputs

The root is: the given folder, else OPFOR_ROOT, else project-opfor-moh next to the suite or next to one of its
parent folders, else any folder found walking up from the suite that holds game\MP\MoHMPGame.exe.
"""
import os
from typing import TypeGuard

OPFOR_NAME = 'project-opfor-moh'
MP_EXE = os.path.join('game', 'MP', 'MoHMPGame.exe')
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))


class GameNotFound(Exception):
    pass


def is_root(folder: str | None) -> TypeGuard[str]:
    if not folder:
        return False
    return os.path.isfile(os.path.join(folder, MP_EXE))


def find_root(given: str | None = None) -> str:
    """The Project OPFOR: Medal of Honor folder (see the module docstring)."""
    candidates: list[str | None] = [given, os.environ.get('OPFOR_ROOT')]
    d = SUITE
    while os.path.dirname(d) != d:
        d = os.path.dirname(d)
        candidates.append(os.path.join(d, OPFOR_NAME))
    d = SUITE
    while os.path.dirname(d) != d:
        d = os.path.dirname(d)
        candidates.append(d)
        try:
            candidates.extend(os.path.join(d, s) for s in sorted(os.listdir(d)))
        except OSError:
            pass
    for c in candidates:
        if is_root(c):
            return os.path.abspath(c)
    if given:
        raise GameNotFound(f'Project OPFOR not found: no {MP_EXE} in {given}')
    raise GameNotFound(f'Project OPFOR not found - pass --opfor <folder> or set OPFOR_ROOT (looked for {OPFOR_NAME} '
                       f'with {MP_EXE} next to the suite and its parent folders)')


class GameCopy:
    """The paths of one Project OPFOR folder's game copy."""

    def __init__(self, root: str | None = None) -> None:
        """root: a Project OPFOR folder, taken as given (tests build one without a game in it); None: find_root()."""
        self.root = os.path.abspath(root) if root else find_root()
        self.game = os.path.join(self.root, 'game')
        self.mp = os.path.join(self.game, 'MP')
        self.sp = os.path.join(self.game, 'Binaries')
        self.exe = os.path.join(self.mp, 'MoHMPGame.exe')
        self.sp_exe = os.path.join(self.sp, 'moh.exe')
        self.dist = os.path.join(self.mp, 'dist', 'win32')
        self.patch = os.path.join(self.dist, 'patch')
        self.output = os.path.join(self.mp, 'Output', 'win32')
        self.ini = os.path.join(self.game, 'opfor.ini')
        self.log = os.path.join(self.game, 'logs', 'opfor_mp.log')
        self.work = os.path.join(self.root, 'work')

    def layers(self, rel: str) -> list[str]:
        """The existing copies of a data file (rel: 'levels/mp_01/level-00.fbrb'), lowest first: dist\\win32, then
        its patch - the patch's bundle replaces the base one."""
        parts = rel.replace('\\', '/').split('/')
        out = []
        for top in (self.dist, self.patch):
            p = os.path.join(top, *parts)
            if os.path.isfile(p):
                out.append(p)
        return out

    def manifest(self) -> dict[str, list[str]]:
        """package.mft's load sets: {source: [targets]}, paths relative to dist\\win32 in lower case ('levels/mp_01/
        level-00.fbrb' -> ['patch/levels/mp_01/level-00.fbrb', 'levels/mp_01/level-00.fbrb', 'patch/levels/
        common_mp_01/...', ...]). The targets are what the game loads for the source, the first found wins - the
        patch's copy before the base one, the level's own bundle before its map's common_mp_<nn>, then common_mp."""
        if getattr(self, '_manifest', None) is None:
            self._manifest: dict[str, list[str]] = {}
            p = os.path.join(self.dist, 'package.mft')
            cur = None
            for line in (open(p, encoding='utf-8', errors='replace').read().splitlines() if os.path.isfile(p) else []):
                kind, _, target = line.partition(' ')
                target = target.strip().lower().removeprefix('game:')
                if kind == 'BundleSource':
                    cur = self._manifest.setdefault(target, [])
                elif kind == 'BundleTarget' and cur is not None:
                    cur.append(target)
        return self._manifest

    def levels(self) -> dict[str, str]:
        """{level (lower case): its name as the folder spells it} of every playable level, base and DLC: the manifest's
        levels\\<level>\\level-00.fbrb sources, without the shared common_mp* bundles. Two DLC levels
        (mp_03_elimination, mp_07_koth) have no bundle of their own: the manifest loads them from their map's
        common_mp_<nn>. Without a manifest: the level folders that hold a level-00.fbrb."""
        out: dict[str, str] = {}
        for src in self.manifest():
            parts = src.split('/')
            if len(parts) == 3 and parts[0] == 'levels' and parts[2] == 'level-00.fbrb':
                out[parts[1]] = parts[1]
        for top in (self.dist, self.patch):
            base = os.path.join(top, 'levels')
            if not os.path.isdir(base):
                continue
            for d in sorted(os.listdir(base)):
                if d.lower() in out or (not self.manifest() and os.path.isfile(os.path.join(base, d, 'level-00.fbrb'))):
                    out[d.lower()] = d                     # the folder's spelling
        return {k: v for k, v in sorted(out.items()) if not k.startswith(('common_mp', 'opfor_'))}   # opfor_*: OPFOR's

    def load_set(self, lv: str, name: str = 'level-00.fbrb') -> list[str]:
        """The existing bundles the game loads for a level's bundle, first found wins (see manifest); without a manifest
        block the level's own copies, the patch's first."""
        targets = self.manifest().get(f'levels/{lv.lower()}/{name}')
        if targets is None:
            return list(reversed(self.level_bundles(lv, name)))
        out = []
        for t in targets:
            p = os.path.join(self.dist, *t.split('/'))
            if os.path.isfile(p):
                out.append(p)
        return out

    def commons(self, lv: str) -> list[str]:
        """The shared bundle folders a level loads, in the game's order: its map's common_mp_<nn>, then common_mp."""
        out = []
        for p in self.load_set(lv):
            d = os.path.basename(os.path.dirname(p)).lower()
            if d.startswith('common_mp') and d not in out:
                out.append(d)
        return out or ['common_mp']

    def level_bundles(self, lv: str, name: str = 'level-00.fbrb') -> list[str]:
        """The level's own copies of one bundle (levels\\<level>\\<name>), lowest first (see layers); [] for a level
        without its own bundles."""
        return self.layers(f'levels/{self.levels().get(lv.lower(), lv)}/{name}')

    def pathdata(self, lv: str, kind: str = 'infantry') -> str:
        """Where OPFOR installs a level's navigation data (MoHMPGame.exe itself ships and reads none)."""
        return os.path.join(self.output, 'ai', 'levels', lv.lower(), kind, 'pathdata.hpd')

    def ai_dir(self) -> str:
        return os.path.join(self.output, 'ai')


def documents_dirs() -> list[str]:
    """The MP client's Documents folders, OPFOR's first: <Documents>\\Project OPFOR\\MOH\\EA Games\\Medal of Honor\\
    Multiplayer (the OPFOR core's), then the base game's <Documents>\\EA Games\\Medal of Honor\\Multiplayer."""
    home = os.path.expanduser('~')
    docs = []
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:   # CSIDL_PERSONAL
            docs.append(buf.value)
    except (AttributeError, OSError):
        pass
    docs += [os.path.join(home, 'Documents'), os.path.join(home, 'OneDrive', 'Documents')]
    out = []
    for sub in (os.path.join('Project OPFOR', 'MOH'), ''):
        for d in docs:
            p = os.path.normpath(os.path.join(d, sub, 'EA Games', 'Medal of Honor', 'Multiplayer'))
            if p not in out:
                out.append(p)
    return out
