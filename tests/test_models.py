"""Tests for the canonical `Job` model and Sheet schema (models.py).

Covers: Job construction + defaults, to_dict keeps dates, to_json_dict turns
dates into ISO strings, SHEET_COLUMNS shape, VALID_STATUSES contents.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pydantic", reason="pydantic underpins config but models is plain")

from job_scout.models import (
    Job,
    SHEET_COLUMNS,
    VALID_STATUSES,
    STATUS_NEW,
    STATUS_REVIEWING,
    STATUS_APPLIED,
    STATUS_REJECTED,
    STATUS_ARCHIVED,
    STATUS_STALE,
)


def _minimal_job(**overrides) -> Job:
    base = dict(
        id="abc123",
        title="Director AI",
        company="Acme",
        url="https://acme.example/jobs/1",
        source="greenhouse",
    )
    base.update(overrides)
    return Job(**base)


def test_job_construction_required_fields_and_defaults():
    job = _minimal_job()
    assert job.id == "abc123"
    assert job.title == "Director AI"
    assert job.company == "Acme"
    assert job.url == "https://acme.example/jobs/1"
    assert job.source == "greenhouse"
    # Optional / later-stage fields default to None.
    assert job.location is None
    assert job.is_remote is None
    assert job.date_posted is None
    assert job.score is None
    assert job.dimension_scores is None
    assert job.red_flags is None
    # Tracker metadata defaults.
    assert job.status == STATUS_NEW
    assert job.first_seen is None
    assert job.last_seen is None


def test_to_dict_keeps_dates_as_date_objects():
    job = _minimal_job(date_posted=date(2026, 5, 1), first_seen=date(2026, 5, 2))
    d = job.to_dict()
    assert d["date_posted"] == date(2026, 5, 1)
    assert isinstance(d["date_posted"], date)
    assert d["title"] == "Director AI"


def test_to_json_dict_converts_dates_to_iso_strings():
    job = _minimal_job(
        date_posted=date(2026, 5, 1),
        first_seen=date(2026, 4, 30),
        last_seen=date(2026, 5, 3),
    )
    d = job.to_json_dict()
    assert d["date_posted"] == "2026-05-01"
    assert d["first_seen"] == "2026-04-30"
    assert d["last_seen"] == "2026-05-03"
    # all date-bearing keys are now strings, not date objects.
    for k in ("date_posted", "first_seen", "last_seen"):
        assert isinstance(d[k], str)


def test_to_json_dict_leaves_none_dates_as_none():
    job = _minimal_job()
    d = job.to_json_dict()
    assert d["date_posted"] is None
    assert d["first_seen"] is None
    assert d["last_seen"] is None


def test_sheet_columns_shape():
    # Stable column order + count; matches PROJECT.md section 10 header.
    assert isinstance(SHEET_COLUMNS, list)
    assert all(isinstance(c, str) for c in SHEET_COLUMNS)
    assert len(SHEET_COLUMNS) == len(set(SHEET_COLUMNS)), "no duplicate columns"
    expected = [
        "score", "mission", "comp", "learning", "wlb", "prestige",
        "title", "company", "location", "comp_estimate", "source",
        "date_posted", "first_seen", "apply_url", "status",
        "rationale", "red_flags", "day_to_day", "company_blurb", "notes", "applied_on",
    ]
    assert SHEET_COLUMNS == expected


def test_valid_statuses_contents():
    assert VALID_STATUSES == frozenset(
        {
            STATUS_NEW,
            STATUS_REVIEWING,
            "interested",
            STATUS_APPLIED,
            "interview",
            "offer",
            "pass",
            STATUS_REJECTED,
            STATUS_ARCHIVED,
            STATUS_STALE,
        }
    )
    # The default Job.status must be a valid status.
    assert _minimal_job().status in VALID_STATUSES
    # Spot-check string values used by the sink/state.
    assert STATUS_NEW == "new"
    assert STATUS_STALE == "stale"
    assert "applied" in VALID_STATUSES
