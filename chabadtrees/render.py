"""הפיכת גריד לקוד ויקי של תבנית "עץ משפחה", ומילוי תבנית "עץ משפחה לאדם אחד"."""
from __future__ import annotations

import html
import re

from . import wikitext as wt
from .layout import Chart

DIRS_TO_SYMBOL = {
    frozenset("ud"): "v", frozenset("lr"): "h", frozenset("udlr"): "cross",
    frozenset("dr"): "down_right", frozenset("dl"): "down_left", frozenset("ur"): "up_right", frozenset("ul"): "up_left",
    frozenset("ulr"): "t_up", frozenset("dlr"): "t_down", frozenset("udr"): "t_right", frozenset("udl"): "t_left",
    frozenset("u"): "v", frozenset("d"): "v", frozenset("l"): "h", frozenset("r"): "h",
}


def symbol_for(dirs: set, chart_cfg: dict, kind: str = "line") -> str:
    dirs = set(dirs)
    if chart_cfg.get("mirror_horizontal"):
        dirs = {{"l": "r", "r": "l"}.get(d, d) for d in dirs}
    if kind == "marriage":
        return chart_cfg["symbols"]["marriage"]
    if kind == "mline":
        return chart_cfg["symbols"].get("marriage_h", chart_cfg["symbols"]["h"])
    name = DIRS_TO_SYMBOL.get(frozenset(dirs), "h")
    return chart_cfg["symbols"][name]


def person_label(person: dict, cfg: dict, link: bool = True, years: bool = True) -> str:
    """תוכן קופסה: קישור פנימי לערך (אם יש), הבהרה, ושנים."""
    name = person["name"]
    if person.get("title") and link:
        title = person["title"]
        disp = wt.display_name(title)
        label = f"[[{title}|{disp}]]" if disp != title else f"[[{title}]]"
        extra = wt.disambiguator(title)
    else:
        label = name
        extra = ""
    small = []
    if extra:
        small.append(extra)
    if years and cfg["chart"].get("box_years") and (person.get("born") or person.get("died")):
        small.append(f"{person.get('born') or '?'}–{person.get('died') or ''}".rstrip("–"))
    if small:
        label += "<br /><small>" + " · ".join(small) + "</small>"
    return label


def refs_for(graph: dict, a: str, b: str, relation: str) -> tuple[list[str], list[str]]:
    """הערות שוליים שתומכות בקשר, והדפים שמצהירים עליו (למקרה שאין הערה)."""
    refs, pages = [], []
    for e in graph["edges"]:
        if e["relation"] != relation:
            continue
        if {e["a"], e["b"]} != {a, b}:
            continue
        for ev in e["evidence"]:
            for r in ev.get("refs", []):
                if r not in refs:
                    refs.append(r)
            if ev.get("source_page") and ev["source_page"] not in pages:
                pages.append(ev["source_page"])
    return refs, pages


def sanitize_ref(ref: str) -> str:
    """הערת שוליים בתוך פרמטר של תבנית: | חייב להפוך ל-{{!}} מחוץ לקישורים/תבניות."""
    parts = wt.split_top(ref)
    return "{{!}}".join(parts) if len(parts) > 1 else ref


_PARENTS_CACHE: dict[int, tuple[int, dict]] = {}


def _relatives_maps(graph: dict) -> tuple[dict, dict, dict]:
    """(הורים, אחים, גיסים) לפי מזהה – נבנה פעם אחת לגרף."""
    key = id(graph)
    cached = _PARENTS_CACHE.get(key)
    if cached is None or cached[0] != len(graph["edges"]):
        pm: dict[str, list[str]] = {}
        sm: dict[str, list[str]] = {}
        im: dict[str, list[str]] = {}
        for e in graph["edges"]:
            if e["confidence"] < 0.5:
                continue
            if e["relation"] == "parent":
                pm.setdefault(e["a"], []).append(e["b"])
            elif e["relation"] == "sibling":
                sm.setdefault(e["a"], []).append(e["b"]); sm.setdefault(e["b"], []).append(e["a"])
            elif e["relation"] == "sibling_in_law":
                im.setdefault(e["a"], []).append(e["b"]); im.setdefault(e["b"], []).append(e["a"])
        _PARENTS_CACHE.clear()
        _PARENTS_CACHE[key] = (len(graph["edges"]), (pm, sm, im))
        cached = _PARENTS_CACHE[key]
    return cached[1]


