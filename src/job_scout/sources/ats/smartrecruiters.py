"""SmartRecruiters ATS source.

Pulls public postings from SmartRecruiters' Posting API and returns raw dicts
per the RAW DICT CONTRACT in `sources/base.py`. `normalize.py` maps these to
`Job`s — this module only gathers + light-filters.

Endpoints (public, no auth):
    list:   GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100
            -> {"content": [ {id, name, location, releasedDate, ref, ...}, ... ]}
    detail: GET https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}
            -> includes jobAd.sections.* (HTML description)

Field mapping (SmartRecruiters -> raw dict):
    title        <- name
    location     <- location.city + location.region (+ "Remote" when remote)
    url          <- ref | applyUrl | https://jobs.smartrecruiters.com/{slug}/{id}
    date_posted  <- releasedDate                     (ISO)
    is_remote    <- location.remote
    description   <- jobAd.sections.* from the detail endpoint (see DESC_FETCH below)
    comp_text    <- None (SmartRecruiters list/detail rarely expose comp uniformly)
    company      <- company.name (config) — the target name
    source       <- "smartrecruiters"

DESC_FETCH CHOICE:
    To keep request volume low and respectful (design principle #5), we fetch the
    per-posting detail endpoint ONLY for postings that survive the freshness
    filter, and we cap the number of detail fetches at `_DETAIL_CAP` (30). Any
    posting beyond the cap (or whose detail fetch fails) is emitted with
    description=None — normalize tolerates a missing description.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("job_scout.sources.smartrecruiters")

NAME = "smartrecruiters"
_BASE = "https://api.smartrecruiters.com/v1/companies"
_JOBS_HOST = "https://jobs.smartrecruiters.com"
_TIMEOUT = 15
_LIST_LIMIT = 100
_DETAIL_CAP = 30  # max per-posting detail fetches per company per run
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _get(url: str, params: dict | None = None) -> requests.Response | None:
    """GET with one light retry. Returns the response or None on hard failure."""
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    last_exc: Exception | None = None
    for attempt in range(2):  # original + one retry
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp
        except Exception as e:  # noqa: BLE001 — sources are untrusted; isolate
            last_exc = e
    log.warning("smartrecruiters GET failed (%s): %s", url, last_exc)
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
        try:
            dt = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_fresh(date_posted: Any, freshness_hours: int | None) -> bool:
    """True if within the freshness window. Missing/unparseable date -> keep."""
    if not freshness_hours or freshness_hours <= 0:
        return True
    dt = _parse_iso(date_posted)
    if dt is None:
        return True
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    return age_h <= freshness_hours


def _location(posting: dict) -> str | None:
    loc = posting.get("location")
    if not isinstance(loc, dict):
        return None
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    parts = [p for p in parts if isinstance(p, str) and p.strip()]
    text = ", ".join(parts) if parts else None
    if loc.get("remote") is True:
        text = f"{text} (Remote)" if text else "Remote"
    return text


def _url(posting: dict, slug: str, pid: str) -> str | None:
    for key in ("ref", "applyUrl"):
        v = posting.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if pid:
        return f"{_JOBS_HOST}/{slug}/{pid}"
    return None


def _description_from_detail(slug: str, pid: str) -> str | None:
    """Fetch the posting detail and flatten jobAd sections to plain text.
    Returns None on any failure."""
    resp = _get(f"{_BASE}/{slug}/postings/{pid}")
    if resp is None:
        return None
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    job_ad = data.get("jobAd")
    if not isinstance(job_ad, dict):
        return None
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return None

    chunks: list[str] = []
    for section in sections.values():
        if not isinstance(section, dict):
            continue
        title = section.get("title")
        text = section.get("text")
        if isinstance(title, str) and title.strip():
            chunks.append(title.strip())
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    joined = "\n\n".join(chunks).strip()
    return joined or None


def fetch(company: Any, config: Any) -> list[dict]:
    """Fetch SmartRecruiters postings for `company`. Returns [] on any failure.

    `company` is a CompanyTarget (needs `.slug` and `.name`).
    `config` is the loaded Config (uses `config.search.freshness_hours`).
    """
    slug = getattr(company, "slug", None)
    if not slug:
        log.warning(
            "smartrecruiters: company %r has no slug; skipping",
            getattr(company, "name", "?"),
        )
        return []

    company_name = getattr(company, "name", None) or slug
    freshness_hours = getattr(getattr(config, "search", None), "freshness_hours", None)

    resp = _get(f"{_BASE}/{slug}/postings", params={"limit": _LIST_LIMIT})
    if resp is None:
        return []

    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("smartrecruiters: bad JSON for %s: %s", slug, e)
        return []

    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, list):
        return []

    rows: list[dict] = []
    detail_fetches = 0
    for posting in content:
        if not isinstance(posting, dict):
            continue
        title = posting.get("name")
        pid = posting.get("id")
        pid = str(pid) if pid is not None else ""
        if not title:
            continue

        date_posted = posting.get("releasedDate")
        if not _is_fresh(date_posted, freshness_hours):
            continue

        url_out = _url(posting, slug, pid)
        if not url_out:
            continue  # skip half-records

        loc = posting.get("location") if isinstance(posting.get("location"), dict) else {}
        is_remote = loc.get("remote") if isinstance(loc, dict) else None

        # Fetch description only for survivors, capped (see DESC_FETCH note above).
        description: str | None = None
        if pid and detail_fetches < _DETAIL_CAP:
            description = _description_from_detail(slug, pid)
            detail_fetches += 1

        rows.append(
            {
                "title": title,
                "company": company_name,
                "url": url_out,
                "source": NAME,
                "location": _location(posting),
                "is_remote": is_remote if isinstance(is_remote, bool) else None,
                "date_posted": date_posted,
                "description": description,
                "comp_text": None,
            }
        )

    log.info(
        "smartrecruiters: %s -> %d postings (%d detail fetches)",
        slug,
        len(rows),
        detail_fetches,
    )
    return rows
