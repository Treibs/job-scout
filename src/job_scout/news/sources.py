"""Free news sources — Google News RSS + GDELT (+ optional local SearxNG).

No API keys, no auth, no scraping behind a login — same ToS-defensible posture as
the job sources. Each source takes a query and returns raw article dicts:
    {title, url, source, published (ISO or ''), snippet}
Never raises: a dead/rate-limited source returns [] so it can't kill a run.
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from xml.etree.ElementTree import ParseError

import requests

try:  # defused parser: protects against XXE / billion-laughs in feed XML
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:  # pragma: no cover - defusedxml is a declared dependency
    from xml.etree.ElementTree import fromstring as _xml_fromstring

log = logging.getLogger("job_scout.news.sources")

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_TIMEOUT = 20
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DROP_PARAMS = ("utm_", "oc")  # prefixes
_DROP_EXACT = {"ref", "cmpid", "fbclid", "gclid", "mc_cid", "mc_eid"}


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(text))).strip()


def _get(url, params=None):
    headers = {"User-Agent": _UA, "Accept": "application/json, text/xml, */*"}
    last = None
    for attempt in range(2):  # one light retry (GDELT rate-limits)
        try:
            r = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        if attempt == 0:
            time.sleep(1.5)
    log.info("news GET failed (%s): %s", url, last)
    return None


def canonical_url(url: str) -> str:
    """Strip tracking params (utm_*, fbclid, ...) so the same article dedups."""
    try:
        p = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(p.query)
             if not k.lower().startswith(_DROP_PARAMS) and k.lower() not in _DROP_EXACT]
        return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ""))
    except Exception:  # noqa: BLE001
        return url or ""


def google_news_rss(query: str, max_items: int = 20, freshness_hours: int = 96) -> list[dict]:
    """Google News RSS search -> raw article dicts."""
    when_days = max(1, round(freshness_hours / 24))
    r = _get("https://news.google.com/rss/search",
             {"q": f"{query} when:{when_days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if not r:
        return []
    return parse_google_rss(r.content, max_items)


def parse_google_rss(xml_bytes, max_items: int = 20) -> list[dict]:
    """Pure parse of a Google News RSS body (separated out for testing)."""
    try:
        root = _xml_fromstring(xml_bytes)
    except (ParseError, ValueError) as e:  # ValueError covers defusedxml's blocks
        log.info("google news RSS parse error: %s", e)
        return []
    out = []
    for item in root.findall(".//item")[:max_items]:
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        src_el = item.find("{*}source")
        source = _clean(src_el.text) if src_el is not None and src_el.text else "Google News"
        out.append({
            "title": title, "url": link, "source": source,
            "published": _rfc822_to_iso(item.findtext("pubDate")),
            "snippet": _clean(item.findtext("description")),
        })
    return out


def gdelt(query: str, max_items: int = 20, freshness_hours: int = 96) -> list[dict]:
    """GDELT 2.1 Doc API (artlist) -> raw article dicts."""
    r = _get("https://api.gdeltproject.org/api/v2/doc/doc",
             {"query": query, "mode": "artlist", "format": "json",
              "maxrecords": min(max_items, 75), "sort": "datedesc",
              "timespan": f"{max(1, freshness_hours)}h"})
    if not r:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    out = []
    for a in (data.get("articles") or [])[:max_items]:
        title = _clean(a.get("title"))
        link = (a.get("url") or "").strip()
        if not title or not link:
            continue
        out.append({"title": title, "url": link, "source": a.get("domain") or "",
                    "published": _gdelt_date_to_iso(a.get("seendate")), "snippet": ""})
    return out


def searxng(query: str, base_url: str, max_items: int = 20) -> list[dict]:
    """Optional: a local SearxNG instance's news category."""
    r = _get(f"{base_url.rstrip('/')}/search",
             {"q": query, "format": "json", "categories": "news"})
    if not r:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    out = []
    for a in (data.get("results") or [])[:max_items]:
        title = _clean(a.get("title"))
        link = (a.get("url") or "").strip()
        if not title or not link:
            continue
        out.append({"title": title, "url": link,
                    "source": a.get("engine") or urlparse(link).netloc,
                    "published": (a.get("publishedDate") or ""),
                    "snippet": _clean(a.get("content"))})
    return out


def _rfc822_to_iso(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _gdelt_date_to_iso(value: str | None) -> str:
    if not value or not isinstance(value, str):
        return ""
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return ""
