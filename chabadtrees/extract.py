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
    "הבחור", "הת'", "הנגיד", "המשפיעה", "השליחה", "האדמו\"ר", "הרה\"ג", "הגה\"צ", "הרה\"צ",
]
FEMALE_HONORIFICS = {"מרת", "הרבנית", "הגברת", "גב'", "המשפיעה", "השליחה"}
MALE_HONORIFICS = {"הרב", "רבי", "ר'", "הרה\"ח", "הרה\"ג", "הרה\"ק", "הרה\"צ", "הרה\"ת", "הגה\"ח", "הגה\"ק", "הגה\"צ",
                   "הגאון", "הצדיק", "החסיד", "התמים", "השליח", "המשפיע", "הר\"ר", "מוהר\"ר", "ר\"ר", "רבינו", "הרבי",
                   "מר", "כ\"ק", "אדמו\"ר", "האדמו\"ר", "הבחור", "הת'", "הנגיד", "הרב הגאון", "הרב החסיד", "הרב הצדיק", "המקובל", "הדיין"}
STOPWORDS = {
    "של", "אשר", "היה", "הייתה", "היתה", "שהיה", "שהייתה", "הוא", "היא", "אשת", "בן", "בת", "את", "על", "אל", "עם", "אצל",
    "לפני", "אחרי", "ו", "רב", "ראש", "אב\"ד", "רבה", "ממלא", "מחבר", "משפיע", "שליח", "נולד", "נולדה", "נפטר", "נפטרה",
    "התחתן", "התחתנה", "נישא", "נישאה", "נשא", "נשאה", "נשוי", "נשואה", "בעיר", "בעיירה", "בכפר", "מהעיר", "בשנת", "בשנה", "ביום", "בליל", "בערב", "מן", "מבני", "מגדולי",
    "מחשובי", "מזקני", "מראשי", "מתלמידי", "משפחת", "לבית", "ואשתו", "ואמו", "ואביו", "ובנו", "ובתו", "וילדיו", "וכן", "גם",
    "אך", "אבל", "כי", "אם", "לא", "אין", "יש", "עוד", "כל", "בכל", "אחד", "אחת", "שני", "שתי", "רק", "כבר", "עדיין", "כאשר",
    "בו", "בה", "לו", "לה", "בהם", "להם", "שם", "כאן", "אז", "שהוא", "שהיא", "כדי", "לאחר", "בעת", "בזמן", "בתקופת",
    "הראשון", "הראשונה", "השני", "השנייה", "השניה", "השלישי", "השלישית", "הרביעי", "החמישי", "האחרון", "האחרונה", "הנוכחי",
    "אשה", "אישה", "לאשה", "לאישה", "איש", "ילד", "ילדה", "ילדים", "בנים", "בנות", "נכדים", "צאצאים",
}
STOPWORDS |= wt.SUFFIX_WORDS
_TOKEN_EXCLUDE = {"של", "בן", "בת", "על", "עם", "את", "מן", "או", "גם", "לא", "זה", "כל", "אל", "הוא", "היא", "רב", "ראש", "אב\"ד"}
# מילים שמופיעות בכותרות ערכים אבל אינן שמות של אנשים (לא ייכנסו ללקסיקון השמות)
NON_NAME_WORDS = {"אשה", "אישה", "ילדים", "בנים", "בנות", "נכדים", "צאצאים", "משפחה", "בית", "עיר", "שנה", "שנים", "ימים", "חודש",
                  "יום", "ליל", "ערב", "בוקר", "אב", "אם", "אח", "אחות", "ספר", "ניגון", "שיחה", "מאמר", "קונטרס", "ישיבה", "כנסת",
                  "מדרש", "תורה", "חסידות", "חסיד", "רבנית", "גאון", "צדיק", "קדוש", "מלך", "נשיא", "ראש", "סגן", "חבר", "עסקן",
                  "שליח", "משפיע", "משגיח", "מנהל", "מורה", "מלמד", "סופר", "דיין", "שוחט", "חזן", "גבאי", "מזכיר", "עורך", "זמר",
                  "צייר", "רופא", "דין", "מהנדס", "פרופסור", "דוקטור", "קצין", "חייל", "שבוי", "אסיר", "עולה", "ניצול", "בעל",
                  "אשת", "אלמנת", "כלת", "חתן", "בעלת", "סבא", "סבתא", "דודה", "נכד", "נכדה", "נין", "שם", "שמות", "תולדות",
                  "פירושונים", "צאצאי", "רשימת", "בני", "בנות", "חסידי", "תלמידי", "אנשי", "רבני", "שלוחי", "עולים", "ביתו", "מ", "ב", "ל"}
BOLD3 = "'" * 3
BOLD2 = "'" * 2

LINK_TPL = r"\[\[(?P<t{n}>[^\[\]|#]+?)(?:#[^\[\]|]*)?(?:\|(?P<d{n}>[^\[\]]*))?\]\]"
HON_TPL = r"(?P<h{n}>(?:(?:" + "|".join(re.escape(h) for h in HONORIFICS) + r")\s+)*)"
NAME_WORD = r"[א-ת][א-ת'\"\-]*"
_STOP_CORE = ["של", "אשר", "היה", "הייתה", "היתה", "הוא", "היא", "את", "על", "עם", "אצל", "בן", "בת", "וכן", "גם", "כי", "אך", "אבל",
              "נולד", "נולדה", "נפטר", "נפטרה", "התחתן", "התחתנה", "נישא", "נישאה", "נשא", "נשאה", "נשוי", "נשואה", "ז\"ל", "זצ\"ל", "ע\"ה", "הי\"ד", "נ\"ע", "שליט\"א",
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
_PRE_LINK_RE = re.compile(r"(?P<pre>.*?)(?P<hon>(?:(?:" + _HON_ALT + r")\s+)*)\[\[(?P<t>[^\[\]|#]+?)(?:#[^\[\]|]*)?(?:\|(?P<d>[^\[\]]*))?\]\]\s*[,–\-:(]?\s*$", re.S)
_PRE_NAME_RE = re.compile(r"(?P<pre>.*?)(?<![א-ת])[לבומכ]?(?P<hon>(?:(?:" + _HON_ALT + r")\s+)+)(?P<n>" + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,2})\s*[,–\-:(]\s*$", re.S)
# בלי פסיק ("התחתן עם מרת פעשה הדסה הלפרין בתו של") – רק בריצה מלאה, כשהלקסיקון מאמת את השם
_PRE_NAME_LOOSE_RE = re.compile(r"(?P<pre>.*?)(?<![א-ת])[לבומכ]?(?P<hon>(?:(?:" + _HON_ALT + r")\s+)+)(?P<n>" + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,3})\s*[,–\-:(]?\s*$", re.S)
_FILE_LINK_RE = re.compile(r"\[\[(?:קובץ|תמונה|מדיה|File|Image|Media):(?:[^\[\]]|\[\[[^\[\]]*\]\])*\]\]", re.I)
_NAMED_AFTER_RE = re.compile(r"(?:על\s+שם|ע\"ש|לזכר|נקרא(?:ת|ו|ה)?\s+(?:על\s+)?שם|שמו\s+על\s+שם|שמה\s+על\s+שם)\s*$")
_MARRIED_OBJECT_RE = re.compile(r"(?:נשא|נשוי|התחתן|נישא)(?:\s+לאי?שה)?\s+(?:את\s+|ל|עם\s+)(?P<hon>(?:(?:" + _HON_ALT + r")\s+)*)(?P<n>"
                                + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,2})\s*,?\s*$")


