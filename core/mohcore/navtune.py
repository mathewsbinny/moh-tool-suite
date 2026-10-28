"""Navigation side files: `<level>.navtune.json` (DESIGN.md section 5.1) - the edits made to a level's AI
navigation data, recorded by position so they still apply after the level is regenerated.

    t = NavTune.new('Levels/SP_01', base_path, kind='navkit')     # or load(path)
    t.add(op('unwalkable', region=box(...)))                       # by / when filled in
    t.undo(); t.redo()
    t.save(path)

Bases: 'navkit' (NavKit's output; every operation allowed) or 'dice' (the level's own path data; only additive
operations: links, route edges, traversal annotations, notes - DESIGN.md section 5.1, decided 2026-10-16).
Each operation records who made it (`by`: the PC's name unless set) and when (`when`, local time).
"""
import copy
import datetime
import hashlib
import json
import os
import socket

FORMAT, VERSION = 'moh-navtune', 1
KINDS = ('navkit', 'dice')
TRAVERSALS = ('ladder', 'jump', 'vault', 'drop')
# operation -> required fields (besides op, by, when)
OPS = {
    'walkable': ('region',),
    'unwalkable': ('region',),
    'seed': ('at',),
    'unseed': ('at',),
    'link-areas': ('a', 'b'),
    'route-edge': ('a', 'b', 'action'),
    'traversal': ('kind', 'bottom', 'top'),
    'note': ('at', 'text'),
}
REGION_OPS = ('walkable', 'unwalkable', 'seed', 'unseed')          # need a rebuild: NavKit bases only


def author():
    """The name recorded with edits: the PC's name (Windows' computer name)."""
    return os.environ.get('COMPUTERNAME') or socket.gethostname() or 'unknown'


def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def polygon(points, y_low, y_high):
    return {'polygon': [[round(float(x), 3), round(float(z), 3)] for x, z in points], 'y': [round(float(y_low), 3), round(float(y_high), 3)]}


def box(x0, y0, z0, x1, y1, z1):
    lo = [min(x0, x1), min(y0, y1), min(z0, z1)]
    hi = [max(x0, x1), max(y0, y1), max(z0, z1)]
    return {'box': [round(float(v), 3) for v in lo + hi]}


def point(p):
    return [round(float(v), 3) for v in p]


def op(name, by=None, when=None, **fields):
    """An operation with its fields; positions may be numpy arrays (stored rounded to millimetres)."""
    o = {'op': name}
    for k, v in fields.items():
        if k in ('at', 'a', 'b', 'bottom', 'top'):
            v = point(v)
        o[k] = v
    o['by'] = by or author()
    o['when'] = when or now()
    return o


def validate(o, kind='navkit'):
    """Problems with one operation (empty list: fine)."""
    problems = []
    name = o.get('op')
    if name not in OPS:
        return [f'unknown operation {name!r}']
    for k in OPS[name]:
        if k not in o:
            problems.append(f'{name}: {k} missing')
    if kind == 'dice' and name in REGION_OPS:
        problems.append(f'{name}: not allowed on a DICE base (region edits need a NavKit base)')
    for k in ('at', 'a', 'b', 'bottom', 'top'):
        if k in o and not (isinstance(o[k], list) and len(o[k]) == 3 and all(isinstance(v, (int, float)) for v in o[k])):
            problems.append(f'{name}: {k} must be [x, y, z]')
    if 'region' in o:
        r = o['region']
        if 'polygon' in r:
            if len(r['polygon']) < 3 or any(len(p) != 2 for p in r['polygon']):
                problems.append(f'{name}: a polygon needs 3 or more [x, z] points')
            if len(r.get('y', [])) != 2 or r['y'][0] > r['y'][1]:
                problems.append(f'{name}: y must be [low, high]')
        elif 'box' in r:
            b = r['box']
            if len(b) != 6 or any(b[k] > b[k + 3] for k in range(3)):
                problems.append(f'{name}: box must be [x0, y0, z0, x1, y1, z1] with each low <= high')
        else:
            problems.append(f'{name}: region must be a polygon or a box')
    if 'two-way' in o and not isinstance(o['two-way'], bool):
        problems.append(f'{name}: two-way must be true or false')
    if name == 'route-edge' and o.get('action') not in ('add', 'remove'):
        problems.append('route-edge: action must be add or remove')
    if name == 'traversal':
        if o.get('kind') not in TRAVERSALS:
            problems.append(f'traversal: kind must be one of {", ".join(TRAVERSALS)}')
    if name == 'note' and not str(o.get('text', '')).strip():
        problems.append('note: text is empty')
    return problems


