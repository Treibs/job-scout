"""Description enrichment — the two-pass trick that keeps LinkedIn block-free.

LinkedIn only returns a job description via a per-job request, and fetching one for
every role would get us rate-limited. So between dedupe and scoring we:

  1. take LinkedIn roles that have no description yet,
  2. serve any we've already fetched from the on-disk cache (so a role is fetched
     at most once, ever),
  3. rank the rest by local resume↔title embedding similarity (cheap, no API),
  4. fetch full JDs for only the top ``linkedin_enrich_max`` (default 30), with a
     randomized delay between requests,
  5. cache them.

Net: a handful of NEW fetches per day, no proxy, full-JD scoring on the roles that
matter. Gated by ``boards.linkedin_fetch_description``; degrades to title-only
scoring on any failure.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path

from .models import Job
from .sources import linkedin_jd

log = logging.getLogger("job_scout.enrich")

# enrich.py is at <repo>/src/job_scout/enrich.py -> parents[2] is <repo>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CACHE_PATH = _REPO_ROOT / "state" / "linkedin_jd_cache.json"


def _load_cache() -> dict:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except OSError as e:
        log.warning("linkedin jd cache save failed: %s", e)


def _delay() -> None:
    """Randomized pause between fetches (env JOB_SCOUT_LINKEDIN_DELAY='lo,hi')."""
    try:
        lo, hi = (float(x) for x in os.getenv("JOB_SCOUT_LINKEDIN_DELAY", "2,5").split(","))
    except (ValueError, TypeError):
        lo, hi = 2.0, 5.0
    if hi > 0:
        time.sleep(random.uniform(lo, hi))


def _rank_by_resume(jobs: list[Job], resume: str) -> list[Job]:
    """Order jobs by descending resume↔(title+company) similarity. Falls back to
    the original order if embeddings are unavailable or there's nothing to rank."""
    if not resume.strip() or len(jobs) <= 1:
        return jobs
    try:
        from sentence_transformers import SentenceTransformer, util  # type: ignore

        model = SentenceTransformer("all-MiniLM-L6-v2")
        texts = [f"{j.title or ''} {j.company or ''}" for j in jobs]
        emb_r = model.encode(resume, convert_to_tensor=True, normalize_embeddings=True)
        emb = model.encode(texts, convert_to_tensor=True, normalize_embeddings=True)
        sims = util.cos_sim(emb_r, emb)[0]
        order = sorted(range(len(jobs)), key=lambda i: float(sims[i]), reverse=True)
        return [jobs[i] for i in order]
    except Exception as e:  # noqa: BLE001
        log.info("resume rank unavailable, using original order: %s", e)
        return jobs


def enrich_descriptions(jobs: list[Job], config, fetch_fn=None) -> list[Job]:
    """Attach LinkedIn JDs to the most resume-relevant undescribed roles (capped,
    cached). ``fetch_fn`` is injectable for tests. Returns the same list, mutated."""
    boards = config.sources.boards
    if not getattr(boards, "linkedin_fetch_description", False):
        return jobs

    targets = [
        j for j in jobs
        if j.source == "linkedin" and not (j.description or "").strip()
    ]
    if not targets:
        return jobs

    cache = _load_cache()
    need: list[Job] = []
    hits = 0
    for j in targets:
        cached = cache.get(j.id)
        if cached:
            j.description = cached
            hits += 1
        else:
            need.append(j)

    max_n = int(getattr(boards, "linkedin_enrich_max", 30) or 30)
    to_fetch = _rank_by_resume(need, config.resume_text or "")[:max_n]

    fetch_fn = fetch_fn or linkedin_jd.fetch_description
    fetched = 0
    for i, j in enumerate(to_fetch):
        desc = fetch_fn(j.url)
        if desc:
            j.description = desc
            cache[j.id] = desc
            fetched += 1
        # Pace EVERY request, not just successes — a 429/block returns None, and we
        # must keep backing off then (skipping the delay would hammer LinkedIn exactly
        # when it's throttling). No trailing sleep after the last item.
        if i < len(to_fetch) - 1:
            _delay()

    if fetched:
        _save_cache(cache)
    log.info(
        "linkedin enrich: %d from cache, %d fetched (of %d undescribed, cap %d)",
        hits, fetched, len(targets), max_n,
    )
    return jobs
