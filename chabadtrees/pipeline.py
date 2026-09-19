"""שלבי הצינור: איסוף כותרות, משיכת דפים (עמידה בשגיאות, ניתנת להמשך), חילוץ, גרף, עצים.

עקרון: שום דף לא נעלם בשקט. כל כשל נרשם ב-fetch_state.json (עם השגיאה ומספר הניסיונות),
מנוסה שוב בסוף הריצה ובריצה הבאה, והפקודה מחזירה קוד יציאה 2 אם נשארו כשלים.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
from collections import Counter

from . import wikitext as wt
from .api import ApiError, MediaWikiClient
from .extract import Extractor, build_aliases
from .persons import is_person_page
from .graph import build_graph, parents_of, year_of
from .layout import TreeBuilder, TreeNode, layout_forest, ancestor_chart
from .render import tree_page, ahnentafel_wikitext, chart_wikitext, slug

log = logging.getLogger(__name__)


class Store:
    def __init__(self, data_dir: str):
        self.dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def load(self, name: str, default):
        p = self.path(name)
        if not os.path.exists(p):
            return default
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, name: str, obj) -> None:
        tmp = self.path(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path(name))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- איסוף כותרות
def collect_person_titles(client: MediaWikiClient, cfg: dict, store: Store) -> list[str]:
    """עובר על עץ הקטגוריות מהשורשים. קטגוריה שנכשלה נרשמת ומנוסה שוב."""
    state = store.load("fetch_state.json", {})
    cats_done: dict[str, list[str]] = state.get("categories_done", {})
    cats_failed: dict[str, dict] = state.get("categories_failed", {})
    exclude = set(cfg.get("exclude_categories", []))
    queue = [(c, 0) for c in cfg["person_root_categories"]]
    seen_cats = set(exclude)
    titles: dict[str, None] = {}
    done_count = 0
    while queue:
        cat, depth = queue.pop(0)
        if cat in seen_cats:
            continue
        seen_cats.add(cat)
        done_count += 1
        if done_count % 25 == 0:
            log.info("קטגוריות: %d נסרקו, %d בתור, %d כותרות עד כה", done_count, len(queue), len(titles))
        if cat in cats_done:
            members = cats_done[cat]
        else:
            try:
                members = [f"{m.get('ns', 0)}:{m['title']}" for m in client.category_members(cat)]
                cats_done[cat] = members
                cats_failed.pop(cat, None)
            except ApiError as exc:
                cats_failed[cat] = {"error": str(exc), "attempts": cats_failed.get(cat, {}).get("attempts", 0) + 1, "at": now()}
                log.error("קטגוריה %s נכשלה (%s) – תנוסה שוב בריצה הבאה", cat, exc)
                continue
            finally:
                state["categories_done"], state["categories_failed"] = cats_done, cats_failed
                store.save("fetch_state.json", state)
        for m in members:
            ns, title = m.split(":", 1)
            if ns == "14":
                sub = title.split(":", 1)[-1]
                if depth + 1 <= cfg.get("max_category_depth", 8) and sub not in seen_cats:
                    queue.append((sub, depth + 1))
            elif ns == "0":
                titles[title] = None
    state["titles"] = list(titles)
    state["titles_collected_at"] = now()
    store.save("fetch_state.json", state)
    if cats_failed:
        log.error("%d קטגוריות נכשלו: %s", len(cats_failed), ", ".join(list(cats_failed)[:10]))
    return list(titles)


# --------------------------------------------------------------------------- משיכת דפים
def fetch_pages(client: MediaWikiClient, cfg: dict, store: Store, refresh: bool = False, limit: int | None = None,
                titles: list[str] | None = None, retry_sleep: float = 30.0) -> dict:
    state = store.load("fetch_state.json", {})
    pages: dict[str, dict] = store.load("pages.json", {})
    if titles is None:
        titles = state.get("titles")
        if not titles and pages and not refresh:
            # אין רשימת כותרות שמורה אבל יש מטמון דפים: משתמשים בו במקום לסרוק שוב את הקטגוריות
            titles = list(pages)
            log.warning("אין רשימת כותרות שמורה; משתמשים ב-%d הדפים שבמטמון (להרצה מלאה מחדש: --recollect)", len(titles))
        if not titles:
            titles = collect_person_titles(client, cfg, store)
            state = store.load("fetch_state.json", {})     # הסריקה שמרה titles/categories – לא לדרוס אותם
    failed: dict[str, dict] = state.get("failed", {})
    missing: dict[str, str] = state.get("missing", {})
    if limit:
        titles = titles[:limit]
    # כשלים מריצה קודמת נכנסים ראשונים לתור
    pending = [t for t in failed if t in titles or True]
    pending += [t for t in titles if t not in pending and (refresh or (t not in pages and t not in missing))]
    log.info("למשיכה: %d דפים (%d כשלים קודמים), %d כבר במטמון", len(pending), len(failed), len(pages))

    def run_pass(queue: list[str], label: str) -> list[str]:
        still: list[str] = []
        for start in range(0, len(queue), 50):
            batch = queue[start:start + 50]
            try:
                got, resolved = client.fetch_batch(batch)
            except ApiError as exc:
                for t in batch:
                    failed[t] = {"error": str(exc), "attempts": failed.get(t, {}).get("attempts", 0) + 1, "at": now()}
                still += batch
                log.error("[%s] קבוצה של %d דפים נכשלה: %s – נרשמה לניסיון חוזר", label, len(batch), exc)
                state["failed"] = failed
                store.save("fetch_state.json", state)
                continue
            by_title = {p["title"]: p for p in got}
            for t in batch:
                final = resolved.get(t)
                if final and final in by_title:
                    page = dict(by_title[final])
                    page["fetched_at"] = now()
                    page["requested_title"] = t
                    pages[t] = page
                    if final != t:
                        pages[t]["redirect_to"] = final
                    failed.pop(t, None)
                    missing.pop(t, None)
                else:
                    missing[t] = now()
                    failed.pop(t, None)
            store.save("pages.json", pages)
            state["failed"], state["missing"] = failed, missing
            store.save("fetch_state.json", state)
            log.info("[%s] %d/%d (%d בקשות, %d האטות)", label, min(start + 50, len(queue)), len(queue), client.requests_made, client.rate_limited)
        return still

    still = run_pass(pending, "מעבר 1")
    if still:
        log.warning("%d דפים נכשלו במעבר הראשון; ממתין %.0f שניות ומנסה שוב", len(still), retry_sleep)
        time.sleep(retry_sleep)
        still = run_pass(still, "מעבר 2")
    state["last_fetch"] = now()
    state["failed"] = failed
    store.save("fetch_state.json", state)
    summary = {"fetched": len(pages), "missing": len(missing), "failed": len(failed), "requests": client.requests_made,
               "rate_limited": client.rate_limited}
    if failed:
        log.error("נשארו %d דפים שלא נמשכו. הריצו שוב `fetch` – הם ינוסו ראשונים. רשימה: data/fetch_state.json → failed", len(failed))
    return summary


# --------------------------------------------------------------------------- חילוץ
def extract_all(cfg: dict, store: Store, pages: dict | None = None, use_llm: bool = False) -> dict:
    all_pages = pages if pages is not None else store.load("pages.json", {})
    # עץ הקטגוריות "אישים" מכיל גם ספרים, ניגונים, חסידויות ואירועים – מסננים לדפי אישים בלבד
    pages = {t: p for t, p in all_pages.items() if is_person_page(p)}
    log.info("%d דפי אישים מתוך %d דפים שנמשכו", len(pages), len(all_pages))
    known = {p["title"] for p in pages.values()} | set(pages)
    extra: dict[str, str] = {}
    for page in pages.values():
        for tpl in wt.find_templates(wt.strip_comments(page.get("wikitext") or "")):
            for key in ("כינוי", "כינויים", "שם נוסף", "ידוע גם בשם"):
                val = tpl["params"].get(key)
                if val:
                    for alias in re.split(r"<br\s*/?>|,|;|/", wt.plain(val)):
                        if alias.strip():
                            extra[alias.strip()] = page["title"]
    # מילים שכיחות בקורפוס (ב-8+ דפים) שאינן מילות-שם – עוצרות שמות לא-מקושרים ("חנה מפעילה פעילות")
    df: Counter = Counter()
    for page in all_pages.values():
        df.update(set(re.findall(r"[א-ת][א-ת'\"\-]{1,}", wt.plain(wt.strip_templates(wt.strip_comments(page.get("wikitext") or ""))))))
    common = {w for w, n in df.items() if n >= 8}
    ex = Extractor(cfg, known_persons=known, aliases=build_aliases(known, extra), common_words=common)
    relations, infos = [], {}
    for title, page in pages.items():
        final = page.get("title", title)
        rels, info = ex.extract(final, page.get("wikitext") or "")
        infos[final] = info
        relations += [r.to_dict() for r in rels]
    if use_llm:
        from .llm import run_llm
        relations += run_llm(cfg, store, pages, known)
    result = {"pages": infos, "relations": relations, "non_person_pages": sorted(set(all_pages) - set(pages)),
              "template_names": dict(sorted(ex.template_names.items(), key=lambda kv: -kv[1])[:40]),
              "unknown_family_fields": ex.unknown_fields, "extracted_at": now()}
    store.save("relations.json", result)
    log.info("חולצו %d קשרים מ-%d דפים", len(relations), len(pages))
    return result


def resolve_links(client: MediaWikiClient, cfg: dict, store: Store, extracted: dict | None = None) -> dict:
    """כל יעד קישור שהופיע בקשרים ואינו דף שנמשך – נשאל את האתר: הפניה? קיים? ונשמרת מפה לכותרת הקנונית.

    כך "[[לוי יצחק שניאורסון (אב הרבי)]]" ו-"[[לוי יצחק שניאורסון (אב אדמו\"ר שליט\"א)]]" מתמזגים לאדם אחד,
    והתיבה בעץ מקשרת לשם הערך האמיתי.
    """
    extracted = extracted or store.load("relations.json", {})
    pages = store.load("pages.json", {})
    resolution: dict[str, dict] = store.load("link_resolution.json", {})
    # הפניות שכבר ידועות מהמשיכה
    for req, page in pages.items():
        if page.get("redirect_to") and page["redirect_to"] != req:
            resolution[req] = {"to": page["redirect_to"], "exists": True, "redirect": True}
    known = {p.get("title", t) for t, p in pages.items()} | set(pages)
    targets = set()
    for rel in extracted["relations"]:
        for side in ("person", "relative"):
            t = rel[side]
            if not t.startswith("~") and t not in known and t not in resolution:
                targets.add(t)
    targets = sorted(targets)
    log.info("פתירת %d יעדי קישור שאינם דפים שנמשכו", len(targets))
    failed = []
    for start in range(0, len(targets), 50):
        batch = targets[start:start + 50]
        try:
            got, resolved = client.fetch_batch(batch)
        except ApiError as exc:
            log.error("פתירת קישורים: קבוצה נכשלה (%s) – תנוסה שוב בריצה הבאה", exc)
            failed += batch
            continue
        for t in batch:
            final = resolved.get(t)
            if final:
                resolution[t] = {"to": final, "exists": True, "redirect": final != t}
            else:
                resolution[t] = {"to": t, "exists": False, "redirect": False}
        store.save("link_resolution.json", resolution)
    if failed:
        log.error("%d יעדי קישור לא נפתרו (יטופלו בריצה הבאה)", len(failed))
    return resolution


def apply_resolution(extracted: dict, resolution: dict, known: set[str] | None = None, fetched: set[str] | None = None) -> dict:
    """מחליף יעדי קישור בכותרת הקנונית ומסמן אנשים שאין להם ערך.

    known: כותרות דפי האישים. קישור לדף שקיים באתר אבל אינו דף אישיות (מקום, תאריך, מוסד) ואין לו תואר לפניו –
    אינו אדם, והקשר מושמט.
    """
    if not resolution:
        return extracted
    def canon(t: str) -> str:
        seen = set()
        while t in resolution and resolution[t].get("redirect") and t not in seen:
            seen.add(t)
            t = resolution[t]["to"]
        return t
    kept, dropped = [], 0
    for rel in extracted["relations"]:
        ok = True
        for side in ("person", "relative"):
            t = rel[side]
            if t.startswith("~"):
                continue
            c = canon(t)
            rel[side] = c
            info = resolution.get(t) or resolution.get(c)
            if info is not None:
                rel[f"{side}_has_article"] = bool(info.get("exists", True))
                exists = bool(info.get("exists", True))
            else:
                exists = bool(fetched) and c in fetched
            if known and exists and c not in known and not rel.get(f"{side}_gender"):
                ok = False
        if ok:
            kept.append(rel)
        else:
            dropped += 1
    if dropped:
        log.info("הושמטו %d קשרים לדפים קיימים שאינם דפי אישים", dropped)
    extracted["relations"] = kept
    return extracted


def graph_step(cfg: dict, store: Store, extracted: dict | None = None) -> dict:
    extracted = extracted or store.load("relations.json", {})
    pages_all = store.load("pages.json", {})
    extracted = apply_resolution(extracted, store.load("link_resolution.json", {}), known=set(extracted.get("pages") or ()),
                                 fetched={p.get("title", t) for t, p in pages_all.items()} | set(pages_all))
    graph = build_graph(extracted["pages"], extracted["relations"], cfg)
    store.save("graph.json", graph)
    log.info("גרף: %d אנשים, %d קשרים, %d משפחות", len(graph["persons"]), len(graph["edges"]), len(graph["families"]))
    return graph


# --------------------------------------------------------------------------- עצים
def family_specs(graph: dict, cfg: dict) -> list[dict]:
    """מגדיר אילו עצים לבנות: לפי קטגוריות "משפחת X", ואחר כך רכיבים שלא כוסו."""
    persons = graph["persons"]
    specs = []
    by_cat: dict[str, set[str]] = {}
    for pid, p in persons.items():
        for c in p.get("family_categories", []):
            by_cat.setdefault(c, set()).add(pid)
    covered: set[str] = set()
    for cat, members in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        if len(members) < cfg.get("min_family_size", 4):
            continue
        specs.append({"kind": "category", "label": cat, "members": set(members), "family_categories": [cat]})
        covered |= members
    for fam in graph["families"]:
        arts = [m for m in fam["members"] if persons[m]["fetched"]]
        if len(arts) < cfg.get("min_family_size", 4):
            continue
        if len(set(arts) & covered) / max(len(arts), 1) >= 0.5:
            continue
        specs.append({"kind": "component", "label": fam["label"], "members": set(fam["members"]),
                      "family_categories": list(fam.get("family_categories", {}))})
    return specs


def family_surnames(graph: dict, spec: dict) -> set[str]:
    names = set()
    label = spec.get("label", "")
    for pref in ("משפחת ", "עץ משפחת "):
        if label.startswith(pref):
            names.add(label[len(pref):].strip())
    from collections import Counter
    cnt = Counter(graph["persons"][m]["surname"] for m in spec["members"] if m in graph["persons"] and graph["persons"][m]["surname"])
    names |= {sn for sn, n in cnt.most_common(3) if n >= 2}
    return names


def married_in(graph: dict, members: set[str], surnames: set[str], tb: TreeBuilder) -> set[str]:
    """בני משפחה שנכנסו אליה בנישואין: אין להם הורה בקבוצה, יש להם בן זוג בקבוצה, ושם המשפחה שונה."""
    out = set()
    for pid in members:
        p = graph["persons"].get(pid, {})
        if any(par in members for par in tb._parents.get(pid, [])):
            continue
        sps = [sp for sp in tb._spouses.get(pid, []) if sp in members]
        if not sps:
            continue
        spouse_is_descendant = any(any(par in members for par in tb._parents.get(sp, [])) for sp in sps)
        if p.get("surname") and p["surname"] in surnames and not spouse_is_descendant:
            continue
        out.add(pid)
    return out


def _with_unlinked_relatives(graph: dict, members: set[str], exclude_parents_of: set[str] = frozenset()) -> set[str]:
    out = set(members)
    for e in graph["edges"]:
        if e["relation"] not in ("parent", "spouse") or e["confidence"] < 0.5:
            continue
        a, b = e["a"], e["b"]
        if e["relation"] == "parent" and a in exclude_parents_of:
            continue
        if a in members and b.startswith("~"):
            out.add(b)
        if b in members and a.startswith("~"):
            out.add(a)
    return out


def choose_roots(graph: dict, members: set[str], cfg: dict, tb: TreeBuilder) -> list[str]:
    """שורשים: בני משפחה בלי הורים בקבוצה, שהם "הבעלים" של לפחות ילד אחד בעץ."""
    persons = graph["persons"]
    min_conf = cfg.get("min_confidence", 0.5)

    owner_of = tb.owner_of_in(members)

    roots = []
    mi = getattr(tb, "married_in", set())
    for pid in members:
        if pid in mi:
            continue
        pars = [e["b"] for e in parents_of(graph["edges"], pid) if e["confidence"] >= min_conf and e["b"] in members]
        if pars:
            continue
        owned = [c for c in tb._children.get(pid, []) if c in members and owner_of(c) == pid]
        if not owned:
            continue
        roots.append(pid)
    roots.sort(key=lambda p: (0 if persons[p].get("fetched") else 1, 0 if persons[p].get("gender") == "m" else 1, year_of(persons[p]) or 9999, p))
    chosen: list[str] = []
    for r in roots:
        if any(sp in chosen for sp in tb._spouses.get(r, [])):
            continue
        chosen.append(r)
    return chosen


def _drop_married_in_roots(forest: list[TreeNode], graph: dict) -> list[TreeNode]:
    """עץ קטן שכל מה שבו הוא הורה של בן/בת זוג שכבר מופיע/ה בעץ אחר – מיותר."""
    spouses_elsewhere: dict[str, set[str]] = {}
    for i, t in enumerate(forest):
        spouses_elsewhere[i] = {m.spouse for n in t.all_nodes() for m in n.marriages if m.spouse}
    keep = []
    for i, t in enumerate(forest):
        nodes = list(t.all_nodes())
        others = set().union(*(v for j, v in spouses_elsewhere.items() if j != i)) if len(forest) > 1 else set()
        non_root = [n.person for n in nodes if n is not t]
        if non_root and all(p in others for p in non_root) and not graph["persons"][t.person].get("fetched"):
            continue
        if non_root and all(p in others for p in non_root) and all(not m.children for n in nodes if n is not t for m in n.marriages):
            continue
        keep.append(t)
    return keep


def build_trees(graph: dict, cfg: dict, only_label: str | None = None, root: str | None = None,
                max_depth: int | None = None, max_nodes: int | None = None, existing: dict | None = None) -> list[dict]:
    tb = TreeBuilder(graph, cfg)
    max_nodes = max_nodes or cfg.get("max_tree_nodes", 70)
    out = []
    if root:
        if root not in graph["persons"]:
            raise SystemExit(f"האדם '{root}' לא נמצא בגרף")
        tree = tb.build(root, None, None, max_nodes, max_depth)
        out.append(_tree_record(graph, cfg, f"עץ משפחת {wt.display_name(root)}", [tree], {"kind": "root", "label": root, "family_categories": []}, existing))
        return out
    for spec in family_specs(graph, cfg):
        if only_label and only_label not in spec["label"]:
            continue
        surnames = family_surnames(graph, spec)
        tb.bloodline_surnames = surnames
        mi = married_in(graph, spec["members"], surnames, tb)
        tb.married_in = mi
        members = _with_unlinked_relatives(graph, spec["members"], exclude_parents_of=mi)
        # הורים (מקושרים) של מי שהתחתן לתוך המשפחה אינם חלק מהעץ
        for pid in list(members):
            if pid in mi:
                for par in tb._parents.get(pid, []):
                    if par in members and par not in spec["members"]:
                        members.discard(par)
        roots = choose_roots(graph, members, cfg, tb)
        if not roots:
            continue
        budget = max_nodes
        forest: list[TreeNode] = []
        for r in roots[:8]:
            if budget <= 1:
                break
            tree = tb.build(r, members, members, budget, max_depth)
            forest.append(tree)
            budget -= sum(1 for _ in tree.all_nodes())
        forest = _drop_married_in_roots(forest, graph)
        shown = {n.person for t in forest for n in t.all_nodes()}
        if len(shown) < 2:
            continue
        out.append(_tree_record(graph, cfg, f"עץ {spec['label']}" if spec["label"].startswith("משפחת") else f"עץ משפחת {spec['label']}", forest, spec, existing))
    return out


def _tree_record(graph: dict, cfg: dict, title: str, forest: list[TreeNode], spec: dict, existing: dict | None = None) -> dict:
    box_ids: dict = {}
    chart = layout_forest(forest, box_ids, spouse_style=cfg["chart"].get("spouse_style", "inline"),
                          root_spouse_boxes=cfg["chart"].get("root_spouse_boxes", True))
    chart.self_tree = "תבנית:" + title
    shown = [n.person for t in forest for n in t.all_nodes()]
    spouses = [m.spouse for t in forest for n in t.all_nodes() for m in n.marriages if m.spouse]
    def disp(pid: str) -> str:
        return graph["persons"].get(pid, {}).get("name") or wt.display_name(pid.split("@", 1)[0].lstrip("~"))

    notes = [f"שורשים: {', '.join(disp(t.person) for t in forest)}",
             f"אנשים בעץ: {len(set(shown))} בני משפחה + {len(set(spouses))} בני זוג"]
    truncated = [n.person for t in forest for n in t.all_nodes() if n.truncated]
    if truncated:
        notes.append("ענפים שנקטעו בגלל מגבלת גודל: " + ", ".join(disp(p) for p in truncated[:8]))
    wikitext = tree_page(title, chart, graph, cfg, spec, notes, existing=existing)
    return {"title": title, "slug": slug(title), "kind": spec["kind"], "label": spec["label"], "roots": [t.person for t in forest],
            "members_shown": sorted(set(shown)), "spouses_shown": sorted(set(spouses)), "truncated": truncated,
            "notes": notes, "wikitext": wikitext, "chart": chart, "width": chart.width, "height": chart.height}


def build_ancestor_trees(graph: dict, cfg: dict, for_person: str | None = None, generations: int = 3, min_known: int = 3) -> list[dict]:
    out = []
    targets = [for_person] if for_person else [pid for pid, p in graph["persons"].items() if p["fetched"]]
    for pid in targets:
        if pid not in graph["persons"]:
            raise SystemExit(f"האדם '{pid}' לא נמצא בגרף")
        box_ids: dict = {}
        chart, slots = ancestor_chart(graph, pid, generations, box_ids)
        known = [k for k, v in slots.items() if v and k != "self"]
        if len(known) < min_known and not for_person:
            continue
        title = f"אבות {wt.display_name(pid)}"
        wikitext = ("<!-- עץ אבות שנבנה אוטומטית. גרסה א: תבנית הגריד הכללית. גרסה ב: תבנית \"עץ משפחה לאדם אחד\" (שמות הפרמטרים לכיול) -->\n"
                    + chart_wikitext(chart, graph, cfg) + "\n\n<!-- גרסה ב -->\n" + ahnentafel_wikitext(slots, graph, cfg) + "\n")
        out.append({"title": title, "slug": slug(title), "kind": "ancestors", "label": pid, "roots": [pid], "members_shown": known,
                    "spouses_shown": [], "truncated": [], "notes": [f"אבות ידועים: {len(known)} מתוך {2 ** (generations + 1) - 2}"],
                    "wikitext": wikitext, "chart": chart, "slots": slots, "width": chart.width, "height": chart.height})
    return out
