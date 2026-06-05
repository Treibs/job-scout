"""Single-URL job ingestion — paste a link, get a scored Job.

Turns one arbitrary job posting URL into a `Job`. Strategy, in order:

  1. **schema.org/JobPosting JSON-LD** — most job pages embed this for Google
     (title, hiringOrganization, description, location, salary). Uniform and
     reliable across a huge range of sites, so we try it first.
  2. **LinkedIn guest endpoint** — for linkedin.com/jobs URLs, the public
     `…/jobs-guest/jobs/api/jobPosting/{id}` card (reusing `linkedin_jd`).
  3. **Generic fallback** — `og:title` / `<title>` / `<h1>` for the title,
     `<meta name=description>` for the body, the domain for the company.

Never raises — returns None if it can't even get a title. The caller scores the
returned Job and appends it to the tracker (as a `manual` source).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from html import unescape
from urllib.parse import urljoin, urlparse, urlunparse

from .models import Job, STATUS_INTERESTED
from .normalize import _provisional_id
from .sources import linkedin_jd

log = logging.getLogger("job_scout.ingest")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 20

_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:title|og:description|description|og:site_name)["\']'
    r'[^>]+content=["\'](.*?)["\']', re.S | re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    out = _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", text))).strip()
    return out or None


def _meta(html: str, key: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]+content=["\'](.*?)["\']',
        html, re.S | re.I,
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:property|name)=["\']{re.escape(key)}["\']',
            html, re.S | re.I,
        )
    return _clean(m.group(1)) if m else None


def _resolve_public_ip(host: str) -> str | None:
    """Resolve ``host`` and return ONE IP only if EVERY resolved address is public
    (blocks metadata/loopback/RFC1918/etc., and round-robin sets that mix public +
    private). Returns None on any failure or if any address is non-public."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    if not infos:
        return None
    chosen = None
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_unspecified or addr.is_multicast):
            return None
        chosen = chosen or info[4][0]
    return chosen


def _is_public_url(url: str) -> bool:
    """True only if ``url``'s host resolves entirely to public IPs. Blocks SSRF —
    cloud metadata (169.254.169.254), localhost, RFC1918 — for server-side fetches."""
    host = (urlparse(url).hostname or "").strip() if url else ""
    return bool(host) and _resolve_public_ip(host) is not None


def _pinned_adapter(host: str, ip: str):
    """A requests HTTPAdapter that connects to a fixed (pre-validated) IP while
    keeping the original hostname for the Host header, TLS SNI, and cert validation.
    Closes the DNS-rebinding TOCTOU: the IP we validated is the IP we connect to."""
    import requests

    bracket = f"[{ip}]" if ":" in ip else ip

    class _PinnedAdapter(requests.adapters.HTTPAdapter):
        def send(self, request, **kw):
            p = urlparse(request.url)
            port = f":{p.port}" if p.port else ""
            request.url = urlunparse((p.scheme, bracket + port, p.path, p.params, p.query, p.fragment))
            request.headers["Host"] = host
            return super().send(request, **kw)

        def init_poolmanager(self, connections, maxsize, block=False, **kw):
            kw["server_hostname"] = host    # SNI
            kw["assert_hostname"] = host    # cert hostname check
            return super().init_poolmanager(connections, maxsize, block=block, **kw)

    return _PinnedAdapter()


def fetch_public(url, *, headers=None, timeout=_TIMEOUT, max_redirects=5):
    """SSRF-safe GET: re-validates EVERY redirect hop is a public host AND pins the
    connection to the exact validated IP (so a public URL can't 30x-bounce or DNS-
    rebind into an internal address). Ignores proxy env. Returns the final 200
    ``requests.Response`` or None. Never raises."""
    try:
        import requests
    except Exception as e:  # noqa: BLE001
        log.warning("requests unavailable: %s", e)
        return None
    cur = (url or "").strip()
    for _ in range(max_redirects + 1):
        if not cur.lower().startswith(("http://", "https://")):
            return None
        host = (urlparse(cur).hostname or "").strip()
        ip = _resolve_public_ip(host) if host else None
        if not ip:
            return None
        sess = requests.Session()
        sess.trust_env = False  # ignore proxy env vars (no surprise egress)
        adapter = _pinned_adapter(host, ip)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        try:
            r = sess.get(cur, headers=headers or _HEADERS, timeout=timeout, allow_redirects=False)
        except requests.RequestException as e:  # noqa: BLE001
            log.info("fetch_public error for %s: %s", cur, e)
            return None
        finally:
            sess.close()
        if r.is_redirect or r.is_permanent_redirect:
            loc = r.headers.get("Location")
            if not loc:
                return None
            cur = urljoin(cur, loc)  # resolved against the hostname URL; re-validated next loop
            continue
        return r if r.status_code == 200 else None
    return None  # too many redirects


