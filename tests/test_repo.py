r"""The repository's own rules, checked before anything is pushed (no game data needed):

- no game file types and no large files anywhere in the history (EA / DICE content is never published);
- no co-author or generated-by lines in commit messages (the author is the only author);
- no build output or caches tracked (.exe, .pyc, __pycache__);
- a README in every tool folder, a LICENSE at the root;
- every Python file compiles.

  python tests\test_repo.py
"""
import os
import py_compile
import re
import subprocess
import sys
import tempfile

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
GAME_TYPES = re.compile(r'\.(dbx|res|fbrb|hpd|sb|raw|bik|fsb|fev|dds|tga|hkx)$', re.I)
MAX_BLOB = 1_000_000
LARGE_ALLOWED = {'moh-dbx-tools/tests/fingerprints_795745.txt',     # SHA-1 hashes only (the BFBC2 corpus)
                 'bfbc2-dbx-tools/tests/fingerprints_795745.txt'}   # the same file before the suite was renamed
ATTRIBUTION = re.compile(r'co-authored-by|generated with \[?claude|noreply@anthropic', re.I)
TRACKED_NEVER = re.compile(r'(\.exe|\.pyc|\.dll|\.pdb)$|(^|/)__pycache__/', re.I)


def git(*args):
    return subprocess.run(['git', '-C', SUITE, *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', check=True).stdout


def main(argv):
    results = []

    blobs = git('rev-list', '--objects', '--all').splitlines()
    sizes = subprocess.run(['git', '-C', SUITE, 'cat-file', '--batch-check=%(objecttype) %(objectsize) %(rest)'],
                           input='\n'.join(l.split(' ')[0] for l in blobs), capture_output=True, text=True).stdout
    paths = {}
    for line in blobs:
        sha, _, path = line.partition(' ')
        if path:
            paths[sha] = path
    game_files, large = set(), set()
    for line, sha in zip(sizes.splitlines(), (l.split(' ')[0] for l in blobs)):
        kind, size, _ = (line.split(' ', 2) + [''])[:3]
        path = paths.get(sha, '')
        if kind != 'blob' or not path:
            continue
        if GAME_TYPES.search(path):
            game_files.add(path)
        if int(size) > MAX_BLOB and path not in LARGE_ALLOWED:
            large.add(f'{path} ({int(size):,} bytes)')
    results.append(('no game file types in the history', sorted(game_files)))
    results.append((f'no files over {MAX_BLOB:,} bytes in the history (allowed: {", ".join(sorted(LARGE_ALLOWED))})', sorted(large)))

    messages = git('log', '--all', '--format=%H%x00%B%x01').split('\x01')
    bad = [m.split('\x00')[0][:10] for m in messages if '\x00' in m and ATTRIBUTION.search(m.split('\x00', 1)[1])]
    results.append(('no co-author / generated-by lines in commit messages', bad))
    authors = sorted(set(git('log', '--all', '--format=%an <%ae>').splitlines()))
    results.append((f'one author ({authors[0] if authors else "none"})', authors[1:]))

    tracked = git('ls-files').splitlines()
    results.append(('no build output or caches tracked', [t for t in tracked if TRACKED_NEVER.search(t)]))

    folders = [d for d in sorted(os.listdir(SUITE)) if os.path.isdir(os.path.join(SUITE, d)) and (d.startswith('moh-') or d == 'core')]
    results.append(('a README.md in every tool folder', [d for d in folders if not os.path.isfile(os.path.join(SUITE, d, 'README.md'))]))
    results.append(('LICENSE at the root', [] if os.path.isfile(os.path.join(SUITE, 'LICENSE')) else ['LICENSE missing']))

    broken = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in tracked:
            if t.endswith(('.py', '.pyw')):
                try:
                    py_compile.compile(os.path.join(SUITE, t), cfile=os.path.join(tmp, 'x.pyc'), doraise=True)
                except py_compile.PyCompileError as e:
                    broken.append(f'{t}: {str(e).strip().splitlines()[-1]}')
    results.append((f'every Python file compiles ({sum(t.endswith((".py", ".pyw")) for t in tracked)} files)', broken))

    failed = 0
    for name, problems in results:
        failed += bool(problems)
        print(f'{"FAIL" if problems else "ok  "} {name}')
        for p in problems[:15]:
            print('       ' + p)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
