"""Source contract + safe-fetch wrapper.

Every source (JobSpy boards and each ATS module) implements `Source` and returns
a list of **raw dicts** — NOT `Job` objects. `normalize.py` is the only place
that builds `Job`s. This keeps gathering deterministic and mapping centralized.

────────────────────────────────────────────────────────────────────────────
RAW DICT CONTRACT  (every source must emit dicts with these keys)
────────────────────────────────────────────────────────────────────────────
Required:
    title: str
    company: str
    url: str          # prefer the direct ATS/apply URL
    source: str        # this source's `name` (e.g. "greenhouse", "indeed")
Optional (use None when unknown — normalize handles defaults):
    location: str | None
    is_remote: bool | None
    date_posted: str | date | None   # ISO string or date; normalize parses it
    description: str | None
    comp_text: str | None
Anything else may be included under a "_raw" key for debugging; normalize ignores it.

A source that cannot produce a value for a required key should SKIP that record
(don't emit a half-record), not raise.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("job_scout.sources")

# The keys normalize.py reads off each raw dict.
RAW_REQUIRED = ("title", "company", "url", "source")
RAW_OPTIONAL = ("location", "is_remote", "date_posted", "description", "comp_text")


@runtime_checkable
class Source(Protocol):
    """A job source. Implementations live in `sources/` and `sources/ats/`."""

    name: str

    def fetch(self, config: Any) -> list[dict]:
        """Return raw job dicts (see RAW DICT CONTRACT). May raise; callers must
        wrap with `safe_fetch`. `config` is the loaded Config object."""
        ...


def safe_fetch(source: Source, config: Any) -> list[dict]:
    """Run `source.fetch`, isolating failures.

    Design principle #3: one dead source (changed selector, a 429, a network
    blip) must NOT kill the run. We log and return an empty list instead.
    """
    name = getattr(source, "name", source.__class__.__name__)
    try:
        rows = source.fetch(config) or []
        log.info("source %s: %d raw listings", name, len(rows))
        # Tag every row with the source name so normalize/dedupe can trust it.
        for r in rows:
            r.setdefault("source", name)
        return rows
    except Exception as e:  # noqa: BLE001 — deliberately broad; sources are untrusted
        log.warning("source %s failed (skipped): %s", name, e, exc_info=False)
        return []


def valid_raw(row: dict) -> bool:
    """True if a raw dict has all required keys non-empty. normalize uses this
    to drop half-records defensively."""
    return all(row.get(k) for k in RAW_REQUIRED)
