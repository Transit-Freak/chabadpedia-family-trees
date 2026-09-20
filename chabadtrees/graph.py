"""איחוד הקשרים לגרף אנשים: מיזוג ראיות, הצלבה, הסקות, בדיקות עקביות וחלוקה למשפחות."""
from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict

from . import wikitext as wt

SYMMETRIC = {"spouse", "sibling", "sibling_in_law"}
STRUCTURAL = {"parent", "spouse"}          # קשרים שמגדירים רכיב משפחתי
WEAK = {"grandparent", "great_grandparent", "parent_in_law", "sibling_in_law", "uncle"}


class UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def edge_key(a: str, b: str, relation: str) -> tuple[str, str, str]:
    if relation in SYMMETRIC and b < a:
        a, b = b, a
    return a, b, relation


def build_graph(pages: dict[str, dict], relations: list[dict], config: dict) -> dict:
    """pages: title -> info (מתוך extract). relations: רשימת מילוני Relation."""
    min_conf = config.get("min_confidence", 0.5)
    persons: dict[str, dict] = {}
    fetched = set(pages)

    def ensure_person(pid: str, name: str, has_article: bool, linked: bool) -> dict:
        p = persons.get(pid)
        if p is None:
            if pid.startswith("~") and (not name or name.startswith("~")):
                name = pid[1:].split("@", 1)[0]      # שם לא-מקושר בלי תווי המזהה
            p = persons[pid] = {
                "id": pid, "title": pid if not pid.startswith("~") else None, "name": name or wt.display_name(pid),
                "linked": linked, "fetched": pid in fetched, "gender": None, "gender_votes": Counter(),
                "born": None, "died": None, "surname": wt.surname_of(pid) if not pid.startswith("~") else _surname_from_name(name),
                "categories": [], "family_categories": [], "flags": [], "mentioned_in": set(),
            }
        return p

    for title, info in pages.items():
        p = ensure_person(title, wt.display_name(title), True, True)
        p["fetched"] = True
        p["born"], p["died"] = info.get("born"), info.get("died")
        p["categories"] = info.get("categories", [])
        p["family_categories"] = [c for c in p["categories"] if any(c.startswith(pref) for pref in config.get("family_category_prefixes", ["משפחת "]))]
        for g, n in (info.get("gender_votes") or {}).items():
            p["gender_votes"][g] += n
        if info.get("surname"):
            p["surname"] = info["surname"]

    # --- אנשים לא-מקושרים: מזהה = ~שם@עוגן:תפקיד (העוגן: הצד המקושר בקשר; התפקיד מבדיל "אשתו חיה שרה" מ"בתו חיה שרה") ---
    resolved: list[dict] = []
    for rel in relations:
        rel = dict(rel)
        if not rel["relative"].startswith("~"):
            anchor = rel["relative"]
        elif not rel["person"].startswith("~"):
            anchor = rel["person"]
        else:
            anchor = rel["source_page"]
        for side in ("person", "relative"):
            pid = rel[side]
            if pid.startswith("~"):
                if rel["relation"] == "parent":
                    role = "child" if side == "person" else "parent"
                else:
                    role = rel["relation"]
                rel[side] = f"{pid}@{anchor}:{role}"
        resolved.append(rel)

    edges: dict[tuple, dict] = {}
    for rel in resolved:
        a, b, relation = rel["person"], rel["relative"], rel["relation"]
        pa = ensure_person(a, rel.get("person_name", ""), rel.get("person_has_article", True), not a.startswith("~"))
        pb = ensure_person(b, rel.get("relative_name", ""), rel.get("relative_has_article", True), not b.startswith("~"))
        pa["mentioned_in"].add(rel["source_page"]); pb["mentioned_in"].add(rel["source_page"])
        if rel.get("person_gender"):
            pa["gender_votes"][rel["person_gender"]] += 1
        if rel.get("relative_gender"):
            pb["gender_votes"][rel["relative_gender"]] += 1
        key = edge_key(a, b, relation)
        e = edges.get(key)
        if e is None:
            e = edges[key] = {"a": key[0], "b": key[1], "relation": relation, "evidence": [], "confidence": 0.0,
                              "flags": [], "side": rel.get("side"), "inferred": False}
        if any(ev["source_page"] == rel["source_page"] and ev["text"] == rel.get("evidence", "") for ev in e["evidence"]):
            # אותו משפט באותו דף נתפס בשני דפוסים – ראיה אחת, לא שתיים
            for ev in e["evidence"]:
                if ev["source_page"] == rel["source_page"] and ev["text"] == rel.get("evidence", ""):
                    ev["confidence"] = max(ev["confidence"], rel.get("confidence", 0.5))
            continue
        e["evidence"].append({"source_page": rel["source_page"], "text": rel.get("evidence", ""), "refs": rel.get("refs", []),
                              "method": rel.get("method"), "pattern": rel.get("pattern"), "confidence": rel.get("confidence", 0.5),
                              "alias": bool(rel.get("alias"))})
        if rel.get("side") and not e.get("side"):
            e["side"] = rel["side"]

    # --- מיזוג אנשים לא-מקושרים בעלי אותו שם שעוגניהם קרובים ---
    _merge_unlinked(persons, edges)

    # --- ביטחון משולב (noisy-or) ואימות מצולב ---
    for e in edges.values():
        conf = 1.0
        for ev in e["evidence"]:
            conf *= 1 - min(max(ev["confidence"], 0.05), 0.95)
        e["confidence"] = round(min(1 - conf, 0.98), 2)
        pages_seen = {ev["source_page"] for ev in e["evidence"]}
        if len(pages_seen) >= 2:
            e["flags"].append("corroborated")
        if not any(ev["refs"] for ev in e["evidence"]):
            e["flags"].append("no_ref")

    # --- מגדר ---
    for p in persons.values():
        votes = p["gender_votes"]
        if votes["m"] or votes["f"]:
            p["gender"] = "m" if votes["m"] >= votes["f"] else "f"

    # --- הסקות ---
    _infer(persons, edges, min_conf)

    # --- בדיקות עקביות ---
    _consistency(persons, edges)

    # --- רכיבים (משפחות) ---
    uf = UnionFind()
    for pid in persons:
        uf.find(pid)
    for e in edges.values():
        if e["relation"] in STRUCTURAL and e["confidence"] >= min_conf:
            uf.union(e["a"], e["b"])
    groups: dict[str, list[str]] = defaultdict(list)
    for pid in persons:
        groups[uf.find(pid)].append(pid)
    families = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        surnames = Counter(persons[m]["surname"] for m in members if persons[m]["surname"])
        cats = Counter(c for m in members for c in persons[m]["family_categories"])
        label = cats.most_common(1)[0][0] if cats else ("משפחת " + surnames.most_common(1)[0][0] if surnames else "משפחה ללא שם")
        families.append({"id": f"F{len(families) + 1}", "label": label, "members": sorted(members),
                         "size": len(members), "articles": sum(1 for m in members if persons[m]["fetched"]),
                         "surnames": [s for s, _ in surnames.most_common(4)], "family_categories": dict(cats)})
    families.sort(key=lambda f: (-f["articles"], -f["size"]))
    for f in families:
        for m in f["members"]:
            persons[m]["family"] = f["id"]

    for p in persons.values():
        p["mentioned_in"] = sorted(p["mentioned_in"])
        p["gender_votes"] = dict(p["gender_votes"])
    return {"persons": persons, "edges": list(edges.values()), "families": families}


