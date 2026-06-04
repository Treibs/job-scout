"""Normalize raw source dicts into canonical `Job` objects.

`normalize_jobs(raw)` is the ONLY place that constructs `Job`. It takes the raw
dicts emitted by sources (see `sources/base.py` RAW DICT CONTRACT), drops any
half-records, parses dates/booleans best-effort, and assigns a *provisional*
stable id (sha1 of the url). `dedupe.py` later overwrites `Job.id` with the
canonical composite-key hash — see `dedupe.canonical_key`.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from typing import Any

from .models import Job, STATUS_NEW
from .sources.base import valid_raw

log = logging.getLogger("job_scout.normalize")

# Strings that, lowercased, mean "yes this is remote" / "no it isn't".
_TRUTHY = {"true", "t", "1", "yes", "y", "remote", "fully remote", "remote-first"}
_FALSY = {"false", "f", "0", "no", "n", "onsite", "on-site", "in-office", "hybrid"}


def _parse_date(value: Any) -> date | None:
    """Best-effort parse of an ISO string, epoch number, datetime, or date.

    Returns a `datetime.date` or None when unparseable. Never raises.
    """
    if value is None:
        return None

    # Already a date/datetime.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    # Epoch seconds (or millis) as int/float.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        # Heuristic: values that large are millis.
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.utcfromtimestamp(ts).date()
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None

        # Numeric string -> treat as epoch.
        if s.lstrip("-").isdigit():
            return _parse_date(int(s))

        # ISO 8601, possibly with a trailing Z or a time component.
        iso = s.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(iso).date()
        except ValueError:
            pass

        # Plain date forms as a fallback.
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue

    return None


def _coerce_remote(value: Any) -> bool | None:
    """Coerce a raw is_remote value to bool. Pass through bool/None; map strings."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return None
        if s in _TRUTHY:
            return True
        if s in _FALSY:
            return False
        # Unknown free-text: best-effort substring check, else None.
        if "remote" in s:
            return True
        return None
    return None


def _provisional_id(url: str) -> str:
    """First 16 hex of sha1(url). Stable per-URL; dedupe overwrites with the
    canonical composite-key id."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def normalize_jobs(raw: list[dict]) -> list[Job]:
    """Map raw source dicts to `Job` objects. Drops invalid records; no dedup."""
    jobs: list[Job] = []
    for row in raw:
        if not valid_raw(row):
            log.debug("skipping half-record (missing required keys): %r", row.get("url"))
            continue

        url = str(row["url"])
        raw_meta = row.get("_raw") or {}
        search_term = raw_meta.get("search_term") if isinstance(raw_meta, dict) else None
        job = Job(
            id=_provisional_id(url),
            title=str(row["title"]),
            company=str(row["company"]),
            url=url,
            source=str(row["source"]),
            location=row.get("location"),
            is_remote=_coerce_remote(row.get("is_remote")),
            date_posted=_parse_date(row.get("date_posted")),
            description=row.get("description"),
            comp_text=row.get("comp_text"),
            search_term=search_term,
            status=STATUS_NEW,
            # first_seen / last_seen are set by dedupe/state.
            first_seen=None,
            last_seen=None,
        )
        jobs.append(job)

    return jobs
