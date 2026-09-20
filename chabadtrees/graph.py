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
    # "בתו אסתר, רעיית הרב X": הנושא של שני הקשרים באותו משפט הוא אותו אדם – מזהה אחד (ולא "בת של" ו"אשת" נפרדים).
    # קשרי הורה קודם, כדי שהעוגן יהיה ההורה.
    same_sentence: dict[tuple, str] = {}
    for rel in sorted(relations, key=lambda r: 0 if r["relation"] == "parent" else 1):
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
                key = (rel["source_page"], rel.get("evidence", ""), pid)
                # הנושא – תמיד; מושא ("התחתן עם מתיה לרר, בתו של...") – רק כשהשם מופיע במשפט פעם אחת
                shareable = side == "person" or rel.get("evidence", "").count(pid[1:]) == 1
                if shareable and key in same_sentence:
                    rel[side] = same_sentence[key]
                    continue
                if rel["relation"] == "parent":
                    role = "child" if side == "person" else "parent"
                else:
                    role = rel["relation"]
                rel[side] = f"{pid}@{anchor}:{role}"
                if shareable:
                    same_sentence.setdefault(key, rel[side])
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

    # --- אזכור שנפתר משם-תצוגה לערך הלא נכון (בן דוד או סב באותו שם) חוזר להיות אדם בלי ערך ---
    _unresolve_conflicting_aliases(persons, edges)

    # --- מיזוג אנשים לא-מקושרים בעלי אותו שם שעוגניהם קרובים ---
    _merge_unlinked(persons, edges)
    _merge_unlinked_by_name(persons, edges, config.get("merge_by_name_max_surname_pages", 15))
    _merge_unlinked(persons, edges)          # מיזוג לפי שם פותח משבצות חדשות ("משה אורי בלויא" = "משה אורי בלוי" → שני "ברוך יהודה")
    # אחרי המיזוגים השרשראות מחוברות, ואפשר לראות ש"אביו" של הערך הוא בעצם צאצא שלו (נכד שקרוי על שם סבו)
    _unresolve_conflicting_aliases(persons, edges)
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
    words = [w for w in words if w and w not in wt.HONORIFIC_WORDS and w not in wt.SUFFIX_WORDS]
    if len(words) >= 2:
        words[-1] = _surname_norm(words[-1])
    return " ".join(words)


def _surname_norm(surname: str) -> str:
    """כתיב יידי של שם משפחה: "בלויא" = "בלוי", "לנדא" נשאר (קצר מדי לקצץ)."""
    if len(surname) >= 5 and surname.endswith("א") and not surname.endswith("יא"):
        return surname[:-1]
    if len(surname) >= 5 and surname.endswith("יא"):
        return surname[:-1]
    return surname


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
    # "אריה הרטמן" / "אריה אברהם הרטמן": אותו שם פרטי ראשון ואותו שם משפחה, שם אמצעי חסר באחד
    if len(wa) >= 2 and len(wb) >= 2 and wa[0] == wb[0] and wa[-1] == wb[-1] and (set(wa) <= set(wb) or set(wb) <= set(wa)):
        return True
    # שגיאת כתיב ("חי שרה"/"חיה שרה"): דמיון גבוה בשם כולו, וגם בחלק שלפני המילה האחרונה – שם משפחה ארוך משותף
    # לא הופך את "משה לברטוב" ל"שלמה לברטוב"
    if difflib.SequenceMatcher(None, a, b).ratio() < 0.85:
        return False
    if len(wa) >= 2 and len(wb) >= 2 and wa[-1] == wb[-1]:
        return difflib.SequenceMatcher(None, " ".join(wa[:-1]), " ".join(wb[:-1])).ratio() >= 0.7
    return True


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

    born = {pid: _year(p.get("born")) for pid, p in persons.items()}
    spouses_map: dict[str, set[str]] = defaultdict(set)
    children_map: dict[str, set[str]] = defaultdict(set)
    for (a, b, rel) in edges:
        if rel == "spouse":
            spouses_map[a].add(b); spouses_map[b].add(a)
        elif rel == "parent":
            children_map[b].add(a)

    def est_born(pid: str, depth: int = 2) -> int | None:
        if born.get(pid):
            return born[pid]
        if depth <= 0:
            return None
        for sp in spouses_map.get(pid, ()):
            if born.get(sp):
                return born[sp]
        kids = [y for y in (est_born(c, depth - 1) for c in children_map.get(pid, ())) if y]
        if kids:
            return min(kids) - 25
        pars = [y for y in (est_born(q, depth - 1) for q in parents_of.get(pid, ())) if y]
        if pars:
            return max(pars) + 25
        return None

    def years_ok(x: str, k: str) -> bool:
        ex, ek = est_born(x), est_born(k)
        return not (ex and ek and abs(ex - ek) > 40)

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
                if _names_match(nm(x), nm(y)) and years_ok(x, y):
                    uf.union(x, y)
            for k in ids:
                if not k.startswith("~") and k in persons and _names_match(nm(x), nm(k)) and years_ok(x, k):
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
    _apply_remap(persons, edges, remap)