def _surname_from_name(name: str) -> str:
    words = [w for w in wt.normalize_quotes(name or "").split() if w not in wt.HONORIFIC_WORDS]
    return words[-1] if len(words) >= 2 else ""


def _name_key(name: str) -> str:
    words = [w.strip("(),.;:'\"") for w in wt.normalize_quotes(name or "").replace("-", " ").split()]
    return " ".join(w for w in words if w and w not in wt.HONORIFIC_WORDS and w not in wt.SUFFIX_WORDS)


def _given_part(name: str) -> str:
    """"אסתר לבית וולף" → "אסתר"."""
    return re.split(r"\s+לבית\s+", name, maxsplit=1)[0]


def _names_match(a: str, b: str) -> bool:
    """אותו אדם? שמות שווים, אחד תחילית-מילים של השני ("חנה" ⊂ "חנה ליבא", "נטע שלמה" ⊂ "נטע שלמה וילהלם"),
    או דמיון גבוה מאוד (שגיאת כתיב: "חי שרה"/"חיה שרה")."""
    a, b = _name_key(_given_part(a)), _name_key(_given_part(b))
    if not a or not b:
        return False
    if a == b:
        return True
    wa, wb = a.split(), b.split()
    if wa[:len(wb)] == wb or wb[:len(wa)] == wa:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _merge_unlinked(persons: dict, edges: dict) -> None:
    """מאחד אזכורים לא-מקושרים של אותו אדם.

    שני אזכורים הם אותו אדם אם השמות תואמים וגם יש להם אותו "משבצת" משפחתית: בן זוג של אותו X, ילד של אותו X,
    הורה של אותו ילד (ולכן בן זוג של ההורה השני), או אח של X (ולכן ילד של הורי X). כך "אשתו חיה שרה" בדף הבעל
    ו"אמו חיה שרה" בדף הבן מתאחדים, אבל סבתא ונכדה בעלות אותו שם – לא.
    אזכור לא-מקושר שתואם לאדם עם ערך באותה משבצת ("בנו נטע שלמה" ↔ הערך "נטע שלמה וילהלם") מתמזג לתוך הערך.
    """
    slots: dict[str, set] = defaultdict(set)
    parents_of: dict[str, set] = defaultdict(set)
    for (a, b, rel) in edges:
        if rel == "parent":
            slots[a].add(("child_of", b)); slots[b].add(("parent_of", a)); parents_of[a].add(b)
        elif rel == "spouse":
            slots[a].add(("spouse", b)); slots[b].add(("spouse", a))
        elif rel == "sibling":
            slots[a].add(("sibling", b)); slots[b].add(("sibling", a))
    for pid in list(slots):
        extra = set()
        for kind, other in slots[pid]:
            if kind == "parent_of":
                extra |= {("spouse", p) for p in parents_of[other] if p != pid}
            elif kind == "sibling":
                extra |= {("child_of", p) for p in parents_of[other]}
        slots[pid] |= extra
    by_slot: dict[tuple, list[str]] = defaultdict(list)
    for pid, ss in slots.items():
        for sl in ss:
            by_slot[sl].append(pid)

    def nm(pid: str) -> str:
        return persons[pid]["name"] if pid.startswith("~") else wt.display_name(pid)

    uf = UnionFind()
    known_match: dict[str, set[str]] = defaultdict(set)      # לא-מקושר → ערכים תואמים באותה משבצת
    for sl, ids in by_slot.items():
        if len(ids) < 2:
            continue
        unl = [x for x in ids if x.startswith("~")]
        if not unl:
            continue
        for i, x in enumerate(unl):
            for y in unl[i + 1:]:
                if _names_match(nm(x), nm(y)):
                    uf.union(x, y)
            for k in ids:
                if not k.startswith("~") and k in persons and _names_match(nm(x), nm(k)):
                    known_match[x].add(k)
    # אותו שם באותו דף מקור (למשל "אשתו חנה" ואחר כך "חנה" ברשימת הילדים – לא: רק כשאין משבצת סותרת)
    groups: dict[str, list[str]] = defaultdict(list)
    for pid in persons:
        if pid.startswith("~"):
            groups[uf.find(pid)].append(pid)
    remap: dict[str, str] = {}
    for root, members in groups.items():
        knowns = set()
        for m in members:
            knowns |= known_match.get(m, set())
        if len(knowns) == 1:
            rep = next(iter(knowns))
        else:
            rep = max(members, key=lambda x: (len(_name_key(persons[x]["name"]).split()), len(persons[x]["name"]), -len(x)))
        for m in members:
            if m != rep:
                remap[m] = rep
    if not remap:
        return
    for old, new in remap.items():
        po, pn = persons.pop(old), persons[new]
        pn["gender_votes"].update(po["gender_votes"])
        pn["mentioned_in"] |= po["mentioned_in"]
        if new.startswith("~") and len(_name_key(po["name"]).split()) > len(_name_key(pn["name"]).split()):
            pn["name"] = po["name"]
    for key in list(edges):
        a, b, rel = key
        na, nb = remap.get(a, a), remap.get(b, b)
        if (na, nb) == (a, b):
            continue
        e = edges.pop(key)
        if na == nb:
            continue
        nkey = edge_key(na, nb, rel)
        if nkey in edges:
            edges[nkey]["evidence"].extend(e["evidence"])
        else:
            e["a"], e["b"] = nkey[0], nkey[1]
            edges[nkey] = e


