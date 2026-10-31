"""The <instance> elements of a converted level partition (DbxEdit's XML) and their fields' references, whatever
the attribute order.

Attribute order varies by author at both levels: DICE's files write <instance guid="..." type="..."> and
<field ref="..." name="...">, Napisal's Onslaught levels <instance type="..." guid="..."> and mostly <field
name="..." ref="..."> (ONS_MP_002: 17,454 name-first, 118 ref-first). Patterns that expected one order read
ONS_MP_002 as 0 placed objects, 0 soldier spawns and 0 AI orders (2026-10-20; spec KYNAPSE_SPEC.md section 4.5).
Any XML-matching code should go through instances() and field_ref() rather than its own patterns; one-attribute
elements (<complex name=...>, <array name=...>, <item ref=...>) are not affected."""
import re
import struct
from collections.abc import Iterator

_INSTANCE = re.compile(r'<instance\b([^>]*)>(.*?)</instance>', re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_FIELD = re.compile(r'<field\b([^>]*?)/?>')


def instances(text: str, type_suffix: str | None = None) -> Iterator[tuple[str, str, str]]:
    """(type, guid, body) of each <instance> in document order; with type_suffix, only those whose type ends with
    it (e.g. 'Entity.ReferenceObjectData', '.SoldierSpawnEntityData')."""
    for m in _INSTANCE.finditer(text):
        attrs = dict(_ATTR.findall(m.group(1)))
        typ = attrs.get('type', '')
        if type_suffix is None or typ.endswith(type_suffix):
            yield typ, attrs.get('guid', ''), m.group(2)


ROTATION_SLOTS = (0, 1, 2, 4, 5, 6, 8, 9, 10)     # of a 4 x 4 transform's 16 values (3, 7, 11: padding; 12-14: position)
DENORMAL_MIN = 1000                               # an integer token at least this big in a rotation slot is a denormal's bits


def transform(text: str) -> list[float]:
    """A 4 x 4 transform's 16 values from its XML text ('a/b/.../p'), as floats - or several, for an array of them.

    DbxEdit writes a 4-byte value whose top byte is 0 as an integer (DBX numbers carry no type), so a denormal float
    (|x| < 1.2e-38, which the game reads as 0) comes out as its bit pattern: MP_09_TDM's invisible walls have rotation
    entries '98304' and '181005' (0x18000, 0x2C30D, about 1e-40). Read as numbers they scaled the walls 100,000-fold
    across the whole map and cut its route network in two (NavKit, 2026-10-30). Here such a token in a rotation slot is
    reinterpreted as the float its bits are."""
    toks = text.split('/')
    out: list[float] = []
    for k, t in enumerate(toks):
        if (k % 16) in ROTATION_SLOTS and t.isdigit() and DENORMAL_MIN <= int(t) < (1 << 24):
            out.append(struct.unpack('<f', struct.pack('<I', int(t)))[0])
        else:
            out.append(float(t))
    return out


def field_ref(body: str, name: str) -> str | None:
    """The ref attribute of the first field named <name> that has one, whatever the attribute order (DICE: ref then
    name; Napisal: name then ref); None when there is none."""
    for m in _FIELD.finditer(body):
        attrs = dict(_ATTR.findall(m.group(1)))
        if attrs.get('name') == name and 'ref' in attrs:
            return attrs['ref']
    return None
