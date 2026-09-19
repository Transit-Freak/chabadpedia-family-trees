"""עזרי ויקיטקסט (ללא תלויות): תבניות, קישורים, קטגוריות, הערות שוליים, פסקאות ומשפטים."""
from __future__ import annotations

import re

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
REF_RE = re.compile(r"<ref(?:\s[^>/]*)?>.*?</ref>|<ref(?:\s[^>/]*)?/>", re.S | re.I)
REF_TOKEN_RE = re.compile(r"\x00R(\d+)\x00")
LINK_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]*))?\]\]")
CAT_RE = re.compile(r"\[\[\s*(?:קטגוריה|Category)\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]", re.I)
HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)
HTML_TAG_RE = re.compile(r"<[^>]+>")
HEBREW_YEAR_RE = re.compile(r"(?<![א-ת\"'])(?:ה['׳])?ת[א-ת]{0,3}[\"״][א-ת](?![א-ת])")
GREG_YEAR_RE = re.compile(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)")

SKIP_NAMESPACES = ("קובץ:", "תמונה:", "קטגוריה:", "תבנית:", "מדיה:", "ויקיפדיה:", "חב\"דפדיה:", "עזרה:",
                   "משתמש:", "file:", "image:", "category:", "template:", "w:", "he:", "en:", "wikt:", "s:")


def normalize_quotes(text: str) -> str:
    """מאחד גרשיים/גרש עבריים לתווי ASCII. שומר על אורך המחרוזת (תו-לתו)."""
    return text.replace("״", '"').replace("׳", "'").replace("’", "'").replace("“", '"').replace("”", '"')


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def tokenize_refs(text: str) -> tuple[str, list[str]]:
    """מחליף כל <ref> באסימון קצר ומחזיר את רשימת ההערות. מאפשר לזהות משפטים בלי שההערות יפריעו."""
    refs: list[str] = []

    def repl(m: re.Match) -> str:
        refs.append(m.group(0))
        return f"\x00R{len(refs) - 1}\x00"

    return REF_RE.sub(repl, text), refs


def refs_in(text: str, refs: list[str]) -> list[str]:
    return [refs[int(i)] for i in REF_TOKEN_RE.findall(text)]


def remove_ref_tokens(text: str) -> str:
    return REF_TOKEN_RE.sub("", text)


def restore_refs(text: str, refs: list[str]) -> str:
    return REF_TOKEN_RE.sub(lambda m: refs[int(m.group(1))], text)


def normalize_title(target: str) -> str:
    t = target.strip().replace("_", " ")
    t = re.sub(r"\s+", " ", t)
    return t


def is_content_link(target: str) -> bool:
    low = target.strip().lower()
    return not any(low.startswith(ns.lower()) for ns in SKIP_NAMESPACES)


def wikilinks(text: str) -> list[tuple[str, str, tuple[int, int]]]:
    """מחזיר (יעד מנורמל, טקסט תצוגה, span) לכל קישור פנימי לדף תוכן."""
    out = []
    for m in LINK_RE.finditer(text):
        target = normalize_title(m.group(1))
        if not target or not is_content_link(target):
            continue
        display = (m.group(2) or "").strip() or target
        out.append((target, display, m.span()))
    return out


def categories(text: str) -> list[str]:
    return [normalize_title(c) for c in CAT_RE.findall(text)]


def split_top(body: str, sep: str = "|") -> list[str]:
    """מפצל לפי sep רק ברמה העליונה (מחוץ ל-{{ }} ול-[[ ]])."""
    parts, cur = [], []
    depth_t = depth_l = 0
    i, n = 0, len(body)
    while i < n:
        two = body[i:i + 2]
        if two == "{{":
            depth_t += 1; cur.append(two); i += 2; continue
        if two == "}}":
            depth_t -= 1; cur.append(two); i += 2; continue
        if two == "[[":
            depth_l += 1; cur.append(two); i += 2; continue
        if two == "]]":
            depth_l -= 1; cur.append(two); i += 2; continue
        ch = body[i]
        if ch == sep and depth_t == 0 and depth_l == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def find_templates(text: str, nested: bool = False) -> list[dict]:
    """מוצא תבניות {{...}} עם התאמת סוגריים. מחזיר name, params, positional, start, end."""
    out = []
    i, n = 0, len(text)
    while True:
        s = text.find("{{", i)
        if s < 0:
            break
        depth, j = 0, s
        while j < n:
            if text.startswith("{{", j):
                depth += 1; j += 2; continue
            if text.startswith("}}", j):
                depth -= 1; j += 2
                if depth == 0:
                    break
                continue
            j += 1
        if depth != 0:
            break
        body = text[s + 2:j - 2]
        tpl = parse_template_body(body)
        tpl["start"], tpl["end"] = s, j
        out.append(tpl)
        if nested:
            for inner in find_templates(body, nested=True):
                inner["start"] += s + 2
                inner["end"] += s + 2
                out.append(inner)
        i = j
    return out


