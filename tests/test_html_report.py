"""HTML report tests — the dashboard is generated from the CSV and self-contained."""

from __future__ import annotations

import csv
import json
import re

from job_scout.models import SHEET_COLUMNS
from job_scout.sinks import html_report


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in SHEET_COLUMNS})


def _embedded_data(html: str):
    m = re.search(r"const DATA = (.*?);\nconst DIMS", html, re.S)
    return json.loads(m.group(1))


def test_render_writes_selfcontained_html(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    _write_csv(csv_path, [
        {"score": "88.0", "title": "Director of AI", "company": "Caterpillar",
         "apply_url": "https://x/1", "status": "new", "rationale": "Strong fit"},
        {"score": "40.0", "title": "Sales Lead", "company": "Acme",
         "apply_url": "https://x/2", "status": "stale"},
    ])
    out = html_report.render(csv_path)
    assert out == csv_path.with_suffix(".html")
    html = out.read_text(encoding="utf-8")
    # self-contained: data embedded, no external data file referenced
    data = _embedded_data(html)
    assert {d["title"] for d in data} == {"Director of AI", "Sales Lead"}
    assert "<title>Job Scout" in html
    assert 'id="minscore"' in html  # the filter UI is present


def test_render_missing_csv_returns_none(tmp_path):
    assert html_report.render(tmp_path / "nope.csv") is None


def test_render_strips_dangerous_url_schemes(tmp_path):
    """javascript:/data: apply_urls are dropped so they can't render as hrefs (XSS)."""
    csv_path = tmp_path / "jobs.csv"
    _write_csv(csv_path, [
        {"score": "50", "title": "Evil", "company": "Y", "status": "new",
         "apply_url": "javascript:alert(document.cookie)"},
        {"score": "60", "title": "Data", "company": "Z", "status": "new",
         "apply_url": "data:text/html,<script>alert(1)</script>"},
        {"score": "70", "title": "Good", "company": "W", "status": "new",
         "apply_url": "https://boards.greenhouse.io/x/jobs/1"},
    ])
    html = html_report.render(csv_path).read_text(encoding="utf-8")
    assert "javascript:alert" not in html
    assert "data:text/html" not in html
    data = {d["title"]: d["apply_url"] for d in _embedded_data(html)}
    assert data["Evil"] == ""        # scheme stripped -> non-clickable
    assert data["Data"] == ""
    assert data["Good"] == "https://boards.greenhouse.io/x/jobs/1"


def test_render_escapes_script_break(tmp_path):
    """A rationale containing </script> must not break the embedded data block."""
    csv_path = tmp_path / "jobs.csv"
    _write_csv(csv_path, [
        {"score": "50", "title": "X", "company": "Y", "apply_url": "https://x/1",
         "status": "new", "rationale": "danger </script> text"},
    ])
    html = html_report.render(csv_path).read_text(encoding="utf-8")
    assert "</script> text" not in html  # the raw closer is escaped
    assert _embedded_data(html)[0]["rationale"] == "danger </script> text"