def _primary_gender(title: str, first_text: str) -> str | None:
    """מגדר הדף מהכותרת ומהמשפט הראשון (לא מפעלים על הבנות בהמשך הדף): "הרבנית X", "(אשת ...)", "נולדה"."""
    t = wt.normalize_quotes(title)
    words = t.split()
    if words and words[0] in FEMALE_HONORIFICS:
        return "f"
    if words and words[0] in MALE_HONORIFICS:
        return "m"
    dis = wt.disambiguator(t)
    if dis:
        if re.match(r"(?:אשת|בת|אם|כלת|נכדת|אחות|אלמנת|סבת|דודת|נינת)\s", dis + " "):
            return "f"
        if re.match(r"(?:בן|אב|אבי|חתן|נכד|אח|בעל|סב|דוד|נין)\s", dis + " "):
            return "m"
    m = re.search(r"(?<![א-ת])(נולד|נולדה|נפטר|נפטרה|היה|הייתה|היתה|הוא|היא|כיהן|כיהנה|שימש|שימשה|למד|למדה|נישא|נישאה|התחתן|התחתנה)(?![א-ת])", first_text)
    if m:
        w = m.group(1)
        return "f" if (w.endswith("ה") and w not in ("היה", "כיהן")) or w == "היא" else "m"
    return None


_OBJECT_TAIL_RE = re.compile(r"(?:(?<![א-ת])(?:של|את|עם)\s*|(?<![א-ת])[לבומכ])$")
# "* חנה ליבא, " בתחילת פריט רשימה – השם הוא הנושא של הביטוי שאחרי הפסיק
_PRE_LISTHEAD_RE = re.compile(r"^\s*[*#:;]+\s*(?P<hon>(?:(?:" + _HON_ALT + r")\s+)*)(?P<n>" + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,3})\s*[,–\-(.]\s*$")
# פסוקית לוואי בין הנושא לביטוי הקשר: "לר' ברוך יהודה, שהיה מראשי התנועה וחבר המועצה, בנו של ..." – מדלגים עליה
_TRAILING_CLAUSE_RE = re.compile(r",\s*(?:ש|אשר|כ|ה|מ)[^,]{3,140},\s*$")
_REL_WORDS = r"בתו|בנו|בתה|בנה|בתם|בנם|אחיו|אחותו|אחיה|אחותה|אימו|אימה|אמו|אביו|אמה|אביה|אמם|אימם|אביהם|אשתו|בעלה|רעייתו|נכדו|נכדתו"
_ORD2 = r"(?:\s+(?:הבכור|הבכורה|השני|השנייה|השניה|השלישי|השלישית|הרביעי|החמישי|הצעיר|הצעירה|היחיד|היחידה|הגדול|הגדולה|הקטן|הקטנה|הראשונה|הראשון))?"
# "... בתו חנה " / "אחיו ר' משה " לפני ביטוי הקשר: השם הלא-מקושר הוא הנושא (pre חמדני – מילת הקשר הקרובה ביותר)
_PRE_REL_NAME_RE = re.compile(r"(?P<pre>.*)(?<![א-ת])ו?[לבמ]?(?P<rel>" + _REL_WORDS + r")" + _ORD2 + r"\s*,?\s*(?:(?:היה|הייתה|היתה|הוא|היא)\s+)?"
                              r"(?P<hon>(?:(?:" + _HON_ALT + r")\s+)*)(?P<n>" + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,3})\s*[,–\-:]?\s*$", re.S)
_REL_WORD_INFO = {   # מילת קשר → (סוג הקשר של השם אל הדף, מגדר השם)
    "בתו": ("child", "f"), "בנו": ("child", "m"), "בתה": ("child", "f"), "בנה": ("child", "m"), "בתם": ("child", "f"), "בנם": ("child", "m"),
    "אחיו": ("sibling", "m"), "אחותו": ("sibling", "f"), "אחיה": ("sibling", "m"), "אחותה": ("sibling", "f"),
    "אמו": ("parent", "f"), "אביו": ("parent", "m"), "אמה": ("parent", "f"), "אביה": ("parent", "m"),
    "אימו": ("parent", "f"), "אימה": ("parent", "f"), "אמם": ("parent", "f"), "אימם": ("parent", "f"), "אביהם": ("parent", "m"),
    "אשתו": ("spouse", "f"), "רעייתו": ("spouse", "f"), "בעלה": ("spouse", "m"), "נכדו": (None, "m"), "נכדתו": (None, "f"),
}
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
    alias: bool = False              # צד אחד נפתר משם-תצוגה לערך (לא מקישור) – ראיה חלשה יותר
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


_HON_BEFORE_RE = re.compile(r"(?:^|[\s,;:(–\-])((?:" + "|".join(re.escape(h) for h in sorted(HONORIFICS, key=len, reverse=True)) + r")(?:\s+(?:"
                            + "|".join(re.escape(h) for h in HONORIFICS) + r"))*)\s*$")