def _unresolve_conflicting_aliases(persons: dict, edges: dict) -> None:
    """"בנו של משה בלוי" נפתר לערך "משה בלוי" (סופר, נולד תשי"ב) – אבל הכוונה למשה אורי בלוי, בלי ערך. כשקשר שנפתר
    משם-תצוגה סותר את הערך – אב מפורש אחר, או שנים שלא מסתדרות (ילד או נכד שנולד לפני ה"הורה") – כל הקשרים שנפתרו
    לערך הזה מאותו דף עוברים לאדם בלי ערך ("~משה בלוי@דף:alias"), והמיזוג הרגיל ימצא לו את המשבצת הנכונה."""
    def alias_only(e: dict) -> bool:
        return bool(e["evidence"]) and all(ev.get("alias") for ev in e["evidence"])

    def gender(pid: str) -> str | None:
        v = persons[pid]["gender_votes"]
        return "m" if v["m"] > v["f"] else "f" if v["f"] > v["m"] else None

    born = {pid: _year(p.get("born")) for pid, p in persons.items()}
    died = {pid: _year(p.get("died")) for pid, p in persons.items()}
    solid_fathers: dict[str, set[str]] = defaultdict(set)
    children: dict[str, list[str]] = defaultdict(list)
    children_solid: dict[str, list[str]] = defaultdict(list)      # בלי קשרים שנפתרו משם – הם עצמם החשודים
    for (a, b, rel), e in edges.items():
        if rel != "parent":
            continue
        children[b].append(a)
        if not alias_only(e):
            children_solid[b].append(a)
            if gender(b) == "m":
                solid_fathers[a].add(b)
    def descendants(pid: str, depth: int = 6) -> set[str]:
        out, frontier = set(), {pid}
        for _ in range(depth):
            frontier = {c for q in frontier for c in children_solid.get(q, [])} - out
            if not frontier:
                break
            out |= frontier
        return out

    # קישור מפורש לערך הלא נכון ("אחיו ר' [[ברוך יהודה בלוי]]" – העורך קישר לנכד באותו שם): גם הוא מופרד, אבל רק
    # כשהשנים לא מסתדרות בכלל; סתירת "אב אחר" נשמרת לפתרון-שם בלבד (קישור מפורש עדיף על שם)
    bad: set[tuple[str, str]] = set()          # (ערך, דף המקור של האזכור)
    for (a, b, rel), e in list(edges.items()):
        if rel not in ("parent", "sibling") or e.get("inferred"):
            continue
        weak = alias_only(e)
        pages = {ev["source_page"] for ev in e["evidence"]}
        if rel == "sibling":
            # אח שנפתר משם או קושר בטעות: "אחיו ר' ברוך יהודה בלוי" (יליד תרי"ז) ↔ הערך ברוך יהודה בלוי יליד תשל"ג
            for art, other in ((a, b), (b, a)):
                if not art.startswith("~") and persons[art].get("fetched") and born.get(art) and born.get(other) \
                        and abs(born[art] - born[other]) > 45 and pages != {art}:
                    bad.update((art, pg) for pg in pages)
            continue
        for art in (a, b):
            if art.startswith("~") or not persons[art].get("fetched") or pages == {art}:
                continue
            conflict = False
            if b in descendants(a):
                conflict = True   # ה"אב" הוא צאצא של הילד – נכד שקרוי על שם סבו
            if art == b:          # הערך כהורה של a
                if born[art] and born.get(a) and born[a] < born[art] + 12:
                    conflict = True
                if born[art] and any(born.get(c) and born[c] < born[art] + 25 for c in children.get(a, [])):
                    conflict = True
                if died[art] and born.get(a) and born[a] > died[art] + 1:
                    conflict = True
            else:                 # הערך כילד של b
                if weak and gender(b) == "m" and any(f != b for f in solid_fathers.get(art, ())):
                    conflict = True
                if born[art] and born.get(b) and born[art] < born[b] + 12:
                    conflict = True
            if conflict:
                bad.update((art, pg) for pg in pages)
    if not bad:
        return
    for key in list(edges):
        a, b, rel = key
        e = edges.get(key)
        if e is None or e.get("inferred"):
            continue
        pages = {ev["source_page"] for ev in e["evidence"]}
        for art in (a, b):
            hits = [pg for pg in pages if (art, pg) in bad and pg != art]
            if not hits:
                continue
            u = f"~{wt.display_name(art)}@{hits[0]}:alias"
            if u not in persons:
                src = persons[art]
                persons[u] = {"id": u, "title": None, "name": wt.display_name(art), "linked": False, "fetched": False, "gender": None,
                              "gender_votes": Counter(src["gender_votes"]), "born": None, "died": None, "surname": src.get("surname") or "",
                              "categories": [], "family_categories": [], "flags": ["הופרד מהערך: פתרון-שם סותר"], "mentioned_in": set(pages)}
            e2 = edges.pop(key)
            for ev in e2["evidence"]:
                ev["alias"] = False
            nkey = edge_key(u if a == art else a, u if b == art else b, rel)
            if nkey in edges:
                edges[nkey]["evidence"].extend(e2["evidence"])
            else:
                e2["a"], e2["b"] = nkey[0], nkey[1]
                edges[nkey] = e2
            break


