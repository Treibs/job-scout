"""Scoring stage — `score_jobs(jobs, config) -> list[Job]` (sorted by score desc).

Three config-driven stages, run in order, cheapest first:

    1. HARD FILTERS  (deterministic, no LLM cost)
         remote policy · exclude industries/keywords · include keywords · seniority gate
    2. PRE-FILTER    (optional — `scoring.pre_filter`)
         "embedding"  -> sentence-transformers cosine sim resume vs JD
         "keyword"    -> token-overlap ratio resume vs JD
         "none"/off   -> skip
    3. LLM RUBRIC    (survivors only — `scoring.dimensions` × `scoring.model`)
         strict-JSON per-dimension scores -> weighted sum normalized to 0-100

Graceful degradation is a first-class requirement:
  · A failed/absent embedding model SKIPS the pre-filter (never crashes).
  · A missing `ANTHROPIC_API_KEY` SKIPS LLM scoring; jobs still return in their
    hard-filtered order.
  · A per-job LLM parse failure (after one retry) leaves `score=None` and tags a
    `scoring_failed` red flag rather than aborting the batch.

`deterministic gather, LLM reason` (principle #6): everything in stages 1-2 is
pure Python; the model only does the judgment work in stage 3.
"""

from __future__ import annotations

import json
import logging
import re

from .config import Config
from .models import Job

log = logging.getLogger("job_scout.score")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def score_jobs(jobs: list[Job], config: Config) -> list[Job]:
    """Filter, pre-filter, then LLM-score `jobs`. Return sorted by score desc."""
    if not jobs:
        return []

    # Stage 1 — deterministic hard filters (cheap rejects, no LLM cost).
    survivors = _hard_filters(jobs, config)
    log.info("hard filters: %d -> %d jobs", len(jobs), len(survivors))

    # Stage 2 — optional pre-filter (embedding / keyword).
    survivors = _pre_filter(survivors, config)
    log.info("pre-filter: -> %d jobs", len(survivors))

    # Stage 3 — LLM rubric scoring on survivors only.
    survivors = _llm_score(survivors, config)

    return _sort_by_score(survivors)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — hard filters
# ─────────────────────────────────────────────────────────────────────────────

# Loose seniority synonyms so the gate stays lenient (avoids false rejects).
_SENIORITY_SYNONYMS: dict[str, list[str]] = {
    "director": ["director", "head", "vp", "vice president"],
    "executive": ["executive", "chief", "c-level", "cxo", "ceo", "cto", "coo", "cfo", "ciso"],
    "vp": ["vp", "vice president", "director", "head"],
    "head": ["head", "director", "vp"],
    "principal": ["principal", "staff", "lead"],
    "lead": ["lead", "principal", "staff", "head"],
    "staff": ["staff", "principal", "senior"],
    "senior": ["senior", "sr", "sr.", "lead", "staff"],
    "manager": ["manager", "mgr", "lead", "head"],
    "chief": ["chief", "executive", "cxo", "ceo", "cto", "coo", "cfo"],
}


def _job_text(job: Job) -> str:
    """Lowercased haystack of title + company + description for keyword matching."""
    parts = [job.title or "", job.company or "", job.description or ""]
    return " ".join(parts).lower()


def _seniority_terms(term: str) -> list[str]:
    """Expand one configured seniority term into itself plus loose synonyms."""
    term = term.strip().lower()
    if not term:
        return []
    terms = {term}
    terms.update(_SENIORITY_SYNONYMS.get(term, []))
    return list(terms)


def _hard_filters(jobs: list[Job], config: Config) -> list[Job]:
    """Deterministic rejects: remote policy, exclude/include keywords, seniority gate."""
    search = config.search
    hf = search.hard_filters
    remote_policy = search.location.remote_policy

    exclude_industries = [s.lower() for s in (hf.exclude_industries or []) if s.strip()]
    exclude_keywords = [s.lower() for s in (hf.exclude_keywords or []) if s.strip()]
    include_keywords = [s.lower() for s in (hf.include_keywords or []) if s.strip()]

    seniority = [s for s in (search.seniority or []) if s and s.strip()]
    gate_on = bool(seniority) and bool(config.scoring.role_fit_gate)

    out: list[Job] = []
    for job in jobs:
        # ── remote policy ───────────────────────────────────────────────
        # "only"    -> keep only is_remote True
        # "exclude" -> drop is_remote True
        # "include" -> keep all
        # Unknown remote status (None) is treated leniently: kept for "include",
        # and not actively dropped for "only"/"exclude" (we only act on known values).
        if remote_policy == "only" and job.is_remote is False:
            continue
        if remote_policy == "exclude" and job.is_remote is True:
            continue

        text = _job_text(job)

        # ── excluded industries / keywords (case-insensitive substring) ──
        if any(term in text for term in exclude_industries):
            continue
        if any(term in text for term in exclude_keywords):
            continue

        # ── required include keywords (at least one if any configured) ───
        if include_keywords and not any(term in text for term in include_keywords):
            continue

        # ── seniority gate (loose contains against title only) ───────────
        if gate_on:
            title = (job.title or "").lower()
            matched = False
            for term in seniority:
                if any(syn in title for syn in _seniority_terms(term)):
                    matched = True
                    break
            if not matched:
                continue

        out.append(job)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — pre-filter (embedding / keyword)
