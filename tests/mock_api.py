"""שרת API מדומה של מדיה-ויקי לבדיקות, בלי תלויות. משרת את הדפים מ-tests/fixtures/pages/*.wiki.

תומך ב: list=categorymembers, list=embeddedin, list=prefixsearch, list=search, prop=revisions|categories,
meta=siteinfo, meta=tokens, action=login/edit (רישום בלבד). אפשר להזריק שגיאות 429 (FAIL_FIRST) לבדיקת עמידות.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(HERE, "fixtures", "pages")
CAT_RE = re.compile(r"\[\[\s*קטגוריה\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]")
TPL_RE = re.compile(r"\{\{\s*([^{}|]+?)\s*(?:\||\}\})")


def load_pages() -> dict[str, dict]:
    pages: dict[str, dict] = {}
    pid = 100
    for fn in sorted(os.listdir(PAGES_DIR)):
        if not fn.endswith(".wiki"):
            continue
        title = fn[:-5].replace("__", "/").replace("_", " ")
        # קידומת מרחב שם בשם הקובץ: "תבנית--X.wiki" → "תבנית:X"
        title = title.replace("--", ":")
        with open(os.path.join(PAGES_DIR, fn), encoding="utf-8") as fh:
            text = fh.read()
        ns = 10 if title.startswith("תבנית:") else (14 if title.startswith("קטגוריה:") else (2 if title.startswith("משתמש:") else 0))
        pages[title] = {"title": title, "pageid": pid, "ns": ns, "text": text,
                        "categories": [c.strip() for c in CAT_RE.findall(text)],
                        "templates": [t.strip() for t in TPL_RE.findall(text)]}
        pid += 1
    # קטגוריות שמוזכרות אבל אין להן דף
    for p in list(pages.values()):
        for c in p["categories"]:
            t = "קטגוריה:" + c
            if t not in pages:
                pages[t] = {"title": t, "pageid": pid, "ns": 14, "text": "", "categories": [], "templates": []}
                pid += 1
    return pages


class State:
    pages = load_pages()
    fail_first = 0          # כמה בקשות ראשונות יחזירו 429
    fail_every = 0          # להחזיר 429 כל N בקשות
    requests = 0
    edits: list[dict] = []
    lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # שקט
        pass

    def do_GET(self):
        self._handle(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        self._handle(urllib.parse.parse_qs(body))

    def _send(self, obj, status=200, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, q):
        with State.lock:
            State.requests += 1
            n = State.requests
        if n <= State.fail_first or (State.fail_every and n % State.fail_every == 0):
            self._send({"error": "too many requests"}, status=429, headers={"Retry-After": "1"})
            return
        action = q.get("action", [""])[0]
        if action == "query":
            self._send({"batchcomplete": True, "query": self._query(q)})
        elif action == "login":
            self._send({"login": {"result": "Success"}})
        elif action == "edit":
            State.edits.append({"title": q["title"][0], "text": q["text"][0], "summary": q.get("summary", [""])[0]})
            self._send({"edit": {"result": "Success", "title": q["title"][0]}})
        else:
            self._send({"error": {"code": "unknown_action", "info": action}})

    def _query(self, q) -> dict:
        out: dict = {}
        pages = State.pages
        if "meta" in q:
            metas = q["meta"][0].split("|")
            if "siteinfo" in metas:
                out["general"] = {"sitename": "חב\"דפדיה (מדומה)", "generator": "MediaWiki 1.41"}
                out["statistics"] = {"articles": sum(1 for p in pages.values() if p["ns"] == 0), "pages": len(pages)}
            if "tokens" in metas:
                out["tokens"] = {"logintoken": "L+\\", "csrftoken": "C+\\"}
        if "list" in q:
            for lst in q["list"][0].split("|"):
                if lst == "categorymembers":
                    cat = q["cmtitle"][0].split(":", 1)[-1]
                    types = q.get("cmtype", ["page|subcat"])[0].split("|")
                    members = []
                    for p in pages.values():
                        if cat in p["categories"]:
                            if p["ns"] == 14 and "subcat" in types:
                                members.append({"pageid": p["pageid"], "ns": 14, "title": p["title"]})
                            elif p["ns"] != 14 and "page" in types:
                                members.append({"pageid": p["pageid"], "ns": p["ns"], "title": p["title"]})
                    out["categorymembers"] = members
                elif lst == "embeddedin":
                    tpl = q["eititle"][0].split(":", 1)[-1]
                    out["embeddedin"] = [{"pageid": p["pageid"], "ns": p["ns"], "title": p["title"]}
                                         for p in pages.values() if tpl in p["templates"]]
                elif lst == "prefixsearch":
                    pref = q["pssearch"][0]
                    out["prefixsearch"] = [{"pageid": p["pageid"], "ns": p["ns"], "title": p["title"]}
                                           for p in pages.values() if p["title"].startswith(pref) or p["title"].split(":", 1)[-1].startswith(pref)]
                elif lst == "search":
                    text = q["srsearch"][0].strip('"')
                    out["search"] = [{"pageid": p["pageid"], "ns": p["ns"], "title": p["title"]}
                                     for p in pages.values() if text in p["text"] or text in p["title"]]
        if "titles" in q:
            titles = q["titles"][0].split("|")
            out["pages"] = []
            out["normalized"] = []
            for t in titles:
                norm = t.replace("_", " ").strip()
                if norm != t:
                    out["normalized"].append({"from": t, "to": norm})
                p = pages.get(norm)
                if p is None:
                    out["pages"].append({"title": norm, "missing": True})
                    continue
                entry = {"pageid": p["pageid"], "ns": p["ns"], "title": p["title"]}
                props = q.get("prop", [""])[0].split("|")
                if "revisions" in props:
                    entry["revisions"] = [{"revid": p["pageid"] * 10, "slots": {"main": {"content": p["text"]}}}]
                if "categories" in props:
                    entry["categories"] = [{"ns": 14, "title": "קטגוריה:" + c} for c in p["categories"]]
                out["pages"].append(entry)
        return out


def serve(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = serve(port)
    print(f"mock MediaWiki API on http://127.0.0.1:{srv.server_port}/api.php ({len(State.pages)} pages)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
