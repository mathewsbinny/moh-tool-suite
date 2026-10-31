r"""The suite's type checks (pyproject.toml [tool.mypy]): mypy over the typed modules - core\mohcore and NavKit's
hpdtool, kymesh, hkpack and navsample - with their imports resolved the way the tools run them. Skipped (exit 0, said
so) when mypy is not installed: python -m pip install mypy==2.4.1 (the version pyproject.toml pins).

  python test_types.py
"""
import os
import subprocess
import sys

SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        import mypy  # noqa: F401 - only whether it is installed
    except ImportError:
        print('skip: mypy is not installed (python -m pip install mypy==2.4.1)')
        return 0
    # the files and options come from pyproject.toml; run from the suite's folder so its paths resolve
    r = subprocess.run([sys.executable, '-m', 'mypy', '--config-file', os.path.join(SUITE, 'pyproject.toml')],
                       capture_output=True, text=True, cwd=SUITE)
    out = (r.stdout + r.stderr).strip()
    if r.returncode:
        print(out)
        print(f'FAIL mypy: {out.splitlines()[-1] if out else "exit " + str(r.returncode)}')
        return 1
    print(f'ok   mypy: {out.splitlines()[-1] if out else "no findings"}')
    print('all passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