def honorific_before(text_before: str) -> str | None:
    """מגדר לפי תואר שצמוד לשם ("הרב [[X]]"), לא תואר שמופיע במקרה קודם במשפט ("הרב שלום, רב ב[[ניו יורק]]")."""
    m = _HON_BEFORE_RE.search(wt.normalize_quotes(text_before))
    return _gender_from_honorific(m.group(1)) if m else None


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
    def __init__(self, config: dict, known_persons: set[str] | None = None, aliases: dict[str, str] | None = None,
                 common_words: set[str] | None = None):
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
        # כשיש רשימה מלאה של דפי אישים (ריצה מלאה), קישור לדף שאינו ברשימה נחשב "לא אדם" ברשימות ובתבניות
        self.strict_persons = len(self.known) >= 1000
        # לקסיקון של מילות-שם מכל כותרות הערכים ("מנחם", "מענדל", "וילהלם"...): שם לא-מקושר נגזר במילה שאינה שם
        self.name_tokens: set[str] = set()       # כל מילות השמות בכותרות
        self.given_tokens: set[str] = set()      # המילה הראשונה בשם (שם פרטי)
        self.surname_tokens: set[str] = set()    # המילה האחרונה בשם של שתי מילים ומעלה (שם משפחה)
        if self.strict_persons:
            for t in self.known:
                words = [w.strip("(),.;:'\"") for w in wt.normalize_quotes(wt.display_name(t)).replace("-", " ").split()]
                # "אשר", "ברוך" הם גם מילות קישור/ברכה – לא מסננים לפי STOPWORDS, רק לפי רשימה קצרה
                words = [w for w in words if len(w) >= 2 and w not in wt.HONORIFIC_WORDS and w not in wt.SUFFIX_WORDS
                         and w not in _TOKEN_EXCLUDE and w not in RELATION_NOUNS and w not in NON_NAME_WORDS]
                self.name_tokens.update(words)
                if words:
                    self.given_tokens.add(words[0])
                if len(words) >= 2:
                    self.surname_tokens.add(words[-1])
            self.surname_tokens -= self.given_tokens
        # מילים שכיחות בקורפוס שאינן מילות-שם (פעלים, שמות עצם) – עוצרות שם לא-מקושר; מילה נדירה אחרי שם היא כנראה שם משפחה
        self.common_words: set[str] = set(common_words or ())
        self._pending: list[Relation] = []

    _MAIDEN_RE = re.compile(r"^(?P<n>.+?)\s+(?:למשפחת|לבית|ממשפחת|מבית)\s+(?P<s>[א-ת][א-ת'\"\-]+)")

    def unlinked_name(self, raw: str, honorific: str = "") -> str:
        """שם לא-מקושר נקי.

        בריצה מלאה (יש לקסיקון): המילה הראשונה חייבת להיות מילת-שם מכותרות הערכים (או לבוא אחרי תואר),
        וההמשך נמשך כל עוד המילים הן מילות-שם או מילים נדירות (שם משפחה בלי ערך) – "חנה מפעילה פעילות" → "חנה",
        "שימשה כמחנכת" → "", "יהודה בלוי" → "יהודה בלוי". "אסתר למשפחת וולף" → "אסתר לבית וולף".
        """
        raw = wt.normalize_quotes(raw or "").replace(BOLD3, " ").replace(BOLD2, " ").strip()
        maiden = ""
        mm = self._MAIDEN_RE.match(raw)
        if mm:
            raw, maiden = mm.group("n"), mm.group("s").strip(",.;:()")
        name = clean_unlinked_name(raw)
        if self.name_tokens and name:
            words = name.split()
            first = words[0].strip("(),.;:'\"")
            raw_first = next((w.strip("(),.;:'\"") for w in raw.split()
                              if w.strip("(),.;:'\"") not in wt.HONORIFIC_WORDS and w.strip("(),.;:'\"") not in wt.SUFFIX_WORDS), "")
            if raw_first != first:
                return ""
            second = words[1].strip("(),.;:'\"") if len(words) > 1 else ""
            if first not in self.given_tokens:
                if first in self.name_tokens and second in self.name_tokens:
                    pass      # "זיסל חנה" – מילה שמוכרת רק כשם משפחה, אבל אחריה שם
                elif second in self.surname_tokens and len(first) >= 3 and first not in self.common_words \
                        and first not in NON_NAME_WORDS and first not in STOPWORDS and not first.startswith("ו"):
                    pass      # "מתיה לרר" – שם פרטי שלא מופיע בכותרות, אבל אחריו שם משפחה מוכר
                elif not honorific or first in self.common_words or first.startswith(("מ", "ו")) or len(first) < 3 or first in NON_NAME_WORDS:
                    # מילה לא מוכרת: מתקבלת רק אחרי תואר ("הרב עמיחי"), ולא אם היא נראית כמקום ("מנדבורנא") או מילה שכיחה
                    return ""
            out = [words[0]]
            for w in words[1:]:
                bare = w.strip("(),.;:'\"")
                if bare in self.name_tokens or (bare not in self.common_words and bare not in NON_NAME_WORDS and len(bare) >= 3
                                               and not bare.startswith("ו")):
                    out.append(w)
                else:
                    break
            name = " ".join(out)
        if name and maiden and maiden not in name.split():
            name = f"{name} לבית {maiden}"
        return name

    def plain_name_ok(self, name: str) -> bool:
        """שם של מילה אחת בלי תואר מתקבל רק אם הוא שם פרטי מוכר (ריצה מלאה) – "אשתו רחל"."""
        return bool(self.name_tokens) and name in self.given_tokens and not name.startswith("ה")

    # ---------------------------------------------------------------- ציבורי
    def extract(self, title: str, wikitext_raw: str) -> tuple[list[Relation], dict]:
        """מחזיר (רשימת קשרים, פרטי האדם: gender/born/died/categories/surname)."""
        text = wt.normalize_quotes(wt.strip_comments(wikitext_raw or ""))
        text = _FILE_LINK_RE.sub(" ", text)      # כיתובי תמונות ("אהל הרבניות: הרבנית X (אשת אדמו"ר Y)...") אינם משפטים
        info = {"title": title, "gender_votes": {"m": 0, "f": 0}, "born": None, "died": None,
                "categories": wt.categories(text), "surname": wt.surname_of(title),
                "primary_gender": _primary_gender(title, wt.plain(wt.strip_templates(text))[:400])}
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
        self._gender_votes(body[:4000], info)      # מגדר הדף ידוע כבר במשפט הראשון
        tokenized, refs = wt.tokenize_refs(body)
        for sec_title, _level, sec_text in wt.sections(tokenized):
            in_family_section = bool(re.search(r"משפח|ילדי|צאצא|קרוב|הורי|נישוא|בני|בנות", sec_title))
            list_context = ""
            for line in sec_text.split("\n"):
                stripped = wt.plain(wt.remove_ref_tokens(line)).strip(" :;'")
                m_ctx = re.fullmatch(r"(ילדיו|ילדיה|ילדיהם|בניו|בנותיו|צאצאיו|צאצאיה|אחיו|אחיותיו|אחיו ואחיותיו|נכדיו|הוריו|חתניו|כלותיו|בניו ובנותיו|בני משפחתו)", stripped)
                if m_ctx:
                    list_context = m_ctx.group(1)
                    continue
                if not line.lstrip().startswith(("*", "#")) and stripped:
                    list_context = ""
                units = [line] if line.lstrip().startswith(("*", "#", ";", ":")) else wt.split_sentences(line)
                for sentence in units:
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
            gender = honorific_before(fragment[max(0, span[0] - 40):span[0]])
            if self.strict_persons and target not in self.known and not gender:
                continue      # קישור לדף שאינו אישיות (מקום, מוסד) – לא אדם
            out.append((target, self._has_article(target), gender, wt.display_name(target)))
        if not links:
            name = self.unlinked_name(wt.plain(fragment), _gender_from_honorific(wt.plain(fragment)) or "")
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
        if kind == "name":
            self._alias_used = True
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
                if _NAMED_AFTER_RE.search(clean[max(0, m.start() - 30):m.start()]):
                    continue      # "נקראה על שם אשת אדמו"ר הצמח צדק" – לא קשר של הדף
                self._doubtful_link = False
                self._alias_used = False
                self._pending = []
                rels = list(handler(self, m, title, clean))
                if self._alias_used:
                    for rel in rels + self._pending:
                        rel.alias = True
                        rel.confidence = min(rel.confidence, 0.7)
                pending, self._pending = self._pending, []
                seen_p = set()
                for rel in pending:
                    key = (rel.person, rel.relative, rel.relation)
                    if key not in seen_p and not any((r.person, r.relative, r.relation) == key for r in rels):
                        seen_p.add(key)
                        rel.pattern = name + "+subject"
                        rels.append(rel)
                for rel in rels:
                    if self._doubtful_link and not rel.pattern:
                        rel.confidence = min(rel.confidence, 0.35)
                    rel.source_page = title
                    rel.evidence = wt.plain(clean).strip()
                    rel.refs = wt.refs_in(sentence, refs)
                    rel.pattern = rel.pattern or name
                    rel.confidence = min(rel.confidence, 0.95) if rel.confidence else base_conf
                    if not rel.relative_has_article or not rel.person_has_article:
                        rel.confidence -= 0.15
                    rel.confidence = round(max(rel.confidence, 0.2), 2)
                    add(rel)
        # רשימות בקטעי משפחה: "* [[X]] – בנו", או תחת "ילדיו:"
        if (in_family_section or list_context) and clean.lstrip().startswith(("*", "#")):
            self._from_list_line(title, clean, sentence, refs, add, sec_title, list_context)

    _SPOUSE_LINK_RE = re.compile(r"(?<![א-ת])(?:אשת|אשתו של|רעיית|אלמנת|נישאה ל|נשואה ל|בעלה|בעלה של|נשוי ל|התחתן עם|התחתנה עם)\s*"
                                 r"(?:(?:" + _HON_ALT + r")\s+)*" + _idx(LINK_TPL, 9))

    def _from_list_line(self, title: str, line: str, raw: str, refs: list[str], add, sec_title: str, list_context: str = "") -> None:
        links = wt.wikilinks(line)
        # "* פלונית – אשת [[בעלה]]" / "* [[פלונית]] – נישאה ל[[בעלה]]": הקישור אחרי מילת הנישואין הוא בן הזוג, לא הילד
        sm = self._SPOUSE_LINK_RE.search(line)
        if sm:
            spouse_target = wt.normalize_title(sm.group("t9"))
            links = [l for l in links if l[0] != spouse_target]
            if not links:
                head = re.split(r"\s+[–\-]\s+|,|\(|:|\.\s|(?<![א-ת])(?:אשת|נישאה|נשואה|בעלה)", wt.plain(line).lstrip("*# ").strip(), maxsplit=1)[0]
                name = self.unlinked_name(head, _gender_from_honorific(head) or "")
                if name:
                    ctx = list_context or sec_title
                    explicit = bool(re.search(r"ילדי|צאצא|בניו|בנותיו|בני משפחתו", ctx) or re.search(r"(?<![א-ת])(?:בתו|בנו)(?![א-ת])", line))
                    implicit = not explicit and bool(re.fullmatch(r"משפחת[וה]?", ctx.strip())) and not re.search(
                        r"(?<![א-ת])(?:אביו|אמו|אחיו|אחותו|סבו|סבתו|אשתו|בעלה|חותנו|חמיו|גיסו|דודו|נכדו|נכדתו|אחיה|אביה|אמה)(?![א-ת])", line)
                    if explicit or implicit:
                        g = "f" if re.search(r"אשת|נישאה|נשואה|בעלה|בתו", line) else "m"
                        add(Relation(person="~" + name, relative=title, relation="parent", source_page=title, evidence=wt.plain(line).strip(),
                                     refs=wt.refs_in(raw, refs), method="list", pattern="list:spouse-link" + ("" if explicit else ":implicit"),
                                     confidence=0.6 if explicit else 0.55,
                                     person_has_article=False, person_gender=g, person_name=name, relative_name=wt.display_name(title)))
                        add(Relation(person="~" + name, relative=spouse_target, relation="spouse", source_page=title, evidence=wt.plain(line).strip(),
                                     refs=wt.refs_in(raw, refs), method="list", pattern="list:spouse-link", confidence=0.6,
                                     person_has_article=False, relative_has_article=self._has_article(spouse_target), person_gender=g,
                                     relative_gender=("m" if g == "f" else "f"), person_name=name, relative_name=wt.display_name(spouse_target)))
                return
        if self.strict_persons:
            links = [l for l in links if l[0] in self.known or honorific_before(line[max(0, l[2][0] - 30):l[2][0]])]
        text = line
        if links:
            target, display, span = links[0]
            if _looks_like_year(target):
                return
            # מילת התפקיד: לפני השם, או מיד אחריו עד סימן פיסוק ("* [[X]] – בנו הבכור"), לא בתיאור ("... של אביו").
            # בין מילת התפקיד לקישור לא יכול להיות שם אחר: "* בנו, ר' אברהם – אביו של [[X]]" – X הוא הנכד
            tail = re.split(r"[,.;:()]", line[span[1]:], maxsplit=1)[0]
            head_part = line[:span[0]]
            hm = None
            for word in LIST_ROLES:
                hm = re.search(r"(?<![א-ת])" + re.escape(word) + r"(?![א-ת])(?!\s+של)", head_part)
                if hm:
                    break
            if hm:
                between = re.sub(r"\([^)]*\)", " ", head_part[hm.end():])
                words = [w for w in re.findall(NAME_WORD, between)
                         if w not in wt.HONORIFIC_WORDS and w not in STOPWORDS and w not in RELATION_NOUNS and w not in wt.SUFFIX_WORDS]
                if re.search(r"\s+של\s+|[–\-]", between) or len(words) >= 2:
                    return
            text = head_part + " " + tail
        else:
            head = re.split(r"\s+[–\-]\s+|,|\(|:|\.\s", wt.plain(line).lstrip("*# ").strip(), maxsplit=1)[0]
            name = self.unlinked_name(head, _gender_from_honorific(head) or "")
            if not name or (not _gender_from_honorific(head) and " " not in name and not self.plain_name_ok(name)):
                return
            resolved = self.resolve_alias(head.strip(), allow_names=False) or self.resolve_alias(name, allow_names=False)
            target = resolved or ("~" + name)
            display = wt.display_name(resolved) if resolved else name
            span = (0, 0)
            # מילת התפקיד: בראש ("* בנו, X" – מטופל בדפוס child_unlinked) או מיד אחרי השם עד סימן פיסוק ("* X – בנו")
            text = re.split(r"[,.;:()]", line.replace(head, "", 1), maxsplit=1)[0]
        role = None
        for word, rel in LIST_ROLES.items():
            if re.search(r"(?<![א-ת])" + re.escape(word) + r"(?![א-ת])(?!\s+של)", text):
                role = rel
                break
        ctx = list_context or sec_title
        if role is None and re.search(r"ילדי|צאצא|בניו|בנותיו|בני משפחתו", ctx):
            role = ("parent", "child")
        implicit_gender = None
        if role is None and not links and re.fullmatch(r"משפחת[וה]?", ctx.strip()):
            # "* רבקה. נשאה לשמואל" תחת "משפחתו" – בת (משתמע מהנישואין); "* משה – נשא את" – בן
            mv = re.search(r"(?<![א-ת])(נשאה|נישאה|נשואה|אשת|בעלה|נשא|נישא|נשוי|התחתן|התחתנה)(?![א-ת])", wt.plain(line))
            if mv:
                role = ("parent", "child")
                implicit_gender = "f" if mv.group(1) in ("נשאה", "נישאה", "נשואה", "אשת", "בעלה", "התחתנה") else "m"
        if role is None and re.search(r"^אחיו|אחיותיו", ctx):
            role = ("sibling", "rel")
        if role is None and re.search(r"^חתניו|^כלותיו", ctx):
            role = ("parent_in_law_rev", "child")
        if role is None:
            return
        relation, direction = role
        has = self._has_article(target) if not target.startswith("~") else False
        gender = honorific_before(line[max(0, span[0] - 30):span[0]]) if links else (_gender_from_honorific(head) or implicit_gender)
        if gender is None and direction == "child":
            if "בנותיו" in ctx or re.search(r"(?<![א-ת])בתו(?![א-ת])", text):
                gender = "f"
            elif "בניו" in ctx or re.search(r"(?<![א-ת])בנו(?![א-ת])", text):
                gender = "m"
        targets = [(target, has, gender)]
        if links:
            # קישורים נוספים המופרדים רק בפסיק/ו' – אותו תפקיד ("* אחיו: [[A]], [[B]] ו[[C]]")
            prev_end = links[0][2][1]
            for t2, _d2, span2 in links[1:]:
                between = line[prev_end:span2[0]]
                if not re.fullmatch(r"\s*(?:,|ו|,\s*ו|-|–|ו-)?\s*(?:" + _HON_ALT + r")?\s*", between) or _looks_like_year(t2):
                    break
                targets.append((t2, self._has_article(t2), _gender_from_honorific(between) or gender))
                prev_end = span2[1]
        for target, has, gender in targets:
            tname = wt.display_name(target) if not target.startswith("~") else target[1:]
            if direction == "child":
                rel = Relation(person=target, relative=title, relation=relation, source_page=title, evidence="",
                               person_has_article=has, person_gender=gender, person_name=tname,
                               relative_name=wt.display_name(title), method="list", confidence=0.8 if has else 0.6)
            else:
                rel = Relation(person=title, relative=target, relation=relation, source_page=title, evidence="",
                               relative_has_article=has, relative_gender=gender, relative_name=tname,
                               person_name=wt.display_name(title), method="list", confidence=0.8 if has else 0.6)
            if relation == "parent_in_law_rev":
                rel.relation, rel.person, rel.relative = "parent_in_law", target, title
                rel.person_name, rel.relative_name = tname, wt.display_name(title)
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
            if self.strict_persons and target not in self.known and not gender:
                self._doubtful_link = True
            return target, self._has_article(target), gender, wt.display_name(target)
        name = self.unlinked_name(gd.get(f"n{n}") or "", hon)
        if not name or _looks_like_year(name) or len(name) < 2:
            return None
        allow = not getattr(self, "_in_list", False)
        resolved = self.resolve_alias((hon + " " + name).strip(), allow) or self.resolve_alias(name, allow)
        if resolved:
            return resolved, True, gender, wt.display_name(resolved)
        if not hon and " " not in name and not self.plain_name_ok(name):
            # מילה בודדת בלי תואר – כנראה לא שם ("נשוי לאישה"); בריצה מלאה שם פרטי מוכר מתקבל ("אשתו רחל")
            return None
        return "~" + name, False, gender, name

    def subject_of(self, m: re.Match, page: str, chain_ok: bool = False, word_gender: str | None = None) -> tuple[str, bool, str | None, str]:
        """הנושא של ביטוי קשר: הקישור/השם שלפניו אם הוא באמת נושא, אחרת נושא הערך.

        "[[X]], בנו של [[Y]]" → X. "היה חתנו של [[A]], בעלה של [[B]]" → A הוא מושא של הביטוי הקודם
        ולכן לא נושא (אלא אם chain_ok – שרשרת יוחסין "בנו של Y, בנו של Z").
        """
        before = m.string[:m.start()]
        subj = self._subject_in(before, m, page, chain_ok, word_gender)
        if subj is None:
            stripped = _TRAILING_CLAUSE_RE.sub(", ", before)
            if stripped != before:
                subj = self._subject_in(stripped, m, page, chain_ok, word_gender)
        return subj if subj is not None else (page, True, None, wt.display_name(page))

    def _subject_in(self, before: str, m: re.Match, page: str, chain_ok: bool, word_gender: str | None):
        page_disp = wt.display_name(page)
        # "הרב יעקב זלמן בלוי (אביהם של [[X]])" – תמורה בסוגריים: השם שלפני הסוגריים הוא הנושא
        apposition = before.rstrip().endswith("(")
        lm = _PRE_LINK_RE.match(before)
        if lm:
            pre = lm.group("pre")
            is_object = bool(_OBJECT_TAIL_RE.search(pre)) and not apposition
            if is_object and word_gender and _gender_from_honorific(lm.group("hon") or "") == word_gender and self.page_gender_conflict(word_gender):
                is_object = False      # "התחתן עם מרת [[X]], בתו של [[Y]]" – X היא הבת, לא הערך (שהוא גבר)
            target = wt.normalize_title(lm.group("t"))
            known = (not self.known) or target in self.known
            if (not is_object or chain_ok) and wt.is_content_link(target) and (known or lm.group("hon")) and not _looks_like_year(target):
                return target, self._has_article(target), _gender_from_honorific(lm.group("hon") or ""), wt.display_name(target)
            return (page, True, None, page_disp) if is_object else None
        rm = _PRE_REL_NAME_RE.match(before)
        if rm and not _OBJECT_TAIL_RE.search(rm.group("pre")):
            hon = (rm.group("hon") or "").strip()
            name = self.unlinked_name(rm.group("n"), hon)
            if name and (hon or " " in name or self.plain_name_ok(name)) and not _looks_like_year(name):
                kind, g = _REL_WORD_INFO.get(rm.group("rel"), (None, None))
                g = _gender_from_honorific(hon) or g
                allow = not getattr(self, "_in_list", False)
                resolved = self.resolve_alias((hon + " " + name).strip(), allow) or self.resolve_alias(name, allow)
                subj = (resolved, True, g, wt.display_name(resolved)) if resolved else ("~" + name, False, g, name)
                page_t = (page, True, None, page_disp)
                if kind == "child":
                    r = _mk(subj, page_t, "parent", m, self, page, conf=0.7, person_gender=g)
                elif kind == "sibling":
                    r = _mk(subj, page_t, "sibling", m, self, page, conf=0.7, person_gender=g)
                elif kind == "parent":
                    r = _mk(page_t, subj, "parent", m, self, page, conf=0.7, relative_gender=g)
                elif kind == "spouse":
                    r = _mk(page_t, subj, "spouse", m, self, page, conf=0.7, relative_gender=g)
                else:
                    r = None
                if r:
                    self._pending.append(r)
                return subj
        mo = _MARRIED_OBJECT_RE.search(before)
        if mo:
            hon = (mo.group("hon") or "").strip()
            name = self.unlinked_name(mo.group("n"), hon)
            if not name:
                # מושא של "נשא את" הוא כמעט תמיד שם – גם אם אינו בלקסיקון ("רוחמה")
                raw = clean_unlinked_name(mo.group("n"))
                if raw and " " not in raw and raw not in self.common_words and raw not in STOPWORDS:
                    name = raw
            if name and not _looks_like_year(name) and name != page_disp:
                resolved = self.resolve_alias((hon + " " + name).strip(), False) or self.resolve_alias(name, False)
                if resolved:
                    return resolved, True, _gender_from_honorific(hon), wt.display_name(resolved)
                return "~" + name, False, _gender_from_honorific(hon), name
        lh = _PRE_LISTHEAD_RE.match(before)
        if lh:
            hon = (lh.group("hon") or "").strip()
            name = self.unlinked_name(lh.group("n"), hon)
            if name and (hon or " " in name or self.plain_name_ok(name)) and not _looks_like_year(name):
                resolved = self.resolve_alias((hon + " " + name).strip(), False) or self.resolve_alias(name, False)
                if resolved:
                    return resolved, True, _gender_from_honorific(hon), wt.display_name(resolved)
                return "~" + name, False, _gender_from_honorific(hon), name
        nm = _PRE_NAME_RE.match(before) or (_PRE_NAME_LOOSE_RE.match(before) if self.name_tokens else None)
        if nm:
            name = self.unlinked_name(nm.group("n"), nm.group("hon") or "")
            pre = nm.group("pre")
            is_object = bool(_OBJECT_TAIL_RE.search(pre)) and not apposition
            if is_object and word_gender and _gender_from_honorific(nm.group("hon") or "") == word_gender and self.page_gender_conflict(word_gender):
                is_object = False
            if name and (not is_object or chain_ok) and not (" " not in name and name.startswith("מ")):
                if name == page_disp or name in page_disp:
                    return page, True, None, page_disp
                allow = not getattr(self, "_in_list", False)
                resolved = self.resolve_alias((nm.group("hon") + " " + name).strip(), allow) or self.resolve_alias(name, allow)
                if resolved:
                    return resolved, True, _gender_from_honorific(nm.group("hon") or ""), wt.display_name(resolved)
                return "~" + name, False, _gender_from_honorific(nm.group("hon") or ""), name
        return None

    def page_gender_conflict(self, word_gender: str | None) -> bool:
        """המילה אומרת בת/בן על נושא הדף, אבל הדף עצמו כבר מסומן במגדר ההפוך."""
        if not word_gender or not self.current_info:
            return False
        primary = self.current_info.get("primary_gender")
        if primary:
            return primary != word_gender
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
        # הבהרות יחס ("אב אדמו\"ר שליט\"א", "בן אדמו\"ר המהר\"ש", "נין אדמו\"ר הצמח צדק") אינן כינויים
        if dis and not dis.startswith(("בן ", "בת ", "אשת ", "אחי ", "אחות ", "אבי ", "אב ", "אם ", "נכד ", "נכדת ", "נין ",
                                       "חתן ", "כלת ", "בעל ", "סב ", "סבת ", "דוד ", "גיס ")):
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