# ─────────────────────────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _jd_text(job: Job) -> str:
    """Text a JD contributes to similarity: title + description."""
    return " ".join(p for p in (job.title or "", job.description or "") if p)


def _pre_filter(jobs: list[Job], config: Config) -> list[Job]:
    """Optional cheap relevance gate before paying for LLM scoring."""
    pf = config.scoring.pre_filter
    if not pf.enabled or pf.method == "none":
        return jobs
    if not jobs:
        return jobs

    resume = config.resume_text or ""
    if not resume.strip():
        log.info("pre-filter skipped: empty resume_text")
        return jobs

    if pf.method == "embedding":
        return _pre_filter_embedding(jobs, resume, pf.threshold)
    if pf.method == "keyword":
        return _pre_filter_keyword(jobs, resume, pf.threshold)

    log.info("pre-filter skipped: unknown method %r", pf.method)
    return jobs


def _pre_filter_embedding(jobs: list[Job], resume: str, threshold: float) -> list[Job]:
    """Cosine-sim resume vs each JD via sentence-transformers. Skip on any failure."""
    try:  # lazy import + lazy model load — both may legitimately fail
        from sentence_transformers import SentenceTransformer, util  # type: ignore

        model = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception as e:  # noqa: BLE001
        log.warning("embedding pre-filter unavailable (skipped): %s", e)
        return jobs

    try:
        jd_texts = [_jd_text(j) for j in jobs]
        resume_emb = model.encode(resume, convert_to_tensor=True, normalize_embeddings=True)
        jd_embs = model.encode(jd_texts, convert_to_tensor=True, normalize_embeddings=True)
        sims = util.cos_sim(resume_emb, jd_embs)[0]

        out: list[Job] = []
        for job, sim in zip(jobs, sims):
            if float(sim) >= threshold:
                out.append(job)
        log.info("embedding pre-filter (thr=%.2f): %d -> %d", threshold, len(jobs), len(out))
        return out
    except Exception as e:  # noqa: BLE001 — never let scoring die on the pre-filter
        log.warning("embedding pre-filter failed mid-run (skipped): %s", e)
        return jobs


def _pre_filter_keyword(jobs: list[Job], resume: str, threshold: float) -> list[Job]:
    """Token-overlap ratio (|resume ∩ JD| / |JD tokens|) >= threshold."""
    resume_tokens = _tokenize(resume)
    if not resume_tokens:
        return jobs

    out: list[Job] = []
    for job in jobs:
        jd_tokens = _tokenize(_jd_text(job))
        if not jd_tokens:
            # No JD signal to compare against — keep (let the LLM decide).
            out.append(job)
            continue
        overlap = len(resume_tokens & jd_tokens) / len(jd_tokens)
        if overlap >= threshold:
            out.append(job)
    log.info("keyword pre-filter (thr=%.2f): %d -> %d", threshold, len(jobs), len(out))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — LLM rubric scoring
# ─────────────────────────────────────────────────────────────────────────────
def _llm_score(jobs: list[Job], config: Config) -> list[Job]:
    """Score each survivor with the configured model; degrade gracefully."""
    if not jobs:
        return jobs

    api_key = config.env.anthropic_api_key
    if not api_key:
        log.info("LLM scoring skipped: ANTHROPIC_API_KEY not set (returning unscored jobs)")
        return jobs

    dimensions = config.scoring.dimensions
    if not dimensions:
        log.info("LLM scoring skipped: no scoring dimensions configured")
        return jobs

    try:  # lazy import — anthropic is only needed when we actually score
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=api_key)
    except Exception as e:  # noqa: BLE001
        log.warning("anthropic client unavailable (LLM scoring skipped): %s", e)
        return jobs

    model = config.scoring.model
    scale = config.scoring.scale or [0, 5]
    scale_lo, scale_hi = scale[0], scale[-1]
    resume = config.resume_text or ""

    for job in jobs:
        try:
            result = _score_one(client, model, dimensions, scale_lo, scale_hi, resume, job)
        except Exception as e:  # noqa: BLE001 — one bad job must not kill the batch
            log.warning("LLM scoring error for %r: %s", job.title, e)
            result = None

        if result is None:
            job.score = None
            job.red_flags = (job.red_flags or []) + ["scoring_failed"]
            continue

        dim_scores = result.get("dimension_scores") or {}
        job.dimension_scores = dim_scores
        job.score = _weighted_overall(dim_scores, dimensions, scale_lo, scale_hi)
        job.rationale = result.get("rationale")
        rf = result.get("red_flags")
        job.red_flags = rf if isinstance(rf, list) else ([] if rf in (None, "") else [str(rf)])
        job.comp_estimate = result.get("comp_estimate")

    return jobs


