"""Binary DBX reader for the MOH suite, written from moh-dbx-tools/FORMAT.md.

    f = read('Levels/MP_005.dbx')            # or parse(bytes)
    f.instances                              # list of Element (tag 'instance')
    f.get(element, 'guid')                   # first attribute of that name, or None
    value(element)                           # a value as the XML shows it (text, 'a/b/c', true/false)
    to_xml(data)                             # FORMAT.md section 3, identical to `DbxEdit xml`

Elements keep their kind and raw value bytes, so numbers can be read exactly (numbers(element)).
"""
import math
import struct
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CONTAINER, TEXT, NUMBERS = 0xA, 2, 7
MAGIC = b'{binary}'
WHOLE_NUMBER_FIELDS = {'OriginalHashedWaveName', 'HashedName', 'HashedWaveName', 'OnRoadMaterialNameHashes', 'Hash', 'Id',
                       'CompositeMeshPartNames'}


@dataclass
class Element:
    tag: str
    kind: int
    attributes: List[Tuple[str, str]] = field(default_factory=list)
    text: str = ''                         # TEXT
    raw: bytes = b''                       # NUMBERS: count/size varints + values; other kinds: the two bytes
    children: Optional[List['Element']] = None

    def attr(self, name, default=None):
        for k, v in self.attributes:
            if k == name:
                return v
        return default

    def child(self, name):
        """The first direct child with attribute name == name."""
        for c in self.children or ():
            if c.attr('name') == name:
                return c
        return None


@dataclass
class DbxFile:
    strings: List[str]
    roots: List[Element]

    @property
    def partition(self):
        return self.roots[0]

    @property
    def instances(self):
        return [e for e in self.roots[0].children if e.tag == 'instance']

    def instance(self, guid):
        g = guid.lower()
        for e in self.instances:
            if (e.attr('guid') or '').lower() == g:
                return e
        return None

    @staticmethod
    def get(e, name):
        return e.attr(name)


def is_binary(data):
    return len(data) > 24 and data[:8] == MAGIC


def _varint(data, pos):
    value, shift = 0, 0
    while True:
        if pos >= len(data):
            raise ValueError('truncated file: a number runs past the end')
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, pos
        shift += 7


def _byte(data, pos):
    if pos >= len(data):
        raise ValueError('truncated file: an element cut short')
    return data[pos]


def _string(strings, k):
    if k >= len(strings):
        raise ValueError(f'string {k} out of range ({len(strings)} strings): a damaged or truncated file')
    return strings[k]


def _strings(data, tolerant):
    # 2026-10-31: truncated input raises ValueError('truncated ...') everywhere, never IndexError or struct.error
    if len(data) < 24:
        raise ValueError('truncated file: no string table header')
    area, count = struct.unpack_from('>II', data, 16)
    if 24 + 4 * count > len(data):
        raise ValueError(f'truncated file: {count} string offsets run past the end')
    if not tolerant and 24 + area > len(data):
        raise ValueError('truncated file: the string table runs past the end')
    offsets = struct.unpack_from(f'>{count}I', data, 24)
    base = 24 + 4 * count
    out = []
    for off in offsets:
        start = base + off
        end = data.find(b'\0', start)
        if end < 0:
            if not tolerant:
                raise ValueError('string without end')
            end = len(data)
        out.append(data[start:end].decode('utf-8', errors='replace'))
    return out, 24 + area


class DeletedEntry(ValueError):
    """The file is a bundle's zero-byte *deleted* marker: there is nothing to read (it is not a broken file)."""


def parse(data):
    """A binary .dbx file as an element tree (FORMAT.md sections 1-2)."""
    if not data:
        raise DeletedEntry('empty file: a *deleted* bundle entry (older FbrbTool unpacks wrote them as files)')
    if not is_binary(data):
        raise ValueError('not a binary .dbx file')
    strings, pos = _strings(data, tolerant=False)
    roots = []
    stack = []
    n = len(data)
    while pos < n:
        tag, pos = _varint(data, pos)
        if tag == 0:
            if not stack:
                break
            stack.pop()
            continue
        head = _byte(data, pos)
        pos += 1
        e = Element(_string(strings, tag), head >> 4)
        for _ in range(head & 0xF):
            k, pos = _varint(data, pos)
            v, pos = _varint(data, pos)
            e.attributes.append((_string(strings, k), _string(strings, v)))
        if e.kind == CONTAINER:
            _byte(data, pos)
            pos += 1
            e.children = []
            (stack[-1].children if stack else roots).append(e)
            stack.append(e)
            continue
        if e.kind == TEXT:
            idx, pos = _varint(data, pos)
            e.text = _string(strings, idx)
        elif e.kind == NUMBERS:
            start = pos
            count, pos = _varint(data, pos)
            size, pos = _varint(data, pos)
            end = pos + count * size
            if end > n:
                raise ValueError(f'truncated file: {count} numbers of {size} bytes run past the end')
            pos = end
            e.raw = data[start:end]
        else:
            if pos + 2 > n:
                raise ValueError('truncated file: value cut short')
            e.raw = data[pos:pos + 2]
            pos += 2
        (stack[-1].children if stack else roots).append(e)
    if stack:
        raise ValueError('truncated file: unclosed container(s)')
    if not roots:
        raise ValueError('truncated file: no elements after the string table')
    return DbxFile(strings, roots)


def read(path):
    with open(path, 'rb') as f:
        return parse(f.read())


# ------------------------------------------------------------------------------------------------ values

