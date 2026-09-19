"""שורת הפקודה: python -m chabadtrees <פקודה> [אפשרויות]."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import __version__
from .api import MediaWikiClient
from .config import load_config
from .pipeline import (Store, collect_person_titles, fetch_pages, extract_all, graph_step, build_trees,
                       build_ancestor_trees)
from .existing import find_existing_trees, coverage
from .preview import render_html, screenshot
from .review import write_review

log = logging.getLogger("chabadtrees")


def make_client(cfg: dict, args) -> MediaWikiClient:
    return MediaWikiClient(args.api or cfg["api_url"], args.user_agent or cfg["user_agent"],
                           rate_limit_seconds=args.rate if args.rate is not None else cfg["rate_limit_seconds"],
                           maxlag=cfg.get("maxlag", 5), timeout=cfg.get("http_timeout", 60))


def write_outputs(trees: list[dict], graph: dict, cfg: dict, out_dir: str, png: bool) -> dict[str, str]:
    previews: dict[str, str] = {}
    os.makedirs(os.path.join(out_dir, "trees"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "preview"), exist_ok=True)
    index = []
    for t in trees:
        sub = "ancestors" if t["kind"] == "ancestors" else "trees"
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
        wiki_path = os.path.join(out_dir, sub, t["slug"] + ".wiki")
        with open(wiki_path, "w", encoding="utf-8") as fh:
            fh.write(t["wikitext"])
        html_path = os.path.join(out_dir, "preview", t["slug"] + ".html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(render_html(t["chart"], graph, cfg, t["title"], " · ".join(t.get("notes", []))))
        previews[t["slug"]] = os.path.relpath(html_path, out_dir)
        rec = {k: v for k, v in t.items() if k not in ("chart", "wikitext", "slots")}
        rec["wiki_file"] = os.path.relpath(wiki_path, out_dir)
        rec["preview_file"] = previews[t["slug"]]
        if png:
            png_path = os.path.join(out_dir, "preview", t["slug"] + ".png")
            width, height = estimate_png_size(t["chart"], graph)
            if screenshot(html_path, png_path, width=width, height=height):
                rec["png_file"] = os.path.relpath(png_path, out_dir)
        index.append(rec)
    with open(os.path.join(out_dir, "trees_index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    return previews


def estimate_png_size(chart, graph) -> tuple[int, int]:
    """גודל חלון לצילום: עמודת קופסה ≈ 215px, עמודת קו ≈ 18px; שורה ≈ 64px + כותרת והערות שוליים."""
    col_w = [18] * chart.width
    refs = set()
    for (r, c), cell in chart.cells.items():
        if cell.kind == "box":
            col_w[c] = 215
            pid = chart.boxes[cell.ref]["person"]
            for e in graph["edges"]:
                if pid in (e["a"], e["b"]):
                    for ev in e["evidence"]:
                        refs.update(ev.get("refs", []))
    width = min(max(700, sum(col_w) + 120), 14000)
    height = min(max(500, chart.height * 64 + 180 + 22 * len(refs)), 14000)
    return width, height


def cmd_fetch(args, cfg, store):
    client = make_client(cfg, args)
    if args.recollect:
        state = store.load("fetch_state.json", {})
        state.pop("titles", None)
        state.pop("categories_done", None)
        store.save("fetch_state.json", state)
    titles = None
    if args.titles:
        titles = [t.strip() for t in open(args.titles, encoding="utf-8") if t.strip()]
    summary = fetch_pages(client, cfg, store, refresh=args.refresh, limit=args.limit, titles=titles, retry_sleep=args.retry_sleep)
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if summary["failed"] else 0


def cmd_extract(args, cfg, store):
    extract_all(cfg, store, use_llm=getattr(args, "llm", False))
    return 0


def cmd_graph(args, cfg, store):
    graph_step(cfg, store)
    return 0


def cmd_existing(args, cfg, store):
    client = make_client(cfg, args)
    existing = find_existing_trees(client, cfg)
    store.save("existing_trees.json", existing)
    print(f"נמצאו {len(existing['trees'])} דפי עץ משפחה; {len(existing['pages_with_ahnentafel'])} דפי אישים עם עץ לאדם אחד")
    graph = store.load("graph.json", None)
    if graph:
        cov = coverage(graph, existing, cfg)
        store.save("coverage.json", cov)
        for c in cov:
            if c["status"] != "none":
                print(f"  {c['label']}: {c['status']} – {c['best']['tree']} ({c['best']['score']})")
    return 0


def cmd_render(args, cfg, store):
    graph = store.load("graph.json", None)
    if not graph:
        raise SystemExit("אין data/graph.json – הריצו קודם fetch, extract, graph")
    trees = []
    if args.ancestors_for or args.ancestors:
        trees += build_ancestor_trees(graph, cfg, for_person=args.ancestors_for, generations=args.generations)
    if not args.ancestors and not args.ancestors_for:
        trees += build_trees(graph, cfg, only_label=args.family, root=args.root, max_depth=args.max_depth, max_nodes=args.max_nodes)
    previews = write_outputs(trees, graph, cfg, args.out, args.png)
    cov = store.load("coverage.json", None)
    review = write_review(graph, trees, cov, cfg, args.out, previews)
    for t in trees:
        print(f"{t['title']}: {len(t['members_shown'])} אנשים, גריד {t['width']}×{t['height']} → {args.out}/{'ancestors' if t['kind']=='ancestors' else 'trees'}/{t['slug']}.wiki")
    print(f"דף בדיקה: {review}")
    return 0


def cmd_all(args, cfg, store):
    rc = cmd_fetch(args, cfg, store)
    cmd_extract(args, cfg, store)
    cmd_graph(args, cfg, store)
    try:
        cmd_existing(args, cfg, store)
    except Exception as exc:  # בדיקת עצים קיימים לא מפילה את הריצה
        log.error("בדיקת עצים קיימים נכשלה: %s", exc)
    args.ancestors_for, args.ancestors, args.family, args.root, args.max_depth, args.max_nodes = None, False, None, None, None, None
    cmd_render(args, cfg, store)
    return rc


def cmd_calibrate(args, cfg, store):
    """מדפיס את מה שצריך כדי לכייל את ההגדרות מול האתר החי."""
    client = make_client(cfg, args)
    info = client.site_info()
    gen, stats = info.get("general", {}), info.get("statistics", {})
    print(f"אתר: {gen.get('sitename')} · מדיה-ויקי {gen.get('generator')} · ערכים: {stats.get('articles')} · דפים: {stats.get('pages')}")
    docs = [f"תבנית:{cfg['chart']['row']}/תיעוד", f"תבנית:{cfg['chart']['row']}", f"תבנית:{cfg['ahnentafel']['template']}/תיעוד",
            f"תבנית:{cfg['ahnentafel']['template']}"]
    for page in client.get_pages(titles=docs):
        print(f"\n===== {page['title']} =====\n{(page.get('wikitext') or '')[:6000]}")
    sample = store.load("pages.json", {})
    if sample:
        from .extract import Extractor
        ex = Extractor(cfg, known_persons=set(sample))
        for title, page in list(sample.items())[:400]:
            ex.extract(page.get("title", title), page.get("wikitext") or "")
        print("\nתבניות נפוצות בדפי אישים:", json.dumps(dict(sorted(ex.template_names.items(), key=lambda kv: -kv[1])[:15]), ensure_ascii=False))
        print("שדות משפחה שלא זוהו בהגדרות:", json.dumps(ex.unknown_fields, ensure_ascii=False))
    return 0


def cmd_upload(args, cfg, store):
    from .upload import upload_tree
    return upload_tree(make_client(cfg, args), cfg, args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chabadtrees", description="בניית עצי משפחה לחב\"דפדיה מתוך ערכי האישים")
    p.add_argument("--api", help="כתובת api.php (ברירת מחדל מההגדרות)")
    p.add_argument("--config", help="קובץ config.json")
    p.add_argument("--data", default="data", help="תיקיית נתוני ביניים")
    p.add_argument("--out", default="output", help="תיקיית פלט")
    p.add_argument("--user-agent")
    p.add_argument("--rate", type=float, help="שניות בין בקשות")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="איסוף דפי האישים (ניתן להמשך; כשלים מנוסים שוב)")
    f.add_argument("--refresh", action="store_true", help="למשוך מחדש גם דפים שבמטמון")
    f.add_argument("--recollect", action="store_true", help="לאסוף מחדש את רשימת הכותרות מהקטגוריות")
    f.add_argument("--limit", type=int)
    f.add_argument("--titles", help="קובץ כותרות (שורה לכל דף) במקום מעבר על הקטגוריות")
    f.add_argument("--retry-sleep", type=float, default=30.0, help="המתנה לפני המעבר השני על כשלים")
    f.set_defaults(func=cmd_fetch)

    e = sub.add_parser("extract", help="חילוץ קשרים מהדפים שבמטמון")
    e.add_argument("--llm", action="store_true", help="גם חילוץ בעזרת Claude (דורש anthropic SDK ומפתח)")
    e.set_defaults(func=cmd_extract)
    sub.add_parser("graph", help="בניית הגרף והמשפחות").set_defaults(func=cmd_graph)
    sub.add_parser("existing", help="איתור עצים קיימים באתר ובדיקת כיסוי").set_defaults(func=cmd_existing)

    r = sub.add_parser("render", help="בניית העצים וקוד הוויקי")
    r.add_argument("--family", help="רק משפחות שהתווית שלהן מכילה טקסט זה")
    r.add_argument("--root", help="עץ צאצאים מאדם מסוים (כותרת הערך)")
    r.add_argument("--max-depth", type=int)
    r.add_argument("--max-nodes", type=int)
    r.add_argument("--ancestors-for", help="עץ אבות לאדם מסוים")
    r.add_argument("--ancestors", action="store_true", help="עצי אבות לכל מי שיש לו מספיק אבות ידועים")
    r.add_argument("--generations", type=int, default=3)
    r.add_argument("--png", action="store_true", help="צילום מסך של כל עץ (דורש Chromium)")
    r.set_defaults(func=cmd_render)

    a = sub.add_parser("all", help="הכול ברצף: fetch, extract, graph, existing, render")
    for opt, kw in (("--refresh", {"action": "store_true"}), ("--recollect", {"action": "store_true"}), ("--limit", {"type": int}),
                    ("--titles", {}), ("--retry-sleep", {"type": float, "default": 30.0}), ("--png", {"action": "store_true"}),
                    ("--generations", {"type": int, "default": 3})):
        a.add_argument(opt, **kw)
    a.set_defaults(func=cmd_all)

    sub.add_parser("calibrate", help="הדפסת תיעוד התבניות ושדות תבניות האישים – לכיול ההגדרות").set_defaults(func=cmd_calibrate)

    u = sub.add_parser("upload", help="העלאת עץ אחד לאתר (ברירת מחדל: הרצה יבשה)")
    u.add_argument("--file", required=True, help="קובץ .wiki מתוך output/")
    u.add_argument("--title", required=True, help="שם הדף ביעד, למשל 'תבנית:עץ משפחת פלוני'")
    u.add_argument("--summary", default="עץ משפחה שנבנה מתוך ערכי חב\"דפדיה (chabadpedia-family-trees)")
    u.add_argument("--user", help="שם משתמש (או משתנה סביבה CHABADPEDIA_USER)")
    u.add_argument("--password", help="סיסמת בוט (או CHABADPEDIA_PASSWORD)")
    u.add_argument("--yes", action="store_true", help="לבצע באמת. בלי הדגל רק מדפיס מה היה נשלח")
    u.add_argument("--i-have-community-approval", action="store_true", help="אישור שהועלה לדיון בקהילה")
    u.set_defaults(func=cmd_upload)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(args.config)
    store = Store(args.data)
    os.makedirs(args.out, exist_ok=True)
    return args.func(args, cfg, store) or 0


if __name__ == "__main__":
    sys.exit(main())
