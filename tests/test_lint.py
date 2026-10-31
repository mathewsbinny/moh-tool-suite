r"""The suite's lint policy (pyproject.toml [tool.ruff]): ruff over every Python file - unused imports and variables,
undefined names, f-strings without fields, syntax errors, and blind `except Exception` outside the boundaries marked
`# noqa: BLE001` with their reason. Skipped (exit 0, said so) when ruff is not installed: python -m pip install
ruff==0.17.3 (the version pyproject.toml pins).

  python test_lint.py
"""
import os
import subprocess
import sys

SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        import ruff  # noqa: F401 - only whether it is installed
    except ImportError:
        print('skip: ruff is not installed (python -m pip install ruff==0.17.3)')
        return 0
    r = subprocess.run([sys.executable, '-m', 'ruff', 'check', '--output-format', 'concise', SUITE],
                       capture_output=True, text=True, cwd=SUITE)
    out = (r.stdout + r.stderr).strip()
    if r.returncode:
        print(out)
        print(f'FAIL ruff: {out.splitlines()[-1] if out else "exit " + str(r.returncode)}')
        return 1
    print('ok   ruff: no findings (F, E9, BLE)')
    print('all passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
