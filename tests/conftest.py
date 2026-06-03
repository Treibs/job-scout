"""Shared pytest fixtures + sys.path bootstrap.

Puts `src/` on sys.path so `import job_scout...` works without an editable
install, and provides a couple of small shared fixtures (a sample raw-dict
list and a few `Job` objects) used across the suite.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# Make `src/job_scout` importable.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def raw_jobs():
    """A list of raw source dicts matching sources/base.py RAW DICT CONTRACT.

    Mix of complete records, half-records (missing required keys), and varied
    date/remote encodings so normalize tests can exercise the parsing paths.
    """
    return [
        {
            "title": "Director AI",
            "company": "Acme Corp",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "source": "greenhouse",
            "location": "Chicago, IL",
            "is_remote": False,
            "date_posted": "2026-05-01",
            "description": "Lead the AI org.",
            "comp_text": "$250k-$300k",
        },
        {
            "title": "Senior Manager AI",
            "company": "Globex",
            "url": "https://example.com/indeed/2",
            "source": "indeed",
            "location": None,
            "is_remote": "remote",
            "date_posted": 1714521600,  # epoch seconds
            "description": "Remote AI manager.",
        },
        {
            # half-record: missing company -> must be dropped by normalize.
            "title": "Ghost Role",
            "url": "https://example.com/ghost/3",
            "source": "indeed",
        },
        {
            # half-record: missing url -> dropped.
            "title": "Phantom Role",
            "company": "Nowhere",
            "source": "indeed",
        },
    ]


@pytest.fixture
def sample_jobs():
    """A couple of fully-built `Job` objects (scorer/sink-stage shape)."""
    from job_scout.models import Job

    return [
        Job(
            id="aaaa1111bbbb2222",
            title="Director AI",
            company="Acme Corp",
            url="https://boards.greenhouse.io/acme/jobs/1",
            source="greenhouse",
            location="Chicago, IL",
            is_remote=False,
            date_posted=date(2026, 5, 1),
            description="Lead the AI org.",
            comp_text="$250k-$300k",
            score=88.0,
            first_seen=date(2026, 5, 1),
            last_seen=date(2026, 5, 2),
        ),
        Job(
            id="cccc3333dddd4444",
            title="Senior Manager AI",
            company="Globex",
            url="https://example.com/indeed/2",
            source="indeed",
            location=None,
            is_remote=True,
            date_posted=date(2026, 5, 2),
            description="Remote AI manager.",
        ),
    ]
