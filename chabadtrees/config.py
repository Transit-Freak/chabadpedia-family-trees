"""הגדרות ברירת המחדל של הצינור.

כל מפתח כאן אפשר לדרוס בקובץ ``config.json`` בתיקיית העבודה (מיזוג רדוד למפתחות
העליונים, ומיזוג מלא למילונים ``infobox_fields``, ``chart`` ו-``ahnentafel``).
הערכים שמסומנים "לכיול" הם ניחוש מושכל שצריך לאמת מול האתר החי בהרצה הראשונה
(``python -m chabadtrees calibrate`` מדפיס את תיעוד התבניות ואת שדות תבניות האישים הנפוצים).
"""
from __future__ import annotations

import copy
import json
import os

DEFAULTS: dict = {
    # --- גישה לאתר ---
    "api_url": "https://chabadpedia.co.il/api.php",
    "site_url": "https://chabadpedia.co.il/index.php/",
    "user_agent": "ChabadpediaFamilyTrees/0.1 (https://github.com/Transit-Freak/chabadpedia-family-trees)",
    "rate_limit_seconds": 1.0,
    "maxlag": 5,
    "http_timeout": 60,
    # --- אילו דפים הם דפי אישים ---
    "person_root_categories": ["אישים"],
    "exclude_categories": ["אישים בתנ\"ך", "עצי משפחה"],
    "max_category_depth": 8,
    "families_root_category": "משפחות",
    "family_category_prefixes": ["משפחת "],
    # --- איתור עצים קיימים ---
    "tree_category": "עצי משפחה",
    "tree_templates": ["עץ משפחה", "עץ משפחה לאדם אחד", "עץ אדמו\"רי חב\"ד"],
    "tree_title_prefixes": ["עץ משפחת", "עץ משפחה"],
    "existing_cover_ratio": 0.5,
    # --- שדות משפחה בתבניות האישים (לכיול) ---
    "infobox_fields": {
        "father": ["אב", "אבא", "אביו", "אביה", "שם האב"],
        "mother": ["אם", "אמא", "אמו", "אמה", "שם האם"],
        "parents": ["הורים", "הוריו", "הוריה"],
        "spouse": ["בן זוג", "בת זוג", "בן/בת זוג", "אישה", "אשה", "בעל", "נשוי ל", "נישואין", "רעייה", "רעיה"],
        "children": ["ילדים", "בנים", "בנות", "צאצאים", "ילדיו", "ילדיה"],
        "siblings": ["אחים", "אחיות", "אחים ואחיות", "אחיו"],
        "born": ["תאריך לידה", "לידה", "נולד", "נולדה", "שנת לידה"],
        "died": ["תאריך פטירה", "פטירה", "נפטר", "נפטרה", "הסתלקות", "תאריך הסתלקות", "שנת פטירה"],
        "gender": ["מין", "מגדר"],
    },
    # --- תבנית עץ המשפחה (הגריד) ---
    # מפת הסמלים נגזרה מקוד תבנית:עץ משפחה באתר. כל מרצפת היא תא אחד; תיבת טקסט תופסת 3 מרצפות
    # והקווים מתחברים למרצפת האמצעית שלה. הכיוונים לוגיים: l = המרצפת הקודמת ברצף (פיזית מימין ב-RTL).
    "chart": {
        "start": "עץ משפחה/התחלה",
        "row": "עץ משפחה",
        "end": "עץ משפחה/סוף",
        "box_tiles": 3,
        "symbols": {
            "h": "-", "v": "!", "cross": "+",
            "down_right": ".", "down_left": ",", "up_right": "'", "up_left": "`",
            "t_up": "^", "t_down": "ז", "t_right": ")", "t_left": "(",
            "marriage": "ד", "marriage_h": "~", "empty": " ",
        },
        "mirror_horizontal": False,
        "box_years": True,
        "references_tag": "<references />",
        # בני זוג של צאצאים: "inline" = בתוך הקופסה ("אשת [[בעלה]]") כמקובל באתר; "boxes" = קופסה נפרדת
        "spouse_style": "inline",
        "root_spouse_boxes": True,
        "show_sons_wives": True,
        "wife_of": "אשת", "husband_label": "אשתו:",
    },
    # --- תבנית העץ לאדם אחד – שמות הפרמטרים הם ניחוש, לכיול ---
    "ahnentafel": {
        "template": "עץ משפחה לאדם אחד",
        "params": {
            "self": "שם", "f": "אבא", "m": "אמא",
            "ff": "סבא מצד האב", "fm": "סבתא מצד האב",
            "mf": "סבא מצד האם", "mm": "סבתא מצד האם",
            "fff": "אבי סבא מצד האב", "ffm": "אם סבא מצד האב",
            "fmf": "אבי סבתא מצד האב", "fmm": "אם סבתא מצד האב",
            "mff": "אבי סבא מצד האם", "mfm": "אם סבא מצד האם",
            "mmf": "אבי סבתא מצד האם", "mmm": "אם סבתא מצד האם",
            "spouse": "בן זוג", "siblings": "אחים", "children": "ילדים",
        },
    },
    # --- בניית העצים ---
    "min_family_size": 4,
    "max_tree_nodes": 45,            # בני משפחה בעץ אחד; מעבר לזה ענפים גדולים נכנסים לעצים נפרדים
    "max_tree_width": 64,            # רוחב במרצפות – העצים הקיימים באתר הם עד ~63
    "min_branch_size": 6,            # ענף עם לפחות כך וכך בני משפחה יכול להפוך לעץ נפרד ("צאצאי X")
    "min_confidence": 0.5,
    "expand_through_daughters": False,
    # --- חילוץ בעזרת מודל (אופציונלי) ---
    "llm": {"model": "claude-opus-5", "effort": "medium", "max_tokens": 16000, "concurrency": 4},
}

DEEP_KEYS = ("infobox_fields", "chart", "ahnentafel", "llm")


def load_config(path: str | None = None, overrides: dict | None = None) -> dict:
    """טוען את ברירות המחדל, ומעליהן config.json (אם קיים) ודריסות מהשורה."""
    cfg = copy.deepcopy(DEFAULTS)
    candidates = [path] if path else ["config.json"]
    for cand in candidates:
        if cand and os.path.exists(cand):
            with open(cand, encoding="utf-8") as fh:
                _merge(cfg, json.load(fh))
            break
    if overrides:
        _merge(cfg, overrides)
    return cfg


def _merge(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if key in DEEP_KEYS and isinstance(value, dict) and isinstance(base.get(key), dict):
            for sub, subval in value.items():
                if isinstance(subval, dict) and isinstance(base[key].get(sub), dict):
                    base[key][sub].update(subval)
                else:
                    base[key][sub] = subval
        else:
            base[key] = value
