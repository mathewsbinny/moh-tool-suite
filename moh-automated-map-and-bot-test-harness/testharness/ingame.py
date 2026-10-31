"""In-game tests: how they plug in. The harness never starts the game; these read what a run the user made left behind.

Now (offline reading of a finished run):
  sessions(text)        the sessions of an opfor.log (one per "---- OPFOR core loaded" / "---- opfor.dll loaded"
                        line; MOH's is game\\logs\\opfor_mp.log): level, session states,
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
import re
from dataclasses import dataclass, field
from typing import List, Optional

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
    pid: str
    start: str = ''
    level: Optional[str] = None
    states: List[int] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    offset_loads: List[tuple] = field(default_factory=list)
    override: Optional[tuple] = None
    pathfinders: List[int] = field(default_factory=list)
    crashed: bool = False
    exited: bool = False
    lines: int = 0

    @property
    def in_game(self):
        return IN_GAME in self.states


def sessions(text):
    out, cur = [], None
    for line in text.splitlines():
        m = START.search(line)
        if m:
            cur = Session(m.group(1) or m.group(2))
            t = STAMP.match(line)
            cur.start = t.group(1) if t else ''
            out.append(cur)
            continue
        if cur is None:
            continue
        cur.lines += 1
        m = LEVEL.search(line)
        if m:
            cur.level = m.group(1)
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
        if 'crashreport.xml' in line:
            cur.crashed = True
        if 'exit(0) called' in line:
            cur.exited = True
    return out


def for_level(all_sessions, lv):
    """The sessions that started the level (latest last)."""
    want = lv.lower().split('/')[-1]
    return [s for s in all_sessions if s.level and s.level.lower().split('/')[-1] == want]


def evaluate(s, asset):
    """{name: Result} for one session. asset: the level's Infantry SectorOffsets (x, z), or None."""
    src = f'opfor.log session {s.start} (PID {s.pid})'
    out = {}
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


def telemetry_tests():
    return {name: Result('hold', f'{what} - ON HOLD: needs OPFOR\'s telemetry module (milestone M6)') for name, what in TELEMETRY_TESTS}
