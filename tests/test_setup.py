"""Tests for the setup front-door's pure helpers (scaffold / tracker_summary / cron_lines)."""

from __future__ import annotations

import csv
import importlib.util
import pathlib

_P = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "setup.py"
_spec = importlib.util.spec_from_file_location("job_scout_setup", _P)
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)


def _mini_repo(tmp_path):
    (tmp_path / "config").mkdir()
    for n in ["search", "companies", "scoring", "sources", "news"]:
        (tmp_path / "config" / f"{n}.example.yaml").write_text(f"# {n} example\n")
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\n")
    (tmp_path / "resume").mkdir()
    (tmp_path / "resume" / "resume.example.md").write_text("# resume template\n")
    return tmp_path


def test_scaffold_creates_missing_and_never_overwrites(tmp_path):
    root = _mini_repo(tmp_path)
    made = setup.scaffold(root)
    assert (root / "config" / "search.yaml").exists() and (root / ".env").exists()
    assert (root / "resume" / "resume.md").read_text() == "# resume template\n"
    assert any("TEMPLATE" in m for m in made)
    # re-run is a no-op and must NOT clobber edited files
    (root / "config" / "search.yaml").write_text("# MY edits\n")
    assert setup.scaffold(root) == []
    assert (root / "config" / "search.yaml").read_text() == "# MY edits\n"


def test_scaffold_places_resume_and_linkedin(tmp_path):
    root = _mini_repo(tmp_path)
    r = tmp_path / "myresume.md"
    r.write_text("MY RESUME")
    li = tmp_path / "conns.csv"
    li.write_text("First Name,Last Name\nA,B\n")
    setup.scaffold(root, resume_src=str(r), linkedin_src=str(li))
    assert (root / "resume" / "resume.md").read_text() == "MY RESUME"
    assert (root / "data" / "linkedin_connections.csv").read_text().startswith("First Name")


def test_tracker_summary(tmp_path):
    assert setup.tracker_summary(tmp_path / "none.csv") is None
    p = tmp_path / "jobs.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "status"])
        w.writeheader()
        w.writerow({"title": "A", "status": "new"})
        w.writerow({"title": "B", "status": "interested"})
        w.writerow({"title": "C", "status": ""})           # empty -> counted as 'new'
    s = setup.tracker_summary(p)
    assert s["total"] == 3 and s["by_status"]["new"] == 2 and s["by_status"]["interested"] == 1


def test_cron_lines(tmp_path):
    lines = setup.cron_lines(tmp_path, "/x/py", hour=6)
    assert len(lines) == 3
    assert lines[0].startswith("0 6 * * *") and "run.py" in lines[0]
    assert "news.py" in lines[1]
    assert "*/3" in lines[2] and "strategist.py" in lines[2]
