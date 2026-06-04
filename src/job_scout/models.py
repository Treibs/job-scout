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
# The "pipeline" the user works in the dashboard:
#   new → interested → applied → interview → offer   (with pass/rejected/archived
#   as exits, and stale = listing disappeared from source).
STATUS_NEW = "new"
STATUS_REVIEWING = "reviewing"
STATUS_INTERESTED = "interested"  # user flagged from the dashboard
STATUS_APPLIED = "applied"
STATUS_INTERVIEW = "interview"
STATUS_OFFER = "offer"
STATUS_PASS = "pass"  # user dismissed from the dashboard
STATUS_REJECTED = "rejected"
STATUS_ARCHIVED = "archived"
STATUS_STALE = "stale"  # listing disappeared from source
VALID_STATUSES = frozenset(
    {STATUS_NEW, STATUS_REVIEWING, STATUS_INTERESTED, STATUS_APPLIED, STATUS_INTERVIEW,
     STATUS_OFFER, STATUS_PASS, STATUS_REJECTED, STATUS_ARCHIVED, STATUS_STALE}
)
# The ordered pipeline stages the user moves a role through (drives the board).
PIPELINE_STAGES = (STATUS_INTERESTED, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_OFFER)


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
    search_term: str | None = None  # board keyword that surfaced this (None for ATS)

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
    notes: str | None = None  # user's free-text notes (preserved across runs)
    applied_on: str | None = None  # ISO date the user marked it applied

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


# The tracker column order. Every sink (google_sheets.py, csv_file.py) and
# scripts/setup_sheet.py import this so the header and the row-writer never drift
# apart.
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
    "notes",
    "applied_on",
]

# scoring.yaml dimension id  ->  SHEET_COLUMNS column name.
DIMENSION_TO_COLUMN: dict[str, str] = {
    "mission_impact": "mission",
    "compensation": "comp",
    "learning_growth": "learning",
    "work_life_balance": "wlb",
    "prestige": "prestige",
}


def _iso_or_value(value):
    if isinstance(value, date):
        return value.isoformat()
    return value


def job_to_row(job: "Job") -> list:
    """Build a row in ``SHEET_COLUMNS`` order from a Job.

    Values are returned raw — ``None`` stays ``None`` (each sink decides how to
    render it; Sheets and CSV both turn it into an empty string). This is the
    single source of truth for the Job→row mapping, shared by every sink.
    """
    dims = job.dimension_scores or {}
    dim_values = {col: dims.get(dim_id) for dim_id, col in DIMENSION_TO_COLUMN.items()}

    field_map = {
        "score": job.score,
        "mission": dim_values.get("mission"),
        "comp": dim_values.get("comp"),
        "learning": dim_values.get("learning"),
        "wlb": dim_values.get("wlb"),
        "prestige": dim_values.get("prestige"),
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "comp_estimate": job.comp_estimate,
        "source": job.source,
        "date_posted": _iso_or_value(job.date_posted),
        "first_seen": _iso_or_value(job.first_seen),
        "apply_url": job.url,
        "status": job.status,
        "rationale": job.rationale,
        "red_flags": ", ".join(job.red_flags) if job.red_flags else "",
        "notes": job.notes,
        "applied_on": job.applied_on,
    }
    return [field_map.get(col) for col in SHEET_COLUMNS]