def parents_of(edges: dict | list, pid: str) -> list[dict]:
    it = edges.values() if isinstance(edges, dict) else edges
    return [e for e in it if e["relation"] == "parent" and e["a"] == pid]


def children_of(edges: dict | list, pid: str) -> list[dict]:
    it = edges.values() if isinstance(edges, dict) else edges
    return [e for e in it if e["relation"] == "parent" and e["b"] == pid]


def spouses_of(edges: dict | list, pid: str) -> list[str]:
    it = edges.values() if isinstance(edges, dict) else edges
    return [e["b"] if e["a"] == pid else e["a"] for e in it if e["relation"] == "spouse" and pid in (e["a"], e["b"])]


def _infer(persons: dict, edges: dict, min_conf: float) -> None:
    def add_inferred(a: str, b: str, relation: str, why: str, conf: float = 0.5):
        key = edge_key(a, b, relation)
        if key in edges or a == b:
            return
        edges[key] = {"a": key[0], "b": key[1], "relation": relation, "confidence": conf, "inferred": True,
                      "flags": ["inferred", "no_ref"], "side": None,
                      "evidence": [{"source_page": "", "text": why, "refs": [], "method": "inference", "pattern": "", "confidence": conf}]}

    # אחים חולקים הורים
    for key, e in list(edges.items()):
        if e["relation"] != "sibling" or e["confidence"] < min_conf:
            continue
        for x, y in ((e["a"], e["b"]), (e["b"], e["a"])):
            for pe in parents_of(edges, x):
                if pe["confidence"] < 0.7:
                    continue
                parent = pe["b"]
                pg = persons[parent]["gender"]
                has_same = any(persons[q["b"]]["gender"] == pg and pg for q in parents_of(edges, y))
                if not has_same:
                    add_inferred(y, parent, "parent", f"הוסק: {persons[y]['name']} אח/ות של {persons[x]['name']} שהוא/היא ילד/ה של {persons[parent]['name']}")
    # חם/חמות → הורה של בן/בת הזוג
    for key, e in list(edges.items()):
        if e["relation"] != "parent_in_law" or e["confidence"] < min_conf:
            continue
        person, in_law = e["a"], e["b"]
        for sp in spouses_of(edges, person):
            g = persons[in_law]["gender"]
            if not any(persons[q["b"]]["gender"] == g and g for q in parents_of(edges, sp)):
                add_inferred(sp, in_law, "parent", f"הוסק: {persons[person]['name']} חתן/כלה של {persons[in_law]['name']} ונשוי/אה ל{persons[sp]['name']}")


