"""In-game tests: how they plug in. The harness never starts the game; these read what a run the user made left behind.

Now (offline reading of a finished run):
  sessions(text)        the sessions of an opfor.log (MOH's is game\\logs\\opfor_mp.log): one per level each game
                        process played (a process starts at a "---- OPFOR core loaded" / "---- opfor.dll loaded"
                        line; a level line naming another level starts its next session): level, session states,
                        AI events, SectorOffsets loads and override, unit-counter pathfinder counts, crash, exit
  evaluate(s, asset)    the three in-game criteria of an AI level (STOCK_MP_AI_PLAN.md 6.3 / 8) as checks.Results:
                        loads-ai-init, offsets-read, pathfinders
  harness.py import-log <run> --level <lv> [--log <opfor.log>] adds them to a saved run.

ON HOLD - needs playtest (designed, not run by the harness):
  testrun        `testrun.bat <level>` (OPFOR) starts the level, follows opfor.log and reports in game / crashed /
                 timed out / exited (exit codes 0-4). The harness will call it per level of a run with --yes and
                 [dev] unit_counters_dump=1, then import the session as above. Kept out until a play test confirms
                 the sequence on a stock MP map (campaign levels stall on a black screen through the dev trigger).
  telemetry (M6) OPFOR's telemetry module streams JSON lines over 127.0.0.1 (DESIGN.md 5.4); a recording of it
                 feeds bot traversal (bots reach given points within a time), stuck detection (no progress for
                 N seconds), squad coordination and vehicle use - TELEMETRY_TESTS below, each 'hold' until the
                 stream exists.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import List, Optional, cast

from .checks import Result

# a session starts at the core's load line: MOH's OPFOR core ('---- OPFOR core loaded into <exe> (PID n) ----', in
# game\logs\opfor_mp.log) or the older opfor.dll ('---- opfor.dll loaded into PID n ----')
START = re.compile(r'---- (?:OPFOR core loaded into .* \(PID (\d+)\)|opfor\.dll loaded into PID (\d+)) ----')
STAMP = re.compile(r'^\[(\d\d:\d\d:\d\d)\.\d+\]')
LEVEL = re.compile(r"(?:queued hostMultiplayerLocal request|dev trigger): level='([^']+)'")
STATE = re.compile(r'Session::updateState.*arg1=([0-9A-F]{8})')
OFFSET_LOAD = re.compile(r'sector offsets load handle .*X\(\+1Ch\)=(-?\d+) Z\(\+18h\)=(-?\d+)')
OVERRIDE = re.compile(r'sector offsets: on - X=(-?\d+) Z=(-?\d+)')
COUNTERS = re.compile(r'unit counters \((interval|exit)\): (\d+) pathfinders')
# the game writing its crash report: the file name on its own (a path's last part, a word), not 'crashreport.xml.bak'
CRASH = re.compile(r'(?:^|[\\/\s\'"(])crashreport\.xml(?![\w.])', re.IGNORECASE)
CRASH_READ = re.compile(r'CreateFile[AW] read ')       # the OPFOR core's userdata log of a file only read
EVENTS = [(re.compile(r'AI: server level load'), 'server level load'), (re.compile(r'AI: init'), 'AI init'),
          (re.compile(r'AI: set active'), 'AI active'), (re.compile(r'AI: path data world'), 'path data loaded'),
          (re.compile(r'AI: Kynapse world'), 'navigation world built'), (re.compile(r'AI: update \(per frame\)'), 'AI running')]
IN_GAME = 3                                     # Session state 3: in game (testrun.py)
TELEMETRY_TESTS = [('bot-traversal', 'bots reach given points within a time'),
                   ('stuck', 'no bot without progress for N seconds'),
                   ('squads', 'squad members keep together and follow their orders'),
                   ('vehicles', 'bots enter and drive vehicles')]


@dataclass
class Session:
    """One level played by one game process: what the log showed from its LEVEL line (from the process's start for
    its first level) to the next level's LEVEL line or the process's end. part: 1 for the process's first level, 2
    for the next one it played, ..."""

    pid: str
    start: str = ''
    level: Optional[str] = None
    states: List[int] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    offset_loads: List[tuple[int, int]] = field(default_factory=list)
    override: Optional[tuple[int, int]] = None
    pathfinders: List[int] = field(default_factory=list)
    crashed: bool = False
    exited: bool = False
    lines: int = 0
    part: int = 1

    @property
    def in_game(self) -> bool:
        """The session reached Session state 3 (in game)."""
        return IN_GAME in self.states


def level_name(level: str) -> str:
    """A level's name as compared: lower case, without its folder ('Levels/MP_01' -> 'mp_01')."""
    return level.lower().split('/')[-1]


def is_crash_line(line: str) -> bool:
    """Whether an opfor.log line shows the game writing its crash report: it names the file crashreport.xml (as a
    file name, not inside a longer name) and is not the OPFOR core's note of the game only reading it
    ('userdata: CreateFileA read ...\\crashreport.xml', which a start-up check of an old report would give)."""
    return bool(CRASH.search(line)) and not CRASH_READ.search(line)


