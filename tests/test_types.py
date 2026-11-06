r"""The suite's type checks (pyproject.toml [tool.mypy]): mypy over every module outside the tests (core\mohcore,
NavKit, the harness and crash reader, the 3D tools and GUIs, the stock-MP AI, the menu tools, the console readers,
run_tests.py), with their imports resolved the way the tools run them. A module whose name clashes with another's
(SEPARATE: moh-config\config.py and moh-stockmp-ai\config.py are both `config`) is checked in a run of its own.
Skipped (exit 0, said so) when mypy is not installed: python -m pip install mypy==2.4.1 (the version pyproject.toml pins).

  python test_types.py
"""
import os
import subprocess
import sys

SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# checked on their own, each in a run with pyproject's settings (their module names clash with a configured one's)
SEPARATE = ['moh-config/config.py']


def main() -> int:
    """Runs mypy; prints the result and returns the exit code."""
    try:
        import mypy  # noqa: F401 - only whether it is installed
    except ImportError:
        print('skip: mypy is not installed (python -m pip install mypy==2.4.1)')
        return 0
    # the files and options come from pyproject.toml; run from the suite's folder so its paths resolve
    config = os.path.join(SUITE, 'pyproject.toml')
    for files in [[], *([f] for f in SEPARATE)]:
        r = subprocess.run([sys.executable, '-m', 'mypy', '--config-file', config, *files],
                           capture_output=True, text=True, cwd=SUITE)
        out = (r.stdout + r.stderr).strip()
        what = ' '.join(files) or 'the configured files'
        if r.returncode:
            print(out)
            print(f'FAIL mypy ({what}): {out.splitlines()[-1] if out else "exit " + str(r.returncode)}')
            return 1
        print(f'ok   mypy ({what}): {out.splitlines()[-1] if out else "no findings"}')
    print('all passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