def ingest_url(url: str, fetch=None) -> Job | None:
    """Fetch ``url`` and build a Job (unscored, source=manual, status=interested).
    ``fetch`` is injectable for tests: a callable(url) -> html string (or None)."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None

    explicit_fetch = fetch is not None  # tests inject fetch; skip the SSRF DNS check then
    fetch = fetch or _fetch
    host = (urlparse(url).hostname or "").lower()

    # 1. Known ATS JSON APIs — clean and reliable (these pages are JS-rendered,
    #    so the raw HTML has no JobPosting; the API does).
    ats = _from_ats(url, host)
    if ats and ats.get("title"):
        return _build(url, ats, host)

    # 2. LinkedIn: the public page is auth-walled; use the guest card.
    if "linkedin.com" in host:
        job = _from_linkedin(url)
        if job:
            return job

    # 3. Generic: fetch HTML, try JobPosting JSON-LD, then og:/meta fallback.
    #    Guard the server-side fetch of a user-supplied host against SSRF.
    if not explicit_fetch and not _is_public_url(url):
        log.info("ingest refused non-public URL: %s", url)
        return None
    html = fetch(url)
    if not html:
        return None

    data = _from_jsonld(html, url) or _from_generic(html, url, host)
    if not data or not data.get("title"):
        return None
    return _build(url, data, host)


def _build(url: str, data: dict, host: str) -> Job:
    return Job(
        id=_provisional_id(url),
        title=data["title"],
        company=data.get("company") or _company_from_host(host),
        url=url,
        source="manual",
        location=data.get("location"),
        description=data.get("description"),
        comp_text=data.get("comp_text"),
        status=STATUS_INTERESTED,
    )


def _fetch_json(url: str):
    try:
        import requests
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def _from_ats(url: str, host: str) -> dict | None:
    """Pull a single job from a known ATS's public JSON API (greenhouse, lever)."""
    path = urlparse(url).path
    if "greenhouse.io" in host:
        slug = re.search(r"/([^/]+)/jobs/", path)
        jid = re.search(r"jobs/(\d+)", path) or re.search(r"gh_jid=(\d+)", url)
        if slug and jid:
            j = _fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug.group(1)}/jobs/{jid.group(1)}")
            if isinstance(j, dict) and j.get("title"):
                return {"title": _clean(j["title"]),
                        "company": _company_from_host(slug.group(1)),
                        "location": _clean((j.get("location") or {}).get("name")),
                        "description": _clean(j.get("content")), "comp_text": None}
    if "lever.co" in host:
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            slug, jid = parts[0], parts[1]
            j = _fetch_json(f"https://api.lever.co/v0/postings/{slug}/{jid}?mode=json")
            if isinstance(j, dict) and j.get("text"):
                cats = j.get("categories") or {}
                return {"title": _clean(j["text"]), "company": slug.replace("-", " ").title(),
                        "location": _clean(cats.get("location")),
                        "description": _clean(j.get("descriptionPlain") or j.get("description")),
                        "comp_text": None}
    return None


def _fetch(url: str) -> str | None:
    r = fetch_public(url)  # redirect-validated, SSRF-safe
    return r.text if r is not None else None


def _from_jsonld(html: str, url: str) -> dict | None:
    for block in _LDJSON_RE.findall(html):
        try:
            parsed = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(parsed):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" in types:
                return _job_from_node(node)
    return None


