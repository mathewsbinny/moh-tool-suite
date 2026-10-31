r"""errors.py, the suite's error handling and logging (no game data; the logs go to a temporary folder):

- causes explained in words for the usual failures (a missing library with its pip line, a file in use, a
  missing file, no memory, OpenGL), a generic text otherwise;
- the log: errors with their traceback, warnings (Python's too), errors in other threads, the tool's start;
- guard(): a crash is logged and the tool exits with code 1; Ctrl+C exits with 130;
- old logs: the 20 newest per tool are kept (only the tool's own: navkit's pruning leaves navkit-gui's alone);
- the 3D view: an error in a panel is logged once and listed in its Problems window, the frame goes on.

  python test_errors.py
"""
import os
import shutil
import sys
import tempfile
import threading
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))


def main(argv):
    tmp = tempfile.mkdtemp(prefix='errors_test_')
    os.environ['LOCALAPPDATA'] = tmp                       # logs go here, not to the real log folder
    from mohcore import errors
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    try:
        try:
            import not_a_real_library_xyz  # noqa: F401
        except ModuleNotFoundError as e:
            cause, fix = errors.explain(e)
            check('not installed' in cause and fix == 'Install it: pip install not_a_real_library_xyz', 'a missing library: cause and pip line')
        e = ModuleNotFoundError("No module named 'imgui_bundle'", name='imgui_bundle')
        check(errors.explain(e)[1] == 'Install it: pip install imgui-bundle', 'pip names differ from module names (imgui-bundle)')
        check('could not be written or read' in errors.explain(PermissionError(13, 'denied', 'C:/x.hpd'))[0], 'a file in use or read-only')
        check('missing' in errors.explain(FileNotFoundError(2, 'no', 'C:/y.dbx'))[0], 'a missing file')
        check('out of memory' in errors.explain(MemoryError())[0], 'out of memory')
        check('OpenGL 3.3' in errors.explain(RuntimeError('cannot create OpenGL context'))[0], 'OpenGL could not start: the driver / GPU advice')
        cause, fix = errors.explain(ValueError('odd'))
        check(cause == 'ValueError: odd' and 'log file' in fix, 'anything else: the error itself, and send the log')

        errors.setup('test-tool', gui=False)
        path = errors._state['path']
        check(os.path.isfile(path) and os.path.dirname(path) == os.path.join(tmp, 'moh-tool-suite', 'logs'),
              'the log file is in %LOCALAPPDATA%\\moh-tool-suite\\logs')
        try:
            {}['missing key']
        except KeyError as ex:
            errors.report(ex, where='a test')
        errors.warn('something to look at')
        warnings.warn('a Python warning')
        t = threading.Thread(target=lambda: 1 / 0, name='worker')
        t.start()
        t.join()
        for h in errors.logger().handlers:
            h.flush()
        text = open(path, encoding='utf-8').read()
        check('test-tool started' in text, 'the start is logged')
        check("KeyError: 'missing key'" in text and 'Traceback' in text and 'in a test' in text, 'an error with its traceback and where')
        check('WARNING' in text and 'something to look at' in text, 'a warning')
        check('a Python warning' in text, 'Python\'s own warnings')
        check('ZeroDivisionError' in text and 'thread worker' in text, 'an error in another thread')

        code = None
        try:
            with errors.guard():
                raise RuntimeError('boom')
        except SystemExit as ex:
            code = ex.code
        check(code == 1 and 'RuntimeError: boom' in open(path, encoding='utf-8').read(), 'guard: a crash is logged, exit code 1')
        try:
            with errors.guard():
                raise KeyboardInterrupt
        except SystemExit as ex:
            code = ex.code
        check(code == 130, 'guard: Ctrl+C exits with 130')

        logs = os.path.join(tmp, 'moh-tool-suite', 'logs')
        for k in range(30):
            open(os.path.join(logs, f'old-tool-2020-01-{k + 1:02d}.log'), 'w').close()
        errors._prune('old-tool')
        kept = sorted(f for f in os.listdir(logs) if f.startswith('old-tool'))
        check(len(kept) == 20 and kept[0] == 'old-tool-2020-01-11.log', 'old logs: the 20 newest per tool are kept')
        # 2026-10-31: a tool whose name starts with another's (navkit, navkit-gui): pruning one keeps the other's
        for k in range(30):
            open(os.path.join(logs, f'navkit-gui-2020-02-{k + 1:02d}.log'), 'w').close()
        for k in range(3):
            open(os.path.join(logs, f'navkit-2020-03-{k + 1:02d}.log'), 'w').close()
        errors._prune('navkit')
        names = os.listdir(logs)
        check(sum(f.startswith('navkit-gui-') for f in names) == 30 and sum(f.startswith('navkit-2020') for f in names) == 3,
              "pruning one tool's logs leaves a tool named like it alone (navkit / navkit-gui)")
        errors._prune('navkit-gui')
        check(sum(f.startswith('navkit-gui-') for f in os.listdir(logs)) == 20, 'and that tool keeps its own 20 newest')

        from mohcore import viewer
        v = viewer.Viewer(viewer.Scene(), 'test')

        def bad_panel(_):
            raise ValueError('a panel broke')
        for _ in range(3):                                 # three frames: logged once, listed once
            v._guarded('the test panel', bad_panel, v)
        n = open(path, encoding='utf-8').read().count('error in the test panel')
        check(len(v._problems) == 1 and 'the test panel' in next(iter(v._problems.values())) and n == 1,
              'the 3D view: a failing panel is logged once and listed in Problems (the frame goes on)')
    finally:
        import logging
        for h in list(logging.getLogger('moh').handlers):
            h.close()
            logging.getLogger('moh').removeHandler(h)
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
