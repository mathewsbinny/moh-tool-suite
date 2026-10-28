"""Checks the core's Python DBX reader against DbxEdit: the XML must be identical, file for file.

  python core\\tests\\test_dbx.py <folder or list.txt> [...]

Uses moh-dbx-tools\\tests\\fingerprints_795745.txt (SHA-1 of DbxEdit's XML per input file); every input
file found under the folders is checked, then every other binary .dbx is parsed (it must not fail).
"""
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
from mohcore import dbx  # noqa: E402


def files(roots):
    for r in roots:
        if r.lower().endswith('.txt') and os.path.isfile(r):
            yield from (l.strip() for l in open(r, encoding='utf-8') if l.strip())
        else:
            for d, _, names in os.walk(r):
                for n in names:
                    if n.lower().endswith('.dbx'):
                        yield os.path.join(d, n)


def main(argv):
    golden = {}
    for line in open(os.path.join(SUITE, 'moh-dbx-tools', 'tests', 'fingerprints_795745.txt')):
        p = line.split()
        if len(p) >= 3 and p[0] == 'xml':
            golden[p[1]] = p[2]
    seen, checked, wrong, parsed, failed = set(), 0, 0, 0, 0
    t = time.time()
    for f in files(argv[1:]):
        data = open(f, 'rb').read()
        h = hashlib.sha1(data).hexdigest()
        if h in seen or not dbx.is_binary(data):
            continue
        seen.add(h)
        try:
            dbx.parse(data)
            parsed += 1
        except Exception as e:
            failed += 1
            print('PARSE FAILED', f, e)
        if h in golden:
            checked += 1
            out = hashlib.sha1(dbx.to_xml(data).encode('utf-8')).hexdigest()
            if out != golden[h]:
                wrong += 1
                if wrong <= 10:
                    print('XML DIFFERS', f)
    print(f'{parsed} files parsed ({failed} failed); XML checked against DbxEdit for {checked}: {wrong} differ  ({time.time() - t:.0f} s)')
    return 0 if wrong == 0 and failed == 0 and checked > 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