_MONTHS = r"תשרי|חשוון|חשון|מרחשוון|מרחשון|כסלו|טבת|שבט|אדר(?:\s+[אב]'?)?|ניסן|אייר|סיוון|סיון|תמוז|אב|מנחם\s+אב|אלול"
_DATE_RE = re.compile(r"(?:(?:[א-ת]'|[א-ת]\"[א-ת]|[א-ת]{1,2})\s*ב?)?(?:" + _MONTHS + r")(?:\s+(?:ה')?ת[א-ת]{0,3}\"[א-ת])?"
                      r"|ראש השנה|יום כיפור|יום הכיפורים|סוכות|שמחת תורה|חנוכה|פורים|פסח|שבועות|תשעה באב|ל\"ג בעומר|שבת|שבת קודש"
                      r"|\d{1,2} ב[א-ת]+")


def _looks_like_year(s: str) -> bool:
    """שנה או תאריך עברי ("תרפ\"ט", "כ' במנחם אב", "ל' בסיוון") – לא שם של אדם."""
    s = wt.normalize_quotes(s.strip())
    return bool(re.fullmatch(r"(?:ה')?ת[א-ת]{0,3}\"[א-ת]|\d{3,4}|שנת .*|[א-ת]{1,2}\"[א-ת]", s)) or bool(_DATE_RE.fullmatch(s))


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


