# Binary DBX format and DbxEdit's behaviour

This is the specification DbxEdit is written from. It describes Battlefield: Bad Company 2's binary `.dbx`
files and exactly what DbxEdit outputs. The format knowledge comes from the BC2 modding community, first
of all **Frankelstner**'s research; the description and DbxEdit's code are this project's own.

All multi-byte header numbers are big-endian. A *varint* is an unsigned number in 7-bit groups, least
significant group first; the high bit of each byte means "another byte follows".

## 1. File layout

| Offset | Size | Content |
|---|---|---|
| 0 | 8 | ASCII `{binary}` |
| 8 | 4 | payload start (= 24 + string-area size) |
| 12 | 4 | a value kept as it is |
| 16 | 4 | string-area size *S* (= 4 × string count + size of the string bytes) |
| 20 | 4 | string count *N* |
| 24 | 4 × *N* | offset of each string, from the end of this table |
| 24 + 4*N* | | the strings: UTF-8, each ended by a zero byte |
| 24 + *S* | | payload: the element tree |

String 0 is the empty string in every game file. It cannot name a tag, because a tag index of 0 closes
the open element, so a new file starts its table with it.

A file is binary DBX if it is longer than 24 bytes and starts with `{binary}`. Other `.dbx` files are plain
XML already.

## 2. Element tree

The payload is a sequence of elements. Each element is:

1. **Tag:** a varint string index (the tag name, e.g. `partition`, `instance`, `field`, `array`, `item`,
   `complex`). Index 0 is not a tag: it closes the element that is currently open.
2. **Type and attribute count:** one byte, type in the high 4 bits, attribute count in the low 4 bits.
3. **Attributes:** that many pairs of varint string indexes (name, value), in order.
4. **Value**, by type:

| Type | Value |
|---|---|
| 0xA, container | one byte (kept as it is), then child elements up to a closing 0 |
| 2, text | a varint string index |
| 7, numbers | varint count *n*, varint size *s*, then *n* × *s* bytes, big-endian |
| anything else | two bytes; the second one is the value (bool or byte) |

The top level holds one `partition` container. A 0 where a tag would start at the top level ends the tree;
that byte and everything after it are kept as they are.

## 3. XML output (`DbxEdit xml`)

For every binary `.dbx` file, `<name>.xml` is written next to it, UTF-8 without a byte-order mark, lines
ended by CR LF:

- First line: `<?xml version="1.0"?>`.
- Each element starts on its own line, indented by one tab per open container.
- Opening: `<tag` then ` name="value"` for each attribute in file order.
- Container: `<tag ...>`; its closing line `</tag>` has the container's own indentation.
- Text: `<tag ...>text</tag>`, or `<tag ... />` when the text is empty.
- Numbers: `<tag ...>v1/v2/...</tag>` (an empty list gives `<tag ...></tag>`), with each value written as:
  - size 4: a whole number if the element's *first* attribute value is one of `OriginalHashedWaveName`,
    `HashedName`, `HashedWaveName`, `OnRoadMaterialNameHashes`, `Hash`, `Id`, `CompositeMeshPartNames`,
    or if the 32-bit value has a zero top byte, or if its eight exponent bits are all ones (a NaN or
    infinity pattern, either sign); otherwise a 32-bit float in .NET round-trip format (`R`). Whole
    numbers are the value read as a signed 32-bit integer;
  - size 8: a 64-bit float in round-trip format;
  - any other size: the first two bytes as an unsigned 16-bit number (the reader still advances by the size).
- Other types: `true` for 1, `false` for 0, else the number.
- In attribute values and text, `&`, `<`, `>` and `"` are written as `&amp;`, `&lt;`, `&gt;`, `&quot;`.
- Strings are decoded as UTF-8; a string runs to its zero byte or the end of the file.

The XML is for reading. DbxEdit edits the binary file itself.

## 4. Writing (`DbxEdit edit`, `DbxEdit roundtrip`)

A file is written back as: the header (payload start and *S* recomputed, the kept value unchanged), the
string table, the element tree, then the kept trailing bytes.

- If no string was changed or added, the original string area is copied byte for byte.
- Otherwise the string area is rebuilt: every string in table order (unused ones included), each followed
  by a zero byte, with offsets counted from the start of the string bytes.
- Varints are written in the shortest form.
- A string needed by an edit reuses the first identical entry in the table, or is added at the end.

An unchanged file therefore comes back byte for byte.

## 5. Edit commands

Commands run in the order given. A GUID comparison ignores case. "References" are attributes named `ref`.
A local reference is a `ref` value 36 characters long without a `/`.

- `replace=<old>|<new>`: every string-table entry exactly `<old>` becomes `<new>`. Fails if none matched.
- `replaceprefix=<old>|<new>[|<except>...]`: every entry starting with `<old>` (ignoring case), unless it
  starts with one of the non-empty exceptions (ignoring case), gets `<new>` in place of that start; if the
  entry's start was all lower case, `<new>` is lower-cased too.
- `drop=<guid>`: walks the tree from the last element to the first, at every level. An `instance` with that
  GUID is removed. Any other element whose `ref` equals the GUID, or ends in `/<guid>`, is removed if it is
  an `item`; otherwise all its `ref` attributes become `null`, and its children are still visited. Fails if
  no instance was removed.
