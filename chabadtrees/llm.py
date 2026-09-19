"""חילוץ קשרי משפחה בעזרת Claude (אופציונלי) – למשפטים שהדפוסים מפספסים.

מופעל עם ``extract --llm``. דורש את ה-SDK (``pip install anthropic``) ומפתח API (או פרופיל ``ant auth login``).
כל תשובה נשמרת ב-data/llm_cache.json לפי כותרת+גרסת הדף, כך שהרצה חוזרת לא משלמת פעמיים,
וכשל בדף אחד נרשם ומנוסה שוב בריצה הבאה – לא נעלם.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import wikitext as wt
from .extract import Relation

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "שם האדם כפי שמופיע בטקסט, או כותרת הערך אם זה נושא הערך"},
                    "person_link": {"type": ["string", "null"], "description": "יעד הקישור הפנימי [[...]] של האדם, אם קיים"},
                    "relative": {"type": "string"},
                    "relative_link": {"type": ["string", "null"]},
                    "relation": {"type": "string", "enum": ["parent", "spouse", "sibling", "grandparent", "parent_in_law", "uncle"]},
                    "relative_gender": {"type": ["string", "null"], "enum": ["m", "f", None]},
                    "evidence": {"type": "string", "description": "המשפט המדויק מהטקסט"},
                    "confidence": {"type": "number"},
                },
                "required": ["person", "person_link", "relative", "relative_link", "relation", "relative_gender", "evidence", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relations"],
    "additionalProperties": False,
}

SYSTEM = """אתה מחלץ קשרי משפחה מערך אנציקלופדי בעברית (חב"דפדיה). הטקסט הוא ויקיטקסט; קישורים פנימיים כתובים [[יעד|תצוגה]].
החזר רק קשרים שנאמרים במפורש בטקסט. הצורות הקנוניות:
parent – relative הוא הורה של person; spouse; sibling; grandparent – relative הוא סב/סבתא של person;
parent_in_law – relative הוא חם/חמות של person (person נשוי לילד/ה של relative); uncle – relative הוא דוד/ה של person.
"בנו של X" = X הורה. "חתנו של X" = X חם. "אביו של X" = הנושא הורה של X. נושא הערך מזוהה לפי הכותרת שניתנת לך.
לכל קשר צטט את המשפט המדויק. אל תסיק קשרים שלא כתובים. שמות ללא קישור – העתק כפי שהם, בלי תארים (הרב, ר', מרת)."""


def _client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("חסר ה-SDK: pip install anthropic") from exc
    return anthropic.Anthropic()


def extract_page(client, cfg: dict, title: str, text: str) -> list[dict]:
    body = wt.strip_comments(text)
    body = body[:60000]
    lcfg = cfg.get("llm", {})
    kwargs = dict(
        model=lcfg.get("model", "claude-opus-5"),
        max_tokens=lcfg.get("max_tokens", 16000),
        system=SYSTEM,
        messages=[{"role": "user", "content": f"כותרת הערך: {title}\n\nויקיטקסט:\n{body}"}],
        output_config={"effort": lcfg.get("effort", "medium"), "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    try:
        response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except TypeError:  # SDK ישן בלי fallbacks
        response = client.messages.create(**kwargs)
    if response.stop_reason == "refusal":
        raise RuntimeError("המודל סירב לבקשה")
    text_block = next(b for b in response.content if b.type == "text")
    return json.loads(text_block.text).get("relations", [])


def run_llm(cfg: dict, store, pages: dict, known: set[str]) -> list[dict]:
    """מריץ על כל הדפים (עם מטמון), מחזיר רשימת Relation-dicts. כשלים נרשמים ב-llm_failed."""
    client = _client()
    cache: dict[str, dict] = store.load("llm_cache.json", {})
    failed: dict[str, str] = store.load("llm_failed.json", {})
    todo = [(t, p) for t, p in pages.items() if f"{t}@{p.get('revid')}" not in cache]
    log.info("LLM: %d דפים לעיבוד, %d במטמון", len(todo), len(cache))
    chars = sum(len(p.get("wikitext") or "") for _t, p in todo)
    log.info("הערכת גודל קלט: ~%d טוקנים", chars // 3)

    def work(item):
        title, page = item
        key = f"{title}@{page.get('revid')}"
        for attempt in range(4):
            try:
                cache[key] = {"title": page.get("title", title), "relations": extract_page(client, cfg, page.get("title", title), page.get("wikitext") or "")}
                failed.pop(title, None)
                return
            except Exception as exc:  # noqa: BLE001
                wait = 10 * (2 ** attempt)
                log.warning("LLM נכשל על %s (%s), ניסיון חוזר בעוד %ds", title, exc, wait)
                time.sleep(wait)
        failed[title] = "נכשל אחרי 4 ניסיונות"

    with ThreadPoolExecutor(max_workers=cfg.get("llm", {}).get("concurrency", 4)) as pool:
        for i, _ in enumerate(pool.map(work, todo), 1):
            if i % 20 == 0:
                store.save("llm_cache.json", cache)
                store.save("llm_failed.json", failed)
                log.info("LLM: %d/%d", i, len(todo))
    store.save("llm_cache.json", cache)
    store.save("llm_failed.json", failed)
    if failed:
        log.error("LLM: %d דפים נכשלו ויינוסו שוב בריצה הבאה (data/llm_failed.json)", len(failed))

    out: list[dict] = []
    for entry in cache.values():
        title = entry["title"]
        for r in entry["relations"]:
            person = r.get("person_link") or (title if r.get("person") in (title, wt.display_name(title)) else "~" + r["person"])
            relative = r.get("relative_link") or "~" + r["relative"]
            rel = Relation(person=person, relative=relative, relation=r["relation"], source_page=title, evidence=r.get("evidence", ""),
                           refs=[], method="llm", pattern="llm", confidence=min(max(float(r.get("confidence", 0.6)), 0.2), 0.85),
                           relative_gender=r.get("relative_gender"), person_has_article=(person in known) or person == title,
                           relative_has_article=relative in known, person_name=wt.display_name(person.lstrip("~")),
                           relative_name=wt.display_name(relative.lstrip("~")))
            out.append(rel.to_dict())
    return out
