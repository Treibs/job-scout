"""CSV sink tests — the dedup-over-time and status-preservation guarantees.

These cover the properties that make the local CSV a safe daily-cron target:
re-running never duplicates a row, manual status edits survive, vanished
listings are marked stale (but terminal ones are kept), and first_seen sticks.
"""

from __future__ import annotations

import csv
from datetime import date


from job_scout.config import Config, EnvCfg, SearchCfg, CompaniesCfg, ScoringCfg, SourcesCfg
from job_scout.models import Job, SHEET_COLUMNS, STATUS_NEW, STATUS_APPLIED, STATUS_STALE
from job_scout.sinks import csv_file


def _config(path) -> Config:
    return Config(
        search=SearchCfg(),
        companies=CompaniesCfg(),
        scoring=ScoringCfg(),
        sources=SourcesCfg(),
        env=EnvCfg(sink="csv", jobs_csv_path=str(path)),
    )


def _job(url, title="Director AI", company="Acme", score=88.0, status=STATUS_NEW,
         first_seen=date(2026, 5, 1)) -> Job:
    return Job(
        id=url[-4:], title=title, company=company, url=url, source="greenhouse",
        location="Chicago, IL", score=score, status=status,
        first_seen=first_seen, last_seen=date(2026, 5, 1),
    )


def _read(path) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return {r["apply_url"]: r for r in csv.DictReader(f)}


def test_fresh_write_creates_file_with_header(tmp_path):
    out = tmp_path / "jobs.csv"
    csv_file.write_csv([_job("https://x/1")], _config(out))

    with open(out, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == SHEET_COLUMNS
    rows = _read(out)
    assert set(rows) == {"https://x/1"}


def test_rerun_does_not_duplicate_rows(tmp_path):
    """The core 'no duplicates over time' guarantee: same job two runs -> one row."""
    out = tmp_path / "jobs.csv"
    cfg = _config(out)
    csv_file.write_csv([_job("https://x/1")], cfg)
    csv_file.write_csv([_job("https://x/1", score=91.0)], cfg)  # same url, new score

    rows = _read(out)
    assert list(rows) == ["https://x/1"]          # exactly one row, not two
    assert rows["https://x/1"]["score"] == "91.0"  # refreshed in place


def test_user_status_is_preserved_across_runs(tmp_path):
    """A manually-set 'applied' must survive the next run (jobs rebuild as 'new')."""
    out = tmp_path / "jobs.csv"
    cfg = _config(out)
    csv_file.write_csv([_job("https://x/1")], cfg)

    # Simulate the user marking it applied in the CSV.
    rows = _read(out)
    rows["https://x/1"]["status"] = STATUS_APPLIED
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        w.writerows(rows.values())

    # Next run re-emits the job as 'new' — status must stay 'applied'.
    csv_file.write_csv([_job("https://x/1", status=STATUS_NEW)], cfg)
    assert _read(out)["https://x/1"]["status"] == STATUS_APPLIED


def test_first_seen_is_preserved(tmp_path):
    out = tmp_path / "jobs.csv"
    cfg = _config(out)
    csv_file.write_csv([_job("https://x/1", first_seen=date(2026, 1, 1))], cfg)
    # A later run with a newer first_seen must NOT overwrite the original.
    csv_file.write_csv([_job("https://x/1", first_seen=date(2026, 6, 1))], cfg)
    assert _read(out)["https://x/1"]["first_seen"] == "2026-01-01"


def test_vanished_listing_marked_stale_but_kept(tmp_path):
    out = tmp_path / "jobs.csv"
    cfg = _config(out)
    csv_file.write_csv([_job("https://x/1"), _job("https://x/2")], cfg)
    # Next run only sees job 1 — job 2 disappeared from source.
    csv_file.write_csv([_job("https://x/1")], cfg)

    rows = _read(out)
    assert set(rows) == {"https://x/1", "https://x/2"}   # both kept
    assert rows["https://x/1"]["status"] == STATUS_NEW
    assert rows["https://x/2"]["status"] == STATUS_STALE  # marked, not deleted


def test_terminal_status_survives_when_listing_vanishes(tmp_path):
    out = tmp_path / "jobs.csv"
    cfg = _config(out)
    csv_file.write_csv([_job("https://x/1", status=STATUS_APPLIED)], cfg)
    # Listing gone this run, but we applied — keep 'applied', don't mark stale.
    csv_file.write_csv([], cfg)
    assert _read(out)["https://x/1"]["status"] == STATUS_APPLIED


def test_rows_sorted_by_score_desc(tmp_path):
    out = tmp_path / "jobs.csv"
    csv_file.write_csv(
        [_job("https://x/1", score=50.0), _job("https://x/2", score=95.0)],
        _config(out),
    )
    with open(out, newline="", encoding="utf-8") as f:
        ordered = [r["apply_url"] for r in csv.DictReader(f)]
    assert ordered == ["https://x/2", "https://x/1"]