def _walk(obj):
    """Yield every dict in a JSON-LD structure (handles lists + @graph)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _walk(it)


def _job_from_node(node: dict) -> dict:
    org = node.get("hiringOrganization")
    company = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else None)

    loc = node.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = loc.get("address") if isinstance(loc, dict) else None
    location = None
    if isinstance(addr, dict):
        parts = [addr.get("addressLocality"), addr.get("addressRegion")]
        location = ", ".join(p for p in parts if p) or addr.get("addressCountry")
    if node.get("jobLocationType") == "TELECOMMUTE" and not location:
        location = "Remote"

    salary = node.get("baseSalary")
    comp = None
    if isinstance(salary, dict):
        val = salary.get("value")
        cur = salary.get("currency") or "USD"
        if isinstance(val, dict):
            lo, hi = val.get("minValue"), val.get("maxValue")
            if lo or hi:
                comp = f"{cur} {lo or ''}{'-' if lo and hi else ''}{hi or ''} / {val.get('unitText','').lower()}".strip()

    return {
        "title": _clean(node.get("title")),
        "company": _clean(company),
        "description": _clean(node.get("description")),
        "location": _clean(location),
        "comp_text": _clean(comp),
    }


def _from_generic(html: str, url: str, host: str) -> dict | None:
    title = _meta(html, "og:title")
    if not title:
        m = _H1_RE.search(html)
        title = _clean(m.group(1)) if m else None
    if not title:
        m = _TITLE_RE.search(html)
        title = _clean(m.group(1)) if m else None
    if not title:
        return None
    return {
        "title": title,
        "company": _meta(html, "og:site_name"),
        "description": _meta(html, "og:description") or _meta(html, "description"),
        "location": None,
        "comp_text": None,
    }


def _from_linkedin(url: str) -> Job | None:
    """Build a Job from a LinkedIn job URL via the public guest card."""
    desc = linkedin_jd.fetch_description(url)
    jid = linkedin_jd.job_id(url)
    if not jid:
        return None
    # Pull title + company from the guest card markup (best-effort).
    try:
        import requests
        guest = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
        r = requests.get(guest, headers=_HEADERS, timeout=_TIMEOUT)
        html = r.text if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001
        html = ""
    title = _clean(_first(html, r'class="top-card-layout__title[^"]*"[^>]*>(.*?)<', r'<h2[^>]*>(.*?)</h2>'))
    company = _clean(_first(html, r'class="topcard__org-name-link[^"]*"[^>]*>(.*?)<',
                            r'class="topcard__flavor[^"]*"[^>]*>(.*?)<'))
    location = _clean(_first(html, r'class="topcard__flavor topcard__flavor--bullet[^"]*"[^>]*>(.*?)<'))
    if not title and not desc:
        return None
    return Job(
        id=_provisional_id(url),
        title=title or "LinkedIn role",
        company=company or "LinkedIn",
        url=url, source="manual", location=location,
        description=desc, status=STATUS_INTERESTED,
    )


def _first(html: str, *patterns) -> str | None:
    for p in patterns:
        m = re.search(p, html, re.S | re.I)
        if m:
            return m.group(1)
    return None


def _company_from_host(host: str) -> str:
    host = re.sub(r"^(www|jobs|careers|boards|apply|job-boards)\.", "", host)
    base = host.split(".")[0] if host else "Unknown"
    return base.replace("-", " ").title()


def parse_company_url(url: str) -> dict | None:
    """Parse a careers-page URL into a companies.yaml entry (name + ats + ids).

    Deterministic from the URL — the same patterns as docs/finding-ats-slugs.md.
    Returns None if the host isn't a supported ATS. The caller should verify the
    resulting target actually returns jobs before trusting it.
    """
    url = (url or "").strip()
    p = urlparse(url)
    host = (p.hostname or "").lower()
    segs = [s for s in p.path.split("/") if s and s.lower() != "en-us"]

    if "greenhouse.io" in host and segs:
        return {"name": _company_from_host(segs[0]), "ats": "greenhouse", "slug": segs[0]}
    if "lever.co" in host and segs:
        return {"name": _company_from_host(segs[0]), "ats": "lever", "slug": segs[0]}
    if "ashbyhq.com" in host and segs:
        return {"name": _company_from_host(segs[0]), "ats": "ashby", "slug": segs[0]}
    if "smartrecruiters.com" in host and segs:
        return {"name": _company_from_host(segs[0]), "ats": "smartrecruiters", "slug": segs[0]}
    if "myworkdayjobs.com" in host:
        # {tenant}.{dc}.myworkdayjobs.com/{site}
        labels = host.split(".")
        if len(labels) >= 3 and segs:
            return {"name": labels[0].replace("-", " ").title(), "ats": "workday",
                    "tenant": labels[0], "datacenter": labels[1], "site": segs[-1]}
    return None
