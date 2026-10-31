// DbxEdit test suite. Built and run by tests\run_tests.bat; compiled together with DbxEdit.cs and DbxEditGUI.cs.
//
//   DbxTests                                   unit tests: small files built in memory (no game data needed)
//   DbxTests --corpus <folder> [...]           + every binary .dbx under the folders: round trip, XML import
//                                              identity, edited imports, imports without a reference
//   DbxTests --corpus ... --golden <file>      + fingerprints: outputs must match recorded SHA-1s
//   DbxTests --corpus ... --make-golden <file> record fingerprints (do this only with a version known to be right)
//   --edit-every <n>   edited-import test on every n-th corpus file (default 25)
//   --no-gui           skip the window test
// Exit code 0 = all passed.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MohDbx;

static class DbxTests
{
    static int passed, failed;
    static readonly string Here = AppDomain.CurrentDomain.BaseDirectory;
    static string temp;

    static void Check(bool ok, string what)
    {
        if (ok) passed++;
        else { failed++; Console.WriteLine("  FAIL  " + what); }
    }

    [STAThread]
    static int Main(string[] args)
    {
        var corpus = new List<string>();
        string golden = null, makeGolden = null;
        int editEvery = 25;
        bool gui = true;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--corpus") { while (i + 1 < args.Length && !args[i + 1].StartsWith("--")) corpus.Add(args[++i]); }
            else if (args[i] == "--golden") golden = args[++i];
            else if (args[i] == "--make-golden") makeGolden = args[++i];
            else if (args[i] == "--edit-every") editEvery = int.Parse(args[++i]);
            else if (args[i] == "--no-gui") gui = false;
            else { Console.Error.WriteLine("unknown option " + args[i]); return 2; }
        }
        temp = Path.Combine(Path.GetTempPath(), "dbxtests_" + Process.GetCurrentProcess().Id);
        Directory.CreateDirectory(temp);
        try
        {
            var groups = new List<KeyValuePair<string, Action>>
            {
                new KeyValuePair<string, Action>("format and round trip", Format),
                new KeyValuePair<string, Action>("XML export", XmlOut),
                new KeyValuePair<string, Action>("XML import", XmlIn),
                new KeyValuePair<string, Action>("XML reader", XmlReader),
                new KeyValuePair<string, Action>("edit commands", Commands),
                new KeyValuePair<string, Action>("graft and copyfield", GraftCopy),
                new KeyValuePair<string, Action>("level check", LevelChecks),
                new KeyValuePair<string, Action>("cull-slot fixer", Slots),
                new KeyValuePair<string, Action>("command line", CommandLine),
            };
            if (gui) groups.Add(new KeyValuePair<string, Action>("window", Window));
            foreach (var g in groups) Run(g.Key, g.Value);
            if (corpus.Count > 0 && makeGolden == null) Run("corpus", () => Corpus(corpus, editEvery));
            if (makeGolden != null) Run("make fingerprints", () => MakeGolden(corpus, makeGolden));
            if (golden != null) Run("fingerprints", () => Golden(corpus, golden));
        }
        finally
        {
            try { Directory.Delete(temp, true); } catch (Exception) { }
        }
        Console.WriteLine();
        Console.WriteLine(failed == 0 ? "ALL PASSED (" + passed + " checks)" : failed + " FAILED, " + passed + " passed");
        return failed == 0 ? 0 : 1;
    }

    static void Run(string name, Action test)
    {
        int before = failed, beforePassed = passed;
        var sw = Stopwatch.StartNew();
        try { test(); }
        catch (Exception e) { failed++; Console.WriteLine("  FAIL  " + name + " threw: " + e.GetType().Name + ": " + e.Message); }
        Console.WriteLine((failed == before ? "ok    " : "FAIL  ") + name + "  (" + (passed - beforePassed) + " checks, " + sw.Elapsed.TotalSeconds.ToString("0.0") + " s)");
    }

    // ------------------------------------------------------------------------------------------- building test files

    sealed class Builder
    {
        public readonly DbxFile F = DbxFile.CreateEmpty();
        public readonly Element Partition;
        public Builder(string guid)
        {
            Partition = Container("partition", "guid", guid, "primaryInstance", guid);
            F.TopLevel.Add(Partition);
        }

        Element E(string tag, int kind, params string[] attrs)
        {
            var e = new Element { Tag = F.Intern(tag), Kind = kind };
            for (int i = 0; i + 1 < attrs.Length; i += 2) e.Attributes.Add(new MohDbx.Attribute(F.Intern(attrs[i]), F.Intern(attrs[i + 1])));
            return e;
        }

        public Element Container(string tag, params string[] attrs)
        {
            var e = E(tag, Element.Container, attrs);
            e.Children = new List<Element>();
            return e;
        }

        public Element Instance(string type, string guid, string id = null)
        {
            var i = id == null ? Container("instance", "guid", guid, "type", type) : Container("instance", "guid", guid, "type", type, "id", id);
            Partition.Children.Add(i);
            return i;
        }

        public Element Text(Element parent, string name, string value)
        {
            var e = E("field", Element.Text, "name", name);
            e.Payload = Varint.Bytes(F.Intern(value));
            parent.Children.Add(e);
            return e;
        }

        public Element Ints(Element parent, string tag, string name, params int[] v)
        {
            return Numbers(parent, tag, name, v.Select(x => (uint)x).ToArray());
        }

        public Element Floats(Element parent, string tag, string name, params float[] v)
        {
            return Numbers(parent, tag, name, v.Select(x => BitConverter.ToUInt32(BitConverter.GetBytes(x), 0)).ToArray());
        }

        Element Numbers(Element parent, string tag, string name, uint[] v)
        {
            var e = E(tag, Element.Numbers, "name", name);
            var p = new List<byte>();
            Varint.Write(p, v.Length);
            Varint.Write(p, 4);
            foreach (uint x in v) BigEndian.Put(p, x);
            e.Payload = p.ToArray();
            parent.Children.Add(e);
            return e;
        }

        public Element Bool(Element parent, string name, bool v)
        {
            var e = E("field", 6, "name", name);
            e.Payload = new byte[] { 1, (byte)(v ? 1 : 0) };
            parent.Children.Add(e);
            return e;
        }

        public Element Ref(Element parent, string name, string target)
        {
            var e = E("field", Element.Text, "ref", target, "name", name);
            e.Payload = Varint.Bytes(F.Intern(""));
            parent.Children.Add(e);
            return e;
        }

        public Element RefList(Element parent, string name, params string[] targets)
        {
            var a = Container("array", "name", name);
            foreach (string t in targets)
            {
                var item = E("item", Element.Text, "ref", t);
                item.Payload = Varint.Bytes(F.Intern(""));
                a.Children.Add(item);
            }
            parent.Children.Add(a);
            return a;
        }

        // a placed object with blueprint, position and cull slot
        public Element Placed(string guid, string blueprint, float x, float y, float z, int slot)
        {
            var i = Instance("Entity.ReferenceObjectData", guid);
            Ref(i, "ReferencedObject", "objects/props/" + blueprint + "/AAAAAAAA-0000-0000-0000-000000000001");
            Floats(i, "complex", "Transform", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1);
            Ints(i, "field", "Enumeration", slot);
            return i;
        }

        public DbxFile Reload() { return new DbxFile(F.Serialize()); }
        public byte[] Bytes() { return F.Serialize(); }
    }

    const string G1 = "11111111-1111-1111-1111-111111111111", G2 = "22222222-2222-2222-2222-222222222222",
                 G3 = "33333333-3333-3333-3333-333333333333", G4 = "44444444-4444-4444-4444-444444444444",
                 GP = "99999999-9999-9999-9999-999999999999";

    static Builder Sample()
    {
        var b = new Builder(GP);
        var a = b.Instance("Game.Thing", G1, "First");
        b.Text(a, "Name", "Levels/Test & <Demo>");
        b.Ints(a, "field", "Count", 7);
        b.Floats(a, "field", "Scale", 1.5f);
        b.Floats(a, "field", "One", 1.0f);
        b.Bool(a, "Enabled", true);
        b.Text(a, "Empty", "");
        b.RefList(a, "Objects", G2, G3);
        var c = b.Instance("Game.Other", G2);
        b.Ref(c, "Partner", G1);
        b.Ints(c, "field", "Id", 5);
        var d = b.Instance("Game.Other", G3);
        b.Ref(d, "Partner", G2);
        return b;
    }

    // ------------------------------------------------------------------------------------------- tests

    static void Format()
    {
        foreach (int v in new[] { 0, 1, 127, 128, 255, 16383, 16384, 2097151, 2097152, int.MaxValue })
        {
            var bytes = Varint.Bytes(v);
            int p = 0;
            Check(Varint.Read(bytes, ref p) == v && p == bytes.Length, "varint " + v);
        }
        var b = Sample();
        byte[] once = b.Bytes();
        var back = new DbxFile(once);
        Check(back.IsBinary, "a written file reads back as binary DBX");
        Check(back.Serialize().SequenceEqual(once), "write, read, write: identical bytes");
        Check(Encoding.ASCII.GetString(once, 0, 8) == "{binary}", "magic");
        Check(BigEndian.U32(once, 8) == 24 + BigEndian.U32(once, 16), "header: payload start = 24 + string area");
        Check(BigEndian.U32(once, 20) == back.Table.Count, "header: string count");
        var unchanged = new DbxFile(once);
        Check(!unchanged.TableChanged && unchanged.Serialize().SequenceEqual(once), "an untouched file keeps its string area as stored");
        Check(!new DbxFile(Encoding.ASCII.GetBytes("<?xml version=\"1.0\"?>")).IsBinary, "plain XML is not binary");
    }

    static void XmlOut()
    {
        string x = XmlExport.Convert(Sample().Bytes());
        var lines = x.Split(new[] { "\r\n" }, StringSplitOptions.None);
        Check(lines[0] == "<?xml version=\"1.0\"?>", "XML declaration line");
        Check(x.Contains("\t<instance guid=\"" + G1 + "\" type=\"Game.Thing\" id=\"First\">\r\n"), "instance line, one tab per level");
        Check(x.Contains("<field name=\"Name\">Levels/Test &amp; &lt;Demo&gt;</field>"), "text with escapes");
        Check(x.Contains("<field name=\"Count\">7</field>"), "whole number");
        Check(x.Contains("<field name=\"Scale\">1.5</field>"), "decimal");
        Check(x.Contains("<field name=\"One\">1</field>"), "a whole decimal prints like an integer (1.0 -> 1)");
        Check(x.Contains("<field name=\"Enabled\">true</field>"), "bool");
        Check(x.Contains("<field name=\"Empty\" />"), "empty text is self-closing");
        Check(x.Contains("\t\t\t<item ref=\"" + G2 + "\" />"), "list item");
        Check(x.Contains("\t</instance>\r\n"), "closing tag at the container's indentation");
        Check(x.EndsWith("</partition>\r\n"), "ends with the partition");
        // hash fields stay whole numbers whatever the bits look like
        var b = new Builder(GP);
        b.Ints(b.Instance("T", G1), "field", "Hash", unchecked((int)0x3F800000));
        Check(XmlExport.Convert(b.Bytes()).Contains("<field name=\"Hash\">1065353216</field>"), "hash fields are whole numbers");
    }

    static string WriteXml(string name, string xml)
    {
        string p = Path.Combine(temp, name);
        File.WriteAllText(p, xml);
        return p;
    }

    static string Value(DbxFile f, string guid, string field)
    {
        var e = f.Instance(guid).Children.First(c => f.Get(c, "name") == field);
        return XmlExport.ValueText(f, e);
    }

    static Element Field(DbxFile f, string guid, string field)
    {
        return f.Instance(guid).Children.First(c => f.Get(c, "name") == field);
    }

    static void XmlIn()
    {
        var b = Sample();
        byte[] orig = b.Bytes();
        var reference = new DbxFile(orig);
        string xml = XmlExport.Convert(orig);
        XmlImport rep;
        var same = XmlImport.Import(WriteXml("same.xml", xml), reference, new DbxFile[0], out rep);
        Check(same.Serialize().SequenceEqual(orig), "unedited XML imports back byte for byte");
        Check(rep.Changed == 0 && rep.Added == 0 && rep.Removed == 0, "unedited: nothing changed, added or removed");

        string edited = xml.Replace(">7<", ">8<").Replace("\"Scale\">1.5<", "\"Scale\">2<").Replace(">true<", ">false<")
                           .Replace("Levels/Test &amp; &lt;Demo&gt;", "Renamed");
        var e = XmlImport.Import(WriteXml("edited.xml", edited), new DbxFile(orig), new DbxFile[0], out rep);
        Check(Value(e, G1, "Count") == "8" && BigEndian.U32(Field(e, G1, "Count").Payload, 2) == 8, "integer field stays an integer");
        Check(BigEndian.U32(Field(e, G1, "Scale").Payload, 2) == BitConverter.ToUInt32(BitConverter.GetBytes(2.0f), 0), "decimal field given '2' stores the decimal 2.0");
        Check(Value(e, G1, "Enabled") == "false", "bool changed");
        Check(Value(e, G1, "Name") == "Renamed", "text changed");
        Check(rep.Changed == 4 && rep.Guesses.Count == 0, "4 changed, no guesses (" + rep.Changed + ", " + rep.Guesses.Count + ")");

        var r = XmlImport.Import(WriteXml("round.xml", xml.Replace("\"Scale\">1.5<", "\"Scale\">0.123456789123<")), new DbxFile(orig), new DbxFile[0], out rep);
        Check(rep.Rounded == 1 && Value(r, G1, "Scale") == 0.123456789123f.ToString("R", CultureInfo.InvariantCulture), "too many digits: rounded to the nearest 4-byte value");

        // an instance removed and one copied under a new guid
        int start = xml.IndexOf("\t<instance guid=\"" + G3), end = xml.IndexOf("\t</instance>", start) + "\t</instance>\r\n".Length;
        string block = xml.Substring(start, end - start);
        string changedSet = xml.Remove(start, end - start).Replace("</partition>", block.Replace(G3, G4) + "</partition>");
        var m = XmlImport.Import(WriteXml("moved.xml", changedSet), new DbxFile(orig), new DbxFile[0], out rep);
        Check(m.Instance(G3) == null && m.Instance(G4) != null, "instance removed and a copy added");
        Check(rep.Removed == 1 && rep.Guesses.Count == 0, "removed 1, new instance typed from the file without guessing");
        Check(XmlExport.Convert(m.Serialize()).Replace("\r\n", "\n") == changedSet.Replace("\r\n", "\n"), "the result shows exactly the edited XML");

        // no reference: shapes decide where they are unambiguous, the rest is reported as guessed
        var blind = XmlImport.Import(WriteXml("blind.xml", xml), null, new DbxFile[0], out rep);
        Check(XmlExport.Convert(blind.Serialize()) == xml, "without a reference the XML still comes back the same");
        Check(rep.Guesses.Count > 0, "without a reference the guesses are reported (" + rep.Guesses.Count + ")");
        Check(Field(blind, G1, "Empty").Kind == Element.Text, "<x/> is empty text");
    }

    static void XmlReader()
    {
        string doc = "<?xml version=\"1.0\"?>\r\n<!-- a comment -->\r\n<partition guid='" + GP + "'>\r\n"
                     + "\t<instance guid=\"" + G1 + "\" type=\"T\">\r\n"
                     + "\t\t<field name=\"A\" name=\"B\">x</field>\r\n"
                     + "\t\t<field name=\"Esc\">a&amp;b&#65;&#x42;<![CDATA[<c>]]></field>\r\n"
                     + "\t\t<field name=\"Lines\">one\r\ntwo</field>\r\n"
                     + "\t\t<array name=\"L\">\r\n\t\t</array>\r\n"
                     + "\t\t<array name=\"N\"></array>\r\n"
                     + "\t</instance>\r\n</partition>\r\n";
        XmlImport rep;
        var f = XmlImport.Import(WriteXml("reader.xml", doc), null, new DbxFile[0], out rep);
        var i = f.Instance(G1);
        var dup = i.Children[0];
        Check(dup.Attributes.Count == 2 && f.Table[dup.Attributes[1].Value] == "B", "the same attribute twice is kept (game files have it)");
        Check(XmlExport.ValueText(f, i.Children[1]) == "a&bAB<c>", "entities, character references and CDATA");
        Check(XmlExport.ValueText(f, i.Children[2]) == "one\r\ntwo", "line breaks inside a value are kept exactly");
        Check(i.Children[3].IsContainer && i.Children[3].Children.Count == 0, "an empty multi-line element is a container");
        Check(i.Children[4].Kind == Element.Numbers, "<x></x> is an empty number list");
        bool threw = false;
        try { XmlImport.Import(WriteXml("bad.xml", "<partition><instance></partition>"), null, new DbxFile[0], out rep); }
        catch (InvalidDataException) { threw = true; }
        Check(threw, "mismatched tags are refused with a line number");
    }

    static void Commands()
    {
        var f = Sample().Reload();
        var ed = new Editor(f);
        Check(ed.Replace("Levels/Test & <Demo>", "Other") == 1 && Value(f, G1, "Name") == "Other", "replace");
        Check(ed.Replace("nothing like this", "x") == 0, "replace: no match");
        f = Sample().Reload();
        f.Intern("LEVELS/a");
        f.Intern("levels/keep/b");
        ed = new Editor(f);
        int n = ed.ReplacePrefix("levels/", "maps/", new[] { "levels/keep" });
        // a start that was not all lower case gets <new> as given; one that was gets it lower-cased
        Check(n == 2 && f.Table.Contains("maps/Test & <Demo>") && f.Table.Contains("maps/a") && f.Table.Contains("levels/keep/b"),
              "replaceprefix: matched ignoring case, exceptions skipped");
        f = Sample().Reload();
        ed = new Editor(f);
        int refs;
        Check(ed.Drop(G2, out refs) == 1 && f.Instance(G2) == null, "drop removes the instance");
        var objects = f.Instance(G1).Children.First(c => f.Get(c, "name") == "Objects");
        Check(objects.Children.Count == 1 && refs == 2, "drop removes list entries pointing to it (refs " + refs + ")");
        Check(f.Get(Field(f, G3, "Partner"), "ref") == "null", "drop turns single references into null");
        f = Sample().Reload();
        ed = new Editor(f);
        Check(ed.DropField("Scale") == 1 && f.Instance(G1).Children.All(c => f.Get(c, "name") != "Scale"), "dropfield");
        Check(ed.SetByte("Enabled", 0) == 1 && Value(f, G1, "Enabled") == "false", "set");
        Check(ed.SetField(G1, "Count", "42") && Value(f, G1, "Count") == "42", "setfield: integer");
        Check(ed.SetField(G1, "Scale".Replace("Scale", "One"), "2.5") && Value(f, G1, "One") == "2.5", "setfield: decimal");
        Check(ed.SetField(G1, "Name", "New/Name") && Value(f, G1, "Name") == "New/Name", "setfield: text");
        Check(!ed.SetField(G1, "Missing", "1"), "setfield: no such field");
        // typed like the present value (as the window and the import encode it): a decimal field set to a whole
        // number stays a decimal; exponent notation is a decimal; an integer field stays an integer
        f = Sample().Reload();
        ed = new Editor(f);
        Check(ed.SetField(G1, "Scale", "2") && Value(f, G1, "Scale") == "2"
              && BigEndian.U32(Field(f, G1, "Scale").Payload, 2) == BitConverter.ToUInt32(BitConverter.GetBytes(2f), 0),
              "setfield: a decimal field set to 2 holds the decimal 2.0, not the integer bits 00000002");
        Check(ed.SetField(G1, "One", "1E-05") && Value(f, G1, "One") == "1E-05", "setfield: 1E-05 is a decimal");
        Check(ed.SetField(G1, "Count", "3") && BigEndian.U32(Field(f, G1, "Count").Payload, 2) == 3, "setfield: an integer field stays an integer");
        Check(ed.SetField(G1, "Enabled", "false") && Value(f, G1, "Enabled") == "false", "setfield: a bool field");
        bool refused = false;
        try { ed.SetField(G1, "Count", "many"); } catch (InvalidDataException) { refused = true; }
        Check(refused, "setfield: a value the field cannot hold is refused");
        ed.SetPartitionGuid(G4);
        Check(f.Get(f.TopLevel[0], "guid") == G4, "partguid");
        f = Sample().Reload();
        ed = new Editor(f);
        Check(ed.AddItem(G1, "Objects", G4, null), "additem at the end");
        Check(ed.AddItem(G1, "Objects", G1, G3), "additem before an entry");
        var list = f.Instance(G1).Children.First(c => f.Get(c, "name") == "Objects").Children.Select(c => f.Get(c, "ref")).ToList();
        Check(list.SequenceEqual(new[] { G2, G1, G3, G4 }), "additem order: " + string.Join(",", list));
        Check(!ed.AddItem(G1, "NoSuchArray", G4, null), "additem: no such array");
    }

    static void GraftCopy()
    {
        var src = Sample().Reload();
        var b = new Builder(GP);
        b.Instance("Game.Target", G4);
        var f = b.Reload();
        var ed = new Editor(f);
        int n = ed.Graft(src, G3, G4);
        Check(n == 3, "graft copies the instance and everything it references in its file (" + n + ")");
        var order = f.Instances().Select(i => f.Get(i, "guid")).ToList();
        Check(order.SequenceEqual(new[] { G3, G2, G1, G4 }), "graft order and position: " + string.Join(",", order.Select(g => g.Substring(0, 1))));
        Check(ed.Graft(src, G3, null) == 0, "graft skips instances already present");
        Check(XmlExport.Convert(f.Serialize()).Contains("Levels/Test &amp; &lt;Demo&gt;"), "grafted strings are added to this file's table");
        f = b.Reload();
        ed = new Editor(f);
        Check(ed.CopyField(src, G1, "Objects", G4), "copyfield");
        var copied = f.Instance(G4).Children.FirstOrDefault(c => f.Get(c, "name") == "Objects");
        Check(copied != null && copied.Children.Count == 2, "copyfield brings the whole list");
        Check(!ed.CopyField(src, G1, "NoSuchField", G4), "copyfield: no such field");
    }

    static Builder LevelSample()
    {
        var b = new Builder(GP);
        var level = b.Instance("GameSharedResources.LevelData", G1, "Level");
        b.RefList(level, "Objects", G2, G3);
        var group = b.Instance("GameSharedResources.StaticModelGroupEntityData", G2);
        b.Ref(group, "PhysicsData", G3);
        var terrain = b.Instance("GameSharedResources.TerrainEntityData", G3);
        b.Ref(terrain, "PhysicsData", "null");
        return b;
    }

    static void LevelChecks()
    {
        var clean = LevelCheck.Run(LevelSample().Reload(), null, new string[0]);
        Check(clean.Count == 0, "a sound level has no findings (terrain may lack PhysicsData) - " + string.Join(" | ", clean));
        var b = LevelSample();
        var objects = b.F.Instance(G1).Children[0];
        objects.Children[0].Attributes[0].Value = b.F.Intern("null");
        var r = LevelCheck.Run(b.Reload(), null, new string[0]);
        Check(r.Any(x => x.Error && x.Message.Contains("empty entry")), "null entry in the object list is an error");
        Check(r.Any(x => !x.Error && x.Message.Contains("not in the level's object list")), "world object not in the list is a warning");
        b = LevelSample();
        b.F.Instance(G1).Children[0].Children[1].Attributes[0].Value = b.F.Intern(G4);
        r = LevelCheck.Run(b.Reload(), null, new string[0]);
        Check(r.Any(x => x.Error && x.Message.Contains("not in this file")), "unresolved entry in the object list is an error");
        b = LevelSample();
        b.F.Instance(G2).Children[0].Attributes[0].Value = b.F.Intern("null");
        r = LevelCheck.Run(b.Reload(), null, new string[0]);
        Check(r.Any(x => x.Error && x.Message.Contains("without PhysicsData")), "model group without PhysicsData is an error");
        b = LevelSample();
        b.Placed(G4, "crate", 1, 2, 3, 77);
        b.Placed("55555555-5555-5555-5555-555555555555", "barrel", 4, 5, 6, 77);
        r = LevelCheck.Run(b.Reload(), null, new string[0]);
        Check(r.Any(x => !x.Error && x.Message.Contains("shared by 2 objects")), "two objects in one cull slot is a warning");
        var other = new Builder(GP);
        other.Placed(G4, "crate", 1, 2, 3, 12);
        r = LevelCheck.Run(b.Reload(), other.Reload(), new string[0]);
        Check(r.Any(x => x.Message.Contains("another slot")), "--slots-from: a slot differing from the cull trees' level");
    }

    static void Slots()
    {
        var mine = new Builder(GP);
        mine.Placed(G1, "crate", 0, 0, 0, 900);            // same guid there
        mine.Placed(G2, "crate", 10, 0, 10, 901);          // same blueprint + position
        mine.Placed(G3, "barrel", 100, 0, 100, 902);       // nearest barrel 20 m away
        mine.Placed(G4, "statue", 0, 0, 0, 903);           // nothing like it
        var theirs = new Builder(GP);
        theirs.Placed(G1, "crate", 0, 0, 0, 1);
        theirs.Placed("66666666-6666-6666-6666-666666666666", "crate", 10, 0, 10, 2);
        theirs.Placed("77777777-7777-7777-7777-777777777777", "barrel", 120, 0, 100, 3);
        theirs.Placed("88888888-8888-8888-8888-888888888888", "barrel", 300, 0, 300, 4);
        var f = mine.Reload();
        var res = SlotFixer.Fix(f, theirs.Reload());
        var slots = PlacedObject.All(f).ToDictionary(o => o.Guid, o => o.SlotValue);
        Check(slots[G1] == 1 && slots[G2] == 2 && slots[G3] == 3 && slots[G4] == 0, "slots by guid, position, nearest within 50 m, none: "
              + string.Join(",", new[] { G1, G2, G3, G4 }.Select(g => slots[g])));
        Check(res.ByGuid == 1 && res.ByPosition == 1 && res.ByNearest == 1 && res.NoCounterpart == 1 && res.Changed == 4, "fixslots counts");
    }

    static string Exe { get { return Path.GetFullPath(Path.Combine(Here, "..", "DbxEdit.exe")); } }

    static string RunExe(string args, out int code)
    {
        var p = new Process { StartInfo = new ProcessStartInfo(Exe, args) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true } };
        p.Start();
        string o = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
        p.WaitForExit();
        code = p.ExitCode;
        return o;
    }

    static void CommandLine()
    {
        if (!File.Exists(Exe)) { Check(false, "DbxEdit.exe not built next to the tests"); return; }
        string a = Path.Combine(temp, "cli.dbx"), b = Path.Combine(temp, "cli_out.dbx");
        File.WriteAllBytes(a, Sample().Bytes());
        int code;
        string o = RunExe("edit \"" + a + "\" \"" + b + "\" setfield=" + G1 + "|Count|9 drop=" + G3, out code);
        Check(code == 0 && o.Contains("drop " + G3 + ": 1 instance(s)") && o.Contains("setfield: 1 field(s)"), "edit: messages and exit code");
        Check(Value(new DbxFile(b), G1, "Count") == "9", "edit: result written");
        o = RunExe("edit \"" + a + "\" \"" + b + "\" drop=00000000-0000-0000-0000-000000000000", out code);
        Check(code == 1 && o.Contains("no instance"), "edit: error exit code 1");
        o = RunExe("edit \"" + a + "\" \"" + b + "\" nonsense", out code);
        Check(code == 2 && o.Contains("unknown command"), "edit: unknown command exit code 2");
        o = RunExe("xml \"" + a + "\"", out code);
        Check(code == 0 && File.Exists(Path.ChangeExtension(a, ".xml")) && o.Contains("converted 1 file(s)"), "xml");
        string folder = Path.Combine(temp, "xmlfolder");
        Directory.CreateDirectory(folder);
        File.WriteAllBytes(Path.Combine(folder, "good.dbx"), Sample().Bytes());
        File.WriteAllText(Path.Combine(folder, "plain.dbx"), "<?xml version=\"1.0\"?>\r\n<partition />\r\n");
        File.WriteAllBytes(Path.Combine(folder, "broken.dbx"), Encoding.ASCII.GetBytes("{binary}\0\0\0\x7f garbage"));
        o = RunExe("xml \"" + folder + "\"", out code);
        Check(code == 1 && o.Contains("converted 1 file(s)") && o.Contains("skipped 1 not binary DBX") && o.Contains("1 FAILED")
              && o.Contains("broken.dbx") && !File.Exists(Path.Combine(folder, "plain.xml")),
              "xml: a file that fails gives exit code 1; a non-binary file is skipped, not counted (" + o.Trim() + ")");
        o = RunExe("import \"" + Path.ChangeExtension(a, ".xml") + "\" \"" + b + "\"", out code);
        Check(code == 0 && File.ReadAllBytes(b).SequenceEqual(File.ReadAllBytes(a)), "import (original next to the XML) gives the original back");
        o = RunExe("roundtrip \"" + a + "\"", out code);
        Check(code == 0 && o.Contains("1 identical"), "roundtrip");
        File.WriteAllBytes(a, LevelSample().Bytes());
        o = RunExe("check \"" + a + "\"", out code);
        Check(code == 0 && o.Contains("0 error(s)"), "check: clean level exit code 0");
    }

    static object Field(object o, string name) { return o.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(o); }
    static void Call(object o, string name, params object[] a) { o.GetType().GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(o, a); }

    static void Window()
    {
        string file = Path.Combine(temp, "gui.dbx"), saved = Path.Combine(temp, "gui_saved.dbx");
        File.WriteAllBytes(file, Sample().Bytes());
        var w = new MohDbx.Gui.MainWindow(null);
        w.Show();
        System.Windows.Forms.Application.DoEvents();
        Call(w, "Open", file);
        var tree = (System.Windows.Forms.TreeView)Field(w, "tree");
        var doc = (DbxFile)Field(w, "doc");
        Check(tree.Nodes.Count == 1 && tree.Nodes[0].Nodes.Count == 3, "window: the tree shows the instances");
        var inst = tree.Nodes[0].Nodes[0];
        inst.Expand();
        var count = inst.Nodes.Cast<System.Windows.Forms.TreeNode>().First(n => doc.Get((Element)n.Tag, "name") == "Count");
        tree.SelectedNode = count;
        ((System.Windows.Forms.TextBox)Field(w, "value")).Text = "11";
        Call(w, "ApplyEdit");
        Call(w, "Save", saved);
        var cli = Sample().Reload();
        new Editor(cli).SetField(G1, "Count", "11");
        Check(File.ReadAllBytes(saved).SequenceEqual(cli.Serialize()), "window: saving an edit gives the command line's bytes");
        Call(w, "Undo");
        Check(((DbxFile)Field(w, "doc")).Serialize().SequenceEqual(File.ReadAllBytes(file)), "window: undo restores the original");
        Check((bool)Field(w, "modified"), "window: edit, save, undo - the file differs from the saved one again (closing asks)");
        Call(w, "Save", saved);
        Check(!(bool)Field(w, "modified"), "window: saved again - unmodified");

        // the edit script replays what the window did: a decimal field set to a whole number
        var script = (List<string>)Field(w, "script");
        doc = (DbxFile)Field(w, "doc");
        inst = tree.Nodes[0].Nodes[0];
        inst.Expand();
        tree.SelectedNode = inst.Nodes.Cast<System.Windows.Forms.TreeNode>().First(n => doc.Get((Element)n.Tag, "name") == "Scale");
        ((System.Windows.Forms.TextBox)Field(w, "value")).Text = "2";
        Call(w, "ApplyEdit");
        string line = script.LastOrDefault() ?? "";
        var replay = Sample().Reload();
        string[] p = line.StartsWith("setfield=") ? line.Substring(9).Split('|') : new string[0];
        Check(p.Length == 3 && new Editor(replay).SetField(p[0], p[1], p[2])
              && replay.Serialize().SequenceEqual(((DbxFile)Field(w, "doc")).Serialize()),
              "window: the script's setfield for a decimal set to 2 replays to the same bytes (" + line + ")");
        Call(w, "Undo");

        // an edit of an attribute only (a reference) has no command form: a comment, not a setfield that drops it
        doc = (DbxFile)Field(w, "doc");
        var other = tree.Nodes[0].Nodes[1];
        other.Expand();
        tree.SelectedNode = other.Nodes.Cast<System.Windows.Forms.TreeNode>().First(n => doc.Get((Element)n.Tag, "name") == "Partner");
        var grid = (System.Windows.Forms.DataGridView)Field(w, "attributes");
        var refRow = grid.Rows.Cast<System.Windows.Forms.DataGridViewRow>().First(r => Convert.ToString(r.Cells[0].Value) == "ref");
        refRow.Cells[1].Value = G3;
        Call(w, "ApplyEdit");
        line = script.LastOrDefault() ?? "";
        Check(line.StartsWith("# edited") && line.Contains("ref=\"" + G3 + "\"") && !line.Contains("setfield="),
              "window: a changed reference is noted as a comment line (" + line + ")");
        Check(((DbxFile)Field(w, "doc")).Get(((DbxFile)Field(w, "doc")).Instance(G2).Children[0], "ref") == G3, "window: the reference changed");
        w.GetType().GetField("modified", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(w, false);
        w.Close();
    }

    // ------------------------------------------------------------------------------------------- corpus

    static List<string> CorpusFiles(List<string> roots)
    {
        var files = new List<string>();
        foreach (string r in roots)
        {
            if (File.Exists(r) && r.EndsWith(".txt", StringComparison.OrdinalIgnoreCase)) files.AddRange(File.ReadAllLines(r).Where(l => l.Length > 0));
            else if (Directory.Exists(r)) files.AddRange(Directory.GetFiles(r, "*.dbx", SearchOption.AllDirectories));
            else if (File.Exists(r)) files.Add(r);
        }
        // the same file content once (unpacked bundles share many files)
        var seen = new HashSet<string>();
        var unique = new List<string>();
        foreach (string f in files)
        {
            try { if (seen.Add(Sha1(File.ReadAllBytes(f)))) unique.Add(f); } catch (IOException) { }
        }
        return unique;
    }

    static void Corpus(List<string> roots, int editEvery)
    {
        var files = CorpusFiles(roots);
        int binary = 0, rt = 0, imp = 0, edits = 0, editsOk = 0, blindBad = 0, shown = 0;
        string xmlPath = Path.Combine(temp, "corpus.xml");
        var rnd = new Random(7);
        foreach (string f in files)
        {
            byte[] data;
            try { data = File.ReadAllBytes(f); } catch (IOException) { continue; }
            DbxFile d;
            try { d = new DbxFile(data); } catch (Exception) { continue; }
            if (!d.IsBinary) continue;
            binary++;
            Action<string> fail = what => { if (shown++ < 15) Console.WriteLine("  FAIL  " + what + ": " + f); };
            if (d.Serialize().SequenceEqual(data)) rt++; else fail("round trip");
            string xml = XmlExport.Convert(data);
            File.WriteAllText(xmlPath, xml);
            XmlImport rep;
            try
            {
                if (XmlImport.Import(xmlPath, new DbxFile(data), new DbxFile[0], out rep).Serialize().SequenceEqual(data)) imp++; else fail("import identity");
                // read back from its bytes: a file only counts if the game could read it
                var blind = new DbxFile(XmlImport.Import(xmlPath, null, new DbxFile[0], out rep).Serialize());
                if (rep.Guesses.Count == 0 && !Same(blind, d)) { blindBad++; fail("import without a reference differs without a reported guess"); }
            }
            catch (Exception e) { fail("import threw " + e.Message); }
            if (binary % editEvery == 0)
            {
                edits++;
                string edited = Mutate(xml, rnd);
                File.WriteAllText(xmlPath, edited);
                try
                {
                    var back = XmlImport.Import(xmlPath, new DbxFile(data), new DbxFile[0], out rep);
                    if (XmlExport.Convert(back.Serialize()).Replace("\r\n", "\n") == edited.Replace("\r\n", "\n") && TypesKept(d, back)) editsOk++;
                    else fail("edited import");
                }
                catch (Exception e) { fail("edited import threw " + e.Message); }
            }
        }
        Console.WriteLine("  " + binary + " binary files: round trip " + rt + ", import identity " + imp + ", edited imports " + editsOk + "/" + edits);
        Check(binary > 0, "corpus has binary .dbx files");
        Check(rt == binary, "every file round-trips byte for byte");
        Check(imp == binary, "every file comes back through xml + import");
        Check(editsOk == edits, "edited imports carry exactly the edits, types kept");
        Check(blindBad == 0, "imports without a reference differ only where a guess was reported");
    }

    static bool Same(DbxFile a, DbxFile b)
    {
        var x = new StringBuilder(); var y = new StringBuilder();
        foreach (var e in a.TopLevel) Describe(a, e, x);
        foreach (var e in b.TopLevel) Describe(b, e, y);
        return x.ToString() == y.ToString();
    }

    static void Describe(DbxFile f, Element e, StringBuilder b)
    {
        b.Append(f.TagOf(e)).Append('|').Append(e.Kind);
        foreach (var a in e.Attributes) b.Append('|').Append(f.Table[a.Name]).Append('=').Append(f.Table[a.Value]);
        if (e.IsContainer) { b.Append('{').Append(e.ContainerByte); foreach (var c in e.Children) { b.Append(';'); Describe(f, c, b); } b.Append('}'); }
        else if (e.Kind == Element.Text) { int p = 0; b.Append(":t:").Append(f.Table[Varint.Read(e.Payload, ref p)]); }
        else b.Append(':').Append(BitConverter.ToString(e.Payload));
    }

    // edits like a person would make: numbers halved (decimals), text suffixed, bools flipped, an instance removed
    // and another copied under a new guid
    static string Mutate(string xml, Random rnd)
    {
        var lines = xml.Replace("\r\n", "\n").Split('\n').ToList();
        var inv = CultureInfo.InvariantCulture;
        for (int i = 0; i < lines.Count; i++)
        {
            string l = lines[i];
            if (rnd.NextDouble() > 0.15) continue;
            int gt = l.IndexOf('>'), lt = l.LastIndexOf("</", StringComparison.Ordinal);
            if (gt < 0 || lt <= gt + 1 || l.TrimStart().StartsWith("</")) continue;
            string value = l.Substring(gt + 1, lt - gt - 1), nv;
            double dummy;
            if (value == "true") nv = "false";
            else if (value == "false") nv = "true";
            else if (value.Split('/').All(t => t.Length > 0 && (char.IsDigit(t[0]) || t[0] == '-') && !t.Contains("E")))
                nv = string.Join("/", value.Split('/').Select(t => t.Contains(".") ? (float.Parse(t, inv) * 0.5f).ToString("R", inv) : t));
            else if (value.Split('/').All(t => double.TryParse(t, NumberStyles.Float, inv, out dummy))) continue;
            else if (!value.Contains("/")) nv = value + "_x";
            else continue;
            lines[i] = l.Substring(0, gt + 1) + nv + l.Substring(lt);
        }
        var starts = Enumerable.Range(0, lines.Count).Where(i => lines[i].StartsWith("\t<instance ")).ToList();
        if (starts.Count >= 3)
        {
            int s = starts[rnd.Next(starts.Count)], e = lines.FindIndex(s, l => l == "\t</instance>");
            if (e > s) lines.RemoveRange(s, e - s + 1);
            starts = Enumerable.Range(0, lines.Count).Where(i => lines[i].StartsWith("\t<instance ")).ToList();
            int c = starts[rnd.Next(starts.Count)], ce = lines.FindIndex(c, l => l == "\t</instance>");
            if (ce > c)
            {
                var copy = lines.GetRange(c, ce - c + 1).ToList();
                copy[0] = Regex.Replace(copy[0], "guid=\"[^\"]+\"", "guid=\"0BADC0DE-0000-4000-8000-" + rnd.Next(100000000, 999999999).ToString("D12") + "\"");
                lines.InsertRange(ce + 1, copy);
            }
        }
        return string.Join("\r\n", lines);
    }

    static bool TypesKept(DbxFile a, DbxFile b)
    {
        var kinds = new Dictionary<string, string>();
        bool ok = true;
        Action<DbxFile, Element, string, bool> walk = null;
        walk = (f, e, p, check) =>
        {
            string kind = e.Kind + (e.Kind == Element.Numbers ? "s" + e.Payload[1] : "");
            if (check) { string k; if (kinds.TryGetValue(p, out k) && k != kind) ok = false; }
            else kinds[p] = kind;
            if (!e.IsContainer) return;
            var seen = new Dictionary<string, int>();
            foreach (var c in e.Children)
            {
                string guid = f.Get(c, "guid");
                string key = guid != null ? "i:" + guid : f.TagOf(c) + "[" + (f.Get(c, "name") ?? "") + "]";
                int n; seen.TryGetValue(key, out n); seen[key] = n + 1;
                walk(f, c, p + "/" + key + (guid != null ? "" : "#" + n), check);
            }
        };
        foreach (var e in a.TopLevel) walk(a, e, "", false);
        foreach (var e in b.TopLevel) walk(b, e, "", true);
        return ok;
    }

    // ------------------------------------------------------------------------------------------- fingerprints

    static string Sha1(byte[] b)
    {
        using (var h = SHA1.Create()) return BitConverter.ToString(h.ComputeHash(b)).Replace("-", "").ToLowerInvariant();
    }

    // Command batteries for one level file, from its own content (deterministic).
    static List<string> Battery(DbxFile f)
    {
        var cmds = new List<string>();
        var instances = f.Instances().ToList();
        var guids = new HashSet<string>(instances.Select(i => f.Get(i, "guid") ?? ""), StringComparer.OrdinalIgnoreCase);
        var referenced = new List<string>();
        Action<Element> walk = null;
        walk = e => { string r = f.Get(e, "ref"); if (r != null && guids.Contains(r) && !referenced.Contains(r)) referenced.Add(r); if (e.IsContainer) foreach (var c in e.Children) walk(c); };
        foreach (var e in f.TopLevel) walk(e);
        if (referenced.Count > 0) cmds.Add("drop=" + referenced[referenced.Count / 2]);
        string name = instances.SelectMany(i => i.Children).Where(c => f.Get(c, "name") == "Name" && c.Kind == Element.Text).Select(c => XmlExport.ValueText(f, c)).FirstOrDefault(v => v.Length > 0 && v.IndexOf('|') < 0);
        if (name != null) { cmds.Add("replace=" + name + "|" + name + "_X"); cmds.Add("replaceprefix=" + name.Split('/')[0] + "|OPFOR_TEST|" + name.Split('/')[0] + "/zz"); }
        cmds.Add("dropfield=Enumeration");
        cmds.Add("partguid=11111111-2222-3333-4444-555555555555");
        var b = instances.SelectMany(i => i.Children).FirstOrDefault(c => c.Kind == 6 && f.Get(c, "name") != null);
        if (b != null) cmds.Add("set=" + f.Get(b, "name") + "|true");
        foreach (var i in instances)
        {
            var n4 = i.Children.FirstOrDefault(c => c.Kind == Element.Numbers && c.Payload.Length == 6 && f.Get(c, "name") != null);
            if (n4 != null) { cmds.Add("setfield=" + f.Get(i, "guid") + "|" + f.Get(n4, "name") + "|42"); break; }
        }
        foreach (var i in instances)
        {
            var arr = i.Children.FirstOrDefault(c => f.TagOf(c) == "array" && c.IsContainer && c.Children.Count > 0 && f.TagOf(c.Children[0]) == "item" && f.Get(c, "name") != null);
            if (arr != null) { cmds.Add("additem=" + f.Get(i, "guid") + "|" + f.Get(arr, "name") + "|aaaaaaaa-0000-0000-0000-000000000000"); break; }
        }
        return cmds;
    }

    static bool IsLevelPartition(string path)
    {
        return Regex.IsMatch(path, @"[\\/]levels[\\/][^\\/]+\.dbx$", RegexOptions.IgnoreCase) && new FileInfo(path).Length > 20000;
    }

    static void MakeGolden(List<string> roots, string path)
    {
        if (!File.Exists(Exe)) { Check(false, "DbxEdit.exe not built"); return; }
        var lines = new List<string> { "# DbxEdit fingerprints: <test> <input sha1> <output sha1> [commands] - made " + DateTime.Now.ToString("yyyy-MM-dd") };
        var seen = new HashSet<string>();
        int levels = 0;
        foreach (string f in CorpusFiles(roots))
        {
            byte[] data = File.ReadAllBytes(f);
            var d = new DbxFile(data);
            if (!d.IsBinary) continue;
            string h = Sha1(data);
            if (!seen.Add(h)) continue;
            lines.Add("xml " + h + " " + Sha1(new UTF8Encoding(false).GetBytes(XmlExport.Convert(data))));
            if (IsLevelPartition(f) && levels < 20)
            {
                levels++;
                foreach (string c in Battery(d))
                {
                    int code;
                    string outPath = Path.Combine(temp, "g.dbx");
                    if (File.Exists(outPath)) File.Delete(outPath);
                    string o = RunExe("edit \"" + f + "\" \"" + outPath + "\" " + Quote(c), out code);
                    string result = File.Exists(outPath) ? Sha1(File.ReadAllBytes(outPath)) : "none";
                    lines.Add("edit " + h + " " + result + " " + code + " " + Sha1(Encoding.UTF8.GetBytes(o)) + " " + c);
                }
            }
        }
        File.WriteAllLines(path, lines);
        Console.WriteLine("  wrote " + (lines.Count - 1) + " fingerprints to " + path);
        Check(lines.Count > 1, "fingerprints recorded");
    }

    static string Quote(string c) { return "\"" + c + "\""; }

    static void Golden(List<string> roots, string path)
    {
        var byHash = new Dictionary<string, string>();
        foreach (string f in CorpusFiles(roots))
        {
            try { string h = Sha1(File.ReadAllBytes(f)); if (!byHash.ContainsKey(h)) byHash[h] = f; } catch (IOException) { }
        }
        int checkedN = 0, missing = 0, bad = 0;
        foreach (string line in File.ReadAllLines(path))
        {
            if (line.StartsWith("#") || line.Trim().Length == 0) continue;
            var p = line.Split(new[] { ' ' }, 6);
            string file;
            if (!byHash.TryGetValue(p[1], out file)) { missing++; continue; }
            checkedN++;
            bool ok;
            if (p[0] == "xml") ok = Sha1(new UTF8Encoding(false).GetBytes(XmlExport.Convert(File.ReadAllBytes(file)))) == p[2];
            else
            {
                int code;
                string outPath = Path.Combine(temp, "g.dbx");
                if (File.Exists(outPath)) File.Delete(outPath);
                string o = RunExe("edit \"" + file + "\" \"" + outPath + "\" " + Quote(p[5]), out code);
                string result = File.Exists(outPath) ? Sha1(File.ReadAllBytes(outPath)) : "none";
                ok = result == p[2] && code.ToString() == p[3] && Sha1(Encoding.UTF8.GetBytes(o)) == p[4];
            }
            if (!ok) { bad++; if (bad <= 10) Console.WriteLine("  FAIL  fingerprint differs: " + line.Substring(0, Math.Min(140, line.Length)) + "  (" + file + ")"); }
        }
        Console.WriteLine("  fingerprints: " + checkedN + " checked, " + bad + " different, " + missing + " without their input file here");
        Check(checkedN > 0, "some fingerprints could be checked");
        Check(bad == 0, "every fingerprint matches");
    }
}
