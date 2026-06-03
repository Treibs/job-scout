"""Canonical data model shared across the whole pipeline.

`Job` is the single schema every stage agrees on:

    sources (raw dicts) --normalize--> Job --dedupe--> Job --score--> Job --sink--> Sheet

Sources never emit `Job` directly; they emit *raw dicts* (see `sources/base.py`
for that contract). `normalize.py` is the only place that constructs `Job`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


# Tracker lifecycle states (also the allowed values for `Job.status`).
STATUS_NEW = "new"
STATUS_REVIEWING = "reviewing"
STATUS_APPLIED = "applied"
STATUS_REJECTED = "rejected"
STATUS_ARCHIVED = "archived"
STATUS_STALE = "stale"  # listing disappeared from source
VALID_STATUSES = frozenset(
    {STATUS_NEW, STATUS_REVIEWING, STATUS_APPLIED, STATUS_REJECTED, STATUS_ARCHIVED, STATUS_STALE}
)


@dataclass
class Job:
    """One job listing, canonicalized.

    `id` is the dedupe hash (see `dedupe.py`); it is the upsert key in the Sheet.
    Fields below `# populated by scorer` / `# tracker metadata` are filled in by
    later stages and may be None until then.
    """

    # ── identity / core (from normalize) ────────────────────────────────
    id: str
    title: str
    company: str
    url: str  # prefer the ATS/apply URL over an aggregator URL
    source: str  # "indeed" | "greenhouse" | "lever" | ...
    location: str | None = None
    is_remote: bool | None = None
    date_posted: date | None = None
    description: str | None = None
    comp_text: str | None = None  # raw comp string if present in the listing

    # ── populated by scorer (score.py) ──────────────────────────────────
    score: float | None = None  # overall_score, 0-100
    dimension_scores: dict[str, float] | None = None
    rationale: str | None = None
    red_flags: list[str] | None = None
    comp_estimate: str | None = None

    # ── tracker metadata (sinks / state) ────────────────────────────────
    status: str = STATUS_NEW
    first_seen: date | None = None
    last_seen: date | None = None

    def to_dict(self) -> dict[str, Any]:
        """Plain dict (dates stay as date objects). For JSON, see `to_json_dict`."""
        return asdict(self)

    def to_json_dict(self) -> dict[str, Any]:
        """JSON-safe dict — dates become ISO strings."""
        d = asdict(self)
        for k in ("date_posted", "first_seen", "last_seen"):
            if isinstance(d.get(k), date):
                d[k] = d[k].isoformat()
        return d


# The Google Sheet column order (sinks/google_sheets.py + scripts/setup_sheet.py
# both import this so the header and the row-writer never drift apart).
SHEET_COLUMNS: list[str] = [
    "score",
    "mission",
    "comp",
    "learning",
    "wlb",
    "prestige",
    "title",
    "company",
    "location",
    "comp_estimate",
    "source",
    "date_posted",
    "first_seen",
    "apply_url",
    "status",
    "rationale",
    "red_flags",
]
