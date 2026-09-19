"""בניית עצי צאצאים מהגרף וסידורם על גריד בסגנון תבנית "עץ משפחה" (Tree chart).

הגריד: כל תא הוא קופסה (אדם), צומת נישואין, קו, או ריק. הכיוונים לוגיים:
'l' = לכיוון התא הקודם ברצף, 'r' = התא הבא, 'u' = למעלה, 'd' = למטה.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .graph import children_of, parents_of, spouses_of, year_of


@dataclass
class Marriage:
    spouse: str | None                     # מזהה בן/בת הזוג, או None אם לא ידוע
    children: list["TreeNode"] = field(default_factory=list)


@dataclass
class TreeNode:
    person: str
    marriages: list[Marriage] = field(default_factory=list)
    depth: int = 0
    truncated: bool = False               # יש צאצאים נוספים שלא הוצגו (מגבלת גודל)
    note: str = ""

    def all_nodes(self):
        yield self
        for m in self.marriages:
            for c in m.children:
                yield from c.all_nodes()


@dataclass
class Cell:
    kind: str                              # box | marriage | line
    ref: str = ""                          # למעבר: מזהה קופסה
    dirs: set = field(default_factory=set)


@dataclass
class Chart:
    cells: dict = field(default_factory=dict)   # (row, col) -> Cell
    boxes: dict = field(default_factory=dict)   # box id -> {"person": id, "role": member|spouse, "node": TreeNode}
    width: int = 0
    height: int = 0

    def add_line(self, row: int, col: int, dirs: set) -> None:
        cell = self.cells.get((row, col))
        if cell is None:
            self.cells[(row, col)] = Cell("line", dirs=set(dirs))
        elif cell.kind == "line":
            cell.dirs |= set(dirs)
        elif cell.kind == "marriage":
            cell.dirs |= set(dirs)
        self.width = max(self.width, col + 1)
        self.height = max(self.height, row + 1)

    def put(self, row: int, col: int, cell: Cell) -> None:
        self.cells[(row, col)] = cell
        self.width = max(self.width, col + 1)
        self.height = max(self.height, row + 1)


# --------------------------------------------------------------------------- בניית העץ
class TreeBuilder:
    def __init__(self, graph: dict, config: dict):
        self.g = graph
        self.persons = graph["persons"]
        self.edges = graph["edges"]
        self.cfg = config
        self.min_conf = config.get("min_confidence", 0.5)
        self._children = defaultdict(list)
        self._parents = defaultdict(list)
        self._spouses = defaultdict(list)
        for e in self.edges:
            if e["confidence"] < self.min_conf:
                continue
            if e["relation"] == "parent":
                self._children[e["b"]].append(e["a"])
                self._parents[e["a"]].append(e["b"])
            elif e["relation"] == "spouse":
                self._spouses[e["a"]].append(e["b"])
                self._spouses[e["b"]].append(e["a"])

    def sort_key(self, pid: str):
        y = year_of(self.persons[pid]) if pid in self.persons else None
        return (0, y) if y else (1, 0)

    def build(self, root: str, members: set[str] | None, expand: set[str] | None, max_nodes: int, max_depth: int | None = None) -> TreeNode:
        """members: מי מותר להופיע כצאצא (None = כולם). expand: מי מרחיבים את צאצאיו (None = לפי כללי המשפחה)."""
        visited = {root}
        node_count = [1]

        def expandable(pid: str) -> bool:
            if expand is not None:
                return pid in expand
            return True

        def owner_of(child: str) -> str | None:
            """ההורה שתחתיו הילד מוצג: מי שיש לו ערך > גבר > מוקדם יותר."""
            pars = [p for p in self._parents.get(child, []) if members is None or p in members]
            if not pars:
                return None
            def rank(p):
                info = self.persons.get(p, {})
                return (0 if info.get("fetched") else 1, 0 if info.get("gender") == "m" else 1, self.sort_key(p), p)
            return sorted(pars, key=rank)[0]

        self.owner_of = owner_of

        def build_node(pid: str, depth: int) -> TreeNode:
            node = TreeNode(person=pid, depth=depth)
            spouses = list(dict.fromkeys(self._spouses.get(pid, [])))
            kids = [c for c in dict.fromkeys(self._children.get(pid, [])) if members is None or c in members]
            kids = [c for c in kids if c not in visited and owner_of(c) == pid]
            kids = sorted(kids, key=self.sort_key)
            # שיוך ילדים לנישואין
            by_spouse: dict[str | None, list[str]] = defaultdict(list)
            for c in kids:
                others = [p for p in self._parents.get(c, []) if p != pid]
                other = next((o for o in others if o in spouses), None)
                if other is None and others:
                    pg = self.persons.get(pid, {}).get("gender")
                    cands = [o for o in others if self.persons.get(o, {}).get("gender") in (None, ("f" if pg == "m" else "m" if pg == "f" else None))
                             or self.persons.get(o, {}).get("gender") != pg]
                    if cands:
                        other = cands[0]
                        if other not in spouses:
                            spouses.append(other)
                if other is None and len(spouses) == 1:
                    other = spouses[0]
                by_spouse[other].append(c)
            for sp in spouses:
                by_spouse.setdefault(sp, [])
            spouses_sorted = sorted(spouses, key=lambda s: (min((self.sort_key(c) for c in by_spouse[s]), default=(2, 0)), spouses.index(s)))
            order = spouses_sorted + ([None] if None in by_spouse else [])
            stop = (max_depth is not None and depth >= max_depth) or not expandable(pid)
            for sp in order:
                m = Marriage(spouse=sp)
                for c in by_spouse[sp]:
                    if stop:
                        node.truncated = True
                        break
                    if node_count[0] >= max_nodes:
                        node.truncated = True
                        break
                    visited.add(c)
                    node_count[0] += 1
                    m.children.append(build_node(c, depth + 1))
                node.marriages.append(m)
            return node

        return build_node(root, 0)


# --------------------------------------------------------------------------- סידור על הגריד
@dataclass
class Block:
    cells: dict                    # (row, col) -> Cell, יחסי
    width: int
    height: int
    attach_col: int                # עמודת הקופסה של בן המשפחה
    marriage_cols: dict            # אינדקס נישואין -> עמודת הצומת


def layout_forest(roots: list[TreeNode], box_ids: dict, gap: int = 1) -> Chart:
    """מסדר כמה עצים זה ליד זה. box_ids: person id -> box id (מתמלא תוך כדי)."""
    conn_rows = generation_connector_rows(roots)
    chart = Chart()
    col = 0
    for root in roots:
        block = _block(root, conn_rows, box_ids, chart)
        for (r, c), cell in block.cells.items():
            if cell.kind == "line":
                chart.add_line(r, c + col, cell.dirs)
            else:
                chart.put(r, c + col, cell)
        col += block.width + gap
    return chart


def generation_connector_rows(roots: list[TreeNode]) -> dict[int, int]:
    """כמה שורות חיבור צריך בין דור g לדור g+1 (מספר הנישואין עם ילדים המקסימלי בדור)."""
    rows: dict[int, int] = defaultdict(lambda: 1)
    for root in roots:
        for node in root.all_nodes():
            with_kids = sum(1 for m in node.marriages if m.children)
            rows[node.depth] = max(rows[node.depth], with_kids, 1)
    return rows


def _box_id(box_ids: dict, chart: Chart, person: str, role: str, node: TreeNode) -> str:
    bid = f"B{len(chart.boxes) + 1}"
    chart.boxes[bid] = {"person": person, "role": role, "node": node}
    box_ids.setdefault(person, bid)
    return bid


def _block(node: TreeNode, conn_rows: dict[int, int], box_ids: dict, chart: Chart) -> Block:
    # שורת בני הזוג: [S1, y, P, y, S2] / [P, y, S1] / [P]
    spouses = [m.spouse for m in node.marriages if m.spouse]
    partner: list[tuple[str, str | None]] = []       # (kind, ref)
    marriage_offset: dict[int, int] = {}
    p_offset = 0
    if len(spouses) >= 2:
        partner = [("box", spouses[0]), ("marriage", None), ("box", node.person), ("marriage", None), ("box", spouses[1])]
        p_offset = 2
        extra = spouses[2:]
    elif len(spouses) == 1:
        partner = [("box", node.person), ("marriage", None), ("box", spouses[0])]
        extra = []
    else:
        partner = [("box", node.person)]
        extra = []
    for i, m in enumerate(node.marriages):
        if m.spouse is None:
            marriage_offset[i] = p_offset
        elif m.spouse == (spouses[0] if spouses else None):
            marriage_offset[i] = 1 if len(spouses) == 1 else 1
        elif len(spouses) >= 2 and m.spouse == spouses[1]:
            marriage_offset[i] = 3
        else:
            marriage_offset[i] = p_offset
    for sp in extra:  # נישואין שלישיים ואילך – קופסה נוספת אחרי תא קו
        partner += [("line_h", None), ("marriage", None), ("box", sp)]
        idx = [m.spouse for m in node.marriages].index(sp)
        marriage_offset[idx] = len(partner) - 2

    # בלוקים של ילדים, לפי נישואין (כל נישואין שומרים את הבלוקים שלהם)
    groups: dict[int, list[tuple[Block, int]]] = {}
    col = 0
    placed_any = False
    child_blocks: list[tuple[Block, int]] = []
    for i in sorted(range(len(node.marriages)), key=lambda k: marriage_offset[k]):
        m = node.marriages[i]
        if not m.children:
            continue
        groups[i] = []
        for c in m.children:
            b = _block(c, conn_rows, box_ids, chart)
            if placed_any:
                col += 1
            left = col
            child_blocks.append((b, left))
            groups[i].append((b, left))
            col = left + b.width
            placed_any = True
    children_width = col
    spans = {i: (min(left + b.attach_col for b, left in blocks), max(left + b.attach_col for b, left in blocks))
             for i, blocks in groups.items()}

    # מיקום שורת בני הזוג: כל צומת נישואין מעל מרכז הילדים שלו, בממוצע
    desired = [((f + l) / 2) - marriage_offset[i] for i, (f, l) in spans.items()]
    L = round(sum(desired) / len(desired)) if desired else 0
    ordered = sorted(spans.items(), key=lambda kv: kv[1][0])
    # שני נישואין עם ילדים: הצומת המאוחר חייב להיות מימין (לוגית) לקבוצה המוקדמת, אחרת הקווים נחתכים
    for (i, (f, l)), (j, (f2, l2)) in zip(ordered, ordered[1:]):
        if marriage_offset[j] > marriage_offset[i]:
            L = max(L, l + 1 - marriage_offset[j])
    shift = 0
    if L < 0:
        shift, L = -L, 0
    width = max(children_width + shift, L + len(partner))
    block_cells: dict = {}
    for k, (kind, ref) in enumerate(partner):
        c = L + k
        if kind == "box":
            role = "member" if ref == node.person else "spouse"
            bid = _box_id(box_ids, chart, ref, role, node)
            block_cells[(0, c)] = Cell("box", ref=bid)
        elif kind == "marriage":
            block_cells[(0, c)] = Cell("marriage", dirs={"l", "r"})
        else:
            block_cells[(0, c)] = Cell("line", dirs={"l", "r"})
    rows_here = conn_rows[node.depth]

    def add_line(r, c, dirs):
        cell = block_cells.get((r, c))
        if cell is None:
            block_cells[(r, c)] = Cell("line", dirs=set(dirs))
        else:
            cell.dirs |= set(dirs)

    for seg_index, (i, (f, l)) in enumerate(ordered):
        anchor = L + marriage_offset[i]
        if block_cells[(0, anchor)].kind == "marriage":
            block_cells[(0, anchor)].dirs.add("d")
        child_cols = sorted(left + shift + b.attach_col for b, left in groups[i])
        seg_row = 1 + seg_index
        for r in range(1, seg_row):
            add_line(r, anchor, {"u", "d"})
        lo, hi = min(anchor, child_cols[0]), max(anchor, child_cols[-1])
        for x in range(lo, hi + 1):
            dirs = set()
            if x > lo:
                dirs.add("l")
            if x < hi:
                dirs.add("r")
            if x == anchor:
                dirs.add("u")
            if x in child_cols:
                dirs.add("d")
            if lo == hi:
                dirs = {"u", "d"}
            add_line(seg_row, x, dirs)
        for r in range(seg_row + 1, rows_here + 1):
            for x in child_cols:
                add_line(r, x, {"u", "d"})
    height = 1
    for b, left in child_blocks:
        for (r, c), cell in b.cells.items():
            rr, cc = r + 1 + rows_here, c + left + shift
            if cell.kind == "line":
                add_line(rr, cc, cell.dirs)
            else:
                block_cells[(rr, cc)] = cell
            height = max(height, rr + 1)
    return Block(cells=block_cells, width=width, height=height, attach_col=L + p_offset, marriage_cols={i: L + o for i, o in marriage_offset.items()})


# --------------------------------------------------------------------------- עץ אבות (לאדם אחד)
def ancestor_chart(graph: dict, person: str, generations: int, box_ids: dict) -> tuple[Chart, dict]:
    """גריד של אבות: האדם למטה, מעליו הוריו, סביו וכו'. מחזיר (chart, slots) כאשר slots ממפה
    מפתח כמו 'ff' (אבי האב) למזהה אדם – לשימוש בתבנית העץ לאדם אחד."""
    persons, edges = graph["persons"], graph["edges"]
    min_conf = 0.5

    def parents(pid: str) -> tuple[str | None, str | None]:
        father = mother = None
        for e in parents_of(edges, pid):
            if e["confidence"] < min_conf:
                continue
            g = persons[e["b"]]["gender"]
            if g == "m" and father is None:
                father = e["b"]
            elif g == "f" and mother is None:
                mother = e["b"]
            elif father is None:
                father = e["b"]
            elif mother is None:
                mother = e["b"]
        # סבים עם צד ידוע – משלימים כשאין קישור דרך ההורה
        return father, mother

    slots: dict[str, str | None] = {"self": person}
    keys = [""]
    for gen in range(1, generations + 1):
        new_keys = []
        for k in keys:
            base = slots.get(k or "self")
            f, m = parents(base) if base else (None, None)
            slots[k + "f"] = f
            slots[k + "m"] = m
            new_keys += [k + "f", k + "m"]
        keys = new_keys
    # סבים שהוזכרו ישירות ("סבו מצד אביו") כשההורה חסר
    for e in edges:
        if e["relation"] == "grandparent" and e["a"] == person and e["confidence"] >= min_conf and e.get("side"):
            key = ("f" if e["side"] == "father" else "m") + ("f" if persons[e["b"]]["gender"] != "f" else "m")
            if slots.get(key) is None:
                slots[key] = e["b"]

    chart = Chart()
    top = generations
    # רוחב: 2**top קופסאות בשורה העליונה, כל זוג תופס 3 תאים + רווח 1 => עמודה של קופסה k: 4*(k//2) + 2*(k%2)
    def col_of(gen: int, index: int) -> int:
        if gen == top:
            return 4 * (index // 2) + 2 * (index % 2)
        # מרכז בין שתי הקופסאות של ההורים
        return (col_of(gen + 1, 2 * index) + col_of(gen + 1, 2 * index + 1)) // 2

    row_of = {gen: 2 * (top - gen) for gen in range(top + 1)}
    for gen in range(top, -1, -1):
        for index in range(2 ** gen):
            key = _slot_key(gen, index)
            pid = slots.get(key)
            r, c = row_of[gen], col_of(gen, index)
            if pid:
                bid = f"B{len(chart.boxes) + 1}"
                chart.boxes[bid] = {"person": pid, "role": "member" if key == "self" else "ancestor", "node": None, "slot": key}
                chart.put(r, c, Cell("box", ref=bid))
            if gen < top:
                # חיבור מההורים (gen+1) לילד (gen)
                fc, mc = col_of(gen + 1, 2 * index), col_of(gen + 1, 2 * index + 1)
                fk, mk = _slot_key(gen + 1, 2 * index), _slot_key(gen + 1, 2 * index + 1)
                has_f, has_m = bool(slots.get(fk)), bool(slots.get(mk))
                if not pid or not (has_f or has_m):
                    continue
                pr = row_of[gen + 1]
                mid = (fc + mc) // 2
                if has_f and has_m:
                    for x in range(fc + 1, mc):
                        chart.add_line(pr, x, {"l", "r"})
                    chart.cells[(pr, mid)] = Cell("marriage", dirs={"l", "r", "d"})
                    chart.add_line(pr + 1, mid, {"u", "d"}) if mid == c else None
                    if mid != c:
                        lo, hi = min(mid, c), max(mid, c)
                        for x in range(lo, hi + 1):
                            dirs = set()
                            if x > lo: dirs.add("l")
                            if x < hi: dirs.add("r")
                            if x == mid: dirs.add("u")
                            if x == c: dirs.add("d")
                            chart.add_line(pr + 1, x, dirs)
                else:
                    src = fc if has_f else mc
                    lo, hi = min(src, c), max(src, c)
                    for x in range(lo, hi + 1):
                        dirs = set()
                        if x > lo: dirs.add("l")
                        if x < hi: dirs.add("r")
                        if x == src: dirs.add("u")
                        if x == c: dirs.add("d")
                        if lo == hi: dirs = {"u", "d"}
                        chart.add_line(pr + 1, x, dirs)
    return chart, slots


def _slot_key(gen: int, index: int) -> str:
    if gen == 0:
        return "self"
    return "".join("f" if (index >> (gen - 1 - i)) & 1 == 0 else "m" for i in range(gen))
