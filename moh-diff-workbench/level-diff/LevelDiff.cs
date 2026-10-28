// LevelDiff - compares two Battlefield: Bad Company 2 binary .dbx files (levels, or any partition) instance by
// instance: what was added, removed or changed, field by field. Reads files with the toolkit's DbxEdit code
// (..\..\moh-dbx-tools\DbxEdit.cs in the suite, compiled in by build.bat).
//
// usage: LevelDiff <a.dbx> <b.dbx> [options]
//   --match-objects     also pair placed objects (ReferenceObjectData) whose GUIDs differ by blueprint + position
//                       (builds of one map often re-create them: PC vs console vs Linux server)
//   --type <text>       only instances whose type contains <text> (e.g. --type ReferenceObjectData)
//   --ignore <field>    leave this field out (repeatable; e.g. --ignore Enumeration)
//   --summary           counts only
//   --all               list every instance (default: the first 40 per section)
//   --json <file>       also write the full result as JSON
// exit code: 0 = no differences, 1 = differences, 2 = bad input
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using MohDbx;

static class LevelDiff
{
    sealed class Options
    {
        public bool MatchObjects, Summary, All;
        public string Type, Json;
        public readonly HashSet<string> Ignore = new HashSet<string>();
    }

    sealed class Change { public string Path, A, B; }

    sealed class InstanceDiff
    {
        public string Guid, GuidB, Type, Id;
        public readonly List<Change> Changes = new List<Change>();
        public readonly List<string> Added = new List<string>(), Removed = new List<string>();   // list entries
    }

    static int Main(string[] args)
    {
        ToolLog.Start("leveldiff");
        try { return MainCore(args); }
        catch (Exception e) { return ToolLog.Fail(e); }       // unexpected: logged and explained, exit code 1
    }

