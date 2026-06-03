"""Greenhouse ATS source — public job board JSON API.

Endpoint (one GET per company, public, no auth):
    GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Field mapping (job object -> raw dict, per RAW DICT CONTRACT in sources/base.py):
    title        <- title
    location     <- location.name
    url          <- absolute_url            (direct apply URL)
    date_posted  <- updated_at              (ISO 8601)
    description  <- content                 (HTML, tags lightly stripped)
    company      <- company.name
    source       =  "greenhouse"
    is_remote    derived from location text containing "remote"
    comp_text    usually absent (None)

Ethics (PROJECT.md §2): public JSON only, low volume — a single GET, one light
retry. On any error we return [] (the pipeline isolates per-company too).
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

import requests

log = logging.getLogger("job_scout.sources.ats.greenhouse")

SOURCE = "greenhouse"
_BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 15
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _strip_html(raw: str | None) -> str | None:
    if not raw:
        return None
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_remote(location: str | None) -> bool | None:
    if not location:
        return None
    return "remote" in location.lower()


def _get(url: str, params: dict) -> requests.Response:
    """Single GET with one light retry/backoff on transient failures."""
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    last_exc: Exception | None = None
    for attempt in range(2):  # at most one retry
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt == 0:
                try:
                    import time

                    time.sleep(1.0)  # light backoff
                except Exception:  # noqa: BLE001
                    pass
    assert last_exc is not None
    raise last_exc


def fetch(company, config) -> list[dict]:
    """Fetch open Greenhouse postings for `company`. Returns raw dicts."""
    slug = getattr(company, "slug", None)
    if not slug:
        log.debug("greenhouse: company %s has no slug; skipping", getattr(company, "name", "?"))
        return []

    try:
        resp = _get(_BASE.format(slug=slug), {"content": "true"})
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — never crash the run
        log.warning("greenhouse fetch failed for %s: %s", slug, e)
        return []

    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []

    freshness_hours = getattr(getattr(config, "search", None), "freshness_hours", None)
    now = datetime.now(timezone.utc)

    rows: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = job.get("title")
        url = job.get("absolute_url")
        if not title or not url:
            continue  # skip half-records (RAW DICT CONTRACT)

        loc = job.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else None

        dt = _parse_iso(job.get("updated_at"))
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
                "date_posted": dt.isoformat() if dt else None,
                "description": _strip_html(job.get("content")),
                "comp_text": None,
            }
        )

    log.info("greenhouse %s: %d listings", slug, len(rows))
    return rows