def parse_template_body(body: str) -> dict:
    parts = split_top(body)
    name = normalize_title(parts[0]) if parts else ""
    params: dict[str, str] = {}
    positional: list[str] = []
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            key = key.strip()
            if key and not key.isdigit():
                params[key] = value.strip()
                continue
        positional.append(part.strip())
    return {"name": name, "params": params, "positional": positional}


def sections(text: str) -> list[tuple[str, int, str]]:
    """מחזיר [(כותרת, רמה, תוכן)] כאשר הקטע הראשון הוא הפתיח (כותרת ריקה)."""
    out = []
    last_title, last_level, last_end = "", 0, 0
    for m in HEADING_RE.finditer(text):
        out.append((last_title, last_level, text[last_end:m.start()]))
        last_title, last_level, last_end = m.group(2).strip(), len(m.group(1)), m.end()
    out.append((last_title, last_level, text[last_end:]))
    return out


def lead_section(text: str) -> str:
    return sections(text)[0][2]


def strip_templates(text: str) -> str:
    """מסיר תבניות ברמה העליונה (למשל תבנית האישיות) כדי שלא יבלבלו את חילוץ המשפטים."""
    out, last = [], 0
    for tpl in find_templates(text):
        out.append(text[last:tpl["start"]])
        last = tpl["end"]
    out.append(text[last:])
    return "".join(out)


def plain(text: str) -> str:
    """טקסט קריא: קישורים לטקסט התצוגה, בלי תבניות/תגיות/הדגשות."""
    text = strip_templates(text)
    text = LINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    text = REF_RE.sub("", text)
    text = HTML_TAG_RE.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    """פיצול גס למשפטים: לפי שורות, ולפי נקודה שאחריה רווח. לא נוגע בנקודות בתוך קישורים."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        depth = 0
        buf = []
        i = 0
        while i < len(line):
            two = line[i:i + 2]
            if two in ("[[", "{{"):
                depth += 1; buf.append(two); i += 2; continue
            if two in ("]]", "}}"):
                depth -= 1; buf.append(two); i += 2; continue
            ch = line[i]
            buf.append(ch)
            if ch in ".!?" and depth <= 0 and (i + 1 == len(line) or line[i + 1].isspace()):
                out.append("".join(buf).strip()); buf = []
            i += 1
        if "".join(buf).strip():
            out.append("".join(buf).strip())
    return out


def hebrew_year(text: str) -> str | None:
    text = normalize_quotes(text)
    m = HEBREW_YEAR_RE.search(text)
    if m:
        return m.group(0)
    m = GREG_YEAR_RE.search(text)
    return m.group(1) if m else None


def display_name(title: str) -> str:
    """שם לתצוגה: בלי ההבהרה שבסוגריים."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def disambiguator(title: str) -> str:
    m = re.search(r"\(([^)]*)\)\s*$", title)
    return m.group(1).strip() if m else ""


HONORIFIC_WORDS = {
    "הרב", "רבי", "ר'", "הרה\"ח", "הרה\"ג", "הרה\"ק", "הרה\"צ", "הגה\"ח", "הגה\"ק", "הגה\"צ", "הגאון", "החסיד",
    "הר\"ר", "מוהר\"ר", "הרבנית", "מרת", "כ\"ק", "אדמו\"ר", "הרבי", "רבינו", "הצדיק", "התמים", "השליח", "המשפיע",
    "הבחור", "גב'", "הגברת", "מר", "ד\"ר", "פרופ'", "הרה\"ת", "המקובל", "הדיין",
}
SUFFIX_WORDS = {"ז\"ל", "זצ\"ל", "זצוק\"ל", "זי\"ע", "ע\"ה", "הי\"ד", "נ\"ע", "שליט\"א", "שיחי'", "שי'", "תחי'", "שתחי'",
                "זצוקללה\"ה", "נבג\"מ", "זיע\"א", "יבלחט\"א", "יבדל\"א", "שיבדל\"א", "הכ\"מ"}


def surname_of(title: str) -> str:
    """שם משפחה משוער: המילה האחרונה בשם (ללא הבהרה, ללא תארים)."""
    name = normalize_quotes(display_name(title))
    words = [w for w in name.split() if w not in HONORIFIC_WORDS and w not in SUFFIX_WORDS]
    if len(words) < 2:
        return ""
    last = words[-1]
    if last.startswith("מ") and len(words) >= 3 and words[-2] not in ("בן", "בת"):
        # "שניאור זלמן מלאדי" – מקום ולא שם משפחה
        return ""
    return last