def h_child_unlinked(self_, m, page, sentence):
    """בתו חנה / בנו, ר' יעקב אמסל (בלי קישור) → ילד של נושא הדף. רק בריצה מלאה, כשיש לקסיקון שמות."""
    if not self_.name_tokens:
        return []
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    w = m.group("w")
    g = "f" if w.startswith("בת") else "m"
    r = _mk(rel, (page, True, None, wt.display_name(page)), "parent", m, self_, page, conf=0.7, person_gender=g)
    return [r] if r else []


def h_married_daughter_of(self_, m, page, sentence):
    subj = self_.subject_of(m, page, word_gender="m")
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    r = _mk(subj, rel, "parent_in_law", m, self_, page, conf=0.75)
    return [r] if r else []


def h_child_construct(self_, m, page, sentence):
    """"מרת חנה מינסקי בת הרב יעקב מינסקי" → יעקב הורה של חנה."""
    if _MARRIED_BEFORE_RE.search(m.string[max(0, m.start() - 30):m.start()]):
        return []
    if not m.group("t1") and not (m.group("h1") or "").strip():
        return []      # "בן ישראל" / "בת 20" – בלי קישור או תואר לא מנחשים
    w = m.group("w")
    pg = "f" if w == "בת" else "m"
    subj = self_.subject_of(m, page, chain_ok=True, word_gender=pg)
    if subj[0] == page:
        return []      # בלי נושא מפורש לא מנחשים
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    r = _mk(subj, rel, "parent", m, self_, page, conf=0.7, person_gender=pg)
    return [r] if r else []


