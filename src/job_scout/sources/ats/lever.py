"""Lever ATS source — public postings JSON API.

Endpoint (one GET per company, public, no auth):
    GET https://api.lever.co/v0/postings/{slug}?mode=json

Field mapping (posting object -> raw dict, per RAW DICT CONTRACT in sources/base.py):
    title        <- text
    location     <- categories.location
    url          <- hostedUrl (preferred) or applyUrl
    date_posted  <- createdAt           (epoch ms -> ISO)
    description  <- descriptionPlain or description (HTML lightly stripped)
    company      <- company.name
    source       =  "lever"
    is_remote    from workplaceType == "remote" or location text containing "remote"
    comp_text    usually absent (None)

Ethics (PROJECT.md §2): public JSON only, low volume — a single GET, one light
retry. On any error we return [] (the pipeline isolates per-company too).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import _common

log = logging.getLogger("job_scout.sources.ats.lever")

SOURCE = "lever"
_BASE = "https://api.lever.co/v0/postings/{slug}"


def _epoch_ms_to_iso(value) -> str | None:
    if value is None:
        return None
    try:
        seconds = float(value) / 1000.0
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _epoch_ms_to_dt(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _is_remote(workplace_type, location: str | None) -> bool | None:
    if isinstance(workplace_type, str) and workplace_type.strip().lower() == "remote":
        return True
    if location:
        return _common.is_remote_text(location)
    if workplace_type is not None:
        return False
    return None


def fetch(company, config) -> list[dict]:
    """Fetch open Lever postings for `company`. Returns raw dicts."""
    slug = getattr(company, "slug", None)
    if not slug:
        log.debug("lever: company %s has no slug; skipping", getattr(company, "name", "?"))
        return []

    try:
        resp = _common.get(_BASE.format(slug=slug), {"mode": "json"})
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — never crash the run
        log.warning("lever fetch failed for %s: %s", slug, e)
        return []

    if not isinstance(data, list):
        return []

    freshness_hours = getattr(getattr(config, "search", None), "freshness_hours", None)
    now = datetime.now(timezone.utc)

    rows: list[dict] = []
    for post in data:
        if not isinstance(post, dict):
            continue
        title = post.get("text")
        url = post.get("hostedUrl") or post.get("applyUrl")
        if not title or not url:
            continue  # skip half-records (RAW DICT CONTRACT)

        cats = post.get("categories") or {}
        location = cats.get("location") if isinstance(cats, dict) else None

        dt = _epoch_ms_to_dt(post.get("createdAt"))
        if dt and freshness_hours:
            age_hours = (now - dt).total_seconds() / 3600.0
            if age_hours > freshness_hours:
                continue  # too old

        # descriptionPlain is already plain text; fall back to stripping HTML.
        description = post.get("descriptionPlain")
        if not description:
            description = _common.strip_html(post.get("description"))

        rows.append(
            {
                "title": title,
                "company": company.name,
                "url": url,
                "source": SOURCE,
                "location": location,
                "is_remote": _is_remote(post.get("workplaceType"), location),
                "date_posted": _epoch_ms_to_iso(post.get("createdAt")),
                "description": description or None,
                "comp_text": None,
            }
        )

    log.info("lever %s: %d listings", slug, len(rows))
    return rows
