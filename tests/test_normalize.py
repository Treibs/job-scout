"""Tests for raw-dict -> Job normalization (normalize.py).

Covers: required-key validation drops half-records; date parsing of ISO strings
+ epoch numbers; is_remote coercion; provisional id is a stable sha1-based hash
of the url.
"""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

pytest.importorskip("job_scout.normalize")

from job_scout.normalize import normalize_jobs
from job_scout.models import Job, STATUS_NEW


def test_drops_half_records(raw_jobs):
    jobs = normalize_jobs(raw_jobs)
    # Two complete records in the fixture; two are half-records.
    assert len(jobs) == 2
    titles = {j.title for j in jobs}
    assert "Ghost Role" not in titles  # missing company
    assert "Phantom Role" not in titles  # missing url


def test_returns_job_objects(raw_jobs):
    jobs = normalize_jobs(raw_jobs)
    assert all(isinstance(j, Job) for j in jobs)
    j = jobs[0]
    assert j.title == "Director AI"
    assert j.company == "Acme Corp"
    assert j.source == "greenhouse"
    assert j.status == STATUS_NEW
    # first/last seen are left for the dedupe/state stage.
    assert j.first_seen is None
    assert j.last_seen is None


def test_iso_date_parsing():
    raw = [{
        "title": "T", "company": "C", "source": "indeed",
        "url": "https://x/1", "date_posted": "2026-05-01",
    }]
    job = normalize_jobs(raw)[0]
    assert job.date_posted == date(2026, 5, 1)


def test_iso_datetime_with_z_parsing():
    raw = [{
        "title": "T", "company": "C", "source": "indeed",
        "url": "https://x/2", "date_posted": "2026-05-01T12:30:00Z",
    }]
    job = normalize_jobs(raw)[0]
    assert job.date_posted == date(2026, 5, 1)


def test_epoch_seconds_parsing():
    # 1714521600 = 2024-05-01 00:00:00 UTC
    raw = [{
        "title": "T", "company": "C", "source": "indeed",
        "url": "https://x/3", "date_posted": 1714521600,
    }]
    job = normalize_jobs(raw)[0]
    assert job.date_posted == date(2024, 5, 1)


def test_epoch_millis_parsing():
    # 1714521600000 ms = 2024-05-01 (heuristic > 1e12 -> millis)
    raw = [{
        "title": "T", "company": "C", "source": "indeed",
        "url": "https://x/4", "date_posted": 1714521600000,
    }]
    job = normalize_jobs(raw)[0]
    assert job.date_posted == date(2024, 5, 1)


def test_unparseable_date_is_none():
    raw = [{
        "title": "T", "company": "C", "source": "indeed",
        "url": "https://x/5", "date_posted": "not-a-date",
    }]
    job = normalize_jobs(raw)[0]
    assert job.date_posted is None


def test_missing_date_is_none():
    raw = [{"title": "T", "company": "C", "source": "indeed", "url": "https://x/6"}]
    job = normalize_jobs(raw)[0]
    assert job.date_posted is None


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("remote", True),
        ("Fully Remote", True),
        ("yes", True),
        ("true", True),
        ("onsite", False),
        ("hybrid", False),
        ("no", False),
        ("", None),
        (None, None),
        ("some remote-friendly role", True),  # substring fallback
        ("unclear text", None),
    ],
)
def test_is_remote_coercion(value, expected):
    raw = [{
        "title": "T", "company": "C", "source": "indeed",
        "url": "https://x/r", "is_remote": value,
    }]
    job = normalize_jobs(raw)[0]
    assert job.is_remote is expected


def test_provisional_id_is_stable_sha1_of_url():
    url = "https://boards.greenhouse.io/acme/jobs/1"
    expected = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    raw = [{"title": "T", "company": "C", "source": "greenhouse", "url": url}]
    job = normalize_jobs(raw)[0]
    assert job.id == expected
    # Stable: same url -> same id across calls.
    job2 = normalize_jobs(raw)[0]
    assert job2.id == job.id


def test_distinct_urls_give_distinct_ids():
    raw = [
        {"title": "T", "company": "C", "source": "indeed", "url": "https://x/a"},
        {"title": "T", "company": "C", "source": "indeed", "url": "https://x/b"},
    ]
    jobs = normalize_jobs(raw)
    assert jobs[0].id != jobs[1].id


def test_empty_input_returns_empty_list():
    assert normalize_jobs([]) == []
