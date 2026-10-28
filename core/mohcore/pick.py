"""A small level picker for the suite's 3D tools, shown when they are started without a level (a double-click,
or `python studio.py` alone): the levels of Project OPFOR's game copy, double-click or OK to open one.

    lv = pick_level('MOH Navigation Studio')      # the level name, or None when cancelled
"""
import os
import sys


def levels():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'moh-auto-navkit'))
    import navkit
    pr = navkit.Project()
    out = []
    for lv in pr.level_dirs():
        prepared = os.path.isdir(os.path.join(pr.work, 'levels', lv, 'dist'))
        out.append((lv, ('prepared' if prepared else 'not prepared yet - about 20 s the first time') +
                    (', DICE path data' if pr.dice_pathdata(lv) else '')))
    return out


def pick_level(title):
    import tkinter as tk
    from tkinter import messagebox, ttk
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # sharp on scaled displays
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.title(title)
    root.minsize(460, 420)
    try:
        ttk.Style().theme_use('vista')
    except tk.TclError:
        pass
    try:
        items = levels()
    except Exception as e:
        messagebox.showerror(title, f'No game copy found: {e}\nPut the suite next to the "Project OPFOR" folder, '
                                    f'or set OPFOR_ROOT.')
        root.destroy()
        return None
    chosen = []
    ttk.Label(root, text='Choose a level to open:', padding=(10, 10, 10, 4)).pack(anchor='w')
    tree = ttk.Treeview(root, columns=('state',), selectmode='browse', height=16)
    tree.heading('#0', text='Level')
    tree.heading('state', text='')
    tree.column('#0', width=140)
    tree.column('state', width=300)
    for lv, state in items:
        tree.insert('', 'end', iid=lv, text=lv, values=(state,))
    tree.pack(fill='both', expand=True, padx=10)

    def ok(*_):
        sel = tree.selection()
        if sel:
            chosen.append(sel[0])
            root.destroy()
    tree.bind('<Double-1>', ok)
    tree.bind('<Return>', ok)
    b = ttk.Frame(root, padding=10)
    b.pack(fill='x')
    ttk.Button(b, text='Open', command=ok).pack(side='right')
    ttk.Button(b, text='Cancel', command=root.destroy).pack(side='right', padx=6)
    root.mainloop()
    return chosen[0] if chosen else None