def _score_one(client, model, dimensions, scale_lo, scale_hi, resume, job) -> dict | None:
    """Prompt the model for one job; parse strict JSON, retry once on failure."""
    system_prompt, user_prompt = _build_prompts(dimensions, scale_lo, scale_hi, resume, job)

    last_err: Exception | None = None
    for attempt in range(2):  # one initial try + one retry on parse failure
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = _response_text(resp)
            parsed = _extract_json(raw)
            if parsed is not None:
                return parsed
            last_err = ValueError("no JSON object found in response")
        except Exception as e:  # noqa: BLE001 — includes API/transport errors
            last_err = e
        if attempt == 0:
            log.info("scoring retry for %r (reason: %s)", job.title, last_err)

    log.warning("LLM scoring failed for %r after retry: %s", job.title, last_err)
    return None


def _build_prompts(dimensions, scale_lo, scale_hi, resume, job) -> tuple[str, str]:
    """Build the (system, user) prompt pair. Tight, to stay cost-aware."""
    dim_lines = "\n".join(
        f'  - "{d.id}" (weight {d.weight}): {d.prompt}' for d in dimensions
    )
    dim_ids = ", ".join(f'"{d.id}"' for d in dimensions)

    system_prompt = (
        "You are a precise job-fit evaluator. Score how well a single job listing "
        "fits a candidate, using ONLY the candidate's resume and the job description "
        "provided — never outside knowledge or assumptions.\n\n"
        f"Score EACH of these dimensions on an integer scale of {scale_lo} to {scale_hi} "
        f"(where {scale_lo} = no fit, {scale_hi} = perfect fit):\n"
        f"{dim_lines}\n\n"
        "Cite concrete evidence from the job description in `rationale`. List concerns "
        "(vague comp, location mismatch, seniority mismatch, red-flag language) in "
        "`red_flags`. Estimate compensation in `comp_estimate` from any signals in the "
        "listing, or \"unknown\" if there are none.\n\n"
        "Respond with STRICT JSON ONLY — no markdown, no prose, no code fences. Schema:\n"
        "{\n"
        f'  "dimension_scores": {{{dim_ids}}},  // each an integer {scale_lo}-{scale_hi}\n'
        '  "rationale": "string citing JD evidence",\n'
        '  "red_flags": ["string", ...],\n'
        '  "comp_estimate": "string"\n'
        "}"
    )

    # Keep the JD bounded to control token cost.
    description = (job.description or "")[:6000]
    user_prompt = (
        "=== CANDIDATE RESUME ===\n"
        f"{resume[:6000]}\n\n"
        "=== JOB LISTING ===\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'unknown'}\n"
        f"Remote: {job.is_remote}\n"
        f"Comp (raw): {job.comp_text or 'not stated'}\n"
        f"Description:\n{description}\n\n"
        "Return the strict JSON object now."
    )
    return system_prompt, user_prompt


def _response_text(resp) -> str:
    """Concatenate text blocks from an anthropic Messages response."""
    chunks: list[str] = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "".join(chunks)


def _extract_json(raw: str) -> dict | None:
    """Robustly pull the first JSON object out of a model response."""
    if not raw:
        return None
    raw = raw.strip()

    # Strip ```json ... ``` fences if the model added them despite instructions.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

    # Fast path: the whole thing is JSON.
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        pass

    # Fallback: scan for a balanced {...} span and parse it.
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    return obj if isinstance(obj, dict) else None
                except Exception:  # noqa: BLE001
                    return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Scoring math
# ─────────────────────────────────────────────────────────────────────────────
def _weighted_overall(dim_scores, dimensions, scale_lo, scale_hi) -> float | None:
    """Weighted sum of dimension scores, normalized to 0-100.

    overall = 100 * (Σ wᵢ·sᵢ) / (Σ wᵢ·scale_hi)   with sᵢ clamped to [lo, hi]

    Only dimensions defined in config contribute (extra keys ignored); missing
    dimensions are skipped (don't penalize on absent keys). Returns None if no
    usable dimension scores are present.
    """
    span = (scale_hi - scale_lo) if (scale_hi - scale_lo) else 1
    total_weighted = 0.0
    total_weight = 0.0

    for d in dimensions:
        if d.id not in dim_scores:
            continue
        raw = dim_scores[d.id]
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        # Clamp to the configured scale, then normalize to 0..1 across the span.
        val = max(scale_lo, min(scale_hi, val))
        norm = (val - scale_lo) / span
        total_weighted += d.weight * norm
        total_weight += d.weight

    if total_weight <= 0:
        return None
    return round(100.0 * (total_weighted / total_weight), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Sorting
# ─────────────────────────────────────────────────────────────────────────────
def _sort_by_score(jobs: list[Job]) -> list[Job]:
    """Sort by score descending; None scores sink to the bottom (stable order)."""
    return sorted(
        jobs,
        key=lambda j: (j.score is not None, j.score if j.score is not None else 0.0),
        reverse=True,
    )
