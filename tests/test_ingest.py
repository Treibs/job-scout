"""Ingest tests — JSON-LD parsing, generic fallback, company-URL parsing (no network)."""

from __future__ import annotations

from job_scout import ingest


def test_parse_company_url_each_ats():
    assert ingest.parse_company_url("https://boards.greenhouse.io/figma") == \
        {"name": "Figma", "ats": "greenhouse", "slug": "figma"}
    assert ingest.parse_company_url("https://jobs.lever.co/brex/") == \
        {"name": "Brex", "ats": "lever", "slug": "brex"}
    assert ingest.parse_company_url("https://jobs.ashbyhq.com/OpenAI")["ats"] == "ashby"
    assert ingest.parse_company_url("https://careers.smartrecruiters.com/Acme")["ats"] == "smartrecruiters"
    wd = ingest.parse_company_url("https://globex.wd5.myworkdayjobs.com/en-US/GlobexCareers")
    assert wd == {"name": "Globex", "ats": "workday", "tenant": "globex", "datacenter": "wd5", "site": "GlobexCareers"}
    assert ingest.parse_company_url("https://example.com/careers") is None


def test_ingest_jsonld(monkeypatch):
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"JobPosting","title":"Head of AI",
     "hiringOrganization":{"@type":"Organization","name":"Northwind"},
     "description":"<p>Lead the AI org &amp; ship.</p>",
     "jobLocation":{"address":{"addressLocality":"Chicago","addressRegion":"IL"}}}
    </script></head><body>x</body></html>
    """
    job = ingest.ingest_url("https://northwind.example/jobs/9", fetch=lambda u: html)
    assert job is not None
    assert job.title == "Head of AI"
    assert job.company == "Northwind"
    assert job.location == "Chicago, IL"
    assert "Lead the AI org & ship" in job.description
    assert job.source == "manual" and job.status == "interested"


def test_ingest_generic_fallback():
    html = '<html><head><meta property="og:title" content="VP, Innovation"></head><body></body></html>'
    job = ingest.ingest_url("https://acmebank.example/careers/vp", fetch=lambda u: html)
    assert job.title == "VP, Innovation"
    assert job.company == "Acmebank"  # derived from host


def test_ingest_rejects_non_http():
    assert ingest.ingest_url("javascript:alert(1)", fetch=lambda u: "x") is None
    assert ingest.ingest_url("", fetch=lambda u: "x") is None