    static int MainCore(string[] args)
    {
        var o = new Options();
        var files = new List<string>();
        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--match-objects": o.MatchObjects = true; break;
                case "--summary": o.Summary = true; break;
                case "--all": o.All = true; break;
                case "--type": o.Type = Next(args, ref i); break;
                case "--ignore": o.Ignore.Add(Next(args, ref i)); break;
                case "--json": o.Json = Next(args, ref i); break;
                default: files.Add(args[i]); break;
            }
        }
        if (files.Count != 2 || files.Any(f => f == null))
        {
            Console.Error.WriteLine("usage: LevelDiff <a.dbx> <b.dbx> [--match-objects] [--type <text>] [--ignore <field>] [--summary] [--all] [--json <file>]");
            return 2;
        }
        DbxFile a, b;
        try { a = new DbxFile(files[0]); b = new DbxFile(files[1]); }
        catch (Exception e)
        {
            ToolLog.Error(e, "reading the files");
            string[] ce = ToolLog.Explain(e);
            Console.Error.WriteLine(e.Message);
            Console.Error.WriteLine("  what to do: " + ce[1] + "  (details: " + (ToolLog.LogPath ?? "no log") + ")");
            return 2;
        }
        if (!a.IsBinary || !b.IsBinary) { Console.Error.WriteLine("both files must be binary .dbx"); return 2; }
        return Run(a, b, files[0], files[1], o);
    }

    static string Next(string[] args, ref int i) { return i + 1 < args.Length ? args[++i] : null; }

    static string TypeOf(DbxFile f, Element i)
    {
        string t = f.Get(i, "type") ?? "";
        int dot = t.LastIndexOf('.');
        return dot >= 0 ? t.Substring(dot + 1) : t;
    }

    static int Run(DbxFile a, DbxFile b, string nameA, string nameB, Options o)
    {
        Func<DbxFile, List<Element>> pick = f => f.Instances().Where(i => o.Type == null || (f.Get(i, "type") ?? "").IndexOf(o.Type, StringComparison.OrdinalIgnoreCase) >= 0).ToList();
        var ia = pick(a);
        var ib = pick(b);
        var byGuidB = new Dictionary<string, Element>(StringComparer.OrdinalIgnoreCase);
        foreach (var i in ib) byGuidB[b.Get(i, "guid") ?? ""] = i;
        var pairs = new List<KeyValuePair<Element, Element>>();
        var onlyA = new List<Element>();
        var matchedB = new HashSet<Element>();
        foreach (var i in ia)
        {
            Element j;
            if (byGuidB.TryGetValue(a.Get(i, "guid") ?? "", out j)) { pairs.Add(new KeyValuePair<Element, Element>(i, j)); matchedB.Add(j); }
            else onlyA.Add(i);
        }
        var onlyB = ib.Where(j => !matchedB.Contains(j)).ToList();
        int pairedByPosition = 0;
        if (o.MatchObjects)
        {
            var posB = new Dictionary<string, Queue<Element>>();
            var objB = PlacedObject.All(b).ToDictionary(x => x.Instance, x => x);
            foreach (var j in onlyB)
            {
                PlacedObject po;
                if (!objB.TryGetValue(j, out po)) continue;
                Queue<Element> q;
                if (!posB.TryGetValue(po.PositionKey, out q)) posB[po.PositionKey] = q = new Queue<Element>();
                q.Enqueue(j);
            }
            var objA = PlacedObject.All(a).ToDictionary(x => x.Instance, x => x);
            foreach (var i in onlyA.ToList())
            {
                PlacedObject po;
                Queue<Element> q;
                if (!objA.TryGetValue(i, out po) || !posB.TryGetValue(po.PositionKey, out q) || q.Count == 0) continue;
                var j = q.Dequeue();
                pairs.Add(new KeyValuePair<Element, Element>(i, j));
                onlyA.Remove(i);
                onlyB.Remove(j);
                pairedByPosition++;
            }
        }

        var diffs = new List<InstanceDiff>();
        foreach (var p in pairs)
        {
            var d = Compare(a, p.Key, b, p.Value, o);
            if (d.Changes.Count + d.Added.Count + d.Removed.Count > 0) diffs.Add(d);
        }

        var outText = new StringBuilder();
        Action<string> say = s => { Console.WriteLine(s); };
        say("A: " + nameA + "  (" + ia.Count + " instances)");
        say("B: " + nameB + "  (" + ib.Count + " instances)");
        say("same instance in both: " + pairs.Count + (pairedByPosition > 0 ? " (" + pairedByPosition + " placed objects paired by blueprint + position)" : "")
            + "; changed: " + diffs.Count + "; only in A: " + onlyA.Count + "; only in B: " + onlyB.Count);
        Section("only in A (removed in B)", onlyA, a, o, say);
        Section("only in B (added in B)", onlyB, b, o, say);
        if (!o.Summary && diffs.Count > 0)
        {
            say("");
            say("== changed (" + diffs.Count + ")");
            foreach (var d in o.All ? diffs : diffs.Take(40))
            {
                say(d.Type + "  " + (d.Id ?? "") + "  " + d.Guid + (d.GuidB != null && !string.Equals(d.GuidB, d.Guid, StringComparison.OrdinalIgnoreCase) ? " = " + d.GuidB + " in B" : ""));
                foreach (var c in d.Changes.Take(o.All ? int.MaxValue : 25)) say("    " + c.Path + ": " + Clip(c.A) + "  ->  " + Clip(c.B));
                if (!o.All && d.Changes.Count > 25) say("    ... " + (d.Changes.Count - 25) + " more field changes");
                foreach (var r in d.Removed.Take(o.All ? int.MaxValue : 15)) say("    - " + r);
                foreach (var r in d.Added.Take(o.All ? int.MaxValue : 15)) say("    + " + r);
                if (!o.All && (d.Removed.Count > 15 || d.Added.Count > 15)) say("    ... list entries: " + d.Removed.Count + " removed, " + d.Added.Count + " added in all");
            }
            if (!o.All && diffs.Count > 40) say("... " + (diffs.Count - 40) + " more changed instances (--all lists them)");
        }
        if (o.Json != null) File.WriteAllText(o.Json, Json(a, b, nameA, nameB, onlyA, onlyB, diffs, pairs.Count));
        return onlyA.Count + onlyB.Count + diffs.Count == 0 ? 0 : 1;
    }

    static string Clip(string s)
    {
        s = (s ?? "(none)").Replace("\r", " ").Replace("\n", " ");
        return s.Length > 120 ? s.Substring(0, 120) + "..." : s;
    }

    static void Section(string title, List<Element> list, DbxFile f, Options o, Action<string> say)
    {
        if (list.Count == 0) return;
        say("");
        say("== " + title + ": " + list.Count + "  (" + string.Join(", ", list.GroupBy(i => TypeOf(f, i)).OrderByDescending(g => g.Count()).Take(8).Select(g => g.Key + " " + g.Count())) + ")");
        if (o.Summary) return;
        foreach (var i in o.All ? list : list.Take(40)) say("    " + TypeOf(f, i) + "  " + (f.Get(i, "id") ?? "") + "  " + f.Get(i, "guid") + Placement(f, i));
        if (!o.All && list.Count > 40) say("    ... " + (list.Count - 40) + " more (--all lists them)");
    }

    static string Placement(DbxFile f, Element i)
    {
        var po = PlacedObject.All(f).FirstOrDefault(x => x.Instance == i);
        return po == null ? "" : "  " + po.Blueprint + " at " + Math.Round(po.X, 1) + ", " + Math.Round(po.Y, 1) + ", " + Math.Round(po.Z, 1);
    }

    // Fields of one instance as path -> value; lists of references as sets of entries.
    static void Flatten(DbxFile f, Element e, string path, Options o, Dictionary<string, string> values, Dictionary<string, List<string>> lists)
    {
        var seen = new Dictionary<string, int>();
        foreach (var c in e.Children)
        {
            string name = f.Get(c, "name");
            if (name != null && o.Ignore.Contains(name)) continue;
            string tag = f.TagOf(c), key = name ?? tag;
            int n;
            seen.TryGetValue(key, out n);
            seen[key] = n + 1;
            string p = path.Length == 0 ? key : path + "." + key;
            if (n > 0 || (name == null && tag != "item")) p += "[" + n + "]";
            if (c.IsContainer)
            {
                bool refList = c.Children.Count > 0 && c.Children.All(x => f.TagOf(x) == "item" && f.Get(x, "ref") != null);
                if (refList)
                {
                    List<string> l;
                    if (!lists.TryGetValue(p, out l)) lists[p] = l = new List<string>();
                    l.AddRange(c.Children.Select(x => f.Get(x, "ref")));
                }
                else
                {
                    values[p] = "(" + c.Children.Count + " entries)";
                    Flatten(f, c, p, o, values, lists);
                }
                continue;
            }
            string r = f.Get(c, "ref");
            values[p] = r != null ? "-> " + r : XmlExport.ValueText(f, c);
        }
    }

    static InstanceDiff Compare(DbxFile a, Element ia, DbxFile b, Element ib, Options o)
    {
        var d = new InstanceDiff { Guid = a.Get(ia, "guid"), GuidB = b.Get(ib, "guid"), Type = TypeOf(a, ia), Id = a.Get(ia, "id") };
        if (TypeOf(b, ib) != d.Type) d.Changes.Add(new Change { Path = "(type)", A = a.Get(ia, "type"), B = b.Get(ib, "type") });
        if (a.Get(ia, "id") != b.Get(ib, "id")) d.Changes.Add(new Change { Path = "(id)", A = a.Get(ia, "id"), B = b.Get(ib, "id") });
        var va = new Dictionary<string, string>();
        var vb = new Dictionary<string, string>();
        var la = new Dictionary<string, List<string>>();
        var lb = new Dictionary<string, List<string>>();
        Flatten(a, ia, "", o, va, la);
        Flatten(b, ib, "", o, vb, lb);
        foreach (var k in va.Keys.Union(vb.Keys))
        {
            string x, y;
            va.TryGetValue(k, out x);
            vb.TryGetValue(k, out y);
            if (x != y) d.Changes.Add(new Change { Path = k, A = x, B = y });
        }
        foreach (var k in la.Keys.Union(lb.Keys))
        {
            List<string> x, y;
            if (!la.TryGetValue(k, out x)) x = new List<string>();
            if (!lb.TryGetValue(k, out y)) y = new List<string>();
            var cx = Counts(x);
            var cy = Counts(y);
            foreach (var kv in cx) for (int n = (cy.ContainsKey(kv.Key) ? cy[kv.Key] : 0); n < kv.Value; n++) d.Removed.Add(k + ": " + kv.Key);
            foreach (var kv in cy) for (int n = (cx.ContainsKey(kv.Key) ? cx[kv.Key] : 0); n < kv.Value; n++) d.Added.Add(k + ": " + kv.Key);
            if (d.Removed.Count == 0 && d.Added.Count == 0 && !x.SequenceEqual(y, StringComparer.OrdinalIgnoreCase))
                d.Changes.Add(new Change { Path = k, A = "same " + x.Count + " entries", B = "in another order" });
        }
        return d;
    }

    static Dictionary<string, int> Counts(List<string> l)
    {
        var c = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var s in l) { int n; c.TryGetValue(s, out n); c[s] = n + 1; }
        return c;
    }

    static string Js(string s)
    {
        if (s == null) return "null";
        var b = new StringBuilder("\"");
        foreach (char ch in s)
        {
            if (ch == '"' || ch == '\\') b.Append('\\').Append(ch);
            else if (ch < 32) b.Append("\\u").Append(((int)ch).ToString("x4"));
            else b.Append(ch);
        }
        return b.Append('"').ToString();
    }

    static string Json(DbxFile a, DbxFile b, string nameA, string nameB, List<Element> onlyA, List<Element> onlyB, List<InstanceDiff> diffs, int same)
    {
        var s = new StringBuilder("{\n");
        s.Append(" \"a\": ").Append(Js(nameA)).Append(",\n \"b\": ").Append(Js(nameB)).Append(",\n \"same_instances\": ").Append(same).Append(",\n");
        Func<DbxFile, List<Element>, string> list = (f, l) => "[" + string.Join(",", l.Select(i => "\n  {\"type\": " + Js(TypeOf(f, i)) + ", \"id\": " + Js(f.Get(i, "id")) + ", \"guid\": " + Js(f.Get(i, "guid")) + "}")) + "]";
        s.Append(" \"only_in_a\": ").Append(list(a, onlyA)).Append(",\n \"only_in_b\": ").Append(list(b, onlyB)).Append(",\n \"changed\": [");
        s.Append(string.Join(",", diffs.Select(d =>
            "\n  {\"type\": " + Js(d.Type) + ", \"id\": " + Js(d.Id) + ", \"guid\": " + Js(d.Guid) + ", \"guid_b\": " + Js(d.GuidB)
            + ", \"fields\": [" + string.Join(", ", d.Changes.Select(c => "{\"path\": " + Js(c.Path) + ", \"a\": " + Js(c.A) + ", \"b\": " + Js(c.B) + "}")) + "]"
            + ", \"removed\": [" + string.Join(", ", d.Removed.Select(Js)) + "], \"added\": [" + string.Join(", ", d.Added.Select(Js)) + "]}")));
        s.Append("]\n}\n");
        return s.ToString();
    }
}
