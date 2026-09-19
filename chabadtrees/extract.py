"""חילוץ קשרי משפחה מוויקיטקסט של ערך אישיות.

שלוש שכבות: שדות בתבנית האישיות, תבניות/דפוסי משפט בעברית, ורשימות בקטעי "משפחתו".
כל קשר נשמר עם המשפט שתומך בו (evidence), ההערות שוליים שצמודות לו, הדף שממנו הגיע, ורמת ביטחון.

צורות קנוניות של קשר (relation):
    parent          relative הוא הורה של person
    spouse          סימטרי
    sibling         סימטרי
    grandparent     relative הוא סב/סבתא של person (עם side אם ידוע: father/mother)
    great_grandparent
    parent_in_law   relative הוא חם/חמות של person (person נשוי לילד של relative)
    sibling_in_law  סימטרי
    uncle           relative הוא דוד/דודה של person
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from . import wikitext as wt

# --------------------------------------------------------------------------- מילונים
HONORIFICS = [
    "הרב הגאון", "הרב החסיד", "הרב הצדיק", "הרה\"ח", "הרה\"ג", "הרה\"ק", "הרה\"צ", "הרה\"ת", "הגה\"ח", "הגה\"ק",
    "הגה\"צ", "הגאון", "הצדיק", "החסיד", "התמים", "השליח", "המשפיע", "המקובל", "הדיין", "הרב", "הר\"ר", "מוהר\"ר",
    "ר\"ר", "רבי", "רבינו", "הרבי", "ר'", "מרת", "הרבנית", "הגברת", "גב'", "מר", "ד\"ר", "פרופ'", "כ\"ק", "אדמו\"ר",
    "הבחור", "הת'", "הנגיד", "המשפיעה", "השליחה",
]
FEMALE_HONORIFICS = {"מרת", "הרבנית", "הגברת", "גב'", "המשפיעה", "השליחה"}
MALE_HONORIFICS = {"הרב", "רבי", "ר'", "הרה\"ח", "הרה\"ג", "הרה\"ק", "הרה\"צ", "הרה\"ת", "הגה\"ח", "הגה\"ק", "הגה\"צ",
                   "הגאון", "הצדיק", "החסיד", "התמים", "השליח", "המשפיע", "הר\"ר", "מוהר\"ר", "ר\"ר", "רבינו", "הרבי",
                   "מר", "כ\"ק", "אדמו\"ר", "הבחור", "הת'", "הנגיד", "הרב הגאון", "הרב החסיד", "הרב הצדיק", "המקובל", "הדיין"}
STOPWORDS = {
    "של", "אשר", "היה", "הייתה", "היתה", "שהיה", "שהייתה", "הוא", "היא", "אשת", "בן", "בת", "את", "על", "אל", "עם", "אצל",
    "לפני", "אחרי", "ו", "רב", "ראש", "אב\"ד", "רבה", "ממלא", "מחבר", "משפיע", "שליח", "נולד", "נולדה", "נפטר", "נפטרה",
    "התחתן", "נישא", "נישאה", "בעיר", "בעיירה", "בכפר", "מהעיר", "בשנת", "בשנה", "ביום", "בליל", "בערב", "מן", "מבני", "מגדולי",
    "מחשובי", "מזקני", "מראשי", "מתלמידי", "משפחת", "לבית", "ואשתו", "ואמו", "ואביו", "ובנו", "ובתו", "וילדיו", "וכן", "גם",
    "אך", "אבל", "כי", "אם", "לא", "אין", "יש", "עוד", "כל", "בכל", "אחד", "אחת", "שני", "שתי", "רק", "כבר", "עדיין", "כאשר",
    "בו", "בה", "לו", "לה", "בהם", "להם", "שם", "כאן", "אז", "שהוא", "שהיא", "כדי", "לאחר", "בעת", "בזמן", "בתקופת",
}
STOPWORDS |= wt.SUFFIX_WORDS
BOLD3 = "'" * 3
BOLD2 = "'" * 2

LINK_TPL = r"\[\[(?P<t{n}>[^\[\]|#]+?)(?:#[^\[\]|]*)?(?:\|(?P<d{n}>[^\[\]]*))?\]\]"
HON_TPL = r"(?P<h{n}>(?:(?:" + "|".join(re.escape(h) for h in HONORIFICS) + r")\s+)*)"
NAME_WORD = r"[א-ת][א-ת'\"\-]*"
_STOP_CORE = ["של", "אשר", "היה", "הייתה", "היתה", "הוא", "היא", "את", "על", "עם", "אצל", "בן", "בת", "וכן", "גם", "כי", "אך", "אבל",
              "נולד", "נולדה", "נפטר", "נפטרה", "התחתן", "נישא", "נישאה", "ז\"ל", "זצ\"ל", "ע\"ה", "הי\"ד", "נ\"ע", "שליט\"א",
              "אמו", "אביו", "אמה", "אביה", "אשתו", "בעלה", "נכדו", "נכדתו", "אחיו", "אחותו", "בנו", "בתו", "חתנו", "כלתו",
              "סבו", "סבתו", "הוריו", "ילדיו", "בניו", "בנותיו", "רעייתו", "זוגתו", "חותנו", "חמיו", "גיסו", "דודו", "נינו", "אלמנתו"]
_STOP_ALL = _STOP_CORE + ["ו" + w for w in _STOP_CORE] + ["ול" + w for w in _STOP_CORE] + ["ל" + w for w in _STOP_CORE]
_STOP_LOOKAHEAD = r"(?!(?:" + "|".join(re.escape(w) for w in _STOP_ALL) + r")(?![א-ת'\"]))"
UNLINKED_TPL = r"(?P<n{n}>" + _STOP_LOOKAHEAD + NAME_WORD + r"(?:\s+" + _STOP_LOOKAHEAD + NAME_WORD + r"){0,3})"


def _idx(tpl: str, n: int) -> str:
    return tpl.replace("{n}", str(n))


def REF(n: int) -> str:
    return _idx(HON_TPL, n) + r"(?:" + _idx(LINK_TPL, n) + r"|" + _idx(UNLINKED_TPL, n) + r")"


def LINK(n: int) -> str:
    return _idx(HON_TPL, n) + _idx(LINK_TPL, n)


_HON_ALT = "|".join(re.escape(h) for h in HONORIFICS)
SUBJ = ""   # הנושא מזוהה מהטקסט שלפני ההתאמה (subject_of), לא בתוך הרגקס
_PRE_LINK_RE = re.compile(r"(?P<pre>.*?)(?P<hon>(?:(?:" + _HON_ALT + r")\s+)*)\[\[(?P<t>[^\[\]|#]+?)(?:#[^\[\]|]*)?(?:\|(?P<d>[^\[\]]*))?\]\]\s*[,–\-:]?\s*$", re.S)
_PRE_NAME_RE = re.compile(r"(?P<pre>.*?)(?<![א-ת])(?P<hon>(?:(?:" + _HON_ALT + r")\s+)+)(?P<n>" + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,2})\s*[,–\-:]\s*$", re.S)
_OBJECT_TAIL_RE = re.compile(r"(?:(?<![א-ת])(?:של|את|עם)\s*|(?<![א-ת])[לבומכ])$")
ORD = r"(?:\s+(?:הבכור|הבכורה|השני|השנייה|השלישי|השלישית|הרביעי|החמישי|הצעיר|הצעירה|היחיד|היחידה|הגדול|הגדולה|הקטן|הקטנה))?"
VERB = r"(?:\s*,?\s*(?:היה|הייתה|היתה|הוא|היא)\s*,?\s*)?"
NOTOF = r"(?!של\b)"


@dataclass
class Relation:
    person: str            # מזהה: כותרת ערך, או "~שם" לאדם בלי ערך
    relative: str
    relation: str
    source_page: str
    evidence: str
    refs: list[str] = field(default_factory=list)
    method: str = "pattern"
    pattern: str = ""
    confidence: float = 0.7
    person_gender: str | None = None
    relative_gender: str | None = None
    side: str | None = None          # לסבים: father / mother
    person_has_article: bool = True
    relative_has_article: bool = True
    person_name: str = ""
    relative_name: str = ""
    order: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- עזרים
def _gender_from_honorific(h: str) -> str | None:
    words = [w for w in (h or "").split()]
    for w in words:
        if w in FEMALE_HONORIFICS:
            return "f"
    for w in words:
        if w in MALE_HONORIFICS:
            return "m"
    return None


RELATION_NOUNS = {"אמו", "אביו", "אמה", "אביה", "אשתו", "בעלה", "נכדו", "נכדתו", "אחיו", "אחותו", "בנו", "בתו", "חתנו",
                  "כלתו", "סבו", "סבתו", "הוריו", "ילדיו", "בניו", "בנותיו", "אחיה", "בנה", "בתה", "רעייתו", "זוגתו",
                  "חותנו", "חמיו", "גיסו", "דודו", "נינו", "אלמנתו", "משפחתו", "צאצאיו"}


def clean_unlinked_name(name: str) -> str:
    """גוזר שם לא-מקושר במילת עצירה ראשונה ומסיר תארי כבוד/סיומות.

    נעצר גם ב"ו"+שם-קשר ("ולאמו", "ואשתו"), וב-"מ"+מקום אחרי שם מלא ("מקישינוב").
    """
    words = wt.normalize_quotes(name).replace(BOLD3, " ").replace(BOLD2, " ").split()
    out = []
    for w in words:
        bare = w.strip(",.;:()'")
        if not bare:
            continue
        if w.strip(",.;:()") in wt.HONORIFIC_WORDS or w.strip(",.;:()") in wt.SUFFIX_WORDS:
            if out:
                break
            continue
        if bare in STOPWORDS or bare in wt.HONORIFIC_WORDS or bare in RELATION_NOUNS:
            if out:
                break
            continue
        if bare[0] == "ו" and (bare[1:] in RELATION_NOUNS or bare[1:] in STOPWORDS or (bare[1:2] == "ל" and bare[2:] in RELATION_NOUNS)):
            break
        if bare[0] == "ל" and bare[1:] in RELATION_NOUNS:
            break
        if bare.startswith("ש") and len(bare) > 3 and bare[1:] in ("היה", "הייתה", "נולד", "נפטר", "נולדה", "נפטרה"):
            break
        if len(out) >= 2 and bare[0] == "מ" and len(bare) > 3:
            break
        out.append(bare)
    return " ".join(out).strip(" ,.;:")


class Extractor:
    def __init__(self, config: dict, known_persons: set[str] | None = None, aliases: dict[str, str] | None = None):
        self.cfg = config
        self.known = known_persons or set()
        # כינוי/שם-תצוגה → כותרת ערך, לפתירת אזכורים לא-מקושרים ("הצמח צדק" → הערך)
        self.aliases: dict[str, str] = dict(aliases or {})
        if not aliases and self.known:
            self.aliases = build_aliases(self.known)
        self.patterns = build_patterns()
        self.infobox_fields = config.get("infobox_fields", {})
        self.unknown_fields: dict[str, int] = {}
        self.template_names: dict[str, int] = {}
        self.current_info: dict | None = None

    # ---------------------------------------------------------------- ציבורי
    def extract(self, title: str, wikitext_raw: str) -> tuple[list[Relation], dict]:
        """מחזיר (רשימת קשרים, פרטי האדם: gender/born/died/categories/surname)."""
        text = wt.normalize_quotes(wt.strip_comments(wikitext_raw or ""))
        info = {"title": title, "gender_votes": {"m": 0, "f": 0}, "born": None, "died": None,
                "categories": wt.categories(text), "surname": wt.surname_of(title)}
        first = wt.normalize_quotes(title).split()[0] if title.split() else ""
        if first in FEMALE_HONORIFICS:
            info["gender_votes"]["f"] += 2
        elif first in MALE_HONORIFICS:
            info["gender_votes"]["m"] += 2
        relations: list[Relation] = []
        order = [0]

        def add(rel: Relation) -> None:
            rel.order = order[0]
            order[0] += 1
            relations.append(rel)

        # שכבה 1: תבניות (תבנית האישיות)
        for tpl in wt.find_templates(text):
            self.template_names[tpl["name"]] = self.template_names.get(tpl["name"], 0) + 1
            self._from_infobox(title, tpl, info, add)

        # שכבה 2: דפוסי משפט – בפתיח ובכל הקטעים (לא בתבניות)
        self.current_info = info
        body = wt.strip_templates(text)
        tokenized, refs = wt.tokenize_refs(body)
        for sec_title, _level, sec_text in wt.sections(tokenized):
            in_family_section = bool(re.search(r"משפח|ילדי|צאצא|קרוב|הורי|נישוא|בני|בנות", sec_title))
            list_context = ""
            for line in sec_text.split("\n"):
                stripped = wt.plain(wt.remove_ref_tokens(line)).strip(" :'")
                m_ctx = re.fullmatch(r"(ילדיו|ילדיה|ילדיהם|בניו|בנותיו|צאצאיו|אחיו|אחיותיו|אחיו ואחיותיו|נכדיו|הוריו)", stripped)
                if m_ctx:
                    list_context = m_ctx.group(1)
                    continue
                if not line.lstrip().startswith(("*", "#")) and stripped:
                    list_context = ""
                for sentence in wt.split_sentences(line):
                    self._from_sentence(title, sentence, refs, info, add, in_family_section, sec_title, list_context)
                    self._gender_votes(sentence, info)

        # לידה/פטירה מהטקסט אם לא נמצאו בתבנית
        if info["born"] is None:
            m = re.search(r"נולד(?:ה)?\s[^.\n]{0,80}?(?:בשנת\s+)?((?:ה')?ת[א-ת]{0,3}\"[א-ת]|1[5-9]\d\d|20\d\d)", wt.plain(body))
            if m:
                info["born"] = m.group(1)
        if info["died"] is None:
            m = re.search(r"(?:נפטר(?:ה)?|הסתלק(?:ה)?|נרצח(?:ה)?|נהרג(?:ה)?)\s[^.\n]{0,80}?(?:בשנת\s+)?((?:ה')?ת[א-ת]{0,3}\"[א-ת]|1[5-9]\d\d|20\d\d)", wt.plain(body))
            if m:
                info["died"] = m.group(1)
        return relations, info

    # ---------------------------------------------------------------- תבנית
    def _from_infobox(self, title: str, tpl: dict, info: dict, add) -> None:
        params = tpl["params"]
        if not params:
            return
        field_map: dict[str, str] = {}
        for role, names in self.infobox_fields.items():
            for name in names:
                if name in params:
                    field_map[name] = role
        for key in params:
            if key not in field_map and re.search(r"אב|אם|בן זוג|בת זוג|ילד|אח|הור|סב|משפח", key):
                self.unknown_fields[key] = self.unknown_fields.get(key, 0) + 1
        for name, role in field_map.items():
            value = params[name]
            if role == "born":
                info["born"] = info["born"] or wt.hebrew_year(value)
                continue
            if role == "died":
                info["died"] = info["died"] or wt.hebrew_year(value)
                continue
            if role == "gender":
                v = value.strip()
                if v.startswith("נ") or v.startswith("א"):
                    info["gender_votes"]["f"] += 3
                elif v.startswith("ז") or v.startswith("ג"):
                    info["gender_votes"]["m"] += 3
                continue
            tokenized, refs = wt.tokenize_refs(value)
            items = re.split(r"<br\s*/?>|\n|\*|;|,(?![^\[]*\]\])", tokenized)
            for item in items:
                item = item.strip(" \t'-–")
                if not item or wt.remove_ref_tokens(item).strip() == "":
                    continue
                for person_id, has_article, gender, disp in self._persons_in(item):
                    relation, person, relative, side = None, title, person_id, None
                    if role in ("father", "mother", "parents"):
                        relation = "parent"
                        if role == "father":
                            gender = gender or "m"
                        elif role == "mother":
                            gender = gender or "f"
                    elif role == "spouse":
                        relation = "spouse"
                    elif role == "children":
                        relation, person, relative = "parent", person_id, title
                    elif role == "siblings":
                        relation = "sibling"
                    if relation is None:
                        continue
                    rel = Relation(person=person, relative=relative, relation=relation, source_page=title,
                                   evidence=f"{name} = {wt.remove_ref_tokens(item).strip()}", refs=wt.refs_in(item, refs),
                                   method="infobox", pattern=f"infobox:{name}", confidence=0.9 if has_article else 0.7)
                    if role == "children":
                        rel.person_has_article = has_article
                        rel.person_gender = gender
                        rel.person_name = disp
                        rel.relative_name = wt.display_name(title)
                    else:
                        rel.relative_has_article = has_article
                        rel.relative_gender = gender
                        rel.relative_name = disp
                        rel.person_name = wt.display_name(title)
                    add(rel)

    def _persons_in(self, fragment: str) -> list[tuple[str, bool, str | None, str]]:
        """אנשים בקטע טקסט קצר: קישורים, ואם אין – שם לא-מקושר."""
        out = []
        links = wt.wikilinks(fragment)
        for target, display, span in links:
            if _looks_like_year(target):
                continue
            hon = fragment[max(0, span[0] - 40):span[0]]
            gender = _gender_from_honorific(" ".join(hon.split()[-2:]))
            out.append((target, self._has_article(target), gender, wt.display_name(target)))
        if not links:
            name = clean_unlinked_name(wt.plain(fragment))
            if name and len(name) <= 40 and not _looks_like_year(name):
                gender = _gender_from_honorific(wt.plain(fragment))
                resolved = self.resolve_alias(wt.plain(fragment).strip(), allow_names=False) or self.resolve_alias(name, allow_names=False)
                if resolved:
                    out.append((resolved, True, gender, wt.display_name(resolved)))
                else:
                    out.append(("~" + name, False, gender, name))
        return out

    def _has_article(self, target: str) -> bool:
        return (target in self.known) if self.known else True

    def resolve_alias(self, name: str, allow_names: bool = True) -> str | None:
        """שם לא-מקושר שמתאים לכינוי ייחודי של ערך ידוע → כותרת הערך. allow_names=False: כינויים בלבד."""
        hit = self.aliases.get(_alias_key(name))
        if not hit:
            return None
        title, kind = hit
        if kind == "name" and not allow_names:
            return None
        return title

    # ---------------------------------------------------------------- משפטים
    def _from_sentence(self, title: str, sentence: str, refs: list[str], info: dict, add,
                       in_family_section: bool, sec_title: str, list_context: str = "") -> None:
        clean = wt.remove_ref_tokens(sentence).replace(BOLD3, "").replace(BOLD2, "")
        self._in_list = clean.lstrip().startswith(("*", "#"))
        base_conf = 0.75 if in_family_section else 0.7
        seen_spans: list[tuple[int, int, str]] = []
        for name, regex, handler in self.patterns:
            for m in regex.finditer(clean):
                if any(s <= m.start() < e and name.split(":")[0] == n for s, e, n in seen_spans):
                    continue
                seen_spans.append((m.start(), m.end(), name.split(":")[0]))
                for rel in handler(self, m, title, clean):
                    rel.source_page = title
                    rel.evidence = wt.plain(clean).strip()
                    rel.refs = wt.refs_in(sentence, refs)
                    rel.pattern = name
                    rel.confidence = min(rel.confidence, 0.95) if rel.confidence else base_conf
                    if not rel.relative_has_article or not rel.person_has_article:
                        rel.confidence -= 0.15
                    rel.confidence = round(max(rel.confidence, 0.2), 2)
                    add(rel)
        # רשימות בקטעי משפחה: "* [[X]] – בנו", או תחת "ילדיו:"
        if (in_family_section or list_context) and clean.lstrip().startswith(("*", "#")):
            self._from_list_line(title, clean, sentence, refs, add, sec_title, list_context)

    def _from_list_line(self, title: str, line: str, raw: str, refs: list[str], add, sec_title: str, list_context: str = "") -> None:
        links = wt.wikilinks(line)
        text = line
        if links:
            target, display, span = links[0]
            if _looks_like_year(target):
                return
        else:
            head = re.split(r"\s+[–\-]\s+|,|\(|:", wt.plain(line).lstrip("*# ").strip(), maxsplit=1)[0]
            name = clean_unlinked_name(head)
            if not name or not _gender_from_honorific(head) and " " not in name:
                return
            resolved = self.resolve_alias(head.strip(), allow_names=False) or self.resolve_alias(name, allow_names=False)
            target = resolved or ("~" + name)
            display = wt.display_name(resolved) if resolved else name
            span = (0, 0)
            text = line.replace(head, "", 1)
        role = None
        for word, rel in LIST_ROLES.items():
            if re.search(r"(?<![א-ת])" + re.escape(word) + r"(?![א-ת])(?!\s+של)", text):
                role = rel
                break
        ctx = list_context or sec_title
        if role is None and re.search(r"ילדי|צאצא|בניו|בנותיו|בני משפחתו", ctx):
            role = ("parent", "child")
        if role is None and re.search(r"^אחיו|אחיותיו", ctx):
            role = ("sibling", "rel")
        if role is None:
            return
        relation, direction = role
        has = self._has_article(target) if not target.startswith("~") else False
        hon = line[max(0, span[0] - 30):span[0]] if links else head
        gender = _gender_from_honorific(" ".join(hon.split()[-2:])) if links else _gender_from_honorific(head)
        if gender is None and direction == "child":
            if "בנותיו" in ctx or re.search(r"(?<![א-ת])בתו(?![א-ת])", text):
                gender = "f"
            elif "בניו" in ctx or re.search(r"(?<![א-ת])בנו(?![א-ת])", text):
                gender = "m"
        if direction == "child":
            rel = Relation(person=target, relative=title, relation=relation, source_page=title, evidence="",
                           person_has_article=has, person_gender=gender, person_name=wt.display_name(target),
                           relative_name=wt.display_name(title), method="list", confidence=0.8 if has else 0.6)
        else:
            rel = Relation(person=title, relative=target, relation=relation, source_page=title, evidence="",
                           relative_has_article=has, relative_gender=gender, relative_name=wt.display_name(target),
                           person_name=wt.display_name(title), method="list", confidence=0.8 if has else 0.6)
        if relation == "parent_in_law_rev":
            rel.relation, rel.person, rel.relative = "parent_in_law", target, title
            rel.person_name, rel.relative_name = wt.display_name(target), wt.display_name(title)
        rel.evidence = wt.plain(line).strip()
        rel.refs = wt.refs_in(raw, refs)
        rel.pattern = "list:" + sec_title
        add(rel)

    def _gender_votes(self, sentence: str, info: dict) -> None:
        s = wt.plain(sentence)
        for w in re.findall(r"(?<![א-ת])(נולד|נולדה|נפטר|נפטרה|נישא|נישאה|התחתן|התחתנה|כיהן|כיהנה|שימש|שימשה|למד|למדה)(?![א-ת])", s):
            info["gender_votes"]["f" if w.endswith("ה") and w not in ("כיהן",) else "m"] += 1

    # ---------------------------------------------------------------- בניית אדם מהתאמה
    def person_from_match(self, m: re.Match, n: int) -> tuple[str, bool, str | None, str] | None:
        gd = m.groupdict()
        hon = gd.get(f"h{n}") or ""
        gender = _gender_from_honorific(hon)
        if gd.get(f"t{n}"):
            target = wt.normalize_title(gd[f"t{n}"])
            if not wt.is_content_link(target) or _looks_like_year(target):
                return None
            return target, self._has_article(target), gender, wt.display_name(target)
        name = clean_unlinked_name(gd.get(f"n{n}") or "")
        if not name or _looks_like_year(name) or len(name) < 2:
            return None
        allow = not getattr(self, "_in_list", False)
        resolved = self.resolve_alias((hon + " " + name).strip(), allow) or self.resolve_alias(name, allow)
        if resolved:
            return resolved, True, gender, wt.display_name(resolved)
        if not hon and " " not in name:
            # מילה בודדת בלי תואר – כנראה לא שם ("נשוי לאישה")
            return None
        return "~" + name, False, gender, name

    def subject_of(self, m: re.Match, page: str, chain_ok: bool = False) -> tuple[str, bool, str | None, str]:
        """הנושא של ביטוי קשר: הקישור/השם שלפניו אם הוא באמת נושא, אחרת נושא הערך.

        "[[X]], בנו של [[Y]]" → X. "היה חתנו של [[A]], בעלה של [[B]]" → A הוא מושא של הביטוי הקודם
        ולכן לא נושא (אלא אם chain_ok – שרשרת יוחסין "בנו של Y, בנו של Z").
        """
        before = m.string[:m.start()]
        page_disp = wt.display_name(page)
        lm = _PRE_LINK_RE.match(before)
        if lm:
            pre = lm.group("pre")
            is_object = bool(_OBJECT_TAIL_RE.search(pre))
            target = wt.normalize_title(lm.group("t"))
            known = (not self.known) or target in self.known
            if (not is_object or chain_ok) and wt.is_content_link(target) and (known or lm.group("hon")) and not _looks_like_year(target):
                return target, self._has_article(target), _gender_from_honorific(lm.group("hon") or ""), wt.display_name(target)
            return page, True, None, page_disp
        nm = _PRE_NAME_RE.match(before)
        if nm:
            name = clean_unlinked_name(nm.group("n"))
            pre = nm.group("pre")
            if name and not _OBJECT_TAIL_RE.search(pre) and not (" " not in name and name.startswith("מ")):
                if name == page_disp or name in page_disp:
                    return page, True, None, page_disp
                allow = not getattr(self, "_in_list", False)
                resolved = self.resolve_alias((nm.group("hon") + " " + name).strip(), allow) or self.resolve_alias(name, allow)
                if resolved:
                    return resolved, True, _gender_from_honorific(nm.group("hon") or ""), wt.display_name(resolved)
                return "~" + name, False, _gender_from_honorific(nm.group("hon") or ""), name
        return page, True, None, page_disp

    def page_gender_conflict(self, word_gender: str | None) -> bool:
        """המילה אומרת בת/בן על נושא הדף, אבל הדף עצמו כבר מסומן במגדר ההפוך."""
        if not word_gender or not self.current_info:
            return False
        votes = self.current_info.get("gender_votes") or {}
        other = "f" if word_gender == "m" else "m"
        return votes.get(other, 0) >= 2 and votes.get(other, 0) > votes.get(word_gender, 0)


def _alias_key(name: str) -> str:
    words = [w for w in wt.normalize_quotes(name).replace(BOLD3, " ").split() if w not in wt.HONORIFIC_WORDS and w not in wt.SUFFIX_WORDS]
    return " ".join(words).strip(" ,.;:'")


def build_aliases(known: set[str], extra: dict[str, str] | None = None) -> dict[str, str]:
    """מפת כינוי → כותרת. שני סוגים:
    * כינוי ממש (ההבהרה שבסוגריים בלי "אדמו\"ר", או שדה "כינוי" בתבנית) – נפתר בכל הקשר.
    * שם התצוגה – רק אם יש בו שם משפחה (2+ מילים) ורק בפרוזה (לא ברשימות/תבניות, שם עורך היה מקשר).
    נשמרים רק כינויים ייחודיים. הערך במפה: (כותרת, סוג) כאשר סוג הוא "nick" או "name".
    """
    counts: dict[str, set[tuple[str, str]]] = {}
    surname_freq: dict[str, int] = {}
    for title in known:
        sn = wt.surname_of(title)
        if sn:
            surname_freq[sn] = surname_freq.get(sn, 0) + 1

    def add(alias: str, title: str, kind: str) -> None:
        key = _alias_key(alias)
        if len(key) < 3:
            return
        if kind == "name":
            sn = wt.surname_of(title)
            words = key.split()
            # שם תצוגה נחשב כינוי רק אם יש בו שם משפחה אמיתי (מופיע אצל 2+ אנשים) או 3+ מילים
            if not sn or not key.endswith(sn) or (surname_freq.get(sn, 0) < 2 and len(words) < 3):
                return
        counts.setdefault(key, set()).add((title, kind))

    for title in known:
        add(wt.display_name(title), title, "name")
        dis = wt.disambiguator(title)
        if dis and not dis.startswith(("בן ", "בת ", "אשת ", "אחי ", "אבי ", "נכד ")):
            add(dis, title, "nick")
            for prefix in ("אדמו\"ר ", "הרב ", "ר' "):
                if dis.startswith(prefix):
                    add(dis[len(prefix):], title, "nick")
    for alias, title in (extra or {}).items():
        add(alias, title, "nick")
    out: dict[str, tuple[str, str]] = {}
    for key, vals in counts.items():
        titles = {t for t, _k in vals}
        if len(titles) == 1:
            title = next(iter(titles))
            kind = "nick" if any(k == "nick" for _t, k in vals) else "name"
            out[key] = (title, kind)
    return out


def _looks_like_year(s: str) -> bool:
    s = wt.normalize_quotes(s.strip())
    return bool(re.fullmatch(r"(?:ה')?ת[א-ת]{0,3}\"[א-ת]|\d{3,4}|שנת .*|[א-ת]{1,2}\"[א-ת]", s))


# --------------------------------------------------------------------------- דפוסים
def _mk(person, relative, relation, m, self_, page, *, conf=0.75, person_gender=None, relative_gender=None, side=None):
    p_id, p_has, p_g, p_name = person
    r_id, r_has, r_g, r_name = relative
    if p_id == r_id:
        return None
    return Relation(person=p_id, relative=r_id, relation=relation, source_page=page, evidence="",
                    confidence=conf, person_gender=person_gender or p_g, relative_gender=relative_gender or r_g,
                    side=side, person_has_article=p_has, relative_has_article=r_has, person_name=p_name,
                    relative_name=r_name)


def h_child_of(self_, m, page, sentence):
    """X, בנו של Y [ושל Z] → Y (ו-Z) הורים של X."""
    out = []
    subj = self_.subject_of(m, page, chain_ok=True)
    w = m.group("w")
    pg = "f" if w.startswith("בת") else "m"
    if subj[0] == page and self_.page_gender_conflict(pg):
        return []
    for n in (1, 2):
        rel = self_.person_from_match(m, n)
        if rel:
            r = _mk(subj, rel, "parent", m, self_, page, person_gender=pg,
                    relative_gender=("f" if n == 2 else None))
            if r:
                out.append(r)
    return out


def h_born_to(self_, m, page, sentence):
    out = []
    subj = (page, True, None, wt.display_name(page))
    for n, g in ((1, "m"), (2, "f")):
        rel = self_.person_from_match(m, n)
        if rel:
            r = _mk(subj, rel, "parent", m, self_, page, conf=0.85, relative_gender=g if (m.groupdict().get(f"k{n}") or "").startswith(("אב", "אמ")) else None)
            if r:
                out.append(r)
    return out


def h_parent_is(self_, m, page, sentence):
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    g = "m" if w.startswith("אב") else "f"
    r = _mk((page, True, None, wt.display_name(page)), rel, "parent", m, self_, page, conf=0.8, relative_gender=g)
    return [r] if r else []


def h_parent_of(self_, m, page, sentence):
    """X, אביו של Y → X הורה של Y."""
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    g = "m" if w.startswith("אב") else "f"
    r = _mk(rel, subj, "parent", m, self_, page, conf=0.7, relative_gender=g)
    return [r] if r else []


def h_spouse_verb(self_, m, page, sentence):
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    pg = "f" if w in ("נשואה", "נישאה", "התחתנה", "נשאה") else "m"
    if subj[0] == page and self_.page_gender_conflict(pg):
        return []
    r = _mk(subj, rel, "spouse", m, self_, page, conf=0.85, person_gender=pg, relative_gender=("m" if pg == "f" else "f"))
    return [r] if r else []


def h_spouse_noun(self_, m, page, sentence):
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    pg = "f" if w.startswith("בעל") or w.startswith("בן זוג") else "m"
    r = _mk((page, True, None, wt.display_name(page)), rel, "spouse", m, self_, page, conf=0.8,
            person_gender=pg, relative_gender=("m" if pg == "f" else "f"))
    return [r] if r else []


def h_spouse_of(self_, m, page, sentence):
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    pg = "m" if w.startswith("בעל") else "f"
    r = _mk(subj, rel, "spouse", m, self_, page, conf=0.8, person_gender=pg, relative_gender=("f" if pg == "m" else "m"))
    return [r] if r else []


def h_sibling_of(self_, m, page, sentence):
    subj = self_.subject_of(m, page)
    out = []
    w = m.group("w")
    pg = "f" if w.startswith("אחות") else "m"
    for n in (1, 2):
        rel = self_.person_from_match(m, n)
        if rel:
            r = _mk(subj, rel, "sibling", m, self_, page, conf=0.8, person_gender=pg)
            if r:
                out.append(r)
    return out


def h_sibling_is(self_, m, page, sentence):
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    rg = "f" if w.startswith("אחות") else "m"
    r = _mk((page, True, None, wt.display_name(page)), rel, "sibling", m, self_, page, conf=0.75, relative_gender=rg)
    return [r] if r else []


def h_list_of_links(relation: str, direction: str, conf: float = 0.75, gender_from_word: bool = False):
    def handler(self_, m, page, sentence):
        out = []
        rest = m.group("rest")
        if " של " in rest[:12]:
            return []
        w = m.groupdict().get("w") or ""
        for target, display, span in wt.wikilinks(rest):
            if _looks_like_year(target):
                continue
            hon = rest[max(0, span[0] - 30):span[0]]
            g = _gender_from_honorific(" ".join(hon.split()[-2:]))
            if gender_from_word and not g:
                g = "f" if w.startswith("בנות") or w.startswith("בת") or w.startswith("אחיות") else None
            rel = (target, self_._has_article(target), g, wt.display_name(target))
            me = (page, True, None, wt.display_name(page))
            r = _mk(rel, me, relation, m, self_, page, conf=conf) if direction == "child" else \
                _mk(me, rel, relation, m, self_, page, conf=conf)
            if r:
                out.append(r)
        return out
    return handler


def h_grandparent_of(self_, m, page, sentence):
    subj = self_.subject_of(m, page, chain_ok=True)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    relation = "great_grandparent" if w.startswith("נין") else "grandparent"
    pg = "f" if w in ("נכדתו", "נכדתם", "נינתו") else "m"
    r = _mk(subj, rel, relation, m, self_, page, conf=0.75, person_gender=pg)
    return [r] if r else []


def h_grandparent_is(self_, m, page, sentence):
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    side_word = m.groupdict().get("side") or ""
    side = "father" if side_word.startswith("אב") else ("mother" if side_word.startswith("אמ") or side_word.startswith("האם") else None)
    rg = "f" if w.startswith("סבת") else "m"
    r = _mk((page, True, None, wt.display_name(page)), rel, "grandparent", m, self_, page, conf=0.75,
            relative_gender=rg, side=side)
    return [r] if r else []


def h_grandparent_rev(self_, m, page, sentence):
    """X, סבו של Y → X סב של Y."""
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    rg = "f" if w.startswith("סבת") else "m"
    r = _mk(rel, subj, "grandparent", m, self_, page, conf=0.7, relative_gender=rg)
    return [r] if r else []


def h_child_in_law_of(self_, m, page, sentence):
    """X, חתנו של Y → Y חם של X."""
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    pg = "f" if w.startswith("כלת") else "m"
    r = _mk(subj, rel, "parent_in_law", m, self_, page, conf=0.8, person_gender=pg)
    return [r] if r else []


def h_parent_in_law_is(self_, m, page, sentence):
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    rg = "f" if w in ("חמותו", "חותנתו", "חמותה", "חותנתה") else "m"
    r = _mk((page, True, None, wt.display_name(page)), rel, "parent_in_law", m, self_, page, conf=0.8, relative_gender=rg)
    return [r] if r else []


def h_child_in_law_is(self_, m, page, sentence):
    """חתנו, [[X]] → X חתן של הדף → הדף חם של X."""
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    pg = "f" if w.startswith("כלת") else "m"
    r = _mk(rel, (page, True, None, wt.display_name(page)), "parent_in_law", m, self_, page, conf=0.75, person_gender=pg)
    return [r] if r else []


def h_sibling_in_law(self_, m, page, sentence):
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    r = _mk(subj, rel, "sibling_in_law", m, self_, page, conf=0.7)
    return [r] if r else []


def h_uncle_of(self_, m, page, sentence):
    """X, דודו של Y → X דוד של Y."""
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    r = _mk(rel, subj, "uncle", m, self_, page, conf=0.7)
    return [r] if r else []


def h_nephew_of(self_, m, page, sentence):
    """X, אחיינו של Y → Y דוד של X."""
    subj = self_.subject_of(m, page)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    r = _mk(subj, rel, "uncle", m, self_, page, conf=0.7)
    return [r] if r else []


def h_uncle_is(self_, m, page, sentence):
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    r = _mk((page, True, None, wt.display_name(page)), rel, "uncle", m, self_, page, conf=0.7)
    return [r] if r else []


LIST_ROLES = {
    "בנו": ("parent", "child"), "בתו": ("parent", "child"), "בנם": ("parent", "child"), "בתם": ("parent", "child"),
    "בנה": ("parent", "child"), "בתה": ("parent", "child"),
    "חתנו": ("parent_in_law_rev", "child"), "כלתו": ("parent_in_law_rev", "child"),
    "חתנם": ("parent_in_law_rev", "child"), "כלתם": ("parent_in_law_rev", "child"),
    "אחיו": ("sibling", "rel"), "אחותו": ("sibling", "rel"), "אחיה": ("sibling", "rel"), "אחותה": ("sibling", "rel"),
    "אביו": ("parent", "rel"), "אמו": ("parent", "rel"), "אביה": ("parent", "rel"), "אמה": ("parent", "rel"),
    "אשתו": ("spouse", "rel"), "בעלה": ("spouse", "rel"), "רעייתו": ("spouse", "rel"), "זוגתו": ("spouse", "rel"),
    "סבו": ("grandparent", "rel"), "סבתו": ("grandparent", "rel"), "סבה": ("grandparent", "rel"), "סבתה": ("grandparent", "rel"),
    "חותנו": ("parent_in_law", "rel"), "חמיו": ("parent_in_law", "rel"), "חמותו": ("parent_in_law", "rel"), "חותנתו": ("parent_in_law", "rel"),
    "גיסו": ("sibling_in_law", "rel"), "גיסתו": ("sibling_in_law", "rel"),
    "דודו": ("uncle", "rel"), "דודתו": ("uncle", "rel"),
}


def build_patterns() -> list[tuple[str, re.Pattern, object]]:
    P = []

    def add(name, regex, handler):
        P.append((name, re.compile(regex), handler))

    # X, בנו של Y ושל Z
    add("child_of", r"(?<![א-ת])(?P<w>בנו|בנה|בתו|בתה|בנם|בתם)" + ORD + r"\s+של\s+" + REF(1) +
        r"(?:\s*,?\s*ו(?:של\s+)?" + REF(2) + r")?", h_child_of)
    # נולד ... לאביו X ולאמו Y / להוריו X ו-Y / ל[[X]] ול[[Y]]
    add("born_to", r"נולד(?:ה)?(?![א-ת])[^.\n]{0,120}?(?<![א-ת])ל(?P<k1>אביו|אביה|הוריו|הוריה)\s*,?\s*" + REF(1) +
        r"(?:[^.\n]{0,40}?(?<![א-ת])ול(?P<k2>אמו|אמה)?\s*,?\s*" + REF(2) + r")?", h_born_to)
    add("born_to_links", r"נולד(?:ה)?(?![א-ת])[^.\n]{0,120}?(?<![א-ת])ל(?P<k1>)" + LINK(1) + r"\s*,?\s*ו(?:ל)?(?P<k2>)" + REF(2), h_born_to)
    # אביו, X / אמו הייתה X
    add("parent_is", r"(?:^|[,.;:()]\s*|(?<!מצד)\s+ו?)(?P<w>אביו|אמו|אביה|אמה|אביהם|אמם)" + VERB + r"\s*,?\s*" + NOTOF + REF(1), h_parent_is)
    # X, אביו של Y
    add("parent_of", r"(?<![א-ת])(?P<w>אביו|אמו|אביה|אמה|אביהם|אמם)\s+של\s+" + REF(1), h_parent_of)
    # נשוי ל / נישאה ל / התחתן עם / נשא לאישה את
    add("spouse_verb", r"(?<![א-ת])(?P<w>נשוי|נשואה|נישא|נישאה|התחתן|התחתנה|נשא|נשאה)(?:\s+לאי?שה)?\s+(?:בזיווג\s+\S+\s+)?"
        r"(?:ל|עם\s+|את\s+)" + REF(1), h_spouse_verb)
    # אשתו, X / בעלה הוא X
    add("spouse_noun", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>אשתו|אישתו|רעייתו|זוגתו|בעלה|בן זוגה|בת זוגו|אשתו הראשונה|אשתו השנייה|אשתו השניה)"
        + VERB + r"\s*,?\s*" + NOTOF + REF(1), h_spouse_noun)
    # X, אשתו של Y / אלמנתו של
    add("spouse_of", r"(?<![א-ת])(?P<w>אשתו|אישתו|רעייתו|זוגתו|בעלה|אלמנתו|אלמנת)\s+של\s+" + REF(1), h_spouse_of)
    # X, אחיו של Y
    add("sibling_of", r"(?<![א-ת])(?P<w>אחיו|אחותו|אחיה|אחותה|אחיהם|אחותם)" + ORD + r"\s+של\s+" + REF(1) +
        r"(?:\s*,?\s*ו(?:של\s+)?" + REF(2) + r")?", h_sibling_of)
    # אחיו, X
    add("sibling_is", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>אחיו|אחותו|אחיה|אחותה)" + ORD + VERB + r"\s*,?\s*" + NOTOF + LINK(1), h_sibling_is)
    # ילדיו: [[X]], [[Y]] / בניו הם / אחיו הם
    add("children_list", r"(?<![א-ת])(?P<w>ילדיו|ילדיה|ילדיהם|בניו|בנותיו|בניהם|בנותיהם|צאצאיו|צאצאיהם)"
        r"\s*(?:הם|הן|היו|הינם)?\s*[:,]?\s*(?P<rest>[^.\n]+)", h_list_of_links("parent", "child", 0.75, True))
    add("siblings_list", r"(?<![א-ת])(?P<w>אחיו ואחיותיו|אחיו|אחיותיו|אחיה|אחיותיה)\s*(?:הם|הן|היו|הינם)\s*[:,]?\s*(?P<rest>[^.\n]+)",
        h_list_of_links("sibling", "rel", 0.7, True))
    # בנו הבכור [[X]] / בתו, [[Y]]
    add("child_is", r"(?<![א-ת])(?P<w>בנו|בתו|בנם|בתם|בנה|בתה)" + ORD + r"\s*,?\s*(?:הוא|היא)?\s*,?\s*" + NOTOF + r"(?P<rest>" + LINK(1) + r")",
        h_list_of_links("parent", "child", 0.75, True))
    # X, נכדו של Y / נינו של
    add("grandparent_of", r"(?<![א-ת])(?P<w>נכדו|נכדתו|נכדם|נכדתם|נינו|נינתו)\s+של\s+" + REF(1), h_grandparent_of)
    # סבו מצד אביו, X
    add("grandparent_is", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>סבו|סבתו|סבה|סבתה)(?:\s+מצד\s+(?P<side>אביו|אמו|אביה|אמה|האב|האם))?"
        + VERB + r"\s*,?\s*" + NOTOF + REF(1), h_grandparent_is)
    # X, סבו של Y
    add("grandparent_rev", r"(?<![א-ת])(?P<w>סבו|סבתו|סבה|סבתה)\s+של\s+" + REF(1), h_grandparent_rev)
    # X, חתנו של Y
    add("child_in_law_of", r"(?<![א-ת])(?P<w>חתנו|כלתו|חתנם|כלתם)\s+של\s+" + REF(1), h_child_in_law_of)
    # חותנו, X
    add("parent_in_law_is", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>חותנו|חמיו|חמותו|חותנתו|חותנה|חמיה|חמותה|חותנתה)" + VERB + r"\s*,?\s*" + NOTOF + REF(1),
        h_parent_in_law_is)
    # חתנו, [[X]]
    add("child_in_law_is", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>חתנו|כלתו|חתנם|כלתם)" + VERB + r"\s*,?\s*" + NOTOF + LINK(1), h_child_in_law_is)
    # גיסו של
    add("sibling_in_law_of", r"(?<![א-ת])(?P<w>גיסו|גיסתו|גיסה|גיסם)\s+של\s+" + REF(1), h_sibling_in_law)
    # דודו של / אחיינו של / דודו, X
    add("uncle_of", r"(?<![א-ת])(?P<w>דודו|דודתו|דודה|דודם)\s+של\s+" + REF(1), h_uncle_of)
    add("nephew_of", r"(?<![א-ת])(?P<w>אחיינו|אחייניתו|אחיינה|אחיינם)\s+של\s+" + REF(1), h_nephew_of)
    add("uncle_is", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>דודו|דודתו)" + VERB + r"\s*,?\s*" + NOTOF + LINK(1), h_uncle_is)
    return P
