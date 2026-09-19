"""איתור עצי משפחה שכבר קיימים בחב"דפדיה, ובדיקת כיסוי מול המשפחות שנבנו."""
from __future__ import annotations

import datetime as dt
import logging
import re

from . import wikitext as wt
from .api import MediaWikiClient

log = logging.getLogger(__name__)


def find_existing_trees(client: MediaWikiClient, config: dict) -> dict:
    trees: dict[str, dict] = {}

    def note(title: str, source: str, ns: int | None = None):
        t = trees.setdefault(title, {"title": title, "ns": ns, "sources": [], "persons": [], "family_hint": family_hint(title),
                                     "embedded_in": []})
        if source not in t["sources"]:
            t["sources"].append(source)
        if ns is not None:
            t["ns"] = ns

    # 1. קטגוריית עצי המשפחה
    for item in client.category_members(config["tree_category"], cmtype="page"):
        note(item["title"], "category", item.get("ns"))
    # 2. דפים שמשבצים את תבניות העץ הכלליות
    pages_with_template: dict[str, list[str]] = {}
    for tpl in config["tree_templates"]:
        for item in client.embedded_in(tpl):
            pages_with_template.setdefault(item["title"], []).append(tpl)
            if item.get("ns") == 10 or any(item["title"].split(":", 1)[-1].startswith(p) for p in config["tree_title_prefixes"]):
                note(item["title"], f"embeds:{tpl}", item.get("ns"))
    # 3. חיפוש לפי תחילית כותרת
    for prefix in config["tree_title_prefixes"]:
        for item in client.prefix_search(prefix):
            note(item["title"], "prefix", item.get("ns"))
        for item in client.prefix_search("תבנית:" + prefix, namespace="10"):
            note(item["title"], "prefix", 10)
    # 4. חיפוש טקסט חופשי
    try:
        for item in client.search('"עץ משפחת"'):
            note(item["title"], "search", item.get("ns"))
    except Exception as exc:  # חיפוש טקסט לא תמיד זמין
        log.warning("חיפוש טקסט נכשל: %s", exc)

    # תוכן העצים: אילו אנשים מופיעים בהם, ואילו דפים מציגים אותם
    titles = sorted(trees)
    for page in client.get_pages(titles=titles):
        t = trees.get(page["title"])
        if t is None:
            continue
        text = wt.normalize_quotes(wt.strip_comments(page.get("wikitext") or ""))
        t["persons"] = sorted({target for target, _d, _s in wt.wikilinks(text)})
        t["uses_templates"] = sorted({tpl["name"] for tpl in wt.find_templates(text, nested=True)
                                      if any(tpl["name"].startswith(x) for x in config["tree_templates"])})
    for title in titles:
        if trees[title].get("ns") == 10:
            trees[title]["embedded_in"] = sorted(item["title"] for item in client.embedded_in(title))

    person_to_trees: dict[str, list[str]] = {}
    for t in trees.values():
        for p in t["persons"]:
            person_to_trees.setdefault(p, []).append(t["title"])
    ahnentafel_pages = sorted(p for p, tpls in pages_with_template.items()
                              if config["ahnentafel"]["template"] in tpls)
    return {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "trees": [trees[t] for t in titles], "person_to_trees": person_to_trees,
            "pages_with_tree_templates": pages_with_template, "pages_with_ahnentafel": ahnentafel_pages}


def family_hint(title: str) -> str:
    t = title.split(":", 1)[-1]
    t = re.sub(r"^(עץ משפחת|עץ משפחה של|עץ משפחה -|עץ משפחה –|עץ משפחה)\s*", "", t).strip()
    t = re.sub(r"/.*$", "", t)
    return t


def coverage(graph: dict, existing: dict, config: dict) -> list[dict]:
    """לכל משפחה בגרף: האם יש כבר עץ שמכסה אותה, ובאיזו מידה."""
    ratio_needed = config.get("existing_cover_ratio", 0.5)
    out = []
    trees = existing.get("trees", [])
    for fam in graph["families"]:
        members = [m for m in fam["members"] if not m.startswith("~")]
        best = None
        for t in trees:
            covered = sorted(set(members) & set(t.get("persons", [])))
            hint_match = any(t["family_hint"] and (t["family_hint"] == s or t["family_hint"] in fam["label"]) for s in fam["surnames"] + [fam["label"].replace("משפחת ", "")])
            score = (len(covered) / len(members)) if members else 0
            if hint_match:
                score = max(score, 0.99 if covered else 0.6)
            if best is None or score > best["score"]:
                best = {"tree": t["title"], "score": round(score, 2), "covered": covered, "hint_match": hint_match}
        status = "none"
        if best and best["score"] >= ratio_needed:
            status = "exists"
        elif best and best["score"] > 0:
            status = "partial"
        out.append({"family": fam["id"], "label": fam["label"], "articles": fam["articles"], "status": status, "best": best})
    return out
