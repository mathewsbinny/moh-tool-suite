// DbxEdit - reads, converts and edits Medal of Honor (2010) binary .dbx files (Frostbite 1 data as a
// tokenised XML tree). Written from FORMAT.md, which describes the format and everything this tool outputs.
// Anything an edit does not change is written back byte for byte.
//
// usage: DbxEdit xml <file.dbx|folder> [...]               write <name>.xml next to every binary .dbx
//        DbxEdit roundtrip <file.dbx|folder>               read + write every file, report any that change
//        DbxEdit edit <in.dbx> <out.dbx> <command> ...      apply commands in order (FORMAT.md section 5):
//            replace=<old>|<new>     every string exactly <old> becomes <new> (re-points references)
//            drop=<guid>             removes the instance with that guid; array items referring to it are
//                                    removed, single references to it become "null"
//            replaceprefix=<old>|<new>[|<except>...]  strings starting with <old> get <new> as prefix
//            partguid=<guid>         sets the partition's own guid (a copy under a new name gets a new one)
//            copyfield=<src.dbx>|<src guid>|<field>|<guid>  copies a field (with arrays) from another file's instance
//            set=<field>|<true|false|0-255>  sets a bool / byte field
//            setfield=<guid>|<field>|<value>  sets a field of that instance (text, numbers, bool / byte), typed like
//                                    its present value
//            additem=<guid>|<array>|<ref>[|<before ref>]  adds an item referring to <ref> to array <array> of
//                                    instance <guid> (at the end, or before the item referring to <before ref>)
//            dropfield=<name>        removes every field named <name> (fields the PC build does not have)
//            graft=<src.dbx>|<guid>[|<before guid>]  copies (inserted before that instance if given) that instance of another file, with every instance of that file it
//                                    references in-partition (members), strings remapped
//        a command argument "@file" reads commands from a file (one per line, # comments)
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace MohDbx
{
    // Varints: 7 bits per byte, low group first, high bit = more bytes follow.
    static class Varint
    {
        public static int Read(byte[] data, ref int pos)
        {
            int value = 0;
            for (int shift = 0; ; shift += 7)
            {
                byte b = data[pos++];
                value |= (b & 0x7F) << shift;
                if (b < 0x80) return value;
            }
        }

        public static void Write(List<byte> output, int value)
        {
            while (value >= 0x80 || value < 0)
            {
                output.Add((byte)(value & 0x7F | 0x80));
                value = (int)((uint)value >> 7);
            }
            output.Add((byte)value);
        }

        public static byte[] Bytes(int value)
        {
            var b = new List<byte>();
            Write(b, value);
            return b.ToArray();
        }
    }

    static class BigEndian
    {
        public static uint U32(byte[] data, int at)
        {
            return (uint)data[at] << 24 | (uint)data[at + 1] << 16 | (uint)data[at + 2] << 8 | data[at + 3];
        }

        public static void Put(List<byte> output, uint value)
        {
            for (int shift = 24; shift >= 0; shift -= 8) output.Add((byte)(value >> shift));
        }
    }

    sealed class Attribute
    {
        public int Name, Value;   // string-table indexes
        public Attribute(int name, int value) { Name = name; Value = value; }
    }

    // One element of the tree (FORMAT.md section 2). Containers have Children; other kinds keep their value
    // bytes exactly as stored (Payload).
    sealed class Element
    {
        public const int Container = 0xA, Text = 2, Numbers = 7;
        public int Tag;
        public int Kind;
        public readonly List<Attribute> Attributes = new List<Attribute>();
        public byte ContainerByte;
        public List<Element> Children;
        public byte[] Payload;

        public bool IsContainer { get { return Children != null; } }
    }

    // A parsed binary .dbx file.
    sealed class DbxFile
    {
        const string Magic = "{binary}";

        public readonly byte[] Original;
        public readonly bool IsBinary;
        public List<string> Table;
        public bool TableChanged;
        public List<Element> TopLevel;
        public uint KeptHeaderValue;
        List<int> originalOffsets;
        byte[] originalStringArea;   // offsets table excluded: the string bytes as stored
        public byte[] Trailer;              // the closing 0 of the tree and anything after it

        public DbxFile(string path) : this(File.ReadAllBytes(path)) { }

        // A new, empty file (header value 0, no trailing bytes: what every game file has). String 0 is the empty
        // string, as in every game file: index 0 cannot be a tag (a 0 closes the open element).
        DbxFile()
        {
            Original = new byte[0];
            IsBinary = true;
            Table = new List<string> { "" };
            TableChanged = true;
            TopLevel = new List<Element>();
            Trailer = new byte[0];
        }

        public static DbxFile CreateEmpty() { return new DbxFile(); }

        public DbxFile(byte[] data)
        {
            Original = data;
            IsBinary = data.Length > 24 && Encoding.ASCII.GetString(data, 0, 8) == Magic;
            if (!IsBinary) return;
            uint areaSize = BigEndian.U32(data, 16), count = BigEndian.U32(data, 20);
            KeptHeaderValue = BigEndian.U32(data, 12);
            int stringsAt = 24 + 4 * (int)count, payloadAt = 24 + (int)areaSize;
            originalOffsets = new List<int>();
            for (int i = 0; i < count; i++) originalOffsets.Add((int)BigEndian.U32(data, 24 + 4 * i));
            Table = new List<string>(originalOffsets.Count);
            foreach (int offset in originalOffsets)
            {
                int from = stringsAt + offset, to = from;
                while (data[to] != 0) to++;
                Table.Add(Encoding.UTF8.GetString(data, from, to - from));
            }
            originalStringArea = new byte[payloadAt - stringsAt];
            Array.Copy(data, stringsAt, originalStringArea, 0, originalStringArea.Length);

            TopLevel = new List<Element>();
            int pos = payloadAt;
            while (pos < data.Length)
            {
                int tag = Varint.Read(data, ref pos);
                if (tag == 0) { pos--; break; }
                TopLevel.Add(ReadElement(data, ref pos, tag));
            }
            Trailer = new byte[data.Length - pos];
            Array.Copy(data, pos, Trailer, 0, Trailer.Length);
        }

        static Element ReadElement(byte[] data, ref int pos, int tag)
        {
            var e = new Element { Tag = tag };
            byte head = data[pos++];
            e.Kind = head >> 4;
            for (int count = head & 0xF; count > 0; count--)
            {
                int name = Varint.Read(data, ref pos);
                e.Attributes.Add(new Attribute(name, Varint.Read(data, ref pos)));
            }
            if (e.Kind == Element.Container)
            {
                e.ContainerByte = data[pos++];
                e.Children = new List<Element>();
                for (int child = Varint.Read(data, ref pos); child != 0; child = Varint.Read(data, ref pos))
                    e.Children.Add(ReadElement(data, ref pos, child));
                return e;
            }
            int valueAt = pos;
            if (e.Kind == Element.Text) Varint.Read(data, ref pos);
            else if (e.Kind == Element.Numbers)
            {
                int n = Varint.Read(data, ref pos), size = Varint.Read(data, ref pos);
                pos += n * size;
            }
            else pos += 2;
            e.Payload = new byte[pos - valueAt];
            Array.Copy(data, valueAt, e.Payload, 0, e.Payload.Length);
            return e;
        }

        public byte[] Serialize()
        {
            var stringArea = originalStringArea;
            var offsets = originalOffsets;
            if (TableChanged)
            {
                var bytes = new List<byte>();
                offsets = new List<int>(Table.Count);
                foreach (string s in Table)
                {
                    offsets.Add(bytes.Count);
                    bytes.AddRange(Encoding.UTF8.GetBytes(s));
                    bytes.Add(0);
                }
                stringArea = bytes.ToArray();
            }
            var tree = new List<byte>();
            foreach (var e in TopLevel) WriteElement(tree, e);
            tree.AddRange(Trailer);

            uint areaSize = (uint)(4 * Table.Count + stringArea.Length);
            var output = new List<byte>(Encoding.ASCII.GetBytes(Magic));
            BigEndian.Put(output, 24 + areaSize);
            BigEndian.Put(output, KeptHeaderValue);
            BigEndian.Put(output, areaSize);
            BigEndian.Put(output, (uint)Table.Count);
            foreach (int offset in offsets) BigEndian.Put(output, (uint)offset);
            output.AddRange(stringArea);
            output.AddRange(tree);
            return output.ToArray();
        }

        static void WriteElement(List<byte> output, Element e)
        {
            Varint.Write(output, e.Tag);
            output.Add((byte)(e.Kind << 4 | e.Attributes.Count));
            foreach (var a in e.Attributes)
            {
                Varint.Write(output, a.Name);
                Varint.Write(output, a.Value);
            }
            if (e.Kind != Element.Container)
            {
                output.AddRange(e.Payload);
                return;
            }
            output.Add(e.ContainerByte);
            foreach (var c in e.Children) WriteElement(output, c);
            output.Add(0);
        }

        // The index of a string: the first identical table entry, or a new one at the end.
        public int Intern(string s)
        {
            int at = Table.IndexOf(s);
            if (at >= 0) return at;
            Table.Add(s);
            TableChanged = true;
            return Table.Count - 1;
        }

        public string TagOf(Element e) { return Table[e.Tag]; }

        // Value of the first attribute with that name, or null.
        public string Get(Element e, string attribute)
        {
            var a = e.Attributes.FirstOrDefault(x => Table[x.Name] == attribute);
            return a == null ? null : Table[a.Value];
        }

        public IEnumerable<Element> Instances()
        {
            return TopLevel[0].Children.Where(e => TagOf(e) == "instance");
        }

        public Element Instance(string guid)
        {
            return Instances().FirstOrDefault(e => Guid.Same(Get(e, "guid"), guid));
        }
    }

    static class Guid
    {
        public static bool Same(string a, string b) { return string.Equals(a, b, StringComparison.OrdinalIgnoreCase); }
    }

    // FORMAT.md section 3: binary .dbx -> readable XML.
    static class XmlExport
    {
        static readonly HashSet<string> WholeNumberFields = new HashSet<string> {
            "OriginalHashedWaveName", "HashedName", "HashedWaveName", "OnRoadMaterialNameHashes", "Hash", "Id",
            "CompositeMeshPartNames" };

        public static string Convert(byte[] data)
        {
            uint areaSize = BigEndian.U32(data, 16), count = BigEndian.U32(data, 20);
            int stringsAt = 24 + 4 * (int)count;
            var offsets = new uint[count];
            for (int i = 0; i < count; i++) offsets[i] = BigEndian.U32(data, 24 + 4 * i);
            var table = new string[count];
            for (int i = 0; i < count; i++)
            {
                int from = stringsAt + (int)offsets[i], to = from;
                while (to < data.Length && data[to] != 0) to++;
                table[i] = Encoding.UTF8.GetString(data, from, to - from);
            }

            var xml = new StringBuilder("<?xml version=\"1.0\"?>\r\n");
            var open = new Stack<string>();
            int pos = 24 + (int)areaSize;
            while (pos < data.Length)
            {
                int tag = Varint.Read(data, ref pos);
                if (tag == 0)
                {
                    if (open.Count == 0) break;
                    string closing = open.Pop();
                    xml.Append('\t', open.Count).Append("</").Append(closing).Append(">\r\n");
                    continue;
                }
                string name = table[tag];
                byte head = data[pos++];
                int kind = head >> 4;
                xml.Append('\t', open.Count).Append('<').Append(name);
                string firstValue = "";
                for (int i = 0, n = head & 0xF; i < n; i++)
                {
                    string key = table[Varint.Read(data, ref pos)], value = table[Varint.Read(data, ref pos)];
                    if (i == 0) firstValue = value;
                    xml.Append(' ').Append(key).Append("=\"").Append(Escape(value)).Append('"');
                }
                if (kind == Element.Container)
                {
                    pos++;
                    xml.Append(">\r\n");
                    open.Push(name);
                    continue;
                }
                string content;
                if (kind == Element.Text)
                {
                    content = Escape(table[Varint.Read(data, ref pos)]);
                    if (content.Length == 0)
                    {
                        xml.Append(" />\r\n");
                        continue;
                    }
                }
                else if (kind == Element.Numbers)
                {
                    int n = Varint.Read(data, ref pos), size = Varint.Read(data, ref pos);
                    var values = new string[n];
                    for (int i = 0; i < n; i++, pos += size) values[i] = Number(data, pos, size, firstValue);
                    content = string.Join("/", values);
                }
                else
                {
                    byte v = data[pos + 1];
                    pos += 2;
                    content = v == 0 ? "false" : v == 1 ? "true" : v.ToString();
                }
                xml.Append('>').Append(content).Append("</").Append(name).Append(">\r\n");
            }
            return xml.ToString();
        }

        // The value of a non-container element as its XML shows it (unescaped): text, numbers joined by '/', or
        // true / false / a byte. field = the element's first attribute value (decides whole-number fields).
        public static string ValueText(DbxFile f, Element e)
        {
            string field = e.Attributes.Count > 0 ? f.Table[e.Attributes[0].Value] : "";
            int pos = 0;
            if (e.Kind == Element.Text) return f.Table[Varint.Read(e.Payload, ref pos)];
            if (e.Kind == Element.Numbers)
            {
                int n = Varint.Read(e.Payload, ref pos), size = Varint.Read(e.Payload, ref pos);
                var values = new string[n];
                for (int i = 0; i < n; i++, pos += size) values[i] = Number(e.Payload, pos, size, field);
                return string.Join("/", values);
            }
            byte v = e.Payload[1];
            return v == 0 ? "false" : v == 1 ? "true" : v.ToString();
        }

        public static bool IsWholeNumberField(string field) { return WholeNumberFields.Contains(field); }

        internal static string Number(byte[] data, int at, int size, string field)
        {
            if (size == 4)
            {
                int whole = (int)BigEndian.U32(data, at);
                int exponent = (whole >> 23) & 0xFF;
                if (WholeNumberFields.Contains(field) || (whole >> 24) == 0 || exponent == 0xFF)
                    return whole.ToString(CultureInfo.InvariantCulture);
                return BitConverter.ToSingle(BitConverter.GetBytes(whole), 0).ToString("R", CultureInfo.InvariantCulture);
            }
            if (size == 8)
            {
                long bits = (long)BigEndian.U32(data, at) << 32 | BigEndian.U32(data, at + 4);
                return BitConverter.Int64BitsToDouble(bits).ToString("R", CultureInfo.InvariantCulture);
            }
            return (data[at] << 8 | data[at + 1]).ToString();
        }

        static string Escape(string s)
        {
            if (s.IndexOfAny(new[] { '&', '<', '>', '"' }) < 0) return s;
            var b = new StringBuilder(s.Length + 16);
            foreach (char c in s)
            {
                switch (c)
                {
                    case '&': b.Append("&amp;"); break;
                    case '<': b.Append("&lt;"); break;
                    case '>': b.Append("&gt;"); break;
                    case '"': b.Append("&quot;"); break;
                    default: b.Append(c); break;
                }
            }
            return b.ToString();
        }
    }

    // Field types learned from binary files: per "tag/first attribute value", the element kind, number size,
    // whether 4-byte values are decimals or integers, and the first byte of bool / byte values.
    sealed class TypeBook
    {
        public sealed class Entry
        {
            public int Kind = -1, Size, FirstByte = 1, Floats, Ints;
            public byte ContainerByte;
        }

        readonly Dictionary<string, Entry> entries = new Dictionary<string, Entry>();

        public static string Key(string tag, string firstValue) { return tag + "/" + firstValue; }

        public void Learn(DbxFile f)
        {
            foreach (var e in f.TopLevel) Learn(f, e);
        }

        void Learn(DbxFile f, Element e)
        {
            string key = Key(f.TagOf(e), e.Attributes.Count > 0 ? f.Table[e.Attributes[0].Value] : "");
            Entry t;
            if (!entries.TryGetValue(key, out t)) entries[key] = t = new Entry { Kind = e.Kind };
            if (e.IsContainer)
            {
                t.ContainerByte = e.ContainerByte;
                foreach (var c in e.Children) Learn(f, c);
                return;
            }
            if (e.Kind == Element.Numbers)
            {
                int pos = 0;
                int n = Varint.Read(e.Payload, ref pos), size = Varint.Read(e.Payload, ref pos);
                if (n > 0 || t.Size == 0) t.Size = size;
                if (size == 4)
                    for (int i = 0; i < n; i++, pos += 4)
                    {
                        // evidence only: zero is the same either way, all-ones exponents are padding patterns
                        uint bits = BigEndian.U32(e.Payload, pos);
                        if (!XmlImport.PrintsAsWhole(bits, "")) t.Floats++;
                        else if (bits != 0 && (bits >> 24) == 0) t.Ints++;
                    }
            }
            else if (e.Kind != Element.Text) t.FirstByte = e.Payload[0];
        }

        public Entry Find(string tag, string firstValue)
        {
            Entry t;
            return entries.TryGetValue(Key(tag, firstValue), out t) ? t : null;
        }
    }

    // XML -> binary (FORMAT.md section 6). Elements are matched to a reference file (the original .dbx): unchanged
    // values keep their exact bytes, changed ones take the reference's types. New elements are typed from the
    // TypeBook; whatever is left is guessed from the XML and reported.
    sealed class XmlImport
    {
        sealed class Node
        {
            public string Tag, Text = "";
            public readonly List<KeyValuePair<string, string>> Attributes = new List<KeyValuePair<string, string>>();
            public List<Node> Children;   // null: no child elements
            public bool SelfClosing, Multiline;
            public int Line;
            public string FirstValue { get { return Attributes.Count > 0 ? Attributes[0].Value : ""; } }
        }

        public int Kept, Changed, Added, Removed, Rounded;
        public readonly List<string> Guesses = new List<string>();
        readonly DbxFile file;
        readonly DbxFile reference;
        readonly TypeBook types;

        XmlImport(DbxFile file, DbxFile reference, TypeBook types)
        {
            this.file = file;
            this.reference = reference;
            this.types = types;
        }

        // Reads <xml> into a binary file. reference: the original .dbx (or null); typeFiles: more files to learn
        // field types from. The result reuses the reference's string table (new strings are appended).
        public static DbxFile Import(string xmlPath, DbxFile reference, IEnumerable<DbxFile> typeFiles, out XmlImport report)
        {
            var types = new TypeBook();
            if (reference != null) types.Learn(reference);
            foreach (var t in typeFiles) types.Learn(t);
            var target = reference != null ? new DbxFile(reference.Original) : DbxFile.CreateEmpty();
            report = new XmlImport(target, reference != null ? target : null, types);
            var root = Parse(xmlPath);
            var old = target.TopLevel.Count > 0 ? target.TopLevel[0] : null;
            var top = report.Build(root, old != null && target.TagOf(old) == root.Tag ? old : null, root.Tag);
            target.TopLevel = new List<Element> { top };
            return target;
        }

        // A small XML reader of our own (not System.Xml): the export can contain what a strict parser rejects -
        // the same attribute twice (game files have it), control characters - and values must keep their exact
        // line breaks. Handles what editors write too: comments, <?...?>, ' or " quotes, entity and character
        // references, CDATA.
        static Node Parse(string path)
        {
            var p = new TextParser(File.ReadAllText(path));
            p.SkipProlog();
            if (!p.At("<")) throw new InvalidDataException("no root element in " + path);
            return p.Element();
        }

        sealed class TextParser
        {
            readonly string s;
            int pos, line = 1;
            public TextParser(string text) { s = text; }

            public bool At(string t) { return string.CompareOrdinal(s, pos, t, 0, t.Length) == 0; }

            InvalidDataException Fail(string what) { return new InvalidDataException("line " + line + ": " + what); }

            void Advance(int n)
            {
                for (int i = 0; i < n && pos < s.Length; i++, pos++) if (s[pos] == '\n') line++;
            }

            void SkipTo(string end)
            {
                int at = s.IndexOf(end, pos, StringComparison.Ordinal);
                if (at < 0) throw Fail("'" + end + "' missing");
                Advance(at + end.Length - pos);
            }

            void SkipSpace() { while (pos < s.Length && char.IsWhiteSpace(s[pos])) Advance(1); }

            public void SkipProlog()
            {
                while (true)
                {
                    SkipSpace();
                    if (At("﻿")) Advance(1);
                    else if (At("<?")) SkipTo("?>");
                    else if (At("<!--")) SkipTo("-->");
                    else if (At("<!DOCTYPE")) SkipTo(">");
                    else return;
                }
            }

            string Name()
            {
                int start = pos;
                while (pos < s.Length && !char.IsWhiteSpace(s[pos]) && s[pos] != '/' && s[pos] != '>' && s[pos] != '=') Advance(1);
                if (pos == start) throw Fail("a name was expected");
                return s.Substring(start, pos - start);
            }

            string Decode(string raw)
            {
                if (raw.IndexOf('&') < 0) return raw;
                var b = new StringBuilder(raw.Length);
                for (int i = 0; i < raw.Length; i++)
                {
                    if (raw[i] != '&') { b.Append(raw[i]); continue; }
                    int semi = raw.IndexOf(';', i);
                    if (semi < 0) throw Fail("'&' without ';'");
                    string e = raw.Substring(i + 1, semi - i - 1);
                    if (e == "amp") b.Append('&');
                    else if (e == "lt") b.Append('<');
                    else if (e == "gt") b.Append('>');
                    else if (e == "quot") b.Append('"');
                    else if (e == "apos") b.Append('\'');
                    else if (e.StartsWith("#x") || e.StartsWith("#X")) b.Append(char.ConvertFromUtf32(Convert.ToInt32(e.Substring(2), 16)));
                    else if (e.StartsWith("#")) b.Append(char.ConvertFromUtf32(int.Parse(e.Substring(1), CultureInfo.InvariantCulture)));
                    else throw Fail("unknown entity &" + e + ";");
                    i = semi;
                }
                return b.ToString();
            }

            public Node Element()
            {
                Advance(1);   // '<'
                var n = new Node { Line = line, Tag = Name() };
                while (true)
                {
                    SkipSpace();
                    if (pos >= s.Length) throw Fail("<" + n.Tag + "> is not closed");
                    if (At("/>")) { Advance(2); n.SelfClosing = true; return n; }
                    if (At(">")) { Advance(1); break; }
                    string key = Name();
                    SkipSpace();
                    if (!At("=")) throw Fail("'=' expected after " + key);
                    Advance(1);
                    SkipSpace();
                    if (pos >= s.Length || (s[pos] != '"' && s[pos] != '\'')) throw Fail("a quoted value expected for " + key);
                    char q = s[pos];
                    Advance(1);
                    int end = s.IndexOf(q, pos);
                    if (end < 0) throw Fail("value of " + key + " not closed");
                    string raw = s.Substring(pos, end - pos);
                    Advance(end + 1 - pos);
                    n.Attributes.Add(new KeyValuePair<string, string>(key, Decode(raw)));
                }
                var text = new StringBuilder();
                bool realText = false;
                while (true)
                {
                    if (pos >= s.Length) throw Fail("<" + n.Tag + "> is not closed");
                    if (At("</"))
                    {
                        Advance(2);
                        string close = Name();
                        if (close != n.Tag) throw Fail("</" + close + "> closes <" + n.Tag + ">");
                        SkipSpace();
                        if (!At(">")) throw Fail("'>' expected");
                        Advance(1);
                        if (n.Children != null && realText) throw Fail("<" + n.Tag + "> has both text and child elements");
                        n.Text = n.Children == null ? text.ToString() : "";
                        n.Multiline = n.Children == null && !realText && n.Text.IndexOf('\n') >= 0;
                        return n;
                    }
                    if (At("<!--")) { SkipTo("-->"); continue; }
                    if (At("<?")) { SkipTo("?>"); continue; }
                    if (At("<![CDATA["))
                    {
                        Advance(9);
                        int end = s.IndexOf("]]>", pos, StringComparison.Ordinal);
                        if (end < 0) throw Fail("CDATA not closed");
                        text.Append(s, pos, end - pos);
                        realText = true;
                        Advance(end + 3 - pos);
                        continue;
                    }
                    if (At("<"))
                    {
                        if (n.Children == null) n.Children = new List<Node>();
                        n.Children.Add(Element());
                        continue;
                    }
                    int next = s.IndexOf('<', pos);
                    if (next < 0) next = s.Length;
                    string chunk = s.Substring(pos, next - pos);
                    if (chunk.Any(c => !char.IsWhiteSpace(c))) realText = true;
                    text.Append(Decode(chunk));
                    Advance(next - pos);
                }
            }
        }

        static string Norm(string s) { return s.Replace("\r\n", "\n").Replace('\r', '\n'); }
        static string NormAttribute(string s) { return s.Replace("\r\n", " ").Replace('\r', ' ').Replace('\n', ' ').Replace('\t', ' '); }

        // What identifies an element among its siblings: its guid, else its name, else its reference, else all
        // attributes (elements without attributes, like array entries, match by position).
        static string Key(string tag, IList<KeyValuePair<string, string>> attrs)
        {
            foreach (string id in new[] { "guid", "name", "ref" })
                foreach (var a in attrs)
                    if (a.Key == id) return tag + "\u0001" + id + "=" + NormAttribute(a.Value);
            return tag + "\u0001" + string.Join("\u0001", attrs.Select(a => a.Key + "=" + NormAttribute(a.Value)));
        }

        string KeyOf(Element e)
        {
            return Key(reference.TagOf(e), e.Attributes.Select(a => new KeyValuePair<string, string>(reference.Table[a.Name], reference.Table[a.Value])).ToList());
        }

        Element Build(Node x, Element old, string path)
        {
            var e = new Element { Tag = old != null ? old.Tag : file.Intern(x.Tag) };
            for (int i = 0; i < x.Attributes.Count; i++)
            {
                var a = x.Attributes[i];
                if (old != null && i < old.Attributes.Count && file.Table[old.Attributes[i].Name] == a.Key
                    && NormAttribute(file.Table[old.Attributes[i].Value]) == NormAttribute(a.Value))
                    e.Attributes.Add(new Attribute(old.Attributes[i].Name, old.Attributes[i].Value));
                else e.Attributes.Add(new Attribute(file.Intern(a.Key), file.Intern(a.Value)));
            }
            var learned = types.Find(x.Tag, x.FirstValue);
            int kind = KindOf(x, old, learned, path);
            e.Kind = kind;
            if (kind == Element.Container)
            {
                if (x.Children == null && x.Text.Trim().Length > 0)
                    throw new InvalidDataException("line " + x.Line + ": " + path + " holds other elements, not the value '" + x.Text.Trim() + "'");
                e.ContainerByte = old != null && old.IsContainer ? old.ContainerByte : learned != null && learned.Kind == Element.Container ? learned.ContainerByte : (byte)0;
                e.Children = new List<Element>();
                var children = x.Children ?? new List<Node>();
                var oldChildren = old != null && old.IsContainer ? old.Children : new List<Element>();
                foreach (var pair in Align(children, oldChildren, path))
                    e.Children.Add(Build(pair.Key, pair.Value, path + "/" + Describe(pair.Key)));
                if (old == null) Added++;
                return e;
            }
            if (x.Children != null) throw new InvalidDataException("line " + x.Line + ": " + path + " has child elements but is a value");
            if (old != null && old.Kind == kind && Norm(XmlExport.ValueText(reference, old)) == Norm(x.Text))
            {
                e.Payload = old.Payload;
                Kept++;
                return e;
            }
            e.Payload = Encode(kind, x, old, learned, path);
            if (old != null) Changed++; else Added++;
            return e;
        }

        static string Describe(Node n)
        {
            return n.Tag + (n.Attributes.Count > 0 ? "[" + n.FirstValue + "]" : "");
        }

        // Pairs each XML child with the reference child it corresponds to (or null): the next unused reference child
        // with the same key, in order. Reference children left over were removed.
        List<KeyValuePair<Node, Element>> Align(List<Node> xs, List<Element> olds, string path)
        {
            var queues = new Dictionary<string, Queue<int>>();
            for (int i = 0; i < olds.Count; i++)
            {
                string k = KeyOf(olds[i]);
                Queue<int> q;
                if (!queues.TryGetValue(k, out q)) queues[k] = q = new Queue<int>();
                q.Enqueue(i);
            }
            var result = new List<KeyValuePair<Node, Element>>();
            int next = 0, used = 0;
            foreach (var x in xs)
            {
                Queue<int> q;
                Element match = null;
                if (queues.TryGetValue(Key(x.Tag, x.Attributes), out q))
                {
                    while (q.Count > 0 && q.Peek() < next) q.Dequeue();
                    if (q.Count > 0)
                    {
                        int i = q.Dequeue();
                        match = olds[i];
                        next = i + 1;
                        used++;
                    }
                }
                result.Add(new KeyValuePair<Node, Element>(x, match));
            }
            Removed += olds.Count - used;
            return result;
        }

        int KindOf(Node x, Element old, TypeBook.Entry learned, string path)
        {
            if (x.Children != null) return Element.Container;
            if (old != null) return old.Kind;
            // where the XML's shape is unambiguous it decides (the export writes <x/> only for empty text, a line
            // break inside an empty element only for a container, <x></x> only for an empty number list)
            if (x.SelfClosing) return Element.Text;
            if (x.Multiline) return Element.Container;
            if (x.Text.Length == 0) return Element.Numbers;
            if (learned != null && learned.Kind >= 0 && learned.Kind != Element.Container) return learned.Kind;
            // nothing to go by: the XML's own shape
            if (x.Multiline) { Guess(x, path, "container"); return Element.Container; }
            if (x.SelfClosing) { Guess(x, path, "empty text"); return Element.Text; }
            if (x.Text == "true" || x.Text == "false") { Guess(x, path, "bool"); return 6; }
            if (x.Text.Length == 0 || x.Text.Split('/').All(t => { double d; return double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out d); }))
            {
                Guess(x, path, "numbers (4 bytes each)");
                return Element.Numbers;
            }
            Guess(x, path, "text");
            return Element.Text;
        }

        void Guess(Node x, string path, string what)
        {
            Guesses.Add("line " + x.Line + ": " + path + ": no type known, taken as " + what);
        }

        // The value bytes for a new text of an existing element, typed like it (what the import does for a changed
        // value). rounded: true if a decimal had to be rounded to the nearest 4-byte value.
        public static byte[] EncodeValue(DbxFile f, Element e, string text, out bool rounded)
        {
            var imp = new XmlImport(f, f, new TypeBook());
            var x = new Node { Tag = f.TagOf(e), Text = text };
            foreach (var a in e.Attributes) x.Attributes.Add(new KeyValuePair<string, string>(f.Table[a.Name], f.Table[a.Value]));
            byte[] payload = imp.Encode(e.Kind, x, e, null, x.Tag + "[" + x.FirstValue + "]");
            rounded = imp.Rounded > 0;
            return payload;
        }

        byte[] Encode(int kind, Node x, Element old, TypeBook.Entry learned, string path)
        {
            string text = x.Text;
            if (kind == Element.Text) return Varint.Bytes(file.Intern(text));
            if (kind != Element.Numbers)
            {
                byte first = old != null && old.Payload != null && old.Payload.Length == 2 ? old.Payload[0] : learned != null ? (byte)learned.FirstByte : (byte)1;
                byte value;
                string t = text.Trim();
                if (t == "true") value = 1;
                else if (t == "false") value = 0;
                else if (!byte.TryParse(t, NumberStyles.None, CultureInfo.InvariantCulture, out value))
                    throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + text + "' is not true, false or 0-255");
                return new[] { first, value };
            }
            string[] tokens = text.Trim().Length == 0 ? new string[0] : text.Trim().Split('/');
            int oldCount = 0, oldSize = 0, oldAt = 0;
            if (old != null && old.Kind == Element.Numbers)
            {
                oldCount = Varint.Read(old.Payload, ref oldAt);
                oldSize = Varint.Read(old.Payload, ref oldAt);
            }
            int size = oldSize > 0 ? oldSize : learned != null && learned.Size > 0 ? learned.Size : 4;
            if (oldSize == 0 && (learned == null || learned.Size == 0) && tokens.Length > 0) Guess(x, path, "4-byte numbers");
            var output = new List<byte>();
            Varint.Write(output, tokens.Length);
            Varint.Write(output, size);
            for (int i = 0; i < tokens.Length; i++)
            {
                string token = tokens[i].Trim();
                uint? oldBits = size == 4 && i < oldCount ? (uint?)BigEndian.U32(old.Payload, oldAt + 4 * i) : null;
                if (size == 4)
                {
                    BigEndian.Put(output, FourBytes(token, x.FirstValue, oldBits, learned, x, path));
                }
                else if (size == 8)
                {
                    double d;
                    if (!double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out d))
                        throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not a number");
                    long bits = BitConverter.DoubleToInt64Bits(d);
                    BigEndian.Put(output, (uint)(bits >> 32));
                    BigEndian.Put(output, (uint)bits);
                }
                else if (size == 2)
                {
                    ushort v;
                    if (!ushort.TryParse(token, NumberStyles.None, CultureInfo.InvariantCulture, out v))
                        throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not 0-65535");
                    output.Add((byte)(v >> 8));
                    output.Add((byte)v);
                }
                else throw new InvalidDataException("line " + x.Line + ": " + path + ": numbers of " + size + " bytes cannot be written");
            }
            return output.ToArray();
        }

        // True if the XML shows these 4 bytes as a whole number (FORMAT.md section 3).
        public static bool PrintsAsWhole(uint bits, string field)
        {
            return XmlExport.IsWholeNumberField(field) || (bits >> 24) == 0 || ((bits >> 23) & 0xFF) == 0xFF;
        }

        // A 4-byte value: the integer or the decimal reading of the token, whichever the XML would show as exactly
        // this token. When both would (a whole decimal like 1.0 also shows as "1"), the original value's kind
        // decides, else the field's usual kind, else decimal (reported).
        uint FourBytes(string token, string field, uint? oldBits, TypeBook.Entry learned, Node x, string path)
        {
            var options = new List<uint>();
            int whole;
            if (int.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)
                && XmlExport.Number(Be((uint)whole), 0, 4, field) == token) options.Add((uint)whole);
            float f;
            if (float.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out f))
            {
                uint bits = BitConverter.ToUInt32(BitConverter.GetBytes(f), 0);
                if (XmlExport.Number(Be(bits), 0, 4, field) == token && !options.Contains(bits)) options.Add(bits);
            }
            if (options.Count == 0)
            {
                // not stored exactly either way (e.g. more digits than a 4-byte decimal holds): an integer for a whole
                // number in an integer field, else the nearest decimal
                bool integerField = oldBits.HasValue ? PrintsAsWhole(oldBits.Value, field) && oldBits.Value != 0
                                  : learned != null && learned.Ints > learned.Floats;
                if (integerField && int.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)) return (uint)whole;
                if (float.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out f))
                {
                    Rounded++;
                    return BitConverter.ToUInt32(BitConverter.GetBytes(f), 0);
                }
                throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not a number");
            }
            if (options.Count == 1) return options[0];
            // two readings: [0] integer, [1] decimal
            if (oldBits.HasValue) return PrintsAsWhole(oldBits.Value, field) ? options[0] : options[1];
            if (learned != null && learned.Floats + learned.Ints > 0) return learned.Floats >= learned.Ints ? options[1] : options[0];
            Guess(x, path, "decimal for '" + token + "'");
            return options[1];
        }

        static byte[] Be(uint v) { return new[] { (byte)(v >> 24), (byte)(v >> 16), (byte)(v >> 8), (byte)v }; }
    }

    // FORMAT.md section 5: the edit commands. Each returns a count or a success flag; Program prints.
    sealed class Editor
    {
        readonly DbxFile file;
        public Editor(DbxFile file) { this.file = file; }

        public int Replace(string from, string to)
        {
            int hits = 0;
            for (int i = 0; i < file.Table.Count; i++)
            {
                if (file.Table[i] != from) continue;
                file.Table[i] = to;
                file.TableChanged = true;
                hits++;
            }
            return hits;
        }

        public int ReplacePrefix(string from, string to, IList<string> except)
        {
            int hits = 0;
            for (int i = 0; i < file.Table.Count; i++)
            {
                string s = file.Table[i];
                if (!s.StartsWith(from, StringComparison.OrdinalIgnoreCase)) continue;
                if (except.Any(x => x.Length > 0 && s.StartsWith(x, StringComparison.OrdinalIgnoreCase))) continue;
                string head = s.Substring(0, from.Length);
                file.Table[i] = (head == head.ToLowerInvariant() ? to.ToLowerInvariant() : to) + s.Substring(from.Length);
                file.TableChanged = true;
                hits++;
            }
            return hits;
        }

        // Returns instances removed; references counts the references met.
        public int Drop(string guid, out int references)
        {
            references = 0;
            return Drop(file.TopLevel, guid, ref references);
        }

        int Drop(List<Element> list, string guid, ref int references)
        {
            int removed = 0;
            for (int i = list.Count - 1; i >= 0; i--)
            {
                var e = list[i];
                string tag = file.TagOf(e);
                if (tag == "instance" && Guid.Same(file.Get(e, "guid"), guid))
                {
                    list.RemoveAt(i);
                    removed++;
                    continue;
                }
                string target = file.Get(e, "ref");
                if (target != null && (Guid.Same(target, guid) || target.EndsWith("/" + guid, StringComparison.OrdinalIgnoreCase)))
                {
                    references++;
                    if (tag == "item")
                    {
                        list.RemoveAt(i);
                        continue;
                    }
                    foreach (var a in e.Attributes.Where(a => file.Table[a.Name] == "ref")) a.Value = file.Intern("null");
                }
                if (e.IsContainer) removed += Drop(e.Children, guid, ref references);
            }
            return removed;
        }

        public int DropField(string name) { return DropField(file.TopLevel, name); }

        int DropField(List<Element> list, string name)
        {
            int removed = 0;
            for (int i = list.Count - 1; i >= 0; i--)
            {
                var e = list[i];
                if (file.TagOf(e) != "instance" && file.Get(e, "name") == name)
                {
                    list.RemoveAt(i);
                    removed++;
                }
                else if (e.IsContainer) removed += DropField(e.Children, name);
            }
            return removed;
        }

        public int SetByte(string name, byte value) { return SetByte(file.TopLevel, name, value); }

        int SetByte(List<Element> list, string name, byte value)
        {
            int set = 0;
            foreach (var e in list)
            {
                if (e.IsContainer) set += SetByte(e.Children, name, value);
                else if (e.Kind != Element.Text && e.Kind != Element.Numbers && e.Payload != null && e.Payload.Length == 2
                         && file.Get(e, "name") == name)
                {
                    e.Payload = new[] { e.Payload[0], value };
                    set++;
                }
            }
            return set;
        }

        public bool SetField(string guid, string field, string value)
        {
            bool rounded;
            return SetField(guid, field, value, out rounded);
        }

        // The value typed like the field's present one, as the import and the window encode a changed value
        // (XmlImport.EncodeValue): text, numbers ('/'-separated; integer or decimal as the old value is), or
        // true / false / 0-255. A value the field's kind cannot hold throws InvalidDataException.
        public bool SetField(string guid, string field, string value, out bool rounded)
        {
            rounded = false;
            var instance = file.Instance(guid);
            if (instance == null || !instance.IsContainer) return false;
            var e = instance.Children.FirstOrDefault(c => file.Get(c, "name") == field && !c.IsContainer && c.Payload != null);
            if (e == null) return false;
            e.Payload = XmlImport.EncodeValue(file, e, value, out rounded);
            return true;
        }

        public void SetPartitionGuid(string guid)
        {
            foreach (var a in file.TopLevel[0].Attributes.Where(a => file.Table[a.Name] == "guid")) a.Value = file.Intern(guid);
        }

        public bool AddItem(string guid, string arrayName, string reference, string before)
        {
            var instance = file.Instance(guid);
            if (instance == null) return false;
            var array = instance.Children.FirstOrDefault(c => file.TagOf(c) == "array" && file.Get(c, "name") == arrayName);
            if (array == null || !array.IsContainer) return false;
            var shape = array.Children.FirstOrDefault(c => file.TagOf(c) == "item");
            if (shape == null) return false;
            var item = new Element { Tag = shape.Tag, Kind = shape.Kind, ContainerByte = shape.ContainerByte, Payload = shape.Payload };
            item.Attributes.Add(new Attribute(file.Intern("ref"), file.Intern(reference)));
            if (shape.IsContainer) item.Children = new List<Element>();
            int at = before == null ? -1 : array.Children.FindIndex(c => Guid.Same(file.Get(c, "ref"), before));
            array.Children.Insert(at >= 0 ? at : array.Children.Count, item);
            return true;
        }

        public bool CopyField(DbxFile source, string sourceGuid, string field, string guid)
        {
            var from = source.Instance(sourceGuid);
            var to = file.Instance(guid);
            if (from == null || to == null) return false;
            var original = FindField(source, from, field);
            if (original == null) return false;
            var copy = Import(source, original);
            if (!ReplaceField(to, field, copy)) to.Children.Add(copy);
            return true;
        }

        // First element named <field> below e that is not an instance (each element checked before its children).
        static Element FindField(DbxFile f, Element e, string field)
        {
            if (!e.IsContainer) return null;
            foreach (var c in e.Children)
            {
                if (f.TagOf(c) != "instance" && f.Get(c, "name") == field) return c;
                var deeper = FindField(f, c, field);
                if (deeper != null) return deeper;
            }
            return null;
        }

        bool ReplaceField(Element e, string field, Element replacement)
        {
            if (!e.IsContainer) return false;
            for (int i = 0; i < e.Children.Count; i++)
            {
                if (file.Get(e.Children[i], "name") == field)
                {
                    e.Children[i] = replacement;
                    return true;
                }
                if (ReplaceField(e.Children[i], field, replacement)) return true;
            }
            return false;
        }

        // Copies an element of another file into this one. Strings are interned in this order: tag, each
        // attribute's name then value, a text value, then the children (FORMAT.md section 5).
        Element Import(DbxFile source, Element e)
        {
            var copy = new Element { Tag = file.Intern(source.Table[e.Tag]), Kind = e.Kind, ContainerByte = e.ContainerByte };
            foreach (var a in e.Attributes)
            {
                int name = file.Intern(source.Table[a.Name]);
                copy.Attributes.Add(new Attribute(name, file.Intern(source.Table[a.Value])));
            }
            if (e.Kind == Element.Text)
            {
                int pos = 0;
                copy.Payload = Varint.Bytes(file.Intern(source.Table[Varint.Read(e.Payload, ref pos)]));
            }
            else copy.Payload = e.Payload;
            if (e.IsContainer) copy.Children = e.Children.Select(c => Import(source, c)).ToList();
            return copy;
        }

        // Copies instance <guid> of source and, depth first, every source instance it references locally that
        // this file does not have yet; inserted before instance <before>, or at the end of the partition.
        public int Graft(DbxFile source, string guid, string before)
        {
            var partition = file.TopLevel[0].Children;
            int at = partition.Count;
            if (before != null)
            {
                int i = partition.FindIndex(e => file.TagOf(e) == "instance" && Guid.Same(file.Get(e, "guid"), before));
                if (i >= 0) at = i;
            }
            var available = new Dictionary<string, Element>();
            foreach (var e in source.Instances()) available[source.Get(e, "guid").ToUpperInvariant()] = e;
            var present = new HashSet<string>(file.Instances().Select(e => file.Get(e, "guid").ToUpperInvariant()));
            var pending = new Stack<string>();
            pending.Push(guid.ToUpperInvariant());
            int copied = 0;
            while (pending.Count > 0)
            {
                string next = pending.Pop();
                Element e;
                if (present.Contains(next) || !available.TryGetValue(next, out e)) continue;
                present.Add(next);
                partition.Insert(at++, Import(source, e));
                copied++;
                foreach (string r in LocalReferences(source, e)) pending.Push(r);
            }
            return copied;
        }

        // Local references (36 characters, no '/') below e, upper-cased, each once, in document order.
        static List<string> LocalReferences(DbxFile f, Element e)
        {
            var found = new List<string>();
            var seen = new HashSet<string>();
            Collect(f, e, found, seen);
            return found;
        }

        static void Collect(DbxFile f, Element e, List<string> found, HashSet<string> seen)
        {
            string r = f.Get(e, "ref");
            if (r != null && r.Length == 36 && r.IndexOf('/') < 0 && seen.Add(r.ToUpperInvariant())) found.Add(r.ToUpperInvariant());
            if (e.IsContainer) foreach (var c in e.Children) Collect(f, c, found, seen);
        }
    }

    // Placed objects (ReferenceObjectData): blueprint name, position and cull slot (Enumeration).
    sealed class PlacedObject
    {
        public Element Instance, Slot;
        public string Guid, Blueprint;
        public float X, Y, Z;
        public int SlotValue { get { return Slot == null ? 0 : (int)BigEndian.U32(Slot.Payload, 2); } }

        public static List<PlacedObject> All(DbxFile f)
        {
            var list = new List<PlacedObject>();
            foreach (var i in f.Instances())
            {
                string type = f.Get(i, "type") ?? "";
                if (!type.EndsWith(".ReferenceObjectData")) continue;
                var o = new PlacedObject { Instance = i, Guid = f.Get(i, "guid") };
                foreach (var c in i.Children)
                {
                    string name = f.Get(c, "name");
                    if (name == "Enumeration" && c.Kind == Element.Numbers && c.Payload.Length == 6) o.Slot = c;
                    else if (name == "ReferencedObject")
                    {
                        string r = f.Get(c, "ref") ?? "";
                        var parts = r.ToLowerInvariant().Split('/');
                        o.Blueprint = parts.Length >= 2 ? parts[parts.Length - 2] : "";
                    }
                    else if (name == "Transform" && c.Kind == Element.Numbers && c.Payload.Length >= 2 + 16 * 4)
                    {
                        o.X = Float(c.Payload, 2 + 12 * 4);
                        o.Y = Float(c.Payload, 2 + 13 * 4);
                        o.Z = Float(c.Payload, 2 + 14 * 4);
                    }
                }
                list.Add(o);
            }
            return list;
        }

        static float Float(byte[] p, int at) { return BitConverter.ToSingle(BitConverter.GetBytes(BigEndian.U32(p, at)), 0); }

        public string PositionKey
        {
            get
            {
                var c = CultureInfo.InvariantCulture;
                return Blueprint + "@" + Math.Round(X, 1).ToString(c) + "," + Math.Round(Y, 1).ToString(c) + "," + Math.Round(Z, 1).ToString(c);
            }
        }

        public double Distance(PlacedObject o)
        {
            double dx = X - o.X, dy = Y - o.Y, dz = Z - o.Z;
            return Math.Sqrt(dx * dx + dy * dy + dz * dz);
        }

        public void SetSlot(int value)
        {
            Slot.Payload = new byte[] { 1, 4, (byte)(value >> 24), (byte)(value >> 16), (byte)(value >> 8), (byte)value };
        }
    }

    // Cull slots (FORMAT.md section 7): each object takes the slot of the same object in the level whose static cull
    // trees the file uses - same guid, else same blueprint and position, else the nearest unused same-blueprint
    // object within 50 m - and 0 (outside the cull tree) when there is none.
    static class SlotFixer
    {
        public sealed class Result { public int ByGuid, ByPosition, ByNearest, NoCounterpart, Changed; public readonly List<string> Unmatched = new List<string>(); }

        public static Result Fix(DbxFile file, DbxFile cullLevel)
        {
            var mine = PlacedObject.All(file).Where(o => o.Slot != null).ToList();
            var theirs = PlacedObject.All(cullLevel).Where(o => o.Slot != null).ToList();
            var byGuid = new Dictionary<string, PlacedObject>(StringComparer.OrdinalIgnoreCase);
            foreach (var o in theirs) byGuid[o.Guid] = o;
            var byPosition = new Dictionary<string, PlacedObject>();
            foreach (var o in theirs) if (!byPosition.ContainsKey(o.PositionKey)) byPosition[o.PositionKey] = o;
            var r = new Result();
            var assigned = new Dictionary<PlacedObject, int>();
            var left = new List<PlacedObject>();
            foreach (var o in mine)
            {
                PlacedObject t;
                if (byGuid.TryGetValue(o.Guid, out t)) { assigned[o] = t.SlotValue; r.ByGuid++; }
                else if (byPosition.TryGetValue(o.PositionKey, out t)) { assigned[o] = t.SlotValue; r.ByPosition++; }
                else left.Add(o);
            }
            var used = new HashSet<int>(assigned.Values);
            foreach (var o in left)
            {
                var best = theirs.Where(t => t.Blueprint == o.Blueprint && t.SlotValue != 0 && !used.Contains(t.SlotValue))
                                 .OrderBy(t => t.Distance(o)).FirstOrDefault();
                if (best != null && best.Distance(o) < 50)
                {
                    assigned[o] = best.SlotValue;
                    used.Add(best.SlotValue);
                    r.ByNearest++;
                }
                else
                {
                    assigned[o] = 0;
                    r.NoCounterpart++;
                    r.Unmatched.Add(o.Blueprint + " " + o.Guid);
                }
            }
            foreach (var kv in assigned)
                if (kv.Key.SlotValue != kv.Value) { kv.Key.SetSlot(kv.Value); r.Changed++; }
            return r;
        }
    }

    // Problems that crash or break a level, found without starting the game (FORMAT.md section 7).
    static class LevelCheck
    {
        public sealed class Finding
        {
            public bool Error;
            public string Where, Message;
            public override string ToString() { return (Error ? "ERROR  " : "WARN   ") + Where + ": " + Message; }
        }

        // model groups and their members need these parts (terrain has its own collision and may leave PhysicsData empty)
        static readonly string[] WorldTypes = { "ModelGroupEntityData", "ModelGroupMemberData", "ForestEntityData", "TreeMemberData" };
        // the level's world: must be listed in LevelData Objects, or it is not loaded
        static readonly string[] ListedWorldTypes = { "TerrainEntityData", "ModelGroupEntityData", "ForestEntityData", "StaticEnvmapEntityData" };
        static readonly string[] WorldParts = { "PhysicsData", "MeshEntityType" };

        // slotsFrom: the level whose cull trees the file uses (or null); bundles: unpacked folders to resolve
        // references into other files (or none).
        public static List<Finding> Run(DbxFile f, DbxFile slotsFrom, IList<string> bundles)
        {
            var found = new List<Finding>();
            var guids = new HashSet<string>(f.Instances().Select(i => f.Get(i, "guid") ?? ""), StringComparer.OrdinalIgnoreCase);
            var external = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
            foreach (var inst in f.Instances())
            {
                string type = f.Get(inst, "type") ?? "", where = Short(type) + " " + f.Get(inst, "guid");
                bool levelData = type.EndsWith(".LevelData");
                bool world = WorldTypes.Any(w => type.EndsWith(w));
                Walk(f, inst, "", (e, path) =>
                {
                    string r = f.Get(e, "ref");
                    if (r == null) return;
                    bool inObjects = levelData && path.StartsWith("Objects");
                    bool isLocal = r.Length == 36 && r.IndexOf('/') < 0;
                    if (r == "null")
                    {
                        if (inObjects && f.TagOf(e) == "item")
                            found.Add(new Finding { Error = true, Where = where + " " + path, Message = "empty entry in the level's object list (the game reads it without a check and crashes)" });
                        else if (world && WorldParts.Contains(f.Get(e, "name")))
                            found.Add(new Finding { Error = true, Where = where + " " + path, Message = "world object without " + f.Get(e, "name") + " (the model-group set-up crashes on it)" });
                    }
                    else if (isLocal && !guids.Contains(r))
                        found.Add(new Finding { Error = true, Where = where + " " + path, Message = "refers to instance " + r + ", which is not in this file" + (inObjects ? " (an empty entry in the level's object list: crash)" : "") });
                    else if (r.IndexOf('/') > 0)
                    {
                        int cut = r.LastIndexOf('/');
                        List<string> users;
                        string key = r.Substring(0, cut) + "|" + r.Substring(cut + 1);
                        if (!external.TryGetValue(key, out users)) external[key] = users = new List<string>();
                        if (users.Count < 3) users.Add(where + " " + path);
                    }
                });
            }
            CheckWorldListed(f, found);
            CheckSlots(f, slotsFrom, found);
            if (bundles.Count > 0) CheckExternal(external, bundles, found);
            return found;
        }

        static void CheckWorldListed(DbxFile f, List<Finding> found)
        {
            var level = f.Instances().FirstOrDefault(i => (f.Get(i, "type") ?? "").EndsWith(".LevelData"));
            if (level == null) return;
            var objects = level.Children.FirstOrDefault(c => f.Get(c, "name") == "Objects" && c.IsContainer);
            var listed = new HashSet<string>(objects == null ? new string[0] : objects.Children.Select(c => f.Get(c, "ref") ?? ""), StringComparer.OrdinalIgnoreCase);
            foreach (var i in f.Instances())
            {
                string type = f.Get(i, "type") ?? "", guid = f.Get(i, "guid");
                if (ListedWorldTypes.Any(w => type.EndsWith(w)) && !listed.Contains(guid))
                    found.Add(new Finding { Error = false, Where = Short(type) + " " + guid, Message = "not in the level's object list, so it is not loaded (missing terrain or buildings in game)" });
            }
        }

        static string Short(string type) { int dot = type.LastIndexOf('.'); return dot >= 0 ? type.Substring(dot + 1) : type; }

        static void Walk(DbxFile f, Element e, string path, Action<Element, string> visit)
        {
            if (!e.IsContainer) return;
            var seen = new Dictionary<string, int>();
            foreach (var c in e.Children)
            {
                string name = f.Get(c, "name") ?? f.TagOf(c);
                int n;
                seen.TryGetValue(name, out n);
                seen[name] = n + 1;
                string p = path.Length == 0 ? name : path + (f.TagOf(c) == "item" ? "[" + n + "]" : "." + name);
                visit(c, p);
                Walk(f, c, p, visit);
            }
        }

        static void CheckSlots(DbxFile f, DbxFile slotsFrom, List<Finding> found)
        {
            var objects = PlacedObject.All(f).Where(o => o.Slot != null && o.SlotValue != 0).ToList();
            foreach (var g in objects.GroupBy(o => o.SlotValue).Where(g => g.Count() > 1).Take(20))
                found.Add(new Finding { Error = false, Where = "cull slot " + g.Key, Message = "shared by " + g.Count() + " objects (" + string.Join(", ", g.Take(3).Select(o => o.Blueprint)) + "): they are shown and hidden together - see-through objects" });
            if (slotsFrom == null) return;
            var theirs = PlacedObject.All(slotsFrom).Where(o => o.Slot != null).ToDictionary(o => o.Guid, o => o, StringComparer.OrdinalIgnoreCase);
            var wrong = PlacedObject.All(f).Where(o => o.Slot != null).Where(o => { PlacedObject t; return theirs.TryGetValue(o.Guid, out t) && t.SlotValue != o.SlotValue; }).ToList();
            if (wrong.Count > 0)
                found.Add(new Finding { Error = false, Where = "cull slots", Message = wrong.Count + " objects have another slot than the same object in the cull trees' level (e.g. " + string.Join(", ", wrong.Take(3).Select(o => o.Blueprint + " " + o.SlotValue)) + ") - see-through objects; fix with: DbxEdit fixslots" });
        }

        static void CheckExternal(Dictionary<string, List<string>> external, IList<string> bundles, List<Finding> found)
        {
            var index = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string b in bundles)
                foreach (string p in Directory.GetFiles(b, "*.dbx", SearchOption.AllDirectories))
                {
                    string rel = p.Substring(b.Length).TrimStart('\\', '/').Replace('\\', '/');
                    rel = rel.Substring(0, rel.Length - 4);
                    if (!index.ContainsKey(rel)) index[rel] = p;
                }
            var loaded = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);
            int missingFiles = 0;
            foreach (var kv in external)
            {
                var parts = kv.Key.Split('|');
                string partition = parts[0], guid = parts[1], path;
                if (!index.TryGetValue(partition, out path))
                {
                    if (missingFiles++ < 30)
                        found.Add(new Finding { Error = false, Where = kv.Value[0], Message = "refers to " + partition + ", which is not in the given folders" });
                    continue;
                }
                HashSet<string> g;
                if (!loaded.TryGetValue(path, out g))
                {
                    var d = new DbxFile(path);
                    g = d.IsBinary ? new HashSet<string>(d.Instances().Select(i => d.Get(i, "guid") ?? ""), StringComparer.OrdinalIgnoreCase) : null;
                    loaded[path] = g;
                }
                if (g != null && !g.Contains(guid))
                    found.Add(new Finding { Error = true, Where = kv.Value[0], Message = "refers to instance " + guid + " in " + partition + ", which that file does not have" });
            }
            if (missingFiles > 30)
                found.Add(new Finding { Error = false, Where = "references to other files", Message = (missingFiles - 30) + " more files not in the given folders" });
        }
    }

    static class Program
    {
        static int Main(string[] args)
        {
            ToolLog.Start("dbxedit");
            try { return Run(args); }
            catch (Exception e) { return ToolLog.Fail(e); }       // unexpected: logged and explained, exit code 1
        }

        static int Run(string[] args)
        {
            if (args.Length >= 2 && args[0] == "xml") return Xml(args.Skip(1));
            if (args.Length >= 2 && args[0] == "roundtrip") return Roundtrip(args[1]);
            if (args.Length >= 3 && args[0] == "edit") return Edit(args);
            if (args.Length >= 3 && args[0] == "import") return Import(args);
            if (args.Length >= 2 && args[0] == "check") return Check(args);
            if (args.Length >= 3 && args[0] == "fixslots") return FixSlots(args);
            Console.Error.WriteLine("usage: DbxEdit xml <file|folder> ... | roundtrip <file|folder> | edit <in.dbx> <out.dbx> <command|@file> ...");
            Console.Error.WriteLine("       DbxEdit import <in.xml> <out.dbx> [--like <original.dbx>] [--types <file.dbx|folder>] ...");
            Console.Error.WriteLine("       DbxEdit check <level.dbx> [--slots-from <level.dbx>] [--bundle <folder>] ...");
            Console.Error.WriteLine("       DbxEdit fixslots <in.dbx> <out.dbx> --from <level.dbx>");
            Console.Error.WriteLine("  replace=<old>|<new>  drop=<guid>  dropfield=<name>  graft=<source.dbx>|<guid>");
            return 2;
        }

        static string[] DbxFiles(string path)
        {
            return Directory.Exists(path) ? Directory.GetFiles(path, "*.dbx", SearchOption.AllDirectories) : new[] { path };
        }

        // A zero-byte file is a bundle's "*deleted*" entry, as older FbrbTool versions unpacked them (current
        // ones list them in fbrb.manifest only): there is nothing to read, and it is not an error.
        static bool DeletedEntry(string f) { return new FileInfo(f).Length == 0; }

        static string DeletedNote(int n)
        {
            return n == 0 ? "" : ", skipped " + n + " empty file(s) (*deleted* bundle entries)";
        }

        static int Xml(IEnumerable<string> paths)
        {
            // exit 1 if any file could not be read or converted; a file that is not binary DBX (plain XML already)
            // is skipped, not counted as converted
            int converted = 0, deleted = 0, notBinary = 0, failed = 0;
            foreach (string f in paths.SelectMany(DbxFiles))
            {
                try
                {
                    if (DeletedEntry(f)) { deleted++; continue; }
                    byte[] data = File.ReadAllBytes(f);
                    if (data.Length < 8 || Encoding.ASCII.GetString(data, 0, 8) != "{binary}") { notBinary++; continue; }
                    File.WriteAllText(Path.ChangeExtension(f, ".xml"), XmlExport.Convert(data));
                    converted++;
                }
                catch (Exception e)
                {
                    failed++;
                    Console.Error.WriteLine(f + ": " + e.Message);
                    ToolLog.Warn("xml: " + f + ": " + e.Message);
                }
            }
            Console.WriteLine("converted " + converted + " file(s)" + DeletedNote(deleted)
                              + (notBinary > 0 ? ", skipped " + notBinary + " not binary DBX" : "")
                              + (failed > 0 ? "; " + failed + " FAILED" : ""));
            return failed > 0 ? 1 : 0;
        }

        // import <in.xml> <out.dbx> [--like <original.dbx>] [--types <file.dbx|folder>]...
        // Reference: --like, else <in>.dbx next to the XML. Exit 0 = done, 1 = failed, 3 = written with guessed types.
        static int Import(string[] args)
        {
            string xml = args[1], output = args[2], like = null;
            var typePaths = new List<string>();
            for (int i = 3; i < args.Length; i++)
            {
                if (args[i] == "--like" && i + 1 < args.Length) like = args[++i];
                else if (args[i] == "--types" && i + 1 < args.Length) typePaths.Add(args[++i]);
                else { Console.Error.WriteLine("import: unknown option " + args[i]); return 2; }
            }
            if (like == null && File.Exists(Path.ChangeExtension(xml, ".dbx"))) like = Path.ChangeExtension(xml, ".dbx");
            try
            {
                DbxFile reference = null;
                if (like != null)
                {
                    reference = new DbxFile(like);
                    if (!reference.IsBinary) { Console.Error.WriteLine("import: not a binary dbx: " + like); return 1; }
                }
                var typeFiles = typePaths.SelectMany(DbxFiles).Select(p => new DbxFile(p)).Where(d => d.IsBinary).ToList();
                XmlImport report;
                var result = XmlImport.Import(xml, reference, typeFiles, out report);
                File.WriteAllBytes(output, result.Serialize());
                Console.WriteLine("import " + xml + " -> " + output + (like != null ? " (types from " + like + ")" : "") + ": "
                                  + report.Kept + " unchanged, " + report.Changed + " changed, " + report.Added + " new, "
                                  + report.Removed + " removed"
                                  + (report.Rounded > 0 ? "; " + report.Rounded + " decimal(s) rounded to the nearest 4-byte value" : ""));
                if (report.Guesses.Count == 0) return 0;
                foreach (string g in report.Guesses.Take(50)) Console.Error.WriteLine("guess: " + g);
                Console.Error.WriteLine(report.Guesses.Count + " value(s) typed by guess - check them, or give the original file (--like) or more files (--types)");
                return 3;
            }
            catch (Exception e)
            {
                if (!(e is InvalidDataException || e is System.Xml.XmlException || e is IOException || e is UnauthorizedAccessException)) throw;
                Console.Error.WriteLine("import failed: " + e.Message);
                ToolLog.Error(e, "import");
                return 1;
            }
        }

        // check <level.dbx> [--slots-from <level.dbx>] [--bundle <folder>]...   exit 0 = no errors, 1 = errors found
        static int Check(string[] args)
        {
            DbxFile slotsFrom = null;
            var bundles = new List<string>();
            for (int i = 2; i < args.Length; i++)
            {
                if (args[i] == "--slots-from" && i + 1 < args.Length) slotsFrom = new DbxFile(args[++i]);
                else if (args[i] == "--bundle" && i + 1 < args.Length) bundles.Add(args[++i]);
                else { Console.Error.WriteLine("check: unknown option " + args[i]); return 2; }
            }
            var f = new DbxFile(args[1]);
            if (!f.IsBinary) { Console.Error.WriteLine("not a binary dbx: " + args[1]); return 2; }
            var found = LevelCheck.Run(f, slotsFrom, bundles);
            foreach (var x in found.OrderBy(x => x.Error ? 0 : 1)) Console.WriteLine(x);
            int errors = found.Count(x => x.Error);
            Console.WriteLine("check " + args[1] + ": " + errors + " error(s), " + (found.Count - errors) + " warning(s)");
            return errors > 0 ? 1 : 0;
        }

        // fixslots <in.dbx> <out.dbx> --from <level.dbx>
        static int FixSlots(string[] args)
        {
            if (args.Length < 5 || args[3] != "--from") { Console.Error.WriteLine("usage: DbxEdit fixslots <in.dbx> <out.dbx> --from <level.dbx>"); return 2; }
            var f = new DbxFile(args[1]);
            var from = new DbxFile(args[4]);
            if (!f.IsBinary || !from.IsBinary) { Console.Error.WriteLine("not a binary dbx"); return 2; }
            var r = SlotFixer.Fix(f, from);
            Console.WriteLine("fixslots: " + r.Changed + " slot(s) changed; matched by guid " + r.ByGuid + ", by blueprint and position "
                              + r.ByPosition + ", nearest within 50 m " + r.ByNearest + ", no counterpart (slot 0) " + r.NoCounterpart);
            foreach (string u in r.Unmatched.Take(30)) Console.WriteLine("  no counterpart: " + u);
            File.WriteAllBytes(args[2], f.Serialize());
            return 0;
        }

        static int Roundtrip(string path)
        {
            int same = 0, different = 0, skipped = 0, deleted = 0;
            foreach (string f in DbxFiles(path))
            {
                if (DeletedEntry(f)) { deleted++; continue; }
                DbxFile d;
                try { d = new DbxFile(f); }
                catch (Exception e)
                {
                    Console.WriteLine("ERROR " + f + ": " + e.Message);
                    ToolLog.Warn("roundtrip: " + f + ": " + e.Message);
                    different++;
                    continue;
                }
                if (!d.IsBinary) { skipped++; continue; }
                byte[] written = d.Serialize();
                if (written.SequenceEqual(d.Original)) same++;
                else if (++different <= 10) Console.WriteLine("DIFF " + f + " (" + d.Original.Length + " -> " + written.Length + ")");
            }
            Console.WriteLine("roundtrip: " + same + " identical, " + different + " different, " + skipped + " not binary" + DeletedNote(deleted));
            return different == 0 ? 0 : 1;
        }

        static int Edit(string[] args)
        {
            var file = new DbxFile(args[1]);
            if (!file.IsBinary) { Console.Error.WriteLine("not a binary dbx: " + args[1]); return 2; }
            var commands = new List<string>();
            foreach (string a in args.Skip(3))
            {
                if (a.StartsWith("@"))
                    commands.AddRange(File.ReadAllLines(a.Substring(1)).Select(l => l.Trim()).Where(l => l.Length > 0 && !l.StartsWith("#")));
                else commands.Add(a);
            }
            var editor = new Editor(file);
            var sources = new Dictionary<string, DbxFile>();
            Func<string, DbxFile> source = p => { DbxFile s; if (!sources.TryGetValue(p, out s)) sources[p] = s = new DbxFile(p); return s; };
            int fieldsSet = 0;
            foreach (string c in commands)
            {
                int eq = c.IndexOf('=');
                string verb = eq < 0 ? c : c.Substring(0, eq + 1);
                string[] p = eq < 0 ? new string[0] : c.Substring(eq + 1).Split('|');
                switch (verb)
                {
                    case "replace=":
                    {
                        int hits = editor.Replace(p[0], p[1]);
                        Console.WriteLine("replace " + p[0] + " -> " + p[1] + ": " + hits + " string(s)");
                        if (hits == 0) { Console.Error.WriteLine("not found: " + p[0]); return 1; }
                        break;
                    }
                    case "drop=":
                    {
                        int references;
                        string guid = c.Substring(eq + 1);
                        int removed = editor.Drop(guid, out references);
                        Console.WriteLine("drop " + guid + ": " + removed + " instance(s), " + references + " reference(s)");
                        if (removed == 0) { Console.Error.WriteLine("no instance " + guid); return 1; }
                        break;
                    }
                    case "replaceprefix=":
                        Console.WriteLine("replaceprefix " + p[0] + " -> " + p[1] + ": " + editor.ReplacePrefix(p[0], p[1], p.Skip(2).ToList()) + " string(s)");
                        break;
                    case "partguid=":
                        editor.SetPartitionGuid(c.Substring(eq + 1));
                        Console.WriteLine("partguid " + c.Substring(eq + 1));
                        break;
                    case "copyfield=":
                    {
                        bool ok = editor.CopyField(source(p[0]), p[1], p[2], p[3]);
                        Console.WriteLine("copyfield " + p[2] + ": " + (ok ? "copied" : "NOT FOUND"));
                        if (!ok) return 1;
                        break;
                    }
                    case "set=":
                    {
                        byte v = p[1] == "true" ? (byte)1 : p[1] == "false" ? (byte)0 : byte.Parse(p[1]);
                        int n = editor.SetByte(p[0], v);
                        Console.WriteLine("set " + p[0] + " = " + p[1] + ": " + n + " field(s)");
                        if (n == 0) { Console.Error.WriteLine("no bool/byte field " + p[0]); return 1; }
                        break;
                    }
                    case "setfield=":
                    {
                        bool rounded;
                        string value = string.Join("|", p.Skip(2));        // a text value may itself hold '|'
                        if (!editor.SetField(p[0], p[1], value, out rounded)) { Console.Error.WriteLine("setfield: no settable field " + p[1] + " in " + p[0]); return 1; }
                        if (rounded) Console.WriteLine("setfield " + p[1] + ": rounded to the nearest 4-byte value");
                        fieldsSet++;
                        break;
                    }
                    case "additem=":
                    {
                        bool ok = editor.AddItem(p[0], p[1], p[2], p.Length > 3 ? p[3] : null);
                        Console.WriteLine("additem " + p[2] + " -> " + p[1] + ": " + (ok ? "added" : "NOT FOUND"));
                        if (!ok) return 1;
                        break;
                    }
                    case "dropfield=":
                        Console.WriteLine("dropfield " + c.Substring(eq + 1) + ": " + editor.DropField(c.Substring(eq + 1)) + " element(s)");
                        break;
                    case "graft=":
                    {
                        int n = editor.Graft(source(p[0]), p[1], p.Length > 2 ? p[2] : null);
                        Console.WriteLine("graft " + p[1] + " from " + Path.GetFileName(p[0]) + ": " + n + " instance(s)");
                        if (n == 0 && file.Instance(p[1]) == null) { Console.Error.WriteLine("nothing grafted for " + p[1]); return 1; }
                        break;
                    }
                    default:
                        Console.Error.WriteLine("unknown command " + c);
                        return 2;
                }
            }
            if (fieldsSet > 0) Console.WriteLine("setfield: " + fieldsSet + " field(s)");
            File.WriteAllBytes(args[2], file.Serialize());
            return 0;
        }
    }

    // Logging and error reports, as the suite's Python tools keep them (core/mohcore/errors.py): a log per tool
    // in %LOCALAPPDATA%\moh-tool-suite\logs\<tool>-<date>.log (the 20 newest kept), and errors explained in
    // words - the likely cause and what to do - with the log's path.
    public static class ToolLog
    {
        static string tool = "tool", path;
        const int Keep = 20;

        public static string LogPath { get { return path; } }

        public static void Start(string name)
        {
            tool = name;
            try
            {
                // LOCALAPPDATA as the Python tools read it (the same folder; tests point it elsewhere)
                string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
                if (string.IsNullOrEmpty(local)) local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string dir = Path.Combine(Path.Combine(local,
                                                       "moh-tool-suite"), "logs");
                Directory.CreateDirectory(dir);
                path = Path.Combine(dir, name + "-" + DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + ".log");
                foreach (string old in Directory.GetFiles(dir, name + "-*.log").OrderByDescending(p => p, StringComparer.OrdinalIgnoreCase).Skip(Keep))
                    try { File.Delete(old); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                Info("--- " + name + " started: " + string.Join(" ", Environment.GetCommandLineArgs().Skip(1)) + " ---");
            }
            catch (Exception) { path = null; }               // no log folder: the tool still runs
        }

        static void Write(string level, string message)
        {
            if (path == null) return;
            try
            {
                File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss,fff", CultureInfo.InvariantCulture) + " " +
                                         level.PadRight(7) + " " + message + Environment.NewLine);
            }
            catch (Exception) { }
        }

        public static void Info(string message) { Write("INFO", message); }
        public static void Warn(string message) { Write("WARNING", message); }

        public static void Error(Exception e, string where)
        {
            string[] ce = Explain(e);
            Write("ERROR", tool + " error" + (where != null ? " in " + where : "") + ": " + ce[0] + Environment.NewLine + e);
        }

        // { cause, what to do }
        public static string[] Explain(Exception e)
        {
            var io = e as IOException;
            int code = io != null ? (io.HResult & 0xFFFF) : 0;
            if (io != null && (code == 32 || code == 33))
                return new[] { "A file is in use by another program: " + e.Message, "Close the program using it (the game, an editor) and try again." };
            if (e is UnauthorizedAccessException)
                return new[] { "A file or folder could not be written or read: " + e.Message, "Check it is not read-only, or choose another folder." };
            if (e is FileNotFoundException || e is DirectoryNotFoundException)
                return new[] { "A file the tool needs is missing: " + e.Message, "Check the path." };
            if (e is InvalidDataException || e is System.IO.EndOfStreamException || e is ArgumentOutOfRangeException || e is IndexOutOfRangeException)
                return new[] { "The file is not the kind the tool reads, or it is damaged: " + e.Message, "Check it is the right file; unpack it again from the game if it is damaged." };
            if (e is OutOfMemoryException)
                return new[] { "The PC ran out of memory.", "Close other programs, or work on fewer files at once." };
            return new[] { e.GetType().Name + ": " + e.Message, "If it happens again, send the log file to the tool's author." };
        }

        // The text a user sees: cause, what to do, where the details are.
        public static string Report(Exception e)
        {
            string[] ce = Explain(e);
            return ce[0] + Environment.NewLine + Environment.NewLine + "What to do: " + ce[1] + Environment.NewLine + Environment.NewLine +
                   "Details were saved to:" + Environment.NewLine + (path ?? "(no log file)");
        }

        // A command-line tool's last word on an unexpected error: logged, explained on stderr, exit code 1.
        public static int Fail(Exception e)
        {
            Error(e, null);
            string[] ce = Explain(e);
            Console.Error.WriteLine("error: " + ce[0]);
            Console.Error.WriteLine("  what to do: " + ce[1]);
            Console.Error.WriteLine("  details: " + (path ?? "(no log file)"));
            return 1;
        }
    }
}
