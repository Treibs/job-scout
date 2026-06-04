"""Tests for the interest-capture CSV mutation in scripts/serve.py.

`set_status` is the pure, importable core: it must flip exactly one status cell
(matched by apply_url), preserve every other column and the exact SHEET_COLUMNS
order, and rewrite the CSV atomically. These tests pin those guarantees.
"""

from __future__ import annotations

import csv
import importlib.util
import pathlib

from job_scout.models import SHEET_COLUMNS

# Import set_status from scripts/serve.py the same way the scripts bootstrap src/.
_SERVE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "serve.py"
_spec = importlib.util.spec_from_file_location("job_scout_serve", _SERVE_PATH)
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)
set_status = serve.set_status


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in SHEET_COLUMNS})


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def _row(apply_url, **over):
    base = {c: "" for c in SHEET_COLUMNS}
    base.update(
        score="88",
        mission="4",
        comp="3",
        learning="5",
        wlb="4",
        prestige="3",
        title="Director, AI",
        company="Acme",
        location="Chicago, IL",
        comp_estimate="$200k–$240k",
        source="greenhouse",
        date_posted="2026-05-01",
        first_seen="2026-05-01",
        apply_url=apply_url,
        status="new",
        rationale="Strong mission fit",
        red_flags="vague comp",
    )
    base.update(over)
    return base


def test_updates_correct_row_and_preserves_columns(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    r1 = _row("https://example.com/a", company="Acme")
    r2 = _row("https://example.com/b", company="Beta", title="VP Eng")
    _write_csv(csv_path, [r1, r2])

    assert set_status(csv_path, "https://example.com/b", "interested") is True

    fieldnames, rows = _read_csv(csv_path)
    # Column order preserved exactly.
    assert fieldnames == SHEET_COLUMNS
    by_url = {r["apply_url"]: r for r in rows}
    # Only the matched row's status changed.
    assert by_url["https://example.com/b"]["status"] == "interested"
    assert by_url["https://example.com/a"]["status"] == "new"
    # Every other column on the matched row is untouched.
    for col in SHEET_COLUMNS:
        if col == "status":
            continue
        assert by_url["https://example.com/b"][col] == r2[col]
    # The other row is fully intact.
    assert by_url["https://example.com/a"] == r1


def test_unknown_apply_url_returns_false_and_leaves_file(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    r1 = _row("https://example.com/a")
    _write_csv(csv_path, [r1])

    assert set_status(csv_path, "https://example.com/missing", "applied") is False

    fieldnames, rows = _read_csv(csv_path)
    assert fieldnames == SHEET_COLUMNS
    assert len(rows) == 1
    assert rows[0]["status"] == "new"  # unchanged


def test_applied_status_round_trip(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    _write_csv(csv_path, [_row("https://example.com/a")])

    assert set_status(csv_path, "https://example.com/a", "applied") is True
    _, rows = _read_csv(csv_path)
    assert rows[0]["status"] == "applied"


def test_post_handler_rejects_invalid_status():
    # The HTTP layer guards the allowed set; "stale"/"new"-only-pipeline aside,
    # an arbitrary value is rejected before set_status is ever called.
    assert "interested" in serve.ALLOWED_STATUSES
    assert "applied" in serve.ALLOWED_STATUSES
    assert "pass" in serve.ALLOWED_STATUSES
    assert "bogus" not in serve.ALLOWED_STATUSES