_MARRIED_BEFORE_RE = re.compile(r"(?:נשא|נשוי|התחתן|נישא)(?:\s+לאי?שה)?\s+(?:את\s+|ל|עם\s+)?$")


def h_child_of(self_, m, page, sentence):
    """X, בנו של Y [ושל Z] → Y (ו-Z) הורים של X."""
    out = []
    if _MARRIED_BEFORE_RE.search(m.string[max(0, m.start() - 30):m.start()]):
        return []      # "נשא את בתו של X" – מטופל כחותן
    w = m.group("w")
    pg = "f" if w.startswith("בת") else "m"
    subj = self_.subject_of(m, page, chain_ok=True, word_gender=pg)
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
    gd = m.groupdict()
    # "נולד בין [[X]] ל[[Y]]" – סדר לידה בין אחים, לא הורים
    if re.search(r"(?<![א-ת])בין\s", m.group(0)):
        return out
    if (gd.get("k1") or "").startswith("הורי") and gd.get("n1") and not gd.get("t2") and not gd.get("n2"):
        # "להוריו משה מאיר וחנה רבקה": מפצלים ב-ו' האחרונה שלפני שם
        parts = re.split(r"\s+ו(?=[א-ת]{2,})", gd["n1"])
        if len(parts) == 2 and all(clean_unlinked_name(x) for x in parts):
            for i, (nm, g) in enumerate(zip(parts, ("m", "f"))):
                name = clean_unlinked_name(nm)
                r = _mk(subj, ("~" + name, False, g, name), "parent", m, self_, page, conf=0.8, relative_gender=g)
                if r:
                    out.append(r)
            return out
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
    """X, אביו של Y [ו-Z] → X הורה של Y (ו-Z)."""
    w = m.group("w")
    g = "m" if w.startswith("אב") else "f"
    subj = self_.subject_of(m, page, word_gender=g)
    out = []
    for n in (1, 2):
        rel = self_.person_from_match(m, n)
        if rel:
            r = _mk(rel, subj, "parent", m, self_, page, conf=0.7, relative_gender=g)
            if r:
                out.append(r)
    return out


