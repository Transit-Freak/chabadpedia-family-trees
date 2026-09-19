"""דף בדיקה (HTML): סיכום העצים מול עצים קיימים, וכל קשר עם המשפט שתומך בו, המקור והדגלים."""
from __future__ import annotations

import html
import os

from . import wikitext as wt

CSS = """
body{font-family:'Noto Sans Hebrew','DejaVu Sans',Arial,sans-serif;margin:20px;color:#111;background:#fff}
h1{font-size:22px}h2{font-size:18px;margin-top:28px;border-bottom:1px solid #ccc}
table{border-collapse:collapse;font-size:13px;width:100%}th,td{border:1px solid #ddd;padding:4px 6px;vertical-align:top;text-align:right}
th{background:#f0f0f0}tr.noref td{background:#fff8e6}tr.inferred td{background:#f3f3f3;color:#555}tr.conflict td{background:#ffe8e8}
.tag{display:inline-block;padding:0 6px;border-radius:8px;font-size:11px;background:#eee;margin-left:3px}
.exists{background:#d8f0d8}.partial{background:#fff0c0}.none{background:#eee}
a{color:#06c;text-decoration:none}small{color:#666}
"""

REL_HE = {"parent": "הורה של", "spouse": "בן/בת זוג של", "sibling": "אח/ות של", "grandparent": "סב/סבתא של",
          "great_grandparent": "אב-סב של", "parent_in_law": "חם/חמות של", "sibling_in_law": "גיס/ה של", "uncle": "דוד/ה של"}


def _name(graph: dict, pid: str, site: str) -> str:
    p = graph["persons"].get(pid, {"name": pid, "title": None})
    if p.get("title"):
        return f'<a href="{html.escape(site + p["title"].replace(" ", "_"))}">{html.escape(p["name"])}</a>'
    return html.escape(p["name"]) + ' <span class="tag">ללא ערך</span>'


def write_review(graph: dict, trees: list[dict], coverage: list[dict] | None, cfg: dict, out_dir: str, previews: dict[str, str]) -> str:
    site = cfg.get("site_url", "")
    parts = [f"<!doctype html><html lang='he' dir='rtl'><head><meta charset='utf-8'><title>בדיקת עצי משפחה</title><style>{CSS}</style></head><body>"]
    parts.append("<h1>דף בדיקה – עצי משפחה שנבנו</h1>")
    parts.append(f"<p>אנשים בגרף: {len(graph['persons'])} · קשרים: {len(graph['edges'])} · עצים: {len(trees)}</p>")
    # --- סיכום ---
    parts.append("<h2>סיכום</h2><table><tr><th>עץ</th><th>סוג</th><th>אנשים</th><th>בני זוג</th><th>מול עצים קיימים</th><th>תצוגה</th></tr>")
    cov_by_label = {c["label"]: c for c in (coverage or [])}
    for t in trees:
        cov = cov_by_label.get(t["label"]) or cov_by_label.get(t["label"].replace("עץ ", ""))
        if cov:
            best = cov.get("best") or {}
            cov_html = f'<span class="tag {cov["status"]}">{ {"exists": "קיים כבר", "partial": "חלקי", "none": "אין"}[cov["status"]] }</span>'
            if best.get("tree"):
                cov_html += f' {html.escape(best["tree"])} ({best.get("score")})'
        else:
            cov_html = '<span class="tag">לא נבדק</span>'
        prev = previews.get(t["slug"])
        prev_html = f'<a href="{html.escape(prev)}">HTML</a>' if prev else ""
        parts.append(f"<tr><td>{html.escape(t['title'])}</td><td>{t['kind']}</td><td>{len(t['members_shown'])}</td>"
                     f"<td>{len(t.get('spouses_shown', []))}</td><td>{cov_html}</td><td>{prev_html}</td></tr>")
    parts.append("</table>")
    # --- קשרים לכל עץ ---
    for t in trees:
        shown = set(t["members_shown"]) | set(t.get("spouses_shown", []))
        parts.append(f"<h2>{html.escape(t['title'])}</h2>")
        for n in t.get("notes", []):
            parts.append(f"<p><small>{html.escape(n)}</small></p>")
        parts.append("<table><tr><th>#</th><th>אדם</th><th>קשר</th><th>קרוב</th><th>ביטחון</th><th>ראיות (המשפט בערך)</th><th>מקורות</th><th>דגלים</th></tr>")
        i = 0
        for e in graph["edges"]:
            if e["a"] not in shown and e["b"] not in shown:
                continue
            if e["relation"] not in ("parent", "spouse", "sibling"):
                continue
            i += 1
            cls = []
            if "no_ref" in e["flags"]:
                cls.append("noref")
            if e.get("inferred"):
                cls.append("inferred")
            if any(f not in ("no_ref", "inferred", "corroborated") for f in e["flags"]):
                cls.append("conflict")
            ev_html = "<br>".join(
                f"<b>{html.escape(ev['source_page'])}</b>: {html.escape((ev['text'] or '')[:220])} <small>({ev['method']} {ev.get('pattern') or ''}, {ev['confidence']})</small>"
                for ev in e["evidence"])
            refs = [wt.plain(r.replace("<ref>", "").replace("</ref>", "")) for ev in e["evidence"] for r in ev.get("refs", [])]
            refs_html = "<br>".join(html.escape(r) for r in dict.fromkeys(refs)) or '<span class="tag">ללא מקור</span>'
            flags = " ".join(f'<span class="tag">{html.escape(f)}</span>' for f in e["flags"])
            parts.append(f"<tr class='{' '.join(cls)}'><td>{i}</td><td>{_name(graph, e['b'] if e['relation']=='parent' else e['a'], site)}</td>"
                         f"<td>{REL_HE.get(e['relation'], e['relation'])}</td><td>{_name(graph, e['a'] if e['relation']=='parent' else e['b'], site)}</td>"
                         f"<td>{e['confidence']}</td><td>{ev_html}</td><td>{refs_html}</td><td>{flags}</td></tr>")
        parts.append("</table>")
        flagged = [(pid, graph["persons"][pid]["flags"]) for pid in shown if graph["persons"].get(pid, {}).get("flags")]
        if flagged:
            parts.append("<p><b>אנשים עם סימני שאלה:</b> " + "; ".join(f"{_name(graph, p, site)}: {html.escape(', '.join(f))}" for p, f in flagged) + "</p>")
    parts.append("</body></html>")
    path = os.path.join(out_dir, "review.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return path
