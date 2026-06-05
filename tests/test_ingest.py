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


def test_is_public_url_blocks_private_and_metadata():
    # Literal IPs / localhost — resolved without external DNS, so this is hermetic.
    assert ingest._is_public_url("http://169.254.169.254/latest/meta-data/") is False
    assert ingest._is_public_url("http://127.0.0.1:8765/") is False
    assert ingest._is_public_url("http://10.0.0.5/careers") is False
    assert ingest._is_public_url("http://192.168.1.10/job/1") is False
    assert ingest._is_public_url("http://localhost/x") is False
    assert ingest._is_public_url("not a url") is False
    # A public literal IP is allowed.
    assert ingest._is_public_url("http://8.8.8.8/jobs/1") is True


def test_ingest_url_refuses_ssrf_target():
    # Real network path (no injected fetch): the SSRF guard must short-circuit to None.
    assert ingest.ingest_url("http://169.254.169.254/latest/meta-data/") is None


def test_fetch_public_blocks_redirect_to_internal(monkeypatch):
    """A public origin that 30x-redirects to an internal host must be refused: the
    redirect target is re-validated before the next hop."""
    import requests

    class _Resp:
        is_redirect = True
        is_permanent_redirect = False
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        status_code = 302
        text = "x"

    class _Sess:
        trust_env = True

        def mount(self, *a):
            pass

        def get(self, url, **kw):
            return _Resp()        # hop 1 (public 8.8.8.8) redirects internally...

        def close(self):
            pass

    monkeypatch.setattr(requests, "Session", _Sess)
    # 8.8.8.8 is public so hop 1 proceeds; the link-local redirect target is blocked.
    assert ingest.fetch_public("http://8.8.8.8/start") is None
    assert ingest.ingest_url("", fetch=lambda u: "x") is None
