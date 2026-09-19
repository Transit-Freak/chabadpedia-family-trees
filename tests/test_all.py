"""בדיקות: פרסור, חילוץ, גרף, סידור, איתור עצים קיימים, וצינור מלא מול שרת מדומה (כולל 429)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from chabadtrees import wikitext as wt  # noqa: E402
from chabadtrees.api import ApiError, MediaWikiClient  # noqa: E402
from chabadtrees.config import load_config  # noqa: E402
from chabadtrees.extract import Extractor, clean_unlinked_name  # noqa: E402
from chabadtrees.graph import build_graph  # noqa: E402
from chabadtrees.layout import Marriage, TreeNode, layout_forest, ancestor_chart, TreeBuilder  # noqa: E402
from chabadtrees.render import chart_wikitext, symbol_for  # noqa: E402
from chabadtrees.pipeline import Store, fetch_pages, extract_all, graph_step, build_trees, build_ancestor_trees, resolve_links  # noqa: E402
from chabadtrees.existing import find_existing_trees, coverage  # noqa: E402

B3 = "'" * 3
CFG = load_config(path="/nonexistent")
ALTER = 'רבי שניאור זלמן מלאדי (אדמו"ר הזקן)'
TZEMACH = 'רבי מנחם מענדל שניאורסון (אדמו"ר הצמח צדק)'
KNOWN = {ALTER, TZEMACH, "הרבנית דבורה לאה", "רבי יהודה לייב שניאורסון", "הרבנית רבקה"}


def rels(title: str, text: str, known=KNOWN):
    ex = Extractor(CFG, known_persons=known)
    out, info = ex.extract(title, text)
    return [(r.relation, r.person, r.relative) for r in out], out, info


class TestWikitext(unittest.TestCase):
    def test_templates_and_links(self):
        tpls = wt.find_templates("{{אישיות|שם=פלוני|אב=[[א|ב]] ו[[ג]]}} טקסט {{x}}")
        self.assertEqual(tpls[0]["name"], "אישיות")
        self.assertEqual(tpls[0]["params"]["אב"], "[[א|ב]] ו[[ג]]")
        self.assertEqual([t for t, _d, _s in wt.wikilinks("[[א|ב]] [[קטגוריה:ג]] [[ד#ה]]")], ["א", "ד"])

    def test_sections_years_surname(self):
        secs = wt.sections("פתיח\n== משפחתו ==\nגוף\n=== ילדיו ===\nרשימה")
        self.assertEqual([s[0] for s in secs], ["", "משפחתו", "ילדיו"])
        self.assertEqual(wt.hebrew_year('נולד בכ"ף חשוון ה׳תרכ״א'), 'ה\'תרכ"א')
        self.assertEqual(wt.surname_of("רבי לוי יצחק שניאורסון"), "שניאורסון")
        self.assertEqual(wt.surname_of(ALTER), "")

    def test_clean_unlinked(self):
        self.assertEqual(clean_unlinked_name("ר' ברוך ולאמו מרת רבקה"), "ברוך")
        self.assertEqual(clean_unlinked_name("אברהם שניאורסון מקישינוב"), "אברהם שניאורסון")
        self.assertEqual(clean_unlinked_name("אהרן אלכסנדרוב ונכדתו"), "אהרן אלכסנדרוב")


class TestExtract(unittest.TestCase):
    def test_child_of_and_born_to(self):
        r, _, info = rels("רבי דובער שניאורי", f"{B3}רבי דובער שניאורי{B3} נולד בליאזנא, בנו של [[{ALTER}|אדמו\"ר הזקן]].")
        self.assertIn(("parent", "רבי דובער שניאורי", ALTER), r)
        r, out, _ = rels(ALTER, "נולד בי\"ח באלול ה'תק\"ה ב[[ליאזנא]] לאביו ר' ברוך ולאמו מרת רבקה.")
        self.assertIn(("parent", ALTER, "~ברוך"), r)
        self.assertIn(("parent", ALTER, "~רבקה"), r)
        self.assertNotIn("~יאזנא", [x[2] for x in r])
        genders = {o.relative: o.relative_gender for o in out}
        self.assertEqual(genders["~ברוך"], "m")
        self.assertEqual(genders["~רבקה"], "f")

    def test_preceding_link_is_subject(self):
        text = "* בתו [[הרבנית דבורה לאה]] נישאה לר' שלום שכנא.\n"
        r, _, _ = rels(ALTER, "== משפחתו ==\n" + text)
        self.assertIn(("spouse", "הרבנית דבורה לאה", "~שלום שכנא"), r)
        self.assertNotIn(("spouse", ALTER, "~שלום שכנא"), r)

    def test_object_link_is_not_subject_for_spouse(self):
        text = f"{B3}רבי מנחם מענדל הורנשטיין{B3} היה חתנו של [[{TZEMACH}|הצמח צדק]], בעלה של [[הרבנית דבורה לאה]]."
        r, _, _ = rels("רבי מנחם מענדל הורנשטיין", text)
        self.assertIn(("spouse", "רבי מנחם מענדל הורנשטיין", "הרבנית דבורה לאה"), r)
        self.assertIn(("parent_in_law", "רבי מנחם מענדל הורנשטיין", TZEMACH), r)
        self.assertNotIn(("spouse", TZEMACH, "הרבנית דבורה לאה"), r)

    def test_genealogical_chain(self):
        text = f"בנו של [[רבי יהודה לייב שניאורסון]], בנו הבכור של [[{TZEMACH}|הצמח צדק]]."
        r, _, _ = rels("פלוני", text)
        self.assertIn(("parent", "פלוני", "רבי יהודה לייב שניאורסון"), r)
        self.assertIn(("parent", "רבי יהודה לייב שניאורסון", TZEMACH), r)

    def test_list_context_and_gender_guard(self):
        text = f"{B3}רבי פלוני{B3} נולד בשנת ה'תר\"ם.\n== משפחתו ==\nילדיו:\n* [[הרבנית רבקה]] – נישאה לר' משה כהן.\n* [[רבי יהודה לייב שניאורסון]]\n"
        r, out, _ = rels("רבי פלוני", text)
        self.assertIn(("parent", "הרבנית רבקה", "רבי פלוני"), r)
        self.assertIn(("parent", "רבי יהודה לייב שניאורסון", "רבי פלוני"), r)
        self.assertIn(("spouse", "הרבנית רבקה", "~משה כהן"), r)
        self.assertNotIn(("spouse", "רבי פלוני", "~משה כהן"), r)

    def test_infobox_and_refs(self):
        text = "{{אישיות|אב=[[" + ALTER + "]]|ילדים=[[הרבנית רבקה]]<br>ר' זלמן|תאריך לידה=ה'תקל\"ד}}\nטקסט.<ref>מקור א</ref>"
        r, out, info = rels("רבי דובער שניאורי", text)
        self.assertIn(("parent", "רבי דובער שניאורי", ALTER), r)
        self.assertIn(("parent", "הרבנית רבקה", "רבי דובער שניאורי"), r)
        self.assertIn(("parent", "~זלמן", "רבי דובער שניאורי"), r)
        self.assertEqual(info["born"], 'ה\'תקל"ד')

    def test_nickname_alias(self):
        text = f"בנו של הצמח צדק."
        r, _, _ = rels("רבי יעקב שניאורסון", text)
        self.assertIn(("parent", "רבי יעקב שניאורסון", TZEMACH), r)

    def test_grandparent_side_not_parent(self):
        text = "סבו מצד אביו, [[רבי יהודה לייב שניאורסון]], היה רב."
        r, out, _ = rels("רבי פלוני", text)
        self.assertIn(("grandparent", "רבי פלוני", "רבי יהודה לייב שניאורסון"), r)
        self.assertNotIn(("parent", "רבי פלוני", "רבי יהודה לייב שניאורסון"), r)
        self.assertEqual([o.side for o in out if o.relation == "grandparent"], ["father"])


class TestGraph(unittest.TestCase):
    def test_corroboration_and_inference(self):
        pages = {"א": {"gender_votes": {"m": 1, "f": 0}, "born": None, "died": None, "categories": [], "surname": ""},
                 "ב": {"gender_votes": {"m": 1, "f": 0}, "born": None, "died": None, "categories": [], "surname": ""},
                 "ג": {"gender_votes": {"m": 1, "f": 0}, "born": None, "died": None, "categories": [], "surname": ""}}
        mk = lambda p, r, rel, src, conf=0.8: {"person": p, "relative": r, "relation": rel, "source_page": src, "evidence": "e",
                                              "refs": [], "method": "pattern", "pattern": "x", "confidence": conf,
                                              "person_gender": None, "relative_gender": None, "side": None,
                                              "person_has_article": True, "relative_has_article": True, "person_name": p, "relative_name": r, "order": 0}
        graph = build_graph(pages, [mk("ב", "א", "parent", "ב"), mk("ב", "א", "parent", "א"), mk("ב", "ג", "sibling", "ב")], CFG)
        edges = {(e["a"], e["b"], e["relation"]): e for e in graph["edges"]}
        self.assertIn("corroborated", edges[("ב", "א", "parent")]["flags"])
        self.assertGreater(edges[("ב", "א", "parent")]["confidence"], 0.9)
        self.assertTrue(edges[("ג", "א", "parent")]["inferred"])

    def test_impossible_parent_penalised(self):
        pages = {"ילד": {"gender_votes": {}, "born": 'ה\'תק"ה', "died": None, "categories": [], "surname": ""},
                 "הורה": {"gender_votes": {}, "born": 'ה\'תר"ם', "died": None, "categories": [], "surname": ""}}
        rel = {"person": "ילד", "relative": "הורה", "relation": "parent", "source_page": "ילד", "evidence": "", "refs": [], "method": "pattern",
               "pattern": "x", "confidence": 0.9, "person_gender": None, "relative_gender": None, "side": None,
               "person_has_article": True, "relative_has_article": True, "person_name": "ילד", "relative_name": "הורה", "order": 0}
        graph = build_graph(pages, [rel], CFG)
        self.assertLessEqual(graph["edges"][0]["confidence"], 0.3)


class TestLayout(unittest.TestCase):
    def grid(self, chart):
        from chabadtrees.render import row_tiles
        out = []
        for r in range(chart.height):
            row = []
            for t in row_tiles(chart, r):
                if t is None:
                    row.append("·")
                elif t == "occupied" or isinstance(t, tuple):
                    row.append("B")
                else:
                    row.append(symbol_for(t.dirs, CFG["chart"], t.kind))
            out.append("".join(row))
        return out

    def test_single_marriage_odd_children(self):
        leaf = lambda n: TreeNode(n, [], 1)
        root = TreeNode("P", [Marriage("S", [leaf("a"), leaf("b"), leaf("c")])], 0)
        g = self.grid(layout_forest([root], {}))
        # תיבה = 3 מרצפות; הצומת ד מעל הילד האמצעי; הילד הראשון מקבל . והאחרון ,
        self.assertEqual(g[0], "··BBBדBBB··")
        self.assertEqual(g[1], "·.---+---,·")
        self.assertEqual(g[2], "BBB·BBB·BBB")

    def test_even_children_use_t_up(self):
        leaf = lambda n: TreeNode(n, [], 1)
        root = TreeNode("P", [Marriage("S", [leaf("a"), leaf("b")])], 0)
        g = self.grid(layout_forest([root], {}))
        self.assertEqual(g[0], "BBBדBBB")
        self.assertEqual(g[1], "·.-^-,·")
        self.assertEqual(g[2], "BBB·BBB")

    def test_two_marriages(self):
        leaf = lambda n: TreeNode(n, [], 1)
        root = TreeNode("P", [Marriage("S1", [leaf("a1"), leaf("a2")]), Marriage("S2", [leaf("b1")])], 0)
        g = self.grid(layout_forest([root], {}))
        self.assertEqual(g[0], "BBBדBBBדBBB")
        self.assertEqual(g[-1].count("B"), 9)
        self.assertTrue(set("".join(g)) <= set("B·!-~+.,ד ז^()'`"), g)

    def test_wikitext_tiles(self):
        leaf = lambda n: TreeNode(n, [], 1)
        root = TreeNode("P", [Marriage("S", [leaf("a"), leaf("b"), leaf("c")])], 0)
        persons = {k: {"name": k, "title": None} for k in "PSabc"}
        text = chart_wikitext(layout_forest([root], {}), {"persons": persons, "edges": []}, CFG)
        lines = text.split("\n")
        self.assertEqual(lines[0], "{{עץ משפחה/התחלה}}")
        import re as _re
        self.assertRegex(lines[1], r"^\{\{עץ משפחה\| \| \|B\d+\|ד\|B\d+\|")      # 2 ריקים, תיבה (3 מרצפות), ד, תיבה
        self.assertEqual(lines[2], "{{עץ משפחה| |.|-|-|-|+|-|-|-|,}}")
        self.assertRegex(lines[3], r"^\{\{עץ משפחה\|B\d+\| \|B\d+\| \|B\d+\|")
        self.assertNotIn("|y|", text)


class MockServerMixin:
    @classmethod
    def start_mock(cls, fail_every=0, fail_first=0):
        from tests import mock_api
        mock_api.State.fail_every = fail_every
        mock_api.State.fail_first = fail_first
        mock_api.State.requests = 0
        cls.srv = mock_api.serve(0)
        cls.api = f"http://127.0.0.1:{cls.srv.server_port}/api.php"

    @classmethod
    def stop_mock(cls):
        cls.srv.shutdown()
        cls.srv.server_close()


class TestPipelineWithMock(MockServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_mock()
        cls.tmp = tempfile.mkdtemp()
        cls.store = Store(os.path.join(cls.tmp, "data"))
        cls.client = MediaWikiClient(cls.api, "test", rate_limit_seconds=0)
        cls.summary = fetch_pages(cls.client, CFG, cls.store, retry_sleep=0)
        cls.extracted = extract_all(CFG, cls.store)
        cls.resolution = resolve_links(cls.client, CFG, cls.store)
        cls.graph = graph_step(CFG, cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.stop_mock()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fetch_all_pages(self):
        self.assertEqual(self.summary["failed"], 0)
        self.assertEqual(self.summary["missing"], 0)
        self.assertGreaterEqual(self.summary["fetched"], 30)

    def test_graph_is_clean(self):
        P, E = self.graph["persons"], self.graph["edges"]
        rebbe = 'רבי מנחם מענדל שניאורסון (אדמו"ר שליט"א)'
        parents = sorted(e["b"] for e in E if e["relation"] == "parent" and e["a"] == rebbe and e["confidence"] >= 0.5)
        self.assertEqual(parents, ["הרבנית חנה שניאורסון", "רבי לוי יצחק שניאורסון"])
        spouses = [e for e in E if e["relation"] == "spouse" and rebbe in (e["a"], e["b"])]
        self.assertEqual(len(spouses), 1)
        # אף אחד לא מסומן עם יותר מאב אחד
        self.assertFalse([p for p in P.values() if "יותר מאב אחד" in p["flags"]], [p["name"] for p in P.values() if p["flags"]])

    def test_trees_and_links(self):
        trees = build_trees(self.graph, CFG)
        self.assertEqual(len(trees), 1)
        t = trees[0]
        self.assertGreaterEqual(len(t["members_shown"]), 28)
        self.assertIn("[[רבי שניאור זלמן מלאדי (אדמו\"ר הזקן)|רבי שניאור זלמן מלאדי]]", t["wikitext"])
        self.assertIn("<ref>", t["wikitext"])
        self.assertIn("אשת [[רבי שמריהו גורארי']]", t["wikitext"])     # בת: בעלה בתוך הקופסה
        self.assertNotIn("|y|", t["wikitext"])
        for line in t["wikitext"].split("\n"):
            if line.startswith("{{עץ משפחה|"):
                cells = [c.strip() for c in wt.split_top(line[len("{{עץ משפחה|"):].rstrip("}")) if "=" not in c]
                bad = [c for c in cells if c and not c.startswith("B") and c not in ("!", "-", "~", "+", ".", ",", "ד", "ז", "^", "(", ")", "'", "`")]
                self.assertEqual(bad, [], line[:80])
        self.assertNotIn("|B", t["wikitext"].split("{{עץ משפחה/התחלה}}")[0])
        anc = build_ancestor_trees(self.graph, CFG, for_person='רבי מנחם מענדל שניאורסון (אדמו"ר שליט"א)')
        self.assertEqual(anc[0]["slots"]["f"], "רבי לוי יצחק שניאורסון")
        self.assertEqual(anc[0]["slots"]["fff"], "רבי לוי יצחק שניאורסון (בן רבי ברוך שלום)")
        self.assertIn("| אבא = [[רבי לוי יצחק שניאורסון]]", anc[0]["wikitext"])

    def test_redirect_links_resolve_to_canonical_title(self):
        # ערך שמקשר ל"(אב הרבי)" (הפניה) מתמזג עם הערך האמיתי ולא יוצר אדם כפול
        self.assertIn("רבי לוי יצחק שניאורסון (אב הרבי)", self.resolution)
        self.assertEqual(self.resolution["רבי לוי יצחק שניאורסון (אב הרבי)"]["to"], "רבי לוי יצחק שניאורסון")
        self.assertNotIn("רבי לוי יצחק שניאורסון (אב הרבי)", self.graph["persons"])
        dovber = 'רבי דובער שניאורסון (אחי הרבי)'
        fathers = [e["b"] for e in self.graph["edges"] if e["relation"] == "parent" and e["a"] == dovber and self.graph["persons"][e["b"]]["gender"] == "m"]
        self.assertEqual(fathers, ["רבי לוי יצחק שניאורסון"])
        trees = build_trees(self.graph, CFG)
        self.assertNotIn("(אב הרבי)", trees[0]["wikitext"])

    def test_existing_trees_detected(self):
        existing = find_existing_trees(self.client, CFG)
        titles = [t["title"] for t in existing["trees"]]
        self.assertIn("תבנית:עץ משפחת שניאורסון", titles)
        cov = coverage(self.graph, existing, CFG)
        self.assertEqual(cov[0]["status"], "exists")
        tree = next(t for t in existing["trees"] if t["title"] == "תבנית:עץ משפחת שניאורסון")
        self.assertIn("ליובאוויטש", tree["embedded_in"])


class TestRateLimitResilience(MockServerMixin, unittest.TestCase):
    """429 באמצע הריצה: אף דף לא נעלם, והלקוח מאט את הקצב."""

    @classmethod
    def setUpClass(cls):
        cls.start_mock(fail_every=3)
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.stop_mock()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_pages_fetched_despite_429(self):
        store = Store(os.path.join(self.tmp, "data"))
        client = MediaWikiClient(self.api, "test", rate_limit_seconds=0)
        summary = fetch_pages(client, CFG, store, retry_sleep=0)
        self.assertGreater(client.rate_limited, 0)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["missing"], 0)
        self.assertGreaterEqual(summary["fetched"], 30)
        self.assertGreater(client.rate_limit, 0)


class TestResumeAfterHardFailure(unittest.TestCase):
    """כשל קשיח בקבוצה אחת: נרשם, לא נעלם, ומצליח בריצה הבאה."""

    class FlakyClient:
        def __init__(self, fail_batches: int):
            self.calls = 0
            self.fail_batches = fail_batches
            self.requests_made = 0
            self.rate_limited = 0

        def fetch_batch(self, titles):
            self.calls += 1
            self.requests_made += 1
            if self.calls <= self.fail_batches:
                raise ApiError("HTTP 429")
            return [{"title": t, "pageid": i, "wikitext": "טקסט", "categories": [], "revid": 1} for i, t in enumerate(titles)], {t: t for t in titles}

    def test_state_records_and_recovers(self):
        tmp = tempfile.mkdtemp()
        try:
            store = Store(os.path.join(tmp, "data"))
            titles = [f"דף {i}" for i in range(120)]     # 3 קבוצות
            client = self.FlakyClient(fail_batches=5)    # נכשל בכל 3 הקבוצות במעבר 1 ובשתיים במעבר 2
            summary = fetch_pages(client, CFG, store, titles=titles, retry_sleep=0)
            state = store.load("fetch_state.json", {})
            self.assertEqual(summary["fetched"] + summary["failed"], 120)
            self.assertGreater(summary["failed"], 0)
            self.assertTrue(all("HTTP 429" in v["error"] for v in state["failed"].values()))
            # ריצה שנייה: הכשלים מנוסים ראשונים ומצליחים
            summary2 = fetch_pages(client, CFG, store, titles=titles, retry_sleep=0)
            self.assertEqual(summary2["failed"], 0)
            self.assertEqual(summary2["fetched"], 120)
            self.assertEqual(store.load("fetch_state.json", {})["failed"], {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRealTextRules(unittest.TestCase):
    """כללים שנוספו אחרי הריצות החיות: לקסיקון שמות, תאריכים, נושא-בת, סיווג דפי אישים, מיזוג לא-מקושרים."""

    def _strict(self):
        known = {f"פלוני {i}" for i in range(1000)} | {"אשר וילהלם", "חנה וילהלם", "שאול וילהלם", "שלמה זלמן לנדא", "שמואל גליצנשטיין"}
        return Extractor(CFG, known_persons=known, common_words={"מפעילה", "פעילות", "רחבה", "שימשה", "כמחנכת", "ביתו"})

    def test_dates_are_not_people(self):
        from chabadtrees.extract import _looks_like_year
        for s in ("כ' במנחם אב", "ל' בסיוון", 'י"א ניסן', 'תרפ"ט', 'ה\'תשמ"ב', "ג' תמוז"):
            self.assertTrue(_looks_like_year(s), s)
        for s in ("חנה", "מנחם מענדל", "אב הרבי"):
            self.assertFalse(_looks_like_year(s), s)

    def test_name_lexicon(self):
        ex = self._strict()
        self.assertTrue(ex.strict_persons)
        self.assertEqual(ex.unlinked_name("שימשה כמחנכת"), "")
        self.assertEqual(ex.unlinked_name("חנה מפעילה פעילות רחבה"), "חנה")
        self.assertEqual(ex.unlinked_name("חנה למשפחת וולף"), "חנה לבית וולף")
        self.assertEqual(ex.unlinked_name("חנה בלה ביתו"), "חנה בלה")       # "ביתו" – שגיאת כתיב של "בתו", לא שם; "בלה" שם נדיר
        self.assertEqual(ex.unlinked_name("שאול אמסעל"), "שאול אמסעל")     # שם משפחה נדיר בלי ערך נשמר
        self.assertEqual(ex.unlinked_name("מנדבורנא", "m"), "")             # מקום אחרי תואר – לא שם
        self.assertTrue(ex.plain_name_ok("חנה"))
        self.assertTrue(ex.plain_name_ok("אשר"))        # שם פרטי, גם אם הוא מילת קישור
        self.assertFalse(ex.plain_name_ok("וילהלם"))    # שם משפחה בלבד

    def test_daughter_is_subject_of_marriage(self):
        ex = self._strict()
        out, _info = ex.extract("אשר וילהלם", "בתו חנה נישאה ל[[שלמה זלמן לנדא]]. בנו, ר' שאול אמסעל - שליח.")
        triples = {(r.relation, r.person, r.relative) for r in out}
        self.assertIn(("spouse", "~חנה", "שלמה זלמן לנדא"), triples)
        self.assertIn(("parent", "~חנה", "אשר וילהלם"), triples)
        self.assertIn(("parent", "~שאול אמסעל", "אשר וילהלם"), triples)
        self.assertNotIn(("spouse", "אשר וילהלם", "שלמה זלמן לנדא"), triples)
        for r in out:
            if (r.relation, r.person) == ("parent", "~חנה"):
                self.assertGreaterEqual(r.confidence, 0.5)

    def test_wife_of_link_in_list(self):
        ex = self._strict()
        out, _ = ex.extract("אשר וילהלם", "==משפחתו==\n* חנה ליבא, אשת [[שמואל גליצנשטיין]]\n")
        triples = {(r.relation, r.person, r.relative) for r in out}
        self.assertIn(("spouse", "~חנה ליבא", "שמואל גליצנשטיין"), triples)
        self.assertNotIn(("parent", "שמואל גליצנשטיין", "אשר וילהלם"), triples)

    def test_honorific_must_touch_the_link(self):
        from chabadtrees.extract import honorific_before
        self.assertIsNone(honorific_before("* הרב שלום הלפרין, רב ב"))
        self.assertEqual(honorific_before("אחיו הרב "), "m")
        self.assertEqual(honorific_before("בתו מרת "), "f")

    def test_person_page_classifier(self):
        from chabadtrees.persons import is_person_page
        self.assertTrue(is_person_page({"title": "פלוני", "categories": ['חסידים בתקופת אדמו"ר שליט"א']}))
        self.assertTrue(is_person_page({"title": "יום טוב עהרליך", "categories": ["זמרים"]}))
        self.assertFalse(is_person_page({"title": 'ניגון דבקות (אדמו"ר הזקן)', "categories": ['ניגוני אדמו"ר הזקן', "ראש השנה"]}))
        self.assertFalse(is_person_page({"title": "השלוחים לארץ הקודש", "categories": ["שלוחים בישראל"]}))
        self.assertFalse(is_person_page({"title": "חסידות ברסלב", "categories": ["חסידויות ושושלות"]}))
        self.assertTrue(is_person_page({"title": "אלמוני", "categories": [], "wikitext": B3 + "אלמוני" + B3 + ' נולד בשנת תש"ך'}))

    def test_unlinked_merge_by_family_slot(self):
        from chabadtrees.graph import _names_match
        self.assertTrue(_names_match("חנה", "חנה ליבא"))
        self.assertTrue(_names_match("חי שרה", "חיה שרה"))
        self.assertTrue(_names_match("נטע שלמה", "נטע שלמה וילהלם"))
        self.assertFalse(_names_match("חיה מושקא", "חיה שרה"))
        pages = {"אשר וילהלם": {"gender_votes": {"m": 3}}, "משה וילהלם": {"gender_votes": {"m": 3}}}

        def rel(person, relative, relation, page, **kw):
            base = dict(person=person, relative=relative, relation=relation, source_page=page, evidence="", refs=[], method="pattern",
                        pattern="t", confidence=0.8, person_gender=None, relative_gender=None, side=None, person_has_article=True,
                        relative_has_article=True, person_name=person.lstrip("~"), relative_name=relative.lstrip("~"), order=0)
            base.update(kw)
            return base
        relations = [
            rel("אשר וילהלם", "~חיה שרה", "spouse", "אשר וילהלם", relative_has_article=False),          # אשתו חיה שרה (בדף הבעל)
            rel("משה וילהלם", "אשר וילהלם", "parent", "משה וילהלם"),                                     # בנו של אשר
            rel("משה וילהלם", "~חי שרה", "parent", "משה וילהלם", relative_has_article=False),            # אמו "חי שרה" (שגיאת כתיב, בדף הבן)
            rel("~חיה שרה", "אשר וילהלם", "parent", "אשר וילהלם", person_has_article=False, person_gender="f"),  # בתו חיה שרה – נכדה בשם הסבתא
        ]
        g = build_graph(pages, relations, CFG)
        unl = [p for p in g["persons"].values() if p["id"].startswith("~")]
        names = sorted(p["name"] for p in unl)
        self.assertEqual(names, ["חיה שרה", "חיה שרה"])     # אישה ואם מוזגו לאחת; הבת נשארה נפרדת
        wife = [p for p in unl if any(e["relation"] == "spouse" and p["id"] in (e["a"], e["b"]) for e in g["edges"])]
        self.assertEqual(len(wife), 1)
        self.assertTrue(any(e["relation"] == "parent" and e["a"] == "משה וילהלם" and e["b"] == wife[0]["id"] for e in g["edges"]))

if __name__ == "__main__":
    unittest.main()