def _article_relative(graph: dict, pid: str) -> tuple[str, str] | None:
    """קרוב עם ערך של אדם בלי ערך – הורה (האב קודם) ואם אין, אח/ות – כדי לכתוב "אשתו: בת [[האב]]" בלי לנקוב בשמה."""
    pm, sm, _im = _relatives_maps(graph)
    persons = graph["persons"]
    for kind, cands in (("parent", pm.get(pid, [])), ("sibling", sm.get(pid, []))):
        arts = [p for p in cands if persons.get(p, {}).get("fetched")]
        arts.sort(key=lambda p: 0 if persons[p].get("gender") == "m" else 1)
        if arts:
            return kind, arts[0]
    return None


def _article_parent(graph: dict, pid: str) -> str | None:
    rel = _article_relative(graph, pid)
    return rel[1] if rel and rel[0] == "parent" else None


def tree_link(chart: Chart, existing: dict | None, pid: str) -> str:
    """"(עץ משפחת X)" – קישור לעץ אחר (קיים באתר או שנבנה כאן) שבו האדם מופיע."""
    if not existing:
        return ""
    trees = existing.get("person_to_trees", {}).get(pid) or []
    trees = [t for t in trees if t.startswith("תבנית:") and t != getattr(chart, "self_tree", None)]
    if not trees:
        return ""
    t = trees[0]
    return f"([[{t}|{t.split(':', 1)[1]}]])"


def spouse_inline(node, pid: str, chart: Chart, graph: dict, cfg: dict, existing: dict | None) -> str:
    """בני הזוג של בן משפחה בתוך הקופסה: "אשת [[בעלה]]" לבת, "אשתו: [[אשתו]]" לבן. רק לבני זוג שאין להם קופסה.
    בן זוג בלי ערך לא נזכר בשמו; אם להורהו יש ערך – "אשתו: בת [[ההורה]]" וקישור לעץ של משפחתו."""
    ccfg = cfg["chart"]
    boxed = {b["person"] for b in chart.boxes.values() if b.get("node") is node and b["role"] == "spouse"}
    spouses = [m.spouse for m in node.marriages if m.spouse and m.spouse not in boxed]
    spouses += [sp for sp in getattr(node, "hidden_spouses", []) if sp not in spouses]
    if not spouses:
        return ""
    persons = graph["persons"]
    hide = getattr(chart, "hide_unlinked", False)
    person = persons[pid]
    parts = []
    for sp in spouses:
        sperson = persons.get(sp, {"name": sp, "title": None})
        pg, sg = person.get("gender"), sperson.get("gender")
        link_pid = sp
        if hide and not sperson.get("fetched"):
            rel = _article_relative(graph, sp)
            if not rel:
                continue
            kind, par = rel
            g = sg or {"m": "f", "f": "m"}.get(pg)
            word = ({"f": "בת", "m": "בן"} if kind == "parent" else {"f": "אחות", "m": "אח"}).get(g)
            if not word:
                continue
            label = f"{word} {person_label(persons[par], cfg, years=False)}"
            link_pid = par
        else:
            label = person_label(sperson, cfg, years=False)
        # מגדר לפי שני הצדדים: בת (או בן זוג גבר) → "אשת X"; בן (או בת זוג אישה) → "אשתו: X"
        if pg == "f" or (pg is None and sg == "m"):
            parts.append(f"{ccfg.get('wife_of', 'אשת')} {label}")
        elif cfg["chart"].get("show_sons_wives", True):
            parts.append(f"{ccfg.get('husband_label', 'אשתו:')} {label}")
        else:
            continue
        # קישור לעץ של משפחת בן הזוג (קיים באתר, או נבנה כאן) – צמוד לשם: "אשת [[X]] (עץ משפחת X)"
        link = tree_link(chart, existing, link_pid)
        if link:
            parts[-1] += " " + link
    return "<br /><small>" + "; ".join(parts) + "</small>" if parts else ""


