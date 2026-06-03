"""Workday ATS source — public CXS JSON job-search endpoint.

Workday is the trickiest ATS: every customer ("tenant") runs its own subdomain on
one of several datacenters, and the public careers site ("site", e.g. "External") is
served by a JSON "CXS" API behind it. There is no single canonical URL — we build it
from per-company config fields (tenant, site, datacenter).

Endpoint (one POST per search, public, no auth):
    POST https://{tenant}.{datacenter}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "<keyword>"}

URL construction
    host        = https://{tenant}.{datacenter}.myworkdayjobs.com
    api jobs    = {host}/wday/cxs/{tenant}/{site}/jobs            (POST search)
    public job  = {host}/{site}{externalPath}                     (apply/detail URL)
                  e.g. https://acme.wd1.myworkdayjobs.com/External/job/Chicago/...
    tenant   <- company.tenant
    datacenter <- company.datacenter (e.g. "wd1", "wd5")
    site     <- company.site         (e.g. "External", "careers")

Field mapping (jobPostings[i] -> raw dict, per RAW DICT CONTRACT in sources/base.py):
    title        <- title
    location     <- locationsText
    url          <- {host}/{site}{externalPath}          (absolute public URL)
    date_posted  <- postedOn   (relative text like "Posted 3 Days Ago" — parsed
                                best-effort to a UTC date; None if unparseable)
    description  =  None        (Workday requires a separate detail call per job;
                                we SKIP it to keep request volume low — see §2 ethics)
    company      <- company.name
    source       =  "workday"
    is_remote    derived from locationsText containing "remote"
    comp_text    usually absent (None)

Ethics (PROJECT.md §2): public JSON only, LOW volume. We run ONE searchText (the first
config keyword), page at most a few offsets capped at ~60 total postings, and never
fetch per-job detail. One light retry per request. On ANY error we return [] — Workday
tenants vary wildly and must never crash the run.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("job_scout.sources.ats.workday")

SOURCE = "workday"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 20
_PAGE_LIMIT = 20          # Workday's typical page size
_MAX_TOTAL = 60          # hard cap on postings pulled per company (low volume)

_HOST = "https://{tenant}.{datacenter}.myworkdayjobs.com"
_API = _HOST + "/wday/cxs/{tenant}/{site}/jobs"

# "Posted 3 Days Ago", "Posted Yesterday", "Posted Today", "Posted 30+ Days Ago"
_REL_RE = re.compile(r"(\d+)\s*\+?\s*(day|week|month|hour|minute)s?\s+ago", re.I)


def _is_remote(location: str | None) -> bool | None:
    if not location:
        return None
    return "remote" in location.lower()


def _parse_posted_on(value: str | None, now: datetime) -> datetime | None:
    """Best-effort parse of Workday's relative `postedOn` text into a UTC datetime.

    Handles: "Posted Today", "Posted Yesterday", "Posted N Day(s) Ago",
    "Posted N Week(s)/Month(s)/Hour(s)/Minute(s) Ago", "Posted 30+ Days Ago".
    Also accepts a bare ISO date if a tenant happens to return one.
    Returns None when nothing parseable is found.
    """
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    low = v.lower()

    if "today" in low:
        return now
    if "yesterday" in low:
        return now - timedelta(days=1)

    m = _REL_RE.search(low)
    if m:
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            return None
        unit = m.group(2).lower()
        if unit == "minute":
            return now - timedelta(minutes=n)
        if unit == "hour":
            return now - timedelta(hours=n)
        if unit == "day":
            return now - timedelta(days=n)
        if unit == "week":
            return now - timedelta(weeks=n)
        if unit == "month":
            return now - timedelta(days=30 * n)
        return None

    # Fallback: a tenant may return a plain ISO date/datetime.
    iso = v[:-1] + "+00:00" if v.endswith("Z") else v
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _post(url: str, body: dict) -> requests.Response:
    """Single POST with one light retry/backoff on transient failures."""
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    last_exc: Exception | None = None
    for attempt in range(2):  # at most one retry
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt == 0:
                try:
                    time.sleep(1.0)  # light backoff
                except Exception:  # noqa: BLE001
                    pass
    assert last_exc is not None
    raise last_exc


def fetch(company, config) -> list[dict]:
    """Fetch open Workday postings for `company`. Returns raw dicts.

    Workday needs three per-company fields; if any is missing we skip cleanly.
    """
    tenant = getattr(company, "tenant", None)
    site = getattr(company, "site", None)
    datacenter = getattr(company, "datacenter", None)
    if not (tenant and site and datacenter):
        log.debug(
            "workday: company %s missing tenant/site/datacenter; skipping",
            getattr(company, "name", "?"),
        )
        return []

    host = _HOST.format(tenant=tenant, datacenter=datacenter)
    api_url = _API.format(tenant=tenant, datacenter=datacenter, site=site)

    # ONE searchText only — the first configured keyword (keep volume low).
    keywords = getattr(getattr(config, "search", None), "keywords", None) or []
    search_text = keywords[0] if keywords else ""

    freshness_hours = getattr(getattr(config, "search", None), "freshness_hours", None)
    now = datetime.now(timezone.utc)

    rows: list[dict] = []
    offset = 0
    while offset < _MAX_TOTAL:
        body = {
            "appliedFacets": {},
            "limit": _PAGE_LIMIT,
            "offset": offset,
            "searchText": search_text,
        }
        try:
            resp = _post(api_url, body)
            data = resp.json()
        except Exception as e:  # noqa: BLE001 — never crash the run
            log.warning("workday fetch failed for %s/%s: %s", tenant, site, e)
            break

        postings = data.get("jobPostings") if isinstance(data, dict) else None
        if not isinstance(postings, list) or not postings:
            break

        for job in postings:
            if not isinstance(job, dict):
                continue
            title = job.get("title")
            ext_path = job.get("externalPath")
            if not title or not ext_path:
                continue  # skip half-records (RAW DICT CONTRACT)

            # externalPath is like "/job/Chicago/Director-AI_R-123";
            # the public URL is {host}/{site}{externalPath}.
            path = ext_path if str(ext_path).startswith("/") else "/" + str(ext_path)
            url = f"{host}/{site}{path}"

            location = job.get("locationsText")

            dt = _parse_posted_on(job.get("postedOn"), now)
            if dt and freshness_hours:
                age_hours = (now - dt).total_seconds() / 3600.0
                if age_hours > freshness_hours:
                    continue  # too old

            rows.append(
                {
                    "title": title,
                    "company": company.name,
                    "url": url,
                    "source": SOURCE,
                    "location": location,
                    "is_remote": _is_remote(location),
                    "date_posted": dt.date().isoformat() if dt else None,
                    "description": None,  # detail call skipped to keep volume low
                    "comp_text": None,
                }
            )

        # Stop early if the page wasn't full (no more results).
        if len(postings) < _PAGE_LIMIT:
            break
        offset += _PAGE_LIMIT

    log.info("workday %s/%s: %d listings", tenant, site, len(rows))
    return rows
