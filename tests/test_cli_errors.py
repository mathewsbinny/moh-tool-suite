r"""Every command-line tool's error handling (no game data): each is run with input that must fail - a file that is
missing, or not the kind the tool reads - and must exit with an error code, tell the cause and what to do in
words, name its log, and record the error in that log (%LOCALAPPDATA%\moh-tool-suite\logs; a temporary folder
here). The C# tools (FbrbTool, LevelDiff, DbxEdit) and the Python ones (NavKit's, the crash reader, the console
readers, the test harness) alike.

  python tests\test_cli_errors.py
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
PY = sys.executable


def main(argv):
    failed = 0

    def check(ok, what):
        nonlocal failed
        failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}', flush=True)

    for bat in ('moh-fbrb-tool', 'moh-dbx-tools', os.path.join('moh-diff-workbench', 'level-diff')):
        exe = glob.glob(os.path.join(SUITE, bat, '*.exe'))
        if not exe:
            subprocess.run(['cmd', '/c', os.path.join(SUITE, bat, 'build.bat')], capture_output=True)
    tmp = tempfile.mkdtemp(prefix='cli_errors_')
    env = {**os.environ, 'LOCALAPPDATA': tmp}
    logs = os.path.join(tmp, 'moh-tool-suite', 'logs')
    junk = os.path.join(tmp, 'junk.bin')
    open(junk, 'wb').write(b'this is not any kind of game file')
    missing = os.path.join(tmp, 'missing.file')
    notrun = os.path.join(tmp, 'notrun.json')
    open(notrun, 'w').write('{"format": "something else"}')
    cases = [
        ('FbrbTool, a file that is not a bundle', [os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe'), 'list', junk], 'fbrbtool', 'not the kind the tool reads'),
        ('FbrbTool, a missing bundle', [os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe'), 'list', missing], 'fbrbtool', 'missing'),
        ('LevelDiff, a missing file', [os.path.join(SUITE, 'moh-diff-workbench', 'level-diff', 'LevelDiff.exe'), missing, junk], 'leveldiff', 'what to do'),
        ('DbxEdit, a missing file', [os.path.join(SUITE, 'moh-dbx-tools', 'DbxEdit.exe'), 'edit', missing, junk + '.out', 'drop=x'], 'dbxedit', 'missing'),
        ('hpdtool, a file that is not path data', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'hpdtool.py'), 'info', junk], 'hpdtool', 'what to do'),
        ('crash reader, a report that is not XML', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'crash-reader', 'crashreader.py'), junk, '--no-code'], 'crashreader', 'what to do'),
        ('STFS reader, a missing package', [PY, os.path.join(SUITE, 'moh-console-readers', 'stfs', 'stfs.py'), 'info', missing], 'stfs', 'missing'),
        ('aptcheck, a file that is not a menu movie', [PY, os.path.join(SUITE, 'moh-menu-tools', 'aptcheck.py'), 'check', junk], 'aptcheck', 'not a menu movie'),
        ('aptrefs, an exe that is not one', [PY, os.path.join(SUITE, 'moh-menu-tools', 'aptrefs.py'), 'table', '--exe', junk], 'aptrefs', 'not a windows program'),
        ('mohloc, a file that is not a language file', [PY, os.path.join(SUITE, 'moh-menu-tools', 'mohloc.py'), 'get', 'X', '--file', junk], 'mohloc', 'not a localization'),
        ('exeref, a missing exe', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'crash-reader', 'exeref.py'), 'str', 'x', '--exe', missing], 'exeref', 'missing'),
        ('navdiff, missing files', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'navdiff.py'), missing, missing], 'navdiff', 'missing'),
        ('navboundary, a missing NavKit file', [PY, os.path.join(SUITE, 'moh-auto-navkit', 'tools', 'navboundary.py'), 'sp_004_b', missing], 'navboundary', 'what to do'),
        ('test harness, a missing saved run', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'harness.py'), 'show', missing + '.json'], 'harness', 'missing'),
        ('test harness, a file that is not a run', [PY, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness', 'harness.py'), 'compare', notrun, notrun], 'harness', 'what to do'),
        ('test runner, --only without a name', [PY, os.path.join(SUITE, 'run_tests.py'), 'quick', '--only'], 'run-tests', 'needs part of a test name'),
        ('test runner, a name no test has', [PY, os.path.join(SUITE, 'run_tests.py'), 'quick', '--only', 'no such test'], 'run-tests', 'no quick test has'),
        # "--only game" filters the quick tier by name; it does not choose the game tier (no quick test has "game")
        ('test runner, --only game', [PY, os.path.join(SUITE, 'run_tests.py'), '--only', 'game', '--list'], 'run-tests', 'no quick test has "game"'),
    ]
    try:
        for label, args, log_name, says in cases:
            r = subprocess.run(args, capture_output=True, text=True, errors='replace', env=env, timeout=300)
            out = r.stdout + r.stderr
            log = sorted(glob.glob(os.path.join(logs, f'{log_name}-*.log')))
            text = open(log[-1], encoding='utf-8', errors='replace').read() if log else ''
            ok = r.returncode != 0 and says in out.lower() and 'details' in out.lower() and 'ERROR' in text
            check(ok, f'{label}: exit {r.returncode}, the cause in words, the log ({os.path.basename(log[-1]) if log else "none"})')
            if not ok:
                print('       ' + out.strip().replace('\n', '\n       ')[:600])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