class _Splitter:
    """sessions()'s state machine: one Session per level of each game process. A process starts at the core's load
    line; the first LEVEL line names its first session's level (the lines before it belong to that session, so one
    level per launch gives one session as before); a later LEVEL line naming another level - or the same level
    again after the session was in game, a replay - ends the session and starts the next one of the same process.
    A LEVEL line of the session's own level before it is in game (the dev trigger, then the host request it
    queues) stays in that session."""

    def __init__(self) -> None:
        self.out: list[Session] = []
        self.cur: Session | None = None

    def feed(self, line: str) -> None:
        """One line of the log."""
        m = START.search(line)
        if m:
            t = STAMP.match(line)
            self.cur = Session(m.group(1) or m.group(2), t.group(1) if t else '')
            self.out.append(self.cur)
            return
        if self.cur is None:
            return
        m = LEVEL.search(line)
        if m:
            self._level(m.group(1), line)
        self._record(self.cur, line)

    def _level(self, level: str, line: str) -> None:
        """A LEVEL line: names the current session's level, or starts the process's next session."""
        cur = cast(Session, self.cur)
        if cur.level is None:
            cur.level = level
        elif level_name(level) != level_name(cur.level) or cur.in_game:
            t = STAMP.match(line)
            self.cur = Session(cur.pid, t.group(1) if t else cur.start, level, part=cur.part + 1)
            self.out.append(self.cur)

    @staticmethod
    def _record(cur: Session, line: str) -> None:
        """What a line within a session shows, into that session."""
        cur.lines += 1
        m = STATE.search(line)
        if m:
            s = int(m.group(1), 16)
            if not cur.states or cur.states[-1] != s:
                cur.states.append(s)
        for rx, label in EVENTS:
            if label not in cur.events and rx.search(line):
                cur.events.append(label)
        m = OFFSET_LOAD.search(line)
        if m:
            cur.offset_loads.append((int(m.group(1)), int(m.group(2))))
        m = OVERRIDE.search(line)
        if m:
            cur.override = (int(m.group(1)), int(m.group(2)))
        m = COUNTERS.search(line)
        if m:
            cur.pathfinders.append(int(m.group(2)))
        if is_crash_line(line):
            cur.crashed = True
        if 'exit(0) called' in line:
            cur.exited = True


def sessions(text: str) -> list[Session]:
    """The game sessions of an opfor.log's text, with what each logged: one per level each game process (one per load
    line of the OPFOR core) played - a process that played mp_01 and then hosted mp_02 gives two (_Splitter)."""
    sp = _Splitter()
    for line in text.splitlines():
        sp.feed(line)
    return sp.out


def for_level(all_sessions: Iterable[Session], lv: str) -> list[Session]:
    """The sessions of the level (latest last): only what was logged while that level was the one played."""
    want = level_name(lv)
    return [s for s in all_sessions if s.level and level_name(s.level) == want]


def evaluate(s: Session, asset: tuple[int, int] | None) -> dict[str, Result]:
    """{name: Result} for one session. asset: the level's Infantry SectorOffsets (x, z), or None."""
    src = f'opfor.log session {s.start} (PID {s.pid})' + (f', level {s.part} of that process' if s.part > 1 else '')
    out: dict[str, Result] = {}
    ok = s.in_game and 'AI init' in s.events and not s.crashed
    out['loads-ai-init'] = Result('pass' if ok else 'fail',
                                  f'{src}: ' + ('crashed' if s.crashed else
                                                f'{"in game" if s.in_game else "never in game"}, AI init {"seen" if "AI init" in s.events else "not seen"}'),
                                  {'in_game': int(s.in_game), 'ai_init': int('AI init' in s.events), 'crashed': int(s.crashed)},
                                  [f'events: {", ".join(s.events) or "none"}'])
    if not s.offset_loads:
        out['offsets-read'] = Result('hold', f'{src}: no SectorOffsets load lines (needs OPFOR\'s probes, [dev] probes=1)')
    else:
        reads = sorted(set(s.offset_loads))
        want = tuple(asset) if isinstance(asset, tuple) else None
        # every load (the game reads SectorOffsets more than once) must have read the asset's values, not just one
        good = want is not None and all(l == want for l in s.offset_loads) and s.override is None
        why = (f'loads read {", ".join(f"{x},{z}" for x, z in reads)}' + (f'; override on ({s.override[0]},{s.override[1]})' if s.override else '')
               + (f'; the asset says {want[0]},{want[1]}' if want else '; the level has no asset'))
        out['offsets-read'] = Result('pass' if good else 'fail', f'{src}: {why}', {'loads': len(s.offset_loads),
                                                                                    'override': int(s.override is not None)})
    if not s.pathfinders:
        out['pathfinders'] = Result('hold', f'{src}: no unit-counter lines (set [dev] unit_counters_dump=1 for the run)')
    else:
        peak = max(s.pathfinders)
        out['pathfinders'] = Result('pass' if peak > 0 else 'fail',
                                    f'{src}: pathfinders {" -> ".join(map(str, s.pathfinders[:8]))}' + (' ...' if len(s.pathfinders) > 8 else ''),
                                    {'pathfinders_peak': peak})
    return out


def telemetry_tests() -> dict[str, Result]:
    """{name: Result} of the telemetry tests, all on hold until OPFOR has its telemetry module."""
    return {name: Result('hold', f'{what} - ON HOLD: needs OPFOR\'s telemetry module (milestone M6)') for name, what in TELEMETRY_TESTS}
