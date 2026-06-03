"""Dedupe — owns the canonical `Job.id`.

Pipeline position:  normalize -> **dedupe** -> score

normalize assigns a *provisional* per-URL id. dedupe replaces it with the
canonical composite-key id (sha1 of `canonical_key`) and collapses duplicates:

  1. exact-match collapse on the canonical id,
  2. a fuzzy second pass (rapidfuzz token_set_ratio >= 90) that catches
     near-dupes like "Acme Corp" vs "Acme Inc" and the same role cross-posted
     to a board and its ATS,
  3. a cross-run pass using `seen` (id -> {first_seen, last_seen, ...}) so the
     daily run only surfaces genuinely-new roles and refreshes `last_seen` on
     still-open ones.

Merge rule: when two records collapse, keep ONE canonical record but prefer the
direct ATS/apply URL (greenhouse/lever/ashby/smartrecruiters/workday) over a
board aggregator (indeed/google/linkedin).
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date

from rapidfuzz import fuzz

from .models import Job

log = logging.getLogger("job_scout.dedupe")

# Sources that are direct ATS endpoints (the apply URL we want to keep).
_ATS_SOURCES = frozenset(
    {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}
)
# Board aggregators (lower priority for the canonical URL).
_BOARD_SOURCES = frozenset({"indeed", "google", "linkedin"})

# Fuzzy merge threshold on canonical_key.
_FUZZY_THRESHOLD = 90

# Seniority noise normalization. Order matters: longer/abbrev forms first.
_SENIORITY_SUBS = (
    (r"\bsr\b", "senior"),
    (r"\bjr\b", "junior"),
)
# Standalone roman-numeral level markers to strip ("Engineer II" -> "Engineer").
_LEVEL_RE = re.compile(r"\b(?:i{1,3}|iv|v)\b")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def canonical_key(job: Job) -> str:
    """Normalized `title | company | location` used for dedupe matching.

    Lowercased, punctuation stripped, whitespace collapsed, seniority noise
    normalized ("sr." -> "senior", "jr." -> "junior") and standalone roman
    level markers (i/ii/iii/iv/v) removed.
    """
    title = _normalize_text(job.title)
    company = _normalize_text(job.company)
    location = _normalize_text(job.location or "")
    return f"{title}|{company}|{location}"


def _normalize_text(text: str) -> str:
    s = text.lower()
    # Strip punctuation FIRST so "sr." -> "sr" before the \bsr\b sub fires.
    s = _PUNCT_RE.sub(" ", s)
    for pat, repl in _SENIORITY_SUBS:
        s = re.sub(pat, repl, s)
    s = _LEVEL_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _canonical_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _source_rank(job: Job) -> int:
    """Lower is better. ATS beats board beats anything unknown."""
    if job.source in _ATS_SOURCES:
        return 0
    if job.source in _BOARD_SOURCES:
        return 2
    return 1


def _prefer(a: Job, b: Job) -> Job:
    """Pick the record to keep when `a` and `b` are duplicates.

    Prefer the direct ATS/apply URL. On a tie, prefer the one carrying more
    information (description present), else keep the incumbent `a`.
    """
    ra, rb = _source_rank(a), _source_rank(b)
    if rb < ra:
        return b
    if ra < rb:
        return a
    # Same source tier: prefer the richer record.
    if not a.description and b.description:
        return b
    return a


def dedupe(jobs: list[Job], seen: dict[str, dict]) -> list[Job]:
    """Collapse duplicates, assign canonical ids, and set first/last seen.

    Returns all surviving unique jobs (both genuinely new and still-open),
    each with `id`, `first_seen`, `last_seen` set correctly.
    """
    today = date.today()

    # 1 + 2 (precompute): canonical key + id, then exact-match collapse on id.
    keyed: list[tuple[str, Job]] = []
    for job in jobs:
        key = canonical_key(job)
        job.id = _canonical_id(key)
        keyed.append((key, job))

    # Exact collapse, keyed by canonical id.
    by_id: dict[str, tuple[str, Job]] = {}
    for key, job in keyed:
        existing = by_id.get(job.id)
        if existing is None:
            by_id[job.id] = (key, job)
        else:
            kept = _prefer(existing[1], job)
            by_id[job.id] = (existing[0] if kept is existing[1] else key, kept)

    survivors: list[tuple[str, Job]] = list(by_id.values())

    # 3. Fuzzy second pass on canonical_key (O(n^2), fine at low volume).
    merged: list[tuple[str, Job]] = []
    for key, job in survivors:
        match_idx = None
        for i, (mkey, mjob) in enumerate(merged):
            if fuzz.token_set_ratio(key, mkey) >= _FUZZY_THRESHOLD:
                match_idx = i
                break
        if match_idx is None:
            merged.append((key, job))
        else:
            mkey, mjob = merged[match_idx]
            kept = _prefer(mjob, job)
            # Keep the key that belongs to the kept record.
            kept_key = mkey if kept is mjob else key
            merged[match_idx] = (kept_key, kept)

    # 4. Cross-run: stamp first_seen / last_seen from prior state.
    result: list[Job] = []
    for _key, job in merged:
        prior = seen.get(job.id)
        if prior:
            job.first_seen = _parse_iso(prior.get("first_seen")) or today
            job.last_seen = today
        else:
            job.first_seen = today
            job.last_seen = today
        result.append(job)

    return result


def _parse_iso(value) -> date | None:
    """Parse an ISO date string from prior state; None if absent/unparseable."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
