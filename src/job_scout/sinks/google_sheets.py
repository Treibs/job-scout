"""Google Sheets tracker sink — the dashboard the user actually works from.

`write_sheet` **upserts by `Job.id`**: existing rows are updated in place
(status / score / last_seen), genuinely new jobs are appended, and rows whose id
is *not* in this run are marked ``stale`` (their listing disappeared from source)
rather than deleted — the user keeps their history and notes.

Column order is owned by ``SHEET_COLUMNS`` in models.py; both this writer and the
header it ensures derive from that single list so they can never drift apart.

gspread + google-auth are imported lazily inside the function so a bare CI
checkout (or a run with sinks disabled) doesn't pay the import cost or require the
dep just to import the module.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Job, SHEET_COLUMNS, STATUS_STALE, job_to_row


_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_WORKSHEET_NAME = "jobs"


def write_sheet(jobs: list[Job], config) -> None:
    """Upsert ``jobs`` into the configured Google Sheet's ``jobs`` worksheet.

    Raises a clear error if credentials or the sheet id are missing (the pipeline
    catches sink errors and logs them without losing the run's data).
    """
    sheet_id = getattr(config.env, "sheet_id", None)
    if not sheet_id:
        raise ValueError(
            "google_sheets sink: config.env.sheet_id is not set "
            "(set the SHEET_ID env var / secret)."
        )

    raw_creds = getattr(config.env, "google_service_account_json", None)
    if not raw_creds:
        raise ValueError(
            "google_sheets sink: config.env.google_service_account_json is not set "
            "(set the GOOGLE_SERVICE_ACCOUNT_JSON env var / secret — raw JSON or a path)."
        )

    # Lazy imports — only needed when we actually write.
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        _load_service_account_info(raw_creds), scopes=_SCOPES
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = _get_or_create_worksheet(spreadsheet)

    _ensure_header(worksheet)

    # Read existing rows once and build id -> sheet-row-number (1-based, header=1).
    existing = worksheet.get_all_values()
    # `Job.id` is NOT a sheet column (it's the upsert key only, never written), so
    # it can't be read back from the sheet. We key the upsert on `apply_url`, which
    # is `job.url` — unique per job and persisted in every row.
    url_col = SHEET_COLUMNS.index("apply_url")
    status_col = SHEET_COLUMNS.index("status")

    url_to_row: dict[str, int] = {}
    for row_idx, row in enumerate(existing[1:], start=2):  # skip header
        if len(row) > url_col and row[url_col]:
            url_to_row[row[url_col]] = row_idx

    run_urls = {job.url for job in jobs}

    updates: list[dict] = []  # batched value ranges for existing rows
    appends: list[list] = []  # new rows to append in one call

    for job in jobs:
        row_values = _job_to_row(job)
        existing_row = url_to_row.get(job.url)
        if existing_row is not None:
            updates.append(
                {
                    "range": _a1_range(existing_row, len(SHEET_COLUMNS)),
                    "values": [row_values],
                }
            )
        else:
            appends.append(row_values)

    # Mark rows whose listing is NOT in this run as stale (status column only).
    for url, row_idx in url_to_row.items():
        if url in run_urls:
            continue
        current_status = (
            existing[row_idx - 1][status_col]
            if len(existing[row_idx - 1]) > status_col
            else ""
        )
        if current_status != STATUS_STALE:
            updates.append(
                {
                    "range": _a1_cell(row_idx, status_col + 1),
                    "values": [[STATUS_STALE]],
                }
            )

    # Batch the in-place updates (one API call), then append new rows (one call).
    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")
    if appends:
        worksheet.append_rows(appends, value_input_option="USER_ENTERED")


# ── helpers ──────────────────────────────────────────────────────────────────


def _load_service_account_info(raw: str) -> dict:
    """Return the service-account dict from a file path or a raw JSON string."""
    candidate = raw.strip()
    # If it looks like a path that exists on disk, read the file.
    if not candidate.startswith("{"):
        p = Path(candidate)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(candidate)


def _get_or_create_worksheet(spreadsheet):
    import gspread

    try:
        return spreadsheet.worksheet(_WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=_WORKSHEET_NAME, rows=1000, cols=max(26, len(SHEET_COLUMNS))
        )


def _ensure_header(worksheet) -> None:
    """Guarantee row 1 is exactly SHEET_COLUMNS."""
    first_row = worksheet.row_values(1)
    if first_row != SHEET_COLUMNS:
        worksheet.update(
            range_name=_a1_range(1, len(SHEET_COLUMNS)),
            values=[SHEET_COLUMNS],
            value_input_option="USER_ENTERED",
        )


def _job_to_row(job: Job) -> list:
    """Build a sheet row from a Job (None rendered as empty string)."""
    return [_cell(v) for v in job_to_row(job)]


def _cell(value):
    """Sheet cells can't hold None — render it as an empty string."""
    return "" if value is None else value


def _a1_range(row: int, ncols: int) -> str:
    """A1 range for a full row of ``ncols`` columns, e.g. row 2 -> 'A2:Q2'."""
    return f"A{row}:{_col_letter(ncols)}{row}"


def _a1_cell(row: int, col: int) -> str:
    """A1 reference for a single cell (1-based col), e.g. (2, 15) -> 'O2'."""
    return f"{_col_letter(col)}{row}"


def _col_letter(n: int) -> str:
    """1-based column number -> spreadsheet letters (1->A, 27->AA)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
