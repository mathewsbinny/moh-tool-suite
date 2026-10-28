"""The <instance> elements of a converted level partition (DbxEdit's XML) and their fields' references, whatever
the attribute order.

Attribute order varies by author at both levels: DICE's files write <instance guid="..." type="..."> and
<field ref="..." name="...">, Napisal's Onslaught levels <instance type="..." guid="..."> and mostly <field
name="..." ref="..."> (ONS_MP_002: 17,454 name-first, 118 ref-first). Patterns that expected one order read
ONS_MP_002 as 0 placed objects, 0 soldier spawns and 0 AI orders (2026-10-20; spec KYNAPSE_SPEC.md section 4.5).
Any XML-matching code should go through instances() and field_ref() rather than its own patterns; one-attribute
elements (<complex name=...>, <array name=...>, <item ref=...>) are not affected."""
import re

_INSTANCE = re.compile(r'<instance\b([^>]*)>(.*?)</instance>', re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_FIELD = re.compile(r'<field\b([^>]*?)/?>')


def instances(text, type_suffix=None):
    """(type, guid, body) of each <instance> in document order; with type_suffix, only those whose type ends with
    it (e.g. 'Entity.ReferenceObjectData', '.SoldierSpawnEntityData')."""
    for m in _INSTANCE.finditer(text):
        attrs = dict(_ATTR.findall(m.group(1)))
        typ = attrs.get('type', '')
        if type_suffix is None or typ.endswith(type_suffix):
            yield typ, attrs.get('guid', ''), m.group(2)


def field_ref(body, name):
    """The ref attribute of the first field named <name> that has one, whatever the attribute order (DICE: ref then
    name; Napisal: name then ref); None when there is none."""
    for m in _FIELD.finditer(body):
        attrs = dict(_ATTR.findall(m.group(1)))
        if attrs.get('name') == name and 'ref' in attrs:
            return attrs['ref']
    return None
