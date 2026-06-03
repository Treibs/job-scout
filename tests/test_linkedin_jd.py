"""linkedin_jd tests — id extraction + HTML parsing (no network)."""

from __future__ import annotations

from job_scout.sources import linkedin_jd


def test_job_id_extraction():
    assert linkedin_jd.job_id("https://www.linkedin.com/jobs/view/4413176256") == "4413176256"
    assert linkedin_jd.job_id("https://www.linkedin.com/jobs/view/x-y-_-1234567890") == "1234567890"
    assert linkedin_jd.job_id("https://example.com/no-id-here") is None
    assert linkedin_jd.job_id("") is None


def test_parse_description_strips_html():
    html = (
        '<div class="show-more-less-html__markup relative">'
        '<strong>Lead</strong> the AI org.<br>Build &amp; ship models.</div>'
    )
    out = linkedin_jd._parse_description(html)
    assert out == "Lead the AI org. Build & ship models."


def test_parse_description_none_when_absent():
    assert linkedin_jd._parse_description("<div>no markup container</div>") is None


def test_fetch_uses_injected_session(monkeypatch):
    class FakeResp:
        status_code = 200
        text = '<div class="description__text">Hello <b>world</b></div>'
    class FakeSession:
        def get(self, url, **kw):
            assert "jobs-guest/jobs/api/jobPosting/4413176256" in url
            return FakeResp()
    out = linkedin_jd.fetch_description(
        "https://www.linkedin.com/jobs/view/4413176256", session=FakeSession())
    assert out == "Hello world"


def test_fetch_returns_none_on_bad_status(monkeypatch):
    class FakeResp:
        status_code = 429
        text = "rate limited"
    class FakeSession:
        def get(self, url, **kw): return FakeResp()
    assert linkedin_jd.fetch_description(
        "https://www.linkedin.com/jobs/view/4413176256", session=FakeSession()) is None
