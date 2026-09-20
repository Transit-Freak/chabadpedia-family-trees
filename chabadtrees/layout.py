"""בניית עצי צאצאים מהגרף וסידורם על גריד בסגנון תבנית "עץ משפחה" (Tree chart).

הגריד: כל תא הוא קופסה (אדם), צומת נישואין, קו, או ריק. הכיוונים לוגיים:
'l' = לכיוון התא הקודם ברצף, 'r' = התא הבא, 'u' = למעלה, 'd' = למטה.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from . import wikitext as wt
from .graph import children_of, parents_of, spouses_of, year_of


@dataclass
class Marriage:
    spouse: str | None                     # מזהה בן/בת הזוג, או None אם לא ידוע
    children: list["TreeNode"] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)   # ילדים בלי ערך שממתינים לשלב השני של הבנייה


@dataclass
class TreeNode:
    person: str
    marriages: list[Marriage] = field(default_factory=list)
    depth: int = 0
    truncated: bool = False               # יש צאצאים נוספים שלא הוצגו (מגבלת גודל)
    note: str = ""
    extra_children: list[str] = field(default_factory=list)   # ילדים בלי ערך, בשם פרטי בלבד – טקסט בקופסה
    hidden_spouses: list[str] = field(default_factory=list)   # בני זוג בלי ערך (פרטיות): בלי קופסה ובלי שם; "בת [[אביה]]" אם אפשר

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
    self_tree: str | None = None     # שם דף העץ שנבנה (לא מצביעים עליו כ"עץ קיים")
    anonymous: set = field(default_factory=set)   # אנשים בלי ערך שמוצגים כקופסה ריקה (חוליה הכרחית)
    hide_unlinked: bool = False      # בני זוג/ילדים בלי ערך אינם מוזכרים

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
        self.width = max(self.width, col + (2 if cell.kind == "box" else 1))
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

    def owner_of_in(self, members):
        """ההורה שתחתיו ילד מוצג: בן שושלת > מי שיש לו ערך > גבר > מוקדם יותר."""
        mi = getattr(self, "married_in", set())

        def owner_of(child: str):
            pars = [p for p in self._parents.get(child, []) if members is None or p in members]
            if not pars:
                return None

            def rank(p):
                info = self.persons.get(p, {})
                # הורה שהוא עצמו ילד של "הורה" אחר ברשימה הוא ההורה האמיתי; השני הוא סב שנרשם בטעות כהורה
                grandparent = any(q in self._children.get(p, []) for q in pars if q != p)
                return (1 if grandparent else 0, 1 if p in mi else 0, 0 if info.get("fetched") else 1, 0 if info.get("gender") == "m" else 1, self.sort_key(p), p)
            return sorted(pars, key=rank)[0]
        return owner_of

    def has_article(self, pid: str) -> bool:
        return bool(self.persons.get(pid, {}).get("fetched"))

    def hidden(self, pid: str) -> bool:
        """פרטיות: מי שאין לו ערך – גם קישור אדום – לא מוצג בשמו."""
        return bool(getattr(self, "hide_unlinked", False)) and not self.has_article(pid)

    def is_bare(self, pid: str) -> bool:
        """ילד בלי ערך, בשם פרטי בלבד, בלי בן זוג ובלי ילדים – מוצג כטקסט בקופסת ההורה, לא כקופסה."""
        if not pid.startswith("~"):
            return False
        words = [w for w in wt.normalize_quotes(self.persons.get(pid, {}).get("name", "")).split() if w not in wt.HONORIFIC_WORDS]
        return len(words) <= 1 and not self._spouses.get(pid) and not self._children.get(pid)

    def build(self, root: str, members: set[str] | None, expand: set[str] | None, max_nodes: int, max_depth: int | None = None) -> TreeNode:
        """members: מי מותר להופיע כצאצא (None = כולם). expand: מי מרחיבים את צאצאיו (None = לפי כללי המשפחה).

        שני שלבים: קודם כל מי שיש לו ערך (כדי שילדים בלי ערך לא ידחקו ענפים מקושרים מחוץ למגבלת הגודל),
        ואחר כך ילדים בלי ערך לפי דורות, כל עוד נשאר מקום. ילד בלי ערך בשם פרטי בלבד נכנס כטקסט לקופסת ההורה.
        """
        visited = {root}
        node_count = [1]

        def expandable(pid: str) -> bool:
            if expand is not None:
                return pid in expand
            return True

        owner_of = self.owner_of_in(members)
        self.owner_of = owner_of

        def make_node(pid: str, depth: int) -> TreeNode:
            node = TreeNode(person=pid, depth=depth)
            spouses = list(dict.fromkeys(self._spouses.get(pid, [])))
            node.hidden_spouses = [s for s in spouses if self.hidden(s)]
            spouses = [s for s in spouses if not self.hidden(s)]
            kids = [c for c in dict.fromkeys(self._children.get(pid, [])) if members is None or c in members]
            kids = [c for c in kids if c not in visited and owner_of(c) == pid]
            kids = sorted(kids, key=self.sort_key)
            # שיוך ילדים לנישואין
            by_spouse: dict[str | None, list[str]] = defaultdict(list)
            for c in kids:
                others = [p for p in self._parents.get(c, []) if p != pid]
                other = next((o for o in others if o in spouses), None)
                if other is None and others:
                    others = [o for o in others if not self.hidden(o)]
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
                    if self.is_bare(c):
                        visited.add(c)
                        node.extra_children.append(c)
                        continue
                    if stop:
                        node.truncated = True
                        continue
                    if c.startswith("~"):
                        m.deferred.append(c)
                        continue
                    if node_count[0] >= max_nodes:
                        node.truncated = True
                        continue
                    visited.add(c)
                    node_count[0] += 1
                    m.children.append(make_node(c, depth + 1))
                node.marriages.append(m)
            return node

        tree = make_node(root, 0)
        # שלב שני: ילדים בלי ערך (עם שם משפחה, בן זוג או ילדים), לפי דורות, כל עוד יש מקום
        pending = sorted(tree.all_nodes(), key=lambda n: n.depth)
        while pending:
            node = pending.pop(0)
            for m in node.marriages:
                for c in m.deferred:
                    if c in visited:
                        continue
                    if node_count[0] >= max_nodes:
                        node.truncated = True
                        continue
                    visited.add(c)
                    node_count[0] += 1
                    child = make_node(c, node.depth + 1)
                    m.children.append(child)
                    pending.append(child)
                m.deferred = []
        return tree


# --------------------------------------------------------------------------- סידור על הגריד
@dataclass
class Block:
    cells: dict                    # (row, col) -> Cell, יחסי
    width: int
    height: int
    attach_col: int                # עמודת הקופסה של בן המשפחה
    marriage_cols: dict            # אינדקס נישואין -> עמודת הצומת


def layout_forest(roots: list[TreeNode], box_ids: dict, gap: int = 1, spouse_style: str = "inline",
                  root_spouse_boxes: bool = True) -> Chart:
    """מסדר כמה עצים זה ליד זה. box_ids: person id -> box id (מתמלא תוך כדי)."""
    if spouse_style != "boxes":
        for root in roots:
            merge_marriages_inline(root)
    conn_rows = generation_connector_rows(roots)
    chart = Chart()
    col = 0
    for root in roots:
        boxes_below = spouse_style == "boxes"
        block = _block(root, conn_rows, box_ids, chart, spouse_boxes=(root_spouse_boxes or boxes_below), spouse_boxes_below_root=boxes_below)
        for (r, c), cell in block.cells.items():
            if cell.kind == "line":
                chart.add_line(r, c + col, cell.dirs)
            else:
                chart.put(r, c + col, cell)
        chart.width = max(chart.width, col + block.width)
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


def merge_marriages_inline(node: TreeNode) -> None:
    """בסגנון inline הילדים של כל הנישואין יורדים מקופסת האדם – מאחדים לנישואין אחד לצורך הסידור."""
    for n in node.all_nodes():
        if n.depth == 0:
            continue
        kids = [c for m in n.marriages for c in m.children]
        spouses = [m.spouse for m in n.marriages if m.spouse]
        n.marriages = [Marriage(spouses[0] if spouses else None, kids)] + [Marriage(sp, []) for sp in spouses[1:]]


def _box_id(box_ids: dict, chart: Chart, person: str, role: str, node: TreeNode) -> str:
    bid = f"B{len(chart.boxes) + 1}"
    chart.boxes[bid] = {"person": person, "role": role, "node": node}
    box_ids.setdefault(person, bid)
    return bid


BOX_W = 3   # תיבת טקסט = 3 מרצפות; הקווים מתחברים למרצפת האמצעית


def _block(node: TreeNode, conn_rows: dict[int, int], box_ids: dict, chart: Chart, spouse_boxes: bool = True,
           spouse_boxes_below_root: bool = False) -> Block:
    """בלוק של אדם, במרצפות: שורת בני הזוג, שורות החיבור, ובלוקי הילדים.

    זוג: ``A |ד| B`` (כמו בתיעוד התבנית); בלי קופסאות (inline) – רק הקופסה של האדם.
    הצומת ממוקם מעל הילד האמצעי (מספר אי-זוגי → ``+``; זוגי → ``^`` בין שני האמצעיים).
    """
    spouses = [m.spouse for m in node.marriages if m.spouse]
    partner: list[tuple[str, str | None, int]] = []      # (kind, ref, width)
    if spouse_boxes and len(spouses) >= 2:
        partner = [("box", spouses[0], BOX_W), ("marriage", None, 1), ("box", node.person, BOX_W),
                   ("marriage", None, 1), ("box", spouses[1], BOX_W)]
        extra = spouses[2:]
    elif spouse_boxes and len(spouses) == 1:
        partner = [("box", node.person, BOX_W), ("marriage", None, 1), ("box", spouses[0], BOX_W)]
        extra = []
    else:
        partner = [("box", node.person, BOX_W)]
        extra = []
    for sp in extra:
        partner += [("marriage", None, 1), ("box", sp, BOX_W)]
    # מיקומי מרכז (במרצפות, יחסית לתחילת השורה); כל צומת שייך לבן הזוג שסמוך לו
    p_offset = 0
    junction: dict[str, int] = {}
    tile = 0
    for k, (kind, ref, w) in enumerate(partner):
        if kind == "box" and ref == node.person:
            p_offset = tile + 1
        elif kind == "marriage":
            prev_ref = partner[k - 1][1] if k > 0 and partner[k - 1][0] == "box" else None
            next_ref = partner[k + 1][1] if k + 1 < len(partner) and partner[k + 1][0] == "box" else None
            owner = prev_ref if prev_ref not in (None, node.person) else next_ref
            if owner is not None:
                junction[owner] = tile
        tile += w
    partner_width = tile
    marriage_offset: dict[int, int] = {}
    for i, m in enumerate(node.marriages):
        marriage_offset[i] = junction.get(m.spouse, p_offset)

    # בלוקים של ילדים, לפי סדר הצמתים
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
            b = _block(c, conn_rows, box_ids, chart, spouse_boxes=spouse_boxes_below_root, spouse_boxes_below_root=spouse_boxes_below_root)
            if placed_any:
                col += 1
            left = col
            child_blocks.append((b, left))
            groups[i].append((b, left))
            col = left + b.width
            placed_any = True
    children_width = col

    def anchor_for(blocks) -> int:
        cols = sorted(left + b.attach_col for b, left in blocks)
        n = len(cols)
        if n % 2 == 1:
            return cols[n // 2]
        mid = cols[n // 2 - 1] + cols[n // 2]
        return mid // 2 if mid % 2 == 0 else cols[n // 2 - 1]

    ordered = sorted(groups.items(), key=lambda kv: min(left + b.attach_col for b, left in kv[1]))
    L = 0
    if ordered:
        first_i = ordered[0][0]
        L = anchor_for(ordered[0][1]) - marriage_offset[first_i]
        for (i, blocks_i), (j, blocks_j) in zip(ordered, ordered[1:]):
            if marriage_offset[j] > marriage_offset[i]:
                last_i = max(left + b.attach_col for b, left in blocks_i)
                L = max(L, last_i + 1 - marriage_offset[j])
    shift = 0
    if L < 0:
        shift, L = -L, 0
    width = max(children_width + shift, L + partner_width)
    block_cells: dict = {}
    tile = L
    for kind, ref, w in partner:
        if kind == "box":
            role = "member" if ref == node.person else "spouse"
            bid = _box_id(box_ids, chart, ref, role, node)
            block_cells[(0, tile + 1)] = Cell("box", ref=bid)
        elif kind == "marriage":
            block_cells[(0, tile)] = Cell("marriage", dirs={"l", "r"})
        else:
            block_cells[(0, tile)] = Cell("mline", dirs={"l", "r"})
        tile += w
    rows_here = conn_rows[node.depth]

    def add_line(r, c, dirs):
        cell = block_cells.get((r, c))
        if cell is None:
            block_cells[(r, c)] = Cell("line", dirs=set(dirs))
        else:
            cell.dirs |= set(dirs)

    for seg_index, (i, blocks) in enumerate(ordered):
        anchor = L + marriage_offset[i]
        if block_cells.get((0, anchor)) is not None and block_cells[(0, anchor)].kind == "marriage":
            block_cells[(0, anchor)].dirs.add("d")
        child_cols = sorted(left + shift + b.attach_col for b, left in blocks)
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
    מפתח כמו 'ff' (אבי האב) למזהה אדם – לשימוש בתבנית העץ לאדם אחד.

    זוג הורים מצויר ``A |~|ד|~| B`` והילד בדיוק מתחת ל-ד. כשידוע רק הורה אחד הוא ממוקם ישירות מעל הילד
    (קו ``!``), כך שלא נדרשים סמלי פינה שלא אומתו בתבנית.
    """
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
    for e in edges:
        if e["relation"] == "grandparent" and e["a"] == person and e["confidence"] >= min_conf and e.get("side"):
            key = ("f" if e["side"] == "father" else "m") + ("f" if persons[e["b"]]["gender"] != "f" else "m")
            if slots.get(key) is None:
                slots[key] = e["b"]

    # גיזום דורות ריקים מלמעלה
    top = generations
    while top > 1 and not any(slots.get(_slot_key(top, i)) for i in range(2 ** top)):
        top -= 1

    chart = Chart()
    # מרצפות: בשורה העליונה כל זוג תופס 7 מרצפות (A ד B) + רווח 2; מרכזי הקופסאות ב-1 וב-5
    cols: dict[str, int] = {}
    for i in range(2 ** top):
        cols[_slot_key(top, i)] = 9 * (i // 2) + 1 + 4 * (i % 2)
    for gen in range(top - 1, -1, -1):
        for i in range(2 ** gen):
            key = _slot_key(gen, i)
            fk, mk = _slot_key(gen + 1, 2 * i), _slot_key(gen + 1, 2 * i + 1)
            fc, mc = cols[fk], cols[mk]
            has_f, has_m = bool(slots.get(fk)), bool(slots.get(mk))
            if has_f and has_m:
                cols[key] = (fc + mc) // 2
            elif has_f:
                cols[key] = fc
            elif has_m:
                cols[key] = mc
            else:
                cols[key] = (fc + mc) // 2
    row_of = {gen: 2 * (top - gen) for gen in range(top + 1)}
    for gen in range(top, -1, -1):
        for i in range(2 ** gen):
            key = _slot_key(gen, i)
            pid = slots.get(key)
            r, c = row_of[gen], cols[key]
            if pid:
                bid = f"B{len(chart.boxes) + 1}"
                chart.boxes[bid] = {"person": pid, "role": "member" if key == "self" else "ancestor", "node": None, "slot": key}
                chart.put(r, c, Cell("box", ref=bid))
            if gen < top and pid:
                fk, mk = _slot_key(gen + 1, 2 * i), _slot_key(gen + 1, 2 * i + 1)
                fc, mc = cols[fk], cols[mk]
                has_f, has_m = bool(slots.get(fk)), bool(slots.get(mk))
                pr = row_of[gen + 1]
                if has_f and has_m:
                    mid = c
                    for x in range(fc + 2, mc - 1):          # מחוץ לקופסאות (3 מרצפות כל אחת)
                        if x == mid:
                            chart.put(pr, x, Cell("marriage", dirs={"l", "r", "d"}))
                        else:
                            chart.put(pr, x, Cell("mline", dirs={"l", "r"}))
                    chart.add_line(pr + 1, mid, {"u", "d"})
                elif has_f or has_m:
                    chart.add_line(pr + 1, c, {"u", "d"})
    return chart, slots


def _slot_key(gen: int, index: int) -> str:
    if gen == 0:
        return "self"
    return "".join("f" if (index >> (gen - 1 - i)) & 1 == 0 else "m" for i in range(gen))