def _consistency(persons: dict, edges: dict) -> None:
    by_child: dict[str, list[dict]] = defaultdict(list)
    for e in edges.values():
        if e["relation"] == "parent":
            by_child[e["a"]].append(e)
    for child, es in by_child.items():
        genders = Counter(persons[e["b"]]["gender"] for e in es if e["confidence"] >= 0.5)
        if genders.get("m", 0) > 1:
            persons[child]["flags"].append("יותר מאב אחד")
        if genders.get("f", 0) > 1:
            persons[child]["flags"].append("יותר מאם אחת")
        for e in es:
            if e["b"] == child:
                e["flags"].append("self")
            cb, pb = _year(persons[child]["born"]), _year(persons[e["b"]]["born"])
            cd, pd = _year(persons[child]["died"]), _year(persons[e["b"]]["died"])
            if (cb and pb and cb < pb + 12) or (cd and pb and cd < pb):
                e["flags"].append("שנת לידה של ההורה מאוחרת מדי")
                e["confidence"] = min(e["confidence"], 0.3)
            elif cb and pd and cb > pd + 1:
                e["flags"].append("ההורה נפטר לפני לידת הילד")
                e["confidence"] = min(e["confidence"], 0.3)
    _break_cycles(persons, edges)
    # סב שנרשם כהורה: "אימה רחל, בתו של ר' דוד" – הדף קיבל גם את ר' דוד כהורה. אם הורה אחד הוא ילד של הורה אחר
    # של אותו אדם, ההורה השני הוא סב – מורידים את הקשר הזה.
    # רק קשרים מוצקים מעידים: לא קשר שהוסק, ולא קשר שכל ראיותיו נפתרו משם-תצוגה (שם שחוזר במשפחה).
    def solid(e: dict) -> bool:
        evs = e.get("evidence") or []
        return e["confidence"] >= 0.5 and not e.get("inferred") and not (evs and all(ev.get("alias") for ev in evs))
    parents_of: dict[str, set[str]] = defaultdict(set)
    for e in edges.values():
        if e["relation"] == "parent" and solid(e):
            parents_of[e["a"]].add(e["b"])
    for child, es in by_child.items():
        for e in es:
            if e["confidence"] < 0.5:
                continue
            if any(e["b"] in parents_of.get(p, ()) for p in parents_of.get(child, ()) if p != e["b"]):
                e["flags"].append("סב שנרשם כהורה")
                e["confidence"] = min(e["confidence"], 0.3)
    # בני זוג באותו מגדר – טעות חילוץ (הנושא של "נישאה ל..." נפל על הדף במקום על הבת)
    for e in edges.values():
        if e["relation"] == "spouse":
            ga, gb = persons[e["a"]].get("gender"), persons[e["b"]].get("gender")
            if ga and gb and ga == gb:
                e["flags"].append("בני זוג באותו מגדר")
                e["confidence"] = min(e["confidence"], 0.3)
    # הורה שהוא גם בן זוג
    spouse_pairs = {(e["a"], e["b"]) for e in edges.values() if e["relation"] == "spouse"}
    for e in edges.values():
        if e["relation"] == "parent" and ((e["a"], e["b"]) in spouse_pairs or (e["b"], e["a"]) in spouse_pairs):
            e["flags"].append("הורה שמסומן גם כבן זוג")


