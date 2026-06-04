"""Ashby ATS source.

Pulls public job-board postings from Ashby's hosted posting API and returns raw
dicts per the RAW DICT CONTRACT in `sources/base.py`. `normalize.py` maps these
to `Job`s — this module only gathers + light-filters.

Endpoint (public, no auth):
    POST https://api.ashbyhq.com/posting-api/job-board/{slug}
    body: {"includeCompensation": true}
    -> {"jobs": [ {title, location, jobUrl, applyUrl, publishedAt, ...}, ... ]}

Field mapping (Ashby -> raw dict):
    title        <- title
    location     <- location | address.postalAddress.addressLocality | locationName
    url          <- jobUrl | applyUrl
    date_posted  <- publishedAt | updatedAt          (ISO)
    description  <- descriptionPlain | descriptionHtml
    is_remote    <- isRemote
    comp_text    <- compensation.* (compensationTierSummary or first tier)
    company      <- company.name (config) — the target name
    source       <- "ashby"
"""

from __future__ import annotations

import logging
from typing import Any

from . import _common

log = logging.getLogger("job_scout.sources.ashby")

NAME = "ashby"
_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def _location(job: dict) -> str | None:
    loc = job.get("location")
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    # `location` can be a dict on some boards; or use secondaryLocations / address.
    if isinstance(loc, dict):
        for k in ("locationName", "name", "city"):
            v = loc.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    name = job.get("locationName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    addr = job.get("address")
    if isinstance(addr, dict):
        postal = addr.get("postalAddress")
        if isinstance(postal, dict):
            parts = [
                postal.get("addressLocality"),
                postal.get("addressRegion"),
                postal.get("addressCountry"),
            ]
            parts = [p for p in parts if isinstance(p, str) and p.strip()]
            if parts:
                return ", ".join(parts)
    return None


def _comp_text(job: dict) -> str | None:
    comp = job.get("compensation")
    if not isinstance(comp, dict):
        return None
    summary = comp.get("compensationTierSummary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    tiers = comp.get("compensationTiers")
    if isinstance(tiers, list):
        for tier in tiers:
            if isinstance(tier, dict):
                t = tier.get("tierSummary") or tier.get("title")
                if isinstance(t, str) and t.strip():
                    return t.strip()
    return None


def fetch(company: Any, config: Any) -> list[dict]:
    """Fetch Ashby postings for `company`. Returns [] on any failure.

    `company` is a CompanyTarget (needs `.slug` and `.name`).
    `config` is the loaded Config (uses `config.search.freshness_hours`).
    """
    slug = getattr(company, "slug", None)
    if not slug:
        log.warning("ashby: company %r has no slug; skipping", getattr(company, "name", "?"))
        return []

    company_name = getattr(company, "name", None) or slug
    freshness_hours = getattr(getattr(config, "search", None), "freshness_hours", None)

    url = f"{_BASE}/{slug}"
    resp = _common.try_post(url, {"includeCompensation": True}, what="ashby POST")
    if resp is None:
        return []

    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("ashby: bad JSON for %s: %s", slug, e)
        return []

    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []

    rows: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = job.get("title")
        url_out = job.get("jobUrl") or job.get("applyUrl")
        if not title or not url_out:
            continue  # skip half-records (contract: don't emit incomplete)

        date_posted = job.get("publishedAt") or job.get("updatedAt")
        if not _common.is_fresh(date_posted, freshness_hours):
            continue

        # Prefer the plain field; if only HTML is present, flatten it to text so
        # downstream scoring/CSV never gets raw markup (matches the other ATSes).
        description = job.get("descriptionPlain")
        if not isinstance(description, str) or not description.strip():
            description = _common.strip_html(job.get("descriptionHtml"))
        is_remote = job.get("isRemote")

        rows.append(
            {
                "title": title,
                "company": company_name,
                "url": url_out,
                "source": NAME,
                "location": _location(job),
                "is_remote": is_remote if isinstance(is_remote, bool) else None,
                "date_posted": date_posted,
                "description": description if isinstance(description, str) else None,
                "comp_text": _comp_text(job),
            }
        )

    log.info("ashby: %s -> %d postings (after freshness)", slug, len(rows))
    return rows