class NavTune:
    def __init__(self, level, base, operations=None):
        self.level = level
        self.base = base                       # {'kind', 'sha1', 'file'}
        self.operations = list(operations or [])
        self.redo_stack = []
        self.dirty = False

    @classmethod
    def new(cls, level, base_path, kind='navkit'):
        if kind not in KINDS:
            raise ValueError(f'base kind must be one of {KINDS}')
        return cls(level, {'kind': kind, 'sha1': sha1_of(base_path), 'file': os.path.basename(base_path)})

    @property
    def kind(self):
        return self.base.get('kind', 'navkit')

    def allowed(self, name):
        return not (self.kind == 'dice' and name in REGION_OPS)

    def add(self, o):
        problems = validate(o, self.kind)
        if problems:
            raise ValueError('; '.join(problems))
        self.operations.append(o)
        self.redo_stack.clear()
        self.dirty = True
        return o

    def remove(self, index):
        o = self.operations.pop(index)
        self.redo_stack.clear()
        self.dirty = True
        return o

    def undo(self):
        if not self.operations:
            return None
        o = self.operations.pop()
        self.redo_stack.append(o)
        self.dirty = True
        return o

    def redo(self):
        if not self.redo_stack:
            return None
        o = self.redo_stack.pop()
        self.operations.append(o)
        self.dirty = True
        return o

    def base_changed(self, base_path):
        """True when the file at base_path is not the one the edits were made on."""
        return bool(self.base.get('sha1')) and sha1_of(base_path) != self.base['sha1']

    def to_json(self):
        return {'format': FORMAT, 'version': VERSION, 'level': self.level, 'base': self.base,
                'operations': copy.deepcopy(self.operations)}

    def save(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(self.to_json(), f, indent=1, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, path)                  # never a half-written side file
        self.dirty = False

    def summary(self, k):
        o = self.operations[k]
        name = o['op']
        if 'region' in o:
            r = o['region']
            where = f'polygon of {len(r["polygon"])} points, y {r["y"][0]:.1f}..{r["y"][1]:.1f}' if 'polygon' in r else \
                f'box {r["box"][3] - r["box"][0]:.1f} x {r["box"][4] - r["box"][1]:.1f} x {r["box"][5] - r["box"][2]:.1f} m'
        elif name == 'traversal':
            where = f'{o["kind"]}{" (two-way)" if o.get("two-way", True) else ""} {fmt(o["bottom"])} -> {fmt(o["top"])}'
        elif 'a' in o:
            where = f'{fmt(o["a"])} -> {fmt(o["b"])}' + (f' ({o["action"]})' if 'action' in o else '')
        else:
            where = fmt(o['at']) + (f': {o["text"]}' if 'text' in o else '')
        return f'{name}: {where}  [{o.get("by", "?")}, {o.get("when", "?")}]'


def fmt(p):
    return '(' + ', '.join(f'{v:.1f}' for v in p) + ')'


def load(path):
    d = json.load(open(path, encoding='utf-8'))
    if d.get('format') != FORMAT:
        raise ValueError(f'{path}: not a navtune side file')
    if d.get('version', 0) > VERSION:
        raise ValueError(f'{path}: version {d["version"]} is newer than this tool reads ({VERSION})')
    t = NavTune(d.get('level', ''), d.get('base', {}), d.get('operations', []))
    problems = [f'operation {k}: {p}' for k, o in enumerate(t.operations) for p in validate(o, t.kind)]
    if problems:
        raise ValueError(f'{path}: ' + '; '.join(problems[:10]))
    return t


def default_path(root, lv):
    """Where the studio keeps a level's side file: OPFOR's work\\navkit\\<level>\\<level>.navtune.json."""
    return os.path.join(root, 'work', 'navkit', lv, f'{lv}.navtune.json')