def numbers(e):
    """(size, [raw big-endian bytes per value]) of a NUMBERS element."""
    count, p = _varint(e.raw, 0)
    size, p = _varint(e.raw, p)
    return size, [e.raw[p + i * size:p + (i + 1) * size] for i in range(count)]


def floats(e):
    """The values of a 4-byte NUMBERS element read as decimals (e.g. a Transform)."""
    size, vals = numbers(e)
    if size != 4:
        raise ValueError('not 4-byte numbers')
    return [struct.unpack('>f', v)[0] for v in vals]


def _net_g(value, precision):
    """.NET's "G<precision>" formatting of a finite double (what "R" builds on): the exact binary value rounded
    to <precision> significant digits, a tie rounded away from zero; scientific notation (E+XX / E-XX) for an
    exponent below -4 or from <precision> up; trailing zeros dropped."""
    if value == 0:
        return '0'   # .NET Framework prints negative zero as 0 too
    d = Decimal(value)                     # exact
    exp = d.adjusted()
    unit = Decimal(1).scaleb(exp - precision + 1)
    r = d.quantize(unit, rounding=ROUND_HALF_UP)
    if r.adjusted() != exp:                # 9.99... rounded up to 10.0...
        exp = r.adjusted()
        r = d.quantize(Decimal(1).scaleb(exp - precision + 1), rounding=ROUND_HALF_UP)
    digits = str(abs(r.scaleb(-r.as_tuple().exponent))).rstrip('0') or '0'
    digits = digits[:precision]
    sign = '-' if r < 0 else ''
    if exp < -4 or exp >= precision:
        m = digits[0] + ('.' + digits[1:] if len(digits) > 1 else '')
        return f"{sign}{m}E{'+' if exp >= 0 else '-'}{abs(exp):02d}"
    if exp >= 0:
        whole = digits[:exp + 1].ljust(exp + 1, '0')
        frac = digits[exp + 1:]
        return sign + whole + ('.' + frac if frac else '')
    return sign + '0.' + '0' * (-exp - 1) + digits


def _net_r_single(bits):
    v = struct.unpack('>f', bits)[0]
    s = _net_g(v, 7)
    if struct.unpack('>f', struct.pack('>f', float(s)))[0] != v:
        s = _net_g(v, 9)
    return s


def _net_r_double(raw):
    v = struct.unpack('>d', raw)[0]
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return 'Infinity' if v > 0 else '-Infinity'
    s = _net_g(v, 15)
    if float(s) != v:
        s = _net_g(v, 17)
    return s


def _number_text(raw, size, field_name):
    if size == 4:
        whole = struct.unpack('>i', raw)[0]
        if field_name in WHOLE_NUMBER_FIELDS or (whole >> 24) == 0 or ((whole >> 23) & 0xFF) == 0xFF:
            return str(whole)
        return _net_r_single(raw)
    if size == 8:
        return _net_r_double(raw)
    return str(raw[0] << 8 | raw[1])


def value(e):
    """A non-container element's value as the XML shows it (unescaped)."""
    if e.kind == TEXT:
        return e.text
    if e.kind == NUMBERS:
        size, vals = numbers(e)
        first = e.attributes[0][1] if e.attributes else ''
        return '/'.join(_number_text(v, size, first) for v in vals)
    v = e.raw[1]
    return 'false' if v == 0 else 'true' if v == 1 else str(v)


# ------------------------------------------------------------------------------------------------ XML

def _escape(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def to_xml(data):
    """FORMAT.md section 3: the readable XML, identical to `DbxEdit xml` (string reading tolerant to a missing
    terminator at the end, as the export is)."""
    strings, pos = _strings(data, tolerant=True)
    out = ['<?xml version="1.0"?>\r\n']
    open_tags = []
    n = len(data)
    while pos < n:
        tag, pos = _varint(data, pos)
        if tag == 0:
            if not open_tags:
                break
            closing = open_tags.pop()
            out.append('\t' * len(open_tags) + '</' + closing + '>\r\n')
            continue
        name = _string(strings, tag)
        head = _byte(data, pos)
        pos += 1
        kind = head >> 4
        line = ['\t' * len(open_tags), '<', name]
        first = ''
        for i in range(head & 0xF):
            k, pos = _varint(data, pos)
            v, pos = _varint(data, pos)
            if i == 0:
                first = _string(strings, v)
            line.append(' ' + _string(strings, k) + '="' + _escape(_string(strings, v)) + '"')
        if kind == CONTAINER:
            _byte(data, pos)
            pos += 1
            out.append(''.join(line) + '>\r\n')
            open_tags.append(name)
            continue
        if kind == TEXT:
            idx, pos = _varint(data, pos)
            content = _escape(_string(strings, idx))
            if not content:
                out.append(''.join(line) + ' />\r\n')
                continue
        elif kind == NUMBERS:
            count, pos = _varint(data, pos)
            size, pos = _varint(data, pos)
            if pos + count * size > n:
                raise ValueError(f'truncated file: {count} numbers of {size} bytes run past the end')
            vals = [_number_text(data[pos + i * size:pos + i * size + (4 if size == 4 else 8 if size == 8 else 2)], size, first)
                    for i in range(count)]
            pos += count * size
            content = '/'.join(vals)
        else:
            v = _byte(data, pos + 1)
            pos += 2
            content = 'false' if v == 0 else 'true' if v == 1 else str(v)
        out.append(''.join(line) + '>' + content + '</' + name + '>\r\n')
    return ''.join(out)
