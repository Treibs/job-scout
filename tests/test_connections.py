"""Tests for the LinkedIn connections overlay (connections.py) + its wiring
into the HTML dashboard."""

from __future__ import annotations

from job_scout import connections
from job_scout.sinks import html_report

# A LinkedIn "Connections.csv" export, including the preamble LinkedIn prepends.
_EXPORT = """\
Notes:
"When exporting your connection data, you may notice that some of the fields are empty."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jane,Doe,https://www.linkedin.com/in/janedoe,,Globex Inc.,Director of AI,04 Jun 2024
John,Smith,https://www.linkedin.com/in/johnsmith,,Initech LLC,VP Engineering,01 Jan 2023
Ada,Lovelace,https://www.linkedin.com/in/ada,,,Independent,12 Dec 2022
"""


def test_normalize_company():
    assert connections.normalize_company("Globex Inc.") == "globex"
    assert connections.normalize_company("Initech, LLC") == "initech"
    assert connections.normalize_company("The Acme Company") == "acme"
    assert connections.normalize_company("") == ""
    assert connections.normalize_company(None) == ""


def test_load_connections_handles_preamble(tmp_path):
    p = tmp_path / "linkedin_connections.csv"
    p.write_text(_EXPORT, encoding="utf-8")
    people = connections.load_connections(p)
    names = {x["name"] for x in people}
    assert names == {"Jane Doe", "John Smith"}  # Ada dropped (no company)
    jane = next(x for x in people if x["name"] == "Jane Doe")
    assert jane["position"] == "Director of AI" and jane["connected_on"] == "04 Jun 2024"


def test_load_connections_missing_file_is_empty(tmp_path):
    assert connections.load_connections(tmp_path / "nope.csv") == []


def test_match_company_picks_closest_group():
    idx = connections.build_index([
        {"name": "A", "company": "Globex Inc"},                 # norm: globex
        {"name": "B", "company": "Northwind Trust Corporation"},  # norm: northwind trust
        {"name": "C", "company": "Acme Foods"},
    ])
    assert [p["name"] for p in connections.match_company("Globex", idx)] == ["A"]            # exact-after-normalize
    assert [p["name"] for p in connections.match_company("Northwind Trust Bank", idx)] == ["B"]  # fuzzy superset
    assert connections.match_company("Zzz Totally Unrelated", idx) == []                    # below threshold


def test_match_company_exact_and_fuzzy(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text(_EXPORT, encoding="utf-8")
    index = connections.build_index(connections.load_connections(p))
    # "Globex Inc." -> normalized "globex"; job says just "Globex".
    assert [m["name"] for m in connections.match_company("Globex", index)] == ["Jane Doe"]
    # Fuzzy: "Initech Corporation" still hits "initech".
    assert [m["name"] for m in connections.match_company("Initech Corporation", index)] == ["John Smith"]
    # No connection there.
    assert connections.match_company("Caterpillar", index) == []
    assert connections.match_company("", index) == []


def test_annotate_attaches_connections(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text(_EXPORT, encoding="utf-8")
    rows = [{"company": "Globex"}, {"company": "Nowhere Co"}]
    connections.annotate(rows, p)
    assert rows[0]["connections"][0]["name"] == "Jane Doe"
    assert rows[1]["connections"] == []


def test_annotate_no_file_gives_empty_lists(tmp_path):
    rows = [{"company": "Globex"}]
    connections.annotate(rows, tmp_path / "absent.csv")
    assert rows[0]["connections"] == []


def test_render_includes_network(tmp_path):
    conn = tmp_path / "linkedin_connections.csv"
    conn.write_text(_EXPORT, encoding="utf-8")
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(
        "score,title,company,apply_url,status\n"
        "88,Head of AI,Globex,https://example.test/1,new\n",
        encoding="utf-8",
    )
    out = html_report.render(csv_path, connections_path=conn)
    html = out.read_text(encoding="utf-8")
    assert "In your network" in html
    assert "Jane Doe" in html
    assert "Know someone" in html  # the filter toggle is present
