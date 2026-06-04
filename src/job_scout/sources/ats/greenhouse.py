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

import logging
from datetime import datetime, timezone

from . import _common

log = logging.getLogger("job_scout.sources.ats.greenhouse")

SOURCE = "greenhouse"
_BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def fetch(company, config) -> list[dict]:
    """Fetch open Greenhouse postings for `company`. Returns raw dicts."""
    slug = getattr(company, "slug", None)
    if not slug:
        log.debug("greenhouse: company %s has no slug; skipping", getattr(company, "name", "?"))
        return []

    try:
        resp = _common.get(_BASE.format(slug=slug), {"content": "true"})
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

        dt = _common.parse_iso(job.get("updated_at"))
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
                "is_remote": _common.is_remote_text(location),
                "date_posted": dt.isoformat() if dt else None,
                "description": _common.strip_html(job.get("content")),
                "comp_text": None,
            }
        )

    log.info("greenhouse %s: %d listings", slug, len(rows))
    return rows
