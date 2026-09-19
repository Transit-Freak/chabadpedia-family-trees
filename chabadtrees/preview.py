"""תצוגה מקדימה של גריד כ-HTML (מדמה את תבנית העץ), וצילום מסך דרך Chromium אם קיים."""
from __future__ import annotations

import glob
import html
import os
import re
import shutil
import subprocess

from . import wikitext as wt
from .layout import Chart
from .render import person_label, refs_for, box_content

CSS = """
body{font-family:'Noto Sans Hebrew','DejaVu Sans',Arial,sans-serif;background:#fff;color:#111;margin:16px}
h1{font-size:20px;margin:0 0 8px}
p.note{color:#555;font-size:13px;margin:0 0 12px}
table.tree{border-collapse:collapse;direction:ltr;margin:8px auto}
td.box>div{direction:rtl;unicode-bidi:embed}
table.tree td{padding:0;height:34px;vertical-align:middle;text-align:center}
td.box>div{display:inline-block;border:1px solid #777;border-radius:3px;background:#f7f7f2;padding:4px 8px;font-size:13px;line-height:1.25;white-space:nowrap;min-width:40px}
td.box.member>div{background:#e8f0e8;border-color:#5a7a5a}
td.box.spouse>div{background:#f4f4f4;border-color:#999;border-style:dashed}
td.box.ancestor>div{background:#eef2f8;border-color:#5a6a8a}
td.box.self>div{background:#e8f0e8;border-color:#5a7a5a;font-weight:bold}
td.line{width:18px;min-width:18px;position:relative}
td.line>div{position:absolute;background:#333}
td.line>div.u{width:2px;height:50%;top:0;left:calc(50% - 1px)}
td.line>div.d{width:2px;height:50%;bottom:0;left:calc(50% - 1px)}
td.line>div.pl{height:2px;width:50%;left:0;top:calc(50% - 1px)}
td.line>div.pr{height:2px;width:50%;right:0;top:calc(50% - 1px)}
td.line.marriage>div.dot{width:6px;height:6px;border-radius:3px;left:calc(50% - 3px);top:calc(50% - 3px)}
td.line.mline>div.pl,td.line.mline>div.pr,td.line.marriage>div.pl,td.line.marriage>div.pr{height:4px;top:calc(50% - 2px);background:repeating-linear-gradient(180deg,#333 0 1px,#fff 1px 3px,#333 3px 4px)}
small{color:#444;font-weight:normal}
ol.refs{font-size:12px;color:#333}
sup{font-size:9px;color:#06c}
"""


def render_html(chart: Chart, graph: dict, cfg: dict, title: str, subtitle: str = "", rtl: bool = True) -> str:
    refs: list[str] = []

    def ref_index(r: str) -> int:
        if r not in refs:
            refs.append(r)
        return refs.index(r) + 1

    rows = []
    for r in range(chart.height):
        tds = []
        # הטבלה עצמה LTR והעמודות הפוכות: העמודה הלוגית 0 מוצגת מימין, כמו בוויקי RTL
        for c in (range(chart.width - 1, -1, -1) if rtl else range(chart.width)):
            cell = chart.cells.get((r, c))
            if cell is None:
                tds.append("<td></td>")
            elif cell.kind == "box":
                info = chart.boxes[cell.ref]
                content = box_content(chart, cell.ref, graph, cfg)
                tds.append(f'<td class="box {info["role"]}"><div>{_wiki_to_html(content, cfg, ref_index)}</div></td>')
            else:
                dirs = set(cell.dirs)
                parts = []
                if "u" in dirs:
                    parts.append('<div class="u"></div>')
                if "d" in dirs:
                    parts.append('<div class="d"></div>')
                # לוגי → פיזי: ב-RTL התא הקודם ברצף (l) נמצא פיזית מימין
                left_phys, right_phys = ("r", "l") if rtl else ("l", "r")
                if left_phys in dirs:
                    parts.append('<div class="pl"></div>')
                if right_phys in dirs:
                    parts.append('<div class="pr"></div>')
                if cell.kind == "marriage":
                    parts.append('<div class="dot"></div>')
                tds.append(f'<td class="line {cell.kind}">{"".join(parts)}</td>')
        rows.append("<tr>" + "".join(tds) + "</tr>")
    refs_html = ""
    if refs:
        refs_html = "<h3>הערות שוליים</h3><ol class=\"refs\">" + "".join(
            f"<li>{html.escape(_ref_text(r))}</li>" for r in refs) + "</ol>"
    return f"""<!doctype html><html lang="he" dir="ltr"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body><div dir="rtl"><h1>{html.escape(title)}</h1><p class="note">{html.escape(subtitle)}</p></div>
<table class="tree">{''.join(rows)}</table><div dir="rtl">{refs_html}</div></body></html>"""


def _ref_text(ref: str) -> str:
    inner = re.sub(r"</?ref[^>]*>", "", ref)
    return wt.plain(inner)


def _wiki_to_html(content: str, cfg: dict, ref_index) -> str:
    site = cfg.get("site_url", "")

    def ref_repl(m):
        return f"<sup>[{ref_index(m.group(0))}]</sup>"

    content = wt.REF_RE.sub(ref_repl, content)

    def link_repl(m):
        target = m.group(1).strip()
        disp = (m.group(2) or target).strip()
        href = site + target.replace(" ", "_")
        return f'<a href="{html.escape(href)}">{html.escape(disp)}</a>'

    out = []
    last = 0
    for m in wt.LINK_RE.finditer(content):
        out.append(_escape_keep_tags(content[last:m.start()]))
        out.append(link_repl(m))
        last = m.end()
    out.append(_escape_keep_tags(content[last:]))
    return "".join(out)


def _escape_keep_tags(s: str) -> str:
    """משאיר <br />, <small>, <sup> ומקודד את השאר."""
    parts = re.split(r"(<br\s*/?>|</?small>|</?sup>)", s)
    return "".join(p if re.fullmatch(r"<br\s*/?>|</?small>|</?sup>", p) else html.escape(p) for p in parts)


def find_chrome() -> str | None:
    for name in ("CHROME_BIN", "CHROMIUM_BIN"):
        if os.environ.get(name) and os.path.exists(os.environ[name]):
            return os.environ[name]
    for cand in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        path = shutil.which(cand)
        if path:
            return path
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome", os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None


def screenshot(html_path: str, png_path: str, width: int = 1600, height: int = 900) -> bool:
    chrome = find_chrome()
    if not chrome:
        return False
    import pathlib
    url = pathlib.Path(html_path).resolve().as_uri()
    cmd = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
           f"--window-size={width},{height}", f"--screenshot={png_path}", url]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return os.path.exists(png_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