def h_spouse_verb(self_, m, page, sentence):
    w = m.group("w")
    pg = "f" if w in ("נשואה", "נישאה", "התחתנה", "נשאה") else "m"
    subj = self_.subject_of(m, page, word_gender=pg)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
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
    w = m.group("w")
    pg = "m" if w.startswith("בעל") else "f"
    subj = self_.subject_of(m, page, word_gender=pg)
    rel = self_.person_from_match(m, 1)
    if not rel:
        return []
    if subj[0] == page and self_.page_gender_conflict(pg):
        return []
    r = _mk(subj, rel, "spouse", m, self_, page, conf=0.8, person_gender=pg, relative_gender=("f" if pg == "m" else "m"))
    return [r] if r else []


def h_spouse_construct(self_, m, page, sentence):
    """X, אשת [[Y]] / אשת הרב Y. שם לא-מקושר מתקבל רק עם תואר לפניו."""
    if not m.group("t1") and not (m.group("h1") or "").strip():
        return []
    return h_spouse_of(self_, m, page, sentence)


def h_sibling_of(self_, m, page, sentence):
    out = []
    w = m.group("w")
    pg = "f" if w.startswith("אחות") else "m"
    subj = self_.subject_of(m, page, word_gender=pg)
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
        # "(בנו ר' [[X]])" – תיאור, לא פריט (גם כשיש קישור בתוכו); סוגריים בתוך קישור ("[[X (בן Y)]]") נשארים
        links_found = re.findall(r"\[\[[^\[\]]*\]\]", rest)
        for i, lk in enumerate(links_found):
            rest = rest.replace(lk, f"\x00{i}\x00", 1)
        rest = re.sub(r"\([^()]*\)", " ", rest)
        for i, lk in enumerate(links_found):
            rest = rest.replace(f"\x00{i}\x00", lk)
        parts = re.split(r"(\[\[[^\[\]]*\]\])", rest)
        cut = next((i for i, part in enumerate(parts) if i % 2 == 0 and re.search(r"\s+[–\-]\s+", part)), None)
        if cut is not None:                                          # "[[A]], [[B]] – רב בעיר [[C]]"
            parts[cut] = re.split(r"\s+[–\-]\s+", parts[cut], maxsplit=1)[0]
            rest = "".join(parts[:cut + 1])
        w = m.groupdict().get("w") or ""
        for target, display, span in wt.wikilinks(rest):
            if _looks_like_year(target):
                continue
            hon = rest[max(0, span[0] - 30):span[0]]
            g = _gender_from_honorific(" ".join(hon.split()[-2:]))
            if self_.strict_persons and target not in self_.known and not g:
                continue
            if rest[max(0, span[0] - 1):span[0]] in ("ב", "מ", "ל") and not g:
                continue      # "ב[[כפר חב"ד]]" – מקום, לא אדם
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
    w = re.sub(r"\s+", " ", m.group("w"))
    side_word = m.groupdict().get("side") or ""
    if not side_word and " " in w:
        side_word = w.split(" ", 1)[1]          # "אם אמו" – סבתא מצד האם
    side = "father" if side_word.startswith("אב") else ("mother" if side_word.startswith("אמ") or side_word.startswith("האם") else None)
    rg = "f" if w.startswith("סבת") or w.startswith("אם ") else "m"
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

    # נשא את בתו של X / התחתן עם בת הרב X → X חותנו (בת הזוג לא נקראת בשם)
    add("married_daughter_of", r"(?<![א-ת])(?P<w>נשא|התחתן|נישא|נשוי)(?:\s+לאי?שה)?\s+(?:את\s+|ל|עם\s+)?(?:בתו|בת|בתה)\s+(?:של\s+)?" + REF(1), h_married_daughter_of)
    # X, בנו של Y ושל Z
    add("child_of", r"(?<![א-ת])(?P<w>בנו|בנה|בתו|בתה|בנם|בתם)" + ORD + r"\s+של\s+" + REF(1) +
        r"(?:\s*,?\s*ו(?:של\s+)?" + REF(2) + r")?", h_child_of)
    # נולד ... לאביו X ולאמו Y / להוריו X ו-Y / ל[[X]] ול[[Y]]
    add("born_to", r"נולד(?:ה)?(?![א-ת])[^.\n]{0,120}?(?<![א-ת])ל(?P<k1>אביו|אביה|הוריו|הוריה)\s*,?\s*" + REF(1) +
        r"(?:[^.\n]{0,40}?(?<![א-ת])ול(?P<k2>אמו|אמה|אימו|אימה)?\s*,?\s*" + REF(2) + r")?", h_born_to)
    add("born_to_links", r"נולד(?:ה)?(?![א-ת])[^.\n]{0,120}?(?<![א-ת])ל(?P<k1>)" + LINK(1) + r"\s*,?\s*ו(?:ל)?(?P<k2>)" + REF(2), h_born_to)
    # נולד ... לר' X / להרב X (בלי קישור, עם תואר; האם אופציונלית "ולאמו/ולמרת Y")
    add("born_to_hon", r"נולד(?:ה)?(?![א-ת])[^.\n]{0,120}?(?<![א-ת])ל(?P<k1>)(?P<h1>(?:(?:" + _HON_ALT + r")\s+)+)" + _idx(UNLINKED_TPL, 1)
        + r"(?:\s*,?\s*ו(?:ל)?(?P<k2>אמו|אמה|אימו|אימה)?\s*,?\s*" + REF(2) + r")?", h_born_to)
    # אביו, X / אמו הייתה X
    add("parent_is", r"(?:^|[,.;:()]\s*|(?<!מצד)\s+ו?)(?<!אחי )(?<!אחות )(?<!גיס )(?<!חותן )(?<!דוד )(?<!דודת )(?<!סב )(?<!סבת )(?<!אח )(?<!בן )(?<!בת )(?<!אם )(?<!אבי )"
        r"(?P<w>אביו|אמו|אביה|אמה|אביהם|אמם)" + VERB + r"\s*,?\s*" + NOTOF + REF(1), h_parent_is)
    # X, אביו של Y
    add("parent_of", r"(?<![א-ת])(?P<w>אביו|אמו|אביה|אמה|אביהם|אמם)\s+של\s+" + REF(1) + r"(?:\s*,?\s*ו(?:של\s+)?" + REF(2) + r")?", h_parent_of)
    # נשוי ל / נישאה ל / התחתן עם / נשא לאישה את
    add("spouse_verb", r"(?<![א-ת])(?P<w>נשוי|נשואה|נישא|נישאה|התחתן|התחתנה|נשא|נשאה)(?:\s+לאי?שה)?\s+(?:בזיווג\s+\S+\s+)?"
        r"(?:ל|עם\s+|את\s+)" + REF(1), h_spouse_verb)
    # אשתו, X / בעלה הוא X
    add("spouse_noun", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>אשתו|אישתו|רעייתו|זוגתו|בעלה|בן זוגה|בת זוגו|אשתו הראשונה|אשתו השנייה|אשתו השניה)"
        + VERB + r"\s*,?\s*" + NOTOF + REF(1), h_spouse_noun)
    # X, אשתו של Y / אלמנתו של
    add("spouse_of", r"(?<![א-ת])(?P<w>אשתו|אישתו|רעייתו|זוגתו|בעלה|אלמנתו|אלמנת)\s+של\s+" + REF(1), h_spouse_of)
    # X, אשת [[Y]] / אשת הרב Y (סמיכות) – קישור, או שם לא-מקושר עם תואר
    add("spouse_construct", r"(?<![א-ת])(?P<w>אשת|רעיית|אלמנת)\s+" + REF(1), h_spouse_construct)
    # X, אחיו של Y
    add("sibling_of", r"(?<![א-ת])(?P<w>אחיו|אחותו|אחיה|אחותה|אחיהם|אחותם)" + ORD + r"\s+של\s+" + REF(1) +
        r"(?:\s*,?\s*ו(?:של\s+)?" + REF(2) + r")?", h_sibling_of)
    # אחיו, X
    add("sibling_is", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>אחיו|אחותו|אחיה|אחותה)" + ORD + VERB + r"\s*,?\s*" + NOTOF + LINK(1), h_sibling_is)
    # ילדיו: [[X]], [[Y]] / בניו הם / אחיו הם
    _LIST_START = r"\s*(?:הם|הן|היו|הינם)?\s*(?::|,|(?=\s*(?:(?:" + _HON_ALT + r")\s+)*\[\[))\s*"
    add("children_list", r"(?<![א-ת])(?P<w>ילדיו|ילדיה|ילדיהם|בניו|בנותיו|בניהם|בנותיהם|צאצאיו|צאצאיהם)" + _LIST_START
        + r"(?P<rest>[^.\n]+)", h_list_of_links("parent", "child", 0.75, True))
    add("siblings_list", r"(?<![א-ת])(?P<w>אחיו ואחיותיו|אחיו|אחיותיו|אחיה|אחיותיה)" + _LIST_START + r"(?P<rest>[^.\n]+)",
        h_list_of_links("sibling", "rel", 0.7, True))
    # בנו הבכור [[X]] / בתו, [[Y]]
    add("child_is", r"(?<![א-ת])(?P<w>בנו|בתו|בנם|בתם|בנה|בתה)" + ORD + r"\s*,?\s*(?:הוא|היא)?\s*,?\s*" + NOTOF + r"(?P<rest>" + LINK(1) + r")",
        h_list_of_links("parent", "child", 0.75, True))
    # בתו חנה / בנו, ר' יעקב אמסל (בלי קישור; ריצה מלאה)
    add("child_unlinked", r"(?<![א-ת])(?P<w>בנו|בתו|בנם|בתם|בנה|בתה)" + ORD + r"\s*,?\s*(?:הוא|היא)?\s*,?\s*" + NOTOF
        + _idx(HON_TPL, 1) + _idx(UNLINKED_TPL, 1), h_child_unlinked)
    # X בת הרב Y / בן [[Y]] (סמיכות) – רק עם תואר או קישור
    add("child_construct", r"(?<![א-ת])(?P<w>בן|בת)\s+" + REF(1), h_child_construct)
    # X, נכדו של Y / נינו של
    add("grandparent_of", r"(?<![א-ת])(?P<w>נכדו|נכדתו|נכדם|נכדתם|נינו|נינתו)\s+של\s+" + REF(1), h_grandparent_of)
    # סבו מצד אביו, X
    add("grandparent_is", r"(?:^|[,.;:()]\s*|\s+ו?)(?P<w>סבו|סבתו|סבה|סבתה|אבי\s+אביו|אבי\s+אמו|אם\s+אביו|אם\s+אמו|אבי\s+אביה|אבי\s+אמה|אם\s+אביה|אם\s+אמה)"
        r"(?:\s+מצד\s+(?P<side>אביו|אמו|אביה|אמה|האב|האם))?"
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
