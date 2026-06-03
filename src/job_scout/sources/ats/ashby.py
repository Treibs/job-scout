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
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("job_scout.sources.ashby")

NAME = "ashby"
_BASE = "https://api.ashbyhq.com/posting-api/job-board"
_TIMEOUT = 15
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _post(url: str, payload: dict) -> requests.Response | None:
    """POST with one light retry. Returns the response or None on hard failure."""
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    last_exc: Exception | None = None
    for attempt in range(2):  # original + one retry
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp
        except Exception as e:  # noqa: BLE001 — sources are untrusted; isolate
            last_exc = e
    log.warning("ashby POST failed (%s): %s", url, last_exc)
    return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Fall back: date-only or unexpected — try the leading date portion.
        try:
            dt = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_fresh(date_posted: Any, freshness_hours: int | None) -> bool:
    """True if within the freshness window. Missing/unparseable date -> keep
    (we don't drop records just because a date is absent)."""
    if not freshness_hours or freshness_hours <= 0:
        return True
    dt = _parse_iso(date_posted)
    if dt is None:
        return True
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    return age_h <= freshness_hours


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
    resp = _post(url, {"includeCompensation": True})
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
        if not _is_fresh(date_posted, freshness_hours):
            continue

        description = job.get("descriptionPlain") or job.get("descriptionHtml")
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
