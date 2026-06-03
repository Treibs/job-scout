"""The pipeline — `run_pipeline(config)` is the ONE entry point. Both the daily
cron and on-demand runs call this; there is no duplicated logic (principle #2).

    gather (sources) -> normalize -> dedupe -> score -> sink (sheet + state)

Every stage below is implemented in its own module. The signatures here ARE the
contract the module authors build to:

    normalize.normalize_jobs(raw: list[dict])                -> list[Job]
    dedupe.dedupe(jobs: list[Job], seen: dict[str, dict])    -> list[Job]
    score.score_jobs(jobs: list[Job], config: Config)        -> list[Job]   # sorted desc
    sinks.state.load_state(config) / save_state(jobs, config)
    sinks.csv_file.write_csv(jobs: list[Job], config)        # default sink
    sinks.google_sheets.write_sheet(jobs: list[Job], config) # if env.sink="google_sheets"

Sources:
    sources.jobspy_source.JobSpySource           (a `Source` — boards)
    sources.ats.<ats>.fetch(company, config)     -> list[dict]   (per-company ATS)
"""

from __future__ import annotations

import logging

from .config import Config, CompanyTarget
from .models import Job
from .sources.base import safe_fetch

log = logging.getLogger("job_scout.pipeline")


def _gather(config: Config) -> list[dict]:
    """Collect raw dicts from every enabled source, isolating per-source failure."""
    raw: list[dict] = []

    # Boards via JobSpy (single Source over several sites).
    if config.sources.boards.enabled:
        from .sources.jobspy_source import JobSpySource

        raw += safe_fetch(JobSpySource(), config)

    # Direct ATS pulls, one per company in companies.yaml.
    if config.sources.ats.enabled and config.companies.companies:
        from .sources.ats import ATS_FETCHERS  # {ats_name: fetch_fn}

        for company in config.companies.companies:
            fetch_fn = ATS_FETCHERS.get(company.ats)
            if fetch_fn is None:
                log.warning("no ATS fetcher for %r (company %s)", company.ats, company.name)
                continue
            raw += _safe_company_fetch(fetch_fn, company, config)

    return raw


def _safe_company_fetch(fetch_fn, company: CompanyTarget, config: Config) -> list[dict]:
    """safe_fetch equivalent for the per-company ATS functions."""
    try:
        rows = fetch_fn(company, config) or []
        for r in rows:
            r.setdefault("source", company.ats)
            r.setdefault("_company", company.name)
        log.info("ats %s/%s: %d raw listings", company.ats, company.name, len(rows))
        return rows
    except Exception as e:  # noqa: BLE001
        log.warning("ats %s/%s failed (skipped): %s", company.ats, company.name, e)
        return []


def run_pipeline(config: Config) -> list[Job]:
    """Run the full discovery+scoring pipeline. Returns the ranked jobs written."""
    from . import normalize, dedupe, score
    from .sinks import state as state_sink

    raw = _gather(config)
    log.info("gathered %d raw listings", len(raw))

    jobs = normalize.normalize_jobs(raw)
    log.info("normalized to %d jobs", len(jobs))

    seen = state_sink.load_state(config)
    jobs = dedupe.dedupe(jobs, seen)
    log.info("%d jobs after dedupe", len(jobs))

    jobs = score.score_jobs(jobs, config)
    log.info("%d jobs after scoring/filtering", len(jobs))

    # Sinks: write the tracker, then persist state back to the repo.
    _write_tracker(jobs, config)
    state_sink.save_state(jobs, config)
    return jobs


def _write_tracker(jobs: list[Job], config: Config) -> None:
    """Write the chosen tracker sink. A sink failure is logged, never raised, so
    it can't lose the run's data (state is still persisted by the caller)."""
    sink = (getattr(config.env, "sink", None) or "csv").lower()
    try:
        if sink == "google_sheets":
            from .sinks import google_sheets

            google_sheets.write_sheet(jobs, config)
        else:
            from .sinks import csv_file, html_report

            csv_file.write_csv(jobs, config)
            # Regenerate the filterable HTML dashboard from the upserted CSV so the
            # cron keeps it fresh. Built from the file (not `jobs`) so it includes
            # carried-over / stale rows. Never let it fail the run.
            try:
                html_report.render(csv_file.output_path(config))
            except Exception as e:  # noqa: BLE001
                log.error("html report failed: %s", e)
    except Exception as e:  # noqa: BLE001 — never let a sink failure lose the run's data
        log.error("%s sink write failed: %s", sink, e)
