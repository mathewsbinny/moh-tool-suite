r"""The suite's structure rules, checked over every Python file the repository holds (research\ left out):
  - every function outside the tests is at most 80 lines long (def line to last line), a test's at most 150;
  - every public function and method outside the tests (no leading underscore, at module level or in a class body,
    not an @overload stub) has a docstring;
  - every module outside the tests is type-checked: in pyproject.toml's [tool.mypy] files, or in test_types.py's
    SEPARATE list (the ones whose module name clashes with another's).

  python test_structure.py
"""
import ast
import glob
import os
import subprocess
import sys
import tomllib

SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_types  # noqa: E402 - its SEPARATE list

MAX_LINES = 80
MAX_TEST_LINES = 150


def sources() -> list[str]:
    """The repository's Python files, relative with forward slashes (research\\ left out)."""
    out = subprocess.run(['git', 'ls-files', '-z', '*.py', '*.pyw'], capture_output=True, text=True, cwd=SUITE,
                         check=True).stdout
    return [f for f in out.split('\0') if f and not f.startswith('research/')]


def is_test(path: str) -> bool:
    """True for a test file: in a tests folder or named test_*."""
    return '/tests/' in path or path.startswith('tests/') or os.path.basename(path).startswith('test_')


def typed_files() -> set[str]:
    """The files mypy checks: pyproject's [tool.mypy] files (globs expanded) and test_types.SEPARATE."""
    with open(os.path.join(SUITE, 'pyproject.toml'), 'rb') as f:
        patterns = tomllib.load(f)['tool']['mypy']['files']
    found = set(test_types.SEPARATE)
    for p in patterns:
        found |= {os.path.relpath(x, SUITE).replace(os.sep, '/') for x in glob.glob(os.path.join(SUITE, p))}
    return found


def is_overload(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for an @overload (or @typing.overload) stub."""
    return any((isinstance(d, ast.Name) and d.id == 'overload') or (isinstance(d, ast.Attribute) and d.attr == 'overload')
               for d in fn.decorator_list)


def public_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The public functions at module level and in class bodies (nested classes included)."""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    todo: list[ast.AST] = [tree]
    while todo:
        node = todo.pop()
        for child in getattr(node, 'body', []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith('_'):
                found.append(child)
            elif isinstance(child, ast.ClassDef):
                todo.append(child)
    return found


def file_problems(path: str, typed: set[str]) -> list[str]:
    """The rule breaks in one file, each as 'path:line: what'."""
    with open(os.path.join(SUITE, path), encoding='utf-8-sig') as f:
        tree = ast.parse(f.read(), path)
    test = is_test(path)
    limit = MAX_TEST_LINES if test else MAX_LINES
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n = (node.end_lineno or node.lineno) - node.lineno + 1
            if n > limit:
                problems.append(f'{path}:{node.lineno}: {node.name} is {n} lines (at most {limit})')
    if not test:
        for fn in public_functions(tree):
            if ast.get_docstring(fn) is None and not is_overload(fn):
                problems.append(f'{path}:{fn.lineno}: {fn.name} has no docstring')
        if path not in typed:
            problems.append(f'{path}: not type-checked (add it to pyproject.toml [tool.mypy] files)')
    return problems


def main() -> int:
    """Checks every file; prints each rule break and a summary line."""
    typed = typed_files()
    files = sources()
    problems = [p for f in files for p in file_problems(f, typed)]
    for p in problems:
        print('FAIL', p)
    if problems:
        print(f'{len(problems)} check(s) failed')
        return 1
    print(f'ok   {len(files)} files: functions at most {MAX_LINES} lines ({MAX_TEST_LINES} in tests), '
          'public functions documented, every module type-checked')
    print('all passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
