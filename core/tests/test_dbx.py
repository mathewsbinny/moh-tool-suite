"""Checks the core's Python DBX reader against DbxEdit: the XML must be identical, file for file.

  python core\\tests\\test_dbx.py <folder or list.txt> [...]

Uses moh-dbx-tools\\tests\\fingerprints_795745.txt (SHA-1 of DbxEdit's XML per input file); every input
file found under the folders is checked, then every other binary .dbx is parsed (it must not fail).

First, without game data (alone when no folder is given): a made-up file cut at every length, and one with a string
index past the table, are refused by parse and to_xml with ValueError, never IndexError / struct.error (2026-10-31).
"""
import hashlib
import os
import struct
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


def made_up():
    """A small binary .dbx: a partition with one instance holding a text field and a 4-byte number."""
    strings = ['', 'partition', 'name', 'P', 'instance', 'guid', 'G1', 'field', 'Text', 'hello', 'Number']
    blob, offs = b'', []
    for t in strings:
        offs.append(len(blob))
        blob += t.encode() + b'\0'
    area = 4 * len(strings) + len(blob)
    head = dbx.MAGIC + bytes(8) + struct.pack('>II', area, len(strings)) + struct.pack(f'>{len(strings)}I', *offs) + blob
    ix = {t: k for k, t in enumerate(strings)}
    body = bytes([ix['partition'], dbx.CONTAINER << 4 | 1, ix['name'], ix['P'], 0,
                  ix['instance'], dbx.CONTAINER << 4 | 1, ix['guid'], ix['G1'], 0,
                  ix['field'], dbx.TEXT << 4 | 1, ix['name'], ix['Text'], ix['hello'],
                  ix['field'], dbx.NUMBERS << 4 | 1, ix['name'], ix['Number'], 1, 4]) + struct.pack('>f', 1.5) + bytes([0, 0])
    return head + body, len(head)


def truncation_checks():
    """Every cut of the made-up file and a string index past the table: ValueError, never another error. Returns
    the number of failed checks."""
    data, body = made_up()
    f = dbx.parse(data)
    ok = bool(f.instances) and f.instances[0].child('Text').text == 'hello' and dbx.floats(f.instances[0].child('Number')) == [1.5]
    print(f'{"ok  " if ok else "FAIL"} the made-up file reads')
    wrong = []
    bad_index = bytearray(data)
    bad_index[body + 5] = 60                     # the instance's tag: string 60 of 11
    cases = [(f'{n} bytes', data[:n]) for n in range(len(data))] + [('a string index past the table', bytes(bad_index))]
    for what, d in cases:
        for name, fn in (('parse', dbx.parse), ('to_xml', dbx.to_xml)):
            try:
                fn(d)
                if name == 'parse':
                    wrong.append(f'{name}, {what}: no error')
            except ValueError:
                pass
            except Exception as e:
                wrong.append(f'{name}, {what}: {type(e).__name__}: {e}')
    print(f'{"ok  " if not wrong else "FAIL"} the file cut at each of its {len(data)} lengths and a string index past '
          f'the table: ValueError every time ({"; ".join(wrong[:3]) or "all"})')
    return (not ok) + bool(wrong)


def main(argv):
    if truncation_checks():
        return 1
    if len(argv) < 2:
        return 0
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