def _break_cycles(persons: dict, edges: dict, min_conf: float = 0.5) -> None:
    """מעגל בקשרי הורה-ילד (X אב של Y ו-Y אב של X, או דרך כמה דורות) אינו אפשרי – בדרך כלל תוצאה של
    שם שחוזר בדורות ("אביו ר' עמרם בלוי" שנפתר לערך של הנכד). מורידים את הקשר החלש ביותר במעגל
    (ראיה שנפתרה משם-תצוגה נחשבת חלשה יותר), עד שאין מעגלים."""
    for _round in range(50):
        parents: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        for e in edges.values():
            if e["relation"] == "parent" and e["confidence"] >= min_conf:
                parents[e["a"]].append((e["b"], e))
        color: dict[str, int] = {}
        stack_edges: list[dict] = []
        cycle: list[dict] | None = None

        def dfs(u: str) -> bool:
            nonlocal cycle
            color[u] = 1
            for v, e in parents.get(u, []):
                stack_edges.append(e)
                if color.get(v, 0) == 1:
                    # המעגל: מהקשר שנכנס ל-v ועד הקשר הנוכחי
                    start = next(i for i, x in enumerate(stack_edges) if x["a"] == v)
                    cycle = stack_edges[start:]
                    return True
                if color.get(v, 0) == 0 and dfs(v):
                    return True
                stack_edges.pop()
            color[u] = 2
            return False

        for node in list(parents):
            if color.get(node, 0) == 0 and dfs(node):
                break
        if not cycle:
            return

        def weakness(e: dict):
            alias = any(ev.get("alias") for ev in e["evidence"])
            return (0 if alias else 1, e["confidence"], len(e["evidence"]))
        weakest = min(cycle, key=weakness)
        weakest["flags"].append("מעגל בעץ היוחסין")
        weakest["confidence"] = min(weakest["confidence"], 0.2)


HEB_VALUES = {"א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9, "י": 10, "כ": 20, "ך": 20, "ל": 30,
              "מ": 40, "ם": 40, "נ": 50, "ן": 50, "ס": 60, "ע": 70, "פ": 80, "ף": 80, "צ": 90, "ץ": 90, "ק": 100,
              "ר": 200, "ש": 300, "ת": 400}


def _year(s: str | None) -> int | None:
    """ממיר שנה עברית (ה'תרכ"א) או לועזית למספר לועזי משוער."""
    if not s:
        return None
    s = wt.normalize_quotes(s)
    if re.fullmatch(r"\d{4}", s):
        return int(s)
    s = s.replace("ה'", "").replace('"', "").replace("'", "")
    if not s or not all(ch in HEB_VALUES for ch in s):
        return None
    val = sum(HEB_VALUES[ch] for ch in s)
    if val < 1000:
        val += 5000
    return val - 3760


def year_of(person: dict) -> int | None:
    return _year(person.get("born")) or _year(person.get("died"))
