"""לקוח מינימלי ל-API של מדיה-ויקי (ספריית התקן בלבד).

מכבד קצב בקשות, ``maxlag`` ו-User-Agent מזהה, ותומך בהמשכיות (continue) של שאילתות.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    pass


def _retry_after(header: str | None, fallback: float) -> float:
    """מפרש Retry-After (שניות או תאריך HTTP). מוגבל ל-10 דקות."""
    if not header:
        return fallback
    header = header.strip()
    if header.isdigit():
        return min(float(header), 600.0)
    try:
        import email.utils
        when = email.utils.parsedate_to_datetime(header)
        return min(max((when.timestamp() - time.time()), 1.0), 600.0)
    except Exception:
        return fallback


class MediaWikiClient:
    def __init__(self, api_url: str, user_agent: str, rate_limit_seconds: float = 1.0,
                 maxlag: int = 5, timeout: int = 60):
        self.api_url = api_url
        self.user_agent = user_agent
        self.rate_limit = rate_limit_seconds
        self.maxlag = maxlag
        self.timeout = timeout
        self._last_request = 0.0
        self.requests_made = 0
        self.rate_limited = 0
        self._cookies: dict[str, str] = {}

    # ------------------------------------------------------------------ בסיס
    def _throttle(self) -> None:
        wait = self.rate_limit - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def raw(self, params: dict, method: str = "GET", retries: int = 6) -> dict:
        """בקשה אחת ל-API עם ניסיונות חוזרים.

        429 / 503 (יותר מדי בקשות, שרת עמוס): מכבד את הכותרת Retry-After, ואם אין – המתנה
        מעריכית (5, 10, 20... שניות עד 5 דקות). אחרי כל 429 הקצב הבסיסי מואט ב-50% לשארית הריצה.
        """
        params = dict(params)
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        if self.maxlag:
            params.setdefault("maxlag", str(self.maxlag))
        data = urllib.parse.urlencode(params, encoding="utf-8").encode("utf-8")
        delay = 5.0
        for attempt in range(retries + 1):
            self._throttle()
            if method == "GET":
                req = urllib.request.Request(self.api_url + "?" + data.decode("utf-8"))
            else:
                req = urllib.request.Request(self.api_url, data=data, method="POST")
            req.add_header("User-Agent", self.user_agent)
            if self._cookies:
                req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self._cookies.items()))
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self.requests_made += 1
                    self._store_cookies(resp)
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < retries:
                    wait = _retry_after(exc.headers.get("Retry-After"), delay)
                    self.rate_limited += 1
                    self.rate_limit = min(self.rate_limit * 1.5 + 0.5, 30.0)
                    log.warning("HTTP %s מהשרת (יותר מדי בקשות). ממתין %.0f שניות; הקצב הואט ל-%.1f שניות בין בקשות",
                                exc.code, wait, self.rate_limit)
                    time.sleep(wait)
                    delay = min(delay * 2, 300)
                    continue
                if exc.code >= 500 and attempt < retries:
                    log.warning("שגיאת שרת %s, ניסיון חוזר בעוד %.0f שניות", exc.code, delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 300)
                    continue
                raise ApiError(f"HTTP {exc.code} מ-{self.api_url}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == retries:
                    raise ApiError(f"בקשה ל-API נכשלה אחרי {retries} ניסיונות: {exc}") from exc
                log.warning("שגיאת רשת (%s), ניסיון חוזר בעוד %.0f שניות", exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 300)
                continue
            err = body.get("error")
            if err and err.get("code") in ("maxlag", "ratelimited") and attempt < retries:
                wait = _retry_after(None, delay) if err.get("code") == "ratelimited" else delay
                log.info("השרת ביקש להאט (%s), ממתין %.0f שניות", err.get("code"), wait)
                time.sleep(wait)
                delay = min(delay * 2, 300)
                continue
            if err:
                raise ApiError(f"{err.get('code')}: {err.get('info')}")
            return body
        raise ApiError("לא הגענו לכאן")

    def _store_cookies(self, resp) -> None:
        for header in resp.headers.get_all("Set-Cookie") or []:
            first = header.split(";", 1)[0]
            if "=" in first:
                k, v = first.split("=", 1)
                self._cookies[k.strip()] = v.strip()

    def query(self, params: dict):
        """מחזיר איטרטור על כל דפי התשובה של action=query (עם continue)."""
        params = dict(params, action="query")
        cont: dict = {}
        while True:
            body = self.raw({**params, **cont})
            yield body.get("query", {})
            if "continue" not in body:
                return
            cont = body["continue"]

    # -------------------------------------------------------------- שאילתות
    def category_members(self, category: str, cmtype: str = "page|subcat", namespace: str | None = None):
        title = category if category.startswith("קטגוריה:") else "קטגוריה:" + category
        params = {"list": "categorymembers", "cmtitle": title, "cmtype": cmtype, "cmlimit": "max"}
        if namespace is not None:
            params["cmnamespace"] = namespace
        for chunk in self.query(params):
            for item in chunk.get("categorymembers", []):
                yield item

    def embedded_in(self, template: str, namespaces: str = "0|10|2"):
        title = template if ":" in template else "תבנית:" + template
        params = {"list": "embeddedin", "eititle": title, "eilimit": "max", "einamespace": namespaces}
        for chunk in self.query(params):
            for item in chunk.get("embeddedin", []):
                yield item

    def prefix_search(self, prefix: str, namespace: str = "0|10|2"):
        params = {"list": "prefixsearch", "pssearch": prefix, "pslimit": "max", "psnamespace": namespace}
        for chunk in self.query(params):
            for item in chunk.get("prefixsearch", []):
                yield item

    def search(self, text: str, namespace: str = "0|10|2"):
        params = {"list": "search", "srsearch": text, "srlimit": "max", "srnamespace": namespace, "srwhat": "text"}
        for chunk in self.query(params):
            for item in chunk.get("search", []):
                yield item

    def fetch_batch(self, titles: list[str]) -> tuple[list[dict], dict[str, str]]:
        """מושך עד 50 דפים. מחזיר (דפים, מיפוי כותרת-שהתבקשה -> כותרת-שהתקבלה).

        כותרת שביקשנו ולא חזרה (דף חסר) לא תופיע במיפוי – המתקשר אחראי לרשום אותה כחסרה.
        """
        params = {
            "titles": "|".join(titles),
            "prop": "revisions|categories",
            "rvprop": "content|ids|timestamp",
            "rvslots": "main",
            "cllimit": "max",
            "redirects": "1",
        }
        pages: dict[int, dict] = {}
        resolved: dict[str, str] = {t: t for t in titles}
        for part in self.query(params):
            for item in part.get("normalized", []) or []:
                for req, cur in list(resolved.items()):
                    if cur == item["from"]:
                        resolved[req] = item["to"]
            for item in part.get("redirects", []) or []:
                for req, cur in list(resolved.items()):
                    if cur == item["from"]:
                        resolved[req] = item["to"]
            for page in part.get("pages", []):
                if page.get("missing") or page.get("invalid"):
                    for req, cur in list(resolved.items()):
                        if cur == page.get("title"):
                            del resolved[req]
                    continue
                entry = pages.setdefault(page["pageid"], {"title": page["title"], "pageid": page["pageid"],
                                                          "wikitext": None, "categories": [], "revid": None})
                revs = page.get("revisions") or []
                if revs and entry["wikitext"] is None:
                    rev = revs[0]
                    entry["wikitext"] = rev.get("slots", {}).get("main", {}).get("content", rev.get("content"))
                    entry["revid"] = rev.get("revid")
                for cat in page.get("categories", []) or []:
                    name = cat["title"].split(":", 1)[-1]
                    if name not in entry["categories"]:
                        entry["categories"].append(name)
        returned = {p["title"] for p in pages.values()}
        resolved = {req: cur for req, cur in resolved.items() if cur in returned}
        return list(pages.values()), resolved

    def get_pages(self, titles: list[str] | None = None, pageids: list[int] | None = None, batch: int = 50):
        """איטרטור נוח על דפים לפי כותרות (לשימוש כשלא צריך מעקב אחרי חסרים)."""
        keys = titles if titles is not None else [str(p) for p in (pageids or [])]
        for start in range(0, len(keys), batch):
            chunk = keys[start:start + batch]
            if titles is not None:
                pages, _resolved = self.fetch_batch(chunk)
                yield from pages
            else:
                params = {"pageids": "|".join(chunk), "prop": "revisions|categories", "rvprop": "content|ids",
                          "rvslots": "main", "cllimit": "max"}
                for part in self.query(params):
                    for page in part.get("pages", []):
                        if page.get("missing"):
                            continue
                        revs = page.get("revisions") or []
                        yield {"title": page["title"], "pageid": page["pageid"], "revid": revs[0].get("revid") if revs else None,
                               "wikitext": revs[0].get("slots", {}).get("main", {}).get("content") if revs else None,
                               "categories": [c["title"].split(":", 1)[-1] for c in page.get("categories", []) or []]}

    def site_info(self) -> dict:
        body = self.raw({"action": "query", "meta": "siteinfo", "siprop": "general|statistics|namespaces"})
        return body.get("query", {})

    # ------------------------------------------------------------- עריכה
    def login(self, username: str, password: str) -> None:
        tok = self.raw({"action": "query", "meta": "tokens", "type": "login"})["query"]["tokens"]["logintoken"]
        body = self.raw({"action": "login", "lgname": username, "lgpassword": password, "lgtoken": tok}, method="POST")
        if body.get("login", {}).get("result") != "Success":
            raise ApiError(f"ההתחברות נכשלה: {body.get('login')}")

    def csrf_token(self) -> str:
        return self.raw({"action": "query", "meta": "tokens"})["query"]["tokens"]["csrftoken"]

    def edit(self, title: str, text: str, summary: str, bot: bool = True, createonly: bool = False) -> dict:
        params = {"action": "edit", "title": title, "text": text, "summary": summary, "token": self.csrf_token()}
        if bot:
            params["bot"] = "1"
        if createonly:
            params["createonly"] = "1"
        return self.raw(params, method="POST")
