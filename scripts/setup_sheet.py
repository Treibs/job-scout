#!/usr/bin/env python3
"""One-time Google Sheets tracker setup (PROJECT.md §10).

Creates (or reuses) a worksheet named "jobs", writes the canonical header row
(`SHEET_COLUMNS` from models.py — single source of truth so the header never
drifts from the row-writer), freezes + bolds the header, and applies conditional
formatting on the `score` column (green / yellow gradient by band).

It hits the live Sheets API, so it is meant to be run once by the end user after
they've created a service account and shared the Sheet with it. Run:

    python scripts/setup_sheet.py
"""

from __future__ import annotations

import json
import pathlib
import sys

# The package lives under src/ — make it importable when run from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from job_scout.config import load_config  # noqa: E402
from job_scout.models import SHEET_COLUMNS  # noqa: E402

WORKSHEET_NAME = "jobs"

# Google Sheets scopes for read/write to a single shared spreadsheet.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _fail(msg: str) -> int:
    print(f"\nsetup_sheet: {msg}", file=sys.stderr)
    return 1


def _load_credentials(raw: str):
    """Build google-auth credentials from either inline JSON or a path to a key file."""
    from google.oauth2.service_account import Credentials

    raw = raw.strip()
    if raw.startswith("{"):
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    key_path = pathlib.Path(raw).expanduser()
    if not key_path.exists():
        raise FileNotFoundError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON points to {raw!r} which does not exist, "
            "and is not inline JSON."
        )
    return Credentials.from_service_account_file(str(key_path), scopes=SCOPES)


def _col_letter(col_idx: int) -> str:
    """0-based column index -> A1 column label (A, B, ..., Z, AA, AB, ...)."""
    label = ""
    n = col_idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


def _score_col_a1(num_rows: int = 1000) -> tuple[str, int]:
    """Return (A1 range for the score column body, 0-based column index)."""
    col_idx = SHEET_COLUMNS.index("score")  # 0-based
    col_letter = _col_letter(col_idx)  # survives reordering past column Z
    # Body rows only (skip the header at row 1).
    return f"{col_letter}2:{col_letter}{num_rows}", col_idx


def _apply_conditional_formatting(worksheet, spreadsheet) -> str:
    """Green (>=80) / yellow (>=60) bands on the score column. Returns a note string."""
    score_range, score_col = _score_col_a1()
    sheet_id = worksheet.id

    # gspread A1 -> GridRange (0-based, end-exclusive). Body rows 2..1000, score col only.
    grid_range = {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "endRowIndex": 1000,
        "startColumnIndex": score_col,
        "endColumnIndex": score_col + 1,
    }

    green = {"red": 0.72, "green": 0.88, "blue": 0.74}
    yellow = {"red": 1.0, "green": 0.95, "blue": 0.70}

    requests = [
        # Rule 0 (highest priority): score >= 80 -> green.
        {
            "addConditionalFormatRule": {
                "index": 0,
                "rule": {
                    "ranges": [grid_range],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_GREATER_THAN_EQ",
                            "values": [{"userEnteredValue": "80"}],
                        },
                        "format": {"backgroundColor": green},
                    },
                },
            }
        },
        # Rule 1: score >= 60 -> yellow (only hits rows that failed rule 0).
        {
            "addConditionalFormatRule": {
                "index": 1,
                "rule": {
                    "ranges": [grid_range],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_GREATER_THAN_EQ",
                            "values": [{"userEnteredValue": "60"}],
                        },
                        "format": {"backgroundColor": yellow},
                    },
                },
            }
        },
    ]
    spreadsheet.batch_update({"requests": requests})
    return f"conditional formatting on {score_range}: >=80 green, >=60 yellow"


def main() -> int:
    cfg = load_config("config/search.yaml")
    sheet_id = cfg.env.sheet_id
    creds_raw = cfg.env.google_service_account_json

    if not sheet_id:
        return _fail(
            "SHEET_ID is not set. Set it in .env (local) or GitHub Secrets (CI), then "
            "re-run. It is the long ID in your Sheet's URL: "
            "https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit"
        )
    if not creds_raw:
        return _fail(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. Create a Google Cloud service "
            "account, enable the Sheets API, download its JSON key, and set "
            "GOOGLE_SERVICE_ACCOUNT_JSON to the JSON (or a path to the key file). "
            "Then SHARE the Sheet with the service-account email as an Editor."
        )

    try:
        import gspread  # noqa: F401
    except ImportError:
        return _fail("gspread is not installed. Run: pip install -r requirements.txt")

    try:
        creds = _load_credentials(creds_raw)
    except FileNotFoundError as e:
        return _fail(str(e))
    except (json.JSONDecodeError, ValueError) as e:
        return _fail(f"could not parse service-account credentials: {e}")

    import gspread

    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open_by_key(sheet_id)
    except Exception as e:  # noqa: BLE001 — gspread raises APIError / SpreadsheetNotFound
        return _fail(
            f"could not open Sheet {sheet_id!r}: {e}\n"
            "Check that the ID is correct AND that you shared the Sheet with the "
            "service-account email (the 'client_email' in the JSON key) as an Editor."
        )

    # Create or reuse the "jobs" worksheet.
    try:
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        print(f"reusing existing worksheet {WORKSHEET_NAME!r}")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=WORKSHEET_NAME, rows=1000, cols=max(len(SHEET_COLUMNS), 26)
        )
        print(f"created worksheet {WORKSHEET_NAME!r}")

    # Header row = canonical column order from models.SHEET_COLUMNS.
    worksheet.update(
        range_name="A1",
        values=[SHEET_COLUMNS],
    )
    print(f"wrote header row ({len(SHEET_COLUMNS)} columns)")

    # Freeze + bold the header.
    notes: list[str] = []
    try:
        worksheet.freeze(rows=1)
        worksheet.format(
            f"A1:{chr(ord('A') + len(SHEET_COLUMNS) - 1)}1",
            {"textFormat": {"bold": True}},
        )
        notes.append("header frozen + bolded")
    except Exception as e:  # noqa: BLE001
        notes.append(f"WARNING: header freeze/bold failed: {e}")

    # Conditional formatting on the score column.
    try:
        notes.append(_apply_conditional_formatting(worksheet, spreadsheet))
    except Exception as e:  # noqa: BLE001
        notes.append(
            f"WARNING: conditional formatting failed ({e}). Header is still "
            "frozen + bolded; you can add score color bands manually via "
            "Format > Conditional formatting on the score column."
        )

    print("\nSetup complete:")
    for n in notes:
        print(f"  - {n}")
    print(
        f"\nSheet ready: https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        f"\nNext: run `python scripts/run.py --config config/search.yaml` to populate it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