def box_content(chart: Chart, bid: str, graph: dict, cfg: dict, with_refs: bool = True, existing: dict | None = None) -> str:
    info = chart.boxes[bid]
    pid = info["person"]
    person = graph["persons"][pid]
    node = info.get("node")
    if pid in getattr(chart, "anonymous", ()):
        # חוליה הכרחית בלי ערך – בלי שם, רק הקשר: "בת (ללא ערך)" תחת הורה, "אב (ללא ערך)" בראש
        base = cfg.get("anonymous_label", "ללא ערך")
        below = node is not None and info["role"] == "member" and node.depth > 0
        word = ({"f": "בת", "m": "בן"} if below else {"f": "אם", "m": "אב"}).get(person.get("gender"))
        label = f"{word} ({base})" if word else base
    else:
        label = person_label(person, cfg)
    if node is not None and info["role"] == "member":
        label += spouse_inline(node, pid, chart, graph, cfg, existing)
    ref_texts: list[str] = []
    if with_refs and node is not None:
        if info["role"] == "member" and node.depth > 0:
            # מקור לקשר הורה-ילד: מחפשים את ההורה שדרכו הגענו
            for e in graph["edges"]:
                if e["relation"] == "parent" and e["a"] == pid:
                    for ev in e["evidence"]:
                        ref_texts += [r for r in ev.get("refs", []) if r not in ref_texts]
        elif info["role"] == "spouse":
            refs, _pages = refs_for(graph, node.person, pid, "spouse")
            ref_texts = refs
    if ref_texts:
        label += "".join(sanitize_ref(r) for r in ref_texts[:2])
    if info["role"] == "spouse":
        link = tree_link(chart, existing, pid)
        if link:
            label += f"<br /><small>{link}</small>"
    if info["role"] == "member" and person.get("fetched"):
        # "גיסו, הרב מנחם לרר" – קשר למשפחה אחרת שכתוב בערך; רק כשהגיס יש לו ערך ואינו מופיע בעץ הזה
        _pm, _sm, im = _relatives_maps(graph)
        shown = {b["person"] for b in chart.boxes.values()}
        lines = []
        for il in im.get(pid, []):
            ip = graph["persons"].get(il, {})
            if not ip.get("fetched") or il in shown:
                continue
            word = "גיסתו" if ip.get("gender") == "f" else "גיסו"
            link = tree_link(chart, existing, il)
            lines.append(f"{word}: {person_label(ip, cfg, years=False)}" + (f" {link}" if link else ""))
        if lines:
            label += "<br /><small>" + "; ".join(lines[:3]) + "</small>"
    if node is not None and info["role"] == "member" and getattr(node, "extra_children", None):
        names = []
        for c in node.extra_children:
            cp = graph["persons"].get(c, {})
            nm = cp.get("name") or c.lstrip("~").split("@", 1)[0]
            sps = [e["b"] if e["a"] == c else e["a"] for e in graph["edges"] if e["relation"] == "spouse" and c in (e["a"], e["b"]) and e["confidence"] >= 0.5]
            if sps:
                sp = graph["persons"].get(sps[0], {"name": sps[0], "title": None})
                word = cfg["chart"].get("wife_of", "אשת") if cp.get("gender") == "f" else "בעל"
                nm += f" ({word} {person_label(sp, cfg, years=False)})"
            names.append(nm)
        word = "ילדים נוספים" if any(m.children for m in node.marriages) else "ילדים"
        label += f"<br /><small>{word}: " + ", ".join(names) + "</small>"
    if node is not None and info["role"] == "member" and node.truncated:
        label += "<br /><small>(יש צאצאים נוספים)</small>"
    if node is not None and info["role"] == "member" and getattr(node, "note", ""):
        label += f"<br /><small>{node.note}</small>"
    return label


