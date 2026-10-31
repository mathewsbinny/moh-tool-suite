"""Error handling and logging for the suite's tools: every tool keeps a log, and when something goes wrong the user
is told what happened, the likely cause and what to do, in words rather than a traceback.

    log = errors.setup('navigation-studio')          # once, at start: log file, warnings, uncaught errors
    with errors.guard('navigation-studio'):          # around main(): a crash is logged and shown, exit code 1
        ...

Logs: %LOCALAPPDATA%\\moh-tool-suite\\logs\\<tool>-<date>.log (the 20 newest per tool are kept). Uncaught errors in
any thread, Python warnings and each tool's own messages go there; errors also show a window (GUI tools) or a
short message (command line) naming the log.
"""
import contextlib
import datetime
import glob
import logging
import os
import re
import sys
import threading
import traceback
import warnings

KEEP = 20
_state = {'tool': None, 'path': None, 'gui': True}

# (test on the error, the cause in words, what to do)
CAUSES = [
    (lambda e: isinstance(e, ModuleNotFoundError),
     lambda e: f'A Python library the tool needs is not installed ({e.name}).',
     lambda e: f'Install it: pip install {PIP_NAMES.get(e.name, e.name)}'),
    (lambda e: 'opengl' in str(e).lower() or 'glfw' in str(e).lower() or type(e).__module__.startswith(('moderngl', 'glfw')),
     lambda e: 'The 3D view could not start OpenGL 3.3 (the graphics driver is too old, or Python runs on a GPU without it).',
     lambda e: 'Update the graphics driver; on a laptop with two GPUs set python.exe to "High performance" in Windows '
               'Settings > System > Display > Graphics.'),
    (lambda e: 'no game copy' in str(e).lower() or 'project opfor' in str(e).lower() and 'not found' in str(e).lower(),
     lambda e: 'The tools could not find Project OPFOR\'s game copy.',
     lambda e: 'Put the suite folder next to the project-opfor-moh folder (with its game\\ copy), or set OPFOR_ROOT to it.'),
    (lambda e: 'not prepared' in str(e).lower() or 'unknown level' in str(e).lower(),
     lambda e: f'The level is not available: {e}',
     lambda e: 'Choose a level from the list, or prepare it first (Levels tab: Prepare level).'),
    (lambda e: isinstance(e, PermissionError),
     lambda e: f'A file could not be written or read: {getattr(e, "filename", "") or e}',
     lambda e: 'Close the program using it (the game, an editor), or check the folder is not read-only.'),
    (lambda e: isinstance(e, FileNotFoundError),
     lambda e: f'A file the tool needs is missing: {getattr(e, "filename", "") or e}',
     lambda e: 'Check the path; if it is a level\'s file, prepare the level again.'),
    (lambda e: isinstance(e, MemoryError),
     lambda e: 'The PC ran out of memory.',
     lambda e: 'Close other programs, or open a smaller level.'),
]
PIP_NAMES = {'imgui_bundle': 'imgui-bundle', 'PIL': 'pillow', 'capstone': 'capstone', 'pefile': 'pefile'}


def log_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'moh-tool-suite', 'logs')
    os.makedirs(d, exist_ok=True)
    return d


def explain(e):
    """(cause, what to do) for an exception, in words; a generic pair when it is not a known case."""
    for test, cause, fix in CAUSES:
        try:
            if test(e):
                return cause(e), fix(e)
        except Exception:
            continue
    return f'{type(e).__name__}: {e}', 'If it happens again, send the log file to the tool\'s author.'


def setup(tool, gui=True, level=logging.INFO):
    """The tool's log file, warnings into it, and uncaught errors (main thread and others) logged and reported."""
    _state.update(tool=tool, gui=gui)
    stamp = datetime.date.today().isoformat()
    path = os.path.join(log_dir(), f'{tool}-{stamp}.log')
    _state['path'] = path
    log = logging.getLogger('moh')
    log.setLevel(level)
    if not any(getattr(h, 'baseFilename', None) == os.path.abspath(path) for h in log.handlers):
        h = logging.FileHandler(path, encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(message)s'))
        log.addHandler(h)
    logging.captureWarnings(True)
    wlog = logging.getLogger('py.warnings')
    for h in log.handlers:
        if h not in wlog.handlers:
            wlog.addHandler(h)
    warnings.simplefilter('default')
    sys.excepthook = lambda t, v, tb: report(v, tb=tb)
    threading.excepthook = lambda a: report(a.exc_value, tb=a.exc_traceback, where=f'thread {a.thread.name if a.thread else "?"}')
    log.info(f'--- {tool} started (Python {sys.version.split()[0]}, {sys.platform}) ---')
    _prune(tool)
    return log


def _prune(tool):
    # only this tool's own logs, <tool>-<YYYY-MM-DD>.log as setup() names them: a glob of '<tool>-*.log' also took
    # another tool's whose name starts with this one's ('navkit' pruned 'navkit-gui-...' and kept too few of its own)
    own = re.compile(re.escape(tool) + r'-\d{4}-\d{2}-\d{2}\.log', re.IGNORECASE)
    files = sorted(p for p in glob.glob(os.path.join(log_dir(), f'{tool}-*.log')) if own.fullmatch(os.path.basename(p)))
    for p in files[:-KEEP]:
        try:
            os.remove(p)
        except OSError:
            pass


def logger():
    return logging.getLogger('moh')


def report(e, tb=None, where=None, show=True):
    """Logs an error with its traceback and tells the user (a window for GUI tools, else a short message)."""
    tool = _state['tool'] or 'tool'
    cause, fix = explain(e)
    lines = ''.join(traceback.format_exception(type(e), e, tb or e.__traceback__))
    logger().error(f'{tool} error{" in " + where if where else ""}: {cause}\n{lines}')
    text = f'{cause}\n\nWhat to do: {fix}\n\nDetails were saved to:\n{_state["path"] or "(no log file)"}'
    print(f'error: {cause}\n  what to do: {fix}\n  details: {_state["path"]}', file=sys.stderr)
    if show and _state['gui']:
        _window(f'{tool} - error', text)
    return _state['path']


def warn(message, show=False):
    """A warning: logged, printed, and shown in a window when show is set (GUI tools)."""
    logger().warning(message)
    print(f'warning: {message}', file=sys.stderr)
    if show and _state['gui']:
        _window(f'{_state["tool"] or "tool"} - warning', message, kind='warning')


def _window(title, text, kind='error'):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk._default_root
        own = root is None
        if own:
            root = tk.Tk()
            root.withdraw()
        (messagebox.showerror if kind == 'error' else messagebox.showwarning)(title, text, parent=root)
        if own:
            root.destroy()
    except Exception:
        pass                                    # no display: the message was printed and logged


@contextlib.contextmanager
def guard(tool=None, gui=None):
    """Around a tool's main(): any error is logged and reported, and the tool exits with code 1."""
    if tool and _state['tool'] != tool:
        setup(tool, gui if gui is not None else True)
    try:
        yield
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger().info('stopped (Ctrl+C)')
        raise SystemExit(130)
    except BaseException as e:
        report(e)
        raise SystemExit(1)
