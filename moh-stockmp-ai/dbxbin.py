"""Binary DBX files as an editable, typed element tree that writes back byte for byte (moh-dbx-tools/FORMAT.md).

The suite's reader (core/mohcore/dbx.py) is read-only and keeps strings, not string indexes; DbxEdit edits in C#.
This module is the Python layer's writer, used to insert new typed instances into a level:

    d = parse(open('MP_005.dbx', 'rb').read())
    sp = d.instance('D8752D50-...')               # an Element
    new = d.clone(sp, src=d)                       # deep copy (strings re-interned into d)
    d.set_text(new.child('Name'), 'AI Team1 #1')
    write(d) == original bytes                     # when nothing was changed

Each element keeps the string-table *indexes* of its tag, attributes and text, so an unchanged file comes back exactly
(including duplicate strings in the table), and new strings are appended at the end of the table, as DbxEdit does
(FORMAT.md section 4). Values keep their kind: text (string index), numbers (count, size, big-endian values) and the
two-byte bool / byte kind; new values are written in the kind of the element they replace, never guessed.
"""
import struct
import uuid as _uuid

CONTAINER, TEXT, NUMBERS = 0xA, 2, 7
MAGIC = b'{binary}'


class Element:
    __slots__ = ('dbx', 'tag_i', 'kind', 'attrs', 'text_i', 'raw', 'cbyte', 'children')

    def __init__(self, dbx, tag_i, kind):
        self.dbx = dbx
        self.tag_i = tag_i
        self.kind = kind
        self.attrs = []            # [(name index, value index)]
        self.text_i = 0            # TEXT
        self.raw = b''             # NUMBERS: count/size varints + values; other non-container kinds: the two bytes
        self.cbyte = 0             # CONTAINER: the byte after the attributes (kept as it is)
        self.children = None       # CONTAINER: list of Element

    # -- reading
    @property
    def tag(self):
        return self.dbx.strings[self.tag_i]

    def attr(self, name, default=None):
        s = self.dbx.strings
        for k, v in self.attrs:
            if s[k] == name:
                return s[v]
        return default

    @property
    def name(self):
        return self.attr('name')

    @property
    def text(self):
        return self.dbx.strings[self.text_i] if self.kind == TEXT else None

    def child(self, name):
        """The first direct child whose attribute name == name."""
        for c in self.children or ():
            if c.attr('name') == name:
                return c
        return None

    def items(self):
        return list(self.children or ())

    def numbers(self):
        """(size, [raw values]) of a NUMBERS element."""
        count, p = _varint(self.raw, 0)
        size, p = _varint(self.raw, p)
        return size, [self.raw[p + i * size:p + (i + 1) * size] for i in range(count)]

    def floats(self):
        size, vals = self.numbers()
        if size != 4:
            raise ValueError('not 4-byte numbers')
        return [struct.unpack('>f', v)[0] for v in vals]

    def ints(self):
        size, vals = self.numbers()
        if size != 4:
            raise ValueError('not 4-byte numbers')
        return [struct.unpack('>i', v)[0] for v in vals]

    @property
    def value(self):
        """bool / byte kinds: the second byte."""
        return self.raw[1] if self.kind not in (CONTAINER, TEXT, NUMBERS) else None

    def walk(self):
        yield self
        for c in self.children or ():
            yield from c.walk()

    def __repr__(self):
        return f'<{self.tag} {dict((self.dbx.strings[k], self.dbx.strings[v]) for k, v in self.attrs)}>'


class Dbx:
    def __init__(self):
        self.header = 0                # the value at offset 12, kept as it is
        self.strings = []
        self._index = {}               # string -> first index
        self.roots = []
        self.trailing = b''
        self._area = None              # original string area bytes (copied back while the table is unchanged)
        self._n_orig = 0

    # -- strings
    def intern(self, s):
        i = self._index.get(s)
        if i is None:
            i = len(self.strings)
            self.strings.append(s)
            self._index[s] = i
        return i

    # -- tree access
    @property
    def partition(self):
        return self.roots[0]

    def instances(self):
        return [e for e in self.partition.children if e.tag == 'instance']

    def instance(self, guid):
        g = guid.upper()
        for e in self.partition.children:
            if e.tag == 'instance' and (e.attr('guid') or '').upper() == g:
                return e
        return None

    def of_type(self, suffix):
        return [e for e in self.instances() if (e.attr('type') or '').endswith(suffix)]

    # -- edits (all keep the element's kind)
    def set_attr(self, e, name, value):
        vi = self.intern(value)
        for k, (a, v) in enumerate(e.attrs):
            if self.strings[a] == name:
                e.attrs[k] = (a, vi)
                return
        e.attrs.append((self.intern(name), vi))

    def set_text(self, e, value):
        if e.kind != TEXT:
            raise ValueError(f'{e!r} is not text')
        e.text_i = self.intern(value)

    def set_bool(self, e, value):
        if e.kind in (CONTAINER, TEXT, NUMBERS) or len(e.raw) != 2:
            raise ValueError(f'{e!r} is not a bool / byte')
        e.raw = bytes([e.raw[0], 1 if value is True else 0 if value is False else int(value)])

    def set_numbers(self, e, values, kinds):
        """values: list; kinds: 'f' or 'i' per value (or one letter for all). Writes 4-byte numbers; the count may
        change. Raises if the element holds numbers of another size."""
        if e.kind != NUMBERS:
            raise ValueError(f'{e!r} is not numbers')
        size, old = e.numbers()
        if old and size != 4:
            raise ValueError(f'{e!r} holds {size}-byte numbers')
        if isinstance(kinds, str) and len(kinds) == 1:
            kinds = kinds * len(values)
        body = b''.join(struct.pack('>f' if k == 'f' else '>i', v) for v, k in zip(values, kinds))
        e.raw = _varint_bytes(len(values)) + _varint_bytes(4) + body

    def set_float(self, e, value):
        self.set_numbers(e, [float(value)], 'f')

    def set_int(self, e, value):
        self.set_numbers(e, [int(value)], 'i')

    def set_transform(self, e, matrix_rows):
        """A LinearTransform (16 x 4-byte numbers: right, up, forward, translation, each x y z + a fourth word).
        matrix_rows: 4 rows of 3 floats. The fourth word of each row keeps its old bytes (DICE writes padding there)."""
        size, old = e.numbers()
        if size != 4 or len(old) != 16:
            raise ValueError(f'{e!r} is not a 16-number transform')
        out = []
        for r in range(4):
            out += [struct.pack('>f', float(v)) for v in matrix_rows[r]]
            out.append(old[4 * r + 3])
        e.raw = _varint_bytes(16) + _varint_bytes(4) + b''.join(out)

    def set_ref(self, e, ref):
        """A reference field or item: its attribute 'ref'."""
        if e.attr('ref') is None:
            raise ValueError(f'{e!r} has no ref attribute')
        self.set_attr(e, 'ref', ref)

    def clone(self, e, src=None):
        """A deep copy of element e (from file src, default this file) with its strings interned into this file."""
        src = src or self
        n = Element(self, self.intern(src.strings[e.tag_i]), e.kind)
        n.attrs = [(self.intern(src.strings[k]), self.intern(src.strings[v])) for k, v in e.attrs]
        n.raw = e.raw
        n.cbyte = e.cbyte
        if e.kind == TEXT:
            n.text_i = self.intern(src.strings[e.text_i])
        if e.children is not None:
            n.children = [self.clone(c, src) for c in e.children]
        return n