def row_tiles(chart: Chart, r: int, box_w: int = 3) -> list:
    """רשימת המרצפות בשורה: None (ריק), ("box", id) במרצפת הראשונה של תיבה, "occupied" לשתי הבאות, או Cell."""
    tiles: list = [None] * chart.width
    for c in range(chart.width):
        cell = chart.cells.get((r, c))
        if cell is None:
            continue
        if cell.kind == "box":
            start = c - box_w // 2
            for t in range(start, start + box_w):
                if 0 <= t < chart.width:
                    tiles[t] = "occupied"
            tiles[max(start, 0)] = ("box", cell.ref)
        else:
            tiles[c] = cell
    return tiles


def chart_wikitext(chart: Chart, graph: dict, cfg: dict, with_refs: bool = True, existing: dict | None = None) -> str:
    ccfg = cfg["chart"]
    box_w = ccfg.get("box_tiles", 3)
    empty = ccfg["symbols"].get("empty", " ")
    lines = [f"{{{{{ccfg['start']}}}}}"]
    for r in range(chart.height):
        tiles = row_tiles(chart, r, box_w)
        last = max((i for i, t in enumerate(tiles) if t is not None), default=-1)
        cells, params = [], []
        for t in tiles[:last + 1]:
            if t is None:
                cells.append(empty)
            elif t == "occupied":
                continue
            elif isinstance(t, tuple):
                cells.append(t[1])
                params.append(f"{t[1]}={box_content(chart, t[1], graph, cfg, with_refs, existing)}")
            elif t.kind in ("marriage", "mline"):
                cells.append(symbol_for(t.dirs, ccfg, t.kind))
            else:
                cells.append(symbol_for(t.dirs, ccfg))
        if not cells:
            continue
        line = f"{{{{{ccfg['row']}|" + "|".join(cells)
        if params:
            line += "|" + "|".join(params)
        lines.append(line + "}}")
    lines.append(f"{{{{{ccfg['end']}}}}}")
    return "\n".join(lines)


def tree_page(title: str, chart: Chart, graph: dict, cfg: dict, family: dict | None, notes: list[str],
              as_template: bool = True, existing: dict | None = None) -> str:
    """דף שלם: הערת מקור, העץ, קטגוריות. כתבנית – עם noinclude."""
    header = ["<!-- עץ משפחה שנבנה אוטומטית מתוך ערכי חב\"דפדיה (chabadpedia-family-trees).",
              "     כל קשר בעץ מגיע ממשפט בערך של אחד מבני המשפחה; ראו את דף הבדיקה שמצורף לעץ.",
              "     קשרים בלי מקור חיצוני מסומנים בדף הבדיקה כ\"ללא מקור\"."]
    header += [f"     {n}" for n in notes]
    header.append("-->")
    body = chart_wikitext(chart, graph, cfg, existing=existing)
    sort_key = title.replace("עץ משפחת ", "").replace("עץ ", "")
    cats = [f"[[קטגוריה:עצי משפחה|{sort_key}]]"]
    if family and family.get("family_categories"):
        cats += [f"[[קטגוריה:{c}|*]]" for c in family["family_categories"]]
    if as_template:
        tail = "<noinclude>\n" + "\n".join(cats) + "\n</noinclude>"
    else:
        tail = "\n== הערות שוליים ==\n" + cfg["chart"]["references_tag"] + "\n\n" + "\n".join(cats)
    return "\n".join(header) + "\n" + body + "\n" + tail + "\n"


def ahnentafel_wikitext(slots: dict, graph: dict, cfg: dict) -> str:
    """ממלא את תבנית העץ לאדם אחד לפי מפת הפרמטרים שבהגדרות (שמות הפרמטרים – לכיול)."""
    acfg = cfg["ahnentafel"]
    params = acfg["params"]
    lines = [f"{{{{{acfg['template']}"]
    for key, param in params.items():
        pid = slots.get(key)
        if key in ("spouse", "siblings", "children"):
            continue
        if pid and pid in graph["persons"]:
            lines.append(f"| {param} = {person_label(graph['persons'][pid], cfg, years=False)}")
    lines.append("}}")
    return "\n".join(lines)


def slug(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", title).strip()


def escape_html(s: str) -> str:
    return html.escape(s, quote=True)
