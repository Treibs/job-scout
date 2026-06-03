"""Tests for dedupe.py — canonical_key normalization, exact + fuzzy collapse,
URL-preference merge rule, and the cross-run first/last seen stamping.

rapidfuzz is a real dependency (dedupe imports it at module level), so we skip
the whole module if it isn't installed in the test env.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("rapidfuzz")
pytest.importorskip("job_scout.dedupe")

from job_scout.dedupe import dedupe, canonical_key
from job_scout.models import Job


def _job(title, company, url, source, location=None, description=None) -> Job:
    return Job(
        id="provisional",
        title=title,
        company=company,
        url=url,
        source=source,
        location=location,
        description=description,
    )


# ── canonical_key normalization ──────────────────────────────────────────────
def test_canonical_key_collapses_sr_and_senior():
    a = _job("Sr. Engineer", "Acme", "https://x/1", "indeed", "Chicago, IL")
    b = _job("Senior Engineer", "Acme", "https://x/2", "indeed", "Chicago, IL")
    assert canonical_key(a) == canonical_key(b)


def test_canonical_key_strips_punctuation_and_case():
    a = _job("Director, AI!", "Acme Corp.", "https://x/1", "indeed", "Chicago")
    b = _job("director ai", "acme corp", "https://x/2", "indeed", "chicago")
    assert canonical_key(a) == canonical_key(b)


def test_canonical_key_strips_roman_level_markers():
    a = _job("Engineer II", "Acme", "https://x/1", "indeed", "Chicago")
    b = _job("Engineer", "Acme", "https://x/2", "indeed", "Chicago")
    assert canonical_key(a) == canonical_key(b)


def test_canonical_key_collapses_whitespace():
    a = _job("Director   AI", "Acme", "https://x/1", "indeed")
    b = _job("Director AI", "Acme", "https://x/2", "indeed")
    assert canonical_key(a) == canonical_key(b)


# ── exact-dup collapse + ATS-over-board preference ───────────────────────────
def test_exact_dup_collapse_keeps_ats_over_board():
    board = _job("Director AI", "Acme", "https://indeed.com/job/1", "indeed", "Chicago")
    ats = _job("Director AI", "Acme", "https://boards.greenhouse.io/acme/1", "greenhouse", "Chicago")
    result = dedupe([board, ats], seen={})
    assert len(result) == 1
    kept = result[0]
    assert kept.source == "greenhouse"
    assert "greenhouse" in kept.url


def test_exact_dup_collapse_order_independent():
    board = _job("Director AI", "Acme", "https://indeed.com/job/1", "indeed", "Chicago")
    ats = _job("Director AI", "Acme", "https://boards.greenhouse.io/acme/1", "greenhouse", "Chicago")
    result = dedupe([ats, board], seen={})  # ATS first this time
    assert len(result) == 1
    assert result[0].source == "greenhouse"


# ── fuzzy second pass ────────────────────────────────────────────────────────
def test_fuzzy_merge_acme_corp_vs_acme_inc():
    a = _job("Director AI", "Acme Corp", "https://indeed.com/job/1", "indeed", "Chicago")
    b = _job("Director AI", "Acme Inc", "https://boards.lever.co/acme/1", "lever", "Chicago")
    result = dedupe([a, b], seen={})
    assert len(result) == 1
    # ATS (lever) wins the merge.
    assert result[0].source == "lever"


def test_distinct_roles_not_merged():
    a = _job("Director AI", "Acme", "https://x/1", "indeed", "Chicago")
    b = _job("Janitor", "Zenith Logistics", "https://x/2", "indeed", "Dallas")
    result = dedupe([a, b], seen={})
    assert len(result) == 2


# ── cross-run first/last seen ────────────────────────────────────────────────
def test_seen_id_keeps_first_seen_and_refreshes_last_seen():
    today = date.today()
    job = _job("Director AI", "Acme", "https://boards.greenhouse.io/acme/1", "greenhouse", "Chicago")
    # First pass to learn the canonical id assigned by dedupe.
    first = dedupe([job], seen={})[0]
    canonical_id = first.id

    prior_first_seen = "2026-01-15"
    seen = {canonical_id: {"first_seen": prior_first_seen, "last_seen": "2026-05-01"}}

    job2 = _job("Director AI", "Acme", "https://boards.greenhouse.io/acme/1", "greenhouse", "Chicago")
    result = dedupe([job2], seen=seen)
    assert len(result) == 1
    kept = result[0]
    assert kept.id == canonical_id
    assert kept.first_seen == date(2026, 1, 15)  # preserved from prior state
    assert kept.last_seen == today  # refreshed to today


def test_new_id_gets_today_for_both():
    today = date.today()
    job = _job("Brand New Role", "Newco", "https://x/new", "indeed", "Remote")
    result = dedupe([job], seen={})
    assert len(result) == 1
    kept = result[0]
    assert kept.first_seen == today
    assert kept.last_seen == today


def test_assigns_canonical_id_overwriting_provisional():
    job = _job("Director AI", "Acme", "https://x/1", "indeed", "Chicago")
    result = dedupe([job], seen={})
    assert result[0].id != "provisional"
    assert len(result[0].id) == 16  # sha1[:16]


def test_empty_jobs_returns_empty():
    assert dedupe([], seen={}) == []