def _apply_remap(persons: dict, edges: dict, remap: dict[str, str]) -> None:
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


def _merge_unlinked_by_name(persons: dict, edges: dict, max_surname_pages: int = 15) -> None:
    """מיזוג לפי שם, לגברים בעלי שם משפחה נדיר (עד max_surname_pages ערכים) שאינו שם פרטי.

    שלב א – אזכור עם שם אמצעי → הערך בלי השם האמצעי: "הרב אפרים צבי לרר" → הערך "אפרים לרר" (ערך יחיד שמתאים, בלי
    קשר ישיר, בלי אב אחר ובלי סתירת שנים); הראיות מסומנות כפתרון-שם.
    שלב ב – שני אזכורים בלי ערך (או קישור אדום) עם אותו שם (גם עם שם אמצעי חסר או כתיב "בלויא"/"בלוי") שמופיע
    בגרף בדיוק פעמיים – אותו אדם, אם אין סתירה: לא קשר ישיר, לא סב ונכד, לא שני אבות שונים."""
    fetched = [p for pid, p in persons.items() if p.get("fetched") and not pid.startswith("~")]
    surname_pages: Counter = Counter(_surname_norm(p["surname"]) for p in fetched if p.get("surname"))
    given_names = {wt.display_name(pid).split()[0] for pid, p in persons.items() if p.get("fetched") and not pid.startswith("~") and wt.display_name(pid).split()}
    born = {pid: _year(p.get("born")) for pid, p in persons.items()}

    def gender(pid: str) -> str | None:      # המגדר הסופי נקבע רק אחרי המיזוגים – כאן לפי ההצבעות עד כה
        v = persons[pid].get("gender_votes") or {}
        return "m" if v.get("m", 0) > v.get("f", 0) else "f" if v.get("f", 0) > v.get("m", 0) else persons[pid].get("gender")

    def words_of(pid: str) -> list[str]:
        p = persons[pid]
        name = p["name"] if pid.startswith("~") else wt.display_name(pid)
        return _name_key(_given_part(name)).split()

    def nested(a: list[str], b: list[str]) -> bool:
        """"אריה הרטמן" ⊂ "אריה אברהם הרטמן": אותו שם פרטי ראשון ואותו שם משפחה, ומילות האחד בתוך השני."""
        return a[0] == b[0] and a[-1] == b[-1] and (set(a) <= set(b) or set(b) <= set(a))

    def maps():
        parents_of: dict[str, set[str]] = defaultdict(set)
        partners_of: dict[str, set[str]] = defaultdict(set)
        children_of: dict[str, set[str]] = defaultdict(set)
        neighbours: dict[str, set[str]] = defaultdict(set)
        solid_fathers: dict[str, set[str]] = defaultdict(set)
        for (a, b, rel), e in edges.items():
            neighbours[a].add(b); neighbours[b].add(a)
            if rel == "parent":
                parents_of[a].add(b); children_of[b].add(a)
                if gender(b) == "m" and not (e["evidence"] and all(ev.get("alias") for ev in e["evidence"])):
                    solid_fathers[a].add(b)
            elif rel == "spouse":
                partners_of[a].add(b); partners_of[b].add(a)
        return parents_of, partners_of, children_of, neighbours, solid_fathers

    # ---- שלב א: אזכור לא-מקושר עם שם אמצעי → ערך ----
    parents_of, partners_of, children_of, neighbours, solid_fathers = maps()
    by_first_last: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pid, p in persons.items():
        if p.get("fetched") and not pid.startswith("~"):
            w = words_of(pid)
            if len(w) >= 2:
                by_first_last[(w[0], w[-1])].append(pid)
    def est_born(pid: str, depth: int = 2) -> int | None:
        """שנת לידה משוערת: של האדם, ואם אין – של בן זוגו, או ילד פחות 25, או הורה ועוד 25 (עד שתי רמות)."""
        if born.get(pid):
            return born[pid]
        if depth <= 0:
            return None
        for sp in partners_of.get(pid, ()):
            if born.get(sp):
                return born[sp]
        kids = [y for y in (est_born(c, depth - 1) for c in children_of.get(pid, ())) if y]
        if kids:
            return min(kids) - 25
        pars = [y for y in (est_born(q, depth - 1) for q in parents_of.get(pid, ())) if y]
        if pars:
            return max(pars) + 25
        return None

    def conflicts_with_article(pid: str, art: str) -> bool:
        my_fathers = {q for q in parents_of.get(pid, ()) if gender(q) == "m"}
        if art in neighbours[pid]:
            return True
        if my_fathers and solid_fathers.get(art) and not (my_fathers & solid_fathers[art]):
            return True                       # אב אחר
        if born.get(art) and any((born.get(c) or 9999) < born[art] + 12 for c in children_of.get(pid, ())):
            return True                       # ילד שנולד לפני הערך
        if children_of[pid] & parents_of[art] or children_of[art] & parents_of[pid]:
            return True                       # סב ונכד
        ea, ep = est_born(art), est_born(pid)
        return bool(ea and ep and abs(ea - ep) > 30)

    # כל האזכורים באותו שם מלא נבחנים יחד: אזכור שסותר את הערך (בן של בן הערך – נכד שקרוי על שם סבו; אב אחר) הוא
    # אדם אחר, ואזכור בלי סתירה מתמזג לערך רק אם הוא רחוק בשנים מאותו אדם אחר – אחרת השם דו-משמעי ונשארים בלי מיזוג
    same_name: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for pid in persons:
        if pid.startswith("~") and gender(pid) == "m":
            w = words_of(pid)
            if len(w) >= 3 and w[-1] not in given_names:
                same_name[tuple(w)].append(pid)
    remap: dict[str, str] = {}
    for w, pids in same_name.items():
        if not 1 <= surname_pages.get(w[-1], 0) <= max_surname_pages:
            continue
        cands = {k for k in by_first_last.get((w[0], w[-1]), []) if set(words_of(k)) < set(w)}
        if len(cands) != 1:
            continue
        art = next(iter(cands))
        others = [pid for pid in pids if conflicts_with_article(pid, art)]
        for pid in pids:
            if pid in others:
                continue
            ep = est_born(pid)
            ambiguous = any(not (ep and est_born(o) and abs(ep - est_born(o)) > 30) for o in others)
            if ambiguous:
                continue
            remap[pid] = art
            for (a, b, rel), e in edges.items():
                if pid in (a, b):
                    for ev in e["evidence"]:
                        ev["alias"] = True
    _apply_remap(persons, edges, remap)

    # ---- שלב ב: שני אזכורים בלי ערך (או קישור אדום) עם אותו שם ----
    parents_of, partners_of, children_of, neighbours, solid_fathers = maps()

    def fathers(pid: str) -> set[str]:
        return {q for q in parents_of.get(pid, ()) if gender(q) == "m"}

    def conflict(x: str, y: str) -> bool:
        if y in neighbours[x]:
            return True
        # אזכור שהופרד מערך בגלל סתירה – רק משבצת (קשר משפחתי) מצרפת אותו לאדם, לא שם בלבד
        if any("הופרד מהערך: פתרון-שם סותר" in persons[q].get("flags", []) for q in (x, y)):
            return True
        ex, ey = est_born(x), est_born(y)
        if ex and ey and abs(ex - ey) > 30:
            return True
        # סב ונכד: x הורה של מישהו ש-y ילד שלו (או להפך)
        if children_of[x] & parents_of[y] or children_of[y] & parents_of[x]:
            return True
        if children_of[x] & children_of[y]:
            return False                      # אותם ילדים – בוודאי אותו אדם
        fx, fy = fathers(x), fathers(y)
        if fx and fy and not (fx & fy):
            return True                      # שני אבות שונים
        return False

    # שתי רמות: שם מלא זהה עם שם אמצעי ("משה אורי בלוי" = "משה אורי בלויא") – גם בשם משפחה נפוץ, ובלי קשר לאזכורי
    # "משה בלוי" אחרים; שם בלי שם אמצעי מול שם עם שם אמצעי ("אריה הרטמן" / "אריה אברהם הרטמן") – רק בשם משפחה נדיר,
    # ורק כשאין אזכור שלישי באותו שם
    exact: dict[tuple[str, ...], list[str]] = defaultdict(list)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pid, p in persons.items():
        if not (pid.startswith("~") or not p.get("fetched")):
            continue
        w = words_of(pid)
        if len(w) < 2:
            continue
        groups[(w[0], w[-1])].append(pid)
        if len(w) >= 3:
            exact[tuple(w)].append(pid)
    pairs: list[tuple[str, str]] = []
    for w, ids in exact.items():
        if len(ids) == 2 and w[-1] not in given_names and surname_pages.get(w[-1], 0) >= 1:
            pairs.append((ids[0], ids[1]))
    for (first, surname), ids in groups.items():
        if surname in given_names or not 1 <= surname_pages.get(surname, 0) <= max_surname_pages:
            continue
        if len(ids) != 2 or not nested(words_of(ids[0]), words_of(ids[1])) or (ids[0], ids[1]) in pairs:
            continue
        pairs.append((ids[0], ids[1]))
    remap = {}
    for x, y in pairs:
        if x in remap or y in remap:
            continue
        gx, gy = gender(x), gender(y)
        if "f" in (gx, gy) or "m" not in (gx, gy):
            continue
        if conflict(x, y):
            continue
        # הנציג: קישור אדום לפני אזכור, ואז השם הארוך יותר, ואז מי שיש לו הורה
        def rank(pid: str):
            return (0 if not pid.startswith("~") else 1, -len(words_of(pid)), 0 if pid in parents_of else 1, pid)
        keep, drop = sorted((x, y), key=rank)
        remap[drop] = keep
        persons[keep]["flags"].append("מוזג לפי שם מלא")
    _apply_remap(persons, edges, remap)


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
    # אינדקסים חיים (סריקה של כל הקשרים לכל אדם הייתה איטית מאוד): בני זוג והורים לפי מזהה, מעל סף הביטחון
    spouse_map: dict[str, set[str]] = defaultdict(set)
    parent_map: dict[str, list[dict]] = defaultdict(list)
    for (a, b, rel), e in edges.items():
        if e["confidence"] < min_conf:
            continue
        if rel == "spouse":
            spouse_map[a].add(b); spouse_map[b].add(a)
        elif rel == "parent":
            parent_map[a].append(e)

    def add_inferred(a: str, b: str, relation: str, why: str, conf: float = 0.5):
        key = edge_key(a, b, relation)
        if key in edges or a == b:
            return
        e = edges[key] = {"a": key[0], "b": key[1], "relation": relation, "confidence": conf, "inferred": True,
                          "flags": ["inferred", "no_ref"], "side": None,
                          "evidence": [{"source_page": "", "text": why, "refs": [], "method": "inference", "pattern": "", "confidence": conf}]}
        if conf >= min_conf:
            if relation == "spouse":
                spouse_map[key[0]].add(key[1]); spouse_map[key[1]].add(key[0])
            elif relation == "parent":
                parent_map[key[0]].append(e)

    # אב ואם של אותו ילד – בני זוג ("נולד לאביו X ולאמו Y")
    parents_by_child: dict[str, list[str]] = defaultdict(list)
    for (a, b, rel), e in list(edges.items()):
        if rel == "parent" and e["confidence"] >= min_conf:
            parents_by_child[a].append(b)
    for child, pars in parents_by_child.items():
        fathers = [p for p in pars if persons[p]["gender"] == "m"]
        mothers = [p for p in pars if persons[p]["gender"] == "f"]
        if len(fathers) == 1 and len(mothers) == 1:
            add_inferred(fathers[0], mothers[0], "spouse", f"הוסק: {persons[fathers[0]]['name']} ו{persons[mothers[0]]['name']} הורי {persons[child]['name']}")
    # אחים חולקים הורים
    for key, e in list(edges.items()):
        if e["relation"] != "sibling" or e["confidence"] < min_conf:
            continue
        for x, y in ((e["a"], e["b"]), (e["b"], e["a"])):
            for pe in list(parent_map.get(x, [])):
                if pe["confidence"] < 0.7:
                    continue
                parent = pe["b"]
                pg = persons[parent]["gender"]
                has_same = any(persons[q["b"]]["gender"] == pg and pg for q in parent_map.get(y, []))
                if not has_same:
                    add_inferred(y, parent, "parent", f"הוסק: {persons[y]['name']} אח/ות של {persons[x]['name']} שהוא/היא ילד/ה של {persons[parent]['name']}")
    # חתן/כלה ↔ בת/בן: X (בנו של A) חתנו של B, ו-Y (בתו של B) כלתו של A – X ו-Y נשואים. וגם: לבתו של B אין
    # בן זוג ידוע ושם המשפחה שלה הוא שם המשפחה של X (שם נישואין: "בתו, רחל הרטמן").
    in_laws: dict[str, set[str]] = defaultdict(set)
    kids: dict[str, list[str]] = defaultdict(list)
    pars: dict[str, set[str]] = defaultdict(set)
    for (a, b, rel), e in list(edges.items()):
        if e["confidence"] < min_conf:
            continue
        if rel == "parent_in_law":
            in_laws[a].add(b)
        elif rel == "parent":
            kids[b].append(a); pars[a].add(b)

    def has_spouse(pid: str) -> bool:
        return bool(spouse_map.get(pid))

    def surname(pid: str) -> str:
        p = persons[pid]
        return (p.get("surname") or "") if not pid.startswith("~") else _surname_from_name(p["name"])

    for x, bs in list(in_laws.items()):
        if has_spouse(x):
            continue
        gx = persons[x]["gender"]
        for b in bs:
            cands = []
            for y in kids.get(b, []):
                if y == x or has_spouse(y) or (gx and persons[y]["gender"] == gx):
                    continue
                mutual = bool(pars.get(x)) and any(a in in_laws.get(y, ()) for a in pars[x])
                married_name = persons[y]["gender"] == "f" and surname(x) and surname(x) == surname(y) and y.startswith("~")
                if mutual or married_name:
                    cands.append((y, 0.6 if mutual else 0.55))
            if len(cands) == 1:
                y, conf = cands[0]
                add_inferred(x, y, "spouse", f"הוסק: {persons[x]['name']} חתן/כלה של {persons[b]['name']}, ו{persons[y]['name']} בת/בן שלו", conf=conf)
    # חם/חמות → הורה של בן/בת הזוג
    for key, e in list(edges.items()):
        if e["relation"] != "parent_in_law" or e["confidence"] < min_conf:
            continue
        person, in_law = e["a"], e["b"]
        known_spouses = sorted(spouse_map.get(person, ()))
        for sp in known_spouses:
            g = persons[in_law]["gender"]
            if not any(persons[q["b"]]["gender"] == g and g for q in parent_map.get(sp, [])):
                add_inferred(sp, in_law, "parent", f"הוסק: {persons[person]['name']} חתן/כלה של {persons[in_law]['name']} ונשוי/אה ל{persons[sp]['name']}")
        if not known_spouses:
            # "חתנו ישראל גולדברג" בלי שם הבת: יש בת (בלי שם ובלי ערך) שנשואה לו – חוליה בעץ בין החותן לחתן
            pg = persons[person]["gender"]
            cg = {"m": "f", "f": "m"}.get(pg)
            name = {"f": "בת", "m": "בן"}.get(cg, "בן/בת")
            vid = f"~{name}@{in_law}:inlaw:{person}"
            if vid not in persons:
                persons[vid] = {"id": vid, "title": None, "name": name, "linked": False, "fetched": False, "gender": cg,
                                "gender_votes": Counter(), "born": None, "died": None, "surname": "", "categories": [],
                                "family_categories": [], "flags": ["virtual"], "mentioned_in": set()}
            why = f"הוסק: {persons[person]['name']} חתן/כלה של {persons[in_law]['name']} – בן/בת בלי שם"
            add_inferred(vid, in_law, "parent", why)
            add_inferred(vid, person, "spouse", why)


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
    # אב שנפתר משם-תצוגה כשיש אב מפורש אחר – פתרון-השם הוא הטעות
    for child, es in by_child.items():
        fathers = [e for e in es if e["confidence"] >= 0.5 and persons[e["b"]]["gender"] == "m"]
        if len(fathers) < 2:
            continue
        solid = [e for e in fathers if not (e["evidence"] and all(ev.get("alias") for ev in e["evidence"]))]
        if solid and len(solid) < len(fathers):
            for e in fathers:
                if e not in solid:
                    e["flags"].append("אב לפי פתרון שם מול אב מפורש")
                    e["confidence"] = min(e["confidence"], 0.3)
    # אחים רחוקים בשנים – קישור לנכד או לסב באותו שם
    for e in edges.values():
        if e["relation"] == "sibling" and e["confidence"] >= 0.5:
            ya, yb = _year(persons[e["a"]]["born"]), _year(persons[e["b"]]["born"])
            if ya and yb and abs(ya - yb) > 45:
                e["flags"].append("אחים רחוקים בשנים")
                e["confidence"] = min(e["confidence"], 0.3)
    # נכד שנולד לפני ה"סב" – ההורה בעץ הוא בן דוד או נכד באותו שם
    kids_of: dict[str, list[str]] = defaultdict(list)
    for e in edges.values():
        if e["relation"] == "parent" and e["confidence"] >= 0.5:
            kids_of[e["b"]].append(e["a"])
    for child, es in by_child.items():
        for e in es:
            if e["confidence"] < 0.5:
                continue
            pb = _year(persons[e["b"]]["born"])
            if pb and any((_year(persons[c]["born"]) or 9999) < pb + 25 for c in kids_of.get(child, [])):
                e["flags"].append("נכד שנולד לפני הסב")
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
    # גם דרך בן הזוג: "אם אמו" – ההורה השני (אשת האב) הוא ילד של ה"הורה" הזה
    spouses_solid: dict[str, set[str]] = defaultdict(set)
    for e in edges.values():
        if e["relation"] == "spouse" and solid(e):
            spouses_solid[e["a"]].add(e["b"])
            spouses_solid[e["b"]].add(e["a"])
    for child, es in by_child.items():
        for e in es:
            if e["confidence"] < 0.5 or "סב שנרשם כהורה" in e["flags"]:
                continue
            g = e["b"]
            for p in parents_of.get(child, ()):
                if p != g and any(g in parents_of.get(sp, ()) for sp in spouses_solid.get(p, ()) if sp != g):
                    e["flags"].append("סב שנרשם כהורה")
                    e["confidence"] = min(e["confidence"], 0.3)
                    break
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
