"""Local CSV tracker sink — a zero-dependency alternative to Google Sheets.

`write_csv` **upserts by `apply_url`** (== ``Job.url``), exactly like the Sheets
sink, so re-running the pipeline never appends a duplicate row for a job we've
already recorded:

  · a job already in the CSV  -> its row is refreshed in place,
  · a genuinely new job       -> appended,
  · a row whose listing is NOT in this run -> marked ``stale`` (kept, not deleted).

Two things are *preserved* across runs so the file stays a useful tracker the
user can edit by hand:

  · ``status`` — if you've advanced a row to reviewing/applied/rejected/archived,
    that is kept (the pipeline always rebuilds jobs as ``new``, so without this
    your manual progress would be clobbered every run). Terminal statuses
    (applied/rejected/archived) survive even when the listing disappears.
  · ``first_seen`` — the earliest date we ever saw the role, taken from the
    existing CSV when present.

Column order is owned by ``SHEET_COLUMNS`` in models.py. Writes are atomic
(tmp file + ``os.replace``). Stdlib only (csv, os, pathlib) — no third-party deps.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

from ..models import Job, SHEET_COLUMNS, STATUS_STALE, job_to_row

log = logging.getLogger("job_scout.csv_file")

# state/csv lives at the repo root. This file is at
# <repo>/src/job_scout/sinks/csv_file.py, so parents[3] is <repo>.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATH = _REPO_ROOT / "output" / "jobs.csv"

# Statuses the *user* sets — never overwritten by the pipeline's default "new".
_USER_STATUSES = frozenset({"reviewing", "applied", "rejected", "archived"})
# Of those, the ones that survive even when the listing vanishes from source
# (you applied/were rejected/archived it — that history outlives the posting).
_TERMINAL_STATUSES = frozenset({"applied", "rejected", "archived"})

_URL_COL = "apply_url"
_STATUS_COL = "status"
_FIRST_SEEN_COL = "first_seen"
_SCORE_COL = "score"


def output_path(config) -> Path:
    """Where the CSV is written. ``JOBS_CSV_PATH`` (config.env.jobs_csv_path) wins;
    relative paths resolve against the repo root, matching the state sink. Public
    so the HTML report can target the same file the pipeline just wrote."""
    raw = getattr(getattr(config, "env", None), "jobs_csv_path", None)
    if not raw:
        return _DEFAULT_PATH
    p = Path(raw)
    return p if p.is_absolute() else (_REPO_ROOT / p)


# Back-compat internal alias.
_resolve_path = output_path


def _read_existing(path: Path) -> dict[str, dict]:
    """Return ``{apply_url: row_dict}`` from an existing CSV, keyed on apply_url.

    Rows are coerced to the canonical ``SHEET_COLUMNS`` schema (unknown columns
    dropped, missing columns filled with ""). A missing/empty/garbled file yields
    an empty map — the run still proceeds.
    """
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return {}

    out: dict[str, dict] = {}
    for row in rows:
        url = (row.get(_URL_COL) or "").strip()
        if not url:
            continue
        out[url] = {col: (row.get(col) or "") for col in SHEET_COLUMNS}
    return out


def _row_dict(job: Job) -> dict:
    """A job as a ``{column: cell}`` dict (None rendered as empty string)."""
    return {
        col: ("" if val is None else val)
        for col, val in zip(SHEET_COLUMNS, job_to_row(job))
    }


def write_csv(jobs: list[Job], config) -> None:
    """Upsert ``jobs`` into the local CSV tracker (dedup-safe, status-preserving)."""
    path = _resolve_path(config)
    existing = _read_existing(path)
    run_urls = {job.url for job in jobs}

    # Start from what's already on disk, overlay this run's jobs.
    out: dict[str, dict] = dict(existing)

    for job in jobs:
        row = _row_dict(job)
        prev = existing.get(job.url)
        if prev:
            # Keep the earliest first_seen and any user-advanced status.
            if prev.get(_FIRST_SEEN_COL):
                row[_FIRST_SEEN_COL] = prev[_FIRST_SEEN_COL]
            prev_status = (prev.get(_STATUS_COL) or "").strip()
            if prev_status in _USER_STATUSES:
                row[_STATUS_COL] = prev_status
        out[job.url] = row

    # Mark rows whose listing is gone from this run as stale (keep terminal ones).
    for url, row in out.items():
        if url in run_urls:
            continue
        status = (row.get(_STATUS_COL) or "").strip()
        if status in _TERMINAL_STATUSES:
            continue
        row[_STATUS_COL] = STATUS_STALE

    _atomic_write_csv(path, out.values())
    log.info(
        "csv sink: %d rows written to %s (%d this run, %d carried over)",
        len(out),
        path,
        len(run_urls),
        len(out) - len(run_urls),
    )


def _score_key(row: dict) -> float:
    """Sort key: score desc, unscored rows last."""
    try:
        return float(row.get(_SCORE_COL) or "")
    except (TypeError, ValueError):
        return float("-inf")


def _atomic_write_csv(path: Path, rows) -> None:
    rows = sorted(rows, key=_score_key, reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in SHEET_COLUMNS})
    os.replace(tmp, path)