def _varint(data, pos):
    value, shift = 0, 0
    while True:
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, pos
        shift += 7


def _varint_bytes(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def is_binary(data):
    return len(data) > 24 and data[:8] == MAGIC


def parse(data):
    if not is_binary(data):
        raise ValueError('not a binary .dbx file')
    d = Dbx()
    payload_start, d.header, area, count = struct.unpack_from('>IIII', data, 8)
    offsets = struct.unpack_from(f'>{count}I', data, 24)
    base = 24 + 4 * count
    for off in offsets:
        start = base + off
        end = data.find(b'\0', start)
        if end < 0:
            raise ValueError('a string without its end')
        s = data[start:end].decode('utf-8', 'surrogateescape')
        d._index.setdefault(s, len(d.strings))
        d.strings.append(s)
    d._area = data[24:24 + area]
    d._n_orig = count
    pos = 24 + area
    stack = []
    n = len(data)
    while pos < n:
        tag, pos = _varint(data, pos)
        if tag == 0:
            if not stack:
                d.trailing = data[pos - 1:]
                pos = n
                break
            stack.pop()
            continue
        head = data[pos]
        pos += 1
        e = Element(d, tag, head >> 4)
        for _ in range(head & 0xF):
            k, pos = _varint(data, pos)
            v, pos = _varint(data, pos)
            e.attrs.append((k, v))
        if e.kind == CONTAINER:
            e.cbyte = data[pos]
            pos += 1
            e.children = []
            (stack[-1].children if stack else d.roots).append(e)
            stack.append(e)
            continue
        if e.kind == TEXT:
            e.text_i, pos = _varint(data, pos)
        elif e.kind == NUMBERS:
            start = pos
            c, pos = _varint(data, pos)
            size, pos = _varint(data, pos)
            pos += c * size
            e.raw = data[start:pos]
        else:
            e.raw = data[pos:pos + 2]
            pos += 2
        (stack[-1].children if stack else d.roots).append(e)
    if stack:
        raise ValueError('the element tree is not closed')
    return d


def write(d):
    out = bytearray()

    def emit(e):
        out.extend(_varint_bytes(e.tag_i))
        if len(e.attrs) > 15:
            raise ValueError('more than 15 attributes')
        out.append((e.kind << 4) | len(e.attrs))
        for k, v in e.attrs:
            out.extend(_varint_bytes(k))
            out.extend(_varint_bytes(v))
        if e.kind == CONTAINER:
            out.append(e.cbyte)
            for c in e.children:
                emit(c)
            out.append(0)
        elif e.kind == TEXT:
            out.extend(_varint_bytes(e.text_i))
        else:
            out.extend(e.raw)
    for r in d.roots:
        emit(r)
    tree = bytes(out)
    if d._area is not None and len(d.strings) == d._n_orig:
        area = d._area
        count = d._n_orig
    else:
        offs, blob = [], bytearray()
        for s in d.strings:
            offs.append(len(blob))
            blob.extend(s.encode('utf-8', 'surrogateescape') + b'\0')
        area = struct.pack(f'>{len(offs)}I', *offs) + bytes(blob)
        count = len(offs)
    head = MAGIC + struct.pack('>IIII', 24 + len(area), d.header, len(area), count)
    return head + area + tree + d.trailing


def read(path):
    with open(path, 'rb') as f:
        return parse(f.read())


def new_guid(namespace, *parts):
    """A deterministic GUID (UUIDv5 over NAMESPACE_URL) in DICE's upper-case form."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, ':'.join([namespace, *map(str, parts)]))).upper()
