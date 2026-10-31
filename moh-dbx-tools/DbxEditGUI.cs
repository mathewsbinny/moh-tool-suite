// DbxEditGUI - a window for DbxEdit: browse a binary .dbx file as a tree, edit values and attributes, run DbxEdit's
// commands (drop, graft, copy field, add item, replace, ...), check a level, fix cull slots, export and import XML.
// Built together with DbxEdit.cs (build.bat); everything it changes goes through DbxEdit's own code.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace MohDbx.Gui
{
    static class GuiProgram
    {
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

        [STAThread]
        static void Main(string[] args)
        {
            ToolLog.Start("dbxedit-gui");
            // anything not caught where it happens: logged and explained in a window, the window keeps running
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += (s, e) => Crash(e.Exception, false);
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Crash(e.ExceptionObject as Exception, true);
            try { SetProcessDPIAware(); } catch (Exception) { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainWindow(args.Length > 0 ? args[0] : null));
        }

        static void Crash(Exception e, bool fatal)
        {
            if (e == null) return;
            ToolLog.Error(e, fatal ? "DbxEditGUI (closing)" : "DbxEditGUI");
            MessageBox.Show(ToolLog.Report(e), fatal ? "DbxEditGUI - error, closing" : "DbxEditGUI - error",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    sealed class MainWindow : Form
    {
        DbxFile doc;
        string path;
        bool modified, backedUp;
        readonly Stack<byte[]> undo = new Stack<byte[]>();
        int savedDepth;            // undo.Count when the file was opened or last saved (-1: that state can no longer be undone to)
        readonly List<string> script = new List<string>();   // the edits as DbxEdit commands

        readonly TreeView tree = new TreeView();
        readonly Label heading = new Label();
        readonly DataGridView attributes = new DataGridView();
        readonly TextBox value = new TextBox();
        readonly Button apply = new Button(), goTo = new Button();
        readonly TextBox output = new TextBox();
        readonly ToolStripStatusLabel status = new ToolStripStatusLabel();
        readonly ToolStripMenuItem instanceMenu = new ToolStripMenuItem("&Instance");
        List<Element> found = new List<Element>();
        int foundAt = -1;
        string lastSearch = "";

        public MainWindow(string open)
        {
            Text = "DbxEdit";
            Font = new Font("Segoe UI", 9f);
            Size = new Size(1200, 800);
            StartPosition = FormStartPosition.CenterScreen;
            AllowDrop = true;
            DragEnter += (s, e) => { if (e.Data.GetDataPresent(DataFormats.FileDrop)) e.Effect = DragDropEffects.Copy; };
            DragDrop += (s, e) => { var files = (string[])e.Data.GetData(DataFormats.FileDrop); if (files.Length > 0 && ConfirmDiscard()) Open(files[0]); };
            BuildMenu();
            BuildLayout();
            FormClosing += (s, e) => { if (!ConfirmDiscard()) e.Cancel = true; };
            UpdateState();
            if (open != null) Shown += (s, e) => Open(open);
        }

        // ---------------------------------------------------------------------------------------------- layout

        void BuildMenu()
        {
            var menu = new MenuStrip();
            var file = new ToolStripMenuItem("&File");
            file.DropDownItems.Add(Item("&Open...", Keys.Control | Keys.O, () => { if (ConfirmDiscard()) OpenDialog(); }));
            file.DropDownItems.Add(Item("&Save", Keys.Control | Keys.S, () => Save(path)));
            file.DropDownItems.Add(Item("Save &as...", Keys.None, SaveAs));
            file.DropDownItems.Add(new ToolStripSeparator());
            file.DropDownItems.Add(Item("&Export XML...", Keys.Control | Keys.E, ExportXml));
            file.DropDownItems.Add(Item("&Import XML...", Keys.Control | Keys.I, ImportXml));
            file.DropDownItems.Add(Item("Save edit &script...", Keys.None, SaveScript));
            file.DropDownItems.Add(new ToolStripSeparator());
            file.DropDownItems.Add(Item("E&xit", Keys.None, Close));
            var edit = new ToolStripMenuItem("&Edit");
            edit.DropDownItems.Add(Item("&Undo", Keys.Control | Keys.Z, Undo));
            edit.DropDownItems.Add(new ToolStripSeparator());
            edit.DropDownItems.Add(Item("&Find...", Keys.Control | Keys.F, Find));
            edit.DropDownItems.Add(Item("Find &next", Keys.F3, FindNext));
            edit.DropDownItems.Add(new ToolStripSeparator());
            edit.DropDownItems.Add(Item("&Replace string...", Keys.Control | Keys.H, ReplaceString));
            edit.DropDownItems.Add(Item("Replace string &prefix...", Keys.None, ReplacePrefix));
            edit.DropDownItems.Add(Item("Remove &field everywhere...", Keys.None, DropField));
            edit.DropDownItems.Add(Item("Set &bool / byte field everywhere...", Keys.None, SetByteField));
            edit.DropDownItems.Add(Item("Partition &GUID...", Keys.None, PartitionGuid));
            instanceMenu.DropDownItems.Add(Item("&Remove instance", Keys.Delete, DropInstance));
            instanceMenu.DropDownItems.Add(Item("&Graft instance from another file...", Keys.Control | Keys.G, Graft));
            instanceMenu.DropDownItems.Add(Item("&Copy field from another file...", Keys.None, CopyField));
            instanceMenu.DropDownItems.Add(Item("&Add array item...", Keys.Control | Keys.Insert, AddItem));
            var level = new ToolStripMenuItem("&Level");
            level.DropDownItems.Add(Item("&Check level", Keys.F5, () => CheckLevel(false)));
            level.DropDownItems.Add(Item("Check level against &cull-tree level...", Keys.None, () => CheckLevel(true)));
            level.DropDownItems.Add(Item("&Fix cull slots...", Keys.None, FixSlots));
            var help = new ToolStripMenuItem("&Help");
            help.DropDownItems.Add(Item("&Format and commands (FORMAT.md)", Keys.F1, () => OpenBeside("FORMAT.md")));
            help.DropDownItems.Add(Item("&README", Keys.None, () => OpenBeside("README.md")));
            menu.Items.AddRange(new ToolStripItem[] { file, edit, instanceMenu, level, help });
            MainMenuStrip = menu;
            Controls.Add(menu);
        }

        static ToolStripMenuItem Item(string text, Keys keys, Action action)
        {
            var i = new ToolStripMenuItem(text);
            if (keys != Keys.None) i.ShortcutKeys = keys;
            i.Click += (s, e) => action();
            return i;
        }

        void BuildLayout()
        {
            var statusStrip = new StatusStrip();
            status.Spring = true;
            status.TextAlign = ContentAlignment.MiddleLeft;
            statusStrip.Items.Add(status);

            var outer = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal };
            var split = new SplitContainer { Dock = DockStyle.Fill };
            Load += (s, e) =>
            {
                outer.SplitterDistance = (int)(outer.Height * 0.74);
                split.SplitterDistance = (int)(split.Width * 0.56);
            };
            tree.Dock = DockStyle.Fill;
            tree.HideSelection = false;
            tree.BeforeExpand += (s, e) => Fill(e.Node);
            tree.AfterSelect += (s, e) => ShowElement(e.Node);
            tree.NodeMouseClick += (s, e) => { if (e.Button == MouseButtons.Right) tree.SelectedNode = e.Node; };
            var context = new ContextMenuStrip();
            context.Items.Add("Remove instance", null, (s, e) => DropInstance());
            context.Items.Add("Graft instance from another file...", null, (s, e) => Graft());
            context.Items.Add("Copy field from another file...", null, (s, e) => CopyField());
            context.Items.Add("Add array item...", null, (s, e) => AddItem());
            context.Items.Add("Copy GUID", null, (s, e) => { var g = GuidOf(tree.SelectedNode); if (g != null) Clipboard.SetText(g); });
            tree.ContextMenuStrip = context;
            split.Panel1.Controls.Add(tree);

            var right = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 5, Padding = new Padding(6) };
            right.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            right.RowStyles.Add(new RowStyle(SizeType.Percent, 45));
            right.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            right.RowStyles.Add(new RowStyle(SizeType.Percent, 55));
            right.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            heading.AutoSize = true;
            heading.Font = new Font(Font, FontStyle.Bold);
            heading.Margin = new Padding(0, 0, 0, 6);
            attributes.Dock = DockStyle.Fill;
            attributes.AllowUserToAddRows = false;
            attributes.AllowUserToDeleteRows = false;
            attributes.RowHeadersVisible = false;
            attributes.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
            attributes.BackgroundColor = SystemColors.Window;
            attributes.Columns.Add("Name", "Attribute");
            attributes.Columns.Add("Value", "Value");
            attributes.Columns[0].ReadOnly = true;
            attributes.Columns[0].FillWeight = 30;
            var valueLabel = new Label { Text = "Value (numbers separated by /):", AutoSize = true, Margin = new Padding(0, 8, 0, 2) };
            value.Dock = DockStyle.Fill;
            value.Multiline = true;
            value.ScrollBars = ScrollBars.Vertical;
            value.Font = new Font("Consolas", 9.5f);
            var buttons = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill };
            apply.Text = "&Apply";
            apply.AutoSize = true;
            apply.Click += (s, e) => ApplyEdit();
            goTo.Text = "&Go to reference";
            goTo.AutoSize = true;
            goTo.Click += (s, e) => GoToReference();
            buttons.Controls.Add(apply);
            buttons.Controls.Add(goTo);
            right.Controls.Add(heading, 0, 0);
            right.Controls.Add(attributes, 0, 1);
            right.Controls.Add(valueLabel, 0, 2);
            right.Controls.Add(value, 0, 3);
            right.Controls.Add(buttons, 0, 4);
            split.Panel2.Controls.Add(right);

            output.Dock = DockStyle.Fill;
            output.Multiline = true;
            output.ReadOnly = true;
            output.ScrollBars = ScrollBars.Both;
            output.WordWrap = false;
            output.Font = new Font("Consolas", 9f);
            output.BackColor = SystemColors.Window;
            output.DoubleClick += (s, e) => GoToGuidOnLine();
            outer.Panel1.Controls.Add(split);
            outer.Panel2.Controls.Add(output);
            Controls.Add(outer);
            Controls.Add(statusStrip);
            outer.BringToFront();
        }

        void Log(string line)
        {
            output.AppendText(line + Environment.NewLine);
        }

        void UpdateState()
        {
            Text = (path == null ? "DbxEdit" : Path.GetFileName(path) + (modified ? " *" : "") + " - DbxEdit");
            status.Text = doc == null ? "Open a binary .dbx file (File > Open, or drop it on the window)."
                : doc.Instances().Count() + " instances, " + doc.Table.Count + " strings" + (modified ? " - modified" : "") + "   " + path;
            bool open = doc != null;
            foreach (ToolStripItem i in MainMenuStrip.Items) if (i != MainMenuStrip.Items[0] && i != MainMenuStrip.Items[MainMenuStrip.Items.Count - 1]) i.Enabled = open;
        }

        // ---------------------------------------------------------------------------------------------- tree

        void Rebuild(Element select)
        {
            var keep = select == null ? IndexPath(tree.SelectedNode) : null;
            tree.BeginUpdate();
            tree.Nodes.Clear();
            foreach (var e in doc.TopLevel) tree.Nodes.Add(MakeNode(e));
            if (tree.Nodes.Count > 0) tree.Nodes[0].Expand();
            tree.EndUpdate();
            if (select != null) Select(select);
            else if (keep != null) SelectIndexPath(keep);
            UpdateState();
        }

        static List<int> IndexPath(TreeNode n)
        {
            if (n == null) return null;
            var p = new List<int>();
            for (; n != null; n = n.Parent) p.Insert(0, n.Index);
            return p;
        }

        void SelectIndexPath(List<int> p)
        {
            TreeNodeCollection level = tree.Nodes;
            TreeNode n = null;
            foreach (int i in p)
            {
                if (n != null) { Fill(n); n.Expand(); level = n.Nodes; }
                if (i >= level.Count) break;
                n = level[i];
            }
            if (n == null) return;
            tree.SelectedNode = n;
            n.EnsureVisible();
        }

        TreeNode MakeNode(Element e)
        {
            var n = new TreeNode(Label(e)) { Tag = e };
            if (e.IsContainer && e.Children.Count > 0) n.Nodes.Add(new TreeNode("..."));
            if (doc.TagOf(e) == "instance") n.NodeFont = new Font(tree.Font, FontStyle.Bold);
            return n;
        }

        void Fill(TreeNode n)
        {
            // children are created on first expansion; until then a single "..." placeholder (no Tag) stands in
            var e = n.Tag as Element;
            bool placeholder = n.Nodes.Count == 1 && n.Nodes[0].Tag == null;
            if (e == null || !e.IsContainer || !placeholder) return;
            tree.BeginUpdate();
            n.Nodes.Clear();
            foreach (var c in e.Children) n.Nodes.Add(MakeNode(c));
            tree.EndUpdate();
        }

        string Label(Element e)
        {
            string tag = doc.TagOf(e);
            if (tag == "partition") return "partition " + (doc.Get(e, "guid") ?? "");
            if (tag == "instance")
            {
                string type = doc.Get(e, "type") ?? "";
                int dot = type.LastIndexOf('.');
                string id = doc.Get(e, "id");
                return (dot >= 0 ? type.Substring(dot + 1) : type) + "   " + (id ?? doc.Get(e, "guid"));
            }
            string name = doc.Get(e, "name"), r = doc.Get(e, "ref");
            string head = name ?? tag;
            if (r != null) return head + " -> " + r;
            if (e.IsContainer) return head + "  [" + e.Children.Count + "]";
            string v = XmlExport.ValueText(doc, e);
            if (v.Length > 90) v = v.Substring(0, 90) + "...";
            return head + " = " + v.Replace("\r", " ").Replace("\n", " ");
        }

        static List<Element> PathTo(Element root, Element target)
        {
            if (root == target) return new List<Element> { root };
            if (!root.IsContainer) return null;
            foreach (var c in root.Children)
            {
                var p = PathTo(c, target);
                if (p != null) { p.Insert(0, root); return p; }
            }
            return null;
        }

        void Select(Element target)
        {
            foreach (TreeNode top in tree.Nodes)
            {
                var p = PathTo((Element)top.Tag, target);
                if (p == null) continue;
                TreeNode n = top;
                for (int i = 1; i < p.Count; i++)
                {
                    Fill(n);
                    n.Expand();
                    n = n.Nodes.Cast<TreeNode>().FirstOrDefault(x => x.Tag == p[i]);
                    if (n == null) return;
                }
                tree.SelectedNode = n;
                n.EnsureVisible();
                return;
            }
        }

        Element Selected { get { return tree.SelectedNode == null ? null : tree.SelectedNode.Tag as Element; } }

        Element InstanceOf(TreeNode n)
        {
            for (; n != null; n = n.Parent)
            {
                var e = n.Tag as Element;
                if (e != null && doc.TagOf(e) == "instance") return e;
            }
            return null;
        }

        string GuidOf(TreeNode n)
        {
            var i = InstanceOf(n);
            return i == null ? null : doc.Get(i, "guid");
        }

        void ShowElement(TreeNode n)
        {
            var e = n == null ? null : n.Tag as Element;
            attributes.Rows.Clear();
            value.Text = "";
            if (e == null) { heading.Text = ""; return; }
            string kind = e.IsContainer ? "container" : e.Kind == Element.Text ? "text" : e.Kind == Element.Numbers ? NumbersKind(e) : "bool / byte";
            heading.Text = doc.TagOf(e) + "  (" + kind + ")";
            foreach (var a in e.Attributes) attributes.Rows.Add(doc.Table[a.Name], doc.Table[a.Value]);
            value.Enabled = !e.IsContainer;
            value.Text = e.IsContainer ? "" : XmlExport.ValueText(doc, e);
            string r = doc.Get(e, "ref");
            goTo.Enabled = r != null && r != "null";
        }

        static string NumbersKind(Element e)
        {
            int p = 0;
            int n = Varint.Read(e.Payload, ref p), size = Varint.Read(e.Payload, ref p);
            return n + " number" + (n == 1 ? "" : "s") + " of " + size + " bytes";
        }

        // ---------------------------------------------------------------------------------------------- editing

        void Snapshot()
        {
            if (undo.Count < savedDepth) savedDepth = -1;     // an edit after undoing past the save: the saved state is gone
            undo.Push(doc.Serialize());
            modified = true;
        }

        // An edit that changed nothing after all: its snapshot goes again.
        void DropSnapshot()
        {
            undo.Pop();
            modified = undo.Count != savedDepth;
        }

        void Undo()
        {
            if (undo.Count == 0) { Log("Nothing to undo."); return; }
            doc = new DbxFile(undo.Pop());
            if (script.Count > 0) script.RemoveAt(script.Count - 1);
            modified = undo.Count != savedDepth;
            Rebuild(null);
            Log("Undone.");
        }

        void ApplyEdit()
        {
            var e = Selected;
            if (e == null) return;
            var before = doc.Serialize();
            try
            {
                bool valueChanged = false;
                string nameBefore = doc.Get(e, "name");
                var attributeChanges = new List<string>();
                for (int i = 0; i < e.Attributes.Count && i < attributes.Rows.Count; i++)
                {
                    string v = Convert.ToString(attributes.Rows[i].Cells[1].Value) ?? "";
                    if (v != doc.Table[e.Attributes[i].Value])
                    {
                        e.Attributes[i].Value = doc.Intern(v);
                        attributeChanges.Add(doc.Table[e.Attributes[i].Name] + "=\"" + v + "\"");
                    }
                }
                if (!e.IsContainer && value.Text != XmlExport.ValueText(doc, e))
                {
                    bool rounded;
                    e.Payload = XmlImport.EncodeValue(doc, e, value.Text, out rounded);
                    valueChanged = true;
                    if (rounded) Log("Rounded to the nearest 4-byte value: " + XmlExport.ValueText(doc, e));
                }
                if (!valueChanged && attributeChanges.Count == 0) return;
                if (undo.Count < savedDepth) savedDepth = -1;
                undo.Push(before);
                modified = true;
                var inst = InstanceOf(tree.SelectedNode);
                string where = inst == null ? "partition" : doc.Get(inst, "guid");
                // one script entry per edit (Undo takes back one): setfield replays a value change of the instance's
                // field that setfield finds (the first non-container child of that name); attribute changes (a
                // reference, a name) have no command form and are noted as a comment
                var lines = new List<string>();
                if (attributeChanges.Count > 0)
                    lines.Add("# edited " + Label(e) + " in " + where + ": " + string.Join(", ", attributeChanges) + " (no command form)");
                if (valueChanged)
                {
                    string name = doc.Get(e, "name");
                    bool replayable = inst != null && name != null && name == nameBefore
                                      && inst.Children.FirstOrDefault(c => doc.Get(c, "name") == name && !c.IsContainer && c.Payload != null) == e;
                    lines.Add(replayable ? "setfield=" + doc.Get(inst, "guid") + "|" + name + "|" + XmlExport.ValueText(doc, e)
                                         : "# edited the value of " + Label(e) + " in " + where + " (no command form)");
                }
                script.Add(string.Join(Environment.NewLine, lines));
                tree.SelectedNode.Text = Label(e);
                ShowElement(tree.SelectedNode);
                UpdateState();
            }
            catch (Exception ex)
            {
                doc = new DbxFile(before);
                Rebuild(null);
                ToolLog.Warn("value not accepted: " + ex.Message);
                MessageBox.Show(this, ex.Message, "Value not accepted", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        void GoToReference()
        {
            var e = Selected;
            string r = e == null ? null : doc.Get(e, "ref");
            if (r == null) return;
            var target = doc.Instance(r);
            if (target != null) { Select(target); return; }
            Log("Reference " + r + (r.IndexOf('/') > 0 ? " points into another file." : " is not in this file."));
        }

        void DropInstance()
        {
            var inst = InstanceOf(tree.SelectedNode);
            if (inst == null) return;
            string guid = doc.Get(inst, "guid");
            if (MessageBox.Show(this, "Remove instance " + Label(inst) + "?\n\nList entries pointing to it are removed, other references to it become null.",
                                "Remove instance", MessageBoxButtons.OKCancel, MessageBoxIcon.Question) != DialogResult.OK) return;
            Snapshot();
            int refs;
            int n = new Editor(doc).Drop(guid, out refs);
            script.Add("drop=" + guid);
            Log("drop " + guid + ": " + n + " instance(s), " + refs + " reference(s)");
            Rebuild(null);
        }

        DbxFile PickFile(string title, out string file)
        {
            file = null;
            using (var d = new OpenFileDialog { Title = title, Filter = "Binary DBX (*.dbx)|*.dbx|All files|*.*" })
            {
                if (path != null) d.InitialDirectory = Path.GetDirectoryName(path);
                if (d.ShowDialog(this) != DialogResult.OK) return null;
                var f = new DbxFile(d.FileName);
                if (!f.IsBinary) { MessageBox.Show(this, "Not a binary .dbx file.", title); return null; }
                file = d.FileName;
                return f;
            }
        }

        void Graft()
        {
            string srcPath;
            var src = PickFile("Graft from which file?", out srcPath);
            if (src == null) return;
            var choice = Chooser.Pick(this, "Instance to copy from " + Path.GetFileName(srcPath),
                src.Instances().Select(i => new KeyValuePair<string, string>(src.Get(i, "guid"), InstanceText(src, i))).ToList());
            if (choice == null) return;
            var before = InstanceOf(tree.SelectedNode);
            string beforeGuid = before == null ? null : doc.Get(before, "guid");
            Snapshot();
            int n = new Editor(doc).Graft(src, choice, beforeGuid);
            script.Add("graft=" + srcPath + "|" + choice + (beforeGuid != null ? "|" + beforeGuid : ""));
            Log("graft " + choice + " from " + Path.GetFileName(srcPath) + ": " + n + " instance(s)" + (beforeGuid != null ? " (inserted before the selected instance)" : ""));
            Rebuild(doc.Instance(choice));
        }

        static string InstanceText(DbxFile f, Element i)
        {
            string type = f.Get(i, "type") ?? "";
            int dot = type.LastIndexOf('.');
            return (dot >= 0 ? type.Substring(dot + 1) : type) + "   " + (f.Get(i, "id") ?? "") + "   " + f.Get(i, "guid");
        }

        void CopyField()
        {
            var target = InstanceOf(tree.SelectedNode);
            if (target == null) { Log("Select the instance to copy the field into."); return; }
            string srcPath;
            var src = PickFile("Copy a field from which file?", out srcPath);
            if (src == null) return;
            var srcGuid = Chooser.Pick(this, "Copy from which instance?",
                src.Instances().Select(i => new KeyValuePair<string, string>(src.Get(i, "guid"), InstanceText(src, i))).ToList());
            if (srcGuid == null) return;
            var fields = src.Instance(srcGuid).Children.Select(c => src.Get(c, "name")).Where(x => x != null).Distinct()
                            .Select(x => new KeyValuePair<string, string>(x, x)).ToList();
            var field = Chooser.Pick(this, "Which field?", fields);
            if (field == null) return;
            Snapshot();
            string guid = doc.Get(target, "guid");
            bool ok = new Editor(doc).CopyField(src, srcGuid, field, guid);
            if (!ok) { DropSnapshot(); Log("copyfield " + field + ": NOT FOUND"); return; }
            script.Add("copyfield=" + srcPath + "|" + srcGuid + "|" + field + "|" + guid);
            Log("copyfield " + field + ": copied into " + guid);
            Rebuild(target);
        }

        void AddItem()
        {
            var e = Selected;
            var inst = InstanceOf(tree.SelectedNode);
            if (e == null || inst == null || doc.TagOf(e) != "array" || !inst.Children.Contains(e))
            {
                Log("Select an array field of an instance (a direct field, e.g. Objects).");
                return;
            }
            string r = Prompt.Ask(this, "Add array item", "Reference of the new item (an instance GUID, or partition/GUID):", "");
            if (string.IsNullOrEmpty(r)) return;
            Snapshot();
            string guid = doc.Get(inst, "guid"), array = doc.Get(e, "name");
            if (!new Editor(doc).AddItem(guid, array, r, null)) { DropSnapshot(); Log("additem: the array has no item to copy the shape from"); return; }
            script.Add("additem=" + guid + "|" + array + "|" + r);
            Log("additem " + r + " -> " + array);
            Rebuild(e);
        }

        void ReplaceString()
        {
            string from = Prompt.Ask(this, "Replace string", "Every string exactly equal to:", "");
            if (string.IsNullOrEmpty(from)) return;
            string to = Prompt.Ask(this, "Replace string", "becomes:", from);
            if (to == null) return;
            Snapshot();
            int n = new Editor(doc).Replace(from, to);
            if (n == 0) { DropSnapshot(); Log("replace: not found: " + from); return; }
            script.Add("replace=" + from + "|" + to);
            Log("replace " + from + " -> " + to + ": " + n + " string(s)");
            Rebuild(Selected);
        }

        void ReplacePrefix()
        {
            string from = Prompt.Ask(this, "Replace prefix", "Strings starting with (case is ignored):", "");
            if (string.IsNullOrEmpty(from)) return;
            string to = Prompt.Ask(this, "Replace prefix", "get this start instead:", from);
            if (to == null) return;
            string except = Prompt.Ask(this, "Replace prefix", "Except strings starting with (separate several with |; may be empty):", "");
            if (except == null) return;
            Snapshot();
            var list = except.Split('|').Where(x => x.Length > 0).ToList();
            int n = new Editor(doc).ReplacePrefix(from, to, list);
            script.Add("replaceprefix=" + from + "|" + to + (list.Count > 0 ? "|" + string.Join("|", list) : ""));
            Log("replaceprefix " + from + " -> " + to + ": " + n + " string(s)");
            Rebuild(Selected);
        }

        void DropField()
        {
            string name = Prompt.Ask(this, "Remove field everywhere", "Remove every field, array or complex named:", Selected == null ? "" : doc.Get(Selected, "name") ?? "");
            if (string.IsNullOrEmpty(name)) return;
            Snapshot();
            int n = new Editor(doc).DropField(name);
            script.Add("dropfield=" + name);
            Log("dropfield " + name + ": " + n + " element(s)");
            Rebuild(null);
        }

        void SetByteField()
        {
            string name = Prompt.Ask(this, "Set bool / byte field everywhere", "Field name:", Selected == null ? "" : doc.Get(Selected, "name") ?? "");
            if (string.IsNullOrEmpty(name)) return;
            string v = Prompt.Ask(this, "Set bool / byte field everywhere", "Value (true, false or 0-255):", "true");
            if (v == null) return;
            byte b;
            if (v == "true") b = 1; else if (v == "false") b = 0; else if (!byte.TryParse(v, out b)) { Log("set: not true, false or 0-255"); return; }
            Snapshot();
            int n = new Editor(doc).SetByte(name, b);
            if (n == 0) { DropSnapshot(); Log("set: no bool/byte field " + name); return; }
            script.Add("set=" + name + "|" + v);
            Log("set " + name + " = " + v + ": " + n + " field(s)");
            Rebuild(Selected);
        }

        void PartitionGuid()
        {
            string current = doc.Get(doc.TopLevel[0], "guid") ?? "";
            string g = Prompt.Ask(this, "Partition GUID", "The file's own GUID (a copy under a new name needs a new one):", current);
            if (string.IsNullOrEmpty(g) || g == current) return;
            Snapshot();
            new Editor(doc).SetPartitionGuid(g);
            script.Add("partguid=" + g);
            Log("partguid " + g);
            Rebuild(null);
        }

        // ---------------------------------------------------------------------------------------------- level tools

        void CheckLevel(bool withCullLevel)
        {
            DbxFile slotsFrom = null;
            string slotsPath = null;
            if (withCullLevel)
            {
                slotsFrom = PickFile("Level whose cull trees this file uses", out slotsPath);
                if (slotsFrom == null) return;
            }
            var results = LevelCheck.Run(doc, slotsFrom, new string[0]);
            Log("");
            Log("=== check " + Path.GetFileName(path) + (slotsPath != null ? " (cull slots against " + Path.GetFileName(slotsPath) + ")" : ""));
            foreach (var x in results.OrderBy(x => x.Error ? 0 : 1)) Log(x.ToString());
            int errors = results.Count(x => x.Error);
            Log(errors + " error(s), " + (results.Count - errors) + " warning(s)" + (results.Count > 0 ? " - double-click a line to go to its instance" : ""));
        }

        void FixSlots()
        {
            string fromPath;
            var from = PickFile("Level whose cull trees this file uses (the slots are taken from it)", out fromPath);
            if (from == null) return;
            Snapshot();
            var r = SlotFixer.Fix(doc, from);
            if (r.Changed == 0) DropSnapshot();
            else script.Add("# fixslots --from " + fromPath + " (DbxEdit fixslots)");
            Log("fixslots: " + r.Changed + " slot(s) changed; by guid " + r.ByGuid + ", by blueprint and position " + r.ByPosition
                + ", nearest within 50 m " + r.ByNearest + ", no counterpart (slot 0) " + r.NoCounterpart);
            foreach (string u in r.Unmatched.Take(30)) Log("  no counterpart: " + u);
            Rebuild(Selected);
        }

        void GoToGuidOnLine()
        {
            int line = output.GetLineFromCharIndex(output.SelectionStart);
            if (line < 0 || line >= output.Lines.Length) return;
            var m = System.Text.RegularExpressions.Regex.Match(output.Lines[line], "[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}");
            if (!m.Success || doc == null) return;
            var i = doc.Instance(m.Value);
            if (i != null) Select(i);
        }

        // ---------------------------------------------------------------------------------------------- search

        void Find()
        {
            if (doc == null) return;
            string s = Prompt.Ask(this, "Find", "Text, GUID, type or value to find (case is ignored):", lastSearch);
            if (string.IsNullOrEmpty(s)) return;
            lastSearch = s;
            found = new List<Element>();
            foreach (var e in doc.TopLevel) Search(e, s, found);
            foundAt = -1;
            Log("find '" + s + "': " + found.Count + " match(es)" + (found.Count > 0 ? " - F3 for the next" : ""));
            FindNext();
        }

        void Search(Element e, string s, List<Element> into)
        {
            bool hit = doc.TagOf(e).IndexOf(s, StringComparison.OrdinalIgnoreCase) >= 0
                       || e.Attributes.Any(a => doc.Table[a.Value].IndexOf(s, StringComparison.OrdinalIgnoreCase) >= 0)
                       || (!e.IsContainer && XmlExport.ValueText(doc, e).IndexOf(s, StringComparison.OrdinalIgnoreCase) >= 0);
            if (hit) into.Add(e);
            if (e.IsContainer) foreach (var c in e.Children) Search(c, s, into);
        }

        void FindNext()
        {
            if (found.Count == 0) return;
            foundAt = (foundAt + 1) % found.Count;
            Select(found[foundAt]);
            status.Text = "match " + (foundAt + 1) + " of " + found.Count + " for '" + lastSearch + "'";
        }

        // ---------------------------------------------------------------------------------------------- files

        void OpenDialog()
        {
            using (var d = new OpenFileDialog { Filter = "Binary DBX (*.dbx)|*.dbx|All files|*.*" })
                if (d.ShowDialog(this) == DialogResult.OK) Open(d.FileName);
        }

        void Open(string file)
        {
            try
            {
                var f = new DbxFile(file);
                if (!f.IsBinary) { MessageBox.Show(this, file + "\n\nis not a binary .dbx file (it may be plain XML already).", "Open"); return; }
                doc = f;
                path = file;
                modified = backedUp = false;
                undo.Clear();
                savedDepth = 0;
                script.Clear();
                found.Clear();
                Rebuild(null);
                Log("opened " + file);
            }
            catch (Exception ex)
            {
                ToolLog.Error(ex, "opening " + file);
                MessageBox.Show(this, ToolLog.Report(ex), "Cannot open " + Path.GetFileName(file), MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        void Save(string target)
        {
            if (doc == null) return;
            if (target == null) { SaveAs(); return; }
            try
            {
                if (target == path && !backedUp && File.Exists(path) && !File.Exists(path + ".bak"))
                {
                    File.Copy(path, path + ".bak");
                    Log("original kept as " + Path.GetFileName(path) + ".bak");
                }
                backedUp = true;
                File.WriteAllBytes(target, doc.Serialize());
                path = target;
                savedDepth = undo.Count;
                modified = false;
                Log("saved " + target);
                UpdateState();
            }
            catch (Exception ex)
            {
                ToolLog.Error(ex, "saving");
                MessageBox.Show(this, ToolLog.Report(ex), "Cannot save", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        void SaveAs()
        {
            if (doc == null) return;
            using (var d = new SaveFileDialog { Filter = "Binary DBX (*.dbx)|*.dbx", FileName = path == null ? "" : Path.GetFileName(path) })
            {
                if (path != null) d.InitialDirectory = Path.GetDirectoryName(path);
                if (d.ShowDialog(this) == DialogResult.OK) { backedUp = true; Save(d.FileName); }
            }
        }

        void ExportXml()
        {
            if (doc == null) return;
            using (var d = new SaveFileDialog { Filter = "XML (*.xml)|*.xml", FileName = Path.GetFileNameWithoutExtension(path) + ".xml" })
            {
                d.InitialDirectory = Path.GetDirectoryName(path);
                if (d.ShowDialog(this) != DialogResult.OK) return;
                File.WriteAllText(d.FileName, XmlExport.Convert(doc.Serialize()));
                Log("exported " + d.FileName);
            }
        }

        void ImportXml()
        {
            if (doc == null) return;
            using (var d = new OpenFileDialog { Filter = "XML (*.xml)|*.xml", Title = "Import XML (the open file gives the types)" })
            {
                d.InitialDirectory = Path.GetDirectoryName(path);
                if (d.ShowDialog(this) != DialogResult.OK) return;
                try
                {
                    XmlImport report;
                    var result = XmlImport.Import(d.FileName, new DbxFile(doc.Serialize()), new DbxFile[0], out report);
                    Snapshot();
                    doc = new DbxFile(result.Serialize());
                    script.Add("# imported " + d.FileName);
                    Log("import " + d.FileName + ": " + report.Kept + " unchanged, " + report.Changed + " changed, " + report.Added + " new, " + report.Removed + " removed"
                        + (report.Rounded > 0 ? "; " + report.Rounded + " decimal(s) rounded" : ""));
                    foreach (string g in report.Guesses.Take(50)) Log("  guess: " + g);
                    if (report.Guesses.Count > 0) Log(report.Guesses.Count + " value(s) typed by guess - check them");
                    Rebuild(null);
                }
                catch (Exception ex)
                {
                    ToolLog.Error(ex, "XML import");
                    MessageBox.Show(this, ToolLog.Report(ex), "Import failed", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }
        }

        void SaveScript()
        {
            if (script.Count == 0) { Log("No edits to save as a script yet."); return; }
            using (var d = new SaveFileDialog { Filter = "DbxEdit commands (*.cmd)|*.cmd|Text (*.txt)|*.txt", FileName = Path.GetFileNameWithoutExtension(path) + "_edits.cmd" })
            {
                d.InitialDirectory = Path.GetDirectoryName(path);
                if (d.ShowDialog(this) != DialogResult.OK) return;
                var lines = new List<string> { "# DbxEdit commands for " + Path.GetFileName(path) + " - replay with: DbxEdit edit <in.dbx> <out.dbx> @" + Path.GetFileName(d.FileName) };
                lines.AddRange(script);
                File.WriteAllLines(d.FileName, lines);
                Log("saved " + script.Count + " edit(s) to " + d.FileName);
            }
        }

        bool ConfirmDiscard()
        {
            if (doc == null || !modified) return true;
            var r = MessageBox.Show(this, "Save the changes to " + Path.GetFileName(path) + "?", "DbxEdit", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question);
            if (r == DialogResult.Cancel) return false;
            if (r == DialogResult.Yes) Save(path);
            return !modified;
        }

        static void OpenBeside(string name)
        {
            string p = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, name);
            if (File.Exists(p)) System.Diagnostics.Process.Start(p);
        }
    }

    // A one-line question.
    static class Prompt
    {
        public static string Ask(IWin32Window owner, string title, string question, string initial)
        {
            using (var f = new Form { Text = title, FormBorderStyle = FormBorderStyle.FixedDialog, MinimizeBox = false, MaximizeBox = false,
                                      StartPosition = FormStartPosition.CenterParent, ClientSize = new Size(520, 120), Font = new Font("Segoe UI", 9f) })
            {
                var label = new Label { Text = question, Left = 12, Top = 12, Width = 496, AutoSize = false, Height = 20 };
                var box = new TextBox { Text = initial, Left = 12, Top = 36, Width = 496 };
                var ok = new Button { Text = "OK", DialogResult = DialogResult.OK, Left = 336, Top = 76, Width = 80 };
                var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, Left = 428, Top = 76, Width = 80 };
                f.Controls.AddRange(new Control[] { label, box, ok, cancel });
                f.AcceptButton = ok;
                f.CancelButton = cancel;
                return f.ShowDialog(owner) == DialogResult.OK ? box.Text : null;
            }
        }
    }

    // Pick one entry of a long list, with a filter box. Returns the chosen key.
    static class Chooser
    {
        public static string Pick(IWin32Window owner, string title, List<KeyValuePair<string, string>> entries)
        {
            using (var f = new Form { Text = title, StartPosition = FormStartPosition.CenterParent, ClientSize = new Size(720, 520), Font = new Font("Segoe UI", 9f), MinimizeBox = false })
            {
                var filter = new TextBox { Dock = DockStyle.Top };
                var list = new ListBox { Dock = DockStyle.Fill, IntegralHeight = false };
                var bottom = new FlowLayoutPanel { Dock = DockStyle.Bottom, FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Padding = new Padding(6) };
                var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, AutoSize = true };
                var ok = new Button { Text = "OK", DialogResult = DialogResult.OK, AutoSize = true };
                bottom.Controls.Add(cancel);
                bottom.Controls.Add(ok);
                var hint = new Label { Text = "Type to filter:", Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(2, 4, 0, 2) };
                Action refill = () =>
                {
                    list.BeginUpdate();
                    list.Items.Clear();
                    foreach (var e in entries.Where(x => filter.Text.Length == 0 || x.Value.IndexOf(filter.Text, StringComparison.OrdinalIgnoreCase) >= 0).Take(5000))
                        list.Items.Add(new Entry(e));
                    if (list.Items.Count > 0) list.SelectedIndex = 0;
                    list.EndUpdate();
                };
                filter.TextChanged += (s, e) => refill();
                list.DoubleClick += (s, e) => { if (list.SelectedItem != null) { f.DialogResult = DialogResult.OK; f.Close(); } };
                f.Controls.Add(list);
                f.Controls.Add(filter);
                f.Controls.Add(hint);
                f.Controls.Add(bottom);
                f.AcceptButton = ok;
                f.CancelButton = cancel;
                refill();
                filter.Select();
                if (f.ShowDialog(owner) != DialogResult.OK || list.SelectedItem == null) return null;
                return ((Entry)list.SelectedItem).Key;
            }
        }

        sealed class Entry
        {
            public readonly string Key, Text;
            public Entry(KeyValuePair<string, string> kv) { Key = kv.Key; Text = kv.Value; }
            public override string ToString() { return Text; }
        }
    }
}