- `dropfield=<name>`: removes every element, at any depth, that is not an `instance` and has an attribute
  `name` equal to `<name>` (children of removed elements are not visited).
- `set=<field>|<true|false|0-255>`: for every non-container element of a type other than text or numbers,
  with a two-byte value and an attribute `name` equal to `<field>`, sets the second byte. Fails if none.
- `setfield=<guid>|<field>|<value>`: on a direct child of that instance named `<field>` (first match that is
  text or numbers), sets the text, or the single 4-byte number (a float if the value has a `.`).
- `partguid=<guid>`: sets the `guid` attribute of the top-level partition.
- `additem=<guid>|<array>|<ref>[|<before ref>]`: adds an item to the direct child `array` of that instance
  named `<array>`, shaped like its first existing item but with only a `ref` attribute; at the end, or
  before the first item whose `ref` is `<before ref>`. Fails if the array has no item to copy the shape from.
- `copyfield=<src.dbx>|<src guid>|<field>|<guid>`: takes the first element named `<field>` that is not an
  `instance` inside the source instance (searching depth first; each element is checked before its
  children), copies it with its children, and puts the copy in place of the first element named `<field>`
  inside the target instance (same search), or appends it to the instance.
- `graft=<src.dbx>|<guid>[|<before guid>]`: copies that instance from the source file, then every source
  instance it references locally, and so on (depth first: the references of each copied instance, in
  document order, are visited last-first). Instances the file already has are skipped. The copies are
  inserted before instance `<before guid>`, or after the last element of the partition.

When an element is copied from another file, its strings are added to this file's table in this order:
tag, then each attribute's name and value, then a text value, then the children in order.

A `@file` argument reads more commands from that file: one per line, blank lines and lines starting with
`#` ignored.

## 6. XML import (`DbxEdit import`)

The XML of section 3 leaves out what the binary needs to be exact:

- whether a value is text, a number list or a bool/byte;
- the size of each number (4, 8 or 2 bytes);
- for 4-byte numbers, integer or decimal (`1` is both the integer 1 and the decimal 1.0);
- the container byte, the first byte of bool/byte values, the header value, trailing bytes and the
  string-table order.

Across 43,289 game files the container byte is always 0, the first byte of bool/byte values always 1,
the header value always 0, and there are no trailing bytes. But 5.9 million decimals print as whole
numbers, and 8,683 text values look like numbers. So the import takes types from a **reference file**,
normally the original `.dbx` the XML was exported from (`--like`, or `<name>.dbx` next to the XML).

- **Matching.** Each XML element is matched to a reference element among its siblings: by `guid`, else
  `name`, else `ref`, else all attributes, in order. A reference element is used once; the next XML
  element looks after it, and reference elements left over count as removed.
- **Unchanged values** (the same text as the reference shows, line endings aside) keep the reference's
  bytes. Unchanged attributes keep its string indexes. The reference's string table is kept and new
  strings are appended, so an unedited XML gives the original file byte for byte.
- **Changed values** keep the reference element's kind and number size. A 4-byte token is read as the
  integer or the decimal that the XML would print as exactly that token. If both would (`1`), the
  reference value's reading decides. If neither would (more digits than a 4-byte decimal holds), an
  integer field takes the integer and anything else the nearest decimal, which is counted as rounded.
- **New elements** take kind, size and integer-or-decimal from the same field (tag + first attribute
  value) in the reference or the `--types` files. With no such field, the XML's own shape decides, and
  each such guess is reported:
  - `<x ... />` is empty text;
  - a multi-line element without children is a container;
  - `true` or `false` is a bool;
  - `/`-separated numbers are 4-byte numbers;
  - anything else is text.
- New files (no reference) get header value 0, container byte 0, first byte 1 and no trailing bytes.

Exit code 0 means written; 1 means failed; 3 means written, but some values were typed by guess.

## 7. Level checks (`DbxEdit check`, `DbxEdit fixslots`)

`check` reports, for a level partition:

- **Error:** an item in LevelData `Objects` that is `null` or refers to an instance not in the file. The
  game reads the list without a check (function 0x006EFBC0, crash at 0x006EFF8A).
- **Error:** a `PhysicsData` or `MeshEntityType` reference that is `null` on a model group, model-group
  member, forest or tree member. The model-group set-up dereferences it (crash at 0x00AF7144). Terrain
  is exempt, since its collision comes from elsewhere.
- **Error:** any other local reference to an instance not in the file.
- **Warning:** terrain, model groups, forests or the static envmap not listed in LevelData `Objects`.
  These are not loaded.
- **Warning:** cull slots shared by objects. A `ReferenceObjectData`'s `Enumeration` is its slot in the
  level's static cull trees: unique per object, 0 for objects outside the trees (dynamic props).
  Objects that share a slot are shown and hidden together.
- **Warning** (with `--slots-from <level>`): objects whose slot differs from the same object (by GUID) in
  the level the cull trees were built for.
- **With `--bundle <folder>`:** references into other files are resolved against the unpacked folders.
  - Warning: a referenced file is missing.
  - Error: the file is there, but the referenced instance is not.

`fixslots <in> <out> --from <level>` gives every object the slot of the same object in `<level>`:

1. by GUID;
2. else by blueprint name and position (rounded to 0.1 m);
3. else the nearest unused same-blueprint object within 50 m;
4. else 0.

It prints how many objects each rule matched.
